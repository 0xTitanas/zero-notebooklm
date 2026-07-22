"""Phase 3B6 fixture-backed chat configure/history/save parity batch.

This batch opens remaining safe chat/config/history surfaces without live RPC,
auth/browser/home reads, or real NotebookLM mutation. Mutations are limited to
per-command in-memory chat/note fixtures.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SYNTHETIC_NOTEBOOK_ID = "fake-notebook-0001"
SYNTHETIC_QUESTION = "Phase 0 synthetic question."
SYNTHETIC_ANSWER = "Phase 0 synthetic answer chunk."


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


def _load(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))
    return SimpleNamespace(cli=importlib.import_module("notebooklm.cli"))


def _run(mods, capsys, argv):
    code = mods.cli.console(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_phase3b6_chat_api_methods_are_fixture_backed_without_home(monkeypatch):
    _poison_home(monkeypatch)

    async def scenario():
        from notebooklm.types import ChatGoal, ChatMode, ChatResponseLength

        client = _client()
        await client.chat.set_mode(SYNTHETIC_NOTEBOOK_ID, ChatMode.LEARNING_GUIDE)
        await client.chat.configure(
            SYNTHETIC_NOTEBOOK_ID,
            goal=ChatGoal.CUSTOM,
            response_length=ChatResponseLength.LONGER,
            custom_prompt="Act as a tutor",
        )
        result = await client.chat.ask(SYNTHETIC_NOTEBOOK_ID, SYNTHETIC_QUESTION)
        assert (
            await client.chat.get_conversation_id(SYNTHETIC_NOTEBOOK_ID)
            == result.conversation_id
        )
        turns = await client.chat.get_conversation_turns(
            SYNTHETIC_NOTEBOOK_ID, result.conversation_id
        )
        assert [turn.as_dict() for turn in turns] == [
            {"query": SYNTHETIC_QUESTION, "answer": SYNTHETIC_ANSWER, "turn_number": 1}
        ]
        note = await client.chat.save_answer_as_note(
            SYNTHETIC_NOTEBOOK_ID, result, title="Saved Answer"
        )
        assert note.notebook_id == SYNTHETIC_NOTEBOOK_ID
        assert note.title == "Saved Answer"
        assert "**Q:** Phase 0 synthetic question." in note.content
        assert "**A:** Phase 0 synthetic answer chunk." in note.content
        assert (
            await client.chat.delete_conversation(
                SYNTHETIC_NOTEBOOK_ID, result.conversation_id
            )
            is True
        )
        assert await client.chat.get_conversation_id(SYNTHETIC_NOTEBOOK_ID) is None

    asyncio.run(scenario())


def test_phase3b6_chat_method_signatures_match_golden():
    from notebooklm.chat import ChatAPI

    expected = {
        "configure": "(self, notebook_id: 'str', goal: 'ChatGoal | None' = None, response_length: 'ChatResponseLength | None' = None, custom_prompt: 'str | None' = None) -> 'None'",
        "delete_conversation": "(self, notebook_id: 'str', conversation_id: 'str') -> 'bool'",
        "get_conversation_id": "(self, notebook_id: 'str') -> 'str | None'",
        "get_conversation_turns": "(self, notebook_id: 'str', conversation_id: 'str', limit: 'int' = 2) -> 'Any'",
        "save_answer_as_note": "(self, notebook_id: 'str', ask_result: 'AskResult', *, title: 'str | None' = None) -> 'Note'",
        "set_bound_loop": "(self, loop: 'asyncio.AbstractEventLoop | None') -> 'None'",
        "set_mode": "(self, notebook_id: 'str', mode: 'ChatMode') -> 'None'",
    }
    actual = {name: str(inspect.signature(getattr(ChatAPI, name))) for name in expected}
    assert actual == expected


def test_phase3b6_configure_cli_is_fixture_backed(repo_root, monkeypatch, capsys):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)

    code, out, err = _run(
        mods,
        capsys,
        [
            "configure",
            "-n",
            SYNTHETIC_NOTEBOOK_ID,
            "--mode",
            "learning-guide",
            "--json",
        ],
    )
    assert code == 0
    assert err == ""
    assert json.loads(out) == {
        "notebook_id": SYNTHETIC_NOTEBOOK_ID,
        "mode": "learning-guide",
        "configured": True,
    }

    code, out, err = _run(
        mods,
        capsys,
        [
            "configure",
            "-n",
            SYNTHETIC_NOTEBOOK_ID,
            "--persona",
            "Act as a tutor",
            "--response-length",
            "longer",
            "--json",
        ],
    )
    assert code == 0
    assert err == ""
    assert json.loads(out) == {
        "notebook_id": SYNTHETIC_NOTEBOOK_ID,
        "mode": None,
        "goal": "custom",
        "persona": "Act as a tutor",
        "response_length": "longer",
        "configured": True,
    }


def test_phase3b6_ask_source_new_and_save_as_note_are_fixture_backed(
    repo_root, monkeypatch, capsys
):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)

    code, out, err = _run(
        mods,
        capsys,
        [
            "ask",
            SYNTHETIC_QUESTION,
            "-n",
            SYNTHETIC_NOTEBOOK_ID,
            "--source",
            "fake-source-0001",
            "--new",
            "--yes",
            "--save-as-note",
            "--note-title",
            "Saved Chat",
            "--json",
        ],
    )
    assert code == 0
    assert err == ""
    payload = json.loads(out)
    assert payload["answer"] == SYNTHETIC_ANSWER
    assert payload["conversation_id"].startswith("offline-chat-")
    assert payload["note"] == {"id": "offline-chat-note-0001", "title": "Saved Chat"}


def test_phase3b6_history_clear_and_save_are_fixture_backed(
    repo_root, monkeypatch, capsys
):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)

    code, out, err = _run(
        mods, capsys, ["history", "-n", SYNTHETIC_NOTEBOOK_ID, "--clear", "--json"]
    )
    assert code == 0
    assert err == ""
    assert json.loads(out) == {"cleared": False, "count": 0}

    code, out, err = _run(
        mods,
        capsys,
        [
            "history",
            "-n",
            SYNTHETIC_NOTEBOOK_ID,
            "--save",
            "--note-title",
            "History",
            "--json",
        ],
    )
    assert code == 0
    assert err == ""
    payload = json.loads(out)
    assert payload["notebook_id"] == SYNTHETIC_NOTEBOOK_ID
    assert payload["conversation_id"] == f"offline-chat-{SYNTHETIC_NOTEBOOK_ID}"
    assert payload["turns"][0]["question"] == SYNTHETIC_QUESTION
    assert payload["note"] == {"id": "offline-note-0001", "title": "History"}


def test_phase3b6_history_help_no_longer_marks_promoted_flags_reserved(
    repo_root, monkeypatch, capsys
):
    mods = _load(repo_root, monkeypatch)

    with pytest.raises(SystemExit) as excinfo:
        mods.cli.console(["history", "--help"])

    assert excinfo.value.code == 0
    help_text = capsys.readouterr().out
    assert "--clear" in help_text
    assert "--save" in help_text
    assert "--note-title" in help_text
    assert "reserved for a later parity phase" not in help_text


def test_phase3b6_command_set_promotes_configure_and_fixture_backed_research_import(
    repo_root, monkeypatch, capsys
):
    mods = _load(repo_root, monkeypatch)
    assert "configure" in mods.cli.IMPLEMENTED_COMMANDS

    code, out, err = _run(
        mods,
        capsys,
        ["research", "wait", "-n", SYNTHETIC_NOTEBOOK_ID, "--import-all", "--json"],
    )
    assert code == 0
    assert err == ""
    assert json.loads(out)["imported"] == 1
