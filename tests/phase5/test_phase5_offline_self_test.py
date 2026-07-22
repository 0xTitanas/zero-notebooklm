"""Phase 5E bundled offline self-test proof.

This phase adds a package-contained self-test that validates committed synthetic
fixtures from an installed artifact without reading live NotebookLM, browser,
keychain, or ambient home state.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import venv
import zipfile
from pathlib import Path

SYNTHETIC_NOTEBOOK_ID = "fake-notebook-0001"
SYNTHETIC_QUESTION = "Phase 0 synthetic question."
WHEEL_NAME = "zero_notebooklm-0.7.2-py3-none-any.whl"


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
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )


def _clean_env(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    clean_home = tmp_path / "home"
    clean_cwd = tmp_path / "cwd"
    clean_home.mkdir(parents=True)
    clean_cwd.mkdir(parents=True)
    env = {
        "HOME": str(clean_home),
        "USERPROFILE": str(clean_home),
        "NOTEBOOKLM_HOME": str(clean_home / ".notebooklm"),
        "PYTHONNOUSERSITE": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    }
    return clean_home, clean_cwd, env


def _make_venv(tmp_path: Path) -> Path:
    env_dir = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True).create(env_dir)
    if sys.platform == "win32":  # pragma: no cover - CI/dev path is POSIX today.
        return env_dir / "Scripts" / "python.exe"
    return env_dir / "bin" / "python"


def _build_wheel(tmp_path: Path) -> Path:
    repo = _repo_root()
    dist_dir = tmp_path / "dist"
    proc = _run(
        [sys.executable, "scripts/build_wheel.py", "--dist-dir", str(dist_dir)],
        cwd=repo,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    wheel = dist_dir / WHEEL_NAME
    assert wheel.is_file()
    return wheel


def _assert_passed_self_test(stdout: str) -> dict[str, object]:
    payload = json.loads(stdout)
    assert payload["status"] == "passed"
    assert payload["live_enabled"] is False
    assert payload["read_only"] is True
    assert payload["fixture_source"] == "packaged"
    assert payload["notebook_id"] == SYNTHETIC_NOTEBOOK_ID
    checks = payload["checks"]
    assert checks["packaged_rpc_fixtures"]["ok"] is True
    assert checks["fake_rpc_round_trip"]["ok"] is True
    assert checks["fake_rpc_round_trip"]["sources"] >= 1
    assert checks["fake_rpc_round_trip"]["notes"] >= 1
    assert checks["fake_rpc_round_trip"]["artifacts"] >= 1
    assert checks["chat_fixture"]["ok"] is True
    assert checks["parity_runtime_categories"]["ok"] is True
    assert "cli" in checks["parity_runtime_categories"]["supported"]
    assert "self-test" in checks["parity_runtime_categories"]["supported"]
    assert checks["question"]["value"] == SYNTHETIC_QUESTION
    return payload


def test_phase5e_matrix_keeps_auth_open_after_cli_api_promotion() -> None:
    matrix = (_repo_root() / "compat" / "parity_matrix.md").read_text(encoding="utf-8")
    rows = {}
    for line in matrix.splitlines():
        if not line.startswith("|") or line.startswith("| ---"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) == 4 and cells[0] in {
            "cli",
            "api",
            "auth",
            "rpc",
            "offline",
            "self-test",
        }:
            rows[cells[0]] = cells[3]
    assert rows["self-test"] == "pass"
    assert rows["offline"] in {"open", "pass"}
    assert rows["cli"] == "pass"
    assert rows["api"] == "pass"
    for category in ("auth",):
        assert rows[category] == "open"


def test_offline_self_test_module_reports_json_without_home_access(
    tmp_path: Path,
) -> None:
    repo = _repo_root()
    clean_home, clean_cwd, env = _clean_env(tmp_path)
    env["PYTHONPATH"] = str(repo)

    proc = _run(
        [sys.executable, "-m", "notebooklm.self_test", "--json"],
        cwd=clean_cwd,
        env=env,
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    payload = _assert_passed_self_test(proc.stdout)
    assert str(clean_home) not in proc.stdout + proc.stderr
    assert payload["home_touched"] is False
    assert not (clean_home / ".notebooklm").exists()


def test_offline_self_test_human_output_is_pathless(tmp_path: Path) -> None:
    repo = _repo_root()
    clean_home, clean_cwd, env = _clean_env(tmp_path)
    env["PYTHONPATH"] = str(repo)

    proc = _run([sys.executable, "-m", "notebooklm.self_test"], cwd=clean_cwd, env=env)

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "ZeroNotebookLM offline self-test: passed" in proc.stdout
    assert "packaged_rpc_fixtures: ok" in proc.stdout
    assert "fake_rpc_round_trip: ok" in proc.stdout
    assert "chat_fixture: ok" in proc.stdout
    assert str(clean_home) not in proc.stdout + proc.stderr


def test_offline_self_test_is_bundled_in_wheel_and_runs_from_clean_install(
    tmp_path: Path,
) -> None:
    wheel = _build_wheel(tmp_path)
    with zipfile.ZipFile(wheel) as zf:
        names = set(zf.namelist())
    assert "notebooklm/self_test.py" in names
    assert "notebooklm/data/rpc_fixtures/list_notebooks.response.txt" in names

    python_exe = _make_venv(tmp_path)
    install = _run(
        [str(python_exe), "-m", "pip", "install", "--no-deps", str(wheel)],
        cwd=tmp_path,
        env={"PIP_DISABLE_PIP_VERSION_CHECK": "1"},
    )
    assert install.returncode == 0, install.stderr + install.stdout

    clean_home, clean_cwd, env = _clean_env(tmp_path / "installed")
    proc = _run(
        [str(python_exe), "-m", "notebooklm.self_test", "--json"],
        cwd=clean_cwd,
        env=env,
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    payload = _assert_passed_self_test(proc.stdout)
    assert payload["package"] == "notebooklm"
    assert str(clean_home) not in proc.stdout + proc.stderr
    assert not (clean_home / ".notebooklm").exists()
