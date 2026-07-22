"""Batch 3B1 offline/read-only status behavior parity tests.

This grouped batch promotes fixture-backed read/status surfaces: language
list/get, artifact poll/wait/suggestions, research status/wait, and share status.
Batch 3B5 extends the language CLI group with fixture-backed output-language set
semantics over the existing in-memory settings seam. It does not promote real
NotebookLM mutation, downloads/exports, public sharing changes, live RPC,
browser/auth/home reads, or credential access.
"""

from __future__ import annotations

import asyncio
import inspect
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SYNTHETIC_NOTEBOOK_ID = "fake-notebook-0001"
COMPLETED_ARTIFACT_ID = "fake-artifact-audio-0001"
PENDING_ARTIFACT_ID = "fake-artifact-quiz-0001"
FAILED_ARTIFACT_ID = "fake-artifact-failed-0001"
COMPLETED_RESEARCH_ID = "fake-research-complete-0001"
RUNNING_RESEARCH_ID = "fake-research-running-0001"


def _load(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))
    return SimpleNamespace(cli=importlib.import_module("notebooklm.cli"))


def _run(mods, capsys, argv):
    code = mods.cli.console(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _poison_home(monkeypatch):
    monkeypatch.setattr(
        Path,
        "home",
        staticmethod(lambda: (_ for _ in ()).throw(AssertionError("real home access"))),
    )


def _json(out: str):
    return json.loads(out)


def test_phase3b1_fixture_exists_is_sanitized_and_has_expected_shape(repo_root):
    path = (
        repo_root
        / "compat"
        / "offline_status_fixtures"
        / "phase3b1_readonly_status.json"
    )
    data = json.loads(path.read_text(encoding="utf-8"))

    assert data["schema_version"] == 1
    assert data["settings"]["output_language"] == "ja"
    assert COMPLETED_ARTIFACT_ID in data["artifact_statuses"][SYNTHETIC_NOTEBOOK_ID]
    assert (
        data["research_tasks"][SYNTHETIC_NOTEBOOK_ID][0]["task_id"]
        == COMPLETED_RESEARCH_ID
    )
    assert data["share_statuses"][SYNTHETIC_NOTEBOOK_ID]["is_public"] is False
    raw = path.read_text(encoding="utf-8")
    assert ("ya" + "29.") not in raw
    assert "/".join(("", "Users", "")) not in raw
    assert ("notebooklm" + ".google.com") not in raw


def test_language_list_matches_upstream_supported_language_table_without_home(
    repo_root, monkeypatch, capsys
):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)

    code, out, err = _run(mods, capsys, ["language", "list", "--json"])

    assert code == 0
    assert err == ""
    payload = _json(out)
    languages = payload["languages"]
    assert len(languages) == 81
    assert list(languages)[:5] == ["en", "zh_Hans", "zh_Hant", "es", "es_419"]
    assert languages["ja"] == "日本語"
    assert languages["mai"] == "मैथिली"


def test_language_get_reads_committed_settings_fixture_without_home(
    repo_root, monkeypatch, capsys
):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)

    code, out, err = _run(mods, capsys, ["language", "get", "--json"])

    assert code == 0
    assert err == ""
    assert _json(out) == {
        "is_default": False,
        "language": "ja",
        "name": "日本語",
        "synced_from_server": True,
    }


def test_language_set_uses_fixture_backed_settings_without_home(
    repo_root, monkeypatch, capsys
):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)

    code, out, err = _run(mods, capsys, ["language", "get", "--local", "--json"])
    assert code == 0
    assert err == ""
    assert _json(out) == {
        "is_default": False,
        "language": "ja",
        "name": "日本語",
        "synced_from_server": False,
    }

    for argv, synced in (
        (["language", "set", "es", "--json"], True),
        (["language", "set", "zh_Hans", "--local", "--json"], False),
    ):
        code, out, err = _run(mods, capsys, argv)
        assert code == 0
        assert err == ""
        payload = _json(out)
        assert payload == {
            "language": argv[2],
            "message": "Language set successfully",
            "name": mods.cli._offline_status.language_name(argv[2]),
            "synced_to_server": synced,
        }

    code, out, err = _run(mods, capsys, ["language", "set", "not-a-language", "--json"])
    assert code == 64
    assert out == ""
    assert "unknown language code" in err


