"""Phase 3A7 offline Python chat API over the fake RPC seam.

This historical slice promoted a synthetic, fixture-backed ``client.chat.ask``
foothold. Phase 3B6 later promotes fixture-backed chat configuration,
conversation helpers, and save-answer-as-note behavior while all live RPC,
browser/auth/credential access, and real NotebookLM mutation remain closed.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import import_origin_audit  # noqa: E402  (placed on sys.path by tests/conftest.py)

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


def test_root_exports_offline_chat_api_models():
    import notebooklm

    required = {
        "AskResult",
        "ChatReference",
        "ConversationTurn",
    }
    for name in required:
        assert hasattr(notebooklm, name)
    assert not hasattr(notebooklm, "ChatAPI")


def test_offline_client_ask_decodes_fixture_and_tracks_local_history_without_home(
    monkeypatch,
):
    _poison_home(monkeypatch)

    async def scenario():
        client = _client()
        result = await client.chat.ask(SYNTHETIC_NOTEBOOK_ID, SYNTHETIC_QUESTION)

        assert result.as_dict() == {
            "answer": SYNTHETIC_ANSWER,
            "conversation_id": "offline-chat-fake-notebook-0001",
            "turn_number": 1,
            "is_follow_up": False,
            "references": [],
            "raw_response": "",
        }
        assert await client.chat.get_history(SYNTHETIC_NOTEBOOK_ID) == [
            (SYNTHETIC_QUESTION, SYNTHETIC_ANSWER),
        ]
        assert [
            turn.as_dict()
            for turn in client.chat.get_cached_turns(result.conversation_id)
        ] == [
            {"query": SYNTHETIC_QUESTION, "answer": SYNTHETIC_ANSWER, "turn_number": 1},
        ]
        assert client.chat.cache_size() == 1
        assert client.chat.clear_cache(result.conversation_id) is True
        assert client.chat.cache_size() == 0
        assert await client.chat.get_history(SYNTHETIC_NOTEBOOK_ID) == []

    asyncio.run(scenario())


def test_offline_chat_rejects_unsupported_request_without_echoing_input(monkeypatch):
    _poison_home(monkeypatch)
    secret = "ya" + "29." + "S" * 40
    synthetic_home = "/".join(("", "Users", "example"))
    private_question = f"private {secret} {synthetic_home}/notebook"

    async def scenario():
        with pytest.raises(Exception) as excinfo:
            await _client().chat.ask(SYNTHETIC_NOTEBOOK_ID, private_question)
        message = str(excinfo.value)
        assert message == "fake rpc request not found"
        assert secret not in message
        assert synthetic_home not in message
        assert excinfo.value.__context__ is None
        assert excinfo.value.__cause__ is None

    asyncio.run(scenario())


def test_chat_configuration_and_conversation_helpers_are_fixture_backed(monkeypatch):
    _poison_home(monkeypatch)

    async def scenario():
        from notebooklm.types import ChatGoal, ChatMode, ChatResponseLength

        client = _client()
        await client.chat.configure(
            SYNTHETIC_NOTEBOOK_ID,
            goal=ChatGoal.CUSTOM,
            response_length=ChatResponseLength.SHORTER,
            custom_prompt="brief",
        )
        await client.chat.set_mode(SYNTHETIC_NOTEBOOK_ID, ChatMode.CONCISE)
        result = await client.chat.ask(SYNTHETIC_NOTEBOOK_ID, SYNTHETIC_QUESTION)
        assert (
            await client.chat.get_conversation_id(SYNTHETIC_NOTEBOOK_ID)
            == result.conversation_id
        )
        turns = await client.chat.get_conversation_turns(
            SYNTHETIC_NOTEBOOK_ID, result.conversation_id
        )
        assert [turn.as_dict() for turn in turns] == [
            {"query": SYNTHETIC_QUESTION, "answer": SYNTHETIC_ANSWER, "turn_number": 1},
        ]
        note = await client.chat.save_answer_as_note(SYNTHETIC_NOTEBOOK_ID, result)
        assert note.title == "Saved Answer"
        assert (
            await client.chat.delete_conversation(
                SYNTHETIC_NOTEBOOK_ID, result.conversation_id
            )
            is True
        )

    asyncio.run(scenario())


def test_phase3a7_cli_ask_root_and_later_notebook_roots_are_promoted():
    from notebooklm import cli

    assert {"list", "use", "ask", "metadata", "summary"} <= set(
        cli.IMPLEMENTED_COMMANDS
    )
    assert {"create", "delete", "rename"} <= set(cli.IMPLEMENTED_COMMANDS)


def test_phase3a7_python_chat_wiring_is_stdlib_and_offline_only(repo_root):
    assert (
        import_origin_audit.audit(
            roots=(
                "notebooklm/__init__.py",
                "notebooklm/client.py",
                "notebooklm/chat.py",
                "notebooklm/fake_rpc.py",
            ),
        )
        == []
    )
    src = "\n".join(
        (repo_root / "notebooklm" / name).read_text(encoding="utf-8")
        for name in ("client.py", "chat.py", "fake_rpc.py")
    )
    forbidden = {
        "socket",
        "http.client",
        "urllib.request",
        "urlopen",
        "subprocess",
        "Path.home",
        "expanduser",
        "os.environ",
        "browser_cookies",
        "interactive_login",
        "Network.",
        "DevTools",
        "keyring",
        "secretstorage",
        "win32crypt",
        "browser_cookie3",
        "browsercookie",
    }
    assert sorted(token for token in forbidden if token in src) == []
