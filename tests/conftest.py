"""Pytest fixtures and path setup for the NotebookLM Bare Phase 0 test suite.

This file is deliberately **stdlib-only**. It never imports ``notebooklm`` (the
upstream oracle is introspected offline into ``compat/`` — the tests assert
against those frozen artifacts, not against a live import). It puts ``scripts/``
on ``sys.path`` so the Phase 0 harness modules (``_phase0_constants``,
``import_origin_audit``) — which are themselves stdlib-only — can be imported and
cross-checked, then exposes session-cached loaders for every committed artifact so
each JSON/markdown file is read and parsed exactly once per run.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
COMPAT_DIR = REPO_ROOT / "compat"
TESTS_DIR = REPO_ROOT / "tests"

# Make the stdlib-only Phase 0 harness importable (single source of truth for the
# pinned target lives in scripts/_phase0_constants.py). No third-party import.
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def pytest_addoption(parser):
    """Accept the verification gate's ``--timeout`` option without a plugin."""

    group = parser.getgroup("zero-notebooklm")
    try:
        group.addoption(
            "--timeout",
            action="store",
            default=None,
            type=float,
            help="Per-test timeout in seconds (stdlib SIGALRM implementation).",
        )
    except (ValueError, argparse.ArgumentError):
        # pytest-timeout or another plugin already registered the option.
        pass


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    """Small stdlib fallback for pytest-timeout's per-test alarm."""

    timeout = item.config.getoption("timeout", default=None)
    if (
        timeout is None
        or not hasattr(signal, "SIGALRM")
        or threading.current_thread() is not threading.main_thread()
    ):
        yield
        return

    previous = signal.getsignal(signal.SIGALRM)

    def _raise_timeout(_signum, _frame):
        raise TimeoutError(f"test exceeded --timeout={timeout:g}s")

    signal.signal(signal.SIGALRM, _raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, timeout)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def load_json(rel: str):
    """Parse a compat/ JSON artifact. Asserts presence with a clear message."""
    path = COMPAT_DIR / rel
    assert path.is_file(), f"required compat artifact missing: compat/{rel}"
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def read_text(rel: str) -> str:
    """Read a compat/ text artifact. Asserts presence with a clear message."""
    path = COMPAT_DIR / rel
    assert path.is_file(), f"required compat artifact missing: compat/{rel}"
    return path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Session-scoped artifact fixtures (parsed once)
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def compat_dir() -> Path:
    return COMPAT_DIR


@pytest.fixture(scope="session")
def oracle():
    return load_json("notebooklm_py_0_7_2_oracle.json")


@pytest.fixture(scope="session")
def cli_surface():
    return load_json("cli_surface.json")


@pytest.fixture(scope="session")
def python_api():
    return load_json("python_api_surface.json")


@pytest.fixture(scope="session")
def auth_matrix():
    return load_json("auth_matrix.json")


@pytest.fixture(scope="session")
def dep_graph():
    return load_json("dependency_graph.json")


@pytest.fixture(scope="session")
def wire_shape():
    return load_json("rpc_fixtures/wire_shape.json")


@pytest.fixture(scope="session")
def cli_index():
    return load_json("cli_golden/_index.json")


@pytest.fixture(scope="session")
def enums_golden():
    return load_json("api_golden/enums.json")


@pytest.fixture(scope="session")
def exceptions_golden():
    return load_json("api_golden/exceptions.json")


@pytest.fixture(scope="session")
def signatures_golden():
    return load_json("api_golden/signatures.json")


@pytest.fixture(scope="session")
def imports_golden() -> list[str]:
    return [ln for ln in read_text("api_golden/imports.txt").splitlines() if ln.strip()]


@pytest.fixture(scope="session")
def parity_md() -> str:
    return read_text("parity_matrix.md")
