"""Phase 20 RPC parity promotion validation.

Verifies that RPC rows are promoted to pass from committed, closed-system,
fake-server fixture evidence while preserving the broader gating invariants
(selected auth profile closed, MCP not implemented, no live/auth/browser/credential/
network scope in the RPC evidence, and still blocked release).
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

RPC_ROW_IDS = {
    "rpc.list_notebooks",
    "rpc.list_sources",
    "rpc.list_notes",
    "rpc.list_artifacts",
    "rpc.chat_ask",
}


def _load_json(repo_root: Path, name: str) -> dict:
    return json.loads((repo_root / "compat" / name).read_text(encoding="utf-8"))


def _load_rows(repo_root: Path) -> list[dict]:
    return _load_json(repo_root, "parity_rows.json")["rows"]


def _load_evidence_records(repo_root: Path) -> list[dict]:
    return _load_json(repo_root, "parity_evidence.json")["evidence_records"]


def _load_module(repo_root: Path, filename: str, module_name: str):
    path = repo_root / "scripts" / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_script(repo_root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "LANG": "C.UTF-8",
        }
    )
    return subprocess.run(
        [sys.executable, *args],
        cwd=str(repo_root),
        env=env,
        text=True,
        capture_output=True,
        timeout=90,
    )


def test_phase20_row_counts_reflect_rpc_promotion(repo_root: Path) -> None:
    rows = _load_rows(repo_root)
    status_counts = Counter(r["status"] for r in rows)
    assert len(rows) == 258, f"expected 258 rows, got {len(rows)}"
    assert status_counts["pass"] == 257, (
        f"expected 257 pass rows, got {status_counts['pass']}"
    )
    assert status_counts["open"] == 0, f"expected no open rows, got {status_counts['open']}"
    assert status_counts["not_applicable"] == 1, (
        f"expected 1 not_applicable row, got {status_counts['not_applicable']}"
    )


def test_phase20_rpc_rows_all_pass_and_selected_auth_rows_are_closed(repo_root: Path) -> None:
    rows = _load_rows(repo_root)
    by_category = {
        cat: [r for r in rows if r.get("category") == cat]
        for cat in ("rpc", "auth", "mcp")
    }

    rpc_rows = by_category["rpc"]
    assert {r["id"] for r in rpc_rows} == RPC_ROW_IDS
    non_pass = [r["id"] for r in rpc_rows if r.get("status") != "pass"]
    assert not non_pass, f"RPC rows not in pass state: {non_pass}"

    auth_rows = by_category["auth"]
    assert len(auth_rows) == 146, f"expected 146 auth rows, got {len(auth_rows)}"
    assert sum(1 for r in auth_rows if r.get("status") == "pass") == 146
    assert sum(1 for r in auth_rows if r.get("status") == "open") == 0
    assert not [r["id"] for r in auth_rows if r.get("status") == "blocked"]

    mcp_rows = by_category["mcp"]
    assert mcp_rows, "MCP rows missing"
    non_na = [r["id"] for r in mcp_rows if r.get("status") != "not_applicable"]
    assert not non_na, f"expected MCP rows not_applicable, got {non_na}"


def test_phase20_exact_one_to_one_claim_is_false(repo_root: Path) -> None:
    mod = _load_module(repo_root, "parity_row_audit.py", "_p20_parity_row")
    report = mod.build_report(repo_root=repo_root)
    assert report["exact_one_to_one_claim_ready"] is False


def test_phase20_rpc_required_evidence_tokens_are_satisfied_offline(
    repo_root: Path,
) -> None:
    rows = _load_rows(repo_root)
    evidence_records = _load_evidence_records(repo_root)

    evidence_index: dict[tuple[str, str], list[dict]] = {}
    for rec in evidence_records:
        key = (rec["row_id"], rec.get("evidence_id", ""))
        evidence_index.setdefault(key, []).append(rec)

    for row in rows:
        if row["id"] not in RPC_ROW_IDS:
            continue
        for token in row.get("required_evidence", []):
            matches = evidence_index.get((row["id"], token), [])
            assert matches, (
                f"required evidence token {token!r} for {row['id']!r} missing from evidence manifest"
            )
            for rec in matches:
                assert rec.get("closed_system") is True
                assert rec.get("no_live") is True


def test_phase20_rpc_evidence_artifacts_and_scope_are_offline(repo_root: Path) -> None:
    evidence_records = _load_evidence_records(repo_root)
    bad: list[str] = []
    forbidden = (
        "http://",
        "https://",
        "browser",
        "keychain",
        "playwright",
        "selenium",
        "--live",
        "notebooklm.google",
        "pypi.org",
        "pip install",
    )

    for rec in evidence_records:
        if rec.get("row_id") not in RPC_ROW_IDS:
            continue
        art = rec.get("artifact_path")
        if art and not (repo_root / art).exists():
            bad.append(f"missing artifact_path: {art!r}")
        command = (rec.get("command") or "").lower()
        if any(token in command for token in forbidden):
            bad.append(
                f"RPC evidence command scope risk: {rec['row_id']}::{rec['evidence_id']}"
            )
    assert not bad, f"RPC evidence scope/artifact issues: {bad}"


def test_phase20_rpc_drift_audit_is_promotion_aware_and_forge_free(
    repo_root: Path,
) -> None:
    mod = _load_module(repo_root, "rpc_drift_audit.py", "_p20_rpc_drift")
    report = mod.build_report(repo_root=repo_root)
    assert report["category_states"]["rpc"] == "pass"
    assert report["category_promotion"] == {"rpc": True}
    assert report["live_access"] is False
    assert report["credential_access"] is False
    assert report["fake_rpc_contract"]["status"] == "pass"
    assert report["fixture_contract"]["pairs"]["roundtrips"] == 5


def test_phase20_release_candidate_still_blocked_by_auth_and_live_only(
    repo_root: Path,
) -> None:
    mod = _load_module(
        repo_root, "release_candidate_audit.py", "_p20_release_candidate"
    )
    report = mod.build_report(repo_root=repo_root)

    assert report["release_candidate_ready"] is False
    assert report["one_to_one_functionality_claim"] is False
    assert report["strict_exit_code"] == 77
    assert report["category_states"]["rpc"] == "pass"

    blockers = set(report["remaining_blockers"])
    assert "rpc_category_open" not in blockers
    assert blockers == {
        "auth_category_open",
        "live_readonly_differential_not_authorized",
        "live_mutation_smoke_not_authorized",
    }


def test_phase20_parity_readiness_open_category_is_auth_only_and_rpc_is_pass(
    repo_root: Path,
) -> None:
    mod = _load_module(repo_root, "parity_readiness.py", "_p20_readiness")
    report = mod.build_report(repo_root=repo_root)

    assert report["open_categories"] == ["auth"]
    assert "rpc" in report["pass_categories"]
    assert report["release_ready"] is False
    assert report["live_authorization_required"] is True
    assert report["mcp_next_phase_allowed"] is True
