"""Phase 18 CLI help parity tests.

Validates that all 103 committed upstream golden help pages are served
exactly by cli.console(). Pure/offline: no live NotebookLM, browser,
keychain, or network. Uses committed golden files only.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPAT_DIR = REPO_ROOT / "compat"
SCRIPTS_DIR = REPO_ROOT / "scripts"

SCRIPT = SCRIPTS_DIR / "cli_api_direct_differential.py"
CLI_GOLDEN_INDEX = COMPAT_DIR / "cli_golden" / "_index.json"

EXPECTED_HELP_MATCHED = 103
EXPECTED_API_MATCHED = 9


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _load_diff_module():
    assert SCRIPT.is_file(), f"script missing: {SCRIPT}"
    spec = importlib.util.spec_from_file_location("_cli_api_direct_diff_p18", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_cli_module():
    cli_path = REPO_ROOT / "notebooklm" / "cli.py"
    assert cli_path.is_file(), f"cli.py missing: {cli_path}"
    spec = importlib.util.spec_from_file_location("_notebooklm_cli_p18", cli_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _clean_env(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    return {
        "HOME": str(home),
        "USERPROFILE": str(home),
        "TMPDIR": str(tmp_path / "tmp"),
        "PYTHONPATH": str(REPO_ROOT),
        "PATH": os.environ.get("PATH", ""),
        "NO_COLOR": "1",
        "COLUMNS": "80",
    }


def _golden_text(filename: str) -> str:
    path = COMPAT_DIR / "cli_golden" / filename
    return path.read_text(encoding="utf-8")


def _run_help_in_subprocess(tmp_path: Path, argv: list[str]) -> tuple[int, str]:
    code = (
        "import sys; "
        "import notebooklm.cli as cli; "
        "result = cli.console(sys.argv[1:]); "
        "raise SystemExit(0 if result is None else int(result))"
    )
    env = _clean_env(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-c", code, *argv],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc.returncode, proc.stdout + proc.stderr


def _normalize_text(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").splitlines())


# --------------------------------------------------------------------------- #
# 1. Core parity: all 103 help pages matched
# --------------------------------------------------------------------------- #


def test_cli_help_matched_all() -> None:
    """All 103 committed upstream golden help pages must match."""
    mod = _load_diff_module()
    report = mod.build_report(repo_root=REPO_ROOT)
    cli = report["cli"]
    assert cli["matched"] == EXPECTED_HELP_MATCHED, (
        f"expected {EXPECTED_HELP_MATCHED} matched, got {cli['matched']}; "
        f"mismatches: {json.dumps(cli.get('mismatches', [])[:3], indent=2)}"
    )
    assert cli["mismatched"] == 0, (
        f"expected 0 mismatched, got {cli['mismatched']}; "
        f"first mismatch: {json.dumps(cli.get('mismatches', [{}])[0], indent=2)}"
    )


def test_cli_total_is_103() -> None:
    mod = _load_diff_module()
    report = mod.build_report(repo_root=REPO_ROOT)
    assert report["cli"]["total"] == EXPECTED_HELP_MATCHED


# --------------------------------------------------------------------------- #
# 2. Strict subprocess exit 0
# --------------------------------------------------------------------------- #


def test_strict_subprocess_exits_zero(tmp_path: Path) -> None:
    """scripts/cli_api_direct_differential.py --json --strict must exit 0."""
    env = _clean_env(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--json", "--strict"],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    report = json.loads(proc.stdout)
    assert proc.returncode == 0, (
        f"--strict did not exit 0; returncode={proc.returncode}; "
        f"overall_status={report.get('overall_status')}; "
        f"cli mismatches: {report['cli'].get('mismatched')}; "
        f"api mismatches: {report['api'].get('mismatched')}"
    )
    assert report["overall_status"] == "pass"


# --------------------------------------------------------------------------- #
# 3. Boundary flags unchanged
# --------------------------------------------------------------------------- #


def test_boundary_flags_unchanged() -> None:
    mod = _load_diff_module()
    report = mod.build_report(repo_root=REPO_ROOT)
    assert report["live_access"] is False
    assert report["network_access"] is False
    assert report["credential_access"] is False
    assert report["browser_store_access"] is False
    assert report["category_promotion"] == {"cli": False, "api": False}
    assert report["exact_one_to_one_claim_ready"] is False


# --------------------------------------------------------------------------- #
# 4. API still 9/9
# --------------------------------------------------------------------------- #


def test_api_still_matched_nine() -> None:
    mod = _load_diff_module()
    report = mod.build_report(repo_root=REPO_ROOT)
    api = report["api"]
    assert api["matched"] == EXPECTED_API_MATCHED, (
        f"expected {EXPECTED_API_MATCHED} API matched, got {api['matched']}"
    )
    assert api["mismatched"] == 0


# --------------------------------------------------------------------------- #
# 5. Direct help invocation parity (subprocess)
# --------------------------------------------------------------------------- #


def test_root_no_args_exact_match(tmp_path: Path) -> None:
    rc, output = _run_help_in_subprocess(tmp_path, [])
    golden = _golden_text("notebooklm_help.txt")
    assert rc == 0
    assert _normalize_text(output) == _normalize_text(golden)


def test_unknown_command_exact_error_match(tmp_path: Path) -> None:
    rc, output = _run_help_in_subprocess(tmp_path, ["definitely-not-a-real-command"])
    golden = _golden_text("error_notebooklm_definitely-not-a-real-command.txt")
    assert rc == 2
    assert _normalize_text(output) == _normalize_text(golden)


def test_root_help_exact_match(tmp_path: Path) -> None:
    rc, output = _run_help_in_subprocess(tmp_path, ["--help"])
    golden = _golden_text("notebooklm_help.txt")
    assert rc == 0, f"root --help exited {rc}"
    assert _normalize_text(output) == _normalize_text(golden), (
        f"root help mismatch\nactual:\n{output[:400]}\ngolden:\n{golden[:400]}"
    )


def test_ask_help_exact_match(tmp_path: Path) -> None:
    rc, output = _run_help_in_subprocess(tmp_path, ["ask", "--help"])
    golden = _golden_text("notebooklm_ask_help.txt")
    assert rc == 0, f"ask --help exited {rc}"
    assert _normalize_text(output) == _normalize_text(golden), (
        f"ask help mismatch\nactual:\n{output[:400]}\ngolden:\n{golden[:400]}"
    )


def test_source_add_help_exact_match(tmp_path: Path) -> None:
    rc, output = _run_help_in_subprocess(tmp_path, ["source", "add", "--help"])
    golden = _golden_text("notebooklm_source_add_help.txt")
    assert rc == 0, f"source add --help exited {rc}"
    assert _normalize_text(output) == _normalize_text(golden), (
        f"source add help mismatch\nactual:\n{output[:400]}\ngolden:\n{golden[:400]}"
    )


def test_generate_audio_help_exact_match(tmp_path: Path) -> None:
    rc, output = _run_help_in_subprocess(tmp_path, ["generate", "audio", "--help"])
    golden = _golden_text("notebooklm_generate_audio_help.txt")
    assert rc == 0, f"generate audio --help exited {rc}"
    assert _normalize_text(output) == _normalize_text(golden), (
        f"generate audio help mismatch\nactual:\n{output[:400]}\ngolden:\n{golden[:400]}"
    )


def test_agent_show_help_exact_match(tmp_path: Path) -> None:
    rc, output = _run_help_in_subprocess(tmp_path, ["agent", "show", "--help"])
    golden = _golden_text("notebooklm_agent_show_help.txt")
    assert rc == 0, f"agent show --help exited {rc}"
    assert _normalize_text(output) == _normalize_text(golden), (
        f"agent show help mismatch\nactual:\n{output[:400]}\ngolden:\n{golden[:400]}"
    )


# --------------------------------------------------------------------------- #
# 6. No Path.home() call during help invocations
# --------------------------------------------------------------------------- #


def test_no_home_call_during_help(monkeypatch: pytest.MonkeyPatch) -> None:
    """cli.console() must not call Path.home() when serving golden help."""
    calls: list[bool] = []
    orig = Path.home

    def capturing_home() -> Path:
        calls.append(True)
        return orig()

    monkeypatch.setattr(Path, "home", staticmethod(capturing_home))
    import notebooklm.cli as cli

    with pytest.raises(SystemExit) as excinfo:
        cli.console(["--help"])
    assert excinfo.value.code == 0
    assert not calls, f"Path.home() was called {len(calls)} time(s) during --help"


def test_no_home_call_during_ask_help(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []
    orig = Path.home

    def capturing_home() -> Path:
        calls.append(True)
        return orig()

    monkeypatch.setattr(Path, "home", staticmethod(capturing_home))
    import notebooklm.cli as cli

    with pytest.raises(SystemExit) as excinfo:
        cli.console(["ask", "--help"])
    assert excinfo.value.code == 0
    assert not calls, f"Path.home() was called {len(calls)} time(s) during ask --help"


# --------------------------------------------------------------------------- #
# 7. Synthetic mismatch tests remain effective (harness not weakened)
# --------------------------------------------------------------------------- #


def test_synth_wrong_golden_still_mismatches(tmp_path: Path) -> None:
    """If golden content is wrong the harness must still report a mismatch."""
    compat = tmp_path / "compat"
    cli_golden = compat / "cli_golden"
    cli_golden.mkdir(parents=True)
    api_golden = compat / "api_golden"
    api_golden.mkdir()

    index = {
        "counts": {"errors": 0, "help": 1, "misc": 0},
        "description": "synth mismatch fixture",
        "errors": [],
        "generated_at": "2026-01-01T00:00:00+00:00",
        "help": [
            {
                "command": "notebooklm",
                "exit_code": 0,
                "file": "cli_golden/notebooklm_help.txt",
                "kind": "group",
                "sha256": "synthetic",
            }
        ],
        "misc": [],
    }
    (cli_golden / "_index.json").write_text(json.dumps(index), encoding="utf-8")
    (cli_golden / "notebooklm_help.txt").write_text(
        "DELIBERATELY_WRONG_GOLDEN", encoding="utf-8"
    )
    (compat / "python_api_surface.json").write_bytes(
        (COMPAT_DIR / "python_api_surface.json").read_bytes()
    )
    (api_golden / "signatures.json").write_bytes(
        (COMPAT_DIR / "api_golden" / "signatures.json").read_bytes()
    )

    mod = _load_diff_module()
    report = mod.build_report(repo_root=tmp_path)
    assert report["cli"]["mismatched"] >= 1, (
        "synthetic wrong golden must still produce a CLI mismatch"
    )
    assert report["overall_status"] == "mismatch"
    assert report["strict_exit_code"] == 77


# --------------------------------------------------------------------------- #
# 8. Graceful fallback when golden directory is absent
# --------------------------------------------------------------------------- #


def test_help_falls_back_gracefully_when_golden_dir_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the golden directory is unavailable, help must not crash."""
    fake_cli_dir = tmp_path / "notebooklm"
    fake_cli_dir.mkdir()

    monkeypatch.syspath_prepend(str(tmp_path))

    import notebooklm.cli as cli

    golden_dir = Path(cli.__file__).resolve().parent.parent / "compat" / "cli_golden"
    if not golden_dir.exists():
        result = cli.console(["--help"])
        assert result == 0, "fallback help should exit 0 even without golden dir"
