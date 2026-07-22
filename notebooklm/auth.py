"""Phase 2A offline auth diagnostics: check, inspect, logout, doctor.

This module implements the fixture-backed foundation for NotebookLM Bare's auth
commands. Its diagnostic and cleanup functions operate purely on local files:

  * :func:`check_storage` validates a Playwright ``storage_state.json`` the way
    ``notebooklm auth check`` does *without* the ``--test`` network probe — it
    confirms the file exists, parses, carries the minimum required cookies, and
    that cookie domains are within the allowed Google set.
  * :func:`inspect_storage` reports account-like metadata and counts from a
    fixture storage file *without ever emitting an email address or any value*.
  * :func:`logout` removes the local auth artifacts (and cached browser-profile
    directory) for one selected profile only.
  * :func:`doctor` runs deterministic local profile/auth/layout checks.

The account-enumeration and token helpers perform explicitly requested network
I/O against caller-provided cookies and may return account emails. This module
does not read browser stores or touch OS keychains, and its redacted diagnostics
never emit cookie values or tokens.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from http.cookiejar import Cookie, CookieJar
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any, Callable, Mapping, NamedTuple, Sequence, TypeAlias
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import Request

from . import cookies as _cookies
from . import http_std as _http_std
from . import io as _io
from . import paths as _paths
from . import profiles as _profiles
from .config import get_base_url
from .errors import (
    AuthenticationError,
    NetworkError,
    NotebookLMError,
    RateLimitError,
    RedirectError,
    ValidationError,
)


class httpx:  # noqa: N801 - annotation-only shim; runtime stays stdlib-only.
    class Cookies(dict):
        pass


_LOG = logging.getLogger(__name__)
_REFRESH_STATE_LOCK = threading.Lock()
_REFRESH_GENERATIONS: dict[str, int] = {}
_REFRESH_LOCKS: dict[str, threading.Lock] = {}
_ACCOUNT_DISCOVERY_POKE_LOCK = threading.Lock()
_ACCOUNT_DISCOVERY_LAST_POKE_ATTEMPT_MONOTONIC = 0.0

__all__ = [
    'Account',
    'advance_cookie_snapshot_after_save',
    'ALLOWED_COOKIE_DOMAINS',
    'AuthTokens',
    'authuser_query',
    'build_cookie_jar',
    'build_httpx_cookies_from_storage',
    'clear_account_metadata',
    'convert_rookiepy_cookies_to_storage_state',
    'cookie_names_from_storage',
    'CookieSaveResult',
    'CookieSnapshot',
    'CookieSnapshotKey',
    'CookieSnapshotValue',
    'enumerate_accounts',
    'extract_cookies_from_storage',
    'extract_cookies_with_domains',
    'extract_csrf_from_html',
    'extract_email_from_html',
    'extract_session_id_from_html',
    'extract_wiz_field',
    'fetch_tokens',
    'fetch_tokens_with_domains',
    'format_authuser_value',
    'get_account_email_for_storage',
    'get_authuser_for_storage',
    'GOOGLE_REGIONAL_CCTLDS',
    'KEEPALIVE_ROTATE_URL',
    'load_auth_from_storage',
    'load_httpx_cookies',
    'MINIMUM_REQUIRED_COOKIES',
    'missing_cookies_hint',
    'normalize_cookie_map',
    'NOTEBOOKLM_DISABLE_KEEPALIVE_POKE_ENV',
    'NOTEBOOKLM_REFRESH_CMD_ENV',
    'NOTEBOOKLM_REFRESH_CMD_USE_SHELL_ENV',
    'OPTIONAL_COOKIE_DOMAINS',
    'OPTIONAL_COOKIE_DOMAINS_BY_LABEL',
    'read_account_metadata',
    'recover_psidts_in_memory',
    'REQUIRED_COOKIE_DOMAINS',
    'save_cookies_to_storage',
    'snapshot_cookie_jar',
    'validate_with_recovery',
    'write_account_metadata',
]

ROTATE_COOKIES_URL = "https://accounts.google.com/RotateCookies"
KEEPALIVE_ROTATE_URL = ROTATE_COOKIES_URL
NOTEBOOKLM_HOME_URL = "https://notebooklm.google.com/"
_ROTATE_BODY = '[000,"-0000000000000000000"]'
_KEEPALIVE_POKE_TIMEOUT = 15.0
_DEFAULT_NETWORK_TIMEOUT = 30.0
_AUTH_MAX_REDIRECTS = 20
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
NOTEBOOKLM_REFRESH_CMD_ENV = "NOTEBOOKLM_REFRESH_CMD"
NOTEBOOKLM_REFRESH_CMD_USE_SHELL_ENV = "NOTEBOOKLM_REFRESH_CMD_USE_SHELL"
_REFRESH_ATTEMPTED_ENV = "_NOTEBOOKLM_REFRESH_ATTEMPTED"

# Keepalive throttle — matches upstream notebooklm-py 0.7.2 _auth/keepalive.py.
# Skip the RotateCookies POST if storage_state.json was rewritten within this
# window. 60 s is well under Google's declared 600 s rotation cadence.
_KEEPALIVE_RATE_LIMIT_SECONDS = 60.0
_KEEPALIVE_PRECISION_TOLERANCE = 2.0
NOTEBOOKLM_DISABLE_KEEPALIVE_POKE_ENV = "NOTEBOOKLM_DISABLE_KEEPALIVE_POKE"

REQUIRED_COOKIE_DOMAINS = frozenset(
    {
        ".google.com",
        "google.com",
        ".notebooklm.google.com",
        "notebooklm.google.com",
        ".notebooklm.cloud.google.com",
        "notebooklm.cloud.google.com",
        ".googleusercontent.com",
        "accounts.google.com",
        ".accounts.google.com",
        "drive.google.com",
        ".drive.google.com",
    }
)
OPTIONAL_COOKIE_DOMAINS_BY_LABEL = {
    "youtube": frozenset(
        {
            ".youtube.com",
            "youtube.com",
            "accounts.youtube.com",
            ".accounts.youtube.com",
        }
    ),
    "docs": frozenset({"docs.google.com", ".docs.google.com"}),
    "myaccount": frozenset({"myaccount.google.com", ".myaccount.google.com"}),
    "mail": frozenset({"mail.google.com", ".mail.google.com"}),
}
OPTIONAL_COOKIE_DOMAINS = frozenset().union(*OPTIONAL_COOKIE_DOMAINS_BY_LABEL.values())
ALLOWED_COOKIE_DOMAINS = REQUIRED_COOKIE_DOMAINS | OPTIONAL_COOKIE_DOMAINS
GOOGLE_REGIONAL_CCTLDS = frozenset(
    {
        "com.sg",
        "com.au",
        "com.br",
        "com.mx",
        "com.ar",
        "com.hk",
        "com.tw",
        "com.my",
        "com.ph",
        "com.vn",
        "com.pk",
        "com.bd",
        "com.ng",
        "com.eg",
        "com.tr",
        "com.ua",
        "com.co",
        "com.pe",
        "com.sa",
        "com.ae",
        "co.uk",
        "co.jp",
        "co.in",
        "co.kr",
        "co.za",
        "co.nz",
        "co.id",
        "co.th",
        "co.il",
        "co.ve",
        "co.cr",
        "co.ke",
        "co.ug",
        "co.tz",
        "co.ma",
        "co.ao",
        "co.mz",
        "co.zw",
        "co.bw",
        "cn",
        "de",
        "fr",
        "it",
        "es",
        "nl",
        "pl",
        "ru",
        "ca",
        "be",
        "at",
        "ch",
        "se",
        "no",
        "dk",
        "fi",
        "pt",
        "gr",
        "cz",
        "ro",
        "hu",
        "ie",
        "sk",
        "bg",
        "hr",
        "si",
        "lt",
        "lv",
        "ee",
        "lu",
        "cl",
        "cat",
    }
)
MINIMUM_REQUIRED_COOKIES = _cookies.MINIMUM_REQUIRED_COOKIES


def _is_recently_rotated(storage_path: Path) -> bool:
    """Return True if *storage_path* was modified within the rate-limit window.

    Matches upstream ``_auth/keepalive._is_recently_rotated``: a future mtime
    (clock skew) is treated as not-recent, and a small negative tolerance
    absorbs sub-second drift between ``time.time()`` and filesystem mtime.
    """
    try:
        mtime = storage_path.stat().st_mtime
    except OSError:
        return False
    age = time.time() - mtime
    return -_KEEPALIVE_PRECISION_TOLERANCE <= age <= _KEEPALIVE_RATE_LIMIT_SECONDS


def _storage_state_lock_path(storage_path: Path) -> Path:
    return storage_path.with_name(f".{storage_path.name}.lock")


@contextlib.contextmanager
def _storage_state_file_lock(storage_path: Path):
    with _io._file_lock(_storage_state_lock_path(storage_path), 10.0):
        yield


def _rotation_lock_path(storage_path: Path) -> Path:
    return storage_path.with_name(f".{storage_path.name}.rotate.lock")


@contextlib.contextmanager
def _rotation_file_lock(storage_path: Path):
    try:
        with _io._file_lock(_rotation_lock_path(storage_path), 0.0):
            yield True
    except TimeoutError:
        yield False


ResponseGetter = Callable[..., _http_std.Response]
ResponsePoster = Callable[..., _http_std.Response]
_default_get: ResponseGetter = _http_std.get
_default_post: ResponsePoster = _http_std.post


CookieKey: TypeAlias = tuple[str, str, str]
DomainCookieMap: TypeAlias = dict[CookieKey, str]


class CookieSnapshotKey(NamedTuple):
    name: str
    domain: str
    path: str


class CookieSnapshotValue(NamedTuple):
    value: str
    expires: int | None
    secure: bool
    http_only: bool


CookieSnapshot = dict[CookieSnapshotKey, CookieSnapshotValue]


@dataclass(frozen=True)
class Account:
    """Discovered account metadata without credential values."""

    authuser: int
    email: str
    is_default: bool
    browser_profile: str | None = None


@dataclass(frozen=True)
class CookieSaveResult:
    """Result of persisting a cookie snapshot."""

    ok: bool
    cas_rejected_keys: frozenset[CookieSnapshotKey] = frozenset()


class _TokenFetchDetails(NamedTuple):
    summary: dict[str, Any]
    csrf_token: str
    session_id: str
    storage_state: dict[str, Any]
    cookie_snapshot: CookieSnapshot | None


@dataclass(repr=False)
class AuthTokens:
    """Stdlib stand-in for the upstream auth-token bundle.

    Cookie/token values are credential-equivalent and must not be printed.
    """

    cookies: DomainCookieMap
    csrf_token: str
    session_id: str
    storage_path: Path | None = None
    cookie_jar: httpx.Cookies | None = None
    authuser: int = 0
    cookie_snapshot: CookieSnapshot | None = None
    account_email: str | None = None

    def __post_init__(self) -> None:
        self.cookies = normalize_cookie_map(self.cookies)
        if self.cookie_jar is None:
            self.cookie_jar = build_cookie_jar(
                cookies=self.cookies,
                storage_path=self.storage_path,
            )

    def __repr__(self) -> str:
        """Return the upstream-shaped redacted representation."""

        jar_state = "<redacted>" if self.cookie_jar is not None else "None"
        snapshot_state = "<redacted>" if self.cookie_snapshot is not None else "None"
        return (
            "AuthTokens("
            f"cookies=<{len(self.cookies)} redacted>, "
            "csrf_token=<redacted>, "
            "session_id=<redacted>, "
            f"storage_path={self.storage_path!r}, "
            f"cookie_jar={jar_state}, "
            f"authuser={self.authuser!r}, "
            f"cookie_snapshot={snapshot_state}, "
            f"account_email={self.account_email!r}"
            ")"
        )

    @property
    def cookie_header(self) -> str:
        return "; ".join(f"{name}={value}" for name, value in self.flat_cookies.items())

    @property
    def account_route(self) -> str:
        return format_authuser_value(self.authuser, self.account_email)

    @property
    def flat_cookies(self) -> dict[str, str]:
        return flatten_cookie_map(self.cookies)

    @classmethod
    async def from_storage(
        cls, path: Path | None = None, profile: str | None = None
    ) -> "AuthTokens":
        return await _auth_tokens_from_storage(cls, path=path, profile=profile)


# --------------------------------------------------------------------------- #
# auth check
# --------------------------------------------------------------------------- #


def _allowed_domain(domain: str) -> bool:
    return _cookies.is_allowed_google_domain(domain)


def _is_google_domain(domain: str) -> bool:
    if domain == ".google.com":
        return True
    if domain.startswith(".google."):
        return domain[8:] in GOOGLE_REGIONAL_CCTLDS
    return False


def _auth_domain_priority(domain: str) -> int:
    if domain == ".google.com":
        return 4
    if domain in {".notebooklm.google.com", ".notebooklm.cloud.google.com"}:
        return 3
    if domain in {"notebooklm.google.com", "notebooklm.cloud.google.com"}:
        return 2
    if _is_google_domain(domain):
        return 1
    return 0


def _has_valid_secondary_binding(cookie_names: set[str]) -> bool:
    return "OSID" in cookie_names or {"APISID", "SAPISID"} <= cookie_names


def cookie_names_from_storage(storage_state: Mapping[str, Any]) -> set[str]:
    raw = storage_state.get("cookies", [])
    return {
        name
        for entry in raw
        if isinstance(entry, Mapping)
        and isinstance(name := entry.get("name"), str)
        and name
    }


def missing_cookies_hint(
    cookie_names: set[str], *, browser_label: str | None = None
) -> str:
    browser = browser_label or "your browser"
    if "SID" not in cookie_names:
        return (
            f"You are not signed in to Google in {browser}.\n"
            f"Sign in to a Google account (Gmail, Drive, NotebookLM, ...) in {browser} and re-run this command."
        )
    psidts_missing = "__Secure-1PSIDTS" not in cookie_names
    has_secondary = _has_valid_secondary_binding(cookie_names)
    if psidts_missing and not has_secondary:
        return (
            f"Your {browser} session is signed in to Google but is missing the cookies NotebookLM needs "
            f"(OSID or APISID+SAPISID, plus __Secure-1PSIDTS).\n"
            f"Open https://notebooklm.google.com in {browser} (sign in if prompted), reload the page, then re-run this command."
        )
    if psidts_missing:
        return (
            "__Secure-1PSIDTS is missing and the automatic RotateCookies recovery did not succeed.\n"
            f"Open https://notebooklm.google.com in {browser} (this triggers Google to refresh the cookie), then re-run this command."
        )
    if not has_secondary:
        return (
            f"Your {browser} cookies are missing the NotebookLM binding (OSID, or APISID+SAPISID).\n"
            f"Open https://notebooklm.google.com in {browser} (sign in if prompted), reload the page, then re-run this command."
        )
    return "This typically means --browser-cookies extraction was incomplete. Run 'notebooklm login' to re-authenticate."


def _validate_required_cookie_names(cookie_names: set[str]) -> None:
    missing = MINIMUM_REQUIRED_COOKIES - cookie_names
    if missing:
        raise ValueError("Missing required cookies: " + ", ".join(sorted(missing)))


def normalize_cookie_map(cookies: Mapping[Any, str] | None) -> dict[tuple[str, str, str], str]:
    normalized: dict[tuple[str, str, str], str] = {}
    for key, value in (cookies or {}).items():
        if isinstance(key, tuple):
            if len(key) == 3:
                name, domain, path = key
            elif len(key) == 2:
                name, domain = key
                path = "/"
            else:
                continue
        else:
            name, domain, path = key, ".google.com", "/"
        if name:
            normalized[(str(name), str(domain or ".google.com"), str(path or "/"))] = str(value)
    return normalized


def flatten_cookie_map(cookies: Mapping[Any, str] | None) -> dict[str, str]:
    flat: dict[str, str] = {}
    priorities: dict[str, int] = {}
    for (name, domain, _path), value in normalize_cookie_map(cookies).items():
        priority = _auth_domain_priority(domain)
        if name not in flat or priority > priorities[name]:
            flat[name] = value
            priorities[name] = priority
    return flat


def convert_rookiepy_cookies_to_storage_state(
    rookiepy_cookies: list[dict[str, Any]],
) -> dict[str, Any]:
    converted = []
    for cookie in rookiepy_cookies:
        domain = cookie.get("domain", "")
        name = cookie.get("name", "")
        value = cookie.get("value", "")
        if not name or not value or not domain or not _allowed_domain(domain):
            continue
        expires = cookie.get("expires")
        converted.append(
            {
                "name": name,
                "value": value,
                "domain": domain,
                "path": cookie.get("path", "/"),
                "expires": expires if expires is not None else -1,
                "httpOnly": cookie.get("http_only", False),
                "secure": cookie.get("secure", False),
                "sameSite": "None",
            }
        )
    return {"cookies": converted, "origins": []}


def extract_cookies_from_storage(storage_state: dict[str, Any]) -> dict[str, str]:
    cookies: dict[str, str] = {}
    priorities: dict[str, int] = {}
    for cookie in storage_state.get("cookies", []):
        if not isinstance(cookie, Mapping):
            continue
        name = cookie.get("name")
        domain = str(cookie.get("domain", ""))
        if not isinstance(name, str) or not name or not _allowed_domain(domain):
            continue
        priority = _auth_domain_priority(domain)
        if name not in cookies or priority > priorities[name]:
            cookies[name] = str(cookie.get("value", ""))
            priorities[name] = priority
    _validate_required_cookie_names(set(cookies))
    return cookies


def extract_cookies_with_domains(storage_state: dict[str, Any]) -> dict[tuple[str, str, str], str]:
    out: dict[tuple[str, str, str], str] = {}
    for cookie in storage_state.get("cookies", []):
        if not isinstance(cookie, Mapping):
            continue
        name = cookie.get("name")
        value = cookie.get("value", "")
        domain = str(cookie.get("domain", ""))
        if isinstance(name, str) and name and value and _allowed_domain(domain):
            out.setdefault((name, domain, str(cookie.get("path") or "/")), str(value))
    _validate_required_cookie_names({name for name, _, _ in out})
    return out


def _load_storage_state(path: Path | str | None = None) -> dict[str, Any]:
    if path:
        return _cookies.load_storage_state(path)
    if "NOTEBOOKLM_AUTH_JSON" in os.environ:
        raw = os.environ["NOTEBOOKLM_AUTH_JSON"].strip()
        if not raw:
            raise ValueError(
                "NOTEBOOKLM_AUTH_JSON environment variable is set but empty.\n"
                "Provide valid Playwright storage state JSON or unset the variable."
            )
        try:
            state = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON in NOTEBOOKLM_AUTH_JSON environment variable: {exc}"
            ) from exc
        if not isinstance(state, dict) or "cookies" not in state:
            raise ValueError(
                "NOTEBOOKLM_AUTH_JSON must contain valid Playwright storage state with a 'cookies' key."
            )
        return state
    return _cookies.load_storage_state(_paths.get_storage_path())


def _storage_entry_to_cookie(entry: Mapping[str, Any]) -> Cookie:
    domain = str(entry.get("domain", "") or "")
    expires = entry.get("expires")
    return Cookie(
        version=0,
        name=str(entry.get("name", "") or ""),
        value=str(entry.get("value", "") or ""),
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=bool(domain),
        domain_initial_dot=domain.startswith("."),
        path=str(entry.get("path") or "/"),
        path_specified=True,
        secure=bool(entry.get("secure", False)),
        expires=None if expires in (None, -1) else int(expires),
        discard=expires in (None, -1),
        comment=None,
        comment_url=None,
        rest={"HttpOnly": ""} if entry.get("httpOnly") or entry.get("http_only") else {},
    )


def _cookie_is_http_only(cookie: Cookie) -> bool:
    return bool(
        cookie.has_nonstandard_attr("HttpOnly")
        or cookie.has_nonstandard_attr("httponly")
    )


def _cookie_to_storage_state(cookie: Cookie) -> dict[str, Any]:
    return {
        "name": cookie.name,
        "value": cookie.value,
        "domain": cookie.domain,
        "path": cookie.path or "/",
        "expires": cookie.expires if cookie.expires is not None else -1,
        "httpOnly": _cookie_is_http_only(cookie),
        "secure": bool(cookie.secure),
        "sameSite": "None",
    }


def _cookie_jar_from_storage_state(state: Mapping[str, Any]) -> CookieJar:
    jar = CookieJar()
    for entry in state.get("cookies", []):
        if (
            isinstance(entry, Mapping)
            and entry.get("name")
            and entry.get("value")
            and _allowed_domain(str(entry.get("domain", "")))
        ):
            jar.set_cookie(_storage_entry_to_cookie(entry))
    return jar


def build_httpx_cookies_from_storage(path: Path | str | None = None) -> CookieJar:
    state = _load_storage_state(path)
    if _psidts_needs_recovery(state) and _recover_psidts_inline(path):
        state = _load_storage_state(path)
    try:
        extract_cookies_from_storage(state)
    except ValueError:
        if not _recover_psidts_inline(path):
            raise
        state = _load_storage_state(path)
        extract_cookies_from_storage(state)
    return _cookie_jar_from_storage_state(state)


load_httpx_cookies = build_httpx_cookies_from_storage


def build_cookie_jar(
    cookies: Mapping[Any, str] | None = None, storage_path: Path | str | None = None
) -> CookieJar:
    if storage_path and Path(storage_path).is_file():
        return build_httpx_cookies_from_storage(storage_path)
    jar = CookieJar()
    for (name, domain, path), value in normalize_cookie_map(cookies).items():
        jar.set_cookie(
            _storage_entry_to_cookie(
                {
                    "name": name,
                    "value": value,
                    "domain": domain,
                    "path": path,
                    "secure": True,
                    "httpOnly": False,
                    "expires": -1,
                }
            )
        )
    return jar


def check_storage(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Validate a ``storage_state.json`` offline. Never raises; never emits values."""

    p = Path(path)
    result: dict[str, Any] = {
        "storage_path": str(p),
        "exists": p.exists(),
        "readable": False,
        "valid_json": False,
        "cookie_count": 0,
        "cookie_names": [],
        "required_cookies": {
            name: False for name in sorted(_cookies.MINIMUM_REQUIRED_COOKIES)
        },
        "missing_cookies": sorted(_cookies.MINIMUM_REQUIRED_COOKIES),
        "has_required_cookies": False,
        "domains_ok": False,
        "unexpected_domains": [],
        "ok": False,
    }
    if not result["exists"]:
        return result
    try:
        text = p.read_text(encoding="utf-8")
        result["readable"] = True
    except OSError:
        return result
    try:
        state = _cookies.load_storage_state(p) if text is not None else None
    except ValidationError:
        return result
    result["valid_json"] = True

    cookies = _cookies.cookies_from_storage_state(state)
    names = sorted(c["name"] for c in cookies)
    result["cookie_count"] = len(cookies)
    result["cookie_names"] = names

    present = set(names)
    required = {
        name: (name in present) for name in sorted(_cookies.MINIMUM_REQUIRED_COOKIES)
    }
    missing = sorted(name for name, ok in required.items() if not ok)
    result["required_cookies"] = required
    result["missing_cookies"] = missing
    result["has_required_cookies"] = not missing

    unexpected = sorted(
        {
            c["domain"]
            for c in cookies
            if c["domain"] and not _allowed_domain(c["domain"])
        }
    )
    result["unexpected_domains"] = unexpected
    result["domains_ok"] = not unexpected

    result["ok"] = bool(
        result["valid_json"] and result["has_required_cookies"] and result["domains_ok"]
    )
    return result


