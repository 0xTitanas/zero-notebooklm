#!/usr/bin/env python3
"""Phase 15 pass-row evidence audit.

Validates that every pass row in compat/parity_rows.json has at least one
evidence record in compat/parity_evidence.json, that each required_evidence
 token is satisfied by a matching record, that referenced files exist, and that
 non-auth rows are closed-system/offline.

Pure/offline: no Path.home(), no live services, no credentials, no
browser/keychain access, no matrix/ledger mutation.

Usage:
  python scripts/parity_evidence_audit.py --json           # exits 0; reports static validity
  python scripts/parity_evidence_audit.py --json --strict  # exits 0 only if evidence valid
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "parity_evidence_audit/1"
TARGET = "notebooklm-py==0.7.2"

REQUIRED_EVIDENCE_FIELDS = [
    "row_id",
    "evidence_id",
    "evidence_type",
    "comparator",
    "closed_system",
    "no_live",
    "promotion_basis",
]

_ALLOWED_AUTH_EVIDENCE_TYPES = frozenset(
    {
        "redacted_live_differential_result",
        "redacted_session_credential_evidence",
    }
)
_AUTH_EVIDENCE_TYPE_BY_ID = {
    "live_differential_result": "redacted_live_differential_result",
    "session_credential_evidence": "redacted_session_credential_evidence",
}

_LIVE_COMMAND_PATTERNS = (
    "http://",
    "https://",
    "curl ",
    "wget ",
    "browser",
    "keychain",
    "playwright",
    "selenium",
    "--live",
    "notebooklm.google",
)

_AUTH_COMMAND_PATTERNS = _LIVE_COMMAND_PATTERNS + (
    "playback",
    "browser",
)

_AUTH_COMMAND_FORBIDDEN_SUBSTRINGS = (
    "browser",
    "keychain",
    "http://",
    "https://",
    "--live",
    "playwright",
    "selenium",
    "notebooklm.google",
)

_VALUE_FREE_PATTERNS = (
    re.compile(r"\b[a-zA-Z0-9_.%+-]+@[a-zA-Z0-9.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"\bya29\.[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    re.compile(r"\b1//[A-Za-z0-9_\-]{30,}\b"),
    re.compile(r"(?:/Users|/home|/var/folders|C:\\Users)[^\s]*"),
    re.compile(
        r"\b(?:SID|__Secure-"
        r"[A-Za-z0-9_-]+|SAPISID|APISID|HSID|SSID|SIDCC|NID)="
        r"[A-Za-z0-9_./+\-]{12,}"
    ),
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_json(path: Path) -> tuple[Any, list[str]]:
    errors: list[str] = []
    try:
        return json.loads(path.read_text(encoding="utf-8")), errors
    except Exception as exc:
        errors.append(f"{path.name} parse error: {exc}")
        return None, errors


def _record_string_fields(value: Any, path: str = "") -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            out.extend(_record_string_fields(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            out.extend(_record_string_fields(child, f"{path}[{idx}]"))
    elif isinstance(value, str):
        out.append((path, value))
    return out


def _contains_value_free_issues(value: str) -> bool:
    if not isinstance(value, str):
        return False
    return any(pattern.search(value) for pattern in _VALUE_FREE_PATTERNS)


def _is_repo_local_reference(path: str, *, repo_root: Path) -> bool:
    if not path:
        return True
    if path.startswith(("/", "\\", "C:", "c:", "~")):
        return False
    if path.startswith(("http://", "https://")):
        return False
    if "://" in path:
        return False
    file_part = path.split("::", 1)[0]
    candidate = Path(file_part)
    if candidate.is_absolute() or ".." in candidate.parts:
        return False
    full = repo_root / candidate
    try:
        full.relative_to(repo_root)
    except ValueError:
        return False
    return True


def _check_repo_path(
    *,
    rec: dict[str, Any],
    field: str,
    repo_root: Path,
    issues: list[str],
) -> None:
    value = str(rec.get(field, ""))
    label = f"{rec.get('row_id', '<no-row_id>')}/<evidence>"
    if not value:
        return
    if not _is_repo_local_reference(value, repo_root=repo_root):
        issues.append(f"{label}: {field} is not a safe repo-local reference")


def _check_evidence_record(
    rec: dict[str, Any],
    repo_root: Path,
    is_auth_pass_row: bool,
    required_evidence: set[str],
) -> dict[str, list[str]]:
    """Return categorised issues for a single evidence record."""
    field_issues: list[str] = []
    file_issues: list[str] = []
    live_issues: list[str] = []

    rid = rec.get("row_id", "<no-row_id>")
    label = f"{rid}/<evidence>"

    # Required fields
    for field in REQUIRED_EVIDENCE_FIELDS:
        if field not in rec:
            field_issues.append(f"{label}: missing required field {field!r}")

    if is_auth_pass_row:
        evidence_id = rec.get("evidence_id")
        evidence_type = rec.get("evidence_type")
        expected_type = _AUTH_EVIDENCE_TYPE_BY_ID.get(str(evidence_id))
        if evidence_id not in required_evidence:
            live_issues.append(
                f"{label}: auth evidence_id must be one of the row required_evidence tokens"
            )
        if evidence_type not in _ALLOWED_AUTH_EVIDENCE_TYPES:
            live_issues.append(f"{label}: unsupported auth evidence_type")
        elif expected_type is None or evidence_type != expected_type:
            live_issues.append(
                f"{label}: auth evidence_type must match evidence_id token"
            )
        if rec.get("redacted") is not True:
            live_issues.append(f"{label}: redacted must be true")
        command = rec.get("command", "")
        if command:
            command_text = str(command)
            command_lower = command_text.lower()
            if not _is_repo_local_reference(command_text, repo_root=repo_root):
                live_issues.append(
                    f"{label}: auth command must be a repo-local reference or empty"
                )
            for token in _AUTH_COMMAND_FORBIDDEN_SUBSTRINGS:
                if token in command_lower:
                    live_issues.append(
                        f"{label}: auth command contains disallowed token {token!r}"
                    )
                    break
            if "http" in command_lower:
                live_issues.append(
                    f"{label}: auth command must not include raw live command text"
                )
            for pattern in _AUTH_COMMAND_PATTERNS:
                if pattern in command_lower:
                    live_issues.append(
                        f"{label}: auth command contains disallowed token {pattern!r}"
                    )
                    break

        _check_repo_path(
            rec=rec,
            field="artifact_path",
            repo_root=repo_root,
            issues=live_issues,
        )
        _check_repo_path(
            rec=rec,
            field="test_path",
            repo_root=repo_root,
            issues=live_issues,
        )

        for _, value in _record_string_fields(rec):
            if _contains_value_free_issues(value):
                live_issues.append(
                    f"{label}: string field contains value-free violation"
                )
                break

    else:
        # Must be closed system and offline
        if not rec.get("closed_system", False):
            live_issues.append(f"{label}: closed_system must be true")
        if not rec.get("no_live", False):
            live_issues.append(f"{label}: no_live must be true")

        # Command must not reference live scope
        command = rec.get("command", "")
        if command:
            for pattern in _LIVE_COMMAND_PATTERNS:
                if pattern in command:
                    live_issues.append(
                        f"{label}: command contains live scope pattern {pattern!r}"
                    )
                    break

        _check_repo_path(
            rec=rec,
            field="artifact_path",
            repo_root=repo_root,
            issues=file_issues,
        )
        _check_repo_path(
            rec=rec,
            field="test_path",
            repo_root=repo_root,
            issues=file_issues,
        )

    # artifact_path/test_path file existence (if non-empty and valid)
    artifact_path = rec.get("artifact_path", "")
    if artifact_path and _is_repo_local_reference(
        str(artifact_path), repo_root=repo_root
    ):
        file_part = str(artifact_path).split("::")[0]
        full = repo_root / file_part
        if not full.exists():
            file_issues.append(f"{label}: artifact_path not found")

    test_path = rec.get("test_path", "")
    if test_path and _is_repo_local_reference(str(test_path), repo_root=repo_root):
        file_part = str(test_path).split("::")[0]
        full_test = repo_root / file_part
        if not full_test.exists():
            file_issues.append(f"{label}: test_path not found")

    return {
        "field_issues": field_issues,
        "file_issues": file_issues,
        "live_issues": live_issues,
    }


def build_report(*, repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    """Return offline pass-row evidence audit report. Pure: no Path.home() call."""
    repo_root = Path(repo_root)
    evidence_path = repo_root / "compat" / "parity_evidence.json"
    rows_path = repo_root / "compat" / "parity_rows.json"

    load_errors: list[str] = []

    # Load evidence manifest
    evidence_present = evidence_path.is_file()
    if not evidence_present:
        load_errors.append("compat/parity_evidence.json missing")
        evidence_records: list[dict] = []
    else:
        evidence_data, errs = _load_json(evidence_path)
        load_errors.extend(errs)
        evidence_records = (
            evidence_data.get("evidence_records", [])
            if isinstance(evidence_data, dict)
            else []
        )

    # Load pass rows
    if not rows_path.is_file():
        load_errors.append("compat/parity_rows.json missing")
        pass_rows: list[dict] = []
        all_rows: list[dict] = []
    else:
        rows_data, errs = _load_json(rows_path)
        load_errors.extend(errs)
        all_rows = rows_data.get("rows", []) if isinstance(rows_data, dict) else []
        pass_rows = [r for r in all_rows if r.get("status") == "pass"]

    row_category: dict[str, str] = {}
    row_required: dict[str, set[str]] = {}
    for row in all_rows:
        if isinstance(row, dict):
            rid = str(row.get("id", ""))
            if rid:
                row_category[rid] = str(row.get("category", ""))
                required_tokens = row.get("required_evidence", [])
                if isinstance(required_tokens, list):
                    row_required[rid] = {
                        str(token)
                        for token in required_tokens
                        if isinstance(token, str)
                    }

    # Build index: row_id -> set of evidence_ids in the manifest
    evidence_by_row: dict[str, set[str]] = {}
    for rec in evidence_records:
        rid = rec.get("row_id", "")
        eid = rec.get("evidence_id", "")
        if rid and eid:
            evidence_by_row.setdefault(rid, set()).add(eid)

    # Build index of pass row IDs for cross-checking
    pass_row_ids = {r.get("id", "") for r in pass_rows}

    # Check each pass row
    rows_missing_evidence: list[str] = []
    rows_with_unmet_tokens: list[dict[str, Any]] = []
    for row in pass_rows:
        rid = row.get("id", "")
        required_tokens = row.get("required_evidence", [])
        row_evidence_ids = evidence_by_row.get(rid, set())

        if not row_evidence_ids:
            rows_missing_evidence.append(rid)
        else:
            unmet = [t for t in required_tokens if t not in row_evidence_ids]
            if unmet:
                rows_with_unmet_tokens.append({"row_id": rid, "unmet_tokens": unmet})

    # Check records reference valid pass rows
    records_for_open_rows: list[str] = []
    for rec in evidence_records:
        rid = rec.get("row_id", "")
        if rid and rid not in pass_row_ids:
            records_for_open_rows.append("evidence record row_id is not in pass rows")

    # Validate each evidence record
    records_missing_required_fields: list[str] = []
    records_missing_referenced_files: list[str] = []
    records_with_live_scope: list[str] = []

    for rec in evidence_records:
        row_id = rec.get("row_id", "")
        is_auth_pass_row = (
            row_id in pass_row_ids and row_category.get(row_id, "") == "auth"
        )
        issues = _check_evidence_record(
            rec,
            repo_root,
            is_auth_pass_row=is_auth_pass_row,
            required_evidence=row_required.get(row_id, set()),
        )
        records_missing_required_fields.extend(issues["field_issues"])
        records_missing_referenced_files.extend(issues["file_issues"])
        records_with_live_scope.extend(issues["live_issues"])

    strict_ok = (
        evidence_present
        and not load_errors
        and not rows_missing_evidence
        and not rows_with_unmet_tokens
        and not records_missing_required_fields
        and not records_missing_referenced_files
        and not records_with_live_scope
        and not records_for_open_rows
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "target": TARGET,
        "evidence_file_present": evidence_present,
        "pass_rows_audited": len(pass_rows),
        "evidence_records_count": len(evidence_records),
        "rows_missing_evidence": rows_missing_evidence,
        "rows_with_unmet_tokens": rows_with_unmet_tokens,
        "records_missing_required_fields": records_missing_required_fields,
        "records_missing_referenced_files": records_missing_referenced_files,
        "records_with_live_scope": records_with_live_scope,
        "records_for_open_rows": records_for_open_rows,
        "strict_ok": strict_ok,
        "strict_exit_code": 0 if strict_ok else 1,
        "load_errors": load_errors,
    }


def _human_text(report: dict[str, Any]) -> str:
    status = "pass" if report["strict_ok"] else "fail"
    lines = [
        f"parity evidence audit: {status}",
        f"pass_rows_audited: {report['pass_rows_audited']}",
        f"evidence_records: {report['evidence_records_count']}",
    ]
    if report["rows_missing_evidence"]:
        lines.append(f"rows_missing_evidence: {report['rows_missing_evidence']}")
    if report["rows_with_unmet_tokens"]:
        lines.append(f"rows_with_unmet_tokens: {report['rows_with_unmet_tokens']}")
    if report["records_with_live_scope"]:
        lines.append(f"records_with_live_scope: {report['records_with_live_scope']}")
    if report["load_errors"]:
        lines.append(f"load_errors: {report['load_errors']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="parity_evidence_audit.py", description=__doc__
    )
    parser.add_argument("--json", action="store_true", help="emit full JSON report")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero (1) if pass-row evidence is invalid",
    )
    args = parser.parse_args(argv)

    report = build_report()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(_human_text(report))

    if args.strict:
        return int(report["strict_exit_code"])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