def test_settings_api_reads_language_limits_and_tier_from_fixture_without_home(
    repo_root, monkeypatch
):
    _poison_home(monkeypatch)
    monkeypatch.syspath_prepend(str(repo_root))
    from notebooklm import AuthTokens, NotebookLMClient

    client = NotebookLMClient(AuthTokens(cookies={}, csrf_token="", session_id=""))

    assert asyncio.run(client.settings.get_output_language()) == "ja"
    limits = asyncio.run(client.settings.get_account_limits())
    assert limits.notebook_limit == 100
    assert limits.source_limit == 50
    assert limits.raw_limits == (None, 100, 50)
    tier = asyncio.run(client.settings.get_account_tier())
    assert tier.tier == "NOTEBOOKLM_TIER_STANDARD"
    assert tier.plan_name == "Standard"


def test_promoted_api_method_signatures_keep_upstream_0_7_2_shapes(
    repo_root, monkeypatch
):
    _poison_home(monkeypatch)
    monkeypatch.syspath_prepend(str(repo_root))
    from notebooklm import AuthTokens, NotebookLMClient

    client = NotebookLMClient(AuthTokens(cookies={}, csrf_token="", session_id=""))

    assert str(inspect.signature(client.research.poll)) == (
        "(notebook_id: 'str', task_id: 'str | None' = None) -> 'ResearchTask'"
    )
    assert [
        (name, param.kind.name)
        for name, param in inspect.signature(
            client.research.wait_for_completion
        ).parameters.items()
    ] == [
        ("notebook_id", "POSITIONAL_OR_KEYWORD"),
        ("task_id", "POSITIONAL_OR_KEYWORD"),
        ("timeout", "KEYWORD_ONLY"),
        ("interval", "KEYWORD_ONLY"),
        ("initial_interval", "KEYWORD_ONLY"),
    ]
    assert str(inspect.signature(client.artifacts.wait_for_completion)) == (
        "(notebook_id: 'str', task_id: 'str', initial_interval: 'float' = 2.0, "
        "max_interval: 'float' = 10.0, timeout: 'float' = 300.0, max_not_found: 'int' = 5, "
        "min_not_found_window: 'float' = 10.0, on_status_change: "
        "'Callable[[GenerationStatus], object] | None' = None) -> 'GenerationStatus'"
    )


def test_artifact_status_and_suggestions_api_use_fixture_without_home(
    repo_root, monkeypatch
):
    _poison_home(monkeypatch)
    monkeypatch.syspath_prepend(str(repo_root))
    from notebooklm import AuthTokens, NotebookLMClient

    client = NotebookLMClient(AuthTokens(cookies={}, csrf_token="", session_id=""))

    completed = asyncio.run(
        client.artifacts.poll_status(SYNTHETIC_NOTEBOOK_ID, COMPLETED_ARTIFACT_ID)
    )
    assert completed.task_id == COMPLETED_ARTIFACT_ID
    assert completed.status == "completed"
    assert completed.url == "https://example.test/notebooklm-bare/audio.mp3"
    assert completed.is_complete is True
    assert completed.is_pending is False

    pending = asyncio.run(
        client.artifacts.poll_status(SYNTHETIC_NOTEBOOK_ID, PENDING_ARTIFACT_ID)
    )
    assert pending.status == "pending"
    assert pending.is_pending is True
    assert pending.is_in_progress is False
    with pytest.raises(TimeoutError) as excinfo:
        asyncio.run(
            client.artifacts.wait_for_completion(
                SYNTHETIC_NOTEBOOK_ID, PENDING_ARTIFACT_ID, timeout=1
            )
        )
    assert (
        str(excinfo.value) == "artifact generation is still pending in offline fixture"
    )

    failed = asyncio.run(
        client.artifacts.poll_status(SYNTHETIC_NOTEBOOK_ID, FAILED_ARTIFACT_ID)
    )
    assert failed.status == "failed"
    assert failed.error == "Synthetic generation failed"
    assert failed.is_failed is True
    assert failed.is_rate_limited is False

    not_found = asyncio.run(
        client.artifacts.poll_status(SYNTHETIC_NOTEBOOK_ID, "missing-task")
    )
    assert not_found.task_id == "missing-task"
    assert not_found.status == "not_found"
    assert not_found.is_not_found is True

    suggestions = asyncio.run(client.artifacts.suggest_reports(SYNTHETIC_NOTEBOOK_ID))
    assert [s.title for s in suggestions] == [
        "Synthetic Briefing",
        "Synthetic Study Guide",
    ]
    assert (
        suggestions[0].prompt
        == "Write a concise briefing about the synthetic fixture notebook."
    )
    assert suggestions[1].audience_level == 1


