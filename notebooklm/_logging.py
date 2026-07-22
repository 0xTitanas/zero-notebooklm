"""Logging helpers shared by the package root and public ``notebooklm.log``."""

from __future__ import annotations

import logging
import os
import re
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any

_current_request_id: ContextVar[str | None] = ContextVar("notebooklm_request_id", default=None)
_HANDLER_MARKER = "_notebooklm_redacting"
_DEFAULT_FMT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
_DEFAULT_DATEFMT = "%H:%M:%S"
_THIRD_PARTY_LOGGERS = ("httpx", "urllib3")
_CSRF_MARKER_QUOTED = re.compile(r"(\b(?:SNlM0e|FdrFJe))([\"']?\s*:\s*)([\"'])(?:[^\"'\\]|\\.)*\3")
_CSRF_MARKER_HTML_ESCAPED = re.compile(
    r"(\b(?:SNlM0e|FdrFJe))((?:&quot;)?\s*:\s*)(&quot;)(?:(?!&quot;).)*&quot;"
)
_CSRF_MARKER_UNQUOTED = re.compile(
    r"(\b(?:SNlM0e|FdrFJe))(\s*(?:value\s+is|[:=])\s*)[^\s\"'<>&;,.?!)\]}]+"
)
_CSRF_BARE_TOKEN = re.compile(r"(AF1_QpN-)[A-Za-z0-9_-]+")
_REDACT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(\bat=)[^&\s\"'<>]+"), r"\1***"),
    (_CSRF_MARKER_QUOTED, r"\1\2\3***\3"),
    (_CSRF_MARKER_HTML_ESCAPED, r"\1\2\3***&quot;"),
    (_CSRF_MARKER_UNQUOTED, r"\1\2***"),
    (re.compile(r"(\bcsrf=)[^&\s\"'<>]+", re.IGNORECASE), r"\1***"),
    (_CSRF_BARE_TOKEN, r"\1***"),
    (re.compile(r"(\bf\.sid=)[^&\s\"'<>]+"), r"\1***"),
    (re.compile(r"(\bupload_id=)[^&\s\"'<>]+", re.IGNORECASE), r"\1***"),
    (
        re.compile(
            r"(\b(?:refresh_token|access_token|id_token|code)=)[^&\s\"'<>]+",
            re.IGNORECASE,
        ),
        r"\1***",
    ),
    (
        re.compile(
            r"(__Secure-1PAPISID|__Secure-3PAPISID"
            r"|__Secure-1PSIDTS|__Secure-3PSIDTS"
            r"|__Secure-1PSIDCC|__Secure-3PSIDCC"
            r"|__Secure-1PSID|__Secure-3PSID"
            r"|SAPISID|APISID|SIDCC|HSID|SSID|LSID|SID)=([^;\s,\"'<>]+)"
        ),
        r"\1=***",
    ),
    (re.compile(r"(Authorization:\s*Bearer\s+)[^\s\"'<>]+", re.IGNORECASE), r"\1***"),
    (re.compile(r"(Cookie:\s*)[^\r\n]+", re.IGNORECASE), r"\1***"),
    (re.compile(r"(Set-Cookie:\s*)[^\r\n]+", re.IGNORECASE), r"\1***"),
)
SECRET_FAST_PATH_TOKENS: tuple[str, ...] = (
    "sid",
    "sapisid",
    "csrf",
    "snlm0e",
    "fdrfje",
    "af1_qpn-",
    "f.sid",
    "continue=",
    "authuser=",
    "upload_id=",
    "at=",
    "cookie",
    "authorization",
    "set-cookie",
    "_token=",
    "code=",
)


def set_request_id(req_id: str | None = None) -> Token[str | None]:
    if req_id is None:
        req_id = uuid.uuid4().hex[:8]
    return _current_request_id.set(req_id)


def reset_request_id(token: Token[str | None]) -> None:
    _current_request_id.reset(token)


def get_request_id() -> str | None:
    return _current_request_id.get()


@contextmanager
def correlation_id(req_id: str | None = None) -> Iterator[str]:
    token = set_request_id(req_id)
    try:
        yield get_request_id() or ""
    finally:
        reset_request_id(token)


