"""Phase 2F: stdlib-only interactive login browser/CDP primitives.

This test file covers bounded interactive-login slices that replace upstream
browser-login dependencies with isolated loopback CDP primitives.

Phase 2F-A: launch/probe/storage-state building blocks.
Phase 2F-B: minimal stdlib DevTools websocket command for
``Network.getAllCookies``.
Phase 2F-C: public CLI interactive-login wiring.
Phase 2F-D: wait-for-login cookie capture and ``--fresh`` profile reset wiring.

Hard boundaries:
* no real browser launch in tests (runner is injected);
* no real DevTools/network access in tests (HTTP/CDP readers are injected);
* no password/token/cookie value in public summaries;
* no Path.home(), keychain, DPAPI, Secret Service, OAuth automation, or Google
  network call.
"""

from __future__ import annotations

import importlib
import json
import sqlite3
from pathlib import Path

import pytest


def _run(cli, capsys, argv):
    code = cli.console(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture()
def il():
    return importlib.import_module("notebooklm.interactive_login")


@pytest.fixture()
def cli():
    return importlib.import_module("notebooklm.cli")


def test_supported_interactive_browsers_match_oracle(il):
    assert tuple(il.INTERACTIVE_LOGIN_BROWSERS) == ("chromium", "chrome", "msedge")
    assert il.normalize_interactive_browser("Chrome") == "chrome"
    with pytest.raises(ValueError):
        il.normalize_interactive_browser("firefox")


def test_build_browser_argv_is_loopback_cdp_and_isolated_profile(il, tmp_path):
    profile = tmp_path / "browser-profile"
    argv = il.build_browser_argv(
        "chrome",
        executable="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        user_data_dir=profile,
        debugging_port=9222,
        url="https://notebooklm.google.com/",
    )

    assert argv[0].endswith("Google Chrome")
    assert "--remote-debugging-address=127.0.0.1" in argv
    assert "--remote-debugging-port=9222" in argv
    assert f"--user-data-dir={profile}" in argv
    assert "--no-first-run" in argv
    assert "--no-default-browser-check" in argv
    assert argv[-1] == "https://notebooklm.google.com/"
    assert all("0.0.0.0" not in part for part in argv)


def test_build_browser_argv_rejects_implicit_profile(il):
    with pytest.raises(Exception):
        il.build_browser_argv(
            "chrome",
            executable="chrome",
            user_data_dir=Path("."),
            debugging_port=9222,
        )


def test_macos_browser_resolver_checks_user_local_applications_before_path(il):
    user_chrome = (
        "/virtual-root/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    )
    checked = []

    def fake_exists(path):
        checked.append(path)
        return path == user_chrome

    def forbidden_which(_name):
        raise AssertionError(
            "PATH should not be needed when user-local Chrome.app exists"
        )

    got = il.resolve_browser_executable(
        "chrome",
        os_name="macOS",
        environ={"HOME": "/virtual-root"},
        exists=fake_exists,
        which=forbidden_which,
    )

    assert got == user_chrome
    assert user_chrome in checked


def test_macos_browser_resolver_uses_process_home_environment_by_default(
    il, monkeypatch
):
    user_chrome = (
        "/virtual-root/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    )
    monkeypatch.setenv("HOME", "/virtual-root")

    def fake_exists(path):
        return path == user_chrome

    def forbidden_which(_name):
        raise AssertionError(
            "PATH should not be needed when user-local Chrome.app exists"
        )

    assert (
        il.resolve_browser_executable(
            "chrome", os_name="macOS", exists=fake_exists, which=forbidden_which
        )
        == user_chrome
    )


def test_prepare_profile_fresh_recreates_only_explicit_directory(
    il, tmp_path, monkeypatch
):
    def boom():
        raise AssertionError("Path.home must not be consulted")

    monkeypatch.setattr(Path, "home", staticmethod(boom))
    profile = tmp_path / "profile"
    nested = profile / "old.txt"
    profile.mkdir()
    nested.write_text("old", encoding="utf-8")

    got = il.prepare_browser_profile(profile, fresh=True)

    assert got == profile
    assert profile.is_dir()
    assert not nested.exists()
    assert not (tmp_path / ".notebooklm").exists()


def test_launch_browser_session_uses_injected_runner_no_shell_and_redacted_summary(
    il, tmp_path, monkeypatch
):
    def boom():
        raise AssertionError("Path.home must not be consulted")

    monkeypatch.setattr(Path, "home", staticmethod(boom))
    calls = []

    class FakeProcess:
        pid = 4242

    def fake_runner(argv, **kwargs):
        calls.append((argv, kwargs))
        return FakeProcess()

    result = il.launch_browser_session(
        "chromium",
        executable="/usr/bin/chromium",
        user_data_dir=tmp_path / "profile",
        debugging_port=9333,
        runner=fake_runner,
    )

    assert result["browser"] == "chromium"
    assert result["debugging_host"] == "127.0.0.1"
    assert result["debugging_port"] == 9333
    assert result["process_id"] == 4242
    assert result["profile_prepared"] is True
    assert "argv" not in result
    assert "user_data_dir" not in result
    assert calls and calls[0][1]["shell"] is False
    assert calls[0][0][-1] == il.NOTEBOOKLM_LOGIN_URL


def test_devtools_version_probe_requires_loopback_and_redacts_bad_payload(il):
    class Response:
        def __init__(self, text):
            self._text = text

        def text(self):
            return self._text

    def ok_get(url, **kwargs):
        assert url == "http://127.0.0.1:9222/json/version"
        assert kwargs["max_body_bytes"] <= 65536
        return Response(
            json.dumps(
                {"webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/abc"}
            )
        )

    assert (
        il.read_devtools_websocket_url(9222, http_get=ok_get)
        == "ws://127.0.0.1:9222/devtools/browser/abc"
    )

    def bad_get(url, **kwargs):
        return Response(
            json.dumps(
                {"webSocketDebuggerUrl": "ws://0.0.0.0:9222/devtools/browser/abc"}
            )
        )

    with pytest.raises(Exception) as exc:
        il.read_devtools_websocket_url(9222, http_get=bad_get)
    msg = str(exc.value)
    assert "DevTools" in msg
    assert "0.0.0.0" not in msg
    assert "abc" not in msg


def test_devtools_page_probe_selects_notebooklm_target_and_redacts(il):
    class Response:
        def __init__(self, text):
            self._text = text

        def text(self):
            return self._text

    def ok_get(url, **kwargs):
        assert url == "http://127.0.0.1:9222/json/list"
        assert kwargs["max_body_bytes"] <= 65536
        return Response(
            json.dumps(
                [
                    {
                        "type": "page",
                        "url": "https://example.com/ignored",
                        "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/ignored",
                    },
                    {
                        "type": "page",
                        "url": "https://notebooklm.google.com/?authuser=0&secret=redacted",
                        "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/notebook-target",
                    },
                ]
            )
        )

    assert (
        il.read_devtools_page_websocket_url(9222, http_get=ok_get)
        == "ws://127.0.0.1:9222/devtools/page/notebook-target"
    )

    def bad_get(url, **kwargs):
        return Response(
            json.dumps(
                [
                    {
                        "type": "page",
                        "url": "https://notebooklm.google.com/?secret=should-not-leak",
                        "webSocketDebuggerUrl": "ws://evil.example:9222/devtools/page/secret-target",
                    },
                ]
            )
        )

    with pytest.raises(Exception) as exc:
        il.read_devtools_page_websocket_url(9222, http_get=bad_get)
    msg = str(exc.value)
    assert "DevTools" in msg
    assert "evil.example" not in msg
    assert "secret-target" not in msg
    assert "should-not-leak" not in msg


def test_devtools_attach_new_target_url_is_loopback_notebooklm_only(il):
    url = il.devtools_new_url(9223)

    assert url.startswith("http://127.0.0.1:9223/json/new?")
    assert "https%3A%2F%2Fnotebooklm.google.com%2F" in url
    assert "0.0.0.0" not in url

    with pytest.raises(Exception) as host_exc:
        il.devtools_new_url(9223, host="192.0.2.10")
    assert "loopback" in str(host_exc.value).lower()

    with pytest.raises(Exception) as target_exc:
        il.devtools_new_url(9223, target_url="https://evil.example/?token=secret")
    msg = str(target_exc.value)
    assert "NotebookLM" in msg
    assert "evil.example" not in msg
    assert "secret" not in msg


def test_devtools_attach_opens_notebooklm_target_if_absent_and_redacts(il):
    class Response:
        def __init__(self, text="[]", status=200):
            self._text = text
            self.status = status

        def text(self):
            return self._text

    calls = []
    lists = [
        json.dumps(
            [
                {
                    "type": "page",
                    "url": "https://example.com/ignored?token=secret",
                    "webSocketDebuggerUrl": "ws://127.0.0.1:9223/devtools/page/ignored",
                }
            ]
        ),
        json.dumps(
            [
                {
                    "type": "page",
                    "url": "https://notebooklm.google.com/?authuser=0&token=secret",
                    "webSocketDebuggerUrl": "ws://127.0.0.1:9223/devtools/page/notebook-target",
                }
            ]
        ),
    ]

    def fake_get(url, **kwargs):
        calls.append(("get", url, kwargs))
        return Response(lists.pop(0))

    def fake_request(method, url, **kwargs):
        calls.append(("request", method, url, kwargs))
        assert method == "PUT"
        assert url.startswith("http://127.0.0.1:9223/json/new?")
        assert "https%3A%2F%2Fnotebooklm.google.com%2F" in url
        assert kwargs["max_body_bytes"] <= 65536
        return Response(status=200)

    ws, opened = il.ensure_devtools_page_websocket_url(
        9223,
        http_get=fake_get,
        http_request=fake_request,
        open_if_missing=True,
        attempts=2,
        delay_seconds=0,
    )

    assert ws == "ws://127.0.0.1:9223/devtools/page/notebook-target"
    assert opened is True
    assert [c[0] for c in calls] == ["get", "request", "get"]

    def failing_request(*_args, **_kwargs):
        raise RuntimeError("https://notebooklm.google.com/?token=secret")

    with pytest.raises(Exception) as exc:
        il.open_devtools_notebooklm_page(9223, http_request=failing_request)
    msg = str(exc.value)
    assert "secret" not in msg
    assert "notebooklm.google.com" not in msg


def test_devtools_discovery_scans_ports_and_prefers_notebooklm(il):
    class Response:
        def __init__(self, text):
            self._text = text

        def text(self):
            return self._text

    seen = []

    def fake_get(url, **kwargs):
        seen.append(url)
        assert kwargs["max_body_bytes"] <= 65536
        if ":9222/" in url:
            return Response(
                json.dumps(
                    [
                        {
                            "type": "page",
                            "url": "https://example.com/?token=secret",
                            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/ignored",
                        }
                    ]
                )
            )
        return Response(
            json.dumps(
                [
                    {
                        "type": "page",
                        "url": "https://notebooklm.google.com/?token=secret",
                        "webSocketDebuggerUrl": "ws://127.0.0.1:9223/devtools/page/notebook-target",
                    }
                ]
            )
        )

    port, ws = il.discover_devtools_page_websocket_url(
        candidate_ports=[9222, 9223],
        http_get=fake_get,
    )

    assert (port, ws) == (
        9223,
        "ws://127.0.0.1:9223/devtools/page/notebook-target",
    )
    assert seen == [
        "http://127.0.0.1:9222/json/list",
        "http://127.0.0.1:9223/json/list",
    ]


def test_devtools_discovery_no_target_fails_closed_and_redacted(il):
    class Response:
        def text(self):
            return json.dumps(
                [
                    {
                        "type": "page",
                        "url": "https://example.com/?secret=do-not-leak",
                        "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/secret-target",
                    }
                ]
            )

    with pytest.raises(Exception) as exc:
        il.discover_devtools_page_websocket_url(
            candidate_ports=[9222],
            http_get=lambda *_args, **_kwargs: Response(),
        )

    msg = str(exc.value)
    assert "NotebookLM page target" in msg
    assert "do-not-leak" not in msg
    assert "secret-target" not in msg


def test_storage_state_from_cdp_cookies_filters_google_and_preserves_values_only_on_disk(
    il,
):
    state = il.storage_state_from_cdp_cookies(
        [
            {
                "name": "SID",
                "value": "sid-cookie-value",
                "domain": ".google.com",
                "path": "/",
                "httpOnly": True,
                "secure": True,
                "sameSite": "Lax",
                "expires": 1999999999,
            },
            {
                "name": "__Secure-1PSIDTS",
                "value": "psidts-cookie-value",
                "domain": ".google.com",
                "path": "/",
                "httpOnly": True,
                "secure": True,
            },
            {
                "name": "EVIL",
                "value": "evil-cookie-value",
                "domain": "evil.example",
                "path": "/",
            },
        ]
    )

    names = sorted(c["name"] for c in state["cookies"])
    assert names == ["SID", "__Secure-1PSIDTS"]
    assert any(c["value"] == "sid-cookie-value" for c in state["cookies"])
    summary = il.redacted_storage_summary(state)
    dumped = json.dumps(summary, sort_keys=True)
    assert summary["cookie_count"] == 2
    assert summary["required_cookies"]["SID"] is True
    assert "sid-cookie-value" not in dumped
    assert "psidts-cookie-value" not in dumped
    assert "evil-cookie-value" not in dumped


def _server_text_frame(payload: str) -> bytes:
    data = payload.encode("utf-8")
    if len(data) < 126:
        return bytes([0x81, len(data)]) + data
    return bytes([0x81, 126]) + len(data).to_bytes(2, "big") + data


def _client_text_payload(frame: bytes) -> str:
    assert frame[0] == 0x81
    assert frame[1] & 0x80, "client frame must be masked"
    length = frame[1] & 0x7F
    offset = 2
    if length == 126:
        length = int.from_bytes(frame[offset : offset + 2], "big")
        offset += 2
    mask = frame[offset : offset + 4]
    offset += 4
    data = frame[offset : offset + length]
    return bytes(b ^ mask[i % 4] for i, b in enumerate(data)).decode("utf-8")


class _FakeSocket:
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.sent = []
        self.closed = False

    def sendall(self, data):
        self.sent.append(data)

    def recv(self, size):
        if not self._chunks:
            return b""
        chunk = self._chunks.pop(0)
        if len(chunk) > size:
            self._chunks.insert(0, chunk[size:])
            return chunk[:size]
        return chunk

    def settimeout(self, _timeout):
        pass

    def close(self):
        self.closed = True


def test_cdp_get_all_cookies_uses_loopback_websocket_and_masked_command(il):
    response = json.dumps(
        {
            "id": 1,
            "result": {
                "cookies": [
                    {
                        "name": "SID",
                        "value": "sid-cookie-value",
                        "domain": ".google.com",
                        "path": "/",
                    },
                ]
            },
        }
    )
    fake = _FakeSocket(
        [
            b"HTTP/1.1 101 Switching Protocols\r\n"
            b"Upgrade: websocket\r\n"
            b"Connection: Upgrade\r\n"
            b"Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=\r\n"
            b"\r\n",
            _server_text_frame(response),
        ]
    )
    calls = []

    def connect(address, timeout=None):
        calls.append((address, timeout))
        return fake

    cookies = il.read_cdp_all_cookies(
        "ws://127.0.0.1:9222/devtools/browser/session-id",
        socket_factory=connect,
        key_factory=lambda: "dGhlIHNhbXBsZSBub25jZQ==",
        mask_factory=lambda n: b"\x01\x02\x03\x04"[:n],
    )

    assert cookies[0]["name"] == "SID"
    assert calls == [(("127.0.0.1", 9222), 2.0)]
    handshake = fake.sent[0].decode("ascii")
    assert handshake.startswith("GET /devtools/browser/session-id HTTP/1.1\r\n")
    assert "Host: 127.0.0.1:9222\r\n" in handshake
    assert "Upgrade: websocket\r\n" in handshake
    assert "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n" in handshake
    command = json.loads(_client_text_payload(fake.sent[1]))
    assert command == {"id": 1, "method": "Network.getAllCookies"}
    assert fake.closed is True


def test_cdp_get_all_cookies_rejects_non_loopback_without_echoing_url(il):
    with pytest.raises(Exception) as exc:
        il.read_cdp_all_cookies("ws://evil.example:9222/devtools/browser/secret-token")
    msg = str(exc.value)
    assert "loopback" in msg.lower()
    assert "evil.example" not in msg
    assert "secret-token" not in msg


def test_capture_retries_redacted_cdp_connect_failure_without_partial_state(il, tmp_path):
    dest = tmp_path / "storage_state.json"
    errors = []
    response = json.dumps(
        {
            "id": 1,
            "result": {
                "cookies": [
                    {"name": "SID", "value": "sid", "domain": ".google.com", "path": "/"},
                    {
                        "name": "__Secure-1PSIDTS",
                        "value": "sidts",
                        "domain": ".google.com",
                        "path": "/",
                    },
                ]
            },
        }
    )
    fake = _FakeSocket(
        [
            b"HTTP/1.1 101 Switching Protocols\r\n"
            b"Upgrade: websocket\r\n"
            b"Connection: Upgrade\r\n"
            b"Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=\r\n\r\n",
            _server_text_frame(response),
        ]
    )
    calls = []

    def connect(*_args, **_kwargs):
        calls.append("connect")
        if len(calls) == 1:
            raise ConnectionRefusedError("synthetic-endpoint-should-not-leak")
        return fake

    def reader():
        try:
            return il.read_cdp_all_cookies(
                "ws://127.0.0.1:9222/devtools/page/raw-endpoint-should-not-leak",
                socket_factory=connect,
                key_factory=lambda: "dGhlIHNhbXBsZSBub25jZQ==",
                mask_factory=lambda n: b"\x01\x02\x03\x04"[:n],
            )
        except il.NetworkError as exc:
            errors.append(str(exc))
            assert not dest.exists()
            raise

    summary = il.capture_cdp_cookies_until_ready(
        dest, cookie_reader=reader, attempts=2, delay_seconds=0, sleep=lambda _x: None
    )

    assert summary["capture_attempts"] == 2
    assert calls == ["connect", "connect"]
    assert errors and "ConnectionRefusedError" in errors[0]
    assert "synthetic-endpoint-should-not-leak" not in errors[0]
    assert "raw-endpoint-should-not-leak" not in errors[0]
    assert dest.exists()


def test_capture_repeated_cdp_connect_failure_leaves_no_storage_state(il, tmp_path):
    dest = tmp_path / "storage_state.json"

    def reader():
        return il.read_cdp_all_cookies(
            "ws://127.0.0.1:9222/devtools/page/raw-endpoint-should-not-leak",
            socket_factory=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                ConnectionRefusedError("synthetic-endpoint-should-not-leak")
            ),
        )

    with pytest.raises(il.NetworkError) as excinfo:
        il.capture_cdp_cookies_until_ready(
            dest, cookie_reader=reader, attempts=2, delay_seconds=0, sleep=lambda _x: None
        )

    assert not dest.exists()
    assert "synthetic-endpoint-should-not-leak" not in str(excinfo.value)
    assert "raw-endpoint-should-not-leak" not in str(excinfo.value)


def test_import_cdp_cookies_to_storage_state_writes_and_summarizes_without_values(
    il, tmp_path
):
    dest = tmp_path / "storage_state.json"

    def fake_cookie_reader():
        return [
            {
                "name": "SID",
                "value": "sid-cookie-value",
                "domain": ".google.com",
                "path": "/",
            },
            {
                "name": "__Secure-1PSIDTS",
                "value": "psidts-cookie-value",
                "domain": ".google.com",
                "path": "/",
            },
        ]

    summary = il.import_cdp_cookies_to_storage_state(
        dest, cookie_reader=fake_cookie_reader
    )

    saved = json.loads(dest.read_text(encoding="utf-8"))
    assert sorted(c["name"] for c in saved["cookies"]) == ["SID", "__Secure-1PSIDTS"]
    assert "sid-cookie-value" in dest.read_text(encoding="utf-8")
    dumped = json.dumps(summary, sort_keys=True)
    assert summary["source_kind"] == il.SOURCE_KIND_INTERACTIVE_BROWSER
    assert summary["cookie_count"] == 2
    assert summary["required_cookies"]["SID"] is True
    assert "sid-cookie-value" not in dumped
    assert "psidts-cookie-value" not in dumped


def test_capture_cdp_cookies_until_ready_waits_and_writes_only_success(il, tmp_path):
    dest = tmp_path / "storage_state.json"
    calls = []
    sleeps = []
    batches = [
        [
            {
                "name": "SID",
                "value": "sid-cookie-value",
                "domain": ".google.com",
                "path": "/",
            },
        ],
        [
            {
                "name": "SID",
                "value": "sid-cookie-value",
                "domain": ".google.com",
                "path": "/",
            },
            {
                "name": "__Secure-1PSIDTS",
                "value": "psidts-cookie-value",
                "domain": ".google.com",
                "path": "/",
            },
        ],
    ]

    def reader():
        calls.append("read")
        return batches.pop(0)

    summary = il.capture_cdp_cookies_until_ready(
        dest,
        cookie_reader=reader,
        attempts=3,
        delay_seconds=1.25,
        sleep=sleeps.append,
    )

    assert summary["has_required_cookies"] is True
    assert summary["capture_attempts"] == 2
    assert calls == ["read", "read"]
    assert sleeps == [1.25]
    saved = json.loads(dest.read_text(encoding="utf-8"))
    assert sorted(c["name"] for c in saved["cookies"]) == ["SID", "__Secure-1PSIDTS"]
    assert "sid-cookie-value" not in json.dumps(summary, sort_keys=True)
    assert "psidts-cookie-value" not in json.dumps(summary, sort_keys=True)


def test_capture_cdp_cookies_until_ready_timeout_does_not_write_partial_state(
    il, tmp_path
):
    dest = tmp_path / "storage_state.json"

    def reader():
        return [
            {
                "name": "SID",
                "value": "sid-cookie-value",
                "domain": ".google.com",
                "path": "/",
            },
        ]

    with pytest.raises(il.AuthenticationError) as excinfo:
        il.capture_cdp_cookies_until_ready(
            dest,
            cookie_reader=reader,
            attempts=2,
            delay_seconds=0,
            sleep=lambda _seconds: None,
        )

    assert not dest.exists()
    msg = str(excinfo.value).lower()
    assert "required" in msg and "cookie" in msg
    assert "sid-cookie-value" not in msg


def test_capture_cdp_cookies_until_ready_retries_network_error_without_writing(
    il, tmp_path
):
    dest = tmp_path / "storage_state.json"
    calls = []
    sleeps = []

    def reader():
        calls.append("read")
        if len(calls) == 1:
            raise il.NetworkError("transient DevTools failure")
        assert not dest.exists()
        return [
            {"name": "SID", "value": "sid", "domain": ".google.com", "path": "/"},
            {
                "name": "__Secure-1PSIDTS",
                "value": "sidts",
                "domain": ".google.com",
                "path": "/",
            },
        ]

    summary = il.capture_cdp_cookies_until_ready(
        dest,
        cookie_reader=reader,
        attempts=2,
        delay_seconds=0.25,
        sleep=sleeps.append,
    )

    assert summary["capture_attempts"] == 2
    assert calls == ["read", "read"]
    assert sleeps == [0.25]
    assert dest.exists()


def test_cli_interactive_login_wires_primitives_and_redacts_output(
    cli, capsys, tmp_path, monkeypatch
):
    calls = []

    def fake_launch(browser, **kwargs):
        calls.append(("launch", browser, kwargs))
        return {
            "source_kind": "interactive_browser",
            "browser": browser,
            "debugging_host": "127.0.0.1",
            "debugging_port": kwargs["debugging_port"],
            "profile_prepared": True,
            "process_id": 4242,
            "url_opened": True,
        }

    def fake_ws(port, **kwargs):
        calls.append(("probe", port, kwargs))
        return "ws://127.0.0.1:9222/devtools/browser/session-id"

    def fake_cookies(ws):
        calls.append(("cookies", ws, {}))
        return [
            {
                "name": "SID",
                "value": "sid-cookie-value",
                "domain": ".google.com",
                "path": "/",
            },
            {
                "name": "__Secure-1PSIDTS",
                "value": "psidts-cookie-value",
                "domain": ".google.com",
                "path": "/",
            },
            {
                "name": "TRACK",
                "value": "tracker-value",
                "domain": "tracker.example",
                "path": "/",
            },
        ]

    monkeypatch.setattr(cli._il, "launch_browser_session", fake_launch)
    monkeypatch.setattr(cli._il, "read_devtools_page_websocket_url", fake_ws)
    monkeypatch.setattr(cli._il, "read_cdp_all_cookies", fake_cookies)

    code, out, err = _run(
        cli,
        capsys,
        ["--storage", str(tmp_path), "login", "--browser", "chrome", "--json"],
    )

    assert code == 0, err
    data = json.loads(out)
    assert data["profile"] == "default"
    assert data["source_kind"] == "interactive_browser"
    assert data["browser"] == "chrome"
    assert data["cookie_count"] == 2
    assert data["auth_source_written"] is False
    assert data["required_cookies"]["SID"] is True
    dumped = json.dumps(data, sort_keys=True)
    assert "sid-cookie-value" not in dumped
    assert "psidts-cookie-value" not in dumped
    assert "tracker-value" not in dumped
    assert str(tmp_path) not in dumped
    assert "devtools/browser/session-id" not in dumped

    storage_path = cli._profiles.ProfileStore(str(tmp_path)).storage_state_path(
        "default"
    )
    saved = json.loads(storage_path.read_text(encoding="utf-8"))
    assert sorted(c["name"] for c in saved["cookies"]) == ["SID", "__Secure-1PSIDTS"]
    assert "sid-cookie-value" in storage_path.read_text(encoding="utf-8")
    assert (
        not cli._profiles.ProfileStore(str(tmp_path))
        .auth_source_path("default")
        .exists()
    )
    launch = calls[0]
    assert launch[0] == "launch"
    assert launch[1] == "chrome"
    assert launch[2]["debugging_port"] == 9222
    assert launch[2]["user_data_dir"] == cli._profiles.ProfileStore(
        str(tmp_path)
    ).browser_profile_dir("default")
    assert calls[1][0:2] == ("probe", 9222)
    assert calls[2][0:2] == ("probe", 9222)
    assert calls[3][0] == "cookies"


def test_cli_interactive_login_defaults_to_upstream_chromium(
    cli, capsys, tmp_path, monkeypatch
):
    seen = []
    monkeypatch.setattr(
        cli._il,
        "launch_browser_session",
        lambda browser, **kw: (
            seen.append(browser)
            or {
                "source_kind": "interactive_browser",
                "browser": browser,
                "debugging_host": "127.0.0.1",
                "debugging_port": kw["debugging_port"],
                "profile_prepared": True,
                "process_id": None,
                "url_opened": True,
            }
        ),
    )
    monkeypatch.setattr(
        cli._il,
        "read_devtools_page_websocket_url",
        lambda port, **kw: "ws://127.0.0.1:9222/devtools/browser/session-id",
    )
    monkeypatch.setattr(
        cli._il,
        "read_cdp_all_cookies",
        lambda ws: [
            {
                "name": "SID",
                "value": "sid-cookie-value",
                "domain": ".google.com",
                "path": "/",
            },
            {
                "name": "__Secure-1PSIDTS",
                "value": "psidts-cookie-value",
                "domain": ".google.com",
                "path": "/",
            },
        ],
    )

    code, out, err = _run(cli, capsys, ["--storage", str(tmp_path), "login", "--json"])

    assert code == 0, err
    assert seen == ["chromium"]
    assert json.loads(out)["browser"] == "chromium"


def test_cli_interactive_login_accepts_explicit_debugging_port(
    cli, capsys, tmp_path, monkeypatch
):
    ports = []

    monkeypatch.setattr(
        cli._il,
        "launch_browser_session",
        lambda browser, **kw: (
            ports.append(("launch", kw["debugging_port"]))
            or {
                "source_kind": "interactive_browser",
                "browser": browser,
                "debugging_host": "127.0.0.1",
                "debugging_port": kw["debugging_port"],
                "profile_prepared": True,
                "process_id": None,
                "url_opened": True,
            }
        ),
    )
    monkeypatch.setattr(
        cli._il,
        "read_devtools_page_websocket_url",
        lambda port, **kw: (
            ports.append(("probe", port))
            or "ws://127.0.0.1:9223/devtools/page/session-id"
        ),
    )
    monkeypatch.setattr(
        cli._il,
        "read_cdp_all_cookies",
        lambda ws: [
            {
                "name": "SID",
                "value": "sid-cookie-value",
                "domain": ".google.com",
                "path": "/",
            },
            {
                "name": "__Secure-1PSIDTS",
                "value": "psidts-cookie-value",
                "domain": ".google.com",
                "path": "/",
            },
        ],
    )

    code, out, err = _run(
        cli,
        capsys,
        [
            "--storage",
            str(tmp_path),
            "login",
            "--browser",
            "chrome",
            "--debugging-port",
            "9223",
            "--json",
        ],
    )

    assert code == 0, err
    data = json.loads(out)
    assert data["debugging_port"] == 9223
    assert ports == [("launch", 9223), ("probe", 9223), ("probe", 9223)]


@pytest.mark.parametrize("browser", ["chrome", "chromium", "msedge"])
def test_cli_interactive_login_attach_devtools_skips_launch_and_redacts_output(
    cli, capsys, tmp_path, monkeypatch, browser
):
    calls = []

    def forbidden_launch(*_args, **_kwargs):
        raise AssertionError("attach-devtools must not launch a browser")

    def forbidden_browser_cookie_import(*_args, **_kwargs):
        raise AssertionError("attach-devtools must not import browser-cookie stores")

    def fake_ensure(port, **kwargs):
        calls.append(("ensure", port, kwargs))
        return "ws://127.0.0.1:9223/devtools/page/notebook-target", True

    def fake_cookies(ws):
        calls.append(("cookies", ws, {}))
        return [
            {
                "name": "SID",
                "value": "sid-cookie-value",
                "domain": ".google.com",
                "path": "/",
            },
            {
                "name": "__Secure-1PSIDTS",
                "value": "psidts-cookie-value",
                "domain": ".google.com",
                "path": "/",
            },
            {
                "name": "TRACK",
                "value": "tracker-value",
                "domain": "tracker.example",
                "path": "/",
            },
        ]

    monkeypatch.setattr(cli._il, "launch_browser_session", forbidden_launch)
    monkeypatch.setattr(cli._il, "ensure_devtools_page_websocket_url", fake_ensure)
    monkeypatch.setattr(
        cli._il,
        "read_devtools_page_websocket_url",
        lambda port: (
            calls.append(("resolve", port, {}))
            or "ws://127.0.0.1:9223/devtools/page/current-notebook-target"
        ),
    )
    monkeypatch.setattr(cli._il, "read_cdp_all_cookies", fake_cookies)
    monkeypatch.setattr(
        cli._bc, "import_live_browser_to_storage_state", forbidden_browser_cookie_import
    )
    monkeypatch.setattr(
        cli._bc, "import_to_storage_state", forbidden_browser_cookie_import
    )

    code, out, err = _run(
        cli,
        capsys,
        [
            "--storage",
            str(tmp_path),
            "login",
            "--browser",
            browser,
            "--attach-devtools",
            "--debugging-port",
            "9223",
            "--json",
        ],
    )

    assert code == 0, err
    data = json.loads(out)
    assert data["browser"] == browser
    assert data["debugging_port"] == 9223
    assert data["attached"] is True
    assert data["target_opened"] is True
    assert data["profile_prepared"] is False
    assert data["process_id"] is None
    assert data["auth_source_written"] is False
    assert data["has_required_cookies"] is True
    dumped = json.dumps(data, sort_keys=True)
    assert "sid-cookie-value" not in dumped
    assert "psidts-cookie-value" not in dumped
    assert "tracker-value" not in dumped
    assert "notebook-target" not in dumped
    assert str(tmp_path) not in dumped

    storage_path = cli._profiles.ProfileStore(str(tmp_path)).storage_state_path(
        "default"
    )
    saved = json.loads(storage_path.read_text(encoding="utf-8"))
    assert sorted(c["name"] for c in saved["cookies"]) == ["SID", "__Secure-1PSIDTS"]
    assert calls[0][0:2] == ("ensure", 9223)
    assert calls[0][2]["open_if_missing"] is True
    assert calls[1][0:2] == ("resolve", 9223)
    assert calls[2][0] == "cookies"


def test_cli_attach_devtools_rejects_conflicting_login_modes(cli, capsys, tmp_path):
    code, _out, err = _run(
        cli,
        capsys,
        [
            "--storage",
            str(tmp_path),
            "login",
            "--browser",
            "chrome",
            "--attach-devtools",
            "--fresh",
        ],
    )
    assert code == 64
    assert "attach" in err.lower()
    assert "fresh" in err.lower()

    code, _out, err = _run(
        cli,
        capsys,
        [
            "--storage",
            str(tmp_path),
            "login",
            "--browser-cookies",
            "chrome",
            "--attach-devtools",
            "--os",
            "macOS",
        ],
    )
    assert code == 64
    assert "attach" in err.lower()
    assert "browser-cookies" in err.lower()


def test_cli_attach_devtools_requires_explicit_browser(
    cli, capsys, tmp_path, monkeypatch
):
    def forbidden_cdp_probe(*_args, **_kwargs):
        raise AssertionError("missing browser must be rejected before CDP probing")

    monkeypatch.setattr(
        cli._il, "ensure_devtools_page_websocket_url", forbidden_cdp_probe
    )

    code, _out, err = _run(
        cli, capsys, ["--storage", str(tmp_path), "login", "--attach-devtools"]
    )

    assert code == 64
    assert "attach" in err.lower()
    assert "browser" in err.lower()


def test_cli_interactive_login_waits_for_required_cookies_and_supports_fresh(
    cli, capsys, tmp_path, monkeypatch
):
    launches = []
    batches = [
        [
            {
                "name": "SID",
                "value": "sid-cookie-value",
                "domain": ".google.com",
                "path": "/",
            },
        ],
        [
            {
                "name": "SID",
                "value": "sid-cookie-value",
                "domain": ".google.com",
                "path": "/",
            },
            {
                "name": "__Secure-1PSIDTS",
                "value": "psidts-cookie-value",
                "domain": ".google.com",
                "path": "/",
            },
        ],
    ]

    monkeypatch.setattr(cli, "INTERACTIVE_LOGIN_COOKIE_ATTEMPTS", 3)
    monkeypatch.setattr(cli, "INTERACTIVE_LOGIN_COOKIE_DELAY_SECONDS", 0)
    monkeypatch.setattr(
        cli._il,
        "launch_browser_session",
        lambda browser, **kw: (
            launches.append(kw)
            or {
                "source_kind": "interactive_browser",
                "browser": browser,
                "debugging_host": "127.0.0.1",
                "debugging_port": kw["debugging_port"],
                "profile_prepared": True,
                "process_id": None,
                "url_opened": True,
            }
        ),
    )
    monkeypatch.setattr(
        cli._il,
        "read_devtools_page_websocket_url",
        lambda port, **kw: "ws://127.0.0.1:9222/devtools/browser/session-id",
    )
    monkeypatch.setattr(cli._il, "read_cdp_all_cookies", lambda ws: batches.pop(0))

    code, out, err = _run(
        cli, capsys, ["--storage", str(tmp_path), "login", "--fresh", "--json"]
    )

    assert code == 0, err
    data = json.loads(out)
    assert data["capture_attempts"] == 2
    assert data["has_required_cookies"] is True
    assert launches and launches[0]["fresh"] is True
    saved = json.loads(
        cli._profiles.ProfileStore(str(tmp_path))
        .storage_state_path("default")
        .read_text(encoding="utf-8")
    )
    assert sorted(c["name"] for c in saved["cookies"]) == ["SID", "__Secure-1PSIDTS"]


def test_cli_attach_reresolves_current_notebooklm_target_for_each_capture(
    cli, capsys, tmp_path, monkeypatch
):
    resolved = []
    cookie_urls = []
    monkeypatch.setattr(cli, "INTERACTIVE_LOGIN_COOKIE_ATTEMPTS", 2)
    monkeypatch.setattr(cli, "INTERACTIVE_LOGIN_COOKIE_DELAY_SECONDS", 0)
    monkeypatch.setattr(
        cli._il,
        "ensure_devtools_page_websocket_url",
        lambda *_args, **_kwargs: ("ws://127.0.0.1:9222/devtools/page/initial", False),
    )
    urls = iter(
        [
            "ws://127.0.0.1:9222/devtools/page/current-one",
            "ws://127.0.0.1:9222/devtools/page/current-two",
        ]
    )
    monkeypatch.setattr(
        cli._il,
        "read_devtools_page_websocket_url",
        lambda port: (resolved.append(port) or next(urls)),
    )

    def cookies(ws_url):
        cookie_urls.append(ws_url)
        if len(cookie_urls) == 1:
            return [{"name": "SID", "value": "sid", "domain": ".google.com", "path": "/"}]
        return [
            {"name": "SID", "value": "sid", "domain": ".google.com", "path": "/"},
            {
                "name": "__Secure-1PSIDTS",
                "value": "sidts",
                "domain": ".google.com",
                "path": "/",
            },
        ]

    monkeypatch.setattr(cli._il, "read_cdp_all_cookies", cookies)
    code, _out, err = _run(
        cli,
        capsys,
        ["--storage", str(tmp_path), "login", "--browser", "chrome", "--attach-devtools"],
    )

    assert code == 0, err
    assert resolved == [9222, 9222]
    assert cookie_urls == [
        "ws://127.0.0.1:9222/devtools/page/current-one",
        "ws://127.0.0.1:9222/devtools/page/current-two",
    ]


def test_cli_fresh_is_ignored_with_browser_cookie_import_like_upstream(
    cli, capsys, tmp_path
):
    db = tmp_path / "Cookies"
    con = sqlite3.connect(str(db))
    try:
        con.execute(
            """
            CREATE TABLE cookies (
                creation_utc INTEGER NOT NULL,
                host_key TEXT NOT NULL,
                name TEXT NOT NULL,
                value TEXT NOT NULL,
                path TEXT NOT NULL,
                expires_utc INTEGER NOT NULL,
                is_secure INTEGER NOT NULL,
                is_httponly INTEGER NOT NULL,
                encrypted_value BLOB DEFAULT '',
                samesite INTEGER NOT NULL DEFAULT -1,
                is_persistent INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        rows = [
            (
                1,
                ".google.com",
                "SID",
                "sid-cookie-value",
                "/",
                18934560000000000,
                1,
                1,
                b"",
                -1,
                1,
            ),
            (
                2,
                ".google.com",
                "__Secure-1PSIDTS",
                "psidts-cookie-value",
                "/",
                18934560000000000,
                1,
                1,
                b"",
                -1,
                1,
            ),
        ]
        con.executemany(
            "INSERT INTO cookies (creation_utc, host_key, name, value, path, expires_utc, "
            "is_secure, is_httponly, encrypted_value, samesite, is_persistent) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        con.commit()
    finally:
        con.close()

    code, out, err = _run(
        cli,
        capsys,
        [
            "--storage",
            str(tmp_path),
            "login",
            "--browser-cookies",
            "chrome",
            "--fresh",
            "--cookie-store",
            str(db),
            "--os",
            "macOS",
        ],
    )
    assert code == 0, err
    assert "Warning: --fresh has no effect with --browser-cookies" in out
    state_path = cli._profiles.ProfileStore(str(tmp_path)).storage_state_path(
        "default"
    )
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert sorted(c["name"] for c in saved["cookies"]) == ["SID", "__Secure-1PSIDTS"]


def test_auth_refresh_without_browser_cookies_uses_network_slice_with_explicit_storage(
    cli, capsys, tmp_path, monkeypatch
):
    from notebooklm import auth, cookies, profiles

    store = profiles.ProfileStore(tmp_path)
    store.create_profile("default")
    cookies.save_storage_state(
        store.storage_state_path("default"),
        {
            "cookies": [
                {
                    "name": "SID",
                    "value": "sidSyntheticPhase2FValue",
                    "domain": ".google.com",
                    "path": "/",
                    "secure": True,
                },
                {
                    "name": "__Secure-1PSIDTS",
                    "value": "sidtsSyntheticPhase2FValue",
                    "domain": ".google.com",
                    "path": "/",
                    "secure": True,
                    "httpOnly": True,
                },
            ],
            "origins": [],
        },
    )

    def boom():
        raise AssertionError(
            "Path.home must not be consulted when --storage is explicit"
        )

    def fake_post(url, **_kwargs):
        return auth._http_std.Response(status=200, url=url, headers={}, body=b"[]")

    def fake_get(url, **_kwargs):
        return auth._http_std.Response(
            status=200,
            url=url,
            headers={},
            body=b'<script>{"SNlM0e":"csrfSyntheticPhase2FValue","FdrFJe":"sessionSyntheticPhase2FValue"}</script>',
        )

    monkeypatch.setattr(Path, "home", staticmethod(boom))
    monkeypatch.setattr(auth, "_default_post", fake_post)
    monkeypatch.setattr(auth, "_default_get", fake_get)
    code, out, err = _run(
        cli, capsys, ["--storage", str(tmp_path), "auth", "refresh", "--json"]
    )
    assert code == 0, err
    data = json.loads(out)
    assert data["token_fetch_ok"] is True
    assert "csrfSyntheticPhase2FValue" not in out
    assert "sessionSyntheticPhase2FValue" not in out


def test_no_forbidden_runtime_dependencies_or_boundary_strings(repo_root):
    src = (repo_root / "notebooklm" / "interactive_login.py").read_text(
        encoding="utf-8"
    )
    src_lower = src.lower()
    forbidden = (
        "playwright",
        "selenium",
        "websockets",
        "rookiepy",
        "requests",
        "httpx",
        "keyring",
        "secretstorage",
        "jeepney",
        "dbus",
        "path.home",
        "expanduser",
        "dpapi",
        "keychain",
        "secret service",
        "secret-tool",
    )
    for token in forbidden:
        assert token not in src_lower