def test_artifact_cli_poll_wait_and_suggestions_json(repo_root, monkeypatch, capsys):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)

    code, out, err = _run(
        mods,
        capsys,
        [
            "artifact",
            "poll",
            COMPLETED_ARTIFACT_ID,
            "--notebook",
            SYNTHETIC_NOTEBOOK_ID,
            "--json",
        ],
    )
    assert code == 0
    assert err == ""
    assert _json(out) == {
        "error": None,
        "error_code": None,
        "metadata": {"artifact_type": "audio", "title": "Synthetic Audio Overview"},
        "status": "completed",
        "task_id": COMPLETED_ARTIFACT_ID,
        "url": "https://example.test/notebooklm-bare/audio.mp3",
    }

    code, out, err = _run(
        mods,
        capsys,
        [
            "artifact",
            "wait",
            COMPLETED_ARTIFACT_ID,
            "-n",
            SYNTHETIC_NOTEBOOK_ID,
            "--json",
        ],
    )
    assert code == 0
    assert err == ""
    assert _json(out) == {
        "artifact_id": COMPLETED_ARTIFACT_ID,
        "error": None,
        "status": "completed",
        "url": "https://example.test/notebooklm-bare/audio.mp3",
    }

    code, out, err = _run(
        mods,
        capsys,
        ["artifact", "suggestions", "-n", SYNTHETIC_NOTEBOOK_ID, "--json"],
    )
    assert code == 0
    assert err == ""
    assert _json(out) == [
        {
            "description": "A concise briefing generated from synthetic fixture sources.",
            "prompt": "Write a concise briefing about the synthetic fixture notebook.",
            "title": "Synthetic Briefing",
        },
        {
            "description": "A study guide prompt over fixture-only sources.",
            "prompt": "Create a study guide from the synthetic fixture notebook.",
            "title": "Synthetic Study Guide",
        },
    ]


def test_artifact_cli_wait_pending_fixture_returns_timeout_payload(
    repo_root, monkeypatch, capsys
):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)

    code, out, err = _run(
        mods,
        capsys,
        [
            "artifact",
            "wait",
            PENDING_ARTIFACT_ID,
            "-n",
            SYNTHETIC_NOTEBOOK_ID,
            "--timeout",
            "1",
            "--json",
        ],
    )

    assert code == 1
    assert err == ""
    assert _json(out) == {
        "artifact_id": PENDING_ARTIFACT_ID,
        "error": "Timed out after 1 seconds",
        "status": "timeout",
    }


def test_research_api_status_and_wait_use_fixture_without_home(repo_root, monkeypatch):
    _poison_home(monkeypatch)
    monkeypatch.syspath_prepend(str(repo_root))
    from notebooklm import AuthTokens, NotebookLMClient, ResearchStatus

    client = NotebookLMClient(AuthTokens(cookies={}, csrf_token="", session_id=""))

    task = asyncio.run(client.research.poll(SYNTHETIC_NOTEBOOK_ID))
    assert task.task_id == COMPLETED_RESEARCH_ID
    assert task.status is ResearchStatus.COMPLETED
    assert (
        task.to_public_dict()["sources"][0]["title"] == "Synthetic research source one"
    )
    assert task.to_public_dict()["tasks"][0]["task_id"] == COMPLETED_RESEARCH_ID

    running = asyncio.run(
        client.research.poll(SYNTHETIC_NOTEBOOK_ID, RUNNING_RESEARCH_ID)
    )
    assert running.status is ResearchStatus.IN_PROGRESS
    with pytest.raises(TimeoutError) as excinfo:
        asyncio.run(
            client.research.wait_for_completion(
                SYNTHETIC_NOTEBOOK_ID, RUNNING_RESEARCH_ID, timeout=1
            )
        )
    assert str(excinfo.value) == "research is still in progress in offline fixture"

    not_found = asyncio.run(
        client.research.poll(SYNTHETIC_NOTEBOOK_ID, "missing-research")
    )
    assert not_found.status is ResearchStatus.NOT_FOUND
    assert not_found.to_public_dict() == {
        "query": "",
        "report": "",
        "sources": [],
        "status": "not_found",
        "summary": "",
        "task_id": "missing-research",
        "tasks": [],
    }

    no_research = asyncio.run(client.research.poll("missing-notebook"))
    assert no_research.status is ResearchStatus.NO_RESEARCH
    assert no_research.to_public_dict() == {"status": "no_research", "tasks": []}


