"""Phase 3B23 canonical public model identity parity.

Upstream notebooklm-py==0.7.2 routes public dataclasses through
``notebooklm.types`` and then re-exports them at the package root. Earlier
offline phases in this repo introduced local fixture dataclasses first; this
regression closes the remaining compatibility gap for field-compatible models
without opening live RPC/auth behavior.
"""

from __future__ import annotations

import asyncio
import importlib
import typing

import pytest


def test_root_public_models_are_canonical_types(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))
    notebooklm = importlib.import_module("notebooklm")
    types_module = importlib.import_module("notebooklm.types")

    for name in [
        "Artifact",
        "AskResult",
        "ChatReference",
        "ConversationTurn",
        "Note",
        "Notebook",
        "NotebookMetadata",
        "Source",
        "SourceFulltext",
        "SourceStatus",
        "SourceSummary",
        "SourceType",
    ]:
        assert getattr(notebooklm, name) is getattr(types_module, name), name


def test_high_traffic_subclients_return_canonical_public_models(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))
    notebooklm = importlib.import_module("notebooklm")
    types_module = importlib.import_module("notebooklm.types")

    async def scenario():
        client = notebooklm.NotebookLMClient(
            notebooklm.AuthTokens(
                cookies={}, csrf_token="synthetic", session_id="synthetic"
            )
        )
        notebooks = await client.notebooks.list()
        notebook = await client.notebooks.get("fake-notebook-0001")
        metadata = await client.notebooks.get_metadata("fake-notebook-0001")
        artifacts = await client.artifacts.list("fake-notebook-0001")
        sources = await client.sources.list("fake-notebook-0001")
        source = await client.sources.get("fake-notebook-0001", "fake-source-0001")
        fulltext = await client.sources.get_fulltext(
            "fake-notebook-0001", "fake-source-0001"
        )
        source_guide = await client.sources.get_guide(
            "fake-notebook-0001", "fake-source-0001"
        )
        ask_result = await client.chat.ask(
            "fake-notebook-0001", "Phase 0 synthetic question."
        )
        note = await client.chat.save_answer_as_note(
            "fake-notebook-0001", ask_result, title="Saved"
        )

        assert notebooks and all(
            type(item) is types_module.Notebook for item in notebooks
        )
        assert type(notebook) is types_module.Notebook
        assert type(metadata) is types_module.NotebookMetadata
        assert type(metadata.notebook) is types_module.Notebook
        assert metadata.sources and all(
            type(item) is types_module.SourceSummary for item in metadata.sources
        )
        assert artifacts and all(
            type(item) is types_module.Artifact for item in artifacts
        )
        assert sources and all(type(item) is types_module.Source for item in sources)
        assert type(source) is types_module.Source
        assert type(fulltext) is types_module.SourceFulltext
        assert type(source_guide) is types_module.SourceGuide
        assert type(ask_result) is types_module.AskResult
        assert all(
            type(item) is types_module.ChatReference for item in ask_result.references
        )
        assert type(note) is types_module.Note

        # Existing offline helper affordances must survive the canonicalization.
        assert notebook.as_dict()["id"] == "fake-notebook-0001"
        assert metadata.as_dict()["notebook"]["id"] == "fake-notebook-0001"
        assert artifacts[0].as_dict()["artifact_type"]
        assert artifacts[0].kind().name
        assert artifacts[0].state().name
        assert isinstance(artifacts[0].is_completed, bool)
        assert source.as_dict()["id"] == "fake-source-0001"
        assert source.kind().name == "WEB_PAGE"
        assert fulltext.as_dict()["source_id"] == "fake-source-0001"
        assert fulltext.kind().name == "WEB_PAGE"
        assert ask_result.as_dict()["answer"]
        assert note.as_dict()["title"] == "Saved"

    asyncio.run(scenario())


def test_subclient_type_hints_resolve_to_canonical_public_models(
    repo_root, monkeypatch
):
    monkeypatch.syspath_prepend(str(repo_root))
    client_module = importlib.import_module("notebooklm.client")
    types_module = importlib.import_module("notebooklm.types")

    expected_returns = {
        ("NotebooksAPI", "list"): list[types_module.Notebook],
        ("NotebooksAPI", "get"): types_module.Notebook,
        ("NotebooksAPI", "get_metadata"): types_module.NotebookMetadata,
        ("ArtifactsAPI", "list"): list[types_module.Artifact],
        ("ArtifactsAPI", "get"): types_module.Artifact | None,
        ("SourcesAPI", "list"): list[types_module.Source],
        ("SourcesAPI", "get"): types_module.Source | None,
        ("SourcesAPI", "get_or_none"): types_module.Source | None,
        ("SourcesAPI", "get_fulltext"): types_module.SourceFulltext,
        ("SourcesAPI", "get_guide"): types_module.SourceGuide,
        ("ChatAPI", "ask"): types_module.AskResult,
        ("ChatAPI", "save_answer_as_note"): types_module.Note,
    }
    for (class_name, method_name), expected in expected_returns.items():
        cls = getattr(client_module, class_name)
        hints = typing.get_type_hints(getattr(cls, method_name))
        assert hints["return"] == expected, (class_name, method_name, hints["return"])