def _psidts_needs_recovery(
    state: Mapping[str, Any], *, now: float | None = None
) -> bool:
    names = cookie_names_from_storage(state)
    if "SID" not in names or not _has_valid_secondary_binding(names):
        return False
    psidts_entries = [
        entry
        for entry in state.get("cookies", [])
        if isinstance(entry, Mapping)
        and entry.get("name") == "__Secure-1PSIDTS"
        and entry.get("value")
        and _allowed_domain(str(entry.get("domain", "")))
    ]
    if not psidts_entries:
        return True
    expires = psidts_entries[0].get("expires")
    if expires in (None, -1):
        return False
    if isinstance(expires, (int, float)) and not isinstance(expires, bool):
        return expires < (time.time() if now is None else now)
    return False


def _resolve_psidts_recovery_path(path: Path | str | None) -> Path | None:
    if path is not None:
        return Path(path)
    if os.environ.get("NOTEBOOKLM_AUTH_JSON"):
        return None
    return _paths.get_storage_path()


def _state_has_fresh_psidts(
    state: Mapping[str, Any], *, now: float | None = None
) -> bool:
    best: Mapping[str, Any] | None = None
    best_priority = -1
    for entry in state.get("cookies", []):
        if (
            not isinstance(entry, Mapping)
            or entry.get("name") != "__Secure-1PSIDTS"
            or not entry.get("value")
        ):
            continue
        domain = str(entry.get("domain", "") or "")
        if not _allowed_domain(domain):
            continue
        priority = _auth_domain_priority(domain)
        if priority > best_priority:
            best = entry
            best_priority = priority

    if best is None:
        return False
    expires = best.get("expires")
    if expires in (None, -1):
        return True
    if isinstance(expires, (int, float)) and not isinstance(expires, bool):
        return expires >= (time.time() if now is None else now)
    return True


