"""Phase 17 CLI/API direct differential audit tests.

Validates scripts/cli_api_direct_differential.py against committed golden
artifacts. Pure/offline: no live NotebookLM, browser, keychain, or network.
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

EXPECTED_HELP_COUNT = 103


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _load_module():
    assert SCRIPT.is_file(), f"script missing: {SCRIPT}"
    spec = importlib.util.spec_from_file_location("_cli_api_direct_diff_p17", SCRIPT)
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
        "PYTHONPATH": "",
        "PATH": os.environ.get("PATH", ""),
    }


def _make_minimal_compat(
    tmp_path: Path,
    *,
    cli_golden_text: str = "WRONG GOLDEN CONTENT",
    signatures_override: dict | None = None,
) -> Path:
    """Minimal compat fixture with one CLI golden entry and real API files."""
    compat = tmp_path / "compat"
    cli_golden = compat / "cli_golden"
    cli_golden.mkdir(parents=True)
    api_golden = compat / "api_golden"
    api_golden.mkdir()

    index = {
        "counts": {"errors": 0, "help": 1, "misc": 0},
        "description": "minimal fixture",
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
    (cli_golden / "notebooklm_help.txt").write_text(cli_golden_text, encoding="utf-8")

    (compat / "python_api_surface.json").write_bytes(
        (COMPAT_DIR / "python_api_surface.json").read_bytes()
    )
    sigs = signatures_override or json.loads(
        (COMPAT_DIR / "api_golden" / "signatures.json").read_text(encoding="utf-8")
    )
    (api_golden / "signatures.json").write_text(json.dumps(sigs), encoding="utf-8")
    return tmp_path


def _make_synth_api_mismatch(tmp_path: Path) -> Path:
    """Compat fixture with a fake extra method injected into artifacts subclient."""
    sigs = json.loads(
        (COMPAT_DIR / "api_golden" / "signatures.json").read_text(encoding="utf-8")
    )
    sigs["subclients"]["artifacts"]["async_methods"] = list(
        sigs["subclients"]["artifacts"]["async_methods"]
    ) + ["__nonexistent_method__"]
    sigs["subclients"]["artifacts"]["method_signatures"]["__nonexistent_method__"] = (
        "async def __nonexistent_method__(self) -> None:"
    )
    return _make_minimal_compat(
        tmp_path,
        cli_golden_text="WRONG",
        signatures_override=sigs,
    )


# --------------------------------------------------------------------------- #
# 1. Script exists and imports
# --------------------------------------------------------------------------- #


def test_script_exists() -> None:
    assert SCRIPT.is_file(), f"script missing: {SCRIPT}"


def test_script_imports() -> None:
    mod = _load_module()
    assert hasattr(mod, "build_report")
    assert hasattr(mod, "main")
    assert hasattr(mod, "SCHEMA_VERSION")


# --------------------------------------------------------------------------- #
# 2. build_report does not call Path.home()
# --------------------------------------------------------------------------- #


def test_build_report_no_home_call(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []
    orig = Path.home

    def capturing_home() -> Path:
        calls.append(True)
        return orig()

    monkeypatch.setattr(Path, "home", staticmethod(capturing_home))
    mod = _load_module()
    mod.build_report(repo_root=REPO_ROOT)
    assert not calls, "build_report() called Path.home()"


# --------------------------------------------------------------------------- #
# 3. JSON report boundary flags
# --------------------------------------------------------------------------- #


def test_report_boundary_flags() -> None:
    mod = _load_module()
    report = mod.build_report(repo_root=REPO_ROOT)
    assert report["live_access"] is False
    assert report["network_access"] is False
    assert report["credential_access"] is False
    assert report["browser_store_access"] is False
    assert report["category_promotion"] == {"cli": False, "api": False}
    assert report["exact_one_to_one_claim_ready"] is False


def test_report_schema_version() -> None:
    mod = _load_module()
    report = mod.build_report(repo_root=REPO_ROOT)
    assert report["schema_version"] == "cli_api_direct_differential/1"
    assert report["target"] == "notebooklm-py==0.7.2"


def test_report_overall_status_valid() -> None:
    mod = _load_module()
    report = mod.build_report(repo_root=REPO_ROOT)
    assert report["overall_status"] in ("pass", "mismatch")
    assert report["strict_exit_code"] in (0, 77)


def test_row_evidence_links_phase16_manifest() -> None:
    mod = _load_module()
    report = mod.build_report(repo_root=REPO_ROOT)
    row_evidence = report["row_evidence"]
    assert row_evidence["manifest_present"] is True
    assert row_evidence["cli_rows_mapped"] == 90
    assert row_evidence["api_rows_mapped"] == 9
    assert row_evidence["api_scenarios_mapped"] == 108
    assert row_evidence["promotion_allowed"] is False


# --------------------------------------------------------------------------- #
# 4. Clean HOME subprocess --json does not leak temp home path
# --------------------------------------------------------------------------- #


def test_subprocess_json_exits_without_error(tmp_path: Path) -> None:
    env = _clean_env(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode in (0, 77), (
        f"unexpected exit code {proc.returncode}\n"
        f"stderr: {proc.stderr[:400]}\nstdout: {proc.stdout[:200]}"
    )
    report = json.loads(proc.stdout)
    assert report["exact_one_to_one_claim_ready"] is False


def test_subprocess_json_no_home_path_leak(tmp_path: Path) -> None:
    env = _clean_env(tmp_path)
    clean_home = tmp_path / "home"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    out = proc.stdout
    assert str(clean_home) not in out, "temp home path leaked into JSON output"
    assert "/".join(("", "Users", "")) not in out
    assert "/".join(("", "home", "")) not in out


# --------------------------------------------------------------------------- #
# 5. CLI golden help coverage total count matches _index.json
# --------------------------------------------------------------------------- #


def test_cli_total_matches_index_count() -> None:
    index = json.loads(CLI_GOLDEN_INDEX.read_text(encoding="utf-8"))
    mod = _load_module()
    report = mod.build_report(repo_root=REPO_ROOT)
    assert report["cli"]["total"] == len(index["help"])
    assert report["cli"]["total"] == EXPECTED_HELP_COUNT


def test_cli_error_and_misc_goldens_are_audited() -> None:
    index = json.loads(CLI_GOLDEN_INDEX.read_text(encoding="utf-8"))
    mod = _load_module()
    report = mod.build_report(repo_root=REPO_ROOT)
    error_probe = report["cli"]["error_probe"]
    misc_probe = report["cli"]["misc_probe"]
    assert error_probe["total"] == index["counts"]["errors"]
    assert error_probe["matched"] == index["counts"]["errors"]
    assert error_probe["mismatched"] == 0
    assert misc_probe["total"] == index["counts"]["misc"]
    assert misc_probe["matched"] == index["counts"]["misc"]
    assert misc_probe["mismatched"] == 0


def test_cli_total_equals_index_counts_help() -> None:
    index = json.loads(CLI_GOLDEN_INDEX.read_text(encoding="utf-8"))
    mod = _load_module()
    report = mod.build_report(repo_root=REPO_ROOT)
    assert report["cli"]["total"] == index["counts"]["help"]


def test_cli_matched_plus_mismatched_equals_total() -> None:
    mod = _load_module()
    report = mod.build_report(repo_root=REPO_ROOT)
    cli = report["cli"]
    assert cli["matched"] + cli["mismatched"] == cli["total"]
    assert len(cli["mismatches"]) == cli["mismatched"]


# --------------------------------------------------------------------------- #
# 6. Synthetic CLI mismatch is detected
# --------------------------------------------------------------------------- #


def test_cli_mismatch_detected_with_wrong_golden(tmp_path: Path) -> None:
    root = _make_minimal_compat(tmp_path, cli_golden_text="WRONG GOLDEN CONTENT")
    mod = _load_module()
    report = mod.build_report(repo_root=root)
    assert report["cli"]["mismatched"] >= 1, (
        "expected at least one CLI mismatch when golden content is wrong"
    )
    assert report["overall_status"] == "mismatch"
    assert report["strict_exit_code"] == 77


def test_cli_mismatch_entries_have_required_fields(tmp_path: Path) -> None:
    root = _make_minimal_compat(tmp_path, cli_golden_text="WRONG")
    mod = _load_module()
    report = mod.build_report(repo_root=root)
    for mm in report["cli"]["mismatches"]:
        assert "command" in mm
        assert "file" in mm
        assert "expected_exit_code" in mm
        assert "actual_exit_code" in mm
        assert "expected_sha256" in mm
        assert "actual_sha256" in mm
        assert "normalized_match" in mm
        assert "expected_excerpt" in mm
        assert "actual_excerpt" in mm


# --------------------------------------------------------------------------- #
# 7. Synthetic API signature mismatch is detected
# --------------------------------------------------------------------------- #


def test_api_mismatch_detected_with_extra_method(tmp_path: Path) -> None:
    root = _make_synth_api_mismatch(tmp_path)
    mod = _load_module()
    report = mod.build_report(repo_root=root)
    assert report["api"]["mismatched"] >= 1, (
        "expected at least one API mismatch when a fake method is injected"
    )


def test_api_mismatch_does_not_affect_overall_when_cli_also_mismatches(
    tmp_path: Path,
) -> None:
    root = _make_synth_api_mismatch(tmp_path)
    mod = _load_module()
    report = mod.build_report(repo_root=root)
    assert report["overall_status"] == "mismatch"
    assert report["strict_exit_code"] == 77


def test_api_matched_plus_mismatched_equals_total() -> None:
    mod = _load_module()
    report = mod.build_report(repo_root=REPO_ROOT)
    api = report["api"]
    assert api["matched"] + api["mismatched"] == api["total"]
    assert len(api["mismatches"]) == api["mismatched"]


def test_api_public_names_present() -> None:
    mod = _load_module()
    report = mod.build_report(repo_root=REPO_ROOT)
    pn = report["api"]["public_names"]
    assert "expected" in pn
    assert "actual" in pn
    assert "missing" in pn
    assert "extra" in pn
    assert "match" in pn
    assert isinstance(pn["match"], bool)


# --------------------------------------------------------------------------- #
# 8. --strict exits consistently with strict_exit_code
# --------------------------------------------------------------------------- #


def test_strict_exit_code_consistent_with_report(tmp_path: Path) -> None:
    env = _clean_env(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--json", "--strict"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    report = json.loads(proc.stdout)
    assert proc.returncode == report["strict_exit_code"], (
        f"exit code {proc.returncode} != strict_exit_code {report['strict_exit_code']}"
    )
    assert report["strict_exit_code"] in (0, 77)


def test_no_strict_exits_zero_regardless_of_status(tmp_path: Path) -> None:
    env = _clean_env(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"without --strict, exit code must be 0; got {proc.returncode}"
    )
