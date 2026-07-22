"""Phase 12 live-readonly differential gate tests.

Tests the scripts/live_readonly_differential.py gate without touching the
script itself, docs, or any committed artifacts beyond reading them.
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
    script = SCRIPTS_DIR / "live_readonly_differential.py"
    spec = importlib.util.spec_from_file_location("_live_diff_gate", script)
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


def _fake_smoke_module():
    """Return a fake smoke module whose run() always passes cleanly."""

    class _FakeSmoke:
        @staticmethod
        def run(argv=None, env=None):
            return 0, {
                "status": "pass",
                "strict_exit_code": 0,
                "checks": {
                    "network_auth": {
                        "ok": True,
                        "token_fetch_ok": "--network-auth" in list(argv or []),
                    }
                },
            }

    return _FakeSmoke()


def _write_probe(tmp_path: Path, name: str, observations: Any) -> str:
    """Write a fake probe script that ignores stdin and echoes fixed observations."""
    obs_json_str = json.dumps(
        json.dumps(observations)
    )  # double-encode → safe string literal
    script = tmp_path / name
    body = textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import json, sys
        _request = json.loads(sys.stdin.read())  # consumed but ignored
        _obs = json.loads({obs_json_str})
        print(json.dumps({{"observations": _obs}}))
    """)
    script.write_text(body, encoding="utf-8")
    script.chmod(0o755)
    return f"{sys.executable} {shlex.quote(str(script))}"


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


def test_default_no_args_skips_with_exit_77(monkeypatch) -> None:
    """1. No args/env + poisoned Path.home → skipped, strict_exit_code=77."""

    def _forbidden_home():
        raise AssertionError("build_report skip path must not call Path.home()")

    monkeypatch.setattr(Path, "home", staticmethod(_forbidden_home))

    mod = _load_module()
    report = mod.build_report(argv=[], env={})

    assert report["status"] == "skipped"
    assert report["strict_exit_code"] == 77
    assert report["live_enabled"] is False
    assert report["mutation_allowed"] is False


def test_cli_json_strict_default_exits_77(tmp_path: Path) -> None:
    """2. CLI --json --strict with no live env/args exits 77."""
    env = _clean_env(tmp_path)
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "live_readonly_differential.py"),
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


def test_live_intent_missing_args_returns_64_with_option_name_blockers() -> None:
    """3. Live intent with missing explicit args → error, code 64, blockers are option names only."""
    mod = _load_module()

    # Via --allow-live flag only: env gate and all explicit args are still required.
    report = mod.build_report(argv=["--allow-live"], env={})
    assert report["status"] == "error"
    assert report["strict_exit_code"] == 64
    blockers = report["blockers"]
    assert set(blockers) == {
        "NOTEBOOKLM_BARE_LIVE_DIFFERENTIAL=1",
        "--storage-state",
        "--notebook-id",
        "--upstream-command",
        "--bare-command",
    }
    assert all(
        b.startswith("--") or b == "NOTEBOOKLM_BARE_LIVE_DIFFERENTIAL=1"
        for b in blockers
    ), f"non-option-name blocker: {blockers}"

    # Via env only: --allow-live and all explicit args are still required.
    report2 = mod.build_report(argv=[], env={"NOTEBOOKLM_BARE_LIVE_DIFFERENTIAL": "1"})
    assert report2["status"] == "error"
    assert report2["strict_exit_code"] == 64
    assert "--allow-live" in report2["blockers"]
    assert "--storage-state" in report2["blockers"]
    assert "--notebook-id" in report2["blockers"]