def _is_psidts_persisted(storage: Path) -> bool:
    try:
        return _state_has_fresh_psidts(_cookies.load_storage_state(storage))
    except ValidationError:
        return False


def _recover_psidts_inline(path: Path | str | None) -> bool:
    storage = _resolve_psidts_recovery_path(path)
    if storage is None:
        return False
    try:
        state = _cookies.load_storage_state(storage)
    except ValidationError:
        return False
    if not _psidts_needs_recovery(state):
        return False

    with _rotation_file_lock(storage) as acquired:
        if not acquired:
            return _is_psidts_persisted(storage)
        try:
            state = _cookies.load_storage_state(storage)
        except ValidationError:
            return False
        if _state_has_fresh_psidts(state):
            return True
        if not _psidts_needs_recovery(state):
            return False
        return _attempt_psidts_rotation(storage, state)


def _attempt_psidts_rotation(storage: Path, state: Mapping[str, Any]) -> bool:
    request_state = {"cookies": list(state.get("cookies", [])), "origins": state.get("origins", [])}
    original_jar = _cookie_jar_from_storage_state(request_state)
    snapshot = snapshot_cookie_jar(original_jar)
    try:
        rotate = _default_post(
            ROTATE_COOKIES_URL,
            body=_ROTATE_BODY,
            headers={
                "Cookie": _cookie_header_from_state(request_state, ROTATE_COOKIES_URL),
                "Content-Type": "application/json",
                "Origin": "https://accounts.google.com",
            },
            timeout=_KEEPALIVE_POKE_TIMEOUT,
            max_redirects=_AUTH_MAX_REDIRECTS,
        )
        _raise_for_bad_response(rotate, "RotateCookies")
    except NotebookLMError:
        return False
    _merge_cookie_updates(
        request_state,
        _cookies_from_set_cookie(rotate.headers.get("set-cookie"), response_url=rotate.url),
    )
    if not _state_has_fresh_psidts(request_state):
        return _is_psidts_persisted(storage)
    rotated_jar = _cookie_jar_from_storage_state(request_state)
    save_cookies_to_storage(
        rotated_jar, storage, original_snapshot=snapshot, return_result=True
    )
    return _is_psidts_persisted(storage)


def load_auth_from_storage(path: Path | str | None = None) -> dict[str, str]:
    state = _load_storage_state(path)
    if _psidts_needs_recovery(state) and _recover_psidts_inline(path):
        state = _load_storage_state(path)
    try:
        return extract_cookies_from_storage(state)
    except ValueError:
        if not _recover_psidts_inline(path):
            raise
        return extract_cookies_from_storage(_load_storage_state(path))


