"""Auth-closure integration tests for row promotion and parity evidence.

Covers the auth row promotion applier, auth-row promotion audit integration,
release-candidate gating, and auth pass-row evidence validation constraints.

Tests are local/offline only and run against copied temporary workspaces.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

TARGET = "notebooklm-py==0.7.2"
TARGET_ROW_ID = "auth.cookie_import.chrome.macos.import"
REQUIRED_EVIDENCE = ["live_differential_result", "session_credential_evidence"]
ALLOWED_AUTH_EVIDENCE_TYPES = {
    "redacted_live_differential_result",
    "redacted_session_credential_evidence",
}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _load_script(root: Path, name: str, module_name: str):
    script = root / "scripts" / name
    spec = importlib.util.spec_from_file_location(module_name, script)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _copy_workspace(repo_root: Path, tmp_path: Path) -> Path:
    workdir = tmp_path / "workspace"
    shutil.copytree(repo_root / "scripts", workdir / "scripts")
    shutil.copytree(repo_root / "compat", workdir / "compat")
    shutil.copytree(repo_root / "tests", workdir / "tests")
    shutil.copytree(repo_root / "singlefile", workdir / "singlefile")
    return workdir


def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return path


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _auth_pass_row_ids(root: Path) -> set[str]:
    parity_rows = _read_json(root / "compat" / "parity_rows.json")
    return {
        row["id"]
        for row in parity_rows.get("rows", [])
        if row.get("category") == "auth" and row.get("status") == "pass"
    }


def _auth_matrix_pass_count(root: Path) -> int:
    auth_matrix = _read_json(root / "compat" / "auth_matrix.json")
    return sum(
        1
        for row in auth_matrix["browser_cookie_import_matrix"]
        + auth_matrix["interactive_login_matrix"]
        if row.get("parity_state") == "pass"
    )


def _mark_row_pass(root: Path, row_id: str) -> None:
    data = _read_json(root / "compat" / "parity_rows.json")
    rows = data.get("rows", [])
    updated = False
    for row in rows:
        if row.get("id") == row_id:
            row["status"] = "pass"
            row["blocker_reason"] = ""
            updated = True
            break
    if not updated:
        raise AssertionError(f"row {row_id} not found in parity_rows snapshot")
    _write_json(root / "compat" / "parity_rows.json", data)


def _demote_selected_row(root: Path, row_id: str) -> None:
    """Create an open selected-row fixture without using excluded browser rows."""
    data = _read_json(root / "compat" / "parity_rows.json")
    for row in data.get("rows", []):
        if row.get("id") == row_id:
            row["status"] = "open"
            row["blocker_reason"] = "synthetic_open_fixture"
            break
    else:
        raise AssertionError(f"row {row_id} not found in parity_rows snapshot")
    _write_json(root / "compat" / "parity_rows.json", data)

    matrix = _read_json(root / "compat" / "auth_matrix.json")
    _find_matrix_entry(matrix, row_id)["parity_state"] = "open"
    _write_json(root / "compat" / "auth_matrix.json", matrix)

    manifest = _read_json(root / "compat" / "auth_row_evidence.json")
    for mapping in manifest.get("auth_mappings", []):
        if mapping.get("row_id") == row_id:
            required = mapping.get("required_evidence", [])
            mapping.update(
                {
                    "status": "open",
                    "row_status": "open",
                    "promotion_allowed": False,
                    "missing_for_promotion": required,
                    "satisfied_required_evidence": [],
                }
            )
            break
    else:
        raise AssertionError(f"row {row_id} not found in auth evidence manifest")
    _write_json(root / "compat" / "auth_row_evidence.json", manifest)

    evidence = _read_json(root / "compat" / "parity_evidence.json")
    evidence["evidence_records"] = [
        rec
        for rec in evidence.get("evidence_records", [])
        if not (isinstance(rec, dict) and rec.get("row_id") == row_id)
    ]
    _write_json(root / "compat" / "parity_evidence.json", evidence)


def _build_auth_proof_payload(row_id: str) -> dict:
    return {
        "schema_version": "auth_row_proof_records/1",
        "target": TARGET,
        "generated_at": "2026-01-01T00:00:00Z",
        "expires_at": "2999-01-01T00:00:00Z",
        "rows": [
            {
                "row_id": row_id,
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
        ],
    }


def _matrix_lookup(row_id: str) -> tuple[str, str, str]:
    _, row_type, browser, os_slug, token = row_id.split(".")
    if row_type == "cookie_import":
        matrix_name = "browser_cookie_import_matrix"
        field = "path"
        if token == "profile_select":
            token = "profile-select"
        elif token == "account_select":
            token = "account-select"
    elif row_type == "interactive":
        matrix_name = "interactive_login_matrix"
        field = "flow"
    else:
        raise AssertionError(f"unexpected auth row type {row_type}")

    return matrix_name, field, f"{browser}|{os_slug}|{token}"


def _find_matrix_entry(matrix: dict, row_id: str) -> dict:
    matrix_name, field, lookup = _matrix_lookup(row_id)
    browser, os_slug, token = lookup.split("|")
    browser = {"opera_gx": "opera-gx"}.get(browser, browser)
    os_map = {
        "macos": "macOS",
        "ubuntu": "Ubuntu-LTS-Linux",
        "windows11": "Windows-11",
    }
    target_os = os_map[os_slug]

    for entry in matrix[matrix_name]:
        if (
            entry.get("browser") == browser
            and entry.get("os") == target_os
            and entry.get(field) == token
        ):
            return entry
    raise AssertionError(f"matrix entry not found for row {row_id}")


def _auth_evidence_records(row_id: str, *, redacted: bool = True) -> list[dict]:
    return [
        {
            "row_id": row_id,
            "evidence_id": "live_differential_result",
            "evidence_type": "redacted_live_differential_result",
            "command": "",
            "test_path": "",
            "artifact_path": "",
            "comparator": "auth.session_state_normalized",
            "closed_system": False,
            "no_live": False,
            "redacted": redacted,
            "promotion_basis": "redacted auth live evidence",
        },
        {
            "row_id": row_id,
            "evidence_id": "session_credential_evidence",
            "evidence_type": "redacted_session_credential_evidence",
            "command": "",
            "test_path": "",
            "artifact_path": "",
            "comparator": "auth.session_state_normalized",
            "closed_system": False,
            "no_live": False,
            "redacted": redacted,
            "promotion_basis": "redacted auth live evidence",
        },
    ]


def _apply_report(
    root: Path,
    proof_row_id: str,
    *,
    apply: bool,
    output_dir: Path | None,
) -> tuple[dict, dict]:
    apply_script = _load_script(root, "auth_row_promotion_apply.py", "_auth_row_apply")
    builder = _load_script(
        root, "auth_row_evidence_report_builder.py", "_auth_row_builder"
    )

    proof_path = _write_json(
        root / "auth_row_proofs.json", _build_auth_proof_payload(proof_row_id)
    )

    return builder.build_report(
        proof_path,
        repo_root=root,
    ), apply_script.build_report(
        proofs=proof_path,
        repo_root=root,
        output_dir=output_dir,
        apply=apply,
    )


def _patch_parity_evidence_for_auth_row(
    root: Path, row_id: str, *, redacted: bool = True
) -> None:
    _mark_row_pass(root, row_id)
    evidence_path = root / "compat" / "parity_evidence.json"
    evidence = _read_json(evidence_path)
    records = evidence.setdefault("evidence_records", [])
    records[:] = [
        rec
        for rec in records
        if not (isinstance(rec, dict) and rec.get("row_id") == row_id)
    ]
    records.extend(_auth_evidence_records(row_id, redacted=redacted))
    _write_json(evidence_path, evidence)


# --------------------------------------------------------------------------- #
# 1) Auth row promotion apply
# --------------------------------------------------------------------------- #


def test_dry_run_does_not_mutate_workspace_compat(
    repo_root: Path, tmp_path: Path
) -> None:
    workspace = _copy_workspace(repo_root, tmp_path)
    _demote_selected_row(workspace, TARGET_ROW_ID)
    before_rows = (workspace / "compat" / "parity_rows.json").read_bytes()
    before_matrix = (workspace / "compat" / "auth_matrix.json").read_bytes()
    before_mapping = (workspace / "compat" / "auth_row_evidence.json").read_bytes()
    before_evidence = (workspace / "compat" / "parity_evidence.json").read_bytes()

    _, report = _apply_report(
        workspace,
        TARGET_ROW_ID,
        apply=False,
        output_dir=None,
    )

    assert report["strict_ok"] is True
    assert report["rows_promoted"] == 1
    assert report["rows_unchanged"] == 0
    assert report["output_files_written"] == 0
    assert (workspace / "compat" / "parity_rows.json").read_bytes() == before_rows
    assert (workspace / "compat" / "auth_matrix.json").read_bytes() == before_matrix
    assert (
        workspace / "compat" / "auth_row_evidence.json"
    ).read_bytes() == before_mapping
    assert (
        workspace / "compat" / "parity_evidence.json"
    ).read_bytes() == before_evidence


def test_apply_updates_single_auth_row_and_auth_evidence(
    tmp_path: Path,
    repo_root: Path,
) -> None:
    workspace = _copy_workspace(repo_root, tmp_path)
    _demote_selected_row(workspace, TARGET_ROW_ID)
    before_pass_rows = _auth_pass_row_ids(workspace)
    before_matrix_pass = _auth_matrix_pass_count(workspace)

    _, report = _apply_report(
        workspace,
        TARGET_ROW_ID,
        apply=True,
        output_dir=workspace / "compat",
    )

    assert report["status"] == "pass"
    assert report["rows_promoted"] == 1
    assert report["rows_unchanged"] == 0
    assert report["output_files_written"] >= 1

    parity_rows = _read_json(workspace / "compat" / "parity_rows.json")
    assert parity_rows["row_count"] == len(parity_rows["rows"])
    assert all("id" in row and "status" in row for row in parity_rows["rows"])
    assert all("parity_state" not in row for row in parity_rows["rows"])
    assert parity_rows["rows"][0]["id"] == "cli.notebooklm.agent.show"
    auth_pass_rows = [
        row
        for row in parity_rows["rows"]
        if row.get("category") == "auth" and row.get("status") == "pass"
    ]
    assert {row["id"] for row in auth_pass_rows} == before_pass_rows | {TARGET_ROW_ID}

    manifest = _read_json(workspace / "compat" / "auth_row_evidence.json")
    auth_pass_mappings = [
        row
        for row in manifest.get("auth_mappings", [])
        if row.get("row_status") == "pass"
    ]
    assert {row["row_id"] for row in auth_pass_mappings} == before_pass_rows | {
        TARGET_ROW_ID
    }
    assert manifest.get("exact_one_to_one_claim_ready") is False

    auth_matrix = _read_json(workspace / "compat" / "auth_matrix.json")
    matrix_entry = _find_matrix_entry(auth_matrix, TARGET_ROW_ID)
    assert matrix_entry["parity_state"] == "pass"
    assert _auth_matrix_pass_count(workspace) == before_matrix_pass + 1

    evidence = _read_json(workspace / "compat" / "parity_evidence.json")
    row_evidence = [
        rec
        for rec in evidence.get("evidence_records", [])
        if rec.get("row_id") == TARGET_ROW_ID
    ]
    assert len(row_evidence) == 2
    assert {rec["evidence_id"] for rec in row_evidence} == set(REQUIRED_EVIDENCE)
    assert {rec["evidence_type"] for rec in row_evidence} == ALLOWED_AUTH_EVIDENCE_TYPES
    assert all(rec["redacted"] is True for rec in row_evidence)
    assert all(rec["closed_system"] is False for rec in row_evidence)
    assert all(rec["no_live"] is False for rec in row_evidence)


def test_apply_handles_opera_gx_browser_slug(tmp_path: Path, repo_root: Path) -> None:
    workspace = _copy_workspace(repo_root, tmp_path)
    row_id = "auth.cookie_import.opera_gx.macos.import"

    _, report = _apply_report(
        workspace,
        row_id,
        apply=True,
        output_dir=workspace / "compat",
    )

    assert report["status"] == "pass"
    assert report["rows_unchanged"] == 1
    auth_matrix = _read_json(workspace / "compat" / "auth_matrix.json")
    assert _find_matrix_entry(auth_matrix, row_id)["parity_state"] == "pass"


def test_apply_replaces_stale_same_key_auth_evidence_records(
    tmp_path: Path, repo_root: Path
) -> None:
    workspace = _copy_workspace(repo_root, tmp_path)

    parity_rows = _read_json(workspace / "compat" / "parity_rows.json")
    for row in parity_rows["rows"]:
        if row.get("id") == TARGET_ROW_ID:
            row["status"] = "pass"
            row["blocker_reason"] = ""
            break
    _write_json(workspace / "compat" / "parity_rows.json", parity_rows)

    manifest = _read_json(workspace / "compat" / "auth_row_evidence.json")
    for mapping in manifest["auth_mappings"]:
        if mapping.get("row_id") == TARGET_ROW_ID:
            mapping.update(
                {
                    "status": "pass",
                    "row_status": "pass",
                    "promotion_allowed": True,
                    "missing_for_promotion": [],
                }
            )
            break
    _write_json(workspace / "compat" / "auth_row_evidence.json", manifest)

    auth_matrix = _read_json(workspace / "compat" / "auth_matrix.json")
    _find_matrix_entry(auth_matrix, TARGET_ROW_ID)["parity_state"] = "pass"
    _write_json(workspace / "compat" / "auth_matrix.json", auth_matrix)

    evidence = _read_json(workspace / "compat" / "parity_evidence.json")
    records = evidence.setdefault("evidence_records", [])
    records[:] = [
        rec
        for rec in records
        if not (isinstance(rec, dict) and rec.get("row_id") == TARGET_ROW_ID)
    ]
    stale = _auth_evidence_records(TARGET_ROW_ID)
    stale[0]["promotion_basis"] = "Cookie __Secure-1PSIDTS=" + "ABCDEFGHIJKLMNO"
    stale[0]["artifact_path"] = "/home/example/Library/Application Support/state.json"
    stale.append(
        {
            "row_id": TARGET_ROW_ID,
            "evidence_id": "unexpected_auth_evidence",
            "evidence_type": "unexpected_type",
            "command": "",
            "test_path": "",
            "artifact_path": "",
            "comparator": "auth.session_state_normalized",
            "closed_system": False,
            "no_live": False,
            "redacted": True,
            "promotion_basis": "Cookie __Secure-1PSIDTS=" + "ABCDEFGHIJKLMNO",
        }
    )
    records.extend(stale)
    _write_json(workspace / "compat" / "parity_evidence.json", evidence)

    _, report = _apply_report(
        workspace,
        TARGET_ROW_ID,
        apply=True,
        output_dir=workspace / "compat",
    )

    assert report["status"] == "pass"
    assert report["rows_promoted"] == 1
    assert report["rows_unchanged"] == 0
    updated = _read_json(workspace / "compat" / "parity_evidence.json")
    row_evidence = [
        rec for rec in updated["evidence_records"] if rec.get("row_id") == TARGET_ROW_ID
    ]
    assert len(row_evidence) == 2
    diagnostics = json.dumps(row_evidence, sort_keys=True)
    assert "__Secure-1PSIDTS" not in diagnostics
    assert "/home/example/" not in diagnostics


def test_apply_forces_auth_category_promotion_false(
    tmp_path: Path, repo_root: Path
) -> None:
    workspace = _copy_workspace(repo_root, tmp_path)
    _demote_selected_row(workspace, TARGET_ROW_ID)
    manifest = _read_json(workspace / "compat" / "auth_row_evidence.json")
    manifest["category_promotion"] = {"auth": True, "cli": True}
    _write_json(workspace / "compat" / "auth_row_evidence.json", manifest)

    _, report = _apply_report(
        workspace,
        TARGET_ROW_ID,
        apply=True,
        output_dir=workspace / "compat",
    )

    assert report["status"] == "pass"
    updated = _read_json(workspace / "compat" / "auth_row_evidence.json")
    assert updated["category_promotion"] == {"auth": False}
    assert updated["exact_one_to_one_claim_ready"] is False


# --------------------------------------------------------------------------- #
# 2) Auth-row audit and release-candidate
# --------------------------------------------------------------------------- #


def test_row_promotion_audit_reports_1_promotable_and_rc_remains_blocked(
    tmp_path: Path, repo_root: Path
) -> None:
    workspace = _copy_workspace(repo_root, tmp_path)
    _demote_selected_row(workspace, TARGET_ROW_ID)
    baseline_promotable = _auth_matrix_pass_count(workspace)
    builder = _load_script(
        workspace,
        "auth_row_evidence_report_builder.py",
        "_auth_builder",
    )
    promotion = _load_script(workspace, "auth_row_promotion_audit.py", "_auth_audit")
    rc = _load_script(workspace, "release_candidate_audit.py", "_release_candidate")

    proof_path = _write_json(
        workspace / "auth_row_proofs.json",
        _build_auth_proof_payload(TARGET_ROW_ID),
    )

    evidence_report_path = workspace / "auth_row_evidence_report.json"
    proof_summary = builder.build_report(
        proof_path,
        repo_root=workspace,
        output=evidence_report_path,
    )
    evidence_report_path = _write_json(evidence_report_path, proof_summary)

    apply = _load_script(workspace, "auth_row_promotion_apply.py", "_auth_apply")
    apply_report = apply.build_report(
        proofs=proof_path,
        repo_root=workspace,
        output_dir=workspace / "compat",
        apply=True,
    )
    assert apply_report["status"] == "pass"

    audit_report = promotion.build_report(
        repo_root=workspace,
        row_evidence_report=evidence_report_path,
    )
    assert audit_report["auth_rows_promotable"] == baseline_promotable + 1
    assert audit_report["auth_rows_blocked"] == 146 - audit_report["auth_rows_promotable"]

    rc_report = rc.build_report(repo_root=workspace)
    assert rc_report["release_candidate_ready"] is False
    assert rc_report["one_to_one_functionality_claim"] is False
    assert "auth_category_open" in rc_report["remaining_blockers"]


def test_apply_fails_closed_when_auth_row_mapping_is_unmapped(
    tmp_path: Path, repo_root: Path
) -> None:
    workspace = _copy_workspace(repo_root, tmp_path)
    manifest = _read_json(workspace / "compat" / "auth_row_evidence.json")
    manifest["auth_mappings"] = [
        row
        for row in manifest.get("auth_mappings", [])
        if row.get("row_id") != TARGET_ROW_ID
    ]
    _write_json(workspace / "compat" / "auth_row_evidence.json", manifest)

    _, report = _apply_report(
        workspace,
        TARGET_ROW_ID,
        apply=True,
        output_dir=workspace / "compat",
    )

    assert report["status"] == "fail"
    assert report["strict_ok"] is False
    assert report["strict_exit_code"] == 77
    assert report["rows_promoted"] == 0
    assert report["rows_unchanged"] == 0
    assert any("missing from auth_row_evidence.json" in err for err in report["errors"])


# --------------------------------------------------------------------------- #
# 3) Parity evidence audit (auth pass-row constraints)
# --------------------------------------------------------------------------- #


def test_parity_evidence_audit_accepts_auth_pass_row_live_evidence_records(
    tmp_path: Path,
    repo_root: Path,
) -> None:
    workspace = _copy_workspace(repo_root, tmp_path)
    _patch_parity_evidence_for_auth_row(workspace, TARGET_ROW_ID)

    mod = _load_script(workspace, "parity_evidence_audit.py", "_parity_evidence")
    report = mod.build_report(repo_root=workspace)

    assert report["strict_ok"] is True
    assert report["strict_exit_code"] == 0
    assert report["rows_with_unmet_tokens"] == []
    auth_rows = [
        rec
        for rec in report["records_with_live_scope"]
        if rec.startswith(f"{TARGET_ROW_ID}/")
    ]
    assert not auth_rows


def test_parity_evidence_audit_rejects_non_redacted_auth_live_evidence(
    tmp_path: Path,
    repo_root: Path,
) -> None:
    workspace = _copy_workspace(repo_root, tmp_path)
    _patch_parity_evidence_for_auth_row(workspace, TARGET_ROW_ID, redacted=False)

    mod = _load_script(workspace, "parity_evidence_audit.py", "_parity_evidence_bad")
    report = mod.build_report(repo_root=workspace)

    assert report["strict_ok"] is False
    assert report["strict_exit_code"] == 1
    assert any(
        "redacted must be true" in item for item in report["records_with_live_scope"]
    )


def test_parity_evidence_audit_rejects_mismatched_auth_evidence_type(
    tmp_path: Path, repo_root: Path
) -> None:
    workspace = _copy_workspace(repo_root, tmp_path)
    _patch_parity_evidence_for_auth_row(workspace, TARGET_ROW_ID)
    evidence = _read_json(workspace / "compat" / "parity_evidence.json")
    for rec in evidence["evidence_records"]:
        if (
            isinstance(rec, dict)
            and rec.get("row_id") == TARGET_ROW_ID
            and rec.get("evidence_id") == "live_differential_result"
        ):
            rec["evidence_type"] = "redacted_session_credential_evidence"
            break
    _write_json(workspace / "compat" / "parity_evidence.json", evidence)

    mod = _load_script(
        workspace, "parity_evidence_audit.py", "_parity_evidence_mismatch"
    )
    report = mod.build_report(repo_root=workspace)

    assert report["strict_ok"] is False
    assert any(
        "auth evidence_type must match evidence_id token" in item
        for item in report["records_with_live_scope"]
    )


def test_parity_evidence_audit_does_not_stat_unsafe_auth_paths(
    tmp_path: Path, repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _copy_workspace(repo_root, tmp_path)
    mod = _load_script(
        workspace, "parity_evidence_audit.py", "_parity_evidence_no_stat"
    )
    concrete_path_type = type(workspace / "probe")

    def forbidden_exists(self: Path) -> bool:  # pragma: no cover - assertion guard
        raise AssertionError(f"unexpected exists() call for {self}")

    monkeypatch.setattr(concrete_path_type, "exists", forbidden_exists)
    record = _auth_evidence_records(TARGET_ROW_ID)[0]
    record["artifact_path"] = "/home/example/Library/Application Support/state.json"
    record["test_path"] = "C:\\Users\\alice\\secret.json"

    result = mod._check_evidence_record(
        record,
        workspace,
        True,
        set(REQUIRED_EVIDENCE),
    )

    diagnostics = "\n".join(result["file_issues"] + result["live_issues"])
    assert "not a safe repo-local reference" in diagnostics
    assert "/home/example/" not in diagnostics
    assert "C:\\Users\\alice" not in diagnostics


@pytest.mark.parametrize(
    "field,value",
    [
        ("artifact_path", "/home/example/Library/Application Support/state.json"),
        ("promotion_basis", "contains raw token leak@example.com"),
        ("promotion_basis", "Cookie __Secure-1PSIDTS=" + "ABCDEFGHIJKLMNO"),
        ("promotion_basis", "C:\\Users\\alice\\secret.json"),
        ("evidence_id", "__Secure-1PSIDTS=" + "ABCDEFGHIJKLMNO"),
        ("evidence_type", "leak@example.com"),
        ("command", "https://notebooklm.google.com/private"),
    ],
)
def test_parity_evidence_audit_rejects_value_free_auth_live_records(
    tmp_path: Path,
    repo_root: Path,
    field: str,
    value: str,
) -> None:
    workspace = _copy_workspace(repo_root, tmp_path)
    evidence = _read_json(workspace / "compat" / "parity_evidence.json")
    records = evidence.get("evidence_records", [])
    records = [
        rec
        for rec in records
        if not (isinstance(rec, dict) and rec.get("row_id") == TARGET_ROW_ID)
    ]
    bad = _auth_evidence_records(TARGET_ROW_ID)
    bad[0][field] = value
    records.append(bad[0])
    records.extend(bad[1:])
    evidence["evidence_records"] = records
    _mark_row_pass(workspace, TARGET_ROW_ID)
    _write_json(workspace / "compat" / "parity_evidence.json", evidence)

    mod = _load_script(
        workspace, "parity_evidence_audit.py", "_parity_evidence_smuggle"
    )
    report = mod.build_report(repo_root=workspace)

    assert report["strict_ok"] is False
    assert report["strict_exit_code"] == 1
    diagnostics = "\n".join(report["records_with_live_scope"])
    assert value not in diagnostics
    assert (
        any(
            "string field contains value-free violation" in item
            for item in report["records_with_live_scope"]
        )
        or any(
            "is not a safe repo-local reference" in item
            for item in report["records_with_live_scope"]
        )
        or any("auth command" in item for item in report["records_with_live_scope"])
    )