def test_matching_probes_pass_shape_match_and_storage_preserved(
    tmp_path: Path, monkeypatch
) -> None:
    """4. Matching fake probes → pass, strict_exit_code 0, shape_match true, storage preserved."""
    storage = tmp_path / "storage_state.json"
    storage.write_text(
        json.dumps({"cookies": [{"name": "SID", "value": "tok-SYNTH"}]}),
        encoding="utf-8",
    )
    bytes_before = storage.read_bytes()

    observations = {"sources": [{"title": "Doc", "id": "nb-SYNTH"}], "count": 1}
    upstream_cmd = _write_probe(tmp_path, "upstream_probe.py", observations)
    bare_cmd = _write_probe(tmp_path, "bare_probe.py", observations)

    mod = _load_module()
    monkeypatch.setattr(mod, "_load_smoke_module", _fake_smoke_module)

    report = mod.build_report(
        argv=[
            "--allow-live",
            "--storage-state",
            str(storage),
            "--notebook-id",
            "nb-SYNTH-ID",
            "--upstream-command",
            upstream_cmd,
            "--bare-command",
            bare_cmd,
        ],
        env={"NOTEBOOKLM_BARE_LIVE_DIFFERENTIAL": "1"},
    )

    assert report["status"] == "pass"
    assert report["strict_exit_code"] == 0
    assert report["shape_match"] is True
    assert report["storage_preserved"] is True
    assert storage.read_bytes() == bytes_before


def test_network_auth_proof_is_reused_without_check_auth_probe(
    tmp_path: Path, monkeypatch
) -> None:
    """With --network-auth, smoke supplies the single proof and probes skip check_auth."""
    storage = tmp_path / "storage_state.json"
    storage.write_text(json.dumps({"cookies": []}), encoding="utf-8")
    probe = tmp_path / "probe.py"
    proof_markers = tmp_path / "proof-markers.txt"
    probe.write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        "request = json.loads(sys.stdin.read())\n"
        "assert 'check_auth' not in request['readonly_operations']\n"
        "assert request['network_auth_proof']['source'] == 'live_readonly_smoke'\n"
        f"Path({str(proof_markers)!r}).write_text('saw-proof')\n"
        "print(json.dumps({'observations': {'ok': True}}))\n",
        encoding="utf-8",
    )
    probe.chmod(0o755)

    mod = _load_module()
    monkeypatch.setattr(mod, "_load_smoke_module", _fake_smoke_module)

    report = mod.build_report(
        argv=[
            "--allow-live",
            "--storage-state",
            str(storage),
            "--notebook-id",
            "nb-SYNTH-ID",
            "--upstream-command",
            f"{sys.executable} {shlex.quote(str(probe))}",
            "--bare-command",
            f"{sys.executable} {shlex.quote(str(probe))}",
            "--network-auth",
        ],
        env={"NOTEBOOKLM_BARE_LIVE_DIFFERENTIAL": "1"},
    )

    assert report["status"] == "pass"
    assert proof_markers.read_text(encoding="utf-8") == "saw-proof"


def test_network_auth_rejects_back_to_back_auth_check_test(tmp_path: Path) -> None:
    """Do not run smoke --network-auth plus an explicit auth check --test command."""
    storage = tmp_path / "storage_state.json"
    storage.write_text(json.dumps({"cookies": []}), encoding="utf-8")
    mod = _load_module()

    report = mod.build_report(
        argv=[
            "--allow-live",
            "--storage-state",
            str(storage),
            "--notebook-id",
            "nb-SYNTH-ID",
            "--upstream-command",
            "notebooklm auth check --test --json",
            "--bare-command",
            "notebooklm-bare list --json",
            "--network-auth",
        ],
        env={"NOTEBOOKLM_BARE_LIVE_DIFFERENTIAL": "1"},
    )

    assert report["status"] == "error"
    assert report["strict_exit_code"] == 64
    assert report["blockers"] == ["duplicate_network_auth_probe"]