def recover_psidts_in_memory(rookiepy_cookies: list[dict[str, Any]]) -> bool:
    """Recover ``__Secure-1PSIDTS`` before browser-cookie persistence.

    This is the stdlib equivalent of upstream's in-memory recovery path. It
    fires only when the same offline preconditions hold: ``SID`` present,
    PSIDTS missing, and a secondary binding (``OSID`` or ``APISID+SAPISID``)
    present.
    """

    state = convert_rookiepy_cookies_to_storage_state(rookiepy_cookies)
    if not _psidts_needs_recovery(state):
        return False
    try:
        rotate = _default_post(
            ROTATE_COOKIES_URL,
            body=_ROTATE_BODY,
            headers={
                "Cookie": _cookie_header_from_state(state, ROTATE_COOKIES_URL),
                "Content-Type": "application/json",
                "Origin": "https://accounts.google.com",
            },
            timeout=_KEEPALIVE_POKE_TIMEOUT,
            max_redirects=_AUTH_MAX_REDIRECTS,
        )
        _raise_for_bad_response(rotate, "RotateCookies")
    except NotebookLMError:
        return False
    updates = _cookies_from_set_cookie(
        rotate.headers.get("set-cookie"), response_url=rotate.url
    )
    if not any(cookie.get("name") == "__Secure-1PSIDTS" for cookie in updates):
        return False
    for cookie in updates:
        if cookie.get("name") not in {"__Secure-1PSIDTS", "__Secure-3PSIDTS"}:
            continue
        rookiepy_cookies.append(
            {
                "name": cookie["name"],
                "value": cookie.get("value", ""),
                "domain": cookie.get("domain", ".google.com"),
                "path": cookie.get("path", "/"),
                "expires": cookie.get("expires"),
                "secure": True,
                "http_only": True,
            }
        )
    return True


def validate_with_recovery(
    rookiepy_cookies: list[dict[str, Any]],
) -> tuple[dict[str, Any], ValueError | None]:
    """Convert rookiepy cookies, retrying once through PSIDTS recovery."""

    storage_state = convert_rookiepy_cookies_to_storage_state(rookiepy_cookies)
    try:
        extract_cookies_from_storage(storage_state)
        return storage_state, None
    except ValueError as initial:
        if not recover_psidts_in_memory(rookiepy_cookies):
            return storage_state, initial
        storage_state = convert_rookiepy_cookies_to_storage_state(rookiepy_cookies)
        try:
            extract_cookies_from_storage(storage_state)
            return storage_state, None
        except ValueError as final:
            return storage_state, final


def snapshot_cookie_jar(cookie_jar: CookieJar) -> CookieSnapshot:
    return {
        CookieSnapshotKey(cookie.name, cookie.domain, cookie.path or "/"): CookieSnapshotValue(
            cookie.value,
            cookie.expires,
            bool(cookie.secure),
            _cookie_is_http_only(cookie),
        )
        for cookie in cookie_jar
        if cookie.name and cookie.domain and cookie.value is not None
    }


def _cookie_snapshot_key_variants(key: CookieSnapshotKey) -> set[CookieSnapshotKey]:
    if key.domain.startswith("."):
        return {key, CookieSnapshotKey(key.name, key.domain[1:], key.path)}
    return {key, CookieSnapshotKey(key.name, f".{key.domain}", key.path)}


def _stored_cookie_snapshot_key(cookie: Mapping[str, Any]) -> CookieSnapshotKey | None:
    name = cookie.get("name")
    domain = cookie.get("domain", "")
    if not name or not domain:
        return None
    return CookieSnapshotKey(str(name), str(domain), str(cookie.get("path") or "/"))


def save_cookies_to_storage(
    cookie_jar: CookieJar,
    path: Path | str | None = None,
    *,
    original_snapshot: CookieSnapshot | None = None,
    return_result: bool = False,
) -> bool | CookieSaveResult:
    if path is None:
        result = CookieSaveResult(True)
        return result if return_result else result.ok
    storage = Path(path)
    with _storage_state_file_lock(storage):
        if not storage.exists():
            result = CookieSaveResult(False)
            return result if return_result else result.ok

        try:
            data = _cookies.load_storage_state(storage)
        except ValidationError:
            result = CookieSaveResult(False)
            return result if return_result else result.ok

        current = snapshot_cookie_jar(cookie_jar)
        cookies_by_snapshot_key = {
            CookieSnapshotKey(cookie.name, cookie.domain, cookie.path or "/"): cookie
            for cookie in cookie_jar
            if (
                cookie.name
                and cookie.domain
                and cookie.value is not None
                and _allowed_domain(cookie.domain)
            )
        }
        rejected: set[CookieSnapshotKey] = set()
        changed = 0
        stored = [dict(cookie) for cookie in data.get("cookies", []) if isinstance(cookie, dict)]

        if original_snapshot is None:
            deltas = set(cookies_by_snapshot_key)
            deletions: set[CookieSnapshotKey] = set()
        else:
            deltas = {
                key
                for key in cookies_by_snapshot_key
                if original_snapshot.get(key) != current.get(key)
            }
            deletions = {
                key
                for key in original_snapshot
                if key not in current and _allowed_domain(key.domain)
            }

        new_cookies: list[dict[str, Any]] = []
        matched_delta_keys: set[CookieSnapshotKey] = set()
        for entry in stored:
            stored_key = _stored_cookie_snapshot_key(entry)
            if stored_key is None:
                new_cookies.append(entry)
                continue
            matched = next(
                (v for v in _cookie_snapshot_key_variants(stored_key) if v in deltas),
                None,
            )
            if matched is not None:
                cookie = cookies_by_snapshot_key[matched]
                if original_snapshot is not None:
                    original = next(
                        (
                            original_snapshot[variant]
                            for variant in _cookie_snapshot_key_variants(matched)
                            if variant in original_snapshot
                        ),
                        None,
                    )
                    stored_value = entry.get("value")
                    if (
                        original is not None
                        and stored_value != original.value
                        and stored_value != cookie.value
                    ):
                        rejected.add(matched)
                        matched_delta_keys.add(matched)
                        new_cookies.append(entry)
                        continue
                    if original is None and stored_value != cookie.value:
                        rejected.add(matched)
                        matched_delta_keys.add(matched)
                        new_cookies.append(entry)
                        continue
                entry.update(_cookie_to_storage_state(cookie))
                changed += 1
                matched_delta_keys.add(matched)
                new_cookies.append(entry)
                continue
            deleted = next(
                (
                    v
                    for v in _cookie_snapshot_key_variants(stored_key)
                    if v in deletions
                ),
                None,
            )
            if deleted is not None:
                original = original_snapshot.get(deleted) if original_snapshot else None
                if original is not None and entry.get("value") == original.value:
                    changed += 1
                    continue
                if original is not None:
                    rejected.add(deleted)
            new_cookies.append(entry)

        for key in deltas - matched_delta_keys:
            cookie = cookies_by_snapshot_key.get(key)
            if cookie is not None:
                new_cookies.append(_cookie_to_storage_state(cookie))
                changed += 1

        if changed:
            data["cookies"] = new_cookies
            _cookies.save_storage_state(storage, data)

        result = CookieSaveResult(not rejected, frozenset(rejected))
    return result if return_result else result.ok


def advance_cookie_snapshot_after_save(
    original_snapshot: CookieSnapshot | None,
    post_save_snapshot: CookieSnapshot,
    cas_rejected_keys: frozenset[CookieSnapshotKey],
) -> CookieSnapshot | None:
    if original_snapshot is None:
        return None
    advanced = dict(post_save_snapshot)
    for key in cas_rejected_keys:
        original_key = next(
            (variant for variant in _cookie_snapshot_key_variants(key) if variant in original_snapshot),
            None,
        )
        for variant in _cookie_snapshot_key_variants(key):
            advanced.pop(variant, None)
        if original_key is not None:
            advanced[original_key] = original_snapshot[original_key]
    return advanced


# --------------------------------------------------------------------------- #
# network auth/token fetch (Phase 2G)
# --------------------------------------------------------------------------- #


def _wiz_patterns(key: str) -> list[re.Pattern[str]]:
    escaped = re.escape(key)
    return [
        re.compile(rf'"{escaped}"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"'),
        re.compile(rf"'{escaped}'\s*:\s*'([^'\\]*(?:\\.[^'\\]*)*)'"),
        re.compile(rf"&quot;{escaped}&quot;\s*:\s*&quot;((?:(?!&quot;).)*)&quot;"),
    ]


def _extract_wiz_field(html: str, key: str) -> str:
    for pattern in _wiz_patterns(key):
        match = pattern.search(html)
        if match is not None:
            return match.group(1)
    raise AuthenticationError(
        f"NotebookLM auth token field {key} was not found; page structure may have changed"
    )


def extract_wiz_field(html: str, key: str, *, strict: bool = True) -> str | None:
    try:
        return _extract_wiz_field(html, key)
    except AuthenticationError:
        if strict:
            raise
        return None


def extract_csrf_from_html(html: str, final_url: str = "") -> str:
    token = extract_wiz_field(html, "SNlM0e", strict=False)
    if token is not None:
        return token
    if _is_auth_redirect(final_url, html):
        raise ValueError("Authentication expired or invalid. Run 'notebooklm login' to re-authenticate.")
    raise ValueError(f"CSRF token not found in HTML. Final URL: {_safe_url(final_url)}")


def extract_session_id_from_html(html: str, final_url: str = "") -> str:
    token = extract_wiz_field(html, "FdrFJe", strict=False)
    if token is not None:
        return token
    if _is_auth_redirect(final_url, html):
        raise ValueError("Authentication expired or invalid. Run 'notebooklm login' to re-authenticate.")
    raise ValueError(f"Session ID not found in HTML. Final URL: {_safe_url(final_url)}")


def extract_auth_tokens_from_html(html: str) -> tuple[str, str]:
    """Extract NotebookLM CSRF/session WIZ fields from homepage HTML.

    The returned values are credential-equivalent and callers must never print
    them. This helper exists so the network probe can verify token availability
    while user-facing summaries report only booleans.
    """

    return _extract_wiz_field(html, "SNlM0e"), _extract_wiz_field(html, "FdrFJe")


_EMAIL_RE = re.compile(r'"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})"')
_NON_USER_EMAIL_LOCALS = frozenset(
    {"abuse", "feedback", "info", "mail-noreply", "googlemail-noreply", "no-reply", "noreply", "press", "privacy", "support"}
)
_NON_USER_EMAIL_DOMAINS = frozenset({"google.com", "accounts.google.com", "gmail.com"})


