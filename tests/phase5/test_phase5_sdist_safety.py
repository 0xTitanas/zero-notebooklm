from __future__ import annotations

import importlib.util
import tarfile
from pathlib import Path


def test_sdist_uses_public_allowlist(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("zero_build_test", repo / "scripts/zero_build.py")
    assert spec is not None and spec.loader is not None
    backend = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backend)

    root = tmp_path / "project"
    for name in (
        "CHANGELOG.md",
        "LICENSE",
        "README.md",
        "SECURITY.md",
        "THIRD_PARTY_NOTICES.md",
        "compat/auth_matrix.json",
        "scripts/build_wheel.py",
        "scripts/zero_build.py",
    ):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("public\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "zero-notebooklm"\nversion = "0.7.2"\n', encoding="utf-8"
    )
    (root / "notebooklm").mkdir()
    (root / "notebooklm" / "__init__.py").write_text("", encoding="utf-8")
    (root / "notebooklm_bare").mkdir()
    (root / "notebooklm_bare" / "__init__.py").write_text("", encoding="utf-8")
    (root / ".ai-bridge").mkdir()
    (root / ".ai-bridge" / "private.txt").write_text("private\n", encoding="utf-8")

    backend.ROOT = root
    backend.PYPROJECT = root / "pyproject.toml"
    out = tmp_path / "dist"
    out.mkdir()
    archive = out / backend.build_sdist(str(out))

    with tarfile.open(archive) as tf:
        names = set(tf.getnames())
    assert any(name.endswith("/LICENSE") for name in names)
    assert not any(".ai-bridge" in name for name in names)
