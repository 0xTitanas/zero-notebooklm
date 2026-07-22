"""Dataclass mapping-compat parity for upstream v0.7.2 typed returns."""

from __future__ import annotations

import warnings

import pytest


def test_mapping_compat_subscript_warns_and_legacy_methods_are_silent(monkeypatch):
    monkeypatch.delenv("NOTEBOOKLM_FUTURE_ERRORS", raising=False)
    monkeypatch.delenv("NOTEBOOKLM_QUIET_DEPRECATIONS", raising=False)

    from notebooklm import (
        MindMapResult,
        ResearchSource,
        ResearchStart,
        ResearchStatus,
        ResearchTask,
        SourceGuide,
    )

    source = ResearchSource(url="https://example.test", title="Example")
    task = ResearchTask(
        task_id="task-1",
        status=ResearchStatus.COMPLETED,
        sources=(source,),
        tasks=(ResearchTask(task_id="child", status=ResearchStatus.IN_PROGRESS),),
    )
    start = ResearchStart("task-1", None, "notebook-1", "query", "fast")
    mind_map = MindMapResult({"name": "Root"}, "note-1")
    guide = SourceGuide(summary="Guide", keywords=("one", "two"))

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        assert start["task_id"] == "task-1"
        assert task["sources"] == [{"url": "https://example.test", "title": "Example", "result_type": 1}]
        assert mind_map["mind_map"] == {"name": "Root"}
        assert guide["keywords"] == ["one", "two"]

    messages = [str(w.message) for w in caught]
    assert len(messages) == 4
    assert all("dict-style access is deprecated" in message for message in messages)

    with warnings.catch_warnings(record=True) as silent:
        warnings.simplefilter("always", DeprecationWarning)
        assert source.get("title") == "Example"
        assert "status" in task
        assert list(guide.keys()) == ["summary", "keywords"]
        assert dict(mind_map) == {"mind_map": {"name": "Root"}, "note_id": "note-1"}
    assert [str(w.message) for w in silent] == [
        "MindMapResult['mind_map'] dict-style access is deprecated and will be removed in v0.8.0; use the typed attribute .mind_map instead. Set NOTEBOOKLM_QUIET_DEPRECATIONS=1 to silence this warning.",
        "MindMapResult['note_id'] dict-style access is deprecated and will be removed in v0.8.0; use the typed attribute .note_id instead. Set NOTEBOOKLM_QUIET_DEPRECATIONS=1 to silence this warning.",
    ]


def test_mapping_compat_quiet_and_future_errors(monkeypatch):
    from notebooklm import ResearchStart

    start = ResearchStart("task-1", None, "notebook-1", "query", "fast")

    monkeypatch.setenv("NOTEBOOKLM_QUIET_DEPRECATIONS", "1")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        assert start["task_id"] == "task-1"
    assert caught == []

    monkeypatch.setenv("NOTEBOOKLM_FUTURE_ERRORS", "true")
    with pytest.raises(TypeError, match="not subscriptable"):
        start["task_id"]
    with pytest.raises(AttributeError, match="no attribute 'get'"):
        start.get("task_id")
    with pytest.raises(TypeError, match="not iterable"):
        "task_id" in start
