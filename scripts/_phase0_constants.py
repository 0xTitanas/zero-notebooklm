"""Single source of truth for the ZeroNotebookLM Phase 0 compatibility oracle.

This module is intentionally **stdlib-only**. It is imported by both the oracle
generator/validator (``run_phase0_oracle.py``) and the Phase 0 test suite so the
pinned upstream target is asserted from one place and can never drift between the
generator and the checks.

Phase 0 freezes the *exact* upstream behavior target before any replacement
functionality is written:

    upstream oracle  =  notebooklm-py == 0.7.2
    wheel SHA-256    =  d850cfea2494732bc5f153406a9637c3ee5fe931d87901d101c026df2a6ecf65
    source commit    =  915b5321e1c1f411e23bd8265517be8740749e56

Nothing here imports a third-party package, so this file is also exercised by the
import-origin denylist audit with zero exceptions.
"""

from __future__ import annotations

from pathlib import Path

# --------------------------------------------------------------------------- #
# Pinned upstream target (immutable for the lifetime of the 0.7.2 oracle)
# --------------------------------------------------------------------------- #

TARGET_PROJECT = "notebooklm-py"
TARGET_VERSION = "0.7.2"
TARGET_REQUIREMENT = "notebooklm-py==0.7.2"

WHEEL_FILENAME = "notebooklm_py-0.7.2-py3-none-any.whl"
WHEEL_SHA256 = "d850cfea2494732bc5f153406a9637c3ee5fe931d87901d101c026df2a6ecf65"

SOURCE_REPO = "teng-lin/notebooklm-py"
SOURCE_COMMIT = "915b5321e1c1f411e23bd8265517be8740749e56"

# SHA-256 of the canonical planning artifact kept outside the public repository.
PLAN_SHA256 = "176f14caa9d44b0f86697f6f57fc2b4f6aa16d28b3ad8fcf0d49f34302e3b998"

REQUIRES_PYTHON = ">=3.10"

# Distribution name as PyPI normalizes it (used for entry-point / metadata lookups).
TARGET_DIST_METADATA_NAME = "notebooklm-py"
CLI_ENTRY_POINT = "notebooklm = notebooklm.notebooklm_cli:main"
CLI_ROOT_IMPORT = "notebooklm.notebooklm_cli:cli"

# --------------------------------------------------------------------------- #
# Selected parity profile + declared matrices
# --------------------------------------------------------------------------- #

# Every extra advertised by the pinned wheel (Provides-Extra in METADATA).
ALL_UPSTREAM_EXTRAS = ("all", "browser", "cookies", "dev", "markdown")

# The parity profile this project targets: base package plus the documented
# browser / cookies / markdown feature surfaces. `dev` and `all` are tooling-only.
PARITY_PROFILE_EXTRAS = ("browser", "cookies", "markdown")

PYTHON_MATRIX = ("3.10", "3.11", "3.12", "3.13")
OS_MATRIX = ("macOS", "Ubuntu-LTS-Linux", "Windows-11")

# Explicit parity-profile exclusions. These selector/OS combinations are
# intentionally outside ZeroNotebookLM's supported auth matrix; they are removed
# rather than treated as passing, blocked, or not-applicable parity rows. Arc on
# Ubuntu is a project supported-platform decision; Safari on Ubuntu is unsupported.
AUTH_COOKIE_PROFILE_EXCLUSIONS = (
    ("arc", "macOS"),
    ("arc", "Ubuntu-LTS-Linux"),
    ("arc", "Windows-11"),
    ("safari", "Ubuntu-LTS-Linux"),
    ("safari", "Windows-11"),
)

# Opera GX/Ubuntu generic-reader and modern Windows Chromium profile paths cannot
# execute in the pinned upstream. Chromium/Vivaldi macOS cookie paths are deferred
# from the current release profile. The Opera GX explicit-profile path remains.
AUTH_COOKIE_PATH_PROFILE_EXCLUSIONS = (
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
)

