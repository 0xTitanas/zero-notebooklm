"""Phase 24 live mutation/export evidence artifact validation tests."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

TARGET = "notebooklm-py==0.7.2"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _load_module(repo_root: Path):
    path = repo_root / "scripts" / "live_mutation_evidence_audit.py"
    spec = importlib.util.spec_from_file_location("_live_mutation_audit", path)
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


def _base_live_report(repo_root: Path) -> dict:
    states = _category_states(repo_root)
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
        "observations": {"upstream_shape": _shape(), "bare_shape": _shape()},
        "category_promotion": {"cli": False, "api": False, "auth": False, "rpc": False},
        "category_states": {
            "cli": states["cli"],
            "api": states["api"],
            "auth": states["auth"],
            "rpc": states["rpc"],
        },
    }


def _write_report(tmp_path: Path, repo_root: Path, payload: dict) -> Path:
    report = tmp_path / "live_mutation_report.json"
    report.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return report


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


def test_default_no_report_is_blocked_expected_and_closed(repo_root: Path) -> None:
    mod = _load_module(repo_root)
    report = mod.build_report()

    assert report["status"] == "blocked_expected"
    assert report["strict_exit_code"] == 77
    assert report["evidence_validated"] is False
    assert report["category_promotion"] == {
        "cli": False,
        "api": False,
        "auth": False,
        "rpc": False,
    }


def test_synthetic_live_mutation_report_passes_validation(
    repo_root: Path, tmp_path: Path
) -> None:
    mod = _load_module(repo_root)
    payload = _base_live_report(repo_root)
    report_path = _write_report(tmp_path, repo_root, payload)

    report = mod.build_report(argv=["--report", str(report_path)])

    assert report["status"] == "pass"
    assert report["strict_exit_code"] == 0
    assert report["evidence_validated"] is True
    assert report["category_promotion"] == {
        "cli": False,
        "api": False,
        "auth": False,
        "rpc": False,
    }
    assert report["category_states"]["auth"] == _category_states(repo_root)["auth"]


def test_invalid_report_fails_closed_with_strict_77(
    repo_root: Path, tmp_path: Path
) -> None:
    mod = _load_module(repo_root)
    payload = _base_live_report(repo_root)
    payload["strict_exit_code"] = 77
    report_path = _write_report(tmp_path, repo_root, payload)

    report = mod.build_report(argv=["--report", str(report_path)])

    assert report["status"] == "fail"
    assert report["strict_exit_code"] == 77
    assert report["evidence_validated"] is False
    assert any(
        "strict_exit_code is not 0" in v for v in report["validation"]["violations"]
    )


def test_operation_allowlist_with_public_share_term_is_rejected(
    repo_root: Path, tmp_path: Path
) -> None:
    mod = _load_module(repo_root)
    payload = _base_live_report(repo_root)
    payload["operation_allowlist"] = ["create_note", "share_add", "delete_note"]
    report_path = _write_report(tmp_path, repo_root, payload)

    report = mod.build_report(argv=["--report", str(report_path)])

    assert report["status"] == "fail"
    assert report["strict_exit_code"] == 77
    assert any(
        "forbidden mutation/public" in v.lower()
        for v in report["validation"]["violations"]
    )


def test_category_promotion_or_auth_promotion_is_rejected(
    repo_root: Path, tmp_path: Path
) -> None:
    mod = _load_module(repo_root)
    payload = _base_live_report(repo_root)
    payload["category_promotion"] = {
        "cli": True,
        "api": False,
        "auth": False,
        "rpc": False,
    }
    report_path = _write_report(tmp_path, repo_root, payload)

    report = mod.build_report(argv=["--report", str(report_path)])

    assert report["status"] == "fail"
    assert report["strict_exit_code"] == 77
    assert any("must be false" in v.lower() for v in report["validation"]["violations"])


def test_duplicate_json_key_is_rejected(repo_root: Path, tmp_path: Path) -> None:
    mod = _load_module(repo_root)
    payload = _write_report(tmp_path, repo_root, _base_live_report(repo_root))
    raw = payload.read_text(encoding="utf-8")
    raw = raw.replace(
        '"status": "pass",',
        '"status": "pass", "status": "pass",',
        1,
    )

    dup_path = tmp_path / "dup_report.json"
    dup_path.write_text(raw, encoding="utf-8")

    report = mod.build_report(argv=["--report", str(dup_path)])

    assert report["status"] == "fail"
    assert report["strict_exit_code"] == 77
    assert any("duplicate JSON keys" in v for v in report["validation"]["violations"])


def test_unknown_top_level_raw_fields_are_rejected(
    repo_root: Path, tmp_path: Path
) -> None:
    mod = _load_module(repo_root)
    payload = _base_live_report(repo_root)
    payload["note_body"] = "private notebook content that must not validate"
    report_path = _write_report(tmp_path, repo_root, payload)

    report = mod.build_report(argv=["--report", str(report_path)])

    assert report["status"] == "fail"
    assert report["strict_exit_code"] == 77
    assert any(
        "unknown top-level report keys" in v for v in report["validation"]["violations"]
    )


def test_missing_blockers_field_is_rejected(repo_root: Path, tmp_path: Path) -> None:
    mod = _load_module(repo_root)
    payload = _base_live_report(repo_root)
    payload.pop("blockers")
    report_path = _write_report(tmp_path, repo_root, payload)

    report = mod.build_report(argv=["--report", str(report_path)])

    assert report["status"] == "fail"
    assert report["strict_exit_code"] == 77
    assert any(
        "blockers must be present as empty list" in v
        for v in report["validation"]["violations"]
    )


def test_raw_sensitive_values_and_storage_path_fail_redaction(
    repo_root: Path, tmp_path: Path
) -> None:
    mod = _load_module(repo_root)
    payload = _base_live_report(repo_root)
    token_like = "ya" + "29.synthetic-token-that-should-be-redacted"
    payload["leak"] = {
        "raw": token_like,
        "path": str(tmp_path / "sensitive" / "path.json"),
        "notebook": "nb-SYNTHETHIC-999",
    }
    report_path = _write_report(tmp_path, repo_root, payload)

    report = mod.build_report(argv=["--report", str(report_path)])

    assert report["status"] == "fail"
    assert report["strict_exit_code"] == 77
    assert any(
        "redaction pattern hit" in v.lower() for v in report["validation"]["violations"]
    )
