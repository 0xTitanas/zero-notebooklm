"""Public NotebookLM data models for the offline parity foundation."""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Literal, TypeAlias
from urllib.parse import quote

from .config import get_base_url
from .exceptions import (
    ArtifactDownloadError,
    ArtifactError,
    ArtifactFeatureUnavailableError,
    ArtifactInProgressTimeoutError,
    ArtifactNotFoundError,
    ArtifactNotReadyError,
    ArtifactParseError,
    ArtifactPendingTimeoutError,
    ArtifactTimeoutError,
    SourceAddError,
    SourceError,
    SourceNotFoundError,
    SourceProcessingError,
    SourceTimeoutError,
)
from .utils import _MappingCompatMixin
from .rpc.types import (
    ArtifactStatus,
    ArtifactType,
    ArtifactTypeCode,
    artifact_status_to_str,
    AudioFormat,
    AudioLength,
    ChatGoal,
    ChatMode,
    ChatResponseLength,
    DriveMimeType,
    ExportType,
    InfographicDetail,
    InfographicOrientation,
    InfographicStyle,
    MindMapKind,
    QuizDifficulty,
    QuizQuantity,
    ReportFormat,
    ResearchStatus,
    ShareAccess,
    SharePermission,
    ShareViewLevel,
    SlideDeckFormat,
    SlideDeckLength,
    SourceStatus,
    SourceType,
    VideoFormat,
    VideoStyle,
    source_status_to_str,
)

ResearchResultType: TypeAlias = int | str
RESEARCH_RESULT_TYPE_WEB = 1
RESEARCH_RESULT_TYPE_DRIVE = 2
RESEARCH_RESULT_TYPE_REPORT = 5
_RESEARCH_RESULT_TYPE_ALIASES = {
    "web": RESEARCH_RESULT_TYPE_WEB,
    "drive": RESEARCH_RESULT_TYPE_DRIVE,
    "report": RESEARCH_RESULT_TYPE_REPORT,
}


def parse_result_type(value: Any) -> ResearchResultType:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return _RESEARCH_RESULT_TYPE_ALIASES.get(value.lower(), value)
    return RESEARCH_RESULT_TYPE_WEB



def _datetime_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _datetime_from_timestamp(value: Any) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _first_text(value: Any) -> str | None:
    if isinstance(value, list) and value:
        first = value[0]
        return first if isinstance(first, str) else None
    return None


def _source_id_from_envelope(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list) or not value:
        return ""
    if value[0] is not None:
        return str(value[0])
    if len(value) > 2 and isinstance(value[2], list) and value[2]:
        return str(value[2][0] or "")
    return ""


def _source_entry_from_response(data: list[Any]) -> tuple[list[Any], bool] | None:
    """Return an upstream source entry plus whether bare metadata URLs apply."""

    if not data:
        return None
    if isinstance(data[0], list):
        outer = data[0]
        if outer and isinstance(outer[0], list):
            if outer[0] and isinstance(outer[0][0], list):
                return outer[0], True
            return outer, False
        return data, False
    return None


def _source_url_from_metadata(metadata: Any, *, allow_bare_http: bool) -> str | None:
    if not isinstance(metadata, list):
        return None
    if len(metadata) > 7:
        url = _first_text(metadata[7])
        if url:
            return url
    if len(metadata) > 5:
        url = _first_text(metadata[5])
        if url:
            return url
    if allow_bare_http and metadata:
        candidate = metadata[0]
        if isinstance(candidate, str) and candidate.startswith("http"):
            return candidate
    return None


def _source_created_at_from_metadata(metadata: Any) -> datetime | None:
    if not isinstance(metadata, list) or len(metadata) <= 2:
        return None
    timestamp = metadata[2]
    if isinstance(timestamp, list) and timestamp:
        return _datetime_from_timestamp(timestamp[0])
    return None


def _source_status_from_entry(entry: list[Any]) -> SourceStatus:
    if len(entry) > 3 and isinstance(entry[3], list) and len(entry[3]) > 1:
        try:
            return SourceStatus(entry[3][1])
        except ValueError:
            return SourceStatus.READY
    return SourceStatus.READY


class UnknownTypeWarning(Warning):
    """Warning emitted when an unknown upstream type code is encountered."""