AUTH_COOKIE_PATH_PROFILE_EXCLUSION_REASONS = {
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

# Interactive-login browser choices are fixed to the upstream-documented set.
# Verified against the real Click ``login --browser`` Choice in 0.7.2.
INTERACTIVE_LOGIN_BROWSERS = ("chromium", "chrome", "msedge")

# --------------------------------------------------------------------------- #
# Click-tree surface lock (verified against real upstream 0.7.2)
# --------------------------------------------------------------------------- #

EXPECTED_CLI_NODES = 103  # total nodes including the root group
EXPECTED_CLI_LEAVES = 90  # leaf (non-group) commands
EXPECTED_CLI_GROUPS = 13  # groups including the root group

# --------------------------------------------------------------------------- #
# Parity-matrix closure states (pass-only closure; open/blocked are not success)
# --------------------------------------------------------------------------- #

PARITY_STATES = ("pass", "open", "blocked")
PHASE0_INITIAL_STATE = "open"

# Required parity-matrix categories. Rows are seeded as `open` during artifact
# generation and may later move to `pass`/`blocked` only with row-specific evidence.
PARITY_CATEGORIES = (
    "cli",
    "api",
    "auth",
    "rpc",
    "offline",
    "self-test",
)

# --------------------------------------------------------------------------- #
# Third-party runtime denylist (JMC-NLB-002)
# --------------------------------------------------------------------------- #
#
# These are *import names* (not PyPI names) that bare runtime/harness code must
# never import. `notebooklm` itself is deliberately absent: in Phase 0 it is the
# upstream oracle being introspected inside a disposable venv, not a bare runtime
# dependency. The Zero-side audit scans project files for these names via AST.
DENYLISTED_RUNTIME_IMPORTS = (
    "httpx",
    "httpcore",
    "h11",
    "requests",
    "click",
    "rich",
    "filelock",
    "rookiepy",
    "playwright",
    "selenium",
    "websockets",
    "fastmcp",
    "mcp",
    "pydantic",
    "anyio",
    "sniffio",
    "starlette",
    "uvicorn",
    "bs4",
    "lxml",
    "markdownify",
    "dotenv",  # python-dotenv
    "certifi",
    "markdown_it",  # markdown-it-py (rich dependency)
)

# PyPI-name -> import-name hints, recorded in the dependency graph for clarity.
PYPI_TO_IMPORT_NAME = {
    "python-dotenv": "dotenv",
    "beautifulsoup4": "bs4",
    "markdown-it-py": "markdown_it",
    "notebooklm-py": "notebooklm",
}

# --------------------------------------------------------------------------- #
# Repo-relative paths
# --------------------------------------------------------------------------- #

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPAT_DIR = REPO_ROOT / "compat"
CLI_GOLDEN_DIR = COMPAT_DIR / "cli_golden"
API_GOLDEN_DIR = COMPAT_DIR / "api_golden"
RPC_FIXTURE_DIR = COMPAT_DIR / "rpc_fixtures"
SCRIPTS_DIR = REPO_ROOT / "scripts"
TESTS_DIR = REPO_ROOT / "tests"

ORACLE_JSON = COMPAT_DIR / "notebooklm_py_0_7_2_oracle.json"
CLI_SURFACE_JSON = COMPAT_DIR / "cli_surface.json"
PYTHON_API_SURFACE_JSON = COMPAT_DIR / "python_api_surface.json"
AUTH_MATRIX_JSON = COMPAT_DIR / "auth_matrix.json"
DEPENDENCY_GRAPH_JSON = COMPAT_DIR / "dependency_graph.json"
PARITY_MATRIX_MD = COMPAT_DIR / "parity_matrix.md"

# Files the Phase 0 exit gate requires to exist and parse.
REQUIRED_COMPAT_JSON = (
    ORACLE_JSON,
    CLI_SURFACE_JSON,
    PYTHON_API_SURFACE_JSON,
    AUTH_MATRIX_JSON,
    DEPENDENCY_GRAPH_JSON,
)

# Project roots scanned by the import-origin denylist audit.
AUDIT_ROOTS = (
    "scripts",
    "tests",
    "notebooklm",
    "singlefile",
)

# Schema version stamped into every generated artifact.
ORACLE_SCHEMA_VERSION = "phase0/1"
