"""Package execution entrypoint parity with pinned upstream."""

from __future__ import annotations

import subprocess
import sys


def test_python_m_notebooklm_help_routes_to_cli(repo_root):
    upstream_main = repo_root / "notebooklm-py-reference/src/notebooklm/__main__.py"
    assert "python -m notebooklm" in upstream_main.read_text(encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "notebooklm", "--help"],
        cwd=repo_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0
    assert "Usage:" in result.stdout
    assert "No module named notebooklm.__main__" not in result.stderr


def test_python_m_notebooklm_version_matches_upstream_golden(repo_root):
    expected = (repo_root / "compat/cli_golden/notebooklm--version.txt").read_text(
        encoding="utf-8"
    )

    result = subprocess.run(
        [sys.executable, "-m", "notebooklm", "--version"],
        cwd=repo_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout == expected
    assert result.stderr == ""
