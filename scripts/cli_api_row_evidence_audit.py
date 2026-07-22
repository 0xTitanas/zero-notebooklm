#!/usr/bin/env python3
"""Phase 16/19 CLI/API row evidence audit.

Validates ``compat/cli_api_row_evidence.json`` against the row ledger and the
Phase 8/9 fixture behavior registries.

Phase 16 mode: open-row mapping gate — every CLI/API row is mapped to
closed-system fixture evidence and the still-missing upstream/direct proof.
Phase 19 mode: promotion-aware — accepts promoted rows (status=pass) backed
by committed offline direct differential evidence while never making the
exact 1:1 claim ready.

Pure/offline: no Path.home(), live NotebookLM, browser, keychain, credential,
network, or user home access.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "cli_api_row_evidence_audit/1"
MANIFEST_SCHEMA_VERSION = "cli_api_row_evidence/1"
TARGET = "notebooklm-py==0.7.2"
EXCLUDED_CATEGORIES = frozenset({"auth", "rpc", "offline", "self_test", "mcp"})
REQUIRED_MISSING_TOKEN = frozenset(
    {"differential_live_result", "upstream_vs_bare_direct_result"}
)
# Fields required on every mapping; missing_for_promotion may be [] for promoted rows.
REQUIRED_MAPPING_FIELDS_ALWAYS = (
    "row_id",
    "category",
    "status",
    "row_status",
    "scenario_refs",
    "comparator",
    "evidence_basis",
    "closed_system",
    "no_live",
    "promotion_allowed",
)
# Legacy alias kept for callers that read the constant directly.
REQUIRED_MAPPING_FIELDS = REQUIRED_MAPPING_FIELDS_ALWAYS + ("missing_for_promotion",)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_cli_leaves(repo_root: Path) -> set[str]:
    """Return the set of Phase 8 CLI scenario leaf strings."""
    script = repo_root / "scripts" / "cli_behavior_parity_audit.py"
    if not script.is_file():
        raise FileNotFoundError("scripts/cli_behavior_parity_audit.py missing")
    spec = importlib.util.spec_from_file_location(
        "_cli_behavior_parity_audit_phase16", script
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load CLI behavior audit module spec")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return {scenario.leaf for scenario in module.build_scenarios()}


def _expected_api_scenario_ids(repo_root: Path) -> set[str]:
    """Return the pinned API scenario IDs used by the Phase 9 fixture audit."""
    sigs = _load_json(repo_root / "compat" / "api_golden" / "signatures.json")
    scenario_ids: set[str] = set()
    for subclient, spec in sigs["subclients"].items():
        for method in spec["async_methods"]:
            scenario_ids.add(f"{subclient}.{method}")
    return scenario_ids


def _api_subclient_for_row(row_id: str) -> str:
    prefix = "api.client."
    if not row_id.startswith(prefix):
        return ""
    return row_id.removeprefix(prefix)


def _issue(errors: list[str], label: str, message: str) -> None:
    errors.append(f"{label}: {message}")


def _normalised_cli_row_id(leaf: str) -> str:
    return "cli." + leaf.replace(" ", ".")


def build_report(repo_root: Path | None = None) -> dict[str, Any]:
    if repo_root is None:
        repo_root = _repo_root()
    repo_root = Path(repo_root)

    errors: list[str] = []
    warnings: list[str] = []

    manifest_path = repo_root / "compat" / "cli_api_row_evidence.json"
    rows_path = repo_root / "compat" / "parity_rows.json"

    if not manifest_path.is_file():
        return {
            "schema_version": SCHEMA_VERSION,
            "target": TARGET,
            "manifest_present": False,
            "cli_rows_expected": 0,
            "api_rows_expected": 0,
            "cli_rows_mapped": 0,
            "api_rows_mapped": 0,
            "api_scenarios_expected": 0,
            "api_scenarios_mapped": 0,
            "strict_ok": False,
            "strict_exit_code": 1,
            "exact_one_to_one_claim_ready": False,
            "errors": ["manifest missing: compat/cli_api_row_evidence.json"],
            "warnings": [],
        }

    manifest = _load_json(manifest_path)
    rows_data = _load_json(rows_path)
    all_rows: list[dict[str, Any]] = rows_data.get("rows", [])
    rows_by_id = {row.get("id", ""): row for row in all_rows}

    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        _issue(
            errors,
            "manifest",
            f"schema_version must be {MANIFEST_SCHEMA_VERSION!r}",
        )
    if manifest.get("target") != TARGET:
        _issue(errors, "manifest", f"target must be {TARGET!r}")
    if manifest.get("exact_one_to_one_claim_ready") is not False:
        _issue(errors, "manifest", "exact_one_to_one_claim_ready must be false")

    cli_rows = [row for row in all_rows if row.get("category") == "cli"]
    api_rows = [row for row in all_rows if row.get("category") == "api"]
    expected_cli_ids = {row["id"] for row in cli_rows}
    expected_api_ids = {row["id"] for row in api_rows}

    cli_mappings = manifest.get("cli_mappings", [])
    api_mappings = manifest.get("api_mappings", [])
    if not isinstance(cli_mappings, list):
        _issue(errors, "manifest", "cli_mappings must be a list")
        cli_mappings = []
    if not isinstance(api_mappings, list):
        _issue(errors, "manifest", "api_mappings must be a list")
        api_mappings = []

    if len(cli_mappings) != len(expected_cli_ids):
        _issue(
            errors,
            "manifest",
            f"cli_mappings count {len(cli_mappings)} != expected {len(expected_cli_ids)}",
        )
    if len(api_mappings) != len(expected_api_ids):
        _issue(
            errors,
            "manifest",
            f"api_mappings count {len(api_mappings)} != expected {len(expected_api_ids)}",
        )

    expected_api_scenarios: set[str] = set()
    try:
        expected_api_scenarios = _expected_api_scenario_ids(repo_root)
    except Exception as exc:  # noqa: BLE001
        _issue(errors, "api_scenarios", f"could not load expected API scenarios: {exc}")

    cli_leaves: set[str] = set()
    try:
        cli_leaves = _load_cli_leaves(repo_root)
    except Exception as exc:  # noqa: BLE001
        _issue(errors, "cli_scenarios", f"could not load CLI leaves: {exc}")

    seen_cli: list[str] = []
    seen_api: list[str] = []
    covered_api_scenarios: set[str] = set()
    covered_cli_leaves: set[str] = set()

    promoted_cli: set[str] = set()
    promoted_api: set[str] = set()

    for expected_category, mapping in [("cli", item) for item in cli_mappings] + [
        ("api", item) for item in api_mappings
    ]:
        if not isinstance(mapping, dict):
            _issue(errors, expected_category, "mapping entry must be an object")
            continue
        row_id = str(mapping.get("row_id", ""))
        label = row_id or "<missing-row_id>"

        # Check fields always required (non-empty).
        for field in REQUIRED_MAPPING_FIELDS_ALWAYS:
            value = mapping.get(field)
            if field not in mapping or value in ("", None):
                _issue(errors, label, f"required field {field!r} must be non-empty")

        # scenario_refs list must be present (may not be empty).
        if "scenario_refs" not in mapping:
            _issue(errors, label, "required field 'scenario_refs' must be non-empty")

        # missing_for_promotion must be a list (may be [] for promoted rows).
        if "missing_for_promotion" not in mapping or not isinstance(
            mapping.get("missing_for_promotion"), list
        ):
            _issue(errors, label, "missing_for_promotion must be a list")

        if mapping.get("category") != expected_category:
            _issue(errors, label, f"category must be {expected_category!r}")
        if mapping.get("closed_system") is not True:
            _issue(errors, label, "closed_system must be true")
        if mapping.get("no_live") is not True:
            _issue(errors, label, "no_live must be true")

        is_promoted = (
            mapping.get("status") == "pass" and mapping.get("row_status") == "pass"
        )
        is_open = (
            mapping.get("status") == "open" and mapping.get("row_status") == "open"
        )

        if is_promoted:
            if mapping.get("promotion_allowed") is not True:
                _issue(
                    errors, label, "promoted mapping must have promotion_allowed=true"
                )
            missing_for_promotion = mapping.get("missing_for_promotion", [])
            if missing_for_promotion:
                _issue(
                    errors,
                    label,
                    "promoted mapping must have missing_for_promotion=[]",
                )
        elif is_open:
            if mapping.get("promotion_allowed") is not False:
                _issue(errors, label, "open mapping must have promotion_allowed=false")
            missing_for_promotion = mapping.get("missing_for_promotion", [])
            if not isinstance(missing_for_promotion, list) or not missing_for_promotion:
                _issue(
                    errors,
                    label,
                    "open mapping missing_for_promotion must be non-empty",
                )
            elif not (REQUIRED_MISSING_TOKEN & set(missing_for_promotion)):
                _issue(
                    errors,
                    label,
                    "missing_for_promotion must include differential_live_result or "
                    "upstream_vs_bare_direct_result",
                )
        else:
            _issue(
                errors,
                label,
                f"status/row_status must be both 'open' or both 'pass'; "
                f"got status={mapping.get('status')!r} row_status={mapping.get('row_status')!r}",
            )

        scenario_refs = mapping.get("scenario_refs", [])
        if not isinstance(scenario_refs, list) or not scenario_refs:
            _issue(errors, label, "scenario_refs must be a non-empty list")
            scenario_refs = []

        row = rows_by_id.get(row_id)
        if row is None:
            _issue(errors, label, "row_id is not present in parity_rows.json")
        else:
            row_category = row.get("category")
            if row_category in EXCLUDED_CATEGORIES:
                _issue(
                    errors, label, f"excluded category row referenced: {row_category}"
                )
            if row_category != expected_category:
                _issue(errors, label, f"row category is {row_category!r}")
            row_status = row.get("status")
            if is_promoted and row_status != "pass":
                _issue(
                    errors,
                    label,
                    f"promoted mapping but ledger row status is {row_status!r}, not pass",
                )
            elif is_open and row_status != "open":
                _issue(
                    errors,
                    label,
                    f"open mapping but ledger row status is {row_status!r}, not open",
                )
            if mapping.get("comparator") != row.get("comparator"):
                _issue(errors, label, "comparator must match parity row comparator")

        if is_promoted:
            if expected_category == "cli":
                promoted_cli.add(row_id)
            else:
                promoted_api.add(row_id)

        if expected_category == "cli":
            seen_cli.append(row_id)
            for ref in scenario_refs:
                if ref not in cli_leaves:
                    _issue(errors, label, f"CLI scenario_ref not registered: {ref!r}")
                    continue
                covered_cli_leaves.add(ref)
                normalised = _normalised_cli_row_id(ref)
                if row_id != normalised:
                    _issue(
                        errors,
                        label,
                        f"CLI scenario_ref {ref!r} normalizes to {normalised!r}",
                    )
        else:
            seen_api.append(row_id)
            subclient = _api_subclient_for_row(row_id)
            for ref in scenario_refs:
                if ref not in expected_api_scenarios:
                    _issue(errors, label, f"API scenario_ref not registered: {ref!r}")
                    continue
                if not ref.startswith(f"{subclient}."):
                    _issue(
                        errors,
                        label,
                        f"API scenario_ref {ref!r} does not belong to subclient {subclient!r}",
                    )
                covered_api_scenarios.add(ref)

    duplicate_cli = sorted({rid for rid in seen_cli if seen_cli.count(rid) > 1})
    duplicate_api = sorted({rid for rid in seen_api if seen_api.count(rid) > 1})
    for row_id in duplicate_cli:
        _issue(errors, row_id, "duplicate CLI mapping")
    for row_id in duplicate_api:
        _issue(errors, row_id, "duplicate API mapping")

    missing_cli = expected_cli_ids - set(seen_cli)
    extra_cli = set(seen_cli) - expected_cli_ids
    missing_api = expected_api_ids - set(seen_api)
    extra_api = set(seen_api) - expected_api_ids
    if missing_cli:
        _issue(errors, "cli", f"rows without mapping: {sorted(missing_cli)[:5]}")
    if extra_cli:
        _issue(errors, "cli", f"unknown row mappings: {sorted(extra_cli)[:5]}")
    if missing_api:
        _issue(errors, "api", f"rows without mapping: {sorted(missing_api)[:5]}")
    if extra_api:
        _issue(errors, "api", f"unknown row mappings: {sorted(extra_api)[:5]}")

    missing_api_scenarios = expected_api_scenarios - covered_api_scenarios
    extra_api_scenarios = covered_api_scenarios - expected_api_scenarios
    if missing_api_scenarios:
        _issue(
            errors,
            "api_scenarios",
            f"missing {len(missing_api_scenarios)} scenario IDs: "
            f"{sorted(missing_api_scenarios)[:5]}",
        )
    if extra_api_scenarios:
        _issue(
            errors,
            "api_scenarios",
            f"unknown scenario IDs: {sorted(extra_api_scenarios)[:5]}",
        )

    cli_all_promoted = bool(expected_cli_ids and promoted_cli >= expected_cli_ids)
    api_all_promoted = bool(expected_api_ids and promoted_api >= expected_api_ids)
    strict_ok = not errors and not warnings
    return {
        "schema_version": SCHEMA_VERSION,
        "target": TARGET,
        "manifest_present": True,
        "cli_rows_expected": len(expected_cli_ids),
        "api_rows_expected": len(expected_api_ids),
        "cli_rows_mapped": len(set(seen_cli) & expected_cli_ids),
        "api_rows_mapped": len(set(seen_api) & expected_api_ids),
        "cli_mapping_count": len(cli_mappings),
        "api_mapping_count": len(api_mappings),
        "api_scenarios_expected": len(expected_api_scenarios),
        "api_scenarios_mapped": len(covered_api_scenarios),
        "strict_ok": strict_ok,
        "strict_exit_code": 0 if strict_ok else 1,
        "exact_one_to_one_claim_ready": False,
        "category_promotion": {"cli": cli_all_promoted, "api": api_all_promoted},
        "errors": errors,
        "warnings": warnings,
    }


def _human_text(report: dict[str, Any]) -> str:
    status = "pass" if report["strict_ok"] else "fail"
    cp = report.get("category_promotion", {})
    cli_promo = "true" if cp.get("cli") else "false"
    api_promo = "true" if cp.get("api") else "false"
    lines = [
        f"cli/api row evidence audit: {status}",
        f"cli_rows_mapped: {report.get('cli_rows_mapped', 0)}/"
        f"{report.get('cli_rows_expected', 0)}",
        f"api_rows_mapped: {report.get('api_rows_mapped', 0)}/"
        f"{report.get('api_rows_expected', 0)}",
        f"api_scenarios_mapped: {report.get('api_scenarios_mapped', 0)}/"
        f"{report.get('api_scenarios_expected', 0)}",
        f"category_promotion: cli={cli_promo} api={api_promo}",
        "exact_one_to_one_claim_ready: false",
    ]
    for error in report.get("errors", []):
        lines.append(f"ERROR: {error}")
    for warning in report.get("warnings", []):
        lines.append(f"WARN: {warning}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cli_api_row_evidence_audit.py",
        description="Phase 16 CLI/API open-row evidence audit.",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON report")
    parser.add_argument(
        "--strict", action="store_true", help="exit non-zero if not strict_ok"
    )
    args = parser.parse_args(argv)

    report = build_report()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(_human_text(report))

    if args.strict:
        return int(report["strict_exit_code"])
    return 0 if report["strict_ok"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
