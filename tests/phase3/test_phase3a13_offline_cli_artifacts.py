"""Phase 3A13 fixture-backed CLI artifact list/get promotion.

This slice promotes only read-only ``notebooklm artifact list`` and
``notebooklm artifact get`` over the committed synthetic artifact fixtures. It
reuses the reviewed offline fake RPC seam and keeps artifact generation,
download/export, rename/delete/retry, polling/wait flows, live RPC,
auth, browser, home, or credential access, real NotebookLM mutation, public sharing,
and parity-row promotion out of scope.
"""

from __future__ import annotations

import importlib
import json
import types
from pathlib import Path

import import_origin_audit  # noqa: E402  (placed on sys.path by tests/conftest.py)

SYNTHETIC_NOTEBOOK_ID = "fake-notebook-0001"
EXPECTED_ARTIFACTS = [
    {
        "id": "fake-artifact-audio-0001",
        "title": "Synthetic Audio Overview",
        "type_code": 1,
        "artifact_type": "AUDIO",
        "status": "COMPLETED",
        "created_at": "2025-06-15T15:15:00+00:00",
        "url": "https://example.test/notebooklm-bare/audio.mp3",
        "variant": None,
    },
    {
        "id": "fake-artifact-report-0001",
        "title": "Synthetic Briefing Doc",
        "type_code": 2,
        "artifact_type": "REPORT",
        "status": "COMPLETED",
        "created_at": "2025-06-15T15:16:40+00:00",
        "url": None,
        "variant": None,
    },
    {
        "id": "fake-artifact-quiz-0001",
        "title": "Synthetic Quiz",
        "type_code": 4,
        "artifact_type": "QUIZ",
        "status": "PENDING",
        "created_at": None,
        "url": None,
        "variant": 2,
    },
]


def _poison_home(monkeypatch):
    monkeypatch.setattr(
        Path,
        "home",
        staticmethod(lambda: (_ for _ in ()).throw(AssertionError("real home access"))),
    )


