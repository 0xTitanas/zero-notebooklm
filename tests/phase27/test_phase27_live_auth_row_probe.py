"""Phase 27 live auth-row probe script tests."""

from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path

import pytest


def _load_probe(repo_root: Path):
    path = repo_root / "scripts" / "live_auth_row_probe.py"
    spec = importlib.util.spec_from_file_location("_phase27_live_auth_row_probe", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_runner(mod, calls: list[list[str]]):
    def run(args, env, cwd, timeout):
        args = list(args)
        calls.append(args)
        if "login" in args:
            storage = Path(args[args.index("--storage") + 1])
            if storage.suffix == ".json":
                target = storage
            else:
                target = storage / "profiles" / "default" / "storage_state.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('{"cookies":[]}', encoding="utf-8")
            return mod.CommandResult(0, '{"has_required_cookies": true}', "")
        if "inspect" in args:
            browser = args[args.index("--browser") + 1]
            return mod.CommandResult(
                0,
                json.dumps(
                    {
                        "browser": browser,
                        "accounts": [
                            {
                                "email": "person@example.com",
                                "is_default": True,
                                "browser_profile": None,
                            }
                        ],
                    }
                ),
                "raw-cookie-output-should-not-persist",
            )
        if "check" in args:
            return mod.CommandResult(0, '{"ok": true}', "")
        if "refresh" in args:
            return mod.CommandResult(0, '{"refreshed": true}', "")
        if "doctor" in args:
            return mod.CommandResult(0, '{"ok": true}', "")
        if "logout" in args:
            storage = Path(args[args.index("--storage") + 1])
            storage.unlink(missing_ok=True)
            return mod.CommandResult(0, "", "")
        raise AssertionError(args)

    return run


def test_inspect_account_envelope_is_strict_and_nonempty(repo_root: Path):
    mod = _load_probe(repo_root)
    account = {
        "email": "person@example.com",
        "is_default": True,
        "browser_profile": None,
    }

    assert mod._has_accounts({"browser": "chrome", "accounts": [account]})
    assert not mod._has_accounts({"browser": "chrome", "accounts": []})
    assert not mod._has_accounts(
        {"browser": "chrome", "accounts": [{**account, "extra": True}]}
    )
    assert not mod._has_accounts(
        {"browser": "chrome", "accounts": [{**account, "email": ""}]}
    )


def test_windows_cookie_probe_generates_redacted_row_proofs(repo_root: Path, tmp_path: Path):
    mod = _load_probe(repo_root)
    calls: list[list[str]] = []
    out_dir = tmp_path / "probe"
    stale_evidence = out_dir / "auth_row_evidence_report.redacted.json"
    stale_evidence.parent.mkdir(parents=True)
    stale_evidence.write_text('{"stale":true}', encoding="utf-8")

    summary = mod.build_report(
        [
            "--target-os",
            "windows11",
            "--output-dir",
            str(out_dir),
            "--cookie-browsers",
            "chromium",
            "--cookie-ops",
            "import,account_select,inspect,refresh",
            "--account",
            "person@example.com",
        ],
        runner=_fake_runner(mod, calls),
        repo_root=repo_root,
        now=datetime.now(timezone.utc),
    )

    assert summary["passed_rows"] == 4
    assert summary["blocked_rows"] == 0
    row_ids = {row["row_id"] for row in summary["rows"]}
    assert {
        "auth.cookie_import.chromium.windows11.import",
        "auth.cookie_import.chromium.windows11.account_select",
        "auth.cookie_import.chromium.windows11.inspect",
        "auth.cookie_import.chromium.windows11.refresh",
    } <= row_ids
    assert any("--os" in call and "Windows-11" in call for call in calls)

    proof_text = (out_dir / "auth_row_proofs.redacted.json").read_text(encoding="utf-8")
    summary_text = (out_dir / "summary.redacted.json").read_text(encoding="utf-8")
    assert "person@example.com" not in proof_text + summary_text
    assert "raw-cookie-output-should-not-persist" not in proof_text + summary_text
    assert summary["evidence_report_builder"].startswith("failed:")
    assert summary["evidence_report_path"] == ""
    assert not stale_evidence.exists()
    proof_tokens = {
        proof["token"]
        for row in json.loads(proof_text)["rows"]
        for proof in row["proofs"]
    }
    assert proof_tokens == {"session_credential_evidence"}
    assert "live_differential_result" not in proof_tokens


def test_probe_storage_uses_private_temp_directory_and_is_removed(
    repo_root: Path, tmp_path: Path
):
    mod = _load_probe(repo_root)
    calls: list[list[str]] = []
    storage_roots: list[Path] = []
    modes: list[int] = []
    runner = _fake_runner(mod, calls)

    def recording_runner(args, env, cwd, timeout):
        args = list(args)
        if "login" in args:
            storage = Path(args[args.index("--storage") + 1])
            scratch_dir = storage.parent.parent
            storage_roots.append(scratch_dir)
            if os.name != "nt":
                modes.append(scratch_dir.stat().st_mode & 0o777)
        return runner(args, env, cwd, timeout)

    out_dir = tmp_path / "probe"
    mod.build_report(
        [
            "--target-os",
            "windows11",
            "--output-dir",
            str(out_dir),
            "--cookie-browsers",
            "chromium",
            "--cookie-ops",
            "import",
        ],
        runner=recording_runner,
        repo_root=repo_root,
        now=datetime.now(timezone.utc),
    )

    assert storage_roots and all(not path.exists() for path in storage_roots)
    if os.name != "nt":
        assert modes == [0o700]
    assert not (out_dir / "stores").exists()
    assert not list(out_dir.rglob("*storage_state*.json"))


def test_probe_storage_is_removed_when_runner_raises(repo_root: Path, tmp_path: Path):
    mod = _load_probe(repo_root)
    storage_roots: list[Path] = []
    out_dir = tmp_path / "probe"
    stale_evidence = out_dir / "auth_row_evidence_report.redacted.json"
    stale_evidence.parent.mkdir(parents=True)
    stale_evidence.write_text('{"stale":true}', encoding="utf-8")

    def failing_runner(args, env, cwd, timeout):
        storage = Path(args[args.index("--storage") + 1])
        scratch_dir = storage.parent.parent
        storage_roots.append(scratch_dir)
        storage.parent.mkdir(parents=True, exist_ok=True)
        storage.write_text('{"cookies":[]}', encoding="utf-8")
        raise RuntimeError("runner failed")

    with pytest.raises(RuntimeError, match="runner failed"):
        mod.build_report(
            [
                "--target-os",
                "windows11",
                "--output-dir",
                str(out_dir),
                "--cookie-browsers",
                "chromium",
                "--cookie-ops",
                "import",
            ],
            runner=failing_runner,
            repo_root=repo_root,
        )

    assert storage_roots and all(not path.exists() for path in storage_roots)
    assert not stale_evidence.exists()


def test_strict_mode_blocks_failed_or_missing_evidence_report(repo_root: Path, monkeypatch):
    mod = _load_probe(repo_root)
    for status, expected_exit in (
        ("written", 0),
        ("failed:RuntimeError", mod.STRICT_BLOCKED_EXIT),
        ("missing", mod.STRICT_BLOCKED_EXIT),
    ):
        monkeypatch.setattr(
            mod,
            "build_report",
            lambda argv, status=status: {
                "selected_rows": 1,
                "blocked_rows": 0,
                "evidence_report_builder": status,
            },
        )
        assert mod.main(["--strict"]) == expected_exit


def test_ubuntu_interactive_probe_generates_login_doctor_logout_rows(
    repo_root: Path, tmp_path: Path
):
    mod = _load_probe(repo_root)
    calls: list[list[str]] = []
    out_dir = tmp_path / "probe"

    summary = mod.build_report(
        [
            "--target-os",
            "ubuntu",
            "--output-dir",
            str(out_dir),
            "--cookie-browsers",
            "",
            "--include-interactive",
            "--interactive-browsers",
            "chrome",
            "--interactive-ops",
            "login,doctor,logout",
        ],
        runner=_fake_runner(mod, calls),
        repo_root=repo_root,
        now=datetime.now(timezone.utc),
    )

    assert summary["passed_rows"] == 3
    assert summary["blocked_rows"] == 0
    assert {row["row_id"] for row in summary["rows"]} == {
        "auth.interactive.chrome.ubuntu.login",
        "auth.interactive.chrome.ubuntu.doctor",
        "auth.interactive.chrome.ubuntu.logout",
    }
    assert any("login" in call and "--browser" in call and "chrome" in call for call in calls)
    assert all(
        "--storage" in call and Path(call[call.index("--storage") + 1]).suffix != ".json"
        for call in calls
        if "login" in call
    )
    assert any(call == [mod.sys.executable, "-m", "notebooklm.cli", "doctor", "--json"] for call in calls)
    assert all("Ubuntu-LTS-Linux" not in call for call in calls if "login" in call)
    proofs = json.loads((out_dir / "auth_row_proofs.redacted.json").read_text(encoding="utf-8"))
    assert len(proofs["rows"]) == 3
    assert summary["evidence_report_builder"].startswith("failed:")