def test_research_cli_status_and_wait_json(repo_root, monkeypatch, capsys):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)

    code, out, err = _run(
        mods, capsys, ["research", "status", "-n", SYNTHETIC_NOTEBOOK_ID, "--json"]
    )
    assert code == 0
    assert err == ""
    status = _json(out)
    assert status["task_id"] == COMPLETED_RESEARCH_ID
    assert status["status"] == "completed"
    assert (
        status["sources"][0]["url"]
        == "https://example.test/notebooklm-bare/research/source-one"
    )

    code, out, err = _run(
        mods, capsys, ["research", "wait", "-n", SYNTHETIC_NOTEBOOK_ID, "--json"]
    )
    assert code == 0
    assert err == ""
    assert _json(out) == {
        "query": "Synthetic NotebookLM parity research",
        "report": "# Synthetic Research Report\n\nFixture-only report body.",
        "sources": [
            {
                "research_task_id": COMPLETED_RESEARCH_ID,
                "result_type": 1,
                "title": "Synthetic research source one",
                "url": "https://example.test/notebooklm-bare/research/source-one",
            }
        ],
        "sources_found": 1,
        "status": "completed",
    }


def test_share_status_api_and_cli_are_fixture_backed_read_only(
    repo_root, monkeypatch, capsys
):
    _poison_home(monkeypatch)
    monkeypatch.syspath_prepend(str(repo_root))
    from notebooklm import (
        AuthTokens,
        NotebookLMClient,
        ShareAccess,
        SharePermission,
        ShareViewLevel,
    )

    client = NotebookLMClient(AuthTokens(cookies={}, csrf_token="", session_id=""))
    status = asyncio.run(client.sharing.get_status(SYNTHETIC_NOTEBOOK_ID))
    assert status.notebook_id == SYNTHETIC_NOTEBOOK_ID
    assert status.is_public is False
    assert status.access is ShareAccess.RESTRICTED
    assert status.view_level is ShareViewLevel.FULL_NOTEBOOK
    assert [(u.email, u.permission) for u in status.shared_users] == [
        ("fixture.viewer@example.test", SharePermission.VIEWER),
        ("fixture.editor@example.test", SharePermission.EDITOR),
    ]

    mods = _load(repo_root, monkeypatch)
    code, out, err = _run(
        mods, capsys, ["share", "status", "-n", SYNTHETIC_NOTEBOOK_ID, "--json"]
    )
    assert code == 0
    assert err == ""
    assert _json(out) == {
        "access": "restricted",
        "is_public": False,
        "notebook_id": SYNTHETIC_NOTEBOOK_ID,
        "share_url": None,
        "shared_users": [
            {
                "display_name": "Fixture Viewer",
                "email": "fixture.viewer@example.test",
                "permission": "viewer",
            },
            {
                "display_name": "Fixture Editor",
                "email": "fixture.editor@example.test",
                "permission": "editor",
            },
        ],
        "view_level": "full_notebook",
    }


def test_research_source_import_is_fixture_backed_after_3b13(
    repo_root, monkeypatch, capsys
):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)

    code, out, err = _run(
        mods,
        capsys,
        ["research", "wait", "-n", SYNTHETIC_NOTEBOOK_ID, "--import-all", "--json"],
    )
    assert code == 0
    assert err == ""
    payload = json.loads(out)
    assert payload["imported"] == 1
    assert payload["imported_sources"][0]["title"] == "Synthetic research source one"
