"""Public exception hierarchy matching notebooklm-py==0.7.2."""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from typing import Any, Literal

from ._logging import scrub_secrets
from .config import DEFAULT_BASE_URL, get_base_url

ArtifactStalledPhase = Literal["pending", "in_progress"]
_PREVIEW_LIMIT = 80
_PREVIEW_SCRUB_CAP = _PREVIEW_LIMIT * 10
_scrub_secrets = scrub_secrets


def _truncate_response_preview(raw: str | None) -> str | None:
    if raw is None:
        return None
    if os.environ.get("NOTEBOOKLM_DEBUG") == "1":
        return _scrub_secrets(raw)
    scrubbed = _scrub_secrets(raw[:_PREVIEW_SCRUB_CAP])
    if len(scrubbed) > _PREVIEW_LIMIT:
        return scrubbed[:_PREVIEW_LIMIT] + "..."
    return scrubbed


class NotebookLMError(Exception):
    """Base exception for all notebooklm-py errors."""


class NotFoundError(NotebookLMError):
    """Common base for resource-not-found exceptions."""


class WaitTimeoutError(NotebookLMError, TimeoutError):
    """Common base for wait/poll timeouts."""


class ValidationError(NotebookLMError):
    """Invalid user input or parameters."""


class ConfigurationError(NotebookLMError):
    """Missing or invalid configuration."""


class NetworkError(NotebookLMError):
    """Connection failures before RPC processing."""

    def __init__(
        self,
        message: str,
        *,
        method_id: str | None = None,
        original_error: Exception | None = None,
    ):
        super().__init__(message)
        self.method_id = method_id
        self.original_error = original_error


class RPCError(NotebookLMError):
    """Base for RPC-specific failures."""

    def __init__(
        self,
        message: str,
        *,
        method_id: str | None = None,
        raw_response: str | None = None,
        rpc_code: str | int | None = None,
        found_ids: list[str] | None = None,
    ):
        super().__init__(message)
        self.method_id = method_id
        self.raw_response = _truncate_response_preview(raw_response)
        self.rpc_code = rpc_code
        self.found_ids = found_ids or []

    @property
    def rpc_id(self) -> str | None:
        return self.method_id

    @property
    def code(self) -> str | int | None:
        return self.rpc_code


class DecodingError(RPCError):
    """Failed to parse RPC response structure."""


class UnknownRPCMethodError(DecodingError):
    """RPC response structure does not match expectations."""

    def __init__(
        self,
        message: str = "",
        *,
        method_id: str | int | None = None,
        path: tuple[int, ...] | None = None,
        source: str | None = None,
        found_ids: list[int | str] | None = None,
        raw_response: Any | None = None,
        data_at_failure: Any | None = None,
        rpc_code: str | int | None = None,
    ):
        super().__init__(
            message,
            method_id=str(method_id) if method_id is not None else None,
            raw_response=raw_response if isinstance(raw_response, str) else None,
            rpc_code=rpc_code,
            found_ids=None if found_ids is None else [str(item) for item in found_ids],
        )
        self.method_id = method_id  # type: ignore[assignment]
        self.path = path
        self.source = source
        if found_ids is not None:
            self.found_ids = found_ids  # type: ignore[assignment]
        if not isinstance(raw_response, str):
            self.raw_response = raw_response
        self.data_at_failure = data_at_failure

    def __str__(self) -> str:
        base = super().__str__()
        extras: list[str] = []
        if self.method_id is not None:
            extras.append(f"method_id={self.method_id!r}")
        if self.path is not None:
            extras.append(f"path={self.path!r}")
        if self.source is not None:
            extras.append(f"source={self.source!r}")
        if self.found_ids:
            extras.append(f"found_ids={self.found_ids!r}")
        if self.data_at_failure is not None:
            extras.append(f"data_at_failure={self.data_at_failure!r}")
        if not extras:
            return base
        return f"{base} [{', '.join(extras)}]" if base else ", ".join(extras)

    def __repr__(self) -> str:
        return (
            "UnknownRPCMethodError("
            f"message={super().__str__()!r}, "
            f"method_id={self.method_id!r}, "
            f"path={self.path!r}, "
            f"source={self.source!r}, "
            f"found_ids={self.found_ids!r}, "
            f"data_at_failure={self.data_at_failure!r})"
        )


