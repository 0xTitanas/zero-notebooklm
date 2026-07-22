"""Offline fixture-backed notes and note-backed mind-map API foothold.

Phase 3A9 introduced local ``NotesAPI`` list/get decoding over committed
synthetic list-notes fixtures. Later parity batches promote deterministic
in-memory note mutation and Phase 3B8 note-backed mind maps. The module still
keeps non-reference CLI note mind-map commands, live RPC, authentication stores,
browser state, credentials, and real NotebookLM data mutation outside scope.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, NoReturn

from .utils import _future_errors_enabled, _resolve_get
from .exceptions import NoteNotFoundError, RPCError
from .errors import ValidationError
from .fake_rpc import OfflineFixtureRpcClient
from .rpc.types import RPCMethod
from .rpc.types import MindMapKind
from .types import MindMap, Note


def _fail(reason: str) -> NoReturn:
    raise ValidationError(f"invalid list_notes payload: {reason}")


def _created_at(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        _fail("created_at must be integer seconds or null")
    try:
        return datetime.fromtimestamp(value, timezone.utc)
    except (OverflowError, OSError, ValueError):
        pass
    _fail("created_at is out of range")


def _created_at_dict(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def parse_list_notes_payload(payload: Any) -> list[Note]:
    """Parse a decoded synthetic list-notes payload into note models."""

    if not isinstance(payload, list):
        _fail("expected note rows")
    parsed: list[Note] = []
    for row in payload:
        if not isinstance(row, list) or len(row) < 5:
            _fail("note row is malformed")
        note_id, notebook_id, title, content, raw_created_at = row[:5]
        if not isinstance(note_id, str) or note_id == "":
            _fail("note id must be non-empty text")
        if not isinstance(notebook_id, str) or notebook_id == "":
            _fail("notebook id must be non-empty text")
        if not isinstance(title, str):
            _fail("note title must be text")
        if not isinstance(content, str):
            _fail("note content must be text")
        parsed.append(
            Note(
                id=note_id,
                notebook_id=notebook_id,
                title=title,
                content=content,
                created_at=_created_at(raw_created_at),
            )
        )
    return parsed


class OfflineNoteService:
    """In-memory service over synthetic note payloads."""

    def __init__(self, notes_by_notebook: dict[str, list[Note]]) -> None:
        self._notes_by_notebook = {
            notebook_id: list(notes) for notebook_id, notes in notes_by_notebook.items()
        }
        self._created_count = 0

    @classmethod
    def from_rpc(
        cls, rpc: OfflineFixtureRpcClient, notebook_ids: list[str]
    ) -> "OfflineNoteService":
        notes_by_notebook: dict[str, list[Note]] = {}
        for notebook_id in notebook_ids:
            try:
                payload = rpc.list_notes_payload(notebook_id)
            except ValidationError:
                notes_by_notebook[notebook_id] = []
                continue
            notes_by_notebook[notebook_id] = parse_list_notes_payload(payload)
        return cls(notes_by_notebook)

    def list(self, notebook_id: str) -> list[Note]:
        return list(self._notes_by_notebook.get(notebook_id, ()))

    def get(self, notebook_id: str, note_id: str) -> Note | None:
        for note in self._notes_by_notebook.get(notebook_id, ()):
            if note.id == note_id:
                return note
        return None

    def create(
        self, notebook_id: str, title: str = "New Note", content: str = ""
    ) -> Note:
        if not isinstance(notebook_id, str) or notebook_id == "":
            raise ValidationError("notebook id must be non-empty text")
        if not isinstance(title, str):
            raise ValidationError("note title must be text")
        if not isinstance(content, str):
            raise ValidationError("note content must be text")
        self._created_count += 1
        note = Note(
            id=f"offline-note-{self._created_count:04d}",
            notebook_id=notebook_id,
            title=title,
            content=content,
            created_at=datetime.fromtimestamp(self._created_count, timezone.utc),
        )
        self._notes_by_notebook.setdefault(notebook_id, []).append(note)
        return note

    def update(self, notebook_id: str, note_id: str, content: str, title: str) -> None:
        note = self.get(notebook_id, note_id)
        if note is None:
            raise ValidationError("note not found")
        if not isinstance(title, str):
            raise ValidationError("note title must be text")
        if not isinstance(content, str):
            raise ValidationError("note content must be text")
        note.title = title
        note.content = content

    def delete(self, notebook_id: str, note_id: str) -> None:
        notes = self._notes_by_notebook.setdefault(notebook_id, [])
        self._notes_by_notebook[notebook_id] = [
            note for note in notes if note.id != note_id
        ]


class OfflineMindMapService:
    """In-memory synthetic mind-map service for offline API parity."""

    def __init__(self, maps_by_notebook: dict[str, list[MindMap]]) -> None:
        self._maps_by_notebook = {
            notebook_id: [self._clone(mind_map) for mind_map in mind_maps]
            for notebook_id, mind_maps in maps_by_notebook.items()
        }
        self._created_count = 0

    @classmethod
    def for_notebooks(cls, notebook_ids: list[str]) -> "OfflineMindMapService":
        maps_by_notebook: dict[str, list[MindMap]] = {}
        for notebook_id in notebook_ids:
            maps_by_notebook[notebook_id] = [
                MindMap(
                    id="fake-mind-map-0001",
                    notebook_id=notebook_id,
                    title="Synthetic Mind Map",
                    kind=MindMapKind.NOTE_BACKED,
                    created_at=datetime.fromtimestamp(1750000500, timezone.utc),
                    tree={"name": "Synthetic Mind Map", "children": []},
                )
            ]
        return cls(maps_by_notebook)

    def _clone(self, mind_map: MindMap) -> MindMap:
        return MindMap(
            id=mind_map.id,
            notebook_id=mind_map.notebook_id,
            title=mind_map.title,
            kind=mind_map.kind,
            created_at=mind_map.created_at,
            tree=copy.deepcopy(mind_map.tree),
        )

    def _coerce_kind(self, kind: MindMapKind | str | None) -> MindMapKind | None:
        if kind is None:
            return None
        if isinstance(kind, MindMapKind):
            return kind
        try:
            return MindMapKind(kind)
        except (TypeError, ValueError) as exc:
            raise ValidationError("unknown mind map kind") from exc

    def list(self, notebook_id: str) -> list[MindMap]:
        return [
            self._clone(mind_map)
            for mind_map in self._maps_by_notebook.get(notebook_id, [])
        ]

    def get(self, notebook_id: str, mind_map_id: str) -> MindMap | None:
        for mind_map in self._maps_by_notebook.get(notebook_id, []):
            if mind_map.id == mind_map_id:
                return self._clone(mind_map)
        return None

    def get_tree(self, notebook_id: str, mind_map_id: str) -> dict[str, Any] | None:
        mind_map = self.get(notebook_id, mind_map_id)
        return copy.deepcopy(mind_map.tree) if mind_map is not None else None

    def delete(self, notebook_id: str, mind_map_id: str) -> None:
        maps = self._maps_by_notebook.setdefault(notebook_id, [])
        self._maps_by_notebook[notebook_id] = [
            mind_map for mind_map in maps if mind_map.id != mind_map_id
        ]

    def rename(
        self, notebook_id: str, mind_map_id: str, new_title: str
    ) -> MindMap | None:
        if not isinstance(new_title, str):
            raise ValidationError("mind map title must be text")
        maps = self._maps_by_notebook.setdefault(notebook_id, [])
        for index, mind_map in enumerate(maps):
            if mind_map.id == mind_map_id:
                renamed = MindMap(
                    id=mind_map.id,
                    notebook_id=mind_map.notebook_id,
                    title=new_title,
                    kind=mind_map.kind,
                    created_at=mind_map.created_at,
                    tree=copy.deepcopy(mind_map.tree),
                )
                maps[index] = renamed
                return self._clone(renamed)
        return None

    def generate(
        self,
        notebook_id: str,
        source_ids: list[str] | None,
        *,
        kind: MindMapKind,
        language: str | None = "en",
        instructions: str | None = None,
    ) -> MindMap:
        kind = self._coerce_kind(kind) or MindMapKind.NOTE_BACKED
        self._created_count += 1
        tree: dict[str, Any] = {"name": "Synthetic Mind Map", "children": []}
        if source_ids is not None:
            tree["source_ids"] = list(source_ids)
        if language is not None:
            tree["language"] = language
        if instructions is not None:
            tree["instructions"] = instructions
        mind_map = MindMap(
            id=f"offline-mind-map-{self._created_count:04d}",
            notebook_id=notebook_id,
            title="Synthetic Mind Map",
            kind=kind,
            created_at=datetime.fromtimestamp(self._created_count, timezone.utc),
            tree=tree,
        )
        self._maps_by_notebook.setdefault(notebook_id, []).append(mind_map)
        return self._clone(mind_map)


class NotesAPI:
    """Offline synthetic notes sub-client over the fake RPC seam."""

    def __init__(
        self,
        *,
        notes: OfflineNoteService,
        mind_maps: Any = None,
        live_rpc: Any = None,
    ) -> None:
        self._notes = notes
        self._mind_maps = mind_maps
        self._live_rpc = live_rpc

    async def list(self, notebook_id: str) -> list[Note]:
        if self._live_rpc is not None:
            notes: list[Note] = []
            for row in await self._live_note_rows(notebook_id):
                if self._live_note_deleted(row):
                    continue
                content = self._live_note_content(row)
                if content is None or self._live_note_is_mind_map(content):
                    continue
                notes.append(self._live_note_from_row(row, notebook_id))
            return notes
        return self._notes.list(notebook_id)

    async def get(self, notebook_id: str, note_id: str) -> Note | None:
        return _resolve_get(
            await self.get_or_none(notebook_id, note_id),
            not_found=NoteNotFoundError(note_id),
            resource="note",
        )

    async def get_or_none(self, notebook_id: str, note_id: str) -> Note | None:
        if self._live_rpc is not None:
            for note in await self.list(notebook_id):
                if note.id == note_id:
                    return note
            return None
        return self._notes.get(notebook_id, note_id)

    async def create(
        self, notebook_id: str, title: str = "New Note", content: str = ""
    ) -> Note:
        if self._live_rpc is not None:
            result = await self._live_rpc.rpc_call(
                RPCMethod.CREATE_NOTE,
                [notebook_id, "", [1], None, title],
                source_path=f"/notebook/{notebook_id}",
                operation_variant="plain",
            )
            note_id = self._live_created_note_id(result)
            if not note_id:
                raise RPCError("CREATE_NOTE returned no usable note id")
            await self._live_rpc.rpc_call(
                RPCMethod.UPDATE_NOTE,
                [notebook_id, note_id, [[[content, title, [], 0]]]],
                source_path=f"/notebook/{notebook_id}",
                allow_null=True,
            )
            return Note(id=note_id, notebook_id=notebook_id, title=title, content=content)
        return self._notes.create(notebook_id, title, content)

    async def delete(self, notebook_id: str, note_id: str) -> None:
        if self._live_rpc is not None:
            await self._live_rpc.rpc_call(
                RPCMethod.DELETE_NOTE,
                [notebook_id, None, [note_id]],
                source_path=f"/notebook/{notebook_id}",
                allow_null=True,
            )
            return None
        self._notes.delete(notebook_id, note_id)

    async def delete_mind_map(self, notebook_id: str, mind_map_id: str) -> None:
        if self._mind_maps is None:
            return None
        self._mind_maps.delete(notebook_id, mind_map_id)

    async def list_mind_maps(self, notebook_id: str) -> list[Any]:
        if self._mind_maps is None:
            return []
        return self._mind_maps.list(notebook_id)

    async def update(
        self, notebook_id: str, note_id: str, content: str, title: str
    ) -> None:
        if _future_errors_enabled() and await self.get_or_none(notebook_id, note_id) is None:
            raise NoteNotFoundError(note_id)
        if self._live_rpc is not None:
            await self._live_rpc.rpc_call(
                RPCMethod.UPDATE_NOTE,
                [notebook_id, note_id, [[[content, title, [], 0]]]],
                source_path=f"/notebook/{notebook_id}",
                allow_null=True,
            )
            return None
        if self._notes.get(notebook_id, note_id) is None:
            return None
        self._notes.update(notebook_id, note_id, content, title)

    async def _live_note_rows(self, notebook_id: str) -> list[Any]:
        result = await self._live_rpc.rpc_call(
            RPCMethod.GET_NOTES_AND_MIND_MAPS,
            [notebook_id],
            source_path=f"/notebook/{notebook_id}",
            allow_null=True,
        )
        rows = self._live_note_row_container(result)
        normalized: list[Any] = []
        for row in rows:
            item = self._normalize_live_note_row(row)
            if item is not None:
                normalized.append(item)
        return normalized

    @classmethod
    def _live_note_row_container(cls, result: Any) -> list[Any]:
        if not result or not isinstance(result, list):
            return []
        first = result[0]
        if cls._is_live_note_row_like(first):
            return result
        if isinstance(first, list):
            return first
        return []

    @classmethod
    def _normalize_live_note_row(cls, item: Any) -> list[Any] | None:
        if not cls._is_live_note_row_like(item):
            return None
        if isinstance(item[0], str):
            return item
        nested = item[1]
        return [nested[0], nested, *item[2:]]

    @staticmethod
    def _is_live_note_row_like(item: Any) -> bool:
        if not isinstance(item, list) or not item:
            return False
        if isinstance(item[0], str):
            return True
        if item[0] is not None or len(item) <= 1:
            return False
        nested = item[1]
        return isinstance(nested, list) and bool(nested) and isinstance(nested[0], str)

    @staticmethod
    def _live_note_deleted(row: list[Any]) -> bool:
        return len(row) > 2 and row[1] is None and row[2] == 2

    @staticmethod
    def _live_note_content(row: list[Any]) -> str | None:
        if len(row) <= 1:
            return None
        slot = row[1]
        if isinstance(slot, str):
            return slot
        if isinstance(slot, list) and len(slot) > 1 and isinstance(slot[1], str):
            return slot[1]
        return None

    @staticmethod
    def _live_note_title(row: list[Any]) -> str:
        if len(row) > 1 and isinstance(row[1], list) and len(row[1]) > 4:
            title = row[1][4]
            return title if isinstance(title, str) else ""
        return ""

    @staticmethod
    def _live_note_is_mind_map(content: str) -> bool:
        return content.startswith("{") and (
            '"children":' in content or '"nodes":' in content
        )

    @classmethod
    def _live_note_from_row(cls, row: list[Any], notebook_id: str) -> Note:
        return Note(
            id=str(row[0]),
            notebook_id=notebook_id,
            title=cls._live_note_title(row),
            content=cls._live_note_content(row) or "",
        )

    @staticmethod
    def _live_created_note_id(result: Any) -> str | None:
        if not result or not isinstance(result, list):
            return None
        first = result[0]
        if isinstance(first, str):
            return first
        if isinstance(first, list) and first and isinstance(first[0], str):
            return first[0]
        return None


__all__ = [
    "Note",
    "NotesAPI",
    "OfflineMindMapService",
    "OfflineNoteService",
    "parse_list_notes_payload",
]
