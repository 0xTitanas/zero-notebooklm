#!/usr/bin/env python3
"""Phase 13 release-candidate parity closure audit.

Repo-local/offline by default. Synthesizes all existing gates into a single
release-candidate readiness verdict. No live NotebookLM access, no
browser/keychain reads, no home discovery, no matrix mutation.

``release_candidate_ready`` remains the universal 1:1 verdict. The separate
``public_alpha_ready`` verdict accepts the reviewed selected profile while
preserving explicit exclusions and the false 1:1 claim.
"""

from __future__ import annotations

import argparse
import functools
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Callable

SCHEMA_VERSION = "release_candidate_audit/1"
TARGET = "notebooklm-py==0.7.2"

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
for _path in (REPO_ROOT, SCRIPTS_DIR):
    _path_s = str(_path)
    if _path_s not in sys.path:
        sys.path.insert(0, _path_s)

_OPEN_CATEGORIES = ("cli", "api", "auth", "rpc")
_ALL_CATEGORIES = ("cli", "api", "auth", "rpc", "offline", "self-test")

_REQUIRED_BLOCKERS: list[str] = sorted(
    [
        "auth_category_open",
        "live_readonly_differential_not_authorized",
        "live_mutation_smoke_not_authorized",
    ]
)


def _compat_path(repo_root: Path, name: str) -> Path:
    return repo_root / "compat" / name


