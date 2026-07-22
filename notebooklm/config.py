"""Public runtime configuration surface matching notebooklm-py 0.7.2."""

from __future__ import annotations

import os
from urllib.parse import urlparse

DEFAULT_BASE_URL = "https://notebooklm.google.com"
PERSONAL_BASE_HOST = "notebooklm.google.com"
ENTERPRISE_BASE_HOST = "notebooklm.cloud.google.com"

_ALLOWED_BASE_HOSTS = frozenset({PERSONAL_BASE_HOST, ENTERPRISE_BASE_HOST})


def get_base_url() -> str:
    raw = os.environ.get("NOTEBOOKLM_BASE_URL", DEFAULT_BASE_URL).strip().rstrip("/")
    if not raw:
        raw = DEFAULT_BASE_URL
    parsed = urlparse(raw)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("NOTEBOOKLM_BASE_URL has an invalid port") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _ALLOWED_BASE_HOSTS
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path.rstrip("/")
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        allowed = ", ".join(sorted(_ALLOWED_BASE_HOSTS))
        raise ValueError(f"NOTEBOOKLM_BASE_URL must use https and one of: {allowed}")
    return f"https://{parsed.hostname}"


def get_base_host() -> str:
    return urlparse(get_base_url()).hostname or PERSONAL_BASE_HOST


def get_default_language() -> str:
    return os.environ.get("NOTEBOOKLM_HL", "").strip() or "en"


__all__ = [
    'DEFAULT_BASE_URL',
    'ENTERPRISE_BASE_HOST',
    'get_base_host',
    'get_base_url',
    'get_default_language',
    'PERSONAL_BASE_HOST',
]
