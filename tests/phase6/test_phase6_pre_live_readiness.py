"""Phase 6 pre-live parity readiness gate.

This phase is intentionally a full-phase readiness checkpoint, not another parity
row promotion. It turns the remaining open surfaces into machine-readable stop
conditions while preserving the live/auth safety gates.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


EXPECTED_OPEN_CATEGORIES = {"auth"}
EXPECTED_PASS_CATEGORIES = {"cli", "api", "offline", "self-test", "rpc"}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _run(
    cmd: list[str], *, cwd: Path, env: dict[str, str] | None = None, timeout: int = 120
) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    merged.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    merged.setdefault("PYTHONIOENCODING", "utf-8")
    merged.setdefault("LANG", "C.UTF-8")
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        env=merged,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )


def _clean_env(tmp_path: Path) -> dict[str, str]:
    clean_home = tmp_path / "home"
    tmp_dir = tmp_path / "tmp"
    clean_home.mkdir(parents=True)
    tmp_dir.mkdir(parents=True)
    return {
        "HOME": str(clean_home),
        "USERPROFILE": str(clean_home),
        "NOTEBOOKLM_HOME": str(clean_home / ".notebooklm"),
        "TMPDIR": str(tmp_dir),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
        "LANG": "C.UTF-8",
    }


def _load_script(repo: Path):
    sys.path.insert(0, str(repo / "scripts"))
    try:
        import parity_readiness

        return parity_readiness
    finally:
        try:
            sys.path.remove(str(repo / "scripts"))
        except ValueError:
            pass


def test_phase6_readiness_report_is_pure_and_keeps_live_gates_closed(
    repo_root: Path, monkeypatch
) -> None:
    parity_readiness = _load_script(repo_root)

    def boom_home() -> Path:
        raise AssertionError("Phase 6 readiness must not consult Path.home()")

    monkeypatch.setattr(Path, "home", staticmethod(boom_home))

    report = parity_readiness.build_report(repo_root=repo_root)

    assert report["schema_version"] == "parity_readiness/1"
    assert report["target"] == "notebooklm-py==0.7.2"
    assert report["release_ready"] is False
    assert report["live_authorization_required"] is True
    assert report["mcp_next_phase_allowed"] is True
    assert report["live_smoke_default"]["status"] == "skipped"
    assert report["live_smoke_default"]["live_enabled"] is False
    assert report["auth_readiness"]["release_blocked"] is True
    assert report["auth_readiness"]["total_rows"] == 146
    assert report["auth_readiness"]["profile_exclusion_path_count"] == 49
    assert report["auth_readiness"]["deferred_future_release_path_count"] == 10


def test_phase6_readiness_counts_remaining_open_surfaces(repo_root: Path) -> None:
    parity_readiness = _load_script(repo_root)

    report = parity_readiness.build_report(repo_root=repo_root)
    categories = report["categories"]

    assert set(report["open_categories"]) == EXPECTED_OPEN_CATEGORIES
    assert set(report["pass_categories"]) == EXPECTED_PASS_CATEGORIES
    assert report["category_state_counts"] == {"pass": 5, "open": 1, "blocked": 0}
    assert categories["cli"]["state"] == "pass"
    assert categories["cli"]["open_leaf_commands"] == 0
    assert categories["cli"]["pass_leaf_commands"] == 90
    assert categories["api"]["state"] == "pass"
    assert categories["api"]["open_subclients"] == 0
    assert categories["api"]["pass_subclients"] == 9
    assert categories["auth"]["state"] == "open"
    assert categories["rpc"]["state"] == "pass"
    assert categories["offline"]["state"] == "pass"
    assert categories["self-test"]["state"] == "pass"


def test_phase6_readiness_names_blockers_without_promoting_rows(
    repo_root: Path,
) -> None:
    parity_readiness = _load_script(repo_root)

    before = (repo_root / "compat" / "parity_matrix.md").read_text(encoding="utf-8")
    report = parity_readiness.build_report(repo_root=repo_root)
    after = (repo_root / "compat" / "parity_matrix.md").read_text(encoding="utf-8")

    assert after == before
    blockers = set(report["blockers"])
    assert {"auth_category_open", "live_smoke_not_authorized"} <= blockers
    assert "cli_category_open" not in blockers
    assert "api_category_open" not in blockers
    assert "mcp_deferred_until_cli_api_parity" not in blockers
    assert report["next_required_authorization"] == {
        "live_notebooklm_smoke": True,
        "browser_cookie_store_reads": False,
        "os_credential_backend_decrypt": False,
        "mutation_smoke": False,
    }


def test_phase6_readiness_script_json_is_pathless_and_non_live(
    repo_root: Path, tmp_path: Path
) -> None:
    env = _clean_env(tmp_path)
    proc = _run(
        [sys.executable, "scripts/parity_readiness.py", "--json"],
        cwd=repo_root,
        env=env,
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    payload = json.loads(proc.stdout)
    assert payload["release_ready"] is False
    assert payload["strict_exit_code"] == 77
    assert payload["live_smoke_default"]["status"] == "skipped"
    assert str(tmp_path / "home") not in proc.stdout + proc.stderr
    assert not (tmp_path / "home" / ".notebooklm").exists()


def test_phase6_readiness_strict_mode_fails_closed_until_release_ready(
    repo_root: Path, tmp_path: Path
) -> None:
    env = _clean_env(tmp_path)
    proc = _run(
        [sys.executable, "scripts/parity_readiness.py", "--json", "--strict"],
        cwd=repo_root,
        env=env,
    )

    assert proc.returncode == 77, proc.stderr + proc.stdout
    payload = json.loads(proc.stdout)
    assert payload["release_ready"] is False
    assert payload["strict_exit_code"] == 77
    assert payload["mcp_next_phase_allowed"] is True
    assert "mcp_deferred_until_cli_api_parity" not in payload["blockers"]


def test_phase6_readiness_human_output_is_compact(
    repo_root: Path, tmp_path: Path
) -> None:
    env = _clean_env(tmp_path)
    proc = _run(
        [sys.executable, "scripts/parity_readiness.py"],
        cwd=repo_root,
        env=env,
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "ZeroNotebookLM readiness: blocked" in proc.stdout
    assert "open categories: auth" in proc.stdout
    assert "MCP next phase: allowed" in proc.stdout
    assert str(tmp_path / "home") not in proc.stdout + proc.stderr
