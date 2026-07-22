"""Research deprecation/future-error parity for upstream v0.7.2 runways."""

from __future__ import annotations

import asyncio
from pathlib import Path
import warnings

import pytest


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


class _ResearchRpc:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def rpc_call(self, method, params, **kwargs):
        self.calls.append((method, params, kwargs))
        return self.result


def test_research_start_future_errors_raise_on_missing_task_id(monkeypatch):
    _poison_home(monkeypatch)
    monkeypatch.setenv("NOTEBOOKLM_FUTURE_ERRORS", "1")

    async def scenario(result):
        from notebooklm import DecodingError

        client = _client()
        client.research._live_rpc = _ResearchRpc(result)
        with pytest.raises(DecodingError):
            await client.research.start(SYNTHETIC_NOTEBOOK_ID, "query")

    asyncio.run(scenario([]))
    asyncio.run(scenario([None]))


def test_research_wait_interval_alias_warns_and_quiet_env_suppresses(monkeypatch):
    _poison_home(monkeypatch)

    async def scenario():
        client = _client()
        started = await client.research.start(SYNTHETIC_NOTEBOOK_ID, "query")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", DeprecationWarning)
            await client.research.wait_for_completion(
                SYNTHETIC_NOTEBOOK_ID, started.task_id, interval=0.25
            )
        assert len(caught) == 1
        assert "ResearchAPI.wait_for_completion(interval=...) is deprecated" in str(
            caught[0].message
        )

        monkeypatch.setenv("NOTEBOOKLM_QUIET_DEPRECATIONS", "yes")
        with warnings.catch_warnings(record=True) as quiet:
            warnings.simplefilter("always", DeprecationWarning)
            await client.research.wait_for_completion(
                SYNTHETIC_NOTEBOOK_ID, started.task_id, interval=0.25
            )
        assert quiet == []

    asyncio.run(scenario())


def test_research_wait_interval_alias_future_errors_raise(monkeypatch):
    _poison_home(monkeypatch)
    monkeypatch.setenv("NOTEBOOKLM_FUTURE_ERRORS", "true")

    async def scenario():
        client = _client()
        started = await client.research.start(SYNTHETIC_NOTEBOOK_ID, "query")
        with pytest.raises(TypeError, match="unexpected keyword argument 'interval'"):
            await client.research.wait_for_completion(
                SYNTHETIC_NOTEBOOK_ID, started.task_id, interval=0.25
            )
        with pytest.raises(TypeError, match="received both 'initial_interval'"):
            await client.research.wait_for_completion(
                SYNTHETIC_NOTEBOOK_ID,
                started.task_id,
                interval=0.25,
                initial_interval=0.5,
            )

    asyncio.run(scenario())
