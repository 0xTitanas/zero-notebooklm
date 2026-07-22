"""NOTEBOOKLM_FUTURE_ERRORS parity for mutation/boolean runways."""

from __future__ import annotations

import asyncio
from pathlib import Path

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


def test_missing_mutations_keep_v07_soft_success_by_default(monkeypatch):
    _poison_home(monkeypatch)
    client = _client()

    async def scenario():
        assert (
            await client.notes.update(
                SYNTHETIC_NOTEBOOK_ID, "missing-note", "body", "title"
            )
            is None
        )
        assert (
            await client.sources.rename(
                SYNTHETIC_NOTEBOOK_ID,
                "missing-source",
                "title",
                return_object=False,
            )
            is None
        )
        assert (
            await client.artifacts.rename(
                SYNTHETIC_NOTEBOOK_ID,
                "missing-artifact",
                "title",
                return_object=False,
            )
            is None
        )
        assert (
            await client.chat.delete_conversation(
                SYNTHETIC_NOTEBOOK_ID, "missing-conversation"
            )
            is True
        )

    asyncio.run(scenario())


def test_future_errors_missing_mutations_raise_matching_not_found(monkeypatch):
    _poison_home(monkeypatch)
    monkeypatch.setenv("NOTEBOOKLM_FUTURE_ERRORS", "true")

    async def scenario():
        from notebooklm import (
            ArtifactNotFoundError,
            NoteNotFoundError,
            SourceNotFoundError,
        )

        client = _client()
        with pytest.raises(NoteNotFoundError):
            await client.notes.update(
                SYNTHETIC_NOTEBOOK_ID, "missing-note", "body", "title"
            )
        with pytest.raises(SourceNotFoundError):
            await client.sources.rename(
                SYNTHETIC_NOTEBOOK_ID,
                "missing-source",
                "title",
                return_object=False,
            )
        with pytest.raises(ArtifactNotFoundError):
            await client.artifacts.rename(
                SYNTHETIC_NOTEBOOK_ID,
                "missing-artifact",
                "title",
                return_object=False,
            )

    asyncio.run(scenario())


def test_future_errors_uninformative_success_returns_none(monkeypatch):
    _poison_home(monkeypatch)
    monkeypatch.setenv("NOTEBOOKLM_FUTURE_ERRORS", "on")
    client = _client()

    async def scenario():
        source = await client.sources.add_text(
            SYNTHETIC_NOTEBOOK_ID, "refresh me", "synthetic"
        )
        answer = await client.chat.ask(SYNTHETIC_NOTEBOOK_ID, "Phase 0 synthetic question.")
        assert await client.sources.refresh(SYNTHETIC_NOTEBOOK_ID, source.id) is None
        assert (
            await client.chat.delete_conversation(
                SYNTHETIC_NOTEBOOK_ID, answer.conversation_id
            )
            is None
        )

    asyncio.run(scenario())


def test_future_errors_generation_missing_id_raises(monkeypatch):
    monkeypatch.setenv("NOTEBOOKLM_FUTURE_ERRORS", "1")

    from notebooklm import ArtifactFeatureUnavailableError
    from notebooklm._artifacts_impl import _parse_generation_result

    with pytest.raises(ArtifactFeatureUnavailableError):
        _parse_generation_result([[]], method_id="test-method")
