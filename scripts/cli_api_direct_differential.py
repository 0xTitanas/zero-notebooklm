#!/usr/bin/env python3
"""Phase 17 CLI/API direct differential audit.

Compares committed golden artifacts against isolated child-process bare CLI
output and in-process bare API output without live upstream access, network,
credentials, or browser stores.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import inspect
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SCHEMA_VERSION = "cli_api_direct_differential/1"
TARGET = "notebooklm-py==0.7.2"


def _compat_path(repo_root: Path, name: str) -> Path:
    return repo_root / "compat" / name


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_text(text: str) -> str:
    """Normalize only low-risk text differences allowed for this gate."""

    return "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").splitlines())


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _excerpt(text: str, limit: int = 240) -> str:
    return text[:limit]


def _run_cli_helps(repo_root: Path, commands: list[str]) -> dict[str, tuple[int, str]]:
    """Run bare CLI help probes in one clean child process.

    The parent build_report path must not call Path.home(). The bare CLI may need
    normal HOME/TMP environment semantics internally, so isolate that in a temp
    subprocess and report only normalized output, never temp paths.
    """

    code = (
        "import contextlib, io, json, sys; "
        "import notebooklm.cli as cli; "
        "results = []; "
        "commands = json.load(sys.stdin); "
        "\nfor command in commands:\n"
        "    args = command.split()[1:] + ['--help']\n"
        "    stdout = io.StringIO(); stderr = io.StringIO(); rc = 0\n"
        "    try:\n"
        "        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):\n"
        "            result = cli.console(args)\n"
        "        rc = 0 if result is None else int(result)\n"
        "    except SystemExit as exc:\n"
        "        try:\n"
        "            rc = int(exc.code or 0)\n"
        "        except (TypeError, ValueError):\n"
        "            rc = 1\n"
        "    except BaseException as exc:\n"
        "        rc = 1\n"
        "        print(f'{type(exc).__name__}: {exc}', file=stderr)\n"
        "    results.append({'command': command, 'rc': rc, 'output': stdout.getvalue() + stderr.getvalue()})\n"
        "json.dump(results, sys.stdout)"
    )
    with tempfile.TemporaryDirectory(prefix="znlm-phase17-") as tmp:
        tmp_path = Path(tmp)
        home = tmp_path / "home"
        temp = tmp_path / "tmp"
        home.mkdir()
        temp.mkdir()
        env = {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "TMPDIR": str(temp),
            "PYTHONPATH": str(repo_root),
            "PATH": os.environ.get("PATH", ""),
            "NO_COLOR": "1",
            "COLUMNS": "80",
        }
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=repo_root,
            env=env,
            input=json.dumps(commands),
            capture_output=True,
            text=True,
            timeout=30,
        )
    if proc.returncode != 0:
        output = proc.stdout + proc.stderr
        return {command: (proc.returncode, output) for command in commands}
    rows = json.loads(proc.stdout)
    return {str(row["command"]): (int(row["rc"]), str(row["output"])) for row in rows}


def _run_cli_exact(repo_root: Path, commands: list[str]) -> dict[str, tuple[int, str]]:
    """Run exact bare CLI probes in one clean child process."""

    code = (
        "import contextlib, io, json, sys; "
        "import notebooklm.cli as cli; "
        "results = []; "
        "commands = json.load(sys.stdin); "
        "\nfor command in commands:\n"
        "    args = command.split()[1:]\n"
        "    stdout = io.StringIO(); stderr = io.StringIO(); rc = 0\n"
        "    try:\n"
        "        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):\n"
        "            result = cli.console(args)\n"
        "        rc = 0 if result is None else int(result)\n"
        "    except SystemExit as exc:\n"
        "        try:\n"
        "            rc = int(exc.code or 0)\n"
        "        except (TypeError, ValueError):\n"
        "            rc = 1\n"
        "    except BaseException as exc:\n"
        "        rc = 1\n"
        "        print(f'{type(exc).__name__}: {exc}', file=stderr)\n"
        "    results.append({'command': command, 'rc': rc, 'output': stdout.getvalue() + stderr.getvalue()})\n"
        "json.dump(results, sys.stdout)"
    )
    with tempfile.TemporaryDirectory(prefix="znlm-phase17-") as tmp:
        tmp_path = Path(tmp)
        home = tmp_path / "home"
        temp = tmp_path / "tmp"
        home.mkdir()
        temp.mkdir()
        env = {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "TMPDIR": str(temp),
            "PYTHONPATH": str(repo_root),
            "PATH": os.environ.get("PATH", ""),
            "NO_COLOR": "1",
            "COLUMNS": "80",
        }
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=repo_root,
            env=env,
            input=json.dumps(commands),
            capture_output=True,
            text=True,
            timeout=30,
        )
    if proc.returncode != 0:
        output = proc.stdout + proc.stderr
        return {command: (proc.returncode, output) for command in commands}
    rows = json.loads(proc.stdout)
    return {str(row["command"]): (int(row["rc"]), str(row["output"])) for row in rows}


def _audit_cli_entries(
    repo_root: Path,
    entries: list[dict[str, Any]],
    *,
    actual_by_command: dict[str, tuple[int, str]],
) -> dict[str, Any]:
    total = len(entries)
    mismatches: list[dict[str, Any]] = []
    for entry in entries:
        command = entry["command"]
        golden_path = repo_root / "compat" / entry["file"]
        golden_text = _normalize_text(golden_path.read_text(encoding="utf-8"))

        rc, actual_output = actual_by_command[command]
        actual_text = _normalize_text(actual_output)
        expected_rc = int(entry.get("exit_code", 0))
        normalized_match = actual_text == golden_text
        exit_match = rc == expected_rc

        if not exit_match or not normalized_match:
            mismatches.append(
                {
                    "command": command,
                    "file": entry["file"],
                    "expected_exit_code": expected_rc,
                    "actual_exit_code": rc,
                    "expected_sha256": _sha256_text(golden_text),
                    "actual_sha256": _sha256_text(actual_text),
                    "normalized_match": normalized_match,
                    "expected_excerpt": _excerpt(golden_text),
                    "actual_excerpt": _excerpt(actual_text),
                }
            )

    return {
        "total": total,
        "matched": total - len(mismatches),
        "mismatched": len(mismatches),
        "mismatches": mismatches,
    }


def _audit_cli(repo_root: Path, index: dict[str, Any]) -> dict[str, Any]:
    entries = index["help"]
    total = len(entries)
    mismatches: list[dict[str, Any]] = []
    commands = [str(entry["command"]) for entry in entries]
    actual_by_command = _run_cli_helps(repo_root, commands)
    exact_entries = [*index.get("errors", []), *index.get("misc", [])]
    exact_commands = [str(entry["command"]) for entry in exact_entries]
    actual_exact = _run_cli_exact(repo_root, exact_commands) if exact_commands else {}

    for entry in entries:
        command = entry["command"]
        golden_path = repo_root / "compat" / entry["file"]
        golden_text = _normalize_text(golden_path.read_text(encoding="utf-8"))

        rc, actual_output = actual_by_command[command]
        actual_text = _normalize_text(actual_output)
        expected_rc = int(entry.get("exit_code", 0))
        normalized_match = actual_text == golden_text
        exit_match = rc == expected_rc

        if not exit_match or not normalized_match:
            mismatches.append(
                {
                    "command": command,
                    "file": entry["file"],
                    "expected_exit_code": expected_rc,
                    "actual_exit_code": rc,
                    "expected_sha256": _sha256_text(golden_text),
                    "actual_sha256": _sha256_text(actual_text),
                    "normalized_match": normalized_match,
                    "expected_excerpt": _excerpt(golden_text),
                    "actual_excerpt": _excerpt(actual_text),
                }
            )

    matched = total - len(mismatches)
    return {
        "total": total,
        "matched": matched,
        "mismatched": len(mismatches),
        "mismatches": mismatches,
        "error_probe": _audit_cli_entries(
            repo_root,
            list(index.get("errors", [])),
            actual_by_command=actual_exact,
        ),
        "misc_probe": _audit_cli_entries(
            repo_root,
            list(index.get("misc", [])),
            actual_by_command=actual_exact,
        ),
    }


def _split_signature_parameters(signature_text: str) -> list[str]:
    body = signature_text[signature_text.find("(") + 1 : signature_text.rfind(")")]
    params: list[str] = []
    current = ""
    depth = 0
    quote: str | None = None
    for char in body:
        if quote is not None:
            current += char
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
            current += char
        elif char in "([{<":
            depth += 1
            current += char
        elif char in ")]}>":
            depth -= 1
            current += char
        elif char == "," and depth == 0:
            params.append(current.strip())
            current = ""
        else:
            current += char
    if current.strip():
        params.append(current.strip())
    return params


def _normalize_default(value: str | None) -> str | None:
    if value is None:
        return None
    if value.startswith("<object object at 0x"):
        return "<object object>"
    return value


def _shape_from_text(signature_text: str) -> list[tuple[str, str, str | None]]:
    shape: list[tuple[str, str, str | None]] = []
    keyword_only = False
    for raw in _split_signature_parameters(signature_text):
        if raw == "*":
            keyword_only = True
            continue
        if raw.startswith("**"):
            name = raw[2:].split(":", 1)[0].split("=", 1)[0].strip()
            kind = "VAR_KEYWORD"
        elif raw.startswith("*"):
            name = raw[1:].split(":", 1)[0].split("=", 1)[0].strip()
            kind = "VAR_POSITIONAL"
        else:
            name = raw.split(":", 1)[0].split("=", 1)[0].strip()
            kind = "KEYWORD_ONLY" if keyword_only else "POSITIONAL_OR_KEYWORD"
        default = raw.split("=", 1)[1].strip() if "=" in raw else None
        shape.append((name, kind, _normalize_default(default)))
    return shape


def _shape_from_callable(func: Any) -> list[tuple[str, str, str | None]]:
    shape: list[tuple[str, str, str | None]] = []
    for name, param in inspect.signature(func).parameters.items():
        default = (
            None if param.default is inspect.Parameter.empty else repr(param.default)
        )
        shape.append((name, param.kind.name, _normalize_default(default)))
    return shape


def _audit_api(
    api_surface: dict[str, Any],
    signatures: dict[str, Any],
) -> dict[str, Any]:
    notebooklm_mod = importlib.import_module("notebooklm")
    client_module = importlib.import_module("notebooklm.client")

    expected_all = set(api_surface["root_all"])
    actual_all = set(getattr(notebooklm_mod, "__all__", []))
    missing_public = sorted(expected_all - actual_all)
    extra_public = sorted(actual_all - expected_all)

    subclients = signatures["subclients"]
    total = len(subclients)
    mismatches: list[dict[str, Any]] = []

    for subclient, expected in subclients.items():
        cls = getattr(client_module, expected["class"], None) or getattr(
            notebooklm_mod, expected["class"], None
        )
        if cls is None:
            mismatches.append(
                {
                    "subclient": subclient,
                    "reason": "missing_class",
                    "expected_class": expected["class"],
                }
            )
            continue

        actual_methods = sorted(
            name
            for name, value in inspect.getmembers(cls, inspect.iscoroutinefunction)
            if not name.startswith("_")
        )
        subclient_failures: list[dict[str, Any]] = []

        if actual_methods != expected["async_methods"]:
            subclient_failures.append(
                {
                    "reason": "async_methods",
                    "expected": expected["async_methods"],
                    "actual": actual_methods,
                }
            )

        golden_sigs = expected.get("method_signatures", {})
        for method_name in expected["async_methods"]:
            if not hasattr(cls, method_name):
                subclient_failures.append(
                    {"reason": "missing_method", "method": method_name}
                )
                continue
            actual_shape = _shape_from_callable(getattr(cls, method_name))
            golden_shape = _shape_from_text(golden_sigs[method_name])
            if actual_shape != golden_shape:
                subclient_failures.append(
                    {
                        "reason": "signature_shape",
                        "method": method_name,
                        "expected": golden_shape,
                        "actual": actual_shape,
                    }
                )

        if subclient_failures:
            mismatches.append({"subclient": subclient, "failures": subclient_failures})

    public_names_match = not missing_public and not extra_public
    matched = total - len(mismatches)
    return {
        "total": total,
        "matched": matched,
        "mismatched": len(mismatches),
        "mismatches": mismatches,
        "public_names": {
            "expected": len(expected_all),
            "actual": len(actual_all),
            "missing": missing_public,
            "extra": extra_public,
            "match": public_names_match,
        },
    }


def _row_evidence_summary(repo_root: Path) -> dict[str, Any]:
    manifest_path = _compat_path(repo_root, "cli_api_row_evidence.json")
    if not manifest_path.is_file():
        return {
            "manifest_present": False,
            "cli_rows_mapped": 0,
            "api_rows_mapped": 0,
            "api_scenarios_mapped": 0,
        }
    manifest = _load_json(manifest_path)
    api_scenarios = sum(
        len(mapping.get("scenario_refs", []))
        for mapping in manifest.get("api_mappings", [])
    )
    return {
        "manifest_present": True,
        "cli_rows_mapped": len(manifest.get("cli_mappings", [])),
        "api_rows_mapped": len(manifest.get("api_mappings", [])),
        "api_scenarios_mapped": api_scenarios,
        "promotion_allowed": False,
    }


def _overall_status(cli_report: dict[str, Any], api_report: dict[str, Any]) -> str:
    if cli_report["mismatched"] > 0:
        return "mismatch"
    if cli_report.get("error_probe", {}).get("mismatched", 0) > 0:
        return "mismatch"
    if cli_report.get("misc_probe", {}).get("mismatched", 0) > 0:
        return "mismatch"
    if api_report["mismatched"] > 0:
        return "mismatch"
    if not api_report["public_names"]["match"]:
        return "mismatch"
    return "pass"


def build_report(*, repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    """Build the Phase 17 CLI/API direct differential report from committed artifacts."""
    repo_root = Path(repo_root)
    index = _load_json(_compat_path(repo_root, "cli_golden/_index.json"))
    api_surface = _load_json(_compat_path(repo_root, "python_api_surface.json"))
    signatures = _load_json(_compat_path(repo_root, "api_golden/signatures.json"))

    cli_report = _audit_cli(repo_root, index)
    api_report = _audit_api(api_surface, signatures)
    row_evidence = _row_evidence_summary(repo_root)
    status = _overall_status(cli_report, api_report)

    return {
        "schema_version": SCHEMA_VERSION,
        "target": TARGET,
        "live_access": False,
        "network_access": False,
        "credential_access": False,
        "browser_store_access": False,
        "category_promotion": {"cli": False, "api": False},
        "exact_one_to_one_claim_ready": False,
        "cli": cli_report,
        "api": api_report,
        "row_evidence": row_evidence,
        "overall_status": status,
        "strict_exit_code": 0 if status == "pass" else 77,
    }


def render_human(report: dict[str, Any]) -> str:
    cli = report["cli"]
    api = report["api"]
    lines = [f"ZeroNotebookLM direct differential: {report['overall_status']}"]
    lines.append(f"CLI help: {cli['matched']}/{cli['total']} matched")
    lines.append(f"API subclients: {api['matched']}/{api['total']} matched")
    lines.append(
        f"API __all__: {'match' if api['public_names']['match'] else 'mismatch'}"
    )
    lines.append("category_promotion: no")
    lines.append("exact_one_to_one_claim_ready: no")
    return "\n".join(lines) + "\n"


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cli_api_direct_differential.py")
    parser.add_argument("--json", action="store_true", help="emit full JSON report")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit 77 if any direct comparison mismatches",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    report = build_report()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_human(report), end="")
    if args.strict:
        return int(report["strict_exit_code"])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
