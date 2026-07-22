"""Phase 25 auth-row promotion evidence guardrail tests."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

TARGET = "notebooklm-py==0.7.2"


def _load_module(repo_root: Path):
    path = repo_root / "scripts" / "auth_row_promotion_audit.py"
    spec = importlib.util.spec_from_file_location("_phase25_auth_row_promotion", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _parse_category_states(repo_root: Path) -> dict[str, str]:
    path = repo_root / "compat" / "parity_matrix.md"
    states: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or "| ---" in line:
            continue
        cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
        if len(cells) == 4 and cells[0] in {
            "cli",
            "api",
            "auth",
            "rpc",
            "offline",
            "self-test",
        }:
            states[cells[0]] = cells[3]
    return {
        "cli": states.get("cli", "open"),
        "api": states.get("api", "open"),
        "auth": states.get("auth", "open"),
        "rpc": states.get("rpc", "open"),
        "offline": states.get("offline", "open"),
        "self-test": states.get("self-test", "open"),
    }


def _base_live_auth_report(repo_root: Path) -> dict:
    states = _parse_category_states(repo_root)
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
        "observations": {
            "upstream_shape": {
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
                                        "key": {
                                            "type": "str",
                                            "length": 5,
                                            "empty": False,
                                        },
                                        "value": {
                                            "type": "str",
                                            "length": 16,
                                            "empty": False,
                                        },
                                    },
                                    {
                                        "key": {
                                            "type": "str",
                                            "length": 5,
                                            "empty": False,
                                        },
                                        "value": {"type": "int"},
                                    },
                                ],
                            }
                        ],
                    }
                ],
            },
            "bare_shape": {
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
                                        "key": {
                                            "type": "str",
                                            "length": 5,
                                            "empty": False,
                                        },
                                        "value": {
                                            "type": "str",
                                            "length": 16,
                                            "empty": False,
                                        },
                                    },
                                    {
                                        "key": {
                                            "type": "str",
                                            "length": 5,
                                            "empty": False,
                                        },
                                        "value": {"type": "int"},
                                    },
                                ],
                            }
                        ],
                    }
                ],
            },
        },
        "category_promotion": {"cli": False, "api": False, "auth": False, "rpc": False},
        "category_states": {
            "cli": states["cli"],
            "api": states["api"],
            "auth": states["auth"],
            "rpc": states["rpc"],
        },
    }


def _base_live_mutation_report(repo_root: Path) -> dict:
    states = _parse_category_states(repo_root)
    return {
        "schema_version": "live_mutation_export_differential/1",
        "target": TARGET,
        "status": "pass",
        "strict_exit_code": 0,
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
        "cleanup_confirmed": True,
        "public_sharing_touched": False,
        "shape_match": True,
        "blockers": [],
        "upstream_probe": {"ok": True, "error": ""},
        "bare_probe": {"ok": True, "error": ""},
        "observations": {
            "upstream_shape": {
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
                                        "key": {
                                            "type": "str",
                                            "length": 5,
                                            "empty": False,
                                        },
                                        "value": {
                                            "type": "str",
                                            "length": 16,
                                            "empty": False,
                                        },
                                    },
                                    {
                                        "key": {
                                            "type": "str",
                                            "length": 5,
                                            "empty": False,
                                        },
                                        "value": {"type": "int"},
                                    },
                                ],
                            }
                        ],
                    }
                ],
            },
            "bare_shape": {
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
                                        "key": {
                                            "type": "str",
                                            "length": 5,
                                            "empty": False,
                                        },
                                        "value": {
                                            "type": "str",
                                            "length": 16,
                                            "empty": False,
                                        },
                                    },
                                    {
                                        "key": {
                                            "type": "str",
                                            "length": 5,
                                            "empty": False,
                                        },
                                        "value": {"type": "int"},
                                    },
                                ],
                            }
                        ],
                    }
                ],
            },
        },
        "category_promotion": {"cli": False, "api": False, "auth": False, "rpc": False},
        "category_states": {
            "cli": states["cli"],
            "api": states["api"],
            "auth": states["auth"],
            "rpc": states["rpc"],
        },
    }


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _base_auth_row_evidence_report() -> dict:
    return {
        "schema_version": "auth_row_evidence_report/1",
        "target": TARGET,
        "generated_at": "2026-01-01T00:00:00Z",
        "expires_at": "2999-01-01T00:00:00Z",
        "rows": [],
    }


def _builder_shaped_auth_row_evidence_report(row_ids: list[str]) -> dict:
    report = _base_auth_row_evidence_report()
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


def _with_row_open_or_pass(
    manifest: dict,
    rows_data: dict,
    row_id: str,
    *,
    as_pass: bool,
) -> None:
    for mapping in manifest["auth_mappings"]:
        if mapping.get("row_id") == row_id:
            mapping["status"] = "pass" if as_pass else "open"
            mapping["row_status"] = "pass" if as_pass else "open"
            mapping["promotion_allowed"] = as_pass
            mapping["missing_for_promotion"] = (
                [] if as_pass else mapping["missing_for_promotion"]
            )
            if as_pass:
                mapping["satisfied_required_evidence"] = list(
                    mapping["required_evidence"]
                )
            else:
                mapping.pop("satisfied_required_evidence", None)
            break

    for row in rows_data["rows"]:
        if row.get("id") == row_id and row.get("category") == "auth":
            row["status"] = "pass" if as_pass else "open"
            row["row_status"] = "pass" if as_pass else "open"
            break


def test_default_auth_row_audit_is_pure_and_counts_committed_pass_rows(
    repo_root: Path, monkeypatch
) -> None:
    mod = _load_module(repo_root)

    def _forbidden_home() -> Path:
        raise AssertionError("auth-row audit must not call Path.home()")

    monkeypatch.setattr(Path, "home", staticmethod(_forbidden_home))

    report = mod.build_report(repo_root=repo_root)

    assert report["status"] == "pass"
    assert report["strict_ok"] is True
    assert report["strict_exit_code"] == 0
    assert report["schema_version"] == "auth_row_promotion_audit/1"
    assert report["target"] == TARGET
    assert report["mapping_count"] == 146
    assert report["auth_rows_expected"] == 146
    assert report["auth_rows_mapped"] == 146
    assert report["auth_rows_promotable"] == 146
    assert report["auth_rows_blocked"] == 0
    assert report["exact_one_to_one_claim_ready"] is False
    assert report["category_promotion"] == {"auth": False}
    assert report["manifest_present"] is True


def test_valid_live_reports_only_give_category_level_evidence_and_do_not_add_row_promotions(
    repo_root: Path, tmp_path: Path
) -> None:
    mod = _load_module(repo_root)
    auth_report = tmp_path / "live_auth.json"
    mutation_report = tmp_path / "live_mutation.json"
    _write_json(auth_report, _base_live_auth_report(repo_root))
    _write_json(mutation_report, _base_live_mutation_report(repo_root))

    report = mod.build_report(
        repo_root=repo_root,
        live_auth_report=auth_report,
        live_mutation_report=mutation_report,
    )

    assert report["status"] == "pass"
    assert report["strict_ok"] is True
    assert report["live_reports"]["live_auth_report"]["validated"] is True
    assert report["live_reports"]["live_mutation_report"]["validated"] is True
    assert report["live_reports"]["category_level_evidence"][
        "live_auth_report_validated"
    ]
    assert report["live_reports"]["category_level_evidence"][
        "live_mutation_report_validated"
    ]
    assert report["auth_rows_promotable"] == 146
    assert report["auth_rows_blocked"] == 0


def test_row_evidence_report_can_support_subset_auth_row_promotion_without_blocking_release_gate(
    repo_root: Path, tmp_path: Path
) -> None:
    mod = _load_module(repo_root)
    src_manifest = json.loads(
        (repo_root / "compat" / "auth_row_evidence.json").read_text()
    )
    src_rows = json.loads((repo_root / "compat" / "parity_rows.json").read_text())
    promote = [row["row_id"] for row in src_manifest["auth_mappings"][:2]]
    for row_id in promote:
        _with_row_open_or_pass(src_manifest, src_rows, row_id, as_pass=False)
    baseline_promotable = sum(
        1 for row in src_manifest["auth_mappings"] if row.get("row_status") == "pass"
    )
    for row_id in promote:
        _with_row_open_or_pass(src_manifest, src_rows, row_id, as_pass=True)

    manifest_path = tmp_path / "auth_row_evidence_pass.json"
    parity_rows_path = tmp_path / "parity_rows_pass.json"
    report_path = tmp_path / "auth_row_evidence_report.json"
    _write_json(manifest_path, src_manifest)
    _write_json(parity_rows_path, src_rows)
    payload = _builder_shaped_auth_row_evidence_report(promote)
    _write_json(report_path, payload)

    report = mod.build_report(
        repo_root=repo_root,
        manifest_path=manifest_path,
        parity_rows_path=parity_rows_path,
        row_evidence_report=report_path,
    )

    assert report["status"] == "pass"
    assert report["strict_ok"] is True
    assert report["auth_rows_promotable"] == baseline_promotable + len(promote)
    assert report["auth_rows_blocked"] == 146 - report["auth_rows_promotable"]
    assert report["category_promotion"] == {"auth": False}


@pytest.mark.parametrize(
    "variant",
    ["missing", "duplicate", "extra"],
)
def test_manifest_variants_fail_closed_for_missing_duplicate_or_extra_mappings(
    repo_root: Path, tmp_path: Path, variant: str
) -> None:
    mod = _load_module(repo_root)
    src = json.loads((repo_root / "compat" / "auth_row_evidence.json").read_text())

    if variant == "missing":
        src["auth_mappings"] = src["auth_mappings"][:-1]
    elif variant == "duplicate":
        src["auth_mappings"].append(src["auth_mappings"][0])
    else:
        extra = copy.deepcopy(src["auth_mappings"][0])
        extra["row_id"] = "auth.invalid.synthetic"
        src["auth_mappings"].append(extra)

    manifest_path = tmp_path / "auth_row_evidence_bad.json"
    _write_json(manifest_path, src)

    report = mod.build_report(repo_root=repo_root, manifest_path=manifest_path)

    assert report["status"] == "fail"
    assert report["strict_ok"] is False
    assert report["strict_exit_code"] == 77
    assert any(
        token in "\n".join(report["errors"]).lower()
        for token in (
            "duplicate auth mappings",
            "missing auth mappings",
            "extra auth mappings",
        )
    )


def test_pass_mappings_require_row_specific_evidence_tokens(
    repo_root: Path, tmp_path: Path
) -> None:
    mod = _load_module(repo_root)
    src_manifest = json.loads(
        (repo_root / "compat" / "auth_row_evidence.json").read_text()
    )
    src_rows = json.loads((repo_root / "compat" / "parity_rows.json").read_text())

    sample_index, sample_mapping = 0, src_manifest["auth_mappings"][0]
    sample = sample_mapping["row_id"]
    _with_row_open_or_pass(src_manifest, src_rows, sample, as_pass=False)
    sample_mapping = src_manifest["auth_mappings"][sample_index]
    target_row = next(
        row
        for row in src_rows["rows"]
        if row.get("id") == sample and row.get("category") == "auth"
    )
    target_row["status"] = "pass"
    target_row["row_status"] = "pass"

    manifest_path = tmp_path / "auth_row_evidence_pass.json"
    parity_rows_path = tmp_path / "parity_rows_pass.json"
    changed = copy.deepcopy(sample_mapping)
    changed["status"] = "pass"
    changed["row_status"] = "pass"
    changed["promotion_allowed"] = True
    changed["missing_for_promotion"] = []

    new_manifest = copy.deepcopy(src_manifest)
    new_manifest["auth_mappings"][sample_index] = copy.deepcopy(changed)

    _write_json(manifest_path, new_manifest)
    _write_json(parity_rows_path, src_rows)

    report = mod.build_report(
        repo_root=repo_root,
        manifest_path=manifest_path,
        parity_rows_path=parity_rows_path,
    )

    assert report["status"] == "fail"
    assert report["strict_ok"] is False
    assert any(
        "missing required row evidence tokens" in msg.lower()
        for msg in report["errors"]
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("missing_for_promotion", ""),
        ("satisfied_required_evidence", "live_differential_result"),
    ],
)
def test_pass_mapping_evidence_fields_must_stay_lists(
    repo_root: Path, tmp_path: Path, field: str, value: object
) -> None:
    mod = _load_module(repo_root)
    src_manifest = json.loads(
        (repo_root / "compat" / "auth_row_evidence.json").read_text()
    )
    src_rows = json.loads((repo_root / "compat" / "parity_rows.json").read_text())

    row_id = src_manifest["auth_mappings"][0]["row_id"]
    _with_row_open_or_pass(src_manifest, src_rows, row_id, as_pass=False)
    _with_row_open_or_pass(src_manifest, src_rows, row_id, as_pass=True)
    src_manifest["auth_mappings"][0][field] = value

    manifest_path = tmp_path / "auth_row_evidence_bad_field.json"
    parity_rows_path = tmp_path / "parity_rows_pass.json"
    report_path = tmp_path / "auth_row_evidence_report.json"
    _write_json(manifest_path, src_manifest)
    _write_json(parity_rows_path, src_rows)
    _write_json(report_path, _builder_shaped_auth_row_evidence_report([row_id]))

    report = mod.build_report(
        repo_root=repo_root,
        manifest_path=manifest_path,
        parity_rows_path=parity_rows_path,
        row_evidence_report=report_path,
    )

    assert report["status"] == "fail"
    assert report["strict_ok"] is False
    assert any("must be a list" in err for err in report["errors"])


def test_mapping_required_evidence_rejects_duplicate_tokens(
    repo_root: Path, tmp_path: Path
) -> None:
    mod = _load_module(repo_root)
    src_manifest = json.loads(
        (repo_root / "compat" / "auth_row_evidence.json").read_text()
    )
    src_rows = json.loads((repo_root / "compat" / "parity_rows.json").read_text())

    row_id = src_manifest["auth_mappings"][0]["row_id"]
    _with_row_open_or_pass(src_manifest, src_rows, row_id, as_pass=True)
    src_manifest["auth_mappings"][0]["required_evidence"].append(
        src_manifest["auth_mappings"][0]["required_evidence"][0]
    )

    manifest_path = tmp_path / "auth_row_evidence_duplicate_required.json"
    parity_rows_path = tmp_path / "parity_rows_pass.json"
    report_path = tmp_path / "auth_row_evidence_report.json"
    _write_json(manifest_path, src_manifest)
    _write_json(parity_rows_path, src_rows)
    _write_json(report_path, _builder_shaped_auth_row_evidence_report([row_id]))

    report = mod.build_report(
        repo_root=repo_root,
        manifest_path=manifest_path,
        parity_rows_path=parity_rows_path,
        row_evidence_report=report_path,
    )

    assert report["status"] == "fail"
    assert report["strict_ok"] is False
    assert any("required_evidence" in err and "duplicate" in err for err in report["errors"])


@pytest.mark.parametrize(
    "field",
    ["missing_for_promotion", "satisfied_required_evidence"],
)
def test_mapping_evidence_lists_reject_duplicate_tokens(
    repo_root: Path, tmp_path: Path, field: str
) -> None:
    mod = _load_module(repo_root)
    src_manifest = json.loads(
        (repo_root / "compat" / "auth_row_evidence.json").read_text()
    )
    src_rows = json.loads((repo_root / "compat" / "parity_rows.json").read_text())

    row_id = src_manifest["auth_mappings"][0]["row_id"]
    report_kwargs = {"repo_root": repo_root}
    manifest_path = tmp_path / "auth_row_evidence_duplicate_list.json"

    if field == "missing_for_promotion":
        mapping = src_manifest["auth_mappings"][0]
        _with_row_open_or_pass(src_manifest, src_rows, row_id, as_pass=False)
        mapping[field] = list(mapping["required_evidence"])
        mapping[field].append(mapping[field][0])
    else:
        _with_row_open_or_pass(src_manifest, src_rows, row_id, as_pass=True)
        src_manifest["auth_mappings"][0][field] = [
            "live_differential_result",
            "session_credential_evidence",
            "live_differential_result",
        ]
        parity_rows_path = tmp_path / "parity_rows_pass.json"
        report_path = tmp_path / "auth_row_evidence_report.json"
        _write_json(parity_rows_path, src_rows)
        _write_json(report_path, _builder_shaped_auth_row_evidence_report([row_id]))
        report_kwargs.update(
            {
                "parity_rows_path": parity_rows_path,
                "row_evidence_report": report_path,
            }
        )

    _write_json(manifest_path, src_manifest)
    report = mod.build_report(manifest_path=manifest_path, **report_kwargs)

    assert report["status"] == "fail"
    assert report["strict_ok"] is False
    assert any(field in err and "duplicate" in err for err in report["errors"])


def test_manifest_mapping_rejects_non_redacted_values(
    repo_root: Path, tmp_path: Path
) -> None:
    mod = _load_module(repo_root)
    src = json.loads((repo_root / "compat" / "auth_row_evidence.json").read_text())
    raw_sid = "SID=" + "ABCDEFGHIJKLMNO"
    src["auth_mappings"][0]["evidence_basis"] = f"captured from {raw_sid}"

    manifest_path = tmp_path / "auth_row_evidence_raw_value.json"
    _write_json(manifest_path, src)

    report = mod.build_report(repo_root=repo_root, manifest_path=manifest_path)

    assert report["status"] == "fail"
    assert report["strict_ok"] is False
    assert any("non-redacted" in err for err in report["errors"])
    assert "ABCDEFGHIJKLMNO" not in "\n".join(report["errors"])


@pytest.mark.parametrize(
    "variant",
    [
        "unknown_row",
        "duplicate_row",
        "stale_report",
        "non_redacted",
        "insufficient_tokens",
    ],
)
def test_row_evidence_report_fails_closed_for_invalid_inputs(
    repo_root: Path, tmp_path: Path, variant: str
) -> None:
    mod = _load_module(repo_root)
    src_manifest = json.loads(
        (repo_root / "compat" / "auth_row_evidence.json").read_text()
    )
    src_rows = json.loads((repo_root / "compat" / "parity_rows.json").read_text())

    payload = _base_auth_row_evidence_report()
    if variant == "unknown_row":
        payload["rows"] = [
            {
                "row_id": "auth.invalid.synthetic",
                "satisfied_required_evidence": [
                    "live_differential_result",
                    "session_credential_evidence",
                ],
            }
        ]
    elif variant == "duplicate_row":
        payload["rows"] = [
            {
                "row_id": src_manifest["auth_mappings"][0]["row_id"],
                "satisfied_required_evidence": [
                    "live_differential_result",
                    "session_credential_evidence",
                ],
            },
            {
                "row_id": src_manifest["auth_mappings"][0]["row_id"],
                "satisfied_required_evidence": [
                    "live_differential_result",
                    "session_credential_evidence",
                ],
            },
        ]
    elif variant == "stale_report":
        payload["expires_at"] = "2000-01-01T00:00:00Z"
        payload["rows"] = [
            {
                "row_id": src_manifest["auth_mappings"][0]["row_id"],
                "satisfied_required_evidence": [
                    "live_differential_result",
                    "session_credential_evidence",
                ],
            }
        ]
    elif variant == "non_redacted":
        payload["rows"] = [
            {
                "row_id": src_manifest["auth_mappings"][0]["row_id"],
                "satisfied_required_evidence": [
                    "live_differential_result",
                    "session_credential_evidence",
                ],
                "notes": "ya" + "29.abcdefghijklmnopqrstuvwxyz123456",
            }
        ]
    else:
        row_id = src_manifest["auth_mappings"][0]["row_id"]
        _with_row_open_or_pass(src_manifest, src_rows, row_id, as_pass=True)
        payload["rows"] = [
            {
                "row_id": row_id,
                "satisfied_required_evidence": ["live_differential_result"],
            }
        ]

    report_path = tmp_path / "auth_row_evidence_report.json"
    manifest_path = tmp_path / "auth_row_evidence_bad.json"
    parity_rows_path = tmp_path / "parity_rows_bad.json"
    _write_json(manifest_path, src_manifest)
    _write_json(parity_rows_path, src_rows)
    _write_json(report_path, payload)

    kwargs = {
        "repo_root": repo_root,
        "manifest_path": manifest_path,
        "parity_rows_path": parity_rows_path,
        "row_evidence_report": report_path,
    }
    report = mod.build_report(**kwargs)

    assert report["status"] == "fail"
    assert report["strict_ok"] is False
    assert report["strict_exit_code"] == 77
    assert any(
        token in "\n".join(report["errors"]).lower()
        for token in (
            "not in parity_rows.json",
            "duplicate row_id",
            "stale",
            "non-redacted",
            "unknown keys",
            "missing required row evidence tokens",
            "missing required satisfied_required_evidence",
        )
    )


def test_exact_one_to_one_claim_ready_must_stay_false(
    repo_root: Path, tmp_path: Path
) -> None:
    mod = _load_module(repo_root)
    src = json.loads((repo_root / "compat" / "auth_row_evidence.json").read_text())
    src["exact_one_to_one_claim_ready"] = True

    manifest_path = tmp_path / "auth_row_evidence_exact.json"
    _write_json(manifest_path, src)

    report = mod.build_report(repo_root=repo_root, manifest_path=manifest_path)

    assert report["status"] == "fail"
    assert any(
        "exact_one_to_one_claim_ready must be false" in err for err in report["errors"]
    )


@pytest.mark.parametrize(
    "category_promotion",
    [
        {"auth": False, "cli": True},
        {"auth": 1},
        ["auth"],
    ],
)
def test_category_promotion_must_be_exact_auth_boolean(
    repo_root: Path, tmp_path: Path, category_promotion: object
) -> None:
    mod = _load_module(repo_root)
    src = json.loads((repo_root / "compat" / "auth_row_evidence.json").read_text())
    src["category_promotion"] = category_promotion

    manifest_path = tmp_path / "auth_row_evidence_bad_category.json"
    _write_json(manifest_path, src)

    report = mod.build_report(repo_root=repo_root, manifest_path=manifest_path)

    assert report["status"] == "fail"
    assert report["strict_ok"] is False
    assert any(
        "category_promotion must be exactly" in err for err in report["errors"]
    )