@dataclass(frozen=True)
class AccountLimits:
    """Pinned notebooklm-py==0.7.2 public dataclass field surface."""

    notebook_limit: int | None = None
    source_limit: int | None = None
    raw_limits: tuple[Any, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class AccountTier:
    """Pinned notebooklm-py==0.7.2 public dataclass field surface."""

    tier: str | None = None
    plan_name: str | None = None


@dataclass(frozen=False)
class Artifact:
    """Pinned notebooklm-py==0.7.2 public dataclass field surface."""

    id: str
    title: str
    _artifact_type: int
    status: int
    created_at: datetime | None = None
    url: str | None = None
    _variant: int | None = None

    @property
    def kind(self) -> ArtifactType:
        if self._artifact_type == 4:
            if self._variant == 1:
                return ArtifactType.FLASHCARDS
            if self._variant == 2:
                return ArtifactType.QUIZ
            if self._variant == 4:
                return ArtifactType.MIND_MAP
            return ArtifactType.UNKNOWN
        mapping = {
            1: ArtifactType.AUDIO,
            2: ArtifactType.REPORT,
            3: ArtifactType.VIDEO,
            5: ArtifactType.MIND_MAP,
            7: ArtifactType.INFOGRAPHIC,
            8: ArtifactType.SLIDE_DECK,
            9: ArtifactType.DATA_TABLE,
        }
        return mapping.get(self._artifact_type, ArtifactType.UNKNOWN)

    @classmethod
    def from_api_response(cls, data: list[Any]) -> "Artifact":
        artifact_id = data[0] if len(data) > 0 and isinstance(data[0], str) else ""
        title = data[1] if len(data) > 1 and isinstance(data[1], str) else ""
        artifact_type = data[2] if len(data) > 2 and isinstance(data[2], int) else 0
        if len(data) > 4 and not isinstance(data[3], int) and isinstance(data[4], int):
            timestamp = (
                data[15][0]
                if len(data) > 15 and isinstance(data[15], list) and data[15]
                else None
            )
            options = data[9] if len(data) > 9 and isinstance(data[9], list) else None
            variant = (
                options[1][0]
                if options is not None
                and len(options) > 1
                and isinstance(options[1], list)
                and options[1]
                and isinstance(options[1][0], int)
                else None
            )
            return cls(
                id=artifact_id,
                title=title,
                _artifact_type=artifact_type,
                status=data[4],
                created_at=_datetime_from_timestamp(timestamp),
                url=None,
                _variant=variant,
            )
        status = (
            data[3]
            if len(data) > 3 and isinstance(data[3], int)
            else ArtifactStatus.PROCESSING.value
        )
        created_at = _datetime_from_timestamp(data[4]) if len(data) > 4 else None
        url = data[5] if len(data) > 5 and isinstance(data[5], str) else None
        variant = data[6] if len(data) > 6 and isinstance(data[6], int) else None
        return cls(
            id=artifact_id,
            title=title,
            _artifact_type=artifact_type,
            status=status,
            created_at=created_at,
            url=url,
            _variant=variant,
        )

    @classmethod
    def from_mind_map(cls, data: list[Any]) -> "Artifact | None":
        if not isinstance(data, list) or len(data) < 1:
            return None
        if data[0] is None and len(data) > 1 and isinstance(data[1], list):
            inner = data[1]
            data = [inner[0], inner, *data[2:]] if inner else data
        mind_map_id = data[0] if isinstance(data[0], str) else ""
        if len(data) >= 3 and data[1] is None and data[2] == 2:
            return None
        title = ""
        created_at = None
        if len(data) > 1 and isinstance(data[1], list):
            inner = data[1]
            if len(inner) > 4 and isinstance(inner[4], str):
                title = inner[4]
            metadata_block = (
                inner[2] if len(inner) > 2 and isinstance(inner[2], list) else None
            )
            if metadata_block is not None and len(metadata_block) > 2:
                ts_data = metadata_block[2]
                if isinstance(ts_data, list) and len(ts_data) > 0:
                    created_at = _datetime_from_timestamp(ts_data[0])
        return cls(
            id=mind_map_id,
            title=title,
            _artifact_type=ArtifactTypeCode.MIND_MAP.value,
            status=ArtifactStatus.COMPLETED.value,
            created_at=created_at,
            _variant=None,
        )

    @property
    def is_completed(self) -> bool:
        return self.status == ArtifactStatus.COMPLETED.value

    @property
    def is_processing(self) -> bool:
        return self.status == ArtifactStatus.PROCESSING.value

    @property
    def is_pending(self) -> bool:
        return self.status == ArtifactStatus.PENDING.value

    @property
    def is_failed(self) -> bool:
        return self.status == ArtifactStatus.FAILED.value

    @property
    def status_str(self) -> str:
        return artifact_status_to_str(self.status)

    @property
    def is_quiz(self) -> bool:
        return self._artifact_type == ArtifactTypeCode.QUIZ.value and self._variant == 2

    @property
    def is_flashcards(self) -> bool:
        return self._artifact_type == ArtifactTypeCode.QUIZ.value and self._variant == 1

    @property
    def is_interactive_mind_map(self) -> bool:
        return self._artifact_type == ArtifactTypeCode.QUIZ.value and self._variant == 4

    @property
    def is_unclassified_type4(self) -> bool:
        return (
            self._artifact_type == ArtifactTypeCode.QUIZ.value and self._variant is None
        )

    @property
    def report_subtype(self) -> str | None:
        if self._artifact_type != ArtifactTypeCode.REPORT.value:
            return None
        title_lower = self.title.lower()
        if title_lower.startswith("briefing doc"):
            return "briefing_doc"
        if title_lower.startswith("study guide"):
            return "study_guide"
        if title_lower.startswith("blog post"):
            return "blog_post"
        return "report"

    def state(self) -> ArtifactStatus:
        return ArtifactStatus(self.status)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "type_code": self._artifact_type,
            "artifact_type": self.kind().name,
            "status": self.state().name,
            "created_at": _datetime_or_none(self.created_at),
            "url": self.url,
            "variant": self._variant,
        }