def scrub_secrets(text: object) -> str:
    scrubbed = str(text)
    lowered = scrubbed.lower()
    if not any(token in lowered for token in SECRET_FAST_PATH_TOKENS):
        return scrubbed
    for pattern, replacement in _REDACT_PATTERNS:
        scrubbed = pattern.sub(replacement, scrubbed)
    return scrubbed


_scrub = scrub_secrets


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
        except (TypeError, ValueError):
            rendered = str(record.msg)
        record.msg = scrub_secrets(rendered)
        record.args = ()
        if record.exc_info and not record.exc_text:
            record.exc_text = scrub_secrets(logging.Formatter().formatException(record.exc_info))
        elif record.exc_text:
            record.exc_text = scrub_secrets(record.exc_text)
        if record.stack_info:
            record.stack_info = scrub_secrets(record.stack_info)
        request_id = get_request_id()
        if request_id and not getattr(record, "_notebooklm_reqid_applied", False):
            record.msg = f"[req={request_id}] {record.msg}"
            record._notebooklm_reqid_applied = True
        return True


class RedactingFormatter(logging.Formatter):
    def __init__(self, inner: logging.Formatter | None = None) -> None:
        super().__init__()
        self._inner = inner or logging.Formatter(_DEFAULT_FMT, _DEFAULT_DATEFMT)

    def format(self, record: logging.LogRecord) -> str:
        rendered = scrub_secrets(self._inner.format(record))
        if record.exc_text:
            record.exc_text = scrub_secrets(record.exc_text)
        return rendered

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        return self._inner.formatTime(record, datefmt)

    def formatException(self, ei: logging._SysExcInfoType | tuple[None, None, None]) -> str:
        return scrub_secrets(self._inner.formatException(ei))

    def formatStack(self, stack_info: str) -> str:
        return scrub_secrets(self._inner.formatStack(stack_info))


def _has_redacting_filter(filters: Iterable[Any]) -> bool:
    return any(isinstance(filter_, RedactingFilter) for filter_ in filters)


def _has_marked_handler(handlers: list[logging.Handler]) -> bool:
    return any(getattr(handler, _HANDLER_MARKER, False) for handler in handlers)


def apply_redaction(handler: logging.Handler) -> logging.Handler:
    if not _has_redacting_filter(handler.filters):
        handler.addFilter(RedactingFilter())
    if not isinstance(handler.formatter, RedactingFormatter):
        handler.setFormatter(RedactingFormatter(handler.formatter))
    setattr(handler, _HANDLER_MARKER, True)
    return handler


def _install_thirdparty_redaction(*logger_names: str) -> None:
    for name in logger_names:
        logger = logging.getLogger(name)
        if not _has_redacting_filter(logger.filters):
            logger.addFilter(RedactingFilter())


def configure_logging() -> None:
    logger = logging.getLogger("notebooklm")
    for handler in logger.handlers:
        apply_redaction(handler)
    if not _has_marked_handler(logger.handlers):
        level_name = os.environ.get("NOTEBOOKLM_LOG_LEVEL", "WARNING").upper()
        if os.environ.get("NOTEBOOKLM_DEBUG_RPC", "").lower() in ("1", "true", "yes"):
            level_name = "DEBUG"
        logger.setLevel(getattr(logging, level_name, logging.WARNING))
        logger.addHandler(_make_default_handler())
    logger.propagate = True
    _install_thirdparty_redaction(*_THIRD_PARTY_LOGGERS)


def _make_default_handler() -> logging.StreamHandler:
    handler = logging.StreamHandler()
    handler.setLevel(logging.NOTSET)
    handler.setFormatter(logging.Formatter(_DEFAULT_FMT, _DEFAULT_DATEFMT))
    apply_redaction(handler)
    return handler


def install_redaction(*logger_names: str) -> None:
    for name in logger_names:
        logger = logging.getLogger(name)
        for handler in logger.handlers:
            apply_redaction(handler)
        if not _has_marked_handler(logger.handlers):
            logger.addHandler(_make_default_handler())


__all__ = [
    "RedactingFilter",
    "RedactingFormatter",
    "SECRET_FAST_PATH_TOKENS",
    "apply_redaction",
    "configure_logging",
    "correlation_id",
    "get_request_id",
    "install_redaction",
    "reset_request_id",
    "scrub_secrets",
    "set_request_id",
]
