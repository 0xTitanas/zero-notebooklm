"""Stdlib interactive browser-session primitives for NotebookLM Bare Phase 2F.

This module provides small, testable building blocks for the live interactive
login lane:

* deterministic launch arguments for Chromium/Chrome/Edge with a loopback-only
  DevTools endpoint and isolated user data directory;
* an injected process-launch seam for tests and CLI wiring;
* a bounded DevTools version probe that accepts only loopback debugger URLs;
* a minimal stdlib WebSocket/CDP command path for ``Network.getAllCookies``;
* conversion of DevTools cookie records into storage_state.json;
* a wait-until-required-cookies capture loop that avoids persisting partial
  first-run auth state.

It does not automate credential entry, account selection, OAuth, or token
refresh. Cookie values are written only to the caller's explicit storage-state
path; summaries never include values, tokens, emails, raw command lines, or full
profile paths.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import quote, urlsplit

from . import cookies as _cookies
from . import http_std
from .errors import AuthenticationError, NetworkError, ValidationError

INTERACTIVE_LOGIN_BROWSERS = ("chromium", "chrome", "msedge")
NOTEBOOKLM_LOGIN_URL = "https://notebooklm.google.com/"
SOURCE_KIND_INTERACTIVE_BROWSER = "interactive_browser"
LOOPBACK_HOST = "127.0.0.1"
CDP_GET_ALL_COOKIES_METHOD = "Network.getAllCookies"
_DEFAULT_DEVTOOLS_MAX_BODY = 64 * 1024
_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_WS_MAX_HANDSHAKE_BYTES = 64 * 1024
_WS_MAX_FRAME_BYTES = 1024 * 1024
DEVTOOLS_DISCOVERY_PORTS = (9222, 9223, 9224, 9333)

__all__ = [
    "AuthenticationError",
    "INTERACTIVE_LOGIN_BROWSERS",
    "NOTEBOOKLM_LOGIN_URL",
    "SOURCE_KIND_INTERACTIVE_BROWSER",
    "LOOPBACK_HOST",
    "CDP_GET_ALL_COOKIES_METHOD",
    "DEVTOOLS_DISCOVERY_PORTS",
    "normalize_interactive_browser",
    "browser_executable_candidates",
    "resolve_browser_executable",
    "build_browser_argv",
    "prepare_browser_profile",
    "launch_browser_session",
    "devtools_new_url",
    "devtools_version_url",
    "read_devtools_websocket_url",
    "devtools_list_url",
    "find_devtools_page_websocket_url",
    "read_devtools_page_websocket_url",
    "discover_devtools_page_websocket_url",
    "open_devtools_notebooklm_page",
    "ensure_devtools_page_websocket_url",
    "read_cdp_all_cookies",
    "storage_state_from_cdp_cookies",
    "redacted_storage_summary",
    "import_cdp_cookies_to_storage_state",
    "capture_cdp_cookies_until_ready",
]


def normalize_interactive_browser(browser: str) -> str:
    """Return the canonical upstream interactive browser name."""

    canon = (browser or "").strip().lower()
    if canon not in INTERACTIVE_LOGIN_BROWSERS:
        raise ValueError("unsupported interactive browser")
    return canon


def _canonical_os_name(os_name: str | None = None) -> str:
    if os_name:
        lowered = os_name.strip().lower()
        if lowered in {"macos", "darwin"}:
            return "macos"
        if lowered.startswith("win"):
            return "windows"
        if "linux" in lowered or lowered in {"ubuntu-lts-linux", "ubuntu"}:
            return "linux"
    if os.name == "nt":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def browser_executable_candidates(
    browser: str,
    *,
    os_name: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> list[str]:
    """Return deterministic executable candidates without scanning user state.

    On macOS this includes the per-user ``$HOME/Applications`` app-bundle
    location when ``HOME`` is an absolute path. The helper never calls Python's
    home-directory lookup API and never walks directories; callers can inject
    ``environ`` for deterministic tests.
    """

    canon = normalize_interactive_browser(browser)
    system = _canonical_os_name(os_name)
    env = dict(os.environ if environ is None else environ)
    if system == "macos":
        home = env.get("HOME")
        user_apps: list[str] = []
        if home and os.path.isabs(home):
            user_apps = [str(Path(home) / "Applications")]
        system_apps = ["/Applications"]

        def app_candidates(app_name: str, executable: str) -> list[str]:
            return [
                str(Path(base) / f"{app_name}.app" / "Contents" / "MacOS" / executable)
                for base in [*user_apps, *system_apps]
            ]

        if canon == "chrome":
            return [
                *app_candidates("Google Chrome", "Google Chrome"),
                "google-chrome",
                "chrome",
            ]
        if canon == "msedge":
            return [
                *app_candidates("Microsoft Edge", "Microsoft Edge"),
                "microsoft-edge",
                "msedge",
            ]
        return [
            *app_candidates("Chromium", "Chromium"),
            "chromium",
            "chromium-browser",
        ]
    if system == "windows":
        program_files = [
            env.get("PROGRAMFILES"),
            env.get("PROGRAMFILES(X86)"),
            env.get("LOCALAPPDATA"),
        ]
        suffixes = {
            "chrome": [r"Google\Chrome\Application\chrome.exe"],
            "msedge": [r"Microsoft\Edge\Application\msedge.exe"],
            "chromium": [
                r"Chromium\Application\chrome.exe",
                r"Chromium\Application\chromium.exe",
            ],
        }[canon]
        candidates: list[str] = []
        for base in program_files:
            if not base:
                continue
            for suffix in suffixes:
                candidates.append(str(Path(base) / suffix))
        candidates.extend(
            {
                "chrome": ["chrome.exe"],
                "msedge": ["msedge.exe"],
                "chromium": ["chromium.exe", "chrome.exe"],
            }[canon]
        )
        return candidates
    if canon == "chrome":
        return ["google-chrome", "google-chrome-stable", "chrome"]
    if canon == "msedge":
        return ["microsoft-edge", "microsoft-edge-stable", "msedge"]
    return ["chromium", "chromium-browser", "chromium-browser-stable"]


def resolve_browser_executable(
    browser: str,
    *,
    os_name: str | None = None,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    exists: Callable[[str], bool] = os.path.exists,
) -> str | None:
    """Resolve a browser executable from deterministic candidates.

    This helper checks static app-bundle/PATH candidates only through injected
    ``exists``/``which`` callables so tests can prove the boundary without
    launching anything. The only user-local macOS candidate is derived from an
    absolute ``HOME`` environment value; no directory scanning or Python
    home-directory lookup is performed.
    """

    for candidate in browser_executable_candidates(
        browser, os_name=os_name, environ=environ
    ):
        if os.path.isabs(candidate):
            try:
                if exists(candidate):
                    return candidate
            except OSError:
                continue
        else:
            try:
                resolved = which(candidate)
            except OSError:
                resolved = None
            if resolved:
                return resolved
    return None


def _validate_debugging_port(debugging_port: int) -> int:
    if isinstance(debugging_port, bool) or not isinstance(debugging_port, int):
        raise ValidationError("debugging port must be an integer")
    if not (1 <= debugging_port <= 65535):
        raise ValidationError("debugging port is out of range")
    return debugging_port


def build_browser_argv(
    browser: str,
    *,
    executable: str,
    user_data_dir: str | os.PathLike[str],
    debugging_port: int,
    url: str = NOTEBOOKLM_LOGIN_URL,
    extra_args: Iterable[str] | None = None,
) -> list[str]:
    """Build a shell-free Chromium-family launch command."""

    normalize_interactive_browser(browser)
    port = _validate_debugging_port(debugging_port)
    exe = str(executable or "").strip()
    if not exe:
        raise ValidationError("browser executable is required")
    profile = Path(user_data_dir)
    if profile == Path("."):
        raise ValidationError("browser profile directory must be explicit")
    argv = [
        exe,
        f"--remote-debugging-address={LOOPBACK_HOST}",
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    for arg in extra_args or ():
        if arg:
            argv.append(str(arg))
    argv.append(str(url or NOTEBOOKLM_LOGIN_URL))
    return argv


def prepare_browser_profile(
    user_data_dir: str | os.PathLike[str], *, fresh: bool = False
) -> Path:
    """Create or freshen the explicit interactive browser profile directory."""

    profile = Path(user_data_dir)
    if profile in {Path(""), Path(".")}:
        raise ValidationError("browser profile directory must be explicit")
    if fresh and profile.exists():
        if not profile.is_dir():
            raise ValidationError("browser profile path is not a directory")
        shutil.rmtree(profile)
    profile.mkdir(parents=True, exist_ok=True)
    return profile


def launch_browser_session(
    browser: str,
    *,
    executable: str | None = None,
    user_data_dir: str | os.PathLike[str],
    debugging_port: int,
    url: str = NOTEBOOKLM_LOGIN_URL,
    fresh: bool = False,
    runner: Callable[..., Any] = subprocess.Popen,
    os_name: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Launch an explicitly requested interactive browser session.

    The public return value is pathless/redacted: it does not expose argv or the
    full profile directory. Tests and future CLI wiring can inject ``runner``.
    """

    canon = normalize_interactive_browser(browser)
    profile = prepare_browser_profile(user_data_dir, fresh=fresh)
    exe = executable or resolve_browser_executable(
        canon, os_name=os_name, environ=environ
    )
    if not exe:
        raise ValidationError("interactive browser executable unavailable")
    argv = build_browser_argv(
        canon,
        executable=exe,
        user_data_dir=profile,
        debugging_port=debugging_port,
        url=url,
    )
    proc = runner(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=False,
        start_new_session=True,
    )
    return {
        "source_kind": SOURCE_KIND_INTERACTIVE_BROWSER,
        "browser": canon,
        "debugging_host": LOOPBACK_HOST,
        "debugging_port": _validate_debugging_port(debugging_port),
        "profile_prepared": True,
        "process_id": getattr(proc, "pid", None),
        "url_opened": bool(url),
    }


