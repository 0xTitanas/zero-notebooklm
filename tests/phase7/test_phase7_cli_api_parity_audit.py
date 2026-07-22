"""Phase 7 CLI/API parity audit gate.

This phase is not MCP and not a parity-row promotion. It adds a broad,
machine-readable audit over the already implemented bare CLI/API surface so the
next release/live decision is based on all 90 CLI leaves and all pinned API
sub-clients, not a single micro-slice.
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path


def _load_script(repo: Path):
    sys.path.insert(0, str(repo / "scripts"))
    try:
        import cli_api_parity_audit

        return importlib.reload(cli_api_parity_audit)
    finally:
        try:
            sys.path.remove(str(repo / "scripts"))
        except ValueError:
            pass


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
        timeout=30,
    )


def test_phase7_audit_is_pure_broad_and_non_promotional(
    repo_root: Path, monkeypatch
) -> None:
    audit = _load_script(repo_root)

    def _forbidden_home() -> Path:
        raise AssertionError("Phase 7 CLI/API audit must not inspect user home")

    monkeypatch.setattr(Path, "home", _forbidden_home)
    before = (repo_root / "compat" / "parity_matrix.md").read_text(encoding="utf-8")

    report = audit.build_report(repo_root=repo_root)

    after = (repo_root / "compat" / "parity_matrix.md").read_text(encoding="utf-8")
    assert after == before
    assert report["schema_version"] == "cli_api_parity_audit/1"
    assert report["target"] == "notebooklm-py==0.7.2"
    assert report["category_promotion"] == {
        "cli": True,
        "api": True,
    }
    assert report["category_states"] == {
        "cli": "pass",
        "api": "pass",
    }
    assert report["live_access"] is False
    assert report["mcp_implementation"] is False


def test_phase7_audit_covers_all_cli_leaf_help(repo_root: Path) -> None:
    audit = _load_script(repo_root)

    report = audit.build_report(repo_root=repo_root)
    cli = report["cli"]

    assert cli["oracle_leaf_commands"] == 90
    assert cli["root_command_count"] == 27
    assert cli["help_probe"]["total"] == 90
    assert cli["help_probe"]["passed"] == 90
    assert cli["help_probe"]["failed"] == 0
    assert cli["help_probe"]["failure_commands"] == []
    assert "notebooklm list" in cli["help_probe"]["commands"]
    assert "notebooklm source wait" in cli["help_probe"]["commands"]
    assert "notebooklm artifact suggestions" in cli["help_probe"]["commands"]


def test_phase7_audit_covers_api_exports_and_subclients(repo_root: Path) -> None:
    audit = _load_script(repo_root)

    report = audit.build_report(repo_root=repo_root)
    api = report["api"]

    assert api["oracle_public_names"] == 105
    assert api["actual_public_names"] == 105
    assert api["missing_public_names"] == []
    assert api["extra_public_names"] == []
    assert api["subclients"]["total"] == 9
    assert api["subclients"]["passed"] == 9
    assert api["subclients"]["failed"] == 0
    assert api["subclients"]["failure_names"] == []
    assert sorted(api["subclients"]["names"]) == [
        "client.artifacts",
        "client.chat",
        "client.mind_maps",
        "client.notebooks",
        "client.notes",
        "client.research",
        "client.settings",
        "client.sharing",
        "client.sources",
    ]


def test_phase7_audit_script_json_and_strict_modes_are_clean(
    repo_root: Path, tmp_path: Path
) -> None:
    env = _clean_env(tmp_path)

    json_proc = _run(
        [sys.executable, "scripts/cli_api_parity_audit.py", "--json"],
        cwd=repo_root,
        env=env,
    )
    assert json_proc.returncode == 0, json_proc.stderr + json_proc.stdout
    assert str(tmp_path) not in json_proc.stdout
    assert str(Path.home()) not in json_proc.stdout
    data = json.loads(json_proc.stdout)
    assert data["overall_status"] == "pass"
    assert data["strict_exit_code"] == 0
    assert data["category_promotion"] == {"cli": True, "api": True}
    assert data["cli"]["help_probe"]["passed"] == 90
    assert data["api"]["subclients"]["passed"] == 9

    strict_proc = _run(
        [sys.executable, "scripts/cli_api_parity_audit.py", "--json", "--strict"],
        cwd=repo_root,
        env=env,
    )
    assert strict_proc.returncode == 0, strict_proc.stderr + strict_proc.stdout
    strict_data = json.loads(strict_proc.stdout)
    assert strict_data["strict_exit_code"] == 0


def test_phase7_audit_human_output_is_compact(repo_root: Path, tmp_path: Path) -> None:
    proc = _run(
        [sys.executable, "scripts/cli_api_parity_audit.py"],
        cwd=repo_root,
        env=_clean_env(tmp_path),
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "ZeroNotebookLM CLI/API audit: pass" in proc.stdout
    assert "CLI leaf help: 90/90" in proc.stdout
    assert "API subclients: 9/9" in proc.stdout
    assert "category promotion: cli=true, api=true" in proc.stdout
    assert len(proc.stdout.splitlines()) <= 6
