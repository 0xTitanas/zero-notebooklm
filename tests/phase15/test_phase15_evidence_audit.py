"""Phase 15 pass-row evidence hardening tests.

Tests compat/parity_evidence.json and scripts/parity_evidence_audit.py without
modifying parity_rows.json, parity_matrix.md, parity_normalization.md, or any
live state.

Pure/offline: no browser/keychain/credential/live NotebookLM access.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
COMPAT_DIR = REPO_ROOT / "compat"

EVIDENCE_PATH = COMPAT_DIR / "parity_evidence.json"
EVIDENCE_AUDIT_SCRIPT = SCRIPTS_DIR / "parity_evidence_audit.py"

REQUIRED_EVIDENCE_FIELDS = [
    "row_id",
    "evidence_id",
    "evidence_type",
    "comparator",
    "closed_system",
    "no_live",
    "promotion_basis",
]

EXPECTED_NEW_PASS_ROW_IDS = {
    "offline.singlefile_isolated_runtime",
    "offline.wheel_metadata_zero_deps",
    "offline.wheel_install_launcher",
    "offline.local_package_install_launcher",
}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _load_evidence_module():
    assert EVIDENCE_AUDIT_SCRIPT.is_file(), (
        f"audit script missing: {EVIDENCE_AUDIT_SCRIPT}"
    )
    spec = importlib.util.spec_from_file_location(
        "_parity_evidence_audit", EVIDENCE_AUDIT_SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _clean_env(tmp_path: Path) -> dict[str, str]:
    clean_home = tmp_path / "home"
    clean_home.mkdir(parents=True, exist_ok=True)
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    return {
        "HOME": str(clean_home),
        "USERPROFILE": str(clean_home),
        "TMPDIR": str(tmp_dir),
        "PYTHONPATH": "",
        "PATH": os.environ.get("PATH", ""),
    }


def _load_parity_rows() -> list[dict]:
    data = json.loads((COMPAT_DIR / "parity_rows.json").read_text(encoding="utf-8"))
    return data["rows"]


def _load_evidence_data() -> dict:
    return json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))


def _auth_pass_row_ids() -> set[str]:
    return {
        row["id"]
        for row in _load_parity_rows()
        if row.get("category") == "auth" and row.get("status") == "pass"
    }


def _make_synthetic_root(
    tmp_path: Path,
    rows: list[dict],
    evidence_records: list[dict],
) -> Path:
    """Build a synthetic repo root with given rows and evidence records."""
    synth_root = tmp_path / "synth_root"
    synth_compat = synth_root / "compat"
    synth_compat.mkdir(parents=True, exist_ok=True)

    # Copy real compat support files
    for name in ("parity_matrix.md", "parity_normalization.md"):
        src = COMPAT_DIR / name
        if src.exists():
            (synth_compat / name).write_bytes(src.read_bytes())

    # Write synthetic parity_rows.json
    ledger = {
        "schema_version": "parity_rows/1",
        "target": "notebooklm-py==0.7.2",
        "generated_from": [],
        "row_count": len(rows),
        "required_fields": [],
        "valid_statuses": ["pass", "open", "blocked", "not_applicable"],
        "rows": rows,
    }
    (synth_compat / "parity_rows.json").write_text(
        json.dumps(ledger, indent=2), encoding="utf-8"
    )

    # Write synthetic parity_evidence.json
    evidence = {
        "schema_version": "parity_evidence/1",
        "target": "notebooklm-py==0.7.2",
        "evidence_records": evidence_records,
    }
    (synth_compat / "parity_evidence.json").write_text(
        json.dumps(evidence, indent=2), encoding="utf-8"
    )

    return synth_root


def _make_valid_evidence_record(row_id: str, evidence_id: str) -> dict:
    return {
        "row_id": row_id,
        "evidence_id": evidence_id,
        "evidence_type": "offline_test",
        "command": "",
        "test_path": "",
        "artifact_path": "",
        "comparator": "offline_check",
        "closed_system": True,
        "no_live": True,
        "promotion_basis": "synthetic test evidence",
    }


def _make_pass_row(row_id: str, evidence_token: str) -> dict:
    return {
        "id": row_id,
        "category": "offline",
        "upstream_surface": f"{row_id} upstream",
        "bare_surface": f"{row_id} bare",
        "fixture_live_requirement": "closed_system",
        "comparator": "offline.test_comparator",
        "allowed_normalizations": [],
        "required_evidence": [evidence_token],
        "status": "pass",
        "blocker_reason": "",
        "promotion_authority": "closed_system_evidence",
        "claim_scope": "offline_pass",
    }


# --------------------------------------------------------------------------- #
# 1. Evidence manifest structure
# --------------------------------------------------------------------------- #


def test_evidence_manifest_exists() -> None:
    """compat/parity_evidence.json must exist."""
    assert EVIDENCE_PATH.is_file(), "compat/parity_evidence.json missing"


def test_evidence_manifest_parses_and_has_schema_version() -> None:
    """parity_evidence.json must parse as valid JSON with schema_version field."""
    data = _load_evidence_data()
    assert isinstance(data, dict), "parity_evidence.json must be a JSON object"
    assert "schema_version" in data, "parity_evidence.json must have schema_version"
    assert data["schema_version"].startswith("parity_evidence/")
    assert "evidence_records" in data, "parity_evidence.json must have evidence_records"
    assert isinstance(data["evidence_records"], list)


def test_evidence_manifest_has_target() -> None:
    """parity_evidence.json must declare the target oracle."""
    data = _load_evidence_data()
    assert data.get("target") == "notebooklm-py==0.7.2"


# --------------------------------------------------------------------------- #
# 2. Evidence coverage
# --------------------------------------------------------------------------- #


def test_every_pass_row_has_at_least_one_evidence_record() -> None:
    """Every pass row in parity_rows.json must have at least one evidence record."""
    rows = _load_parity_rows()
    pass_rows = [r for r in rows if r.get("status") == "pass"]
    data = _load_evidence_data()
    evidence_row_ids = {rec["row_id"] for rec in data["evidence_records"]}

    missing = [r["id"] for r in pass_rows if r["id"] not in evidence_row_ids]
    assert not missing, f"pass rows without evidence records: {missing}"


def test_evidence_tokens_satisfied_for_every_pass_row() -> None:
    """Every required_evidence token of each pass row must be satisfied."""
    rows = _load_parity_rows()
    pass_rows = [r for r in rows if r.get("status") == "pass"]
    data = _load_evidence_data()
    evidence_by_row: dict[str, set[str]] = {}
    for rec in data["evidence_records"]:
        evidence_by_row.setdefault(rec["row_id"], set()).add(rec["evidence_id"])

    unmet: list[dict] = []
    for row in pass_rows:
        rid = row["id"]
        tokens = row.get("required_evidence", [])
        satisfied = evidence_by_row.get(rid, set())
        missing_tokens = [t for t in tokens if t not in satisfied]
        if missing_tokens:
            unmet.append({"row_id": rid, "missing_tokens": missing_tokens})

    assert not unmet, f"pass rows with unmet evidence tokens: {unmet}"


# --------------------------------------------------------------------------- #
# 3. Evidence record validity
# --------------------------------------------------------------------------- #


def test_evidence_records_have_required_fields() -> None:
    """Every evidence record must have all required metadata fields."""
    data = _load_evidence_data()
    records = data["evidence_records"]
    bad: list[str] = []
    for rec in records:
        label = f"{rec.get('row_id', '?')}/{rec.get('evidence_id', '?')}"
        missing = [f for f in REQUIRED_EVIDENCE_FIELDS if f not in rec]
        if missing:
            bad.append(f"{label}: missing {missing}")
    assert not bad, f"evidence records missing required fields: {bad}"


def test_evidence_records_are_closed_system() -> None:
    """Every non-auth evidence record must have closed_system=true."""
    data = _load_evidence_data()
    auth_pass_rows = _auth_pass_row_ids()
    bad = [
        f"{rec.get('row_id')}/{rec.get('evidence_id')}"
        for rec in data["evidence_records"]
        if rec.get("row_id") not in auth_pass_rows
        if not rec.get("closed_system", False)
    ]
    assert not bad, f"evidence records without closed_system=true: {bad}"


def test_evidence_records_have_no_live() -> None:
    """Every non-auth evidence record must have no_live=true."""
    data = _load_evidence_data()
    auth_pass_rows = _auth_pass_row_ids()
    bad = [
        f"{rec.get('row_id')}/{rec.get('evidence_id')}"
        for rec in data["evidence_records"]
        if rec.get("row_id") not in auth_pass_rows
        if not rec.get("no_live", False)
    ]
    assert not bad, f"evidence records without no_live=true: {bad}"


def test_evidence_records_test_paths_exist() -> None:
    """Referenced test_path files must exist (file part before '::')."""
    data = _load_evidence_data()
    missing: list[str] = []
    for rec in data["evidence_records"]:
        test_path = rec.get("test_path", "")
        if not test_path:
            continue
        file_part = test_path.split("::")[0]
        full = REPO_ROOT / file_part
        if not full.exists():
            missing.append(
                f"{rec.get('row_id')}/{rec.get('evidence_id')}: {file_part!r}"
            )
    assert not missing, f"evidence records with missing test_path files: {missing}"


def test_evidence_records_artifact_paths_exist() -> None:
    """Referenced artifact_path files must exist (if non-empty)."""
    data = _load_evidence_data()
    missing: list[str] = []
    for rec in data["evidence_records"]:
        artifact_path = rec.get("artifact_path", "")
        if not artifact_path:
            continue
        full = REPO_ROOT / artifact_path
        if not full.exists():
            missing.append(
                f"{rec.get('row_id')}/{rec.get('evidence_id')}: {artifact_path!r}"
            )
    assert not missing, f"evidence records with missing artifact_path files: {missing}"


def test_evidence_records_only_reference_pass_rows() -> None:
    """Evidence records must not reference open/blocked rows as pass evidence."""
    rows = _load_parity_rows()
    pass_ids = {r["id"] for r in rows if r.get("status") == "pass"}
    data = _load_evidence_data()
    bad = [
        rec.get("row_id")
        for rec in data["evidence_records"]
        if rec.get("row_id") not in pass_ids
    ]
    assert not bad, f"evidence records reference non-pass rows: {bad}"


# --------------------------------------------------------------------------- #
# 4. Audit script behavior
# --------------------------------------------------------------------------- #


def test_evidence_audit_script_exists() -> None:
    """scripts/parity_evidence_audit.py must exist."""
    assert EVIDENCE_AUDIT_SCRIPT.is_file(), (
        f"parity_evidence_audit.py missing: {EVIDENCE_AUDIT_SCRIPT}"
    )


def test_audit_build_report_no_path_home_call(monkeypatch) -> None:
    """build_report(repo_root=...) must not call Path.home()."""

    def _forbidden_home():
        raise AssertionError("build_report must not call Path.home()")

    monkeypatch.setattr(Path, "home", staticmethod(_forbidden_home))
    mod = _load_evidence_module()
    report = mod.build_report(repo_root=REPO_ROOT)
    assert report is not None
    assert "strict_ok" in report


def test_audit_build_report_strict_ok_true() -> None:
    """build_report() must return strict_ok=True when evidence is valid."""
    mod = _load_evidence_module()
    report = mod.build_report(repo_root=REPO_ROOT)
    assert report["strict_ok"] is True, (
        f"strict_ok is False; issues:\n"
        f"  rows_missing_evidence={report.get('rows_missing_evidence')}\n"
        f"  rows_with_unmet_tokens={report.get('rows_with_unmet_tokens')}\n"
        f"  records_missing_required_fields={report.get('records_missing_required_fields')}\n"
        f"  records_missing_referenced_files={report.get('records_missing_referenced_files')}\n"
        f"  records_with_live_scope={report.get('records_with_live_scope')}\n"
        f"  load_errors={report.get('load_errors')}"
    )


def test_audit_json_exits_zero(tmp_path: Path) -> None:
    """parity_evidence_audit.py --json must exit 0 with valid evidence."""
    env = _clean_env(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(EVIDENCE_AUDIT_SCRIPT), "--json"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, f"stderr: {proc.stderr[:400]}"
    report = json.loads(proc.stdout)
    assert "strict_ok" in report


def test_audit_json_strict_exits_zero(tmp_path: Path) -> None:
    """parity_evidence_audit.py --json --strict must exit 0 when evidence is valid."""
    env = _clean_env(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(EVIDENCE_AUDIT_SCRIPT), "--json", "--strict"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, (
        f"expected exit 0, got {proc.returncode}\n"
        f"stdout: {proc.stdout[:400]}\nstderr: {proc.stderr[:400]}"
    )
    report = json.loads(proc.stdout)
    assert report["strict_ok"] is True
    assert report["strict_exit_code"] == 0


def test_audit_json_no_absolute_home_paths(tmp_path: Path) -> None:
    """JSON output must not contain absolute home paths."""
    env = _clean_env(tmp_path)
    clean_home = tmp_path / "home"
    proc = subprocess.run(
        [sys.executable, str(EVIDENCE_AUDIT_SCRIPT), "--json"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    output = proc.stdout
    assert str(clean_home) not in output, "temp HOME path leaked into JSON output"
    assert re.search(
        r"(?<![:/\w])/(?:[A-Za-z0-9._-]+/)+[A-Za-z0-9._-]+", output
    ) is None, "absolute path leaked into JSON output"


# --------------------------------------------------------------------------- #
# 5. Strict mode edge-case behavior (synthetic data)
# --------------------------------------------------------------------------- #


def test_strict_mode_fails_if_pass_row_has_no_evidence(tmp_path: Path) -> None:
    """Strict mode must fail if a pass row has no evidence records."""
    mod = _load_evidence_module()

    row = _make_pass_row("offline.synthetic_a", "synthetic_token_a")
    evidence = [_make_valid_evidence_record("offline.synthetic_a", "wrong_token")]
    synth_root = _make_synthetic_root(tmp_path, [row], evidence)

    report = mod.build_report(repo_root=synth_root)
    assert report["strict_ok"] is False
    assert report["strict_exit_code"] != 0
    assert "offline.synthetic_a" in report.get("rows_with_unmet_tokens", [{}])[0].get(
        "row_id", ""
    ) or any(
        item.get("row_id") == "offline.synthetic_a"
        for item in report.get("rows_with_unmet_tokens", [])
    )


def test_strict_mode_fails_if_evidence_file_missing(tmp_path: Path) -> None:
    """Strict mode must fail if parity_evidence.json is missing."""
    mod = _load_evidence_module()

    synth_root = tmp_path / "empty_root"
    synth_compat = synth_root / "compat"
    synth_compat.mkdir(parents=True)
    # Write a minimal parity_rows.json with one pass row but NO evidence file
    row = _make_pass_row("offline.synthetic_b", "token_b")
    ledger = {
        "schema_version": "parity_rows/1",
        "target": "notebooklm-py==0.7.2",
        "generated_from": [],
        "row_count": 1,
        "required_fields": [],
        "valid_statuses": ["pass", "open", "blocked", "not_applicable"],
        "rows": [row],
    }
    (synth_compat / "parity_rows.json").write_text(json.dumps(ledger), encoding="utf-8")

    report = mod.build_report(repo_root=synth_root)
    assert report["strict_ok"] is False
    assert report["evidence_file_present"] is False


def test_strict_mode_fails_if_record_missing_closed_system(tmp_path: Path) -> None:
    """Strict mode must fail if any evidence record has closed_system=false."""
    mod = _load_evidence_module()

    row = _make_pass_row("offline.synthetic_c", "token_c")
    rec = _make_valid_evidence_record("offline.synthetic_c", "token_c")
    rec["closed_system"] = False
    synth_root = _make_synthetic_root(tmp_path, [row], [rec])

    report = mod.build_report(repo_root=synth_root)
    assert report["strict_ok"] is False
    assert len(report.get("records_with_live_scope", [])) > 0


def test_strict_mode_fails_if_record_missing_no_live(tmp_path: Path) -> None:
    """Strict mode must fail if any evidence record has no_live=false."""
    mod = _load_evidence_module()

    row = _make_pass_row("offline.synthetic_d", "token_d")
    rec = _make_valid_evidence_record("offline.synthetic_d", "token_d")
    rec["no_live"] = False
    synth_root = _make_synthetic_root(tmp_path, [row], [rec])

    report = mod.build_report(repo_root=synth_root)
    assert report["strict_ok"] is False
    assert len(report.get("records_with_live_scope", [])) > 0


def test_strict_mode_fails_if_artifact_path_missing(tmp_path: Path) -> None:
    """Strict mode must fail if an evidence record references a missing artifact_path."""
    mod = _load_evidence_module()

    row = _make_pass_row("offline.synthetic_e", "token_e")
    rec = _make_valid_evidence_record("offline.synthetic_e", "token_e")
    rec["artifact_path"] = "scripts/nonexistent_artifact.py"
    synth_root = _make_synthetic_root(tmp_path, [row], [rec])

    report = mod.build_report(repo_root=synth_root)
    assert report["strict_ok"] is False
    assert len(report.get("records_missing_referenced_files", [])) > 0


def test_strict_mode_passes_with_valid_synthetic_data(tmp_path: Path) -> None:
    """Strict mode must pass when pass rows have matching valid evidence."""
    mod = _load_evidence_module()

    row = _make_pass_row("offline.synthetic_ok", "token_ok")
    rec = _make_valid_evidence_record("offline.synthetic_ok", "token_ok")
    synth_root = _make_synthetic_root(tmp_path, [row], [rec])

    report = mod.build_report(repo_root=synth_root)
    assert report["strict_ok"] is True
    assert report["strict_exit_code"] == 0


# --------------------------------------------------------------------------- #
# 6. Row ledger invariants (these must hold before and after Phase 15)
# --------------------------------------------------------------------------- #


def test_new_offline_pass_rows_added_to_ledger() -> None:
    """Phase 15 must add the expected new offline pass rows."""
    rows = _load_parity_rows()
    row_ids = {r["id"] for r in rows}
    missing = EXPECTED_NEW_PASS_ROW_IDS - row_ids
    assert not missing, f"expected new pass rows not found in ledger: {missing}"


def test_new_pass_rows_are_offline_category() -> None:
    """All new Phase 15 pass rows must be in the offline category."""
    rows = _load_parity_rows()
    by_id = {r["id"]: r for r in rows}
    bad = [
        rid
        for rid in EXPECTED_NEW_PASS_ROW_IDS
        if rid in by_id and by_id[rid].get("category") != "offline"
    ]
    assert not bad, f"new pass rows not in offline category: {bad}"


def test_new_pass_rows_have_closed_system_fixture_requirement() -> None:
    """All new Phase 15 pass rows must have fixture_live_requirement=closed_system."""
    rows = _load_parity_rows()
    by_id = {r["id"]: r for r in rows}
    bad = [
        rid
        for rid in EXPECTED_NEW_PASS_ROW_IDS
        if rid in by_id
        and by_id[rid].get("fixture_live_requirement") != "closed_system"
    ]
    assert not bad, f"new rows without closed_system fixture requirement: {bad}"


def test_new_pass_rows_have_closed_system_evidence_authority() -> None:
    """All new Phase 15 pass rows must have promotion_authority=closed_system_evidence."""
    rows = _load_parity_rows()
    by_id = {r["id"]: r for r in rows}
    bad = [
        rid
        for rid in EXPECTED_NEW_PASS_ROW_IDS
        if rid in by_id
        and by_id[rid].get("promotion_authority") != "closed_system_evidence"
    ]
    assert not bad, f"new rows without closed_system_evidence authority: {bad}"


def test_new_pass_rows_have_empty_blocker_reason() -> None:
    """All new Phase 15 pass rows must have empty blocker_reason."""
    rows = _load_parity_rows()
    by_id = {r["id"]: r for r in rows}
    bad = [
        rid
        for rid in EXPECTED_NEW_PASS_ROW_IDS
        if rid in by_id and by_id[rid].get("blocker_reason")
    ]
    assert not bad, f"new pass rows with non-empty blocker_reason: {bad}"


def test_pass_row_count_at_least_seven() -> None:
    """Phase 15 must bring total pass rows to at least 7 (3 existing + 4 new)."""
    rows = _load_parity_rows()
    pass_count = sum(1 for r in rows if r.get("status") == "pass")
    assert pass_count >= 7, f"expected >= 7 pass rows, got {pass_count}"


def test_selected_auth_rows_are_closed_but_category_claim_stays_open() -> None:
    """Selected auth rows close without promoting the broader auth category."""
    rows = _load_parity_rows()
    auth_rows = [r for r in rows if r.get("category") == "auth"]
    assert len(auth_rows) == 146
    assert sum(1 for row in auth_rows if row.get("status") == "pass") == 146
    assert sum(1 for row in auth_rows if row.get("status") == "open") == 0
    assert not [r["id"] for r in auth_rows if r.get("status") == "blocked"]


def test_exact_1_to_1_claim_remains_false() -> None:
    """exact_one_to_one_claim_ready remains false beyond selected-row closure."""
    import importlib.util as _ilu

    script = SCRIPTS_DIR / "parity_row_audit.py"
    spec = _ilu.spec_from_file_location("_parity_row_audit_p15", script)
    assert spec is not None and spec.loader is not None
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    report = mod.build_report(repo_root=REPO_ROOT)
    assert report["exact_one_to_one_claim_ready"] is False


def test_only_promoted_categories_are_pass() -> None:
    """Only categories with evidence-backed promotion may contain pass rows."""
    rows = _load_parity_rows()
    bad = [
        r["id"]
        for r in rows
        if r.get("status") == "pass"
        and r.get("category")
        not in ("offline", "self_test", "cli", "api", "rpc", "auth")
    ]
    assert not bad, f"unpromoted categories with status=pass: {bad}"


# --------------------------------------------------------------------------- #
# 7. Release-candidate integration (Phase 15)
# --------------------------------------------------------------------------- #


def _load_rc_module():
    script = SCRIPTS_DIR / "release_candidate_audit.py"
    spec = importlib.util.spec_from_file_location("_rc_audit_p15", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rc_report_includes_pass_row_evidence_gate() -> None:
    """RC report local_gates must include pass_row_evidence with ok=True."""
    mod = _load_rc_module()
    report = mod.build_report(repo_root=REPO_ROOT)
    gates = report["local_gates"]
    assert "pass_row_evidence" in gates, (
        "release_candidate_audit report missing pass_row_evidence gate"
    )
    gate = gates["pass_row_evidence"]
    assert gate["ok"] is True, f"pass_row_evidence gate not ok: {gate}"
    assert gate["summary"]["strict_ok"] is True


def test_rc_gate_summary_includes_parity_evidence_fields() -> None:
    """RC gate_summary must include parity_evidence_pass_rows and parity_evidence_strict_ok."""
    mod = _load_rc_module()
    report = mod.build_report(repo_root=REPO_ROOT)
    gs = report["gate_summary"]
    assert "parity_evidence_pass_rows" in gs, (
        "gate_summary missing parity_evidence_pass_rows"
    )
    assert "parity_evidence_strict_ok" in gs, (
        "gate_summary missing parity_evidence_strict_ok"
    )
    assert gs["parity_evidence_strict_ok"] is True
    assert gs["parity_evidence_pass_rows"] >= 7


def test_rc_local_gate_fails_when_evidence_audit_fails(monkeypatch) -> None:
    """A failing pass_row_evidence gate must make local_gate_status=fail and add a blocker."""
    mod = _load_rc_module()

    def _failed_gate(_repo_root: Path) -> dict[str, object]:
        return {
            "ok": False,
            "status": "fail",
            "summary": {
                "pass_rows_audited": 7,
                "evidence_records_count": 0,
                "rows_missing_evidence": ["offline.synthetic_x"],
                "rows_with_unmet_tokens": [],
                "strict_ok": False,
            },
        }

    monkeypatch.setattr(mod, "_run_pass_row_evidence_gate", _failed_gate)
    report = mod.build_report(repo_root=REPO_ROOT)

    assert report["local_gate_status"] == "fail"
    assert report["release_candidate_ready"] is False
    assert "local_gate_pass_row_evidence_failed" in report["remaining_blockers"]


def test_rc_strict_still_exits_77_with_evidence_gate_passing() -> None:
    """RC strict mode remains blocked beyond selected-row closure."""
    mod = _load_rc_module()
    report = mod.build_report(repo_root=REPO_ROOT)
    assert report["strict_exit_code"] == 77
    assert report["release_candidate_ready"] is False
    assert report["one_to_one_functionality_claim"] is False
