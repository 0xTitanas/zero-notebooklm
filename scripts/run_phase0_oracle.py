#!/usr/bin/env python3
"""NotebookLM Bare — Phase 0 compatibility-oracle generator and validator.

Single entry point for the Phase 0 surface lock against ``notebooklm-py==0.7.2``.

Modes
-----
``--check``      (default) Offline validation of the committed ``compat/`` oracle:
                 artifacts exist and parse, the pinned target/hash/commit/plan-SHA
                 are exact, the Click tree reports 103 nodes / 90 leaves, the parity
                 matrix uses only pass/open/blocked, and no denylisted third-party
                 import exists in project code. No network,
                 no venv. This is what CI and the test-suite rely on.

``--generate``   Regenerate every ``compat/`` artifact from the pinned upstream
                 inside a disposable, isolated venv. Never installs globally:
                 downloads the pinned wheel (verifying its SHA-256), builds a venv
                 (``uv`` preferred, stdlib ``venv`` fallback), runs the reflective
                 prober, captures real CLI ``--help`` / error goldens, and assembles
                 the JSON/markdown artifacts. The venv is deleted unless ``--keep``.

``--verify-remote``  Confirm the pinned source commit exists in the upstream GitHub
                 repository (network). Best-effort; ``--generate`` also attempts it.

The pinned facts live in ``_phase0_constants.py`` and are asserted from there, so
the generator and the checks can never disagree about the target.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _phase0_constants as C  # noqa: E402
import import_origin_audit  # noqa: E402

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
INTROSPECT = C.SCRIPTS_DIR / "introspect_upstream.py"


# --------------------------------------------------------------------------- #
# Small utilities
# --------------------------------------------------------------------------- #


def _now_iso() -> str:
    override = os.environ.get("PHASE0_GENERATED_AT")
    if override:
        return override
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write("\n")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _redactor(work_dir: Path):
    home = os.path.expanduser("~")
    tmp = os.environ.get("TMPDIR", "").rstrip("/")
    pairs = [(str(work_dir), "<work>"), (home, "<home>")]
    if tmp:
        pairs.append((tmp, "<tmp>"))

    def redact(text: str) -> str:
        for raw, token in pairs:
            if raw and raw not in ("/", "") and raw in text:
                text = text.replace(raw, token)
        return text

    return redact


# --------------------------------------------------------------------------- #
# Generation: wheel / venv / introspection
# --------------------------------------------------------------------------- #


def ensure_wheel(work_dir: Path, explicit: Path | None) -> Path:
    if explicit is not None:
        wheel = Path(explicit)
        if not wheel.is_file():
            raise SystemExit(f"--wheel not found: {wheel}")
        return wheel
    wheelhouse = work_dir / "wheelhouse"
    wheelhouse.mkdir(parents=True, exist_ok=True)
    existing = list(wheelhouse.glob(C.WHEEL_FILENAME))
    if existing:
        return existing[0]
    print(
        f"[generate] downloading pinned wheel {C.TARGET_REQUIREMENT} (no global install)…"
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--no-deps",
            "--only-binary=:all:",
            C.TARGET_REQUIREMENT,
            "-d",
            str(wheelhouse),
        ],
        check=True,
    )
    found = list(wheelhouse.glob(C.WHEEL_FILENAME))
    if not found:
        raise SystemExit("pip download did not produce the expected wheel filename")
    return found[0]


def verify_wheel(wheel: Path) -> tuple[str, bool]:
    digest = _sha256_file(wheel)
    return digest, (digest == C.WHEEL_SHA256)


def build_venv(work_dir: Path, wheel: Path, reuse: Path | None) -> tuple[Path, Path]:
    """Return (python_exe, bin_dir) for an isolated venv with the base wheel installed."""
    if reuse is not None:
        venv = Path(reuse)
        py = venv / "bin" / "python"
        if not py.exists():  # pragma: no cover - windows layout
            py = venv / "Scripts" / "python.exe"
        if not py.exists():
            raise SystemExit(f"--reuse-venv has no python: {venv}")
        return py, py.parent

    venv = work_dir / "venv"
    uv = shutil.which("uv")
    if uv:
        print("[generate] building isolated venv via uv (CPython 3.13)…")
        subprocess.run([uv, "venv", "--python", "3.13", str(venv)], check=True)
        py = venv / "bin" / "python"
        env = dict(os.environ, VIRTUAL_ENV=str(venv))
        subprocess.run([uv, "pip", "install", str(wheel)], check=True, env=env)
    else:
        print("[generate] building isolated venv via stdlib venv…")
        import venv as venv_mod

        venv_mod.EnvBuilder(with_pip=True).create(str(venv))
        py = venv / "bin" / "python"
        if not py.exists():  # pragma: no cover - windows
            py = venv / "Scripts" / "python.exe"
        subprocess.run(
            [str(py), "-m", "pip", "install", "--quiet", str(wheel)], check=True
        )
    return py, py.parent


def run_introspection(venv_python: Path, out_path: Path) -> dict:
    print("[generate] running reflective oracle prober inside venv…")
    subprocess.run(
        [
            str(venv_python),
            str(INTROSPECT),
            "--out",
            str(out_path),
            "--root-import",
            C.CLI_ROOT_IMPORT,
        ],
        check=True,
    )
    with open(out_path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------- #
# Generation: CLI golden capture (real upstream invocations)
# --------------------------------------------------------------------------- #


def _golden_env(golden_home: Path) -> dict:
    """A hermetic environment for golden capture.

    Built from scratch (NOT inherited) with an isolated, empty HOME and no
    ``NOTEBOOKLM_*`` variables, so the real `~/.notebooklm` session/credentials
    are unreachable. Golden capture must only ever produce ``--help`` /
    ``--version`` output and Click parse errors — it must never authenticate,
    reach the network, or run a leaf callback against a real notebook.
    """
    golden_home.mkdir(parents=True, exist_ok=True)
    return {
        "HOME": str(golden_home),
        "USERPROFILE": str(golden_home),  # windows-equivalent
        "XDG_CONFIG_HOME": str(golden_home / ".config"),
        "XDG_DATA_HOME": str(golden_home / ".local" / "share"),
        "APPDATA": str(golden_home / "AppData" / "Roaming"),
        "LOCALAPPDATA": str(golden_home / "AppData" / "Local"),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "NO_COLOR": "1",
        "TERM": "dumb",
        "COLUMNS": "80",
        "LINES": "40",
        "LANG": "C.UTF-8",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    }


def _slug(parts) -> str:
    raw = "_".join(parts)
    return re.sub(r"[^A-Za-z0-9_.+-]", "-", raw)


def _normalize(text: str, redact) -> str:
    text = ANSI_RE.sub("", text)
    text = redact(text)
    lines = [ln.rstrip() for ln in text.splitlines()]
    return "\n".join(lines).rstrip("\n") + "\n"


def _derive_error_cases(nodes: list[dict]) -> list[tuple[str, list[str]]]:
    """Build deterministic, parse-time-only error invocations from the real tree.

    Every case below fails during Click parsing (unknown command / unknown option /
    invalid Choice value / missing required argument) BEFORE any leaf callback runs,
    so none can authenticate, hit the network, or mutate a notebook. We read the real
    option flag strings from the introspected nodes so the goldens exercise genuine
    upstream parse errors.
    """
    by_cmd = {n["command"]: n for n in nodes}

    def first_choice_opt(command):
        node = by_cmd.get(command)
        if not node:
            return None
        # Prefer an --option flag with a Choice; fall back to any choice param.
        for want_option in (True, False):
            for p in node["params"]:
                if not p["type"].get("choices") or not p["opts"]:
                    continue
                is_option = p["param_kind"] == "option" and p["opts"][0].startswith("-")
                if is_option == want_option:
                    return p["opts"][0]
        return None

    cases: list[tuple[str, list[str]]] = [
        ("unknown-command", ["definitely-not-a-real-command"]),
        ("unknown-option", ["ask", "--definitely-not-a-real-option"]),
        ("group-missing-subcommand", ["source"]),
    ]

    # invalid Choice value on two real --options (real flag strings, bogus value).
    # trailing dummy positionals satisfy required args so the ONLY error is the bad
    # choice, which Click rejects during parsing before any callback runs.
    for command, argpath, trailing in (
        ("notebooklm source add", ["source", "add"], ["__dummy_content__"]),
        ("notebooklm generate audio", ["generate", "audio"], []),
    ):
        opt = first_choice_opt(command)
        if opt and opt.startswith("-"):
            cases.append(
                (
                    f"invalid-choice-{argpath[-1]}",
                    argpath + [opt, "__bogus__"] + trailing,
                )
            )

    # missing-required-argument: pick a leaf with a genuinely required positional arg
    for n in sorted(nodes, key=lambda x: x["command"]):
        if n["kind"] != "command":
            continue
        required_args = [
            p for p in n["params"] if p["param_kind"] == "argument" and p["required"]
        ]
        # avoid options that themselves are required (keep it a pure missing-arg case)
        if required_args and not any(
            p["required"] for p in n["params"] if p["param_kind"] == "option"
        ):
            cases.append(("missing-required-argument", n["path"][1:]))
            break

    return cases


def capture_cli_goldens(
    bin_dir: Path, work_dir: Path, nodes: list[dict], redact
) -> dict:
    cli = bin_dir / "notebooklm"
    env = _golden_env(work_dir / "golden_home")
    # Start clean so no stale golden (e.g. from a prior non-hermetic run) survives.
    if C.CLI_GOLDEN_DIR.exists():
        shutil.rmtree(C.CLI_GOLDEN_DIR)
    C.CLI_GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    index = {"help": [], "misc": [], "errors": []}

    def run(args):
        proc = subprocess.run(
            [str(cli)] + args, env=env, capture_output=True, text=True, timeout=60
        )
        return proc

    # 1) --help for every node (root, every group, every leaf) -> proves real upstream.
    for node in sorted(nodes, key=lambda n: n["command"]):
        path_parts = node["path"][1:]  # drop the synthetic 'notebooklm' root token
        proc = run(path_parts + ["--help"])
        fname = _slug(["notebooklm"] + path_parts + ["help"]) + ".txt"
        body = _normalize(proc.stdout + proc.stderr, redact)
        (C.CLI_GOLDEN_DIR / fname).write_text(body, encoding="utf-8")
        index["help"].append(
            {
                "command": node["command"],
                "kind": node["kind"],
                "file": f"cli_golden/{fname}",
                "exit_code": proc.returncode,
                "sha256": hashlib.sha256(body.encode()).hexdigest(),
            }
        )

    # 2) --version (representative success).
    proc = run(["--version"])
    body = _normalize(proc.stdout + proc.stderr, redact)
    (C.CLI_GOLDEN_DIR / "notebooklm--version.txt").write_text(body, encoding="utf-8")
    index["misc"].append(
        {
            "command": "notebooklm --version",
            "file": "cli_golden/notebooklm--version.txt",
            "exit_code": proc.returncode,
            "sha256": hashlib.sha256(body.encode()).hexdigest(),
        }
    )

    # 3) Representative deterministic, PARSE-TIME-ONLY error cases. These fail
    #    during Click parsing before any leaf callback runs, so they never
    #    authenticate, reach the network, or mutate a notebook. Captured under the
    #    hermetic empty-HOME env as a second layer of safety.
    for label, args in _derive_error_cases(nodes):
        proc = run(args)
        # Guard: a representative error case must NOT have run a leaf operation.
        # Parse errors exit non-zero; group-help exits 0/2 but only prints help.
        if proc.returncode == 0 and "Started:" in (proc.stdout + proc.stderr):
            raise SystemExit(
                f"SAFETY ABORT: error case '{label}' ({' '.join(args)}) executed a live "
                f"operation. Golden capture must stay parse-time only."
            )
        fname = "error_" + _slug(["notebooklm"] + args) + ".txt"
        body = _normalize(proc.stdout + proc.stderr, redact)
        (C.CLI_GOLDEN_DIR / fname).write_text(body, encoding="utf-8")
        index["errors"].append(
            {
                "label": label,
                "command": "notebooklm " + " ".join(args),
                "file": f"cli_golden/{fname}",
                "exit_code": proc.returncode,
                "sha256": hashlib.sha256(body.encode()).hexdigest(),
            }
        )

    _write_json(
        C.CLI_GOLDEN_DIR / "_index.json",
        {
            "description": "Normalized golden output captured from real upstream "
            "notebooklm-py==0.7.2 (NO_COLOR, COLUMNS=80). help present "
            "for every one of the 103 Click-tree nodes.",
            "generated_at": _now_iso(),
            "counts": {
                "help": len(index["help"]),
                "misc": len(index["misc"]),
                "errors": len(index["errors"]),
            },
            **index,
        },
    )
    return index


# --------------------------------------------------------------------------- #
# Generation: API goldens
# --------------------------------------------------------------------------- #


def write_api_goldens(api: dict) -> None:
    C.API_GOLDEN_DIR.mkdir(parents=True, exist_ok=True)

    # importable public names (one per line)
    (C.API_GOLDEN_DIR / "imports.txt").write_text(
        "\n".join(api["root_all"]) + "\n", encoding="utf-8"
    )

    # enum value inventory
    _write_json(
        C.API_GOLDEN_DIR / "enums.json",
        {
            "description": "Public enum members and values from notebooklm-py==0.7.2.",
            "enums": api["enum_inventory"],
        },
    )

    # exception hierarchy
    _write_json(
        C.API_GOLDEN_DIR / "exceptions.json",
        {
            "description": "Public exception hierarchy (bases + MRO) from notebooklm-py==0.7.2.",
            "exceptions": api["exception_hierarchy"],
        },
    )

    # curated signatures: client lifecycle + sub-client async methods + key dataclasses
    signatures = {"client": api["client"], "subclients": {}}
    for name, sub in api["subclients"].items():
        signatures["subclients"][name] = {
            "class": sub["class"],
            "module": sub["module"],
            "async_methods": sub["async_methods"],
            "method_signatures": {
                m["name"]: (m.get("signature") or {}).get("text")
                for m in sub["methods"]
                if m.get("signature")
            },
        }
    # AuthTokens + key public dataclasses signatures from the auth/types modules
    for mod_name in ("notebooklm.auth", "notebooklm.types", "notebooklm"):
        for m in api["modules"].get(mod_name, {}).get("members", []):
            if m["kind"] == "dataclass" and m["name"] not in signatures:
                signatures.setdefault("dataclasses", {})[m["name"]] = {
                    "module": mod_name,
                    "init_signature": (m.get("init_signature") or {}).get("text"),
                    "fields": [f["name"] for f in m.get("fields", [])],
                }
    _write_json(
        C.API_GOLDEN_DIR / "signatures.json",
        {
            "description": "Curated public signatures: client lifecycle, sub-client async "
            "methods, and public dataclasses from notebooklm-py==0.7.2.",
            "annotation_identity_deviations": api["annotation_identity_deviations"],
            **signatures,
        },
    )


# --------------------------------------------------------------------------- #
# Generation: RPC fixtures (sanitized, structurally faithful skeletons)
# --------------------------------------------------------------------------- #


def write_rpc_fixtures(rpc_shape: dict) -> None:
    C.RPC_FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    xssi = rpc_shape["xssi_prefixes"][0] if rpc_shape["xssi_prefixes"] else ")]}'"

    _write_json(
        C.RPC_FIXTURE_DIR / "wire_shape.json",
        {
            "description": "batchexecute wire shape extracted from upstream "
            "notebooklm-py==0.7.2 rpc modules. Used to author fake-server "
            "fixtures and (later) to test the bare parser.",
            "xssi_prefix": xssi,
            "batchexecute_markers": rpc_shape["batchexecute_markers"],
            "host_literals": rpc_shape["host_literals"],
            "endpoint_path_literals": rpc_shape["endpoint_path_literals"],
            "rpc_modules": rpc_shape["rpc_modules"],
            "notes": [
                "Responses are XSSI-guarded: the literal prefix must be stripped before JSON parse.",
                "Payloads are batchexecute envelopes; the useful data is a JSON string nested "
                "inside the 'wrb.fr' row and must be parsed a second time.",
                "All fixture data below is SYNTHETIC and SANITIZED; no real account, cookie, "
                "notebook id, or source content is present.",
            ],
        },
    )

    # A representative "list notebooks"-shaped response envelope (synthetic).
    inner = json.dumps(
        [[["fake-notebook-0001", "Phase 0 Synthetic Notebook", [], 1750000000]]],
        separators=(",", ":"),
    )
    envelope = [["wrb.fr", "rpcids-synthetic", inner, None, None, None, "generic"]]
    response_text = f"{xssi}\n\n" + json.dumps(envelope, separators=(",", ":")) + "\n"
    (C.RPC_FIXTURE_DIR / "list_notebooks.response.txt").write_text(
        response_text, encoding="utf-8"
    )

    # Matching synthetic request body (batchexecute form-encoded f.req).
    freq = json.dumps(
        [[["wXbhsf", json.dumps([None, 1], separators=(",", ":")), None, "generic"]]],
        separators=(",", ":"),
    )
    request_body = "f.req=" + urllib.parse.quote(freq) + "&at=SYNTHETIC_XSRF_TOKEN&"
    (C.RPC_FIXTURE_DIR / "list_notebooks.request.txt").write_text(
        request_body + "\n", encoding="utf-8"
    )

    # A synthetic streaming chat chunk fixture (long-poll style).
    chat_inner = json.dumps(
        ["Phase 0 synthetic answer chunk.", [], []], separators=(",", ":")
    )
    chat_chunk = (
        f"{xssi}\n\n"
        + json.dumps(
            [["wrb.fr", "chat-rpc", chat_inner, None, None, None, "generic"]],
            separators=(",", ":"),
        )
        + "\n"
    )
    (C.RPC_FIXTURE_DIR / "chat_ask.streaming.response.txt").write_text(
        chat_chunk, encoding="utf-8"
    )

    # Matching synthetic chat request (batchexecute form-encoded f.req), pairing
    # with chat_ask.streaming.response.txt on the same synthetic 'chat-rpc' id.
    chat_freq = json.dumps(
        [
            [
                [
                    "chat-rpc",
                    json.dumps(
                        ["fake-notebook-0001", "Phase 0 synthetic question."],
                        separators=(",", ":"),
                    ),
                    None,
                    "generic",
                ]
            ]
        ],
        separators=(",", ":"),
    )
    chat_request_body = (
        "f.req=" + urllib.parse.quote(chat_freq) + "&at=SYNTHETIC_XSRF_TOKEN&"
    )
    (C.RPC_FIXTURE_DIR / "chat_ask.request.txt").write_text(
        chat_request_body + "\n", encoding="utf-8"
    )

    (C.RPC_FIXTURE_DIR / "README.md").write_text(
        "# RPC fixtures (Phase 0 skeletons)\n"
        "\n"
        "Synthetic, sanitized batchexecute fixtures mirroring the upstream\n"
        "`notebooklm-py==0.7.2` wire shape (see `wire_shape.json`). They are "
        "structurally\n"
        "faithful — XSSI prefix (`)]}'`), `wrb.fr` envelope, JSON-in-string payloads — "
        "so the\n"
        "request/response parser tests in `tests/fake_server/` exercise real decoding\n"
        "behavior without any live NotebookLM call or private data.\n"
        "\n"
        "## Fixture pairs\n"
        "\n"
        "| Request | Response | Shape |\n"
        "| --- | --- | --- |\n"
        "| `list_notebooks.request.txt` | `list_notebooks.response.txt` | unary "
        "batchexecute (list notebooks) |\n"
        "| `chat_ask.request.txt` | `chat_ask.streaming.response.txt` | streamed chat "
        "answer chunk |\n"
        "\n"
        "Each `*.request.txt` is a form-encoded `f.req=...&at=...` batchexecute body; "
        "each\n"
        "`*.response.txt` begins with the `)]}'` XSSI guard and carries its data as a "
        "JSON\n"
        "string nested inside a `wrb.fr` row (a second JSON parse).\n"
        "\n"
        "**No real account, cookie, notebook id, token, or source content appears in "
        "any\n"
        "fixture.** All identifiers are obvious placeholders (`fake-notebook-0001`,\n"
        "`SYNTHETIC_XSRF_TOKEN`, `chat-rpc`).\n",
        encoding="utf-8",
    )


# --------------------------------------------------------------------------- #
# Generation: assemble compat/*.json + parity_matrix.md
# --------------------------------------------------------------------------- #


def assemble_oracle(bundle: dict, provenance: dict) -> dict:
    counts = bundle["cli_tree"]["counts"]
    api = bundle["python_api"]
    return {
        "schema_version": C.ORACLE_SCHEMA_VERSION,
        "artifact": "notebooklm-bare phase 0 compatibility oracle",
        "generated_at": _now_iso(),
        "plan_sha256": C.PLAN_SHA256,
        "target": {
            "project": C.TARGET_PROJECT,
            "version": C.TARGET_VERSION,
            "requirement": C.TARGET_REQUIREMENT,
            "requires_python": bundle["dependency_meta"]["requires_python"],
            "cli_entry_point": C.CLI_ENTRY_POINT,
        },
        "provenance": provenance,
        "parity_profile": {
            "selected_extras": list(C.PARITY_PROFILE_EXTRAS),
            "all_upstream_extras": list(C.ALL_UPSTREAM_EXTRAS),
            "note": "base package plus documented browser/cookies/markdown feature "
            "surfaces; dev/all are tooling-only and not runtime parity targets.",
        },
        "matrix": {
            "python": list(C.PYTHON_MATRIX),
            "os": list(C.OS_MATRIX),
            "interactive_login_browsers": list(C.INTERACTIVE_LOGIN_BROWSERS),
        },
        "cli_surface_summary": {
            "nodes": counts["nodes"],
            "groups": counts["groups"],
            "leaves": counts["leaves"],
            "group_paths": bundle["cli_tree"]["groups"],
        },
        "python_api_summary": {
            "public_modules": sorted(api["modules"].keys()),
            "root_all_count": api["root_all_count"],
            "subclients": sorted(api["subclients"].keys()),
            "exception_count": len(api["exception_hierarchy"]),
            "enum_count": len(api["enum_inventory"]),
            "annotation_identity_deviation_count": len(
                api["annotation_identity_deviations"]
            ),
            "is_async_context_manager": api["client"].get("is_async_context_manager"),
        },
        "rpc_shape": bundle["rpc_shape"],
        "inspection": {
            "python_version": bundle["meta"]["inspection_python_version"],
            "implementation": bundle["meta"]["implementation"],
        },
        "artifacts": {
            "oracle": "compat/notebooklm_py_0_7_2_oracle.json",
            "cli_surface": "compat/cli_surface.json",
            "python_api_surface": "compat/python_api_surface.json",
            "auth_matrix": "compat/auth_matrix.json",
            "dependency_graph": "compat/dependency_graph.json",
            "parity_matrix": "compat/parity_matrix.md",
            "cli_golden": "compat/cli_golden/",
            "api_golden": "compat/api_golden/",
            "rpc_fixtures": "compat/rpc_fixtures/",
        },
    }


def assemble_cli_surface(bundle: dict, golden_index: dict, provenance: dict) -> dict:
    tree = bundle["cli_tree"]
    return {
        "schema_version": C.ORACLE_SCHEMA_VERSION,
        "target": C.TARGET_REQUIREMENT,
        "generated_at": _now_iso(),
        "provenance": provenance,
        "root_class": tree["root_class"],
        "counts": tree["counts"],
        "expected_counts": {
            "nodes": C.EXPECTED_CLI_NODES,
            "groups": C.EXPECTED_CLI_GROUPS,
            "leaves": C.EXPECTED_CLI_LEAVES,
        },
        "groups": tree["groups"],
        "leaf_commands": tree["leaf_commands"],
        "choice_options": tree["choice_options"],
        "golden_help_files": len(golden_index.get("help", [])),
        "nodes": tree["nodes"],
    }


def assemble_python_api(bundle: dict, provenance: dict) -> dict:
    api = dict(bundle["python_api"])
    api["schema_version"] = C.ORACLE_SCHEMA_VERSION
    api["target"] = C.TARGET_REQUIREMENT
    api["generated_at"] = _now_iso()
    api["provenance"] = provenance
    return api


def assemble_auth_matrix(bundle: dict, provenance: dict) -> dict:
    src = bundle["auth_sources"]
    os_rows = list(C.OS_MATRIX)

    # Interactive-login matrix: documented browsers x OS x documented flows.
    login_flows = ["login", "refresh", "status", "logout", "doctor"]
    interactive = []
    for browser in src["interactive_login_browsers"]:
        for osname in os_rows:
            for flow in login_flows:
                interactive.append(
                    {
                        "matrix": "interactive_login",
                        "browser": browser,
                        "os": osname,
                        "flow": flow,
                        "parity_state": C.PHASE0_INITIAL_STATE,
                        "differential_basis": "upstream interactive-login behavior vs bare",
                    }
                )

    # Browser-cookie import matrix: every documented selector x OS x cookie paths.
    cookie_browsers = sorted(
        set(
            src["chromium_family_cookie_browsers"]
            + (["firefox"] if src["firefox_cookie_support"] else [])
            + (["safari"] if src["safari_cookie_support"] else [])
        )
    )
    cookie_paths = ["import", "profile-select", "account-select", "inspect", "refresh"]
    cookie_import = []
    for browser in cookie_browsers:
        for osname in os_rows:
            if (browser, osname) in C.AUTH_COOKIE_PROFILE_EXCLUSIONS:
                continue
            for path in cookie_paths:
                if (browser, osname, path) in C.AUTH_COOKIE_PATH_PROFILE_EXCLUSIONS:
                    continue
                cookie_import.append(
                    {
                        "matrix": "browser_cookie_import",
                        "browser": browser,
                        "os": osname,
                        "path": path,
                        "parity_state": C.PHASE0_INITIAL_STATE,
                        "differential_basis": "upstream rookiepy-backed cookie import vs bare",
                    }
                )

    return {
        "schema_version": C.ORACLE_SCHEMA_VERSION,
        "target": C.TARGET_REQUIREMENT,
        "generated_at": _now_iso(),
        "provenance": provenance,
        "closure_states": list(C.PARITY_STATES),
        "note": "Two separate matrices per the plan: interactive login is fixed to the "
        "upstream chromium|chrome|msedge set; browser-cookie import is the "
        "generated selector matrix after explicit profile exclusions. Included "
        "rows start 'open' in Phase 0; excluded rows are outside the claimed "
        "compatibility profile and are never counted as passes.",
        "profile_exclusions": [
            {"browser": browser, "os": osname, "reason": "unsupported_by_project_scope"}
            for browser, osname in C.AUTH_COOKIE_PROFILE_EXCLUSIONS
        ]
        + [
            {
                "browser": browser,
                "os": osname,
                "path": path,
                "reason": C.AUTH_COOKIE_PATH_PROFILE_EXCLUSION_REASONS[
                    (browser, osname)
                ],
            }
            for browser, osname, path in C.AUTH_COOKIE_PATH_PROFILE_EXCLUSIONS
        ],
        "sources_from_upstream": {
            "interactive_login_browsers": src["interactive_login_browsers"],
            "chromium_family_cookie_browsers": src["chromium_family_cookie_browsers"],
            "firefox_cookie_support": src["firefox_cookie_support"],
            "safari_cookie_support": src["safari_cookie_support"],
            "rookiepy_backends_referenced": src["rookiepy_backends_referenced"],
            "os_cookie_store_path_keys": src["os_cookie_store_path_keys"],
            "login_command_params": src["login_command_params"],
            "auth_session_profile_commands": src["auth_session_profile_commands"],
        },
        "counts": {
            "interactive_login_rows": len(interactive),
            "browser_cookie_import_rows": len(cookie_import),
            "cookie_browsers": cookie_browsers,
            "os_rows": os_rows,
        },
        "interactive_login_matrix": interactive,
        "browser_cookie_import_matrix": cookie_import,
    }


def assemble_dependency_graph(bundle: dict, provenance: dict) -> dict:
    dep = bundle["dependency_meta"]

    # Where each third-party upstream dependency influences behavior (plan task 6).
    influence = {
        "click": {
            "extra": None,
            "role": "CLI framework: command tree, options, prompts, exit codes.",
            "bare_replacement": "argparse-based CLI parity surface (stdlib).",
        },
        "httpx": {
            "extra": None,
            "role": "Async HTTP transport, Timeout/Cookies types, connection limits.",
            "bare_replacement": "stdlib urllib/http.client + selector/executor async boundary.",
            "annotation_identity_deviations": bundle["python_api"][
                "annotation_identity_deviations"
            ],
        },
        "rich": {
            "extra": None,
            "role": "Human-facing console rendering (tables, panels, progress).",
            "bare_replacement": "plain stdlib output writer with JSON mode.",
        },
        "filelock": {
            "extra": None,
            "role": "Cross-process lock for profile/session/cookie files.",
            "bare_replacement": "stdlib os.open(O_CREAT|O_EXCL)/fcntl/msvcrt lock helper.",
        },
        "rookiepy": {
            "extra": "cookies",
            "role": "Browser cookie-store extraction (chromium-family, firefox, safari).",
            "bare_replacement": "stdlib sqlite3 + OS credential facilities (Keychain/DPAPI/secret).",
            "lazy_import_sites": [
                "notebooklm/cli/_chromium_profiles.py",
                "notebooklm/cli/services/login/browser_accounts.py",
            ],
        },
        "playwright": {
            "extra": "browser",
            "role": "Interactive browser login automation (chromium|chrome|msedge).",
            "bare_replacement": "documented bare-CDP / OS browser launch helper.",
            "lazy_import_sites": ["notebooklm/cli/services/playwright_login.py"],
        },
        "markdownify": {
            "extra": "markdown",
            "role": "HTML->Markdown conversion for source content extraction.",
            "bare_replacement": "stdlib html.parser-based converter.",
            "lazy_import_sites": ["notebooklm/_source/content.py"],
        },
    }

    return {
        "schema_version": C.ORACLE_SCHEMA_VERSION,
        "target": C.TARGET_REQUIREMENT,
        "generated_at": _now_iso(),
        "provenance": provenance,
        "requires_python": dep["requires_python"],
        "base_runtime_requirements": dep["base_runtime_requirements"],
        "extras": dep["extras"],
        "installed_base_versions": dep["installed_base_versions"],
        "requires_dist_raw": dep["requires_dist_raw"],
        "pypi_to_import_name": C.PYPI_TO_IMPORT_NAME,
        "denylisted_runtime_imports": list(C.DENYLISTED_RUNTIME_IMPORTS),
        "behavioral_influence": influence,
        "rookiepy_backends_referenced": bundle["auth_sources"][
            "rookiepy_backends_referenced"
        ],
    }


def write_parity_matrix(bundle: dict, auth_matrix: dict) -> None:
    counts = bundle["cli_tree"]["counts"]
    api = bundle["python_api"]
    n_login = auth_matrix["counts"]["interactive_login_rows"]
    n_cookie = auth_matrix["counts"]["browser_cookie_import_rows"]

    lines = []
    w = lines.append
    w("# NotebookLM Bare — Parity Matrix")
    w("")
    w(f"**Target oracle:** `{C.TARGET_REQUIREMENT}`  ")
    w(f"**Wheel SHA-256:** `{C.WHEEL_SHA256}`  ")
    w(f"**Source commit:** `{C.SOURCE_COMMIT}`  ")
    w(f"**Generated:** {_now_iso()}")
    w("")
    w("## Closure states (pass-only, JMC-NLB-011)")
    w("")
    w(
        "- `pass` — differential upstream-vs-bare result matches for the same sanitized "
        "fixture or disposable live account."
    )
    w("- `open` — not yet proven. **Not** a success state.")
    w(
        "- `blocked` — cannot currently be proven (tier/quota/platform). **Not** a success state."
    )
    w("")
    w(
        "No row is recorded `pass` without a real differential result. The generator "
        "seeds rows as `open`; later phases may promote a row to `pass` or `blocked` "
        "only when row-specific evidence is committed. Per-leaf *success*-path parity "
        "is therefore proven by that harness, never shown green before it is real."
    )
    w("")
    w("## Category matrix")
    w("")
    w("| Category | Scope (from pinned upstream) | Differential basis | State |")
    w("| --- | --- | --- | --- |")
    w(
        f"| cli | {counts['leaves']} leaf commands across {counts['nodes']} Click-tree nodes "
        f"({counts['groups']} groups) | upstream Click `--help`/error goldens vs bare CLI "
        f"| {C.PHASE0_INITIAL_STATE} |"
    )
    w(
        f"| api | {api['root_all_count']} public names, {len(api['subclients'])} sub-clients, "
        f"{len(api['exception_hierarchy'])} exceptions, {len(api['enum_inventory'])} enums "
        f"| upstream import/signature/enum/exception goldens vs bare | {C.PHASE0_INITIAL_STATE} |"
    )
    w(
        f"| auth | interactive login ({n_login} rows) + browser-cookie import ({n_cookie} rows) "
        f"| upstream auth matrix vs bare | {C.PHASE0_INITIAL_STATE} |"
    )
    w(
        f"| rpc | batchexecute encode/decode + streaming parse ({len(bundle['rpc_shape']['rpc_modules'])} "
        f"upstream rpc modules) | fake-server fixture contract | {C.PHASE0_INITIAL_STATE} |"
    )
    w(
        f"| offline | `python -I -S` import-origin audit + denylist | isolated audit | {C.PHASE0_INITIAL_STATE} |"
    )
    w(
        f"| self-test | bundled offline self-test against sanitized fixtures | offline fixture run "
        f"| {C.PHASE0_INITIAL_STATE} |"
    )
    w("")
    w("## CLI leaf-command rows (seeded `open` by generator)")
    w("")
    w("| Leaf command | State |")
    w("| --- | --- |")
    for leaf in bundle["cli_tree"]["leaf_commands"]:
        w(f"| `{leaf}` | {C.PHASE0_INITIAL_STATE} |")
    w("")
    w("## Python API sub-client rows")
    w("")
    w("| Sub-client | Class | State |")
    w("| --- | --- | --- |")
    for name, sub in sorted(bundle["python_api"]["subclients"].items()):
        w(f"| `client.{name}` | `{sub['class']}` | {C.PHASE0_INITIAL_STATE} |")
    w("")
    C.PARITY_MATRIX_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# Remote verification
# --------------------------------------------------------------------------- #


def verify_remote_commit(timeout: float = 12.0) -> dict:
    url = f"https://api.github.com/repos/{C.SOURCE_REPO}/commits/{C.SOURCE_COMMIT}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "notebooklm-bare-phase0-oracle",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            data = json.loads(resp.read().decode("utf-8"))
            sha = data.get("sha")
            return {
                "checked": True,
                "exists": sha == C.SOURCE_COMMIT,
                "http_status": status,
                "returned_sha": sha,
                "checked_at": _now_iso(),
            }
    except urllib.error.HTTPError as exc:
        return {
            "checked": True,
            "exists": False,
            "http_status": exc.code,
            "checked_at": _now_iso(),
        }
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {
            "checked": False,
            "exists": None,
            "error": str(exc),
            "checked_at": _now_iso(),
        }


# --------------------------------------------------------------------------- #
# Mode: generate
# --------------------------------------------------------------------------- #


def cmd_generate(args) -> int:
    keep = args.keep or bool(args.work_dir)
    work_dir = (
        Path(args.work_dir)
        if args.work_dir
        else Path(tempfile.mkdtemp(prefix="nlb-phase0-gen."))
    )
    redact = _redactor(work_dir)
    print(f"[generate] work dir: {work_dir} (keep={keep})")
    try:
        wheel = ensure_wheel(work_dir, Path(args.wheel) if args.wheel else None)
        digest, ok = verify_wheel(wheel)
        if not ok:
            raise SystemExit(
                f"WHEEL SHA MISMATCH: got {digest}, expected {C.WHEEL_SHA256}. Refusing to generate."
            )
        print(f"[generate] wheel SHA-256 verified: {digest}")

        venv_python, bin_dir = build_venv(
            work_dir, wheel, Path(args.reuse_venv) if args.reuse_venv else None
        )

        if args.bundle:
            with open(args.bundle, "r", encoding="utf-8") as fh:
                bundle = json.load(fh)
        else:
            bundle = run_introspection(
                venv_python, work_dir / "introspection_bundle.json"
            )

        # Hard gate: counts must match the locked target before we assemble anything.
        counts = bundle["cli_tree"]["counts"]
        if (counts["nodes"], counts["leaves"]) != (
            C.EXPECTED_CLI_NODES,
            C.EXPECTED_CLI_LEAVES,
        ):
            raise SystemExit(
                f"CLI COUNT MISMATCH: nodes={counts['nodes']} leaves={counts['leaves']} "
                f"expected {C.EXPECTED_CLI_NODES}/{C.EXPECTED_CLI_LEAVES}. Plan-change blocker."
            )

        remote = (
            verify_remote_commit()
            if not args.skip_remote
            else {"checked": False, "exists": None}
        )

        provenance = {
            "wheel_filename": C.WHEEL_FILENAME,
            "wheel_sha256": digest,
            "wheel_sha256_expected": C.WHEEL_SHA256,
            "wheel_sha256_verified": ok,
            "source_repo": C.SOURCE_REPO,
            "source_commit": C.SOURCE_COMMIT,
            "source_commit_remote_check": remote,
            "download_command": f"pip download --no-deps --only-binary=:all: {C.TARGET_REQUIREMENT}",
            "isolation": "disposable venv; no global install; base deps only "
            "(extras imported lazily by upstream, so unneeded for surface lock).",
        }

        print("[generate] capturing CLI goldens (hermetic empty-HOME, real upstream)…")
        golden_index = capture_cli_goldens(
            bin_dir, work_dir, bundle["cli_tree"]["nodes"], redact
        )

        print("[generate] assembling compat/*.json …")
        _write_json(C.ORACLE_JSON, assemble_oracle(bundle, provenance))
        _write_json(
            C.CLI_SURFACE_JSON, assemble_cli_surface(bundle, golden_index, provenance)
        )
        _write_json(C.PYTHON_API_SURFACE_JSON, assemble_python_api(bundle, provenance))
        auth_matrix = assemble_auth_matrix(bundle, provenance)
        _write_json(C.AUTH_MATRIX_JSON, auth_matrix)
        _write_json(
            C.DEPENDENCY_GRAPH_JSON, assemble_dependency_graph(bundle, provenance)
        )

        print("[generate] writing api goldens, rpc fixtures, parity matrix …")
        write_api_goldens(bundle["python_api"])
        write_rpc_fixtures(bundle["rpc_shape"])
        write_parity_matrix(bundle, auth_matrix)

        print("[generate] done.")
    finally:
        if not keep and work_dir.exists():
            shutil.rmtree(work_dir, ignore_errors=True)
            print(f"[generate] removed work dir {work_dir}")

    print("\n[generate] validating freshly written artifacts…")
    return cmd_check(args)


# --------------------------------------------------------------------------- #
# Mode: check (offline validation)
# --------------------------------------------------------------------------- #


def _load_json(path: Path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def parse_parity_states(md_path: Path) -> list[str]:
    states = []
    in_state_table = False
    for line in md_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("|"):
            in_state_table = False
            continue
        if set(line) <= set("| -"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells:
            continue
        last = cells[-1].strip().strip("`")
        if last == "State":
            in_state_table = True
            continue
        if in_state_table:
            states.append(last)
    return states


def validate_all() -> list[tuple[str, bool, str]]:
    """Return [(check_name, ok, detail)] for every Phase 0 exit-gate assertion."""
    results: list[tuple[str, bool, str]] = []

    def check(name, ok, detail=""):
        results.append((name, bool(ok), detail))

    # 1) required artifacts exist and parse
    parsed = {}
    for path in C.REQUIRED_COMPAT_JSON + (C.PARITY_MATRIX_MD,):
        exists = path.exists()
        rel = path.relative_to(C.REPO_ROOT)
        if not exists:
            check(f"exists:{rel}", False, "missing")
            continue
        if path.suffix == ".json":
            try:
                parsed[path.name] = _load_json(path)
                check(f"parse:{rel}", True)
            except Exception as exc:
                check(f"parse:{rel}", False, str(exc))
        else:
            check(f"exists:{rel}", True)

    oracle = parsed.get("notebooklm_py_0_7_2_oracle.json", {})
    cli = parsed.get("cli_surface.json", {})
    api = parsed.get("python_api_surface.json", {})
    auth = parsed.get("auth_matrix.json", {})
    dep = parsed.get("dependency_graph.json", {})

    # 2) target is exactly notebooklm-py==0.7.2
    check(
        "oracle.target.requirement == notebooklm-py==0.7.2",
        oracle.get("target", {}).get("requirement") == C.TARGET_REQUIREMENT,
        str(oracle.get("target", {}).get("requirement")),
    )
    check(
        "oracle.target.version == 0.7.2",
        oracle.get("target", {}).get("version") == C.TARGET_VERSION,
        str(oracle.get("target", {}).get("version")),
    )
    check(
        "oracle.requires_python == >=3.10",
        oracle.get("target", {}).get("requires_python") == C.REQUIRES_PYTHON,
        str(oracle.get("target", {}).get("requires_python")),
    )

    # 3) wheel hash matches
    prov = oracle.get("provenance", {})
    check(
        "oracle.wheel_sha256 matches pin",
        prov.get("wheel_sha256") == C.WHEEL_SHA256
        and prov.get("wheel_sha256_verified") is True,
        str(prov.get("wheel_sha256")),
    )

    # 4) source commit matches
    check(
        "oracle.source_commit matches pin",
        prov.get("source_commit") == C.SOURCE_COMMIT,
        str(prov.get("source_commit")),
    )

    # 5) plan SHA matches
    check(
        "oracle.plan_sha256 matches pin",
        oracle.get("plan_sha256") == C.PLAN_SHA256,
        str(oracle.get("plan_sha256")),
    )

    # 6) CLI manifest reports 103 nodes / 90 leaves
    counts = cli.get("counts", {})
    check(
        "cli.counts.nodes == 103",
        counts.get("nodes") == C.EXPECTED_CLI_NODES,
        str(counts.get("nodes")),
    )
    check(
        "cli.counts.leaves == 90",
        counts.get("leaves") == C.EXPECTED_CLI_LEAVES,
        str(counts.get("leaves")),
    )
    check(
        "cli.counts.groups == 13",
        counts.get("groups") == C.EXPECTED_CLI_GROUPS,
        str(counts.get("groups")),
    )
    check(
        "cli.nodes length == 103",
        len(cli.get("nodes", [])) == C.EXPECTED_CLI_NODES,
        str(len(cli.get("nodes", []))),
    )
    check(
        "cli.leaf_commands length == 90",
        len(cli.get("leaf_commands", [])) == C.EXPECTED_CLI_LEAVES,
        str(len(cli.get("leaf_commands", []))),
    )
    check(
        "oracle.cli_summary nodes/leaves == 103/90",
        oracle.get("cli_surface_summary", {}).get("nodes") == C.EXPECTED_CLI_NODES
        and oracle.get("cli_surface_summary", {}).get("leaves")
        == C.EXPECTED_CLI_LEAVES,
        str(oracle.get("cli_surface_summary", {})),
    )

    # 7) Python API surface is real and complete-ish
    check(
        "api.root_all_count >= 100",
        api.get("root_all_count", 0) >= 100,
        str(api.get("root_all_count")),
    )
    check(
        "api.subclients == 9",
        len(api.get("subclients", {})) == 9,
        str(sorted(api.get("subclients", {}))),
    )
    check(
        "api.exception_hierarchy non-empty",
        len(api.get("exception_hierarchy", [])) >= 40,
        str(len(api.get("exception_hierarchy", []))),
    )
    check(
        "api.annotation_identity_deviations recorded",
        len(api.get("annotation_identity_deviations", [])) >= 1,
        str(len(api.get("annotation_identity_deviations", []))),
    )
    check(
        "api.client async context manager",
        api.get("client", {}).get("is_async_context_manager") is True,
    )

    # 8) auth matrices present with the locked interactive-login browser set
    src = auth.get("sources_from_upstream", {})
    check(
        "auth.interactive_login_browsers == {chromium,chrome,msedge}",
        set(src.get("interactive_login_browsers", []))
        == set(C.INTERACTIVE_LOGIN_BROWSERS),
        str(src.get("interactive_login_browsers")),
    )
    check(
        "auth.interactive_login_matrix non-empty",
        len(auth.get("interactive_login_matrix", [])) > 0,
    )
    check(
        "auth.browser_cookie_import_matrix non-empty",
        len(auth.get("browser_cookie_import_matrix", [])) > 0,
    )
    auth_states = {
        r.get("parity_state")
        for r in auth.get("interactive_login_matrix", [])
        + auth.get("browser_cookie_import_matrix", [])
    }
    check(
        "auth rows use only valid states",
        auth_states.issubset(set(C.PARITY_STATES)),
        str(sorted(auth_states)),
    )
    check(
        "auth matrix contains rows",
        bool(auth_states),
        str(sorted(auth_states)),
    )

    # 9) dependency graph identifies third-party influence
    infl = dep.get("behavioral_influence", {})
    check(
        "dep graph covers httpx/click/rich/filelock/rookiepy/playwright/markdownify",
        all(
            k in infl
            for k in (
                "httpx",
                "click",
                "rich",
                "filelock",
                "rookiepy",
                "playwright",
                "markdownify",
            )
        ),
        str(sorted(infl)),
    )
    check(
        "dep graph base requirements present",
        len(dep.get("base_runtime_requirements", [])) == 4,
        str(dep.get("base_runtime_requirements")),
    )

    # 10) parity matrix uses only pass/open/blocked and carries category rows.
    # Individual rows start conservative but may later move to pass/blocked only
    # when row-specific evidence is committed.
    if C.PARITY_MATRIX_MD.exists():
        states = parse_parity_states(C.PARITY_MATRIX_MD)
        check("parity_matrix has rows", len(states) > 0, str(len(states)))
        check(
            "parity_matrix states subset of {pass,open,blocked}",
            set(states).issubset(set(C.PARITY_STATES)),
            str(sorted(set(states))),
        )
        matrix_text = C.PARITY_MATRIX_MD.read_text(encoding="utf-8")
        for cat in C.PARITY_CATEGORIES:
            present = f"| {cat} |" in matrix_text
            check(f"parity_matrix has category '{cat}'", present)

    # 11) denylist audit clean over project code
    violations = import_origin_audit.audit()
    check(
        "import-origin denylist audit clean",
        len(violations) == 0,
        "; ".join(
            f"{os.path.relpath(v['file'], C.REPO_ROOT)}:{v['line']}:{v.get('module')}"
            for v in violations
        ),
    )

    # 12) golden corpus exists (real upstream evidence)
    help_dir = C.CLI_GOLDEN_DIR
    n_help = len(list(help_dir.glob("*help.txt"))) if help_dir.exists() else 0
    check(
        "cli_golden has help for every node (>=103 files)",
        n_help >= C.EXPECTED_CLI_NODES,
        str(n_help),
    )
    check("api_golden imports.txt present", (C.API_GOLDEN_DIR / "imports.txt").exists())
    check(
        "rpc_fixtures wire_shape present",
        (C.RPC_FIXTURE_DIR / "wire_shape.json").exists(),
    )

    # 13) fake-server request/response fixture pairs (#10): each request carries a
    #     batchexecute f.req body; each response carries the XSSI guard + wrb.fr
    #     JSON-in-string envelope. Absence of any pair re-opens the #10 blocker.
    fx = C.RPC_FIXTURE_DIR
    xssi_prefix = ")]}'"
    ws_path = fx / "wire_shape.json"
    if ws_path.exists():
        try:
            xssi_prefix = _load_json(ws_path).get("xssi_prefix", xssi_prefix)
        except Exception:  # pragma: no cover - defensive
            pass
    for reqf, respf in (
        ("list_notebooks.request.txt", "list_notebooks.response.txt"),
        ("chat_ask.request.txt", "chat_ask.streaming.response.txt"),
    ):
        rp, sp = fx / reqf, fx / respf
        req_ok = rp.exists() and "f.req=" in rp.read_text(encoding="utf-8")
        check(
            f"rpc fixture request batchexecute body: {reqf}",
            req_ok,
            "missing or no f.req=",
        )
        if sp.exists():
            body = sp.read_text(encoding="utf-8")
            resp_ok = body.startswith(xssi_prefix) and "wrb.fr" in body
        else:
            resp_ok = False
        check(
            f"rpc fixture response XSSI+wrb.fr envelope: {respf}",
            resp_ok,
            "missing or no XSSI/wrb.fr envelope",
        )

    # 14) Differential exit-gate prong: the harness that runs the upstream probes
    #     and records the strict-xfail bare side must be present and discoverable.
    diff_dir = C.TESTS_DIR / "differential"
    fake_dir = C.TESTS_DIR / "fake_server"
    check(
        "differential harness present (tests/differential/test_*.py)",
        diff_dir.is_dir() and any(diff_dir.glob("test_*.py")),
        str(diff_dir),
    )
    check(
        "fake-server parser scaffold present (tests/fake_server/test_*.py)",
        fake_dir.is_dir() and any(fake_dir.glob("test_*.py")),
        str(fake_dir),
    )

    # 15) Auth matrix row totals are locked for the selected compatibility profile.
    acounts = auth.get("counts", {})
    check(
        "auth.interactive_login_rows == 45",
        acounts.get("interactive_login_rows") == 45,
        str(acounts.get("interactive_login_rows")),
    )
    check(
        "auth.browser_cookie_import_rows == 101",
        acounts.get("browser_cookie_import_rows") == 101,
        str(acounts.get("browser_cookie_import_rows")),
    )
    check(
        "auth has both interactive_login + browser_cookie_import blocks",
        bool(auth.get("interactive_login_matrix"))
        and bool(auth.get("browser_cookie_import_matrix")),
    )

    return results


def cmd_check(args) -> int:
    results = validate_all()
    failed = [r for r in results if not r[1]]
    width = max((len(r[0]) for r in results), default=0)
    for name, ok, detail in results:
        mark = "PASS" if ok else "FAIL"
        line = f"  [{mark}] {name.ljust(width)}"
        if not ok and detail:
            line += f"  -> {detail}"
        print(line)
    print(f"\n[check] {len(results) - len(failed)}/{len(results)} checks passed.")
    if failed:
        print(f"[check] {len(failed)} FAILED — Phase 0 exit gate NOT satisfied.")
        return 1
    print("[check] Phase 0 oracle artifacts validated — exit gate checks satisfied.")
    return 0


def cmd_verify_remote(args) -> int:
    result = verify_remote_commit()
    print(json.dumps(result, indent=2))
    if not result.get("checked"):
        print("[verify-remote] could not reach GitHub (offline?). Non-fatal.")
        return 0
    if result.get("exists"):
        print(
            f"[verify-remote] confirmed commit {C.SOURCE_COMMIT} exists in {C.SOURCE_REPO}."
        )
        return 0
    print(f"[verify-remote] commit {C.SOURCE_COMMIT} NOT confirmed.")
    return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check", action="store_true", help="Offline validation (default)"
    )
    mode.add_argument(
        "--generate",
        action="store_true",
        help="Regenerate compat artifacts from pinned upstream",
    )
    mode.add_argument(
        "--verify-remote", action="store_true", help="Confirm source commit on GitHub"
    )
    parser.add_argument("--work-dir", help="Reuse/keep this work dir for --generate")
    parser.add_argument("--wheel", help="Path to a pre-downloaded pinned wheel")
    parser.add_argument(
        "--reuse-venv",
        help="Path to an existing isolated venv with the wheel installed",
    )
    parser.add_argument(
        "--bundle", help="Path to a pre-computed introspection bundle JSON"
    )
    parser.add_argument(
        "--keep", action="store_true", help="Keep the work dir after --generate"
    )
    parser.add_argument(
        "--skip-remote",
        action="store_true",
        help="Skip GitHub commit check during --generate",
    )
    args = parser.parse_args(argv)

    if args.generate:
        return cmd_generate(args)
    if args.verify_remote:
        return cmd_verify_remote(args)
    return cmd_check(args)


if __name__ == "__main__":
    raise SystemExit(main())
