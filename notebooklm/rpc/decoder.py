"""Tiny stdlib-only batchexecute parser foundation.

Phase 3A1 intentionally implements only offline decoding for committed synthetic
batchexecute fixtures. It performs no network I/O, authentication, browser access,
notebook mutation, or request sending.
"""

from __future__ import annotations

import json
import logging
import reprlib
import threading
import urllib.parse
from typing import Any, NoReturn

from ..exceptions import (
    AuthError,
    ClientError,
    NetworkError,
    RateLimitError,
    RPCError,
    RPCTimeoutError,
    ServerError,
    UnknownRPCMethodError,
    _truncate_response_preview,
)
from ..errors import ValidationError
from .types import RPCErrorCode

XSSI_PREFIX = ")]}'"
_JSON_DECODE_ERROR = object()
_SAFE_INDEX_REPR_TRUNCATE = 200
logger = logging.getLogger(__name__)
_BYTE_COUNT_MISMATCH_TOTAL = 0
_BYTE_COUNT_MISMATCH_LOCK = threading.Lock()
_SAFE_INDEX_REPR = reprlib.Repr()
_SAFE_INDEX_REPR.maxstring = _SAFE_INDEX_REPR_TRUNCATE
_SAFE_INDEX_REPR.maxother = _SAFE_INDEX_REPR_TRUNCATE
_SAFE_INDEX_REPR.maxlist = 10
_SAFE_INDEX_REPR.maxtuple = 10
_SAFE_INDEX_REPR.maxdict = 10
_SAFE_INDEX_REPR.maxarray = 10
_SAFE_INDEX_REPR.maxset = 10
_SAFE_INDEX_REPR.maxfrozenset = 10
_SAFE_INDEX_REPR.maxdeque = 10
_SAFE_INDEX_REPR.maxlevel = 4
_GRPC_STATUS_MESSAGES: dict[int, str] = {
    0: "OK",
    1: "Cancelled",
    2: "Unknown",
    3: "Invalid argument",
    4: "Deadline exceeded",
    5: "Not found",
    6: "Already exists",
    7: "Permission denied",
    8: "Resource exhausted",
    9: "Failed precondition",
    10: "Aborted",
    11: "Out of range",
    12: "Not implemented",
    13: "Internal",
    14: "Unavailable",
    15: "Data loss",
    16: "Unauthenticated",
}
_ACCOUNT_MISMATCH_HINT = (
    " If you have multiple Google accounts signed in, this is commonly an "
    "account-routing mismatch — the request defaults to account index 0 when "
    "no authuser is set. See issues #114 and #294 for context."
)
_ERROR_CODE_MESSAGES: dict[int, tuple[str, bool]] = {
    RPCErrorCode.INVALID_REQUEST: (
        "Invalid request parameters. Check your input and try again.",
        False,
    ),
    RPCErrorCode.UNAUTHORIZED: (
        "Authentication required. Run 'notebooklm login' to re-authenticate.",
        False,
    ),
    RPCErrorCode.FORBIDDEN: (
        "Insufficient permissions for this operation.",
        False,
    ),
    RPCErrorCode.NOT_FOUND: (
        "Requested resource not found.",
        False,
    ),
    RPCErrorCode.RATE_LIMITED: (
        "API rate limit exceeded. Please wait before retrying.",
        True,
    ),
    RPCErrorCode.SERVER_ERROR: (
        "Server error occurred. This is usually temporary - try again later.",
        True,
    ),
}


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

    Live Google responses can declare counts that do not match UTF-8 byte
    lengths, so parse by chunk lines and treat count mismatches as tolerated
    framing drift when the following payload is valid JSON. Malformed payloads
    still fail closed without echoing caller-provided material.
    """

    try:
        lines = body.lstrip("\r\n").splitlines()
    except UnicodeError:
        _fail("response", "malformed chunk text")
    chunks: list[Any] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip().strip("\r")
        if not line:
            i += 1
            continue
        if not line.isdigit():
            _fail("response", "malformed chunk length")
        length = int(line)
        i += 1
        if length == 0:
            if any(rest.strip().strip("\r") for rest in lines[i:]):
                _fail("response", "trailing chunk data")
            return chunks
        if i >= len(lines):
            _fail("response", "missing chunk payload")
        chunk_text = lines[i].strip("\r")
        chunk = _decode_response_envelope(chunk_text)
        chunks.extend(chunk)
        i += 1
    if chunks:
        return chunks
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


parse_batchexecute_response = decode_batchexecute_response


def strip_anti_xssi(response: str) -> str:
    if not isinstance(response, str):
        _fail("response", "expected text")
    if response.startswith(XSSI_PREFIX + "\r\n"):
        return response[len(XSSI_PREFIX) + 2 :]
    if response.startswith(XSSI_PREFIX + "\n"):
        return response[len(XSSI_PREFIX) + 1 :]
    if response.startswith(XSSI_PREFIX):
        return response[len(XSSI_PREFIX) :]
    return response


def parse_chunked_response(response: str) -> list[Any]:
    global _BYTE_COUNT_MISMATCH_TOTAL

    if not response or not response.strip():
        return []

    chunks: list[Any] = []
    malformed_payload_records = 0
    payload_records = 0
    malformed_framing_records = 0
    framing_records = 0
    response_records = 0
    lines = [line.removesuffix("\r") for line in response.strip().split("\n")]

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        try:
            byte_count = int(line)
            framing_records += 1
            response_records += 1
            i += 1
            if i >= len(lines):
                malformed_framing_records += 1
                logger.warning("Skipping byte-count line %d without payload", i)
                continue
            json_str = lines[i]
            payload_records += 1
            actual_byte_count = len(json_str.encode("utf-8"))
            if actual_byte_count != byte_count:
                logger.debug(
                    "Chunk at line %d declares %d bytes but payload is %d bytes",
                    i + 1,
                    byte_count,
                    actual_byte_count,
                )
                with _BYTE_COUNT_MISMATCH_LOCK:
                    _BYTE_COUNT_MISMATCH_TOTAL += 1
            try:
                chunks.append(json.loads(json_str))
            except json.JSONDecodeError as exc:
                malformed_payload_records += 1
                logger.warning("Skipping malformed chunk at line %d: %s", i + 1, exc)
            i += 1
        except ValueError:
            payload_records += 1
            response_records += 1
            try:
                chunks.append(json.loads(line))
            except json.JSONDecodeError as exc:
                malformed_payload_records += 1
                logger.warning("Skipping non-JSON line at %d: %s", i + 1, exc)
            i += 1

    payload_error_rate = malformed_payload_records / payload_records if payload_records else 0
    framing_error_rate = malformed_framing_records / framing_records if framing_records else 0
    malformed_records = malformed_payload_records + malformed_framing_records
    response_error_rate = malformed_records / response_records if response_records else 0

    if payload_error_rate > 0.1:
        raise RPCError(
            f"Response parsing failed: {malformed_payload_records} of "
            f"{payload_records} payload records malformed. "
            f"This may indicate API changes or data corruption.",
            raw_response=response,
        )
    if framing_error_rate > 0.1:
        raise RPCError(
            f"Response parsing failed: {malformed_framing_records} of "
            f"{framing_records} framing records malformed. "
            f"This may indicate API changes or data corruption.",
            raw_response=response,
        )
    if response_error_rate > 0.1:
        raise RPCError(
            f"Response parsing failed: {malformed_records} of "
            f"{response_records} response records malformed. "
            f"This may indicate API changes or data corruption.",
            raw_response=response,
        )
    return chunks


def _iter_rpc_items(chunks: list[Any]) -> list[list[Any]]:
    items: list[list[Any]] = []
    for chunk in chunks:
        if not isinstance(chunk, list) or not chunk:
            continue
        first = chunk[0]
        candidates = chunk if isinstance(first, list) else [chunk]
        for item in candidates:
            if isinstance(item, list):
                items.append(item)
    return items


def collect_rpc_ids(chunks: list[Any]) -> list[str]:
    found: list[str] = []
    for row in _iter_rpc_items(chunks):
        if len(row) > 1 and row[0] in {"wrb.fr", "er"} and isinstance(row[1], str):
            found.append(row[1])
    return found


def _extract_status_code(error_info: Any) -> tuple[int, str] | None:
    if not isinstance(error_info, list) or len(error_info) != 1:
        return None
    code = error_info[0]
    if type(code) is not int or code not in _GRPC_STATUS_MESSAGES:
        return None
    return code, _GRPC_STATUS_MESSAGES[code]


def _find_wrb_status(chunks: list[Any], rpc_id: str) -> tuple[int, str] | None:
    for row in _iter_rpc_items(chunks):
        if len(row) < 6 or row[0] != "wrb.fr" or row[1] != rpc_id:
            continue
        if row[2] is not None or row[5] is None:
            continue
        status = _extract_status_code(row[5])
        if status is not None:
            return status
    return None


def _contains_user_displayable_error(obj: Any) -> bool:
    if isinstance(obj, str):
        return "UserDisplayableError" in obj
    if isinstance(obj, list):
        return any(_contains_user_displayable_error(item) for item in obj)
    if isinstance(obj, dict):
        return any(_contains_user_displayable_error(value) for value in obj.values())
    return False


def _extract_user_displayable_status(error_info: Any) -> tuple[int, str] | None:
    if not isinstance(error_info, list) or not error_info:
        return None
    code = error_info[0]
    if type(code) is not int or code not in _GRPC_STATUS_MESSAGES:
        return None
    return code, _GRPC_STATUS_MESSAGES[code]


def _user_displayable_error_message(error_info: Any) -> str:
    message = "API rate limit or quota exceeded. Please wait before retrying."
    status = _extract_user_displayable_status(error_info)
    if status is None:
        return message
    code, label = status
    return f"{message} Upstream status code {code} ({label})."


_SENTINEL_NO_RESULT = object()


def extract_rpc_result(chunks: list[Any], rpc_id: str) -> Any:
    last_result: Any = _SENTINEL_NO_RESULT
    for row in _iter_rpc_items(chunks):
        if len(row) < 3:
            continue
        tag = row[0]
        id_field = row[1]
        if tag == "er" and id_field == rpc_id:
            error_code = row[2]
            if isinstance(error_code, int):
                error_msg, is_retryable = get_error_message_for_code(error_code)
                logger.debug(
                    "RPC error code %d for %s: %s (retryable: %s)",
                    error_code,
                    rpc_id,
                    error_msg,
                    is_retryable,
                )
            else:
                error_msg = str(error_code) if error_code else "Unknown error"
            raise RPCError(error_msg, method_id=rpc_id, rpc_code=error_code)
        if tag != "wrb.fr" or id_field != rpc_id:
            continue

        result_data = row[2]
        if result_data is None and len(row) > 5:
            error_info = row[5]
            if error_info is not None and _contains_user_displayable_error(error_info):
                raise RateLimitError(
                    _user_displayable_error_message(error_info),
                    method_id=rpc_id,
                    rpc_code="USER_DISPLAYABLE_ERROR",
                )
        if isinstance(result_data, str):
            try:
                parsed: Any = json.loads(result_data)
            except json.JSONDecodeError:
                parsed = result_data
        else:
            parsed = result_data
        if parsed is not None or last_result is _SENTINEL_NO_RESULT:
            last_result = parsed
    if last_result is _SENTINEL_NO_RESULT:
        return None
    return last_result


def decode_response(raw_response: str, rpc_id: str, allow_null: bool = False) -> Any:
    cleaned = strip_anti_xssi(raw_response)
    chunks = parse_chunked_response(cleaned)
    response_preview = cleaned
    found_ids = collect_rpc_ids(chunks)

    try:
        result = extract_rpc_result(chunks, rpc_id)
    except RPCError as exc:
        if not exc.found_ids:
            exc.found_ids = found_ids
        if not exc.raw_response:
            exc.raw_response = _truncate_response_preview(response_preview)
        raise

    if result is not None:
        return result

    if found_ids and rpc_id not in found_ids:
        raise UnknownRPCMethodError(
            f"No result found for RPC ID '{rpc_id}'. "
            f"Response contains IDs: {found_ids}. "
            f"The RPC method ID may have changed.",
            method_id=rpc_id,
            found_ids=list(found_ids),
            raw_response=response_preview,
        )

    if not found_ids:
        raise RPCError(
            f"No result found for RPC ID: {rpc_id} "
            f"(response contained no RPC data — {len(chunks)} chunks parsed)",
            method_id=rpc_id,
            raw_response=response_preview,
        )

    if allow_null:
        return None

    status = _find_wrb_status(chunks, rpc_id)
    found_ids_suffix = f" Found IDs: {found_ids}."
    if status is not None:
        code, label = status
        message = f"RPC {rpc_id} returned null result with status code {code} ({label})."
        if code in (5, 7):
            raise ClientError(
                message + found_ids_suffix + _ACCOUNT_MISMATCH_HINT,
                method_id=rpc_id,
                rpc_code=code,
                found_ids=found_ids,
                raw_response=response_preview,
            )
        raise RPCError(
            message + found_ids_suffix,
            method_id=rpc_id,
            rpc_code=code,
            found_ids=found_ids,
            raw_response=response_preview,
        )
    raise RPCError(
        f"RPC {rpc_id} returned null result data "
        f"(possible server error or parameter mismatch).{found_ids_suffix}",
        method_id=rpc_id,
        found_ids=found_ids,
        raw_response=response_preview,
    )


def get_error_message_for_code(code: int | None) -> tuple[str, bool]:
    if code is None:
        return ("Unknown error occurred.", False)
    if code in _ERROR_CODE_MESSAGES:
        return _ERROR_CODE_MESSAGES[code]
    if 400 <= code < 500:
        return (f"Client error {code}. Check your request parameters.", False)
    if 500 <= code < 600:
        return (f"Server error {code}. This is usually temporary - try again later.", True)
    return (f"Error code: {code}", False)


def _safe_index_truncate(value: Any) -> str:
    text = _SAFE_INDEX_REPR.repr(value)
    if len(text) <= _SAFE_INDEX_REPR_TRUNCATE:
        return text
    return text[:_SAFE_INDEX_REPR_TRUNCATE] + "..."


def safe_index(data: Any, *path: int, method_id: str | int | None, source: str) -> Any:
    current: Any = data
    for i, key in enumerate(path):
        if isinstance(current, (str, bytes, bytearray)):
            failing_path = tuple(path[:i])
            raise UnknownRPCMethodError(
                f"safe_index drift at path {failing_path}[{key}]: cannot index "
                f"into {type(current).__name__} (expected a nested list/tuple)",
                method_id=method_id,
                path=failing_path,
                source=source,
                data_at_failure=_safe_index_truncate(current),
            )
        try:
            current = current[key]
        except (IndexError, TypeError, KeyError) as exc:
            failing_path = tuple(path[:i])
            raise UnknownRPCMethodError(
                f"safe_index drift at path {failing_path}[{key}]",
                method_id=method_id,
                path=failing_path,
                source=source,
                data_at_failure=_safe_index_truncate(current),
            ) from exc
    return current


def reset_byte_count_mismatch_total() -> None:
    global _BYTE_COUNT_MISMATCH_TOTAL
    with _BYTE_COUNT_MISMATCH_LOCK:
        _BYTE_COUNT_MISMATCH_TOTAL = 0


def byte_count_mismatch_total() -> int:
    with _BYTE_COUNT_MISMATCH_LOCK:
        return _BYTE_COUNT_MISMATCH_TOTAL


__all__ = [
    "RPCError",
    "AuthError",
    "NetworkError",
    "RPCTimeoutError",
    "RateLimitError",
    "ServerError",
    "ClientError",
    "UnknownRPCMethodError",
    "RPCErrorCode",
    "get_error_message_for_code",
    "strip_anti_xssi",
    "parse_chunked_response",
    "collect_rpc_ids",
    "extract_rpc_result",
    "decode_response",
    "safe_index",
    "byte_count_mismatch_total",
    "reset_byte_count_mismatch_total",
]
