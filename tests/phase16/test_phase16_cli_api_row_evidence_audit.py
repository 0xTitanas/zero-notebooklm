"""Phase 16 CLI/API row evidence audit tests.

Validates compat/cli_api_row_evidence.json and scripts/cli_api_row_evidence_audit.py.
Pure/offline: no live NotebookLM, browser, keychain, credential, or network access.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPAT_DIR = REPO_ROOT / "compat"
SCRIPTS_DIR = REPO_ROOT / "scripts"

MANIFEST_PATH = COMPAT_DIR / "cli_api_row_evidence.json"
AUDIT_SCRIPT = SCRIPTS_DIR / "cli_api_row_evidence_audit.py"

EXPECTED_CLI_COUNT = 90
EXPECTED_API_COUNT = 9
EXPECTED_API_SCENARIO_COUNT = 108


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _load_rows() -> list[dict]:
    data = json.loads((COMPAT_DIR / "parity_rows.json").read_text(encoding="utf-8"))
    return data["rows"]


def _load_audit_module():
    assert AUDIT_SCRIPT.is_file(), f"audit script missing: {AUDIT_SCRIPT}"
    spec = importlib.util.spec_from_file_location(
        "_cli_api_row_evidence_audit_p16", AUDIT_SCRIPT
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _clean_env(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    return {
        "HOME": str(home),
        "USERPROFILE": str(home),
        "TMPDIR": str(tmp_path / "tmp"),
        "PYTHONPATH": "",
        "PATH": os.environ.get("PATH", ""),
    }


def _make_minimal_manifest(
    *,
    cli_mappings: list[dict] | None = None,
    api_mappings: list[dict] | None = None,
) -> dict:
    return {
        "schema_version": "cli_api_row_evidence/1",
        "target": "notebooklm-py==0.7.2",
        "exact_one_to_one_claim_ready": False,
        "cli_mappings": cli_mappings or [],
        "api_mappings": api_mappings or [],
    }


def _valid_cli_mapping(row_id: str, leaf: str = "notebooklm ask") -> dict:
    return {
        "row_id": row_id,
        "category": "cli",
        "status": "open",
        "closed_system": True,
        "no_live": True,
        "promotion_allowed": False,
        "scenario_refs": [leaf],
        "missing_for_promotion": ["differential_live_result"],
    }


def _valid_api_mapping(row_id: str, scenario_refs: list[str] | None = None) -> dict:
    return {
        "row_id": row_id,
        "category": "api",
        "status": "open",
        "closed_system": True,
        "no_live": True,
        "promotion_allowed": False,
        "scenario_refs": scenario_refs or ["artifacts.delete"],
        "missing_for_promotion": ["upstream_vs_bare_direct_result"],
    }


def _synth_root(tmp_path: Path, manifest: dict) -> Path:
    root = tmp_path / "synth"
    compat = root / "compat"
    compat.mkdir(parents=True)
    scripts = root / "scripts"
    scripts.mkdir()
    (compat / "cli_api_row_evidence.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    # Copy the real rows ledger so the audit can load CLI/API row IDs
    (compat / "parity_rows.json").write_bytes(
        (COMPAT_DIR / "parity_rows.json").read_bytes()
    )
    # Copy api_golden for API scenario validation
    golden_src = COMPAT_DIR / "api_golden"
    golden_dst = compat / "api_golden"
    golden_dst.mkdir()
    for f in golden_src.iterdir():
        (golden_dst / f.name).write_bytes(f.read_bytes())
    (scripts / "cli_behavior_parity_audit.py").write_bytes(
        (SCRIPTS_DIR / "cli_behavior_parity_audit.py").read_bytes()
    )
    return root


# --------------------------------------------------------------------------- #
# 1. Manifest structure
# --------------------------------------------------------------------------- #


def test_manifest_exists() -> None:
    assert MANIFEST_PATH.is_file(), "compat/cli_api_row_evidence.json missing"


def test_manifest_parses() -> None:
    data = _load_manifest()
    assert isinstance(data, dict)
    assert data.get("schema_version", "").startswith("cli_api_row_evidence/")


def test_manifest_has_exact_one_to_one_false() -> None:
    data = _load_manifest()
    assert data.get("exact_one_to_one_claim_ready") is False


def test_manifest_has_cli_and_api_mappings_keys() -> None:
    data = _load_manifest()
    assert "cli_mappings" in data
    assert "api_mappings" in data
    assert isinstance(data["cli_mappings"], list)
    assert isinstance(data["api_mappings"], list)


# --------------------------------------------------------------------------- #
# 2. CLI mapping coverage
# --------------------------------------------------------------------------- #


def test_cli_mapping_count_is_90() -> None:
    data = _load_manifest()
    assert len(data["cli_mappings"]) == EXPECTED_CLI_COUNT, (
        f"expected {EXPECTED_CLI_COUNT} CLI mappings, got {len(data['cli_mappings'])}"
    )


def test_cli_mappings_cover_all_cli_rows() -> None:
    rows = _load_rows()
    cli_row_ids = {r["id"] for r in rows if r["category"] == "cli"}
    data = _load_manifest()
    mapped_ids = {m["row_id"] for m in data["cli_mappings"]}
    missing = cli_row_ids - mapped_ids
    assert not missing, f"CLI rows without mapping: {sorted(missing)}"


def test_cli_mappings_no_duplicates() -> None:
    data = _load_manifest()
    ids = [m["row_id"] for m in data["cli_mappings"]]
    dupes = [rid for rid in set(ids) if ids.count(rid) > 1]
    assert not dupes, f"duplicate CLI mappings: {dupes}"


def test_cli_mappings_only_cli_rows() -> None:
    data = _load_manifest()
    bad = [
        m["row_id"] for m in data["cli_mappings"] if not m["row_id"].startswith("cli.")
    ]
    assert not bad, f"non-CLI rows in cli_mappings: {bad}"


# --------------------------------------------------------------------------- #
# 3. API mapping coverage
# --------------------------------------------------------------------------- #


def test_api_mapping_count_is_9() -> None:
    data = _load_manifest()
    assert len(data["api_mappings"]) == EXPECTED_API_COUNT, (
        f"expected {EXPECTED_API_COUNT} API mappings, got {len(data['api_mappings'])}"
    )


def test_api_mappings_cover_all_api_rows() -> None:
    rows = _load_rows()
    api_row_ids = {r["id"] for r in rows if r["category"] == "api"}
    data = _load_manifest()
    mapped_ids = {m["row_id"] for m in data["api_mappings"]}
    missing = api_row_ids - mapped_ids
    assert not missing, f"API rows without mapping: {sorted(missing)}"


def test_api_mappings_no_duplicates() -> None:
    data = _load_manifest()
    ids = [m["row_id"] for m in data["api_mappings"]]
    dupes = [rid for rid in set(ids) if ids.count(rid) > 1]
    assert not dupes, f"duplicate API mappings: {dupes}"


def test_api_scenario_refs_cover_all_108_ids() -> None:
    sigs = json.loads(
        (COMPAT_DIR / "api_golden" / "signatures.json").read_text(encoding="utf-8")
    )
    expected: set[str] = set()
    for subclient, spec in sigs["subclients"].items():
        for method in spec["async_methods"]:
            expected.add(f"{subclient}.{method}")
    assert len(expected) == EXPECTED_API_SCENARIO_COUNT

    data = _load_manifest()
    covered: set[str] = set()
    for m in data["api_mappings"]:
        covered.update(m.get("scenario_refs", []))
    missing = expected - covered
    assert not missing, (
        f"API scenario_refs missing {len(missing)} IDs: {sorted(missing)[:5]}"
    )


# --------------------------------------------------------------------------- #
# 4. Schema integrity — all mappings
# --------------------------------------------------------------------------- #


def test_all_mappings_closed_system_true() -> None:
    data = _load_manifest()
    bad = [
        m["row_id"]
        for m in data["cli_mappings"] + data["api_mappings"]
        if not m.get("closed_system", False)
    ]
    assert not bad, f"mappings without closed_system=true: {bad}"


def test_all_mappings_no_live_true() -> None:
    data = _load_manifest()
    bad = [
        m["row_id"]
        for m in data["cli_mappings"] + data["api_mappings"]
        if not m.get("no_live", False)
    ]
    assert not bad, f"mappings without no_live=true: {bad}"


def test_all_mappings_promotion_allowed_true() -> None:
    data = _load_manifest()
    bad = [
        m["row_id"]
        for m in data["cli_mappings"] + data["api_mappings"]
        if m.get("promotion_allowed") is not True
    ]
    assert not bad, f"promoted mappings without promotion_allowed=true: {bad}"


def test_all_mappings_status_pass() -> None:
    data = _load_manifest()
    bad = [
        m["row_id"]
        for m in data["cli_mappings"] + data["api_mappings"]
        if m.get("status") != "pass" or m.get("row_status") != "pass"
    ]
    assert not bad, f"mappings not in pass status: {bad}"


def test_all_mappings_missing_for_promotion_empty_after_promotion() -> None:
    data = _load_manifest()
    bad = [
        m["row_id"]
        for m in data["cli_mappings"] + data["api_mappings"]
        if m.get("missing_for_promotion") != []
    ]
    assert not bad, f"promoted mappings with remaining missing_for_promotion: {bad}"


def test_all_mappings_evidence_basis_mentions_direct_result() -> None:
    data = _load_manifest()
    bad = [
        m["row_id"]
        for m in data["cli_mappings"] + data["api_mappings"]
        if "direct" not in m.get("evidence_basis", "").lower()
    ]
    assert not bad, f"promoted mappings without direct evidence basis: {bad}"


def test_all_mappings_have_comparator_and_evidence_basis() -> None:
    data = _load_manifest()
    bad = [
        m["row_id"]
        for m in data["cli_mappings"] + data["api_mappings"]
        if not m.get("comparator") or not m.get("evidence_basis")
    ]
    assert not bad, f"mappings without comparator/evidence_basis: {bad}"


def test_no_excluded_category_rows_referenced() -> None:
    excluded = {"auth", "rpc", "offline", "self_test", "mcp"}
    data = _load_manifest()
    all_ids = [m["row_id"] for m in data["cli_mappings"] + data["api_mappings"]]
    bad = [rid for rid in all_ids if rid.split(".")[0] in excluded]
    assert not bad, f"excluded category rows referenced: {bad}"


# --------------------------------------------------------------------------- #
# 5. CLI scenario refs reference existing leaves
# --------------------------------------------------------------------------- #


def test_cli_scenario_refs_are_valid_leaves() -> None:
    mod_name = "_cli_parity_p16"
    spec = importlib.util.spec_from_file_location(
        mod_name, SCRIPTS_DIR / "cli_behavior_parity_audit.py"
    )
    assert spec is not None and spec.loader is not None
    cli_mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = cli_mod
    spec.loader.exec_module(cli_mod)
    leaves = {s.leaf for s in cli_mod.build_scenarios()}

    data = _load_manifest()
    bad: list[str] = []
    for m in data["cli_mappings"]:
        for ref in m.get("scenario_refs", []):
            if ref not in leaves:
                bad.append(f"{m['row_id']}: {ref!r}")
    assert not bad, f"CLI scenario_refs not in build_scenarios: {bad[:5]}"


# --------------------------------------------------------------------------- #
# 6. Audit script — happy path
# --------------------------------------------------------------------------- #


def test_audit_script_exists() -> None:
    assert AUDIT_SCRIPT.is_file(), f"audit script missing: {AUDIT_SCRIPT}"


def test_audit_build_report_strict_ok() -> None:
    mod = _load_audit_module()
    report = mod.build_report(repo_root=REPO_ROOT)
    assert report["strict_ok"] is True, (
        f"strict_ok is False; errors: {report.get('errors', [])}"
    )


def test_audit_report_exposes_explicit_mapping_counts() -> None:
    mod = _load_audit_module()
    report = mod.build_report(repo_root=REPO_ROOT)
    assert report["warnings"] == []
    assert report["cli_rows_mapped"] == EXPECTED_CLI_COUNT
    assert report["api_rows_mapped"] == EXPECTED_API_COUNT
    assert report["api_scenarios_mapped"] == EXPECTED_API_SCENARIO_COUNT
    assert report["strict_exit_code"] == 0


def test_audit_build_report_exact_claim_false() -> None:
    mod = _load_audit_module()
    report = mod.build_report(repo_root=REPO_ROOT)
    assert report["exact_one_to_one_claim_ready"] is False


def test_audit_json_exits_zero(tmp_path: Path) -> None:
    env = _clean_env(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(AUDIT_SCRIPT), "--json"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, (
        f"stderr: {proc.stderr[:400]}\nstdout: {proc.stdout[:400]}"
    )
    report = json.loads(proc.stdout)
    assert report["strict_ok"] is True
    assert report["exact_one_to_one_claim_ready"] is False


def test_audit_json_no_absolute_home_paths(tmp_path: Path) -> None:
    env = _clean_env(tmp_path)
    clean_home = tmp_path / "home"
    proc = subprocess.run(
        [sys.executable, str(AUDIT_SCRIPT), "--json"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    out = proc.stdout
    assert str(clean_home) not in out
    assert "/".join(("", "Users", "")) not in out
    assert "/".join(("", "home", "")) not in out


# --------------------------------------------------------------------------- #
# 7. Negative tests — synthetic manifests
# --------------------------------------------------------------------------- #


def test_audit_fails_if_manifest_missing(tmp_path: Path) -> None:
    root = tmp_path / "no_manifest"
    compat = root / "compat"
    compat.mkdir(parents=True)
    (compat / "parity_rows.json").write_bytes(
        (COMPAT_DIR / "parity_rows.json").read_bytes()
    )
    mod = _load_audit_module()
    report = mod.build_report(repo_root=root)
    assert report["strict_ok"] is False
    assert report["manifest_present"] is False


def test_audit_fails_if_cli_mapping_missing(tmp_path: Path) -> None:
    data = _load_manifest()
    manifest = dict(data)
    manifest["cli_mappings"] = data["cli_mappings"][:-1]  # drop one
    root = _synth_root(tmp_path, manifest)
    mod = _load_audit_module()
    report = mod.build_report(repo_root=root)
    assert report["strict_ok"] is False


def test_audit_fails_if_api_mapping_missing(tmp_path: Path) -> None:
    data = _load_manifest()
    manifest = dict(data)
    manifest["api_mappings"] = data["api_mappings"][:-1]  # drop one
    root = _synth_root(tmp_path, manifest)
    mod = _load_audit_module()
    report = mod.build_report(repo_root=root)
    assert report["strict_ok"] is False


def test_audit_fails_if_duplicate_cli_mapping(tmp_path: Path) -> None:
    data = _load_manifest()
    manifest = dict(data)
    # duplicate the first CLI mapping
    manifest["cli_mappings"] = list(data["cli_mappings"]) + [data["cli_mappings"][0]]
    root = _synth_root(tmp_path, manifest)
    mod = _load_audit_module()
    report = mod.build_report(repo_root=root)
    assert report["strict_ok"] is False


def test_audit_fails_if_promoted_mapping_promotion_allowed_false(
    tmp_path: Path,
) -> None:
    data = _load_manifest()
    bad_mapping = dict(data["cli_mappings"][0])
    bad_mapping["promotion_allowed"] = False
    manifest = dict(data)
    manifest["cli_mappings"] = [bad_mapping] + list(data["cli_mappings"][1:])
    root = _synth_root(tmp_path, manifest)
    mod = _load_audit_module()
    report = mod.build_report(repo_root=root)
    assert report["strict_ok"] is False


def test_audit_fails_if_promoted_mapping_has_remaining_missing_for_promotion(
    tmp_path: Path,
) -> None:
    data = _load_manifest()
    bad_mapping = dict(data["cli_mappings"][0])
    bad_mapping["missing_for_promotion"] = ["upstream_vs_bare_direct_result"]
    manifest = dict(data)
    manifest["cli_mappings"] = [bad_mapping] + list(data["cli_mappings"][1:])
    root = _synth_root(tmp_path, manifest)
    mod = _load_audit_module()
    report = mod.build_report(repo_root=root)
    assert report["strict_ok"] is False


def test_audit_fails_if_comparator_empty(tmp_path: Path) -> None:
    data = _load_manifest()
    bad_mapping = dict(data["cli_mappings"][0])
    bad_mapping["comparator"] = ""
    manifest = dict(data)
    manifest["cli_mappings"] = [bad_mapping] + list(data["cli_mappings"][1:])
    root = _synth_root(tmp_path, manifest)
    mod = _load_audit_module()
    report = mod.build_report(repo_root=root)
    assert report["strict_ok"] is False


def test_audit_fails_if_evidence_basis_empty(tmp_path: Path) -> None:
    data = _load_manifest()
    bad_mapping = dict(data["api_mappings"][0])
    bad_mapping["evidence_basis"] = ""
    manifest = dict(data)
    manifest["api_mappings"] = [bad_mapping] + list(data["api_mappings"][1:])
    root = _synth_root(tmp_path, manifest)
    mod = _load_audit_module()
    report = mod.build_report(repo_root=root)
    assert report["strict_ok"] is False


def test_audit_fails_if_cli_scenario_ref_unknown(tmp_path: Path) -> None:
    data = _load_manifest()
    bad_mapping = dict(data["cli_mappings"][0])
    bad_mapping["scenario_refs"] = ["notebooklm definitely-not-a-command"]
    manifest = dict(data)
    manifest["cli_mappings"] = [bad_mapping] + list(data["cli_mappings"][1:])
    root = _synth_root(tmp_path, manifest)
    mod = _load_audit_module()
    report = mod.build_report(repo_root=root)
    assert report["strict_ok"] is False


def test_audit_fails_if_closed_system_false(tmp_path: Path) -> None:
    data = _load_manifest()
    bad_mapping = dict(data["cli_mappings"][0])
    bad_mapping["closed_system"] = False
    manifest = dict(data)
    manifest["cli_mappings"] = [bad_mapping] + list(data["cli_mappings"][1:])
    root = _synth_root(tmp_path, manifest)
    mod = _load_audit_module()
    report = mod.build_report(repo_root=root)
    assert report["strict_ok"] is False


def test_audit_fails_if_api_scenario_missing(tmp_path: Path) -> None:
    data = _load_manifest()
    # Remove one scenario_ref from the first API mapping
    bad_api = dict(data["api_mappings"][0])
    bad_api["scenario_refs"] = list(bad_api["scenario_refs"])[1:]  # drop first
    manifest = dict(data)
    manifest["api_mappings"] = [bad_api] + list(data["api_mappings"][1:])
    root = _synth_root(tmp_path, manifest)
    mod = _load_audit_module()
    report = mod.build_report(repo_root=root)
    assert report["strict_ok"] is False
