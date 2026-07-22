"""Phase 4 closed-system single-file artifact tests.

The generated single-file artifact must run key fixture-backed parity surfaces from
an arbitrary clean working directory under ``python -I -S``. It must not depend on
site-packages, the checkout being on ``sys.path``, a real home directory, browser
stores, credentials, live NotebookLM RPC, or network access.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


SYNTHETIC_NOTEBOOK_ID = "fake-notebook-0001"
SYNTHETIC_SOURCE_ID = "fake-source-0001"


def _isolated_env(tmp_path: Path) -> dict[str, str]:
    env = {
        "HOME": str(tmp_path / "home"),
        "USERPROFILE": str(tmp_path / "home"),
        "TMPDIR": str(tmp_path / "tmp"),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
        "LANG": "C.UTF-8",
    }
    return env


def _run_artifact(
    repo_root: Path, tmp_path: Path, *argv: str
) -> subprocess.CompletedProcess[str]:
    artifact = repo_root / "singlefile" / "zero_notebooklm.py"
    return subprocess.run(
        [sys.executable, "-B", "-I", "-S", str(artifact), *argv],
        cwd=str(tmp_path / "cwd"),
        env=_isolated_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_singlefile_artifact_exists_and_exposes_real_fixture_backed_help(
    repo_root, tmp_path
):
    (tmp_path / "cwd").mkdir()
    (tmp_path / "tmp").mkdir()

    proc = _run_artifact(repo_root, tmp_path, "--help")

    combined = proc.stdout + proc.stderr
    assert proc.returncode == 0, combined
    assert "Usage: notebooklm [OPTIONS] COMMAND [ARGS]..." in combined
    assert "NotebookLM CLI." in combined
    assert "Quick start:" in combined
    assert "Phase 1 single-file help scaffold" not in combined
    assert "Traceback" not in combined


def test_singlefile_artifact_runs_fixture_backed_list_from_clean_cwd(
    repo_root, tmp_path
):
    (tmp_path / "cwd").mkdir()
    (tmp_path / "tmp").mkdir()

    proc = _run_artifact(repo_root, tmp_path, "list", "--json")

    combined = proc.stdout + proc.stderr
    assert proc.returncode == 0, combined
    payload = json.loads(proc.stdout)
    assert payload["count"] >= 1
    assert payload["notebooks"][0]["id"] == SYNTHETIC_NOTEBOOK_ID
    assert str(repo_root) not in combined
    assert "Traceback" not in combined


def test_singlefile_artifact_embeds_rpc_status_and_package_data(repo_root, tmp_path):
    (tmp_path / "cwd").mkdir()
    (tmp_path / "tmp").mkdir()

    guide = _run_artifact(
        repo_root,
        tmp_path,
        "source",
        "guide",
        SYNTHETIC_SOURCE_ID,
        "--notebook",
        SYNTHETIC_NOTEBOOK_ID,
        "--json",
    )
    assert guide.returncode == 0, guide.stdout + guide.stderr
    guide_payload = json.loads(guide.stdout)
    assert guide_payload["source_id"] == SYNTHETIC_SOURCE_ID
    assert guide_payload["kind"] == "WEB_PAGE"
    assert "notebooklm-bare" in guide_payload["keywords"]

    status = _run_artifact(
        repo_root, tmp_path, "artifact", "poll", "fake-artifact-audio-0001", "--json"
    )
    assert status.returncode == 0, status.stdout + status.stderr
    status_payload = json.loads(status.stdout)
    assert status_payload["status"] == "completed"

    completion = _run_artifact(repo_root, tmp_path, "completion", "bash")
    assert completion.returncode == 0, completion.stdout + completion.stderr
    assert "notebooklm" in completion.stdout


def test_singlefile_artifact_auth_matrix_diagnostic_is_readonly(repo_root, tmp_path):
    (tmp_path / "cwd").mkdir()
    (tmp_path / "tmp").mkdir()

    proc = _run_artifact(repo_root, tmp_path, "doctor", "--auth-matrix", "--json")

    combined = proc.stdout + proc.stderr
    assert proc.returncode == 0, combined
    payload = json.loads(proc.stdout)
    assert payload["target"] == "notebooklm-py==0.7.2"
    assert payload["summary"]["interactive_login_rows"] == 45
    assert payload["matrices"]["browser_cookie_import"]["rows"] == 101
    assert not (tmp_path / "home").exists(), (
        "matrix diagnostic should not touch real/default home"
    )
    assert "Traceback" not in combined


def test_singlefile_builder_is_deterministic(repo_root, tmp_path):
    out_a = tmp_path / "a.py"
    out_b = tmp_path / "b.py"
    builder = repo_root / "scripts" / "build_singlefile.py"

    first = subprocess.run(
        [sys.executable, str(builder), "--out", str(out_a)],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=30,
    )
    second = subprocess.run(
        [sys.executable, str(builder), "--out", str(out_b)],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert first.returncode == 0, first.stdout + first.stderr
    assert second.returncode == 0, second.stdout + second.stderr
    assert out_a.read_bytes() == out_b.read_bytes()
    assert out_a.read_bytes() == (
        repo_root / "singlefile" / "zero_notebooklm.py"
    ).read_bytes()
    assert out_a.read_text(encoding="utf-8").startswith("#!/usr/bin/env python3\n")