@dataclass(frozen=False)
class AskResult:
    """Pinned notebooklm-py==0.7.2 public dataclass field surface."""

    answer: str
    conversation_id: str
    turn_number: int
    is_follow_up: bool
    references: list[ChatReference] = field(default_factory=list)
    raw_response: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "conversation_id": self.conversation_id,
            "turn_number": self.turn_number,
            "is_follow_up": self.is_follow_up,
            "references": [
                ref.as_dict() if hasattr(ref, "as_dict") else ref
                for ref in self.references
            ],
            "raw_response": self.raw_response,
        }



@dataclass(frozen=False)
class ChatReference:
    """Pinned notebooklm-py==0.7.2 public dataclass field surface."""

    source_id: str
    citation_number: int | None = None
    cited_text: str | None = None
    start_char: int | None = None
    end_char: int | None = None
    chunk_id: str | None = None
    passage_id: str | None = None
    answer_start_char: int | None = None
    answer_end_char: int | None = None
    score: float | None = None

    def __post_init__(self) -> None:
        if (self.start_char is None) != (self.end_char is None):
            raise ValueError(
                "ChatReference start_char/end_char must both be set or both None "
                f"(got start_char={self.start_char!r}, end_char={self.end_char!r})"
            )
        if (self.answer_start_char is None) != (self.answer_end_char is None):
            raise ValueError(
                "ChatReference answer_start_char/answer_end_char must both be set or both None "
                f"(got answer_start_char={self.answer_start_char!r}, "
                f"answer_end_char={self.answer_end_char!r})"
            )
        if (
            self.start_char is not None
            and self.end_char is not None
            and self.start_char > self.end_char
        ):
            raise ValueError(
                f"ChatReference start_char ({self.start_char}) > end_char ({self.end_char})"
            )
        if (
            self.answer_start_char is not None
            and self.answer_end_char is not None
            and self.answer_start_char > self.answer_end_char
        ):
            raise ValueError(
                f"ChatReference answer_start_char ({self.answer_start_char}) "
                f"> answer_end_char ({self.answer_end_char})"
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "citation_number": self.citation_number,
            "cited_text": self.cited_text,
            "start_char": self.start_char,
            "end_char": self.end_char,
            "chunk_id": self.chunk_id,
            "passage_id": self.passage_id,
            "answer_start_char": self.answer_start_char,
            "answer_end_char": self.answer_end_char,
            "score": self.score,
        }


