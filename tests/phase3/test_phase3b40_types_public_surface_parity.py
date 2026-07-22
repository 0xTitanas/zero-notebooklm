"""Public ``notebooklm.types`` export surface parity."""

from __future__ import annotations

import ast
from pathlib import Path

REFERENCE_ROOT = Path("notebooklm-py-reference/src/notebooklm")


def _upstream_all() -> list[str]:
    tree = ast.parse((REFERENCE_ROOT / "types.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    return ast.literal_eval(node.value)
    raise AssertionError("upstream types.py has no __all__")


def test_types_all_matches_upstream_order_and_names():
    import notebooklm.types as types

    expected = _upstream_all()

    assert types.__all__ == expected
    assert all(hasattr(types, name) for name in expected)


def test_types_reexports_public_exceptions_and_status_helpers():
    import notebooklm.exceptions as exceptions
    import notebooklm.types as types

    assert types.SourceError is exceptions.SourceError
    assert types.ArtifactNotFoundError is exceptions.ArtifactNotFoundError
    assert types.artifact_status_to_str(types.ArtifactStatus.COMPLETED) == "completed"
    assert types.source_status_to_str(types.SourceStatus.READY) == "ready"


def test_types_keeps_only_upstream_private_compat_attrs():
    import notebooklm.types as types

    assert hasattr(types, "ArtifactTypeCode")
    assert "ArtifactTypeCode" not in types.__all__
    for name in ("Account", "AuthTokens", "CookieSaveResult", "RPCMethod", "RPCErrorCode"):
        assert not hasattr(types, name)


def test_public_type_response_helpers_match_upstream_shapes(monkeypatch):
    import sys
    import types as py_types

    from notebooklm.types import (
        ConnectionLimits,
        NotebookDescription,
        ReportSuggestion,
        ShareStatus,
        SharedUser,
        SuggestedTopic,
    )
    from notebooklm.rpc.types import ShareAccess, SharePermission, ShareViewLevel

    description = NotebookDescription.from_api_response(
        {
            "summary": "Notebook summary",
            "suggested_topics": [
                {"question": "Question?", "prompt": "Prompt."},
                {"question": "Question only"},
            ],
        }
    )
    assert description == NotebookDescription(
        summary="Notebook summary",
        suggested_topics=[
            SuggestedTopic(question="Question?", prompt="Prompt."),
            SuggestedTopic(question="Question only", prompt=""),
        ],
    )

    assert ReportSuggestion.from_api_response({"title": "T"}) == ReportSuggestion(
        title="T", description="", prompt="", audience_level=2
    )

    assert SharedUser.from_api_response(["user@example.test", 999, [], ["User", "avatar"]]) == SharedUser(
        email="user@example.test",
        permission=SharePermission.VIEWER,
        display_name="User",
        avatar_url="avatar",
    )

    status = ShareStatus.from_api_response(
        [[["user@example.test", SharePermission.EDITOR.value]], [True], 1000],
        "notebook/with spaces",
    )
    assert status.notebook_id == "notebook/with spaces"
    assert status.is_public is True
    assert status.access is ShareAccess.ANYONE_WITH_LINK
    assert status.view_level is ShareViewLevel.FULL_NOTEBOOK
    assert status.shared_users == [
        SharedUser(email="user@example.test", permission=SharePermission.EDITOR)
    ]
    assert status.share_url == "https://notebooklm.google.com/notebook/notebook%2Fwith%20spaces"

    class FakeLimits:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setitem(sys.modules, "httpx", py_types.SimpleNamespace(Limits=FakeLimits))
    limits = ConnectionLimits(1, 2, 3.5).to_httpx_limits()
    assert limits.kwargs == {
        "max_connections": 1,
        "max_keepalive_connections": 2,
        "keepalive_expiry": 3.5,
    }
