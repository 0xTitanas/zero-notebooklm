"""Phase 3B13 offline ``research wait --import-all`` CLI parity.

This batch promotes the pinned notebooklm-py==0.7.2 research wait source-
import flags over deterministic read-only research fixtures and the existing
in-memory source service. It does not start live NotebookLM research, perform
live RPC/network calls, read auth/browser/home state, or mutate real NotebookLM
data.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

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


@pytest.fixture()
def mods(repo_root, monkeypatch):
    _poison_home(monkeypatch)
    return _load(repo_root, monkeypatch)


def test_cli_research_wait_import_all_imports_fixture_sources(mods, capsys):
    code, out, err = _run(
        mods,
        capsys,
        ["research", "wait", "-n", SYNTHETIC_NOTEBOOK_ID, "--import-all", "--json"],
    )

    assert code == 0
    assert err == ""
    payload = json.loads(out)
    assert payload["status"] == "completed"
    assert payload["query"] == "Synthetic NotebookLM parity research"
    assert payload["sources_found"] == 1
    assert payload["imported"] == 1
    assert payload["imported_sources"] == [
        {"id": "offline-source-0001", "title": "Synthetic research source one"}
    ]
    assert "cited_only" not in payload


def test_cli_research_wait_cited_only_falls_back_when_report_has_no_citations(
    mods, capsys
):
    code, out, err = _run(
        mods,
        capsys,
        [
            "research",
            "wait",
            "-n",
            SYNTHETIC_NOTEBOOK_ID,
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


def test_cli_research_wait_cited_only_requires_import_all(mods, capsys):
    code, out, err = _run(
        mods,
        capsys,
        ["research", "wait", "-n", SYNTHETIC_NOTEBOOK_ID, "--cited-only", "--json"],
    )

    assert code == 64
    assert out == ""
    assert "--cited-only requires --import-all" in err


def test_cli_research_wait_import_all_preserves_timeout_boundary(
    mods, capsys, tmp_path
):
    fixture = tmp_path / "research_status.json"
    fixture.write_text(
        json.dumps(
            {
                "research_tasks": {
                    SYNTHETIC_NOTEBOOK_ID: [
                        {
                            "task_id": "fake-research-in-progress-0001",
                            "status": "in_progress",
                            "query": "still running",
                            "sources": [],
                            "summary": "",
                            "report": "",
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    code, out, err = _run(
        mods,
        capsys,
        [
            "research",
            "wait",
            "-n",
            SYNTHETIC_NOTEBOOK_ID,
            "--import-all",
            "--json",
            "--status-fixture",
            str(fixture),
        ],
    )

    assert code == 1
    assert err == ""
    payload = json.loads(out)
    assert payload == {"status": "timeout", "error": "Timed out after 300s"}


def test_cli_research_wait_import_all_preserves_no_research_boundary(
    mods, capsys, tmp_path
):
    fixture = tmp_path / "research_status.json"
    fixture.write_text(
        json.dumps({"research_tasks": {SYNTHETIC_NOTEBOOK_ID: []}}),
        encoding="utf-8",
    )

    code, out, err = _run(
        mods,
        capsys,
        [
            "research",
            "wait",
            "-n",
            SYNTHETIC_NOTEBOOK_ID,
            "--import-all",
            "--json",
            "--status-fixture",
            str(fixture),
        ],
    )

    assert code == 1
    assert err == ""
    payload = json.loads(out)
    assert payload == {"status": "no_research", "error": "No research running"}
