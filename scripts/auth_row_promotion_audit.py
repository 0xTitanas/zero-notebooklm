#!/usr/bin/env python3
"""Phase 25 auth-row promotion evidence audit.

Validates auth-row manifest mappings as a dedicated promotion gatekeeper before any
row can be promoted. The guardrail is intentionally conservative: every auth row is
present and must remain blocked unless row-specific required evidence is present.

Pure/offline by default: no `Path.home()`, no browser/keychain reads,
no credential store reads, and no live NotebookLM execution.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
from collections import Counter
from datetime import datetime, timezone
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "auth_row_promotion_audit/1"
MANIFEST_SCHEMA_VERSION = "auth_row_evidence/1"
ROW_EVIDENCE_SCHEMA_VERSION = "auth_row_evidence_report/1"
ROW_EVIDENCE_PROOF_BUILDER = "auth_row_evidence_report_builder.py"
ROW_EVIDENCE_PROOF_SCHEMA_VERSION = "auth_row_proof_records/1"
TARGET = "notebooklm-py==0.7.2"
STRICT_BLOCKED_EXIT = 77
REQUIRED_AUTH_ROWS = 146

REPO_ROOT = Path(__file__).resolve().parents[1]

REQUIRED_MAPPING_FIELDS = (
    "row_id",
    "category",
    "status",
    "row_status",
    "comparator",
    "allowed_normalizations",
    "required_evidence",
    "missing_for_promotion",
    "promotion_allowed",
    "evidence_basis",
)

_REDACT_PATTERNS = (
    re.compile(r"(?<![:/\w])/(?:[A-Za-z0-9._-]+/)+[A-Za-z0-9._-]+"),
    re.compile(r"[A-Za-z]:\\"),
    re.compile(r"\b[a-zA-Z0-9_.%+-]+@[a-zA-Z0-9.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"\bnb-[A-Za-z0-9_-]{3,}\b"),
    re.compile(r"ya29\.[A-Za-z0-9_-]{20,}"),
    re.compile(r"1//[A-Za-z0-9_\-]{30,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(
        r"\b(?:SID|__Secure-[13]PSID|__Secure-[13]PAPISID|SAPISID|APISID|HSID|SSID|SIDCC|NID)="
        r"[A-Za-z0-9_./+\-]{12,}"
    ),
)
_SAFE_PLACEHOLDER_STRINGS = frozenset({"", "set", "<redacted>", "redacted"})
_SENSITIVE_FIELD_NAMES = frozenset(
    {
        "access_token",
        "auth_token",
        "cookie",
        "cookie_value",
        "cookies",
        "csrf_token",
        "email",
        "id_token",
        "notebook_id",
        "oauth_token",
        "refresh_token",
        "session_id",
        "storage_path",
        "storage_state",
        "token",
        "value",
    }
)
_ALLOWED_ROW_EVIDENCE_REPORT_KEYS = frozenset(
    {
        "schema_version",
        "proof_builder",
        "proof_schema_version",
        "target",
        "generated_at",
        "expires_at",
        "rows",
    }
)
_ALLOWED_ROW_EVIDENCE_ROW_KEYS = frozenset(
    {"row_id", "satisfied_required_evidence", "proofs"}
)
_ALLOWED_ROW_EVIDENCE_PROOF_KEYS = frozenset(
    {"token", "evidence_id", "evidence_type", "status", "redacted"}
)
_ROW_EVIDENCE_TOKEN_TYPES = {
    "live_differential_result": "live_differential",
    "session_credential_evidence": "session_credential",
}


def _repo_root(path: str | Path | None = None) -> Path:
    return Path(REPO_ROOT if path is None else path)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_module(script_name: str, module_name: str):
    path = _repo_root() / "scripts" / script_name
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load module: {script_name}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _validate_live_report(
    report_path: str | Path | None,
    *,
    script_name: str,
    module_name: str,
    repo_root: Path,
) -> dict[str, Any]:
    if report_path is None:
        return {
            "provided": False,
            "status": "not_provided",
            "strict_exit_code": None,
            "validated": False,
            "evidence_validated": False,
            "errors": [],
            "path": "missing",
        }

    path = Path(report_path)
    module = _load_module(script_name, module_name)
    try:
        report = module.build_report(
            argv=["--report", str(path)],
            repo_root=repo_root,
        )
    except Exception as exc:  # pragma: no cover - defensive boundary
        return {
            "provided": True,
            "status": "invalid",
            "strict_exit_code": STRICT_BLOCKED_EXIT,
            "validated": False,
            "evidence_validated": False,
            "errors": [str(exc)],
            "path": str(path),
        }

    status = str(report.get("status", "fail"))
    evidence_validated = bool(
        report.get("evidence_validated") is True and status == "pass"
    )
    return {
        "provided": True,
        "status": status,
        "strict_exit_code": report.get("strict_exit_code"),
        "validated": evidence_validated,
        "evidence_validated": evidence_validated,
        "errors": report.get("validation", {}).get("violations", []),
        "path": str(path),
    }


def _parse_iso_datetime(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None
    normalized = raw.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _find_row_evidence_redaction_hits(payload: dict[str, Any]) -> list[str]:
    text_hits = [
        rx.pattern
        for rx in _REDACT_PATTERNS
        if rx.search(json.dumps(payload, sort_keys=True))
    ]

    structural_hits: list[str] = []

    def _walk(value: Any, path: str = "$") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                key_lower = str(key).lower()
                if key_lower in _SENSITIVE_FIELD_NAMES:
                    if (
                        key_lower == "token"
                        and isinstance(child, str)
                        and child in _ROW_EVIDENCE_TOKEN_TYPES
                    ):
                        _walk(child, f"{path}.{key}")
                        continue
                    if isinstance(child, str):
                        if child not in _SAFE_PLACEHOLDER_STRINGS:
                            structural_hits.append(
                                f"{path}.{key} contains non-redacted sensitive value"
                            )
                    elif child not in (None, [], {}):
                        structural_hits.append(
                            f"{path}.{key} contains non-redacted sensitive object"
                        )
                _walk(child, f"{path}.{key}")
        elif isinstance(value, list):
            for idx, child in enumerate(value):
                _walk(child, f"{path}[{idx}]")

    _walk(payload)
    return text_hits + structural_hits


def _validate_auth_row_evidence_report(
    report_path: str | Path | None,
    *,
    expected_row_ids: set[str],
    expected_required_evidence: dict[str, set[str]],
) -> tuple[dict[str, set[str]], dict[str, Any]]:
    summary: dict[str, Any] = {
        "provided": False,
        "status": "not_provided",
        "validated": False,
        "strict_exit_code": STRICT_BLOCKED_EXIT,
        "errors": [],
        "path": "missing",
        "row_evidence_count": 0,
    }

    if report_path is None:
        return {}, summary

    path = Path(report_path)
    summary.update({"provided": True, "path": str(path)})
    try:
        report = _load_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        summary["errors"].append(f"cannot read auth row evidence report: {exc}")
        summary["status"] = "invalid"
        return {}, summary

    if not isinstance(report, dict):
        summary["status"] = "invalid"
        summary["errors"].append("auth row evidence report must be an object")
        return {}, summary

    unknown_report_keys = sorted(set(report) - _ALLOWED_ROW_EVIDENCE_REPORT_KEYS)
    if unknown_report_keys:
        summary["errors"].append(
            "auth row evidence report contains unknown top-level keys"
        )

    if report.get("schema_version") != ROW_EVIDENCE_SCHEMA_VERSION:
        summary["errors"].append(
            f"schema_version must be {ROW_EVIDENCE_SCHEMA_VERSION!r}"
        )
    if report.get("target") != TARGET:
        summary["errors"].append(f"target must be {TARGET!r}")

    expires_at = _parse_iso_datetime(report.get("expires_at"))
    generated_at = _parse_iso_datetime(report.get("generated_at"))
    now = datetime.now(timezone.utc)

    if generated_at is None:
        summary["errors"].append(
            "auth row evidence report missing or invalid generated_at"
        )
    elif generated_at > now:
        summary["errors"].append("auth row evidence report generated_at is future")
    if expires_at is None:
        summary["errors"].append(
            "auth row evidence report missing or invalid expires_at"
        )
    elif expires_at <= now:
        summary["errors"].append("auth row evidence report is stale")

    rows = report.get("rows")
    if not isinstance(rows, list):
        summary["status"] = "invalid"
        summary["errors"].append("auth row evidence report must include a rows list")
        return {}, summary

    if rows and (
        report.get("proof_builder") != ROW_EVIDENCE_PROOF_BUILDER
        or report.get("proof_schema_version") != ROW_EVIDENCE_PROOF_SCHEMA_VERSION
    ):
        summary["errors"].append(
            "auth row evidence report with rows must come from proof builder"
        )

    row_evidence: dict[str, set[str]] = {}
    seen_rows: list[str] = []
    evidence_rows_by_id: dict[str, set[str]] = {}
    for idx, item in enumerate(rows):
        if not isinstance(item, dict):
            summary["errors"].append(f"rows[{idx}] must be an object")
            continue

        unknown_row_keys = sorted(set(item) - _ALLOWED_ROW_EVIDENCE_ROW_KEYS)
        if unknown_row_keys:
            summary["errors"].append(f"rows[{idx}] contains unknown keys")
            continue

        row_id = str(item.get("row_id", ""))
        if not row_id:
            summary["errors"].append(f"rows[{idx}] missing required row_id")
            continue

        if row_id in seen_rows:
            summary["errors"].append(
                f"rows[{idx}] duplicate row_id in auth row evidence report"
            )
        seen_rows.append(row_id)

        if row_id not in expected_row_ids:
            summary["errors"].append(f"rows[{idx}] row_id not in parity_rows.json")

        satisfied_raw = item.get("satisfied_required_evidence")
        if satisfied_raw is None:
            satisfied_values: list[str] = []
        elif isinstance(satisfied_raw, list):
            if not all(isinstance(token, str) for token in satisfied_raw):
                summary["errors"].append(
                    f"rows[{idx}] satisfied_required_evidence items must be strings"
                )
                continue
            satisfied_values = satisfied_raw
        else:
            summary["errors"].append(
                f"rows[{idx}] satisfied_required_evidence must be a list"
            )
            continue

        allowed_tokens = expected_required_evidence.get(row_id, set())
        satisfied_set = set(satisfied_values)
        if len(satisfied_values) != len(satisfied_set):
            summary["errors"].append(
                f"rows[{idx}] duplicate satisfied_required_evidence tokens"
            )
            continue
        unknown_tokens = sorted(satisfied_set - allowed_tokens)
        if unknown_tokens:
            summary["errors"].append(
                f"rows[{idx}] contains unknown satisfied_required_evidence tokens"
            )
            continue

        missing_tokens = sorted(allowed_tokens - satisfied_set)
        if missing_tokens:
            summary["errors"].append(
                f"rows[{idx}] missing required satisfied_required_evidence tokens"
            )
            continue

        row_errors_before = len(summary["errors"])
        proofs = item.get("proofs")
        if not isinstance(proofs, list) or not proofs:
            summary["errors"].append(f"rows[{idx}] proofs must be a non-empty list")
            continue

        proof_tokens: set[str] = set()
        for proof_idx, proof in enumerate(proofs):
            if not isinstance(proof, dict):
                summary["errors"].append(
                    f"rows[{idx}].proofs[{proof_idx}] must be an object"
                )
                continue

            if set(proof) != _ALLOWED_ROW_EVIDENCE_PROOF_KEYS:
                summary["errors"].append(
                    f"rows[{idx}].proofs[{proof_idx}] contains unknown keys"
                )
                continue

            token = proof.get("token")
            if not isinstance(token, str) or token not in allowed_tokens:
                summary["errors"].append(
                    f"rows[{idx}].proofs[{proof_idx}] token is not allowed"
                )
                continue
            if token not in satisfied_set:
                summary["errors"].append(
                    f"rows[{idx}].proofs[{proof_idx}] token is not satisfied"
                )
                continue
            if token in proof_tokens:
                summary["errors"].append(
                    f"rows[{idx}].proofs[{proof_idx}] duplicate proof token"
                )
                continue
            proof_tokens.add(token)

            evidence_id = proof.get("evidence_id")
            if not isinstance(evidence_id, str) or not evidence_id:
                summary["errors"].append(
                    f"rows[{idx}].proofs[{proof_idx}] evidence_id must be set"
                )
            else:
                evidence_rows_by_id.setdefault(evidence_id, set()).add(row_id)
                if row_id not in evidence_id:
                    summary["errors"].append(
                        f"rows[{idx}].proofs[{proof_idx}] evidence_id must identify its auth row"
                    )

            if proof.get("status") != "pass":
                summary["errors"].append(
                    f"rows[{idx}].proofs[{proof_idx}] status must be pass"
                )
            if proof.get("redacted") is not True:
                summary["errors"].append(
                    f"rows[{idx}].proofs[{proof_idx}] must be redacted"
                )

            evidence_type = proof.get("evidence_type")
            if not isinstance(evidence_type, str) or evidence_type != (
                _ROW_EVIDENCE_TOKEN_TYPES.get(token) or ""
            ):
                summary["errors"].append(
                    f"rows[{idx}].proofs[{proof_idx}] evidence_type does not match token"
                )

        if allowed_tokens - proof_tokens:
            summary["errors"].append(f"rows[{idx}] missing required proof tokens")
            continue
        if len(summary["errors"]) != row_errors_before:
            continue

        row_evidence[row_id] = {value for value in satisfied_values if value}
        summary["row_evidence_count"] += 1

    if any(len(row_ids) > 1 for row_ids in evidence_rows_by_id.values()):
        summary["errors"].append(
            "proof evidence_id must be row-specific and not reused across auth rows"
        )

    if summary["errors"]:
        summary["status"] = "invalid"
        return {}, summary

    redaction_hits = _find_row_evidence_redaction_hits(report)
    if redaction_hits:
        summary["status"] = "invalid"
        summary["errors"].append(
            "auth row evidence report contains non-redacted values"
        )
        return {}, summary

    summary["status"] = "pass"
    summary["validated"] = True
    summary["strict_exit_code"] = 0
    if generated_at is not None:
        summary["validated_at"] = generated_at.isoformat()
    if expires_at is not None:
        summary["expires_at"] = expires_at.isoformat()
    return row_evidence, summary


def _validate_auth_matrix(matrix_path: Path, errors: list[str]) -> dict[str, int]:
    if not matrix_path.is_file():
        errors.append(f"missing auth matrix file: {matrix_path}")
        return {
            "auth_rows_total": 0,
            "parity_open": 0,
            "parity_pass": 0,
            "parity_blocked": 0,
        }

    try:
        matrix = _load_json(matrix_path)
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"cannot read auth matrix: {exc}")
        return {
            "auth_rows_total": 0,
            "parity_open": 0,
            "parity_pass": 0,
            "parity_blocked": 0,
        }

    rows = list(matrix.get("interactive_login_matrix", [])) + list(
        matrix.get("browser_cookie_import_matrix", [])
    )
    if not isinstance(rows, list):
        errors.append("auth matrix rows not a list")
        return {
            "auth_rows_total": 0,
            "parity_open": 0,
            "parity_pass": 0,
            "parity_blocked": 0,
        }

    summary = Counter(row.get("parity_state", "") for row in rows)
    auth_total = len(rows)
    counts = {
        "auth_rows_total": auth_total,
        "parity_open": summary.get("open", 0),
        "parity_pass": summary.get("pass", 0),
        "parity_blocked": summary.get("blocked", 0),
    }

    if auth_total != REQUIRED_AUTH_ROWS:
        errors.append(
            f"auth matrix auth rows is {auth_total}, expected {REQUIRED_AUTH_ROWS}"
        )
    unexpected_states = sorted(set(summary) - {"open", "pass", "blocked"})
    if unexpected_states:
        errors.append(
            f"auth matrix contains unexpected parity_state values: {unexpected_states}"
        )
    if counts["parity_blocked"]:
        errors.append("auth matrix contains blocked auth rows; expected open/pass only")
    return counts


def _required_evidence_set(tokens: Any) -> set[str]:
    if not isinstance(tokens, list):
        return set()
    return {str(item) for item in tokens}


def _validate_mapping(
    mapping: dict[str, Any],
    idx: int,
    row: dict[str, Any],
    row_id: str,
    errors: list[str],
    row_evidence: dict[str, set[str]],
) -> tuple[bool, bool]:
    start_error_count = len(errors)

    for field in REQUIRED_MAPPING_FIELDS:
        if field not in mapping:
            errors.append(f"mapping[{idx}] missing field {field!r}")

    if mapping.get("category") != "auth":
        errors.append(f"mapping[{idx}] row_id {row_id!r} must set category='auth'")

    if mapping.get("comparator") != row.get("comparator"):
        errors.append(
            f"mapping[{idx}] row_id {row_id!r} comparator mismatch "
            f"(got {mapping.get('comparator')!r}, expected {row.get('comparator')!r})"
        )

    allowed = mapping.get("allowed_normalizations")
    if not isinstance(allowed, list):
        errors.append(
            f"mapping[{idx}] row_id {row_id!r} allowed_normalizations must be a list"
        )
    elif allowed != row.get("allowed_normalizations", []):
        errors.append(
            f"mapping[{idx}] row_id {row_id!r} allowed_normalizations mismatch"
        )

    required_raw = mapping.get("required_evidence")
    if not isinstance(required_raw, list) or not all(
        isinstance(item, str) for item in required_raw
    ):
        required: set[str] = set()
    else:
        required = set(required_raw)
    row_required = _required_evidence_set(row.get("required_evidence"))
    if not required:
        errors.append(
            f"mapping[{idx}] row_id {row_id!r} required_evidence missing/invalid"
        )
    elif len(required_raw) != len(required):
        errors.append(
            f"mapping[{idx}] row_id {row_id!r} required_evidence contains duplicate tokens"
        )
    elif required != row_required:
        errors.append(f"mapping[{idx}] row_id {row_id!r} required_evidence mismatch")

    status = mapping.get("status")
    row_status = mapping.get("row_status")
    if status not in {"open", "pass"} or row_status not in {"open", "pass"}:
        errors.append(
            f"mapping[{idx}] row_id {row_id!r} status and row_status must be open/pass"
        )
        return False, False

    if status != row_status:
        errors.append(
            f"mapping[{idx}] row_id {row_id!r} status={status!r} row_status={row_status!r} mismatch"
        )

    missing_raw = mapping.get("missing_for_promotion")
    if not isinstance(missing_raw, list) or not all(
        isinstance(item, str) for item in missing_raw
    ):
        errors.append(
            f"mapping[{idx}] row_id {row_id!r} missing_for_promotion must be a list of strings"
        )
        missing_for_promotion: set[str] = set()
    else:
        missing_for_promotion = set(missing_raw)
        if len(missing_raw) != len(missing_for_promotion):
            errors.append(
                f"mapping[{idx}] row_id {row_id!r} missing_for_promotion contains duplicate tokens"
            )
    is_valid = len(errors) == start_error_count
    if status == "open" and row_status == "open":
        if mapping.get("promotion_allowed") is not False:
            errors.append(
                f"mapping[{idx}] row_id {row_id!r} open mapping must set promotion_allowed=false"
            )
        if missing_for_promotion != row_required:
            errors.append(
                f"mapping[{idx}] row_id {row_id!r} open mapping must set missing_for_promotion to required evidence tokens"
            )
        if row.get("status") != "open":
            errors.append(
                f"mapping[{idx}] row_id {row_id!r} row status is {row.get('status')!r}, expected open"
            )
        is_valid = len(errors) == start_error_count
        return False, is_valid

    if row.get("status") != "pass":
        errors.append(
            f"mapping[{idx}] row_id {row_id!r} pass mapping requires parity row status 'pass'"
        )

    if mapping.get("promotion_allowed") is not True:
        errors.append(
            f"mapping[{idx}] row_id {row_id!r} pass mapping must set promotion_allowed=true"
        )

    if missing_for_promotion:
        errors.append(
            f"mapping[{idx}] row_id {row_id!r} pass mapping must set missing_for_promotion=[]"
        )

    satisfied_raw = mapping.get("satisfied_required_evidence")
    if satisfied_raw is None:
        satisfied = set()
    elif not isinstance(satisfied_raw, list) or not all(
        isinstance(item, str) for item in satisfied_raw
    ):
        errors.append(
            f"mapping[{idx}] row_id {row_id!r} satisfied_required_evidence must be a list of strings"
        )
        satisfied = set()
    else:
        satisfied = set(satisfied_raw)
        if len(satisfied_raw) != len(satisfied):
            errors.append(
                f"mapping[{idx}] row_id {row_id!r} satisfied_required_evidence contains duplicate tokens"
            )
    satisfied |= row_evidence.get(row_id, set())
    if row_required and row_required - satisfied:
        errors.append(
            f"mapping[{idx}] row_id {row_id!r} missing required row evidence tokens "
            f"{sorted(row_required - satisfied)}"
        )

    is_valid = len(errors) == start_error_count
    if is_valid:
        return True, False
    return False, True


def _validate_manifest_against_rows(
    *,
    manifest: dict[str, Any],
    parity_rows: list[dict[str, Any]],
    row_evidence: dict[str, set[str]],
    errors: list[str],
) -> tuple[int, int, int]:
    if not isinstance(manifest, dict):
        errors.append("manifest must be an object")
        return 0, 0, 0

    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        errors.append(f"schema_version must be {MANIFEST_SCHEMA_VERSION!r}")
    if manifest.get("target") != TARGET:
        errors.append(f"target must be {TARGET!r}")
    if manifest.get("exact_one_to_one_claim_ready") is not False:
        errors.append("exact_one_to_one_claim_ready must be false")
    category_promotion = manifest.get("category_promotion")
    if not (
        isinstance(category_promotion, dict)
        and set(category_promotion) == {"auth"}
        and isinstance(category_promotion.get("auth"), bool)
    ):
        errors.append("category_promotion must be exactly {'auth': <boolean>}")
        manifest_auth_promotion = False
    else:
        manifest_auth_promotion = category_promotion["auth"]

    mapping_count = manifest.get("mapping_count")
    if mapping_count != REQUIRED_AUTH_ROWS:
        errors.append(
            f"mapping_count is {mapping_count!r}, expected {REQUIRED_AUTH_ROWS}"
        )

    mappings = manifest.get("auth_mappings")
    if not isinstance(mappings, list):
        errors.append("auth_mappings must be a list")
        return 0, 0, 0

    if len(mappings) != REQUIRED_AUTH_ROWS:
        errors.append(
            f"auth_mappings length is {len(mappings)}, expected {REQUIRED_AUTH_ROWS}"
        )

    rows_by_id = {str(row.get("id", "")): row for row in parity_rows}
    expected_ids = sorted(rows_by_id)

    seen_ids: list[str] = []
    mapped_ids: set[str] = set()
    promotable = 0
    blocked = 0

    for idx, mapping in enumerate(mappings):
        if not isinstance(mapping, dict):
            errors.append(f"mapping[{idx}] must be an object")
            continue

        row_id = str(mapping.get("row_id", ""))
        if not row_id:
            errors.append(f"mapping[{idx}] row_id is missing")
            continue

        seen_ids.append(row_id)
        mapped_ids.add(row_id)
        row = rows_by_id.get(row_id)
        if row is None:
            errors.append(f"mapping[{idx}] row_id {row_id!r} not in parity_rows.json")
            continue
        if row.get("category") != "auth":
            errors.append(
                f"mapping[{idx}] row_id {row_id!r} parity row category is {row.get('category')!r}"
            )
            continue

        is_promoted, is_blocked = _validate_mapping(
            mapping,
            idx,
            row,
            row_id,
            errors,
            row_evidence,
        )
        if is_promoted:
            promotable += 1
        elif is_blocked:
            blocked += 1

    duplicates = sorted(
        {row_id for row_id, count in Counter(seen_ids).items() if count > 1}
    )
    missing_ids = [row_id for row_id in expected_ids if row_id not in mapped_ids]
    extra_ids = [row_id for row_id in sorted(mapped_ids) if row_id not in expected_ids]

    if duplicates:
        errors.append(f"duplicate auth mappings: {duplicates}")
    if missing_ids:
        errors.append(f"missing auth mappings: {missing_ids[:5]}")
    if extra_ids:
        errors.append(f"extra auth mappings: {extra_ids[:5]}")

    if len(expected_ids) != REQUIRED_AUTH_ROWS:
        errors.append(
            f"parity_rows auth rows is {len(expected_ids)}, expected {REQUIRED_AUTH_ROWS}"
        )

    if manifest_auth_promotion is True:
        errors.append(
            "category_promotion.auth must remain false while explicit profile exclusions exist"
        )

    unknown_rows = sorted(
        row_id for row_id in row_evidence.keys() if row_id not in rows_by_id
    )
    if unknown_rows:
        errors.append(f"row evidence contains unknown row_id entries: {unknown_rows}")

    return mapped_ids.__len__(), promotable, blocked


def build_report(
    *,
    repo_root: str | Path | None = None,
    manifest_path: str | Path | None = None,
    parity_rows_path: str | Path | None = None,
    auth_matrix_path: str | Path | None = None,
    live_auth_report: str | Path | None = None,
    live_mutation_report: str | Path | None = None,
    row_evidence_report: str | Path | None = None,
) -> dict[str, Any]:
    root = _repo_root(repo_root)
    manifest_file = Path(
        manifest_path
        if manifest_path is not None
        else root / "compat" / "auth_row_evidence.json"
    )
    rows_file = Path(
        parity_rows_path
        if parity_rows_path is not None
        else root / "compat" / "parity_rows.json"
    )
    matrix_file = Path(
        auth_matrix_path
        if auth_matrix_path is not None
        else root / "compat" / "auth_matrix.json"
    )

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "target": TARGET,
        "status": "pass",
        "strict_ok": True,
        "strict_exit_code": 0,
        "manifest_present": manifest_file.is_file(),
        "manifest_path": "set" if manifest_file.is_file() else "missing",
        "mapping_count": 0,
        "auth_rows_expected": 0,
        "auth_rows_mapped": 0,
        "auth_rows_promotable": 0,
        "auth_rows_blocked": 0,
        "auth_rows_matrix_summary": {},
        "category_promotion": {"auth": False},
        "exact_one_to_one_claim_ready": False,
        "live_reports": {},
        "auth_row_evidence": {},
        "errors": [],
        "warnings": [],
    }
    errors = report["errors"]

    if not rows_file.is_file():
        errors.append(f"missing compat file: {rows_file}")
        report["status"] = "fail"
        report["strict_ok"] = False
        report["strict_exit_code"] = 1
        return report

    if not manifest_file.is_file():
        errors.append(f"manifest missing: {manifest_file}")
        report["status"] = "fail"
        report["strict_ok"] = False
        report["strict_exit_code"] = 1
        return report

    try:
        manifest = _load_json(manifest_file)
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"cannot read manifest: {exc}")
        report["status"] = "fail"
        report["strict_ok"] = False
        report["strict_exit_code"] = 1
        return report
    if isinstance(manifest, dict) and _find_row_evidence_redaction_hits(manifest):
        errors.append("auth row evidence manifest contains non-redacted values")

    try:
        rows_data = _load_json(rows_file)
        rows = rows_data.get("rows", [])
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"cannot read parity_rows.json: {exc}")
        rows = []

    auth_rows = [
        row for row in rows if isinstance(row, dict) and row.get("category") == "auth"
    ]
    report["mapping_count"] = len(manifest.get("auth_mappings", []))
    report["auth_rows_expected"] = len(auth_rows)
    auth_row_ids = {
        str(row.get("id", "")) for row in auth_rows if isinstance(row, dict)
    }
    auth_row_required_evidence = {
        str(row.get("id", "")): _required_evidence_set(row.get("required_evidence"))
        for row in auth_rows
        if isinstance(row, dict)
    }

    report["auth_row_evidence"] = {}
    auth_row_evidence, report_evidence = _validate_auth_row_evidence_report(
        row_evidence_report,
        expected_row_ids=auth_row_ids,
        expected_required_evidence=auth_row_required_evidence,
    )
    report["auth_row_evidence"] = report_evidence
    if not report_evidence["validated"] and report_evidence["provided"]:
        errors.extend(report_evidence["errors"])

    report["auth_rows_matrix_summary"] = _validate_auth_matrix(matrix_file, errors)

    report["live_reports"] = {
        "live_auth_report": _validate_live_report(
            live_auth_report,
            script_name="live_auth_evidence_audit.py",
            module_name="_phase25_live_auth_evidence_audit",
            repo_root=root,
        ),
        "live_mutation_report": _validate_live_report(
            live_mutation_report,
            script_name="live_mutation_evidence_audit.py",
            module_name="_phase25_live_mutation_evidence_audit",
            repo_root=root,
        ),
        "category_level_evidence": {
            "live_auth_report_validated": False,
            "live_mutation_report_validated": False,
        },
    }

    if report["live_reports"]["live_auth_report"]["validated"]:
        report["live_reports"]["category_level_evidence"][
            "live_auth_report_validated"
        ] = True
    if report["live_reports"]["live_mutation_report"]["validated"]:
        report["live_reports"]["category_level_evidence"][
            "live_mutation_report_validated"
        ] = True

    if any(
        item.get("provided") and item.get("status") not in {"not_provided", "pass"}
        for item in [
            report["live_reports"]["live_auth_report"],
            report["live_reports"]["live_mutation_report"],
        ]
    ):
        errors.append("one or more provided live reports is invalid")

    if not isinstance(rows, list):
        errors.append("compat/parity_rows.json must include a rows list")
        rows_list: list[dict[str, Any]] = []
    else:
        rows_list = [row for row in rows if isinstance(row, dict)]

    mapped, promotable, blocked = _validate_manifest_against_rows(
        manifest=manifest,
        parity_rows=[row for row in rows_list if row.get("category") == "auth"],
        row_evidence=auth_row_evidence,
        errors=errors,
    )
    report["auth_rows_mapped"] = mapped
    report["auth_rows_promotable"] = promotable
    report["auth_rows_blocked"] = blocked

    if report["auth_rows_expected"] != REQUIRED_AUTH_ROWS:
        errors.append(
            f"auth rows in parity_rows.json is {report['auth_rows_expected']}, expected {REQUIRED_AUTH_ROWS}"
        )

    category_promotion = manifest.get("category_promotion")
    report["category_promotion"]["auth"] = (
        category_promotion.get("auth", False)
        if isinstance(category_promotion, dict)
        else False
    )

    if (
        report["auth_rows_promotable"] + report["auth_rows_blocked"]
        != report["auth_rows_expected"]
    ):
        errors.append(
            "auth_rows_promotable + auth_rows_blocked must equal auth_rows_expected"
        )

    if report["exact_one_to_one_claim_ready"] is not False:
        errors.append("exact_one_to_one_claim_ready must be false")

    if errors:
        report["status"] = "fail"
        report["strict_ok"] = False
        report["strict_exit_code"] = STRICT_BLOCKED_EXIT

    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", dest="json_out", action="store_true")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--manifest")
    parser.add_argument("--parity-rows")
    parser.add_argument("--auth-matrix")
    parser.add_argument("--auth-row-evidence-report")
    parser.add_argument("--live-auth-report")
    parser.add_argument("--live-mutation-report")
    return parser


def _human_text(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"ZeroNotebookLM auth-row promotion audit: {report['status']}",
            f"auth mappings: {report['auth_rows_mapped']}/{report['mapping_count']}",
            f"auth rows promotable: {report['auth_rows_promotable']}",
            f"auth rows blocked: {report['auth_rows_blocked']}",
            f"category promotion: auth={report['category_promotion']['auth']}",
            f"exact_one_to_one_claim_ready: {str(report['exact_one_to_one_claim_ready']).lower()}",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    actual_argv = list(sys.argv[1:] if argv is None else argv)
    args = _parser().parse_args(actual_argv)
    report = build_report(
        manifest_path=args.manifest,
        parity_rows_path=args.parity_rows,
        auth_matrix_path=args.auth_matrix,
        row_evidence_report=args.auth_row_evidence_report,
        live_auth_report=args.live_auth_report,
        live_mutation_report=args.live_mutation_report,
    )

    if args.json_out:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(_human_text(report))

    return int(report["strict_exit_code"]) if args.strict else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
