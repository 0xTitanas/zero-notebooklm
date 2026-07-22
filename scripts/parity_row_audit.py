#!/usr/bin/env python3
"""Phase 14A parity row-level audit.

Reads compat/parity_rows.json, compat/parity_matrix.md, and
compat/parity_normalization.md. Produces a JSON report with row-level
integrity checks, claim readiness, and remaining blockers.

Pure / offline: no Path.home(), no live services, no credentials, no
browser/keychain access, no matrix/ledger/spec mutation.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "parity_row_audit/1"
STRICT_BLOCKED_EXIT = 77
TARGET = "notebooklm-py==0.7.2"

REQUIRED_FIELDS = [
    "id",
    "category",
    "upstream_surface",
    "bare_surface",
    "fixture_live_requirement",
    "comparator",
    "allowed_normalizations",
    "required_evidence",
    "status",
    "blocker_reason",
    "promotion_authority",
    "claim_scope",
]
VALID_STATUSES = frozenset({"pass", "open", "blocked", "not_applicable"})

# Categories from parity_matrix.md that must have row expansion.
# "self-test" in the matrix maps to "self_test" in row category values.
MATRIX_CATEGORIES = ("cli", "api", "auth", "rpc", "offline", "self_test")
MATRIX_CATEGORY_ALIASES = {"self-test": "self_test"}

# Required normalization rule families (from parity_normalization.md).
REQUIRED_NORM_FAMILIES = frozenset(
    {
        "timestamp",
        "id_redaction",
        "ordering",
        "whitespace",
        "help_formatting",
        "generated_text",
        "cookie_redaction",
        "token_redaction",
        "platform_path",
        "traceback",
        "locale",
        "terminal_width",
        "nondeterministic_live",
        "xssi_prefix",
        "type_annotation_format",
    }
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_script_module(scripts_dir: Path, script_name: str, module_name: str):
    path = scripts_dir / script_name
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {script_name}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_live_auth_evidence_gate(
    repo_root: Path, live_auth_report: str | Path | None = None
) -> dict[str, Any]:
    if live_auth_report is None:
        return {
            "status": "blocked_expected",
            "strict_exit_code": STRICT_BLOCKED_EXIT,
            "evidence_validated": False,
            "report_path": "missing",
        }
    mod = _load_script_module(
        repo_root / "scripts",
        "live_auth_evidence_audit.py",
        "_parity_row_live_auth_evidence",
    )
    report = mod.build_report(
        argv=[], repo_root=repo_root, report_path=live_auth_report
    )
    return {
        "status": report.get("status"),
        "strict_exit_code": report.get("strict_exit_code"),
        "evidence_validated": report.get("evidence_validated") is True,
        "report_path": report.get("report_path"),
    }


def _run_live_mutation_evidence_gate(
    repo_root: Path, live_mutation_report: str | Path | None = None
) -> dict[str, Any]:
    if live_mutation_report is None:
        return {
            "status": "blocked_expected",
            "strict_exit_code": STRICT_BLOCKED_EXIT,
            "evidence_validated": False,
            "report_path": "missing",
        }
    mod = _load_script_module(
        repo_root / "scripts",
        "live_mutation_evidence_audit.py",
        "_parity_row_live_mutation_evidence",
    )
    report = mod.build_report(
        argv=[], repo_root=repo_root, report_path=live_mutation_report
    )
    return {
        "status": report.get("status"),
        "strict_exit_code": report.get("strict_exit_code"),
        "evidence_validated": report.get("evidence_validated") is True,
        "report_path": report.get("report_path"),
    }


def _compat(name: str) -> Path:
    return REPO_ROOT / "compat" / name


def _profile_exclusion_count(repo_root: Path) -> int | None:
    """Return explicit auth paths excluded from the current-release profile."""
    try:
        data = json.loads(
            (repo_root / "compat" / "auth_matrix.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return None
    exclusions = data.get("profile_exclusions")
    if not isinstance(exclusions, list) or not all(
        isinstance(row, dict) for row in exclusions
    ):
        return None
    # Browser/OS exclusions without a path stand for the five cookie flows.
    return sum(1 if row.get("path") else 5 for row in exclusions)


def _load_rows(path: Path) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"parity_rows.json parse error: {exc}")
        return {}, errors
    if not isinstance(data.get("rows"), list):
        errors.append("parity_rows.json missing 'rows' list")
        return data, errors
    return data, errors


def _parse_matrix_categories(md_text: str) -> set[str]:
    categories: set[str] = set()
    for line in md_text.splitlines():
        if not line.startswith("|") or "| ---" in line:
            continue
        cells = [c.strip().strip("`") for c in line.strip().strip("|").split("|")]
        if len(cells) == 4 and cells[0] not in ("Category", "category"):
            raw = cells[0]
            canonical = MATRIX_CATEGORY_ALIASES.get(raw, raw)
            categories.add(canonical)
    return categories


def _parse_norm_rules(md_text: str) -> list[str]:
    rules: list[str] = []
    for line in md_text.splitlines():
        if re.match(r"###\s+\d+\.", line):
            found = re.findall(r"`(\w+)`", line)
            rules.extend(found)
    return rules


def _check_rows(rows: list[dict], known_norm_keys: frozenset[str]) -> dict[str, Any]:
    missing_fields: list[str] = []
    invalid_status: list[str] = []
    seen_ids: list[str] = []
    duplicate_ids: list[str] = []
    missing_comparator: list[str] = []
    missing_evidence: list[str] = []
    missing_authority: list[str] = []
    rows_with_unknown_normalizations: list[dict[str, Any]] = []
    category_ids: dict[str, list[str]] = {}

    for row in rows:
        rid = row.get("id", "<no-id>")
        seen_ids.append(rid)

        absent = [f for f in REQUIRED_FIELDS if f not in row]
        if absent:
            missing_fields.append(f"{rid}: {absent}")

        status = row.get("status", "")
        if status not in VALID_STATUSES:
            invalid_status.append(f"{rid}: {status!r}")

        if not row.get("comparator"):
            missing_comparator.append(rid)

        evidence = row.get("required_evidence")
        if not evidence or (isinstance(evidence, list) and len(evidence) == 0):
            missing_evidence.append(rid)

        if not row.get("promotion_authority"):
            missing_authority.append(rid)

        anorms = row.get("allowed_normalizations", [])
        if isinstance(anorms, list):
            unknowns = [n for n in anorms if n not in known_norm_keys]
            if unknowns:
                rows_with_unknown_normalizations.append(
                    {"id": rid, "unknown_normalizations": unknowns}
                )

        cat = row.get("category", "")
        category_ids.setdefault(cat, []).append(rid)

    id_counts = Counter(seen_ids)
    duplicate_ids = sorted({rid for rid, cnt in id_counts.items() if cnt > 1})

    return {
        "missing_required_fields": missing_fields,
        "invalid_status_rows": invalid_status,
        "duplicate_row_ids": duplicate_ids,
        "rows_missing_comparator": missing_comparator,
        "rows_missing_required_evidence": missing_evidence,
        "rows_missing_promotion_authority": missing_authority,
        "rows_with_unknown_normalizations": rows_with_unknown_normalizations,
        "category_ids": category_ids,
    }


def _build_remaining_blockers(
    rows: list[dict],
    check: dict[str, Any],
    matrix_cats: set[str],
    category_ids: dict[str, list[str]],
    *,
    live_auth_evidence_valid: bool = False,
    live_mutation_evidence_valid: bool = False,
) -> list[str]:
    blockers: list[str] = []

    if check["missing_required_fields"]:
        blockers.append("rows_missing_required_fields")
    if check["invalid_status_rows"]:
        blockers.append("rows_with_invalid_status")
    if check["duplicate_row_ids"]:
        blockers.append("duplicate_row_ids")
    if check["rows_missing_comparator"]:
        blockers.append("rows_missing_comparator")
    if check["rows_missing_required_evidence"]:
        blockers.append("rows_missing_required_evidence")
    if check["rows_missing_promotion_authority"]:
        blockers.append("rows_missing_promotion_authority")
    if check["rows_with_unknown_normalizations"]:
        blockers.append("rows_with_unknown_normalizations")

    for cat in MATRIX_CATEGORIES:
        if cat not in matrix_cats:
            blockers.append(f"matrix_category_missing_from_spec_{cat}")
        if cat not in category_ids or len(category_ids[cat]) == 0:
            blockers.append(f"category_has_no_rows_{cat}")

    non_pass_cats = set()
    for row in rows:
        if row.get("status") in ("open", "blocked"):
            cat = row.get("category", "")
            if cat in MATRIX_CATEGORIES:
                non_pass_cats.add(cat)

    for cat in sorted(non_pass_cats):
        blockers.append(f"{cat}_category_open_or_blocked")

    if not live_auth_evidence_valid:
        blockers.append("live_readonly_differential_not_authorized")
    if not live_mutation_evidence_valid:
        blockers.append("live_mutation_smoke_not_authorized")
    if {"cli", "api"} & non_pass_cats:
        blockers.append("mcp_deferred_until_cli_api_parity_pass")
    else:
        blockers.append("mcp_adapter_not_implemented_out_of_scope")

    return sorted(dict.fromkeys(blockers))


def build_report(
    *,
    repo_root: Path = REPO_ROOT,
    live_auth_report: str | Path | None = None,
    live_mutation_report: str | Path | None = None,
) -> dict[str, Any]:
    """Return parity row audit report. Pure/offline except explicit report-file validation."""
    repo_root = Path(repo_root)
    rows_path = repo_root / "compat" / "parity_rows.json"
    matrix_path = repo_root / "compat" / "parity_matrix.md"
    norm_path = repo_root / "compat" / "parity_normalization.md"

    load_errors: list[str] = []

    # --- load parity_rows.json ---
    if not rows_path.is_file():
        load_errors.append("compat/parity_rows.json missing")
        data: dict[str, Any] = {}
        rows: list[dict] = []
    else:
        data, errs = _load_rows(rows_path)
        load_errors.extend(errs)
        rows = data.get("rows", []) if isinstance(data, dict) else []

    # --- load parity_matrix.md ---
    if not matrix_path.is_file():
        load_errors.append("compat/parity_matrix.md missing")
        matrix_cats: set[str] = set()
    else:
        matrix_cats = _parse_matrix_categories(matrix_path.read_text(encoding="utf-8"))

    # --- load parity_normalization.md ---
    norm_present = norm_path.is_file()
    if norm_present:
        norm_rules = _parse_norm_rules(norm_path.read_text(encoding="utf-8"))
        missing_norm = sorted(REQUIRED_NORM_FAMILIES - set(norm_rules))
    else:
        norm_rules = []
        missing_norm = sorted(REQUIRED_NORM_FAMILIES)
        load_errors.append("compat/parity_normalization.md missing")

    # --- row-level checks ---
    check = _check_rows(rows, frozenset(norm_rules))
    category_ids: dict[str, list[str]] = check.pop("category_ids")

    status_counts: dict[str, int] = dict(Counter(r.get("status", "") for r in rows))
    category_counts: dict[str, int] = {
        cat: len(ids) for cat, ids in category_ids.items()
    }

    # --- missing category expansion ---
    categories_without_rows = sorted(
        cat
        for cat in MATRIX_CATEGORIES
        if cat not in category_ids or not category_ids[cat]
    )

    # --- claim readiness ---
    blocking_statuses = {"open", "blocked"}
    any_open_or_blocked = any(r.get("status") in blocking_statuses for r in rows)
    explicit_profile_exclusion_count = _profile_exclusion_count(repo_root)
    if explicit_profile_exclusion_count is None:
        load_errors.append("compat/auth_matrix.json profile exclusions unavailable")
    has_integrity_errors = bool(
        check["missing_required_fields"]
        or check["invalid_status_rows"]
        or check["duplicate_row_ids"]
        or missing_norm
        or categories_without_rows
        or load_errors
        or check["rows_with_unknown_normalizations"]
        or check["rows_missing_comparator"]
        or check["rows_missing_required_evidence"]
        or check["rows_missing_promotion_authority"]
    )
    exact_one_to_one = (
        not any_open_or_blocked
        and explicit_profile_exclusion_count == 0
        and not has_integrity_errors
    )

    live_auth_evidence = _run_live_auth_evidence_gate(repo_root, live_auth_report)
    live_mutation_evidence = _run_live_mutation_evidence_gate(
        repo_root, live_mutation_report
    )

    remaining_blockers = _build_remaining_blockers(
        rows,
        check,
        matrix_cats,
        category_ids,
        live_auth_evidence_valid=live_auth_evidence["evidence_validated"],
        live_mutation_evidence_valid=live_mutation_evidence["evidence_validated"],
    )
    if explicit_profile_exclusion_count is None:
        remaining_blockers.append("profile_exclusion_state_unavailable")
        remaining_blockers.sort()
    elif explicit_profile_exclusion_count:
        remaining_blockers.append("explicit_profile_exclusions_remain")
        remaining_blockers.sort()

    strict_exit = STRICT_BLOCKED_EXIT if not exact_one_to_one else 0

    return {
        "schema_version": SCHEMA_VERSION,
        "target": TARGET,
        "row_count": len(rows),
        "explicit_profile_exclusion_count": explicit_profile_exclusion_count,
        "status_counts": status_counts,
        "category_counts": category_counts,
        "missing_required_fields": check["missing_required_fields"],
        "invalid_status_rows": check["invalid_status_rows"],
        "duplicate_row_ids": check["duplicate_row_ids"],
        "rows_missing_comparator": check["rows_missing_comparator"],
        "rows_missing_required_evidence": check["rows_missing_required_evidence"],
        "rows_missing_promotion_authority": check["rows_missing_promotion_authority"],
        "rows_with_unknown_normalizations": check["rows_with_unknown_normalizations"],
        "normalization_spec_present": norm_present,
        "normalization_rules": norm_rules,
        "missing_normalization_rules": missing_norm,
        "categories_without_rows": categories_without_rows,
        "matrix_categories_found": sorted(matrix_cats),
        "load_errors": load_errors,
        "live_auth_evidence": live_auth_evidence,
        "live_mutation_evidence": live_mutation_evidence,
        "exact_one_to_one_claim_ready": exact_one_to_one,
        "strict_exit_code": strict_exit,
        "remaining_blockers": remaining_blockers,
    }


def render_human(report: dict[str, Any]) -> str:
    status = "pass" if report["exact_one_to_one_claim_ready"] else "blocked"
    lines = [f"parity row audit: {status}"]
    lines.append(f"rows: {report['row_count']}")
    sc = report["status_counts"]
    lines.append("status: " + "  ".join(f"{k}={v}" for k, v in sorted(sc.items()) if v))
    blockers = report["remaining_blockers"]
    if blockers:
        lines.append(f"blockers ({len(blockers)}): " + ", ".join(blockers[:8]))
        if len(blockers) > 8:
            lines.append(f"  ... and {len(blockers) - 8} more")
    else:
        lines.append("blockers: none")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="parity_row_audit.py")
    parser.add_argument("--json", action="store_true", help="emit full JSON report")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero (77) while exact 1:1 claim is blocked",
    )
    parser.add_argument(
        "--live-auth-report",
        default=None,
        help="optional explicit live_readonly_differential JSON report to validate",
    )
    parser.add_argument(
        "--live-mutation-report",
        default=None,
        help="optional explicit live_mutation_export_differential JSON report to validate",
    )
    args = parser.parse_args(argv)

    report = build_report(
        live_auth_report=args.live_auth_report,
        live_mutation_report=args.live_mutation_report,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_human(report), end="")

    if args.strict:
        return int(report["strict_exit_code"])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
