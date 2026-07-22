"""Parity tests for public client lifecycle/metrics methods."""

from __future__ import annotations

import asyncio
import importlib
import inspect

import pytest


def _client(notebooklm):
    return notebooklm.NotebookLMClient(
        notebooklm.AuthTokens(cookies={}, csrf_token="", session_id="")
    )


def test_client_lifecycle_signatures_match_upstream(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))
    notebooklm = importlib.import_module("notebooklm")

    close_sig = inspect.signature(notebooklm.NotebookLMClient.close)
    drain_sig = inspect.signature(notebooklm.NotebookLMClient.drain)
    exit_sig = inspect.signature(notebooklm.NotebookLMClient.__aexit__)

    assert list(exit_sig.parameters) == ["self", "exc_type", "exc_val", "exc_tb"]
    assert list(close_sig.parameters) == ["self", "drain", "drain_timeout"]
    assert close_sig.parameters["drain"].kind is inspect.Parameter.KEYWORD_ONLY
    assert close_sig.parameters["drain"].default is True
    assert close_sig.parameters["drain_timeout"].default is None
    assert list(drain_sig.parameters) == ["self", "timeout"]
    assert drain_sig.parameters["timeout"].default is None
    assert hasattr(notebooklm.NotebookLMClient, "metrics_snapshot")


def test_client_rpc_call_params_argument_matches_upstream(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))
    notebooklm = importlib.import_module("notebooklm")

    rpc_sig = inspect.signature(notebooklm.NotebookLMClient.rpc_call)

    assert list(rpc_sig.parameters) == [
        "self",
        "method",
        "params",
        "allow_null",
        "disable_internal_retries",
    ]
    assert rpc_sig.parameters["params"].default is inspect.Parameter.empty
    with pytest.raises(TypeError):
        rpc_sig.bind(_client(notebooklm), "live-method")


def test_client_close_drains_by_default_and_can_skip_drain(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))
    notebooklm = importlib.import_module("notebooklm")

    async def scenario():
        drained: list[float | None] = []
        client = _client(notebooklm)

        async def drain(timeout=None):
            drained.append(timeout)

        client.drain = drain
        await client.close(drain_timeout=1.25)
        assert drained == [1.25]
        assert client.is_connected is False

        skipped: list[float | None] = []
        client = _client(notebooklm)

        async def skipped_drain(timeout=None):
            skipped.append(timeout)

        client.drain = skipped_drain
        await client.close(drain=False)
        assert skipped == []
        assert client.is_connected is False

    asyncio.run(scenario())


def test_client_aexit_preserves_body_exception_over_close_error(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))
    notebooklm = importlib.import_module("notebooklm")

    async def scenario():
        client = _client(notebooklm)

        async def close_with_error():
            raise RuntimeError("synthetic close failure")

        client.close = close_with_error
        result = await client.__aexit__(
            ValueError, ValueError("body failure"), None
        )
        assert result is None

        client = _client(notebooklm)
        client.close = close_with_error
        with pytest.raises(RuntimeError, match="synthetic close failure"):
            await client.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_client_close_after_drain_timeout_still_closes(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))
    notebooklm = importlib.import_module("notebooklm")
    client = _client(notebooklm)

    async def drain(timeout=None):
        raise TimeoutError("synthetic drain timeout")

    client.drain = drain

    async def scenario():
        with pytest.raises(TimeoutError, match="synthetic drain timeout"):
            await client.close(drain_timeout=0.01)

    asyncio.run(scenario())
    assert client.is_connected is False


def test_client_metrics_snapshot_returns_upstream_dataclass(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))
    notebooklm = importlib.import_module("notebooklm")
    client = _client(notebooklm)

    snapshot = client.metrics_snapshot()

    assert type(snapshot) is notebooklm.ClientMetricsSnapshot
    assert snapshot == notebooklm.ClientMetricsSnapshot()
    assert snapshot is not client.metrics_snapshot()


def test_client_rpc_call_updates_metrics_and_emits_event(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))
    notebooklm = importlib.import_module("notebooklm")
    rpc = importlib.import_module("notebooklm.rpc")
    events = []
    client = notebooklm.NotebookLMClient(
        notebooklm.AuthTokens(cookies={}, csrf_token="", session_id=""),
        on_rpc_event=events.append,
    )

    result = asyncio.run(client.rpc_call(rpc.RPCMethod.LIST_NOTEBOOKS, [None, 1]))
    snapshot = client.metrics_snapshot()

    assert result
    assert snapshot.rpc_calls_started == 1
    assert snapshot.rpc_calls_succeeded == 1
    assert snapshot.rpc_calls_failed == 0
    assert snapshot.rpc_latency_seconds_total >= 0
    assert len(events) == 1
    assert type(events[0]) is notebooklm.RpcTelemetryEvent
    assert events[0].method == "LIST_NOTEBOOKS"
    assert events[0].status == "success"