@dataclass(frozen=True)
class CitedSourceSelection:
    """Pinned notebooklm-py==0.7.2 public dataclass field surface."""

    sources: list[ResearchSourceInput]
    cited_url_count: int
    matched_url_source_count: int
    used_fallback: bool = False


@dataclass(frozen=True)
class ClientMetricsSnapshot:
    """Pinned notebooklm-py==0.7.2 public dataclass field surface."""

    rpc_calls_started: int = 0
    rpc_calls_succeeded: int = 0
    rpc_calls_failed: int = 0
    rpc_rate_limit_retries: int = 0
    rpc_server_error_retries: int = 0
    rpc_auth_retries: int = 0
    rpc_latency_seconds_total: float = 0.0
    rpc_queue_wait_seconds_total: float = 0.0
    rpc_queue_wait_seconds_max: float = 0.0
    upload_queue_wait_seconds_total: float = 0.0
    upload_queue_wait_seconds_max: float = 0.0
    lock_wait_seconds_total: float = 0.0
    lock_wait_seconds_max: float = 0.0


@dataclass(frozen=True)
class ConnectionLimits:
    """Pinned notebooklm-py==0.7.2 public dataclass field surface."""

    max_connections: int = 100
    max_keepalive_connections: int = 50
    keepalive_expiry: float = 30.0

    def to_httpx_limits(self) -> Any:
        httpx = importlib.import_module("httpx")
        return httpx.Limits(
            max_connections=self.max_connections,
            max_keepalive_connections=self.max_keepalive_connections,
            keepalive_expiry=self.keepalive_expiry,
        )


@dataclass(frozen=False)
class ConversationTurn:
    """Pinned notebooklm-py==0.7.2 public dataclass field surface."""

    query: str
    answer: str
    turn_number: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "answer": self.answer,
            "turn_number": self.turn_number,
        }



@dataclass(frozen=False)
class GenerationStatus:
    """Pinned notebooklm-py==0.7.2 public dataclass field surface."""

    task_id: str
    status: str
    url: str | None = None
    error: str | None = None
    error_code: str | None = None
    metadata: dict[str, Any] | None = None

    @property
    def is_complete(self) -> bool:
        return self.status == "completed"

    @property
    def is_failed(self) -> bool:
        return self.status == "failed"

    @property
    def is_pending(self) -> bool:
        return self.status == "pending"

    @property
    def is_in_progress(self) -> bool:
        return self.status == "in_progress"

    @property
    def is_not_found(self) -> bool:
        return self.status == "not_found"

    @property
    def is_removed(self) -> bool:
        return self.status == "removed"

    @property
    def is_rate_limited(self) -> bool:
        if not (self.is_failed or self.is_removed):
            return False
        if self.error_code == "USER_DISPLAYABLE_ERROR":
            return True
        if self.error is None:
            return False
        text = self.error.lower()
        return "rate limit" in text or "quota" in text or "limit exceeded" in text


@dataclass(frozen=True)
class MindMap:
    """Pinned notebooklm-py==0.7.2 public dataclass field surface."""

    id: str
    notebook_id: str
    title: str
    kind: MindMapKind
    created_at: datetime | None = None
    tree: dict[str, Any] | None = None


@dataclass(frozen=True)
class MindMapResult(_MappingCompatMixin):
    """Pinned notebooklm-py==0.7.2 public dataclass field surface."""

    mind_map: Any = None
    note_id: str | None = None

    def to_public_dict(self) -> dict[str, Any]:
        return {"mind_map": self.mind_map, "note_id": self.note_id}


@dataclass(frozen=False)
class Note:
    """Pinned notebooklm-py==0.7.2 public dataclass field surface."""

    id: str
    notebook_id: str
    title: str
    content: str
    created_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "notebook_id": self.notebook_id,
            "title": self.title,
            "content": self.content,
            "created_at": _datetime_or_none(self.created_at),
        }


