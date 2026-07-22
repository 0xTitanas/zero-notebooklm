"""Stdlib live RPC parity slice for authenticated read-only notebook listing.

The tests use synthetic storage and fake transports only. They verify the
upstream notebooklm-py 0.7.2 wire shape without touching live Google services.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest


SENTINEL_SID = "sid-live-rpc-sentinel"
SENTINEL_PSIDTS = "psidts-live-rpc-sentinel"
SENTINEL_CSRF = "csrf-live-rpc-sentinel"
SENTINEL_SESSION = "session-live-rpc-sentinel"


def _storage_state() -> dict:
    return {
        "cookies": [
            {
                "name": "SID",
                "value": SENTINEL_SID,
                "domain": ".google.com",
                "path": "/",
                "secure": True,
                "httpOnly": False,
                "sameSite": "Lax",
                "expires": 1893456000,
            },
            {
                "name": "__Secure-1PSIDTS",
                "value": SENTINEL_PSIDTS,
                "domain": ".google.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
                "sameSite": "Lax",
                "expires": 1893456000,
            },
        ],
        "accounts": [{"authuser": 2, "is_default": True}],
        "origins": [],
    }


def _wiz_html() -> str:
    return (
        '<script>var WIZ_global_data = {"SNlM0e":"'
        + SENTINEL_CSRF
        + '","FdrFJe":"'
        + SENTINEL_SESSION
        + '"};</script>'
    )


def _write_storage(tmp_path: Path, cookies_mod) -> Path:
    storage = tmp_path / "storage_state.json"
    cookies_mod.save_storage_state(storage, _storage_state())
    return storage


class FakeTokenTransport:
    def __init__(self, http_std):
        self.http_std = http_std
        self.get_urls: list[str] = []

    def get(
        self,
        url,
        *,
        headers=None,
        timeout=None,
        max_redirects=5,
        max_body_bytes=None,
    ):
        self.get_urls.append(url)
        return self.http_std.Response(
            status=200,
            url=url,
            headers={},
            body=_wiz_html().encode(),
        )

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
        raise AssertionError("RotateCookies should be disabled in this fixture")


class FakeRpcTransport:
    def __init__(self, http_std):
        self.http_std = http_std
        self.calls: list[dict[str, object]] = []

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
        self.calls.append(
            {
                "url": url,
                "body": body.decode() if isinstance(body, bytes) else body,
                "headers": dict(headers or {}),
                "timeout": timeout,
                "max_redirects": max_redirects,
            }
        )
        notebook_row = [
            "thought\nLive Fixture Notebook",
            [["source-one"], ["source-two"]],
            "live-notebook-0001",
            None,
            None,
            [None, False, None, None, None, [1700000000]],
        ]
        payload = [[notebook_row]]
        envelope = [
            [
                "wrb.fr",
                "wXbhsf",
                json.dumps(payload, separators=(",", ":")),
                None,
                None,
                None,
                "generic",
            ]
        ]
        return self.http_std.Response(
            status=200,
            url=url,
            headers={},
            body=(")]}'" + json.dumps(envelope, separators=(",", ":"))).encode(),
        )


def test_auth_tokens_from_storage_fetches_tokens_and_preserves_domain_cookies(
    repo_root, tmp_path, monkeypatch
):
    monkeypatch.syspath_prepend(str(repo_root))
    monkeypatch.setenv("NOTEBOOKLM_DISABLE_KEEPALIVE_POKE", "1")

    from notebooklm import AuthTokens
    from notebooklm import auth as auth_mod
    from notebooklm import cookies as cookies_mod
    from notebooklm import http_std

    storage = _write_storage(tmp_path, cookies_mod)
    transport = FakeTokenTransport(http_std)
    monkeypatch.setattr(auth_mod, "_default_get", transport.get)
    monkeypatch.setattr(auth_mod, "_default_post", transport.post)

    tokens = asyncio.run(AuthTokens.from_storage(storage))

    assert tokens.csrf_token == SENTINEL_CSRF
    assert tokens.session_id == SENTINEL_SESSION
    assert tokens.storage_path == storage
    assert tokens.authuser == 2
    assert tokens.cookies[("SID", ".google.com", "/")] == SENTINEL_SID
    assert tokens.cookies[("__Secure-1PSIDTS", ".google.com", "/")] == SENTINEL_PSIDTS
    module_tokens = asyncio.run(auth_mod.AuthTokens.from_storage(storage))

    assert module_tokens.csrf_token == SENTINEL_CSRF
    assert module_tokens.session_id == SENTINEL_SESSION
    assert module_tokens.authuser == 2
    assert transport.get_urls == [
        "https://notebooklm.google.com/?authuser=2",
        "https://notebooklm.google.com/?authuser=2",
    ]


def test_live_notebooks_list_uses_upstream_batchexecute_shape_with_stdlib_transport(
    repo_root, monkeypatch
):
    monkeypatch.syspath_prepend(str(repo_root))

    from notebooklm import AuthTokens, NotebookLMClient
    from notebooklm import http_std

    transport = FakeRpcTransport(http_std)
    client = NotebookLMClient(
        AuthTokens(
            cookies={
                ("SID", ".google.com", "/"): SENTINEL_SID,
                ("__Secure-1PSIDTS", ".google.com", "/"): SENTINEL_PSIDTS,
                ("ACCOUNT_ONLY", "accounts.google.com", "/"): "not-sent",
            },
            csrf_token=SENTINEL_CSRF,
            session_id=SENTINEL_SESSION,
            authuser=1,
        )
    )
    client.set_rpc_transport(post=transport.post)

    notebooks = asyncio.run(client.notebooks.list())

    assert [(nb.id, nb.title, nb.sources_count, nb.is_owner) for nb in notebooks] == [
        ("live-notebook-0001", "Live Fixture Notebook", 2, True)
    ]
    assert len(transport.calls) == 1
    call = transport.calls[0]
    url = str(call["url"])
    query = parse_qs(urlsplit(url).query)
    assert urlsplit(url).path == "/_/LabsTailwindUi/data/batchexecute"
    assert query == {
        "rpcids": ["wXbhsf"],
        "source-path": ["/"],
        "f.sid": [SENTINEL_SESSION],
        "hl": ["en"],
        "rt": ["c"],
        "authuser": ["1"],
    }

    headers = call["headers"]
    assert headers["Content-Type"] == "application/x-www-form-urlencoded;charset=UTF-8"
    assert headers["Cookie"] == (
        "SID=sid-live-rpc-sentinel; __Secure-1PSIDTS=psidts-live-rpc-sentinel"
    )

    fields = parse_qs(str(call["body"]), keep_blank_values=True)
    assert fields["at"] == [SENTINEL_CSRF]
    assert json.loads(fields["f.req"][0]) == [
        [["wXbhsf", "[null,1,null,[2]]", None, "generic"]]
    ]


class FakeNotebookMutationTransport:
    def __init__(self, http_std):
        self.http_std = http_std
        self.calls: list[dict[str, object]] = []

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
        body_text = body.decode() if isinstance(body, bytes) else str(body)
        rpc_id = parse_qs(urlsplit(url).query)["rpcids"][0]
        payload_text = json.loads(parse_qs(body_text)["f.req"][0])[0][0][1]
        payload = json.loads(payload_text)
        self.calls.append(
            {
                "rpc_id": rpc_id,
                "source_path": parse_qs(urlsplit(url).query).get("source-path", [""])[0],
                "payload": payload,
            }
        )
        row = [
            "thought\nDisposable Live Fixture",
            [],
            "created-notebook-0001",
            None,
            None,
            [None, False, None, None, None, [1700000001]],
        ]
        if rpc_id == "CCqFvf":
            result = row
        elif rpc_id == "rLM1Ne":
            result = [row]
        elif rpc_id == "s0tc2d":
            result = []
        elif rpc_id == "VfAZjd":
            result = [[["Tiny summary"], [[["What changed?", "Explain the update."]]]]]
        elif rpc_id == "fejl7e":
            result = []
        elif rpc_id == "WWINqb":
            result = []
        else:
            raise AssertionError(f"unexpected rpc id {rpc_id}")
        envelope = [
            [
                "wrb.fr",
                rpc_id,
                json.dumps(result, separators=(",", ":")),
                None,
                None,
                None,
                "generic",
            ]
        ]
        return self.http_std.Response(
            status=200,
            url=url,
            headers={},
            body=(")]}'" + json.dumps(envelope, separators=(",", ":"))).encode(),
        )


def test_live_notebook_create_get_delete_use_upstream_rpc_shapes(
    repo_root, monkeypatch
):
    monkeypatch.syspath_prepend(str(repo_root))

    from notebooklm import AuthTokens, NotebookLMClient
    from notebooklm import http_std

    transport = FakeNotebookMutationTransport(http_std)
    client = NotebookLMClient(
        AuthTokens(
            cookies={
                ("SID", ".google.com", "/"): SENTINEL_SID,
                ("__Secure-1PSIDTS", ".google.com", "/"): SENTINEL_PSIDTS,
            },
            csrf_token=SENTINEL_CSRF,
            session_id=SENTINEL_SESSION,
        )
    )
    client.set_rpc_transport(post=transport.post)

    created = asyncio.run(client.notebooks.create("Disposable Live Fixture"))
    fetched = asyncio.run(client.notebooks.get(created.id))
    raw = asyncio.run(client.notebooks.get_raw(created.id))
    source_ids = asyncio.run(client.notebooks.get_source_ids(created.id))
    metadata = asyncio.run(client.notebooks.get_metadata(created.id))
    summary = asyncio.run(client.notebooks.get_summary(created.id))
    description = asyncio.run(client.notebooks.get_description(created.id))
    renamed = asyncio.run(client.notebooks.rename(created.id, "Renamed Disposable"))
    removed = asyncio.run(client.notebooks.remove_from_recent(created.id))
    deleted = asyncio.run(client.notebooks.delete(created.id))

    assert created.id == "created-notebook-0001"
    assert fetched.title == "Disposable Live Fixture"
    assert raw[0][2] == "created-notebook-0001"
    assert source_ids == []
    assert metadata.notebook.id == "created-notebook-0001"
    assert metadata.sources == []
    assert summary == "Tiny summary"
    assert description.summary == "Tiny summary"
    assert [(topic.question, topic.prompt) for topic in description.suggested_topics] == [
        ("What changed?", "Explain the update.")
    ]
    assert renamed.id == "created-notebook-0001"
    assert removed is None
    assert deleted is None
    assert transport.calls == [
        {
            "rpc_id": "CCqFvf",
            "source_path": "/",
            "payload": [
                "Disposable Live Fixture",
                None,
                None,
                [
                    2,
                    None,
                    None,
                    [1, None, None, None, None, None, None, None, None, None, [1]],
                ],
            ],
        },
        {
            "rpc_id": "rLM1Ne",
            "source_path": "/notebook/created-notebook-0001",
            "payload": ["created-notebook-0001", None, [2], None, 0],
        },
        {
            "rpc_id": "rLM1Ne",
            "source_path": "/notebook/created-notebook-0001",
            "payload": ["created-notebook-0001", None, [2], None, 0],
        },
        {
            "rpc_id": "rLM1Ne",
            "source_path": "/notebook/created-notebook-0001",
            "payload": ["created-notebook-0001", None, [2], None, 0],
        },
        {
            "rpc_id": "rLM1Ne",
            "source_path": "/notebook/created-notebook-0001",
            "payload": ["created-notebook-0001", None, [2], None, 0],
        },
        {
            "rpc_id": "rLM1Ne",
            "source_path": "/notebook/created-notebook-0001",
            "payload": ["created-notebook-0001", None, [2], None, 0],
        },
        {
            "rpc_id": "VfAZjd",
            "source_path": "/notebook/created-notebook-0001",
            "payload": ["created-notebook-0001", [2]],
        },
        {
            "rpc_id": "VfAZjd",
            "source_path": "/notebook/created-notebook-0001",
            "payload": ["created-notebook-0001", [2]],
        },
        {
            "rpc_id": "s0tc2d",
            "source_path": "/",
            "payload": [
                "created-notebook-0001",
                [[None, None, None, [None, "Renamed Disposable"]]],
            ],
        },
        {
            "rpc_id": "rLM1Ne",
            "source_path": "/notebook/created-notebook-0001",
            "payload": ["created-notebook-0001", None, [2], None, 0],
        },
        {
            "rpc_id": "fejl7e",
            "source_path": "/",
            "payload": ["created-notebook-0001"],
        },
        {
            "rpc_id": "WWINqb",
            "source_path": "/",
            "payload": [["created-notebook-0001"], [2]],
        },
    ]


class FakeSourceLifecycleTransport:
    def __init__(self, http_std):
        self.http_std = http_std
        self.calls: list[dict[str, object]] = []
        self.source_id = "source-live-0001"
        self.title = "Live Text Source"
        self.content = "Tiny live source body."

    def _source_entry(self, title=None):
        return [
            [self.source_id],
            title or self.title,
            [None, None, [1700000020], None, 4, None, None, None],
            [None, 2],
        ]

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
        body_text = body.decode() if isinstance(body, bytes) else str(body)
        rpc_id = parse_qs(urlsplit(url).query)["rpcids"][0]
        payload_text = json.loads(parse_qs(body_text)["f.req"][0])[0][0][1]
        payload = json.loads(payload_text)
        self.calls.append(
            {
                "rpc_id": rpc_id,
                "source_path": parse_qs(urlsplit(url).query).get("source-path", [""])[0],
                "payload": payload,
            }
        )
        if rpc_id == "izAoDd":
            spec = payload[0][0]
            if isinstance(spec[1], list):
                result = [self._source_entry(title=spec[1][0])]
            elif isinstance(spec[2], list):
                result = [self._source_entry(title=spec[2][0])]
            elif isinstance(spec[7], list):
                result = [self._source_entry(title=spec[7][0])]
            elif isinstance(spec[0], list):
                result = [self._source_entry(title=spec[0][3])]
            else:
                result = [self._source_entry()]
        elif rpc_id == "rLM1Ne":
            result = [
                [
                    "thought\nSource Notebook",
                    [self._source_entry()],
                    "notebook-live-0001",
                    None,
                    None,
                    [None, False, None, None, None, [1700000001]],
                ]
            ]
        elif rpc_id == "b7Wfje":
            result = self._source_entry(title=payload[2][0][0][0])
        elif rpc_id == "yR9Yof":
            result = [[None, True, [self.source_id]]]
        elif rpc_id == "FLmJqe":
            result = []
        elif rpc_id == "tGMBJ":
            result = []
        else:
            raise AssertionError(f"unexpected rpc id {rpc_id}")
        envelope = [
            [
                "wrb.fr",
                rpc_id,
                json.dumps(result, separators=(",", ":")),
                None,
                None,
                None,
                "generic",
            ]
        ]
        return self.http_std.Response(
            status=200,
            url=url,
            headers={},
            body=(")]}'" + json.dumps(envelope, separators=(",", ":"))).encode(),
        )


def test_live_source_add_text_list_get_delete_use_upstream_rpc_shapes(
    repo_root, monkeypatch
):
    monkeypatch.syspath_prepend(str(repo_root))

    from notebooklm import AuthTokens, NotebookLMClient, NonIdempotentRetryError
    from notebooklm import http_std

    transport = FakeSourceLifecycleTransport(http_std)
    client = NotebookLMClient(
        AuthTokens(
            cookies={
                ("SID", ".google.com", "/"): SENTINEL_SID,
                ("__Secure-1PSIDTS", ".google.com", "/"): SENTINEL_PSIDTS,
            },
            csrf_token=SENTINEL_CSRF,
            session_id=SENTINEL_SESSION,
        )
    )
    client.set_rpc_transport(post=transport.post)

    added = asyncio.run(
        client.sources.add_text("notebook-live-0001", transport.title, transport.content)
    )
    url_added = asyncio.run(
        client.sources.add_url("notebook-live-0001", "https://example.com/source")
    )
    youtube_added = asyncio.run(
        client.sources.add_url("notebook-live-0001", "https://youtu.be/abc_123")
    )
    drive_added = asyncio.run(
        client.sources.add_drive(
            "notebook-live-0001", "drive-file-0001", "Drive Source"
        )
    )
    listed = asyncio.run(client.sources.list("notebook-live-0001"))
    fetched = asyncio.run(client.sources.get("notebook-live-0001", transport.source_id))
    renamed = asyncio.run(
        client.sources.rename(
            "notebook-live-0001", transport.source_id, "Renamed Text Source"
        )
    )
    fresh = asyncio.run(
        client.sources.check_freshness("notebook-live-0001", transport.source_id)
    )
    refreshed = asyncio.run(client.sources.refresh("notebook-live-0001", transport.source_id))
    deleted = asyncio.run(client.sources.delete("notebook-live-0001", transport.source_id))

    assert added.id == transport.source_id
    assert added.title == transport.title
    assert url_added.title == "https://example.com/source"
    assert youtube_added.title == "https://youtu.be/abc_123"
    assert drive_added.title == "Drive Source"
    assert added._type_code == 4
    assert added.kind().name == "PASTED_TEXT"
    assert added.status.name == "READY"
    assert [source.id for source in listed] == [transport.source_id]
    assert fetched is not None
    assert fetched.id == transport.source_id
    assert renamed is not None
    assert renamed.title == "Renamed Text Source"
    assert fresh is True
    assert refreshed is True
    assert deleted is None
    assert transport.calls == [
        {
            "rpc_id": "izAoDd",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [
                [
                    [
                        None,
                        [transport.title, transport.content],
                        None,
                        2,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        1,
                    ]
                ],
                "notebook-live-0001",
                [
                    2,
                    None,
                    None,
                    [1, None, None, None, None, None, None, None, None, None, [1]],
                ],
            ],
        },
        {
            "rpc_id": "izAoDd",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [
                [
                    [
                        None,
                        None,
                        ["https://example.com/source"],
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        1,
                    ]
                ],
                "notebook-live-0001",
                [
                    2,
                    None,
                    None,
                    [1, None, None, None, None, None, None, None, None, None, [1]],
                ],
            ],
        },
        {
            "rpc_id": "izAoDd",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [
                [
                    [
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        ["https://youtu.be/abc_123"],
                        None,
                        None,
                        1,
                    ]
                ],
                "notebook-live-0001",
                [
                    2,
                    None,
                    None,
                    [1, None, None, None, None, None, None, None, None, None, [1]],
                ],
            ],
        },
        {
            "rpc_id": "izAoDd",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [
                [
                    [
                        ["drive-file-0001", "application/vnd.google-apps.document", 1, "Drive Source"],
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        1,
                    ]
                ],
                "notebook-live-0001",
                [2],
                [1, None, None, None, None, None, None, None, None, None, [1]],
            ],
        },
        {
            "rpc_id": "rLM1Ne",
            "source_path": "/notebook/notebook-live-0001",
            "payload": ["notebook-live-0001", None, [2], None, 0],
        },
        {
            "rpc_id": "rLM1Ne",
            "source_path": "/notebook/notebook-live-0001",
            "payload": ["notebook-live-0001", None, [2], None, 0],
        },
        {
            "rpc_id": "b7Wfje",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [None, [transport.source_id], [[["Renamed Text Source"]]]],
        },
        {
            "rpc_id": "yR9Yof",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [None, [transport.source_id], [2]],
        },
        {
            "rpc_id": "FLmJqe",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [None, [transport.source_id], [2]],
        },
        {
            "rpc_id": "tGMBJ",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [[[transport.source_id]]],
        },
    ]

    with pytest.raises(NonIdempotentRetryError):
        asyncio.run(
            client.sources.add_text(
                "notebook-live-0001",
                transport.title,
                transport.content,
                idempotent=True,
            )
        )


class FakeSourceUploadTransport:
    def __init__(self, http_std):
        self.http_std = http_std
        self.calls: list[dict[str, object]] = []
        self.source_id = "source-upload-0001"
        self.upload_url = (
            "https://notebooklm.google.com/upload/_/?upload_id=upload-live-0001"
        )
        self.registered = False

    def _source_entry(self, title="fixture.txt"):
        return [
            [self.source_id],
            title,
            [None, None, [1700000060], None, None, None, None, None],
            [None, 1],
        ]

    def _rpc_response(self, url, rpc_id, result):
        envelope = [
            [
                "wrb.fr",
                rpc_id,
                json.dumps(result, separators=(",", ":")),
                None,
                None,
                None,
                "generic",
            ]
        ]
        return self.http_std.Response(
            status=200,
            url=url,
            headers={},
            body=(")]}'" + json.dumps(envelope, separators=(",", ":"))).encode(),
        )

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
        headers = dict(headers or {})
        if "/_/LabsTailwindUi/data/batchexecute" in url:
            body_text = body.decode() if isinstance(body, bytes) else str(body)
            rpc_id = parse_qs(urlsplit(url).query)["rpcids"][0]
            payload_text = json.loads(parse_qs(body_text)["f.req"][0])[0][0][1]
            payload = json.loads(payload_text)
            self.calls.append(
                {
                    "kind": "rpc",
                    "rpc_id": rpc_id,
                    "source_path": parse_qs(urlsplit(url).query).get(
                        "source-path", [""]
                    )[0],
                    "payload": payload,
                    "headers": headers,
                }
            )
            if rpc_id == "rLM1Ne":
                sources = [self._source_entry()] if self.registered else []
                return self._rpc_response(
                    url,
                    rpc_id,
                    [["thought\nUpload Notebook", sources, "notebook-live-0001"]],
                )
            if rpc_id == "o4cbdc":
                self.registered = True
                return self._rpc_response(url, rpc_id, [[self.source_id]])
            if rpc_id == "b7Wfje":
                return self._rpc_response(
                    url, rpc_id, self._source_entry(title=payload[2][0][0][0])
                )
            raise AssertionError(f"unexpected rpc id {rpc_id}")

        body_bytes = body.encode() if isinstance(body, str) else (body or b"")
        self.calls.append(
            {
                "kind": "upload",
                "url": url,
                "body": body_bytes,
                "headers": headers,
            }
        )
        if url.startswith("https://notebooklm.google.com/upload/_/?authuser=0"):
            return self.http_std.Response(
                status=200,
                url=url,
                headers={"x-goog-upload-url": self.upload_url},
                body=b"",
            )
        if url == self.upload_url:
            return self.http_std.Response(
                status=200,
                url=url,
                headers={},
                body=b"",
            )
        raise AssertionError(f"unexpected upload url {url}")


def test_live_source_add_file_uses_upstream_resumable_upload_shape(
    repo_root, tmp_path, monkeypatch
):
    monkeypatch.syspath_prepend(str(repo_root))

    from notebooklm import AuthTokens, NotebookLMClient
    from notebooklm import http_std

    upload_file = tmp_path / "fixture.txt"
    upload_file.write_text("hello upload", encoding="utf-8")
    progress: list[tuple[int, int]] = []
    transport = FakeSourceUploadTransport(http_std)
    client = NotebookLMClient(
        AuthTokens(
            cookies={
                ("SID", ".google.com", "/"): SENTINEL_SID,
                ("__Secure-1PSIDTS", ".google.com", "/"): SENTINEL_PSIDTS,
            },
            csrf_token=SENTINEL_CSRF,
            session_id=SENTINEL_SESSION,
        )
    )
    client.set_rpc_transport(post=transport.post)

    source = asyncio.run(
        client.sources.add_file(
            "notebook-live-0001",
            upload_file,
            title=" Renamed Upload ",
            on_progress=lambda sent, total: progress.append((sent, total)),
        )
    )

    assert source.id == transport.source_id
    assert source.title == "Renamed Upload"
    assert progress == [(0, 12), (12, 12)]
    assert [call["kind"] for call in transport.calls] == [
        "rpc",
        "rpc",
        "upload",
        "upload",
        "rpc",
        "rpc",
    ]
    baseline, register, start, finalize, poll, rename = transport.calls
    assert baseline["rpc_id"] == "rLM1Ne"
    assert register["rpc_id"] == "o4cbdc"
    assert register["payload"] == [
        [["fixture.txt"]],
        "notebook-live-0001",
        [
            2,
            None,
            None,
            [1, None, None, None, None, None, None, None, None, None, [1]],
        ],
    ]
    assert start["headers"] == {
        "Accept": "*/*",
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        "Cookie": "SID=sid-live-rpc-sentinel; __Secure-1PSIDTS=psidts-live-rpc-sentinel",
        "Origin": "https://notebooklm.google.com",
        "Referer": "https://notebooklm.google.com/",
        "x-goog-authuser": "0",
        "x-goog-upload-command": "start",
        "x-goog-upload-header-content-length": "12",
        "x-goog-upload-header-content-type": "text/plain",
        "x-goog-upload-protocol": "resumable",
    }
    assert json.loads(start["body"]) == {
        "PROJECT_ID": "notebook-live-0001",
        "SOURCE_NAME": "fixture.txt",
        "SOURCE_ID": transport.source_id,
    }
    assert finalize["url"] == transport.upload_url
    assert finalize["body"] == b"hello upload"
    assert finalize["headers"] == {
        "Accept": "*/*",
        "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
        "Cookie": "SID=sid-live-rpc-sentinel; __Secure-1PSIDTS=psidts-live-rpc-sentinel",
        "Origin": "https://notebooklm.google.com",
        "Referer": "https://notebooklm.google.com/",
        "x-goog-authuser": "0",
        "x-goog-upload-command": "upload, finalize",
        "x-goog-upload-offset": "0",
    }
    assert poll["rpc_id"] == "rLM1Ne"
    assert rename["rpc_id"] == "b7Wfje"
    assert rename["payload"] == [None, [transport.source_id], [[["Renamed Upload"]]]]


class FakeSourceContentTransport:
    def __init__(self, http_std):
        self.http_std = http_std
        self.calls: list[dict[str, object]] = []
        self.source_id = "source-content-0001"

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
        body_text = body.decode() if isinstance(body, bytes) else str(body)
        rpc_id = parse_qs(urlsplit(url).query)["rpcids"][0]
        payload_text = json.loads(parse_qs(body_text)["f.req"][0])[0][0][1]
        payload = json.loads(payload_text)
        self.calls.append(
            {
                "rpc_id": rpc_id,
                "source_path": parse_qs(urlsplit(url).query).get("source-path", [""])[0],
                "payload": payload,
            }
        )
        if rpc_id == "hizoJc":
            result = [
                [
                    [self.source_id],
                    "Live Fulltext Source",
                    [
                        None,
                        None,
                        [1700000070],
                        None,
                        6,
                        None,
                        None,
                        ["https://example.com/source.pdf"],
                    ],
                    [None, 2],
                ],
                None,
                None,
                [[["First paragraph.", ["Nested detail."], "Second paragraph."]]],
            ]
        elif rpc_id == "tr032e":
            result = [[["unused", ["Tiny summary"], [["alpha", "beta"]]]]]
        else:
            raise AssertionError(f"unexpected rpc id {rpc_id}")
        envelope = [
            [
                "wrb.fr",
                rpc_id,
                json.dumps(result, separators=(",", ":")),
                None,
                None,
                None,
                "generic",
            ]
        ]
        return self.http_std.Response(
            status=200,
            url=url,
            headers={},
            body=(")]}'" + json.dumps(envelope, separators=(",", ":"))).encode(),
        )


def test_live_source_fulltext_and_guide_use_upstream_rpc_shapes(
    repo_root, monkeypatch
):
    monkeypatch.syspath_prepend(str(repo_root))

    from notebooklm import AuthTokens, NotebookLMClient
    from notebooklm import http_std

    transport = FakeSourceContentTransport(http_std)
    client = NotebookLMClient(
        AuthTokens(
            cookies={
                ("SID", ".google.com", "/"): SENTINEL_SID,
                ("__Secure-1PSIDTS", ".google.com", "/"): SENTINEL_PSIDTS,
            },
            csrf_token=SENTINEL_CSRF,
            session_id=SENTINEL_SESSION,
        )
    )
    client.set_rpc_transport(post=transport.post)

    fulltext = asyncio.run(
        client.sources.get_fulltext("notebook-live-0001", transport.source_id)
    )
    guide = asyncio.run(client.sources.get_guide("notebook-live-0001", transport.source_id))

    assert fulltext.as_dict() == {
        "source_id": transport.source_id,
        "title": "Live Fulltext Source",
        "content": "First paragraph.\nNested detail.\nSecond paragraph.",
        "type_code": 6,
        "url": "https://example.com/source.pdf",
        "char_count": 49,
        "kind": "PDF",
    }
    assert guide.as_dict() == {"summary": "Tiny summary", "keywords": ["alpha", "beta"]}
    assert transport.calls == [
        {
            "rpc_id": "hizoJc",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [[transport.source_id], [2], [2]],
        },
        {
            "rpc_id": "tr032e",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [[[[transport.source_id]]]],
        },
    ]


class FakeNoteLifecycleTransport:
    def __init__(self, http_std):
        self.http_std = http_std
        self.calls: list[dict[str, object]] = []
        self.note_id = "note-live-0001"

    def _note_rows(self):
        return [
            [None, [self.note_id, "Body", [None, None, [1700000030]], None, "Title"]],
            ["deleted-note", None, 2],
            [None, ["mind-map-note", '{"children":[]}', [], None, "Mind Map"]],
        ]

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
        body_text = body.decode() if isinstance(body, bytes) else str(body)
        rpc_id = parse_qs(urlsplit(url).query)["rpcids"][0]
        payload_text = json.loads(parse_qs(body_text)["f.req"][0])[0][0][1]
        payload = json.loads(payload_text)
        self.calls.append(
            {
                "rpc_id": rpc_id,
                "source_path": parse_qs(urlsplit(url).query).get("source-path", [""])[0],
                "payload": payload,
            }
        )
        if rpc_id == "CYK0Xb":
            result = [self.note_id]
        elif rpc_id == "cYAfTb":
            result = []
        elif rpc_id == "cFji9":
            result = [self._note_rows()]
        elif rpc_id == "AH0mwd":
            result = []
        else:
            raise AssertionError(f"unexpected rpc id {rpc_id}")
        envelope = [
            [
                "wrb.fr",
                rpc_id,
                json.dumps(result, separators=(",", ":")),
                None,
                None,
                None,
                "generic",
            ]
        ]
        return self.http_std.Response(
            status=200,
            url=url,
            headers={},
            body=(")]}'" + json.dumps(envelope, separators=(",", ":"))).encode(),
        )


def test_live_notes_create_list_get_delete_use_upstream_rpc_shapes(
    repo_root, monkeypatch
):
    monkeypatch.syspath_prepend(str(repo_root))

    from notebooklm import AuthTokens, NotebookLMClient
    from notebooklm import http_std

    transport = FakeNoteLifecycleTransport(http_std)
    client = NotebookLMClient(
        AuthTokens(
            cookies={
                ("SID", ".google.com", "/"): SENTINEL_SID,
                ("__Secure-1PSIDTS", ".google.com", "/"): SENTINEL_PSIDTS,
            },
            csrf_token=SENTINEL_CSRF,
            session_id=SENTINEL_SESSION,
        )
    )
    client.set_rpc_transport(post=transport.post)

    created = asyncio.run(client.notes.create("notebook-live-0001", "Title", "Body"))
    listed = asyncio.run(client.notes.list("notebook-live-0001"))
    fetched = asyncio.run(client.notes.get("notebook-live-0001", transport.note_id))
    deleted = asyncio.run(client.notes.delete("notebook-live-0001", transport.note_id))

    assert created.as_dict() == {
        "id": transport.note_id,
        "notebook_id": "notebook-live-0001",
        "title": "Title",
        "content": "Body",
        "created_at": None,
    }
    assert [note.id for note in listed] == [transport.note_id]
    assert fetched is not None
    assert fetched.content == "Body"
    assert deleted is None
    assert transport.calls == [
        {
            "rpc_id": "CYK0Xb",
            "source_path": "/notebook/notebook-live-0001",
            "payload": ["notebook-live-0001", "", [1], None, "Title"],
        },
        {
            "rpc_id": "cYAfTb",
            "source_path": "/notebook/notebook-live-0001",
            "payload": ["notebook-live-0001", transport.note_id, [[["Body", "Title", [], 0]]]],
        },
        {
            "rpc_id": "cFji9",
            "source_path": "/notebook/notebook-live-0001",
            "payload": ["notebook-live-0001"],
        },
        {
            "rpc_id": "cFji9",
            "source_path": "/notebook/notebook-live-0001",
            "payload": ["notebook-live-0001"],
        },
        {
            "rpc_id": "AH0mwd",
            "source_path": "/notebook/notebook-live-0001",
            "payload": ["notebook-live-0001", None, [transport.note_id]],
        },
    ]


class FakeArtifactReadTransport:
    def __init__(self, http_std):
        self.http_std = http_std
        self.calls: list[dict[str, object]] = []
        self.artifact_id = "artifact-live-0001"
        self.title = "Live Briefing Doc"

    def _artifact_row(self):
        row = [
            self.artifact_id,
            self.title,
            2,
            None,
            3,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            [1700000040],
        ]
        return row

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
        body_text = body.decode() if isinstance(body, bytes) else str(body)
        rpc_id = parse_qs(urlsplit(url).query)["rpcids"][0]
        payload_text = json.loads(parse_qs(body_text)["f.req"][0])[0][0][1]
        payload = json.loads(payload_text)
        self.calls.append(
            {
                "rpc_id": rpc_id,
                "source_path": parse_qs(urlsplit(url).query).get("source-path", [""])[0],
                "payload": payload,
            }
        )
        if rpc_id == "gArtLc":
            result = [[self._artifact_row()]]
        elif rpc_id == "cFji9":
            result = [[]]
        elif rpc_id == "rc3d8d":
            self.title = payload[0][1]
            result = []
        elif rpc_id == "Krh3pd":
            result = [["https://docs.example.invalid/redacted"]]
        elif rpc_id == "V5N4be":
            result = []
        elif rpc_id == "Rytqqe":
            result = [[self.artifact_id, None, None, None, 1]]
        elif rpc_id == "KmcKPe":
            result = [["slide-revision-live-0001", None, None, None, 1]]
        elif rpc_id == "ciyUvf":
            result = [[["Briefing Doc", "Key insights", None, None, "Summarize", 2]]]
        else:
            raise AssertionError(f"unexpected rpc id {rpc_id}")
        envelope = [
            [
                "wrb.fr",
                rpc_id,
                json.dumps(result, separators=(",", ":")),
                None,
                None,
                None,
                "generic",
            ]
        ]
        return self.http_std.Response(
            status=200,
            url=url,
            headers={},
            body=(")]}'" + json.dumps(envelope, separators=(",", ":"))).encode(),
        )


def test_live_artifacts_list_get_poll_status_use_upstream_rpc_shapes(
    repo_root, monkeypatch
):
    monkeypatch.syspath_prepend(str(repo_root))

    from notebooklm import AuthTokens, NotebookLMClient
    from notebooklm import http_std

    transport = FakeArtifactReadTransport(http_std)
    client = NotebookLMClient(
        AuthTokens(
            cookies={
                ("SID", ".google.com", "/"): SENTINEL_SID,
                ("__Secure-1PSIDTS", ".google.com", "/"): SENTINEL_PSIDTS,
            },
            csrf_token=SENTINEL_CSRF,
            session_id=SENTINEL_SESSION,
        )
    )
    client.set_rpc_transport(post=transport.post)

    listed = asyncio.run(client.artifacts.list("notebook-live-0001"))
    fetched = asyncio.run(client.artifacts.get("notebook-live-0001", transport.artifact_id))
    status = asyncio.run(
        client.artifacts.poll_status("notebook-live-0001", transport.artifact_id)
    )
    missing = asyncio.run(client.artifacts.poll_status("notebook-live-0001", "missing"))
    renamed = asyncio.run(
        client.artifacts.rename(
            "notebook-live-0001", transport.artifact_id, "Renamed Briefing Doc"
        )
    )
    exported = asyncio.run(
        client.artifacts.export_report(
            "notebook-live-0001", transport.artifact_id, title="Exported Briefing"
        )
    )
    retry = asyncio.run(client.artifacts.retry_failed("notebook-live-0001", transport.artifact_id))
    revision = asyncio.run(
        client.artifacts.revise_slide(
            "notebook-live-0001", transport.artifact_id, 2, "tighten the slide"
        )
    )
    suggestions = asyncio.run(client.artifacts.suggest_reports("notebook-live-0001"))
    deleted = asyncio.run(client.artifacts.delete("notebook-live-0001", transport.artifact_id))

    assert [artifact.id for artifact in listed] == [transport.artifact_id]
    assert listed[0].title == "Live Briefing Doc"
    assert listed[0].kind().name == "REPORT"
    assert listed[0].status_str == "completed"
    assert fetched is not None
    assert fetched.id == transport.artifact_id
    assert status.task_id == transport.artifact_id
    assert status.status == "completed"
    assert missing.status == "not_found"
    assert renamed is not None
    assert renamed.title == "Renamed Briefing Doc"
    assert exported == [["https://docs.example.invalid/redacted"]]
    assert retry.task_id == transport.artifact_id
    assert retry.status == "in_progress"
    assert revision.task_id == "slide-revision-live-0001"
    assert revision.status == "in_progress"
    assert [(s.title, s.description, s.prompt, s.audience_level) for s in suggestions] == [
        ("Briefing Doc", "Key insights", "Summarize", 2)
    ]


    assert deleted is None
    assert transport.calls == [
        {
            "rpc_id": "gArtLc",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [
                [2],
                "notebook-live-0001",
                'NOT artifact.status = "ARTIFACT_STATUS_SUGGESTED"',
            ],
        },
        {
            "rpc_id": "cFji9",
            "source_path": "/notebook/notebook-live-0001",
            "payload": ["notebook-live-0001"],
        },
        {
            "rpc_id": "gArtLc",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [
                [2],
                "notebook-live-0001",
                'NOT artifact.status = "ARTIFACT_STATUS_SUGGESTED"',
            ],
        },
        {
            "rpc_id": "cFji9",
            "source_path": "/notebook/notebook-live-0001",
            "payload": ["notebook-live-0001"],
        },
        {
            "rpc_id": "gArtLc",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [
                [2],
                "notebook-live-0001",
                'NOT artifact.status = "ARTIFACT_STATUS_SUGGESTED"',
            ],
        },
        {
            "rpc_id": "gArtLc",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [
                [2],
                "notebook-live-0001",
                'NOT artifact.status = "ARTIFACT_STATUS_SUGGESTED"',
            ],
        },
        {
            "rpc_id": "rc3d8d",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [[transport.artifact_id, "Renamed Briefing Doc"], [["title"]]],
        },
        {
            "rpc_id": "gArtLc",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [
                [2],
                "notebook-live-0001",
                'NOT artifact.status = "ARTIFACT_STATUS_SUGGESTED"',
            ],
        },
        {
            "rpc_id": "Krh3pd",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [
                None,
                transport.artifact_id,
                None,
                "Exported Briefing",
                1,
            ],
        },
        {
            "rpc_id": "Rytqqe",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [
                [
                    2,
                    None,
                    None,
                    [1, None, None, None, None, None, None, None, None, None, [1]],
                    [[1, 4, 8, 2, 3, 6]],
                ],
                transport.artifact_id,
            ],
        },
        {
            "rpc_id": "KmcKPe",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [[2], transport.artifact_id, [[[2, "tighten the slide"]]]],
        },
        {
            "rpc_id": "ciyUvf",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [[2], "notebook-live-0001"],
        },
        {
            "rpc_id": "V5N4be",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [[2], transport.artifact_id],
        },
    ]

class FakeArtifactDownloadTransport:
    def __init__(self, http_std):
        self.http_std = http_std
        self.calls: list[dict[str, object]] = []
        self.artifact_id = "artifact-audio-0001"
        self.media_url = "https://notebooklm.google.com/media/audio.mp4"

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
        body_text = body.decode() if isinstance(body, bytes) else str(body)
        rpc_id = parse_qs(urlsplit(url).query)["rpcids"][0]
        payload_text = json.loads(parse_qs(body_text)["f.req"][0])[0][0][1]
        payload = json.loads(payload_text)
        self.calls.append(
            {
                "kind": "rpc",
                "rpc_id": rpc_id,
                "source_path": parse_qs(urlsplit(url).query).get("source-path", [""])[0],
                "payload": payload,
            }
        )
        assert rpc_id == "gArtLc"
        row = [
            self.artifact_id,
            "Live Audio",
            1,
            None,
            3,
            None,
            [None, None, None, None, None, [[self.media_url, 1, "audio/mp4"]]],
        ]
        envelope = [
            [
                "wrb.fr",
                rpc_id,
                json.dumps([[row]], separators=(",", ":")),
                None,
                None,
                None,
                "generic",
            ]
        ]
        return self.http_std.Response(
            status=200,
            url=url,
            headers={},
            body=(")]}'" + json.dumps(envelope, separators=(",", ":"))).encode(),
        )

    def get(
        self,
        url,
        *,
        headers=None,
        timeout=None,
        max_redirects=5,
        max_body_bytes=None,
    ):
        self.calls.append(
            {
                "kind": "download",
                "url": url,
                "headers": dict(headers or {}),
                "timeout": timeout,
                "max_redirects": max_redirects,
                "max_body_bytes": max_body_bytes,
            }
        )
        assert url == self.media_url
        return self.http_std.Response(
            status=200,
            url=url,
            headers={"content-type": "audio/mp4"},
            body=b"synthetic-audio-bytes",
        )


def test_live_artifact_audio_download_uses_upstream_media_url(
    repo_root, tmp_path, monkeypatch
):
    monkeypatch.syspath_prepend(str(repo_root))

    from notebooklm import AuthTokens, NotebookLMClient
    from notebooklm import http_std

    transport = FakeArtifactDownloadTransport(http_std)
    client = NotebookLMClient(
        AuthTokens(
            cookies={
                ("SID", ".google.com", "/"): SENTINEL_SID,
                ("__Secure-1PSIDTS", ".google.com", "/"): SENTINEL_PSIDTS,
            },
            csrf_token=SENTINEL_CSRF,
            session_id=SENTINEL_SESSION,
        )
    )
    client.set_rpc_transport(post=transport.post, get=transport.get)

    output_path = tmp_path / "audio.mp4"
    returned = asyncio.run(
        client.artifacts.download_audio(
            "notebook-live-0001", str(output_path), transport.artifact_id
        )
    )

    assert returned == str(output_path)
    assert output_path.read_bytes() == b"synthetic-audio-bytes"
    assert transport.calls == [
        {
            "kind": "rpc",
            "rpc_id": "gArtLc",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [
                [2],
                "notebook-live-0001",
                'NOT artifact.status = "ARTIFACT_STATUS_SUGGESTED"',
            ],
        },
        {
            "kind": "download",
            "url": transport.media_url,
            "headers": {
                "Cookie": "SID=sid-live-rpc-sentinel; __Secure-1PSIDTS=psidts-live-rpc-sentinel"
            },
            "timeout": 30.0,
            "max_redirects": 5,
            "max_body_bytes": 536870912,
        },
    ]


class FakeArtifactReportDownloadTransport:
    def __init__(self, http_std):
        self.http_std = http_std
        self.calls: list[dict[str, object]] = []
        self.artifact_id = "artifact-report-0001"

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
        body_text = body.decode() if isinstance(body, bytes) else str(body)
        rpc_id = parse_qs(urlsplit(url).query)["rpcids"][0]
        payload_text = json.loads(parse_qs(body_text)["f.req"][0])[0][0][1]
        payload = json.loads(payload_text)
        self.calls.append(
            {
                "rpc_id": rpc_id,
                "source_path": parse_qs(urlsplit(url).query).get("source-path", [""])[0],
                "payload": payload,
            }
        )
        assert rpc_id == "gArtLc"
        row = [
            self.artifact_id,
            "Live Report",
            2,
            None,
            3,
            None,
            None,
            ["# Live Report\n\nReport body."],
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            [1700000080],
        ]
        envelope = [
            [
                "wrb.fr",
                rpc_id,
                json.dumps([[row]], separators=(",", ":")),
                None,
                None,
                None,
                "generic",
            ]
        ]
        return self.http_std.Response(
            status=200,
            url=url,
            headers={},
            body=(")]}'" + json.dumps(envelope, separators=(",", ":"))).encode(),
        )


def test_live_artifact_report_download_writes_upstream_markdown(
    repo_root, tmp_path, monkeypatch
):
    monkeypatch.syspath_prepend(str(repo_root))

    from notebooklm import AuthTokens, NotebookLMClient
    from notebooklm import http_std

    transport = FakeArtifactReportDownloadTransport(http_std)
    client = NotebookLMClient(
        AuthTokens(
            cookies={
                ("SID", ".google.com", "/"): SENTINEL_SID,
                ("__Secure-1PSIDTS", ".google.com", "/"): SENTINEL_PSIDTS,
            },
            csrf_token=SENTINEL_CSRF,
            session_id=SENTINEL_SESSION,
        )
    )
    client.set_rpc_transport(post=transport.post)

    output_path = tmp_path / "report.md"
    returned = asyncio.run(
        client.artifacts.download_report(
            "notebook-live-0001", str(output_path), transport.artifact_id
        )
    )

    assert returned == str(output_path)
    assert output_path.read_text(encoding="utf-8") == "# Live Report\n\nReport body."
    assert transport.calls == [
        {
            "rpc_id": "gArtLc",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [
                [2],
                "notebook-live-0001",
                'NOT artifact.status = "ARTIFACT_STATUS_SUGGESTED"',
            ],
        }
    ]


class FakeArtifactDataTableDownloadTransport:
    def __init__(self, http_std):
        self.http_std = http_std
        self.calls: list[dict[str, object]] = []
        self.artifact_id = "artifact-table-0001"

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
        body_text = body.decode() if isinstance(body, bytes) else str(body)
        rpc_id = parse_qs(urlsplit(url).query)["rpcids"][0]
        payload_text = json.loads(parse_qs(body_text)["f.req"][0])[0][0][1]
        payload = json.loads(payload_text)
        self.calls.append(
            {
                "rpc_id": rpc_id,
                "source_path": parse_qs(urlsplit(url).query).get("source-path", [""])[0],
                "payload": payload,
            }
        )
        assert rpc_id == "gArtLc"
        rows = [
            [0, 0, [[["Name"]], [["Score"]]]],
            [0, 0, [[["Ada"]], [["42"]]]],
            [0, 0, [[["Linus"]], [["37"]]]],
        ]
        raw_table = [[[[[None, None, None, None, [None, None, rows]]]]]]
        row = [
            self.artifact_id,
            "Live Data Table",
            9,
            None,
            3,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            [1700000090],
            None,
            None,
            raw_table,
        ]
        envelope = [
            [
                "wrb.fr",
                rpc_id,
                json.dumps([[row]], separators=(",", ":")),
                None,
                None,
                None,
                "generic",
            ]
        ]
        return self.http_std.Response(
            status=200,
            url=url,
            headers={},
            body=(")]}'" + json.dumps(envelope, separators=(",", ":"))).encode(),
        )


def test_live_artifact_data_table_download_writes_upstream_csv(
    repo_root, tmp_path, monkeypatch
):
    monkeypatch.syspath_prepend(str(repo_root))

    from notebooklm import AuthTokens, NotebookLMClient
    from notebooklm import http_std

    transport = FakeArtifactDataTableDownloadTransport(http_std)
    client = NotebookLMClient(
        AuthTokens(
            cookies={
                ("SID", ".google.com", "/"): SENTINEL_SID,
                ("__Secure-1PSIDTS", ".google.com", "/"): SENTINEL_PSIDTS,
            },
            csrf_token=SENTINEL_CSRF,
            session_id=SENTINEL_SESSION,
        )
    )
    client.set_rpc_transport(post=transport.post)

    output_path = tmp_path / "table.csv"
    returned = asyncio.run(
        client.artifacts.download_data_table(
            "notebook-live-0001", str(output_path), transport.artifact_id
        )
    )

    assert returned == str(output_path)
    assert output_path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert output_path.read_text(encoding="utf-8-sig") == "Name,Score\nAda,42\nLinus,37\n"
    assert transport.calls == [
        {
            "rpc_id": "gArtLc",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [
                [2],
                "notebook-live-0001",
                'NOT artifact.status = "ARTIFACT_STATUS_SUGGESTED"',
            ],
        }
    ]


class FakeArtifactInteractiveDownloadTransport:
    def __init__(self, http_std):
        self.http_std = http_std
        self.calls: list[dict[str, object]] = []
        self.quiz_id = "artifact-quiz-0001"
        self.flashcards_id = "artifact-flashcards-0001"

    def _artifact_row(self, artifact_id, title, variant):
        return [
            artifact_id,
            title,
            4,
            None,
            3,
            None,
            None,
            None,
            None,
            [None, [variant]],
            None,
            None,
            None,
            None,
            None,
            [1700000100 + variant],
        ]

    def _html(self, payload):
        encoded = json.dumps(payload, separators=(",", ":")).replace('"', "&quot;")
        return f'<div data-app-data="{encoded}"></div>'

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
        body_text = body.decode() if isinstance(body, bytes) else str(body)
        rpc_id = parse_qs(urlsplit(url).query)["rpcids"][0]
        payload_text = json.loads(parse_qs(body_text)["f.req"][0])[0][0][1]
        payload = json.loads(payload_text)
        self.calls.append(
            {
                "rpc_id": rpc_id,
                "source_path": parse_qs(urlsplit(url).query).get("source-path", [""])[0],
                "payload": payload,
            }
        )
        if rpc_id == "gArtLc":
            result = [
                [
                    self._artifact_row(self.quiz_id, "Live Quiz", 2),
                    self._artifact_row(self.flashcards_id, "Live Flashcards", 1),
                ]
            ]
        elif rpc_id == "v9rmvd":
            if payload == [self.quiz_id]:
                result = [
                    [
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        [
                            self._html(
                                {
                                    "quiz": [
                                        {
                                            "question": "Who?",
                                            "answerOptions": [
                                                {"text": "Ada", "isCorrect": True},
                                                {"text": "Grace", "isCorrect": False},
                                            ],
                                            "hint": "First programmer",
                                        }
                                    ]
                                }
                            )
                        ],
                    ]
                ]
            else:
                result = [
                    [
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        [
                            self._html(
                                {"flashcards": [{"f": "Front", "b": "Back"}]}
                            )
                        ],
                    ]
                ]
        else:
            raise AssertionError(f"unexpected rpc id {rpc_id}")
        envelope = [
            [
                "wrb.fr",
                rpc_id,
                json.dumps(result, separators=(",", ":")),
                None,
                None,
                None,
                "generic",
            ]
        ]
        return self.http_std.Response(
            status=200,
            url=url,
            headers={},
            body=(")]}'" + json.dumps(envelope, separators=(",", ":"))).encode(),
        )


def test_live_interactive_artifact_downloads_use_upstream_html_app_data(
    repo_root, tmp_path, monkeypatch
):
    monkeypatch.syspath_prepend(str(repo_root))

    from notebooklm import AuthTokens, NotebookLMClient
    from notebooklm import http_std

    transport = FakeArtifactInteractiveDownloadTransport(http_std)
    client = NotebookLMClient(
        AuthTokens(
            cookies={
                ("SID", ".google.com", "/"): SENTINEL_SID,
                ("__Secure-1PSIDTS", ".google.com", "/"): SENTINEL_PSIDTS,
            },
            csrf_token=SENTINEL_CSRF,
            session_id=SENTINEL_SESSION,
        )
    )
    client.set_rpc_transport(post=transport.post)

    quiz_path = tmp_path / "quiz.json"
    flashcards_path = tmp_path / "cards.md"
    quiz_returned = asyncio.run(
        client.artifacts.download_quiz(
            "notebook-live-0001", str(quiz_path), transport.quiz_id
        )
    )
    cards_returned = asyncio.run(
        client.artifacts.download_flashcards(
            "notebook-live-0001",
            str(flashcards_path),
            transport.flashcards_id,
            output_format="markdown",
        )
    )

    assert quiz_returned == str(quiz_path)
    assert cards_returned == str(flashcards_path)
    assert json.loads(quiz_path.read_text(encoding="utf-8")) == {
        "title": "Live Quiz",
        "questions": [
            {
                "question": "Who?",
                "answerOptions": [
                    {"text": "Ada", "isCorrect": True},
                    {"text": "Grace", "isCorrect": False},
                ],
                "hint": "First programmer",
            }
        ],
    }
    assert flashcards_path.read_text(encoding="utf-8") == (
        "# Live Flashcards\n\n"
        "## Card 1\n\n"
        "**Q:** Front\n\n"
        "**A:** Back\n\n"
        "---\n"
    )
    assert transport.calls == [
        {
            "rpc_id": "gArtLc",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [
                [2],
                "notebook-live-0001",
                'NOT artifact.status = "ARTIFACT_STATUS_SUGGESTED"',
            ],
        },
        {
            "rpc_id": "v9rmvd",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [transport.quiz_id],
        },
        {
            "rpc_id": "gArtLc",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [
                [2],
                "notebook-live-0001",
                'NOT artifact.status = "ARTIFACT_STATUS_SUGGESTED"',
            ],
        },
        {
            "rpc_id": "v9rmvd",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [transport.flashcards_id],
        },
    ]


class FakeArtifactMindMapDownloadTransport:
    def __init__(self, http_std):
        self.http_std = http_std
        self.calls: list[dict[str, object]] = []
        self.note_map_id = "mind-map-note-0001"
        self.interactive_id = "artifact-mind-map-0001"

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
        body_text = body.decode() if isinstance(body, bytes) else str(body)
        rpc_id = parse_qs(urlsplit(url).query)["rpcids"][0]
        payload_text = json.loads(parse_qs(body_text)["f.req"][0])[0][0][1]
        payload = json.loads(payload_text)
        self.calls.append(
            {
                "rpc_id": rpc_id,
                "source_path": parse_qs(urlsplit(url).query).get("source-path", [""])[0],
                "payload": payload,
            }
        )
        if rpc_id == "cFji9":
            result = [
                [
                    [
                        None,
                        [
                            self.note_map_id,
                            '{"children":[{"name":"Note child"}]}',
                            [None, None, [1700000110]],
                            None,
                            "Note Mind Map",
                        ],
                    ]
                ]
            ]
        elif rpc_id == "gArtLc":
            row = [
                self.interactive_id,
                "Interactive Mind Map",
                4,
                None,
                3,
                None,
                None,
                None,
                None,
                [None, [4]],
                None,
                None,
                None,
                None,
                None,
                [1700000120],
            ]
            result = [[row]]
        elif rpc_id == "v9rmvd":
            result = [
                [
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    [None, None, None, '{"nodes":[{"name":"Interactive child"}]}'],
                ]
            ]
        else:
            raise AssertionError(f"unexpected rpc id {rpc_id}")
        envelope = [
            [
                "wrb.fr",
                rpc_id,
                json.dumps(result, separators=(",", ":")),
                None,
                None,
                None,
                "generic",
            ]
        ]
        return self.http_std.Response(
            status=200,
            url=url,
            headers={},
            body=(")]}'" + json.dumps(envelope, separators=(",", ":"))).encode(),
        )


def test_live_mind_map_download_supports_note_backed_and_interactive_shapes(
    repo_root, tmp_path, monkeypatch
):
    monkeypatch.syspath_prepend(str(repo_root))

    from notebooklm import AuthTokens, NotebookLMClient
    from notebooklm import http_std

    transport = FakeArtifactMindMapDownloadTransport(http_std)
    client = NotebookLMClient(
        AuthTokens(
            cookies={
                ("SID", ".google.com", "/"): SENTINEL_SID,
                ("__Secure-1PSIDTS", ".google.com", "/"): SENTINEL_PSIDTS,
            },
            csrf_token=SENTINEL_CSRF,
            session_id=SENTINEL_SESSION,
        )
    )
    client.set_rpc_transport(post=transport.post)

    note_path = tmp_path / "note-map.json"
    interactive_path = tmp_path / "interactive-map.json"
    note_returned = asyncio.run(
        client.artifacts.download_mind_map(
            "notebook-live-0001", str(note_path), transport.note_map_id
        )
    )
    interactive_returned = asyncio.run(
        client.artifacts.download_mind_map(
            "notebook-live-0001", str(interactive_path), transport.interactive_id
        )
    )

    assert note_returned == str(note_path)
    assert interactive_returned == str(interactive_path)
    assert json.loads(note_path.read_text(encoding="utf-8")) == {
        "children": [{"name": "Note child"}]
    }
    assert json.loads(interactive_path.read_text(encoding="utf-8")) == {
        "nodes": [{"name": "Interactive child"}]
    }
    assert transport.calls == [
        {
            "rpc_id": "cFji9",
            "source_path": "/notebook/notebook-live-0001",
            "payload": ["notebook-live-0001"],
        },
        {
            "rpc_id": "cFji9",
            "source_path": "/notebook/notebook-live-0001",
            "payload": ["notebook-live-0001"],
        },
        {
            "rpc_id": "gArtLc",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [
                [2],
                "notebook-live-0001",
                'NOT artifact.status = "ARTIFACT_STATUS_SUGGESTED"',
            ],
        },
        {
            "rpc_id": "v9rmvd",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [transport.interactive_id],
        },
    ]


class FakeArtifactGenerationTransport:
    def __init__(self, http_std):
        self.http_std = http_std
        self.calls: list[dict[str, object]] = []
        self.next_task = 0

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
        body_text = body.decode() if isinstance(body, bytes) else str(body)
        rpc_id = parse_qs(urlsplit(url).query)["rpcids"][0]
        payload_text = json.loads(parse_qs(body_text)["f.req"][0])[0][0][1]
        payload = json.loads(payload_text)
        self.calls.append(
            {
                "rpc_id": rpc_id,
                "source_path": parse_qs(urlsplit(url).query).get("source-path", [""])[0],
                "payload": payload,
            }
        )
        if rpc_id == "rLM1Ne":
            result = [
                [
                    "thought\nArtifact Notebook",
                    [
                        [
                            ["source-a"],
                            "Source A",
                            [None, None, [1700000020], None, 4, None, None, None],
                            [None, 2],
                        ],
                        [
                            ["source-b"],
                            "Source B",
                            [None, None, [1700000021], None, 4, None, None, None],
                            [None, 2],
                        ],
                    ],
                    "notebook-live-0001",
                    None,
                    None,
                    [None, False, None, None, None, [1700000001]],
                ]
            ]
        elif rpc_id == "R7cb6c":
            self.next_task += 1
            result = [[f"artifact-task-{self.next_task:02d}", None, None, None, 1]]
        elif rpc_id == "yyryJe":
            result = [[json.dumps({"name": "Generated Mind Map", "children": []})]]
        elif rpc_id == "CYK0Xb":
            result = ["mind-map-note-0001"]
        elif rpc_id == "cYAfTb":
            result = []
        else:
            raise AssertionError(f"unexpected rpc id {rpc_id}")
        envelope = [
            [
                "wrb.fr",
                rpc_id,
                json.dumps(result, separators=(",", ":")),
                None,
                None,
                None,
                "generic",
            ]
        ]
        return self.http_std.Response(
            status=200,
            url=url,
            headers={},
            body=(")]}'" + json.dumps(envelope, separators=(",", ":"))).encode(),
        )


def test_live_artifact_generation_uses_upstream_rpc_shapes(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))

    from notebooklm import (
        AuthTokens,
        NotebookLMClient,
        AudioFormat,
        AudioLength,
        InfographicDetail,
        InfographicOrientation,
        InfographicStyle,
        QuizDifficulty,
        QuizQuantity,
        ReportFormat,
        SlideDeckFormat,
        SlideDeckLength,
        VideoFormat,
        VideoStyle,
    )
    from notebooklm import http_std

    transport = FakeArtifactGenerationTransport(http_std)
    client = NotebookLMClient(
        AuthTokens(
            cookies={
                ("SID", ".google.com", "/"): SENTINEL_SID,
                ("__Secure-1PSIDTS", ".google.com", "/"): SENTINEL_PSIDTS,
            },
            csrf_token=SENTINEL_CSRF,
            session_id=SENTINEL_SESSION,
        )
    )
    client.set_rpc_transport(post=transport.post)

    audio = asyncio.run(
        client.artifacts.generate_audio(
            "notebook-live-0001",
            source_ids=None,
            language=None,
            instructions="audio instructions",
            audio_format=AudioFormat.DEBATE,
            audio_length=AudioLength.LONG,
        )
    )
    video = asyncio.run(
        client.artifacts.generate_video(
            "notebook-live-0001",
            ["source-a"],
            language="fr",
            instructions="video instructions",
            video_format=VideoFormat.EXPLAINER,
            video_style=VideoStyle.CUSTOM,
            style_prompt="  noir style  ",
        )
    )
    cinematic = asyncio.run(
        client.artifacts.generate_cinematic_video(
            "notebook-live-0001", ["source-a"], language="es", instructions="cine"
        )
    )
    report = asyncio.run(
        client.artifacts.generate_report(
            "notebook-live-0001",
            ReportFormat.BLOG_POST,
            ["source-a"],
            language="de",
            extra_instructions="extra",
        )
    )
    study = asyncio.run(
        client.artifacts.generate_study_guide(
            "notebook-live-0001", ["source-a"], language="it", extra_instructions="study"
        )
    )
    quiz = asyncio.run(
        client.artifacts.generate_quiz(
            "notebook-live-0001",
            ["source-a"],
            instructions="quiz",
            quantity=QuizQuantity.FEWER,
            difficulty=QuizDifficulty.HARD,
        )
    )
    flashcards = asyncio.run(
        client.artifacts.generate_flashcards(
            "notebook-live-0001",
            ["source-a"],
            instructions="cards",
            quantity=QuizQuantity.STANDARD,
            difficulty=QuizDifficulty.EASY,
        )
    )
    infographic = asyncio.run(
        client.artifacts.generate_infographic(
            "notebook-live-0001",
            ["source-a"],
            language="pt",
            instructions="info",
            orientation=InfographicOrientation.PORTRAIT,
            detail_level=InfographicDetail.DETAILED,
            style=InfographicStyle.EDITORIAL,
        )
    )
    slide_deck = asyncio.run(
        client.artifacts.generate_slide_deck(
            "notebook-live-0001",
            ["source-a"],
            language="ja",
            instructions="slides",
            slide_format=SlideDeckFormat.PRESENTER_SLIDES,
            slide_length=SlideDeckLength.SHORT,
        )
    )
    data_table = asyncio.run(
        client.artifacts.generate_data_table(
            "notebook-live-0001", ["source-a"], language="ko", instructions="table"
        )
    )
    mind_map = asyncio.run(
        client.artifacts.generate_mind_map(
            "notebook-live-0001", ["source-a"], language="en", instructions="map"
        )
    )

    assert [s.task_id for s in [audio, video, cinematic, report, study, quiz, flashcards, infographic, slide_deck, data_table]] == [
        f"artifact-task-{index:02d}" for index in range(1, 11)
    ]
    assert all(
        status.status == "in_progress"
        for status in [
            audio,
            video,
            cinematic,
            report,
            study,
            quiz,
            flashcards,
            infographic,
            slide_deck,
            data_table,
        ]
    )
    assert mind_map.note_id == "mind-map-note-0001"
    assert mind_map.mind_map == {"name": "Generated Mind Map", "children": []}

    source_ids_triple = [[["source-a"]]]
    source_ids_double = [["source-a"]]
    client_options = [
        2,
        None,
        None,
        [1, None, None, None, None, None, None, None, None, None, [1]],
        [[1, 4, 8, 2, 3, 6]],
    ]
    create_calls = [call for call in transport.calls if call["rpc_id"] == "R7cb6c"]
    assert transport.calls[0] == {
        "rpc_id": "rLM1Ne",
        "source_path": "/notebook/notebook-live-0001",
        "payload": ["notebook-live-0001", None, [2], None, 0],
    }
    assert len(create_calls) == 10
    assert all(call["source_path"] == "/notebook/notebook-live-0001" for call in create_calls)
    assert all(call["payload"][0] == client_options for call in create_calls)
    assert [call["payload"][2][2] for call in create_calls] == [1, 3, 3, 2, 2, 4, 4, 7, 8, 9]
    assert create_calls[0]["payload"][2][3] == [[["source-a"]], [["source-b"]]]
    assert create_calls[0]["payload"][2][6][1] == [
        "audio instructions",
        3,
        None,
        [["source-a"], ["source-b"]],
        "en",
        None,
        4,
    ]
    assert create_calls[1]["payload"][2][3] == source_ids_triple
    assert create_calls[1]["payload"][2][8][2] == [
        source_ids_double,
        "fr",
        "video instructions",
        None,
        1,
        None,
        "noir style",
    ]
    assert create_calls[2]["payload"][2][8][2] == [
        source_ids_double,
        "es",
        "cine",
        None,
        3,
    ]
    assert create_calls[3]["payload"][2][7][1][0:6] == [
        "Blog Post",
        "Insightful takeaways in readable article format",
        None,
        source_ids_double,
        "de",
        (
            "Write an engaging blog post that presents the key insights "
            "in an accessible, reader-friendly format. Include an attention-"
            "grabbing introduction, well-organized sections, and a compelling "
            "conclusion with takeaways.\n\nextra"
        ),
    ]
    assert create_calls[4]["payload"][2][7][1][0:2] == [
        "Study Guide",
        "Short-answer quiz, essay questions, glossary",
    ]
    assert create_calls[5]["payload"][2][9][1] == [
        2,
        None,
        "quiz",
        None,
        None,
        None,
        None,
        [1, 3],
    ]
    assert create_calls[6]["payload"][2][9][1] == [
        1,
        None,
        "cards",
        None,
        None,
        None,
        [1, 2],
    ]
    assert create_calls[7]["payload"][2][14] == [["info", "pt", None, 2, 3, 5]]
    assert create_calls[8]["payload"][2][16] == [["slides", "ja", 2, 2]]
    assert create_calls[9]["payload"][2][18] == [None, ["table", "ko"]]
    assert transport.calls[-3:] == [
        {
            "rpc_id": "yyryJe",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [
                source_ids_triple,
                None,
                None,
                None,
                None,
                ["interactive_mindmap", [["[CONTEXT]", "map"]], "en"],
                None,
                [2, None, [1]],
            ],
        },
        {
            "rpc_id": "CYK0Xb",
            "source_path": "/notebook/notebook-live-0001",
            "payload": ["notebook-live-0001", "", [1], None, "Generated Mind Map"],
        },
        {
            "rpc_id": "cYAfTb",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [
                "notebook-live-0001",
                "mind-map-note-0001",
                [[[json.dumps({"name": "Generated Mind Map", "children": []}), "Generated Mind Map", [], 0]]],
            ],
        },
    ]


class FakeMindMapLiveTransport:
    def __init__(self, http_std):
        self.http_std = http_std
        self.calls: list[dict[str, object]] = []
        self.artifact_id = "interactive-map-0001"
        self.note_id = "note-map-0001"
        self.interactive_title = "Interactive Map"
        self.note_title = "Note Map"

    def _artifact_row(self):
        return [
            self.artifact_id,
            self.interactive_title,
            4,
            None,
            3,
            None,
            None,
            None,
            None,
            [None, [4], None, json.dumps({"name": "Interactive Tree", "children": []})],
            None,
            None,
            None,
            None,
            None,
            [1700000050],
        ]

    def _note_row(self):
        return [
            None,
            [
                self.note_id,
                json.dumps({"name": self.note_title, "children": []}),
                [None, None, [1700000040]],
                None,
                self.note_title,
            ],
        ]

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
        body_text = body.decode() if isinstance(body, bytes) else str(body)
        rpc_id = parse_qs(urlsplit(url).query)["rpcids"][0]
        payload_text = json.loads(parse_qs(body_text)["f.req"][0])[0][0][1]
        payload = json.loads(payload_text)
        self.calls.append(
            {
                "rpc_id": rpc_id,
                "source_path": parse_qs(urlsplit(url).query).get("source-path", [""])[0],
                "payload": payload,
            }
        )
        if rpc_id == "rLM1Ne":
            result = [
                [
                    "thought\nMind Map Notebook",
                    [
                        [
                            ["source-a"],
                            "Source A",
                            [None, None, [1700000020], None, 4, None, None, None],
                            [None, 2],
                        ]
                    ],
                    "notebook-live-0001",
                    None,
                    None,
                    [None, False, None, None, None, [1700000001]],
                ]
            ]
        elif rpc_id == "R7cb6c":
            result = [[self.artifact_id, None, None, None, 1]]
        elif rpc_id == "gArtLc":
            result = [[self._artifact_row()]]
        elif rpc_id == "cFji9":
            result = [[self._note_row()]]
        elif rpc_id == "v9rmvd":
            result = [[None, None, None, None, None, None, None, None, None, self._artifact_row()[9]]]
        elif rpc_id == "rc3d8d":
            self.interactive_title = payload[0][1]
            result = []
        elif rpc_id == "cYAfTb":
            self.note_title = payload[2][0][0][1]
            result = []
        elif rpc_id in {"V5N4be", "AH0mwd"}:
            result = []
        else:
            raise AssertionError(f"unexpected rpc id {rpc_id}")
        envelope = [
            [
                "wrb.fr",
                rpc_id,
                json.dumps(result, separators=(",", ":")),
                None,
                None,
                None,
                "generic",
            ]
        ]
        return self.http_std.Response(
            status=200,
            url=url,
            headers={},
            body=(")]}'" + json.dumps(envelope, separators=(",", ":"))).encode(),
        )


def test_live_mind_maps_use_upstream_note_and_interactive_rpc_shapes(
    repo_root, monkeypatch
):
    monkeypatch.syspath_prepend(str(repo_root))

    from notebooklm import AuthTokens, MindMapKind, NotebookLMClient
    from notebooklm import http_std

    transport = FakeMindMapLiveTransport(http_std)
    client = NotebookLMClient(
        AuthTokens(
            cookies={
                ("SID", ".google.com", "/"): SENTINEL_SID,
                ("__Secure-1PSIDTS", ".google.com", "/"): SENTINEL_PSIDTS,
            },
            csrf_token=SENTINEL_CSRF,
            session_id=SENTINEL_SESSION,
        )
    )
    client.set_rpc_transport(post=transport.post)

    generated = asyncio.run(
        client.mind_maps.generate(
            "notebook-live-0001", kind=MindMapKind.INTERACTIVE, wait=True
        )
    )
    listed = asyncio.run(client.mind_maps.list("notebook-live-0001"))
    interactive_tree = asyncio.run(
        client.mind_maps.get_tree(
            "notebook-live-0001", transport.artifact_id, kind=MindMapKind.INTERACTIVE
        )
    )
    renamed_interactive = asyncio.run(
        client.mind_maps.rename(
            "notebook-live-0001",
            transport.artifact_id,
            "Renamed Interactive",
            kind=MindMapKind.INTERACTIVE,
        )
    )
    renamed_note = asyncio.run(
        client.mind_maps.rename(
            "notebook-live-0001",
            transport.note_id,
            "Renamed Note",
            kind=MindMapKind.NOTE_BACKED,
        )
    )
    deleted_interactive = asyncio.run(
        client.mind_maps.delete(
            "notebook-live-0001", transport.artifact_id, kind=MindMapKind.INTERACTIVE
        )
    )
    deleted_note = asyncio.run(
        client.mind_maps.delete(
            "notebook-live-0001", transport.note_id, kind=MindMapKind.NOTE_BACKED
        )
    )

    assert generated.id == transport.artifact_id
    assert generated.kind == MindMapKind.INTERACTIVE
    assert generated.tree == {"name": "Interactive Tree", "children": []}
    assert [(m.id, m.kind) for m in listed] == [
        (transport.note_id, MindMapKind.NOTE_BACKED),
        (transport.artifact_id, MindMapKind.INTERACTIVE),
    ]
    assert interactive_tree == {"name": "Interactive Tree", "children": []}
    assert renamed_interactive is not None
    assert renamed_interactive.title == "Renamed Interactive"
    assert renamed_note is not None
    assert renamed_note.title == "Renamed Note"
    assert deleted_interactive is None
    assert deleted_note is None

    client_options = [
        2,
        None,
        None,
        [1, None, None, None, None, None, None, None, None, None, [1]],
        [[1, 4, 8, 2, 3, 6]],
    ]
    assert transport.calls[0] == {
        "rpc_id": "rLM1Ne",
        "source_path": "/notebook/notebook-live-0001",
        "payload": ["notebook-live-0001", None, [2], None, 0],
    }
    assert transport.calls[1] == {
        "rpc_id": "R7cb6c",
        "source_path": "/notebook/notebook-live-0001",
        "payload": [
            client_options,
            "notebook-live-0001",
            [
                None,
                None,
                4,
                [[["source-a"]]],
                None,
                None,
                None,
                None,
                None,
                [None, [4]],
            ],
        ],
    }
    assert {
        (call["rpc_id"], call["source_path"], json.dumps(call["payload"], sort_keys=True))
        for call in transport.calls
    } >= {
        (
            "v9rmvd",
            "/notebook/notebook-live-0001",
            json.dumps([transport.artifact_id], sort_keys=True),
        ),
        (
            "rc3d8d",
            "/notebook/notebook-live-0001",
            json.dumps([[transport.artifact_id, "Renamed Interactive"], [["title"]]], sort_keys=True),
        ),
        (
            "cYAfTb",
            "/notebook/notebook-live-0001",
            json.dumps(
                [
                    "notebook-live-0001",
                    transport.note_id,
                    [
                        [
                            [
                                json.dumps({"name": "Note Map", "children": []}),
                                "Renamed Note",
                                [],
                                0,
                            ]
                        ]
                    ],
                ],
                sort_keys=True,
            ),
        ),
        (
            "V5N4be",
            "/notebook/notebook-live-0001",
            json.dumps([[2], transport.artifact_id], sort_keys=True),
        ),
        (
            "AH0mwd",
            "/notebook/notebook-live-0001",
            json.dumps(["notebook-live-0001", None, [transport.note_id]], sort_keys=True),
        ),
    }


class FakeSettingsTransport:
    def __init__(self, http_std):
        self.http_std = http_std
        self.calls: list[dict[str, object]] = []
        self.language = "ja"

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
        body_text = body.decode() if isinstance(body, bytes) else str(body)
        rpc_id = parse_qs(urlsplit(url).query)["rpcids"][0]
        payload_text = json.loads(parse_qs(body_text)["f.req"][0])[0][0][1]
        payload = json.loads(payload_text)
        self.calls.append(
            {
                "rpc_id": rpc_id,
                "source_path": parse_qs(urlsplit(url).query).get("source-path", [""])[0],
                "payload": payload,
            }
        )
        if rpc_id == "ZwVcOc":
            result = [[None, [None, 100, 50], [None, None, None, None, [self.language]]]]
        elif rpc_id == "hT54vc":
            self.language = payload[0][0][1][0][4][0]
            result = [None, None, [None, None, None, None, [self.language]]]
        elif rpc_id == "ozz5Z":
            result = [[None, ["NOTEBOOKLM_TIER_PRO"]]]
        else:
            raise AssertionError(f"unexpected rpc id {rpc_id}")
        envelope = [
            [
                "wrb.fr",
                rpc_id,
                json.dumps(result, separators=(",", ":")),
                None,
                None,
                None,
                "generic",
            ]
        ]
        return self.http_std.Response(
            status=200,
            url=url,
            headers={},
            body=(")]}'" + json.dumps(envelope, separators=(",", ":"))).encode(),
        )


def test_live_settings_use_upstream_rpc_shapes(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))

    from notebooklm import AuthTokens, NotebookLMClient
    from notebooklm import http_std

    transport = FakeSettingsTransport(http_std)
    client = NotebookLMClient(
        AuthTokens(
            cookies={
                ("SID", ".google.com", "/"): SENTINEL_SID,
                ("__Secure-1PSIDTS", ".google.com", "/"): SENTINEL_PSIDTS,
            },
            csrf_token=SENTINEL_CSRF,
            session_id=SENTINEL_SESSION,
        )
    )
    client.set_rpc_transport(post=transport.post)

    language = asyncio.run(client.settings.get_output_language())
    limits = asyncio.run(client.settings.get_account_limits())
    tier = asyncio.run(client.settings.get_account_tier())
    changed = asyncio.run(client.settings.set_output_language("es"))
    empty = asyncio.run(client.settings.set_output_language(""))

    assert language == "ja"
    assert limits.notebook_limit == 100
    assert limits.source_limit == 50
    assert limits.raw_limits == (None, 100, 50)
    assert tier.tier == "NOTEBOOKLM_TIER_PRO"
    assert tier.plan_name == "Google AI Pro"
    assert changed == "es"
    assert empty is None
    assert transport.calls == [
        {
            "rpc_id": "ZwVcOc",
            "source_path": "/",
            "payload": [
                None,
                [1, None, None, None, None, None, None, None, None, None, [1]],
            ],
        },
        {
            "rpc_id": "ZwVcOc",
            "source_path": "/",
            "payload": [
                None,
                [1, None, None, None, None, None, None, None, None, None, [1]],
            ],
        },
        {
            "rpc_id": "ozz5Z",
            "source_path": "/",
            "payload": [
                [
                    [
                        [None, "1", 627],
                        [None, None, None, None, None, None, None, None, None, [None, None, 2]],
                        1,
                    ]
                ]
            ],
        },
        {
            "rpc_id": "hT54vc",
            "source_path": "/",
            "payload": [[[None, [[None, None, None, None, ["es"]]]]]],
        },
    ]


class FakeSharingTransport:
    def __init__(self, http_std):
        self.http_std = http_std
        self.calls: list[dict[str, object]] = []
        self.public = False
        self.users: dict[str, int] = {}

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
        body_text = body.decode() if isinstance(body, bytes) else str(body)
        rpc_id = parse_qs(urlsplit(url).query)["rpcids"][0]
        payload_text = json.loads(parse_qs(body_text)["f.req"][0])[0][0][1]
        payload = json.loads(payload_text)
        self.calls.append(
            {
                "rpc_id": rpc_id,
                "source_path": parse_qs(urlsplit(url).query).get("source-path", [""])[0],
                "payload": payload,
            }
        )
        if rpc_id == "JFMDGd":
            result = [
                [
                    [email, permission, [], [email.split("@")[0].title(), None]]
                    for email, permission in sorted(self.users.items())
                ],
                [1 if self.public else 0],
                1000,
            ]
        elif rpc_id == "QDyure":
            share_spec = payload[0][0]
            if share_spec[1] is None:
                self.public = share_spec[2][0] == 1
            else:
                email, _unused, permission = share_spec[1][0]
                if permission == 4:
                    self.users.pop(email, None)
                else:
                    self.users[email] = permission
            result = []
        elif rpc_id == "s0tc2d":
            result = []
        elif rpc_id == "RGP97b":
            result = []
        else:
            raise AssertionError(f"unexpected rpc id {rpc_id}")
        envelope = [
            [
                "wrb.fr",
                rpc_id,
                json.dumps(result, separators=(",", ":")),
                None,
                None,
                None,
                "generic",
            ]
        ]
        return self.http_std.Response(
            status=200,
            url=url,
            headers={},
            body=(")]}'" + json.dumps(envelope, separators=(",", ":"))).encode(),
        )


def test_live_sharing_uses_upstream_rpc_shapes(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))

    from notebooklm import AuthTokens, NotebookLMClient, SharePermission, ShareViewLevel
    from notebooklm import http_std

    transport = FakeSharingTransport(http_std)
    client = NotebookLMClient(
        AuthTokens(
            cookies={
                ("SID", ".google.com", "/"): SENTINEL_SID,
                ("__Secure-1PSIDTS", ".google.com", "/"): SENTINEL_PSIDTS,
            },
            csrf_token=SENTINEL_CSRF,
            session_id=SENTINEL_SESSION,
        )
    )
    client.set_rpc_transport(post=transport.post)

    initial = asyncio.run(client.sharing.get_status("notebook-live-0001"))
    public = asyncio.run(client.sharing.set_public("notebook-live-0001", True))
    chat_only = asyncio.run(
        client.sharing.set_view_level("notebook-live-0001", ShareViewLevel.CHAT_ONLY)
    )
    added = asyncio.run(
        client.sharing.add_user(
            "notebook-live-0001",
            "reader@example.test",
            SharePermission.VIEWER,
            notify=False,
            welcome_message="Welcome",
        )
    )
    updated = asyncio.run(
        client.sharing.update_user(
            "notebook-live-0001", "reader@example.test", SharePermission.EDITOR
        )
    )
    removed = asyncio.run(
        client.sharing.remove_user("notebook-live-0001", "reader@example.test")
    )
    legacy_share = asyncio.run(
        client.notebooks.share(
            "notebook-live-0001", public=True, artifact_id="artifact/unsafe?"
        )
    )

    assert initial.is_public is False
    assert public.is_public is True
    assert public.share_url == "https://notebooklm.google.com/notebook/notebook-live-0001"
    assert chat_only.view_level is ShareViewLevel.CHAT_ONLY
    assert [(u.email, u.permission) for u in added.shared_users] == [
        ("reader@example.test", SharePermission.VIEWER)
    ]
    assert updated.shared_users[0].permission is SharePermission.EDITOR
    assert removed.shared_users == []
    assert legacy_share == {
        "public": True,
        "url": (
            "https://notebooklm.google.com/notebook/notebook-live-0001"
            "?artifactId=artifact%2Funsafe%3F"
        ),
        "artifact_id": "artifact/unsafe?",
    }
    assert transport.calls == [
        {
            "rpc_id": "JFMDGd",
            "source_path": "/notebook/notebook-live-0001",
            "payload": ["notebook-live-0001", [2]],
        },
        {
            "rpc_id": "QDyure",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [
                [["notebook-live-0001", None, [1], [1, ""]]],
                1,
                None,
                [2],
            ],
        },
        {
            "rpc_id": "JFMDGd",
            "source_path": "/notebook/notebook-live-0001",
            "payload": ["notebook-live-0001", [2]],
        },
        {
            "rpc_id": "s0tc2d",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [
                "notebook-live-0001",
                [[None, None, None, None, None, None, None, None, [[1]]]],
            ],
        },
        {
            "rpc_id": "JFMDGd",
            "source_path": "/notebook/notebook-live-0001",
            "payload": ["notebook-live-0001", [2]],
        },
        {
            "rpc_id": "QDyure",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [
                [
                    [
                        "notebook-live-0001",
                        [["reader@example.test", None, 3]],
                        None,
                        [0, "Welcome"],
                    ]
                ],
                0,
                None,
                [2],
            ],
        },
        {
            "rpc_id": "JFMDGd",
            "source_path": "/notebook/notebook-live-0001",
            "payload": ["notebook-live-0001", [2]],
        },
        {
            "rpc_id": "QDyure",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [
                [
                    [
                        "notebook-live-0001",
                        [["reader@example.test", None, 2]],
                        None,
                        [1, ""],
                    ]
                ],
                0,
                None,
                [2],
            ],
        },
        {
            "rpc_id": "JFMDGd",
            "source_path": "/notebook/notebook-live-0001",
            "payload": ["notebook-live-0001", [2]],
        },
        {
            "rpc_id": "QDyure",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [
                [
                    [
                        "notebook-live-0001",
                        [["reader@example.test", None, 4]],
                        None,
                        [0, ""],
                    ]
                ],
                0,
                None,
                [2],
            ],
        },
        {
            "rpc_id": "JFMDGd",
            "source_path": "/notebook/notebook-live-0001",
            "payload": ["notebook-live-0001", [2]],
        },
        {
            "rpc_id": "RGP97b",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [[1], "notebook-live-0001", "artifact/unsafe?"],
        },
    ]


class FakeResearchTransport:
    def __init__(self, http_std):
        self.http_std = http_std
        self.calls: list[dict[str, object]] = []

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
        body_text = body.decode() if isinstance(body, bytes) else str(body)
        rpc_id = parse_qs(urlsplit(url).query)["rpcids"][0]
        payload_text = json.loads(parse_qs(body_text)["f.req"][0])[0][0][1]
        payload = json.loads(payload_text)
        self.calls.append(
            {
                "rpc_id": rpc_id,
                "source_path": parse_qs(urlsplit(url).query).get("source-path", [""])[0],
                "payload": payload,
            }
        )
        if rpc_id == "Ljjv0c":
            result = ["fast-task-0001"]
        elif rpc_id == "QA9ei":
            result = ["deep-task-0001", "deep-report-0001"]
        elif rpc_id == "e3bVqc":
            result = [
                [
                    "fast-task-0001",
                    [
                        None,
                        ["Synthetic research query"],
                        None,
                        [
                            [
                                [
                                    "https://example.test/research",
                                    "Research Result",
                                    "desc",
                                    1,
                                ],
                                [
                                    None,
                                    ["Deep Report", "# Report\n\nBody"],
                                    None,
                                    5,
                                ],
                            ],
                            "Research summary",
                        ],
                        2,
                    ],
                ]
            ]
        elif rpc_id == "LBwxtb":
            result = [
                [
                    [["source-report-0001"], "Deep Report"],
                    [["source-web-0001"], "Research Result"],
                ]
            ]
        else:
            raise AssertionError(f"unexpected rpc id {rpc_id}")
        envelope = [
            [
                "wrb.fr",
                rpc_id,
                json.dumps(result, separators=(",", ":")),
                None,
                None,
                None,
                "generic",
            ]
        ]
        return self.http_std.Response(
            status=200,
            url=url,
            headers={},
            body=(")]}'" + json.dumps(envelope, separators=(",", ":"))).encode(),
        )


def test_live_research_uses_upstream_rpc_shapes(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))

    from notebooklm import AuthTokens, NotebookLMClient, ResearchSource
    from notebooklm import http_std

    transport = FakeResearchTransport(http_std)
    client = NotebookLMClient(
        AuthTokens(
            cookies={
                ("SID", ".google.com", "/"): SENTINEL_SID,
                ("__Secure-1PSIDTS", ".google.com", "/"): SENTINEL_PSIDTS,
            },
            csrf_token=SENTINEL_CSRF,
            session_id=SENTINEL_SESSION,
        )
    )
    client.set_rpc_transport(post=transport.post)

    fast = asyncio.run(
        client.research.start("notebook-live-0001", "Synthetic research query")
    )
    deep = asyncio.run(
        client.research.start(
            "notebook-live-0001", "Synthetic deep query", mode="deep"
        )
    )
    polled = asyncio.run(client.research.poll("notebook-live-0001", fast.task_id))
    waited = asyncio.run(
        client.research.wait_for_completion(
            "notebook-live-0001", fast.task_id, timeout=1, initial_interval=0.01
        )
    )
    imported = asyncio.run(
        client.research.import_sources(
            "notebook-live-0001",
            fast.task_id,
            [
                ResearchSource(
                    url="",
                    title="Deep Report",
                    result_type=5,
                    research_task_id=fast.task_id,
                    report_markdown="# Report\n\nBody",
                ),
                ResearchSource(
                    url="https://example.test/research",
                    title="Research Result",
                    research_task_id=fast.task_id,
                ),
            ],
        )
    )

    assert fast.task_id == "fast-task-0001"
    assert fast.report_id is None
    assert fast.mode == "fast"
    assert deep.task_id == "deep-task-0001"
    assert deep.report_id == "deep-report-0001"
    assert polled.task_id == "fast-task-0001"
    assert polled.status.value == "completed"
    assert polled.query == "Synthetic research query"
    assert polled.summary == "Research summary"
    assert polled.report == "# Report\n\nBody"
    assert waited.task_id == "fast-task-0001"
    assert waited.status.value == "completed"
    assert [(src.url, src.title, src.result_type) for src in polled.sources] == [
        ("https://example.test/research", "Research Result", 1),
        ("", "Deep Report", 5),
    ]
    assert imported == [
        {"id": "source-report-0001", "title": "Deep Report"},
        {"id": "source-web-0001", "title": "Research Result"},
    ]
    assert transport.calls == [
        {
            "rpc_id": "Ljjv0c",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [["Synthetic research query", 1], None, 1, "notebook-live-0001"],
        },
        {
            "rpc_id": "QA9ei",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [
                None,
                [1],
                ["Synthetic deep query", 1],
                5,
                "notebook-live-0001",
            ],
        },
        {
            "rpc_id": "e3bVqc",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [None, None, "notebook-live-0001"],
        },
        {
            "rpc_id": "e3bVqc",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [None, None, "notebook-live-0001"],
        },
        {
            "rpc_id": "LBwxtb",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [
                None,
                [1],
                "fast-task-0001",
                "notebook-live-0001",
                [
                    [
                        None,
                        ["Deep Report", "# Report\n\nBody"],
                        None,
                        3,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        3,
                    ],
                    [
                        None,
                        None,
                        ["https://example.test/research", "Research Result"],
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        2,
                    ],
                ],
            ],
        },
    ]


class FakeChatTransport:
    def __init__(self, http_std):
        self.http_std = http_std
        self.calls: list[dict[str, object]] = []

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
        body_text = body.decode() if isinstance(body, bytes) else str(body)
        if "GenerateFreeFormStreamed" in url:
            fields = parse_qs(body_text, keep_blank_values=True)
            payload = json.loads(fields["f.req"][0])
            self.calls.append(
                {
                    "rpc_id": "stream",
                    "path": urlsplit(url).path,
                    "query": parse_qs(urlsplit(url).query),
                    "payload": payload,
                    "at": fields.get("at"),
                }
            )
            inner = [
                [
                    "Synthetic live answer",
                    None,
                    ["stream-id-0001"],
                    None,
                    [None, None, None, [], 1],
                ]
            ]
            envelope = [["wrb.fr", "stream", json.dumps(inner, separators=(",", ":"))]]
            return self.http_std.Response(
                status=200,
                url=url,
                headers={},
                body=(")]}'\n" + json.dumps(envelope, separators=(",", ":"))).encode(),
            )
        rpc_id = parse_qs(urlsplit(url).query)["rpcids"][0]
        payload_text = json.loads(parse_qs(body_text)["f.req"][0])[0][0][1]
        payload = json.loads(payload_text)
        self.calls.append(
            {
                "rpc_id": rpc_id,
                "source_path": parse_qs(urlsplit(url).query).get("source-path", [""])[0],
                "payload": payload,
            }
        )
        if rpc_id == "s0tc2d":
            result = []
        elif rpc_id == "hPTbtc":
            result = [[["conversation-live-0001"]]]
        elif rpc_id == "khqZz":
            result = [
                [
                    [None, None, 2, None, [["First answer"]]],
                    [None, None, 1, "First question"],
                ]
            ]
        elif rpc_id == "J7Gthc":
            result = []
        elif rpc_id == "CYK0Xb":
            result = [["saved-note-0001", None, None, None, "Server Saved Title"]]
        else:
            raise AssertionError(f"unexpected rpc id {rpc_id}")
        envelope = [
            [
                "wrb.fr",
                rpc_id,
                json.dumps(result, separators=(",", ":")),
                None,
                None,
                None,
                "generic",
            ]
        ]
        return self.http_std.Response(
            status=200,
            url=url,
            headers={},
            body=(")]}'" + json.dumps(envelope, separators=(",", ":"))).encode(),
        )


def test_live_chat_management_uses_upstream_rpc_shapes(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))

    from notebooklm import AuthTokens, ChatGoal, ChatResponseLength, NotebookLMClient
    from notebooklm import http_std

    transport = FakeChatTransport(http_std)
    client = NotebookLMClient(
        AuthTokens(
            cookies={
                ("SID", ".google.com", "/"): SENTINEL_SID,
                ("__Secure-1PSIDTS", ".google.com", "/"): SENTINEL_PSIDTS,
            },
            csrf_token=SENTINEL_CSRF,
            session_id=SENTINEL_SESSION,
        )
    )
    client.set_rpc_transport(post=transport.post)

    configured = asyncio.run(
        client.chat.configure(
            "notebook-live-0001",
            ChatGoal.CUSTOM,
            ChatResponseLength.LONGER,
            "Answer tersely.",
        )
    )
    conversation_id = asyncio.run(client.chat.get_conversation_id("notebook-live-0001"))
    turns = asyncio.run(
        client.chat.get_conversation_turns(
            "notebook-live-0001", conversation_id, limit=2
        )
    )
    history = asyncio.run(
        client.chat.get_history(
            "notebook-live-0001", conversation_id=conversation_id, limit=2
        )
    )
    deleted = asyncio.run(
        client.chat.delete_conversation("notebook-live-0001", conversation_id)
    )

    assert configured is None
    assert conversation_id == "conversation-live-0001"
    assert turns[0][0][4][0][0] == "First answer"
    assert history == [("First question", "First answer")]
    assert deleted is True
    assert transport.calls == [
        {
            "rpc_id": "s0tc2d",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [
                "notebook-live-0001",
                [
                    [
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        [[2, "Answer tersely."], [4]],
                    ]
                ],
            ],
        },
        {
            "rpc_id": "hPTbtc",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [[], None, "notebook-live-0001", 1],
        },
        {
            "rpc_id": "khqZz",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [[], None, None, "conversation-live-0001", 2],
        },
        {
            "rpc_id": "khqZz",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [[], None, None, "conversation-live-0001", 2],
        },
        {
            "rpc_id": "J7Gthc",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [[], "conversation-live-0001", None, 1],
        },
    ]


def test_live_chat_ask_uses_upstream_streaming_shape(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))

    from notebooklm import AuthTokens, NotebookLMClient
    from notebooklm import http_std

    transport = FakeChatTransport(http_std)
    client = NotebookLMClient(
        AuthTokens(
            cookies={
                ("SID", ".google.com", "/"): SENTINEL_SID,
                ("__Secure-1PSIDTS", ".google.com", "/"): SENTINEL_PSIDTS,
            },
            csrf_token=SENTINEL_CSRF,
            session_id=SENTINEL_SESSION,
            authuser=3,
        )
    )
    client.set_rpc_transport(post=transport.post)

    first = asyncio.run(
        client.chat.ask(
            "notebook-live-0001",
            "What is alpha?",
            source_ids=["source-a"],
        )
    )
    follow_up = asyncio.run(
        client.chat.ask(
            "notebook-live-0001",
            "Can you elaborate?",
            source_ids=["source-a"],
            conversation_id=first.conversation_id,
        )
    )

    assert first.answer == "Synthetic live answer"
    assert first.conversation_id == "conversation-live-0001"
    assert first.turn_number == 1
    assert first.is_follow_up is False
    assert follow_up.turn_number == 2
    assert follow_up.is_follow_up is True
    stream_payload = json.loads(transport.calls[0]["payload"][1])
    follow_up_payload = json.loads(transport.calls[2]["payload"][1])
    assert transport.calls[0]["rpc_id"] == "stream"
    assert transport.calls[0]["path"].endswith("/GenerateFreeFormStreamed")
    assert transport.calls[0]["query"] == {
        "bl": ["boq_labs-tailwind-frontend_20260301.03_p0"],
        "hl": ["en"],
        "_reqid": ["1"],
        "rt": ["c"],
        "f.sid": [SENTINEL_SESSION],
        "authuser": ["3"],
    }
    assert transport.calls[0]["at"] == [SENTINEL_CSRF]
    assert stream_payload == [
        [[["source-a"]]],
        "What is alpha?",
        None,
        [2, None, [1], [1]],
        None,
        None,
        None,
        "notebook-live-0001",
        1,
    ]
    assert transport.calls[1] == {
        "rpc_id": "hPTbtc",
        "source_path": "/notebook/notebook-live-0001",
        "payload": [[], None, "notebook-live-0001", 1],
    }
    assert follow_up_payload == [
        [[["source-a"]]],
        "Can you elaborate?",
        [["Synthetic live answer", None, 2], ["What is alpha?", None, 1]],
        [2, None, [1], [1]],
        "conversation-live-0001",
        None,
        None,
        "notebook-live-0001",
        1,
    ]


def test_live_chat_save_answer_as_note_uses_upstream_saved_chat_shape(
    repo_root, monkeypatch
):
    monkeypatch.syspath_prepend(str(repo_root))

    from notebooklm import AuthTokens, AskResult, ChatReference, NotebookLMClient
    from notebooklm import http_std

    transport = FakeChatTransport(http_std)
    client = NotebookLMClient(
        AuthTokens(
            cookies={
                ("SID", ".google.com", "/"): SENTINEL_SID,
                ("__Secure-1PSIDTS", ".google.com", "/"): SENTINEL_PSIDTS,
            },
            csrf_token=SENTINEL_CSRF,
            session_id=SENTINEL_SESSION,
        )
    )
    client.set_rpc_transport(post=transport.post)
    ask_result = AskResult(
        answer="Alpha [1].",
        conversation_id="conversation-live-0001",
        turn_number=1,
        is_follow_up=False,
        references=[
            ChatReference(
                source_id="source-a",
                citation_number=1,
                cited_text="Alpha source",
                start_char=5,
                end_char=17,
                chunk_id="chunk-a",
                passage_id="passage-a",
            )
        ],
    )

    note = asyncio.run(
        client.chat.save_answer_as_note(
            "notebook-live-0001", ask_result, title="Saved Alpha"
        )
    )

    flags = [0, 0, 0, None, None, None, None, 0, 0]
    source_passage = [
        None,
        None,
        None,
        [[None, 5, 17]],
        [[
            [
                0,
                12,
                [[[0, 12, ["Alpha source", flags]]], [None, 1]],
            ]
        ]],
        [[["passage-a"], "source-a"]],
        ["chunk-a"],
    ]
    assert note.id == "saved-note-0001"
    assert note.title == "Server Saved Title"
    assert note.content == "Alpha [1]."
    assert transport.calls == [
        {
            "rpc_id": "CYK0Xb",
            "source_path": "/notebook/notebook-live-0001",
            "payload": [
                "notebook-live-0001",
                "Alpha [1].",
                [2],
                [source_passage],
                "Saved Alpha",
                [
                    [
                        [
                            [
                                0,
                                6,
                                [[[0, 6, ["Alpha.", flags]]], [None, 1]],
                            ]
                        ],
                        [[["chunk-a"], [None, 0, 5]]],
                    ],
                    None,
                    None,
                    [[["chunk-a"], source_passage]],
                    1,
                ],
                [2],
            ],
        }
    ]


def test_generate_video_rejects_upstream_invalid_style_prompt_combinations(
    repo_root, monkeypatch
):
    monkeypatch.syspath_prepend(str(repo_root))

    from notebooklm import AuthTokens, NotebookLMClient, VideoFormat, VideoStyle
    from notebooklm.errors import ValidationError

    client = NotebookLMClient(AuthTokens(cookies={}, csrf_token="", session_id=""))

    with pytest.raises(ValidationError, match="style_prompt is required"):
        asyncio.run(
            client.artifacts.generate_video(
                "notebook-live-0001",
                ["source-a"],
                video_style=VideoStyle.CUSTOM,
            )
        )
    with pytest.raises(ValidationError, match="style_prompt requires"):
        asyncio.run(
            client.artifacts.generate_video(
                "notebook-live-0001",
                ["source-a"],
                style_prompt="noir",
            )
        )
    with pytest.raises(ValidationError, match="not supported for cinematic"):
        asyncio.run(
            client.artifacts.generate_video(
                "notebook-live-0001",
                ["source-a"],
                video_format=VideoFormat.CINEMATIC,
                video_style=VideoStyle.CUSTOM,
                style_prompt="noir",
            )
        )


RPC_CAP_BYTES = 50 * 1024 * 1024


def _live_rpc_error_client(
    notebooklm, *, rate_limit_max_retries=3, server_error_max_retries=3
):
    return notebooklm.NotebookLMClient(
        notebooklm.AuthTokens(
            cookies={
                ("SID", ".google.com", "/"): SENTINEL_SID,
                ("__Secure-1PSIDTS", ".google.com", "/"): SENTINEL_PSIDTS,
            },
            csrf_token=SENTINEL_CSRF,
            session_id=SENTINEL_SESSION,
        ),
        rate_limit_max_retries=rate_limit_max_retries,
        server_error_max_retries=server_error_max_retries,
    )


def _rpc_status_response(http_std, status, *, headers=None, result=None):
    if result is None:
        body = b""
    else:
        envelope = [
            [
                "wrb.fr",
                "wXbhsf",
                json.dumps(result, separators=(",", ":")),
                None,
                None,
                None,
                "generic",
            ]
        ]
        body = (")]}'" + json.dumps(envelope, separators=(",", ":"))).encode()
    return http_std.Response(
        status=status,
        url="https://notebooklm.google.com/rpc",
        headers=headers or {},
        body=body,
    )


class SequenceRpcPost:
    def __init__(self, *items):
        self.items = list(items)
        self.calls = []

    def __call__(
        self,
        url,
        *,
        body=None,
        headers=None,
        timeout=None,
        max_redirects=5,
        max_body_bytes=None,
    ):
        self.calls.append(
            {
                "rpc_id": parse_qs(urlsplit(url).query)["rpcids"][0],
                "max_body_bytes": max_body_bytes,
            }
        )
        item = self.items.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def test_live_rpc_retries_429_retry_after_then_returns_success(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))

    import notebooklm
    from notebooklm import http_std
    from notebooklm.rpc.types import RPCMethod

    post = SequenceRpcPost(
        _rpc_status_response(http_std, 429, headers={"retry-after": "0"}),
        _rpc_status_response(http_std, 200, result=[["ok"]]),
    )
    client = _live_rpc_error_client(notebooklm, rate_limit_max_retries=1)
    client.set_rpc_transport(post=post)

    result = asyncio.run(
        client.rpc_call(RPCMethod.LIST_NOTEBOOKS, [None, 1, None, [2]])
    )

    assert result == [["ok"]]
    assert [call["rpc_id"] for call in post.calls] == ["wXbhsf", "wXbhsf"]


def test_live_rpc_exhausted_429_maps_to_public_rate_limit_error(
    repo_root, monkeypatch
):
    monkeypatch.syspath_prepend(str(repo_root))

    import notebooklm
    from notebooklm import http_std
    from notebooklm.rpc.types import RPCMethod

    post = SequenceRpcPost(_rpc_status_response(http_std, 429, headers={"retry-after": "5"}))
    client = _live_rpc_error_client(notebooklm, rate_limit_max_retries=0)
    client.set_rpc_transport(post=post)

    with pytest.raises(notebooklm.RateLimitError) as exc:
        asyncio.run(client.rpc_call(RPCMethod.LIST_NOTEBOOKS, [None, 1, None, [2]]))

    assert type(exc.value) is notebooklm.RateLimitError
    assert exc.value.method_id == "wXbhsf"
    assert exc.value.retry_after == 5


def test_live_rpc_exhausted_5xx_and_network_errors_map_to_public_errors(
    repo_root, monkeypatch
):
    monkeypatch.syspath_prepend(str(repo_root))

    import notebooklm
    from notebooklm import http_std
    from notebooklm.errors import HTTPTransportError
    from notebooklm.rpc.types import RPCMethod

    server_post = SequenceRpcPost(_rpc_status_response(http_std, 503))
    server_client = _live_rpc_error_client(notebooklm, server_error_max_retries=0)
    server_client.set_rpc_transport(post=server_post)

    with pytest.raises(notebooklm.ServerError) as server_exc:
        asyncio.run(
            server_client.rpc_call(RPCMethod.LIST_NOTEBOOKS, [None, 1, None, [2]])
        )
    assert type(server_exc.value) is notebooklm.ServerError
    assert server_exc.value.method_id == "wXbhsf"
    assert server_exc.value.status_code == 503

    network_post = SequenceRpcPost(HTTPTransportError("offline transport failed"))
    network_client = _live_rpc_error_client(notebooklm, server_error_max_retries=0)
    network_client.set_rpc_transport(post=network_post)

    with pytest.raises(notebooklm.NetworkError) as network_exc:
        asyncio.run(
            network_client.rpc_call(RPCMethod.LIST_NOTEBOOKS, [None, 1, None, [2]])
        )
    assert type(network_exc.value) is notebooklm.NetworkError
    assert network_exc.value.method_id == "wXbhsf"
    assert isinstance(network_exc.value.original_error, HTTPTransportError)


def test_live_rpc_uses_50mib_response_cap_and_maps_size_error(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))

    import notebooklm
    from notebooklm.errors import BodyTooLargeError
    from notebooklm.rpc.types import RPCMethod

    post = SequenceRpcPost(BodyTooLargeError("response body exceeded 52428800 bytes"))
    client = _live_rpc_error_client(notebooklm)
    client.set_rpc_transport(post=post)

    with pytest.raises(notebooklm.RPCResponseTooLargeError) as exc:
        asyncio.run(client.rpc_call(RPCMethod.LIST_NOTEBOOKS, [None, 1, None, [2]]))

    assert post.calls[0]["max_body_bytes"] == RPC_CAP_BYTES
    assert type(exc.value) is notebooklm.RPCResponseTooLargeError
    assert exc.value.method_id == "wXbhsf"
    assert exc.value.limit_bytes == RPC_CAP_BYTES