def extract_email_from_html(html: str) -> str | None:
    for match in _EMAIL_RE.finditer(html):
        email = match.group(1)
        local, _, domain = email.partition("@")
        if local.lower() in _NON_USER_EMAIL_LOCALS and domain.lower() in _NON_USER_EMAIL_DOMAINS:
            continue
        return email
    return None


_ACCOUNT_DISCOVERY_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
)


def _cookie_state_from_jar(cookie_jar: CookieJar) -> dict[str, Any]:
    return {
        "cookies": [_cookie_to_storage_state(cookie) for cookie in cookie_jar],
        "origins": [],
    }


def _claim_account_discovery_rotation() -> bool:
    global _ACCOUNT_DISCOVERY_LAST_POKE_ATTEMPT_MONOTONIC
    with _ACCOUNT_DISCOVERY_POKE_LOCK:
        now = time.monotonic()
        if (
            _ACCOUNT_DISCOVERY_LAST_POKE_ATTEMPT_MONOTONIC > 0
            and now - _ACCOUNT_DISCOVERY_LAST_POKE_ATTEMPT_MONOTONIC
            < _KEEPALIVE_RATE_LIMIT_SECONDS
        ):
            return False
        _ACCOUNT_DISCOVERY_LAST_POKE_ATTEMPT_MONOTONIC = now
        return True


def _poke_account_discovery_state(state: dict[str, Any]) -> None:
    if os.environ.get(NOTEBOOKLM_DISABLE_KEEPALIVE_POKE_ENV) == "1":
        return
    if not _claim_account_discovery_rotation():
        return

    def get_without_empty_cookie(url: str, **kwargs: Any) -> _http_std.Response:
        headers = dict(kwargs.pop("headers", {}))
        if not headers.get("Cookie"):
            headers.pop("Cookie", None)
        return _default_get(url, headers=headers, **kwargs)

    current_url = ROTATE_COOKIES_URL
    try:
        for redirect_count in range(_AUTH_MAX_REDIRECTS + 1):
            headers = {
                "Content-Type": "application/json",
                "Origin": "https://accounts.google.com",
            }
            cookie_header = _cookie_header_from_state(state, current_url)
            if cookie_header:
                headers["Cookie"] = cookie_header
            response = _default_post(
                current_url,
                body=_ROTATE_BODY,
                headers=headers,
                timeout=_KEEPALIVE_POKE_TIMEOUT,
                max_redirects=_AUTH_MAX_REDIRECTS,
                follow_redirects=False,
            )
            _merge_cookie_updates(
                state,
                _cookies_from_set_cookie(
                    response.headers.get("set-cookie"), response_url=response.url
                ),
            )
            if response.status not in _REDIRECT_STATUSES:
                _raise_for_bad_response(response, "RotateCookies")
                return
            if redirect_count >= _AUTH_MAX_REDIRECTS:
                raise RedirectError(
                    f"redirect limit exceeded at {_safe_url(current_url)}"
                )
            location = response.headers.get("location")
            if not location:
                raise RedirectError(
                    f"redirect response from {_safe_url(current_url)} missing Location header"
                )
            try:
                current_url = urljoin(current_url, location)
                urlsplit(current_url).port
            except ValueError:
                return
            if response.status in {301, 302, 303}:
                response, _ = _get_with_cookie_state(
                    state,
                    current_url,
                    get_without_empty_cookie,
                    timeout=_KEEPALIVE_POKE_TIMEOUT,
                    max_redirects=_AUTH_MAX_REDIRECTS - redirect_count - 1,
                )
                _raise_for_bad_response(response, "RotateCookies")
                return
    except NotebookLMError:
        return


async def enumerate_accounts(
    cookie_jar: CookieJar, *, max_authuser: int = 10
) -> list[Account]:
    """Enumerate signed-in browser accounts with bounded ``authuser`` probes."""

    state = _cookie_state_from_jar(cookie_jar)
    await asyncio.to_thread(_poke_account_discovery_state, state)

    def probe(authuser: int) -> str | None:
        def get_with_browser_headers(url: str, **kwargs: Any) -> _http_std.Response:
            headers = dict(kwargs.pop("headers", {}))
            headers.update(
                {"User-Agent": _ACCOUNT_DISCOVERY_USER_AGENT, "Accept": "text/html,*/*"}
            )
            return _default_get(url, headers=headers, **kwargs)

        response, _ = _get_with_cookie_state(
            state,
            _homepage_url(authuser=authuser, force_authuser_query=True),
            get_with_browser_headers,
            timeout=60.0,
            max_redirects=_AUTH_MAX_REDIRECTS,
        )
        if response.status != 200 or _is_auth_redirect(response.url):
            return None
        return extract_email_from_html(response.text())

    default_email = await asyncio.to_thread(probe, 0)
    if default_email is None:
        raise ValueError(
            "Authentication expired or invalid; authuser=0 did not return a "
            "signed-in account. Run 'notebooklm login' to re-authenticate."
        )
    accounts = [Account(authuser=0, email=default_email, is_default=True)]
    for authuser in range(1, max_authuser + 1):
        email = await asyncio.to_thread(probe, authuser)
        if email is None or email == default_email:
            break
        accounts.append(Account(authuser=authuser, email=email, is_default=False))
    return accounts


def _safe_url(url: str) -> str:
    if not url:
        return ""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return "<redacted-url>"
    host = parts.hostname or ""
    netloc = host
    if port is not None:
        netloc = f"{host}:{port}"
    path = parts.path or ""
    if path and path != "/":
        path = "/<redacted>"
    return f"{parts.scheme}://{netloc}{path}"


def _is_auth_redirect(url: str, body: str = "") -> bool:
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        host = ""
    if host == "accounts.google.com" or host.endswith(".accounts.google.com"):
        return True
    return any(
        _is_auth_redirect(match.group(0))
        for match in re.finditer(r'https?://[^\s"\'<>]+', body or "")
    )


def _cookie_header(cookies: Sequence[Mapping[str, Any]]) -> str:
    return "; ".join(
        f"{c['name']}={c.get('value', '')}" for c in cookies if c.get("name")
    )


def _cookie_header_from_state(state: Mapping[str, Any], url: str) -> str:
    jar = CookieJar()
    for entry in state.get("cookies", []):
        if (
            isinstance(entry, Mapping)
            and entry.get("name")
            and entry.get("value")
            and _allowed_domain(str(entry.get("domain", "")))
        ):
            jar.set_cookie(_storage_entry_to_cookie(entry))
    request = Request(url)
    jar.add_cookie_header(request)
    return request.get_header("Cookie") or ""


def _path_matches(request_path: str, cookie_path: str) -> bool:
    cpath = cookie_path or "/"
    if not cpath.startswith("/"):
        cpath = "/"
    rpath = request_path or "/"
    if cpath == "/":
        return True
    return rpath == cpath or rpath.startswith(cpath.rstrip("/") + "/")


def _cookies_for_url(
    cookies: Sequence[Mapping[str, Any]], url: str
) -> list[Mapping[str, Any]]:
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        request_path = parts.path or "/"
    except ValueError:
        return []
    if not host:
        return []
    secure_request = parts.scheme.lower() == "https"
    scoped = []
    for cookie in cookies:
        if cookie.get("secure") and not secure_request:
            continue
        if not _domain_matches(host, str(cookie.get("domain", ""))):
            continue
        if not _path_matches(request_path, str(cookie.get("path", "/"))):
            continue
        scoped.append(cookie)
    return scoped


def _get_with_cookie_state(
    state: dict[str, Any],
    url: str,
    getter: ResponseGetter,
    *,
    timeout: float,
    max_redirects: int,
) -> tuple[_http_std.Response, list[str]]:
    """GET with httpx-like cookie updates/re-scoping across redirects."""

    current_url = url
    changed: list[str] = []
    for redirect_count in range(max_redirects + 1):
        headers = {"Cookie": _cookie_header_from_state(state, current_url)}
        kwargs: dict[str, Any] = {
            "headers": headers,
            "timeout": timeout,
            "max_redirects": max_redirects,
            "follow_redirects": False,
        }
        try:
            response = getter(current_url, **kwargs)
        except TypeError as exc:
            if "follow_redirects" not in str(exc):
                raise
            kwargs.pop("follow_redirects", None)
            response = getter(current_url, **kwargs)

        changed.extend(
            _merge_cookie_updates(
                state,
                _cookies_from_set_cookie(
                    response.headers.get("set-cookie"), response_url=response.url
                ),
            )
        )
        if response.status not in _REDIRECT_STATUSES:
            return response, changed
        if redirect_count >= max_redirects:
            raise RedirectError(
                f"redirect limit exceeded at {_safe_url(current_url)}"
            )
        location = response.headers.get("location")
        if not location:
            raise RedirectError(
                f"redirect response from {_safe_url(current_url)} missing Location header"
            )
        current_url = urljoin(current_url, location)

    raise RedirectError(f"redirect limit exceeded at {_safe_url(current_url)}")


def _split_set_cookie_header(header: str | None) -> list[str]:
    if not header:
        return []
    # http_std joins duplicate Set-Cookie headers with newlines; tests may pass
    # a single header. Avoid comma-splitting because Expires contains commas.
    return [line.strip() for line in str(header).splitlines() if line.strip()]


