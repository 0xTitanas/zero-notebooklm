"""Tiny stdlib-only batchexecute parser foundation.

Phase 3A1 intentionally implements only offline decoding for committed synthetic
batchexecute fixtures. It performs no network I/O, authentication, browser access,
notebook mutation, or request sending.
"""

from __future__ import annotations

import json
import urllib.parse
from typing import Any, NoReturn

from .errors import ValidationError

XSSI_PREFIX = ")]}'"
_JSON_DECODE_ERROR = object()


def _fail(kind: str, reason: str) -> NoReturn:
    """Raise a deterministic, input-redacted batchexecute validation error."""

    raise ValidationError(f"invalid batchexecute {kind}: {reason}")


def _loads_json(text: str) -> Any:
    """Return parsed JSON or a private sentinel without retaining input context."""

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return _JSON_DECODE_ERROR


def _decode_response_envelope(body: str) -> list[Any]:
    envelope = _loads_json(body)
    if envelope is _JSON_DECODE_ERROR:
        _fail("response", "malformed envelope JSON")
    if not isinstance(envelope, list):
        _fail("response", "envelope must be a list")
    return envelope


def _decode_chunk_frames(body: str) -> list[Any]:
    """Decode length-prefixed batchexecute ``rt=c`` chunks.

    The parser is intentionally strict for this offline foothold: declared byte
    counts must match the UTF-8 chunk bytes, each chunk must be JSON, and no
    caller-provided material is ever echoed in errors.
    """

    try:
        data = body.lstrip("\r\n").encode("utf-8")
    except UnicodeEncodeError:
        _fail("response", "malformed chunk text")
    chunks: list[Any] = []
    pos = 0
    while pos < len(data):
        line_end = data.find(b"\n", pos)
        if line_end < 0:
            _fail("response", "missing chunk length")
        length_text = data[pos:line_end].strip(b"\r")
        if not length_text or not all(48 <= b <= 57 for b in length_text):
            _fail("response", "malformed chunk length")
        length = int(length_text)
        pos = line_end + 1
        if length == 0:
            if data[pos:].strip(b"\r\n\t "):
                _fail("response", "trailing chunk data")
            return chunks
        if pos + length > len(data):
            _fail("response", "chunk byte count mismatch")
        chunk_bytes = data[pos : pos + length]
        try:
            chunk_text = chunk_bytes.decode("utf-8")
        except UnicodeDecodeError:
            _fail("response", "malformed chunk text")
        pos += length
        if pos < len(data) and data[pos : pos + 2] == b"\r\n":
            pos += 2
        elif pos < len(data) and data[pos : pos + 1] == b"\n":
            pos += 1
        elif pos < len(data):
            _fail("response", "chunk byte count mismatch")
        chunk = _decode_response_envelope(chunk_text)
        chunks.extend(chunk)
    _fail("response", "missing terminal chunk")


def _response_rows_after_xssi(text: str) -> list[Any]:
    body = text[len(XSSI_PREFIX) :]
    stripped = body.lstrip("\r\n")
    if stripped[:1] and "0" <= stripped[0] <= "9":
        return _decode_chunk_frames(body)
    return _decode_response_envelope(body)


def decode_batchexecute_response(text: str) -> list[Any]:
    """Decode an XSSI-guarded batchexecute response.

    The useful values are JSON strings nested in ``wrb.fr`` rows. This helper
    strips the XSSI guard, parses either a simple JSON envelope or strict
    length-prefixed ``rt=c`` chunk frames, selects ``wrb.fr`` rows, parses each
    row's payload string, and returns those parsed payloads in order. Validation
    failures are fail-closed and never echo caller-provided input.
    """

    if not isinstance(text, str):
        _fail("response", "expected text")
    if not text.startswith(XSSI_PREFIX):
        _fail("response", "missing XSSI guard")

    payloads: list[Any] = []
    for row in _response_rows_after_xssi(text):
        if not isinstance(row, list) or not row or row[0] != "wrb.fr":
            continue
        if len(row) < 3:
            _fail("response", "wrb.fr row missing payload")
        payload_text = row[2]
        if not isinstance(payload_text, str):
            _fail("response", "wrb.fr payload must be text")
        payload = _loads_json(payload_text)
        if payload is _JSON_DECODE_ERROR:
            _fail("response", "malformed wrb.fr payload JSON")
        payloads.append(payload)

    if not payloads:
        _fail("response", "missing wrb.fr payload")
    return payloads


def decode_batchexecute_request(text: str) -> Any:
    """Decode the form-encoded ``f.req`` field from a batchexecute request body."""

    if not isinstance(text, str):
        _fail("request", "expected text")
    fields = urllib.parse.parse_qs(text.strip(), keep_blank_values=True)
    values = fields.get("f.req")
    if not values or not isinstance(values[0], str) or values[0] == "":
        _fail("request", "missing f.req")
    value = _loads_json(values[0])
    if value is _JSON_DECODE_ERROR:
        _fail("request", "malformed f.req JSON")
    return value


__all__ = [
    "XSSI_PREFIX",
    "decode_batchexecute_response",
    "decode_batchexecute_request",
]
