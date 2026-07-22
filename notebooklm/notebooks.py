"""Offline notebook metadata models for synthetic Phase 3A fixtures.

This module is deliberately local-only: it consumes already-decoded synthetic
``LIST_NOTEBOOKS`` payloads and performs deterministic notebook selection. It does
not send RPCs, read browser/auth state, touch credentials, or mutate NotebookLM.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .errors import ValidationError
from .types import Notebook, NotebookMetadata


def _fail(reason: str) -> None:
    """Raise a deterministic, input-redacted notebook payload error."""

    raise ValidationError(f"invalid list_notebooks payload: {reason}")


def _created_at(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        _fail("created_at must be integer seconds or null")
    try:
        return datetime.fromtimestamp(value, timezone.utc)
    except (OverflowError, OSError, ValueError):
        _fail("created_at is out of range")


def _created_at_dict(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_list_payload_with_sources(payload: Any) -> list[tuple[Notebook, list[str]]]:
    if not isinstance(payload, list) or len(payload) != 1:
        _fail("expected outer singleton list")
    rows = payload[0]
    if not isinstance(rows, list):
        _fail("expected notebook rows")

    parsed: list[tuple[Notebook, list[str]]] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 4:
            _fail("notebook row is malformed")
        notebook_id, title, source_ids, created_raw = row[:4]
        if not isinstance(notebook_id, str) or notebook_id == "":
            _fail("notebook id must be non-empty text")
        if not isinstance(title, str) or title == "":
            _fail("notebook title must be non-empty text")
        if not isinstance(source_ids, list):
            _fail("source id list must be a list")
        for source_id in source_ids:
            if not isinstance(source_id, str) or source_id == "":
                _fail("source ids must be non-empty text")
        ids = list(source_ids)
        parsed.append(
            (
                Notebook(
                    id=notebook_id,
                    title=title,
                    created_at=_created_at(created_raw),
                    sources_count=len(ids),
                    is_owner=True,
                ),
                ids,
            )
        )
    return parsed


def parse_list_notebooks_payload(payload: Any) -> list[Notebook]:
    """Parse a decoded synthetic LIST_NOTEBOOKS payload into notebooks."""

    return [notebook for notebook, _ in _parse_list_payload_with_sources(payload)]


def resolve_notebook(
    notebooks: list[Notebook] | tuple[Notebook, ...], selector: str
) -> Notebook:
    """Resolve a notebook by exact id, exact title, or unambiguous id prefix."""

    if not isinstance(selector, str) or selector == "":
        raise ValidationError("notebook selector not found")
    exact_ids = [notebook for notebook in notebooks if notebook.id == selector]
    if exact_ids:
        return exact_ids[0]

    title_matches = [notebook for notebook in notebooks if notebook.title == selector]
    if len(title_matches) == 1:
        return title_matches[0]
    if len(title_matches) > 1:
        raise ValidationError("notebook selector is ambiguous")

    prefix_matches = [
        notebook for notebook in notebooks if notebook.id.startswith(selector)
    ]
    if len(prefix_matches) == 1:
        return prefix_matches[0]
    if len(prefix_matches) > 1:
        raise ValidationError("notebook selector is ambiguous")
    raise ValidationError("notebook selector not found")


class OfflineNotebookMetadataService:
    """In-memory service over offline notebook metadata."""

    def __init__(
        self,
        notebooks: list[Notebook] | tuple[Notebook, ...],
        *,
        source_ids_by_id: dict[str, list[str]] | None = None,
    ) -> None:
        self._notebooks = list(notebooks)
        mapping = source_ids_by_id or {}
        self._source_ids_by_id = {key: list(value) for key, value in mapping.items()}
        self._created_count = 0

    @classmethod
    def from_list_payload(cls, payload: Any) -> "OfflineNotebookMetadataService":
        parsed = _parse_list_payload_with_sources(payload)
        notebooks = [notebook for notebook, _ in parsed]
        source_ids = {notebook.id: ids for notebook, ids in parsed}
        return cls(notebooks, source_ids_by_id=source_ids)

    def list(self) -> list[Notebook]:
        return list(self._notebooks)

    def list_dicts(self) -> list[dict[str, Any]]:
        return [notebook.as_dict() for notebook in self._notebooks]

    def resolve(self, selector: str) -> Notebook:
        return resolve_notebook(self._notebooks, selector)

    def get_source_ids(self, selector: str) -> list[str]:
        notebook = self.resolve(selector)
        return list(self._source_ids_by_id.get(notebook.id, ()))

    def get_metadata(self, selector: str) -> NotebookMetadata:
        notebook = self.resolve(selector)
        return NotebookMetadata(notebook=notebook, sources=[])

    def create(self, title: str) -> Notebook:
        if not isinstance(title, str) or title == "":
            raise ValidationError("notebook title must be non-empty text")
        self._created_count += 1
        notebook = Notebook(
            id=f"offline-notebook-{self._created_count:04d}",
            title=title,
            created_at=datetime.fromtimestamp(self._created_count, timezone.utc),
            sources_count=0,
            is_owner=True,
        )
        self._notebooks.append(notebook)
        self._source_ids_by_id[notebook.id] = []
        return notebook

    def delete(self, selector: str) -> None:
        notebook = self.resolve(selector)
        self._notebooks = [item for item in self._notebooks if item.id != notebook.id]
        self._source_ids_by_id.pop(notebook.id, None)

    def rename(self, selector: str, new_title: str) -> Notebook:
        if not isinstance(new_title, str) or new_title == "":
            raise ValidationError("notebook title must be non-empty text")
        notebook = self.resolve(selector)
        renamed = Notebook(
            id=notebook.id,
            title=new_title,
            created_at=notebook.created_at,
            sources_count=notebook.sources_count,
            is_owner=notebook.is_owner,
        )
        self._notebooks = [
            renamed if item.id == notebook.id else item for item in self._notebooks
        ]
        return renamed

    def remove_from_recent(self, selector: str) -> None:
        self.resolve(selector)


__all__ = [
    "Notebook",
    "NotebookMetadata",
    "OfflineNotebookMetadataService",
    "parse_list_notebooks_payload",
    "resolve_notebook",
]