def devtools_version_url(debugging_port: int, *, host: str = LOOPBACK_HOST) -> str:
    """Return the loopback-only DevTools version endpoint URL."""

    if host not in {LOOPBACK_HOST, "localhost"}:
        raise ValidationError("DevTools host must be loopback")
    return f"http://{host}:{_validate_debugging_port(debugging_port)}/json/version"


def devtools_list_url(debugging_port: int, *, host: str = LOOPBACK_HOST) -> str:
    """Return the loopback-only DevTools target-list endpoint URL."""

    if host not in {LOOPBACK_HOST, "localhost"}:
        raise ValidationError("DevTools host must be loopback")
    return f"http://{host}:{_validate_debugging_port(debugging_port)}/json/list"


def _validate_notebooklm_target_url(url: str) -> str:
    target = str(url or "")
    try:
        parts = urlsplit(target)
    except ValueError:
        raise ValidationError("DevTools target URL must be NotebookLM") from None
    if parts.scheme != "https" or parts.hostname != "notebooklm.google.com":
        raise ValidationError("DevTools target URL must be NotebookLM")
    return target


def devtools_new_url(
    debugging_port: int,
    *,
    host: str = LOOPBACK_HOST,
    target_url: str = NOTEBOOKLM_LOGIN_URL,
) -> str:
    """Return the loopback-only DevTools new-target endpoint for NotebookLM.

    The target URL is validated and percent-encoded. Callers must not echo the
    returned URL in user-facing errors because it contains the target URL.
    """

    if host not in {LOOPBACK_HOST, "localhost"}:
        raise ValidationError("DevTools host must be loopback")
    target = _validate_notebooklm_target_url(target_url)
    return (
        f"http://{host}:{_validate_debugging_port(debugging_port)}/json/new?"
        f"{quote(target, safe='')}"
    )


