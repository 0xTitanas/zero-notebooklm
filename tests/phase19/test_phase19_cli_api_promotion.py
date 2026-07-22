"""Phase 19 CLI/API row promotion tests.

Validates that 90 CLI and 9 API parity rows are promoted to pass using
committed offline direct evidence (upstream-vs-bare golden comparison).
Pure/offline: no live NotebookLM, browser, keychain, credential, or network.

The selected auth profile is closed while the broader auth category remains open.
MCP remains not_applicable.
exact_one_to_one_claim_ready and release_candidate_ready remain false.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPAT_DIR = REPO_ROOT / "compat"
SCRIPTS_DIR = REPO_ROOT / "scripts"

EXPECTED_CLI_PASS = 90
EXPECTED_API_PASS = 9
EXPECTED_AUTH_TOTAL = 146
EXPECTED_AUTH_PASS = 146
EXPECTED_AUTH_OPEN = 0
EXPECTED_RPC_ROWS = 5
EXPECTED_TOTAL_PASS = (
    7 + EXPECTED_CLI_PASS + EXPECTED_API_PASS + EXPECTED_RPC_ROWS + EXPECTED_AUTH_PASS
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _load_rows() -> list[dict]:
    data = json.loads((COMPAT_DIR / "parity_rows.json").read_text(encoding="utf-8"))
    return data["rows"]


def _load_evidence() -> dict:
    return json.loads((COMPAT_DIR / "parity_evidence.json").read_text(encoding="utf-8"))


def _load_cli_api_evidence_manifest() -> dict:
    return json.loads(
        (COMPAT_DIR / "cli_api_row_evidence.json").read_text(encoding="utf-8")
    )


def _load_evidence_audit():
    script = SCRIPTS_DIR / "parity_evidence_audit.py"
    spec = importlib.util.spec_from_file_location("_evidence_audit_p19", script)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_cli_api_row_evidence_audit():
    script = SCRIPTS_DIR / "cli_api_row_evidence_audit.py"
    spec = importlib.util.spec_from_file_location(
        "_cli_api_row_evidence_audit_p19", script
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_rc_audit():
    script = SCRIPTS_DIR / "release_candidate_audit.py"
    spec = importlib.util.spec_from_file_location("_rc_audit_p19", script)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# 1. Row ledger: CLI/API/RPC and selected auth rows promoted
# --------------------------------------------------------------------------- #


def test_cli_rows_are_pass() -> None:
    """All 90 CLI parity rows must be promoted to pass."""
    rows = _load_rows()
    cli_rows = [r for r in rows if r.get("category") == "cli"]
    assert len(cli_rows) == EXPECTED_CLI_PASS, (
        f"expected {EXPECTED_CLI_PASS} CLI rows, got {len(cli_rows)}"
    )
    non_pass = [r["id"] for r in cli_rows if r.get("status") != "pass"]
    assert not non_pass, f"CLI rows not in pass state: {non_pass[:5]}"


def test_api_rows_are_pass() -> None:
    """All 9 API parity rows must be promoted to pass."""
    rows = _load_rows()
    api_rows = [r for r in rows if r.get("category") == "api"]
    assert len(api_rows) == EXPECTED_API_PASS, (
        f"expected {EXPECTED_API_PASS} API rows, got {len(api_rows)}"
    )
    non_pass = [r["id"] for r in api_rows if r.get("status") != "pass"]
    assert not non_pass, f"API rows not in pass state: {non_pass}"


def test_selected_auth_rows_are_closed() -> None:
    """All selected current-release auth rows pass."""
    rows = _load_rows()
    auth_rows = [r for r in rows if r.get("category") == "auth"]
    assert len(auth_rows) == EXPECTED_AUTH_TOTAL, (
        f"expected {EXPECTED_AUTH_TOTAL} auth rows, got {len(auth_rows)}"
    )
    assert sum(1 for r in auth_rows if r.get("status") == "pass") == EXPECTED_AUTH_PASS
    assert sum(1 for r in auth_rows if r.get("status") == "open") == EXPECTED_AUTH_OPEN
    assert not [r["id"] for r in auth_rows if r.get("status") == "blocked"]


def test_rpc_rows_are_promoted_to_pass() -> None:
    """All 5 RPC parity rows must be promoted to pass."""
    rows = _load_rows()
    rpc_rows = [r for r in rows if r.get("category") == "rpc"]
    assert len(rpc_rows) == EXPECTED_RPC_ROWS, (
        f"expected {EXPECTED_RPC_ROWS} RPC rows, got {len(rpc_rows)}"
    )
    non_pass = [r["id"] for r in rpc_rows if r.get("status") != "pass"]
    assert not non_pass, f"rpc rows not in pass state: {non_pass}"


def test_mcp_rows_not_applicable() -> None:
    """MCP rows must remain not_applicable."""
    rows = _load_rows()
    mcp_rows = [r for r in rows if r.get("category") == "mcp"]
    for r in mcp_rows:
        assert r.get("status") == "not_applicable", (
            f"mcp row {r['id']} has unexpected status {r.get('status')!r}"
        )


def test_total_pass_count_includes_partial_auth_rows() -> None:
    """Total pass rows include offline/CLI/API/RPC plus row-specific auth passes."""
    rows = _load_rows()
    pass_rows = [r for r in rows if r.get("status") == "pass"]
    assert len(pass_rows) == EXPECTED_TOTAL_PASS, (
        f"expected {EXPECTED_TOTAL_PASS} total pass rows, got {len(pass_rows)}"
    )


def test_cli_rows_blocker_reason_empty() -> None:
    """Promoted CLI rows must have empty blocker_reason."""
    rows = _load_rows()
    cli_rows = [r for r in rows if r.get("category") == "cli"]
    bad = [r["id"] for r in cli_rows if r.get("blocker_reason")]
    assert not bad, f"CLI rows with non-empty blocker_reason: {bad[:3]}"


def test_api_rows_blocker_reason_empty() -> None:
    """Promoted API rows must have empty blocker_reason."""
    rows = _load_rows()
    api_rows = [r for r in rows if r.get("category") == "api"]
    bad = [r["id"] for r in api_rows if r.get("blocker_reason")]
    assert not bad, f"API rows with non-empty blocker_reason: {bad}"


def test_cli_rows_required_evidence_has_upstream_vs_bare() -> None:
    """CLI rows must have upstream_vs_bare_direct_result as required evidence."""
    rows = _load_rows()
    cli_rows = [r for r in rows if r.get("category") == "cli"]
    bad = [
        r["id"]
        for r in cli_rows
        if "upstream_vs_bare_direct_result" not in r.get("required_evidence", [])
    ]
    assert not bad, f"CLI rows missing upstream_vs_bare_direct_result token: {bad[:3]}"


def test_api_rows_required_evidence_has_upstream_vs_bare() -> None:
    """API rows must have upstream_vs_bare_direct_result as required evidence."""
    rows = _load_rows()
    api_rows = [r for r in rows if r.get("category") == "api"]
    bad = [
        r["id"]
        for r in api_rows
        if "upstream_vs_bare_direct_result" not in r.get("required_evidence", [])
    ]
    assert not bad, f"API rows missing upstream_vs_bare_direct_result token: {bad}"


def test_cli_rows_no_differential_live_result_token() -> None:
    """CLI rows must not have differential_live_result as required evidence (replaced)."""
    rows = _load_rows()
    cli_rows = [r for r in rows if r.get("category") == "cli"]
    bad = [
        r["id"]
        for r in cli_rows
        if "differential_live_result" in r.get("required_evidence", [])
    ]
    assert not bad, f"CLI rows still using differential_live_result token: {bad[:3]}"


def test_api_rows_no_differential_live_result_token() -> None:
    """API rows must not have differential_live_result as required evidence (replaced)."""
    rows = _load_rows()
    api_rows = [r for r in rows if r.get("category") == "api"]
    bad = [
        r["id"]
        for r in api_rows
        if "differential_live_result" in r.get("required_evidence", [])
    ]
    assert not bad, f"API rows still using differential_live_result token: {bad}"


def test_exact_one_to_one_claim_ready_remains_false() -> None:
    """exact_one_to_one_claim_ready must remain false while auth remains open."""
    # parity_row_audit tracks this separately
    script = SCRIPTS_DIR / "parity_row_audit.py"
    spec = importlib.util.spec_from_file_location("_parity_row_audit_p19", script)
    assert spec is not None and spec.loader is not None
    row_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(row_mod)
    report = row_mod.build_report(repo_root=REPO_ROOT)
    assert report["exact_one_to_one_claim_ready"] is False, (
        f"exact_one_to_one_claim_ready must remain false; report: {report}"
    )


# --------------------------------------------------------------------------- #
# 2. Evidence audit: pass rows with satisfied tokens
# --------------------------------------------------------------------------- #


def test_evidence_audit_strict_ok_with_pass_rows() -> None:
    """parity_evidence_audit.py must be strict-ok with all pass rows after promotion."""
    mod = _load_evidence_audit()
    report = mod.build_report(repo_root=REPO_ROOT)
    assert report["strict_ok"] is True, (
        f"strict_ok is False; issues:\n"
        f"  rows_missing_evidence={report.get('rows_missing_evidence')}\n"
        f"  rows_with_unmet_tokens={report.get('rows_with_unmet_tokens')}\n"
        f"  records_missing_required_fields={report.get('records_missing_required_fields')}\n"
        f"  records_with_live_scope={report.get('records_with_live_scope')}\n"
        f"  records_for_open_rows={report.get('records_for_open_rows')}"
    )
    assert report["pass_rows_audited"] == EXPECTED_TOTAL_PASS, (
        f"expected {EXPECTED_TOTAL_PASS} pass rows audited, got {report['pass_rows_audited']}"
    )


def test_cli_evidence_tokens_satisfied() -> None:
    """Every CLI pass row must have cli_golden_help_match and upstream_vs_bare_direct_result."""
    rows = _load_rows()
    evidence_data = _load_evidence()
    evidence_by_row: dict[str, set[str]] = {}
    for rec in evidence_data["evidence_records"]:
        evidence_by_row.setdefault(rec["row_id"], set()).add(rec["evidence_id"])

    cli_rows = [r for r in rows if r.get("category") == "cli"]
    unmet: list[dict] = []
    for row in cli_rows:
        rid = row["id"]
        tokens = set(row.get("required_evidence", []))
        satisfied = evidence_by_row.get(rid, set())
        missing = tokens - satisfied
        if missing:
            unmet.append({"row_id": rid, "missing": sorted(missing)})

    assert not unmet, f"CLI rows with unmet evidence tokens: {unmet[:3]}"


def test_api_evidence_tokens_satisfied() -> None:
    """Every API pass row must have api_golden_signature_match and upstream_vs_bare_direct_result."""
    rows = _load_rows()
    evidence_data = _load_evidence()
    evidence_by_row: dict[str, set[str]] = {}
    for rec in evidence_data["evidence_records"]:
        evidence_by_row.setdefault(rec["row_id"], set()).add(rec["evidence_id"])

    api_rows = [r for r in rows if r.get("category") == "api"]
    unmet: list[dict] = []
    for row in api_rows:
        rid = row["id"]
        tokens = set(row.get("required_evidence", []))
        satisfied = evidence_by_row.get(rid, set())
        missing = tokens - satisfied
        if missing:
            unmet.append({"row_id": rid, "missing": sorted(missing)})

    assert not unmet, f"API rows with unmet evidence tokens: {unmet}"


def test_all_cli_api_evidence_records_closed_system() -> None:
    """All CLI/API evidence records must be closed_system=true and no_live=true."""
    rows = _load_rows()
    evidence_data = _load_evidence()
    cli_api_ids = {r["id"] for r in rows if r.get("category") in ("cli", "api")}

    bad_cs: list[str] = []
    bad_nl: list[str] = []
    for rec in evidence_data["evidence_records"]:
        if rec.get("row_id") not in cli_api_ids:
            continue
        label = f"{rec.get('row_id')}/{rec.get('evidence_id')}"
        if not rec.get("closed_system", False):
            bad_cs.append(label)
        if not rec.get("no_live", False):
            bad_nl.append(label)

    assert not bad_cs, (
        f"CLI/API evidence records without closed_system=true: {bad_cs[:3]}"
    )
    assert not bad_nl, f"CLI/API evidence records without no_live=true: {bad_nl[:3]}"


# --------------------------------------------------------------------------- #
# 3. CLI/API row evidence manifest: promotion-aware
# --------------------------------------------------------------------------- #


def test_cli_api_row_evidence_audit_strict_ok() -> None:
    """cli_api_row_evidence_audit.py must be strict-ok after promotion."""
    mod = _load_cli_api_row_evidence_audit()
    report = mod.build_report(repo_root=REPO_ROOT)
    assert report["strict_ok"] is True, (
        f"strict_ok is False; errors: {report.get('errors', [])[:5]}"
    )


def test_cli_api_row_evidence_category_promotion_true() -> None:
    """cli_api_row_evidence_audit must report category_promotion cli=True api=True."""
    mod = _load_cli_api_row_evidence_audit()
    report = mod.build_report(repo_root=REPO_ROOT)
    assert report.get("category_promotion") == {"cli": True, "api": True}, (
        f"expected category_promotion={{cli: True, api: True}}, "
        f"got {report.get('category_promotion')}"
    )


def test_cli_api_row_evidence_exact_claim_false() -> None:
    """cli_api_row_evidence_audit must report exact_one_to_one_claim_ready=False."""
    mod = _load_cli_api_row_evidence_audit()
    report = mod.build_report(repo_root=REPO_ROOT)
    assert report["exact_one_to_one_claim_ready"] is False


def test_cli_api_manifest_category_promotion_flags() -> None:
    """compat/cli_api_row_evidence.json must declare category_promotion cli/api true."""
    manifest = _load_cli_api_evidence_manifest()
    cp = manifest.get("category_promotion", {})
    assert cp.get("cli") is True, (
        f"manifest category_promotion.cli must be true, got {cp}"
    )
    assert cp.get("api") is True, (
        f"manifest category_promotion.api must be true, got {cp}"
    )


def test_cli_api_manifest_exact_claim_false() -> None:
    """compat/cli_api_row_evidence.json must have exact_one_to_one_claim_ready=false."""
    manifest = _load_cli_api_evidence_manifest()
    assert manifest.get("exact_one_to_one_claim_ready") is False


def test_cli_api_manifest_promoted_mappings_have_pass_status() -> None:
    """Promoted CLI/API mappings must have status=pass and row_status=pass."""
    manifest = _load_cli_api_evidence_manifest()
    for mapping in manifest.get("cli_mappings", []) + manifest.get("api_mappings", []):
        rid = mapping.get("row_id", "?")
        assert mapping.get("status") == "pass", (
            f"{rid}: expected status=pass, got {mapping.get('status')!r}"
        )
        assert mapping.get("row_status") == "pass", (
            f"{rid}: expected row_status=pass, got {mapping.get('row_status')!r}"
        )
        assert mapping.get("promotion_allowed") is True, (
            f"{rid}: expected promotion_allowed=true"
        )
        assert mapping.get("missing_for_promotion") == [], (
            f"{rid}: expected missing_for_promotion=[], got {mapping.get('missing_for_promotion')}"
        )


def test_cli_api_row_evidence_audit_rejects_synthetic_exact_claim_true(
    tmp_path: Path,
) -> None:
    """audit must fail when exact_one_to_one_claim_ready=true."""
    manifest = _load_cli_api_evidence_manifest()
    bad_manifest = dict(manifest)
    bad_manifest["exact_one_to_one_claim_ready"] = True

    synth_root = tmp_path / "synth"
    compat = synth_root / "compat"
    compat.mkdir(parents=True)
    scripts = synth_root / "scripts"
    scripts.mkdir()

    (compat / "cli_api_row_evidence.json").write_text(
        json.dumps(bad_manifest), encoding="utf-8"
    )
    (compat / "parity_rows.json").write_bytes(
        (COMPAT_DIR / "parity_rows.json").read_bytes()
    )
    golden_src = COMPAT_DIR / "api_golden"
    golden_dst = compat / "api_golden"
    golden_dst.mkdir()
    for f in golden_src.iterdir():
        (golden_dst / f.name).write_bytes(f.read_bytes())
    (scripts / "cli_behavior_parity_audit.py").write_bytes(
        (SCRIPTS_DIR / "cli_behavior_parity_audit.py").read_bytes()
    )

    mod = _load_cli_api_row_evidence_audit()
    report = mod.build_report(repo_root=synth_root)
    assert report["strict_ok"] is False, (
        "audit must reject manifest with exact_one_to_one_claim_ready=true"
    )


def test_cli_api_row_evidence_audit_rejects_false_closed_system(
    tmp_path: Path,
) -> None:
    """audit must fail when a promoted mapping has closed_system=false."""
    manifest = _load_cli_api_evidence_manifest()
    bad_manifest = dict(manifest)
    bad_cli = list(manifest["cli_mappings"])
    bad_cli[0] = dict(bad_cli[0])
    bad_cli[0]["closed_system"] = False
    bad_manifest["cli_mappings"] = bad_cli

    synth_root = tmp_path / "synth"
    compat = synth_root / "compat"
    compat.mkdir(parents=True)
    scripts = synth_root / "scripts"
    scripts.mkdir()

    (compat / "cli_api_row_evidence.json").write_text(
        json.dumps(bad_manifest), encoding="utf-8"
    )
    (compat / "parity_rows.json").write_bytes(
        (COMPAT_DIR / "parity_rows.json").read_bytes()
    )
    golden_src = COMPAT_DIR / "api_golden"
    golden_dst = compat / "api_golden"
    golden_dst.mkdir()
    for f in golden_src.iterdir():
        (golden_dst / f.name).write_bytes(f.read_bytes())
    (scripts / "cli_behavior_parity_audit.py").write_bytes(
        (SCRIPTS_DIR / "cli_behavior_parity_audit.py").read_bytes()
    )

    mod = _load_cli_api_row_evidence_audit()
    report = mod.build_report(repo_root=synth_root)
    assert report["strict_ok"] is False, (
        "audit must reject mapping with closed_system=false"
    )


def test_cli_api_row_evidence_audit_rejects_missing_scenario_refs(
    tmp_path: Path,
) -> None:
    """audit must fail when a promoted mapping has empty scenario_refs."""
    manifest = _load_cli_api_evidence_manifest()
    bad_manifest = dict(manifest)
    bad_cli = list(manifest["cli_mappings"])
    bad_cli[0] = dict(bad_cli[0])
    bad_cli[0]["scenario_refs"] = []
    bad_manifest["cli_mappings"] = bad_cli

    synth_root = tmp_path / "synth"
    compat = synth_root / "compat"
    compat.mkdir(parents=True)
    scripts = synth_root / "scripts"
    scripts.mkdir()

    (compat / "cli_api_row_evidence.json").write_text(
        json.dumps(bad_manifest), encoding="utf-8"
    )
    (compat / "parity_rows.json").write_bytes(
        (COMPAT_DIR / "parity_rows.json").read_bytes()
    )
    golden_src = COMPAT_DIR / "api_golden"
    golden_dst = compat / "api_golden"
    golden_dst.mkdir()
    for f in golden_src.iterdir():
        (golden_dst / f.name).write_bytes(f.read_bytes())
    (scripts / "cli_behavior_parity_audit.py").write_bytes(
        (SCRIPTS_DIR / "cli_behavior_parity_audit.py").read_bytes()
    )

    mod = _load_cli_api_row_evidence_audit()
    report = mod.build_report(repo_root=synth_root)
    assert report["strict_ok"] is False, (
        "audit must reject mapping with empty scenario_refs"
    )


# --------------------------------------------------------------------------- #
# 4. Release candidate audit: cli/api/rpc pass, auth/live still blocked
# --------------------------------------------------------------------------- #


def test_rc_audit_cli_category_state_pass() -> None:
    """release_candidate_audit must show cli category_state=pass."""
    mod = _load_rc_audit()
    report = mod.build_report(repo_root=REPO_ROOT)
    assert report["category_states"]["cli"] == "pass", (
        f"expected cli=pass, got {report['category_states']['cli']!r}"
    )


def test_rc_audit_api_category_state_pass() -> None:
    """release_candidate_audit must show api category_state=pass."""
    mod = _load_rc_audit()
    report = mod.build_report(repo_root=REPO_ROOT)
    assert report["category_states"]["api"] == "pass", (
        f"expected api=pass, got {report['category_states']['api']!r}"
    )


def test_rc_audit_auth_category_state_open() -> None:
    """release_candidate_audit must show auth category_state=open."""
    mod = _load_rc_audit()
    report = mod.build_report(repo_root=REPO_ROOT)
    assert report["category_states"]["auth"] == "open", (
        f"expected auth=open, got {report['category_states']['auth']!r}"
    )


def test_rc_audit_rpc_category_state_pass() -> None:
    """release_candidate_audit must show rpc category_state=pass."""
    mod = _load_rc_audit()
    report = mod.build_report(repo_root=REPO_ROOT)
    assert report["category_states"]["rpc"] == "pass", (
        f"expected rpc=pass, got {report['category_states']['rpc']!r}"
    )


def test_rc_audit_release_candidate_ready_false() -> None:
    """release_candidate_audit must keep release_candidate_ready=False."""
    mod = _load_rc_audit()
    report = mod.build_report(repo_root=REPO_ROOT)
    assert report["release_candidate_ready"] is False


def test_rc_audit_one_to_one_claim_false() -> None:
    """release_candidate_audit must keep one_to_one_functionality_claim=False."""
    mod = _load_rc_audit()
    report = mod.build_report(repo_root=REPO_ROOT)
    assert report["one_to_one_functionality_claim"] is False


def test_rc_audit_strict_exit_77() -> None:
    """release_candidate_audit --strict must still exit 77 after promotion."""
    mod = _load_rc_audit()
    report = mod.build_report(repo_root=REPO_ROOT)
    assert report["strict_exit_code"] == 77


def test_rc_audit_no_stale_cli_api_open_blockers() -> None:
    """release_candidate_audit remaining_blockers must not contain stale cli/api open blockers."""
    mod = _load_rc_audit()
    report = mod.build_report(repo_root=REPO_ROOT)
    blockers = set(report["remaining_blockers"])
    assert "cli_category_open" not in blockers, (
        "cli_category_open must not be a blocker when cli category is pass"
    )
    assert "api_category_open" not in blockers, (
        "api_category_open must not be a blocker when api category is pass"
    )


def test_rc_audit_auth_still_blocked_with_live_and_no_rpc_open_blocker() -> None:
    """release_candidate_audit remaining_blockers must include auth/live blockers only."""
    mod = _load_rc_audit()
    report = mod.build_report(repo_root=REPO_ROOT)
    blockers = set(report["remaining_blockers"])
    assert "auth_category_open" in blockers, "auth_category_open must remain a blocker"
    assert "rpc_category_open" not in blockers, (
        "rpc_category_open should no longer be a blocker"
    )
    assert blockers == {
        "auth_category_open",
        "live_readonly_differential_not_authorized",
        "live_mutation_smoke_not_authorized",
    }


def test_rc_audit_local_gate_status_pass() -> None:
    """release_candidate_audit local_gate_status must remain pass after promotion."""
    mod = _load_rc_audit()
    report = mod.build_report(repo_root=REPO_ROOT)
    assert report["local_gate_status"] == "pass", (
        f"local_gate_status must be pass; blockers: {report.get('remaining_blockers')}"
    )


def test_rc_audit_pass_row_evidence_shows_expected_count(repo_root: Path) -> None:
    """release_candidate_audit pass_row_evidence gate must show all pass rows."""
    mod = _load_rc_audit()
    report = mod.build_report(repo_root=repo_root)
    gate = report["local_gates"]["pass_row_evidence"]
    assert gate["ok"] is True
    assert gate["summary"]["pass_rows_audited"] == EXPECTED_TOTAL_PASS, (
        f"expected {EXPECTED_TOTAL_PASS} pass rows in gate summary, "
        f"got {gate['summary']['pass_rows_audited']}"
    )
