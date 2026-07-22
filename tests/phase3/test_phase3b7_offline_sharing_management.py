"""Phase 3B7 fixture-backed sharing management parity batch.

This batch promotes upstream sharing CLI/API management leaves over in-memory
synthetic share status only. It does not enter live RPC, auth/browser/home reads,
credential stores, email delivery, or real NotebookLM sharing mutation.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SYNTHETIC_NOTEBOOK_ID = "fake-notebook-0001"


def _poison_home(monkeypatch):
    monkeypatch.setattr(
        Path,
        "home",
        staticmethod(lambda: (_ for _ in ()).throw(AssertionError("real home access"))),
    )


def _client():
    from notebooklm import AuthTokens, NotebookLMClient

    return NotebookLMClient(
        AuthTokens(cookies={}, csrf_token="synthetic", session_id="synthetic")
    )


def _load(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))
    return SimpleNamespace(cli=importlib.import_module("notebooklm.cli"))


def _run(mods, capsys, argv):
    code = mods.cli.console(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _users_by_email(status):
    return {user.email: user for user in status.shared_users}


def test_phase3b7_sharing_api_management_is_fixture_backed_without_home(monkeypatch):
    _poison_home(monkeypatch)

    async def scenario():
        from notebooklm.types import ShareAccess, SharePermission, ShareViewLevel

        client = _client()
        initial = await client.sharing.get_status(SYNTHETIC_NOTEBOOK_ID)
        assert initial.is_public is False
        assert "fixture.viewer@example.test" in _users_by_email(initial)

        public = await client.sharing.set_public(SYNTHETIC_NOTEBOOK_ID, True)
        assert public.is_public is True
        assert public.access is ShareAccess.ANYONE_WITH_LINK
        assert (
            public.share_url
            == f"https://notebooklm.google.com/notebook/{SYNTHETIC_NOTEBOOK_ID}"
        )

        chat_only = await client.sharing.set_view_level(
            SYNTHETIC_NOTEBOOK_ID, ShareViewLevel.CHAT_ONLY
        )
        assert chat_only.view_level is ShareViewLevel.CHAT_ONLY
        assert chat_only.is_public is True

        added = await client.sharing.add_user(
            SYNTHETIC_NOTEBOOK_ID,
            "reader@example.test",
            SharePermission.EDITOR,
            notify=False,
            welcome_message="synthetic only",
        )
        assert (
            _users_by_email(added)["reader@example.test"].permission
            is SharePermission.EDITOR
        )

        updated = await client.sharing.update_user(
            SYNTHETIC_NOTEBOOK_ID,
            "reader@example.test",
            SharePermission.VIEWER,
        )
        assert (
            _users_by_email(updated)["reader@example.test"].permission
            is SharePermission.VIEWER
        )

        removed = await client.sharing.remove_user(
            SYNTHETIC_NOTEBOOK_ID, "reader@example.test"
        )
        assert "reader@example.test" not in _users_by_email(removed)

        private = await client.sharing.set_public(SYNTHETIC_NOTEBOOK_ID, False)
        assert private.is_public is False
        assert private.access is ShareAccess.RESTRICTED
        assert private.share_url is None

    asyncio.run(scenario())


def test_phase3b7_sharing_api_signatures_match_golden_shapes():
    from notebooklm.client import SharingAPI

    expected = {
        "add_user": "(self, notebook_id: 'str', email: 'str', permission: 'SharePermission' = <SharePermission.VIEWER: 3>, notify: 'bool' = True, welcome_message: 'str' = '') -> 'ShareStatus'",
        "get_status": "(self, notebook_id: 'str') -> 'ShareStatus'",
        "remove_user": "(self, notebook_id: 'str', email: 'str') -> 'ShareStatus'",
        "set_public": "(self, notebook_id: 'str', public: 'bool') -> 'ShareStatus'",
        "set_view_level": "(self, notebook_id: 'str', level: 'ShareViewLevel') -> 'ShareStatus'",
        "update_user": "(self, notebook_id: 'str', email: 'str', permission: 'SharePermission') -> 'ShareStatus'",
    }
    actual = {
        name: str(inspect.signature(getattr(SharingAPI, name))) for name in expected
    }
    assert actual == expected


@pytest.mark.parametrize("bad_permission", ["owner", "remove"])
def test_phase3b7_sharing_api_rejects_unsafe_permission_values(bad_permission):
    from notebooklm.types import SharePermission

    permission = (
        SharePermission.OWNER if bad_permission == "owner" else SharePermission._REMOVE
    )

    async def scenario():
        with pytest.raises(ValueError):
            await _client().sharing.add_user(
                SYNTHETIC_NOTEBOOK_ID, "bad@example.test", permission
            )

    asyncio.run(scenario())


def test_phase3b7_share_cli_management_is_fixture_backed(
    repo_root, monkeypatch, capsys
):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)

    code, out, err = _run(
        mods,
        capsys,
        ["share", "public", "-n", SYNTHETIC_NOTEBOOK_ID, "--enable", "--json"],
    )
    assert code == 0
    assert err == ""
    assert json.loads(out) == {
        "notebook_id": SYNTHETIC_NOTEBOOK_ID,
        "is_public": True,
        "share_url": f"https://notebooklm.google.com/notebook/{SYNTHETIC_NOTEBOOK_ID}",
    }

    code, out, err = _run(
        mods,
        capsys,
        ["share", "view-level", "chat", "-n", SYNTHETIC_NOTEBOOK_ID, "--json"],
    )
    assert code == 0
    assert err == ""
    assert json.loads(out) == {
        "notebook_id": SYNTHETIC_NOTEBOOK_ID,
        "view_level": "chat_only",
    }

    code, out, err = _run(
        mods,
        capsys,
        [
            "share",
            "add",
            "reader@example.test",
            "-n",
            SYNTHETIC_NOTEBOOK_ID,
            "--permission",
            "editor",
            "--no-notify",
            "--message",
            "synthetic",
            "--json",
        ],
    )
    assert code == 0
    assert err == ""
    assert json.loads(out) == {
        "notebook_id": SYNTHETIC_NOTEBOOK_ID,
        "added_user": "reader@example.test",
        "permission": "editor",
        "notified": False,
    }

    code, out, err = _run(
        mods,
        capsys,
        [
            "share",
            "update",
            "fixture.viewer@example.test",
            "-n",
            SYNTHETIC_NOTEBOOK_ID,
            "--permission",
            "editor",
            "--json",
        ],
    )
    assert code == 0
    assert err == ""
    assert json.loads(out) == {
        "notebook_id": SYNTHETIC_NOTEBOOK_ID,
        "updated_user": "fixture.viewer@example.test",
        "permission": "editor",
    }

    code, out, err = _run(
        mods,
        capsys,
        [
            "share",
            "remove",
            "fixture.viewer@example.test",
            "-n",
            SYNTHETIC_NOTEBOOK_ID,
            "--yes",
            "--json",
        ],
    )
    assert code == 0
    assert err == ""
    assert json.loads(out) == {
        "notebook_id": SYNTHETIC_NOTEBOOK_ID,
        "removed_user": "fixture.viewer@example.test",
    }


def test_phase3b7_share_help_no_longer_marks_promoted_management_reserved(
    repo_root, monkeypatch, capsys
):
    mods = _load(repo_root, monkeypatch)

    for argv in (
        ["share", "public", "--help"],
        ["share", "view-level", "--help"],
        ["share", "add", "--help"],
        ["share", "update", "--help"],
        ["share", "remove", "--help"],
    ):
        with pytest.raises(SystemExit) as excinfo:
            mods.cli.console(argv)
        assert excinfo.value.code == 0
        help_text = capsys.readouterr().out
        assert "reserved for a later parity phase" not in help_text


def test_phase3b7_command_set_allows_fixture_backed_research_import(
    repo_root, monkeypatch, capsys
):
    mods = _load(repo_root, monkeypatch)

    code, out, err = _run(
        mods,
        capsys,
        ["research", "wait", "-n", SYNTHETIC_NOTEBOOK_ID, "--import-all", "--json"],
    )
    assert code == 0
    assert err == ""
    assert json.loads(out)["imported"] == 1
