"""Phase 0 acceptance tests for the NotebookLM Bare compatibility oracle.

These tests prove the Phase 0 *surface lock* against ``notebooklm-py==0.7.2`` is
real, complete, and pinned. They assert against the committed ``compat/``
artifacts only — they never import ``notebooklm`` (the upstream oracle was
introspected offline inside a disposable venv) and never touch the network, a
NotebookLM account, a session, or a cookie.

The pinned facts (wheel SHA, source commit, plan SHA, CLI counts, browser set,
closure states) are hard-coded here as the **independent** source of truth and
cross-checked against both ``scripts/_phase0_constants.py`` and the generated
artifacts, so neither the harness constants nor the artifacts can silently drift
from the plan.

Run with::

    PYTHONDONTWRITEBYTECODE=1 \\
      python -m pytest -q -p no:cacheprovider

stdlib + the stdlib-only Phase 0 harness modules only; no third-party imports.
"""

from __future__ import annotations

import hashlib
import itertools
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

# scripts/ is placed on sys.path by tests/conftest.py. Both modules below are
# stdlib-only (verified by the import-origin audit), so importing them here keeps
# the suite dependency-free while exercising the harness's single source of truth.
import _phase0_constants as C  # noqa: E402
import import_origin_audit  # noqa: E402

# --------------------------------------------------------------------------- #
# Independent literal pins (the plan is the source of truth; do not relax)
# --------------------------------------------------------------------------- #

PIN_TARGET_REQUIREMENT = "notebooklm-py==0.7.2"
PIN_TARGET_PROJECT = "notebooklm-py"
PIN_TARGET_VERSION = "0.7.2"
PIN_REQUIRES_PYTHON = ">=3.10"
PIN_WHEEL_SHA256 = "d850cfea2494732bc5f153406a9637c3ee5fe931d87901d101c026df2a6ecf65"
PIN_SOURCE_COMMIT = "915b5321e1c1f411e23bd8265517be8740749e56"
PIN_PLAN_SHA256 = "176f14caa9d44b0f86697f6f57fc2b4f6aa16d28b3ad8fcf0d49f34302e3b998"

PIN_CLI_NODES = 103
PIN_CLI_LEAVES = 90
PIN_CLI_GROUPS = 13

PIN_INTERACTIVE_LOGIN_BROWSERS = {"chromium", "chrome", "msedge"}
PIN_PARITY_STATES = {"pass", "open", "blocked"}
PIN_PARITY_CATEGORIES = ("cli", "api", "auth", "rpc", "offline", "self-test")

# Auth-matrix dimensions, locked independently of the harness constants. The two
# matrices follow the selected compatibility profile, so their row totals are:
#   interactive login = 3 browsers x 3 OS x 5 flows = 45
#   browser-cookie    = 150 upstream cells - 25 browser/OS exclusions
#                       - 4 Opera GX/Ubuntu, 10 Windows Chromium, and
#                         10 deferred macOS Chromium/Vivaldi path exclusions = 101
PIN_OS_MATRIX = ("macOS", "Ubuntu-LTS-Linux", "Windows-11")
PIN_INTERACTIVE_LOGIN_FLOWS = {"login", "refresh", "status", "logout", "doctor"}
PIN_COOKIE_PATHS = {"import", "profile-select", "account-select", "inspect", "refresh"}
# chromium-family (8) + firefox + safari = 10 documented cookie-store browsers.
PIN_COOKIE_CHROMIUM_FAMILY = {
    "arc",
    "brave",
    "chrome",
    "chromium",
    "edge",
    "opera",
    "opera-gx",
    "vivaldi",
}
PIN_COOKIE_BROWSERS = PIN_COOKIE_CHROMIUM_FAMILY | {"firefox", "safari"}
PIN_COOKIE_PROFILE_EXCLUSIONS = {
    ("arc", "macOS"),
    ("arc", "Ubuntu-LTS-Linux"),
    ("arc", "Windows-11"),
    ("safari", "Ubuntu-LTS-Linux"),
    ("safari", "Windows-11"),
}
PIN_COOKIE_PATH_PROFILE_EXCLUSIONS = {
    ("opera-gx", "Ubuntu-LTS-Linux", "import"),
    ("opera-gx", "Ubuntu-LTS-Linux", "account-select"),
    ("opera-gx", "Ubuntu-LTS-Linux", "inspect"),
    ("opera-gx", "Ubuntu-LTS-Linux", "refresh"),
    ("chrome", "Windows-11", "import"),
    ("chrome", "Windows-11", "profile-select"),
    ("chrome", "Windows-11", "account-select"),
    ("chrome", "Windows-11", "inspect"),
    ("chrome", "Windows-11", "refresh"),
    ("edge", "Windows-11", "import"),
    ("edge", "Windows-11", "profile-select"),
    ("edge", "Windows-11", "account-select"),
    ("edge", "Windows-11", "inspect"),
    ("edge", "Windows-11", "refresh"),
    ("chromium", "macOS", "import"),
    ("chromium", "macOS", "profile-select"),
    ("chromium", "macOS", "account-select"),
    ("chromium", "macOS", "inspect"),
    ("chromium", "macOS", "refresh"),
    ("vivaldi", "macOS", "import"),
    ("vivaldi", "macOS", "profile-select"),
    ("vivaldi", "macOS", "account-select"),
    ("vivaldi", "macOS", "inspect"),
    ("vivaldi", "macOS", "refresh"),
}
PIN_COOKIE_PATH_PROFILE_EXCLUSION_REASONS = {
    ("opera-gx", "Ubuntu-LTS-Linux"): (
        "pinned_upstream_rookiepy_0_5_6_"
        "opera_gx_linux_generic_reader_unsupported"
    ),
    ("chrome", "Windows-11"): (
        "pinned_upstream_notebooklm_py_0_7_2_rookiepy_0_5_6_"
        "modern_windows_chromium_profile_unsupported"
    ),
    ("edge", "Windows-11"): (
        "pinned_upstream_notebooklm_py_0_7_2_rookiepy_0_5_6_"
        "modern_windows_chromium_profile_unsupported"
    ),
    ("chromium", "macOS"): "deferred_to_future_release",
    ("vivaldi", "macOS"): "deferred_to_future_release",
}
PIN_INTERACTIVE_LOGIN_ROWS = 45
PIN_BROWSER_COOKIE_IMPORT_ROWS = 101

