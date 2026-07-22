"""Phase 26 auth-row evidence report integration tests.

These tests keep the row-evidence path honest: explicit reports may be
validated and surfaced by the release audit, but invalid/private-looking report
content fails closed and no exact 1:1 claim is made.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

TARGET = "notebooklm-py==0.7.2"


def _load_script(repo_root: Path, script_name: str, module_name: str):
    path = repo_root / "scripts" / script_name
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return path


def _base_row_evidence_report() -> dict:
    return {
        "schema_version": "auth_row_evidence_report/1",
        "target": TARGET,
        "generated_at": "2026-01-01T00:00:00Z",
        "expires_at": "2999-01-01T00:00:00Z",
        "rows": [],
    }


def _builder_shaped_row_evidence_report(row_ids: list[str]) -> dict:
    report = _base_row_evidence_report()
    report["proof_builder"] = "auth_row_evidence_report_builder.py"
    report["proof_schema_version"] = "auth_row_proof_records/1"
    report["rows"] = [
        {
            "row_id": row_id,
            "satisfied_required_evidence": [
                "live_differential_result",
                "session_credential_evidence",
            ],
            "proofs": [
                {
                    "token": "live_differential_result",
                    "evidence_id": f"{row_id}::live_differential_result",
                    "evidence_type": "live_differential",
                    "status": "pass",
                    "redacted": True,
                },
                {
                    "token": "session_credential_evidence",
                    "evidence_id": f"{row_id}::session_credential_evidence",
                    "evidence_type": "session_credential",
                    "status": "pass",
                    "redacted": True,
                },
            ],
        }
        for row_id in row_ids
    ]
    return report


def test_release_candidate_audit_forwards_auth_row_evidence_report(
    repo_root: Path, tmp_path: Path
) -> None:
    mod = _load_script(repo_root, "release_candidate_audit.py", "_phase26_release")
    evidence_report = _write_json(
        tmp_path / "auth_row_evidence_report.json",
        _base_row_evidence_report(),
    )

    report = mod.build_report(
        repo_root=repo_root,
        auth_row_evidence_report=evidence_report,
    )

    assert report["local_gates"]["auth_row_evidence"]["ok"] is True
    forwarded = report["auth_row_evidence"]["auth_row_evidence_report"]
    assert forwarded["provided"] is True
    assert forwarded["validated"] is True
    assert forwarded["status"] == "pass"
    assert forwarded["row_evidence_count"] == 0
    assert report["auth_row_evidence"]["auth_rows_promotable"] == 146
    assert report["auth_row_evidence"]["auth_rows_blocked"] == 0
    assert report["one_to_one_functionality_claim"] is False
    assert "auth_category_open" in report["remaining_blockers"]


def test_release_candidate_audit_rejects_invalid_auth_row_evidence_report(
    repo_root: Path, tmp_path: Path
) -> None:
    mod = _load_script(repo_root, "release_candidate_audit.py", "_phase26_release_bad")
    payload = _base_row_evidence_report()
    payload["rows"] = [
        {
            "row_id": "auth.interactive.chromium.macos.login",
            "satisfied_required_evidence": [],
            "cookies": [{"name": "NID", "value": "raw-cookie-value"}],
        }
    ]
    evidence_report = _write_json(tmp_path / "auth_row_evidence_report.json", payload)

    report = mod.build_report(
        repo_root=repo_root,
        auth_row_evidence_report=evidence_report,
    )

    assert report["local_gates"]["auth_row_evidence"]["ok"] is False
    forwarded = report["auth_row_evidence"]["auth_row_evidence_report"]
    assert forwarded["provided"] is True
    assert forwarded["validated"] is False
    assert forwarded["status"] == "invalid"
    assert any("unknown keys" in err for err in forwarded["errors"])
    assert "local_gate_auth_row_evidence_failed" in report["remaining_blockers"]
    assert report["one_to_one_functionality_claim"] is False


def test_auth_row_audit_rejects_sensitive_values_under_unapproved_row_keys(
    repo_root: Path, tmp_path: Path
) -> None:
    mod = _load_script(
        repo_root,
        "auth_row_promotion_audit.py",
        "_phase26_auth_row_audit_smuggling",
    )
    payload = _base_row_evidence_report()
    payload["rows"] = [
        {
            "row_id": "auth.interactive.chromium.macos.login",
            "satisfied_required_evidence": [],
            "session_cookie": "raw-session-cookie-value",
            "raw_token": "raw-token-value",
            "browser_profile_path": "raw-browser-profile-path",
        }
    ]
    evidence_report = _write_json(tmp_path / "auth_row_evidence_report.json", payload)

    report = mod.build_report(repo_root=repo_root, row_evidence_report=evidence_report)

    assert report["status"] == "fail"
    assert report["strict_ok"] is False
    assert report["strict_exit_code"] == 77
    assert report["auth_row_evidence"]["validated"] is False
    assert any("unknown keys" in err for err in report["errors"])
    diagnostics = "\n".join(report["errors"])
    assert "session_cookie" not in diagnostics
    assert "raw_token" not in diagnostics
    assert "browser_profile_path" not in diagnostics


def test_auth_row_audit_rejects_unknown_top_level_report_keys(
    repo_root: Path, tmp_path: Path
) -> None:
    mod = _load_script(
        repo_root,
        "auth_row_promotion_audit.py",
        "_phase26_auth_row_audit_top_level_smuggling",
    )
    payload = _base_row_evidence_report()
    payload["browser_profile_path"] = "raw-browser-profile-path"
    evidence_report = _write_json(tmp_path / "auth_row_evidence_report.json", payload)

    report = mod.build_report(repo_root=repo_root, row_evidence_report=evidence_report)

    assert report["status"] == "fail"
    assert report["strict_ok"] is False
    assert report["strict_exit_code"] == 77
    assert report["auth_row_evidence"]["validated"] is False
    assert any("unknown top-level keys" in err for err in report["errors"])
    assert "browser_profile_path" not in "\n".join(report["errors"])


def test_auth_row_audit_rejects_future_generated_at(
    repo_root: Path, tmp_path: Path
) -> None:
    mod = _load_script(
        repo_root,
        "auth_row_promotion_audit.py",
        "_phase26_auth_row_audit_future_generated_at",
    )
    payload = _base_row_evidence_report()
    payload["generated_at"] = "2998-01-01T00:00:00Z"
    evidence_report = _write_json(tmp_path / "auth_row_evidence_report.json", payload)

    report = mod.build_report(repo_root=repo_root, row_evidence_report=evidence_report)

    assert report["status"] == "fail"
    assert report["strict_ok"] is False
    assert report["auth_row_evidence"]["validated"] is False
    assert any("generated_at" in err and "future" in err for err in report["errors"])


def test_auth_row_audit_rejects_non_string_satisfied_evidence_items(
    repo_root: Path, tmp_path: Path
) -> None:
    mod = _load_script(
        repo_root,
        "auth_row_promotion_audit.py",
        "_phase26_auth_row_audit_nested_token_smuggling",
    )
    payload = _base_row_evidence_report()
    payload["rows"] = [
        {
            "row_id": "auth.interactive.chromium.macos.login",
            "satisfied_required_evidence": [
                "live_differential_result",
                "session_credential_evidence",
                {"raw_token": "raw-token-value"},
            ],
        }
    ]
    evidence_report = _write_json(tmp_path / "auth_row_evidence_report.json", payload)

    report = mod.build_report(repo_root=repo_root, row_evidence_report=evidence_report)

    assert report["status"] == "fail"
    assert report["strict_ok"] is False
    assert report["auth_row_evidence"]["validated"] is False
    assert any("items must be strings" in err for err in report["errors"])
    assert "raw_token" not in "\n".join(report["errors"])


def test_auth_row_audit_rejects_extra_satisfied_evidence_tokens(
    repo_root: Path, tmp_path: Path
) -> None:
    mod = _load_script(
        repo_root,
        "auth_row_promotion_audit.py",
        "_phase26_auth_row_audit_extra_token_smuggling",
    )
    payload = _base_row_evidence_report()
    payload["rows"] = [
        {
            "row_id": "auth.interactive.chromium.macos.login",
            "satisfied_required_evidence": [
                "live_differential_result",
                "session_credential_evidence",
                "browser_profile_path",
            ],
        }
    ]
    evidence_report = _write_json(tmp_path / "auth_row_evidence_report.json", payload)

    report = mod.build_report(repo_root=repo_root, row_evidence_report=evidence_report)

    assert report["status"] == "fail"
    assert report["strict_ok"] is False
    assert report["auth_row_evidence"]["validated"] is False
    assert any(
        "unknown satisfied_required_evidence tokens" in err for err in report["errors"]
    )
    assert "browser_profile_path" not in "\n".join(report["errors"])


def test_auth_row_audit_rejects_partial_satisfied_evidence_tokens(
    repo_root: Path, tmp_path: Path
) -> None:
    mod = _load_script(
        repo_root,
        "auth_row_promotion_audit.py",
        "_phase26_auth_row_audit_partial_token_report",
    )
    payload = _base_row_evidence_report()
    payload["rows"] = [
        {
            "row_id": "auth.interactive.chromium.macos.login",
            "satisfied_required_evidence": ["live_differential_result"],
        }
    ]
    evidence_report = _write_json(tmp_path / "auth_row_evidence_report.json", payload)

    report = mod.build_report(repo_root=repo_root, row_evidence_report=evidence_report)

    assert report["status"] == "fail"
    assert report["strict_ok"] is False
    assert report["strict_exit_code"] == 77
    assert report["auth_row_evidence"]["validated"] is False
    assert any(
        "missing required satisfied_required_evidence tokens" in err
        for err in report["errors"]
    )


def test_auth_row_audit_rejects_direct_row_evidence_without_builder_proof(
    repo_root: Path, tmp_path: Path
) -> None:
    mod = _load_script(
        repo_root,
        "auth_row_promotion_audit.py",
        "_phase26_auth_row_audit_direct_row_report",
    )
    row_id = json.loads((repo_root / "compat" / "auth_row_evidence.json").read_text())[
        "auth_mappings"
    ][0]["row_id"]
    payload = _base_row_evidence_report()
    payload["rows"] = [
        {
            "row_id": row_id,
            "satisfied_required_evidence": [
                "live_differential_result",
                "session_credential_evidence",
            ],
        }
    ]
    evidence_report = _write_json(tmp_path / "auth_row_evidence_report.json", payload)

    report = mod.build_report(repo_root=repo_root, row_evidence_report=evidence_report)

    assert report["status"] == "fail"
    assert report["strict_ok"] is False
    assert report["auth_row_evidence"]["validated"] is False
    assert any("proof builder" in err for err in report["errors"])
    assert row_id not in "\n".join(report["errors"])


def test_auth_row_audit_rejects_builder_report_with_row_agnostic_proof_id(
    repo_root: Path, tmp_path: Path
) -> None:
    mod = _load_script(
        repo_root,
        "auth_row_promotion_audit.py",
        "_phase26_auth_row_audit_row_agnostic_proof",
    )
    row_id = json.loads((repo_root / "compat" / "auth_row_evidence.json").read_text())[
        "auth_mappings"
    ][0]["row_id"]
    payload = _builder_shaped_row_evidence_report([row_id])
    payload["rows"][0]["proofs"][0]["evidence_id"] = "redacted-live-proof"
    evidence_report = _write_json(tmp_path / "auth_row_evidence_report.json", payload)

    report = mod.build_report(repo_root=repo_root, row_evidence_report=evidence_report)

    assert report["status"] == "fail"
    assert report["auth_row_evidence"]["validated"] is False
    assert any("identify its auth row" in err for err in report["errors"])
    assert "redacted-live-proof" not in "\n".join(report["errors"])
    assert row_id not in "\n".join(report["errors"])


@pytest.mark.parametrize("duplicate_field", ["satisfied", "proof"])
def test_auth_row_audit_rejects_duplicate_row_evidence_tokens(
    repo_root: Path, tmp_path: Path, duplicate_field: str
) -> None:
    mod = _load_script(
        repo_root,
        "auth_row_promotion_audit.py",
        f"_phase26_auth_row_audit_duplicate_{duplicate_field}",
    )
    row_id = json.loads((repo_root / "compat" / "auth_row_evidence.json").read_text())[
        "auth_mappings"
    ][0]["row_id"]
    payload = _builder_shaped_row_evidence_report([row_id])
    if duplicate_field == "satisfied":
        payload["rows"][0]["satisfied_required_evidence"].append(
            "live_differential_result"
        )
    else:
        payload["rows"][0]["proofs"].append(dict(payload["rows"][0]["proofs"][0]))
    evidence_report = _write_json(tmp_path / "auth_row_evidence_report.json", payload)

    report = mod.build_report(repo_root=repo_root, row_evidence_report=evidence_report)

    assert report["status"] == "fail"
    assert report["auth_row_evidence"]["validated"] is False
    assert any("duplicate" in err for err in report["errors"])
    assert row_id not in "\n".join(report["errors"])


def test_auth_row_audit_accepts_future_auth_matrix_pass_states_when_rows_are_evidenced(
    repo_root: Path, tmp_path: Path
) -> None:
    mod = _load_script(
        repo_root,
        "auth_row_promotion_audit.py",
        "_phase26_auth_row_audit",
    )
    manifest = json.loads((repo_root / "compat" / "auth_row_evidence.json").read_text())
    rows = json.loads((repo_root / "compat" / "parity_rows.json").read_text())
    matrix = json.loads((repo_root / "compat" / "auth_matrix.json").read_text())
    mapping = manifest["auth_mappings"][0]
    row_id = mapping["row_id"]
    mapping["status"] = "open"
    mapping["row_status"] = "open"
    mapping["promotion_allowed"] = False
    mapping["missing_for_promotion"] = list(mapping["required_evidence"])
    mapping.pop("satisfied_required_evidence", None)
    for row in rows["rows"]:
        if row.get("id") == row_id:
            row["status"] = "open"
            row["row_status"] = "open"
            break
    baseline_promotable = sum(
        1 for row in manifest["auth_mappings"] if row.get("row_status") == "pass"
    )
    mapping["status"] = "pass"
    mapping["row_status"] = "pass"
    mapping["promotion_allowed"] = True
    mapping["missing_for_promotion"] = []
    mapping["satisfied_required_evidence"] = list(mapping["required_evidence"])

    for row in rows["rows"]:
        if row.get("id") == row_id:
            row["status"] = "pass"
            row["row_status"] = "pass"
            break
    _, row_type, browser, os_slug, token = row_id.split(".")
    matrix_name, field = (
        ("browser_cookie_import_matrix", "path")
        if row_type == "cookie_import"
        else ("interactive_login_matrix", "flow")
    )
    matrix_entry = next(
        entry
        for entry in matrix[matrix_name]
        if entry["browser"] == browser
        and entry["os"] == {"macos": "macOS", "ubuntu": "Ubuntu-LTS-Linux", "windows11": "Windows-11"}[os_slug]
        and entry[field] == token.replace("_", "-")
    )
    matrix_entry["parity_state"] = "pass"

    manifest_path = _write_json(tmp_path / "auth_row_evidence.json", manifest)
    rows_path = _write_json(tmp_path / "parity_rows.json", rows)
    matrix_path = _write_json(tmp_path / "auth_matrix.json", matrix)
    evidence_report = _builder_shaped_row_evidence_report([row_id])
    evidence_path = _write_json(
        tmp_path / "auth_row_evidence_report.json", evidence_report
    )

    report = mod.build_report(
        repo_root=repo_root,
        manifest_path=manifest_path,
        parity_rows_path=rows_path,
        auth_matrix_path=matrix_path,
        row_evidence_report=evidence_path,
    )

    assert report["status"] == "pass"
    assert report["strict_ok"] is True
    assert report["auth_rows_promotable"] == baseline_promotable + 1
    assert report["auth_rows_blocked"] == 146 - report["auth_rows_promotable"]
    assert report["auth_rows_matrix_summary"]["parity_pass"] == baseline_promotable + 1
    assert report["category_promotion"] == {"auth": False}
    assert report["exact_one_to_one_claim_ready"] is False


def test_auth_row_audit_rejects_partial_category_promotion_claim(
    repo_root: Path, tmp_path: Path
) -> None:
    mod = _load_script(
        repo_root,
        "auth_row_promotion_audit.py",
        "_phase26_auth_row_audit_partial_claim",
    )
    manifest = json.loads((repo_root / "compat" / "auth_row_evidence.json").read_text())
    rows = json.loads((repo_root / "compat" / "parity_rows.json").read_text())

    manifest["category_promotion"] = {"auth": True}
    row_id = manifest["auth_mappings"][0]["row_id"]
    manifest["auth_mappings"][0]["status"] = "pass"
    manifest["auth_mappings"][0]["row_status"] = "pass"
    manifest["auth_mappings"][0]["promotion_allowed"] = True
    manifest["auth_mappings"][0]["missing_for_promotion"] = []
    for row in rows["rows"]:
        if row.get("id") == row_id:
            row["status"] = "pass"
            row["row_status"] = "pass"
            break

    manifest_path = _write_json(tmp_path / "auth_row_evidence.json", manifest)
    rows_path = _write_json(tmp_path / "parity_rows.json", rows)
    evidence_report = _builder_shaped_row_evidence_report([row_id])
    evidence_path = _write_json(
        tmp_path / "auth_row_evidence_report.json", evidence_report
    )

    report = mod.build_report(
        repo_root=repo_root,
        manifest_path=manifest_path,
        parity_rows_path=rows_path,
        row_evidence_report=evidence_path,
    )

    assert report["status"] == "fail"
    assert report["strict_ok"] is False
    assert any(
        "category_promotion.auth must remain false" in err
        for err in report["errors"]
    )
    assert report["exact_one_to_one_claim_ready"] is False
