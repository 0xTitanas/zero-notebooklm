"""NOTEBOOKLM_FUTURE_ERRORS get() parity for optional lookups."""

from __future__ import annotations

import asyncio
import warnings
from pathlib import Path


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


async def _missing_gets(client):
    return {
        "source": await client.sources.get(SYNTHETIC_NOTEBOOK_ID, "missing-source"),
        "artifact": await client.artifacts.get(SYNTHETIC_NOTEBOOK_ID, "missing-artifact"),
        "note": await client.notes.get(SYNTHETIC_NOTEBOOK_ID, "missing-note"),
        "mind_map": await client.mind_maps.get(SYNTHETIC_NOTEBOOK_ID, "missing-map"),
    }


def test_get_missing_warns_and_returns_none_by_default(monkeypatch):
    _poison_home(monkeypatch)
    client = _client()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        result = asyncio.run(_missing_gets(client))

    assert result == {
        "source": None,
        "artifact": None,
        "note": None,
        "mind_map": None,
    }
    messages = [str(w.message) for w in caught]
    assert len(messages) == 4
    assert any("sources.get() returning None for a missing source" in m for m in messages)
    assert any("artifacts.get() returning None for a missing artifact" in m for m in messages)
    assert any("notes.get() returning None for a missing note" in m for m in messages)
    assert any("mind_maps.get() returning None for a missing mind_map" in m for m in messages)


def test_get_missing_quiet_env_suppresses_warning(monkeypatch):
    _poison_home(monkeypatch)
    monkeypatch.setenv("NOTEBOOKLM_QUIET_DEPRECATIONS", "yes")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        result = asyncio.run(_missing_gets(_client()))

    assert set(result.values()) == {None}
    assert caught == []


def test_get_missing_future_errors_raise_matching_not_found(monkeypatch):
    _poison_home(monkeypatch)
    monkeypatch.setenv("NOTEBOOKLM_QUIET_DEPRECATIONS", "1")
    monkeypatch.setenv("NOTEBOOKLM_FUTURE_ERRORS", "on")

    async def scenario():
        from notebooklm import (
            ArtifactNotFoundError,
            MindMapNotFoundError,
            NoteNotFoundError,
            SourceNotFoundError,
        )

        client = _client()
        checks = [
            (client.sources.get, "missing-source", SourceNotFoundError),
            (client.artifacts.get, "missing-artifact", ArtifactNotFoundError),
            (client.notes.get, "missing-note", NoteNotFoundError),
            (client.mind_maps.get, "missing-map", MindMapNotFoundError),
        ]
        for getter, missing_id, expected in checks:
            try:
                await getter(SYNTHETIC_NOTEBOOK_ID, missing_id)
            except expected as exc:
                assert missing_id in str(exc)
            else:  # pragma: no cover - assertion path
                raise AssertionError(f"{expected.__name__} was not raised")

    asyncio.run(scenario())


def test_get_or_none_missing_remains_silent_under_future_errors(monkeypatch):
    _poison_home(monkeypatch)
    monkeypatch.setenv("NOTEBOOKLM_FUTURE_ERRORS", "1")
    client = _client()

    async def scenario():
        return [
            await client.sources.get_or_none(SYNTHETIC_NOTEBOOK_ID, "missing-source"),
            await client.artifacts.get_or_none(
                SYNTHETIC_NOTEBOOK_ID, "missing-artifact"
            ),
            await client.notes.get_or_none(SYNTHETIC_NOTEBOOK_ID, "missing-note"),
            await client.mind_maps.get_or_none(SYNTHETIC_NOTEBOOK_ID, "missing-map"),
        ]

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        assert asyncio.run(scenario()) == [None, None, None, None]
    assert caught == []