def _cookies_from_set_cookie(
    header: str | None, *, response_url: str
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        host = urlsplit(response_url).hostname or ""
    except ValueError:
        host = ""
    for line in _split_set_cookie_header(header):
        jar = SimpleCookie()
        try:
            jar.load(line)
        except Exception:  # pragma: no cover - SimpleCookie is permissive; fail closed.
            continue
        for morsel in jar.values():
            explicit_domain = morsel["domain"]
            if explicit_domain and not _domain_matches(host, explicit_domain):
                continue
            domain = explicit_domain or host
            path = morsel["path"] or "/"
            cookie = _cookies.normalize_cookie(
                {
                    "name": morsel.key,
                    "value": morsel.value,
                    "domain": domain,
                    "path": path,
                }
            )
            if morsel["secure"]:
                cookie["secure"] = True
                cookie["_set_cookie_secure_present"] = True
            if morsel["httponly"]:
                cookie["http_only"] = True
                cookie["_set_cookie_http_only_present"] = True
            out.append(cookie)
    return out


def _domain_matches(host: str, domain: str) -> bool:
    clean_host = host.lower().lstrip(".")
    clean_domain = domain.lower().lstrip(".")
    return bool(
        clean_host
        and clean_domain
        and (clean_host == clean_domain or clean_host.endswith("." + clean_domain))
    )


def _merge_target_index(
    existing: list[dict[str, Any]],
    positions: dict[tuple[str, str, str], int],
    update: dict[str, Any],
) -> int | None:
    key = (update["name"], update.get("domain", ""), update.get("path", "/"))
    if key in positions:
        return positions[key]
    # RotateCookies may omit Domain on an auth-cookie rotation. If we already
    # hold exactly one domain-scoped Google auth cookie with the same name/path,
    # update that cookie rather than appending a duplicate header value.
    if update["name"] not in _cookies.MINIMUM_REQUIRED_COOKIES:
        return None
    update_domain = update.get("domain", "")
    if update_domain.startswith("."):
        return None
    matches = [
        i
        for i, cookie in enumerate(existing)
        if cookie.get("name") == update["name"]
        and cookie.get("path", "/") == update.get("path", "/")
        and str(cookie.get("domain", "")).startswith(".")
        and _domain_matches(update_domain, str(cookie.get("domain", "")))
    ]
    return matches[0] if len(matches) == 1 else None


def _merge_cookie(
    prior: dict[str, Any] | None, update: dict[str, Any]
) -> dict[str, Any]:
    secure_present = bool(update.pop("_set_cookie_secure_present", False))
    http_only_present = bool(update.pop("_set_cookie_http_only_present", False))
    if prior is None:
        return update
    merged = {**prior, **update}
    if not secure_present:
        merged["secure"] = prior.get("secure", False)
    if not http_only_present:
        merged["http_only"] = prior.get("http_only", False)
    if update.get("expires") is None and prior.get("value") == update.get("value"):
        merged["expires"] = prior.get("expires")
    if update.get("same_site") is None:
        merged["same_site"] = prior.get("same_site")
    return merged


def _merge_cookie_updates(
    state: dict[str, Any], updates: list[dict[str, Any]]
) -> list[str]:
    if not updates:
        return []
    existing = _cookies.cookies_from_storage_state(state)
    positions = {
        (c["name"], c.get("domain", ""), c.get("path", "/")): i
        for i, c in enumerate(existing)
    }
    changed: list[str] = []
    for raw_update in updates:
        update = dict(raw_update)
        target = _merge_target_index(existing, positions, update)
        key = (update["name"], update.get("domain", ""), update.get("path", "/"))
        if target is None:
            positions[key] = len(existing)
            existing.append(_merge_cookie(None, update))
            changed.append(update["name"])
        else:
            prior = existing[target]
            update["domain"] = prior.get("domain", update.get("domain", ""))
            update["path"] = prior.get("path", update.get("path", "/"))
            if prior.get("value") != update.get("value"):
                changed.append(update["name"])
            merged = _merge_cookie(prior, update)
            existing[target] = merged
            positions[
                (merged["name"], merged.get("domain", ""), merged.get("path", "/"))
            ] = target
    rebuilt = _cookies.build_storage_state(existing, origins=state.get("origins", []))
    state["cookies"] = rebuilt["cookies"]
    state.setdefault("origins", rebuilt["origins"])
    return sorted(dict.fromkeys(changed))


def _raise_for_bad_response(response: _http_std.Response, label: str) -> None:
    if response.status in (401, 403):
        raise AuthenticationError(
            f"{label} rejected stored authentication with HTTP {response.status}; run 'notebooklm login'"
        )
    if response.status == 429:
        raise RateLimitError(f"{label} returned HTTP 429")
    if response.status >= 400:
        raise NetworkError(f"{label} returned HTTP {response.status}")


def _windows_split_refresh_cmd(cmd: str) -> list[str]:
    import ctypes
    from ctypes import wintypes

    command_line_to_argv = ctypes.windll.shell32.CommandLineToArgvW  # type: ignore[attr-defined]
    command_line_to_argv.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    command_line_to_argv.restype = ctypes.POINTER(wintypes.LPWSTR)
    local_free = ctypes.windll.kernel32.LocalFree  # type: ignore[attr-defined]
    local_free.argtypes = [wintypes.HLOCAL]
    local_free.restype = wintypes.HLOCAL

    argc = ctypes.c_int(0)
    argv_ptr = command_line_to_argv(cmd, ctypes.byref(argc))
    if not argv_ptr:
        return []
    try:
        return [argv_ptr[i] for i in range(argc.value) if argv_ptr[i]]
    finally:
        local_free(ctypes.cast(argv_ptr, wintypes.HLOCAL))


def _split_refresh_cmd(cmd: str) -> list[str]:
    if os.name == "nt":
        return _windows_split_refresh_cmd(cmd)
    return shlex.split(cmd)


def _run_refresh_cmd(storage_path: Path | None, profile: str | None = None) -> None:
    cmd = os.environ.get(NOTEBOOKLM_REFRESH_CMD_ENV)
    if not cmd:
        raise RuntimeError(f"{NOTEBOOKLM_REFRESH_CMD_ENV} is not set; cannot refresh cookies.")
    resolved_storage_path = storage_path or _paths.get_storage_path(profile=profile)
    refresh_env = os.environ.copy()
    refresh_env.pop("NOTEBOOKLM_AUTH_JSON", None)
    refresh_env[_REFRESH_ATTEMPTED_ENV] = "1"
    refresh_env["NOTEBOOKLM_REFRESH_PROFILE"] = _paths.resolve_profile(profile)
    refresh_env["NOTEBOOKLM_REFRESH_STORAGE_PATH"] = str(resolved_storage_path)
    use_shell = os.environ.get(NOTEBOOKLM_REFRESH_CMD_USE_SHELL_ENV) == "1"
    try:
        target: str | list[str] = cmd if use_shell else _split_refresh_cmd(cmd)
    except ValueError as exc:
        raise RuntimeError(
            f"{NOTEBOOKLM_REFRESH_CMD_ENV} could not be parsed: {exc}"
        ) from exc
    if not use_shell and not target:
        raise RuntimeError(f"{NOTEBOOKLM_REFRESH_CMD_ENV} parsed to empty argv")
    try:
        result = subprocess.run(
            target,
            shell=use_shell,
            capture_output=True,
            text=True,
            timeout=60,
            env=refresh_env,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"{NOTEBOOKLM_REFRESH_CMD_ENV} failed to execute: {exc}") from exc
    if result.returncode != 0:
        executable = os.path.basename(target[0] if isinstance(target, list) else target.split()[0])
        raise RuntimeError(
            f"{NOTEBOOKLM_REFRESH_CMD_ENV} exited {result.returncode} (executable: {executable}). "
            "Run with --verbose to see captured stdout/stderr in the debug log."
        )


def _coalesced_run_refresh_cmd(storage_path: Path, profile: str | None) -> None:
    refresh_key = str(storage_path.expanduser().resolve())
    with _REFRESH_STATE_LOCK:
        refresh_generation = _REFRESH_GENERATIONS.get(refresh_key, 0)
        refresh_lock = _REFRESH_LOCKS.get(refresh_key)
        if refresh_lock is None:
            refresh_lock = threading.Lock()
            _REFRESH_LOCKS[refresh_key] = refresh_lock

    with refresh_lock:
        with _REFRESH_STATE_LOCK:
            should_run_refresh = (
                _REFRESH_GENERATIONS.get(refresh_key, 0) <= refresh_generation
            )
        if not should_run_refresh:
            return
        _run_refresh_cmd(storage_path, profile)
        with _REFRESH_STATE_LOCK:
            _REFRESH_GENERATIONS[refresh_key] = (
                _REFRESH_GENERATIONS.get(refresh_key, 0) + 1
            )


def _homepage_url(*, authuser: int = 0, account_email: str | None = None, force_authuser_query: bool = False) -> str:
    base = get_base_url() + "/"
    if account_email or authuser or force_authuser_query:
        return f"{base}?{authuser_query(authuser, account_email)}"
    return base


def _should_try_refresh(err: Exception) -> bool:
    if os.environ.get(_REFRESH_ATTEMPTED_ENV) == "1":
        return False
    if not os.environ.get(NOTEBOOKLM_REFRESH_CMD_ENV):
        return False
    msg = str(err).lower()
    return any(
        signal in msg
        for signal in (
            "authentication expired",
            "redirected to",
            "run 'notebooklm login'",
            "token field",
        )
    )


def _load_valid_storage_for_fetch(storage: Path) -> dict[str, Any]:
    local = check_storage(storage)
    if local.get("ok"):
        return _cookies.load_storage_state(storage)
    try:
        load_auth_from_storage(storage)
    except (OSError, ValueError, ValidationError) as exc:
        raise AuthenticationError(
            "stored authentication is incomplete; run 'notebooklm login'"
        ) from exc
    local = check_storage(storage)
    if not local.get("ok"):
        raise AuthenticationError(
            "stored authentication is incomplete; run 'notebooklm login'"
        )
    return _cookies.load_storage_state(storage)


def fetch_tokens_from_storage(
    path: str | os.PathLike[str],
    *,
    get: ResponseGetter | None = None,
    post: ResponsePoster | None = None,
    persist: bool = True,
    authuser: int | None = None,
    account_email: str | None = None,
    profile: str | None = None,
    _return_tokens: bool = False,
    _return_details: bool = False,
) -> dict[str, Any] | tuple[dict[str, Any], str, str] | _TokenFetchDetails:
    """Exercise stored auth and optionally persist rotated cookies.

    Matches the offline-observable upstream sequence: optional RotateCookies
    freshness poke, NotebookLM homepage token fetch, one refresh-command retry
    on auth expiry, account/authuser routing, and value-redacted summaries.
    """

    storage = Path(path)
    getter = get or _default_get
    poster = post or _default_post
    refreshed = False

    while True:
        state = _load_valid_storage_for_fetch(storage)
        original_snapshot = snapshot_cookie_jar(_cookie_jar_from_storage_state(state))
        rotated_names: list[str] = []

        disable_poke = os.environ.get(NOTEBOOKLM_DISABLE_KEEPALIVE_POKE_ENV) == "1"
        skip_rotate = disable_poke or _is_recently_rotated(storage)

        if not skip_rotate:
            headers = {
                "Cookie": _cookie_header_from_state(state, ROTATE_COOKIES_URL),
                "Content-Type": "application/json",
                "Origin": "https://accounts.google.com",
            }
            try:
                rotate = poster(
                    ROTATE_COOKIES_URL,
                    body=_ROTATE_BODY,
                    headers=headers,
                    timeout=_KEEPALIVE_POKE_TIMEOUT,
                    max_redirects=_AUTH_MAX_REDIRECTS,
                )
                _raise_for_bad_response(rotate, "RotateCookies")
            except NotebookLMError as exc:
                _LOG.debug("Keepalive RotateCookies POST failed (non-fatal): %s", exc)
            else:
                rotated_names.extend(
                    _merge_cookie_updates(
                        state,
                        _cookies_from_set_cookie(
                            rotate.headers.get("set-cookie"), response_url=rotate.url
                        ),
                    )
                )

        resolved_authuser = (
            get_authuser_for_storage(storage) if authuser is None else authuser
        )
        resolved_email = (
            get_account_email_for_storage(storage)
            if account_email is None and authuser is None
            else account_email
        )
        homepage_url = _homepage_url(
            authuser=resolved_authuser,
            account_email=resolved_email,
            force_authuser_query=authuser is not None,
        )
        try:
            homepage, homepage_rotated_names = _get_with_cookie_state(
                state,
                homepage_url,
                getter,
                timeout=_DEFAULT_NETWORK_TIMEOUT,
                max_redirects=_AUTH_MAX_REDIRECTS,
            )
            rotated_names.extend(homepage_rotated_names)
            _raise_for_bad_response(homepage, "NotebookLM homepage")
            text = homepage.text()
            if _is_auth_redirect(homepage.url):
                raise AuthenticationError(
                    "Authentication expired or invalid. Redirected to: "
                    + _safe_url(homepage.url)
                    + "\nRun 'notebooklm login' to re-authenticate."
                )
            csrf = extract_csrf_from_html(text, homepage.url)
            session_id = extract_session_id_from_html(text, homepage.url)
        except (AuthenticationError, ValueError) as exc:
            if refreshed or not _should_try_refresh(exc):
                if isinstance(exc, AuthenticationError):
                    raise
                raise AuthenticationError(str(exc)) from exc
            _coalesced_run_refresh_cmd(storage, profile)
            refreshed = True
            continue

        cookie_snapshot: CookieSnapshot | None = None
        if persist:
            save_jar = _cookie_jar_from_storage_state(state)
            post_save_snapshot = snapshot_cookie_jar(save_jar)
            save_result = save_cookies_to_storage(
                save_jar,
                storage,
                original_snapshot=original_snapshot,
                return_result=True,
            )
            if isinstance(save_result, CookieSaveResult):
                if save_result.ok:
                    cookie_snapshot = None
                elif save_result.cas_rejected_keys:
                    cookie_snapshot = advance_cookie_snapshot_after_save(
                        original_snapshot,
                        post_save_snapshot,
                        save_result.cas_rejected_keys,
                    )
                else:
                    cookie_snapshot = original_snapshot
            else:
                cookie_snapshot = None if save_result else original_snapshot
        final_names = sorted(
            {c["name"] for c in _cookies.cookies_from_storage_state(state)}
        )
        summary = {
            "ok": True,
            "network_test": True,
            "token_fetch_ok": True,
            "csrf_token_present": bool(csrf),
            "session_id_present": bool(session_id),
            "rotated_cookie_names": sorted(dict.fromkeys(rotated_names)),
            "cookie_count": len(final_names),
            "cookie_names": final_names,
        }
        if _return_details:
            return _TokenFetchDetails(summary, csrf, session_id, state, cookie_snapshot)
        if _return_tokens:
            return summary, csrf, session_id
        return summary


async def _auth_tokens_from_storage(
    cls: type[Any],
    *,
    path: str | os.PathLike[str] | None = None,
    profile: str | None = None,
) -> Any:
    """Build an AuthTokens-shaped object from storage using stdlib auth fetch."""

    resolved_path = Path(path) if path is not None else None
    temp_path: Path | None = None
    if resolved_path is None:
        if "NOTEBOOKLM_AUTH_JSON" in os.environ:
            temp_path = _temporary_storage_state(_load_storage_state(None))
            fetch_path = temp_path
            persist = False
        else:
            store = _profiles.ProfileStore(None)
            resolved_path = store.storage_state_path(store.resolve_profile(profile))
            fetch_path = resolved_path
            persist = True
    else:
        fetch_path = resolved_path
        persist = True

    try:
        details = await asyncio.to_thread(
            fetch_tokens_from_storage,
            fetch_path,
            persist=persist,
            profile=profile,
            _return_tokens=True,
            _return_details=True,
        )
        if isinstance(details, _TokenFetchDetails):
            state = details.storage_state
            csrf = details.csrf_token
            session_id = details.session_id
            cookie_snapshot = details.cookie_snapshot
        else:
            _summary, csrf, session_id = details
            state = _load_storage_state(fetch_path)
            cookie_snapshot = None
        cookies = extract_cookies_with_domains(state)
        if resolved_path is None:
            records = _account_records_from_storage_state(state)
            account = records[0] if records else {}
            raw_authuser = account.get("authuser")
            authuser = (
                raw_authuser
                if isinstance(raw_authuser, int) and raw_authuser >= 0
                else 0
            )
            raw_email = account.get("email")
            account_email = (
                raw_email.strip()
                if isinstance(raw_email, str) and raw_email.strip()
                else None
            )
            cookie_jar = build_cookie_jar(cookies=cookies)
        else:
            authuser = get_authuser_for_storage(resolved_path)
            account_email = get_account_email_for_storage(resolved_path)
            cookie_jar = _cookie_jar_from_storage_state(state)
        return cls(
            cookies=cookies,
            csrf_token=csrf,
            session_id=session_id,
            storage_path=resolved_path,
            cookie_jar=cookie_jar,
            authuser=authuser,
            cookie_snapshot=cookie_snapshot,
            account_email=account_email,
        )
    finally:
        if temp_path is not None:
            with contextlib.suppress(OSError):
                temp_path.unlink()


def check_storage_with_network(path: str | os.PathLike[str]) -> dict[str, Any]:
    result = check_storage(path)
    result["network_test"] = True
    if not result.get("ok"):
        result["token_fetch"] = {
            "ok": False,
            "skipped": True,
            "reason": "offline_auth_not_ready",
        }
        return result
    try:
        result["token_fetch"] = fetch_tokens_from_storage(path, persist=False)
    except NotebookLMError as exc:
        result["ok"] = False
        result["token_fetch"] = {"ok": False, "error": str(exc)}
    return result


def refresh_storage(path: str | os.PathLike[str]) -> dict[str, Any]:
    return fetch_tokens_from_storage(path, persist=True)


def _temporary_storage_state(state: dict[str, Any]):
    """Write storage state to a private temp file for in-env/in-memory auth."""

    tmp = tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False)
    try:
        json.dump(state, tmp)
        tmp.close()
        return Path(tmp.name)
    except Exception:
        tmp.close()
        with contextlib.suppress(OSError):
            Path(tmp.name).unlink()
        raise


