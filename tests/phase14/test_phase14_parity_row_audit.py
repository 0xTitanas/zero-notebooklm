"""Phase 14A parity row-level audit tests.

Tests scripts/parity_row_audit.py without modifying parity_matrix.md,
parity_rows.json, parity_normalization.md, auth_matrix.json, or any live state.
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
VALID_STATUSES = {"pass", "open", "blocked", "not_applicable"}

REQUIRED_NORM_FAMILIES = {
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

MATRIX_CATEGORIES = ("cli", "api", "auth", "rpc", "offline", "self_test")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _load_module():
    script = SCRIPTS_DIR / "parity_row_audit.py"
    assert script.is_file(), f"audit script missing: {script.name}"
    spec = importlib.util.spec_from_file_location("_parity_row_audit", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _clean_env(tmp_path: Path) -> dict[str, str]:
    clean_home = tmp_path / "home"
    clean_home.mkdir()
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    return {
        "HOME": str(clean_home),
        "USERPROFILE": str(clean_home),
        "TMPDIR": str(tmp_dir),
        "PYTHONPATH": "",
        "PATH": os.environ.get("PATH", ""),
    }


def _auth_status_counts(rows: list[dict]) -> dict[str, int]:
    auth_rows = [row for row in rows if row.get("category") == "auth"]
    return {
        state: sum(1 for row in auth_rows if row.get("status") == state)
        for state in ("open", "pass", "blocked")
    }


def _make_minimal_row(rid: str, status: str = "pass") -> dict:
    return {
        "id": rid,
        "category": "offline",
        "upstream_surface": f"{rid} upstream",
        "bare_surface": f"{rid} bare",
        "fixture_live_requirement": "closed_system",
        "comparator": "offline.import_origin_audit",
        "allowed_normalizations": [],
        "required_evidence": ["import_origin_audit_pass"],
        "status": status,
        "blocker_reason": "",
        "promotion_authority": "closed_system_evidence",
        "claim_scope": "offline_pass",
    }


def _write_synthetic_ledger(tmp_path: Path, rows: list[dict]) -> Path:
    ledger = {
        "schema_version": "parity_rows/1",
        "target": "notebooklm-py==0.7.2",
        "generated_from": [],
        "row_count": len(rows),
        "required_fields": REQUIRED_FIELDS,
        "valid_statuses": list(VALID_STATUSES),
        "rows": rows,
    }
    p = tmp_path / "parity_rows.json"
    p.write_text(json.dumps(ledger), encoding="utf-8")
    return p


def _make_all_matrix_category_pass_rows() -> list[dict]:
    """One valid pass row per matrix category; guarantees categories_without_rows == []."""
    rows = []
    for cat in MATRIX_CATEGORIES:
        rid = f"{cat}.synthetic.base"
        rows.append(
            {
                "id": rid,
                "category": cat,
                "upstream_surface": f"{rid} upstream",
                "bare_surface": f"{rid} bare",
                "fixture_live_requirement": "closed_system",
                "comparator": f"{cat}.synthetic_comparator",
                "allowed_normalizations": [],
                "required_evidence": [f"{cat}_evidence_pass"],
                "status": "pass",
                "blocker_reason": "",
                "promotion_authority": "closed_system_evidence",
                "claim_scope": f"{cat}_pass",
            }
        )
    return rows


# --------------------------------------------------------------------------- #
# 1. build_report() is pure / does not call Path.home()
# --------------------------------------------------------------------------- #


def test_build_report_no_path_home_call(repo_root: Path, monkeypatch) -> None:
    """build_report(repo_root=...) must not call Path.home()."""

    def _forbidden_home():
        raise AssertionError("build_report must not call Path.home()")

    monkeypatch.setattr(Path, "home", staticmethod(_forbidden_home))

    mod = _load_module()
    report = mod.build_report(repo_root=repo_root)
    assert report is not None
    assert "exact_one_to_one_claim_ready" in report


# --------------------------------------------------------------------------- #
# 2. Required files exist and parse cleanly
# --------------------------------------------------------------------------- #


def test_required_files_exist_and_parse(repo_root: Path) -> None:
    """parity_rows.json, parity_matrix.md, and parity_normalization.md must exist and parse."""
    rows_path = repo_root / "compat" / "parity_rows.json"
    matrix_path = repo_root / "compat" / "parity_matrix.md"
    norm_path = repo_root / "compat" / "parity_normalization.md"

    assert rows_path.is_file(), "compat/parity_rows.json missing"
    assert matrix_path.is_file(), "compat/parity_matrix.md missing"
    assert norm_path.is_file(), "compat/parity_normalization.md missing"

    data = json.loads(rows_path.read_text(encoding="utf-8"))
    assert "rows" in data, "parity_rows.json must have 'rows' key"
    assert isinstance(data["rows"], list), "'rows' must be a list"
    assert len(data["rows"]) > 0, "'rows' must not be empty"

    md_text = matrix_path.read_text(encoding="utf-8")
    assert "notebooklm" in md_text.lower()

    norm_text = norm_path.read_text(encoding="utf-8")
    assert "normalization" in norm_text.lower()


# --------------------------------------------------------------------------- #
# 3. Every row has all required fields
# --------------------------------------------------------------------------- #


def test_every_row_has_required_fields(repo_root: Path) -> None:
    """Report must show zero missing-required-field violations."""
    mod = _load_module()
    report = mod.build_report(repo_root=repo_root)
    assert report["missing_required_fields"] == [], (
        f"rows missing fields: {report['missing_required_fields'][:5]}"
    )


def test_every_row_status_is_valid(repo_root: Path) -> None:
    """Every row status must be one of: pass, open, blocked, not_applicable."""
    mod = _load_module()
    report = mod.build_report(repo_root=repo_root)
    assert report["invalid_status_rows"] == [], (
        f"rows with invalid status: {report['invalid_status_rows'][:5]}"
    )


# --------------------------------------------------------------------------- #
# 4. Row IDs are unique and stable-looking
# --------------------------------------------------------------------------- #


def test_row_ids_unique(repo_root: Path) -> None:
    """Report must show zero duplicate row IDs."""
    mod = _load_module()
    report = mod.build_report(repo_root=repo_root)
    assert report["duplicate_row_ids"] == [], (
        f"duplicate IDs: {report['duplicate_row_ids'][:5]}"
    )


def test_row_ids_stable_looking() -> None:
    """Every row ID must look stable: lowercase, dot/underscore-separated, no spaces."""
    data = json.loads((COMPAT_DIR / "parity_rows.json").read_text(encoding="utf-8"))
    bad = [
        r["id"]
        for r in data["rows"]
        if not re.match(r"^[a-z0-9][a-z0-9._\-]+$", r["id"])
    ]
    assert not bad, f"unstable-looking IDs: {bad[:5]}"


# --------------------------------------------------------------------------- #
# 5. Every parity_matrix.md category has at least one row
# --------------------------------------------------------------------------- #


def test_every_matrix_category_has_rows(repo_root: Path) -> None:
    """categories_without_rows must be empty."""
    mod = _load_module()
    report = mod.build_report(repo_root=repo_root)
    assert report["categories_without_rows"] == [], (
        f"matrix categories with no rows: {report['categories_without_rows']}"
    )


def test_matrix_categories_all_found(repo_root: Path) -> None:
    """Audit must detect all six matrix categories in parity_matrix.md."""
    mod = _load_module()
    report = mod.build_report(repo_root=repo_root)
    found = set(report["matrix_categories_found"])
    for cat in MATRIX_CATEGORIES:
        assert cat in found, f"matrix category '{cat}' not found in parity_matrix.md"


# --------------------------------------------------------------------------- #
# 6. CLI leaf row count matches 90 pinned leaves
# --------------------------------------------------------------------------- #


def test_cli_leaf_row_count() -> None:
    """parity_rows.json must have exactly 90 cli-category rows."""
    data = json.loads((COMPAT_DIR / "parity_rows.json").read_text(encoding="utf-8"))
    cli_rows = [r for r in data["rows"] if r.get("category") == "cli"]
    assert len(cli_rows) == 90, f"expected 90 cli rows, got {len(cli_rows)}"


# --------------------------------------------------------------------------- #
# 7. Auth row count matches the 146-row selected compatibility profile
# --------------------------------------------------------------------------- #


def test_auth_row_count() -> None:
    """parity_rows.json must have exactly 146 in-profile auth rows."""
    data = json.loads((COMPAT_DIR / "parity_rows.json").read_text(encoding="utf-8"))
    auth_rows = [r for r in data["rows"] if r.get("category") == "auth"]
    assert len(auth_rows) == 146, f"expected 146 auth rows, got {len(auth_rows)}"


# --------------------------------------------------------------------------- #
# 8. Only offline/self_test rows are pass; exact 1:1 claim remains false
# --------------------------------------------------------------------------- #


def test_only_promoted_categories_are_pass() -> None:
    """Only evidence-promoted categories may have status='pass'."""
    data = json.loads((COMPAT_DIR / "parity_rows.json").read_text(encoding="utf-8"))
    bad = [
        r["id"]
        for r in data["rows"]
        if r.get("status") == "pass"
        and r.get("category")
        not in ("offline", "self_test", "cli", "api", "rpc", "auth")
    ]
    assert not bad, f"unpromoted categories with status=pass: {bad[:5]}"


def test_exact_one_to_one_claim_is_false(repo_root: Path) -> None:
    """Explicit current-release exclusions keep universal exact 1:1 false."""
    mod = _load_module()
    report = mod.build_report(repo_root=repo_root)
    assert report["exact_one_to_one_claim_ready"] is False
    assert report["explicit_profile_exclusion_count"] == 49


def test_missing_auth_matrix_fails_closed(tmp_path: Path, repo_root: Path) -> None:
    """A missing exclusion ledger must never enable a universal exact claim."""
    mod = _load_module()
    ledger_path = _write_synthetic_ledger(
        tmp_path, _make_all_matrix_category_pass_rows()
    )
    report = mod.build_report(
        repo_root=_synthetic_repo_root(tmp_path, ledger_path, repo_root)
    )
    assert report["explicit_profile_exclusion_count"] is None
    assert report["exact_one_to_one_claim_ready"] is False
    assert "profile_exclusion_state_unavailable" in report["remaining_blockers"]


def test_auth_rows_are_closed_and_cli_rpc_api_are_promoted() -> None:
    """Selected-profile auth rows and CLI/API/RPC rows are promoted."""
    data = json.loads((COMPAT_DIR / "parity_rows.json").read_text(encoding="utf-8"))
    auth_rows = [r for r in data["rows"] if r.get("category") == "auth"]
    assert len(auth_rows) == 146
    counts = _auth_status_counts(data["rows"])
    assert counts["pass"] == 146
    assert counts["open"] == 0
    assert counts["blocked"] == 0
    for cat in ("cli", "api", "rpc"):
        cat_rows = [r for r in data["rows"] if r.get("category") == cat]
        assert cat_rows, f"no rows found for category {cat!r}"
        non_pass = [r["id"] for r in cat_rows if r.get("status") != "pass"]
        assert not non_pass, f"{cat} rows not in 'pass' state: {non_pass[:3]}"


# --------------------------------------------------------------------------- #
# 9. Open/blocked rows prevent exact_one_to_one_claim_ready
# --------------------------------------------------------------------------- #


def test_single_open_row_blocks_claim(tmp_path: Path, repo_root: Path) -> None:
    """A single 'open' row among otherwise-pass rows must block the 1:1 claim."""
    mod = _load_module()

    rows = [_make_minimal_row(f"offline.synthetic_{i}", "pass") for i in range(5)]
    rows.append(_make_minimal_row("offline.synthetic_open", "open"))
    ledger_path = _write_synthetic_ledger(tmp_path, rows)

    report = mod.build_report(
        repo_root=_synthetic_repo_root(tmp_path, ledger_path, repo_root)
    )
    assert report["exact_one_to_one_claim_ready"] is False
    assert report["strict_exit_code"] == 77


def test_single_blocked_row_blocks_claim(tmp_path: Path, repo_root: Path) -> None:
    """A single 'blocked' row among otherwise-pass rows must block the 1:1 claim."""
    mod = _load_module()

    rows = [_make_minimal_row(f"offline.synthetic_{i}", "pass") for i in range(5)]
    rows.append(_make_minimal_row("offline.synthetic_blocked", "blocked"))
    ledger_path = _write_synthetic_ledger(tmp_path, rows)

    report = mod.build_report(
        repo_root=_synthetic_repo_root(tmp_path, ledger_path, repo_root)
    )
    assert report["exact_one_to_one_claim_ready"] is False
    assert report["strict_exit_code"] == 77


# --------------------------------------------------------------------------- #
# 10. Missing comparator / evidence / promotion authority fails audit
# --------------------------------------------------------------------------- #


def test_row_missing_comparator_flagged(tmp_path: Path, repo_root: Path) -> None:
    """Rows with empty comparator must appear in rows_missing_comparator."""
    mod = _load_module()

    row = _make_minimal_row("offline.no_comparator", "pass")
    row["comparator"] = ""
    ledger_path = _write_synthetic_ledger(tmp_path, [row])

    report = mod.build_report(
        repo_root=_synthetic_repo_root(tmp_path, ledger_path, repo_root)
    )
    assert "offline.no_comparator" in report["rows_missing_comparator"]


def test_row_missing_required_evidence_flagged(tmp_path: Path, repo_root: Path) -> None:
    """Rows with empty required_evidence must appear in rows_missing_required_evidence."""
    mod = _load_module()

    row = _make_minimal_row("offline.no_evidence", "pass")
    row["required_evidence"] = []
    ledger_path = _write_synthetic_ledger(tmp_path, [row])

    report = mod.build_report(
        repo_root=_synthetic_repo_root(tmp_path, ledger_path, repo_root)
    )
    assert "offline.no_evidence" in report["rows_missing_required_evidence"]


def test_row_missing_promotion_authority_flagged(
    tmp_path: Path, repo_root: Path
) -> None:
    """Rows with empty promotion_authority must appear in rows_missing_promotion_authority."""
    mod = _load_module()

    row = _make_minimal_row("offline.no_authority", "pass")
    row["promotion_authority"] = ""
    ledger_path = _write_synthetic_ledger(tmp_path, [row])

    report = mod.build_report(
        repo_root=_synthetic_repo_root(tmp_path, ledger_path, repo_root)
    )
    assert "offline.no_authority" in report["rows_missing_promotion_authority"]


def test_missing_required_field_flagged(tmp_path: Path, repo_root: Path) -> None:
    """Rows with a missing required field must appear in missing_required_fields."""
    mod = _load_module()

    row = _make_minimal_row("offline.no_comparator_field", "pass")
    del row["comparator"]
    ledger_path = _write_synthetic_ledger(tmp_path, [row])

    report = mod.build_report(
        repo_root=_synthetic_repo_root(tmp_path, ledger_path, repo_root)
    )
    assert any(
        "offline.no_comparator_field" in item
        for item in report["missing_required_fields"]
    ), f"missing_required_fields: {report['missing_required_fields']}"


def test_empty_comparator_blocks_claim(tmp_path: Path, repo_root: Path) -> None:
    """Pass rows with empty comparator must block exact 1:1 claim and exit 77.

    All six matrix categories have a valid pass row so categories_without_rows
    cannot confound the result — the empty comparator is the sole blocker.
    """
    mod = _load_module()

    target_id = "cli.synthetic.target"
    rows = _make_all_matrix_category_pass_rows()
    rows.append(
        {
            "id": target_id,
            "category": "cli",
            "upstream_surface": "cli.synthetic.target upstream",
            "bare_surface": "cli.synthetic.target bare",
            "fixture_live_requirement": "closed_system",
            "comparator": "",
            "allowed_normalizations": [],
            "required_evidence": ["cli_evidence_pass"],
            "status": "pass",
            "blocker_reason": "",
            "promotion_authority": "closed_system_evidence",
            "claim_scope": "cli_pass",
        }
    )
    ledger_path = _write_synthetic_ledger(tmp_path, rows)

    report = mod.build_report(
        repo_root=_synthetic_repo_root(tmp_path, ledger_path, repo_root)
    )
    assert target_id in report["rows_missing_comparator"]
    assert report["categories_without_rows"] == []
    assert not any(
        b.startswith("category_has_no_rows_") for b in report["remaining_blockers"]
    )
    assert report["exact_one_to_one_claim_ready"] is False
    assert report["strict_exit_code"] == 77


def test_empty_required_evidence_blocks_claim(tmp_path: Path, repo_root: Path) -> None:
    """Pass rows with empty required_evidence must block exact 1:1 claim and exit 77.

    All six matrix categories have a valid pass row so categories_without_rows
    cannot confound the result — the empty required_evidence is the sole blocker.
    """
    mod = _load_module()

    target_id = "cli.synthetic.target"
    rows = _make_all_matrix_category_pass_rows()
    rows.append(
        {
            "id": target_id,
            "category": "cli",
            "upstream_surface": "cli.synthetic.target upstream",
            "bare_surface": "cli.synthetic.target bare",
            "fixture_live_requirement": "closed_system",
            "comparator": "cli.synthetic_comparator",
            "allowed_normalizations": [],
            "required_evidence": [],
            "status": "pass",
            "blocker_reason": "",
            "promotion_authority": "closed_system_evidence",
            "claim_scope": "cli_pass",
        }
    )
    ledger_path = _write_synthetic_ledger(tmp_path, rows)

    report = mod.build_report(
        repo_root=_synthetic_repo_root(tmp_path, ledger_path, repo_root)
    )
    assert target_id in report["rows_missing_required_evidence"]
    assert report["categories_without_rows"] == []
    assert not any(
        b.startswith("category_has_no_rows_") for b in report["remaining_blockers"]
    )
    assert report["exact_one_to_one_claim_ready"] is False
    assert report["strict_exit_code"] == 77


def test_empty_promotion_authority_blocks_claim(
    tmp_path: Path, repo_root: Path
) -> None:
    """Pass rows with empty promotion_authority must block exact 1:1 claim and exit 77.

    All six matrix categories have a valid pass row so categories_without_rows
    cannot confound the result — the empty promotion_authority is the sole blocker.
    """
    mod = _load_module()

    target_id = "cli.synthetic.target"
    rows = _make_all_matrix_category_pass_rows()
    rows.append(
        {
            "id": target_id,
            "category": "cli",
            "upstream_surface": "cli.synthetic.target upstream",
            "bare_surface": "cli.synthetic.target bare",
            "fixture_live_requirement": "closed_system",
            "comparator": "cli.synthetic_comparator",
            "allowed_normalizations": [],
            "required_evidence": ["cli_evidence_pass"],
            "status": "pass",
            "blocker_reason": "",
            "promotion_authority": "",
            "claim_scope": "cli_pass",
        }
    )
    ledger_path = _write_synthetic_ledger(tmp_path, rows)

    report = mod.build_report(
        repo_root=_synthetic_repo_root(tmp_path, ledger_path, repo_root)
    )
    assert target_id in report["rows_missing_promotion_authority"]
    assert report["categories_without_rows"] == []
    assert not any(
        b.startswith("category_has_no_rows_") for b in report["remaining_blockers"]
    )
    assert report["exact_one_to_one_claim_ready"] is False
    assert report["strict_exit_code"] == 77


# --------------------------------------------------------------------------- #
# 11. Normalization spec contains all required rule families
# --------------------------------------------------------------------------- #


def test_normalization_spec_present(repo_root: Path) -> None:
    """normalization_spec_present must be True."""
    mod = _load_module()
    report = mod.build_report(repo_root=repo_root)
    assert report["normalization_spec_present"] is True


def test_normalization_spec_has_all_required_families(repo_root: Path) -> None:
    """missing_normalization_rules must be empty."""
    mod = _load_module()
    report = mod.build_report(repo_root=repo_root)
    missing = report.get("missing_normalization_rules", [])
    assert missing == [], f"missing norm families: {missing}"


def test_normalization_rules_include_all_families(repo_root: Path) -> None:
    """normalization_rules list must contain all required family keys."""
    mod = _load_module()
    report = mod.build_report(repo_root=repo_root)
    found = set(report.get("normalization_rules", []))
    missing = REQUIRED_NORM_FAMILIES - found
    assert not missing, f"missing rule families in spec: {sorted(missing)}"


# --------------------------------------------------------------------------- #
# 11b. Unknown normalization keys are flagged; real ledger is clean
# --------------------------------------------------------------------------- #


def test_row_with_unknown_normalization_flagged(
    tmp_path: Path, repo_root: Path
) -> None:
    """A row whose allowed_normalizations references an unknown key must be reported."""
    mod = _load_module()

    row = _make_minimal_row("offline.unknown_norm", "pass")
    row["allowed_normalizations"] = ["unknown_normalization"]
    ledger_path = _write_synthetic_ledger(tmp_path, [row])

    report = mod.build_report(
        repo_root=_synthetic_repo_root(tmp_path, ledger_path, repo_root)
    )
    unknown = report.get("rows_with_unknown_normalizations", [])
    assert any(
        entry.get("id") == "offline.unknown_norm"
        and "unknown_normalization" in entry.get("unknown_normalizations", [])
        for entry in unknown
    ), f"rows_with_unknown_normalizations: {unknown}"
    assert "rows_with_unknown_normalizations" in report["remaining_blockers"], (
        f"blockers: {report['remaining_blockers']}"
    )


def test_no_unknown_normalizations_in_real_ledger(repo_root: Path) -> None:
    """Real parity_rows.json must not reference any undocumented normalization keys."""
    mod = _load_module()
    report = mod.build_report(repo_root=repo_root)
    unknown = report.get("rows_with_unknown_normalizations", [])
    assert unknown == [], f"rows with unknown normalizations: {unknown}"


# --------------------------------------------------------------------------- #
# 12. CLI --json --strict exits 77 while blocked; leaks no temp HOME path
# --------------------------------------------------------------------------- #


def test_cli_json_strict_exits_77(tmp_path: Path) -> None:
    """CLI --json --strict must exit 77 while exact 1:1 claim is blocked."""
    env = _clean_env(tmp_path)

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "parity_row_audit.py"),
            "--json",
            "--strict",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 77, (
        f"expected exit 77, got {proc.returncode}\nstderr: {proc.stderr[:300]}"
    )
    report = json.loads(proc.stdout)
    assert report["strict_exit_code"] == 77
    assert report["exact_one_to_one_claim_ready"] is False


def test_cli_json_strict_no_temp_home_path(tmp_path: Path) -> None:
    """JSON output must not contain the temporary HOME path."""
    env = _clean_env(tmp_path)
    clean_home = tmp_path / "home"

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "parity_row_audit.py"),
            "--json",
            "--strict",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    output = proc.stdout
    assert str(clean_home) not in output, "temp HOME path leaked into JSON output"


def test_cli_json_no_strict_exits_zero(tmp_path: Path) -> None:
    """CLI --json (without --strict) must exit 0 even when blocked."""
    env = _clean_env(tmp_path)

    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "parity_row_audit.py"), "--json"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, f"stderr: {proc.stderr[:300]}"
    report = json.loads(proc.stdout)
    assert "exact_one_to_one_claim_ready" in report


# --------------------------------------------------------------------------- #
# 13. Human output says blocked and leaks no absolute home paths
# --------------------------------------------------------------------------- #


def test_human_output_says_blocked(tmp_path: Path) -> None:
    """Human output must say 'blocked' while exact 1:1 claim is not ready."""
    env = _clean_env(tmp_path)

    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "parity_row_audit.py")],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, f"stderr: {proc.stderr[:300]}"
    output = proc.stdout
    assert "blocked" in output.lower(), "human output must contain 'blocked'"


def test_human_output_no_absolute_paths(tmp_path: Path) -> None:
    """Human output must not leak absolute home paths."""
    env = _clean_env(tmp_path)

    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "parity_row_audit.py")],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, f"stderr: {proc.stderr[:300]}"
    output = proc.stdout
    assert re.search(
        r"(?<![:/\w])/(?:[A-Za-z0-9._-]+/)+[A-Za-z0-9._-]+", output
    ) is None, "absolute path leaked into human output"


# --------------------------------------------------------------------------- #
# Bonus: strict exit code field consistency
# --------------------------------------------------------------------------- #


def test_strict_exit_code_matches_claim_ready(repo_root: Path) -> None:
    """strict_exit_code must be 77 iff exact_one_to_one_claim_ready is False."""
    mod = _load_module()
    report = mod.build_report(repo_root=repo_root)
    if report["exact_one_to_one_claim_ready"]:
        assert report["strict_exit_code"] == 0
    else:
        assert report["strict_exit_code"] == 77


def test_remaining_blockers_non_empty(repo_root: Path) -> None:
    """remaining_blockers must be non-empty while open rows exist."""
    mod = _load_module()
    report = mod.build_report(repo_root=repo_root)
    assert len(report["remaining_blockers"]) > 0


# --------------------------------------------------------------------------- #
# Helpers for synthetic repo roots
# --------------------------------------------------------------------------- #


def _synthetic_repo_root(
    tmp_path: Path, ledger_path: Path, real_repo_root: Path
) -> Path:
    """Build a synthetic repo root that uses the given ledger but real matrix/norm files."""
    synth_root = tmp_path / "synth_root"
    synth_compat = synth_root / "compat"
    synth_compat.mkdir(parents=True, exist_ok=True)

    # Copy real matrix and normalization spec
    real_matrix = real_repo_root / "compat" / "parity_matrix.md"
    real_norm = real_repo_root / "compat" / "parity_normalization.md"
    (synth_compat / "parity_matrix.md").write_bytes(real_matrix.read_bytes())
    (synth_compat / "parity_normalization.md").write_bytes(real_norm.read_bytes())

    # Use the synthetic ledger
    (synth_compat / "parity_rows.json").write_bytes(ledger_path.read_bytes())

    return synth_root