def test_matching_probe_report_no_raw_sentinels(tmp_path: Path, monkeypatch) -> None:
    """5. Report JSON must not include cookie values, email, paths, notebook id, or probe content."""
    cookie_value = "SYNTH-COOKIE-ABC123"
    storage = tmp_path / "storage_state.json"
    storage.write_text(
        json.dumps({"cookies": [{"name": "SID", "value": cookie_value}]}),
        encoding="utf-8",
    )

    email = "synth-user@example.com"
    source_title = "SYNTH-SOURCE-TITLE-DO-NOT-LEAK"
    note_body = "SYNTH-NOTE-BODY-DO-NOT-LEAK"
    artifact_content = "SYNTH-ARTIFACT-CONTENT-DO-NOT-LEAK"
    notebook_id = "SYNTH-NOTEBOOK-ID-XYZ"

    path_key = str(tmp_path / "SYNTH-PATH-KEY-DO-NOT-LEAK")
    email_key = "SYNTH-KEY@example.com"
    numeric_secret = 424242424242
    observations = {
        "email": email,
        email_key: "SYNTH-KEYED-VALUE-DO-NOT-LEAK",
        path_key: {"nested": "SYNTH-PATH-KEYED-VALUE-DO-NOT-LEAK"},
        "numeric_secret": numeric_secret,
        "boolean_secret": True,
        "sources": [{"title": source_title}],
        "notes": [{"body": note_body}],
        "artifacts": [{"content": artifact_content}],
    }
    upstream_cmd = _write_probe(tmp_path, "upstream_probe.py", observations)
    bare_cmd = _write_probe(tmp_path, "bare_probe.py", observations)

    mod = _load_module()
    monkeypatch.setattr(mod, "_load_smoke_module", _fake_smoke_module)

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
        env={"NOTEBOOKLM_BARE_LIVE_DIFFERENTIAL": "1"},
    )

    report_json = json.dumps(report)
    for sentinel in [
        cookie_value,
        email,
        source_title,
        note_body,
        artifact_content,
        notebook_id,
        str(storage),
        email_key,
        path_key,
        str(numeric_secret),
    ]:
        assert sentinel not in report_json, (
            f"raw sentinel leaked into report: {sentinel!r}"
        )


def test_mismatching_probes_fail_shape_mismatch(tmp_path: Path, monkeypatch) -> None:
    """6. Mismatching fake probes → fail, strict_exit_code 77, shape_match false."""
    storage = tmp_path / "storage_state.json"
    storage.write_text(json.dumps({"cookies": []}), encoding="utf-8")

    upstream_obs = {"sources": [{"title": "Doc A"}], "count": 1}
    bare_obs = {"sources": [{"title": "Doc A"}, {"title": "Doc B"}], "count": 2}

    upstream_cmd = _write_probe(tmp_path, "upstream_probe.py", upstream_obs)
    bare_cmd = _write_probe(tmp_path, "bare_probe.py", bare_obs)

    mod = _load_module()
    monkeypatch.setattr(mod, "_load_smoke_module", _fake_smoke_module)

    report = mod.build_report(
        argv=[
            "--allow-live",
            "--storage-state",
            str(storage),
            "--notebook-id",
            "nb-123",
            "--upstream-command",
            upstream_cmd,
            "--bare-command",
            bare_cmd,
        ],
        env={"NOTEBOOKLM_BARE_LIVE_DIFFERENTIAL": "1"},
    )

    assert report["status"] == "fail"
    assert report["strict_exit_code"] == 77
    assert report["shape_match"] is False


def test_live_smoke_failure_stops_before_probe_execution(
    tmp_path: Path, monkeypatch
) -> None:
    """Live smoke failure is fail-closed and probe commands are not executed."""
    storage = tmp_path / "storage_state.json"
    storage.write_text(json.dumps({"cookies": []}), encoding="utf-8")
    probe_marker = tmp_path / "probe-ran.txt"
    probe = tmp_path / "probe.py"
    probe.write_text(
        "from pathlib import Path\n"
        f"Path({str(probe_marker)!r}).write_text('ran')\n"
        'print(\'{"observations": {"ok": true}}\')\n',
        encoding="utf-8",
    )
    probe.chmod(0o755)

    class _FailingSmoke:
        @staticmethod
        def run(argv=None, env=None):
            return 77, {"status": "failed", "reason": "synthetic smoke failure"}

    mod = _load_module()
    monkeypatch.setattr(mod, "_load_smoke_module", lambda: _FailingSmoke())

    report = mod.build_report(
        argv=[
            "--allow-live",
            "--storage-state",
            str(storage),
            "--notebook-id",
            "nb-123",
            "--upstream-command",
            f"{sys.executable} {shlex.quote(str(probe))}",
            "--bare-command",
            f"{sys.executable} {shlex.quote(str(probe))}",
        ],
        env={"NOTEBOOKLM_BARE_LIVE_DIFFERENTIAL": "1"},
    )

    assert report["status"] == "fail"
    assert report["strict_exit_code"] == 77
    assert "live_smoke_failed" in report["blockers"]
    assert probe_marker.exists() is False


