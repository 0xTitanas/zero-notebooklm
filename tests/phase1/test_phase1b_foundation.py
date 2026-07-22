"""Phase 1B stdlib foundation tests.

This file covers only the final Phase 1 foundation modules:
``http_std``, ``async_transport``, and ``lockfile``. Tests use local fake servers...
"""

from __future__ import annotations

import asyncio
import gzip
import importlib
import importlib.abc
import json
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest

import _phase0_constants as C  # noqa: E402
import import_origin_audit  # noqa: E402

DENYLIST = set(C.DENYLISTED_RUNTIME_IMPORTS) | {"aiohttp", "urllib3"}


class DenyThirdPartyFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):  # noqa: D401 - import hook protocol
        if fullname.split(".", 1)[0] in DENYLIST:
            raise AssertionError(f"denylisted runtime import attempted: {fullname}")
        return None


class Phase1BHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # pragma: no cover - keeps test output quiet.
        return

    def _send(
        self, status: int, body: bytes = b"", headers: dict[str, str] | None = None
    ) -> None:
        headers = dict(headers or {})
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):  # noqa: N802 - stdlib handler API
        path = urlparse(self.path).path
        if path == "/get":
            marker = self.headers.get("X-Test", "missing")
            self._send(
                200,
                f"hello:{marker}".encode(),
                {"X-Reply": "phase1b", "Content-Type": "text/plain; charset=utf-8"},
            )
        elif path == "/gzip":
            self._send(
                200,
                gzip.compress(b"compressed response"),
                {"Content-Encoding": "GZip, identity"},
            )
        elif path == "/redirect-to-get":
            self._send(302, b"", {"Location": "/get"})
        elif path == "/relative-start":
            self._send(302, b"", {"Location": "nested/final"})
        elif path == "/nested/final":
            self._send(200, b"relative ok")
        elif path == "/loop":
            self._send(302, b"", {"Location": "/loop"})
        elif path == "/large":
            # Deliberately no Content-Length: the client must enforce limits while reading.
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"x" * 4096)
        elif path == "/gzip-large":
            body = gzip.compress(b"y" * 4096)
            self._send(200, body, {"Content-Encoding": "gzip"})
        else:
            self._send(404, b"missing")

    def do_POST(self):  # noqa: N802 - stdlib handler API
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        if path == "/post":
            self._send(
                201,
                body,
                {"X-Method": "POST", "Content-Type": "text/plain; charset=utf-8"},
            )
        else:
            self._send(404, b"missing")


@pytest.fixture
def fake_http_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Phase1BHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture
def malformed_http_url():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(2)
    host, port = listener.getsockname()

    def serve_once():
        try:
            conn, _addr = listener.accept()
            with conn:
                conn.recv(4096)
                conn.sendall(b"not-http-at-all\r\n\r\n")
        except OSError:
            return
        finally:
            try:
                listener.close()
            except OSError:
                pass

    thread = threading.Thread(target=serve_once, daemon=True)
    thread.start()
    try:
        yield f"http://{host}:{port}/bad"
    finally:
        try:
            listener.close()
        except OSError:
            pass
        thread.join(timeout=2)


def test_phase1b_modules_import_without_denylisted_runtime_dependencies(
    repo_root, monkeypatch
):
    monkeypatch.syspath_prepend(str(repo_root))
    finder = DenyThirdPartyFinder()
    sys.meta_path.insert(0, finder)
    try:
        for mod_name in (
            "notebooklm.http_std",
            "notebooklm.async_transport",
            "notebooklm.lockfile",
        ):
            mod = importlib.import_module(mod_name)
            assert Path(mod.__file__).resolve().is_relative_to(repo_root)
    finally:
        sys.meta_path.remove(finder)


def test_http_get_post_gzip_redirects_and_text_helper(
    repo_root, monkeypatch, fake_http_server
):
    monkeypatch.syspath_prepend(str(repo_root))
    http_std = importlib.import_module("notebooklm.http_std")

    response = http_std.get(
        f"{fake_http_server}/get",
        headers={"X-Test": "ok"},
        timeout=2,
        max_body_bytes=1024,
    )
    assert response.status == 200
    assert response.url.endswith("/get")
    assert response.body == b"hello:ok"
    assert response.text() == "hello:ok"
    assert response.headers["x-reply"] == "phase1b"

    posted = http_std.post(
        f"{fake_http_server}/post",
        body="payload",
        headers={"X-Test": "post"},
        timeout=2,
    )
    assert posted.status == 201
    assert posted.body == b"payload"
    assert posted.text() == "payload"
    assert posted.headers["x-method"] == "POST"

    gzipped = http_std.get(f"{fake_http_server}/gzip", timeout=2, max_body_bytes=1024)
    assert gzipped.body == b"compressed response"
    assert gzipped.text() == "compressed response"

    redirected = http_std.get(
        f"{fake_http_server}/redirect-to-get",
        headers={"X-Test": "redir"},
        timeout=2,
        max_redirects=1,
    )
    assert redirected.status == 200
    assert redirected.url.endswith("/get")
    assert redirected.body == b"hello:redir"

    relative = http_std.get(
        f"{fake_http_server}/relative-start", timeout=2, max_redirects=1
    )
    assert relative.status == 200
    assert relative.url.endswith("/nested/final")
    assert relative.body == b"relative ok"

    no_follow = http_std.get(
        f"{fake_http_server}/redirect-to-get",
        timeout=2,
        follow_redirects=False,
    )
    assert no_follow.status == 302
    assert no_follow.url.endswith("/redirect-to-get")
    assert no_follow.headers["location"] == "/get"