def _response_text(response: Any) -> str:
    text = getattr(response, "text", None)
    if callable(text):
        return str(text())
    if isinstance(text, str):
        return text
    body = getattr(response, "body", None)
    if isinstance(body, bytes):
        return body.decode("utf-8", errors="replace")
    raise NetworkError("DevTools version probe returned no text")


def _validate_websocket_url(url: Any, *, debugging_port: int) -> str:
    if not isinstance(url, str) or not url:
        raise NetworkError("DevTools version probe did not expose a debugger URL")
    parts = urlsplit(url)
    if parts.scheme not in {"ws", "wss"}:
        raise NetworkError("DevTools debugger URL was not a websocket URL")
    if parts.hostname not in {LOOPBACK_HOST, "localhost"}:
        raise NetworkError("DevTools debugger URL was not loopback-bound")
    if parts.port != _validate_debugging_port(debugging_port):
        raise NetworkError("DevTools debugger URL port mismatch")
    return url


def read_devtools_websocket_url(
    debugging_port: int,
    *,
    host: str = LOOPBACK_HOST,
    http_get: Callable[..., Any] = http_std.get,
    timeout: float = 2.0,
) -> str:
    """Read and validate the local DevTools websocket URL.

    Errors are intentionally static and pathless; the raw endpoint payload is never
    included in exception text.
    """

    try:
        response = http_get(
            devtools_version_url(debugging_port, host=host),
            timeout=timeout,
            max_body_bytes=_DEFAULT_DEVTOOLS_MAX_BODY,
        )
        data = json.loads(_response_text(response))
        return _validate_websocket_url(
            data.get("webSocketDebuggerUrl"), debugging_port=debugging_port
        )
    except NetworkError:
        raise
    except Exception as exc:
        raise NetworkError(
            f"DevTools version probe failed: {exc.__class__.__name__}"
        ) from None