def test_probe_storage_deletion_fails_closed_without_path_leak(
    tmp_path: Path, monkeypatch
) -> None:
    """A probe that deletes storage state returns a redacted failure report."""
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
        "print(json.dumps({'observations': {'ok': 'yes'}}))\n",
        encoding="utf-8",
    )
    probe.chmod(0o755)

    mod = _load_module()
    monkeypatch.setattr(mod, "_load_smoke_module", _fake_smoke_module)

    report = mod.build_report(
        argv=[
            "--allow-live",
            "--storage-state",
            str(storage),
            "--notebook-id",
            "nb-123",
            "--upstream-command",
            f"{sys.executable} {shlex.quote(str(probe))}",
            "--bare-command",
            f"{sys.executable} {shlex.quote(str(probe))}",
        ],
        env={"NOTEBOOKLM_BARE_LIVE_DIFFERENTIAL": "1"},
    )

    report_json = json.dumps(report)
    assert report["status"] == "fail"
    assert report["strict_exit_code"] == 77
    assert report["storage_preserved"] is False
    assert "storage_state_modified" in report["blockers"]
    assert str(storage) not in report_json


def test_observation_scalar_values_are_shaped_by_type() -> None:
    """Observation scalar values are not copied into the report shape."""
    mod = _load_module()

    shaped = mod._shape({"number": 424242424242, "flag": True, "nothing": None})

    assert shaped == {
        "type": "dict",
        "size": 3,
        "entries": [
            {
                "key": {"type": "str", "length": 4, "empty": False},
                "value": {"type": "bool"},
            },
            {
                "key": {"type": "str", "length": 7, "empty": False},
                "value": {"type": "null"},
            },
            {
                "key": {"type": "str", "length": 6, "empty": False},
                "value": {"type": "int"},
            },
        ],
    }


def test_readonly_operations_allowlist_no_deny_words() -> None:
    """7. READONLY_OPERATIONS contains no deny words."""
    mod = _load_module()
    deny_words = frozenset(
        {
            "create",
            "delete",
            "update",
            "mutate",
            "mutation",
            "generate",
            "upload",
            "import",
            "refresh",
            "chat",
            "ask",
            "share_add",
            "share_remove",
            "share_update",
            "public",
        }
    )
    for op in mod.READONLY_OPERATIONS:
        op_lower = op.lower()
        for deny in deny_words:
            assert deny not in op_lower, (
                f"READONLY_OPERATIONS entry {op!r} contains deny word {deny!r}"
            )


def test_build_report_does_not_mutate_parity_matrix(repo_root: Path) -> None:
    """8. build_report() on the default skip path never mutates compat/parity_matrix.md."""
    parity_path = repo_root / "compat" / "parity_matrix.md"
    before = parity_path.read_bytes()

    mod = _load_module()
    mod.build_report(argv=[], env={})

    assert parity_path.read_bytes() == before, "build_report mutated parity_matrix.md"


def test_human_output_includes_promotion_no_and_current_ledger_note(
    tmp_path: Path,
) -> None:
    """9. Human text output says the default live gate itself promotes no category."""
    env = _clean_env(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "live_readonly_differential.py")],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    output = proc.stdout
    assert "category promotion: no" in output
    assert "consult parity_matrix.md for current ledger states" in output
