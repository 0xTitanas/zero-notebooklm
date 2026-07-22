"""Phase 3B18 offline client auth/runtime closure.

This batch closes remaining public ``NotebookLMClient`` API stubs without live
NotebookLM access:

* ``from_storage`` returns upstream's awaitable async context wrapper and defers
  storage auth loading until enter/await.
* ``refresh_auth`` uses the injected fake homepage transport and persists
  homepage cookies in fixture storage.
* ``rpc_call`` routes read-only RPCMethod requests through the existing sanitized
  fake RPC seam.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest


SECRET_SID = "sidPhase3B18SyntheticSecretValue0123456789abcdef"
SECRET_PSIDTS_OLD = "sidtsPhase3B18OldSyntheticSecretValue0123456789abcdef"
SECRET_PSIDTS_NEW = "sidtsPhase3B18NewSyntheticSecretValue0123456789abcdef"
SECRET_CSRF = "csrfPhase3B18SyntheticSecretValue0123456789abcdef"
SECRET_SESSION = "sessionPhase3B18SyntheticSecretValue0123456789abcdef"


def _storage_state(*, psidts: str = SECRET_PSIDTS_OLD) -> dict:
    return {
        "cookies": [
            {
                "name": "SID",
                "value": SECRET_SID,
                "domain": ".google.com",
                "path": "/",
                "secure": True,
                "httpOnly": False,
                "sameSite": "Lax",
                "expires": 1893456000,
            },
            {
                "name": "__Secure-1PSIDTS",
                "value": psidts,
                "domain": ".google.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
                "sameSite": "Lax",
                "expires": 1893456000,
            },
        ],
        "origins": [],
    }


def _write_profile_storage(
    tmp_path: Path, cookies_mod, *, profile: str = "default"
) -> Path:
    path = tmp_path / "nlm-home" / "profiles" / profile / "storage_state.json"
    cookies_mod.save_storage_state(path, _storage_state())
    return path


def _wiz_html() -> str:
    return (
        '<script>var WIZ_global_data = {"SNlM0e":"'
        + SECRET_CSRF
        + '", "FdrFJe":"'
        + SECRET_SESSION
        + '"};</script>'
    )


class FakeTransport:
    def __init__(self, http_std, *, html: str | None = None):
        self.http_std = http_std
        self.calls: list[tuple[str, str, dict]] = []
        self.timeouts: list[float | None] = []
        self.html = html if html is not None else _wiz_html()

    def post(
        self,
        url,
        *,
        body=None,
        headers=None,
        timeout=None,
        max_redirects=5,
        max_body_bytes=None,
    ):
        self.timeouts.append(timeout)
        self.calls.append(("POST", url, dict(headers or {})))
        return self.http_std.Response(
            status=200,
            url=url,
            headers={
                "set-cookie": (
                    "__Secure-1PSIDTS="
                    + SECRET_PSIDTS_NEW
                    + "; Domain=.google.com; Path=/; Secure; HttpOnly"
                )
            },
            body=b"[]",
        )

    def get(
        self, url, *, headers=None, timeout=None, max_redirects=5, max_body_bytes=None
    ):
        self.timeouts.append(timeout)
        self.calls.append(("GET", url, dict(headers or {})))
        return self.http_std.Response(
            status=200,
            url=url,
            headers={
                "set-cookie": (
                    "__Secure-1PSIDTS="
                    + SECRET_PSIDTS_NEW
                    + "; Domain=.google.com; Path=/; Secure; HttpOnly"
                )
            },
            body=self.html.encode(),
        )


def _cookie_value(path: Path, name: str) -> str:
    for cookie in json.loads(path.read_text(encoding="utf-8"))["cookies"]:
        if cookie["name"] == name:
            return cookie["value"]
    raise KeyError(name)


def _fake_token_fetch(monkeypatch):
    from notebooklm import auth as auth_mod

    calls = []

    def fake_fetch_tokens(path, *, persist=True, profile=None, _return_tokens=False, **_):
        calls.append(
            {
                "path": Path(path),
                "persist": persist,
                "profile": profile,
                "return_tokens": _return_tokens,
            }
        )
        if _return_tokens:
            return {"ok": True}, SECRET_CSRF, SECRET_SESSION
        return {"ok": True}

    monkeypatch.setattr(auth_mod, "fetch_tokens_from_storage", fake_fetch_tokens)
    return calls


async def _enter_from_storage(ctx):
    async with ctx as client:
        return {
            "storage_path": client.auth.storage_path,
            "csrf_token": client.auth.csrf_token,
            "session_id": client.auth.session_id,
            "connected": client.is_connected,
        }


def test_from_storage_returns_upstream_async_context_and_loads_on_enter(
    repo_root, tmp_path, monkeypatch
):
    monkeypatch.syspath_prepend(str(repo_root))
    monkeypatch.setenv("NOTEBOOKLM_HOME", str(tmp_path / "nlm-home"))

    from notebooklm import NotebookLMClient
    from notebooklm import cookies as cookies_mod

    storage = _write_profile_storage(tmp_path, cookies_mod)
    calls = _fake_token_fetch(monkeypatch)

    explicit = NotebookLMClient.from_storage(path=str(storage))
    assert type(explicit).__name__ == "_FromStorageContext"
    assert calls == []
    payload = asyncio.run(_enter_from_storage(explicit))
    assert payload == {
        "storage_path": storage,
        "csrf_token": SECRET_CSRF,
        "session_id": SECRET_SESSION,
        "connected": True,
    }
    assert calls[-1] == {
        "path": storage,
        "persist": True,
        "profile": None,
        "return_tokens": True,
    }

    defaulted = NotebookLMClient.from_storage()
    assert asyncio.run(_enter_from_storage(defaulted))["storage_path"] == storage


def test_from_storage_legacy_await_warns_and_returns_built_client(
    repo_root, tmp_path, monkeypatch
):
    monkeypatch.syspath_prepend(str(repo_root))

    from notebooklm import NotebookLMClient
    from notebooklm import cookies as cookies_mod

    storage = _write_profile_storage(tmp_path, cookies_mod)
    _fake_token_fetch(monkeypatch)

    async def build():
        return await NotebookLMClient.from_storage(path=str(storage))

    with pytest.warns(DeprecationWarning, match="Awaiting NotebookLMClient.from_storage"):
        client = asyncio.run(build())
    assert isinstance(client, NotebookLMClient)
    assert client.auth.storage_path == storage
    assert client.auth.csrf_token == SECRET_CSRF


def test_refresh_auth_uses_injected_homepage_transport_and_persists_cookies(
    repo_root, tmp_path, monkeypatch
):
    monkeypatch.syspath_prepend(str(repo_root))

    from notebooklm import AuthTokens, NotebookLMClient
    from notebooklm import cookies as cookies_mod
    from notebooklm import http_std

    storage = _write_profile_storage(tmp_path, cookies_mod)
    transport = FakeTransport(http_std)
    client = NotebookLMClient(
        AuthTokens(cookies={}, csrf_token="", session_id="", storage_path=storage),
        storage_path=storage,
    )
    client.set_auth_transport(get=transport.get, post=transport.post)

    auth = asyncio.run(client.refresh_auth())

    assert [(method, url) for method, url, _headers in transport.calls] == [
        ("GET", "https://notebooklm.google.com/"),
    ]
    assert transport.timeouts == [30.0]
    assert auth is client.auth
    assert auth.csrf_token == SECRET_CSRF
    assert auth.session_id == SECRET_SESSION
    assert _cookie_value(storage, "__Secure-1PSIDTS") == SECRET_PSIDTS_NEW


def test_refresh_auth_requires_injected_get_transport_and_does_not_touch_storage(
    repo_root, tmp_path, monkeypatch
):
    monkeypatch.syspath_prepend(str(repo_root))

    from notebooklm import AuthTokens, NotebookLMClient
    from notebooklm import cookies as cookies_mod
    from notebooklm.errors import NotImplementedInPhaseError

    storage = _write_profile_storage(tmp_path, cookies_mod)
    before = storage.read_text(encoding="utf-8")
    client = NotebookLMClient(
        AuthTokens(cookies={}, csrf_token="", session_id="", storage_path=storage),
        storage_path=storage,
    )

    with pytest.raises(NotImplementedInPhaseError, match="offline auth transport"):
        asyncio.run(client.refresh_auth())
    assert storage.read_text(encoding="utf-8") == before

    client.set_auth_transport(post=lambda *args, **kwargs: None)
    with pytest.raises(NotImplementedInPhaseError, match="offline auth transport"):
        asyncio.run(client.refresh_auth())
    assert storage.read_text(encoding="utf-8") == before


def test_refresh_auth_routes_authuser_like_upstream(repo_root, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))

    from notebooklm import AuthTokens, NotebookLMClient
    from notebooklm import http_std

    transport = FakeTransport(http_std)
    client = NotebookLMClient(
        AuthTokens(
            cookies={"SID": SECRET_SID, "__Secure-1PSIDTS": SECRET_PSIDTS_OLD},
            csrf_token="",
            session_id="",
            authuser=2,
        )
    )
    client.set_auth_transport(get=transport.get)

    asyncio.run(client.refresh_auth())

    assert [(method, url) for method, url, _headers in transport.calls] == [
        ("GET", "https://notebooklm.google.com/?authuser=2"),
    ]


def test_refresh_auth_allows_signed_in_html_with_incidental_accounts_links(
    repo_root, tmp_path, monkeypatch
):
    monkeypatch.syspath_prepend(str(repo_root))

    from notebooklm import AuthTokens, NotebookLMClient
    from notebooklm import http_std

    transport = FakeTransport(
        http_std,
        html=_wiz_html() + '<a href="https://accounts.google.com/ManageAccount">account</a>',
    )
    client = NotebookLMClient(
        AuthTokens(cookies={"SID": SECRET_SID}, csrf_token="", session_id="")
    )
    client.set_auth_transport(get=transport.get)

    auth = asyncio.run(client.refresh_auth())

    assert auth.csrf_token == SECRET_CSRF
    assert auth.session_id == SECRET_SESSION


def test_rpc_call_routes_readonly_fixture_rpc_methods(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))

    from notebooklm import AuthTokens, NotebookLMClient
    from notebooklm.rpc.types import RPCMethod

    client = NotebookLMClient(
        AuthTokens(cookies={}, csrf_token="synthetic", session_id="synthetic")
    )

    notebook_payload = asyncio.run(client.rpc_call(RPCMethod.LIST_NOTEBOOKS, [None, 1]))
    assert notebook_payload[0][0][0] == "fake-notebook-0001"

    artifacts_payload = asyncio.run(
        client.rpc_call(RPCMethod.LIST_ARTIFACTS, ["fake-notebook-0001"])
    )
    assert "fake-artifact" in json.dumps(artifacts_payload)


def test_rpc_call_rejects_non_fixture_mutating_rpc_without_side_effect(
    repo_root, monkeypatch
):
    monkeypatch.syspath_prepend(str(repo_root))

    from notebooklm import AuthTokens, NotebookLMClient
    from notebooklm.errors import ValidationError
    from notebooklm.rpc.types import RPCMethod

    client = NotebookLMClient(
        AuthTokens(cookies={}, csrf_token="synthetic", session_id="synthetic")
    )

    with pytest.raises(ValidationError, match="not supported"):
        asyncio.run(client.rpc_call(RPCMethod.DELETE_NOTEBOOK, ["fake-notebook-0001"]))