class AuthError(RPCError):
    """Authentication or authorization failure."""

    recoverable: bool = False


class AuthExtractionError(RPCError):
    """Failed to extract a required field from the NotebookLM HTML response."""

    PREVIEW_LENGTH = 200

    def __init__(self, key: str, payload_preview: str, *, message: str | None = None):
        self.key = key
        pre_sliced = payload_preview[: self.PREVIEW_LENGTH * 10]
        scrubbed = _scrub_secrets(pre_sliced)
        collapsed = re.sub(r"\s+", " ", scrubbed[: self.PREVIEW_LENGTH * 5]).strip()
        self.payload_preview = collapsed[: self.PREVIEW_LENGTH]
        rendered = message or (
            f"Failed to extract {key!r} from NotebookLM HTML response. "
            "This usually means Google changed the page structure. "
            f"Preview: {self.payload_preview!r}"
        )
        super().__init__(rendered)


class RateLimitError(RPCError):
    """Rate limit exceeded."""

    def __init__(
        self,
        message: str,
        *,
        retry_after: int | None = None,
        method_id: str | None = None,
        raw_response: str | None = None,
        rpc_code: str | int | None = None,
        found_ids: list[str] | None = None,
    ):
        super().__init__(
            message,
            method_id=method_id,
            raw_response=raw_response,
            rpc_code=rpc_code,
            found_ids=found_ids,
        )
        self.retry_after = retry_after


class ServerError(RPCError):
    """Server-side error."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        method_id: str | None = None,
        raw_response: str | None = None,
        rpc_code: str | int | None = None,
        found_ids: list[str] | None = None,
    ):
        super().__init__(
            message,
            method_id=method_id,
            raw_response=raw_response,
            rpc_code=rpc_code,
            found_ids=found_ids,
        )
        self.status_code = status_code


class ClientError(RPCError):
    """Client-side error."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        method_id: str | None = None,
        raw_response: str | None = None,
        rpc_code: str | int | None = None,
        found_ids: list[str] | None = None,
    ):
        super().__init__(
            message,
            method_id=method_id,
            raw_response=raw_response,
            rpc_code=rpc_code,
            found_ids=found_ids,
        )
        self.status_code = status_code


class RPCTimeoutError(NetworkError):
    """RPC request timed out."""

    def __init__(
        self,
        message: str,
        *,
        timeout_seconds: float | None = None,
        method_id: str | None = None,
        original_error: Exception | None = None,
    ):
        super().__init__(message, method_id=method_id, original_error=original_error)
        self.timeout_seconds = timeout_seconds


class RPCResponseTooLargeError(RPCError):
    """RPC response body exceeded the configured maximum size."""

    def __init__(
        self,
        message: str,
        *,
        limit_bytes: int | None = None,
        bytes_read: int | None = None,
        method_id: str | None = None,
    ):
        super().__init__(message, method_id=method_id)
        self.limit_bytes = limit_bytes
        self.bytes_read = bytes_read


class NonIdempotentRetryError(NotebookLMError):
    """Raised when idempotent retry cannot guarantee single-write semantics."""


class IdempotencyVariantError(NotebookLMError):
    """Raised when an unknown operation variant is requested."""


class NotebookError(NotebookLMError):
    """Base for notebook operations."""


class NotebookNotFoundError(NotFoundError, RPCError, NotebookError):
    """Notebook not found."""

    def __init__(
        self,
        notebook_id: str,
        *,
        method_id: str | None = None,
        raw_response: str | None = None,
    ):
        self.notebook_id = notebook_id
        super().__init__(
            f"Notebook not found: {notebook_id}",
            method_id=method_id,
            raw_response=raw_response,
        )