async def fetch_tokens(
    cookies: Mapping[Any, str],
    storage_path: Path | None = None,
    profile: str | None = None,
    *,
    authuser: int | None = None,
    account_email: str | None = None,
) -> tuple[str, str]:
    """Fetch CSRF/session tokens from cookies; file-backed path preferred."""

    if storage_path is not None:
        return await fetch_tokens_with_domains(
            storage_path,
            profile,
            authuser=authuser,
            account_email=account_email,
        )

    state = _cookies.build_storage_state(
        {
            "name": name,
            "value": value,
            "domain": domain,
            "path": path,
            "secure": True,
        }
        for (name, domain, path), value in normalize_cookie_map(cookies).items()
    )
    tmp_path = _temporary_storage_state(state)
    try:
        return await fetch_tokens_with_domains(
            tmp_path,
            profile,
            authuser=authuser,
            account_email=account_email,
        )
    finally:
        with contextlib.suppress(OSError):
            tmp_path.unlink()


async def fetch_tokens_with_domains(
    path: Path | None = None,
    profile: str | None = None,
    *,
    authuser: int | None = None,
    account_email: str | None = None,
) -> tuple[str, str]:
    """Fetch CSRF/session tokens from storage, preserving cookie domains."""

    temp_path: Path | None = None
    if path is None:
        if "NOTEBOOKLM_AUTH_JSON" in os.environ:
            temp_path = _temporary_storage_state(_load_storage_state(None))
            path = temp_path
        else:
            path = _paths.get_storage_path(profile=profile)
    try:
        _summary, csrf, session_id = await asyncio.to_thread(
            fetch_tokens_from_storage,
            path,
            persist=temp_path is None,
            authuser=authuser,
            account_email=account_email,
            profile=profile,
            _return_tokens=True,
        )
        return csrf, session_id
    finally:
        if temp_path is not None:
            with contextlib.suppress(OSError):
                temp_path.unlink()