@dataclass(frozen=False)
class Notebook:
    """Pinned notebooklm-py==0.7.2 public dataclass field surface."""

    id: str
    title: str
    created_at: datetime | None = None
    sources_count: int = 0
    is_owner: bool = True

    @classmethod
    def from_api_response(cls, data: list[Any]) -> "Notebook":
        raw_title = data[0] if len(data) > 0 and isinstance(data[0], str) else ""
        title = raw_title.replace("thought\n", "").strip()
        sources = data[1] if len(data) > 1 else None
        sources_count = len(sources) if isinstance(sources, list) else 0
        notebook_id = data[2] if len(data) > 2 and isinstance(data[2], str) else ""
        meta = data[5] if len(data) > 5 and isinstance(data[5], list) else None
        created_at = None
        if meta is not None and len(meta) > 5:
            ts_data = meta[5]
            if isinstance(ts_data, list) and len(ts_data) > 0:
                created_at = _datetime_from_timestamp(ts_data[0])
        is_owner = True
        if meta is not None and len(meta) > 1:
            is_owner = meta[1] is False
        return cls(
            id=notebook_id,
            title=title,
            created_at=created_at,
            sources_count=sources_count,
            is_owner=is_owner,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": _datetime_or_none(self.created_at),
            "sources_count": self.sources_count,
            "is_owner": self.is_owner,
        }


@dataclass(frozen=False)
class NotebookDescription:
    """Pinned notebooklm-py==0.7.2 public dataclass field surface."""

    summary: str
    suggested_topics: list[SuggestedTopic] = field(default_factory=list)

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> "NotebookDescription":
        return cls(
            summary=data.get("summary", ""),
            suggested_topics=[
                SuggestedTopic(question=t.get("question", ""), prompt=t.get("prompt", ""))
                for t in data.get("suggested_topics", [])
            ],
        )


@dataclass(frozen=False)
class NotebookMetadata:
    """Pinned notebooklm-py==0.7.2 public dataclass field surface."""

    notebook: Notebook
    sources: list[SourceSummary] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.notebook.id

    @property
    def title(self) -> str:
        return self.notebook.title

    @property
    def created_at(self) -> datetime | None:
        return self.notebook.created_at

    @property
    def is_owner(self) -> bool:
        return self.notebook.is_owner

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": _datetime_or_none(self.created_at),
            "is_owner": self.is_owner,
            "sources": [
                source.to_dict() if hasattr(source, "to_dict") else source
                for source in self.sources
            ],
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "notebook": self.notebook.as_dict()
            if hasattr(self.notebook, "as_dict")
            else self.notebook,
            "sources": [
                source.as_dict() if hasattr(source, "as_dict") else source
                for source in self.sources
            ],
        }


@dataclass(frozen=False)
class ReportSuggestion:
    """Pinned notebooklm-py==0.7.2 public dataclass field surface."""

    title: str
    description: str
    prompt: str
    audience_level: int = 2

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> "ReportSuggestion":
        return cls(
            title=data.get("title", ""),
            description=data.get("description", ""),
            prompt=data.get("prompt", ""),
            audience_level=data.get("audience_level", 2),
        )


@dataclass(frozen=True)
class ResearchSource(_MappingCompatMixin):
    """Pinned notebooklm-py==0.7.2 public dataclass field surface."""

    url: str
    title: str
    result_type: ResearchResultType = RESEARCH_RESULT_TYPE_WEB
    research_task_id: str | None = None
    report_markdown: str = ""

    @classmethod
    def from_public_dict(cls, source: Mapping[str, Any]) -> ResearchSource:
        url_raw = source.get("url", "")
        title_raw = source.get("title", "Untitled")
        research_task_id_raw = source.get("research_task_id")
        report_markdown_raw = source.get("report_markdown", "")
        return cls(
            url=url_raw if isinstance(url_raw, str) else "",
            title=title_raw if isinstance(title_raw, str) else "Untitled",
            result_type=parse_result_type(
                source.get("result_type", RESEARCH_RESULT_TYPE_WEB)
            ),
            research_task_id=(
                research_task_id_raw if isinstance(research_task_id_raw, str) else None
            ),
            report_markdown=(
                report_markdown_raw if isinstance(report_markdown_raw, str) else ""
            ),
        )

    @property
    def is_report(self) -> bool:
        return self.result_type == RESEARCH_RESULT_TYPE_REPORT

    def with_report_markdown(self, report: str) -> ResearchSource:
        return replace(self, report_markdown=report)

    def to_public_dict(self) -> dict[str, Any]:
        public: dict[str, Any] = {
            "url": self.url,
            "title": self.title,
            "result_type": self.result_type,
        }
        if self.research_task_id is not None:
            public["research_task_id"] = self.research_task_id
        if self.report_markdown:
            public["report_markdown"] = self.report_markdown
        return public


