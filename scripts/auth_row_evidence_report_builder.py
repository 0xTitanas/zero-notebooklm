#!/usr/bin/env python3
"""Phase 27 auth-row evidence report builder.

Converts explicit proof-record artifacts into the existing Phase 26 row evidence
schema consumed by auth-row promotion/audit phases. The builder is strict and
offline by design:

- No discovery of browser stores, credential paths, keychains, network calls, or
  home directory inspection.
- Input is an explicit JSON proof-record file.
- Every validation failure is value-free and redaction-oriented.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any

SCHEMA_VERSION = "auth_row_proof_records/1"
TARGET = "notebooklm-py==0.7.2"
OUTPUT_SCHEMA_VERSION = "auth_row_evidence_report/1"
PROOF_BUILDER = "auth_row_evidence_report_builder.py"
STRICT_BLOCKED_EXIT = 77

REPO_ROOT = Path(__file__).resolve().parents[1]

_ALLOWED_TOP_LEVEL_KEYS = frozenset(
    {"schema_version", "target", "generated_at", "expires_at", "rows"}
)
_ALLOWED_ROW_KEYS = frozenset({"row_id", "proofs"})
_ALLOWED_PROOF_KEYS = frozenset(
    {"token", "evidence_id", "evidence_type", "status", "redacted"}
)
_ALLOWED_EVIDENCE_TYPES = frozenset({"live_differential", "session_credential"})

_REDACTION_PATTERNS = (
    re.compile(r"/[U]sers/"),
    re.compile(r"/[h]ome/"),
    re.compile(r"[A-Za-z]:\\"),
    re.compile(r"\b/[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)+\b"),
    re.compile(r"\b[a-zA-Z0-9_.%+-]+@[a-zA-Z0-9.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"\bnb-[A-Za-z0-9_-]{3,}\b"),
    re.compile(r"ya29\.[A-Za-z0-9_-]{20,}"),
    re.compile(r"1//[A-Za-z0-9_\-]{30,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"\b(?:SID|__Secure-[A-Za-z0-9_-]+)=[A-Za-z0-9_./+\-]{12,}\b"),
    re.compile(
        r"\b(?:__Secure-[13]PSID|__Secure-[13]PAPISID|SAPISID|APISID|HSID|SSID|SIDCC|NID)="
        r"[A-Za-z0-9_./+\-]{12,}"
    ),
    re.compile(r"\b[A-Z][A-Z0-9_]{1,40}=[A-Za-z0-9_./+\-]{8,}\b"),
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
)


class _ValidationError(ValueError):
    def __init__(self, violations: list[str]):
        self.violations = violations
        super().__init__("proof input validation failed")


def _repo_root(path: str | Path | None = None) -> Path:
    return Path(REPO_ROOT if path is None else path)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _find_row_evidence_requirements(
    root: Path,
) -> tuple[dict[str, set[str]], dict[str, list[str]]]:
    path = root / "compat" / "parity_rows.json"
    data = _load_json(path)
    rows = data.get("rows", []) if isinstance(data, dict) else []

    sets: dict[str, set[str]] = {}
    ordered: dict[str, list[str]] = {}
    if not isinstance(rows, list):
        return sets, ordered

    for row in rows:
        if not isinstance(row, dict):
            continue
        row_id = str(row.get("id", ""))
        if not row_id:
            continue
        if str(row.get("category", "")) != "auth":
            continue

        required_raw = row.get("required_evidence", [])
        required_tokens: list[str] = []
        if isinstance(required_raw, list):
            required_tokens = [
                str(item) for item in required_raw if isinstance(item, str)
            ]

        sets[row_id] = set(required_tokens)
        ordered[row_id] = required_tokens

    return sets, ordered


def _parse_iso_datetime(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None
    normalized = raw.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _find_redaction_hits(payload: dict[str, Any], *, raw: str) -> list[str]:
    hits: list[str] = []
    hits.extend(
        pattern.pattern for pattern in _REDACTION_PATTERNS if pattern.search(raw)
    )
    return hits


def _required_evidence_token_type(token: str) -> str:
    if token == "session_credential_evidence":
        return "session_credential"
    if token == "live_differential_result":
        return "live_differential"
    return ""


def _validate_input(
    payload: dict[str, Any], row_required: dict[str, set[str]]
) -> dict[str, Any]:
    violations: list[str] = []

    if set(payload) != _ALLOWED_TOP_LEVEL_KEYS:
        violations.append("proof input has unexpected top-level schema")

    if payload.get("schema_version") != SCHEMA_VERSION:
        violations.append("proof schema_version must match expected value")

    if payload.get("target") != TARGET:
        violations.append("proof target must match expected value")

    generated_at_raw = payload.get("generated_at")
    expires_at_raw = payload.get("expires_at")
    generated_at = _parse_iso_datetime(generated_at_raw)
    expires_at = _parse_iso_datetime(expires_at_raw)
    now = datetime.now(timezone.utc)
    if generated_at is None:
        violations.append("generated_at must be an ISO-8601 timestamp")
    elif generated_at > now:
        violations.append("generated_at cannot be in the future")
    if expires_at is None:
        violations.append("expires_at must be an ISO-8601 timestamp")
    elif expires_at <= now:
        violations.append("expires_at must be in the future")

    raw = json.dumps(payload, sort_keys=True)
    if _find_redaction_hits(payload, raw=raw):
        violations.append("proof payload contains non-redacted values")

    rows = payload.get("rows")
    if not isinstance(rows, list):
        violations.append("rows must be a list")
        if violations:
            raise _ValidationError(violations)
        raise _ValidationError(violations)

    output_rows: list[dict[str, Any]] = []
    seen_rows: set[str] = set()
    evidence_rows_by_id: dict[str, set[str]] = {}

    for row in rows:
        if not isinstance(row, dict):
            violations.append("rows entries must be objects")
            continue

        if set(row) != _ALLOWED_ROW_KEYS:
            violations.append("rows entries contain unexpected keys")
            continue

        raw_row_id = row.get("row_id")
        if not isinstance(raw_row_id, str) or raw_row_id != raw_row_id.strip():
            violations.append("rows entries require an exact string row_id")
            continue
        row_id = raw_row_id
        if not row_id:
            violations.append("rows entries require a row_id")
            continue

        required_tokens = row_required.get(row_id)
        if required_tokens is None:
            violations.append("one or more rows are not valid auth rows")
            continue

        if row_id in seen_rows:
            violations.append("rows contains duplicate row_id")
            continue
        seen_rows.add(row_id)

        proofs = row.get("proofs")
        if not isinstance(proofs, list):
            violations.append("proofs must be a list")
            continue

        if not proofs:
            violations.append(
                "proofs must contain proof entries for all required tokens"
            )
            continue

        provided_tokens: set[str] = set()
        proof_summaries: list[dict[str, Any]] = []
        for proof in proofs:
            if not isinstance(proof, dict):
                violations.append("proof entries must be objects")
                continue

            if set(proof) != _ALLOWED_PROOF_KEYS:
                violations.append("proof entries contain unexpected keys")
                continue

            token = proof.get("token")
            if not isinstance(token, str):
                violations.append("proof token must be a string")
                continue
            if token not in required_tokens:
                violations.append("proof token is not allowed for this auth row")
                continue
            if token in provided_tokens:
                violations.append("proof token must not be duplicated")
                continue

            status = proof.get("status")
            if status != "pass":
                violations.append("proof status must be pass")

            evidence_id = proof.get("evidence_id")
            if not isinstance(evidence_id, str) or not evidence_id:
                violations.append("proof evidence_id must be a non-empty string")
            else:
                evidence_rows_by_id.setdefault(evidence_id, set()).add(row_id)
                if row_id not in evidence_id:
                    violations.append("proof evidence_id must identify its auth row")

            redacted = proof.get("redacted")
            if redacted is not True:
                violations.append("all required proofs must be redacted")

            evidence_type = proof.get("evidence_type")
            if not isinstance(evidence_type, str) or not evidence_type:
                violations.append("proof evidence_type must be a non-empty string")
            elif evidence_type not in _ALLOWED_EVIDENCE_TYPES:
                violations.append("proof evidence_type is not allowed")
            elif (
                _required_evidence_token_type(token)
                and _required_evidence_token_type(token) != evidence_type
            ):
                violations.append(
                    "proof evidence_type does not match required evidence token"
                )

            provided_tokens.add(token)
            proof_summaries.append(
                {
                    "token": token,
                    "evidence_id": evidence_id,
                    "evidence_type": evidence_type,
                    "status": status,
                    "redacted": redacted,
                }
            )

        if required_tokens - provided_tokens:
            violations.append(
                "rows missing required satisfied_required_evidence tokens"
            )
            continue

        output_rows.append(
            {
                "row_id": row_id,
                "satisfied_required_evidence": sorted(required_tokens),
                "proofs": sorted(proof_summaries, key=lambda item: item["token"]),
            }
        )

    if any(len(row_ids) > 1 for row_ids in evidence_rows_by_id.values()):
        violations.append(
            "proof evidence_id must be row-specific and not reused across auth rows"
        )

    if violations:
        raise _ValidationError(violations)

    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "proof_builder": PROOF_BUILDER,
        "proof_schema_version": SCHEMA_VERSION,
        "target": payload["target"],
        "generated_at": payload["generated_at"],
        "expires_at": payload["expires_at"],
        "rows": output_rows,
    }


def build_report(
    proofs: str | Path,
    *,
    repo_root: str | Path | None = None,
    output: str | Path | None = None,
) -> dict[str, Any]:
    root = _repo_root(repo_root)
    proof_required = _find_row_evidence_requirements(root)[0]
    if not proof_required:
        raise _ValidationError(["auth rows missing from compat/parity_rows.json"])

    proof_path = Path(proofs)
    try:
        payload = _load_json(proof_path)
    except (OSError, json.JSONDecodeError):
        raise _ValidationError(["proof file is not readable JSON"])

    if not isinstance(payload, dict):
        raise _ValidationError(["proof payload must be an object"])

    report = _validate_input(payload, proof_required)
    if output is not None:
        Path(output).write_text(
            json.dumps(report, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    return report


def _build_invalid_report(errors: list[str]) -> dict[str, Any]:
    return {
        "status": "fail",
        "errors": sorted(set(errors)),
    }


def _human_text(report: dict[str, Any]) -> str:
    return "\n".join(
        ["auth row evidence report builder: " + report.get("status", "ok")]
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proofs", required=True)
    parser.add_argument("--output")
    parser.add_argument("--json", dest="json_out", action="store_true")
    parser.add_argument("--strict", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    actual_argv = list(sys.argv[1:] if argv is None else argv)
    args = _parser().parse_args(actual_argv)

    try:
        report = build_report(args.proofs, output=args.output, repo_root=REPO_ROOT)
    except _ValidationError as exc:
        invalid = _build_invalid_report(exc.violations)
        if args.json_out:
            print(json.dumps(invalid, indent=2, sort_keys=True))
        else:
            _ = _human_text(invalid)
            for item in invalid["errors"]:
                print(item)
        return STRICT_BLOCKED_EXIT if args.strict else 0

    if args.output:
        return 0

    if args.json_out:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            "auth row evidence report built: "
            + str(len(report["rows"]))
            + " rows satisfied"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