def _target_matches_notebooklm(
    target: Mapping[str, Any], *, expected_host: str = "notebooklm.google.com"
) -> bool:
    if target.get("type") != "page":
        return False
    url = target.get("url")
    if not isinstance(url, str):
        return False
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return False
    return host == expected_host


def find_devtools_page_websocket_url(
    debugging_port: int,
    *,
    host: str = LOOPBACK_HOST,
    http_get: Callable[..., Any] = http_std.get,
    timeout: float = 2.0,
    expected_host: str = "notebooklm.google.com",
) -> str | None:
    """Return the loopback WebSocket URL for the NotebookLM page target, if any.

    Modern Chrome exposes some commands, including cookie reads, more reliably on
    the page target than on the browser-level target from ``/json/version``. This
    helper selects only a page whose URL host is NotebookLM, returns ``None``
    when no such target is present, and never echoes raw target URLs or WebSocket
    identifiers in errors.
    """

    try:
        response = http_get(
            devtools_list_url(debugging_port, host=host),
            timeout=timeout,
            max_body_bytes=_DEFAULT_DEVTOOLS_MAX_BODY,
        )
        data = json.loads(_response_text(response))
        if not isinstance(data, list):
            raise NetworkError("DevTools page target probe returned invalid payload")
        for target in data:
            if isinstance(target, Mapping) and _target_matches_notebooklm(
                target, expected_host=expected_host
            ):
                return _validate_websocket_url(
                    target.get("webSocketDebuggerUrl"),
                    debugging_port=debugging_port,
                )
        return None
    except NetworkError:
        raise
    except Exception as exc:
        raise NetworkError(
            f"DevTools page target probe failed: {exc.__class__.__name__}"
        ) from None


def read_devtools_page_websocket_url(
    debugging_port: int,
    *,
    host: str = LOOPBACK_HOST,
    http_get: Callable[..., Any] = http_std.get,
    timeout: float = 2.0,
    expected_host: str = "notebooklm.google.com",
) -> str:
    """Read the loopback WebSocket URL for the NotebookLM page target."""

    ws_url = find_devtools_page_websocket_url(
        debugging_port,
        host=host,
        http_get=http_get,
        timeout=timeout,
        expected_host=expected_host,
    )
    if ws_url is None:
        raise NetworkError("DevTools page target probe found no NotebookLM page target")
    return ws_url


def discover_devtools_page_websocket_url(
    *,
    candidate_ports: Iterable[int] = DEVTOOLS_DISCOVERY_PORTS,
    host: str = LOOPBACK_HOST,
    http_get: Callable[..., Any] = http_std.get,
    timeout: float = 2.0,
    expected_host: str = "notebooklm.google.com",
) -> tuple[int, str]:
    """Scan loopback DevTools ports and return the NotebookLM page target.

    The helper only inspects ``/json/list`` through the existing redacting page
    probe; it never opens a browser, reads cookies, or includes raw target data
    in errors.
    """

    ports = [_validate_debugging_port(port) for port in candidate_ports]
    if not ports:
        raise ValidationError("DevTools discovery requires candidate ports")
    for port in ports:
        try:
            ws_url = find_devtools_page_websocket_url(
                port,
                host=host,
                http_get=http_get,
                timeout=timeout,
                expected_host=expected_host,
            )
        except NetworkError:
            continue
        if ws_url is not None:
            return port, ws_url
    raise NetworkError("DevTools discovery found no NotebookLM page target")