ResearchSourceInput: TypeAlias = ResearchSource | Mapping[str, Any]


@dataclass(frozen=True)
class ResearchStart(_MappingCompatMixin):
    """Pinned notebooklm-py==0.7.2 public dataclass field surface."""

    task_id: str
    report_id: str | None
    notebook_id: str
    query: str
    mode: str

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "report_id": self.report_id,
            "notebook_id": self.notebook_id,
            "query": self.query,
            "mode": self.mode,
        }


@dataclass(frozen=True)
class ResearchTask(_MappingCompatMixin):
    """Pinned notebooklm-py==0.7.2 public dataclass field surface."""

    task_id: str
    status: ResearchStatus
    query: str = ""
    sources: tuple[ResearchSource, ...] = ()
    summary: str = ""
    report: str = ""
    tasks: tuple[ResearchTask, ...] = ()

    @classmethod
    def empty(cls) -> ResearchTask:
        return cls(task_id="", status=ResearchStatus.NO_RESEARCH)

    @classmethod
    def not_found(cls, task_id: str) -> ResearchTask:
        return cls(task_id=task_id, status=ResearchStatus.NOT_FOUND)

    def _to_task_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "query": self.query,
            "sources": [source.to_public_dict() for source in self.sources],
            "summary": self.summary,
            "report": self.report,
        }

    def to_public_dict(self) -> dict[str, Any]:
        sibling_tasks = [task._to_task_dict() for task in self.tasks]
        if self.status == ResearchStatus.NO_RESEARCH and not self.task_id:
            return {"status": self.status.value, "tasks": sibling_tasks}
        return {**self._to_task_dict(), "tasks": sibling_tasks}


@dataclass(frozen=True)
class RpcTelemetryEvent:
    """Pinned notebooklm-py==0.7.2 public dataclass field surface."""

    method: str
    status: Literal["success", "error"]
    elapsed_seconds: float
    request_id: str | None = None
    error_type: str | None = None


@dataclass(frozen=False)
class ShareStatus:
    """Pinned notebooklm-py==0.7.2 public dataclass field surface."""

    notebook_id: str
    is_public: bool
    access: ShareAccess
    view_level: ShareViewLevel
    shared_users: list[SharedUser] = field(default_factory=list)
    share_url: str | None = None

    @classmethod
    def from_api_response(cls, data: list[Any], notebook_id: str) -> "ShareStatus":
        users = [
            SharedUser.from_api_response(user)
            for user in (data[0] if data and isinstance(data[0], list) else [])
            if isinstance(user, list)
        ]
        public_block = data[1] if len(data) > 1 and isinstance(data[1], list) else None
        is_public = bool(public_block[0]) if public_block else False
        return cls(
            notebook_id=notebook_id,
            is_public=is_public,
            access=ShareAccess.ANYONE_WITH_LINK if is_public else ShareAccess.RESTRICTED,
            view_level=ShareViewLevel.FULL_NOTEBOOK,
            shared_users=users,
            share_url=f"{get_base_url()}/notebook/{quote(notebook_id, safe='')}"
            if is_public
            else None,
        )


@dataclass(frozen=False)
class SharedUser:
    """Pinned notebooklm-py==0.7.2 public dataclass field surface."""

    email: str
    permission: SharePermission
    display_name: str | None = None
    avatar_url: str | None = None

    @classmethod
    def from_api_response(cls, data: list[Any]) -> "SharedUser":
        try:
            permission = SharePermission(data[1] if len(data) > 1 else 3)
        except (TypeError, ValueError):
            permission = SharePermission.VIEWER
        user_info = data[3] if len(data) > 3 and isinstance(data[3], list) else []
        return cls(
            email=data[0] if data else "",
            permission=permission,
            display_name=user_info[0] if user_info else None,
            avatar_url=user_info[1] if len(user_info) > 1 else None,
        )


