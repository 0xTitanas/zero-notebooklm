"""Phase 3A9/3B8 offline Python notes API over synthetic fixtures.

Phase 3A9 introduced read-only, fixture-backed ``client.notes`` list/get
decoding over committed synthetic list-notes fixtures. Later batches promote
in-memory note mutation and Phase 3B8 note-backed mind maps while non-reference
CLI note mind-map commands, live RPC, browser/auth, credential access, and real
NotebookLM data mutation remain out of scope.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

import import_origin_audit  # noqa: E402  (placed on sys.path by tests/conftest.py)

SYNTHETIC_NOTEBOOK_ID = "fake-notebook-0001"
EXPECTED_NOTE_DICTS = [
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


def _client():
    from notebooklm import AuthTokens, NotebookLMClient

    return NotebookLMClient(
        AuthTokens(cookies={}, csrf_token="synthetic", session_id="synthetic")
    )


def test_root_exports_offline_notes_api_model_and_golden_methods(python_api):
    import notebooklm

    required = {"Note"}
    for name in required:
        assert hasattr(notebooklm, name)
    assert not hasattr(notebooklm, "NotesAPI")

    assert python_api["subclients"]["notes"]["async_methods"] == [
        "create",
        "delete",
        "delete_mind_map",
        "get",
        "get_or_none",
        "list",
        "list_mind_maps",
        "update",
    ]


def test_note_fixture_pair_is_committed_and_decoded_through_fake_rpc(compat_dir):
    from notebooklm.fake_rpc import (
        LIST_NOTES_RPCID,
        OfflineFixtureRpcClient,
        list_notes_request,
    )

    fixture_dir = compat_dir / "rpc_fixtures"
    assert (fixture_dir / "list_notes.request.txt").is_file()
    assert (fixture_dir / "list_notes.response.txt").is_file()

    client = OfflineFixtureRpcClient.from_fixture_dir(fixture_dir)
    request = list_notes_request(SYNTHETIC_NOTEBOOK_ID)
    assert request.rpcid == LIST_NOTES_RPCID
    payload = client.list_notes_payload(SYNTHETIC_NOTEBOOK_ID)
    assert payload[0][0] == "fake-note-0001"
    assert payload[1][0] == "fake-note-0002"


def test_offline_client_lists_and_gets_notes_without_home(monkeypatch):
    _poison_home(monkeypatch)

    async def scenario():
        client = _client()
        notes = await client.notes.list(SYNTHETIC_NOTEBOOK_ID)
        assert [note.as_dict() for note in notes] == EXPECTED_NOTE_DICTS

        first = await client.notes.get(SYNTHETIC_NOTEBOOK_ID, "fake-note-0001")
        assert first is not None
        assert first.as_dict() == EXPECTED_NOTE_DICTS[0]
        assert (await client.notes.get(SYNTHETIC_NOTEBOOK_ID, "missing-note")) is None
        assert (
            await client.notes.get_or_none(SYNTHETIC_NOTEBOOK_ID, "missing-note")
        ) is None
        assert await client.notes.list("missing-notebook") == []

    asyncio.run(scenario())


def test_note_model_matches_pinned_shape_and_redacted_dict():
    from notebooklm import Note

    created_at = datetime.fromtimestamp(1750000400, timezone.utc)
    note = Note(
        id="custom-note",
        notebook_id="custom-notebook",
        title="Custom",
        content="Local synthetic content",
        created_at=created_at,
    )
    assert note.as_dict() == {
        "id": "custom-note",
        "notebook_id": "custom-notebook",
        "title": "Custom",
        "content": "Local synthetic content",
        "created_at": "2025-06-15T15:13:20+00:00",
    }


def test_list_notes_payload_validation_is_strict_and_redacted():
    from notebooklm.notes import parse_list_notes_payload
    from notebooklm.errors import ValidationError

    private_payload = [["fake-note", "fake-notebook", "Title", object(), 1750000300]]
    with pytest.raises(ValidationError) as excinfo:
        parse_list_notes_payload(private_payload)
    assert str(excinfo.value) == "invalid list_notes payload: note content must be text"
    assert "object at" not in str(excinfo.value)
    assert excinfo.value.__context__ is None
    assert excinfo.value.__cause__ is None


def test_list_notes_created_at_out_of_range_drops_exception_context():
    from notebooklm.notes import parse_list_notes_payload
    from notebooklm.errors import ValidationError

    payload = [["fake-note", "fake-notebook", "Title", "Body", 10**100]]
    with pytest.raises(ValidationError) as excinfo:
        parse_list_notes_payload(payload)
    assert (
        str(excinfo.value) == "invalid list_notes payload: created_at is out of range"
    )
    assert excinfo.value.__context__ is None
    assert excinfo.value.__cause__ is None


def test_note_mind_map_backing_is_promoted_over_synthetic_maps():
    client = _client()

    maps = asyncio.run(client.notes.list_mind_maps(SYNTHETIC_NOTEBOOK_ID))
    assert [mind_map.id for mind_map in maps] == ["fake-mind-map-0001"]

    asyncio.run(
        client.notes.delete_mind_map(SYNTHETIC_NOTEBOOK_ID, "fake-mind-map-0001")
    )
    assert asyncio.run(client.notes.list_mind_maps(SYNTHETIC_NOTEBOOK_ID)) == []


def test_phase3a9_keeps_unrelated_cli_surfaces_unpromoted():
    from notebooklm import cli

    assert {"list", "use", "artifact", "ask", "metadata", "summary"} <= set(
        cli.IMPLEMENTED_COMMANDS
    )
    assert "download" in cli.IMPLEMENTED_COMMANDS
    assert "generate" in cli.IMPLEMENTED_COMMANDS


def test_phase3a9_python_notes_wiring_is_stdlib_and_offline_only(repo_root):
    assert (
        import_origin_audit.audit(
            roots=(
                "notebooklm/__init__.py",
                "notebooklm/client.py",
                "notebooklm/fake_rpc.py",
                "notebooklm/notes.py",
            ),
        )
        == []
    )
    src = "\n".join(
        (repo_root / "notebooklm" / name).read_text(encoding="utf-8")
        for name in ("client.py", "fake_rpc.py", "notes.py")
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
