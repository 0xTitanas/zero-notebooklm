"""Phase 5F isolated offline runtime/import-origin proof.

This phase closes only the parity-matrix ``offline`` category as the already scoped
``python -I -S`` import-origin/denylist proof. It does not claim live NotebookLM
works offline, and it leaves CLI/API/auth success-path parity rows open while
RPC is now pass-promoted from fake-server fixture evidence.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


DENYLISTED_IMPORTS = (
    "httpx",
    "httpcore",
    "h11",
    "requests",
    "click",
    "rich",
    "filelock",
    "rookiepy",
    "playwright",
    "selenium",
    "websockets",
    "fastmcp",
    "mcp",
    "pydantic",
    "anyio",
    "sniffio",
    "starlette",
    "uvicorn",
    "bs4",
    "lxml",
    "markdownify",
    "dotenv",
    "certifi",
    "markdown_it",
)
SYNTHETIC_NOTEBOOK_ID = "fake-notebook-0001"


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
    clean_home.mkdir(parents=True)
    tmp_dir = tmp_path / "tmp"
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


def _category_rows() -> dict[str, str]:
    matrix = (_repo_root() / "compat" / "parity_matrix.md").read_text(encoding="utf-8")
    rows: dict[str, str] = {}
    for line in matrix.splitlines():
        if not line.startswith("|") or "| ---" in line:
            continue
        cells = [cell.strip(" `") for cell in line.strip().strip("|").split("|")]
        if len(cells) == 4 and cells[0] in {
            "cli",
            "api",
            "auth",
            "rpc",
            "offline",
            "self-test",
        }:
            rows[cells[0]] = cells[3]
    return rows


def test_phase5f_promoted_categories_match_current_evidence() -> None:
    rows = _category_rows()
    assert rows["offline"] == "pass"
    assert rows["self-test"] == "pass"
    assert rows["cli"] == "pass"
    assert rows["api"] == "pass"
    for category in ("auth",):
        assert rows[category] == "open"
    assert rows["rpc"] == "pass"


def test_import_origin_audit_runs_under_isolated_python_without_violations(
    tmp_path: Path,
) -> None:
    repo = _repo_root()
    proc = _run(
        [
            sys.executable,
            "-B",
            "-I",
            "-S",
            str(repo / "scripts" / "import_origin_audit.py"),
            "--json",
        ],
        cwd=tmp_path,
        env=_clean_env(tmp_path),
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    payload = json.loads(proc.stdout)
    assert payload == {"violations": []}
    assert "site-packages" not in proc.stdout + proc.stderr
    assert "dist-packages" not in proc.stdout + proc.stderr


def test_source_self_test_runs_under_isolated_python_with_denylist_blocked(
    tmp_path: Path,
) -> None:
    repo = _repo_root()
    code = r"""
from __future__ import annotations
import builtins
import json
import sys
from pathlib import Path

repo = Path(sys.argv[1]).resolve()
deny = set(sys.argv[2].split(','))
sys.path.insert(0, str(repo))
real_import = builtins.__import__

def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    top = (name or '').split('.', 1)[0]
    if level == 0 and top in deny:
        raise AssertionError(f'denylisted import attempted: {name}')
    return real_import(name, globals, locals, fromlist, level)

builtins.__import__ = guarded_import
from notebooklm.self_test import run_offline_self_test

payload = run_offline_self_test()
loaded_denylisted = sorted(name for name in sys.modules if name.split('.', 1)[0] in deny)
site_paths = [p for p in sys.path if 'site-packages' in p or 'dist-packages' in p]
foreign_project_modules = []
for name, module in sorted(sys.modules.items()):
    if name == 'notebooklm' or name.startswith('notebooklm.'):
        raw_path = getattr(module, '__file__', None)
        if raw_path and not str(Path(raw_path).resolve()).startswith(str(repo)):
            foreign_project_modules.append([name, raw_path])
assert payload['status'] == 'passed', payload
assert payload['live_enabled'] is False, payload
assert payload['home_touched'] is False, payload
assert loaded_denylisted == [], loaded_denylisted
assert site_paths == [], site_paths
assert foreign_project_modules == [], foreign_project_modules
print(json.dumps({
    'status': payload['status'],
    'notebook_id': payload['notebook_id'],
    'site_paths': site_paths,
    'loaded_denylisted': loaded_denylisted,
}, sort_keys=True))
"""
    proc = _run(
        [
            sys.executable,
            "-B",
            "-I",
            "-S",
            "-c",
            code,
            str(repo),
            ",".join(DENYLISTED_IMPORTS),
        ],
        cwd=tmp_path,
        env=_clean_env(tmp_path),
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    payload = json.loads(proc.stdout)
    assert payload["status"] == "passed"
    assert payload["notebook_id"] == SYNTHETIC_NOTEBOOK_ID
    assert payload["site_paths"] == []
    assert payload["loaded_denylisted"] == []


def test_singlefile_artifact_runs_under_isolated_python_without_site_packages(
    tmp_path: Path,
) -> None:
    repo = _repo_root()
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    proc = _run(
        [
            sys.executable,
            "-B",
            "-I",
            "-S",
            str(repo / "singlefile" / "notebooklm_bare.py"),
            "list",
            "--json",
        ],
        cwd=cwd,
        env=_clean_env(tmp_path),
    )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    payload = json.loads(proc.stdout)
    assert payload["notebooks"][0]["id"] == SYNTHETIC_NOTEBOOK_ID
    assert "site-packages" not in proc.stdout + proc.stderr
    assert "dist-packages" not in proc.stdout + proc.stderr
    assert str(tmp_path / "home") not in proc.stdout + proc.stderr
