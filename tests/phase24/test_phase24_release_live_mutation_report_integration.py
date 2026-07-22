"""Phase 24 release-audit integration for live mutation/export evidence.

Verifies release candidate logic when a validated Phase 24 mutation/export evidence
report is provided alongside the existing live-auth evidence artifact.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

TARGET = "notebooklm-py==0.7.2"


def _load_module(repo_root: Path):
    path = repo_root / "scripts" / "release_candidate_audit.py"
    spec = importlib.util.spec_from_file_location("_phase24_release", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_parity_row_module(repo_root: Path):
    path = repo_root / "scripts" / "parity_row_audit.py"
    spec = importlib.util.spec_from_file_location("_phase24_parity_row", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _category_states(repo_root: Path) -> dict[str, str]:
    states: dict[str, str] = {}
    matrix = repo_root / "compat" / "parity_matrix.md"
    for line in matrix.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or "| ---" in line:
            continue
        cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
        if len(cells) == 4:
            states[cells[0]] = cells[3]
    return {
        "cli": states.get("cli", "open"),
        "api": states.get("api", "open"),
        "auth": states.get("auth", "open"),
        "rpc": states.get("rpc", "open"),
        "offline": states.get("offline", "open"),
        "self-test": states.get("self-test", "open"),
    }


def _shape() -> dict:
    return {
        "type": "dict",
        "size": 1,
        "entries": [
            {
                "key": {"type": "str", "length": 7, "empty": False},
                "value": [
                    {
                        "type": "dict",
                        "size": 2,
                        "entries": [
                            {
                                "key": {"type": "str", "length": 5, "empty": False},
                                "value": {"type": "str", "length": 16, "empty": False},
                            },
                            {
                                "key": {"type": "str", "length": 5, "empty": False},
                                "value": {"type": "int"},
                            },
                        ],
                    }
                ],
            }
        ],
    }


def _write_live_auth_report(tmp_path: Path) -> Path:
    payload = {
        "schema_version": "live_readonly_differential/1",
        "target": TARGET,
        "status": "pass",
        "strict_exit_code": 0,
        "live_enabled": True,
        "read_only": True,
        "mutation_allowed": False,
        "storage_state": "set",
        "notebook_id": "set",
        "read_only_operations": [
            "list_notebooks",
            "get_notebook",
            "list_sources",
            "get_source",
            "list_notes",
            "get_note",
            "list_artifacts",
            "get_artifact",
            "get_status",
            "check_auth",
            "inspect_auth",
        ],
        "smoke": {"status": "passed", "exit_code": 0},
        "storage_preserved": True,
        "shape_match": True,
        "blockers": [],
        "upstream_probe": {"ok": True, "error": ""},
        "bare_probe": {"ok": True, "error": ""},
        "observations": {"upstream_shape": _shape(), "bare_shape": _shape()},
        "category_promotion": {"cli": False, "api": False, "auth": False, "rpc": False},
        "category_states": _category_states(Path(__file__).resolve().parents[2]),
    }
    path = tmp_path / "live_auth_report.json"
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return path


def _write_live_mutation_report(
    tmp_path: Path, repo_root: Path, *, status: str = "pass"
) -> Path:
    states = _category_states(repo_root)
    payload = {
        "schema_version": "live_mutation_export_differential/1",
        "target": TARGET,
        "status": status,
        "strict_exit_code": 0 if status == "pass" else 1,
        "live_enabled": True,
        "read_only": False,
        "mutation_allowed": True,
        "public_sharing_allowed": False,
        "disposable_notebook_only": True,
        "storage_state": "set",
        "notebook_id": "set",
        "operation_allowlist": [
            "create_note",
            "update_note",
            "delete_note",
            "add_text_source",
            "delete_source",
            "export_artifact",
            "download_artifact",
            "rename_notebook",
        ],
        "storage_preserved": True,
        "shape_match": True,
        "cleanup_confirmed": True,
        "public_sharing_touched": False,
        "blockers": [],
        "upstream_probe": {"ok": True, "error": ""},
        "bare_probe": {"ok": True, "error": ""},
        "observations": {"upstream_shape": _shape(), "bare_shape": _shape()},
        "category_promotion": {"cli": False, "api": False, "auth": False, "rpc": False},
        "category_states": {
            "cli": states["cli"],
            "api": states["api"],
            "auth": states["auth"],
            "rpc": states["rpc"],
        },
    }
    path = tmp_path / "live_mutation_report.json"
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return path


def _write_payload(tmp_path: Path, path: Path, payload: dict) -> Path:
    dest = tmp_path / path
    dest.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return dest


def test_release_audit_consumes_live_auth_and_live_mutation_reports_and_clears_both_blockers(
    repo_root: Path, tmp_path: Path
) -> None:
    mod = _load_module(repo_root)
    auth_report = _write_live_auth_report(tmp_path)
    mutation_report = _write_live_mutation_report(tmp_path, repo_root)

    report = mod.build_report(
        repo_root=repo_root,
        live_auth_report=auth_report,
        live_mutation_report=mutation_report,
    )

    assert report["release_candidate_ready"] is False
    assert report["one_to_one_functionality_claim"] is False
    assert report["category_promotion"]["auth"] is False
    assert report["category_states"]["auth"] == "open"
    assert report["local_gates"]["live_auth_evidence"]["status"] == "pass"
    assert report["local_gates"]["live_mutation_evidence"]["status"] == "pass"
    assert report["local_gates"]["auth_row_evidence"]["status"] == "pass"
    assert report["auth_row_evidence"]["live_category_level_evidence"] == {
        "live_auth_report_validated": True,
        "live_mutation_report_validated": True,
    }
    assert report["auth_row_evidence"]["auth_rows_promotable"] == 146
    assert report["auth_row_evidence"]["auth_rows_blocked"] == 0
    assert report["live_mutation_evidence"]["status"] == "pass"
    assert report["live_mutation_evidence"]["evidence_validated"] is True
    assert (
        "live_readonly_differential_not_authorized" not in report["remaining_blockers"]
    )
    assert "live_mutation_smoke_not_authorized" not in report["remaining_blockers"]
    assert "auth_category_open" in report["remaining_blockers"]


def test_invalid_live_mutation_report_keeps_mutation_blocker(
    repo_root: Path, tmp_path: Path
) -> None:
    mod = _load_module(repo_root)
    auth_report = _write_live_auth_report(tmp_path)
    invalid_mutation_report = _write_live_mutation_report(
        tmp_path,
        repo_root,
        status="fail",
    )

    report = mod.build_report(
        repo_root=repo_root,
        live_auth_report=auth_report,
        live_mutation_report=invalid_mutation_report,
    )

    assert report["live_mutation_evidence"]["status"] == "fail"
    assert report["live_mutation_evidence"]["evidence_validated"] is False
    assert "live_mutation_smoke_not_authorized" in report["remaining_blockers"]
    assert (
        "live_readonly_differential_not_authorized" not in report["remaining_blockers"]
    )
    assert report["one_to_one_functionality_claim"] is False


def test_release_audit_default_with_no_mutation_report_keeps_mutation_blocker(
    repo_root: Path, tmp_path: Path
) -> None:
    mod = _load_module(repo_root)
    auth_report = _write_live_auth_report(tmp_path)

    report = mod.build_report(repo_root=repo_root, live_auth_report=auth_report)

    assert report["live_mutation_evidence"]["status"] == "blocked_expected"
    assert report["live_mutation_evidence"]["evidence_validated"] is False
    assert "live_mutation_smoke_not_authorized" in report["remaining_blockers"]


def test_parity_row_audit_consumes_live_reports_and_clears_stale_live_blockers(
    repo_root: Path, tmp_path: Path
) -> None:
    mod = _load_parity_row_module(repo_root)
    auth_report = _write_live_auth_report(tmp_path)
    mutation_report = _write_live_mutation_report(tmp_path, repo_root)

    report = mod.build_report(
        repo_root=repo_root,
        live_auth_report=auth_report,
        live_mutation_report=mutation_report,
    )

    assert report["exact_one_to_one_claim_ready"] is False
    assert report["live_auth_evidence"]["evidence_validated"] is True
    assert report["live_mutation_evidence"]["evidence_validated"] is True
    assert "explicit_profile_exclusions_remain" in report["remaining_blockers"]
    assert (
        "live_differential_evidence_not_yet_collected"
        not in report["remaining_blockers"]
    )
    assert (
        "live_readonly_differential_not_authorized" not in report["remaining_blockers"]
    )
    assert "live_mutation_smoke_not_authorized" not in report["remaining_blockers"]


def test_parity_row_audit_default_without_reports_keeps_live_blockers(
    repo_root: Path,
) -> None:
    mod = _load_parity_row_module(repo_root)

    report = mod.build_report(repo_root=repo_root)

    assert report["live_auth_evidence"]["status"] == "blocked_expected"
    assert report["live_mutation_evidence"]["status"] == "blocked_expected"
    assert "live_readonly_differential_not_authorized" in report["remaining_blockers"]
    assert "live_mutation_smoke_not_authorized" in report["remaining_blockers"]