def _run(mods, capsys, argv):
    code = mods.cli.console(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_cli_artifact_list_json_uses_committed_fixture_without_profile_home(
    repo_root, monkeypatch, capsys
):
    monkeypatch.syspath_prepend(str(repo_root))
    mods = types.SimpleNamespace(cli=importlib.import_module("notebooklm.cli"))
    _poison_home(monkeypatch)

    code, out, err = _run(mods, capsys, ["artifact", "list", "--json"])

    assert code == 0
    assert err == ""
    assert json.loads(out) == EXPECTED_ARTIFACTS


def test_cli_artifact_list_accepts_notebook_type_limit_and_no_truncate(
    repo_root, monkeypatch, capsys
):
    monkeypatch.syspath_prepend(str(repo_root))
    mods = types.SimpleNamespace(cli=importlib.import_module("notebooklm.cli"))

    code, out, err = _run(
        mods,
        capsys,
        [
            "artifact",
            "list",
            "--notebook",
            SYNTHETIC_NOTEBOOK_ID,
            "--type",
            "audio",
            "--limit",
            "1",
            "--no-truncate",
            "--json",
        ],
    )

    assert code == 0
    assert err == ""
    assert json.loads(out) == EXPECTED_ARTIFACTS[:1]


def test_cli_artifact_list_unknown_type_choice_is_rejected(
    repo_root, monkeypatch, capsys
):
    monkeypatch.syspath_prepend(str(repo_root))
    mods = types.SimpleNamespace(cli=importlib.import_module("notebooklm.cli"))

    code, out, err = _run(
        mods, capsys, ["artifact", "list", "--type", "made-up", "--json"]
    )

    assert code == 64
    assert out == ""
    assert "invalid choice" in err
    assert "made-up" in err


def test_cli_artifact_get_json_uses_committed_fixture_without_profile_home(
    repo_root, monkeypatch, capsys
):
    monkeypatch.syspath_prepend(str(repo_root))
    mods = types.SimpleNamespace(cli=importlib.import_module("notebooklm.cli"))
    _poison_home(monkeypatch)

    code, out, err = _run(
        mods, capsys, ["artifact", "get", "fake-artifact-report-0001", "--json"]
    )

    assert code == 0
    assert err == ""
    assert json.loads(out) == EXPECTED_ARTIFACTS[1]


def test_cli_artifact_get_missing_selector_is_redacted(repo_root, monkeypatch, capsys):
    monkeypatch.syspath_prepend(str(repo_root))
    mods = types.SimpleNamespace(cli=importlib.import_module("notebooklm.cli"))
    synthetic_home = "/".join(("", "Users", "example"))
    private_artifact = "private ya" + "29." + "S" * 40 + f" {synthetic_home}/artifact"

    code, out, err = _run(mods, capsys, ["artifact", "get", private_artifact, "--json"])

    assert code == 64
    assert out == ""
    assert "artifact not found" in err
    assert "ya29." not in err
    assert synthetic_home not in err


def test_cli_artifact_download_group_is_promoted_but_artifact_status_stays_fixture_backed(
    repo_root, monkeypatch, tmp_path, capsys
):
    monkeypatch.syspath_prepend(str(repo_root))
    mods = types.SimpleNamespace(cli=importlib.import_module("notebooklm.cli"))

    output_path = tmp_path / "audio.mp3"
    code, out, err = _run(
        mods, capsys, ["download", "audio", str(output_path), "--dry-run", "--json"]
    )
    assert code == 0
    assert err == ""
    assert "download_single" in out
    assert not output_path.exists()

    # Batch 3B1 promotes only read/status artifact commands; Batch 3B4 promotes
    # top-level downloads separately. Both remain fixture-backed and offline.
    code, out, err = _run(
        mods, capsys, ["artifact", "poll", "fake-artifact-audio-0001", "--json"]
    )
    assert code == 0
    assert err == ""
    assert "completed" in out

    code, out, err = _run(mods, capsys, ["artifact", "suggestions", "--json"])
    assert code == 0
    assert err == ""
    assert "Synthetic Briefing" in out

    code, out, err = _run(
        mods, capsys, ["artifact", "wait", "fake-artifact-audio-0001", "--json"]
    )
    assert code == 0
    assert err == ""
    assert "completed" in out


def test_phase3a13_promotes_artifact_read_root_and_later_generate_download_batches(
    repo_root, monkeypatch
):
    monkeypatch.syspath_prepend(str(repo_root))
    cli = importlib.import_module("notebooklm.cli")

    assert {
        "list",
        "use",
        "note",
        "source",
        "artifact",
        "ask",
        "metadata",
        "summary",
    } <= set(cli.IMPLEMENTED_COMMANDS)
    assert "download" in cli.IMPLEMENTED_COMMANDS
    assert "generate" in cli.IMPLEMENTED_COMMANDS


def test_phase3a13_cli_artifact_wiring_is_stdlib_and_offline_only(repo_root):
    assert (
        import_origin_audit.audit(
            roots=(
                "notebooklm/cli.py",
                "notebooklm/fake_rpc.py",
                "notebooklm/artifacts.py",
            ),
        )
        == []
    )
    src = "\n".join(
        (repo_root / "notebooklm" / name).read_text(encoding="utf-8")
        for name in ("fake_rpc.py", "artifacts.py")
    )
    forbidden = {
        "socket",
        "http.client",
        "urllib.request",
        "urlopen",
        "subprocess",
        "Path.home",
        "expanduser",
        "os.environ",
        "browser_cookies",
        "interactive_login",
        "Network.",
        "DevTools",
        "keyring",
        "secretstorage",
        "win32crypt",
        "browser_cookie3",
        "browsercookie",
    }
    assert sorted(token for token in forbidden if token in src) == []
