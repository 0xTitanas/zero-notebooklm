"""Phase 10 RPC drift containment / fake-server closure gate.

Phase 10 consolidates the fake-server RPC contract into one executable audit over
all committed sanitized batchexecute fixture pairs. It is offline and now
category-aware so it reflects the `rpc` parity row promotion state after
closed-system fixture evidence.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path


EXPECTED_FIXTURE_IDS = [
    "chat_ask",
    "list_artifacts",
    "list_notebooks",
    "list_notes",
    "list_sources",
]


def _load_audit_module(repo_root: Path):
    script = repo_root / "scripts" / "rpc_drift_audit.py"
    spec = importlib.util.spec_from_file_location("phase10_rpc_drift_audit", script)
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


def test_phase10_rpc_audit_is_pure_and_non_promotional(
    repo_root: Path, monkeypatch
) -> None:
    audit = _load_audit_module(repo_root)

    def _forbidden_home() -> Path:
        raise AssertionError("Phase 10 RPC audit must not inspect user home")

    monkeypatch.setattr(Path, "home", _forbidden_home)
    matrix = repo_root / "compat" / "parity_matrix.md"
    before = matrix.read_text(encoding="utf-8")

    report = audit.build_report(repo_root=repo_root)

    assert matrix.read_text(encoding="utf-8") == before
    assert report["schema_version"] == "rpc_drift_audit/1"
    assert report["target"] == "notebooklm-py==0.7.2"
    assert report["live_access"] is False
    assert report["credential_access"] is False
    assert report["category_promotion"] == {"rpc": True}
    assert report["category_states"] == {"rpc": "pass"}
    assert report["overall_status"] == "pass"


def test_phase10_rpc_audit_covers_every_committed_fixture_pair(
    repo_root: Path,
) -> None:
    audit = _load_audit_module(repo_root)

    report = audit.build_report(repo_root=repo_root)
    fixtures = report["fixture_contract"]

    assert fixtures["wire_shape"]["xssi_prefix"] == ")]}'"
    assert fixtures["wire_shape"]["rpc_modules"] == [
        "rpc/__init__.py",
        "rpc/_safe_index.py",
        "rpc/decoder.py",
        "rpc/encoder.py",
        "rpc/overrides.py",
        "rpc/types.py",
    ]
    assert fixtures["pairs"]["fixture_ids"] == EXPECTED_FIXTURE_IDS
    assert fixtures["pairs"]["total"] == 5
    assert fixtures["pairs"]["requests_decoded"] == 5
    assert fixtures["pairs"]["responses_decoded"] == 5
    assert fixtures["pairs"]["roundtrips"] == 5
    assert fixtures["pairs"]["streaming_responses"] == ["chat_ask"]
    assert fixtures["sanitization"]["status"] == "pass"
    assert fixtures["sanitization"]["synthetic_markers"] >= 3


def test_phase10_rpc_audit_proves_package_and_runtime_agreement(
    repo_root: Path,
) -> None:
    audit = _load_audit_module(repo_root)

    report = audit.build_report(repo_root=repo_root)
    parser = report["parser_contract"]

    assert parser["package_decoder"] == "pass"
    assert parser["zero_rpc_surface"] == "pass"
    assert parser["parity_runtime_rpc"] == "pass"
    assert parser["package_response_matches"] == 5
    assert parser["package_vs_runtime_response_matches"] == 5
    assert parser["canonical_encode_roundtrips"] == 5
    assert parser["fail_closed_cases"] == 7
    assert parser["redacted_error_messages"] is True


def test_phase10_rpc_audit_proves_fake_rpc_seam_round_trip(repo_root: Path) -> None:
    audit = _load_audit_module(repo_root)

    report = audit.build_report(repo_root=repo_root)
    fake_rpc = report["fake_rpc_contract"]

    assert fake_rpc["status"] == "pass"
    assert fake_rpc["operations"] == {
        "chat_ask_payload": "pass",
        "list_artifacts_payload": "pass",
        "list_notebooks_payload": "pass",
        "list_notes_payload": "pass",
        "list_sources_payload": "pass",
        "unsupported_request_fail_closed": "pass",
    }
    assert fake_rpc["payload_summaries"] == {
        "notebooks": "list[1]",
        "sources": "list[2]",
        "notes": "list[2]",
        "artifacts": "list[3]",
        "chat": "list[3]",
    }


def test_phase10_rpc_audit_script_json_strict_and_human_modes(
    repo_root: Path, tmp_path: Path
) -> None:
    env = _clean_env(tmp_path)

    json_proc = _run(
        [sys.executable, "scripts/rpc_drift_audit.py", "--json"],
        cwd=repo_root,
        env=env,
    )
    assert json_proc.returncode == 0, json_proc.stderr + json_proc.stdout
    assert str(tmp_path) not in json_proc.stdout
    assert str(Path.home()) not in json_proc.stdout
    data = json.loads(json_proc.stdout)
    assert data["overall_status"] == "pass"
    assert data["strict_exit_code"] == 0
    assert data["fixture_contract"]["pairs"]["total"] == 5
    assert data["category_promotion"] == {"rpc": True}

    strict_proc = _run(
        [sys.executable, "scripts/rpc_drift_audit.py", "--json", "--strict"],
        cwd=repo_root,
        env=env,
    )
    assert strict_proc.returncode == 0, strict_proc.stderr + strict_proc.stdout
    strict_data = json.loads(strict_proc.stdout)
    assert strict_data["strict_exit_code"] == 0

    human_proc = _run(
        [sys.executable, "scripts/rpc_drift_audit.py"],
        cwd=repo_root,
        env=env,
    )
    assert human_proc.returncode == 0, human_proc.stderr + human_proc.stdout
    assert "ZeroNotebookLM RPC drift audit: pass" in human_proc.stdout
    assert "fixture pairs: 5/5" in human_proc.stdout
    assert "parser agreement: pass" in human_proc.stdout
    assert "fake RPC seam: pass" in human_proc.stdout
    assert "category promotion: pass" in human_proc.stdout
    assert len(human_proc.stdout.splitlines()) <= 6
