#!/usr/bin/env python3
"""Phase 24 live mutation/export evidence artifact audit.

This gate validates a redacted live mutation/export differential artifact report
for safe local-only review. It is repo-local/offline by default, does not touch
home, browser stores, keychains, credentials, network services, or live
NotebookLM.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from scripts.live_mutation_export_differential import (  # noqa: E402
    MUTATION_EXPORT_OPERATIONS as _LIVE_MUTATION_OPERATIONS,
    SCHEMA_VERSION as _LIVE_MUTATION_SCHEMA,
)

SCHEMA_VERSION = "live_mutation_evidence_audit/1"
TARGET = "notebooklm-py==0.7.2"

STRICT_BLOCKED_EXIT = 77
STRICT_ERROR_EXIT = 64

_OPEN_CATEGORIES = ("cli", "api", "auth", "rpc")
_ALL_CATEGORIES = ("cli", "api", "auth", "rpc", "offline", "self-test")
_DENYWORDS = frozenset(
    {
        "public",
        "share",
        "share_add",
        "share_remove",
        "share_update",
    }
)
_COOKIE_NAMES = frozenset(
    {
        "SID",
        "__Secure-1PSID",
        "__Secure-3PSID",
        "__Secure-1PAPISID",
        "__Secure-3PAPISID",
        "SAPISID",
        "APISID",
        "HSID",
        "SSID",
        "SIDCC",
        "NID",
    }
)
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
_SAFE_PLACEHOLDER_STRINGS = frozenset({"", "set", "<redacted>", "redacted"})
_ALLOWED_LIVE_REPORT_KEYS = frozenset(
    {
        "schema_version",
        "target",
        "status",
        "strict_exit_code",
        "live_enabled",
        "read_only",
        "mutation_allowed",
        "public_sharing_allowed",
        "disposable_notebook_only",
        "storage_state",
        "notebook_id",
        "operation_allowlist",
        "storage_preserved",
        "cleanup_confirmed",
        "public_sharing_touched",
        "shape_match",
        "blockers",
        "upstream_probe",
        "bare_probe",
        "observations",
        "category_promotion",
        "category_states",
    }
)

_REDACT_PATTERNS = (
    re.compile(r"/[U]sers/"),
    re.compile(r"/[h]ome/"),
    re.compile(r"[A-Za-z]:\\"),
    re.compile(r"\b/[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)+\b"),
    re.compile(r"\b[A-Za-z0-9_.%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"\bnb-[A-Za-z0-9_-]{3,}\b"),
    re.compile(r"ya29\.[A-Za-z0-9_-]{20,}"),
    re.compile(r"\b1//[A-Za-z0-9_\-]{30,}"),
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    re.compile(
        r"\b(?:__Secure-[13]PSID|__Secure-[13]PAPISID|SAPISID|APISID|HSID|SSID|SIDCC|NID)="
        r"[A-Za-z0-9_./+\-]{12,}"
    ),
    re.compile(r"\b(?:SID|__Secure-[A-Za-z0-9_-]+)=[A-Za-z0-9_./+\-]{12,}"),
    re.compile(r"\b[A-Z][A-Z0-9_]{1,40}=[A-Za-z0-9_./+\-]{8,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report",
        metavar="PATH",
        help="path to live_mutation_export_differential JSON report",
    )
    parser.add_argument("--json", action="store_true", dest="json_out")
    parser.add_argument("--strict", action="store_true")
    return parser


def _table_cells(line: str) -> list[str] | None:
    if not line.startswith("|") or "| ---" in line:
        return None
    cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
    return cells if cells else None


def _parse_category_states(repo_root: Path) -> dict[str, str]:
    matrix_path = repo_root / "compat" / "parity_matrix.md"
    states: dict[str, str] = {}
    for line in matrix_path.read_text(encoding="utf-8").splitlines():
        cells = _table_cells(line)
        if not cells:
            continue
        if len(cells) == 4 and cells[0] in _ALL_CATEGORIES:
            states[cells[0]] = cells[3]
    return {cat: states.get(cat, "open") for cat in _ALL_CATEGORIES}


def _find_regex_redaction_hits(report_text: str) -> list[str]:
    return [rx.pattern for rx in _REDACT_PATTERNS if rx.search(report_text)]


def _find_structural_redaction_hits(value: Any, *, path: str = "$") -> list[str]:
    """Find structured sensitive fields that regex scans can miss."""
    hits: list[str] = []
    if isinstance(value, dict):
        cookie_name = value.get("name")
        cookie_value = value.get("value")
        if isinstance(cookie_name, str) and cookie_name in _COOKIE_NAMES:
            if not (
                isinstance(cookie_value, str)
                and cookie_value in _SAFE_PLACEHOLDER_STRINGS
            ):
                hits.append("structured_cookie_value")

        for key, child in value.items():
            key_text = str(key)
            key_lower = key_text.lower()
            child_path = f"{path}.{key_text}"
            if key_lower in _SENSITIVE_FIELD_NAMES:
                if isinstance(child, str):
                    if child not in _SAFE_PLACEHOLDER_STRINGS:
                        hits.append("structured_sensitive_field")
                elif key_lower != "value":
                    hits.append("structured_sensitive_field")
            hits.extend(_find_structural_redaction_hits(child, path=child_path))

    elif isinstance(value, list):
        for idx, child in enumerate(value):
            hits.extend(_find_structural_redaction_hits(child, path=f"{path}[{idx}]"))

    return hits


def _find_redaction_hits(payload: dict[str, Any]) -> list[str]:
    text_hits = _find_regex_redaction_hits(json.dumps(payload, sort_keys=True))
    structural_hits = _find_structural_redaction_hits(payload)
    return text_hits + structural_hits


def _parse_json_no_duplicate_keys(
    raw: str,
) -> tuple[dict[str, Any] | None, bool, bool]:
    duplicate_seen = False

    def _object_pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        nonlocal duplicate_seen
        seen: set[str] = set()
        obj: dict[str, Any] = {}
        for key, value in pairs:
            if key in seen:
                duplicate_seen = True
            seen.add(key)
            obj[key] = value
        return obj

    try:
        parsed = json.loads(raw, object_pairs_hook=_object_pairs_hook)
    except json.JSONDecodeError:
        return None, False, True

    if not isinstance(parsed, dict):
        return None, duplicate_seen, False
    return parsed, duplicate_seen, False


def _validate_shape_tree(value: Any, *, path: str = "$") -> list[str]:
    """Validate shape trees produced by live_mutation_export_differential."""
    violations: list[str] = []
    if isinstance(value, list):
        for idx, child in enumerate(value):
            violations.extend(_validate_shape_tree(child, path=f"{path}[{idx}]"))
        return violations

    if not isinstance(value, dict):
        return [f"{path}: observation shape contains raw {type(value).__name__}"]

    typ = value.get("type")
    if not isinstance(typ, str):
        return ["unknown observation shape type"]
    if typ == "str":
        if set(value) != {"type", "length", "empty"}:
            violations.append(f"{path}: string shape has unexpected keys")
        if not isinstance(value.get("length"), int):
            violations.append(f"{path}: string shape length must be int")
        if not isinstance(value.get("empty"), bool):
            violations.append(f"{path}: string shape empty must be bool")
        return violations
    if typ in {"bool", "null", "int", "float"}:
        if set(value) != {"type"}:
            violations.append(f"{path}: scalar shape has unexpected keys")
        return violations
    if typ == "dict":
        if set(value) != {"type", "size", "entries"}:
            violations.append(f"{path}: dict shape has unexpected keys")
        if not isinstance(value.get("size"), int):
            violations.append(f"{path}: dict shape size must be int")
        entries = value.get("entries")
        if not isinstance(entries, list):
            violations.append(f"{path}: dict shape entries must be list")
            return violations
        for idx, entry in enumerate(entries):
            entry_path = f"{path}.entries[{idx}]"
            if not isinstance(entry, dict) or set(entry) != {"key", "value"}:
                violations.append(f"{entry_path}: entry must contain key/value shapes")
                continue
            violations.extend(
                _validate_shape_tree(entry["key"], path=f"{entry_path}.key")
            )
            violations.extend(
                _validate_shape_tree(entry["value"], path=f"{entry_path}.value")
            )
        return violations
    return ["unknown observation shape type"]


def _validate_live_payload(
    payload: dict[str, Any],
    category_states: dict[str, str],
) -> list[str]:
    violations: list[str] = []

    unknown_keys = sorted(set(payload) - _ALLOWED_LIVE_REPORT_KEYS)
    if unknown_keys:
        violations.append("unknown top-level report keys: " + ",".join(unknown_keys))
    missing_keys = sorted(_ALLOWED_LIVE_REPORT_KEYS - set(payload))
    if missing_keys:
        violations.append("missing top-level report keys: " + ",".join(missing_keys))

    if payload.get("schema_version") != _LIVE_MUTATION_SCHEMA:
        violations.append("schema_version mismatch")
    if payload.get("target") != TARGET:
        violations.append("target mismatch")

    if payload.get("status") != "pass":
        violations.append("status is not pass")
    if payload.get("strict_exit_code") != 0:
        violations.append("strict_exit_code is not 0")
    if payload.get("live_enabled") is not True:
        violations.append("live_enabled must be true")
    if payload.get("read_only") is not False:
        violations.append("read_only must be false")
    if payload.get("mutation_allowed") is not True:
        violations.append("mutation_allowed must be true")
    if payload.get("public_sharing_allowed") is not False:
        violations.append("public_sharing_allowed must be false")
    if payload.get("disposable_notebook_only") is not True:
        violations.append("disposable_notebook_only must be true")

    if payload.get("storage_state") != "set":
        violations.append("storage_state is not set")
    if payload.get("notebook_id") != "set":
        violations.append("notebook_id is not set")
    if payload.get("shape_match") is not True:
        violations.append("shape_match is not true")
    if payload.get("storage_preserved") is not True:
        violations.append("storage_preserved is not true")

    operation_allowlist = payload.get("operation_allowlist")
    if operation_allowlist != list(_LIVE_MUTATION_OPERATIONS):
        violations.append("operation_allowlist does not match Phase 24 allowlist")
    if isinstance(operation_allowlist, list):
        for operation in operation_allowlist:
            op_lower = str(operation).lower()
            for deny in _DENYWORDS:
                if deny in op_lower:
                    violations.append(
                        "operation_allowlist contains forbidden mutation/public term"
                    )
                    break

    blockers = payload.get("blockers")
    if "blockers" not in payload or blockers != []:
        violations.append("blockers must be present as empty list")

    upstream_probe = payload.get("upstream_probe")
    if not isinstance(upstream_probe, dict):
        violations.append("upstream_probe must be an object")
        upstream_probe = {}
    bare_probe = payload.get("bare_probe")
    if not isinstance(bare_probe, dict):
        violations.append("bare_probe must be an object")
        bare_probe = {}
    if upstream_probe.get("ok") is not True:
        violations.append("upstream_probe.ok must be true")
    if bare_probe.get("ok") is not True:
        violations.append("bare_probe.ok must be true")

    if payload.get("cleanup_confirmed") is not True:
        violations.append("cleanup_confirmed must be true")

    if payload.get("public_sharing_touched") is not False:
        violations.append("public_sharing_touched must be false")

    observations = payload.get("observations")
    if not isinstance(observations, dict):
        violations.append("observations missing or not a mapping")
    else:
        upstream_shape = observations.get("upstream_shape")
        bare_shape = observations.get("bare_shape")
        if upstream_shape is None:
            violations.append("observations.upstream_shape missing")
        else:
            violations.extend(
                _validate_shape_tree(
                    upstream_shape, path="$.observations.upstream_shape"
                )
            )
        if bare_shape is None:
            violations.append("observations.bare_shape missing")
        else:
            violations.extend(
                _validate_shape_tree(bare_shape, path="$.observations.bare_shape")
            )
        if upstream_shape != bare_shape:
            violations.append("observations upstream/bare shapes differ")

    promo = payload.get("category_promotion")
    if not isinstance(promo, dict):
        violations.append("category_promotion missing or not a mapping")
    else:
        for cat in _OPEN_CATEGORIES:
            if promo.get(cat) is not False:
                violations.append(f"category_promotion['{cat}'] must be false")
        if set(promo.keys()) != set(_OPEN_CATEGORIES):
            violations.append("category_promotion keys must match cli/api/auth/rpc")

    source_states = payload.get("category_states")
    if not isinstance(source_states, dict):
        violations.append("category_states missing or not a mapping")
    else:
        expected_states = {
            "cli": category_states.get("cli"),
            "api": category_states.get("api"),
            "rpc": category_states.get("rpc"),
            "auth": category_states.get("auth"),
        }
        for cat, expected_state in expected_states.items():
            if source_states.get(cat) != expected_state:
                violations.append(
                    f"category_states['{cat}'] should be '{expected_state}'"
                )

        if source_states.get("auth") != "open":
            violations.append("auth category must remain open")

    return violations


def _report_blocked(expected_states: dict[str, str]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "target": TARGET,
        "status": "blocked_expected",
        "strict_exit_code": STRICT_BLOCKED_EXIT,
        "report_path": "missing",
        "evidence_validated": False,
        "category_promotion": {cat: False for cat in _OPEN_CATEGORIES},
        "category_states": expected_states,
        "validation": {
            "violations": ["no --report provided"],
            "live_schema": "pending",
            "evidence_source_path": "missing",
            "evidence_redaction_hits": [],
        },
        "notes": [
            "live mutation evidence is validation-only; this phase runs in blocked_expected mode without a --report path",
            "no auth/api/cli/rpc promotion occurs from this gate",
            "auth category remains open",
        ],
    }


def _report_invalid(
    report_path: Path,
    expected_states: dict[str, str],
    violations: list[str],
    redaction_hits: list[str],
    strict_exit_code: int,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "target": TARGET,
        "status": "fail",
        "strict_exit_code": strict_exit_code,
        "report_path": "set",
        "evidence_validated": False,
        "category_promotion": {cat: False for cat in _OPEN_CATEGORIES},
        "category_states": expected_states,
        "validation": {
            "violations": violations,
            "live_schema": str(_LIVE_MUTATION_SCHEMA),
            "evidence_source_path": "set",
            "evidence_source": "set",
            "evidence_redaction_hits": redaction_hits,
        },
        "notes": [
            "live mutation evidence validation failed",
            "no auth/api/cli/rpc promotion occurs from this gate",
            "auth category remains open",
        ],
    }


def _report_valid(
    source: dict[str, Any],
    expected_states: dict[str, str],
    redaction_hits: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "target": TARGET,
        "status": "pass",
        "strict_exit_code": 0,
        "report_path": "set",
        "evidence_validated": True,
        "category_promotion": {cat: False for cat in _OPEN_CATEGORIES},
        "category_states": expected_states,
        "validation": {
            "violations": [],
            "live_schema": source.get("schema_version"),
            "evidence_source_path": "set",
            "evidence_source": "set",
            "evidence_redaction_hits": redaction_hits,
            "source_status": source.get("status"),
            "source_strict_exit_code": source.get("strict_exit_code"),
        },
        "notes": [
            "live mutation evidence artifact validates against current category ledger",
            "no auth/api/cli/rpc promotion occurs from this gate",
            "auth category remains open",
        ],
    }


def build_report(
    argv: list[str] | None = None,
    *,
    repo_root: str | Path | None = None,
    report_path: str | Path | None = None,
) -> dict[str, Any]:
    parser = _parser()
    args = parser.parse_args(list(argv or []))
    report_path = args.report if report_path is None else str(report_path)
    root = Path(REPO_ROOT if repo_root is None else repo_root)

    expected_states = _parse_category_states(root)
    if not report_path:
        return _report_blocked(expected_states)

    path = Path(report_path)
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):  # pragma: no cover - defensive FS boundary
        return _report_invalid(
            path,
            expected_states,
            ["report_path not readable"],
            [],
            STRICT_ERROR_EXIT,
        )

    raw_redaction_hits = _find_regex_redaction_hits(raw)
    source, duplicate_keys, parse_error = _parse_json_no_duplicate_keys(raw)
    if parse_error:
        return _report_invalid(
            path,
            expected_states,
            ["report is not JSON"],
            [],
            STRICT_ERROR_EXIT,
        )
    if source is None:
        return _report_invalid(
            path,
            expected_states,
            ["report JSON root must be an object"],
            [],
            STRICT_ERROR_EXIT,
        )

    redaction_hits = raw_redaction_hits + _find_redaction_hits(source)
    violations = _validate_live_payload(source, expected_states)
    if duplicate_keys:
        violations.append("report contains duplicate JSON keys")
    if redaction_hits:
        violations.extend(
            f"redaction pattern hit: {pattern}" for pattern in redaction_hits
        )

    if violations:
        return _report_invalid(
            path,
            expected_states,
            violations,
            redaction_hits,
            STRICT_BLOCKED_EXIT,
        )

    return _report_valid(source, expected_states, redaction_hits)


def _human_text(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"ZeroNotebookLM live mutation evidence audit: {report['status']}",
            f"strict_exit_code: {report['strict_exit_code']}",
            "evidence_validated: " + str(report["evidence_validated"]).lower(),
            "category promotion: no",
            "public_sharing_allowed: false",
            "no public sharing",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    actual_argv = list(sys.argv[1:] if argv is None else argv)
    args = _parser().parse_args(actual_argv)
    report = build_report(argv=actual_argv)

    if args.json_out:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(_human_text(report))

    if args.strict:
        return int(report["strict_exit_code"])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
