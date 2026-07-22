"""Phase 3A10 fixture-backed CLI note list/get promotion.

This slice promotes only read-only ``notebooklm note list`` and
``notebooklm note get`` over the committed synthetic notes fixtures. It reuses
the reviewed offline fake RPC seam and keeps note create/update/delete,
mind-map-backed notes, live RPC, auth, browser, home, or credential access, real
NotebookLM mutation, and parity-row promotion out of scope.
"""

from __future__ import annotations

import importlib
import json
import types
from pathlib import Path

import import_origin_audit  # noqa: E402  (placed on sys.path by tests/conftest.py)

SYNTHETIC_NOTEBOOK_ID = "fake-notebook-0001"
EXPECTED_NOTES = [
    {
        "id": "fake-note-0001",
        "notebook_id": SYNTHETIC_NOTEBOOK_ID,
        "title": "Synthetic Study Note",
        "content": "A fixture-backed note for NotebookLM Bare parity tests.",
        "created_at": "2025-06-15T15:11:40+00:00",
    },
    {
        "id": "fake-note-0002",
        "notebook_id": SYNTHETIC_NOTEBOOK_ID,
        "title": "Synthetic Follow-up Note",
        "content": "Second synthetic note; contains no real NotebookLM data.",
        "created_at": "2025-06-15T15:13:20+00:00",
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


def test_cli_note_list_json_uses_committed_fixture_without_profile_home(
    repo_root, monkeypatch, capsys
):
    monkeypatch.syspath_prepend(str(repo_root))
    mods = types.SimpleNamespace(cli=importlib.import_module("notebooklm.cli"))
    _poison_home(monkeypatch)

    code, out, err = _run(mods, capsys, ["note", "list", "--json"])

    assert code == 0
    assert err == ""
    assert json.loads(out) == EXPECTED_NOTES


def test_cli_note_list_accepts_notebook_limit_and_no_truncate(
    repo_root, monkeypatch, capsys
):
    monkeypatch.syspath_prepend(str(repo_root))
    mods = types.SimpleNamespace(cli=importlib.import_module("notebooklm.cli"))

    code, out, err = _run(
        mods,
        capsys,
        [
            "note",
            "list",
            "--notebook",
            SYNTHETIC_NOTEBOOK_ID,
            "--limit",
            "1",
            "--no-truncate",
            "--json",
        ],
    )

    assert code == 0
    assert err == ""
    assert json.loads(out) == EXPECTED_NOTES[:1]


def test_cli_note_get_json_uses_committed_fixture_without_profile_home(
    repo_root, monkeypatch, capsys
):
    monkeypatch.syspath_prepend(str(repo_root))
    mods = types.SimpleNamespace(cli=importlib.import_module("notebooklm.cli"))
    _poison_home(monkeypatch)

    code, out, err = _run(mods, capsys, ["note", "get", "fake-note-0002", "--json"])

    assert code == 0
    assert err == ""
    assert json.loads(out) == EXPECTED_NOTES[1]


def test_cli_note_get_missing_selector_is_redacted(repo_root, monkeypatch, capsys):
    monkeypatch.syspath_prepend(str(repo_root))
    mods = types.SimpleNamespace(cli=importlib.import_module("notebooklm.cli"))
    synthetic_home = "/".join(("", "Users", "example"))
    private_note = "private ya" + "29." + "S" * 40 + f" {synthetic_home}/note"

    code, out, err = _run(mods, capsys, ["note", "get", private_note, "--json"])

    assert code == 64
    assert out == ""
    assert "note not found" in err
    assert "ya29." not in err
    assert synthetic_home not in err


def test_cli_note_unpromoted_mind_map_and_update_surfaces_remain_closed(
    repo_root, monkeypatch, capsys
):
    monkeypatch.syspath_prepend(str(repo_root))
    mods = types.SimpleNamespace(cli=importlib.import_module("notebooklm.cli"))

    for argv in (
        ["note", "update", "fake-note-0001", "Title"],
        ["note", "list-mind-maps"],
    ):
        code, out, err = _run(mods, capsys, argv)
        assert code == 78
        assert out == ""
        assert "notebooklm note" in err
        assert "later parity phase" in err


def test_phase3a10_promotes_note_and_artifact_roots_without_chat(
    repo_root, monkeypatch
):
    monkeypatch.syspath_prepend(str(repo_root))
    cli = importlib.import_module("notebooklm.cli")

    assert {"list", "use", "note", "artifact", "ask", "metadata", "summary"} <= set(
        cli.IMPLEMENTED_COMMANDS
    )
    assert "download" in cli.IMPLEMENTED_COMMANDS
    assert "generate" in cli.IMPLEMENTED_COMMANDS


def test_phase3a10_cli_note_wiring_is_stdlib_and_offline_only(repo_root):
    assert (
        import_origin_audit.audit(
            roots=(
                "notebooklm/cli.py",
                "notebooklm/fake_rpc.py",
                "notebooklm/notes.py",
            ),
        )
        == []
    )
    src = "\n".join(
        (repo_root / "notebooklm" / name).read_text(encoding="utf-8")
        for name in ("fake_rpc.py", "notes.py")
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
