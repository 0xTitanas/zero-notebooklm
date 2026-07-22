"""Small stdlib-only HTTP transport helpers for NotebookLM Bare Phase 1.

This module intentionally implements only generic transport infrastructure.
It does not know NotebookLM endpoints, auth, cookies, RPC IDs, or Google session
state.
"""

from __future__ import annotations

import socket
import zlib
from dataclasses import dataclass
from http.client import HTTPConnection, HTTPException, HTTPResponse, HTTPSConnection
from typing import Mapping
from urllib.parse import urljoin, urlsplit

from .errors import (
    BodyTooLargeError,
    HTTPTransportError,
    RedirectError,
    UnsupportedSchemeError,
)

_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_DEFAULT_TIMEOUT = 30.0
_DEFAULT_MAX_BODY_BYTES = 10 * 1024 * 1024
_CHUNK_SIZE = 64 * 1024


@dataclass(frozen=True)
class Response:
    """Buffered HTTP response from the stdlib transport."""

    status: int
    url: str
    headers: Mapping[str, str]
    body: bytes

    def text(self, encoding: str | None = None, errors: str = "replace") -> str:
        """Decode response bytes using an explicit or Content-Type charset."""

        chosen = (
            encoding
            or _charset_from_content_type(self.headers.get("content-type", ""))
            or "utf-8"
        )
        return self.body.decode(chosen, errors=errors)


def get(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float | None = None,
    max_redirects: int = 5,
    max_body_bytes: int = _DEFAULT_MAX_BODY_BYTES,
    follow_redirects: bool = True,
) -> Response:
    """Perform a bounded stdlib HTTP GET."""

    return request(
        "GET",
        url,
        headers=headers,
        timeout=timeout,
        max_redirects=max_redirects,
        max_body_bytes=max_body_bytes,
        follow_redirects=follow_redirects,
    )


def post(
    url: str,
    *,
    body: bytes | str | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float | None = None,
    max_redirects: int = 5,
    max_body_bytes: int = _DEFAULT_MAX_BODY_BYTES,
    follow_redirects: bool = True,
) -> Response:
    """Perform a bounded stdlib HTTP POST."""

    return request(
        "POST",
        url,
        headers=headers,
        body=body,
        timeout=timeout,
        max_redirects=max_redirects,
        max_body_bytes=max_body_bytes,
        follow_redirects=follow_redirects,
    )


def request(
    method: str,
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    body: bytes | str | None = None,
    timeout: float | None = None,
    max_redirects: int = 5,
    max_body_bytes: int = _DEFAULT_MAX_BODY_BYTES,
    follow_redirects: bool = True,
) -> Response:
    """Perform a bounded HTTP/HTTPS request with explicit redirect handling."""

    if max_redirects < 0:
        raise RedirectError("max_redirects must be >= 0")
    if max_body_bytes < 0:
        raise BodyTooLargeError("max_body_bytes must be >= 0")

    current_url = url
    current_method = method.upper()
    current_body = _coerce_body(body)
    current_headers = _prepare_headers(headers, current_body)

    for redirect_count in range(max_redirects + 1):
        response = _single_request(
            current_method,
            current_url,
            headers=current_headers,
            body=current_body,
            timeout=_DEFAULT_TIMEOUT if timeout is None else timeout,
            max_body_bytes=max_body_bytes,
        )
        if not follow_redirects:
            return response
        if response.status not in _REDIRECT_STATUSES:
            return response

        if redirect_count >= max_redirects:
            raise RedirectError(
                f"redirect limit exceeded at {_redact_url_for_error(current_url)}"
            )
        location = response.headers.get("location")
        if not location:
            raise RedirectError(
                f"redirect response from {_redact_url_for_error(current_url)} missing Location header"
            )
        current_url = urljoin(current_url, location)
        if response.status == 303 or (
            response.status in {301, 302} and current_method != "GET"
        ):
            current_method = "GET"
            current_body = None
            current_headers = _prepare_headers(headers, current_body)

    raise RedirectError(
        f"redirect limit exceeded at {_redact_url_for_error(current_url)}"
    )  # defensive fallback


def _redact_url_for_error(url: str) -> str:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return "<redacted-url>"
    netloc = parts.hostname or ""
    if port is not None:
        netloc = f"{netloc}:{port}"
    path = "/<redacted>" if parts.path and parts.path != "/" else (parts.path or "")
    return f"{parts.scheme}://{netloc}{path}"


