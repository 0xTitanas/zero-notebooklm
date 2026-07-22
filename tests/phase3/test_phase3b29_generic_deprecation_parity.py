"""Generic deprecation warning parity for upstream v0.7.2."""

from __future__ import annotations

import asyncio
from pathlib import Path
import warnings


SYNTHETIC_NOTEBOOK_ID = "fake-notebook-0001"


def _poison_home(monkeypatch):
    monkeypatch.setattr(
        Path,
        "home",
        staticmethod(lambda: (_ for _ in ()).throw(AssertionError("real home access"))),
    )


def _client():
    from notebooklm import AuthTokens, NotebookLMClient

    return NotebookLMClient(AuthTokens(cookies={}, csrf_token="", session_id=""))


class _ResearchRpc:
    async def rpc_call(self, method, params, **kwargs):
        return [
            [
                ["task-a", [None, ["Query A"], None, [[], "Summary A"], 1]],
                ["task-b", [None, ["Query B"], None, [[], "Summary B"], 1]],
            ]
        ]


def test_notebooks_share_warns_and_quiet_env_suppresses(monkeypatch):
    _poison_home(monkeypatch)
    client = _client()

    async def share_once():
        return await client.notebooks.share(SYNTHETIC_NOTEBOOK_ID, public=True)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        result = asyncio.run(share_once())
    assert result["public"] is True
    assert len(caught) == 1
    assert "NotebooksAPI.share() is deprecated" in str(caught[0].message)

    monkeypatch.setenv("NOTEBOOKLM_QUIET_DEPRECATIONS", "1")
    with warnings.catch_warnings(record=True) as quiet:
        warnings.simplefilter("always", DeprecationWarning)
        asyncio.run(share_once())
    assert quiet == []


def test_from_storage_await_warns_and_quiet_env_suppresses(monkeypatch, tmp_path):
    _poison_home(monkeypatch)
    from notebooklm import AuthTokens, NotebookLMClient
    import notebooklm.client as client_mod

    async def fake_from_storage(cls, path=None, profile=None):
        return cls(cookies={}, csrf_token="", session_id="", storage_path=path)

    monkeypatch.setattr(client_mod.AuthTokens, "from_storage", classmethod(fake_from_storage))

    async def build():
        return await NotebookLMClient.from_storage(path=tmp_path / "storage.json")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        client = asyncio.run(build())
    assert isinstance(client.auth, AuthTokens)
    assert len(caught) == 1
    assert "Awaiting NotebookLMClient.from_storage(...) is deprecated" in str(caught[0].message)

    monkeypatch.setenv("NOTEBOOKLM_QUIET_DEPRECATIONS", "yes")
    with warnings.catch_warnings(record=True) as quiet:
        warnings.simplefilter("always", DeprecationWarning)
        asyncio.run(build())
    assert quiet == []


def test_research_poll_ambiguous_without_task_id_warns_and_quiet_suppresses(monkeypatch):
    _poison_home(monkeypatch)
    client = _client()
    client.research._live_rpc = _ResearchRpc()

    async def poll_once():
        return await client.research.poll(SYNTHETIC_NOTEBOOK_ID)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        result = asyncio.run(poll_once())
    assert result.task_id == "task-a"
    assert [task.task_id for task in result.tasks] == ["task-a", "task-b"]
    assert len(caught) == 1
    assert "returned 2 in-flight tasks but no task_id discriminator" in str(caught[0].message)

    monkeypatch.setenv("NOTEBOOKLM_QUIET_DEPRECATIONS", "on")
    with warnings.catch_warnings(record=True) as quiet:
        warnings.simplefilter("always", DeprecationWarning)
        asyncio.run(poll_once())
    assert quiet == []