def test_http_limits_redirect_errors_and_unsupported_schemes(
    repo_root, monkeypatch, fake_http_server, malformed_http_url
):
    monkeypatch.syspath_prepend(str(repo_root))
    errors = importlib.import_module("notebooklm.errors")
    http_std = importlib.import_module("notebooklm.http_std")

    with pytest.raises(errors.RedirectError):
        http_std.get(f"{fake_http_server}/redirect-to-get", max_redirects=0, timeout=2)

    with pytest.raises(errors.RedirectError):
        http_std.get(f"{fake_http_server}/loop", max_redirects=2, timeout=2)

    with pytest.raises(errors.BodyTooLargeError):
        http_std.get(f"{fake_http_server}/large", max_body_bytes=32, timeout=2)

    with pytest.raises(errors.BodyTooLargeError):
        http_std.get(f"{fake_http_server}/gzip-large", max_body_bytes=32, timeout=2)

    if hasattr(http_std, "gzip"):

        def forbidden_full_buffer_decompress(_body):
            raise AssertionError(
                "full-buffer gzip.decompress must not be used for bounded decode"
            )

        monkeypatch.setattr(
            http_std.gzip, "decompress", forbidden_full_buffer_decompress
        )
    with pytest.raises(errors.BodyTooLargeError):
        http_std.get(f"{fake_http_server}/gzip-large", max_body_bytes=128, timeout=2)

    with pytest.raises(errors.HTTPTransportError):
        http_std.get(malformed_http_url, timeout=2)

    with pytest.raises(errors.UnsupportedSchemeError):
        http_std.get("file:///tmp/notebooklm-bare", timeout=2)


def test_async_transport_returns_times_out_cancels_and_closes(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))
    errors = importlib.import_module("notebooklm.errors")
    async_transport = importlib.import_module("notebooklm.async_transport")

    async def scenario():
        transport = async_transport.AsyncTransport(max_workers=2)
        release = threading.Event()
        started = threading.Event()

        def blocking_value():
            started.set()
            release.wait(timeout=2)
            return "done"

        task = transport.start(blocking_value)
        assert started.wait(timeout=1)
        await asyncio.sleep(0)
        release.set()
        assert await asyncio.wait_for(task, timeout=1) == "done"

        timeout_started = threading.Event()
        timeout_release = threading.Event()

        def blocks_until_released():
            timeout_started.set()
            timeout_release.wait(timeout=2)
            return "late"

        with pytest.raises(errors.TransportTimeoutError):
            await transport.run(blocks_until_released, timeout=0.05)
        assert timeout_started.wait(timeout=1)
        timeout_release.set()

        cancel_started = threading.Event()
        cancel_release = threading.Event()

        def cancellable():
            cancel_started.set()
            cancel_release.wait(timeout=2)
            return "cancelled too late"

        cancel_task = transport.start(cancellable)
        assert cancel_started.wait(timeout=1)
        cancel_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancel_task
        cancel_release.set()

        await transport.close()
        await transport.close()
        with pytest.raises(errors.TransportClosedError):
            await transport.run(lambda: "closed")

    asyncio.run(scenario())


def test_lockfile_acquire_contention_stale_and_safe_release(
    repo_root, monkeypatch, tmp_path
):
    monkeypatch.syspath_prepend(str(repo_root))
    errors = importlib.import_module("notebooklm.errors")
    lockfile = importlib.import_module("notebooklm.lockfile")

    lock_path = tmp_path / "profile.lock"
    lock = lockfile.LockFile(lock_path)

    with lock.acquire() as handle:
        assert lock_path.exists()
        metadata = json.loads(lock_path.read_text(encoding="utf-8"))
        assert metadata["pid"] == os.getpid()
        assert metadata["owner_id"] == handle.owner_id
        forbidden_words = {
            "cookie",
            "oauth",
            "token",
            "token_value",
            "authorization",
            "bearer",
        }
        assert not (forbidden_words & {k.lower() for k in metadata})
        with pytest.raises(errors.ProfileLockError):
            lockfile.LockFile(lock_path).acquire()
    assert not lock_path.exists()

    lock_path.write_text(
        '{"pid": 999999, "created_at": "old", "owner_id": "other"}', encoding="utf-8"
    )
    old = time.time() - 86400
    os.utime(lock_path, (old, old))
    before = lock_path.read_text(encoding="utf-8")
    with pytest.raises(errors.ProfileLockError):
        lockfile.LockFile(lock_path).acquire()
    assert lock_path.read_text(encoding="utf-8") == before
    lock_path.unlink()

    replacement_lock = lockfile.LockFile(lock_path)
    handle = replacement_lock.acquire()
    lock_path.write_text('{"pid": 1, "owner_id": "replacement"}', encoding="utf-8")
    handle.release()
    assert lock_path.exists()
    assert "replacement" in lock_path.read_text(encoding="utf-8")

    with pytest.raises(RuntimeError):
        with lockfile.LockFile(tmp_path / "ctx.lock").acquire():
            raise RuntimeError("boom")
    assert not (tmp_path / "ctx.lock").exists()


def test_import_origin_audit_covers_phase1b_modules(repo_root):
    scanned = [
        Path(p).relative_to(repo_root).as_posix()
        for p in import_origin_audit._iter_python_files(C.AUDIT_ROOTS)
    ]
    for expected in (
        "notebooklm/http_std.py",
        "notebooklm/async_transport.py",
        "notebooklm/lockfile.py",
    ):
        assert expected in scanned
    assert import_origin_audit.audit(roots=C.AUDIT_ROOTS) == []