@dataclass(frozen=False)
class Source:
    """Pinned notebooklm-py==0.7.2 public dataclass field surface."""

    id: str
    title: str | None = None
    url: str | None = None
    _type_code: int | None = None
    created_at: datetime | None = None
    status: SourceStatus = SourceStatus.READY

    @classmethod
    def from_row(cls, row: Any) -> "Source":
        return cls(
            id=getattr(row, "id", ""),
            title=getattr(row, "title", None),
            url=getattr(row, "url", None),
            _type_code=getattr(row, "type_code", None),
            created_at=getattr(row, "created_at", None),
            status=getattr(row, "status", SourceStatus.READY),
        )

    @classmethod
    def from_api_response(
        cls,
        data: list[Any],
        notebook_id: str | None = None,
        *,
        method_id: str | None = None,
    ) -> "Source":
        live_entry = _source_entry_from_response(data)
        if live_entry is not None:
            entry, allow_bare_http = live_entry
            metadata = entry[2] if len(entry) > 2 else None
            raw_title = entry[1] if len(entry) > 1 else None
            title = raw_title if isinstance(raw_title, str) else None
            if title is None and raw_title is not None:
                title = str(raw_title)
            return cls(
                id=_source_id_from_envelope(entry[0] if entry else None),
                title=title,
                url=_source_url_from_metadata(
                    metadata, allow_bare_http=allow_bare_http
                ),
                _type_code=metadata[4]
                if isinstance(metadata, list)
                and len(metadata) > 4
                and isinstance(metadata[4], int)
                else None,
                created_at=_source_created_at_from_metadata(metadata),
                status=_source_status_from_entry(entry),
            )

        source_id = data[0] if len(data) > 0 and isinstance(data[0], str) else ""
        title = (
            data[1]
            if len(data) > 1 and (data[1] is None or isinstance(data[1], str))
            else None
        )
        url = (
            data[2]
            if len(data) > 2 and (data[2] is None or isinstance(data[2], str))
            else None
        )
        type_code = data[3] if len(data) > 3 and isinstance(data[3], int) else None
        created_at = _datetime_from_timestamp(data[4]) if len(data) > 4 else None
        raw_status = (
            data[5]
            if len(data) > 5 and isinstance(data[5], int)
            else SourceStatus.READY
        )
        try:
            status = SourceStatus(raw_status)
        except ValueError:
            status = SourceStatus.READY
        return cls(
            id=source_id,
            title=title,
            url=url,
            _type_code=type_code,
            created_at=created_at,
            status=status,
        )

    @property
    def kind(self) -> SourceType:
        if self._type_code == 4:
            if self.url and self.url.startswith("gdrive://"):
                return SourceType.GOOGLE_SLIDES
            return SourceType.PASTED_TEXT
        if self._type_code == 5 and self.url and self.url.startswith("http"):
            return SourceType.WEB_PAGE
        mapping = {
            1: SourceType.WEB_PAGE,
            2: SourceType.PASTED_TEXT,
            3: SourceType.GOOGLE_DOCS,
            5: SourceType.GOOGLE_SPREADSHEET,
            6: SourceType.PDF,
            8: SourceType.MARKDOWN,
            9: SourceType.YOUTUBE,
            10: SourceType.MEDIA,
            11: SourceType.DOCX,
            13: SourceType.IMAGE,
            14: SourceType.GOOGLE_SPREADSHEET,
            16: SourceType.CSV,
            17: SourceType.EPUB,
        }
        if self._type_code is None:
            return SourceType.UNKNOWN
        return mapping.get(self._type_code, SourceType.UNKNOWN)

    @property
    def is_ready(self) -> bool:
        return self.status == SourceStatus.READY

    @property
    def is_processing(self) -> bool:
        return self.status == SourceStatus.PROCESSING

    @property
    def is_error(self) -> bool:
        return self.status == SourceStatus.ERROR

    def summary(self) -> SourceSummary:
        return SourceSummary(self.kind(), self.title, self.url)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "url": self.url,
            "type_code": self._type_code,
            "created_at": _datetime_or_none(self.created_at),
            "status": self.status.name,
        }