def open_devtools_notebooklm_page(
    debugging_port: int,
    *,
    host: str = LOOPBACK_HOST,
    target_url: str = NOTEBOOKLM_LOGIN_URL,
    http_request: Callable[..., Any] = http_std.request,
    timeout: float = 2.0,
) -> None:
    """Open a NotebookLM page through an existing loopback DevTools endpoint.

    This is for attach-only interactive login. It does not launch a browser and
    never echoes target URLs, target IDs, response payloads, or cookies in errors.
    """

    url = devtools_new_url(debugging_port, host=host, target_url=target_url)
    try:
        response = http_request(
            "PUT",
            url,
            timeout=timeout,
            max_redirects=0,
            max_body_bytes=_DEFAULT_DEVTOOLS_MAX_BODY,
        )
    except Exception as exc:
        raise NetworkError(
            f"DevTools NotebookLM target open failed: {exc.__class__.__name__}"
        ) from None
    status = getattr(response, "status", 0)
    if not isinstance(status, int) or status < 200 or status >= 300:
        raise NetworkError("DevTools NotebookLM target open failed")


def ensure_devtools_page_websocket_url(
    debugging_port: int,
    *,
    host: str = LOOPBACK_HOST,
    target_url: str = NOTEBOOKLM_LOGIN_URL,
    http_get: Callable[..., Any] = http_std.get,
    http_request: Callable[..., Any] = http_std.request,
    timeout: float = 2.0,
    open_if_missing: bool = False,
    attempts: int = 5,
    delay_seconds: float = 0.5,
    sleep: Callable[[float], Any] = time.sleep,
) -> tuple[str, bool]:
    """Return a NotebookLM page websocket URL, optionally opening a target first.

    Returns ``(websocket_url, opened_target)``. All probing remains loopback-only.
    """

    if attempts < 1:
        raise ValidationError("DevTools target attempts must be positive")
    if delay_seconds < 0:
        raise ValidationError("DevTools target delay must not be negative")

    ws_url = find_devtools_page_websocket_url(
        debugging_port,
        host=host,
        http_get=http_get,
        timeout=timeout,
    )
    if ws_url is not None:
        return ws_url, False
    if not open_if_missing:
        raise NetworkError("DevTools page target probe found no NotebookLM page target")

    open_devtools_notebooklm_page(
        debugging_port,
        host=host,
        target_url=target_url,
        http_request=http_request,
        timeout=timeout,
    )
    for attempt in range(1, attempts + 1):
        ws_url = find_devtools_page_websocket_url(
            debugging_port,
            host=host,
            http_get=http_get,
            timeout=timeout,
        )
        if ws_url is not None:
            return ws_url, True
        if attempt < attempts:
            sleep(delay_seconds)
    raise NetworkError("DevTools page target probe found no NotebookLM page target")


def _default_ws_key() -> str:
    return base64.b64encode(os.urandom(16)).decode("ascii")


def _read_until(sock: Any, marker: bytes, *, max_bytes: int) -> bytes:
    data = bytearray()
    while marker not in data:
        chunk = sock.recv(4096)
        if not chunk:
            raise NetworkError("DevTools websocket closed during handshake")
        data.extend(chunk)
        if len(data) > max_bytes:
            raise NetworkError("DevTools websocket handshake exceeded size limit")
    return bytes(data)


def _header_map(raw: bytes) -> tuple[str, dict[str, str]]:
    head = raw.split(b"\r\n\r\n", 1)[0]
    lines = head.decode("iso-8859-1", errors="replace").split("\r\n")
    status = lines[0] if lines else ""
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        headers[name.strip().lower()] = value.strip()
    return status, headers


