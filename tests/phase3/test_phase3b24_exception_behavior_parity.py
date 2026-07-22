"""Pinned exception constructor/diagnostic behavior parity."""

from __future__ import annotations

import inspect


def test_rpc_error_diagnostics_and_debug_truncation_match_upstream(monkeypatch):
    from notebooklm.exceptions import RPCError

    err = RPCError(
        "boom",
        method_id="abc123",
        raw_response="x" * 100,
        rpc_code=429,
        found_ids=["one"],
    )

    assert str(err) == "boom"
    assert err.method_id == "abc123"
    assert err.rpc_id == "abc123"
    assert err.rpc_code == 429
    assert err.code == 429
    assert err.found_ids == ["one"]
    assert err.raw_response == ("x" * 80) + "..."

    monkeypatch.setenv("NOTEBOOKLM_DEBUG", "1")
    debug = RPCError("boom", raw_response="x" * 100)
    assert debug.raw_response == "x" * 100


def test_unknown_rpc_method_error_keeps_structured_context():
    from notebooklm.exceptions import UnknownRPCMethodError

    err = UnknownRPCMethodError(
        "drift",
        method_id=123,
        path=(0, 2),
        source="decoder",
        found_ids=[1, "two"],
        raw_response={"payload": []},
        data_at_failure=["bad"],
        rpc_code="E",
    )

    assert err.method_id == 123
    assert err.path == (0, 2)
    assert err.source == "decoder"
    assert err.found_ids == [1, "two"]
    assert err.raw_response == {"payload": []}
    assert err.data_at_failure == ["bad"]
    assert str(err) == (
        "drift [method_id=123, path=(0, 2), source='decoder', "
        "found_ids=[1, 'two'], data_at_failure=['bad']]"
    )
    assert repr(err) == (
        "UnknownRPCMethodError(message='drift', method_id=123, path=(0, 2), "
        "source='decoder', found_ids=[1, 'two'], data_at_failure=['bad'])"
    )


def test_auth_extraction_error_scrubs_and_collapses_preview():
    from notebooklm.exceptions import AuthExtractionError

    err = AuthExtractionError("SNlM0e", "  <html>\nSNlM0e: AF1_QpN-secret\n</html>")

    assert err.key == "SNlM0e"
    assert err.payload_preview == "<html> SNlM0e: *** </html>"
    assert "AF1_QpN-secret" not in str(err)
    assert "Failed to extract 'SNlM0e'" in str(err)