class NotebookLimitError(NotebookError):
    """Notebook quota appears to be exhausted."""

    def __init__(
        self,
        current_count: int,
        *,
        limit: int | None = None,
        known_limits: tuple[int, ...] = (),
        original_error: RPCError | None = None,
    ):
        self.current_count = current_count
        self.limit = limit
        self.known_limits = known_limits
        self.original_error = original_error
        count_text = f"{current_count}/{limit}" if limit is not None else str(current_count)
        try:
            base_url = get_base_url()
        except ValueError:
            base_url = DEFAULT_BASE_URL
        message = (
            "Cannot create notebook: account appears to be at or very near the "
            f"NotebookLM notebook limit ({count_text} owned notebooks reported). "
            f"Delete old notebooks at {base_url} and try again."
        )
        if known_limits:
            message += " Known NotebookLM limits include: " + ", ".join(
                str(value) for value in known_limits
            ) + "."
        if original_error is not None:
            message += f" Original RPC error: {original_error}"
        super().__init__(message)

    def to_error_response_extra(self) -> dict[str, Any]:
        extra: dict[str, Any] = {"current_count": self.current_count, "limit": self.limit}
        if self.known_limits:
            extra["known_limits"] = list(self.known_limits)
        if self.original_error is not None:
            if self.original_error.method_id is not None:
                extra["method_id"] = self.original_error.method_id
            if self.original_error.rpc_code is not None:
                extra["rpc_code"] = self.original_error.rpc_code
        return extra


class ChatError(NotebookLMError):
    """Base for chat operations."""


class ChatResponseParseError(ChatError):
    """The streaming chat response yielded no parseable chunks."""


class SourceError(NotebookLMError):
    """Base for source operations."""


class SourceAddError(SourceError):
    """Failed to add a source."""

    def __init__(
        self,
        url: str,
        cause: Exception | None = None,
        message: str | None = None,
    ):
        self.url = url
        self.cause = cause
        msg = message or (
            f"Failed to add source: {url}\n"
            "Possible causes:\n"
            "  - URL is invalid or inaccessible\n"
            "  - Content is behind a paywall or requires authentication\n"
            "  - Page content is empty or could not be parsed\n"
            "  - Rate limiting or quota exceeded"
        )
        super().__init__(msg)


class SourceNotFoundError(NotFoundError, RPCError, SourceError):
    """Source not found in notebook."""

    def __init__(
        self,
        source_id: str,
        *,
        method_id: str | None = None,
        raw_response: str | None = None,
    ):
        self.source_id = source_id
        super().__init__(
            f"Source not found: {source_id}",
            method_id=method_id,
            raw_response=raw_response,
        )


class SourceProcessingError(SourceError):
    """Source failed to process."""

    def __init__(self, source_id: str, status: int = 3, message: str = ""):
        self.source_id = source_id
        self.status = status
        super().__init__(message or f"Source {source_id} failed to process")


class SourceTimeoutError(WaitTimeoutError, SourceError):
    """Timed out waiting for source readiness."""

    def __init__(self, source_id: str, timeout: float, last_status: int | None = None):
        self.source_id = source_id
        self.timeout = timeout
        self.last_status = last_status
        status_info = f" (last status: {last_status})" if last_status is not None else ""
        super().__init__(f"Source {source_id} not ready after {timeout:.1f}s{status_info}")


class ArtifactError(NotebookLMError):
    """Base for artifact operations."""


class ArtifactNotFoundError(NotFoundError, RPCError, ArtifactError):
    """Artifact not found."""

    def __init__(
        self,
        artifact_id: str,
        artifact_type: str | None = None,
        *,
        method_id: str | None = None,
        raw_response: str | None = None,
    ):
        self.artifact_id = artifact_id
        self.artifact_type = artifact_type
        type_label = f"{artifact_type.capitalize()} artifact" if artifact_type else "Artifact"
        super().__init__(
            f"{type_label} not found: {artifact_id}",
            method_id=method_id,
            raw_response=raw_response,
        )


