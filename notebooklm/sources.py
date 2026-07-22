"""Offline fixture-backed sources API surface.

The local ``SourcesAPI`` decodes committed synthetic list-source fixtures through
the fake RPC seam and layers deterministic fixture-derived fulltext, guide,
freshness, wait, local-file ingestion, and in-memory mutation helpers on top.
It never performs live RPC, reads authentication/browser/home state, uploads to
NotebookLM, or mutates real NotebookLM data.
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any, NoReturn
from urllib.parse import parse_qsl, urlencode, urlsplit

from .utils import _future_errors_enabled, _resolve_get
from .config import get_base_url
from .errors import ValidationError
from .exceptions import (
    NonIdempotentRetryError,
    SourceAddError,
    SourceNotFoundError,
    SourceProcessingError,
    SourceTimeoutError,
)
from .fake_rpc import OfflineFixtureRpcClient
from .rpc.types import RPCMethod, get_upload_url
from .types import (
    Source,
    SourceFulltext,
    SourceGuide,
    SourceStatus,
    SourceSummary,
    SourceType,
    _source_url_from_metadata,
)
from .urls import is_youtube_url


def _fail(reason: str) -> NoReturn:
    raise ValidationError(f"invalid list_sources payload: {reason}")


_TYPE_BY_CODE = {
    1: SourceType.WEB_PAGE,
    2: SourceType.PASTED_TEXT,
    3: SourceType.GOOGLE_DOCS,
    4: SourceType.GOOGLE_SLIDES,
    5: SourceType.GOOGLE_SPREADSHEET,
    6: SourceType.PDF,
}

_DRIVE_TYPE_CODE_BY_MIME = {
    "google-doc": 3,
    "application/vnd.google-apps.document": 3,
    "google-slides": 4,
    "application/vnd.google-apps.presentation": 4,
    "google-sheets": 5,
    "application/vnd.google-apps.spreadsheet": 5,
    "pdf": 6,
    "application/pdf": 6,
}


def _template_block() -> list[Any]:
    return [2, None, None, [1, None, None, None, None, None, None, None, None, None, [1]]]


def _resolve_upload_content_type(file_path: Path, mime_type: str | None) -> str:
    if mime_type is not None:
        content_type = mime_type.strip()
        if not content_type:
            raise ValidationError("mime_type cannot be empty or whitespace-only")
        return content_type
    guessed, _encoding = mimetypes.guess_type(file_path.name)
    return guessed or "application/octet-stream"


def _validate_upload_file_supported(file_path: Path, content_type: str) -> None:
    normalized = content_type.split(";", 1)[0].strip().lower()
    if file_path.suffix.lower() in {".html", ".htm", ".xhtml", ".xht"} or normalized in {
        "text/html",
        "application/xhtml+xml",
    }:
        raise ValidationError(
            "HTML file uploads are not supported by NotebookLM's upload endpoint: "
            f"{file_path.name}. Convert the page to .txt, .md, or .pdf first, then retry."
        )


def _default_port_for_scheme(scheme: str) -> int | None:
    if scheme == "https":
        return 443
    if scheme == "http":
        return 80
    return None


def _normalize_upload_path(path: str) -> str:
    return (path or "/").rstrip("/") + "/"


def _validate_resumable_upload_url(upload_url: str) -> str:
    try:
        parsed = urlsplit(upload_url)
        actual_port = parsed.port or _default_port_for_scheme(parsed.scheme)
        expected = urlsplit(get_upload_url())
        expected_port = expected.port or _default_port_for_scheme(expected.scheme)
    except ValueError as exc:
        raise ValidationError("Upload URL is not valid") from exc
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.hostname is None
        or parsed.hostname != expected.hostname
        or actual_port != expected_port
        or _normalize_upload_path(parsed.path) != _normalize_upload_path(expected.path)
    ):
        raise ValidationError("Upload URL host is not trusted")
    upload_ids = [
        value
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() == "upload_id"
    ]
    if len(upload_ids) != 1 or not upload_ids[0]:
        raise ValidationError("Upload URL must include exactly one non-empty upload_id")
    return upload_url


def _header(headers: Any, name: str) -> str | None:
    lowered = name.lower()
    for key, value in dict(headers or {}).items():
        if str(key).lower() == lowered:
            return str(value)
    return None


async def _maybe_await(value: Any) -> None:
    if hasattr(value, "__await__"):
        await value


def _extract_file_source_id(result: Any, filename: str) -> str | None:
    def unwrap(value: Any) -> Any:
        depth = 0
        while isinstance(value, list) and len(value) == 1 and depth < 8:
            value = value[0]
            depth += 1
        return value

    def candidate(value: Any) -> str | None:
        value = unwrap(value)
        if not isinstance(value, str):
            return None
        value = value.strip()
        if value and value != filename:
            return value
        return None

    direct = candidate(result)
    if direct is not None:
        return direct
    if isinstance(result, dict):
        for key in ("SOURCE_ID", "source_id", "sourceId", "id"):
            found = candidate(result.get(key))
            if found is not None:
                return found
        for value in result.values():
            found = _extract_file_source_id(value, filename)
            if found is not None:
                return found
    if isinstance(result, list):
        for value in result:
            found = _extract_file_source_id(value, filename)
            if found is not None:
                return found
    return None


def _extract_all_text(data: list[Any], max_depth: int = 100) -> list[str]:
    if max_depth <= 0:
        return []
    texts: list[str] = []
    for item in data:
        if isinstance(item, str) and item:
            texts.append(item)
        elif isinstance(item, list):
            texts.extend(_extract_all_text(item, max_depth - 1))
    return texts


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


def _status(value: Any) -> SourceStatus:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail("status must be an integer source status")
    try:
        return SourceStatus(value)
    except ValueError:
        _fail("status is not supported")


def _type_code(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        _fail("type code must be integer or null")
    return value


def _guide_keywords(source: Source, title: str) -> tuple[str, ...]:
    words: list[str] = []
    for token in title.replace("_", "-").split():
        cleaned = "".join(ch for ch in token.lower() if ch.isalnum() or ch == "-")
        if cleaned and cleaned not in words:
            words.append(cleaned)
    if (
        source.url
        and "notebooklm-bare" in source.url
        and "notebooklm-bare" not in words
    ):
        words.append("notebooklm-bare")
    return tuple(words[:6])


def parse_list_sources_payload(payload: Any) -> list[Source]:
    """Parse a decoded synthetic list-sources payload into source models."""

    if not isinstance(payload, list):
        _fail("expected source rows")
    parsed: list[Source] = []
    for row in payload:
        if not isinstance(row, list) or len(row) < 6:
            _fail("source row is malformed")
        source_id, title, url, raw_type_code, raw_created_at, raw_status = row[:6]
        if not isinstance(source_id, str) or source_id == "":
            _fail("source id must be non-empty text")
        if title is not None and not isinstance(title, str):
            _fail("source title must be text or null")
        if url is not None and not isinstance(url, str):
            _fail("source url must be text or null")
        parsed.append(
            Source(
                id=source_id,
                title=title,
                url=url,
                _type_code=_type_code(raw_type_code),
                created_at=_created_at(raw_created_at),
                status=_status(raw_status),
            )
        )
    return parsed


class OfflineSourceService:
    """In-memory service over synthetic source payloads."""

    def __init__(self, sources_by_notebook: dict[str, list[Source]]) -> None:
        self._sources_by_notebook = {
            notebook_id: list(sources)
            for notebook_id, sources in sources_by_notebook.items()
        }
        self._created_count = 0
        self._fulltext_by_source: dict[tuple[str, str], str] = {}
        self._file_size_by_source: dict[tuple[str, str], int] = {}

    @classmethod
    def from_rpc(
        cls, rpc: OfflineFixtureRpcClient, notebook_ids: list[str]
    ) -> "OfflineSourceService":
        sources_by_notebook: dict[str, list[Source]] = {}
        for notebook_id in notebook_ids:
            try:
                payload = rpc.list_sources_payload(notebook_id)
            except ValidationError:
                sources_by_notebook[notebook_id] = []
                continue
            sources_by_notebook[notebook_id] = parse_list_sources_payload(payload)
        return cls(sources_by_notebook)

    def list(self, notebook_id: str) -> list[Source]:
        return list(self._sources_by_notebook.get(notebook_id, ()))

    def get(self, notebook_id: str, source_id: str) -> Source | None:
        for source in self._sources_by_notebook.get(notebook_id, ()):
            if source.id == source_id:
                return source
        return None

    def add_text(
        self,
        notebook_id: str,
        title: str,
        content: str,
        *,
        wait: bool = False,
        wait_timeout: float = 120.0,
        idempotent: bool = False,
    ) -> Source:
        if not isinstance(title, str) or title == "":
            raise ValidationError("source title must be non-empty text")
        if not isinstance(content, str):
            raise ValidationError("source content must be text")
        if idempotent:
            for source in self._sources_by_notebook.get(notebook_id, ()):
                if source.title == title and source._type_code == 2:
                    return source
        source = self._create_source(notebook_id, title=title, url=None, type_code=2)
        self._fulltext_by_source[(notebook_id, source.id)] = content
        return source

    def add_file(
        self,
        notebook_id: str,
        file_path: str | Path,
        mime_type: str | None = None,
        *,
        wait: bool = False,
        wait_timeout: float = 120.0,
        title: str | None = None,
        follow_symlinks: bool = False,
    ) -> Source:
        try:
            path = Path(file_path)
        except TypeError:
            raise ValidationError("file path must be a path") from None
        try:
            is_symlink = path.is_symlink()
            exists = path.exists()
            is_file = path.is_file() if exists else False
        except (OSError, ValueError):
            raise ValidationError("file path could not be inspected") from None
        if is_symlink and not follow_symlinks:
            raise ValidationError(
                "file path is a symlink; pass --follow-symlinks to allow"
            )
        if not exists:
            raise ValidationError("file path must exist")
        if not is_file:
            raise ValidationError("file path must be a regular file")
        if mime_type is not None:
            if not isinstance(mime_type, str) or mime_type.strip() == "":
                raise ValidationError("mime_type cannot be empty")
        source_title = path.name if title is None else title
        if not isinstance(source_title, str) or source_title == "":
            raise ValidationError("source title must be non-empty text")
        try:
            data = path.read_bytes()
        except OSError:
            raise ValidationError("file could not be read") from None
        content = data.decode("utf-8", errors="replace")
        source = self._create_source(
            notebook_id, title=source_title, url=None, type_code=None
        )
        self._fulltext_by_source[(notebook_id, source.id)] = content
        self._file_size_by_source[(notebook_id, source.id)] = len(data)
        return source

    def file_size(self, notebook_id: str, source_id: str) -> int:
        return self._file_size_by_source.get((notebook_id, source_id), 0)

    def add_url(
        self,
        notebook_id: str,
        url: str,
        *,
        wait: bool = False,
        wait_timeout: float = 120.0,
    ) -> Source:
        if not isinstance(url, str) or url == "":
            raise ValidationError("source url must be non-empty text")
        return self._create_source(notebook_id, title=url, url=url, type_code=1)

    def add_drive(
        self,
        notebook_id: str,
        file_id: str,
        title: str,
        mime_type: str = "application/vnd.google-apps.document",
        *,
        wait: bool = False,
        wait_timeout: float = 120.0,
    ) -> Source:
        if not isinstance(file_id, str) or file_id == "":
            raise ValidationError("drive file id must be non-empty text")
        if not isinstance(title, str) or title == "":
            raise ValidationError("source title must be non-empty text")
        type_code = _DRIVE_TYPE_CODE_BY_MIME.get(mime_type)
        if type_code is None:
            raise ValidationError("unsupported drive mime type")
        return self._create_source(
            notebook_id, title=title, url=f"gdrive://{file_id}", type_code=type_code
        )

    def _create_source(
        self,
        notebook_id: str,
        *,
        title: str,
        url: str | None,
        type_code: int | None,
    ) -> Source:
        self._created_count += 1
        source = Source(
            id=f"offline-source-{self._created_count:04d}",
            title=title,
            url=url,
            _type_code=type_code,
            created_at=datetime.fromtimestamp(self._created_count, timezone.utc),
            status=SourceStatus.READY,
        )
        self._sources_by_notebook.setdefault(notebook_id, []).append(source)
        return source

    def delete(self, notebook_id: str, source_id: str) -> None:
        sources = self._sources_by_notebook.setdefault(notebook_id, [])
        self._sources_by_notebook[notebook_id] = [
            source for source in sources if source.id != source_id
        ]

    def rename(
        self,
        notebook_id: str,
        source_id: str,
        new_title: str,
        *,
        return_object: bool = True,
    ) -> Source | None:
        source = self.get(notebook_id, source_id)
        if source is None:
            raise ValidationError("source not found")
        if not isinstance(new_title, str) or new_title == "":
            raise ValidationError("source title must be non-empty text")
        source.title = new_title
        return source if return_object else None

    def refresh(self, notebook_id: str, source_id: str) -> bool:
        if self.get(notebook_id, source_id) is None:
            raise ValidationError("source not found")
        return True

    def fulltext(
        self,
        notebook_id: str,
        source_id: str,
        *,
        output_format: str = "text",
    ) -> SourceFulltext:
        source = self.get(notebook_id, source_id)
        if source is None:
            raise ValidationError("source not found")
        if output_format not in {"text", "markdown"}:
            raise ValidationError("output format must be 'text' or 'markdown'")
        title = source.title or "Untitled source"
        if source.url:
            text = f"Synthetic full text for {title} from {source.url}."
        else:
            text = self._fulltext_by_source.get(
                (notebook_id, source.id),
                f"Synthetic full text for {title}.",
            )
        content = f"# {title}\n\n{text}" if output_format == "markdown" else text
        return SourceFulltext(
            source_id=source.id,
            title=title,
            content=content,
            _type_code=source._type_code,
            url=source.url,
            char_count=len(content),
        )

    def guide(self, notebook_id: str, source_id: str) -> SourceGuide:
        source = self.get(notebook_id, source_id)
        if source is None:
            raise ValidationError("source not found")
        title = source.title or "Untitled source"
        if source.url:
            summary = f"Synthetic source guide for {title} from {source.url}."
        else:
            summary = f"Synthetic source guide for {title}."
        return SourceGuide(
            summary=summary,
            keywords=_guide_keywords(source, title),
        )

    def check_freshness(self, notebook_id: str, source_id: str) -> bool:
        """Return fixture-derived staleness for a source.

        The offline fixture layer cannot contact NotebookLM or the source URL to
        perform live freshness checks. It therefore exposes a deterministic
        read-only predicate from committed source status only: ``READY`` rows are
        treated as not stale, while non-ready rows require attention.
        """

        source = self.get(notebook_id, source_id)
        if source is None:
            raise ValidationError("source not found")
        return source.status is not SourceStatus.READY

    def wait_until_ready(
        self, notebook_id: str, source_id: str, timeout: float = 120.0, **kwargs: Any
    ) -> Source:
        """Return a ready source or fail from committed fixture status only."""

        if timeout <= 0:
            raise ValidationError("source wait timeout must be positive")
        source = self.get(notebook_id, source_id)
        if source is None:
            raise ValidationError("source not found")
        if source.status is SourceStatus.READY:
            return source
        if source.status is SourceStatus.ERROR:
            raise ValidationError("source processing failed")
        raise TimeoutError("source is still processing in offline fixture")

    def wait_for_sources(
        self,
        notebook_id: str,
        source_ids: list[str],
        timeout: float = 120.0,
        **kwargs: Any,
    ) -> list[Source]:
        return [
            self.wait_until_ready(notebook_id, source_id, timeout=timeout, **kwargs)
            for source_id in source_ids
        ]

    def wait_until_registered(
        self, notebook_id: str, source_id: str, timeout: float = 30.0, **kwargs: Any
    ) -> Source:
        if timeout <= 0:
            raise ValidationError("source wait timeout must be positive")
        source = self.get(notebook_id, source_id)
        if source is None:
            raise TimeoutError("source is not registered in offline fixture")
        return source

    def has_notebook(self, notebook_id: str) -> bool:
        return notebook_id in self._sources_by_notebook


class SourcesAPI:
    """Offline synthetic sources sub-client over the fake RPC seam."""

    def __init__(
        self,
        rpc: OfflineFixtureRpcClient,
        *,
        source_service: OfflineSourceService | None = None,
        notebook_ids: list[str] | None = None,
        live_rpc: Any = None,
    ) -> None:
        self._rpc = rpc
        self._source_service = source_service or OfflineSourceService.from_rpc(
            rpc, notebook_ids or []
        )
        self._live_rpc = live_rpc

    async def list(self, notebook_id: str, *, strict: bool = False) -> list[Source]:
        if self._live_rpc is not None:
            result = await self._live_rpc.rpc_call(
                RPCMethod.GET_NOTEBOOK,
                [notebook_id, None, [2], None, 0],
                source_path=f"/notebook/{notebook_id}",
            )
            rows = self._live_source_rows(notebook_id, result, strict=strict)
            return [Source.from_api_response(row) for row in rows]
        if not self._source_service.has_notebook(notebook_id):
            if strict:
                raise ValidationError("source notebook not found")
            return []
        return self._source_service.list(notebook_id)

    async def get(self, notebook_id: str, source_id: str) -> Source | None:
        return _resolve_get(
            await self.get_or_none(notebook_id, source_id),
            not_found=SourceNotFoundError(source_id),
            resource="source",
        )

    async def get_or_none(self, notebook_id: str, source_id: str) -> Source | None:
        if self._live_rpc is not None:
            for source in await self.list(notebook_id):
                if source.id == source_id:
                    return source
            return None
        return self._source_service.get(notebook_id, source_id)

    async def add_drive(
        self,
        notebook_id: str,
        file_id: str,
        title: str,
        mime_type: str = "application/vnd.google-apps.document",
        *,
        wait: bool = False,
        wait_timeout: float = 120.0,
    ) -> Source:
        if self._live_rpc is not None:
            source_data = [
                [file_id, mime_type, 1, title],
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                1,
            ]
            result = await self._live_rpc.rpc_call(
                RPCMethod.ADD_SOURCE,
                [
                    [source_data],
                    notebook_id,
                    [2],
                    [1, None, None, None, None, None, None, None, None, None, [1]],
                ],
                source_path=f"/notebook/{notebook_id}",
                allow_null=True,
                disable_internal_retries=True,
                operation_variant="drive",
            )
            if result is None:
                raise SourceAddError(
                    title, message=f"API returned no data for Drive source: {title}"
                )
            source = Source.from_api_response(
                result, method_id=RPCMethod.ADD_SOURCE.value
            )
            if wait:
                return await self.wait_until_ready(
                    notebook_id, source.id, timeout=wait_timeout
                )
            return source
        return self._source_service.add_drive(
            notebook_id,
            file_id,
            title,
            mime_type,
            wait=wait,
            wait_timeout=wait_timeout,
        )

    async def add_file(
        self,
        notebook_id: str,
        file_path: str | Any,
        mime_type: str | None = None,
        *,
        wait: bool = False,
        wait_timeout: float = 120.0,
        title: str | None = None,
        on_progress: Any = None,
    ) -> Source:
        if self._live_rpc is not None:
            return await self._live_add_file(
                notebook_id,
                file_path,
                mime_type=mime_type,
                wait=wait,
                wait_timeout=wait_timeout,
                title=title,
                on_progress=on_progress,
            )
        source = self._source_service.add_file(
            notebook_id,
            file_path,
            mime_type=mime_type,
            wait=wait,
            wait_timeout=wait_timeout,
            title=title,
        )
        if on_progress is not None:
            total = self._source_service.file_size(notebook_id, source.id)
            result = on_progress(total, total)
            if hasattr(result, "__await__"):
                await result
        return source

    async def _live_add_file(
        self,
        notebook_id: str,
        file_path: str | Any,
        mime_type: str | None,
        *,
        wait: bool,
        wait_timeout: float,
        title: str | None,
        on_progress: Any,
    ) -> Source:
        if title is not None:
            title = title.strip()
            if not title:
                raise ValidationError("Title cannot be empty or whitespace-only")

        resolved = Path(file_path).resolve()
        if not resolved.exists():
            raise FileNotFoundError(f"File not found: {resolved}")
        if not resolved.is_file():
            raise ValidationError(f"Not a regular file: {resolved}")

        filename = resolved.name
        content_type = _resolve_upload_content_type(resolved, mime_type)
        _validate_upload_file_supported(resolved, content_type)
        try:
            baseline = {source.id for source in await self.list(notebook_id)}
        except Exception:
            baseline = None

        result = await self._live_rpc.rpc_call(
            RPCMethod.ADD_SOURCE_FILE,
            [[[filename]], notebook_id, _template_block()],
            source_path=f"/notebook/{notebook_id}",
            disable_internal_retries=True,
        )
        source_id = _extract_file_source_id(result, filename)
        if source_id is None or (baseline is not None and source_id in baseline):
            source_id = await self._probe_registered_file_source(
                notebook_id, filename, baseline
            )
        if source_id is None:
            raise SourceAddError(f"Failed to get SOURCE_ID for {filename}")

        data = resolved.read_bytes()
        await self._live_start_and_finish_upload(
            notebook_id,
            filename,
            source_id,
            content_type,
            data,
            on_progress=on_progress,
        )

        needs_title_rename = title is not None and title != filename
        if wait:
            source = await self.wait_until_ready(
                notebook_id, source_id, timeout=wait_timeout
            )
        elif needs_title_rename:
            source = await self.wait_until_registered(
                notebook_id, source_id, timeout=wait_timeout
            )
        else:
            source = Source(
                id=source_id,
                title=filename,
                status=SourceStatus.PROCESSING,
                _type_code=None,
            )

        if needs_title_rename:
            try:
                assert title is not None
                renamed = await self.rename(notebook_id, source_id, title)
                source.title = (renamed.title if renamed else None) or title
            except Exception:
                pass
        return source

    async def _probe_registered_file_source(
        self, notebook_id: str, filename: str, baseline: set[str] | None
    ) -> str | None:
        matches = [source.id for source in await self.list(notebook_id) if source.title == filename]
        if baseline is not None:
            matches = [source_id for source_id in matches if source_id not in baseline]
        elif matches:
            raise SourceAddError(
                f"Cannot disambiguate file source with title {filename!r}: "
                "baseline snapshot was unavailable"
            )
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise SourceAddError(
                f"Cannot disambiguate file source with title {filename!r}"
            )
        return None

    async def _live_start_and_finish_upload(
        self,
        notebook_id: str,
        filename: str,
        source_id: str,
        content_type: str,
        data: bytes,
        *,
        on_progress: Any,
    ) -> None:
        auth = self._live_rpc._auth
        account_email = (auth.account_email or "").strip()
        authuser = account_email or str(auth.authuser)
        base_url = get_base_url()
        start_url = f"{get_upload_url()}?{self._live_authuser_query()}"
        start_headers = {
            "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Cookie": self._live_rpc._cookie_header_for_url(start_url),
            "Origin": base_url,
            "Referer": f"{base_url}/",
            "x-goog-authuser": authuser,
            "x-goog-upload-command": "start",
            "x-goog-upload-header-content-length": str(len(data)),
            "x-goog-upload-header-content-type": content_type,
            "x-goog-upload-protocol": "resumable",
        }
        start = self._live_rpc._post(
            start_url,
            body=json.dumps(
                {
                    "PROJECT_ID": notebook_id,
                    "SOURCE_NAME": filename,
                    "SOURCE_ID": source_id,
                }
            ),
            headers=start_headers,
            timeout=self._live_rpc._timeout,
            max_redirects=5,
        )
        self._live_rpc._raise_for_status(start.status, RPCMethod.ADD_SOURCE_FILE)
        upload_url = _header(start.headers, "x-goog-upload-url")
        if not upload_url:
            raise SourceAddError("Failed to get upload URL from response headers")
        upload_url = _validate_resumable_upload_url(upload_url)

        if on_progress is not None:
            await _maybe_await(on_progress(0, len(data)))
        finalize_headers = {
            "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
            "Cookie": self._live_rpc._cookie_header_for_url(upload_url),
            "Origin": base_url,
            "Referer": f"{base_url}/",
            "x-goog-authuser": authuser,
            "x-goog-upload-command": "upload, finalize",
            "x-goog-upload-offset": "0",
        }
        finalize = self._live_rpc._post(
            upload_url,
            body=data,
            headers=finalize_headers,
            timeout=self._live_rpc._timeout,
            max_redirects=5,
        )
        self._live_rpc._raise_for_status(finalize.status, RPCMethod.ADD_SOURCE_FILE)
        if on_progress is not None:
            await _maybe_await(on_progress(len(data), len(data)))

    def _live_authuser_query(self) -> str:
        auth = self._live_rpc._auth
        account_email = (auth.account_email or "").strip()
        if account_email:
            return urlencode({"authuser": account_email})
        return urlencode({"authuser": str(auth.authuser)})

    async def add_text(
        self,
        notebook_id: str,
        title: str,
        content: str,
        *,
        wait: bool = False,
        wait_timeout: float = 120.0,
        idempotent: bool = False,
    ) -> Source:
        if self._live_rpc is not None:
            if idempotent:
                raise NonIdempotentRetryError(
                    "add_text cannot be marked idempotent: text sources have no "
                    "reliable server-side dedupe key"
                )
            params = [
                [[None, [title, content], None, 2, None, None, None, None, None, None, 1]],
                notebook_id,
                _template_block(),
            ]
            result = await self._live_rpc.rpc_call(
                RPCMethod.ADD_SOURCE,
                params,
                source_path=f"/notebook/{notebook_id}",
                operation_variant="text",
            )
            if result is None:
                raise SourceAddError(f"API returned no data for text source: {title}")
            source = Source.from_api_response(
                result, method_id=RPCMethod.ADD_SOURCE.value
            )
            if wait:
                return await self.wait_until_ready(
                    notebook_id, source.id, timeout=wait_timeout
                )
            return source
        return self._source_service.add_text(
            notebook_id,
            title,
            content,
            wait=wait,
            wait_timeout=wait_timeout,
            idempotent=idempotent,
        )

    async def add_url(
        self,
        notebook_id: str,
        url: str,
        *,
        wait: bool = False,
        wait_timeout: float = 120.0,
    ) -> Source:
        if self._live_rpc is not None:
            source_spec = (
                [None, None, None, None, None, None, None, [url], None, None, 1]
                if is_youtube_url(url)
                else [None, None, [url], None, None, None, None, None, None, None, 1]
            )
            result = await self._live_rpc.rpc_call(
                RPCMethod.ADD_SOURCE,
                [[source_spec], notebook_id, _template_block()],
                source_path=f"/notebook/{notebook_id}",
                disable_internal_retries=True,
                operation_variant="url",
            )
            if result is None:
                raise SourceAddError(url, message=f"API returned no data for URL: {url}")
            source = Source.from_api_response(
                result, method_id=RPCMethod.ADD_SOURCE.value
            )
            if wait:
                return await self.wait_until_ready(
                    notebook_id, source.id, timeout=wait_timeout
                )
            return source
        return self._source_service.add_url(
            notebook_id,
            url,
            wait=wait,
            wait_timeout=wait_timeout,
        )

    async def check_freshness(self, notebook_id: str, source_id: str) -> bool:
        if self._live_rpc is not None:
            result = await self._live_rpc.rpc_call(
                RPCMethod.CHECK_SOURCE_FRESHNESS,
                [None, [source_id], [2]],
                source_path=f"/notebook/{notebook_id}",
                allow_null=True,
            )
            if result is True:
                return True
            if result is False:
                return False
            if isinstance(result, list):
                if len(result) == 0:
                    return True
                first = result[0]
                if isinstance(first, list) and len(first) > 1 and first[1] is True:
                    return True
            return False
        return self._source_service.check_freshness(notebook_id, source_id)

    async def delete(self, notebook_id: str, source_id: str) -> None:
        if self._live_rpc is not None:
            await self._live_rpc.rpc_call(
                RPCMethod.DELETE_SOURCE,
                [[[source_id]]],
                source_path=f"/notebook/{notebook_id}",
                allow_null=True,
            )
            return None
        self._source_service.delete(notebook_id, source_id)

    async def get_fulltext(
        self, notebook_id: str, source_id: str, *, output_format: str = "text"
    ) -> SourceFulltext:
        if self._live_rpc is not None:
            if output_format not in {"text", "markdown"}:
                raise ValueError(
                    f"Invalid format: '{output_format}'. Must be 'text' or 'markdown'."
                )
            if output_format == "markdown":
                raise ImportError(
                    "The 'markdown' format requires the 'markdownify' package. "
                    "Install it with: pip install 'notebooklm-py[markdown]'"
                )
            result = await self._live_rpc.rpc_call(
                RPCMethod.GET_SOURCE,
                [[source_id], [2], [2]],
                source_path=f"/notebook/{notebook_id}",
                allow_null=True,
            )
            if not result or not isinstance(result, list):
                raise SourceNotFoundError(
                    f"Source {source_id} not found in notebook {notebook_id}"
                )
            title = ""
            source_type = None
            url = None
            descriptor = result[0]
            if isinstance(descriptor, list) and len(descriptor) > 1:
                title = descriptor[1] if isinstance(descriptor[1], str) else ""
                if len(descriptor) > 2 and isinstance(descriptor[2], list):
                    metadata = descriptor[2]
                    if len(metadata) > 4:
                        source_type = metadata[4]
                    url = _source_url_from_metadata(metadata, allow_bare_http=False)
            content = ""
            text_block = result[3] if len(result) > 3 and isinstance(result[3], list) else None
            if text_block:
                content_blocks = text_block[0]
                if isinstance(content_blocks, list):
                    content = "\n".join(_extract_all_text(content_blocks))
            return SourceFulltext(
                source_id=source_id,
                title=title,
                content=content,
                _type_code=source_type,
                url=url,
                char_count=len(content),
            )
        return self._source_service.fulltext(
            notebook_id,
            source_id,
            output_format=output_format,
        )

    async def get_guide(self, notebook_id: str, source_id: str) -> SourceGuide:
        if self._live_rpc is not None:
            result = await self._live_rpc.rpc_call(
                RPCMethod.GET_SOURCE_GUIDE,
                [[[[source_id]]]],
                source_path=f"/notebook/{notebook_id}",
                allow_null=True,
            )
            summary = ""
            keywords: list[str] = []
            if result and isinstance(result, list) and result:
                outer = result[0]
                if isinstance(outer, list) and outer:
                    inner = outer[0]
                    if isinstance(inner, list):
                        summary_block = (
                            inner[1] if len(inner) > 1 and isinstance(inner[1], list) else None
                        )
                        if summary_block:
                            summary = summary_block[0] if isinstance(summary_block[0], str) else ""
                        keyword_block = (
                            inner[2] if len(inner) > 2 and isinstance(inner[2], list) else None
                        )
                        if keyword_block:
                            keywords = (
                                keyword_block[0]
                                if isinstance(keyword_block[0], list)
                                else []
                            )
            return SourceGuide(summary=summary, keywords=tuple(keywords))
        return self._source_service.guide(notebook_id, source_id)

    async def refresh(self, notebook_id: str, source_id: str) -> bool:
        if self._live_rpc is not None:
            await self._live_rpc.rpc_call(
                RPCMethod.REFRESH_SOURCE,
                [None, [source_id], [2]],
                source_path=f"/notebook/{notebook_id}",
                allow_null=True,
            )
        else:
            self._source_service.refresh(notebook_id, source_id)
        return None if _future_errors_enabled() else True

    async def rename(
        self,
        notebook_id: str,
        source_id: str,
        new_title: str,
        *,
        return_object: bool = True,
    ) -> Source | None:
        if self._live_rpc is not None:
            result = await self._live_rpc.rpc_call(
                RPCMethod.UPDATE_SOURCE,
                [None, [source_id], [[[new_title]]]],
                source_path=f"/notebook/{notebook_id}",
                allow_null=True,
            )
            if result and return_object:
                return Source.from_api_response(
                    result, method_id=RPCMethod.UPDATE_SOURCE.value
                )
            if not return_object and (result or not _future_errors_enabled()):
                return None
            source = await self.get_or_none(notebook_id, source_id)
            if source is None:
                raise SourceNotFoundError(
                    source_id, method_id=RPCMethod.UPDATE_SOURCE.value
                )
            return source
        source = self._source_service.get(notebook_id, source_id)
        if source is None:
            if return_object or _future_errors_enabled():
                raise SourceNotFoundError(
                    source_id, method_id=RPCMethod.UPDATE_SOURCE.value
                )
            return None
        return self._source_service.rename(
            notebook_id,
            source_id,
            new_title,
            return_object=return_object,
        )

    async def wait_for_sources(
        self,
        notebook_id: str,
        source_ids: list[str],
        timeout: float = 120.0,
        **kwargs: Any,
    ) -> list[Source]:
        if self._live_rpc is not None:
            tasks = [
                self.wait_until_ready(notebook_id, source_id, timeout=timeout, **kwargs)
                for source_id in source_ids
            ]
            return list(await asyncio.gather(*tasks))
        return self._source_service.wait_for_sources(
            notebook_id,
            source_ids,
            timeout=timeout,
            **kwargs,
        )

    async def wait_until_ready(
        self,
        notebook_id: str,
        source_id: str,
        timeout: float = 120.0,
        initial_interval: float = 1.0,
        max_interval: float = 10.0,
        backoff_factor: float = 1.5,
        transient_error_types: tuple[int | None, ...] | None = None,
    ) -> Source:
        if self._live_rpc is not None:
            return await self._live_wait_for_source(
                notebook_id,
                source_id,
                timeout=timeout,
                initial_interval=initial_interval,
                max_interval=max_interval,
                backoff_factor=backoff_factor,
                transient_error_types=transient_error_types,
                require_ready=True,
            )
        return self._source_service.wait_until_ready(
            notebook_id,
            source_id,
            timeout=timeout,
            initial_interval=initial_interval,
            max_interval=max_interval,
            backoff_factor=backoff_factor,
            transient_error_types=transient_error_types,
        )

    async def wait_until_registered(
        self,
        notebook_id: str,
        source_id: str,
        timeout: float = 30.0,
        initial_interval: float = 0.5,
        max_interval: float = 5.0,
        backoff_factor: float = 1.5,
        transient_error_types: tuple[int | None, ...] | None = None,
    ) -> Source:
        if self._live_rpc is not None:
            return await self._live_wait_for_source(
                notebook_id,
                source_id,
                timeout=timeout,
                initial_interval=initial_interval,
                max_interval=max_interval,
                backoff_factor=backoff_factor,
                transient_error_types=transient_error_types,
                require_ready=False,
            )
        return self._source_service.wait_until_registered(
            notebook_id,
            source_id,
            timeout=timeout,
            initial_interval=initial_interval,
            max_interval=max_interval,
            backoff_factor=backoff_factor,
            transient_error_types=transient_error_types,
        )

    async def _live_wait_for_source(
        self,
        notebook_id: str,
        source_id: str,
        *,
        timeout: float,
        initial_interval: float,
        max_interval: float,
        backoff_factor: float,
        transient_error_types: tuple[int | None, ...] | None,
        require_ready: bool,
    ) -> Source:
        deadline = monotonic() + timeout
        interval = initial_interval
        transient = (10, 0, None) if transient_error_types is None else transient_error_types
        while True:
            source = await self.get_or_none(notebook_id, source_id)
            if source is None:
                if require_ready:
                    raise SourceNotFoundError(source_id)
            else:
                if source.is_ready or (not require_ready and not source.is_error):
                    return source
                if source.is_error and source._type_code not in transient:
                    raise SourceProcessingError(source_id)

            remaining = deadline - monotonic()
            if remaining <= 0:
                last_status = source._type_code if source is not None else None
                raise SourceTimeoutError(source_id, timeout, last_status=last_status)
            await asyncio.sleep(min(interval, remaining))
            interval = min(interval * backoff_factor, max_interval)

    @staticmethod
    def _live_source_rows(
        notebook_id: str, result: Any, *, strict: bool = False
    ) -> list[Any]:
        if not result or not isinstance(result, list):
            if strict:
                raise ValidationError("could not list sources")
            return []
        notebook = result[0]
        if not isinstance(notebook, list) or len(notebook) <= 1:
            if strict:
                raise ValidationError("could not list sources")
            return []
        rows = notebook[1]
        if rows is None:
            return []
        if not isinstance(rows, list):
            if strict:
                raise ValidationError("could not list sources")
            return []
        return rows


__all__ = [
    "OfflineSourceService",
    "Source",
    "SourceFulltext",
    "SourceGuide",
    "SourcesAPI",
    "SourceStatus",
    "SourceSummary",
    "SourceType",
    "parse_list_sources_payload",
]
