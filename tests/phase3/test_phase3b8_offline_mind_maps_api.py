"""Phase 3B8 fixture-backed mind-map API parity batch.

This batch promotes the note-backed mind-map API surfaces over deterministic
in-memory synthetic maps only. It does not add non-reference CLI leaves and does
not enter live RPC, auth/browser/home reads, credentials, network, or real
NotebookLM mutation.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path

SYNTHETIC_NOTEBOOK_ID = "fake-notebook-0001"
SYNTHETIC_MIND_MAP_ID = "fake-mind-map-0001"


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


def test_phase3b8_notes_mind_map_backing_is_fixture_backed_without_home(monkeypatch):
    _poison_home(monkeypatch)

    async def scenario():
        client = _client()
        maps = await client.notes.list_mind_maps(SYNTHETIC_NOTEBOOK_ID)
        assert [mind_map.id for mind_map in maps] == [SYNTHETIC_MIND_MAP_ID]
        assert maps[0].title == "Synthetic Mind Map"
        assert maps[0].tree == {"name": "Synthetic Mind Map", "children": []}

        await client.notes.delete_mind_map(SYNTHETIC_NOTEBOOK_ID, SYNTHETIC_MIND_MAP_ID)
        assert await client.notes.list_mind_maps(SYNTHETIC_NOTEBOOK_ID) == []

    asyncio.run(scenario())


def test_phase3b8_mind_maps_api_read_write_cycle_is_in_memory(monkeypatch):
    _poison_home(monkeypatch)

    async def scenario():
        from notebooklm.types import MindMapKind

        client = _client()
        initial = await client.mind_maps.list(SYNTHETIC_NOTEBOOK_ID)
        assert [mind_map.id for mind_map in initial] == [SYNTHETIC_MIND_MAP_ID]
        assert await client.mind_maps.get(SYNTHETIC_NOTEBOOK_ID, "missing") is None
        assert (
            await client.mind_maps.get_or_none(SYNTHETIC_NOTEBOOK_ID, "missing") is None
        )
        tree = await client.mind_maps.get_tree(
            SYNTHETIC_NOTEBOOK_ID, SYNTHETIC_MIND_MAP_ID
        )
        assert tree == {"name": "Synthetic Mind Map", "children": []}

        renamed = await client.mind_maps.rename(
            SYNTHETIC_NOTEBOOK_ID,
            SYNTHETIC_MIND_MAP_ID,
            "Renamed Mind Map",
            kind=MindMapKind.NOTE_BACKED,
        )
        assert renamed is not None
        assert renamed.title == "Renamed Mind Map"

        generated = await client.mind_maps.generate(
            SYNTHETIC_NOTEBOOK_ID,
            ["fake-source-0001"],
            kind=MindMapKind.INTERACTIVE,
            language="en",
            instructions="synthetic only",
        )
        assert generated.id.startswith("offline-mind-map-")
        assert generated.kind is MindMapKind.INTERACTIVE
        assert generated.tree == {
            "name": "Synthetic Mind Map",
            "children": [],
            "source_ids": ["fake-source-0001"],
            "language": "en",
            "instructions": "synthetic only",
        }

        await client.mind_maps.delete(SYNTHETIC_NOTEBOOK_ID, generated.id)
        assert await client.mind_maps.get(SYNTHETIC_NOTEBOOK_ID, generated.id) is None

    asyncio.run(scenario())


def test_phase3b8_mind_map_api_signatures_keep_stable_public_shapes():
    from notebooklm.client import MindMapsAPI
    from notebooklm.notes import NotesAPI

    expected_notes = {
        "delete_mind_map": "(self, notebook_id: 'str', mind_map_id: 'str') -> 'None'",
        "list_mind_maps": "(self, notebook_id: 'str') -> 'list[Any]'",
    }
    expected_mind_maps = {
        "delete": "(self, notebook_id: 'str', mind_map_id: 'str', *, kind: 'MindMapKind | None' = None) -> 'None'",
        "generate": "(self, notebook_id: 'str', source_ids: 'list[str] | None' = None, *, kind: 'MindMapKind', language: 'str | None' = 'en', instructions: 'str | None' = None, wait: 'bool' = True) -> 'MindMap'",
        "get": "(self, notebook_id: 'str', mind_map_id: 'str') -> 'MindMap | None'",
        "get_or_none": "(self, notebook_id: 'str', mind_map_id: 'str') -> 'MindMap | None'",
        "get_tree": "(self, notebook_id: 'str', mind_map_id: 'str', *, kind: 'MindMapKind | None' = None) -> 'dict[str, Any] | None'",
        "list": "(self, notebook_id: 'str') -> 'list[MindMap]'",
        "rename": "(self, notebook_id: 'str', mind_map_id: 'str', new_title: 'str', *, kind: 'MindMapKind | None' = None, return_object: 'bool' = True) -> 'MindMap | None'",
    }

    actual_notes = {
        name: str(inspect.signature(getattr(NotesAPI, name))) for name in expected_notes
    }
    actual_mind_maps = {
        name: str(inspect.signature(getattr(MindMapsAPI, name)))
        for name in expected_mind_maps
    }
    assert actual_notes == expected_notes
    assert actual_mind_maps == expected_mind_maps


def test_phase3b8_does_not_add_non_reference_note_mind_map_cli(
    repo_root, monkeypatch, capsys
):
    monkeypatch.syspath_prepend(str(repo_root))
    from notebooklm import cli

    for argv in (
        ["note", "list-mind-maps", "-n", SYNTHETIC_NOTEBOOK_ID],
        ["note", "delete-mind-map", SYNTHETIC_MIND_MAP_ID, "-n", SYNTHETIC_NOTEBOOK_ID],
    ):
        code = cli.console(argv)
        captured = capsys.readouterr()
        assert code == 78
        assert captured.out == ""
        assert "reserved for a later parity phase" in captured.err


def test_phase3b8_allows_fixture_backed_research_import(repo_root, monkeypatch, capsys):
    monkeypatch.syspath_prepend(str(repo_root))
    from notebooklm import cli

    code = cli.console(
        ["research", "wait", "-n", SYNTHETIC_NOTEBOOK_ID, "--import-all", "--json"]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert captured.err == ""
    assert json.loads(captured.out)["imported"] == 1