class ArtifactNotReadyError(ArtifactError):
    """Artifact not in completed/ready state."""

    def __init__(
        self,
        artifact_type: str,
        artifact_id: str | None = None,
        status: str | None = None,
    ):
        self.artifact_type = artifact_type
        self.artifact_id = artifact_id
        self.status = status
        if artifact_id:
            msg = f"{artifact_type.capitalize()} artifact {artifact_id} is not ready"
            if status:
                msg += f" (status: {status})"
        else:
            msg = f"No completed {artifact_type} found"
        super().__init__(msg)


class ArtifactParseError(ArtifactError):
    """Artifact data cannot be parsed."""

    def __init__(
        self,
        artifact_type: str,
        details: str | None = None,
        artifact_id: str | None = None,
        cause: Exception | None = None,
    ):
        self.artifact_type = artifact_type
        self.artifact_id = artifact_id
        self.details = details
        self.cause = cause
        msg = f"Failed to parse {artifact_type} artifact"
        if artifact_id:
            msg += f" {artifact_id}"
        if details:
            msg += f": {details}"
        super().__init__(msg)


class ArtifactDownloadError(ArtifactError):
    """Failed to download artifact content."""

    def __init__(
        self,
        artifact_type: str,
        details: str | None = None,
        artifact_id: str | None = None,
        cause: Exception | None = None,
        status_code: int | None = None,
    ):
        self.artifact_type = artifact_type
        self.artifact_id = artifact_id
        self.details = details
        self.cause = cause
        self.status_code = status_code
        msg = f"Failed to download {artifact_type} artifact"
        if artifact_id:
            msg += f" {artifact_id}"
        if details:
            msg += f": {details}"
        super().__init__(msg)


class ArtifactFeatureUnavailableError(RPCError, ArtifactError):
    """Artifact generation feature is unavailable for this request."""

    def __init__(
        self,
        artifact_type: str,
        *,
        method_id: str | None = None,
        raw_response: str | None = None,
    ):
        self.artifact_type = artifact_type
        super().__init__(
            f"{artifact_type.replace('_', ' ').capitalize()} generation is unavailable",
            method_id=method_id,
            raw_response=raw_response,
        )


class ArtifactTimeoutError(WaitTimeoutError, ArtifactError):
    """Artifact generation did not reach a terminal state before timeout."""

    def __init__(
        self,
        notebook_id: str,
        task_id: str,
        timeout: float,
        *,
        last_status: str | None = None,
        status_history: Sequence[str] | None = None,
        status_transitions: Sequence[Any] | None = None,
        stalled_phase: ArtifactStalledPhase | None = None,
    ):
        self.notebook_id = notebook_id
        self.task_id = task_id
        self.timeout = timeout
        self.timeout_seconds = timeout
        self.last_status = last_status
        self.status_transitions = tuple(status_transitions or ())
        if status_history is None:
            status_history = tuple(
                status.status
                for status in self.status_transitions
                if isinstance(getattr(status, "status", None), str)
            )
        self.status_history = tuple(status_history)
        self.stalled_phase = stalled_phase
        history = " -> ".join(self.status_history)
        history_info = f"; status history: {history}" if history else ""
        status_info = f"last status: {last_status}" if last_status is not None else "no status"
        super().__init__(
            f"Task {task_id} in notebook {notebook_id} timed out after "
            f"{timeout}s ({status_info}{history_info})"
        )


class ArtifactPendingTimeoutError(ArtifactTimeoutError):
    """Artifact generation timed out before reaching in_progress."""

    def __init__(
        self,
        notebook_id: str,
        task_id: str,
        timeout: float,
        *,
        last_status: str | None = None,
        status_history: Sequence[str] | None = None,
        status_transitions: Sequence[Any] | None = None,
    ):
        super().__init__(
            notebook_id,
            task_id,
            timeout,
            last_status=last_status,
            status_history=status_history,
            status_transitions=status_transitions,
            stalled_phase="pending",
        )


