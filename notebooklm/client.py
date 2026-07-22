"""Offline fixture-backed NotebookLM client parity surface.

The classes in this module expose deterministic, local-only notebooks/sources/
notes/artifacts/chat/settings/sharing/mind-map API footholds. They mirror
upstream client/sub-client names for parity tests and use committed fixtures plus
in-memory state; they do not send live RPCs, read credentials, or mutate real
NotebookLM data.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import inspect
import json
import logging
import math
import os
from pathlib import Path
import time
from types import TracebackType
from typing import Any, Generator, TypeAlias, cast
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

from . import auth as _auth_module
from . import http_std as _http_std
from . import profiles as _profiles
from ._artifact_payloads import build_interactive_mind_map_artifact_params
from .utils import (
    _deprecated_kwarg,
    _future_errors_enabled,
    _resolve_get,
    _warn_deprecated,
)
from ._artifacts_impl import ArtifactsAPI, OfflineArtifactService
from .auth import AuthTokens
from .chat import ChatAPI
from .config import get_default_language
from .errors import (
    AuthenticationError,
    BodyTooLargeError,
    HTTPTransportError,
    NetworkError,
    NotImplementedInPhaseError,
    TransportClosedError,
    TransportTimeoutError,
    ValidationError,
)
from .exceptions import (
    ArtifactFeatureUnavailableError,
    DecodingError,
    MindMapNotFoundError,
    NotebookNotFoundError,
    NetworkError as PublicNetworkError,
    RPCError,
    RPCResponseTooLargeError,
    RPCTimeoutError,
    RateLimitError,
    ResearchTimeoutError,
    ResearchTaskMismatchError,
    ServerError,
    UnknownRPCMethodError,
)
from .fake_rpc import FakeRpcRequest, OfflineFixtureRpcClient
from .notebooks import Notebook, NotebookMetadata, OfflineNotebookMetadataService
from .notes import NotesAPI, OfflineMindMapService, OfflineNoteService
from .offline_status import OfflineReadOnlyStatusFixtures, language_name
from .rpc.decoder import decode_response
from .rpc.encoder import build_request_body, encode_rpc_request
from .rpc.overrides import resolve_rpc_id
from .rpc.types import RPCMethod, ResearchStatus, get_batchexecute_url
from .sources import OfflineSourceService, SourcesAPI
from .types import (
    AccountLimits,
    AccountTier,
    ClientMetricsSnapshot,
    ConnectionLimits,
    ResearchSource,
    ResearchStart,
    ResearchTask,
    RpcTelemetryEvent,
    ShareAccess,
    SharePermission,
    ShareStatus,
    ShareViewLevel,
    SharedUser,
    ArtifactType,
    MindMap,
    MindMapKind,
    NotebookDescription,
    SourceSummary,
    SuggestedTopic,
)


ResponseGetter: TypeAlias = Callable[..., Any]
ResponsePoster: TypeAlias = Callable[..., Any]
CookieSaver: TypeAlias = Callable[..., Any]
CookieRotator: TypeAlias = Callable[..., Any]


class httpx:  # noqa: N801 - matches upstream annotation namespace without dependency.
    """Annotation-only httpx namespace shim; never used for transport."""

    Timeout = Any


_DEFAULT_RESEARCH_INITIAL_INTERVAL = cast(float, object())
_ERROR_INJECT_ENV_VAR = "NOTEBOOKLM_VCR_RECORD_ERRORS"
_VALID_ERROR_INJECT_MODES = {"429", "5xx", "expired_csrf"}
_MAX_RPC_RESPONSE_BYTES = 50 * 1024 * 1024
_MAX_RETRY_AFTER_SECONDS = 300
_BACKOFF_MIN_SECONDS = 0.1
_BACKOFF_CAP_SECONDS = 30.0
_TRANSPORT_NETWORK_ERRORS = (
    HTTPTransportError,
    TransportTimeoutError,
    TransportClosedError,
    OSError,
    TimeoutError,
)


def _get_error_injection_mode() -> str | None:
    raw = os.getenv(_ERROR_INJECT_ENV_VAR, "").strip()
    if not raw:
        return None
    normalized = raw.casefold()
    return normalized if normalized in _VALID_ERROR_INJECT_MODES else None


def _response_header(response: _http_std.Response, name: str) -> str | None:
    lowered = name.lower()
    for key, value in response.headers.items():
        if key.lower() == lowered:
            return value
    return None


def _parse_retry_after(value: str | None) -> int | None:
    if not value:
        return None
    value = value.strip()
    try:
        return min(_MAX_RETRY_AFTER_SECONDS, max(0, int(value)))
    except ValueError:
        pass
    try:
        seconds = float(value)
        if math.isfinite(seconds):
            return min(_MAX_RETRY_AFTER_SECONDS, max(0, math.ceil(seconds)))
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = (dt - datetime.now(timezone.utc)).total_seconds()
    return min(_MAX_RETRY_AFTER_SECONDS, max(0, int(delta)))


def _refuse_synthetic_error_outside_test_context() -> None:
    mode = _get_error_injection_mode()
    if mode is None or os.getenv("PYTEST_CURRENT_TEST"):
        return
    message = (
        f"{_ERROR_INJECT_ENV_VAR}={mode!r} is set but no pytest context was "
        "detected (PYTEST_CURRENT_TEST unset). This env var is test-only — "
        "it substitutes synthetic error responses for every batchexecute RPC "
        f"and must not be set in production. Unset {_ERROR_INJECT_ENV_VAR} "
        "to restore normal behavior, or run under pytest if synthetic-error "
        "recording is intended."
    )
    logging.getLogger("notebooklm").warning(message)
    raise RuntimeError(message)


def _normalize_import_verification_url(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path.rstrip("/"),
            parsed.query,
            "",
        )
    )


def _source_import_verification_url(source: ResearchSource) -> str | None:
    return _normalize_import_verification_url(source.url) if source.url else None


def _requested_import_verification_urls(sources: Sequence[ResearchSource]) -> set[str]:
    return {url for source in sources if (url := _source_import_verification_url(source))}


def _no_import_verification_url_entry_count(
    sources: Sequence[ResearchSource],
) -> int:
    return sum(1 for source in sources if _source_import_verification_url(source) is None)


def _imported_source_entry(source: Any) -> dict[str, str]:
    return {"id": source.id, "title": source.title or source.url or ""}


def _merge_imported_sources(
    imported: list[dict[str, str]],
    verified_imported: list[dict[str, str]],
    verified_imported_ids: set[str],
) -> list[dict[str, str]]:
    if not verified_imported:
        return imported
    return [
        *verified_imported,
        *(entry for entry in imported if entry.get("id") not in verified_imported_ids),
    ]


def _default_rpc_fixture_dir() -> Path:
    """Return the committed synthetic RPC fixture directory."""

    return Path(__file__).resolve().parent.parent / "compat" / "rpc_fixtures"


def _rpc_from_fixtures() -> OfflineFixtureRpcClient:
    """Build the read-only fake RPC client from committed fixtures."""

    return OfflineFixtureRpcClient.from_fixture_dir(_default_rpc_fixture_dir())


def _service_from_fixtures() -> OfflineNotebookMetadataService:
    """Build a read-only notebook metadata service from committed fixtures."""

    return OfflineNotebookMetadataService.from_list_payload(
        _rpc_from_fixtures().list_notebooks_payload()
    )


def _resolve_storage_path(path: str | Path | None, profile: str | None = None) -> Path:
    if path is not None:
        return Path(path)
    store = _profiles.ProfileStore(None)
    return store.storage_state_path(store.resolve_profile(profile))


class MindMapsAPI:
    """Offline synthetic mind-map sub-client over in-memory fixtures."""

    def __init__(
        self,
        rpc: Any = None,
        *,
        mind_maps: OfflineMindMapService | None = None,
        live_rpc: Any = None,
        artifacts: Any = None,
        notes: Any = None,
        notebooks: Any = None,
    ) -> None:
        self._rpc = rpc
        self._mind_maps = mind_maps or OfflineMindMapService.for_notebooks([])
        self._live_rpc = live_rpc
        self._artifacts = artifacts
        self._notes = notes
        self._notebooks = notebooks

    async def delete(
        self, notebook_id: str, mind_map_id: str, *, kind: MindMapKind | None = None
    ) -> None:
        if self._live_rpc is not None:
            if kind is None:
                note_row = await self._live_note_row(notebook_id, mind_map_id)
                if note_row is not None:
                    await self._notes.delete(notebook_id, mind_map_id)
                    return None
                if await self._find_live_interactive(notebook_id, mind_map_id) is None:
                    return None
                kind = MindMapKind.INTERACTIVE
            if kind == MindMapKind.NOTE_BACKED:
                await self._notes.delete(notebook_id, mind_map_id)
            else:
                await self._artifacts.delete(notebook_id, mind_map_id)
            return None
        self._mind_maps.delete(notebook_id, mind_map_id)

    async def generate(
        self,
        notebook_id: str,
        source_ids: list[str] | None = None,
        *,
        kind: MindMapKind,
        language: str | None = "en",
        instructions: str | None = None,
        wait: bool = True,
    ) -> MindMap:
        if self._live_rpc is not None:
            if kind == MindMapKind.NOTE_BACKED:
                result = await self._artifacts.generate_mind_map(
                    notebook_id, source_ids, language, instructions
                )
                tree = result.mind_map if isinstance(result.mind_map, dict) else None
                return MindMap(
                    id=result.note_id or "",
                    notebook_id=notebook_id,
                    title=self._tree_title(tree),
                    kind=MindMapKind.NOTE_BACKED,
                    tree=tree,
                )
            if source_ids is None:
                source_ids = await self._notebooks.get_source_ids(notebook_id)
            result = await self._live_rpc.rpc_call(
                RPCMethod.CREATE_ARTIFACT,
                build_interactive_mind_map_artifact_params(notebook_id, source_ids),
                source_path=f"/notebook/{notebook_id}",
                allow_null=True,
                operation_variant=None,
            )
            artifact_id = self._new_artifact_id(result)
            if artifact_id is None:
                raise ArtifactFeatureUnavailableError(
                    ArtifactType.MIND_MAP.value,
                    method_id=RPCMethod.CREATE_ARTIFACT.value,
                )
            if wait:
                await self._artifacts.wait_for_completion(notebook_id, artifact_id)
            artifact = await self._find_live_interactive(
                notebook_id, artifact_id, allow_unclassified=True
            )
            tree = (
                await self.get_tree(
                    notebook_id,
                    artifact.id if artifact is not None else artifact_id,
                    kind=MindMapKind.INTERACTIVE,
                )
                if wait
                else None
            )
            if artifact is not None:
                return MindMap(
                    id=artifact.id,
                    notebook_id=notebook_id,
                    title=artifact.title,
                    kind=MindMapKind.INTERACTIVE,
                    created_at=artifact.created_at,
                    tree=tree,
                )
            return MindMap(
                id=artifact_id,
                notebook_id=notebook_id,
                title="Mind Map",
                kind=MindMapKind.INTERACTIVE,
                tree=tree,
            )
        return self._mind_maps.generate(
            notebook_id,
            source_ids,
            kind=kind,
            language=language,
            instructions=instructions,
        )

    async def get(self, notebook_id: str, mind_map_id: str) -> MindMap | None:
        return _resolve_get(
            await self.get_or_none(notebook_id, mind_map_id),
            not_found=MindMapNotFoundError(mind_map_id),
            resource="mind_map",
        )

    async def get_or_none(self, notebook_id: str, mind_map_id: str) -> MindMap | None:
        if self._live_rpc is not None:
            for mind_map in await self.list(notebook_id):
                if mind_map.id == mind_map_id:
                    return mind_map
            return None
        return self._mind_maps.get(notebook_id, mind_map_id)

    async def get_tree(
        self, notebook_id: str, mind_map_id: str, *, kind: MindMapKind | None = None
    ) -> dict[str, Any] | None:
        if self._live_rpc is not None:
            if kind is None or kind == MindMapKind.NOTE_BACKED:
                note_row = await self._live_note_row(notebook_id, mind_map_id)
                if note_row is not None:
                    return self._parse_tree(self._notes._live_note_content(note_row))
                if kind == MindMapKind.NOTE_BACKED:
                    return None
            if kind is None and await self._find_live_interactive(
                notebook_id, mind_map_id
            ) is None:
                return None
            result = await self._live_rpc.rpc_call(
                RPCMethod.GET_INTERACTIVE_HTML,
                [mind_map_id],
                source_path=f"/notebook/{notebook_id}",
                allow_null=True,
            )
            return self._parse_tree(self._interactive_tree_leaf(result))
        return self._mind_maps.get_tree(notebook_id, mind_map_id)

    async def list(self, notebook_id: str) -> list[MindMap]:
        if self._live_rpc is not None:
            maps = [
                self._mind_map_from_note_row(notebook_id, row)
                for row in await self._live_note_rows(notebook_id)
            ]
            for artifact in await self._artifacts.list(notebook_id, ArtifactType.MIND_MAP):
                if artifact.is_interactive_mind_map:
                    maps.append(
                        MindMap(
                            id=artifact.id,
                            notebook_id=notebook_id,
                            title=artifact.title,
                            kind=MindMapKind.INTERACTIVE,
                            created_at=artifact.created_at,
                        )
                    )
            return maps
        return self._mind_maps.list(notebook_id)

    async def rename(
        self,
        notebook_id: str,
        mind_map_id: str,
        new_title: str,
        *,
        kind: MindMapKind | None = None,
        return_object: bool = True,
    ) -> MindMap | None:
        if self._live_rpc is not None:
            if kind is None:
                note_row = await self._live_note_row(notebook_id, mind_map_id)
                if note_row is not None:
                    await self._rename_live_note(notebook_id, mind_map_id, new_title, note_row)
                    return await self._hydrate_live_renamed(
                        notebook_id, mind_map_id, return_object
                    )
                if await self._find_live_interactive(notebook_id, mind_map_id) is None:
                    raise MindMapNotFoundError(mind_map_id)
                await self._artifacts.rename(
                    notebook_id, mind_map_id, new_title, return_object=False
                )
                return await self._hydrate_live_renamed(
                    notebook_id, mind_map_id, return_object
                )
            if kind == MindMapKind.NOTE_BACKED:
                note_row = await self._live_note_row(notebook_id, mind_map_id)
                if note_row is None:
                    raise MindMapNotFoundError(mind_map_id)
                await self._rename_live_note(notebook_id, mind_map_id, new_title, note_row)
            else:
                if await self._find_live_interactive(notebook_id, mind_map_id) is None:
                    raise MindMapNotFoundError(mind_map_id)
                await self._artifacts.rename(
                    notebook_id, mind_map_id, new_title, return_object=False
                )
            return await self._hydrate_live_renamed(
                notebook_id, mind_map_id, return_object
            )
        renamed = self._mind_maps.rename(notebook_id, mind_map_id, new_title)
        return renamed if return_object else None

    async def _hydrate_live_renamed(
        self, notebook_id: str, mind_map_id: str, return_object: bool
    ) -> MindMap | None:
        if not return_object:
            return None
        mind_map = await self.get_or_none(notebook_id, mind_map_id)
        if mind_map is None:
            raise MindMapNotFoundError(mind_map_id)
        return mind_map

    async def _rename_live_note(
        self, notebook_id: str, mind_map_id: str, title: str, row: list[Any]
    ) -> None:
        await self._notes.update(
            notebook_id,
            mind_map_id,
            self._notes._live_note_content(row) or "",
            title,
        )

    async def _live_note_row(
        self, notebook_id: str, mind_map_id: str
    ) -> list[Any] | None:
        for row in await self._live_note_rows(notebook_id):
            if row and row[0] == mind_map_id:
                return row
        return None

    async def _live_note_rows(self, notebook_id: str) -> list[Any]:
        rows: list[Any] = []
        for row in await self._notes._live_note_rows(notebook_id):
            content = self._notes._live_note_content(row)
            if content and self._parse_tree(content) is not None:
                rows.append(row)
        return rows

    def _mind_map_from_note_row(self, notebook_id: str, row: list[Any]) -> MindMap:
        tree = self._parse_tree(self._notes._live_note_content(row))
        return MindMap(
            id=str(row[0]),
            notebook_id=notebook_id,
            title=self._notes._live_note_title(row),
            kind=MindMapKind.NOTE_BACKED,
            tree=tree,
        )

    async def _find_live_interactive(
        self, notebook_id: str, mind_map_id: str, *, allow_unclassified: bool = False
    ) -> Any | None:
        for artifact in await self._artifacts.list(notebook_id):
            if artifact.id != mind_map_id:
                continue
            if artifact.is_interactive_mind_map or (
                allow_unclassified and artifact.is_unclassified_type4
            ):
                return artifact
        return None

    @staticmethod
    def _new_artifact_id(result: Any) -> str | None:
        if not isinstance(result, list) or not result:
            return None
        inner = result[0]
        if isinstance(inner, list) and inner and isinstance(inner[0], str):
            return inner[0]
        return None

    @staticmethod
    def _interactive_tree_leaf(result: Any) -> Any | None:
        if result is None:
            return None
        try:
            options = result[0][9]
        except (IndexError, TypeError):
            raise UnknownRPCMethodError("unexpected GET_INTERACTIVE_HTML payload") from None
        if not isinstance(options, list):
            raise UnknownRPCMethodError("unexpected GET_INTERACTIVE_HTML options block")
        return options[3] if len(options) > 3 else None

    @staticmethod
    def _parse_tree(content: Any) -> dict[str, Any] | None:
        if not isinstance(content, str) or content == "":
            return None
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _tree_title(tree: dict[str, Any] | None) -> str:
        name = tree.get("name") if isinstance(tree, dict) else None
        return name if isinstance(name, str) and name else "Mind Map"


class ResearchAPI:
    """Offline research sub-client over deterministic in-memory state."""

    def __init__(
        self,
        rpc: Any = None,
        status_fixtures: OfflineReadOnlyStatusFixtures | None = None,
        *,
        source_service: OfflineSourceService | None = None,
        source_lister: Callable[..., Any] | None = None,
        live_rpc: Any = None,
    ) -> None:
        self._rpc = rpc
        self._live_rpc = live_rpc
        self._status_fixtures = (
            status_fixtures or OfflineReadOnlyStatusFixtures.load_default()
        )
        self._source_service = source_service or OfflineSourceService.from_rpc(rpc, [])
        self._source_lister = source_lister
        self._started_count = 0
        self._started_tasks: dict[str, ResearchTask] = {}
        self._started_task_notebooks: dict[str, str] = {}

    @staticmethod
    def _coerce_source(source: Any) -> ResearchSource:
        if isinstance(source, ResearchSource):
            return source
        if not isinstance(source, Mapping):
            raise ValidationError("research source must be a mapping or ResearchSource")
        return ResearchSource.from_public_dict(source)

    @staticmethod
    def _is_importable_report_source(
        source_input: Any, source: ResearchSource
    ) -> bool:
        if not source.is_report or not source.report_markdown:
            return False
        if isinstance(source_input, ResearchSource):
            return isinstance(source.title, str)
        return isinstance(source_input.get("title"), str) and isinstance(
            source_input.get("report_markdown"), str
        )

    async def _list_sources_for_verification(self, notebook_id: str) -> list[Any]:
        if self._source_lister is not None:
            return await self._source_lister(notebook_id, strict=True)
        if not self._source_service.has_notebook(notebook_id):
            raise ValidationError("source notebook not found")
        return self._source_service.list(notebook_id)

    @staticmethod
    def _warn_ambiguous_poll(notebook_id: str, task_count: int) -> None:
        _warn_deprecated(
            f"ResearchAPI.poll(notebook_id={notebook_id!r}) returned "
            f"{task_count} in-flight tasks but no task_id discriminator was "
            "supplied. The latest task is returned for back-compat, but this "
            "is ambiguous and may surface results for the wrong task. Pass "
            "task_id=<id> (from research.start) to select explicitly. The "
            "None default will be removed in a future major release.",
            removal=None,
            stacklevel=4,
        )

    @staticmethod
    def _public_poll_result(task: ResearchTask) -> ResearchTask:
        public_task = ResearchTask(
            task_id=task.task_id,
            status=task.status,
            query=task.query,
            sources=task.sources,
            summary=task.summary,
            report=task.report,
        )
        return ResearchTask(
            task_id=public_task.task_id,
            status=public_task.status,
            query=public_task.query,
            sources=public_task.sources,
            summary=public_task.summary,
            report=public_task.report,
            tasks=(public_task,),
        )

    @staticmethod
    def _completed_started_task(task: ResearchTask) -> ResearchTask:
        source = ResearchSource(
            url=f"https://example.test/notebooklm-bare/research/{task.task_id}",
            title=f"Synthetic research result for {task.query}",
            research_task_id=task.task_id,
        )
        completed = ResearchTask(
            task_id=task.task_id,
            status=ResearchStatus.COMPLETED,
            query=task.query,
            sources=(source,),
            summary=f"Synthetic research completed for {task.query}.",
            report=(
                "# Synthetic Research Report\n\n"
                f"Offline fixture-backed research report for {task.query}."
            ),
        )
        return ResearchTask(
            task_id=completed.task_id,
            status=completed.status,
            query=completed.query,
            sources=completed.sources,
            summary=completed.summary,
            report=completed.report,
            tasks=(completed,),
        )

    @staticmethod
    def _build_report_import_entry(title: str, markdown: str) -> list[Any]:
        return [None, [title, markdown], None, 3, None, None, None, None, None, None, 3]

    @staticmethod
    def _build_web_import_entry(url: str, title: str) -> list[Any]:
        return [None, None, [url, title], None, None, None, None, None, None, None, 2]

    @classmethod
    def _parse_live_research_tasks(cls, result: Any) -> list[ResearchTask]:
        if not isinstance(result, list) or not result:
            return []
        first = result[0]
        rows = (
            first
            if isinstance(first, list) and first and isinstance(first[0], list)
            else result
        )
        tasks: list[ResearchTask] = []
        for task_data in rows:
            if not isinstance(task_data, list) or len(task_data) < 2:
                continue
            task_id = task_data[0] if isinstance(task_data[0], str) else None
            task_info = task_data[1] if isinstance(task_data[1], list) else None
            if task_id is None or task_info is None:
                continue
            query_info = task_info[1] if len(task_info) > 1 else None
            query = query_info[0] if isinstance(query_info, list) and query_info else ""
            bundle = task_info[3] if len(task_info) > 3 else None
            source_rows = bundle[0] if isinstance(bundle, list) and bundle else []
            summary = (
                bundle[1]
                if isinstance(bundle, list)
                and len(bundle) > 1
                and isinstance(bundle[1], str)
                else ""
            )
            status_code = task_info[4] if len(task_info) > 4 else None
            status = cls._research_status_from_code(status_code)
            sources, report = cls._parse_live_research_sources(source_rows, task_id)
            tasks.append(
                ResearchTask(
                    task_id=task_id,
                    status=status,
                    query=query if isinstance(query, str) else "",
                    sources=tuple(sources),
                    summary=summary,
                    report=report,
                )
            )
        return tasks

    @staticmethod
    def _research_status_from_code(status_code: Any) -> ResearchStatus:
        if status_code in (2, 6):
            return ResearchStatus.COMPLETED
        if status_code == 1 or status_code is None:
            return ResearchStatus.IN_PROGRESS
        return ResearchStatus.FAILED

    @classmethod
    def _parse_live_research_sources(
        cls, source_rows: Any, task_id: str
    ) -> tuple[list[ResearchSource], str]:
        if not isinstance(source_rows, list):
            return [], ""
        sources: list[ResearchSource] = []
        report = ""
        for row in source_rows:
            parsed, source_report = cls._parse_live_research_source(row, task_id)
            if parsed is not None:
                sources.append(parsed)
            if not report and source_report:
                report = source_report
        return sources, report

    @staticmethod
    def _parse_live_research_source(
        row: Any, task_id: str
    ) -> tuple[ResearchSource | None, str]:
        if not isinstance(row, list) or len(row) < 2:
            return None, ""
        result_type = row[3] if len(row) > 3 and isinstance(row[3], (int, str)) else 1
        url = ""
        title = ""
        report = ""
        if row[0] is None:
            payload = row[1]
            if (
                isinstance(payload, list)
                and len(payload) >= 2
                and isinstance(payload[0], str)
                and isinstance(payload[1], str)
            ):
                title = payload[0]
                report = payload[1]
                result_type = 5 if result_type == 1 else result_type
            elif isinstance(payload, str):
                title = payload
                result_type = 5 if result_type == 1 else result_type
        else:
            url = row[0] if isinstance(row[0], str) else ""
            title = row[1] if len(row) > 1 and isinstance(row[1], str) else ""
        if not title and not url:
            return None, ""
        return (
            ResearchSource(
                url=url,
                title=title,
                result_type=result_type,
                research_task_id=task_id,
                report_markdown=report,
            ),
            report,
        )

    async def import_sources(
        self,
        notebook_id: str,
        task_id: str,
        sources: Sequence[Any],
    ) -> list[dict[str, str]]:
        if not sources:
            return []
        owner_notebook = self._started_task_notebooks.get(task_id)
        if owner_notebook is not None and owner_notebook != notebook_id:
            raise ValidationError("research task id does not belong to notebook")
        source_inputs = list(sources)
        source_models = [self._coerce_source(source) for source in source_inputs]
        for source in source_models:
            source_task_id = source.research_task_id
            if source_task_id and source_task_id != task_id:
                raise ResearchTaskMismatchError(
                    task_id=task_id,
                    source_research_task_id=source_task_id,
                )
        research_task_ids = {
            source.research_task_id
            for source in source_models
            if source.research_task_id
        }
        if len(research_task_ids) > 1:
            raise ValidationError(
                "Cannot import sources from multiple research tasks in one batch."
            )
        report_source_indexes = {
            index
            for index, (source_input, source) in enumerate(
                zip(source_inputs, source_models, strict=True)
            )
            if self._is_importable_report_source(source_input, source)
        }

        if self._live_rpc is not None:
            effective_task_id = next(iter(research_task_ids), task_id)
            source_array = [
                self._build_report_import_entry(source.title, source.report_markdown)
                for index, source in enumerate(source_models)
                if index in report_source_indexes
            ]
            source_array.extend(
                self._build_web_import_entry(source.url, source.title)
                for index, source in enumerate(source_models)
                if source.url and index not in report_source_indexes
            )
            if not source_array:
                return []
            result = await self._live_rpc.rpc_call(
                RPCMethod.IMPORT_RESEARCH,
                [None, [1], effective_task_id, notebook_id, source_array],
                source_path=f"/notebook/{notebook_id}",
            )
            if (
                isinstance(result, list)
                and result
                and isinstance(result[0], list)
                and result[0]
                and isinstance(result[0][0], list)
            ):
                result = result[0]
            imported: list[dict[str, str]] = []
            if isinstance(result, list):
                for src_data in result:
                    if isinstance(src_data, list) and len(src_data) >= 2:
                        src_id = (
                            src_data[0][0]
                            if isinstance(src_data[0], list) and src_data[0]
                            else None
                        )
                        if src_id:
                            imported.append({"id": src_id, "title": src_data[1]})
            return imported

        report_sources = [
            source
            for index, source in enumerate(source_models)
            if index in report_source_indexes
        ]
        valid_sources = [
            source
            for index, source in enumerate(source_models)
            if source.url and index not in report_source_indexes
        ]
        imported: list[dict[str, str]] = []
        ordered_sources = [(True, source) for source in report_sources] + [
            (False, source) for source in valid_sources
        ]
        for is_report, source in ordered_sources:
            if is_report:
                created = self._source_service.add_text(
                    notebook_id,
                    source.title,
                    source.report_markdown,
                )
            else:
                created = self._source_service.add_url(notebook_id, source.url)
                if source.title:
                    created.title = source.title
            imported.append(
                {"id": created.id, "title": created.title or created.url or ""}
            )
        return imported

    async def import_sources_with_verification(
        self,
        notebook_id: str,
        task_id: str,
        sources: Sequence[Any],
        *,
        max_elapsed: float = 1800,
        initial_delay: float = 5,
        backoff_factor: float = 2,
        max_delay: float = 60,
    ) -> list[dict[str, str]]:
        if not sources:
            return []
        source_inputs = list(sources)
        source_models = [self._coerce_source(source) for source in source_inputs]
        started_at = time.monotonic()
        delay = initial_delay
        attempt = 1
        verified_imported: list[dict[str, str]] = []
        verified_imported_ids: set[str] = set()
        requested_urls_norm = _requested_import_verification_urls(source_models)
        requested_no_url_count = _no_import_verification_url_entry_count(source_models)
        try:
            baseline = await self._list_sources_for_verification(notebook_id)
            baseline_ids: set[str] | None = {source.id for source in baseline}
        except (NetworkError, PublicNetworkError, RPCError, ValidationError):
            baseline_ids = None

        while True:
            try:
                imported = await self.import_sources(notebook_id, task_id, source_inputs)
                return _merge_imported_sources(
                    imported, verified_imported, verified_imported_ids
                )
            except RPCTimeoutError:
                elapsed = time.monotonic() - started_at
                remaining = max_elapsed - elapsed

                if requested_urls_norm:
                    try:
                        current = await self._list_sources_for_verification(notebook_id)
                        new_sources = (
                            [source for source in current if source.id not in baseline_ids]
                            if baseline_ids is not None
                            else []
                        )
                        new_urls_norm = {
                            _normalize_import_verification_url(source.url)
                            for source in new_sources
                            if source.url
                        }
                        current_urls_norm = {
                            _normalize_import_verification_url(source.url)
                            for source in current
                            if source.url
                        }
                        committed_urls_norm = requested_urls_norm & new_urls_norm
                        if baseline_ids is not None and requested_urls_norm.issubset(
                            new_urls_norm
                        ):
                            timeout_verified: list[dict[str, str]] = []
                            remaining_no_url = requested_no_url_count
                            for source in new_sources:
                                if (
                                    source.url
                                    and _normalize_import_verification_url(source.url)
                                    in requested_urls_norm
                                ):
                                    timeout_verified.append(
                                        _imported_source_entry(source)
                                    )
                                elif not source.url and remaining_no_url > 0:
                                    timeout_verified.append(
                                        _imported_source_entry(source)
                                    )
                                    remaining_no_url -= 1
                            return _merge_imported_sources(
                                timeout_verified,
                                verified_imported,
                                verified_imported_ids,
                            )

                        source_norms = [
                            (
                                source_input,
                                source,
                                _source_import_verification_url(source),
                            )
                            for source_input, source in zip(
                                source_inputs, source_models, strict=True
                            )
                        ]
                        drop_no_url_entries = bool(committed_urls_norm)
                        filtered_source_pairs = [
                            (source_input, source)
                            for source_input, source, url in source_norms
                            if url not in current_urls_norm
                            and not (drop_no_url_entries and url is None)
                        ]
                        if len(filtered_source_pairs) != len(source_models):
                            for source in new_sources:
                                if (
                                    source.url
                                    and _normalize_import_verification_url(source.url)
                                    in committed_urls_norm
                                    and source.id not in verified_imported_ids
                                ):
                                    verified_imported.append(
                                        _imported_source_entry(source)
                                    )
                                    verified_imported_ids.add(source.id)
                            source_inputs = [
                                source_input
                                for source_input, _ in filtered_source_pairs
                            ]
                            source_models = [
                                source for _, source in filtered_source_pairs
                            ]
                            requested_urls_norm = _requested_import_verification_urls(
                                source_models
                            )
                            requested_no_url_count = (
                                _no_import_verification_url_entry_count(source_models)
                            )
                            if not source_models:
                                return _merge_imported_sources(
                                    [], verified_imported, verified_imported_ids
                                )
                    except (NetworkError, PublicNetworkError, RPCError, ValidationError):
                        pass

                if remaining <= 0:
                    raise
                if not requested_urls_norm and attempt >= 2:
                    raise
                sleep_for = min(delay, max_delay, remaining)
                if sleep_for > 0:
                    await asyncio.sleep(sleep_for)
                delay = min(delay * backoff_factor, max_delay)
                attempt += 1

    async def poll(self, notebook_id: str, task_id: str | None = None) -> ResearchTask:
        if self._live_rpc is not None:
            result = await self._live_rpc.rpc_call(
                RPCMethod.POLL_RESEARCH,
                [None, None, notebook_id],
                source_path=f"/notebook/{notebook_id}",
            )
            tasks = self._parse_live_research_tasks(result)
            if task_id is None and len(tasks) > 1:
                self._warn_ambiguous_poll(notebook_id, len(tasks))
            selected = (
                [task for task in tasks if task.task_id == task_id]
                if task_id
                else tasks
            )
            if selected:
                return ResearchTask(
                    task_id=selected[0].task_id,
                    status=selected[0].status,
                    query=selected[0].query,
                    sources=selected[0].sources,
                    summary=selected[0].summary,
                    report=selected[0].report,
                    tasks=tuple(selected),
                )
            return ResearchTask.not_found(task_id) if task_id else ResearchTask.empty()
        if task_id and task_id in self._started_tasks:
            if self._started_task_notebooks.get(task_id) != notebook_id:
                return ResearchTask.not_found(task_id)
            return self._public_poll_result(self._started_tasks[task_id])
        task = self._status_fixtures.poll_research(notebook_id, task_id)
        if task_id is None and len(task.tasks) > 1:
            self._warn_ambiguous_poll(notebook_id, len(task.tasks))
        return task

    async def start(
        self,
        notebook_id: str,
        query: str,
        source: str = "web",
        mode: str = "fast",
    ) -> ResearchStart | None:
        if not isinstance(source, str):
            raise ValidationError("source must be text")
        if not isinstance(mode, str):
            raise ValidationError("mode must be text")
        source_lower = source.lower()
        mode_lower = mode.lower()
        if source_lower not in ("web", "drive"):
            raise ValidationError(f"Invalid source '{source}'. Use 'web' or 'drive'.")
        if mode_lower not in ("fast", "deep"):
            raise ValidationError(f"Invalid mode '{mode}'. Use 'fast' or 'deep'.")
        if mode_lower == "deep" and source_lower == "drive":
            raise ValidationError("Deep Research only supports Web sources.")
        if self._live_rpc is not None:
            source_type = 1 if source_lower == "web" else 2
            if mode_lower == "fast":
                method = RPCMethod.START_FAST_RESEARCH
                params = [[query, source_type], None, 1, notebook_id]
            else:
                method = RPCMethod.START_DEEP_RESEARCH
                params = [None, [1], [query, source_type], 5, notebook_id]
            result = await self._live_rpc.rpc_call(
                method,
                params,
                source_path=f"/notebook/{notebook_id}",
            )
            if isinstance(result, list) and result:
                if not result[0] and _future_errors_enabled():
                    raise DecodingError(
                        f"research.start returned no task id: {result!r}",
                        method_id=method.value,
                    )
                return ResearchStart(
                    task_id=result[0],
                    report_id=result[1] if len(result) > 1 else None,
                    notebook_id=notebook_id,
                    query=query,
                    mode=mode_lower,
                )
            if _future_errors_enabled():
                raise DecodingError(
                    "research.start returned an empty / non-list payload",
                    method_id=method.value,
                )
            return None
        self._started_count += 1
        task_id = f"offline-research-{self._started_count:04d}"
        report_id = (
            f"offline-report-{self._started_count:04d}"
            if mode_lower == "deep"
            else None
        )
        task = ResearchTask(
            task_id=task_id,
            status=ResearchStatus.IN_PROGRESS,
            query=query,
        )
        self._started_tasks[task_id] = task
        self._started_task_notebooks[task_id] = notebook_id
        if report_id is not None:
            self._started_tasks[report_id] = ResearchTask(
                task_id=report_id,
                status=ResearchStatus.IN_PROGRESS,
                query=query,
            )
            self._started_task_notebooks[report_id] = notebook_id
        return ResearchStart(
            task_id=task_id,
            report_id=report_id,
            notebook_id=notebook_id,
            query=query,
            mode=mode_lower,
        )

    async def wait_for_completion(
        self,
        notebook_id: str,
        task_id: str | None = None,
        *,
        timeout: float = 1800,
        interval: float = 5,
        initial_interval: float = _DEFAULT_RESEARCH_INITIAL_INTERVAL,
    ) -> ResearchTask:
        legacy_interval: Any = (
            interval if interval != 5 else _DEFAULT_RESEARCH_INITIAL_INTERVAL
        )
        poll_interval = _deprecated_kwarg(
            legacy_interval,
            initial_interval,
            old="interval",
            new="initial_interval",
            owner="ResearchAPI.wait_for_completion",
            sentinel=_DEFAULT_RESEARCH_INITIAL_INTERVAL,
            stacklevel=3,
        )
        if poll_interval is _DEFAULT_RESEARCH_INITIAL_INTERVAL:
            poll_interval = 5
        if timeout < 0:
            raise ValueError("timeout must be non-negative")
        if not isinstance(poll_interval, (int, float)) or isinstance(
            poll_interval, bool
        ):
            raise TypeError("poll interval must be a number")
        if poll_interval <= 0:
            raise ValueError("poll interval must be positive")
        if self._live_rpc is not None:
            loop = asyncio.get_running_loop()
            start_time = loop.time()
            pinned_task_id = task_id
            while True:
                task = await self.poll(notebook_id, pinned_task_id)
                if pinned_task_id is None and task.task_id:
                    pinned_task_id = task.task_id
                if task.status in (ResearchStatus.COMPLETED, ResearchStatus.FAILED):
                    return task
                if task.status is ResearchStatus.NO_RESEARCH and pinned_task_id is None:
                    return task
                elapsed = loop.time() - start_time
                if elapsed >= timeout:
                    raise ResearchTimeoutError(
                        notebook_id,
                        pinned_task_id or "unknown",
                        timeout,
                        last_status=task.status.value,
                    )
                await asyncio.sleep(min(float(poll_interval), timeout - elapsed))
        if task_id and task_id in self._started_tasks:
            if self._started_task_notebooks.get(task_id) != notebook_id:
                return ResearchTask.not_found(task_id)
            completed = self._completed_started_task(self._started_tasks[task_id])
            self._started_tasks[task_id] = ResearchTask(
                task_id=completed.task_id,
                status=completed.status,
                query=completed.query,
                sources=completed.sources,
                summary=completed.summary,
                report=completed.report,
            )
            return completed
        return self._status_fixtures.wait_for_research(notebook_id, task_id)


class SettingsAPI:
    """Phase-gated settings sub-client shell for public API parity."""

    def __init__(
        self,
        rpc: Any = None,
        status_fixtures: OfflineReadOnlyStatusFixtures | None = None,
        live_rpc: Any = None,
    ) -> None:
        self._rpc = rpc
        self._live_rpc = live_rpc
        self._status_fixtures = (
            status_fixtures or OfflineReadOnlyStatusFixtures.load_default()
        )
        self._output_language = self._status_fixtures.get_output_language()

    async def get_account_limits(self) -> AccountLimits:
        if self._live_rpc is not None:
            return self._extract_account_limits(
                await self._live_rpc.rpc_call(
                    RPCMethod.GET_USER_SETTINGS,
                    self._get_user_settings_params(),
                    source_path="/",
                )
            )
        return self._status_fixtures.get_account_limits()

    async def get_account_tier(self) -> AccountTier:
        if self._live_rpc is not None:
            return self._extract_account_tier(
                await self._live_rpc.rpc_call(
                    RPCMethod.GET_USER_TIER,
                    [
                        [
                            [
                                [None, "1", 627],
                                [
                                    None,
                                    None,
                                    None,
                                    None,
                                    None,
                                    None,
                                    None,
                                    None,
                                    None,
                                    [None, None, 2],
                                ],
                                1,
                            ]
                        ]
                    ],
                    source_path="/",
                )
            )
        return self._status_fixtures.get_account_tier()

    async def get_output_language(self) -> str | None:
        if self._live_rpc is not None:
            return self._extract_language(
                await self._live_rpc.rpc_call(
                    RPCMethod.GET_USER_SETTINGS,
                    self._get_user_settings_params(),
                    source_path="/",
                ),
                (0, 2),
                (4, 0),
            )
        return self._output_language

    async def set_output_language(self, language: str) -> str | None:
        if not language:
            return None
        if self._live_rpc is not None:
            return self._extract_language(
                await self._live_rpc.rpc_call(
                    RPCMethod.SET_USER_SETTINGS,
                    [[[None, [[None, None, None, None, [language]]]]]],
                    source_path="/",
                ),
                (2,),
                (4, 0),
            )
        if language_name(language) is None:
            raise ValidationError("unsupported output language")
        self._output_language = language
        return language

    @staticmethod
    def _get_user_settings_params() -> list[Any]:
        return [None, [1, None, None, None, None, None, None, None, None, None, [1]]]

    @staticmethod
    def _extract_language(
        data: Any, required_prefix: tuple[int, ...], optional_tail: tuple[int, ...]
    ) -> str | None:
        current = data
        try:
            for idx in required_prefix:
                current = current[idx]
        except (IndexError, TypeError):
            raise UnknownRPCMethodError("unexpected user settings payload") from None
        for idx in optional_tail:
            if not isinstance(current, list) or not 0 <= idx < len(current):
                return None
            current = current[idx]
        return current or None

    @staticmethod
    def _extract_account_limits(data: Any) -> AccountLimits:
        limits = None
        try:
            candidate = data[0][1]
        except (IndexError, TypeError):
            candidate = None
        if isinstance(candidate, list):
            limits = candidate
        if limits is None:
            return AccountLimits()
        notebook_limit = (
            limits[1] if len(limits) > 1 and isinstance(limits[1], int) and limits[1] > 0 else None
        )
        source_limit = (
            limits[2] if len(limits) > 2 and isinstance(limits[2], int) and limits[2] > 0 else None
        )
        return AccountLimits(
            notebook_limit=notebook_limit,
            source_limit=source_limit,
            raw_limits=tuple(limits),
        )

    @classmethod
    def _extract_account_tier(cls, data: Any) -> AccountTier:
        tier = cls._find_tier(data)
        names = {
            "NOTEBOOKLM_TIER_STANDARD": "Standard",
            "NOTEBOOKLM_TIER_PLUS": "Google AI Plus",
            "NOTEBOOKLM_TIER_PRO": "Google AI Pro",
            "NOTEBOOKLM_TIER_PRO_DASHER_END_USER": "Google Workspace Pro",
            "NOTEBOOKLM_TIER_ULTRA": "Google AI Ultra",
        }
        return AccountTier(tier=tier, plan_name=names.get(tier) if tier else None)

    @classmethod
    def _find_tier(cls, value: Any) -> str | None:
        if isinstance(value, str) and value.startswith("NOTEBOOKLM_TIER_"):
            return value
        if isinstance(value, list):
            for item in value:
                found = cls._find_tier(item)
                if found:
                    return found
        return None


class SharingAPI:
    """Fixture-backed sharing sub-client over in-memory synthetic status."""

    def __init__(
        self,
        rpc: Any = None,
        status_fixtures: OfflineReadOnlyStatusFixtures | None = None,
        live_rpc: _StdlibLiveRpcClient | None = None,
    ) -> None:
        self._rpc = rpc
        self._live_rpc = live_rpc
        self._status_fixtures = (
            status_fixtures or OfflineReadOnlyStatusFixtures.load_default()
        )
        self._statuses: dict[str, ShareStatus] = {}

    def _clone_status(self, status: ShareStatus) -> ShareStatus:
        return ShareStatus(
            notebook_id=status.notebook_id,
            is_public=status.is_public,
            access=status.access,
            view_level=status.view_level,
            shared_users=[
                SharedUser(
                    email=user.email,
                    permission=user.permission,
                    display_name=user.display_name,
                    avatar_url=user.avatar_url,
                )
                for user in status.shared_users
            ],
            share_url=status.share_url,
        )

    def _status(self, notebook_id: str) -> ShareStatus:
        if not isinstance(notebook_id, str) or notebook_id == "":
            raise ValidationError("notebook id must be non-empty text")
        if notebook_id not in self._statuses:
            self._statuses[notebook_id] = self._clone_status(
                self._status_fixtures.get_share_status(notebook_id)
            )
        return self._statuses[notebook_id]

    def _public_share_url(self, notebook_id: str) -> str:
        return f"https://notebooklm.google.com/notebook/{quote(notebook_id, safe='')}"

    def _status_from_api_response(self, data: Any, notebook_id: str) -> ShareStatus:
        users: list[SharedUser] = []
        if isinstance(data, list) and data and isinstance(data[0], list):
            for user_data in data[0]:
                if isinstance(user_data, list):
                    users.append(self._user_from_api_response(user_data))

        public_block = data[1] if isinstance(data, list) and len(data) > 1 else None
        is_public = bool(public_block[0]) if isinstance(public_block, list) and public_block else False
        access = ShareAccess.ANYONE_WITH_LINK if is_public else ShareAccess.RESTRICTED
        return ShareStatus(
            notebook_id=notebook_id,
            is_public=is_public,
            access=access,
            view_level=ShareViewLevel.FULL_NOTEBOOK,
            shared_users=users,
            share_url=self._public_share_url(notebook_id) if is_public else None,
        )

    @staticmethod
    def _user_from_api_response(data: list[Any]) -> SharedUser:
        email = data[0] if data else ""
        try:
            permission = SharePermission(data[1] if len(data) > 1 else 3)
        except (TypeError, ValueError):
            permission = SharePermission.VIEWER
        user_info = data[3] if len(data) > 3 and isinstance(data[3], list) else []
        return SharedUser(
            email=email,
            permission=permission,
            display_name=user_info[0] if user_info else None,
            avatar_url=user_info[1] if len(user_info) > 1 else None,
        )

    def _coerce_permission(self, permission: SharePermission | int) -> SharePermission:
        try:
            coerced = (
                permission
                if isinstance(permission, SharePermission)
                else SharePermission(permission)
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("unknown share permission") from exc
        if coerced is SharePermission.OWNER:
            raise ValueError("Cannot assign OWNER permission")
        if coerced is SharePermission._REMOVE:
            raise ValueError("Use remove_user() instead")
        return coerced

    async def add_user(
        self,
        notebook_id: str,
        email: str,
        permission: SharePermission = SharePermission.VIEWER,
        notify: bool = True,
        welcome_message: str = "",
    ) -> ShareStatus:
        if self._live_rpc is not None:
            if permission == SharePermission.OWNER:
                raise ValueError("Cannot assign OWNER permission")
            if permission == SharePermission._REMOVE:
                raise ValueError("Use remove_user() instead")
            message_flag = 0 if welcome_message else 1
            notify_flag = 1 if notify else 0
            await self._live_rpc.rpc_call(
                RPCMethod.SHARE_NOTEBOOK,
                [
                    [
                        [
                            notebook_id,
                            [[email, None, permission.value]],
                            None,
                            [message_flag, welcome_message],
                        ]
                    ],
                    notify_flag,
                    None,
                    [2],
                ],
                source_path=f"/notebook/{notebook_id}",
                allow_null=True,
            )
            return await self.get_status(notebook_id)
        if not isinstance(email, str) or email == "":
            raise ValidationError("email must be non-empty text")
        permission = self._coerce_permission(permission)
        status = self._status(notebook_id)
        status.shared_users = [
            user for user in status.shared_users if user.email != email
        ]
        status.shared_users.append(SharedUser(email=email, permission=permission))
        return self._clone_status(status)

    async def get_status(self, notebook_id: str) -> ShareStatus:
        if self._live_rpc is not None:
            result = await self._live_rpc.rpc_call(
                RPCMethod.GET_SHARE_STATUS,
                [notebook_id, [2]],
                source_path=f"/notebook/{notebook_id}",
            )
            return self._status_from_api_response(result, notebook_id)
        return self._clone_status(self._status(notebook_id))

    async def remove_user(self, notebook_id: str, email: str) -> ShareStatus:
        if self._live_rpc is not None:
            await self._live_rpc.rpc_call(
                RPCMethod.SHARE_NOTEBOOK,
                [
                    [[notebook_id, [[email, None, SharePermission._REMOVE.value]], None, [0, ""]]],
                    0,
                    None,
                    [2],
                ],
                source_path=f"/notebook/{notebook_id}",
                allow_null=True,
            )
            return await self.get_status(notebook_id)
        if not isinstance(email, str) or email == "":
            raise ValidationError("email must be non-empty text")
        status = self._status(notebook_id)
        status.shared_users = [
            user for user in status.shared_users if user.email != email
        ]
        return self._clone_status(status)

    async def set_public(self, notebook_id: str, public: bool) -> ShareStatus:
        if self._live_rpc is not None:
            access = ShareAccess.ANYONE_WITH_LINK if public else ShareAccess.RESTRICTED
            await self._live_rpc.rpc_call(
                RPCMethod.SHARE_NOTEBOOK,
                [
                    [[notebook_id, None, [access.value], [access.value, ""]]],
                    1,
                    None,
                    [2],
                ],
                source_path=f"/notebook/{notebook_id}",
                allow_null=True,
            )
            return await self.get_status(notebook_id)
        if not isinstance(public, bool):
            raise ValidationError("public must be bool")
        status = self._status(notebook_id)
        status.is_public = public
        status.access = (
            ShareAccess.ANYONE_WITH_LINK if public else ShareAccess.RESTRICTED
        )
        status.share_url = self._public_share_url(notebook_id) if public else None
        return self._clone_status(status)

    async def set_view_level(
        self, notebook_id: str, level: ShareViewLevel
    ) -> ShareStatus:
        if self._live_rpc is not None:
            await self._live_rpc.rpc_call(
                RPCMethod.RENAME_NOTEBOOK,
                [
                    notebook_id,
                    [[None, None, None, None, None, None, None, None, [[level.value]]]],
                ],
                source_path=f"/notebook/{notebook_id}",
                allow_null=True,
            )
            status = await self.get_status(notebook_id)
            return ShareStatus(
                notebook_id=status.notebook_id,
                is_public=status.is_public,
                access=status.access,
                view_level=level,
                shared_users=status.shared_users,
                share_url=status.share_url,
            )
        try:
            view_level = (
                level if isinstance(level, ShareViewLevel) else ShareViewLevel(level)
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("unknown share view level") from exc
        status = self._status(notebook_id)
        status.view_level = view_level
        return self._clone_status(status)

    async def update_user(
        self, notebook_id: str, email: str, permission: SharePermission
    ) -> ShareStatus:
        return await self.add_user(notebook_id, email, permission, notify=False)


class _StdlibLiveRpcClient:
    """Small stdlib batchexecute caller for authenticated live read RPCs."""

    def __init__(
        self,
        auth: AuthTokens,
        *,
        timeout: float,
        rate_limit_max_retries: int = 3,
        server_error_max_retries: int = 3,
        get: ResponseGetter | None = None,
        post: ResponsePoster | None = None,
    ) -> None:
        self._auth = auth
        self._timeout = timeout
        self._rate_limit_max_retries = rate_limit_max_retries
        self._server_error_max_retries = server_error_max_retries
        self._get = get or _http_std.get
        self._post = post or _http_std.post

    def set_transport(
        self,
        *,
        get: ResponseGetter | None = None,
        post: ResponsePoster | None = None,
    ) -> None:
        if get is not None:
            self._get = get
        if post is not None:
            self._post = post

    async def rpc_call(
        self,
        method: RPCMethod,
        params: list[Any],
        source_path: str = "/",
        allow_null: bool = False,
        *,
        disable_internal_retries: bool = False,
        **_kwargs: Any,
    ) -> Any:
        return await asyncio.to_thread(
            self._rpc_call_sync,
            method,
            params,
            source_path,
            allow_null,
            disable_internal_retries,
        )

    def _rpc_call_sync(
        self,
        method: RPCMethod,
        params: list[Any],
        source_path: str,
        allow_null: bool,
        disable_internal_retries: bool,
    ) -> Any:
        if not self._auth.csrf_token or not self._auth.session_id:
            raise AuthenticationError("NotebookLMClient live RPC requires auth tokens")
        if not self._auth.cookies:
            raise AuthenticationError("NotebookLMClient live RPC requires cookies")

        resolved_id = resolve_rpc_id(method.name, method.value)
        rpc_request = encode_rpc_request(method, params, rpc_id_override=resolved_id)
        url = self._build_url(resolved_id, source_path)
        body = build_request_body(rpc_request, self._auth.csrf_token)
        headers = {
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Cookie": self._cookie_header_for_url(url),
        }
        rate_limit_retries = 0
        server_error_retries = 0
        started = time.monotonic()
        while True:
            try:
                response = self._post(
                    url,
                    body=body,
                    headers=headers,
                    timeout=self._timeout,
                    max_redirects=5,
                    max_body_bytes=_MAX_RPC_RESPONSE_BYTES,
                )
            except BodyTooLargeError as exc:
                raise RPCResponseTooLargeError(
                    str(exc),
                    method_id=method.value,
                    limit_bytes=_MAX_RPC_RESPONSE_BYTES,
                ) from exc
            except _TRANSPORT_NETWORK_ERRORS as exc:
                if (
                    disable_internal_retries
                    or server_error_retries >= self._server_error_max_retries
                    or not self._sleep_before_retry(
                        self._backoff_delay(server_error_retries), started
                    )
                ):
                    self._raise_network_error(method, exc)
                server_error_retries += 1
                continue

            retry_after = _parse_retry_after(_response_header(response, "retry-after"))
            if response.status == 429:
                if (
                    disable_internal_retries
                    or rate_limit_retries >= self._rate_limit_max_retries
                    or not self._sleep_before_retry(
                        float(retry_after)
                        if retry_after is not None
                        else self._backoff_delay(rate_limit_retries),
                        started,
                    )
                ):
                    self._raise_rate_limit(method, retry_after)
                rate_limit_retries += 1
                continue
            if 500 <= response.status < 600:
                if (
                    disable_internal_retries
                    or server_error_retries >= self._server_error_max_retries
                    or not self._sleep_before_retry(
                        self._backoff_delay(server_error_retries), started
                    )
                ):
                    self._raise_server_error(method, response.status)
                server_error_retries += 1
                continue
            self._raise_for_status(response.status, method)
            return decode_response(response.text(), resolved_id, allow_null=allow_null)

    def _build_url(self, rpc_id: str, source_path: str) -> str:
        params: dict[str, str] = {
            "rpcids": rpc_id,
            "source-path": source_path,
            "f.sid": self._auth.session_id,
            "hl": get_default_language(),
            "rt": "c",
        }
        if self._auth.account_email or self._auth.authuser:
            params["authuser"] = _auth_module.format_authuser_value(
                self._auth.authuser,
                self._auth.account_email,
            )
        return f"{get_batchexecute_url()}?{urlencode(params)}"

    def _cookie_header_for_url(self, url: str) -> str:
        state = _auth_module._cookies.build_storage_state(
            {
                "name": name,
                "value": value,
                "domain": domain,
                "path": path,
                "secure": True,
            }
            for (name, domain, path), value in _auth_module.normalize_cookie_map(
                self._auth.cookies
            ).items()
        )
        return _auth_module._cookie_header(
            _auth_module._cookies_for_url(state["cookies"], url)
        )

    @staticmethod
    def _raise_for_status(status: int, method: RPCMethod) -> None:
        if status == 429:
            _StdlibLiveRpcClient._raise_rate_limit(method, None)
        if status in {401, 403}:
            raise AuthenticationError(f"HTTP {status} calling {method.name}")
        if 500 <= status < 600:
            _StdlibLiveRpcClient._raise_server_error(method, status)
        if 400 <= status < 500:
            raise ValidationError(f"Client error {status} calling {method.name}")

    @staticmethod
    def _raise_rate_limit(method: RPCMethod, retry_after: int | None) -> None:
        msg = f"API rate limit exceeded calling {method.name}"
        if retry_after:
            msg += f". Retry after {retry_after} seconds"
        raise RateLimitError(
            msg,
            method_id=method.value,
            retry_after=retry_after,
        )

    @staticmethod
    def _raise_server_error(method: RPCMethod, status: int) -> None:
        raise ServerError(
            f"Server error {status} calling {method.name}",
            method_id=method.value,
            status_code=status,
        )

    @staticmethod
    def _raise_network_error(method: RPCMethod, exc: BaseException) -> None:
        raise PublicNetworkError(
            f"Request failed calling {method.name}: {exc}",
            method_id=method.value,
            original_error=exc if isinstance(exc, Exception) else None,
        ) from exc

    def _sleep_before_retry(self, delay: float, started: float) -> bool:
        timeout = self._timeout
        if timeout is not None and math.isfinite(float(timeout)):
            remaining = float(timeout) - (time.monotonic() - started)
            if remaining <= 0.0 or delay >= remaining:
                return False
            delay = min(delay, remaining)
        if delay > 0.0:
            time.sleep(delay)
        return True

    @staticmethod
    def _backoff_delay(attempt: int) -> float:
        return min(_BACKOFF_CAP_SECONDS, _BACKOFF_MIN_SECONDS * (2**attempt))


def _build_template_block() -> list[Any]:
    return [2, None, None, [1, None, None, None, None, None, None, None, None, None, [1]]]


def _extract_summary(outer: Any) -> str:
    if outer is None:
        return ""
    if isinstance(outer, list) and (not outer or outer[0] is None):
        return ""
    if not isinstance(outer, list) or not isinstance(outer[0], list):
        raise UnknownRPCMethodError("unexpected SUMMARIZE summary payload")
    summary = outer[0][0] if outer[0] else None
    return "" if summary is None else str(summary)


def _extract_suggested_topics(outer: Any) -> list[SuggestedTopic]:
    if not isinstance(outer, list) or len(outer) < 2:
        return []
    container = outer[1]
    if not isinstance(container, list) or not container:
        return []
    rows = container[0]
    if not isinstance(rows, list):
        return []
    topics: list[SuggestedTopic] = []
    for row in rows:
        if isinstance(row, list) and len(row) >= 2:
            topics.append(
                SuggestedTopic(
                    question=str(row[0]) if row[0] else "",
                    prompt=str(row[1]) if row[1] else "",
                )
            )
    return topics


class NotebooksAPI:
    """Read-only offline notebooks sub-client over the synthetic fixture seam."""

    def __init__(
        self,
        rpc: Any = None,
        sources_api: Any = None,
        *,
        metadata_service: OfflineNotebookMetadataService | None = None,
        live_rpc: _StdlibLiveRpcClient | None = None,
        share_manager: Any = None,
    ) -> None:
        self._metadata_service = metadata_service or _service_from_fixtures()
        self._rpc = rpc
        self._live_rpc = live_rpc
        self._sources_api = sources_api
        self._share_manager = share_manager

    async def list(self) -> list[Notebook]:
        if self._live_rpc is not None:
            result = await self._live_rpc.rpc_call(
                RPCMethod.LIST_NOTEBOOKS,
                [None, 1, None, [2]],
            )
            if result and isinstance(result, list) and len(result) > 0:
                raw_notebooks = result[0] if isinstance(result[0], list) else result
                return [Notebook.from_api_response(nb) for nb in raw_notebooks]
            return []
        return self._metadata_service.list()

    async def get(self, notebook_id: str) -> Notebook:
        if self._live_rpc is not None:
            result = await self._live_rpc.rpc_call(
                RPCMethod.GET_NOTEBOOK,
                [notebook_id, None, [2], None, 0],
                source_path=f"/notebook/{notebook_id}",
            )
            nb_info = (
                result[0]
                if result and isinstance(result, list) and len(result) > 0
                else []
            )
            if not nb_info:
                raise NotebookNotFoundError(notebook_id)
            notebook = Notebook.from_api_response(nb_info)
            if not notebook.id and not notebook.title:
                raise NotebookNotFoundError(notebook_id)
            return notebook
        return self._metadata_service.resolve(notebook_id)

    async def get_or_none(self, notebook_id: str) -> Notebook | None:
        try:
            return await self.get(notebook_id)
        except (NotebookNotFoundError, ValidationError):
            return None

    async def get_metadata(self, notebook_id: str) -> NotebookMetadata:
        if self._live_rpc is not None and self._sources_api is not None:
            notebook, sources = await asyncio.gather(
                self.get(notebook_id),
                self._sources_api.list(notebook_id),
            )
            return NotebookMetadata(
                notebook=notebook,
                sources=[
                    SourceSummary(
                        kind=source.kind,
                        title=source.title,
                        url=source.url,
                    )
                    for source in sources
                ],
            )
        notebook = self._metadata_service.resolve(notebook_id)
        sources: list[Any] = []
        if self._sources_api is not None:
            sources = [
                source.summary() for source in await self._sources_api.list(notebook.id)
            ]
        return NotebookMetadata(notebook=notebook, sources=sources)

    async def get_source_ids(self, notebook_id: str) -> list[str]:
        if self._live_rpc is not None and self._sources_api is not None:
            return [source.id for source in await self._sources_api.list(notebook_id)]
        return self._metadata_service.get_source_ids(notebook_id)

    async def get_raw(self, notebook_id: str) -> Any:
        if self._live_rpc is not None:
            return await self._live_rpc.rpc_call(
                RPCMethod.GET_NOTEBOOK,
                [notebook_id, None, [2], None, 0],
                source_path=f"/notebook/{notebook_id}",
            )
        return self._metadata_service.get_metadata(notebook_id).as_dict()

    async def get_description(self, notebook_id: str) -> NotebookDescription:
        if self._live_rpc is not None:
            result = await self._live_rpc.rpc_call(
                RPCMethod.SUMMARIZE,
                [notebook_id, [2]],
                source_path=f"/notebook/{notebook_id}",
            )
            outer = result[0] if isinstance(result, list) and result else None
            return NotebookDescription(
                summary=_extract_summary(outer),
                suggested_topics=_extract_suggested_topics(outer),
            )
        notebook = self._metadata_service.resolve(notebook_id)
        source_count = len(self._metadata_service.get_source_ids(notebook.id))
        summary = f"{notebook.title} contains {source_count} synthetic source{'s' if source_count != 1 else ''}."
        return NotebookDescription(
            summary=summary,
            suggested_topics=[
                SuggestedTopic(
                    question=f"What are the key points in {notebook.title}?",
                    prompt="Summarize the synthetic notebook's committed fixture sources.",
                )
            ],
        )

    async def get_summary(self, notebook_id: str) -> str:
        if self._live_rpc is not None:
            result = await self._live_rpc.rpc_call(
                RPCMethod.SUMMARIZE,
                [notebook_id, [2]],
                source_path=f"/notebook/{notebook_id}",
            )
            if not isinstance(result, list) or not result:
                return ""
            return _extract_summary(result[0])
        return (await self.get_description(notebook_id)).summary

    def get_share_url(self, notebook_id: str, artifact_id: str | None = None) -> str:
        notebook_url = (
            f"https://notebooklm.google.com/notebook/{quote(notebook_id, safe='')}"
        )
        if artifact_id:
            return f"{notebook_url}?artifactId={quote(artifact_id, safe='')}"
        return notebook_url

    async def create(self, title: str) -> Notebook:
        if self._live_rpc is not None:
            result = await self._live_rpc.rpc_call(
                RPCMethod.CREATE_NOTEBOOK,
                [title, None, None, _build_template_block()],
                disable_internal_retries=True,
            )
            return Notebook.from_api_response(result)
        return self._metadata_service.create(title)

    async def delete(self, notebook_id: str) -> None:
        if self._live_rpc is not None:
            await self._live_rpc.rpc_call(
                RPCMethod.DELETE_NOTEBOOK,
                [[notebook_id], [2]],
            )
            return None
        self._metadata_service.delete(notebook_id)

    async def remove_from_recent(self, notebook_id: str) -> None:
        if self._live_rpc is not None:
            await self._live_rpc.rpc_call(
                RPCMethod.REMOVE_RECENTLY_VIEWED,
                [notebook_id],
                allow_null=True,
            )
            return None
        self._metadata_service.remove_from_recent(notebook_id)

    async def rename(self, notebook_id: str, new_title: str) -> Notebook:
        if self._live_rpc is not None:
            await self._live_rpc.rpc_call(
                RPCMethod.RENAME_NOTEBOOK,
                [notebook_id, [[None, None, None, [None, new_title]]]],
                source_path="/",
                allow_null=True,
            )
            return await self.get(notebook_id)
        return self._metadata_service.rename(notebook_id, new_title)

    async def share(
        self,
        notebook_id: str,
        public: bool = True,
        artifact_id: str | None = None,
    ) -> dict[str, Any]:
        _warn_deprecated(
            "NotebooksAPI.share() is deprecated; use client.sharing.set_public() "
            "for the canonical notebook-level public-sharing toggle (paired with "
            "client.sharing.add_user(), set_view_level(), get_status()). Return "
            "shape is unchanged in this release; the wrapper will be removed in "
            "a future major release.",
            removal=None,
            stacklevel=3,
        )
        if self._live_rpc is not None:
            params: list[Any] = [[1] if public else [0], notebook_id]
            if artifact_id:
                params.append(artifact_id)
            await self._live_rpc.rpc_call(
                RPCMethod.SHARE_ARTIFACT,
                params,
                source_path=f"/notebook/{notebook_id}",
                allow_null=True,
            )
            return {
                "public": public,
                "url": self.get_share_url(notebook_id, artifact_id) if public else None,
                "artifact_id": artifact_id,
            }
        if self._share_manager is None:
            return {
                "public": public,
                "url": self.get_share_url(notebook_id, artifact_id) if public else None,
                "artifact_id": artifact_id,
            }
        await self._share_manager.set_public(notebook_id, public)
        return {
            "public": public,
            "url": self.get_share_url(notebook_id, artifact_id) if public else None,
            "artifact_id": artifact_id,
        }


class NotebookLMClient:
    """Offline fixture-backed client exposing local-only parity surfaces."""

    def __init__(
        self,
        auth: AuthTokens,
        timeout: float = 30.0,
        storage_path: Path | None = None,
        keepalive: float | None = None,
        keepalive_min_interval: float = 60.0,
        rate_limit_max_retries: int = 3,
        server_error_max_retries: int = 3,
        limits: ConnectionLimits | None = None,
        max_concurrent_uploads: int | None = 4,
        max_concurrent_rpcs: int | None = 16,
        upload_timeout: httpx.Timeout | None = None,
        on_rpc_event: Callable[[RpcTelemetryEvent], object] | None = None,
        cookie_saver: CookieSaver | None = None,
        cookie_rotator: CookieRotator | None = None,
        chat_timeout: float | None = 180.0,
    ):
        _refuse_synthetic_error_outside_test_context()
        if rate_limit_max_retries < 0:
            raise ValueError(
                f"rate_limit_max_retries must be >= 0, got {rate_limit_max_retries}"
            )
        if server_error_max_retries < 0:
            raise ValueError(
                f"server_error_max_retries must be >= 0, got {server_error_max_retries}"
            )
        if max_concurrent_rpcs is not None:
            if max_concurrent_rpcs < 1:
                raise ValueError(
                    f"max_concurrent_rpcs must be >= 1, got {max_concurrent_rpcs!r}"
                )
            effective_limits = limits if limits is not None else ConnectionLimits()
            if max_concurrent_rpcs > effective_limits.max_connections:
                raise ValueError(
                    "max_concurrent_rpcs must be <= limits.max_connections "
                    f"(got max_concurrent_rpcs={max_concurrent_rpcs}, "
                    f"max_connections={effective_limits.max_connections})"
                )
        if storage_path is not None and auth.storage_path != storage_path:
            auth = dataclasses.replace(auth, storage_path=storage_path)
        self._auth = auth
        self._timeout = timeout
        self._storage_path = storage_path or auth.storage_path
        self._auth_get: ResponseGetter | None = None
        self._auth_post: ResponsePoster | None = None
        self._keepalive = keepalive
        self._keepalive_min_interval = keepalive_min_interval
        self._rate_limit_max_retries = rate_limit_max_retries
        self._server_error_max_retries = server_error_max_retries
        self._limits = limits
        self._max_concurrent_uploads = max_concurrent_uploads
        self._max_concurrent_rpcs = max_concurrent_rpcs
        self._upload_timeout = upload_timeout
        self._on_rpc_event = on_rpc_event
        self._metrics_started = 0
        self._metrics_succeeded = 0
        self._metrics_failed = 0
        self._metrics_latency_total = 0.0
        self._cookie_saver = cookie_saver
        self._cookie_rotator = cookie_rotator
        self._chat_timeout = chat_timeout
        self._closed = False
        live_rpc = (
            _StdlibLiveRpcClient(
                auth,
                timeout=timeout,
                rate_limit_max_retries=rate_limit_max_retries,
                server_error_max_retries=server_error_max_retries,
            )
            if auth.cookies and auth.csrf_token and auth.session_id
            else None
        )
        self._live_rpc = live_rpc
        rpc = _rpc_from_fixtures()
        self._rpc = rpc
        metadata_service = OfflineNotebookMetadataService.from_list_payload(
            rpc.list_notebooks_payload()
        )
        notebook_ids = [notebook.id for notebook in metadata_service.list()]
        source_service = OfflineSourceService.from_rpc(rpc, notebook_ids)
        self.sources = SourcesAPI(
            rpc,
            source_service=source_service,
            notebook_ids=notebook_ids,
            live_rpc=live_rpc,
        )
        note_service = OfflineNoteService.from_rpc(rpc, notebook_ids)
        mind_map_service = OfflineMindMapService.for_notebooks(notebook_ids)
        self.notes = NotesAPI(
            notes=note_service, mind_maps=mind_map_service, live_rpc=live_rpc
        )
        status_fixtures = OfflineReadOnlyStatusFixtures.load_default()
        artifact_service = OfflineArtifactService.from_rpc(rpc, notebook_ids)
        self.sharing = SharingAPI(rpc, status_fixtures=status_fixtures, live_rpc=live_rpc)
        self.notebooks = NotebooksAPI(
            metadata_service=metadata_service,
            live_rpc=live_rpc,
            sources_api=self.sources,
            share_manager=self.sharing,
        )
        self.artifacts = ArtifactsAPI(
            artifacts=artifact_service,
            status_fixtures=status_fixtures,
            live_rpc=live_rpc,
            source_ids_provider=self.notebooks.get_source_ids,
            note_creator=self.notes.create,
        )
        self.chat = ChatAPI(
            rpc=rpc,
            chat_timeout=chat_timeout,
            live_rpc=live_rpc,
            source_ids_provider=self.notebooks.get_source_ids,
        )
        self.mind_maps = MindMapsAPI(
            rpc,
            mind_maps=mind_map_service,
            live_rpc=live_rpc,
            artifacts=self.artifacts,
            notes=self.notes,
            notebooks=self.notebooks,
        )
        self.research = ResearchAPI(
            rpc,
            status_fixtures=status_fixtures,
            source_service=source_service,
            source_lister=self.sources.list,
            live_rpc=live_rpc,
        )
        self.settings = SettingsAPI(rpc, status_fixtures=status_fixtures, live_rpc=live_rpc)

    @classmethod
    def from_storage(
        cls,
        path: str | Path | None = None,
        timeout: float = 30.0,
        profile: str | None = None,
        keepalive: float | None = None,
        keepalive_min_interval: float = 60.0,
        rate_limit_max_retries: int = 3,
        server_error_max_retries: int = 3,
        limits: ConnectionLimits | None = None,
        max_concurrent_uploads: int | None = 4,
        max_concurrent_rpcs: int | None = 16,
        upload_timeout: httpx.Timeout | None = None,
        on_rpc_event: Callable[[RpcTelemetryEvent], object] | None = None,
        chat_timeout: float | None = 180.0,
    ) -> "_FromStorageContext":
        return _FromStorageContext(
            cls,
            path=path,
            timeout=timeout,
            profile=profile,
            keepalive=keepalive,
            keepalive_min_interval=keepalive_min_interval,
            rate_limit_max_retries=rate_limit_max_retries,
            server_error_max_retries=server_error_max_retries,
            limits=limits,
            max_concurrent_uploads=max_concurrent_uploads,
            max_concurrent_rpcs=max_concurrent_rpcs,
            upload_timeout=upload_timeout,
            on_rpc_event=on_rpc_event,
            chat_timeout=chat_timeout,
        )

    @property
    def auth(self) -> AuthTokens:
        return self._auth

    def set_auth_transport(
        self,
        *,
        get: ResponseGetter | None = None,
        post: ResponsePoster | None = None,
    ) -> None:
        """Inject auth-refresh transport callbacks for offline tests/callers."""

        self._auth_get = get
        self._auth_post = post

    def set_rpc_transport(
        self,
        *,
        get: ResponseGetter | None = None,
        post: ResponsePoster | None = None,
    ) -> None:
        """Inject the live RPC POST seam for offline transport tests."""

        if self._live_rpc is None:
            self._live_rpc = _StdlibLiveRpcClient(
                self._auth,
                timeout=self._timeout,
                rate_limit_max_retries=self._rate_limit_max_retries,
                server_error_max_retries=self._server_error_max_retries,
            )
            self.notebooks._live_rpc = self._live_rpc
            self.sources._live_rpc = self._live_rpc
            self.notes._live_rpc = self._live_rpc
            self.artifacts._live_rpc = self._live_rpc
            self.mind_maps._live_rpc = self._live_rpc
            self.chat._live_rpc = self._live_rpc
            self.settings._live_rpc = self._live_rpc
            self.sharing._live_rpc = self._live_rpc
            self.research._live_rpc = self._live_rpc
        self._live_rpc.set_transport(get=get, post=post)

    @property
    def is_connected(self) -> bool:
        return not self._closed

    async def __aenter__(self) -> "NotebookLMClient":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        try:
            await self.close()
        except BaseException as close_exc:
            if exc_val is not None:
                logging.getLogger(__name__).warning(
                    "Suppressing close() error to preserve original exception: %s",
                    close_exc,
                )
                return
            raise

    async def close(
        self,
        *,
        drain: bool = True,
        drain_timeout: float | None = None,
    ) -> None:
        if not drain:
            self._closed = True
            return
        try:
            await self.drain(timeout=drain_timeout)
        except (TimeoutError, asyncio.CancelledError):
            self._closed = True
            raise
        self._closed = True

    async def drain(self, timeout: float | None = None) -> None:
        return None

    def metrics_snapshot(self) -> ClientMetricsSnapshot:
        return ClientMetricsSnapshot(
            rpc_calls_started=self._metrics_started,
            rpc_calls_succeeded=self._metrics_succeeded,
            rpc_calls_failed=self._metrics_failed,
            rpc_latency_seconds_total=self._metrics_latency_total,
        )

    async def _emit_rpc_event(self, event: RpcTelemetryEvent) -> None:
        callback = self._on_rpc_event
        if callback is None:
            return
        try:
            result = callback(event)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:  # noqa: BLE001 - telemetry must not break RPCs
            logging.getLogger(__name__).warning("RPC telemetry callback failed: %s", exc)

    async def refresh_auth(self) -> AuthTokens:
        storage = self._storage_path or self._auth.storage_path
        getter = self._auth_get
        if getter is None:
            raise NotImplementedInPhaseError(
                "NotebookLMClient.refresh_auth requires injected offline auth transport callbacks"
            )

        if storage is not None and Path(storage).is_file():
            state = _auth_module._cookies.load_storage_state(storage)
        else:
            state = {
                "cookies": [
                    {
                        "name": name,
                        "value": value,
                        "domain": domain,
                        "path": path,
                        "secure": True,
                        "httpOnly": False,
                        "expires": -1,
                    }
                    for (name, domain, path), value in _auth_module.normalize_cookie_map(
                        self._auth.cookies
                    ).items()
                ],
                "origins": [],
            }
        original_snapshot = _auth_module.snapshot_cookie_jar(
            _auth_module._cookie_jar_from_storage_state(state)
        )
        cookies = _auth_module._cookies.cookies_from_storage_state(state)
        homepage_url = _auth_module._homepage_url(
            authuser=self._auth.authuser,
            account_email=self._auth.account_email,
        )
        homepage = getter(
            homepage_url,
            headers={
                "Cookie": _auth_module._cookie_header(
                    _auth_module._cookies_for_url(
                        cookies, homepage_url
                    )
                )
            },
            timeout=_auth_module._DEFAULT_NETWORK_TIMEOUT,
            max_redirects=_auth_module._AUTH_MAX_REDIRECTS,
        )
        _auth_module._raise_for_bad_response(homepage, "NotebookLM homepage")
        text = homepage.text()
        if _auth_module._is_auth_redirect(homepage.url):
            raise _auth_module.AuthenticationError("Authentication expired or invalid")
        csrf, session_id = _auth_module.extract_auth_tokens_from_html(text)
        _auth_module._merge_cookie_updates(
            state,
            _auth_module._cookies_from_set_cookie(
                homepage.headers.get("set-cookie"), response_url=homepage.url
            ),
        )
        if storage is not None:
            _auth_module.save_cookies_to_storage(
                _auth_module._cookie_jar_from_storage_state(state),
                storage,
                original_snapshot=original_snapshot,
            )
        self._auth.csrf_token = csrf
        self._auth.session_id = session_id
        self._auth.storage_path = Path(storage) if storage is not None else None
        self._auth.cookies = _auth_module.normalize_cookie_map(
            {
                (
                    c.get("name"),
                    c.get("domain", ".google.com"),
                    c.get("path", "/"),
                ): c.get("value", "")
                for c in _auth_module._cookies.cookies_from_storage_state(state)
            }
        )
        self._auth.cookie_jar = _auth_module._cookie_jar_from_storage_state(state)
        return self._auth

    async def rpc_call(
        self,
        method: Any,
        params: list[Any],
        allow_null: bool = False,
        *,
        disable_internal_retries: bool = False,
    ) -> Any:
        rpcid = getattr(method, "value", method)
        if not isinstance(rpcid, str) or not isinstance(params, list):
            raise ValidationError("rpc_call requires an RPC method and parameter list")
        if self._live_rpc is None and rpcid not in {"wXbhsf", "gArtLc"}:
            raise ValidationError("fake rpc request is not supported")
        event_method = getattr(method, "name", rpcid)
        started_at = time.monotonic()
        self._metrics_started += 1
        try:
            result = await self._rpc_call_unmeasured(
                method,
                params,
                allow_null=allow_null,
                disable_internal_retries=disable_internal_retries,
            )
        except Exception as exc:
            elapsed = time.monotonic() - started_at
            self._metrics_failed += 1
            self._metrics_latency_total += elapsed
            await self._emit_rpc_event(
                RpcTelemetryEvent(
                    method=event_method,
                    status="error",
                    elapsed_seconds=elapsed,
                    error_type=type(exc).__name__,
                )
            )
            raise
        elapsed = time.monotonic() - started_at
        self._metrics_succeeded += 1
        self._metrics_latency_total += elapsed
        await self._emit_rpc_event(
            RpcTelemetryEvent(
                method=event_method,
                status="success",
                elapsed_seconds=elapsed,
            )
        )
        return result

    async def _rpc_call_unmeasured(
        self,
        method: Any,
        params: list[Any],
        allow_null: bool = False,
        *,
        disable_internal_retries: bool = False,
    ) -> Any:
        rpcid = getattr(method, "value", method)
        if self._live_rpc is not None:
            try:
                rpc_method = method if isinstance(method, RPCMethod) else RPCMethod(rpcid)
            except ValueError as exc:
                raise ValidationError("rpc_call requires a known RPC method") from exc
            return await self._live_rpc.rpc_call(
                rpc_method,
                params,
                allow_null=allow_null,
                disable_internal_retries=disable_internal_retries,
            )
        request = FakeRpcRequest(
            rpcid=rpcid,
            payload=json.dumps(params, separators=(",", ":")),
            kind="generic",
        )
        payloads = self._rpc.call(request)
        if not payloads and allow_null:
            return None
        if len(payloads) != 1:
            raise ValidationError("fake rpc response is not supported")
        return payloads[0]


class _FromStorageContext:
    __slots__ = ("_cls", "_kwargs", "_client", "_owns_close")

    def __init__(self, cls: type[NotebookLMClient], **kwargs: Any) -> None:
        self._cls = cls
        self._kwargs = kwargs
        self._client: NotebookLMClient | None = None
        self._owns_close = False

    async def _build(self) -> NotebookLMClient:
        if self._client is not None:
            return self._client
        kwargs = self._kwargs
        path = kwargs["path"]
        profile = kwargs["profile"]
        auth = await AuthTokens.from_storage(Path(path) if path else None, profile=profile)
        self._client = self._cls(
            auth,
            timeout=kwargs["timeout"],
            storage_path=auth.storage_path,
            keepalive=kwargs["keepalive"],
            keepalive_min_interval=kwargs["keepalive_min_interval"],
            rate_limit_max_retries=kwargs["rate_limit_max_retries"],
            server_error_max_retries=kwargs["server_error_max_retries"],
            limits=kwargs["limits"],
            max_concurrent_uploads=kwargs["max_concurrent_uploads"],
            max_concurrent_rpcs=kwargs["max_concurrent_rpcs"],
            upload_timeout=kwargs["upload_timeout"],
            on_rpc_event=kwargs["on_rpc_event"],
            chat_timeout=kwargs["chat_timeout"],
        )
        return self._client

    def __await__(self) -> Generator[Any, None, NotebookLMClient]:
        _warn_deprecated(
            "Awaiting NotebookLMClient.from_storage(...) is deprecated; use "
            "`async with NotebookLMClient.from_storage(...) as client:` "
            "instead. The await form will be removed in v1.0.",
            removal="1.0",
            stacklevel=3,
        )
        return self._build().__await__()

    async def __aenter__(self) -> NotebookLMClient:
        client = await self._build()
        await client.__aenter__()
        self._owns_close = True
        return client

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._owns_close and self._client is not None:
            await self._client.__aexit__(exc_type, exc_val, exc_tb)


__all__ = [
    "NotebookLMClient",
]
