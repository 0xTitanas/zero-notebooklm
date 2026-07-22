"""Phase 9 Python API behavior parity audit gate.

Phase 7 proved API import/signature coverage. Phase 9 must execute one safe,
fixture-backed behavior scenario for every pinned public async sub-client method
without live access, credentials, or parity-row promotion. The API row stays
``open`` until later direct differential/live evidence closes it.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path


def _load_audit_module(repo_root: Path):
    script = repo_root / "scripts" / "api_behavior_parity_audit.py"
    spec = importlib.util.spec_from_file_location(
        "phase9_api_behavior_parity_audit", script
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _clean_env(tmp_path: Path) -> dict[str, str]:
    clean_home = tmp_path / "home"
    clean_home.mkdir()
    return {
        "HOME": str(clean_home),
        "USERPROFILE": str(clean_home),
        "TMPDIR": str(tmp_path / "tmp"),
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


def test_phase9_audit_is_pure_broad_and_non_promotional(
    repo_root: Path, monkeypatch, tmp_path: Path
) -> None:
    audit = _load_audit_module(repo_root)

    def _forbidden_home() -> Path:
        raise AssertionError("Phase 9 API behavior audit must not inspect user home")

    monkeypatch.setattr(Path, "home", _forbidden_home)
    matrix = repo_root / "compat" / "parity_matrix.md"
    before = matrix.read_text(encoding="utf-8")

    report = audit.build_report(repo_root=repo_root, work_dir=tmp_path / "audit-work")

    after = matrix.read_text(encoding="utf-8")
    assert after == before
    assert report["schema_version"] == "api_behavior_parity_audit/1"
    assert report["target"] == "notebooklm-py==0.7.2"
    assert report["live_access"] is False
    assert report["credential_access"] is False
    assert report["category_promotion"] == {"api": True}
    assert report["category_states"] == {"api": "pass"}


def test_phase9_audit_executes_all_pinned_subclient_async_methods(
    repo_root: Path, tmp_path: Path
) -> None:
    audit = _load_audit_module(repo_root)

    report = audit.build_report(repo_root=repo_root, work_dir=tmp_path / "audit-work")
    api = report["api_behavior"]

    assert api["oracle_subclients"] == 9
    assert api["oracle_async_methods"] == 108
    assert api["scenario_probe"]["total"] == 108
    assert api["scenario_probe"]["passed"] == 108
    assert api["scenario_probe"]["failed"] == 0
    assert api["scenario_probe"]["failure_ids"] == []
    assert sorted(api["scenario_probe"]["subclients"]) == [
        "artifacts",
        "chat",
        "mind_maps",
        "notebooks",
        "notes",
        "research",
        "settings",
        "sharing",
        "sources",
    ]
    assert "artifacts.generate_report" in api["scenario_probe"]["scenario_ids"]
    assert "chat.ask" in api["scenario_probe"]["scenario_ids"]
    assert "sources.add_file" in api["scenario_probe"]["scenario_ids"]


def test_phase9_audit_executes_client_lifecycle_and_model_behavior(
    repo_root: Path, tmp_path: Path
) -> None:
    audit = _load_audit_module(repo_root)

    report = audit.build_report(repo_root=repo_root, work_dir=tmp_path / "audit-work")
    lifecycle = report["client_lifecycle"]
    models = report["model_behavior"]

    assert lifecycle["async_methods"] == {
        "close": "pass",
        "drain": "pass",
        "refresh_auth": "pass",
        "rpc_call": "pass",
    }
    assert lifecycle["classmethods"] == {"from_storage": "pass"}
    assert lifecycle["properties"] == {"auth": "pass", "is_connected": "pass"}
    assert models["dataclasses_checked"] == 31
    assert models["roundtrip_helpers_checked"] >= 8
    assert models["redaction_checked"] is True


def test_phase9_audit_script_json_and_strict_modes_are_clean(
    repo_root: Path, tmp_path: Path
) -> None:
    env = _clean_env(tmp_path)

    json_proc = _run(
        [sys.executable, "scripts/api_behavior_parity_audit.py", "--json"],
        cwd=repo_root,
        env=env,
    )
    assert json_proc.returncode == 0, json_proc.stderr + json_proc.stdout
    assert str(tmp_path) not in json_proc.stdout
    assert str(Path.home()) not in json_proc.stdout
    data = json.loads(json_proc.stdout)
    assert data["overall_status"] == "pass"
    assert data["strict_exit_code"] == 0
    assert data["category_promotion"] == {"api": True}
    assert data["api_behavior"]["scenario_probe"]["passed"] == 108

    strict_proc = _run(
        [sys.executable, "scripts/api_behavior_parity_audit.py", "--json", "--strict"],
        cwd=repo_root,
        env=env,
    )
    assert strict_proc.returncode == 0, strict_proc.stderr + strict_proc.stdout
    strict_data = json.loads(strict_proc.stdout)
    assert strict_data["strict_exit_code"] == 0


def test_phase9_audit_human_output_is_compact(repo_root: Path, tmp_path: Path) -> None:
    proc = _run(
        [sys.executable, "scripts/api_behavior_parity_audit.py"],
        cwd=repo_root,
        env=_clean_env(tmp_path),
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "ZeroNotebookLM API behavior audit: pass" in proc.stdout
    assert "API async behavior: 108/108" in proc.stdout
    assert "client lifecycle: pass" in proc.stdout
    assert "category promotion: api=true" in proc.stdout
    assert len(proc.stdout.splitlines()) <= 6
