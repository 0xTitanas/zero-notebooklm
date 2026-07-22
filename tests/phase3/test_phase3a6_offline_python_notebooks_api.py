"""Phase 3A6 offline Python notebooks API over the fake RPC seam.

This slice promotes a narrow, read-only Python API foothold after the fixture RPC
seam and CLI list/use wiring have been reviewed: ``NotebookLMClient`` exposes an
offline ``notebooks`` sub-client whose list/get/metadata/source-id methods read
only the committed synthetic list-notebooks fixture. It does not enter live RPC,
read browser/auth/credential state, mutate notebooks, or promote CLI rows.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import import_origin_audit  # noqa: E402  (placed on sys.path by tests/conftest.py)

EXPECTED_NOTEBOOK_DICT = {
    "created_at": "2025-06-15T15:06:40+00:00",
    "id": "fake-notebook-0001",
    "is_owner": True,
    "sources_count": 2,
    "title": "Phase 0 Synthetic Notebook",
}


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


def test_root_exports_offline_client_models_and_auth_tokens():
    import notebooklm

    required = {
        "AuthTokens",
        "Notebook",
        "NotebookLMClient",
        "NotebookMetadata",
    }
    for name in required:
        assert hasattr(notebooklm, name)
    assert not hasattr(notebooklm, "NotebooksAPI")


def test_auth_tokens_repr_matches_upstream_diagnostic_shape(tmp_path):
    from notebooklm import AuthTokens

    secret = "ya" + "29." + "S" * 40
    storage = tmp_path / "private-profile" / "storage_state.json"
    tokens = AuthTokens(
        cookies={"SID": secret},
        csrf_token=secret,
        session_id=secret,
        storage_path=storage,
        authuser=2,
        account_email="private@example.test",
    )

    rendered = repr(tokens)
    assert secret not in rendered
    assert f"storage_path={storage!r}" in rendered
    assert "cookies=<1 redacted>" in rendered
    assert "cookie_jar=<redacted>" in rendered
    assert "authuser=2" in rendered
    assert "account_email='private@example.test'" in rendered
    assert "<redacted>" in rendered


def test_offline_client_lists_gets_and_returns_metadata_without_home(monkeypatch):
    _poison_home(monkeypatch)
    client = _client()

    notebooks = asyncio.run(client.notebooks.list())
    assert [nb.as_dict() for nb in notebooks] == [EXPECTED_NOTEBOOK_DICT]

    by_id = asyncio.run(client.notebooks.get("fake-notebook-0001"))
    assert by_id.as_dict() == EXPECTED_NOTEBOOK_DICT

    by_prefix = asyncio.run(client.notebooks.get("fake-notebook"))
    assert by_prefix.as_dict() == EXPECTED_NOTEBOOK_DICT

    assert asyncio.run(client.notebooks.get_or_none("missing-private-selector")) is None

    metadata = asyncio.run(client.notebooks.get_metadata("Phase 0 Synthetic Notebook"))
    assert metadata.as_dict() == {
        "notebook": EXPECTED_NOTEBOOK_DICT,
        "sources": [
            {
                "kind": "WEB_PAGE",
                "title": "Synthetic Web Source",
                "url": "https://example.test/notebooklm-bare/source",
            },
            {
                "kind": "PASTED_TEXT",
                "title": "Synthetic Pasted Text Source",
                "url": None,
            },
        ],
    }
    assert asyncio.run(client.notebooks.get_source_ids("fake-notebook-0001")) == [
        "fake-source-0001",
        "fake-source-0002",
    ]


def test_offline_client_async_context_and_lifecycle_are_local_only(monkeypatch):
    _poison_home(monkeypatch)

    async def scenario():
        client = _client()
        assert client.is_connected is True
        async with client as active:
            assert active is client
            assert [nb.as_dict() for nb in await active.notebooks.list()] == [
                EXPECTED_NOTEBOOK_DICT
            ]
        assert client.is_connected is False
        await client.drain()
        await client.close()
        assert client.is_connected is False

    asyncio.run(scenario())


def test_notebooks_share_wrapper_is_fixture_backed_and_keeps_low_level_rpc_closed():
    from notebooklm.errors import ValidationError

    client = _client()
    shared = asyncio.run(client.notebooks.share("fake-notebook-0001", public=True))
    assert shared["public"] is True
    assert shared["url"] == "https://notebooklm.google.com/notebook/fake-notebook-0001"

    with pytest.raises(ValidationError):
        asyncio.run(client.rpc_call("live-method", []))


def test_phase3a6_preserves_later_notebook_command_promotions():
    from notebooklm import cli

    assert {"list", "use", "metadata", "summary"} <= set(cli.IMPLEMENTED_COMMANDS)
    assert "share" in cli.IMPLEMENTED_COMMANDS
    assert {"create", "delete", "rename"} <= set(cli.IMPLEMENTED_COMMANDS)


def test_phase3a6_python_api_wiring_is_stdlib_and_offline_only(repo_root):
    assert (
        import_origin_audit.audit(
            roots=(
                "notebooklm/__init__.py",
                "notebooklm/auth.py",
                "notebooklm/client.py",
            ),
        )
        == []
    )
    src = (repo_root / "notebooklm" / "client.py").read_text(encoding="utf-8")
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