def test_domain_exception_constructors_match_upstream_messages_and_attrs():
    from notebooklm.exceptions import (
        ArtifactDownloadError,
        ArtifactFeatureUnavailableError,
        ArtifactInProgressTimeoutError,
        ArtifactNotFoundError,
        ArtifactNotReadyError,
        ArtifactParseError,
        ArtifactPendingTimeoutError,
        ArtifactTimeoutError,
        ClientError,
        NetworkError,
        NoteNotFoundError,
        NotebookLimitError,
        NotebookNotFoundError,
        RPCResponseTooLargeError,
        RPCTimeoutError,
        RateLimitError,
        ResearchTaskMismatchError,
        ResearchTimeoutError,
        ServerError,
        SourceAddError,
        SourceNotFoundError,
        SourceProcessingError,
        SourceTimeoutError,
        MindMapNotFoundError,
    )

    original = RuntimeError("rpc failed")
    cases = [
        (NetworkError("net", method_id="m", original_error=original), "net", {"method_id": "m", "original_error": original}),
        (RateLimitError("slow", retry_after=5, method_id="m"), "slow", {"retry_after": 5, "method_id": "m"}),
        (ServerError("oops", status_code=503), "oops", {"status_code": 503}),
        (ClientError("bad", status_code=404), "bad", {"status_code": 404}),
        (RPCTimeoutError("late", timeout_seconds=1.5), "late", {"timeout_seconds": 1.5}),
        (RPCResponseTooLargeError("huge", limit_bytes=10, bytes_read=11), "huge", {"limit_bytes": 10, "bytes_read": 11}),
        (NotebookNotFoundError("nb"), "Notebook not found: nb", {"notebook_id": "nb"}),
        (SourceNotFoundError("src"), "Source not found: src", {"source_id": "src"}),
        (NoteNotFoundError("note"), "Note not found: note", {"note_id": "note"}),
        (MindMapNotFoundError("map"), "Mind map not found: map", {"mind_map_id": "map"}),
        (ArtifactNotFoundError("art", "audio"), "Audio artifact not found: art", {"artifact_id": "art", "artifact_type": "audio"}),
        (SourceProcessingError("src", 3), "Source src failed to process", {"source_id": "src", "status": 3}),
        (SourceTimeoutError("src", 2.0, last_status=10), "Source src not ready after 2.0s (last status: 10)", {"source_id": "src", "timeout": 2.0, "last_status": 10}),
        (SourceAddError("https://example.test"), "Failed to add source: https://example.test\nPossible causes:\n  - URL is invalid or inaccessible\n  - Content is behind a paywall or requires authentication\n  - Page content is empty or could not be parsed\n  - Rate limiting or quota exceeded", {"url": "https://example.test", "cause": None}),
        (ArtifactNotReadyError("audio", "art", "pending"), "Audio artifact art is not ready (status: pending)", {"artifact_type": "audio", "artifact_id": "art", "status": "pending"}),
        (ArtifactParseError("quiz", "bad json", artifact_id="art", cause=original), "Failed to parse quiz artifact art: bad json", {"artifact_type": "quiz", "details": "bad json", "artifact_id": "art", "cause": original}),
        (ArtifactDownloadError("audio", "404", artifact_id="art", status_code=404), "Failed to download audio artifact art: 404", {"artifact_type": "audio", "details": "404", "artifact_id": "art", "status_code": 404}),
        (ArtifactFeatureUnavailableError("slide_deck"), "Slide deck generation is unavailable", {"artifact_type": "slide_deck"}),
        (ArtifactTimeoutError("nb", "task", 3.0, last_status="pending", status_history=["pending"]), "Task task in notebook nb timed out after 3.0s (last status: pending; status history: pending)", {"notebook_id": "nb", "task_id": "task", "timeout": 3.0, "timeout_seconds": 3.0, "last_status": "pending", "status_history": ("pending",), "stalled_phase": None}),
        (ArtifactPendingTimeoutError("nb", "task", 3.0), "Task task in notebook nb timed out after 3.0s (no status)", {"stalled_phase": "pending"}),
        (ArtifactInProgressTimeoutError("nb", "task", 3.0), "Task task in notebook nb timed out after 3.0s (no status)", {"stalled_phase": "in_progress"}),
        (ResearchTimeoutError("nb", "task", 4.0, last_status="running"), "Research task task in notebook nb timed out after 4.0s (last status: running)", {"notebook_id": "nb", "task_id": "task", "timeout": 4.0, "timeout_seconds": 4.0, "last_status": "running"}),
        (ResearchTaskMismatchError(task_id="task-a", source_research_task_id="task-b"), "research_task_id mismatch: source carries research_task_id='task-b' but caller passed task_id='task-a'. Sources discovered under one research task cannot be imported under another.", {"task_id": "task-a", "source_research_task_id": "task-b"}),
    ]

    for err, message, attrs in cases:
        assert str(err) == message
        for name, value in attrs.items():
            assert getattr(err, name) == value

    limit = NotebookLimitError(7, limit=10, known_limits=(10,), original_error=RateLimitError("rate", method_id="m", rpc_code=8))
    assert limit.current_count == 7
    assert limit.limit == 10
    assert limit.known_limits == (10,)
    assert limit.to_error_response_extra() == {
        "current_count": 7,
        "limit": 10,
        "known_limits": [10],
        "method_id": "m",
        "rpc_code": 8,
    }


def test_custom_exception_signatures_match_pinned_oracle_shape():
    from notebooklm import exceptions as ex

    assert str(inspect.signature(ex.SourceTimeoutError)) == (
        "(source_id: 'str', timeout: 'float', last_status: 'int | None' = None)"
    )
    assert str(inspect.signature(ex.ResearchTaskMismatchError)) == (
        "(*, task_id: 'str', source_research_task_id: 'str')"
    )
    assert str(inspect.signature(ex.RPCError)) == (
        "(message: 'str', *, method_id: 'str | None' = None, "
        "raw_response: 'str | None' = None, rpc_code: 'str | int | None' = None, "
        "found_ids: 'list[str] | None' = None)"
    )
