#!/usr/bin/env python3
"""Build a deterministic stdlib-only ZeroNotebookLM wheel artifact.

The normal packaging metadata in ``pyproject.toml`` remains the source of truth.
This script exists because the Phase 5 closed-system artifact proof cannot rely
on the optional third-party ``wheel`` or ``build`` packages being installed.
"""

from __future__ import annotations

import argparse
import ast
import base64
import csv
import hashlib
import io
import re
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from email.message import Message
from pathlib import Path
from typing import Any

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised by import-block test.
    tomllib = None  # type: ignore[assignment]

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"
DEFAULT_DIST_DIR = REPO_ROOT / "dist"
FIXED_ZIP_TIMESTAMP = (2026, 1, 1, 0, 0, 0)
PACKAGE_ROOTS = ("notebooklm",)
EXCLUDED_PARTS = {"__pycache__"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}
EXTRA_PAYLOADS = (("compat/auth_matrix.json", "notebooklm/data/auth_matrix.json"),)


@dataclass(frozen=True)
class WheelProject:
    name: str
    normalized_name: str
    version: str
    description: str
    license: str
    requires_python: str
    classifiers: tuple[str, ...]
    project_urls: tuple[tuple[str, str], ...]
    scripts: tuple[tuple[str, str], ...]

    @property
    def dist_info(self) -> str:
        return f"{self.normalized_name}-{self.version}.dist-info"

    @property
    def wheel_name(self) -> str:
        return f"{self.normalized_name}-{self.version}-py3-none-any.whl"


def _normalize_distribution_name(name: str) -> str:
    return re.sub(r"[-_.]+", "_", name).lower()


def _parse_toml_literal(value: str) -> Any:
    if value == "true":
        return True
    if value == "false":
        return False
    return ast.literal_eval(value)


def _parse_limited_pyproject_toml(text: str) -> dict[str, Any]:
    """Parse the subset of TOML used by this repository's pyproject.

    Python 3.10 lacks ``tomllib``. The wheel builder needs only simple string
    keys, one-line arrays, and multiline string arrays from ``pyproject.toml``;
    keeping that fallback local avoids a build-time dependency on ``tomli``.
    """

    root: dict[str, Any] = {}
    current = root
    array_key: str | None = None
    array_values: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if array_key is not None:
            if line == "]":
                current[array_key] = array_values
                array_key = None
                array_values = []
                continue
            array_values.append(_parse_toml_literal(line.rstrip(",")))
            continue

        if line.startswith("[") and line.endswith("]"):
            current = root
            for part in line.strip("[]").split("."):
                current = current.setdefault(part, {})
            continue

        key, raw_value = line.split("=", 1)
        key = key.strip()
        value = raw_value.strip()
        if value == "[":
            array_key = key
            array_values = []
        else:
            current[key] = _parse_toml_literal(value)

    if array_key is not None:
        raise ValueError(f"unterminated array for {array_key!r}")
    return root


def _load_pyproject(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if tomllib is not None:
        return tomllib.loads(text)
    return _parse_limited_pyproject_toml(text)


def load_project(path: Path = PYPROJECT) -> WheelProject:
    data = _load_pyproject(path)
    project = data["project"]
    scripts = tuple(sorted(project.get("scripts", {}).items()))
    urls = tuple(sorted(project.get("urls", {}).items()))
    return WheelProject(
        name=project["name"],
        normalized_name=_normalize_distribution_name(project["name"]),
        version=project["version"],
        description=project.get("description", ""),
        license=project.get("license", ""),
        requires_python=project["requires-python"],
        classifiers=tuple(project.get("classifiers", ())),
        project_urls=urls,
        scripts=scripts,
    )


def iter_package_files(repo: Path = REPO_ROOT) -> Iterable[Path]:
    for root_name in PACKAGE_ROOTS:
        root = repo / root_name
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(repo)
            if any(part in EXCLUDED_PARTS for part in rel.parts):
                continue
            if path.suffix in EXCLUDED_SUFFIXES:
                continue
            yield rel


def _message_text(message: Message) -> str:
    return message.as_string(policy=None).replace("\n", "\r\n")


def render_metadata(project: WheelProject) -> str:
    message = Message()
    message["Metadata-Version"] = "2.3"
    message["Name"] = project.name
    message["Version"] = project.version
    if project.description:
        message["Summary"] = project.description
    if project.license:
        message["License"] = project.license
    message["Requires-Python"] = project.requires_python
    for classifier in project.classifiers:
        message["Classifier"] = classifier
    for label, url in project.project_urls:
        message["Project-URL"] = f"{label}, {url}"
    return _message_text(message)


def render_wheel_metadata() -> str:
    return "\n".join(
        [
            "Wheel-Version: 1.0",
            "Generator: zero-notebooklm scripts/build_wheel.py",
            "Root-Is-Purelib: true",
            "Tag: py3-none-any",
            "",
        ]
    )


def render_entry_points(project: WheelProject) -> str:
    lines = ["[console_scripts]"]
    lines.extend(f"{name} = {target}" for name, target in project.scripts)
    lines.append("")
    return "\n".join(lines)


def _record_hash(data: bytes) -> str:
    digest = hashlib.sha256(data).digest()
    encoded = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return f"sha256={encoded}"


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=FIXED_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    return info


def _record_text(rows: list[tuple[str, str, str]]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerows(rows)
    return buffer.getvalue()


def build_wheel(dist_dir: Path = DEFAULT_DIST_DIR) -> Path:
    project = load_project()
    dist_dir.mkdir(parents=True, exist_ok=True)
    wheel_path = dist_dir / project.wheel_name

    entries: list[tuple[str, bytes]] = []
    for rel in iter_package_files():
        entries.append((rel.as_posix(), (REPO_ROOT / rel).read_bytes()))
    for source, destination in EXTRA_PAYLOADS:
        entries.append((destination, (REPO_ROOT / source).read_bytes()))

    dist_info = project.dist_info
    entries.extend(
        [
            (f"{dist_info}/METADATA", render_metadata(project).encode("utf-8")),
            (f"{dist_info}/WHEEL", render_wheel_metadata().encode("utf-8")),
            (
                f"{dist_info}/entry_points.txt",
                render_entry_points(project).encode("utf-8"),
            ),
        ]
    )
    entries.extend(
        (
            f"{dist_info}/licenses/{name}",
            (REPO_ROOT / name).read_bytes(),
        )
        for name in ("LICENSE", "THIRD_PARTY_NOTICES.md")
    )

    entries.sort(key=lambda item: item[0])
    record_name = f"{dist_info}/RECORD"
    record_rows = [(name, _record_hash(data), str(len(data))) for name, data in entries]
    record_rows.append((record_name, "", ""))

    with zipfile.ZipFile(wheel_path, "w") as zf:
        for name, data in entries:
            zf.writestr(_zip_info(name), data)
        zf.writestr(_zip_info(record_name), _record_text(record_rows).encode("utf-8"))
    return wheel_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build_wheel.py")
    parser.add_argument(
        "--dist-dir",
        default=str(DEFAULT_DIST_DIR),
        help="directory where the .whl artifact should be written",
    )
    args = parser.parse_args(argv)
    wheel = build_wheel(Path(args.dist_dir))
    print(wheel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
