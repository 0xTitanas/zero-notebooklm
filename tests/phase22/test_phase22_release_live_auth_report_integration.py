"""Phase 22 release-audit integration for validated live-auth evidence reports.

This phase does not run live NotebookLM. It proves that a redacted
live_readonly_differential report, once validated by the Phase 21 gate, can be
provided explicitly to the release audit to clear only the live-readonly evidence
blocker. Auth rows, mutation, and exact 1:1 remain blocked.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

TARGET = "notebooklm-py==0.7.2"


def _load_release_module(repo_root: Path):
    path = repo_root / "scripts" / "release_candidate_audit.py"
    spec = importlib.util.spec_from_file_location("_phase22_release_candidate", path)
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
    }


def _shape_report(repo_root: Path) -> dict:
    states = _category_states(repo_root)
    shape = {
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
    return {
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
        "observations": {"upstream_shape": shape, "bare_shape": shape},
        "category_promotion": {
            "cli": False,
            "api": False,
            "auth": False,
            "rpc": False,
        },
        "category_states": states,
    }


def _write_report(
    tmp_path: Path, repo_root: Path, *, mutate: dict | None = None
) -> Path:
    payload = _shape_report(repo_root)
    if mutate:
        payload.update(mutate)
    path = tmp_path / "live-auth-report.json"
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return path


def _clean_env(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    tmp = tmp_path / "tmp"
    home.mkdir(exist_ok=True)
    tmp.mkdir(exist_ok=True)
    return {
        "HOME": str(home),
        "USERPROFILE": str(home),
        "TMPDIR": str(tmp),
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": "",
    }


def test_release_audit_consumes_valid_live_auth_report_without_auth_promotion(
    repo_root: Path, tmp_path: Path
) -> None:
    mod = _load_release_module(repo_root)
    report_path = _write_report(tmp_path, repo_root)

    report = mod.build_report(repo_root=repo_root, live_auth_report=report_path)

    assert report["release_candidate_ready"] is False
    assert report["one_to_one_functionality_claim"] is False
    assert report["category_promotion"]["auth"] is False
    assert report["category_states"]["auth"] == "open"
    assert report["local_gates"]["live_auth_evidence"]["status"] == "pass"
    assert report["live_auth_evidence"]["status"] == "pass"
    assert report["live_auth_evidence"]["evidence_validated"] is True
    assert report["live_auth_evidence"]["evidence_report_path"] == "set"
    assert (
        "live_readonly_differential_not_authorized" not in report["remaining_blockers"]
    )
    assert "auth_category_open" in report["remaining_blockers"]
    assert "live_mutation_smoke_not_authorized" in report["remaining_blockers"]


def test_release_audit_cli_accepts_live_auth_report_argument(
    repo_root: Path, tmp_path: Path
) -> None:
    report_path = _write_report(tmp_path, repo_root)
    proc = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "release_candidate_audit.py"),
            "--json",
            "--strict",
            "--live-auth-report",
            str(report_path),
        ],
        cwd=repo_root,
        env=_clean_env(tmp_path),
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 77
    assert proc.stderr == ""
    report = json.loads(proc.stdout)
    assert report["live_auth_evidence"]["status"] == "pass"
    assert report["live_auth_evidence"]["evidence_validated"] is True
    assert (
        "live_readonly_differential_not_authorized" not in report["remaining_blockers"]
    )
    assert report["one_to_one_functionality_claim"] is False


def test_invalid_live_auth_report_fails_closed_and_keeps_live_blocker(
    repo_root: Path, tmp_path: Path
) -> None:
    mod = _load_release_module(repo_root)
    report_path = _write_report(tmp_path, repo_root, mutate={"status": "fail"})

    report = mod.build_report(repo_root=repo_root, live_auth_report=report_path)

    assert report["local_gate_status"] == "fail"
    assert report["release_candidate_ready"] is False
    assert report["live_auth_evidence"]["status"] == "fail"
    assert report["live_auth_evidence"]["evidence_validated"] is False
    assert "local_gate_live_auth_evidence_failed" in report["remaining_blockers"]
    assert "live_readonly_differential_not_authorized" in report["remaining_blockers"]
    assert report["category_promotion"]["auth"] is False


def test_default_release_audit_still_requires_live_readonly_evidence(
    repo_root: Path,
) -> None:
    mod = _load_release_module(repo_root)

    report = mod.build_report(repo_root=repo_root)

    assert report["live_auth_evidence"]["status"] == "blocked_expected"
    assert report["live_auth_evidence"]["evidence_validated"] is False
    assert "live_readonly_differential_not_authorized" in report["remaining_blockers"]
    assert report["one_to_one_functionality_claim"] is False
