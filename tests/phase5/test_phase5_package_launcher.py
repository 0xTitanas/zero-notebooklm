"""Phase 5A package/launcher closed-system proof.

This phase proves the repository can be installed locally as a zero-runtime-
dependency package and that the installed console launcher still runs the
fixture-backed CLI from an arbitrary clean cwd/HOME without reading live
NotebookLM, browser, or keychain state by default.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib  # type: ignore[no-redef]


SYNTHETIC_NOTEBOOK_ID = "fake-notebook-0001"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _run(
    cmd: list[str], *, cwd: Path, env: dict[str, str] | None = None
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
        timeout=120,
    )


def _make_venv(tmp_path: Path) -> Path:
    env_dir = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True).create(env_dir)
    if sys.platform == "win32":  # pragma: no cover - CI/dev path is POSIX today.
        return env_dir / "Scripts" / "python.exe"
    return env_dir / "bin" / "python"


def _cleanup_source_build_artifacts(repo: Path) -> None:
    for name in ("build", "dist"):
        shutil.rmtree(repo / name, ignore_errors=True)
    for path in repo.glob("*.egg-info"):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)


def _console_bin(python_exe: Path, name: str) -> Path:
    scripts = python_exe.parent
    suffix = ".exe" if sys.platform == "win32" else ""
    return scripts / f"{name}{suffix}"


def test_pyproject_declares_zero_runtime_dependency_distribution() -> None:
    repo = _repo_root()
    pyproject = repo / "pyproject.toml"
    assert pyproject.is_file(), "Phase 5A requires explicit package metadata"

    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    project = data["project"]
    assert project["name"] == "zero-notebooklm"
    assert project["version"] == "0.7.2"
    assert project["requires-python"] == ">=3.10"
    assert project.get("dependencies", []) == []

    scripts = project.get("scripts", {})
    assert scripts["notebooklm"] == "notebooklm.cli:console"
    assert scripts.get("notebooklm-bare") == "notebooklm.cli:console"


def test_local_install_exposes_import_metadata_and_console_launcher(
    tmp_path: Path,
) -> None:
    repo = _repo_root()
    python_exe = _make_venv(tmp_path)
    _cleanup_source_build_artifacts(repo)
    install = _run(
        [
            str(python_exe),
            "-m",
            "pip",
            "install",
            "--no-build-isolation",
            "--no-deps",
            str(repo),
        ],
        cwd=tmp_path,
        env={"PIP_DISABLE_PIP_VERSION_CHECK": "1"},
    )
    _cleanup_source_build_artifacts(repo)
    assert install.returncode == 0, install.stderr + install.stdout

    probe = _run(
        [
            str(python_exe),
            "-c",
            (
                "import importlib.metadata as m, notebooklm; "
                "dist=m.metadata('zero-notebooklm'); "
                "requires=m.requires('zero-notebooklm'); "
                "print(notebooklm.__version__); "
                "print(dist['Name']); "
                "print(requires)"
            ),
        ],
        cwd=tmp_path,
    )
    assert probe.returncode == 0, probe.stderr + probe.stdout
    lines = probe.stdout.strip().splitlines()
    assert lines == ["0.7.2", "zero-notebooklm", "None"]

    console = _console_bin(python_exe, "notebooklm")
    assert console.is_file(), "notebooklm console launcher was not installed"
    alias = _console_bin(python_exe, "notebooklm-bare")
    assert alias.is_file(), "notebooklm-bare console launcher was not installed"

    clean_home = tmp_path / "home"
    clean_cwd = tmp_path / "cwd"
    clean_home.mkdir()
    clean_cwd.mkdir()
    env = {
        "HOME": str(clean_home),
        "USERPROFILE": str(clean_home),
        "NOTEBOOKLM_HOME": str(clean_home / ".notebooklm"),
        "PYTHONNOUSERSITE": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    }

    help_proc = _run([str(console), "--help"], cwd=clean_cwd, env=env)
    assert help_proc.returncode == 0, help_proc.stderr + help_proc.stdout
    assert "usage: notebooklm" in help_proc.stdout
    assert "source" in help_proc.stdout
    assert "auth" in help_proc.stdout

    list_proc = _run([str(console), "list", "--json"], cwd=clean_cwd, env=env)
    assert list_proc.returncode == 0, list_proc.stderr + list_proc.stdout
    payload = json.loads(list_proc.stdout)
    assert isinstance(payload, dict)
    assert payload["count"] == 1
    assert payload["notebooks"][0]["id"] == SYNTHETIC_NOTEBOOK_ID

    alias_proc = _run([str(alias), "list", "--json"], cwd=clean_cwd, env=env)
    assert alias_proc.returncode == 0, alias_proc.stderr + alias_proc.stdout
    assert json.loads(alias_proc.stdout)["notebooks"][0]["id"] == SYNTHETIC_NOTEBOOK_ID
