"""Phase 3B9 fixture-backed notebook metadata/share convenience parity.

This batch promotes upstream ``NotebooksAPI`` convenience tails that can be
safely derived from existing synthetic notebook metadata and in-memory sharing
state. It does not perform live RPC, browser/auth/home reads, network calls, or
real NotebookLM sharing/mutation.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

SYNTHETIC_NOTEBOOK_ID = "fake-notebook-0001"


def _client():
    from notebooklm import AuthTokens, NotebookLMClient

    return NotebookLMClient(
        AuthTokens(cookies={}, csrf_token="synthetic", session_id="synthetic")
    )


def test_phase3b9_notebook_description_and_summary_are_synthetic_metadata():
    from notebooklm.types import NotebookDescription, SuggestedTopic

    client = _client()
    description = asyncio.run(client.notebooks.get_description(SYNTHETIC_NOTEBOOK_ID))
    summary = asyncio.run(client.notebooks.get_summary(SYNTHETIC_NOTEBOOK_ID))

    assert isinstance(description, NotebookDescription)
    assert description.summary == summary
    assert summary == "Phase 0 Synthetic Notebook contains 2 synthetic sources."
    assert description.suggested_topics == [
        SuggestedTopic(
            question="What are the key points in Phase 0 Synthetic Notebook?",
            prompt="Summarize the synthetic notebook's committed fixture sources.",
        )
    ]


@pytest.mark.parametrize(
    "notebook_id,artifact_id,expected",
    [
        (
            SYNTHETIC_NOTEBOOK_ID,
            None,
            "https://notebooklm.google.com/notebook/fake-notebook-0001",
        ),
        (
            "fake notebook/with spaces?and=query",
            "artifact/value & details",
            "https://notebooklm.google.com/notebook/fake%20notebook%2Fwith%20spaces%3Fand%3Dquery?artifactId=artifact%2Fvalue%20%26%20details",
        ),
    ],
)
def test_phase3b9_get_share_url_matches_legacy_percent_encoded_shape(
    notebook_id, artifact_id, expected
):
    client = _client()

    assert client.notebooks.get_share_url(notebook_id, artifact_id) == expected


def test_phase3b9_notebook_share_wraps_in_memory_public_status_and_return_shape():
    client = _client()

    enabled = asyncio.run(client.notebooks.share(SYNTHETIC_NOTEBOOK_ID, public=True))
    status = asyncio.run(client.sharing.get_status(SYNTHETIC_NOTEBOOK_ID))
    assert enabled == {
        "public": True,
        "url": "https://notebooklm.google.com/notebook/fake-notebook-0001",
        "artifact_id": None,
    }
    assert status.is_public is True
    assert status.share_url == enabled["url"]

    artifact = asyncio.run(
        client.notebooks.share(
            SYNTHETIC_NOTEBOOK_ID, public=True, artifact_id="artifact/value"
        )
    )
    assert artifact == {
        "public": True,
        "url": "https://notebooklm.google.com/notebook/fake-notebook-0001?artifactId=artifact%2Fvalue",
        "artifact_id": "artifact/value",
    }

    disabled = asyncio.run(client.notebooks.share(SYNTHETIC_NOTEBOOK_ID, public=False))
    status = asyncio.run(client.sharing.get_status(SYNTHETIC_NOTEBOOK_ID))
    assert disabled == {"public": False, "url": None, "artifact_id": None}
    assert status.is_public is False


def test_phase3b9_notebook_convenience_signatures_keep_oracle_shapes():
    from notebooklm.client import NotebooksAPI

    expected = {
        "get_description": "(self, notebook_id: 'str') -> 'NotebookDescription'",
        "get_summary": "(self, notebook_id: 'str') -> 'str'",
        "get_share_url": "(self, notebook_id: 'str', artifact_id: 'str | None' = None) -> 'str'",
        "share": "(self, notebook_id: 'str', public: 'bool' = True, artifact_id: 'str | None' = None) -> 'dict[str, Any]'",
    }
    actual = {
        name: str(inspect.signature(getattr(NotebooksAPI, name))) for name in expected
    }

    assert actual == expected


def test_phase3b9_keeps_low_level_live_rpc_closed_after_research_api_promotion():
    from notebooklm.errors import ValidationError

    client = _client()

    with pytest.raises(ValidationError):
        asyncio.run(client.rpc_call("live-method", []))