EXPECTED_SUBCLIENTS = {
    "artifacts",
    "chat",
    "mind_maps",
    "notebooks",
    "notes",
    "research",
    "settings",
    "sharing",
    "sources",
}

# Every command directly under the root group, per the locked Click tree.
EXPECTED_ROOT_SUBCOMMANDS = {
    "agent",
    "artifact",
    "ask",
    "auth",
    "clear",
    "completion",
    "configure",
    "create",
    "delete",
    "doctor",
    "download",
    "generate",
    "history",
    "language",
    "list",
    "login",
    "metadata",
    "note",
    "profile",
    "rename",
    "research",
    "share",
    "skill",
    "source",
    "status",
    "summary",
    "use",
}
# The 12 non-root command groups (each is itself a node with subcommands).
EXPECTED_SUBGROUPS = {
    "agent",
    "artifact",
    "auth",
    "download",
    "generate",
    "language",
    "note",
    "profile",
    "research",
    "share",
    "skill",
    "source",
}
EXPECTED_GROUP_PATHS = {"notebooklm"} | {f"notebooklm {g}" for g in EXPECTED_SUBGROUPS}

# The canonical plan lives outside the public repository. Its optional path is
# supplied explicitly so this test never embeds or guesses an operator home path.
PLAN_PATH_ENV = "ZERO_NOTEBOOKLM_CANONICAL_PLAN"

# --------------------------------------------------------------------------- #
# Small, local, deterministic helpers
# --------------------------------------------------------------------------- #

# High-signal secret/session markers. Each targets a *real* credential format,
# not the generic words "cookie"/"token"/"password" that legitimately appear in
# upstream CLI --help goldens. Validated to produce zero hits on the clean tree.
SECRET_PATTERNS = (
    ("google-oauth-access-token", re.compile(r"ya29\.[A-Za-z0-9_\-]{20,}")),
    ("google-oauth-refresh-token", re.compile(r"\b1//[A-Za-z0-9_\-]{30,}")),
    ("pem-private-key", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
    (
        "authorization-bearer",
        re.compile(r"[Aa]uthorization:\s*Bearer\s+[A-Za-z0-9._\-]{16,}"),
    ),
    (
        "google-auth-cookie-value",
        re.compile(
            r"\b(?:__Secure-[13]PSID|__Secure-[13]PAPISID|SAPISID|APISID|"
            r"HSID|SSID|SIDCC|NID)=[A-Za-z0-9_./+\-]{12,}"
        ),
    ),
)

# This module defines the SECRET_PATTERNS literals above, so it would self-match.
# It is the only file exempt from the secret scan; everything else is scanned.
_SECRET_SCAN_EXEMPT = {Path(__file__).resolve()}

_PY_SKIP_DIRS = {
    "__pycache__",
    ".git",
    ".ai-bridge",
    ".venv",
    "venv",
    ".pytest_cache",
    "build",
    "dist",
    "notebooklm-py-reference",
}


def _iter_files(*dirs: Path):
    for base in dirs:
        if not base.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in _PY_SKIP_DIRS]
            for fn in filenames:
                yield Path(dirpath) / fn


def _iter_repo_py_files(repo_root: Path):
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [
            d
            for d in dirnames
            if d not in _PY_SKIP_DIRS and not d.endswith(".egg-info")
        ]
        for fn in sorted(filenames):
            if fn.endswith(".py"):
                yield Path(dirpath) / fn


def _table_rows(md: str):
    """Yield cell-lists for each Markdown table data row (skips separators)."""
    for line in md.splitlines():
        s = line.strip()
        if not s.startswith("|") or set(s) <= set("| -"):
            continue
        yield [c.strip() for c in s.strip("|").split("|")]


