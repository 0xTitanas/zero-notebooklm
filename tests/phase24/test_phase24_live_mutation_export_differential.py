"""Phase 24 live mutation/export differential gate tests.

These tests are fully offline: they validate redaction, fail-closed behavior,
and probe contract handling without live NotebookLM access or real user data.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shlex
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _load_module():
    script = SCRIPTS_DIR / "live_mutation_export_differential.py"
    spec = importlib.util.spec_from_file_location("_phase24_live_mutation", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _clean_env(tmp_path: Path) -> dict[str, str]:
    clean_home = tmp_path / "home"
    clean_home.mkdir()
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    return {
        "HOME": str(clean_home),
        "USERPROFILE": str(clean_home),
        "TMPDIR": str(tmp_dir),
        "PYTHONPATH": "",
        "PATH": os.environ.get("PATH", ""),
    }


def _write_probe(
    tmp_path: Path,
    name: str,
    observations: Any,
    *,
    cleanup_confirmed: bool | None = True,
    public_sharing_touched: bool | None = False,
) -> str:
    """Write a fake probe script and return command line for execution."""
    payload = {
        "observations": observations,
    }
    if cleanup_confirmed is not None:
        payload["cleanup_confirmed"] = cleanup_confirmed
    if public_sharing_touched is not None:
        payload["public_sharing_touched"] = public_sharing_touched

    script = tmp_path / name
    body = textwrap.dedent(
        f"""\
        #!/usr/bin/env python3
        import json, sys

        _request = json.loads(sys.stdin.read())
        _payload = json.loads({json.dumps(json.dumps(payload))})
        print(json.dumps(_payload))
        """
    )
    script.write_text(body, encoding="utf-8")
    script.chmod(0o755)
    return f"{sys.executable} {shlex.quote(str(script))}"


def _base_observation_set() -> dict[str, Any]:
    return {
        "sources": [
            {
                "id": "SYNTH-ID",
                "title": "SYNTH Title",
            }
        ]
    }


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


def test_default_no_args_skips_with_exit_77(monkeypatch) -> None:
    """1. No args/env + no Path.home call → skipped, strict_exit_code=77."""

    def _forbidden_home():
        raise AssertionError("build_report skip path must not call Path.home()")

    monkeypatch.setattr(Path, "home", staticmethod(_forbidden_home))

    mod = _load_module()
    report = mod.build_report(argv=[], env={})

    assert report["status"] == "skipped"
    assert report["strict_exit_code"] == 77
    assert report["live_enabled"] is False
    assert report["read_only"] is False
    assert report["mutation_allowed"] is True


def test_cli_json_strict_default_exits_77(tmp_path: Path) -> None:
    """2. CLI --json --strict with no live env/args exits 77."""
    env = _clean_env(tmp_path)
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "live_mutation_export_differential.py"),
            "--json",
            "--strict",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 77
    report = json.loads(proc.stdout)
    assert report["status"] == "skipped"
    assert report["strict_exit_code"] == 77


def test_missing_args_require_both_live_gates(tmp_path: Path) -> None:
    """3. Live intent with incomplete args returns error, strict_exit_code 64."""
    mod = _load_module()

    report = mod.build_report(
        argv=["--allow-live", "--storage-state", str(tmp_path / "storage_state.json")],
        env={},
    )
    assert report["status"] == "error"
    assert report["strict_exit_code"] == 64
    blockers = set(report["blockers"])
    assert blockers == {
        "NOTEBOOKLM_BARE_LIVE_MUTATION_EXPORT=1",
        "--notebook-id",
        "--upstream-command",
        "--bare-command",
    }
    assert all(
        block == "--allow-live"
        or block.startswith("--")
        or block == "NOTEBOOKLM_BARE_LIVE_MUTATION_EXPORT=1"
        for block in blockers
    )

    report2 = mod.build_report(
        argv=[],
        env={"NOTEBOOKLM_BARE_LIVE_MUTATION_EXPORT": "1"},
    )
    assert report2["status"] == "error"
    assert report2["strict_exit_code"] == 64
    assert "--allow-live" in report2["blockers"]


def test_matching_probes_pass_storage_preserved_and_shape_match(tmp_path: Path) -> None:
    """4. Matching fake probes pass with storage preserved and shape match."""
    mod = _load_module()

    storage = tmp_path / "storage_state.json"
    storage.write_text(
        json.dumps({"cookies": [{"name": "SID", "value": "token"}]}), encoding="utf-8"
    )
    bytes_before = storage.read_bytes()

    observations = _base_observation_set()
    upstream_cmd = _write_probe(
        tmp_path,
        "upstream_probe.py",
        observations,
        cleanup_confirmed=True,
        public_sharing_touched=False,
    )
    bare_cmd = _write_probe(
        tmp_path,
        "bare_probe.py",
        observations,
        cleanup_confirmed=True,
        public_sharing_touched=False,
    )

    report = mod.build_report(
        argv=[
            "--allow-live",
            "--storage-state",
            str(storage),
            "--notebook-id",
            "nb-SYNTH-24",
            "--upstream-command",
            upstream_cmd,
            "--bare-command",
            bare_cmd,
        ],
        env={"NOTEBOOKLM_BARE_LIVE_MUTATION_EXPORT": "1"},
    )

    assert report["status"] == "pass"
    assert report["strict_exit_code"] == 0
    assert report["shape_match"] is True
    assert report["storage_preserved"] is True
    assert report["cleanup_confirmed"] is True
    assert report["public_sharing_touched"] is False
    assert storage.read_bytes() == bytes_before


def test_probe_missing_required_safety_signals_fails_closed(tmp_path: Path) -> None:
    """5. Probes must explicitly confirm cleanup and public-sharing status."""
    mod = _load_module()
    storage = tmp_path / "storage_state.json"
    storage.write_text(json.dumps({"cookies": []}), encoding="utf-8")

    observations = _base_observation_set()
    upstream_cmd = _write_probe(
        tmp_path,
        "upstream_probe.py",
        observations,
        cleanup_confirmed=None,
        public_sharing_touched=False,
    )
    bare_cmd = _write_probe(
        tmp_path,
        "bare_probe.py",
        observations,
        cleanup_confirmed=True,
        public_sharing_touched=None,
    )

    report = mod.build_report(
        argv=[
            "--allow-live",
            "--storage-state",
            str(storage),
            "--notebook-id",
            "nb-SYNTH-24",
            "--upstream-command",
            upstream_cmd,
            "--bare-command",
            bare_cmd,
        ],
        env={"NOTEBOOKLM_BARE_LIVE_MUTATION_EXPORT": "1"},
    )

    assert report["status"] == "fail"
    assert report["strict_exit_code"] == 77
    assert report["upstream_probe"]["error"] == "missing-cleanup-confirmed"
    assert report["bare_probe"]["error"] == "missing-public-sharing-flag"
    assert "upstream_probe_failed" in report["blockers"]
    assert "bare_probe_failed" in report["blockers"]


def test_report_no_raw_sentinels_in_output(tmp_path: Path) -> None:
    """6. Report JSON contains no raw secrets, paths, notebook IDs, or payload values."""
    mod = _load_module()
    storage = tmp_path / "storage_state.json"
    storage.write_text(
        json.dumps({"cookies": [{"name": "SID", "value": "SYNTH-COOKIE"}]}),
        encoding="utf-8",
    )

    storage_path = str(storage)
    notebook_id = "nb-SYNTH-NB-24"
    token_like = "ya" + "29.testtokenexample0123456789"
    observations = {
        "email": "note@example.com",
        "token": token_like,
        "path": f"{tmp_path}/secret-location.json",
        "secret_ref": "SYNTH-SECRET-ABC",
        "notes": [{"id": "SYNTH-ID", "text": "SYNTH-NOTE-DO-NOT-LEAK"}],
    }

    upstream_cmd = _write_probe(
        tmp_path,
        "upstream_probe.py",
        observations,
        cleanup_confirmed=True,
        public_sharing_touched=False,
    )
    bare_cmd = _write_probe(
        tmp_path,
        "bare_probe.py",
        observations,
        cleanup_confirmed=True,
        public_sharing_touched=False,
    )

    report = mod.build_report(
        argv=[
            "--allow-live",
            "--storage-state",
            str(storage),
            "--notebook-id",
            notebook_id,
            "--upstream-command",
            upstream_cmd,
            "--bare-command",
            bare_cmd,
        ],
        env={"NOTEBOOKLM_BARE_LIVE_MUTATION_EXPORT": "1"},
    )

    report_json = json.dumps(report)
    assert "SYNTH-COOKIE" not in report_json
    assert "note@example.com" not in report_json
    assert token_like not in report_json
    assert "SYNTH-NOTE-DO-NOT-LEAK" not in report_json
    assert notebook_id not in report_json
    assert storage_path not in report_json


def test_mismatching_probes_fail_shape_mismatch(tmp_path: Path) -> None:
    """6. Mismatching fake probes fail and set shape_match false."""
    mod = _load_module()
    storage = tmp_path / "storage_state.json"
    storage.write_text(json.dumps({"cookies": []}), encoding="utf-8")

    upstream_cmd = _write_probe(
        tmp_path,
        "upstream_probe.py",
        {"sources": [{"count": 1}]},
        cleanup_confirmed=True,
        public_sharing_touched=False,
    )
    bare_cmd = _write_probe(
        tmp_path,
        "bare_probe.py",
        {"sources": [{"count": 1}, {"count": 2}]},
        cleanup_confirmed=True,
        public_sharing_touched=False,
    )

    report = mod.build_report(
        argv=[
            "--allow-live",
            "--storage-state",
            str(storage),
            "--notebook-id",
            "nb-SYNTH-24",
            "--upstream-command",
            upstream_cmd,
            "--bare-command",
            bare_cmd,
        ],
        env={"NOTEBOOKLM_BARE_LIVE_MUTATION_EXPORT": "1"},
    )

    assert report["status"] == "fail"
    assert report["strict_exit_code"] == 77
    assert report["shape_match"] is False
    assert report["blockers"] == ["shape_mismatch"]


def test_probe_storage_deletion_fails_closed_without_storage_leak(
    tmp_path: Path,
) -> None:
    """7. Probe that deletes storage_state returns fail and blocks on storage modification."""
    storage = tmp_path / "storage_state.json"
    storage.write_text(json.dumps({"cookies": []}), encoding="utf-8")
    probe = tmp_path / "delete_storage_probe.py"
    probe.write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        "request = json.loads(sys.stdin.read())\n"
        "path = Path(request['storage_state'])\n"
        "if path.exists():\n"
        "    path.unlink()\n"
        "print(json.dumps({'observations': {'ok': True}, 'cleanup_confirmed': True}))\n",
        encoding="utf-8",
    )
    probe.chmod(0o755)

    mod = _load_module()
    cmd = f"{sys.executable} {shlex.quote(str(probe))}"

    report = mod.build_report(
        argv=[
            "--allow-live",
            "--storage-state",
            str(storage),
            "--notebook-id",
            "nb-SYNTH-24",
            "--upstream-command",
            cmd,
            "--bare-command",
            cmd,
        ],
        env={"NOTEBOOKLM_BARE_LIVE_MUTATION_EXPORT": "1"},
    )

    report_json = json.dumps(report)
    assert report["status"] == "fail"
    assert report["strict_exit_code"] == 77
    assert report["storage_preserved"] is False
    assert str(storage) not in report_json
    assert "storage_state_modified" in report["blockers"]


def test_public_sharing_touched_fails(tmp_path: Path) -> None:
    """8. Public sharing flag true in probes fails the mutation gate."""
    mod = _load_module()
    storage = tmp_path / "storage_state.json"
    storage.write_text(json.dumps({"cookies": []}), encoding="utf-8")

    observations = _base_observation_set()
    upstream_cmd = _write_probe(
        tmp_path,
        "upstream_probe.py",
        observations,
        cleanup_confirmed=True,
        public_sharing_touched=True,
    )
    bare_cmd = _write_probe(
        tmp_path,
        "bare_probe.py",
        observations,
        cleanup_confirmed=True,
        public_sharing_touched=True,
    )

    report = mod.build_report(
        argv=[
            "--allow-live",
            "--storage-state",
            str(storage),
            "--notebook-id",
            "nb-SYNTH-24",
            "--upstream-command",
            upstream_cmd,
            "--bare-command",
            bare_cmd,
        ],
        env={"NOTEBOOKLM_BARE_LIVE_MUTATION_EXPORT": "1"},
    )

    assert report["status"] == "fail"
    assert report["strict_exit_code"] == 77
    assert report["public_sharing_touched"] is True
    assert "public_sharing_touched" in report["blockers"]


def test_operation_allowlist_contains_no_public_share_terms() -> None:
    """9. MUTATION_EXPORT_OPERATIONS contains no public/share terms."""
    mod = _load_module()
    deny_words = frozenset(
        {
            "public",
            "share",
            "share_add",
            "share_remove",
            "share_update",
        }
    )
    for op in mod.MUTATION_EXPORT_OPERATIONS:
        op_lower = op.lower()
        for deny in deny_words:
            assert deny not in op_lower, (
                f"mutation operation {op!r} contains deny word {deny!r}"
            )