def _load_script_module(scripts_dir: Path, filename: str, module_name: str) -> Any:
    """Load a repo-local script as an isolated module name."""
    script = scripts_dir / filename
    spec = importlib.util.spec_from_file_location(module_name, script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load script module: {filename}")
    mod = importlib.util.module_from_spec(spec)
    old_module = sys.modules.get(module_name)
    sys.modules[module_name] = mod
    try:
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    finally:
        if old_module is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = old_module
    return mod


def _parse_category_states(matrix_path: Path) -> dict[str, str]:
    """Return category → state mapping from parity_matrix.md."""
    states: dict[str, str] = {}
    for line in matrix_path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or "| ---" in line:
            continue
        cells = [c.strip().strip("`") for c in line.strip().strip("|").split("|")]
        if len(cells) == 4 and cells[0] in _ALL_CATEGORIES:
            states[cells[0]] = cells[3]
    return {cat: states.get(cat, "open") for cat in _ALL_CATEGORIES}


def _gate(ok: bool, status: str, summary: dict[str, Any]) -> dict[str, Any]:
    """Normalize a prior gate into a pathless Phase 13 gate summary."""
    return {"ok": bool(ok), "status": status, "summary": summary}


def _run_cli_api_gate(repo_root: Path) -> dict[str, Any]:
    mod = _load_script_module(
        repo_root / "scripts", "cli_api_parity_audit.py", "_phase13_cli_api_audit"
    )
    report = mod.build_report(repo_root=repo_root)
    cli_probe = report["cli"]["help_probe"]
    api_subclients = report["api"]["subclients"]
    ok = (
        report["overall_status"] == "pass"
        and report["strict_exit_code"] == 0
        and report["live_access"] is False
        and report["category_promotion"]
        == {
            "cli": report["category_states"].get("cli") == "pass",
            "api": report["category_states"].get("api") == "pass",
        }
    )
    return _gate(
        ok,
        report["overall_status"],
        {
            "cli_help_passed": cli_probe["passed"],
            "cli_help_total": cli_probe["total"],
            "api_public_names": report["api"]["actual_public_names"],
            "api_oracle_public_names": report["api"]["oracle_public_names"],
            "api_subclients_passed": api_subclients["passed"],
            "api_subclients_total": api_subclients["total"],
        },
    )


def _run_cli_behavior_gate(repo_root: Path) -> dict[str, Any]:
    mod = _load_script_module(
        repo_root / "scripts",
        "cli_behavior_parity_audit.py",
        "_phase13_cli_behavior_audit",
    )
    report = mod.run_audit(
        surface_path=_compat_path(repo_root, "cli_surface.json"),
        matrix_path=_compat_path(repo_root, "parity_matrix.md"),
    )
    ok = (
        report["status"] == "passed"
        and report["failed_scenario_count"] == 0
        and report["category_promotion"]
        == {"cli": report["cli_category_state"] == "pass"}
    )
    return _gate(
        ok,
        report["status"],
        {
            "passed": report["passed_scenario_count"],
            "total": report["pinned_leaf_total"],
            "covered": report["covered_leaf_total"],
            "failed": report["failed_scenario_count"],
            "cli_category_state": report["cli_category_state"],
        },
    )


def _run_api_behavior_gate(repo_root: Path) -> dict[str, Any]:
    mod = _load_script_module(
        repo_root / "scripts",
        "api_behavior_parity_audit.py",
        "_phase13_api_behavior_audit",
    )
    report = mod.build_report(repo_root=repo_root)
    probe = report["api_behavior"]["scenario_probe"]
    ok = (
        report["overall_status"] == "pass"
        and report["strict_exit_code"] == 0
        and report["live_access"] is False
        and report["credential_access"] is False
        and report["category_promotion"]
        == {"api": report["category_states"]["api"] == "pass"}
    )
    return _gate(
        ok,
        report["overall_status"],
        {
            "passed": probe["passed"],
            "total": probe["total"],
            "api_category_state": report["category_states"]["api"],
            "model_roundtrip_helpers_checked": report["model_behavior"][
                "roundtrip_helpers_checked"
            ],
        },
    )


def _run_rpc_drift_gate(repo_root: Path) -> dict[str, Any]:
    mod = _load_script_module(
        repo_root / "scripts", "rpc_drift_audit.py", "_phase13_rpc_drift_audit"
    )
    report = mod.build_report(repo_root)
    pairs = report["fixture_contract"]["pairs"]
    rpc_category_state = report["category_states"].get("rpc", "open")
    ok = (
        report["overall_status"] == "pass"
        and report["strict_exit_code"] == 0
        and report["live_access"] is False
        and report["credential_access"] is False
        and report["category_promotion"] == {"rpc": rpc_category_state == "pass"}
    )
    return _gate(
        ok,
        report["overall_status"],
        {
            "fixture_pairs_passed": pairs["roundtrips"],
            "fixture_pairs_total": pairs["total"],
            "fake_rpc_status": report["fake_rpc_contract"]["status"],
            "rpc_category_state": report["category_states"]["rpc"],
        },
    )


def _run_auth_evidence_gate(repo_root: Path) -> dict[str, Any]:
    mod = _load_script_module(
        repo_root / "scripts",
        "auth_parity_evidence_audit.py",
        "_phase13_auth_evidence_audit",
    )
    report = mod.build_report(repo_root=repo_root)
    matrix = report["auth_matrix_summary"]
    readiness = report["readiness_summary"]
    ok = (
        report["overall_status"] == "pass"
        and report["strict_exit_code"] == 0
        and report["live_access"] is False
        and report["network_access"] is False
        and report["browser_store_access"] is False
        and report["credential_access"] is False
        and report["category_promotion"] == {"auth": False}
    )
    return _gate(
        ok,
        report["overall_status"],
        {
            "total_rows": matrix["total"],
            "parity_open": matrix["parity_open"],
            "parity_pass": matrix["parity_pass"],
            "foundation_covered": readiness["foundation_covered_count"],
            "foundation_partial": readiness["foundation_partial_count"],
            "foundation_none": readiness["foundation_none_count"],
            "auth_category_state": report["category_states"]["auth"],
        },
    )


def _run_parity_readiness_gate(repo_root: Path) -> dict[str, Any]:
    mod = _load_script_module(
        repo_root / "scripts", "parity_readiness.py", "_phase13_parity_readiness"
    )
    report = mod.build_report(repo_root=repo_root)
    cli_api_pass = all(
        category in report["pass_categories"] for category in ("cli", "api")
    )
    ok = (
        report["release_ready"] is False
        and report["strict_exit_code"] == 77
        and report["live_authorization_required"] is True
        and report["mcp_next_phase_allowed"] is cli_api_pass
    )
    return _gate(
        ok,
        "blocked_expected",
        {
            "release_ready": report["release_ready"],
            "strict_exit_code": report["strict_exit_code"],
            "open_categories": report["open_categories"],
            "pass_categories": report["pass_categories"],
        },
    )


def _run_pass_row_evidence_gate(repo_root: Path) -> dict[str, Any]:
    mod = _load_script_module(
        repo_root / "scripts",
        "parity_evidence_audit.py",
        "_phase15_parity_evidence_audit",
    )
    report = mod.build_report(repo_root=repo_root)
    ok = report["strict_ok"] is True
    return _gate(
        ok,
        "pass" if ok else "fail",
        {
            "pass_rows_audited": report["pass_rows_audited"],
            "evidence_records_count": report["evidence_records_count"],
            "rows_missing_evidence": report["rows_missing_evidence"],
            "rows_with_unmet_tokens": report["rows_with_unmet_tokens"],
            "strict_ok": report["strict_ok"],
        },
    )


def _run_cli_api_row_evidence_gate(repo_root: Path) -> dict[str, Any]:
    mod = _load_script_module(
        repo_root / "scripts",
        "cli_api_row_evidence_audit.py",
        "_phase16_cli_api_row_evidence_audit",
    )
    report = mod.build_report(repo_root=repo_root)
    ok = report["strict_ok"] is True
    return _gate(
        ok,
        "pass" if ok else "fail",
        {
            "strict_ok": report["strict_ok"],
            "cli_rows_mapped": report["cli_rows_mapped"],
            "api_rows_mapped": report["api_rows_mapped"],
            "api_scenarios_mapped": report["api_scenarios_mapped"],
            "exact_one_to_one_claim_ready": report["exact_one_to_one_claim_ready"],
            "errors": report["errors"],
            "warnings": report["warnings"],
        },
    )


@functools.lru_cache(maxsize=4)
def _run_cli_api_direct_differential_gate_cached(repo_root_text: str) -> dict[str, Any]:
    repo_root = Path(repo_root_text)
    mod = _load_script_module(
        repo_root / "scripts",
        "cli_api_direct_differential.py",
        "_phase17_cli_api_direct_differential",
    )
    report = mod.build_report(repo_root=repo_root)
    ok = (
        report["overall_status"] in {"pass", "mismatch"}
        and report["strict_exit_code"] in {0, 77}
        and report["live_access"] is False
        and report["network_access"] is False
        and report["credential_access"] is False
        and report["browser_store_access"] is False
        and report["category_promotion"] == {"cli": False, "api": False}
        and report["exact_one_to_one_claim_ready"] is False
        and report["row_evidence"]["manifest_present"] is True
    )
    return _gate(
        ok,
        "pass" if report["overall_status"] == "pass" else "mismatch_expected",
        {
            "overall_status": report["overall_status"],
            "strict_exit_code": report["strict_exit_code"],
            "cli_matched": report["cli"]["matched"],
            "cli_total": report["cli"]["total"],
            "cli_mismatched": report["cli"]["mismatched"],
            "api_matched": report["api"]["matched"],
            "api_total": report["api"]["total"],
            "api_mismatched": report["api"]["mismatched"],
            "api_public_names_match": report["api"]["public_names"]["match"],
            "exact_one_to_one_claim_ready": report["exact_one_to_one_claim_ready"],
            "row_evidence": report["row_evidence"],
        },
    )


def _run_cli_api_direct_differential_gate(repo_root: Path) -> dict[str, Any]:
    return _run_cli_api_direct_differential_gate_cached(str(Path(repo_root).resolve()))


def _run_live_readonly_differential_gate(repo_root: Path) -> dict[str, Any]:
    mod = _load_script_module(
        repo_root / "scripts",
        "live_readonly_differential.py",
        "_phase13_live_readonly_differential",
    )
    report = mod.build_report(argv=[], env={})
    ok = (
        report["status"] == "skipped"
        and report["strict_exit_code"] == 77
        and report["live_enabled"] is False
        and report["category_promotion"]
        == {
            "cli": False,
            "api": False,
            "auth": False,
            "rpc": False,
        }
    )
    return _gate(
        ok,
        "skipped_expected" if report["status"] == "skipped" else report["status"],
        {
            "status": report["status"],
            "strict_exit_code": report["strict_exit_code"],
            "live_enabled": report["live_enabled"],
            "read_only": report["read_only"],
            "blockers": report.get("blockers", []),
        },
    )


def _run_live_auth_evidence_gate(
    repo_root: Path, live_auth_report: str | Path | None = None
) -> dict[str, Any]:
    mod = _load_script_module(
        repo_root / "scripts",
        "live_auth_evidence_audit.py",
        "_phase21_live_auth_evidence",
    )
    report = mod.build_report(
        argv=[], repo_root=repo_root, report_path=live_auth_report
    )
    ok = (
        report["status"] in {"blocked_expected", "pass"}
        and report["strict_exit_code"] in {0, 77}
        and report["category_promotion"]
        == {"cli": False, "api": False, "auth": False, "rpc": False}
    )
    return _gate(
        ok,
        "blocked_expected"
        if report["status"] == "blocked_expected"
        else report["status"],
        {
            "status": report["status"],
            "strict_exit_code": report["strict_exit_code"],
            "report_path": report["report_path"],
            "evidence_validated": report["evidence_validated"],
            "category_promotion": report["category_promotion"],
            "violations": report["validation"].get("violations", []),
        },
    )


def _run_live_mutation_evidence_gate(
    repo_root: Path, live_mutation_report: str | Path | None = None
) -> dict[str, Any]:
    mod = _load_script_module(
        repo_root / "scripts",
        "live_mutation_evidence_audit.py",
        "_phase24_live_mutation_evidence",
    )
    report = mod.build_report(
        argv=[],
        repo_root=repo_root,
        report_path=live_mutation_report,
    )
    ok = (
        report["status"] in {"blocked_expected", "pass"}
        and report["strict_exit_code"] in {0, 77}
        and report["category_promotion"]
        == {"cli": False, "api": False, "auth": False, "rpc": False}
    )
    return _gate(
        ok,
        "blocked_expected"
        if report["status"] == "blocked_expected"
        else report["status"],
        {
            "status": report["status"],
            "strict_exit_code": report["strict_exit_code"],
            "report_path": report["report_path"],
            "evidence_validated": report["evidence_validated"],
            "category_promotion": report["category_promotion"],
            "violations": report["validation"].get("violations", []),
        },
    )


def _run_auth_row_evidence_gate(
    repo_root: Path,
    live_auth_report: str | Path | None = None,
    live_mutation_report: str | Path | None = None,
    auth_row_evidence_report: str | Path | None = None,
) -> dict[str, Any]:
    mod = _load_script_module(
        repo_root / "scripts",
        "auth_row_promotion_audit.py",
        "_phase25_auth_row_promotion",
    )
    report = mod.build_report(
        repo_root=repo_root,
        live_auth_report=live_auth_report,
        live_mutation_report=live_mutation_report,
        row_evidence_report=auth_row_evidence_report,
    )
    ok = (
        report["status"] == "pass"
        and report["strict_ok"] is True
        and report["strict_exit_code"] == 0
        and report["exact_one_to_one_claim_ready"] is False
    )
    return _gate(
        ok,
        report["status"],
        {
            "status": report["status"],
            "strict_exit_code": report["strict_exit_code"],
            "auth_rows_mapped": report["auth_rows_mapped"],
            "auth_rows_promotable": report["auth_rows_promotable"],
            "auth_rows_blocked": report["auth_rows_blocked"],
            "exact_one_to_one_claim_ready": report["exact_one_to_one_claim_ready"],
            "live_category_level_evidence": report["live_reports"].get(
                "category_level_evidence", {}
            ),
            "auth_row_evidence_report": report.get("auth_row_evidence", {}),
            "auth_rows_matrix_summary": report["auth_rows_matrix_summary"],
        },
    )


def _run_local_gates(
    repo_root: Path,
    live_auth_report: str | Path | None = None,
    live_mutation_report: str | Path | None = None,
    auth_row_evidence_report: str | Path | None = None,
) -> dict[str, dict[str, Any]]:
    runners: tuple[tuple[str, Callable[..., dict[str, Any]]], ...] = (
        ("cli_api", _run_cli_api_gate),
        ("cli_behavior", _run_cli_behavior_gate),
        ("api_behavior", _run_api_behavior_gate),
        ("rpc_drift", _run_rpc_drift_gate),
        ("auth_evidence", _run_auth_evidence_gate),
        ("parity_readiness", _run_parity_readiness_gate),
        ("live_readonly_differential", _run_live_readonly_differential_gate),
        ("auth_row_evidence", _run_auth_row_evidence_gate),
        ("pass_row_evidence", _run_pass_row_evidence_gate),
        ("cli_api_row_evidence", _run_cli_api_row_evidence_gate),
        ("cli_api_direct_differential", _run_cli_api_direct_differential_gate),
        ("live_auth_evidence", _run_live_auth_evidence_gate),
        ("live_mutation_evidence", _run_live_mutation_evidence_gate),
    )
    gates: dict[str, dict[str, Any]] = {}
    for name, runner in runners:
        try:
            if name == "live_auth_evidence":
                gates[name] = runner(repo_root, live_auth_report)
            elif name == "live_mutation_evidence":
                gates[name] = runner(repo_root, live_mutation_report)
            elif name == "auth_row_evidence":
                gates[name] = runner(
                    repo_root,
                    live_auth_report,
                    live_mutation_report,
                    auth_row_evidence_report,
                )
            else:
                gates[name] = runner(repo_root)
        except Exception as exc:  # pragma: no cover - defensive path.
            gates[name] = _gate(False, "error", {"error_type": type(exc).__name__})
    return gates


def _remaining_blockers(
    category_states: dict[str, str], local_gates: dict[str, dict[str, Any]]
) -> list[str]:
    live_auth_evidence = local_gates.get("live_auth_evidence", {}).get("summary", {})
    live_auth_evidence_is_valid = (
        live_auth_evidence.get("status") == "pass"
        and live_auth_evidence.get("evidence_validated") is True
    )
    live_mutation_evidence = local_gates.get("live_mutation_evidence", {}).get(
        "summary", {}
    )
    live_mutation_evidence_is_valid = (
        live_mutation_evidence.get("status") == "pass"
        and live_mutation_evidence.get("evidence_validated") is True
    )
    blockers = [
        blocker
        for blocker in _REQUIRED_BLOCKERS
        if not (
            blocker == "live_readonly_differential_not_authorized"
            and live_auth_evidence_is_valid
        )
        and not (
            blocker == "live_mutation_smoke_not_authorized"
            and live_mutation_evidence_is_valid
        )
    ]
    for category in _OPEN_CATEGORIES:
        if category_states.get(category) != "pass":
            blockers.append(f"{category}_category_open")
    if any(category_states.get(category) != "pass" for category in ("cli", "api")):
        blockers.append("mcp_deferred_until_cli_api_parity")
    for name, gate in local_gates.items():
        if gate.get("ok") is not True:
            blockers.append(f"local_gate_{name}_failed")
    return sorted(dict.fromkeys(blockers))


def build_report(
    *,
    repo_root: Path = REPO_ROOT,
    live_auth_report: str | Path | None = None,
    live_mutation_report: str | Path | None = None,
    auth_row_evidence_report: str | Path | None = None,
) -> dict[str, Any]:
    """Build the Phase 13 release-candidate audit report.

    Pure/offline. Does not call ``Path.home()``, access live services, mutate
    ``parity_matrix.md``, or promote any parity rows.
    """
    repo_root = Path(repo_root)

    matrix_path = _compat_path(repo_root, "parity_matrix.md")
    matrix_bytes_before = matrix_path.read_bytes()

    category_states = _parse_category_states(matrix_path)
    local_gates = _run_local_gates(
        repo_root,
        live_auth_report,
        live_mutation_report,
        auth_row_evidence_report,
    )
    local_gate_status = (
        "pass"
        if all(gate.get("ok") is True for gate in local_gates.values())
        and category_states.get("offline") == "pass"
        and category_states.get("self-test") == "pass"
        else "fail"
    )
    blockers = _remaining_blockers(category_states, local_gates)
    release_candidate_ready = (
        local_gate_status == "pass"
        and not blockers
        and all(category_states.get(category) == "pass" for category in _ALL_CATEGORIES)
    )

    # Invariant: parity_matrix.md must not be mutated.
    if matrix_path.read_bytes() != matrix_bytes_before:
        raise RuntimeError("build_report mutated parity_matrix.md")

    def _summary(name: str) -> dict[str, Any]:
        summary = local_gates.get(name, {}).get("summary", {})
        return summary if isinstance(summary, dict) else {}

    def _gate(name: str) -> dict[str, Any]:
        gate = local_gates.get(name, {})
        return gate if isinstance(gate, dict) else {}

    cli_behavior = _summary("cli_behavior")
    api_behavior = _summary("api_behavior")
    auth = _summary("auth_evidence")
    rpc = _summary("rpc_drift")
    live_diff = _gate("live_readonly_differential")
    live_diff_summary = _summary("live_readonly_differential")
    live_auth = _gate("live_auth_evidence")
    live_auth_summary = _summary("live_auth_evidence")
    live_mutation = _gate("live_mutation_evidence")
    live_mutation_summary = _summary("live_mutation_evidence")
    auth_row = _summary("auth_row_evidence")
    pe = _summary("pass_row_evidence")
    cae = _summary("cli_api_row_evidence")
    cad = _summary("cli_api_direct_differential")

    public_alpha_blockers: list[str] = []
    if local_gate_status != "pass":
        public_alpha_blockers.append("local_gates_failed")
    if auth.get("parity_open", 0) or auth_row.get("auth_rows_blocked", 0):
        public_alpha_blockers.append("selected_auth_rows_open")
    for category in ("cli", "api", "rpc", "offline", "self-test"):
        if category_states.get(category) != "pass":
            public_alpha_blockers.append(f"{category}_category_open")
    public_alpha_ready = not public_alpha_blockers

    promoted_categories = {
        cat: category_states.get(cat) == "pass" for cat in _OPEN_CATEGORIES
    }
    promoted_rows = [
        f"{cat}:pass" for cat in _OPEN_CATEGORIES if category_states.get(cat) == "pass"
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "target": TARGET,
        "release_candidate_ready": release_candidate_ready,
        "public_alpha_ready": public_alpha_ready,
        "public_alpha_blockers": public_alpha_blockers,
        "local_gate_status": local_gate_status,
        "strict_exit_code": 0 if release_candidate_ready else 77,
        "one_to_one_functionality_claim": release_candidate_ready,
        "parity_rows_promoted": promoted_rows,
        "category_promotion": promoted_categories,
        "category_states": category_states,
        "remaining_blockers": blockers,
        "local_gates": local_gates,
        "gate_summary": {
            "cli_behavior_coverage": (
                f"{cli_behavior.get('passed', 0)}/{cli_behavior.get('total', 0)}"
            ),
            "cli_behavior_gate": category_states.get("cli", "open"),
            "api_behavior_coverage": (
                f"{api_behavior.get('passed', 0)}/{api_behavior.get('total', 0)}"
            ),
            "api_behavior_gate": category_states.get("api", "open"),
            "auth_rows_open": auth.get("parity_open", 0),
            "auth_rows_pass": auth.get("parity_pass", 0),
            "rpc_fixture_coverage": (
                f"{rpc.get('fixture_pairs_passed', 0)}/{rpc.get('fixture_pairs_total', 0)}"
            ),
            "rpc_fixture_gate": rpc.get("fake_rpc_status", "error"),
            "offline_category": category_states.get("offline", "open"),
            "self_test_category": category_states.get("self-test", "open"),
            "live_readonly_differential": live_diff.get("status", "error"),
            "live_mutation_smoke": live_mutation.get("status", "error"),
            "live_auth_evidence": live_auth.get("status", "error"),
            "auth_row_evidence_promotable": auth_row.get("auth_rows_promotable", 0),
            "auth_row_evidence_blocked": auth_row.get("auth_rows_blocked", 0),
            "parity_evidence_pass_rows": pe.get("pass_rows_audited", 0),
            "parity_evidence_strict_ok": pe.get("strict_ok", False),
            "cli_api_row_evidence_cli_rows": cae.get("cli_rows_mapped", 0),
            "cli_api_row_evidence_api_rows": cae.get("api_rows_mapped", 0),
            "cli_api_row_evidence_api_scenarios": cae.get("api_scenarios_mapped", 0),
            "cli_api_row_evidence_strict_ok": cae.get("strict_ok", False),
            "cli_api_direct_differential_status": cad.get("overall_status", "error"),
            "cli_api_direct_differential_cli_matched": cad.get("cli_matched", 0),
            "cli_api_direct_differential_cli_total": cad.get("cli_total", 0),
            "cli_api_direct_differential_api_matched": cad.get("api_matched", 0),
            "cli_api_direct_differential_api_total": cad.get("api_total", 0),
            "cli_api_direct_differential_strict_exit_code": cad.get(
                "strict_exit_code", 77
            ),
        },
        "live_readonly_differential": {
            "status": live_diff_summary.get("status", "skipped"),
            "strict_exit_code": live_diff_summary.get("strict_exit_code", 77),
            "live_enabled": live_diff_summary.get("live_enabled", False),
        },
        "live_mutation_evidence": {
            "status": live_mutation_summary.get("status", "blocked"),
            "strict_exit_code": live_mutation_summary.get("strict_exit_code", 77),
            "evidence_validated": live_mutation_summary.get(
                "evidence_validated", False
            ),
            "evidence_report_path": live_mutation_summary.get("report_path", "missing"),
        },
        "auth_row_evidence": {
            "status": auth_row.get("status", "error"),
            "strict_exit_code": auth_row.get("strict_exit_code", 77),
            "auth_rows_mapped": auth_row.get("auth_rows_mapped", 0),
            "auth_rows_promotable": auth_row.get("auth_rows_promotable", 0),
            "auth_rows_blocked": auth_row.get("auth_rows_blocked", 0),
            "exact_one_to_one_claim_ready": auth_row.get(
                "exact_one_to_one_claim_ready", False
            ),
            "live_category_level_evidence": auth_row.get(
                "live_category_level_evidence", {}
            ),
            "auth_row_evidence_report": auth_row.get("auth_row_evidence_report", {}),
            "auth_rows_matrix_summary": auth_row.get("auth_rows_matrix_summary", {}),
        },
        "live_auth_evidence": {
            "status": live_auth_summary.get("status", "blocked"),
            "strict_exit_code": live_auth_summary.get("strict_exit_code", 77),
            "evidence_validated": live_auth_summary.get("evidence_validated", False),
            "evidence_report_path": live_auth_summary.get("report_path", "missing"),
        },
    }


def _human_text(report: dict[str, Any]) -> str:
    rc_status = "ready" if report["release_candidate_ready"] else "blocked"
    lines = [
        f"ZeroNotebookLM release candidate audit: {rc_status}",
        f"local_gate_status: {report['local_gate_status']}",
        f"release_candidate_ready: {str(report['release_candidate_ready']).lower()}",
        f"public_alpha_ready: {str(report['public_alpha_ready']).lower()}",
        "one_to_one_functionality_claim: "
        + str(report["one_to_one_functionality_claim"]).lower(),
        "category promotion: "
        + ", ".join(
            f"{cat}={str(report['category_promotion'].get(cat, False)).lower()}"
            for cat in _OPEN_CATEGORIES
        ),
        "remaining blockers: " + ", ".join(report["remaining_blockers"]),
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", dest="json_out")
    strict = parser.add_mutually_exclusive_group()
    strict.add_argument("--strict", action="store_true")
    strict.add_argument("--strict-alpha", action="store_true")
    parser.add_argument("--live-auth-report", dest="live_auth_report")
    parser.add_argument("--live-mutation-report", dest="live_mutation_report")
    parser.add_argument("--auth-row-evidence-report", dest="auth_row_evidence_report")
    args = parser.parse_args(argv)

    report = build_report(
        live_auth_report=args.live_auth_report,
        live_mutation_report=args.live_mutation_report,
        auth_row_evidence_report=args.auth_row_evidence_report,
    )

    if args.json_out:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(_human_text(report))

    if args.strict_alpha:
        return 0 if report["public_alpha_ready"] else 77
    return int(report["strict_exit_code"]) if args.strict else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
