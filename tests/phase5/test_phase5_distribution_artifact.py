"""Phase 5D distribution artifact proof.

This phase proves ZeroNotebookLM can build a closed-system wheel artifact using
only the stdlib + existing project metadata, then install and run that artifact
from a clean virtual environment without reading live NotebookLM, browser, or
keychain state by default.
"""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import venv
import zipfile
from pathlib import Path

SYNTHETIC_NOTEBOOK_ID = "fake-notebook-0001"
DIST_INFO = "zero_notebooklm-0.7.2.dist-info"
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


def _make_venv(tmp_path: Path) -> Path:
    env_dir = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True).create(env_dir)
    if sys.platform == "win32":  # pragma: no cover - CI/dev path is POSIX today.
        return env_dir / "Scripts" / "python.exe"
    return env_dir / "bin" / "python"


def _console_bin(python_exe: Path, name: str) -> Path:
    scripts = python_exe.parent
    suffix = ".exe" if sys.platform == "win32" else ""
    return scripts / f"{name}{suffix}"


def _build_wheel(tmp_path: Path) -> Path:
    repo = _repo_root()
    dist_dir = tmp_path / "dist"
    proc = _run(
        [
            sys.executable,
            "scripts/build_wheel.py",
            "--dist-dir",
            str(dist_dir),
        ],
        cwd=repo,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert proc.stdout.strip().endswith(WHEEL_NAME)
    wheel = dist_dir / WHEEL_NAME
    assert wheel.is_file()
    return wheel


def test_wheel_artifact_contains_metadata_entry_points_and_package_data(
    tmp_path: Path,
) -> None:
    wheel = _build_wheel(tmp_path)
    with zipfile.ZipFile(wheel) as zf:
        names = set(zf.namelist())
        assert "notebooklm/__init__.py" in names
        assert "notebooklm/cli.py" in names
        assert "notebooklm/rpc/__init__.py" in names
        assert "notebooklm/data/auth_matrix.json" in names
        assert "notebooklm/data/rpc_fixtures/list_notebooks.response.txt" in names
        assert f"{DIST_INFO}/METADATA" in names
        assert f"{DIST_INFO}/WHEEL" in names
        assert f"{DIST_INFO}/entry_points.txt" in names
        assert f"{DIST_INFO}/licenses/LICENSE" in names
        assert f"{DIST_INFO}/licenses/THIRD_PARTY_NOTICES.md" in names
        assert f"{DIST_INFO}/RECORD" in names

        metadata = zf.read(f"{DIST_INFO}/METADATA").decode("utf-8")
        metadata_lines = set(metadata.splitlines())
        assert "Metadata-Version: 2.3" in metadata_lines
        assert "Name: zero-notebooklm" in metadata_lines
        assert "Version: 0.7.2" in metadata_lines
        assert "Requires-Python: >=3.10" in metadata_lines
        assert "License: MIT" in metadata_lines
        assert "Requires-Dist:" not in metadata

        wheel_metadata = zf.read(f"{DIST_INFO}/WHEEL").decode("utf-8")
        assert "Wheel-Version: 1.0\n" in wheel_metadata
        assert "Root-Is-Purelib: true\n" in wheel_metadata
        assert "Tag: py3-none-any\n" in wheel_metadata

        entry_points = zf.read(f"{DIST_INFO}/entry_points.txt").decode("utf-8")
        assert "[console_scripts]\n" in entry_points
        assert "notebooklm = notebooklm.cli:console\n" in entry_points
        assert "zero-notebooklm = notebooklm.cli:console\n" in entry_points

        record_rows = list(
            csv.reader(zf.read(f"{DIST_INFO}/RECORD").decode("utf-8").splitlines())
        )
        record_names = {row[0] for row in record_rows}
        assert names == record_names
        for row in record_rows:
            assert len(row) == 3
            if row[0].endswith("/RECORD"):
                assert row[1:] == ["", ""]
            else:
                assert row[1].startswith("sha256=")
                assert row[2].isdigit()


def test_wheel_builder_loads_project_metadata_without_tomllib_or_tomli(
    tmp_path: Path,
) -> None:
    repo = _repo_root()
    probe = tmp_path / "probe.py"
    probe.write_text(
        """
from __future__ import annotations
import importlib.abc
import importlib.util
import sys
from pathlib import Path

repo = Path(sys.argv[1])

class BlockToml(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {'tomllib', 'tomli'}:
            raise ModuleNotFoundError(fullname)
        return None

sys.meta_path.insert(0, BlockToml())
spec = importlib.util.spec_from_file_location('build_wheel_probe', repo / 'scripts/build_wheel.py')
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
project = module.load_project(repo / 'pyproject.toml')
print(project.name)
print(project.version)
print(project.requires_python)
""".lstrip(),
        encoding="utf-8",
    )
    proc = _run([sys.executable, str(probe), str(repo)], cwd=repo)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert proc.stdout.strip().splitlines() == [
        "zero-notebooklm",
        "0.7.2",
        ">=3.10",
    ]


def test_wheel_build_is_deterministic(tmp_path: Path) -> None:
    first = _build_wheel(tmp_path / "first")
    second = _build_wheel(tmp_path / "second")
    assert first.read_bytes() == second.read_bytes()


def test_wheel_artifact_installs_console_launcher_with_packaged_fixtures(
    tmp_path: Path,
) -> None:
    wheel = _build_wheel(tmp_path)
    python_exe = _make_venv(tmp_path)

    install = _run(
        [str(python_exe), "-m", "pip", "install", "--no-deps", str(wheel)],
        cwd=tmp_path,
        env={"PIP_DISABLE_PIP_VERSION_CHECK": "1"},
    )
    assert install.returncode == 0, install.stderr + install.stdout

    probe = _run(
        [
            str(python_exe),
            "-c",
            (
                "import importlib.metadata as m, notebooklm; "
                "dist=m.metadata('zero-notebooklm'); "
                "requires=m.requires('zero-notebooklm'); "
                "eps=sorted(ep.name for ep in m.entry_points(group='console_scripts') "
                "if ep.value == 'notebooklm.cli:console'); "
                "print(notebooklm.__version__); "
                "print(dist['Name']); "
                "print(requires); "
                "print(','.join(eps))"
            ),
        ],
        cwd=tmp_path,
    )
    assert probe.returncode == 0, probe.stderr + probe.stdout
    assert probe.stdout.strip().splitlines() == [
        "0.7.2",
        "zero-notebooklm",
        "None",
        "notebooklm,zero-notebooklm",
    ]

    console = _console_bin(python_exe, "notebooklm")
    alias = _console_bin(python_exe, "zero-notebooklm")
    assert console.is_file()
    assert alias.is_file()

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

    doctor = _run(
        [str(console), "doctor", "--auth-matrix", "--json"],
        cwd=clean_cwd,
        env=env,
    )
    assert doctor.returncode == 0, doctor.stderr + doctor.stdout
    assert json.loads(doctor.stdout)["summary"]["total_rows"] == 146

    list_proc = _run([str(console), "list", "--json"], cwd=clean_cwd, env=env)
    assert list_proc.returncode == 0, list_proc.stderr + list_proc.stdout
    assert json.loads(list_proc.stdout)["notebooks"][0]["id"] == SYNTHETIC_NOTEBOOK_ID

    alias_proc = _run([str(alias), "list", "--json"], cwd=clean_cwd, env=env)
    assert alias_proc.returncode == 0, alias_proc.stderr + alias_proc.stdout
    assert json.loads(alias_proc.stdout)["notebooks"][0]["id"] == SYNTHETIC_NOTEBOOK_ID