@dataclass(frozen=False)
class SourceFulltext:
    """Pinned notebooklm-py==0.7.2 public dataclass field surface."""

    source_id: str
    title: str
    content: str
    _type_code: int | None = None
    url: str | None = None
    char_count: int = 0

    @property
    def kind(self) -> SourceType:
        mapping = {
            1: SourceType.WEB_PAGE,
            2: SourceType.PASTED_TEXT,
            3: SourceType.GOOGLE_DOCS,
            4: SourceType.GOOGLE_SLIDES,
            5: SourceType.GOOGLE_SPREADSHEET,
            6: SourceType.PDF,
        }
        if self._type_code is None:
            return SourceType.UNKNOWN
        return mapping.get(self._type_code, SourceType.UNKNOWN)

    def find_citation_context(
        self,
        cited_text: str,
        context_chars: int = 200,
    ) -> list[tuple[str, int]]:
        if not cited_text or not self.content:
            return []
        search_text = cited_text[: min(40, len(cited_text))]
        matches: list[tuple[str, int]] = []
        pos = 0
        while (idx := self.content.find(search_text, pos)) != -1:
            start = max(0, idx - context_chars)
            end = min(len(self.content), idx + len(search_text) + context_chars)
            matches.append((self.content[start:end], idx))
            pos = idx + len(search_text)
        return matches

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "title": self.title,
            "content": self.content,
            "type_code": self._type_code,
            "url": self.url,
            "char_count": self.char_count,
            "kind": self.kind().name,
        }


@dataclass(frozen=True)
class SourceGuide(_MappingCompatMixin):
    """Pinned notebooklm-py==0.7.2 public dataclass field surface."""

    summary: str = ""
    keywords: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.keywords, tuple):
            object.__setattr__(self, "keywords", tuple(self.keywords))

    def as_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "keywords": list(self.keywords),
        }

    def to_public_dict(self) -> dict[str, Any]:
        return self.as_dict()


@dataclass(frozen=False)
class SourceSummary:
    """Pinned notebooklm-py==0.7.2 public dataclass field surface."""

    kind: SourceType
    title: str | None = None
    url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.kind.value,
            "title": self.title,
            "url": self.url,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.name,
            "title": self.title,
            "url": self.url,
        }


@dataclass(frozen=False)
class SuggestedTopic:
    """Pinned notebooklm-py==0.7.2 public dataclass field surface."""

    question: str
    prompt: str


__all__ = [
    'CitedSourceSelection',
    'ConnectionLimits',
    'ClientMetricsSnapshot',
    'RpcTelemetryEvent',
    'Notebook',
    'NotebookDescription',
    'NotebookMetadata',
    'SuggestedTopic',
    'Source',
    'SourceFulltext',
    'SourceSummary',
    'Artifact',
    'GenerationStatus',
    'ReportSuggestion',
    'Note',
    'ConversationTurn',
    'ChatReference',
    'AskResult',
    'ChatMode',
    'SharedUser',
    'ShareStatus',
    'ResearchStatus',
    'ResearchSource',
    'ResearchTask',
    'ResearchStart',
    'MindMap',
    'MindMapKind',
    'MindMapResult',
    'SourceGuide',
    'SourceError',
    'SourceAddError',
    'SourceProcessingError',
    'SourceTimeoutError',
    'SourceNotFoundError',
    'ArtifactError',
    'ArtifactFeatureUnavailableError',
    'ArtifactNotFoundError',
    'ArtifactNotReadyError',
    'ArtifactParseError',
    'ArtifactDownloadError',
    'ArtifactTimeoutError',
    'ArtifactPendingTimeoutError',
    'ArtifactInProgressTimeoutError',
    'UnknownTypeWarning',
    'SourceType',
    'ArtifactType',
    'ArtifactStatus',
    'AudioFormat',
    'AudioLength',
    'VideoFormat',
    'VideoStyle',
    'QuizQuantity',
    'QuizDifficulty',
    'InfographicOrientation',
    'InfographicDetail',
    'InfographicStyle',
    'SlideDeckFormat',
    'SlideDeckLength',
    'ReportFormat',
    'ChatGoal',
    'ChatResponseLength',
    'DriveMimeType',
    'ExportType',
    'SourceStatus',
    'ShareAccess',
    'ShareViewLevel',
    'SharePermission',
    'artifact_status_to_str',
    'source_status_to_str',
]
