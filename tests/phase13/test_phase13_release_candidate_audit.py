"""Phase 13 release-candidate parity closure audit tests.

Tests scripts/release_candidate_audit.py without modifying parity_matrix.md,
browser stores, keychains, live NotebookLM state, or operator-local config.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"

_REQUIRED_BLOCKERS = frozenset(
    {
        "auth_category_open",
        "live_readonly_differential_not_authorized",
        "live_mutation_smoke_not_authorized",
    }
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _load_module():
    script = SCRIPTS_DIR / "release_candidate_audit.py"
    spec = importlib.util.spec_from_file_location("_rc_audit", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _clean_env(tmp_path: Path) -> dict[str, str]:
    clean_home = tmp_path / "home"
    clean_home.mkdir()
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    return {
        "HOME": str(clean_home),
        "USERPROFILE": str(clean_home),
        "TMPDIR": str(tmp_dir),
        "PYTHONPATH": "",
        "PATH": os.environ.get("PATH", ""),
    }


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


def test_build_report_no_path_home_call(repo_root: Path, monkeypatch) -> None:
    """1. build_report(repo_root=...) is pure/pathless and does not call Path.home()."""

    def _forbidden_home():
        raise AssertionError("build_report must not call Path.home()")

    monkeypatch.setattr(Path, "home", staticmethod(_forbidden_home))

    mod = _load_module()
    report = mod.build_report(repo_root=repo_root)

    assert report is not None
    assert "release_candidate_ready" in report


def test_local_gate_pass_release_not_ready(repo_root: Path) -> None:
    """2. local_gate_status=pass, release_candidate_ready=False, one_to_one_functionality_claim=False."""
    mod = _load_module()
    report = mod.build_report(repo_root=repo_root)

    assert report["local_gate_status"] == "pass"
    assert report["release_candidate_ready"] is False
    assert report["one_to_one_functionality_claim"] is False


def test_selected_profile_is_ready_for_public_alpha(repo_root: Path) -> None:
    mod = _load_module()
    report = mod.build_report(repo_root=repo_root)

    assert report["public_alpha_ready"] is True
    assert report["public_alpha_blockers"] == []
    assert report["one_to_one_functionality_claim"] is False


def test_remaining_blockers_contains_required(repo_root: Path) -> None:
    """3. Report contains all required remaining blockers."""
    mod = _load_module()
    report = mod.build_report(repo_root=repo_root)

    actual = set(report["remaining_blockers"])
    missing = _REQUIRED_BLOCKERS - actual
    assert not missing, f"missing blockers: {sorted(missing)}"


def test_local_gates_summarize_actual_prior_audit_reports(repo_root: Path) -> None:
    """4. Phase 13 is an integration audit over the actual prior gate reports."""
    mod = _load_module()

    report = mod.build_report(repo_root=repo_root)
    gates = report["local_gates"]

    assert set(gates) == {
        "cli_api",
        "cli_behavior",
        "api_behavior",
        "rpc_drift",
        "auth_evidence",
        "auth_row_evidence",
        "parity_readiness",
        "live_readonly_differential",
        "live_auth_evidence",
        "live_mutation_evidence",
        "pass_row_evidence",
        "cli_api_row_evidence",
        "cli_api_direct_differential",
    }
    assert all(gate["ok"] is True for gate in gates.values())
    assert gates["cli_behavior"]["summary"]["passed"] == 90
    assert gates["api_behavior"]["summary"]["passed"] == 108
    assert gates["auth_evidence"]["summary"]["total_rows"] == 146
    assert gates["live_readonly_differential"]["status"] == "skipped_expected"


def test_prior_gate_failure_makes_local_gate_fail(repo_root: Path, monkeypatch) -> None:
    """5. local_gate_status must be derived from prior gates, not assumed by counts."""
    mod = _load_module()

    def _failed_cli_api_gate(_repo_root: Path) -> dict[str, object]:
        return {
            "ok": False,
            "status": "fail",
            "summary": {"reason": "synthetic_cli_api_failure"},
        }

    monkeypatch.setattr(mod, "_run_cli_api_gate", _failed_cli_api_gate)

    report = mod.build_report(repo_root=repo_root)

    assert report["local_gate_status"] == "fail"
    assert report["release_candidate_ready"] is False
    assert "local_gate_cli_api_failed" in report["remaining_blockers"]


def test_prior_gate_exception_fails_closed_without_traceback_or_message_leak(
    repo_root: Path, monkeypatch
) -> None:
    """Gate exceptions must produce a blocked report, not crash while summarizing."""
    mod = _load_module()

    def _raising_cli_behavior_gate(_repo_root: Path) -> dict[str, object]:
        raise RuntimeError("SHOULD-NOT-LEAK-SYNTHETIC-DETAIL")

    monkeypatch.setattr(mod, "_run_cli_behavior_gate", _raising_cli_behavior_gate)

    report = mod.build_report(repo_root=repo_root)
    report_json = json.dumps(report)

    assert report["local_gate_status"] == "fail"
    assert report["release_candidate_ready"] is False
    assert report["strict_exit_code"] == 77
    assert report["local_gates"]["cli_behavior"]["status"] == "error"
    assert report["local_gates"]["cli_behavior"]["summary"] == {
        "error_type": "RuntimeError"
    }
    assert report["gate_summary"]["cli_behavior_coverage"] == "0/0"
    assert "local_gate_cli_behavior_failed" in report["remaining_blockers"]
    assert "SHOULD-NOT-LEAK-SYNTHETIC-DETAIL" not in report_json


def test_parity_matrix_not_mutated_reports_cli_api_promotion(repo_root: Path) -> None:
    """6. build_report does not mutate parity_matrix.md and reports existing CLI/API promotion."""
    parity_path = repo_root / "compat" / "parity_matrix.md"
    before = parity_path.read_bytes()

    mod = _load_module()
    report = mod.build_report(repo_root=repo_root)

    assert parity_path.read_bytes() == before, "parity_matrix.md was mutated"
    assert report["parity_rows_promoted"] == ["cli:pass", "api:pass", "rpc:pass"]
    assert report["category_promotion"] == {
        "cli": True,
        "api": True,
        "auth": False,
        "rpc": True,
    }


def test_cli_json_clean_home_no_path_leak(tmp_path: Path) -> None:
    """5. CLI --json works from clean temp HOME and does not leak the home path."""
    env = _clean_env(tmp_path)
    clean_home = tmp_path / "home"

    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "release_candidate_audit.py"), "--json"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, f"stderr: {proc.stderr[:500]}"
    output = proc.stdout
    assert str(clean_home) not in output, "temp home path leaked into JSON output"
    report = json.loads(output)
    assert "release_candidate_ready" in report


def test_cli_json_strict_exits_77(tmp_path: Path) -> None:
    """6. CLI --json --strict exits 77 while not release candidate ready."""
    env = _clean_env(tmp_path)

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "release_candidate_audit.py"),
            "--json",
            "--strict",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 77
    report = json.loads(proc.stdout)
    assert report["strict_exit_code"] == 77
    assert report["release_candidate_ready"] is False


def test_human_output_blocked_no_paths(tmp_path: Path) -> None:
    """7. Human output contains clear blocked status and no absolute paths."""
    env = _clean_env(tmp_path)

    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "release_candidate_audit.py")],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, f"stderr: {proc.stderr[:500]}"
    output = proc.stdout
    assert "blocked" in output.lower(), "human output must contain 'blocked'"
    assert re.search(
        r"(?<![:/\w])/(?:[A-Za-z0-9._-]+/)+[A-Za-z0-9._-]+", output
    ) is None, "absolute path leaked into human output"


def test_live_differential_incorporated_as_skipped(repo_root: Path) -> None:
    """8. Report incorporates Phase 12 default live differential as skipped/not authorized."""
    mod = _load_module()
    report = mod.build_report(repo_root=repo_root)

    live_diff = report.get("live_readonly_differential", {})
    assert live_diff.get("status") == "skipped", (
        f"expected status=skipped, got {live_diff.get('status')!r}"
    )
    assert live_diff.get("live_enabled") is False
    assert "live_readonly_differential_not_authorized" in report["remaining_blockers"]


def test_release_candidate_summarizes_pass_row_evidence_gate(repo_root: Path) -> None:
    """9. pass_row_evidence in local_gates shows exact counts and no issues."""
    mod = _load_module()
    report = mod.build_report(repo_root=repo_root)
    rows = json.loads(
        (repo_root / "compat" / "parity_rows.json").read_text(encoding="utf-8")
    )["rows"]
    evidence_records = json.loads(
        (repo_root / "compat" / "parity_evidence.json").read_text(encoding="utf-8")
    )["evidence_records"]
    expected_pass_rows = sum(1 for row in rows if row.get("status") == "pass")

    assert "pass_row_evidence" in report["local_gates"]
    gate = report["local_gates"]["pass_row_evidence"]
    summary = gate["summary"]

    assert summary["strict_ok"] is True
    assert summary["pass_rows_audited"] == expected_pass_rows
    assert summary["evidence_records_count"] == len(evidence_records)
    assert summary["rows_missing_evidence"] == []
    assert summary["rows_with_unmet_tokens"] == []

    gs = report["gate_summary"]
    assert gs["parity_evidence_pass_rows"] == expected_pass_rows
    assert gs["parity_evidence_strict_ok"] is True


def test_pass_row_evidence_gate_failure_makes_local_gate_fail(
    repo_root: Path, monkeypatch
) -> None:
    """10. Failing pass_row_evidence gate propagates to local_gate_status=fail and adds blocker."""
    mod = _load_module()

    def _failing_gate(_repo_root: Path) -> dict[str, object]:
        return {
            "ok": False,
            "status": "fail",
            "summary": {
                "strict_ok": False,
                "pass_rows_audited": 7,
                "evidence_records_count": 7,
                "rows_missing_evidence": ["offline.synthetic"],
                "rows_with_unmet_tokens": [],
            },
        }

    monkeypatch.setattr(mod, "_run_pass_row_evidence_gate", _failing_gate)
    report = mod.build_report(repo_root=repo_root)

    assert report["local_gate_status"] == "fail"
    assert report["release_candidate_ready"] is False
    assert "local_gate_pass_row_evidence_failed" in report["remaining_blockers"]


def test_release_candidate_summarizes_cli_api_row_evidence_gate(
    repo_root: Path,
) -> None:
    """11. cli_api_row_evidence local gate maps all promoted CLI/API rows with evidence."""
    mod = _load_module()
    report = mod.build_report(repo_root=repo_root)

    assert "cli_api_row_evidence" in report["local_gates"]
    gate = report["local_gates"]["cli_api_row_evidence"]
    summary = gate["summary"]

    assert gate["ok"] is True
    assert summary["strict_ok"] is True
    assert summary["cli_rows_mapped"] == 90
    assert summary["api_rows_mapped"] == 9
    assert summary["api_scenarios_mapped"] == 108
    assert summary["exact_one_to_one_claim_ready"] is False
    assert summary["errors"] == []
    assert summary["warnings"] == []

    gs = report["gate_summary"]
    assert gs["cli_api_row_evidence_cli_rows"] == 90
    assert gs["cli_api_row_evidence_api_rows"] == 9
    assert gs["cli_api_row_evidence_api_scenarios"] == 108
    assert gs["cli_api_row_evidence_strict_ok"] is True
    assert report["one_to_one_functionality_claim"] is False


def test_cli_api_row_evidence_gate_failure_makes_local_gate_fail(
    repo_root: Path, monkeypatch
) -> None:
    """12. Failing cli_api_row_evidence gate propagates to local_gate_status=fail."""
    mod = _load_module()

    def _failing_gate(_repo_root: Path) -> dict[str, object]:
        return {
            "ok": False,
            "status": "fail",
            "summary": {
                "strict_ok": False,
                "cli_rows_mapped": 89,
                "api_rows_mapped": 9,
                "api_scenarios_mapped": 108,
                "exact_one_to_one_claim_ready": False,
                "errors": ["synthetic missing CLI row"],
                "warnings": [],
            },
        }

    monkeypatch.setattr(mod, "_run_cli_api_row_evidence_gate", _failing_gate)
    report = mod.build_report(repo_root=repo_root)

    assert report["local_gate_status"] == "fail"
    assert report["release_candidate_ready"] is False
    assert "local_gate_cli_api_row_evidence_failed" in report["remaining_blockers"]


def test_release_candidate_summarizes_cli_api_direct_differential_gate(
    repo_root: Path,
) -> None:
    """13. cli_api_direct_differential local gate captures direct CLI/API comparison as evidence."""
    mod = _load_module()
    report = mod.build_report(repo_root=repo_root)

    assert "cli_api_direct_differential" in report["local_gates"]
    gate = report["local_gates"]["cli_api_direct_differential"]
    summary = gate["summary"]

    assert gate["ok"] is True
    assert gate["status"] in {"pass", "mismatch_expected"}
    assert summary["overall_status"] in {"pass", "mismatch"}
    assert summary["strict_exit_code"] in {0, 77}
    assert summary["cli_total"] == 103
    assert summary["api_total"] == 9
    assert summary["api_matched"] == 9
    assert summary["exact_one_to_one_claim_ready"] is False
    assert summary["row_evidence"]["cli_rows_mapped"] == 90
    assert summary["row_evidence"]["api_rows_mapped"] == 9
    assert summary["row_evidence"]["api_scenarios_mapped"] == 108

    gs = report["gate_summary"]
    assert gs["cli_api_direct_differential_status"] == summary["overall_status"]
    assert gs["cli_api_direct_differential_cli_total"] == 103
    assert gs["cli_api_direct_differential_api_total"] == 9
    assert report["one_to_one_functionality_claim"] is False


def test_cli_api_direct_differential_gate_failure_makes_local_gate_fail(
    repo_root: Path, monkeypatch
) -> None:
    """14. Harness errors in cli_api_direct_differential propagate to local gate failure."""
    mod = _load_module()

    def _failing_gate(_repo_root: Path) -> dict[str, object]:
        return {
            "ok": False,
            "status": "error",
            "summary": {
                "overall_status": "error",
                "strict_exit_code": 1,
                "cli_matched": 0,
                "cli_total": 103,
                "api_matched": 0,
                "api_total": 9,
            },
        }

    monkeypatch.setattr(mod, "_run_cli_api_direct_differential_gate", _failing_gate)
    report = mod.build_report(repo_root=repo_root)

    assert report["local_gate_status"] == "fail"
    assert report["release_candidate_ready"] is False
    assert (
        "local_gate_cli_api_direct_differential_failed" in report["remaining_blockers"]
    )