class ArtifactInProgressTimeoutError(ArtifactTimeoutError):
    """Artifact generation timed out after reaching in_progress."""

    def __init__(
        self,
        notebook_id: str,
        task_id: str,
        timeout: float,
        *,
        last_status: str | None = None,
        status_history: Sequence[str] | None = None,
        status_transitions: Sequence[Any] | None = None,
    ):
        super().__init__(
            notebook_id,
            task_id,
            timeout,
            last_status=last_status,
            status_history=status_history,
            status_transitions=status_transitions,
            stalled_phase="in_progress",
        )


class ResearchError(NotebookLMError):
    """Base for research operations."""


class ResearchTimeoutError(WaitTimeoutError, ResearchError):
    """Research task did not reach a terminal state before timeout."""

    def __init__(
        self,
        notebook_id: str,
        task_id: str,
        timeout: float,
        *,
        last_status: str | None = None,
    ):
        self.notebook_id = notebook_id
        self.task_id = task_id
        self.timeout = timeout
        self.timeout_seconds = timeout
        self.last_status = last_status
        status_info = f" (last status: {last_status})" if last_status is not None else ""
        super().__init__(
            f"Research task {task_id} in notebook {notebook_id} timed out "
            f"after {timeout}s{status_info}"
        )


class ResearchTaskMismatchError(ValidationError):
    """Per-source research_task_id does not match the caller's task_id."""

    def __init__(self, *, task_id: str, source_research_task_id: str):
        self.task_id = task_id
        self.source_research_task_id = source_research_task_id
        super().__init__(
            "research_task_id mismatch: source carries "
            f"research_task_id={source_research_task_id!r} but caller passed "
            f"task_id={task_id!r}. Sources discovered under one research "
            "task cannot be imported under another."
        )


class NoteError(NotebookLMError):
    """Base for note operations."""


class NoteNotFoundError(NotFoundError, RPCError, NoteError):
    """Note not found in notebook."""

    def __init__(
        self,
        note_id: str,
        *,
        method_id: str | None = None,
        raw_response: str | None = None,
    ):
        self.note_id = note_id
        super().__init__(
            f"Note not found: {note_id}",
            method_id=method_id,
            raw_response=raw_response,
        )


class MindMapError(NotebookLMError):
    """Base for mind-map operations."""


class MindMapNotFoundError(NotFoundError, RPCError, MindMapError):
    """Mind map not found in notebook."""

    def __init__(
        self,
        mind_map_id: str,
        *,
        method_id: str | None = None,
        raw_response: str | None = None,
    ):
        self.mind_map_id = mind_map_id
        super().__init__(
            f"Mind map not found: {mind_map_id}",
            method_id=method_id,
            raw_response=raw_response,
        )


__all__ = [
    'NotebookLMError',
    'NotFoundError',
    'WaitTimeoutError',
    'ValidationError',
    'ConfigurationError',
    'NetworkError',
    'RPCError',
    'DecodingError',
    'UnknownRPCMethodError',
    'AuthError',
    'AuthExtractionError',
    'RateLimitError',
    'ServerError',
    'ClientError',
    'RPCTimeoutError',
    'RPCResponseTooLargeError',
    'NonIdempotentRetryError',
    'IdempotencyVariantError',
    'NotebookError',
    'NotebookNotFoundError',
    'NotebookLimitError',
    'ChatError',
    'ChatResponseParseError',
    'SourceError',
    'SourceAddError',
    'SourceNotFoundError',
    'SourceProcessingError',
    'SourceTimeoutError',
    'ArtifactError',
    'ArtifactNotFoundError',
    'ArtifactNotReadyError',
    'ArtifactParseError',
    'ArtifactDownloadError',
    'ArtifactFeatureUnavailableError',
    'ArtifactTimeoutError',
    'ArtifactPendingTimeoutError',
    'ArtifactInProgressTimeoutError',
    'ResearchError',
    'ResearchTimeoutError',
    'ResearchTaskMismatchError',
    'NoteError',
    'NoteNotFoundError',
    'MindMapError',
    'MindMapNotFoundError',
]
