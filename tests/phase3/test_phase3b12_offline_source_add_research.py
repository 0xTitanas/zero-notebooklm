"""Phase 3B12 offline ``source add-research`` CLI parity.

This batch promotes the pinned notebooklm-py==0.7.2 ``source add-research``
leaf over the deterministic in-memory research/source services. Later Phase
3B13 promotes ``research wait --import-all`` over the same offline services. It
does not start live NotebookLM research, import from real NotebookLM, or read
auth/browser/home state.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from notebooklm.auth import AuthTokens

SYNTHETIC_NOTEBOOK_ID = "fake-notebook-0001"


def _poison_home(monkeypatch):
    monkeypatch.setattr(
        Path,
        "home",
        staticmethod(lambda: (_ for _ in ()).throw(AssertionError("real home access"))),
    )


def _load(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))
    return SimpleNamespace(cli=importlib.import_module("notebooklm.cli"))


def _run(mods, capsys, argv):
    code = mods.cli.console(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _client():
    from notebooklm.client import NotebookLMClient

    return NotebookLMClient(AuthTokens(cookies={}, csrf_token="", session_id=""))


@pytest.fixture()
def mods(repo_root, monkeypatch):
    _poison_home(monkeypatch)
    return _load(repo_root, monkeypatch)


def test_research_started_tasks_complete_with_task_scoped_synthetic_sources():
    from notebooklm.rpc.types import ResearchStatus

    client = _client()
    start = asyncio.run(
        client.research.start(
            SYNTHETIC_NOTEBOOK_ID, "edge agent research", source="web", mode="fast"
        )
    )
    assert start is not None

    completed = asyncio.run(
        client.research.wait_for_completion(SYNTHETIC_NOTEBOOK_ID, start.task_id)
    )

    assert completed.task_id == start.task_id
    assert completed.status is ResearchStatus.COMPLETED
    assert completed.query == "edge agent research"
    assert completed.report.startswith("# Synthetic Research Report")
    assert len(completed.sources) == 1
    assert completed.sources[0].research_task_id == start.task_id
    assert (
        completed.sources[0].url
        == "https://example.test/notebooklm-bare/research/offline-research-0001"
    )
    assert [task.task_id for task in completed.tasks] == [start.task_id]


def test_cli_source_add_research_no_wait_starts_without_import(mods, capsys):
    code, out, err = _run(
        mods,
        capsys,
        [
            "source",
            "add-research",
            "synthetic no wait query",
            "--no-wait",
            "--json",
        ],
    )

    assert code == 0
    assert err == ""
    payload = json.loads(out)
    assert payload == {"status": "started", "task_id": "offline-research-0001"}


def test_cli_source_add_research_waits_and_renders_completed_fixture(mods, capsys):
    code, out, err = _run(
        mods,
        capsys,
        [
            "source",
            "add-research",
            "synthetic completed query",
            "--from",
            "web",
            "--mode",
            "fast",
            "--timeout",
            "1",
            "--json",
        ],
    )

    assert code == 0
    assert err == ""
    payload = json.loads(out)
    assert payload["status"] == "completed"
    assert payload["task_id"] == "offline-research-0001"
    assert payload["sources_found"] == 1
    assert payload["sources"][0]["research_task_id"] == "offline-research-0001"
    assert (
        payload["sources"][0]["title"]
        == "Synthetic research result for synthetic completed query"
    )
    assert payload["report"].startswith("# Synthetic Research Report")
    assert "imported" not in payload


def test_cli_source_add_research_deep_mode_polls_report_id(mods, capsys):
    code, out, err = _run(
        mods,
        capsys,
        ["source", "add-research", "synthetic deep query", "--mode", "deep", "--json"],
    )

    assert code == 0
    assert err == ""
    payload = json.loads(out)
    assert payload["status"] == "completed"
    assert payload["task_id"] == "offline-report-0001"
    assert payload["sources"][0]["research_task_id"] == "offline-report-0001"


def test_cli_source_add_research_import_all_uses_shared_source_store(mods, capsys):
    code, out, err = _run(
        mods,
        capsys,
        [
            "source",
            "add-research",
            "synthetic import query",
            "--import-all",
            "--json",
        ],
    )

    assert code == 0
    assert err == ""
    payload = json.loads(out)
    assert payload["status"] == "completed"
    assert payload["task_id"] == "offline-research-0001"
    assert payload["imported"] == 1
    assert payload["imported_sources"] == [
        {
            "id": "offline-source-0001",
            "title": "Synthetic research result for synthetic import query",
        }
    ]


def test_cli_source_add_research_cited_only_falls_back_when_report_has_no_citations(
    mods, capsys
):
    code, out, err = _run(
        mods,
        capsys,
        [
            "source",
            "add-research",
            "synthetic cited query",
            "--import-all",
            "--cited-only",
            "--json",
        ],
    )

    assert code == 0
    assert err == ""
    payload = json.loads(out)
    assert payload["status"] == "completed"
    assert payload["imported"] == 1
    assert payload["cited_only"] is True
    assert payload["cited_sources_selected"] == 1
    assert payload["cited_only_fallback"] is True


def test_cli_source_add_research_prompt_file_stdin_and_conflicts(
    mods, capsys, monkeypatch, tmp_path
):
    monkeypatch.setattr(sys, "stdin", StringIO("stdin research query\n"))
    code, out, err = _run(
        mods,
        capsys,
        ["source", "add-research", "--prompt-file", "-", "--no-wait", "--json"],
    )
    assert code == 0
    assert err == ""
    assert json.loads(out)["task_id"] == "offline-research-0001"

    monkeypatch.setattr(sys, "stdin", StringIO("positional stdin research query\n"))
    code, out, err = _run(
        mods,
        capsys,
        ["source", "add-research", "-", "--no-wait", "--json"],
    )
    assert code == 0
    assert err == ""
    assert json.loads(out)["task_id"] == "offline-research-0001"

    prompt_file = tmp_path / "query.txt"
    prompt_file.write_text("file research query\n", encoding="utf-8")
    code, out, err = _run(
        mods,
        capsys,
        [
            "source",
            "add-research",
            "--prompt-file",
            str(prompt_file),
            "--no-wait",
            "--json",
        ],
    )
    assert code == 0
    assert err == ""
    assert json.loads(out)["task_id"] == "offline-research-0001"

    code, out, err = _run(
        mods,
        capsys,
        [
            "source",
            "add-research",
            "query",
            "--prompt-file",
            str(prompt_file),
            "--no-wait",
            "--json",
        ],
    )
    assert code == 64
    assert out == ""
    assert "mutually exclusive" in err

    code, out, err = _run(
        mods,
        capsys,
        ["source", "add-research", "query", "--cited-only", "--json"],
    )
    assert code == 64
    assert out == ""
    assert "--cited-only requires --import-all" in err

    code, out, err = _run(
        mods,
        capsys,
        ["source", "add-research", "query", "--no-wait", "--import-all", "--json"],
    )
    assert code == 64
    assert out == ""
    assert "--import-all requires --wait" in err


def test_cli_source_add_research_allows_wait_import_after_3b13(mods, capsys):
    code, out, err = _run(
        mods,
        capsys,
        ["research", "wait", "-n", SYNTHETIC_NOTEBOOK_ID, "--import-all", "--json"],
    )

    assert code == 0
    assert err == ""
    assert json.loads(out)["imported"] == 1
