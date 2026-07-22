"""Public URL helpers."""

from __future__ import annotations

import re
from urllib.parse import urlparse


def is_youtube_url(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except (AttributeError, TypeError, ValueError):
        return False
    return host == "youtube.com" or host.endswith(".youtube.com") or host == "youtu.be"


def is_google_auth_redirect(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except (AttributeError, TypeError, ValueError):
        return False
    return host == "accounts.google.com" or host.endswith(".accounts.google.com")


def contains_google_auth_redirect(text: str) -> bool:
    return any(
        is_google_auth_redirect(url)
        for url in re.findall(r'https?://[^\s"\'<>]+', text)
    )


__all__ = [
    "contains_google_auth_redirect",
    "is_google_auth_redirect",
    "is_youtube_url",
]
