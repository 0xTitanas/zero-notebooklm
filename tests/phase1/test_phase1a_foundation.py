"""Phase 1A stdlib-foundation tests.

These tests intentionally target only the Phase 1A slice:
package skeleton, argparse help, single-file help, output helpers, errors, and
import-origin audit coverage. They do not exercise live NotebookLM, Google auth,
cookies, browser automation, MCP, or RPC behavior.
"""

from __future__ import annotations

import contextlib
import importlib
import importlib.abc
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

import _phase0_constants as C  # noqa: E402
import import_origin_audit  # noqa: E402

REQUIRED_GLOBAL_FLAGS = ("--version", "--storage", "--profile", "--verbose", "--quiet")
DENYLIST = set(C.DENYLISTED_RUNTIME_IMPORTS) | {"aiohttp"}


class DenyThirdPartyFinder(importlib.abc.MetaPathFinder):
    """Fail if isolated imports try to resolve any denied third-party runtime."""

    def find_spec(self, fullname, path=None, target=None):  # noqa: D401 - protocol method
        if fullname.split(".", 1)[0] in DENYLIST:
            raise AssertionError(f"denylisted runtime import attempted: {fullname}")
        return None


def _isolated_env(tmp_path: Path) -> dict[str, str]:
    return {
        "HOME": str(tmp_path / "home"),
        "USERPROFILE": str(tmp_path / "home"),
        "TMPDIR": str(tmp_path),
        "PATH": "/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
        "LANG": "C.UTF-8",
    }


def test_package_imports_safely_under_denylisted_import_guard(repo_root, monkeypatch):
    """Package and Phase 1A modules import from the checkout with no third-party imports."""

    monkeypatch.syspath_prepend(str(repo_root))
    finder = DenyThirdPartyFinder()
    sys.meta_path.insert(0, finder)
    try:
        notebooklm = importlib.import_module("notebooklm")
        assert notebooklm.__version__ == C.TARGET_VERSION
        for mod_name in (
            "notebooklm.errors",
            "notebooklm.output",
            "notebooklm.cli",
        ):
            mod = importlib.import_module(mod_name)
            origin = Path(mod.__file__).resolve()
            assert origin.is_relative_to(repo_root)
    finally:
        sys.meta_path.remove(finder)


def test_cli_help_exposes_phase1a_global_options_without_claiming_parity(
    repo_root, monkeypatch
):
    monkeypatch.syspath_prepend(str(repo_root))
    cli = importlib.import_module("notebooklm.cli")

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    help_text = buf.getvalue()
    assert "usage:" in help_text.lower()
    assert "notebooklm" in help_text
    for flag in REQUIRED_GLOBAL_FLAGS:
        assert flag in help_text
    assert (
        "fixture-backed" in help_text.lower() or "not implemented" in help_text.lower()
    )
    assert "100%" not in help_text
    assert "full parity" not in help_text.lower()


def test_single_file_help_works_under_python_isolated_mode(repo_root, tmp_path):
    single = repo_root / "singlefile" / "zero_notebooklm.py"
    proc = subprocess.run(
        [sys.executable, "-B", "-I", "-S", str(single), "--help"],
        cwd=str(repo_root),
        env=_isolated_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 0, combined
    assert "usage:" in combined.lower()
    assert "notebooklm" in combined.lower()
    for flag in REQUIRED_GLOBAL_FLAGS:
        assert flag in combined
    assert "Traceback" not in combined


def test_output_helpers_render_json_and_plain(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))
    output = importlib.import_module("notebooklm.output")

    payload = {"status": "phase1", "items": [2, 1]}
    rendered_json = output.render(payload, json_mode=True)
    assert json.loads(rendered_json) == payload
    assert rendered_json.endswith("\n")
    assert rendered_json.splitlines()[0] == "{"

    rendered_plain = output.render(payload, json_mode=False)
    assert "status: phase1" in rendered_plain
    assert "items:" in rendered_plain
    assert rendered_plain.endswith("\n")


def test_errors_have_deterministic_exit_code_mapping(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))
    errors = importlib.import_module("notebooklm.errors")
    public = importlib.import_module("notebooklm")

    assert issubclass(errors.NotebookLMError, Exception)
    assert errors.exit_code_for(errors.NotImplementedInPhaseError("x")) == 78
    assert errors.exit_code_for(errors.AuthenticationError("x")) == 77
    assert errors.exit_code_for(errors.NetworkError("x")) == 69
    assert (
        errors.exit_code_for(
            public.ResearchTaskMismatchError(
                task_id="expected-row", source_research_task_id="other-row"
            )
        )
        == 64
    )
    assert errors.exit_code_for(Exception("x")) == 1


def test_console_catches_public_notebooklm_exceptions(repo_root, monkeypatch, capsys):
    monkeypatch.syspath_prepend(str(repo_root))
    cli = importlib.import_module("notebooklm.cli")
    public = importlib.import_module("notebooklm")

    def raise_public_error(argv):
        raise public.ResearchTaskMismatchError(
            task_id="expected-row", source_research_task_id="other-row"
        )

    monkeypatch.setattr(cli, "_try_golden_help", lambda argv: None)
    monkeypatch.setattr(cli, "main", raise_public_error)

    assert cli.console(["source", "add-research"]) == 64
    captured = capsys.readouterr()
    assert "research_task_id mismatch" in captured.err


def test_import_origin_audit_scans_phase1a_runtime_roots(repo_root):
    roots = tuple(C.AUDIT_ROOTS)
    assert "scripts" in roots and "tests" in roots
    assert "notebooklm" in roots
    assert "singlefile" in roots

    scanned = [
        Path(p).relative_to(repo_root).as_posix()
        for p in import_origin_audit._iter_python_files(roots)
    ]
    assert "notebooklm/__init__.py" in scanned
    assert "notebooklm/cli.py" in scanned
    assert "notebooklm/errors.py" in scanned
    assert "notebooklm/output.py" in scanned
    assert "singlefile/zero_notebooklm.py" in scanned

    violations = import_origin_audit.audit(roots=roots)
    assert violations == []


def test_isolated_import_origin_audit_script_passes(repo_root, tmp_path):
    proc = subprocess.run(
        [
            sys.executable,
            "-B",
            "-I",
            "-S",
            str(repo_root / "scripts" / "import_origin_audit.py"),
        ],
        cwd=str(repo_root),
        env=_isolated_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 0, combined
    assert "PASS: no denylisted third-party imports" in combined
    assert "notebooklm" in combined or "project file" in combined