def _single_request(
    method: str,
    url: str,
    *,
    headers: Mapping[str, str],
    body: bytes | None,
    timeout: float,
    max_body_bytes: int,
) -> Response:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        raise UnsupportedSchemeError(
            f"unsupported URL scheme: {parts.scheme or '<missing>'}"
        )
    if not parts.hostname:
        raise UnsupportedSchemeError("HTTP URL must include a host")

    conn_cls = HTTPSConnection if parts.scheme == "https" else HTTPConnection
    port = parts.port
    target = parts.path or "/"
    if parts.query:
        target += "?" + parts.query

    conn = conn_cls(parts.hostname, port=port, timeout=timeout)
    try:
        conn.request(method, target, body=body, headers=dict(headers))
        raw = conn.getresponse()
        response_headers = _normalize_headers(raw)
        raw_body = _read_limited(raw, max_body_bytes)
        body_bytes = _decode_content(raw_body, response_headers, max_body_bytes)
        return Response(
            status=raw.status, url=url, headers=response_headers, body=body_bytes
        )
    except (OSError, socket.timeout, TimeoutError, HTTPException) as exc:
        raise HTTPTransportError(
            f"HTTP {method} {_redact_url_for_error(url)} failed: {exc.__class__.__name__}: {exc}"
        ) from exc
    finally:
        conn.close()


def _coerce_body(body: bytes | str | None) -> bytes | None:
    if body is None:
        return None
    if isinstance(body, bytes):
        return body
    return body.encode("utf-8")


def _prepare_headers(
    headers: Mapping[str, str] | None, body: bytes | None
) -> dict[str, str]:
    prepared = {str(k): str(v) for k, v in (headers or {}).items()}
    if body is not None and not any(k.lower() == "content-length" for k in prepared):
        prepared["Content-Length"] = str(len(body))
    return prepared


def _normalize_headers(raw: HTTPResponse) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in raw.getheaders():
        lowered = key.lower()
        if lowered == "set-cookie" and lowered in normalized:
            normalized[lowered] = normalized[lowered] + "\n" + value
        else:
            normalized[lowered] = value
    return normalized


def _read_limited(raw: HTTPResponse, max_body_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        # Read at most one byte beyond the configured limit. This keeps no-length
        # responses from blocking while waiting for EOF before the limit can fire.
        remaining_until_limit = max_body_bytes + 1 - total
        read_size = max(1, min(_CHUNK_SIZE, remaining_until_limit))
        chunk = raw.read(read_size)
        if not chunk:
            break
        total += len(chunk)
        if total > max_body_bytes:
            raise BodyTooLargeError(f"response body exceeded {max_body_bytes} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


def _decode_content(
    body: bytes, headers: Mapping[str, str], max_body_bytes: int
) -> bytes:
    encodings = [
        part.strip().lower() for part in headers.get("content-encoding", "").split(",")
    ]
    if "gzip" not in encodings:
        return body

    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    chunks: list[bytes] = []
    total = 0
    try:
        for offset in range(0, len(body), _CHUNK_SIZE):
            remaining_until_limit = max_body_bytes + 1 - total
            if remaining_until_limit <= 0:
                raise BodyTooLargeError(
                    f"decompressed response body exceeded {max_body_bytes} bytes"
                )
            part = decompressor.decompress(
                body[offset : offset + _CHUNK_SIZE], remaining_until_limit
            )
            total += len(part)
            if total > max_body_bytes:
                raise BodyTooLargeError(
                    f"decompressed response body exceeded {max_body_bytes} bytes"
                )
            chunks.append(part)
            if decompressor.unconsumed_tail and total >= max_body_bytes:
                raise BodyTooLargeError(
                    f"decompressed response body exceeded {max_body_bytes} bytes"
                )

        remaining_until_limit = max_body_bytes + 1 - total
        if remaining_until_limit <= 0:
            raise BodyTooLargeError(
                f"decompressed response body exceeded {max_body_bytes} bytes"
            )
        tail = decompressor.flush(remaining_until_limit)
    except zlib.error as exc:
        raise HTTPTransportError(
            f"gzip response decode failed: {exc.__class__.__name__}: {exc}"
        ) from exc

    total += len(tail)
    if total > max_body_bytes:
        raise BodyTooLargeError(
            f"decompressed response body exceeded {max_body_bytes} bytes"
        )
    chunks.append(tail)
    if not decompressor.eof:
        raise HTTPTransportError("gzip response decode failed: incomplete stream")
    return b"".join(chunks)


def _charset_from_content_type(content_type: str) -> str | None:
    for part in content_type.split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name.lower() == "charset" and value:
            return value.strip().strip('"')
    return None


__all__ = ["Response", "request", "get", "post"]
