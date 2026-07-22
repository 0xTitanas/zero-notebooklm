"""Phase 11 auth parity evidence gate.

This phase adds an executable, offline-only auth evidence audit. It deliberately
keeps the auth category open while allowing row-specific pass evidence: the gate
proves current fixture/injected foundations and claim boundaries, not full live
NotebookLM/browser-cookie parity.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path


EXPECTED_EVIDENCE_BUCKETS = {
    "matrix_readiness",
    "offline_profile_storage",
    "browser_cookie_fixture",
    "interactive_login_primitive",
    "network_refresh_primitive",
    "os_credential_boundary",
    "purity_redaction_non_mutation",
}


def _load_audit_module(repo_root: Path):
    script = repo_root / "scripts" / "auth_parity_evidence_audit.py"
    spec = importlib.util.spec_from_file_location("phase11_auth_audit", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _clean_env(tmp_path: Path) -> dict[str, str]:
    clean_home = tmp_path / "home"
    clean_home.mkdir()
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    env = {
        "HOME": str(clean_home),
        "USERPROFILE": str(clean_home),
        "TMPDIR": str(tmp_dir),
        "PYTHONPATH": "",
        "PATH": os.environ.get("PATH", ""),
    }
    return env


def _run(
    args: list[str], *, cwd: Path, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
    )


def _auth_matrix_counts(repo_root: Path) -> dict[str, int]:
    data = json.loads(
        (repo_root / "compat" / "auth_matrix.json").read_text(encoding="utf-8")
    )
    login = data["interactive_login_matrix"]
    cookie = data["browser_cookie_import_matrix"]
    rows = login + cookie
    return {
        "total": len(rows),
        "interactive": len(login),
        "browser_cookie": len(cookie),
        "parity_open": sum(1 for row in rows if row.get("parity_state") == "open"),
        "parity_pass": sum(1 for row in rows if row.get("parity_state") == "pass"),
        "parity_blocked": sum(
            1 for row in rows if row.get("parity_state") == "blocked"
        ),
    }


def _parity_counts(rows: list[dict]) -> dict[str, int]:
    return {
        state: sum(1 for row in rows if row.get("parity_state") == state)
        for state in ("open", "pass", "blocked")
    }


def test_phase11_auth_audit_is_pure_non_promotional_and_counts_exact(
    repo_root: Path, monkeypatch
) -> None:
    audit = _load_audit_module(repo_root)

    def _forbidden_home() -> Path:
        raise AssertionError("Phase 11 auth audit must not inspect user home")

    monkeypatch.setattr(Path, "home", _forbidden_home)
    auth_matrix = repo_root / "compat" / "auth_matrix.json"
    parity_matrix = repo_root / "compat" / "parity_matrix.md"
    auth_before = auth_matrix.read_bytes()
    parity_before = parity_matrix.read_bytes()

    report = audit.build_report(repo_root=repo_root)
    expected_counts = _auth_matrix_counts(repo_root)

    assert auth_matrix.read_bytes() == auth_before
    assert parity_matrix.read_bytes() == parity_before
    assert report["schema_version"] == "auth_parity_evidence_audit/1"
    assert report["target"] == "notebooklm-py==0.7.2"
    assert report["overall_status"] == "pass"
    assert report["strict_exit_code"] == 0
    assert report["live_access"] is False
    assert report["network_access"] is False
    assert report["browser_store_access"] is False
    assert report["credential_access"] is False
    assert report["category_promotion"] == {"auth": False}
    assert report["category_states"] == {"auth": "open"}

    assert report["auth_matrix_summary"] == expected_counts
    assert expected_counts["parity_pass"] == 146
    assert expected_counts["parity_open"] + expected_counts["parity_pass"] == 146

    readiness = report["readiness_summary"]
    matrix_data = json.loads(auth_matrix.read_text(encoding="utf-8"))
    login_counts = _parity_counts(matrix_data["interactive_login_matrix"])
    cookie_counts = _parity_counts(matrix_data["browser_cookie_import_matrix"])
    assert readiness["total_rows"] == 146
    assert readiness["interactive_login_rows"] == 45
    assert readiness["browser_cookie_import_rows"] == 101
    assert readiness["parity_pass_count"] == expected_counts["parity_pass"]
    assert readiness["parity_open_count"] == expected_counts["parity_open"]
    assert readiness["parity_blocked_count"] == 0
    assert readiness["foundation_covered_count"] == 60
    assert readiness["foundation_partial_count"] == 59
    assert readiness["foundation_none_count"] == 27
    assert readiness["release_blocked"] is True
    assert readiness["profile_exclusion_path_count"] == 49
    assert readiness["deferred_future_release_path_count"] == 10
    assert readiness["interactive_login_aggregate"] == {
        "rows": 45,
        "foundation_covered": 0,
        "foundation_partial": 18,
        "foundation_none": 27,
        "blocked_live": 0,
        "parity_pass": login_counts["pass"],
        "parity_open": login_counts["open"],
        "parity_blocked": login_counts["blocked"],
    }
    assert readiness["browser_cookie_import_aggregate"] == {
        "rows": 101,
        "foundation_covered": 60,
        "foundation_partial": 41,
        "foundation_none": 0,
        "blocked_live": 0,
        "parity_pass": cookie_counts["pass"],
        "parity_open": cookie_counts["open"],
        "parity_blocked": cookie_counts["blocked"],
    }


def test_phase11_auth_audit_evidence_buckets_pass(repo_root: Path) -> None:
    audit = _load_audit_module(repo_root)

    report = audit.build_report(repo_root=repo_root)
    evidence = report["evidence"]

    assert set(evidence) == EXPECTED_EVIDENCE_BUCKETS
    assert {
        name for name, bucket in evidence.items() if bucket["status"] != "pass"
    } == set()
    assert evidence["matrix_readiness"]["matrix_counts_exact"] is True
    assert evidence["matrix_readiness"]["readiness_counts_exact"] is True
    assert evidence["offline_profile_storage"]["operations"] == {
        "check_storage_ok": "pass",
        "check_storage_no_unexpected_domains": "pass",
        "inspect_storage_readable": "pass",
        "inspect_storage_cookie_count": "pass",
    }
    assert evidence["browser_cookie_fixture"]["operations"] == {
        "has_import_to_storage_state": "pass",
        "has_inspect_cookie_store": "pass",
        "has_refresh_browser_cookies": "pass",
        "cookie_browsers_defined": "pass",
        "encrypted_browsers_defined": "pass",
    }
    assert evidence["network_refresh_primitive"]["operations"] == {
        "has_refresh_storage": "pass",
        "has_check_storage_with_network": "pass",
        "refresh_storage_requires_path": "pass",
    }
    assert evidence["purity_redaction_non_mutation"]["operations"] == {
        "auth_matrix_unmodified": "pass",
        "parity_matrix_unmodified": "pass",
        "no_secrets_in_synth_labels": "pass",
        "report_redaction_scan": "pass",
    }
    assert any("Selected-profile auth closure is complete" in note for note in report["notes"])


def test_phase11_auth_audit_script_json_strict_and_human_modes(
    repo_root: Path, tmp_path: Path
) -> None:
    env = _clean_env(tmp_path)

    json_proc = _run(
        [sys.executable, "scripts/auth_parity_evidence_audit.py", "--json"],
        cwd=repo_root,
        env=env,
    )
    assert json_proc.returncode == 0, json_proc.stderr + json_proc.stdout
    assert str(tmp_path) not in json_proc.stdout
    assert str(Path.home()) not in json_proc.stdout
    data = json.loads(json_proc.stdout)
    counts = data["auth_matrix_summary"]
    assert data["overall_status"] == "pass"
    assert data["strict_exit_code"] == 0
    assert data["auth_matrix_summary"]["total"] == 146
    assert data["category_promotion"] == {"auth": False}
    assert data["category_states"] == {"auth": "open"}

    strict_proc = _run(
        [sys.executable, "scripts/auth_parity_evidence_audit.py", "--json", "--strict"],
        cwd=repo_root,
        env=env,
    )
    assert strict_proc.returncode == 0, strict_proc.stderr + strict_proc.stdout
    strict_data = json.loads(strict_proc.stdout)
    assert strict_data["strict_exit_code"] == 0

    human_proc = _run(
        [sys.executable, "scripts/auth_parity_evidence_audit.py"],
        cwd=repo_root,
        env=env,
    )
    assert human_proc.returncode == 0, human_proc.stderr + human_proc.stdout
    assert "ZeroNotebookLM auth parity evidence audit: pass" in human_proc.stdout
    assert (
        "auth matrix rows: 146 (45 interactive, 101 browser-cookie)"
        in human_proc.stdout
    )
    assert (
        f"parity open/pass/blocked: {counts['parity_open']}/{counts['parity_pass']}/{counts['parity_blocked']}"
        in human_proc.stdout
    )
    assert "evidence buckets: pass" in human_proc.stdout
    assert "category promotion: no" in human_proc.stdout
    assert "auth state: open" in human_proc.stdout
    assert len(human_proc.stdout.splitlines()) <= 6


def test_phase11_auth_audit_allows_row_specific_pass_evidence(
    repo_root: Path, tmp_path: Path
) -> None:
    env = _clean_env(tmp_path)
    matrix_copy = tmp_path / "auth_matrix_promoted.json"
    data = json.loads(
        (repo_root / "compat" / "auth_matrix.json").read_text(encoding="utf-8")
    )
    data["interactive_login_matrix"][0]["parity_state"] = "pass"
    matrix_copy.write_text(json.dumps(data), encoding="utf-8")

    proc = _run(
        [
            sys.executable,
            "scripts/auth_parity_evidence_audit.py",
            "--json",
            "--strict",
            "--auth-matrix",
            str(matrix_copy),
        ],
        cwd=repo_root,
        env=env,
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    report = json.loads(proc.stdout)
    assert report["overall_status"] == "pass"
    assert report["auth_matrix_summary"]["parity_pass"] >= 1
    assert "pass row" not in proc.stderr


def test_phase11_auth_audit_fails_closed_on_blocked_auth_row(
    repo_root: Path, tmp_path: Path
) -> None:
    env = _clean_env(tmp_path)
    matrix_copy = tmp_path / "auth_matrix_blocked.json"
    data = json.loads(
        (repo_root / "compat" / "auth_matrix.json").read_text(encoding="utf-8")
    )
    row = data["interactive_login_matrix"][0]
    row["parity_state"] = "blocked"
    matrix_copy.write_text(json.dumps(data), encoding="utf-8")

    proc = _run(
        [
            sys.executable,
            "scripts/auth_parity_evidence_audit.py",
            "--json",
            "--strict",
            "--auth-matrix",
            str(matrix_copy),
        ],
        cwd=repo_root,
        env=env,
    )

    assert proc.returncode != 0
    report = json.loads(proc.stdout)
    assert report["overall_status"] == "fail"
    assert report["auth_matrix_summary"]["parity_blocked"] == 1
    assert "blocked row" in proc.stderr


def test_phase11_auth_audit_fails_closed_on_auth_category_promotion(
    repo_root: Path, tmp_path: Path
) -> None:
    env = _clean_env(tmp_path)
    parity_copy = tmp_path / "parity_matrix_promoted.md"
    text = (repo_root / "compat" / "parity_matrix.md").read_text(encoding="utf-8")
    text = text.replace(
        "| auth | interactive login (45 rows) + browser-cookie import (101 in-profile rows; 49 explicit exclusions) | upstream auth matrix vs bare | open |",
        "| auth | interactive login (45 rows) + browser-cookie import (101 in-profile rows; 49 explicit exclusions) | upstream auth matrix vs bare | pass |",
    )
    parity_copy.write_text(text, encoding="utf-8")

    proc = _run(
        [
            sys.executable,
            "scripts/auth_parity_evidence_audit.py",
            "--json",
            "--strict",
            "--parity-matrix",
            str(parity_copy),
        ],
        cwd=repo_root,
        env=env,
    )

    assert proc.returncode != 0
    report = json.loads(proc.stdout)
    assert report["overall_status"] == "fail"
    assert report["category_states"] == {"auth": "pass"}
    assert "auth category" in proc.stderr


def test_phase11_auth_audit_report_redacts_secrets_and_local_paths(
    repo_root: Path, tmp_path: Path
) -> None:
    env = _clean_env(tmp_path)

    proc = _run(
        [sys.executable, "scripts/auth_parity_evidence_audit.py", "--json", "--strict"],
        cwd=repo_root,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    report_text = proc.stdout

    forbidden_literals = [
        str(tmp_path),
        "leak@example.com",
        "SID=SYNTH",
        "__Secure-1PSIDTS=",
        "github" + "_pat_",
        "ya29.",
        "sk-",
    ]
    for literal in forbidden_literals:
        assert literal not in report_text
    assert re.search(
        r"(?<![:/\w])/(?:[A-Za-z0-9._-]+/)+[A-Za-z0-9._-]+", report_text
    ) is None

    assert (
        re.search(r"\b[A-Z][A-Z0-9_]{1,40}=[A-Za-z0-9_./+\-]{8,}", report_text) is None
    )
