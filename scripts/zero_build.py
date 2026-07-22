"""Tiny stdlib PEP 517 backend for the zero-runtime-dependency wheel.

The project intentionally installs with ``--no-build-isolation --no-deps`` in a
fresh venv. Python 3.14 venvs no longer guarantee ``setuptools`` is present, so
using ``setuptools.build_meta`` made the package depend on an undeclared build
tool. This backend builds the pure-Python wheel directly with the stdlib.
"""

from __future__ import annotations

import os
import re
import tarfile
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback.
    tomllib = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"


def _project() -> dict[str, Any]:
    text = PYPROJECT.read_text(encoding="utf-8")
    if tomllib is not None:
        return tomllib.loads(text)["project"]
    # Python 3.10 fallback: parse only this repository's simple project table.
    project: dict[str, Any] = {"scripts": {}, "urls": {}}
    section = ""
    current_array: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line.strip("[]")
            current_array = None
            continue
        if current_array:
            if line == "]":
                current_array = None
            elif line.startswith('"'):
                project.setdefault(current_array, []).append(line.strip('",'))
            continue
        if section == "project" and line.endswith("["):
            key = line.split("=", 1)[0].strip()
            project[key] = []
            current_array = key
            continue
        if "=" not in line:
            continue
        key, value = [part.strip() for part in line.split("=", 1)]
        value = value.strip('"')
        if section == "project":
            if value == "[]":
                project[key] = []
            else:
                project[key] = value
        elif section == "project.scripts":
            project["scripts"][key] = value
        elif section == "project.urls":
            project["urls"][key] = value
    return project


def _dist_base() -> str:
    project = _project()
    name = re.sub(r"[^\w\d.]+", "_", project["name"]).strip("_")
    return f"{name}-{project['version']}"


def _dist_info_files() -> dict[str, str]:
    from build_wheel import (
        load_project,
        render_entry_points,
        render_metadata,
        render_wheel_metadata,
    )

    project = load_project()
    dist = project.dist_info
    return {
        f"{dist}/METADATA": render_metadata(project),
        f"{dist}/WHEEL": render_wheel_metadata(),
        f"{dist}/entry_points.txt": render_entry_points(project),
        f"{dist}/licenses/LICENSE": (ROOT / "LICENSE").read_text(encoding="utf-8"),
        f"{dist}/licenses/THIRD_PARTY_NOTICES.md": (
            ROOT / "THIRD_PARTY_NOTICES.md"
        ).read_text(encoding="utf-8"),
    }


def _write_metadata(metadata_directory: str | os.PathLike[str]) -> str:
    dist = _dist_base() + ".dist-info"
    target = Path(metadata_directory) / dist
    target.mkdir(parents=True, exist_ok=True)
    for arcname, text in _dist_info_files().items():
        path = Path(metadata_directory) / arcname
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return dist


def get_requires_for_build_wheel(config_settings=None) -> list[str]:
    return []


def get_requires_for_build_sdist(config_settings=None) -> list[str]:
    return []


def prepare_metadata_for_build_wheel(
    metadata_directory: str, config_settings=None
) -> str:
    return _write_metadata(metadata_directory)


def build_wheel(
    wheel_directory: str,
    config_settings=None,
    metadata_directory: str | None = None,
) -> str:
    from build_wheel import build_wheel as build_deterministic_wheel

    return build_deterministic_wheel(Path(wheel_directory)).name


def build_sdist(sdist_directory: str, config_settings=None) -> str:
    filename = _dist_base() + ".tar.gz"
    target = Path(sdist_directory) / filename
    prefix = _dist_base()
    files = [
        ROOT / name
        for name in (
            "CHANGELOG.md",
            "LICENSE",
            "README.md",
            "SECURITY.md",
            "THIRD_PARTY_NOTICES.md",
            "pyproject.toml",
            "compat/auth_matrix.json",
            "scripts/build_wheel.py",
            "scripts/zero_build.py",
        )
    ]
    for base in (ROOT / "notebooklm", ROOT / "notebooklm_bare"):
        files.extend(
            path
            for path in sorted(base.rglob("*"))
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix not in {".pyc", ".pyo"}
        )
    with tarfile.open(target, "w:gz") as tf:
        for path in files:
            tf.add(path, arcname=f"{prefix}/{path.relative_to(ROOT).as_posix()}")
    return filename