def _expected_accept(key: str) -> str:
    digest = hashlib.sha1((key + _WS_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def _validate_ws_handshake(raw: bytes, *, key: str) -> None:
    status, headers = _header_map(raw)
    if " 101 " not in f" {status} ":
        raise NetworkError("DevTools websocket handshake was not accepted")
    if headers.get("upgrade", "").lower() != "websocket":
        raise NetworkError("DevTools websocket handshake missing upgrade")
    if "upgrade" not in headers.get("connection", "").lower():
        raise NetworkError("DevTools websocket handshake missing connection upgrade")
    accept = headers.get("sec-websocket-accept")
    if accept != _expected_accept(key):
        raise NetworkError("DevTools websocket handshake accept mismatch")


def _mask_payload(data: bytes, mask: bytes) -> bytes:
    if len(mask) != 4:
        raise NetworkError("DevTools websocket mask generation failed")
    return bytes(byte ^ mask[i % 4] for i, byte in enumerate(data))


def _client_text_frame(
    payload: str, *, mask_factory: Callable[[int], bytes] = os.urandom
) -> bytes:
    data = payload.encode("utf-8")
    header = bytearray([0x81])
    if len(data) < 126:
        header.append(0x80 | len(data))
    elif len(data) <= 0xFFFF:
        header.extend([0x80 | 126])
        header.extend(len(data).to_bytes(2, "big"))
    else:
        header.extend([0x80 | 127])
        header.extend(len(data).to_bytes(8, "big"))
    mask = mask_factory(4)
    return bytes(header) + mask + _mask_payload(data, mask)


def _read_exact(sock: Any, length: int) -> bytes:
    data = bytearray()
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            raise NetworkError("DevTools websocket closed unexpectedly")
        data.extend(chunk)
    return bytes(data)


def _read_server_text_frame(sock: Any, *, max_bytes: int = _WS_MAX_FRAME_BYTES) -> str:
    header = _read_exact(sock, 2)
    opcode = header[0] & 0x0F
    length = header[1] & 0x7F
    masked = bool(header[1] & 0x80)
    if length == 126:
        length = int.from_bytes(_read_exact(sock, 2), "big")
    elif length == 127:
        length = int.from_bytes(_read_exact(sock, 8), "big")
    if length > max_bytes:
        raise NetworkError("DevTools websocket frame exceeded size limit")
    mask = _read_exact(sock, 4) if masked else b""
    payload = _read_exact(sock, length)
    if masked:
        payload = _mask_payload(payload, mask)
    if opcode == 0x8:
        raise NetworkError("DevTools websocket closed")
    if opcode != 0x1:
        raise NetworkError("DevTools websocket returned non-text frame")
    return payload.decode("utf-8", errors="replace")


def read_cdp_all_cookies(
    websocket_url: str,
    *,
    socket_factory: Callable[..., Any] = socket.create_connection,
    timeout: float = 2.0,
    key_factory: Callable[[], str] = _default_ws_key,
    mask_factory: Callable[[int], bytes] = os.urandom,
) -> list[dict[str, Any]]:
    """Return cookies from a loopback DevTools websocket via Network.getAllCookies.

    This is a low-level primitive only. It performs a single stdlib WebSocket
    handshake, sends one masked CDP command, waits for the matching response, and
    returns the raw CDP cookie dictionaries. It never opens a browser, never
    automates credential entry, and never includes the raw URL or payload in error
    text.
    """

    parts = urlsplit(websocket_url or "")
    if parts.scheme != "ws":
        raise NetworkError("DevTools websocket URL must use ws loopback")
    host = parts.hostname
    if host not in {LOOPBACK_HOST, "localhost"}:
        raise NetworkError("DevTools websocket URL was not loopback-bound")
    if parts.port is None:
        raise NetworkError("DevTools websocket URL did not include a port")
    port = _validate_debugging_port(parts.port)
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    key = key_factory()
    if not isinstance(key, str) or not key:
        raise NetworkError("DevTools websocket key generation failed")

    sock = None
    try:
        sock = socket_factory((host, port), timeout=timeout)
        settimeout = getattr(sock, "settimeout", None)
        if callable(settimeout):
            settimeout(timeout)
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        ).encode("ascii")
        sock.sendall(request)
        _validate_ws_handshake(
            _read_until(sock, b"\r\n\r\n", max_bytes=_WS_MAX_HANDSHAKE_BYTES),
            key=key,
        )
        command = {"id": 1, "method": CDP_GET_ALL_COOKIES_METHOD}
        sock.sendall(
            _client_text_frame(
                json.dumps(command, separators=(",", ":")), mask_factory=mask_factory
            )
        )
        for _ in range(32):
            message = json.loads(_read_server_text_frame(sock))
            if message.get("id") != 1:
                continue
            if "error" in message:
                raise NetworkError("DevTools cookie command failed")
            raw_result = message.get("result")
            result = raw_result if isinstance(raw_result, dict) else {}
            cookies = result.get("cookies")
            if not isinstance(cookies, list):
                raise NetworkError("DevTools cookie command returned no cookie list")
            return [c for c in cookies if isinstance(c, dict)]
        raise NetworkError("DevTools cookie command response not received")
    except NetworkError:
        raise
    except Exception as exc:
        raise NetworkError(
            f"DevTools cookie command failed: {exc.__class__.__name__}"
        ) from None
    finally:
        close = getattr(sock, "close", None)
        if callable(close):
            close()


def storage_state_from_cdp_cookies(
    cdp_cookies: Iterable[Mapping[str, Any]],
    *,
    google_only: bool = True,
    include_domains: set[str] | None = None,
    origins: list | None = None,
) -> dict[str, Any]:
    """Convert DevTools cookie records into browser storage state."""

    cookies = []
    for raw in cdp_cookies:
        try:
            normalized = _cookies.normalize_cookie(raw)
        except ValidationError:
            continue
        if google_only:
            if include_domains is None:
                allowed = _cookies.is_allowed_google_domain(normalized.get("domain"))
            else:
                from . import browser_cookies as _bc

                allowed = _bc._is_included_cookie_domain(
                    normalized.get("domain"), include_domains
                )
            if not allowed:
                continue
        cookies.append(normalized)
    return _cookies.build_storage_state(cookies, origins=origins)


def redacted_storage_summary(state: Mapping[str, Any]) -> dict[str, Any]:
    """Return value-free metadata for an interactive storage-state capture."""

    redacted = _cookies.redact_storage_state(state)
    names = set(redacted.get("cookie_names", []))
    required = {
        name: (name in names) for name in sorted(_cookies.MINIMUM_REQUIRED_COOKIES)
    }
    missing = sorted(name for name, present in required.items() if not present)
    return {
        "source_kind": SOURCE_KIND_INTERACTIVE_BROWSER,
        "cookie_count": redacted.get("cookie_count", 0),
        "cookie_names": redacted.get("cookie_names", []),
        "origin_count": redacted.get("origin_count", 0),
        "required_cookies": required,
        "missing_cookies": missing,
        "has_required_cookies": not missing,
    }


def import_cdp_cookies_to_storage_state(
    dest_path: str | os.PathLike[str],
    *,
    cookie_reader: Callable[[], Iterable[Mapping[str, Any]]],
    google_only: bool = True,
    origins: list | None = None,
) -> dict[str, Any]:
    """Persist injected DevTools cookies and return a redacted summary."""

    if not callable(cookie_reader):
        raise ValidationError("cookie reader is required")
    state = storage_state_from_cdp_cookies(
        cookie_reader(), google_only=google_only, origins=origins
    )
    _cookies.save_storage_state(dest_path, state)
    return redacted_storage_summary(state)


def capture_cdp_cookies_until_ready(
    dest_path: str | os.PathLike[str],
    *,
    cookie_reader: Callable[[], Iterable[Mapping[str, Any]]],
    attempts: int,
    delay_seconds: float,
    sleep: Callable[[float], Any] = time.sleep,
    google_only: bool = True,
    include_domains: set[str] | None = None,
    origins: list | None = None,
) -> dict[str, Any]:
    """Wait for required auth cookies before persisting storage_state.

    First-run browser login can take human time. This helper repeatedly samples
    DevTools cookies, but writes the storage-state file only after the minimum
    required NotebookLM auth cookies are present, avoiding a durable partial auth
    state from an early capture.
    """

    if not callable(cookie_reader):
        raise ValidationError("cookie reader is required")
    if attempts < 1:
        raise ValidationError("cookie capture attempts must be positive")
    if delay_seconds < 0:
        raise ValidationError("cookie capture delay must not be negative")

    last_summary: dict[str, Any] | None = None
    for attempt in range(1, attempts + 1):
        try:
            state = storage_state_from_cdp_cookies(
                cookie_reader(),
                google_only=google_only,
                include_domains=include_domains,
                origins=origins,
            )
        except NetworkError:
            if attempt >= attempts:
                raise
            sleep(delay_seconds)
            continue
        summary = redacted_storage_summary(state)
        summary["capture_attempts"] = attempt
        if summary.get("has_required_cookies") is True:
            _cookies.save_storage_state(dest_path, state)
            return summary
        last_summary = summary
        if attempt < attempts:
            sleep(delay_seconds)

    missing = (
        [] if last_summary is None else list(last_summary.get("missing_cookies", []))
    )
    missing_text = ", ".join(str(name) for name in missing) or "unknown"
    raise AuthenticationError(
        "interactive login did not capture required cookies before timeout; "
        f"missing cookies: {missing_text}"
    )
