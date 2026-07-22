#!/usr/bin/env python3
"""Auth-closure auth-row promotion applier.

Consumes auth-row proof reports from ``auth_row_evidence_report_builder.py`` and,
when requested, writes a minimal, purely local compatibility-delta set for
auth-row promotion evidence.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "auth_row_promotion_apply/1"
TARGET = "notebooklm-py==0.7.2"
STRICT_BLOCKED_EXIT = 77
REPO_ROOT = Path(__file__).resolve().parents[1]

_AUTH_EVIDENCE_TYPES = {
    "live_differential_result": "redacted_live_differential_result",
    "session_credential_evidence": "redacted_session_credential_evidence",
}

_OS_MAP = {
    "macos": "macOS",
    "ubuntu": "Ubuntu-LTS-Linux",
    "windows11": "Windows-11",
}

_PATH_MAP = {
    "profile_select": "profile-select",
    "account_select": "account-select",
}

_BROWSER_SLUG_MAP = {
    "opera_gx": "opera-gx",
}

_AUTH_MATRIX_FIELDS = {
    "cookie_import": {
        "name": "browser_cookie_import_matrix",
        "required_field": "path",
    },
    "interactive": {
        "name": "interactive_login_matrix",
        "required_field": "flow",
    },
}


class _ApplyError(ValueError):
    """Raised for invalid proof-to-ledger mapping operations."""


def _repo_root(path: str | Path | None = None) -> Path:
    return Path(REPO_ROOT if path is None else path)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, indent=2) + "\n").encode("utf-8")


def _write_json(path: Path, payload: Any) -> None:
    path.write_bytes(_json_bytes(payload))


def _load_builder_module(root: Path):
    path = root / "scripts" / "auth_row_evidence_report_builder.py"
    spec = importlib.util.spec_from_file_location(
        "_auth_row_report_builder",
        str(path),
    )
    if spec is None or spec.loader is None:
        raise _ApplyError("could not load auth_row_evidence_report_builder.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _auth_row_matrix_spec(row_id: str) -> tuple[str, dict[str, str]]:
    parts = row_id.split(".")
    if len(parts) != 5:
        raise _ApplyError("invalid auth row id format")

    _, row_type, browser, os_slug, token = parts
    browser = _BROWSER_SLUG_MAP.get(browser, browser)
    matrix_spec = _AUTH_MATRIX_FIELDS.get(row_type)
    if matrix_spec is None:
        raise _ApplyError("unknown auth row type")

    os_name = _OS_MAP.get(os_slug)
    if os_name is None:
        raise _ApplyError("unknown auth row os token")

    match_field = matrix_spec["required_field"]
    if row_type == "cookie_import":
        token = _PATH_MAP.get(token, token)

    return matrix_spec["name"], {
        "browser": browser,
        "os": os_name,
        match_field: token,
    }


def _find_matrix_row(
    matrix: dict[str, Any],
    matrix_name: str,
    match_fields: dict[str, str],
) -> dict[str, Any]:
    rows = matrix.get(matrix_name)
    if not isinstance(rows, list):
        raise _ApplyError("matrix file is missing expected row list")

    for row in rows:
        if not isinstance(row, dict):
            continue
        if all(row.get(field) == value for field, value in match_fields.items()):
            return row

    raise _ApplyError("auth row has no corresponding auth_matrix entry")


def _evidence_basis(row_id: str, satisfied_tokens: list[str]) -> str:
    token_list = ", ".join(sorted(set(satisfied_tokens)))
    return f"auth closure redacted proof satisfies: {token_list} for {row_id}"


def _build_auth_evidence_records(row_id: str, comparator: str) -> list[dict[str, Any]]:
    return [
        {
            "row_id": row_id,
            "evidence_id": token,
            "evidence_type": evidence_type,
            "command": "",
            "test_path": "",
            "artifact_path": "",
            "comparator": comparator,
            "closed_system": False,
            "no_live": False,
            "redacted": True,
            "promotion_basis": "auth-row proof evidence from validated auth-row proof",
        }
        for token, evidence_type in _AUTH_EVIDENCE_TYPES.items()
    ]


def _evidence_record_exists(
    records: list[dict[str, Any]], candidate: dict[str, Any]
) -> bool:
    return any(isinstance(record, dict) and record == candidate for record in records)


def _auth_records_for_required_tokens(
    records: list[dict[str, Any]], row_id: str, required_tokens: set[str]
) -> list[dict[str, Any]]:
    return [
        record
        for record in records
        if isinstance(record, dict)
        and record.get("row_id") == row_id
        and record.get("evidence_id") in required_tokens
    ]


def _make_invalid_report(code: int, errors: list[str]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "target": TARGET,
        "status": "fail",
        "strict_ok": False,
        "strict_exit_code": code,
        "proof_rows": 0,
        "rows_promoted": 0,
        "rows_unchanged": 0,
        "output_files_written": 0,
        "exact_one_to_one_claim_ready": False,
        "errors": errors,
        "notes": [],
    }


def build_report(
    *,
    proofs: str | Path,
    repo_root: str | Path | None = None,
    output_dir: str | Path | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    root = _repo_root(repo_root)
    errors: list[str] = []

    try:
        builder = _load_builder_module(root)
        proof_report = builder.build_report(
            proofs,
            repo_root=root,
        )
    except Exception as exc:  # pragma: no cover - delegated validation diagnostics
        msg = str(exc)
        if hasattr(exc, "violations"):
            msg = ", ".join(exc.violations)
        return _make_invalid_report(STRICT_BLOCKED_EXIT, [msg])

    proof_rows = proof_report.get("rows", [])
    if not isinstance(proof_rows, list):
        return _make_invalid_report(STRICT_BLOCKED_EXIT, ["proof rows malformed"])

    parity_path = root / "compat" / "parity_rows.json"
    manifest_path = root / "compat" / "auth_row_evidence.json"
    matrix_path = root / "compat" / "auth_matrix.json"
    evidence_path = root / "compat" / "parity_evidence.json"

    try:
        parity_data = _load_json(parity_path)
        manifest = _load_json(manifest_path)
        matrix = _load_json(matrix_path)
        evidence_data = _load_json(evidence_path)
    except Exception as exc:  # pragma: no cover
        return _make_invalid_report(STRICT_BLOCKED_EXIT, [f"load failure: {exc}"])

    rows = parity_data.get("rows", [])
    if not isinstance(rows, list):
        return _make_invalid_report(
            STRICT_BLOCKED_EXIT,
            ["parity_rows rows malformed"],
        )

    mappings = manifest.get("auth_mappings", [])
    if not isinstance(mappings, list):
        return _make_invalid_report(
            STRICT_BLOCKED_EXIT,
            ["auth_row_evidence malformed"],
        )

    rows_by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        rid = str(row.get("id", "")).strip()
        if rid and str(row.get("category", "")) == "auth":
            rows_by_id[rid] = row

    mappings_by_id: dict[str, dict[str, Any]] = {}
    for mapping in mappings:
        if not isinstance(mapping, dict):
            continue
        rid = str(mapping.get("row_id", "")).strip()
        if rid:
            mappings_by_id[rid] = mapping

    auth_evidence_records = (
        evidence_data.get("evidence_records", [])
        if isinstance(evidence_data, dict)
        else []
    )
    if not isinstance(auth_evidence_records, list):
        return _make_invalid_report(
            STRICT_BLOCKED_EXIT,
            ["parity_evidence evidence_records malformed"],
        )

    proof_rows_count = len(proof_rows)
    rows_promoted = 0
    rows_unchanged = 0
    modified = False

    for proof_row in proof_rows:
        if not isinstance(proof_row, dict):
            errors.append("proof row is not an object")
            continue

        row_id = str(proof_row.get("row_id", "")).strip()
        satisfied = proof_row.get("satisfied_required_evidence", [])
        if not isinstance(satisfied, list):
            errors.append(
                f"proof row {row_id!r} has malformed satisfied_required_evidence"
            )
            continue

        parity_row = rows_by_id.get(row_id)
        if parity_row is None:
            errors.append(f"proof row {row_id!r} missing from parity_rows.json")
            continue

        mapping = mappings_by_id.get(row_id)
        if mapping is None:
            errors.append(f"proof row {row_id!r} missing from auth_row_evidence.json")
            continue

        if parity_row.get("category") != "auth":
            errors.append(f"proof row {row_id!r} is not an auth row")
            continue

        required = parity_row.get("required_evidence", [])
        if not isinstance(required, list):
            errors.append(f"parity row {row_id!r} required_evidence malformed")
            continue

        required_set = set(required)
        if set(satisfied) != required_set:
            errors.append(
                f"proof row {row_id!r} required tokens do not match parity row required_evidence"
            )
            continue

        if not all(item in _AUTH_EVIDENCE_TYPES for item in satisfied):
            errors.append(f"proof row {row_id!r} contains unknown evidence tokens")
            continue

        try:
            matrix_name, match_fields = _auth_row_matrix_spec(row_id)
            matrix_row = _find_matrix_row(matrix, matrix_name, match_fields)
        except _ApplyError as exc:
            errors.append(f"auth row {row_id!r}: {exc}")
            continue

        if not isinstance(matrix_row, dict):
            errors.append(f"auth row {row_id!r}: malformed matrix row")
            continue

        comparator = str(
            parity_row.get("comparator") or "auth.session_state_normalized"
        )
        canonical_auth_records = _build_auth_evidence_records(row_id, comparator)
        existing_required_auth_records = _auth_records_for_required_tokens(
            auth_evidence_records, row_id, required_set
        )
        canonical_records_present = all(
            _evidence_record_exists(auth_evidence_records, evidence_record)
            for evidence_record in canonical_auth_records
        )
        stale_auth_records_present = len(existing_required_auth_records) != len(
            canonical_auth_records
        ) or any(
            not _evidence_record_exists(canonical_auth_records, evidence_record)
            for evidence_record in existing_required_auth_records
        )

        prev_parity_status = str(parity_row.get("status", ""))
        prev_blocker_reason = str(parity_row.get("blocker_reason", ""))
        prev_mapping_status = str(mapping.get("status", ""))
        prev_mapping_row_status = str(mapping.get("row_status", ""))
        prev_promotion_allowed = mapping.get("promotion_allowed")
        prev_missing_for_promotion = list(mapping.get("missing_for_promotion", []))
        prev_satisfied = list(mapping.get("satisfied_required_evidence", []))
        prev_matrix_state = str(matrix_row.get("parity_state", ""))

        already_passed = (
            prev_parity_status == "pass"
            and prev_blocker_reason == ""
            and prev_mapping_status == "pass"
            and prev_mapping_row_status == "pass"
            and prev_promotion_allowed is True
            and prev_missing_for_promotion == []
            and set(prev_satisfied) == required_set
            and prev_matrix_state == "pass"
            and canonical_records_present
            and not stale_auth_records_present
        )
        if already_passed:
            rows_unchanged += 1
        else:
            rows_promoted += 1

        parity_row["status"] = "pass"
        parity_row["blocker_reason"] = ""

        mapping.update(
            {
                "status": "pass",
                "row_status": "pass",
                "promotion_allowed": True,
                "missing_for_promotion": [],
                "satisfied_required_evidence": sorted(required_set),
                "evidence_basis": _evidence_basis(row_id, satisfied),
            }
        )

        matrix_row["parity_state"] = "pass"

        old_auth_evidence_records = list(auth_evidence_records)
        auth_evidence_records[:] = [
            record
            for record in auth_evidence_records
            if not (isinstance(record, dict) and record.get("row_id") == row_id)
        ]
        auth_evidence_records.extend(canonical_auth_records)
        if auth_evidence_records != old_auth_evidence_records:
            modified = True

        if prev_parity_status != "pass":
            modified = True
        if prev_blocker_reason != "":
            modified = True
        if prev_mapping_status != "pass":
            modified = True
        if prev_mapping_row_status != "pass":
            modified = True
        if prev_promotion_allowed is not True:
            modified = True
        if prev_missing_for_promotion != []:
            modified = True
        if set(prev_satisfied) != required_set:
            modified = True
        if prev_matrix_state != "pass":
            modified = True

    if errors:
        return {
            "schema_version": SCHEMA_VERSION,
            "target": TARGET,
            "status": "fail",
            "strict_ok": False,
            "strict_exit_code": STRICT_BLOCKED_EXIT,
            "proof_rows": proof_rows_count,
            "rows_promoted": rows_promoted,
            "rows_unchanged": rows_unchanged,
            "output_files_written": 0,
            "exact_one_to_one_claim_ready": False,
            "errors": errors,
            "notes": [],
            "parity_rows_path": str(parity_path),
            "auth_row_evidence_path": str(manifest_path),
            "auth_matrix_path": str(matrix_path),
            "parity_evidence_path": str(evidence_path),
        }

    if rows_promoted == 0 and proof_rows_count == 0:
        errors.append("no proof rows were provided")
        return {
            "schema_version": SCHEMA_VERSION,
            "target": TARGET,
            "status": "fail",
            "strict_ok": False,
            "strict_exit_code": STRICT_BLOCKED_EXIT,
            "proof_rows": proof_rows_count,
            "rows_promoted": rows_promoted,
            "rows_unchanged": rows_unchanged,
            "output_files_written": 0,
            "exact_one_to_one_claim_ready": False,
            "errors": errors,
            "notes": [],
            "parity_rows_path": str(parity_path),
            "auth_row_evidence_path": str(manifest_path),
            "auth_matrix_path": str(matrix_path),
            "parity_evidence_path": str(evidence_path),
        }

    if apply and output_dir is None:
        return {
            "schema_version": SCHEMA_VERSION,
            "target": TARGET,
            "status": "fail",
            "strict_ok": False,
            "strict_exit_code": STRICT_BLOCKED_EXIT,
            "proof_rows": proof_rows_count,
            "rows_promoted": rows_promoted,
            "rows_unchanged": rows_unchanged,
            "output_files_written": 0,
            "exact_one_to_one_claim_ready": False,
            "errors": ["--apply requires --output-dir"],
            "notes": [],
            "parity_rows_path": str(parity_path),
            "auth_row_evidence_path": str(manifest_path),
            "auth_matrix_path": str(matrix_path),
            "parity_evidence_path": str(evidence_path),
        }

    if not apply:
        return {
            "schema_version": SCHEMA_VERSION,
            "target": TARGET,
            "status": "pass",
            "strict_ok": True,
            "strict_exit_code": 0,
            "proof_rows": proof_rows_count,
            "rows_promoted": rows_promoted,
            "rows_unchanged": rows_unchanged,
            "output_files_written": 0,
            "exact_one_to_one_claim_ready": False,
            "errors": [],
            "notes": ["dry-run mode: no files were mutated"],
            "parity_rows_path": str(parity_path),
            "auth_row_evidence_path": str(manifest_path),
            "auth_matrix_path": str(matrix_path),
            "parity_evidence_path": str(evidence_path),
        }

    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    evidence_data["evidence_records"] = auth_evidence_records

    manifest["category_promotion"] = {"auth": False}
    manifest["exact_one_to_one_claim_ready"] = False

    outputs = {
        "parity_rows.json": parity_data,
        "auth_row_evidence.json": manifest,
        "auth_matrix.json": matrix,
        "parity_evidence.json": evidence_data,
    }
    output_files_written = 0
    for filename, payload in outputs.items():
        output_path = target_dir / filename
        serialised = _json_bytes(payload)
        old = output_path.read_bytes() if output_path.exists() else None
        if old != serialised:
            _write_json(output_path, payload)
            output_files_written += 1

    if modified:
        manifest["exact_one_to_one_claim_ready"] = False

    return {
        "schema_version": SCHEMA_VERSION,
        "target": TARGET,
        "status": "pass",
        "strict_ok": True,
        "strict_exit_code": 0,
        "proof_rows": proof_rows_count,
        "rows_promoted": rows_promoted,
        "rows_unchanged": rows_unchanged,
        "output_files_written": output_files_written,
        "exact_one_to_one_claim_ready": False,
        "errors": [],
        "notes": [
            f"output wrote to {target_dir}",
            f"proof rows: {proof_rows_count}",
            f"rows promoted: {rows_promoted}",
        ],
        "parity_rows_path": str(target_dir / "parity_rows.json"),
        "auth_row_evidence_path": str(target_dir / "auth_row_evidence.json"),
        "auth_matrix_path": str(target_dir / "auth_matrix.json"),
        "parity_evidence_path": str(target_dir / "parity_evidence.json"),
    }


def _human_text(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"Auth-closure promotion apply: {report['status']}",
            f"proof_rows: {report['proof_rows']}",
            f"rows_promoted: {report['rows_promoted']}",
            f"rows_unchanged: {report['rows_unchanged']}",
            f"output_files_written: {report['output_files_written']}",
            f"exact_one_to_one_claim_ready: {str(report['exact_one_to_one_claim_ready']).lower()}",
        ]
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proofs", required=True)
    parser.add_argument("--repo-root")
    parser.add_argument("--output-dir")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    report = build_report(
        proofs=args.proofs,
        repo_root=args.repo_root,
        output_dir=args.output_dir,
        apply=args.apply,
    )

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(_human_text(report))

    return int(report["strict_exit_code"])


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