def _parity_states(md: str):
    states = []
    in_state_table = False
    for line in md.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            in_state_table = False
            continue
        if set(s) <= set("| -"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if not cells:
            continue
        last = cells[-1].strip("` ").strip()
        if last == "State":
            in_state_table = True
            continue
        if in_state_table:
            states.append(last)
    return states


def _run_script(script_name: str, *script_args: str) -> subprocess.CompletedProcess:
    """Run a Phase 0 harness script the way Core does: same interpreter, no
    bytecode (``-B``), so the subprocess cannot leave ``__pycache__`` behind."""
    script = C.SCRIPTS_DIR / script_name
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(
        [sys.executable, "-B", str(script), *script_args],
        cwd=str(C.REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


# --------------------------------------------------------------------------- #
# Source-of-truth guard: the harness constants must equal the literal pins
# --------------------------------------------------------------------------- #


def test_phase0_constants_match_literal_pins():
    assert C.TARGET_REQUIREMENT == PIN_TARGET_REQUIREMENT
    assert C.TARGET_PROJECT == PIN_TARGET_PROJECT
    assert C.TARGET_VERSION == PIN_TARGET_VERSION
    assert C.REQUIRES_PYTHON == PIN_REQUIRES_PYTHON
    assert C.WHEEL_SHA256 == PIN_WHEEL_SHA256
    assert C.SOURCE_COMMIT == PIN_SOURCE_COMMIT
    assert C.PLAN_SHA256 == PIN_PLAN_SHA256
    assert C.EXPECTED_CLI_NODES == PIN_CLI_NODES
    assert C.EXPECTED_CLI_LEAVES == PIN_CLI_LEAVES
    assert C.EXPECTED_CLI_GROUPS == PIN_CLI_GROUPS
    assert set(C.INTERACTIVE_LOGIN_BROWSERS) == PIN_INTERACTIVE_LOGIN_BROWSERS
    assert set(C.PARITY_STATES) == PIN_PARITY_STATES
    assert set(C.PARITY_CATEGORIES) == set(PIN_PARITY_CATEGORIES)
    assert tuple(C.OS_MATRIX) == PIN_OS_MATRIX
    assert set(C.AUTH_COOKIE_PROFILE_EXCLUSIONS) == PIN_COOKIE_PROFILE_EXCLUSIONS
    assert (
        set(C.AUTH_COOKIE_PATH_PROFILE_EXCLUSIONS)
        == PIN_COOKIE_PATH_PROFILE_EXCLUSIONS
    )
    assert (
        C.AUTH_COOKIE_PATH_PROFILE_EXCLUSION_REASONS
        == PIN_COOKIE_PATH_PROFILE_EXCLUSION_REASONS
    )


# --------------------------------------------------------------------------- #
# 1) Required compat artifacts exist and parse
# --------------------------------------------------------------------------- #


def test_required_compat_artifacts_exist_and_parse(
    oracle,
    cli_surface,
    python_api,
    auth_matrix,
    dep_graph,
    wire_shape,
    cli_index,
    enums_golden,
    exceptions_golden,
    signatures_golden,
    imports_golden,
    parity_md,
):
    # The 5 JSON artifacts the exit gate names, plus the markdown parity matrix.
    assert {p.name for p in C.REQUIRED_COMPAT_JSON} == {
        "notebooklm_py_0_7_2_oracle.json",
        "cli_surface.json",
        "python_api_surface.json",
        "auth_matrix.json",
        "dependency_graph.json",
    }
    for art in C.REQUIRED_COMPAT_JSON + (C.PARITY_MATRIX_MD,):
        assert art.is_file(), f"missing required artifact: {art}"

    # All five JSON artifacts are parsed (fixtures already loaded them) and carry
    # the Phase 0 schema stamp.
    for doc in (oracle, cli_surface, python_api, auth_matrix, dep_graph):
        assert doc.get("schema_version") == C.ORACLE_SCHEMA_VERSION

    # The secondary goldens/fixtures parse and are non-trivial.
    assert isinstance(enums_golden.get("enums"), dict) and enums_golden["enums"]
    assert (
        isinstance(exceptions_golden.get("exceptions"), list)
        and exceptions_golden["exceptions"]
    )
    assert isinstance(signatures_golden.get("client"), dict)
    assert wire_shape.get("xssi_prefix")
    assert cli_index.get("help")
    assert imports_golden  # non-empty
    assert parity_md.lstrip().startswith("# ZeroNotebookLM")


# --------------------------------------------------------------------------- #
# 2) Oracle target is exactly notebooklm-py==0.7.2
# --------------------------------------------------------------------------- #


def test_oracle_target_is_pinned(
    oracle, cli_surface, python_api, auth_matrix, dep_graph
):
    target = oracle["target"]
    assert target["requirement"] == PIN_TARGET_REQUIREMENT
    assert target["project"] == PIN_TARGET_PROJECT
    assert target["version"] == PIN_TARGET_VERSION
    assert target["requires_python"] == PIN_REQUIRES_PYTHON
    # Every other artifact agrees on the same single pinned target.
    for doc in (cli_surface, python_api, auth_matrix, dep_graph):
        assert doc["target"] == PIN_TARGET_REQUIREMENT
    assert dep_graph["requires_python"] == PIN_REQUIRES_PYTHON


# --------------------------------------------------------------------------- #
# 3) Wheel SHA-256 is exactly the pin
# --------------------------------------------------------------------------- #


def test_wheel_sha256_pinned(oracle, dep_graph, parity_md):
    prov = oracle["provenance"]
    assert prov["wheel_sha256"] == PIN_WHEEL_SHA256
    assert prov["wheel_sha256_expected"] == PIN_WHEEL_SHA256
    assert prov["wheel_sha256_verified"] is True
    assert prov["wheel_filename"] == C.WHEEL_FILENAME
    # dependency graph provenance carries the same verified hash.
    assert dep_graph["provenance"]["wheel_sha256"] == PIN_WHEEL_SHA256
    assert dep_graph["provenance"]["wheel_sha256_verified"] is True
    # and the human-facing parity matrix prints it.
    assert PIN_WHEEL_SHA256 in parity_md


# --------------------------------------------------------------------------- #
# 4) Source commit is exactly the pin
# --------------------------------------------------------------------------- #


def test_source_commit_pinned(oracle, dep_graph, parity_md):
    prov = oracle["provenance"]
    assert prov["source_commit"] == PIN_SOURCE_COMMIT
    assert prov["source_repo"] == C.SOURCE_REPO
    assert dep_graph["provenance"]["source_commit"] == PIN_SOURCE_COMMIT
    assert PIN_SOURCE_COMMIT in parity_md
    # If the upstream commit was confirmed on GitHub, it must be the pinned SHA
    # (a recorded failed/!=pin confirmation is not acceptable).
    remote = prov.get("source_commit_remote_check", {})
    if remote.get("checked") and remote.get("exists") is not None:
        assert remote["exists"] is True
        assert remote.get("returned_sha") == PIN_SOURCE_COMMIT


# --------------------------------------------------------------------------- #
# 5) Plan SHA-256 is exactly the pin
# --------------------------------------------------------------------------- #


def test_plan_sha256_pinned(oracle):
    assert oracle["plan_sha256"] == PIN_PLAN_SHA256


def test_plan_file_hashes_to_pin_when_present():
    configured_path = os.environ.get(PLAN_PATH_ENV)
    if not configured_path:
        pytest.skip(f"canonical plan path not configured via {PLAN_PATH_ENV}")
    plan_path = Path(configured_path).expanduser()
    if not plan_path.is_file():
        pytest.skip("configured canonical plan is not present")
    digest = hashlib.sha256(plan_path.read_bytes()).hexdigest()
    assert digest == PIN_PLAN_SHA256, (
        f"canonical plan content drifted: {digest} != pinned {PIN_PLAN_SHA256}"
    )


# --------------------------------------------------------------------------- #
# 6) CLI manifest — 103 nodes / 90 leaves, root commands, help coverage
# --------------------------------------------------------------------------- #


def test_cli_counts_103_nodes_90_leaves(cli_surface, oracle, parity_md):
    counts = cli_surface["counts"]
    assert counts["nodes"] == PIN_CLI_NODES
    assert counts["leaves"] == PIN_CLI_LEAVES
    assert counts["groups"] == PIN_CLI_GROUPS
    assert cli_surface["expected_counts"] == {
        "nodes": PIN_CLI_NODES,
        "leaves": PIN_CLI_LEAVES,
        "groups": PIN_CLI_GROUPS,
    }
    assert len(cli_surface["nodes"]) == PIN_CLI_NODES
    assert len(cli_surface["leaf_commands"]) == PIN_CLI_LEAVES
    assert len(cli_surface["groups"]) == PIN_CLI_GROUPS

    summary = oracle["cli_surface_summary"]
    assert (summary["nodes"], summary["leaves"], summary["groups"]) == (
        PIN_CLI_NODES,
        PIN_CLI_LEAVES,
        PIN_CLI_GROUPS,
    )
    # The parity matrix states the same counts in prose.
    assert (
        f"{PIN_CLI_LEAVES} leaf commands across {PIN_CLI_NODES} Click-tree nodes"
        in parity_md
    )


def test_cli_node_structure_is_consistent(cli_surface):
    nodes = cli_surface["nodes"]
    required_keys = {"command", "kind", "path", "params", "subcommands", "name"}
    for n in nodes:
        assert required_keys <= set(n), f"node missing keys: {n.get('command')}"
        assert n["kind"] in {"group", "command"}

    leaf_nodes = sorted(n["command"] for n in nodes if n["kind"] == "command")
    group_nodes = sorted(n["command"] for n in nodes if n["kind"] == "group")
    assert len(leaf_nodes) == PIN_CLI_LEAVES
    assert len(group_nodes) == PIN_CLI_GROUPS
    assert len(leaf_nodes) + len(group_nodes) == PIN_CLI_NODES
    # Counts derived from the node list must match the published summaries.
    assert leaf_nodes == sorted(cli_surface["leaf_commands"])
    assert group_nodes == sorted(cli_surface["groups"])


def test_cli_root_commands_present(cli_surface, oracle):
    nodes = {n["command"]: n for n in cli_surface["nodes"]}
    root = nodes["notebooklm"]
    assert root["kind"] == "group"
    root_subs = set(root["subcommands"])
    # Every expected top-level command is present in the locked tree.
    assert EXPECTED_ROOT_SUBCOMMANDS <= root_subs, (
        f"missing root commands: {sorted(EXPECTED_ROOT_SUBCOMMANDS - root_subs)}"
    )
    # Every expected sub-group is itself a real group node.
    assert set(cli_surface["groups"]) == EXPECTED_GROUP_PATHS
    for g in EXPECTED_SUBGROUPS:
        assert nodes[f"notebooklm {g}"]["kind"] == "group"
    # Oracle's group_paths agree.
    assert set(oracle["cli_surface_summary"]["group_paths"]) == EXPECTED_GROUP_PATHS


def test_cli_help_golden_coverage(cli_surface, cli_index, compat_dir):
    # A help golden exists for every one of the 103 nodes.
    help_entries = cli_index["help"]
    assert len(help_entries) == PIN_CLI_NODES
    assert cli_index["counts"]["help"] == PIN_CLI_NODES
    assert cli_surface["golden_help_files"] == PIN_CLI_NODES

    help_commands = {e["command"] for e in help_entries}
    node_commands = {n["command"] for n in cli_surface["nodes"]}
    assert help_commands == node_commands, "help goldens must cover exactly every node"

    for e in help_entries:
        f = compat_dir / e["file"]
        assert f.is_file(), f"missing help golden: {e['file']}"
        assert f.stat().st_size > 0

    on_disk = list((compat_dir / "cli_golden").glob("*help.txt"))
    assert len(on_disk) >= PIN_CLI_NODES

    # Representative success/error goldens captured from real upstream.
    assert (compat_dir / "cli_golden" / "notebooklm--version.txt").is_file()
    assert len(cli_index.get("errors", [])) >= 3


def test_cli_leaf_commands_match_parity_matrix(cli_surface, parity_md):
    leaf_rows = set()
    for cells in _table_rows(parity_md):
        if len(cells) == 2:
            name = cells[0].strip("` ").strip()
            state = cells[1].strip("` ").strip()
            if name.startswith("notebooklm ") and state in PIN_PARITY_STATES:
                leaf_rows.add(name)
    assert len(leaf_rows) == PIN_CLI_LEAVES
    assert leaf_rows == set(cli_surface["leaf_commands"])


# --------------------------------------------------------------------------- #
# 7) Python API manifest — names/modules/subclients/exceptions/goldens/client
# --------------------------------------------------------------------------- #


def test_python_api_root_all_count(python_api, oracle, imports_golden):
    n = python_api["root_all_count"]
    assert n >= 105, f"root_all_count weaker than pin: {n}"
    assert n == len(python_api["root_all"])
    assert n == len(python_api["importable_public_names"])
    assert oracle["python_api_summary"]["root_all_count"] == n
    # imports.txt golden enumerates exactly the public surface.
    assert set(imports_golden) == set(python_api["root_all"])
    assert len(imports_golden) == n
    # A few load-bearing public names must be exported.
    for name in ("NotebookLMClient", "AuthTokens", "NotebookLMError", "__version__"):
        assert name in python_api["root_all"]


def test_python_api_modules_nonempty(python_api):
    modules = python_api["modules"]
    assert modules, "no public modules recorded"
    for mod_name, mod in modules.items():
        assert mod.get("members"), f"module has no members: {mod_name}"
    expected = {
        "notebooklm",
        "notebooklm.client",
        "notebooklm.exceptions",
        "notebooklm.rpc",
        "notebooklm.types",
        "notebooklm.auth",
    }
    assert expected <= set(modules)


def test_python_api_subclients(python_api, oracle, signatures_golden):
    subs = python_api["subclients"]
    assert set(subs) == EXPECTED_SUBCLIENTS
    assert len(subs) == 9
    for name, sub in subs.items():
        assert sub.get("class")
        assert sub.get("module")
        assert isinstance(sub.get("methods"), list)
        assert isinstance(sub.get("async_methods"), list)
    assert set(oracle["python_api_summary"]["subclients"]) == EXPECTED_SUBCLIENTS
    # signatures golden curates the same 9 sub-clients.
    assert set(signatures_golden["subclients"]) == EXPECTED_SUBCLIENTS


def test_python_api_exceptions(python_api, oracle, exceptions_golden):
    hierarchy = python_api["exception_hierarchy"]
    assert len(hierarchy) >= 40
    assert oracle["python_api_summary"]["exception_count"] == len(hierarchy)
    names = {e["name"] for e in hierarchy}
    assert "NotebookLMError" in names
    golden_names = {e["name"] for e in exceptions_golden["exceptions"]}
    assert golden_names == names
    # Every exception's MRO is rooted in the upstream base and the builtins.
    for e in exceptions_golden["exceptions"]:
        mro = e["mro"]
        assert "notebooklm.exceptions.NotebookLMError" in mro
        assert mro[-1] == "builtins.BaseException"


def test_python_api_enum_signature_import_goldens(
    python_api, oracle, enums_golden, signatures_golden
):
    enum_inv = python_api["enum_inventory"]
    assert enum_inv, "no enums recorded"
    assert oracle["python_api_summary"]["enum_count"] == len(enum_inv)
    # enums golden enumerates exactly the surface's enums, each with members.
    assert set(enums_golden["enums"]) == set(enum_inv)
    for enum_name, members in enums_golden["enums"].items():
        assert members, f"enum has no members: {enum_name}"
    # signatures golden carries client lifecycle + dataclasses.
    assert signatures_golden["client"]
    assert signatures_golden.get("dataclasses")


def test_python_api_client_async_and_context_manager(
    python_api, oracle, signatures_golden
):
    client = python_api["client"]
    assert client["is_async_context_manager"] is True
    assert oracle["python_api_summary"]["is_async_context_manager"] is True
    # awaitable lifecycle methods are present (evidence of async behavior).
    assert client["async_public_methods"], "no async public methods recorded"
    assert {"close", "drain"} <= set(client["async_public_methods"])
    assert "from_storage" in client["classmethods"]
    assert client["init_signature"]["parameters"]
    # the curated signatures golden agrees the client is an async CM.
    assert signatures_golden["client"]["is_async_context_manager"] is True


def test_python_api_annotation_identity_deviations(python_api, oracle, dep_graph):
    devs = python_api["annotation_identity_deviations"]
    assert len(devs) >= 1
    assert oracle["python_api_summary"]["annotation_identity_deviation_count"] == len(
        devs
    )
    # httpx-owned annotations are explicitly recorded as deviations (the runtime
    # must not import httpx — JMC-NLB-004).
    assert dep_graph["behavioral_influence"]["httpx"]["annotation_identity_deviations"]


# --------------------------------------------------------------------------- #
# 8) Auth matrix — interactive-login browsers + cookie-import row states
# --------------------------------------------------------------------------- #


def test_auth_interactive_login_browsers(auth_matrix, oracle):
    src = auth_matrix["sources_from_upstream"]
    assert set(src["interactive_login_browsers"]) == PIN_INTERACTIVE_LOGIN_BROWSERS
    assert (
        set(oracle["matrix"]["interactive_login_browsers"])
        == PIN_INTERACTIVE_LOGIN_BROWSERS
    )


def test_auth_matrices_nonempty_and_closed(auth_matrix):
    assert auth_matrix["closure_states"] == ["pass", "open", "blocked"]
    login = auth_matrix["interactive_login_matrix"]
    cookie = auth_matrix["browser_cookie_import_matrix"]
    assert login, "interactive login matrix is empty"
    assert cookie, "browser-cookie import matrix is empty"

    counts = auth_matrix["counts"]
    assert counts["interactive_login_rows"] == len(login)
    assert counts["browser_cookie_import_rows"] == len(cookie)

    # Interactive-login rows are confined to the documented browser set.
    assert {r["browser"] for r in login} == PIN_INTERACTIVE_LOGIN_BROWSERS
    assert {r["matrix"] for r in login} == {"interactive_login"}
    assert {r["matrix"] for r in cookie} == {"browser_cookie_import"}
    # Cookie-import rows carry a real selector path.
    assert {r["path"] for r in cookie}  # non-empty selector dimension

    all_states = {r["parity_state"] for r in login + cookie}
    assert all_states <= PIN_PARITY_STATES
    assert all_states == {"pass"}


def test_auth_matrix_row_coverage_counts(auth_matrix):
    """Both auth matrices have locked in-profile row totals.

    Proves the 45 interactive-login and 101 in-profile browser-cookie rows are
    real (not a bare count field), with explicit browser/OS and path exclusions.
    """
    login = auth_matrix["interactive_login_matrix"]
    cookie = auth_matrix["browser_cookie_import_matrix"]
    counts = auth_matrix["counts"]

    # Both blocks exist side by side (the review flagged that only the cookie
    # block was visibly evidenced).
    assert login, "interactive_login_matrix block missing"
    assert cookie, "browser_cookie_import_matrix block missing"

    # --- interactive login: 3 browsers x 3 OS x 5 flows = 45 ------------------ #
    assert counts["interactive_login_rows"] == PIN_INTERACTIVE_LOGIN_ROWS
    assert len(login) == PIN_INTERACTIVE_LOGIN_ROWS
    login_browsers = {r["browser"] for r in login}
    login_os = {r["os"] for r in login}
    login_flows = {r["flow"] for r in login}
    assert login_browsers == PIN_INTERACTIVE_LOGIN_BROWSERS
    assert login_os == set(PIN_OS_MATRIX)
    assert login_flows == PIN_INTERACTIVE_LOGIN_FLOWS
    # Full cartesian coverage with no duplicate or missing cell.
    login_cells = [(r["browser"], r["os"], r["flow"]) for r in login]
    assert len(login_cells) == len(set(login_cells))
    assert set(login_cells) == set(
        itertools.product(
            PIN_INTERACTIVE_LOGIN_BROWSERS,
            set(PIN_OS_MATRIX),
            PIN_INTERACTIVE_LOGIN_FLOWS,
        )
    )

    # --- browser-cookie import: 150 cells - 25 browser/OS - 24 paths = 101 ---- #
    assert counts["browser_cookie_import_rows"] == PIN_BROWSER_COOKIE_IMPORT_ROWS
    assert len(cookie) == PIN_BROWSER_COOKIE_IMPORT_ROWS
    cookie_browsers = {r["browser"] for r in cookie}
    cookie_os = {r["os"] for r in cookie}
    cookie_paths = {r["path"] for r in cookie}
    assert cookie_browsers == PIN_COOKIE_BROWSERS - {"arc"}
    assert len(cookie_browsers) == 9
    assert cookie_os == set(PIN_OS_MATRIX)
    assert cookie_paths == PIN_COOKIE_PATHS
    cookie_cells = [(r["browser"], r["os"], r["path"]) for r in cookie]
    assert len(cookie_cells) == len(set(cookie_cells))
    expected_cookie_cells = {
        cell
        for cell in itertools.product(
            PIN_COOKIE_BROWSERS, set(PIN_OS_MATRIX), PIN_COOKIE_PATHS
        )
        if (cell[0], cell[1]) not in PIN_COOKIE_PROFILE_EXCLUSIONS
        and cell not in PIN_COOKIE_PATH_PROFILE_EXCLUSIONS
    }
    assert set(cookie_cells) == expected_cookie_cells
    assert {
        (row["browser"], row["os"], row.get("path"))
        for row in auth_matrix["profile_exclusions"]
    } == {
        (browser, osname, None)
        for browser, osname in PIN_COOKIE_PROFILE_EXCLUSIONS
    } | PIN_COOKIE_PATH_PROFILE_EXCLUSIONS
    assert {
        (row["browser"], row["os"]): row["reason"]
        for row in auth_matrix["profile_exclusions"]
        if "path" in row
    } == PIN_COOKIE_PATH_PROFILE_EXCLUSION_REASONS
    assert {
        (row["browser"], row["os"], row["path"])
        for row in cookie
        if row["browser"] == "opera-gx" and row["os"] == "Ubuntu-LTS-Linux"
    } == {
        ("opera-gx", "Ubuntu-LTS-Linux", "profile-select"),
    }
    assert not {
        (row["browser"], row["os"], row["path"])
        for row in cookie
        if row["browser"] in {"chrome", "edge"} and row["os"] == "Windows-11"
    }
    assert not {
        (row["browser"], row["os"], row["path"])
        for row in cookie
        if row["browser"] in {"chromium", "vivaldi"} and row["os"] == "macOS"
    }

    # --- counts block is internally consistent with the rows ------------------ #
    assert set(counts["os_rows"]) == set(PIN_OS_MATRIX)
    assert set(counts["cookie_browsers"]) == PIN_COOKIE_BROWSERS

    # --- the cookie browser set is exactly the upstream-sourced derivation ----- #
    src = auth_matrix["sources_from_upstream"]
    derived = set(src["chromium_family_cookie_browsers"])
    assert derived == PIN_COOKIE_CHROMIUM_FAMILY
    if src["firefox_cookie_support"]:
        derived.add("firefox")
    if src["safari_cookie_support"]:
        derived.add("safari")
    assert derived == PIN_COOKIE_BROWSERS


# --------------------------------------------------------------------------- #
# 9) Dependency graph — third-party influence + base/extras distinction
# --------------------------------------------------------------------------- #


def test_dependency_graph_covers_third_parties(dep_graph):
    infl = dep_graph["behavioral_influence"]
    for pkg in (
        "httpx",
        "click",
        "rich",
        "filelock",
        "rookiepy",
        "playwright",
        "markdownify",
    ):
        assert pkg in infl, f"dependency graph omits {pkg}"
        assert infl[pkg]["role"]
        assert infl[pkg]["bare_replacement"]

    # Base runtime requirements (no extra) vs extras-gated behavior is distinguished.
    base = dep_graph["base_runtime_requirements"]
    assert len(base) == 4
    base_names = {req.split("<")[0].split(">")[0].split("=")[0].strip() for req in base}
    assert {"click", "filelock", "httpx", "rich"} == base_names
    for pkg in ("click", "httpx", "rich", "filelock"):
        assert infl[pkg]["extra"] is None, f"{pkg} should be a base dependency"
    assert infl["rookiepy"]["extra"] == "cookies"
    assert infl["playwright"]["extra"] == "browser"
    assert infl["markdownify"]["extra"] == "markdown"

    extras = dep_graph["extras"]
    assert {"browser", "cookies", "markdown", "all", "dev"} <= set(extras)
    assert dep_graph["requires_python"] == PIN_REQUIRES_PYTHON


# --------------------------------------------------------------------------- #
# 10) Parity matrix — pass/open/blocked only, categories present
# --------------------------------------------------------------------------- #


def test_parity_matrix_states_are_valid(parity_md):
    states = _parity_states(parity_md)
    assert states, "parity matrix has no recognizable state rows"
    assert set(states) <= PIN_PARITY_STATES


def test_parity_matrix_has_valid_category_rows(parity_md):
    category_state = {}
    for cells in _table_rows(parity_md):
        if cells and cells[0] in PIN_PARITY_CATEGORIES:
            category_state[cells[0]] = cells[-1].strip("` ").strip()
    for cat in PIN_PARITY_CATEGORIES:
        assert cat in category_state, f"parity matrix missing category '{cat}'"
        assert category_state[cat] in PIN_PARITY_STATES, (
            f"category '{cat}' has invalid state"
        )


# --------------------------------------------------------------------------- #
# 11) RPC fixtures — wire shape + sanitized skeletons load & carry evidence
# --------------------------------------------------------------------------- #


def test_rpc_wire_shape(wire_shape, oracle):
    assert wire_shape["xssi_prefix"] == ")]}'"
    assert "wrb.fr" in wire_shape["batchexecute_markers"]
    assert "rpcids" in wire_shape["batchexecute_markers"]
    assert "notebooklm.google.com" in wire_shape["host_literals"]
    assert wire_shape["endpoint_path_literals"]
    assert wire_shape["rpc_modules"]
    # Oracle's recorded rpc_shape agrees with the fixture wire shape.
    rpc_shape = oracle["rpc_shape"]
    assert rpc_shape["xssi_prefixes"][0] == wire_shape["xssi_prefix"]
    assert set(wire_shape["batchexecute_markers"]) <= set(
        rpc_shape["batchexecute_markers"]
    )
    assert rpc_shape["rpc_modules"] == wire_shape["rpc_modules"]


def test_rpc_fixture_skeletons_load_and_are_sanitized(compat_dir, wire_shape):
    fx = compat_dir / "rpc_fixtures"
    xssi = wire_shape["xssi_prefix"]

    for name in (
        "list_notebooks.response.txt",
        "list_sources.response.txt",
        "list_notes.response.txt",
        "chat_ask.streaming.response.txt",
    ):
        body = (fx / name).read_text(encoding="utf-8")
        assert body.startswith(xssi), f"{name} missing XSSI guard prefix"
        assert "wrb.fr" in body, f"{name} missing batchexecute envelope marker"

    req = (fx / "list_notebooks.request.txt").read_text(encoding="utf-8")
    assert "f.req=" in req, "request fixture missing batchexecute f.req body"

    assert (fx / "README.md").is_file()

    # Positive sanitization evidence: fixtures use obvious synthetic placeholders,
    # and (req 15) carry no real session token.
    combined = "".join(
        (fx / n).read_text(encoding="utf-8")
        for n in (
            "list_notebooks.response.txt",
            "list_notebooks.request.txt",
            "list_sources.response.txt",
            "list_sources.request.txt",
            "list_notes.response.txt",
            "list_notes.request.txt",
            "chat_ask.streaming.response.txt",
        )
    )
    assert (
        ("SYNTHETIC" in combined)
        or ("fake-notebook" in combined)
        or ("synthetic" in combined)
    )
    for label, rx in SECRET_PATTERNS:
        assert not rx.search(combined), f"rpc fixture contains {label}"


# --------------------------------------------------------------------------- #
# 12) run_phase0_oracle.py --check passes (subprocess smoke, no bytecode)
# --------------------------------------------------------------------------- #


def test_run_phase0_oracle_check_subprocess():
    proc = _run_script("run_phase0_oracle.py", "--check")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, f"--check failed:\n{out}"
    assert "checks passed" in out
    assert "Phase 0 oracle artifacts validated" in out
    assert "exit gate NOT satisfied" not in out


# --------------------------------------------------------------------------- #
# 13) import_origin_audit.py passes (subprocess smoke, no bytecode)
# --------------------------------------------------------------------------- #


def test_import_origin_audit_subprocess():
    proc = _run_script("import_origin_audit.py")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, f"audit failed:\n{out}"
    assert "PASS: no denylisted third-party imports" in out


# --------------------------------------------------------------------------- #
# 14) Runtime surface remains bounded to the current stdlib phase
# --------------------------------------------------------------------------- #


def test_runtime_surfaces_are_limited_to_phase3b18(repo_root):
    # Phase 1 introduced the stdlib package skeleton, CLI/help surface, HTTP
    # transport, async boundary, and lockfile helper. Phase 2A adds the offline
    # auth/session/profile foundation (profiles, cookies, auth); Phase 2B adds the
    # offline browser-cookie import foundation (browser_cookies, os_credentials);
    # Phase 2D adds the pure offline auth-matrix readiness classifier
    # (auth_readiness); Phase 2F-A adds stdlib interactive browser/CDP primitives
    # (interactive_login); Phase 3A1 adds only the offline batchexecute fixture
    # parser (rpc); Phase 3A2 adds only offline notebook metadata models/service
    # over synthetic list payloads (notebooks); Phase 3A3 adds only an offline
    # fake RPC client seam over committed synthetic fixtures (fake_rpc); Phase
    # 3A4 hardens the existing rpc.py parser for strict offline chunk frames;
    # 3A5 promotes only fixture-backed CLI list/use wiring; Phase 3A6 adds
    # only a read-only fixture-backed Python client/notebooks API foothold; Phase
    # 3A7 adds only a synthetic fixture-backed Python client/chat ask foothold;
    # Phase 3A8 adds only a read-only fixture-backed Python client/sources API
    # foothold; Phase 3A9 adds only a read-only fixture-backed Python
    # client/notes API foothold; Phase 3A12 adds only a read-only fixture-backed
    # Python client/artifacts API foothold; Phase 3A15 adds only fixture-backed
    # CLI metadata rendering; Phase 3A16 adds only fixture-backed CLI history;
    # Phase 3A17 adds only fixture-backed CLI summary; Phase 3A18 adds only
    # fixture-backed source fulltext; Phase 3A19 adds only fixture-backed source
    # guide; Phase 3A20 adds only fixture-backed source stale/freshness over
    # existing source fixtures; Phase 3A21 adds only fixture-backed source wait
    # over existing source fixture status; Batch 3B1 adds only grouped
    # fixture-backed read/status surfaces (language/artifact/research/share);
    # Batch 3B2 adds in-memory mutation/generation/export API surfaces; Batch
    # 3B3 adds only fixture-backed generate CLI wiring; Batch 3B14 adds only
    # static bundled agent instruction display data/loader; Batch 3B17 adds
    # only the offline direct-comparison runtime and notebooklm_bare.rpc alias.
    # Phase 4 opens only the generated stdlib single-file artifact. Build
    # outputs, distributions, MCP adapters, and browser/live-runtime directories
    # remain forbidden.
    for forbidden in ("build", "dist", "mcp", "fastmcp"):
        assert not (repo_root / forbidden).exists(), (
            f"forbidden post-Phase-1 surface present: {forbidden}/"
        )
    package_modules = {p.name for p in (repo_root / "notebooklm").glob("*.py")}
    assert package_modules == {
        "__init__.py",
        "__main__.py",
        "cli.py",
        "errors.py",
        "output.py",
        "http_std.py",
        "async_transport.py",
        "lockfile.py",
        # Phase 2A auth/session/profile foundation:
        "profiles.py",
        "cookies.py",
        "auth.py",
        # Phase 2B offline browser-cookie import foundation:
        "browser_cookies.py",
        "os_credentials.py",
        # Phase 2D offline auth-matrix readiness classifier:
        "auth_readiness.py",
        # Phase 2F-A interactive browser/CDP primitives:
        "interactive_login.py",
        # Phase 3A1 offline batchexecute fixture parser:
        "rpc.py",
        # Phase 3A2 offline notebook metadata models/service:
        "notebooks.py",
        # Phase 3A3 offline fake RPC client seam:
        "fake_rpc.py",
        # Phase 3A6 offline Python client/notebooks API foothold:
        "client.py",
        # Phase 3A7 offline Python client/chat ask API foothold:
        "chat.py",
        # Phase 3A8 offline Python client/sources API foothold:
        "sources.py",
        # Phase 3A9 offline Python client/notes API foothold:
        "notes.py",
            # Phase 3A12 offline Python client/artifacts API foothold;
            # artifact payload helper mirrors pinned upstream wire shapes:
            "artifacts.py",
            "_artifacts_impl.py",
            "_artifact_payloads.py",
        # Phase 3B0 public API surface parity foundation:
        "exceptions.py",
        "_logging.py",
        "types.py",
        "urls.py",
        "log.py",
        "utils.py",
        "config.py",
        "io.py",
        "migration.py",
        "paths.py",
        "research.py",
        # Batch 3B1 offline/read-only status fixture seam:
        "offline_status.py",
        # Batch 3B14 static bundled agent instruction loader:
        "agent_templates.py",
        # Batch 3B17 offline direct-comparison runtime; Batch 3B18 opens only
        # existing client.py methods and introduces no additional runtime file.
        # Phase 5E opens only the packaged offline self-test module.
        "_parity_runtime.py",
        "self_test.py",
    }
    allowed_roots = {
        "compat",
        "docs",
        "notebooklm",
        "notebooklm_bare",
        "scripts",
        "tests",
    }
    allowed_singlefile = "singlefile/notebooklm_bare.py"
    allowed_root_files = {
        ".gitignore",
        "CHANGELOG.md",
        "README.md",
        "SECURITY.md",
        "notebooklm_bare.py",
    }
    offenders = []
    for p in _iter_repo_py_files(repo_root):
        rel = p.relative_to(repo_root)
        if len(rel.parts) == 1:
            if rel.name not in allowed_root_files:
                offenders.append(str(rel))
            continue
        if rel.parts[0] == "singlefile":
            if rel.as_posix() != allowed_singlefile:
                offenders.append(str(rel))
            continue
        if rel.parts[0] not in allowed_roots:
            offenders.append(str(rel))
    assert not offenders, f"unexpected runtime .py outside Phase 1 roots: {offenders}"


def test_denylist_includes_mcp_and_third_parties():
    deny = set(C.DENYLISTED_RUNTIME_IMPORTS)
    must_block = {
        "mcp",
        "fastmcp",
        "httpx",
        "click",
        "rich",
        "rookiepy",
        "playwright",
        "markdownify",
        "filelock",
        "requests",
        "selenium",
    }
    assert must_block <= deny, f"denylist missing: {sorted(must_block - deny)}"


def test_no_denylisted_or_mcp_imports_repo_wide(repo_root):
    # AST-level scan (string literals like the ones in *this* file are ignored) of
    # every project .py for any denylisted runtime import, including mcp/fastmcp.
    deny = set(C.DENYLISTED_RUNTIME_IMPORTS)
    violations = []
    for path in _iter_repo_py_files(repo_root):
        violations.extend(import_origin_audit.scan_file(str(path), deny))
    pretty = [
        f"{os.path.relpath(v['file'], repo_root)}:{v['line']}:{v.get('module')}"
        for v in violations
    ]
    assert not violations, f"denylisted/MCP imports found: {pretty}"


# --------------------------------------------------------------------------- #
# 15) No obvious secret / session material under compat/, scripts/, tests/
# --------------------------------------------------------------------------- #


def test_no_secret_material(repo_root):
    hits = []
    for path in _iter_files(
        repo_root / "compat",
        repo_root / "scripts",
        repo_root / "tests",
        repo_root / "notebooklm",
        repo_root / "singlefile",
        repo_root / "notebooklm_bare.py",
    ):
        if path.resolve() in _SECRET_SCAN_EXEMPT:
            continue  # this module defines the detector patterns themselves
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:  # pragma: no cover - defensive
            continue
        for label, rx in SECRET_PATTERNS:
            if rx.search(text):
                hits.append(f"{path.relative_to(repo_root)}: {label}")
    assert not hits, f"possible secret/session material: {hits}"