def test_public_model_helper_parity_preserves_existing_offline_calls(
    repo_root, monkeypatch
):
    monkeypatch.syspath_prepend(str(repo_root))
    notebooklm = importlib.import_module("notebooklm")

    artifact = notebooklm.Artifact(
        id="artifact-1",
        title="Study Guide: Synthetic",
        _artifact_type=2,
        status=3,
    )
    source = notebooklm.Source(
        id="source-1",
        title="Synthetic Source",
        url="https://example.test/source",
        _type_code=1,
        status=notebooklm.SourceStatus.READY,
    )
    fulltext = notebooklm.SourceFulltext(
        source_id="source-1",
        title="Synthetic Source",
        content="alpha cited passage omega cited passage",
        _type_code=1,
        url="https://example.test/source",
        char_count=38,
    )
    summary = notebooklm.SourceSummary(
        notebooklm.SourceType.WEB_PAGE,
        "Synthetic Source",
        "https://example.test/source",
    )
    metadata = notebooklm.NotebookMetadata(
        notebook=notebooklm.Notebook("nb-1", "Notebook"), sources=[summary]
    )

    # Upstream-style property access and existing offline call-style access both work.
    assert artifact.kind is notebooklm.ArtifactType.REPORT
    assert artifact.kind() is notebooklm.ArtifactType.REPORT
    assert artifact.status_str == "completed"
    assert artifact.is_completed is True
    assert artifact.is_processing is False
    assert artifact.is_pending is False
    assert artifact.is_failed is False
    assert artifact.report_subtype == "study_guide"

    assert source.kind is notebooklm.SourceType.WEB_PAGE
    assert source.kind() is notebooklm.SourceType.WEB_PAGE
    assert source.is_ready is True
    assert source.is_processing is False
    assert source.is_error is False
    assert fulltext.kind is notebooklm.SourceType.WEB_PAGE
    assert fulltext.kind() is notebooklm.SourceType.WEB_PAGE
    assert fulltext.find_citation_context("cited passage", context_chars=6)

    # Upstream helpers are present without removing the existing offline JSON helpers.
    assert summary.to_dict() == {
        "type": "web_page",
        "title": "Synthetic Source",
        "url": "https://example.test/source",
    }
    assert summary.as_dict()["kind"] == "WEB_PAGE"
    assert metadata.id == "nb-1"
    assert metadata.title == "Notebook"
    assert metadata.created_at is None
    assert metadata.is_owner is True
    assert metadata.to_dict() == {
        "id": "nb-1",
        "title": "Notebook",
        "created_at": None,
        "is_owner": True,
        "sources": [summary.to_dict()],
    }
    assert metadata.as_dict()["notebook"]["id"] == "nb-1"

    with pytest.raises(ValueError):
        notebooklm.ChatReference(source_id="source-1", start_char=3)
    with pytest.raises(ValueError):
        notebooklm.ChatReference(source_id="source-1", answer_end_char=1)

    artifact_from_row = notebooklm.Artifact.from_api_response(
        [
            "fake-artifact-report-0001",
            "Synthetic Briefing Doc",
            2,
            3,
            1750000600,
            None,
            None,
        ]
    )
    assert type(artifact_from_row) is notebooklm.Artifact
    assert artifact_from_row.kind is notebooklm.ArtifactType.REPORT
    assert artifact_from_row.is_completed is True

    mind_map_from_row = notebooklm.Artifact.from_mind_map(
        [
            "fake-mind-map-0001",
            [
                "fake-mind-map-0001",
                "{}",
                [1, "user", [1750000700, 0]],
                None,
                "Synthetic Mind Map",
            ],
        ]
    )
    assert type(mind_map_from_row) is notebooklm.Artifact
    assert mind_map_from_row.id == "fake-mind-map-0001"
    assert mind_map_from_row.kind is notebooklm.ArtifactType.MIND_MAP

    source_from_response = notebooklm.Source.from_api_response(
        [
            "fake-source-0001",
            "Synthetic Web Source",
            "https://example.test/notebooklm-bare/source",
            1,
            1750000100,
            2,
        ]
    )
    assert type(source_from_response) is notebooklm.Source
    assert source_from_response.kind is notebooklm.SourceType.WEB_PAGE
    assert source_from_response.is_ready is True

    class _Row:
        id = "fake-source-row"
        title = "Synthetic Row"
        url = None
        type_code = 2
        created_at = None
        status = notebooklm.SourceStatus.READY

    source_from_row = notebooklm.Source.from_row(_Row())
    assert type(source_from_row) is notebooklm.Source
    assert source_from_row.id == "fake-source-row"
    assert source_from_row.kind is notebooklm.SourceType.PASTED_TEXT
