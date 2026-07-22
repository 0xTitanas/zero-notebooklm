"""Parity tests for the public chat citation resolver helper."""

from __future__ import annotations

import asyncio
import importlib
import inspect

import pytest


class _FakeSources:
    def __init__(self, fulltext):
        self.fulltext = fulltext
        self.calls: list[tuple[str, str]] = []

    async def get_fulltext(self, notebook_id: str, source_id: str):
        self.calls.append((notebook_id, source_id))
        return self.fulltext


class _FakeClient:
    def __init__(self, fulltext):
        self.sources = _FakeSources(fulltext)


def test_resolve_chat_reference_passage_matches_upstream_helper(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))
    notebooklm = importlib.import_module("notebooklm")
    utils = importlib.import_module("notebooklm.utils")

    cited = "the principle of least action"
    content = "A" * 80 + " " + cited + " " + "B" * 80
    fulltext = notebooklm.SourceFulltext(
        source_id="src-action",
        title="Variational Principles",
        content=content,
    )
    client = _FakeClient(fulltext)
    reference = notebooklm.ChatReference(source_id="src-action", cited_text=cited)

    assert inspect.iscoroutinefunction(utils.resolve_chat_reference_passage)
    passage = asyncio.run(
        notebooklm.resolve_chat_reference_passage(
            client, "nb-action", reference, context_chars=20
        )
    )

    assert client.sources.calls == [("nb-action", "src-action")]
    assert cited in passage
    assert len(passage) < len(content)


def test_resolve_chat_reference_passage_raises_without_cited_text(
    repo_root, monkeypatch
):
    monkeypatch.syspath_prepend(str(repo_root))
    notebooklm = importlib.import_module("notebooklm")
    errors = importlib.import_module("notebooklm.exceptions")
    client = _FakeClient(None)
    reference = notebooklm.ChatReference(source_id="src-anchor", cited_text=None)

    with pytest.raises(errors.ChatResponseParseError, match="no cited_text"):
        asyncio.run(
            notebooklm.resolve_chat_reference_passage(client, "nb", reference)
        )

    assert client.sources.calls == []


def test_resolve_chat_reference_passage_raises_when_source_text_misses_citation(
    repo_root, monkeypatch
):
    monkeypatch.syspath_prepend(str(repo_root))
    notebooklm = importlib.import_module("notebooklm")
    errors = importlib.import_module("notebooklm.exceptions")
    fulltext = notebooklm.SourceFulltext(
        source_id="src-other",
        title="Other",
        content="This document is about cooking pasta.",
    )
    client = _FakeClient(fulltext)
    reference = notebooklm.ChatReference(
        source_id="src-other",
        cited_text="quantum entanglement permits non-classical correlations",
    )

    with pytest.raises(errors.ChatResponseParseError, match="Could not locate"):
        asyncio.run(
            notebooklm.resolve_chat_reference_passage(client, "nb-other", reference)
        )

    assert client.sources.calls == [("nb-other", "src-other")]