# --------------------------------------------------------------------------- #
# auth inspect
# --------------------------------------------------------------------------- #


def _account_records_from_storage_state(state: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(state, dict):
        return []
    raw = state.get("accounts")
    if isinstance(raw, list):
        return [a for a in raw if isinstance(a, dict)]
    single = state.get("account")
    if isinstance(single, dict):
        return [single]
    namespace = state.get("notebooklm")
    if isinstance(namespace, dict) and isinstance(namespace.get("account"), dict):
        return [namespace["account"]]
    return []


def _account_metadata_from_storage_state(state: Any) -> dict[str, Any]:
    if not isinstance(state, dict):
        return {}
    namespace = state.get("notebooklm")
    if not isinstance(namespace, dict):
        return {}
    account = namespace.get("account")
    return account if isinstance(account, dict) else {}


def _account_context_path(storage_path: Path | str) -> Path:
    return Path(storage_path).with_name("context.json")


def _read_legacy_account_metadata(storage_path: Path | str) -> dict[str, Any]:
    context_path = _account_context_path(storage_path)
    try:
        data = json.loads(context_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    account = data.get("account")
    return account if isinstance(account, dict) else {}


def _drop_legacy_account_metadata(storage_path: Path | str) -> None:
    context_path = _account_context_path(storage_path)
    try:
        data = json.loads(context_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return
    if not isinstance(data, dict) or "account" not in data:
        return
    data.pop("account", None)
    if data:
        _profiles.write_json_atomic(context_path, data)
    else:
        with contextlib.suppress(OSError):
            context_path.unlink()


def read_account_metadata(storage_path: Path | str | None) -> dict[str, Any]:
    """Read profile account metadata from storage, matching upstream."""

    if storage_path is None:
        return {}
    try:
        state = _cookies.load_storage_state(storage_path)
    except ValidationError:
        state = {}
    in_band = _account_metadata_from_storage_state(state)
    if in_band:
        return in_band
    return _read_legacy_account_metadata(storage_path)


def _read_account_metadata_for_storage(path: Path | str | None) -> dict[str, Any]:
    metadata = read_account_metadata(path)
    if metadata or path is None:
        return metadata
    try:
        state = _cookies.load_storage_state(path)
    except ValidationError:
        return {}
    records = _account_records_from_storage_state(state)
    return records[0] if records else {}


def get_authuser_for_storage(storage_path: Path | str | None) -> int:
    raw = _read_account_metadata_for_storage(storage_path).get("authuser")
    return raw if isinstance(raw, int) and raw >= 0 else 0


def get_account_email_for_storage(storage_path: Path | str | None) -> str | None:
    raw = _read_account_metadata_for_storage(storage_path).get("email")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def format_authuser_value(authuser: int = 0, account_email: str | None = None) -> str:
    return account_email.strip() if account_email and account_email.strip() else str(authuser)


def authuser_query(authuser: int = 0, account_email: str | None = None) -> str:
    return urlencode({"authuser": format_authuser_value(authuser, account_email)})


def write_account_metadata(
    storage_path: Path | str, *, authuser: int, email: str | None = None
) -> None:
    storage = Path(storage_path)
    with _storage_state_file_lock(storage):
        try:
            state = _cookies.load_storage_state(storage)
        except ValidationError:
            state = {"cookies": [], "origins": []}
        payload: dict[str, Any] = {"authuser": authuser}
        if email:
            payload["email"] = email
        namespace = state.get("notebooklm")
        if not isinstance(namespace, dict):
            namespace = {}
        namespace["version"] = 1
        namespace["account"] = payload
        state["notebooklm"] = namespace
        _cookies.save_storage_state(storage, state)
    _drop_legacy_account_metadata(storage_path)


def clear_account_metadata(storage_path: Path | str | None) -> None:
    if storage_path is None:
        return
    storage = Path(storage_path)
    with _storage_state_file_lock(storage):
        try:
            state = _cookies.load_storage_state(storage)
        except ValidationError:
            return
        state.pop("account", None)
        state.pop("accounts", None)
        namespace = state.get("notebooklm")
        if isinstance(namespace, dict):
            namespace.pop("account", None)
            if set(namespace) <= {"version"}:
                state.pop("notebooklm", None)
        _cookies.save_storage_state(storage, state)
    _drop_legacy_account_metadata(storage_path)


def inspect_storage(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Report account-like metadata/counts from fixture storage, without values."""

    p = Path(path)
    result: dict[str, Any] = {
        "storage_path": str(p),
        "exists": p.exists(),
        "valid_json": False,
        "cookie_count": 0,
        "account_count": 0,
        "accounts": [],
    }
    if not result["exists"]:
        return result
    try:
        state = _cookies.load_storage_state(p)
    except ValidationError:
        return result
    result["valid_json"] = True
    result["cookie_count"] = len(_cookies.cookies_from_storage_state(state))

    accounts = []
    for record in _account_records_from_storage_state(state):
        email = record.get("email")
        accounts.append(
            {
                "authuser": record.get("authuser", 0),
                "is_default": bool(record.get("is_default", False)),
                "email_present": bool(email),
            }
        )
    result["account_count"] = len(accounts)
    result["accounts"] = accounts
    return result


# --------------------------------------------------------------------------- #
# auth logout
# --------------------------------------------------------------------------- #


def logout(
    *,
    storage_path: str | os.PathLike[str],
    browser_profile_dir: str | os.PathLike[str] | None = None,
    context_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Remove local auth artifacts for one profile only.

    Deletes the ``storage_state.json`` file plus supplied context/browser paths.
    Nothing outside the supplied paths is touched.
    """

    storage = Path(storage_path)
    removed: list[str] = []

    storage_removed = False
    try:
        storage.unlink()
        storage_removed = True
        removed.append(str(storage))
    except FileNotFoundError:
        pass

    browser_removed = False
    if browser_profile_dir is not None:
        browser = Path(browser_profile_dir)
        if browser.is_dir():
            shutil.rmtree(browser)
            browser_removed = True
            removed.append(str(browser))

    context_removed = False
    if context_path is not None:
        context = Path(context_path)
        try:
            context.unlink()
            context_removed = True
            removed.append(str(context))
        except FileNotFoundError:
            pass

    return {
        "storage_path": str(storage),
        "storage_removed": storage_removed,
        "browser_profile_removed": browser_removed,
        "context_removed": context_removed,
        "removed": removed,
    }


# --------------------------------------------------------------------------- #
# doctor
# --------------------------------------------------------------------------- #


def _check(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


def doctor(
    home: str | os.PathLike[str] | None = None,
    *,
    profile: str | None = None,
    fix: bool = False,
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run deterministic offline profile/auth/layout checks.

    With ``fix=True`` only safe, idempotent repairs are made (creating a missing
    home or ``profiles/`` directory). No network access; no values are emitted.
    """

    store = _profiles.ProfileStore(home, environ=environ)
    fixed: list[str] = []

    if fix:
        if not store.home.exists():
            store.home.mkdir(parents=True, exist_ok=True)
            fixed.append(str(store.home))
        if not store.profiles_dir.exists():
            store.profiles_dir.mkdir(parents=True, exist_ok=True)
            fixed.append(str(store.profiles_dir))

    resolved = store.resolve_profile(profile)
    checks: list[dict[str, Any]] = []

    checks.append(
        _check("home_dir", store.home.is_dir(), "NotebookLM home directory exists")
    )
    checks.append(
        _check(
            "profiles_dir", store.profiles_dir.is_dir(), "profiles/ directory exists"
        )
    )

    profile_dir = store.profile_dir(resolved)
    checks.append(
        _check(
            "profile_exists",
            profile_dir.is_dir(),
            f"profile '{resolved}' directory exists",
        )
    )

    storage_path = store.storage_state_path(resolved)
    storage_present = storage_path.is_file()
    checks.append(
        _check("storage_present", storage_present, "storage_state.json is present")
    )

    auth = check_storage(storage_path) if storage_present else None
    checks.append(
        _check(
            "storage_valid",
            bool(auth and auth["valid_json"]),
            "storage_state.json parses as valid JSON",
        )
    )
    checks.append(
        _check(
            "auth_cookies",
            bool(auth and auth["has_required_cookies"] and auth["domains_ok"]),
            "required auth cookies present with valid domains",
        )
    )

    context_path = store.context_path(resolved)
    context_valid = True
    context_detail = "session context is absent or valid"
    if context_path.exists():
        try:
            _profiles.read_context(context_path)
        except ValidationError:
            context_valid = False
            context_detail = "session context is corrupt"
    checks.append(_check("context_valid", context_valid, context_detail))

    report = {
        "home": str(store.home),
        "profile": resolved,
        "checks": checks,
        "fixed": fixed,
        "ok": all(c["ok"] for c in checks),
    }
    # Defensive: ensure nothing sensitive ever rides along in a diagnostic report.
    return _cookies.scrub(report)
