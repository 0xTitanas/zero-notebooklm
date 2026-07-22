"""Phase 3B10 fixture-backed ResearchAPI start/import parity batch.

This batch promotes the Python API research start/import helpers that can
be represented safely with deterministic in-memory state. Later Phase 3B13
promotes the pinned CLI ``research wait --import-all`` source-import flags over
those same offline services. It does not start live NotebookLM research, import
from real NotebookLM, read browser/auth/home state, or mutate real data.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any

import pytest

from notebooklm.auth import AuthTokens

SYNTHETIC_NOTEBOOK_ID = "fake-notebook-0001"


def _client():
    from notebooklm.client import NotebookLMClient

    return NotebookLMClient(AuthTokens(cookies={}, csrf_token="", session_id=""))


def test_research_start_and_import_signature_shape_matches_reference():
    client = _client()

    start_sig = inspect.signature(client.research.start)
    assert list(start_sig.parameters) == ["notebook_id", "query", "source", "mode"]
    assert start_sig.parameters["source"].default == "web"
    assert start_sig.parameters["mode"].default == "fast"

    import_sig = inspect.signature(client.research.import_sources)
    assert list(import_sig.parameters) == ["notebook_id", "task_id", "sources"]

    verified_sig = inspect.signature(client.research.import_sources_with_verification)
    assert list(verified_sig.parameters) == [
        "notebook_id",
        "task_id",
        "sources",
        "max_elapsed",
        "initial_delay",
        "backoff_factor",
        "max_delay",
    ]
    assert verified_sig.parameters["max_elapsed"].default == 1800
    assert verified_sig.parameters["initial_delay"].default == 5
    assert verified_sig.parameters["backoff_factor"].default == 2
    assert verified_sig.parameters["max_delay"].default == 60


def test_research_start_is_deterministic_and_pollable_without_live_rpc():
    from notebooklm.rpc.types import ResearchStatus
    from notebooklm.types import ResearchStart

    client = _client()

    fast = asyncio.run(
        client.research.start(SYNTHETIC_NOTEBOOK_ID, "synthetic web query")
    )
    assert isinstance(fast, ResearchStart)
    assert fast.task_id == "offline-research-0001"
    assert fast.report_id is None
    assert fast.notebook_id == SYNTHETIC_NOTEBOOK_ID
    assert fast.query == "synthetic web query"
    assert fast.mode == "fast"

    deep = asyncio.run(
        client.research.start(
            SYNTHETIC_NOTEBOOK_ID, "synthetic deep query", mode="deep"
        )
    )
    assert deep.task_id == "offline-research-0002"
    assert deep.report_id == "offline-report-0002"
    assert deep.mode == "deep"

    polled = asyncio.run(client.research.poll(SYNTHETIC_NOTEBOOK_ID, fast.task_id))
    assert polled.task_id == fast.task_id
    assert polled.status is ResearchStatus.IN_PROGRESS
    assert polled.query == "synthetic web query"
    assert [task.task_id for task in polled.tasks] == [fast.task_id]


def test_research_start_validates_reference_source_and_mode_rules():
    from notebooklm.errors import ValidationError

    client = _client()

    bad_source: Any = object()
    bad_mode: Any = object()
    with pytest.raises(ValidationError, match="source must be text"):
        asyncio.run(
            client.research.start(SYNTHETIC_NOTEBOOK_ID, "q", source=bad_source)
        )
    with pytest.raises(ValidationError, match="mode must be text"):
        asyncio.run(client.research.start(SYNTHETIC_NOTEBOOK_ID, "q", mode=bad_mode))
    with pytest.raises(ValidationError, match="Invalid source"):
        asyncio.run(client.research.start(SYNTHETIC_NOTEBOOK_ID, "q", source="rss"))
    with pytest.raises(ValidationError, match="Invalid mode"):
        asyncio.run(client.research.start(SYNTHETIC_NOTEBOOK_ID, "q", mode="slow"))
    with pytest.raises(
        ValidationError, match="Deep Research only supports Web sources"
    ):
        asyncio.run(
            client.research.start(
                SYNTHETIC_NOTEBOOK_ID,
                "q",
                source="drive",
                mode="deep",
            )
        )


def test_research_import_sources_uses_shared_source_store_and_skips_unimportable_rows():
    from notebooklm.types import ResearchSource

    client = _client()
    task = asyncio.run(
        client.research.start(SYNTHETIC_NOTEBOOK_ID, "import candidates")
    )
    before = asyncio.run(client.sources.list(SYNTHETIC_NOTEBOOK_ID))

    imported = asyncio.run(
        client.research.import_sources(
            SYNTHETIC_NOTEBOOK_ID,
            task.task_id,
            [
                {
                    "url": "https://example.test/research/alpha",
                    "title": "Research Alpha",
                    "research_task_id": task.task_id,
                },
                ResearchSource(
                    url="",
                    title="Research Report",
                    result_type=5,
                    research_task_id=task.task_id,
                    report_markdown="# Research Report\n\nSynthetic report body.",
                ),
                {"title": "Missing URL is skipped"},
            ],
        )
    )

    assert imported == [
        {"id": "offline-source-0001", "title": "Research Report"},
        {"id": "offline-source-0002", "title": "Research Alpha"},
    ]
    after = asyncio.run(client.sources.list(SYNTHETIC_NOTEBOOK_ID))
    assert len(after) == len(before) + 2
    created = {
        source.id: source for source in after if source.id.startswith("offline-source-")
    }
    assert created["offline-source-0001"].url is None
    assert created["offline-source-0001"].title == "Research Report"
    assert created["offline-source-0002"].url == "https://example.test/research/alpha"
    assert created["offline-source-0002"].title == "Research Alpha"


def test_research_import_sources_normalizes_public_dicts_like_upstream():
    client = _client()
    task = asyncio.run(
        client.research.start(SYNTHETIC_NOTEBOOK_ID, "public dict candidates")
    )

    imported = asyncio.run(
        client.research.import_sources(
            SYNTHETIC_NOTEBOOK_ID,
            task.task_id,
            [
                {"url": 42, "title": None, "research_task_id": task.task_id},
                {
                    "url": "https://example.test/research/beta",
                    "title": None,
                    "result_type": "web",
                    "research_task_id": task.task_id,
                },
                {
                    "title": "Research Report",
                    "result_type": "report",
                    "research_task_id": task.task_id,
                    "report_markdown": "# Research Report",
                },
                {
                    "result_type": "report",
                    "research_task_id": task.task_id,
                    "report_markdown": "# Missing raw title",
                },
            ],
        )
    )

    assert imported == [
        {"id": "offline-source-0001", "title": "Research Report"},
        {"id": "offline-source-0002", "title": "Untitled"},
    ]


def test_research_import_sources_rejects_mismatched_task_id():
    from notebooklm import ResearchTaskMismatchError
    from notebooklm.types import ResearchSource

    client = _client()
    task = asyncio.run(
        client.research.start(SYNTHETIC_NOTEBOOK_ID, "import candidates")
    )

    with pytest.raises(ResearchTaskMismatchError) as exc_info:
        asyncio.run(
            client.research.import_sources(
                SYNTHETIC_NOTEBOOK_ID,
                task.task_id,
                [
                    ResearchSource(
                        url="https://example.test/wrong-task",
                        title="Wrong task",
                        research_task_id="other-task",
                    )
                ],
            )
        )
    assert exc_info.value.task_id == task.task_id
    assert exc_info.value.source_research_task_id == "other-task"


def test_research_task_state_is_notebook_scoped_for_poll_and_import():
    from notebooklm.errors import ValidationError
    from notebooklm.rpc.types import ResearchStatus

    client = _client()
    task = asyncio.run(client.research.start(SYNTHETIC_NOTEBOOK_ID, "scoped task"))
    assert task is not None

    wrong_notebook_poll = asyncio.run(
        client.research.poll("other-notebook", task.task_id)
    )
    assert wrong_notebook_poll.status is ResearchStatus.NOT_FOUND
    assert wrong_notebook_poll.task_id == task.task_id

    with pytest.raises(ValidationError, match="does not belong to notebook"):
        asyncio.run(
            client.research.import_sources(
                "other-notebook",
                task.task_id,
                [
                    {
                        "url": "https://example.test/research/wrong-notebook",
                        "title": "Wrong Notebook",
                        "research_task_id": task.task_id,
                    }
                ],
            )
        )


def test_research_import_sources_with_verification_delegates_successful_import():
    client = _client()
    task = asyncio.run(client.research.start(SYNTHETIC_NOTEBOOK_ID, "verified import"))

    imported = asyncio.run(
        client.research.import_sources_with_verification(
            SYNTHETIC_NOTEBOOK_ID,
            task.task_id,
            [
                {
                    "url": "https://example.test/research/verified",
                    "title": "Verified Research Source",
                    "research_task_id": task.task_id,
                }
            ],
            max_elapsed=0,
            initial_delay=0,
            backoff_factor=1,
            max_delay=0,
        )
    )

    assert imported == [
        {"id": "offline-source-0001", "title": "Verified Research Source"}
    ]
    created = asyncio.run(
        client.sources.get(SYNTHETIC_NOTEBOOK_ID, "offline-source-0001")
    )
    assert created is not None
    assert created.url == "https://example.test/research/verified"


def test_research_import_sources_with_verification_treats_timeout_committed_url_as_success(
    monkeypatch,
):
    from notebooklm import RPCTimeoutError

    client = _client()
    task = asyncio.run(client.research.start(SYNTHETIC_NOTEBOOK_ID, "timeout import"))
    calls = 0

    async def timed_out_import(notebook_id, task_id, sources):
        nonlocal calls
        calls += 1
        created = await client.sources.add_url(
            notebook_id, "https://example.test/research/beta?q=1"
        )
        created.title = "Committed Beta"
        raise RPCTimeoutError("IMPORT_RESEARCH timed out", timeout_seconds=30)

    monkeypatch.setattr(client.research, "import_sources", timed_out_import)

    imported = asyncio.run(
        client.research.import_sources_with_verification(
            SYNTHETIC_NOTEBOOK_ID,
            task.task_id,
            [
                {
                    "url": "HTTPS://Example.TEST/research/beta/?q=1#ignored",
                    "title": "Beta",
                    "research_task_id": task.task_id,
                }
            ],
            max_elapsed=30,
            initial_delay=0,
        )
    )

    assert calls == 1
    assert imported == [{"id": "offline-source-0001", "title": "Committed Beta"}]


def test_research_cli_source_import_is_fixture_backed_after_cli_promotion(
    repo_root, monkeypatch, capsys
):
    monkeypatch.syspath_prepend(str(repo_root))
    from notebooklm import cli

    code = cli.console(
        ["research", "wait", "-n", SYNTHETIC_NOTEBOOK_ID, "--import-all", "--json"]
    )

    captured = capsys.readouterr()
    assert code == 0
    assert captured.err == ""
    assert json.loads(captured.out)["imported"] == 1
