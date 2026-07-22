"""Phase 2B stdlib browser-cookie import foundation.

This module is the offline, fixture-backed foundation for the upstream
``rookiepy``-style "import auth cookies from a browser" capability. It supports
the three on-disk cookie-store shapes upstream reads from:

  * **Chromium-family** ``Cookies`` SQLite databases (Chrome, Chromium, Brave,
    Edge, Opera, Opera GX, Vivaldi, Arc) — both the plaintext ``value`` column
    and the AES-encrypted ``encrypted_value`` column, where encrypted values are
    routed through an explicit decryptor callback;
  * **Firefox** ``cookies.sqlite`` databases (the ``moz_cookies`` table), whose
    values are plaintext;
  * **Safari** ``Cookies.binarycookies`` files, parsed with a stdlib ``struct``
    reader.

Phase 2B began as an *explicit-source only* foundation:
``resolve_cookie_store`` computes a deterministic path **under an explicit fixture
root** (a synthetic ``$HOME``) or accepts an explicit cookie-store path. With
neither, it returns a redacted "unsupported" result and never inspects the real
machine.

Phase 2E-A adds the first explicitly authorized live browser lane:
``resolve_live_cookie_store('firefox')`` may consult the current user's Firefox
profile directory and read plaintext ``cookies.sqlite`` values for an opt-in
``login --browser-cookies firefox`` / ``auth inspect --browser firefox`` /
``auth refresh --browser-cookies firefox`` command. Live summaries and persisted
``auth_source.json`` metadata remain redacted: cookie values and live store paths
are never emitted or persisted. Secret Service/libsecret lookup, subprocess
helpers, and network refresh remain out of scope.

Phase 2E-B adds the second plaintext live lane:
``resolve_live_cookie_store('safari', os_name='macOS')`` may consult the current
user's macOS Safari ``Cookies.binarycookies`` file (the loose ``Library/Cookies``
store, the sandboxed ``com.apple.Safari`` container, or the shared App Group
container, in that order) for the same opt-in ``login`` / ``auth inspect`` /
``auth refresh`` commands. Safari has no browser-profile concept, so a requested
``--browser-profile`` is ignored and the persisted/echoed ``browser_profile`` is
always ``None``. The same redaction guarantees hold: cookie values and the live
store path are never emitted or persisted.

Phase 2E-C1 adds a bounded Chromium-family live-discovery foothold for
``auth inspect --browser``. It may resolve a Chromium ``Network/Cookies`` or
legacy ``Cookies`` database and report plaintext rows plus encrypted-row blocked
counts/names. Explicit supported-OS gates also support live Chromium
import/refresh with bounded decryptors.

Decryption of Chromium encrypted values requires AES (absent from the stdlib)
plus OS key material; explicit live OS gates provide bounded decryptors. Linux
uses only the legacy ``peanuts`` fallback, requires the Chromium host digest,
and never queries Secret Service or a keyring. Other unavailable encrypted rows
are reported as *blocked* without ever exposing the encrypted bytes.

Cookie *values* only ever flow into a user-owned ``storage_state.json`` (the auth
artifact) on an explicit import; redacted summaries never emit a value or encrypted
blob. Explicit account-listing, import, and refresh flows may return or persist
account metadata.
"""

from __future__ import annotations

import asyncio
import configparser
import json
import re
import shutil
import sqlite3
import struct
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from . import auth as _auth
from . import cookies as _cookies
from . import os_credentials as _oscreds
from . import profiles as _profiles
from .errors import ValidationError

# --------------------------------------------------------------------------- #
# Generated auth-matrix constants (mirrors compat/auth_matrix.json)
# --------------------------------------------------------------------------- #

MACOS = "macOS"
LINUX = "Ubuntu-LTS-Linux"
WINDOWS = "Windows-11"
OS_ROWS = (MACOS, LINUX, WINDOWS)

# Chromium-family cookie browsers (read an encrypted SQLite ``Cookies`` DB).
CHROMIUM_FAMILY = (
    "arc",
    "brave",
    "chrome",
    "chromium",
    "edge",
    "opera",
    "opera-gx",
    "vivaldi",
)
FIREFOX = "firefox"
SAFARI = "safari"

# The 10 documented cookie-store browsers (chromium family + firefox + safari).
COOKIE_BROWSERS = tuple(sorted(CHROMIUM_FAMILY + (FIREFOX, SAFARI)))

# The five browser-cookie matrix selector paths.
COOKIE_IMPORT_PATHS = (
    "import",
    "profile-select",
    "account-select",
    "inspect",
    "refresh",
)
INCLUDE_DOMAINS_ALL = "all"

# Which chromium-family browsers expose a cookie store on each OS (mirrors the
# upstream-sourced ``os_cookie_store_path_keys`` mapping in the auth matrix).
OS_COOKIE_STORE_BROWSERS = {
    "macos": [
        "arc",
        "brave",
        "chrome",
        "chromium",
        "edge",
        "opera",
        "opera-gx",
        "vivaldi",
    ],
    "linux": ["brave", "chrome", "chromium", "edge", "opera", "opera-gx", "vivaldi"],
    "windows": [
        "brave",
        "chrome",
        "chromium",
        "edge",
        "opera",
        "opera-gx",
        "vivaldi",
    ],
}

# Cookie-value families.
FAMILY_CHROMIUM = "chromium"
FAMILY_FIREFOX = "firefox"
FAMILY_SAFARI = "safari"

# --------------------------------------------------------------------------- #
# Phase 2C: explicit browser-cookie source metadata (``auth_source.json``)
# --------------------------------------------------------------------------- #

# Schema marker stamped into persisted source metadata, so a future reader can
# detect/upgrade the shape deterministically.
AUTH_SOURCE_SCHEMA = "browser_cookies/auth_source/1"

# Source kinds a profile may have been imported from. ``cookie_store`` and
# ``fixture_root`` are explicit offline sources. ``live_browser`` is the Phase 2E
# opt-in live browser-store lane; it stores only routing metadata and re-resolves
# the browser path on refresh rather than persisting a live cookie-store path.
SOURCE_KIND_COOKIE_STORE = "cookie_store"
SOURCE_KIND_FIXTURE_ROOT = "fixture_root"
SOURCE_KIND_LIVE_BROWSER = "live_browser"
SOURCE_KINDS = (
    SOURCE_KIND_COOKIE_STORE,
    SOURCE_KIND_FIXTURE_ROOT,
    SOURCE_KIND_LIVE_BROWSER,
)

# The exhaustive, whitelisted key set for ``auth_source.json``. Anything outside
# this set is rejected on validation, so the file can never carry a cookie value,
# encrypted blob, token, raw email, or the ``account_email`` selector.
AUTH_SOURCE_KEYS = frozenset(
    {
        "schema",
        "browser",
        "family",
        "source_kind",
        "cookie_store",
        "fixture_root",
        "os_name",
        "browser_profile",
        "google_only",
        "selected_authuser",
    }
)

# Epoch conversions (no wall-clock dependence — pure arithmetic).
_CHROMIUM_EPOCH_OFFSET = 11644473600  # seconds 1601-01-01 -> 1970-01-01
_MAC_EPOCH_OFFSET = 978307200  # seconds 1970-01-01 -> 2001-01-01

_BROWSER_ALIASES = {
    "google-chrome": "chrome",
    "google chrome": "chrome",
    "googlechrome": "chrome",
    "msedge": "edge",
    "microsoft-edge": "edge",
    "microsoft edge": "edge",
    "operagx": "opera-gx",
    "opera_gx": "opera-gx",
    "opera gx": "opera-gx",
    "ff": "firefox",
    "mozilla": "firefox",
    "mozilla-firefox": "firefox",
}

_OS_CANON = {
    "macos": "macos",
    "macOS": "macos",
    "mac": "macos",
    "darwin": "macos",
    "linux": "linux",
    "Ubuntu-LTS-Linux": "linux",
    "ubuntu": "linux",
    "windows": "windows",
    "Windows-11": "windows",
    "win": "windows",
    "win32": "windows",
}

__all__ = [
    "MACOS",
    "LINUX",
    "WINDOWS",
    "OS_ROWS",
    "CHROMIUM_FAMILY",
    "FIREFOX",
    "SAFARI",
    "COOKIE_BROWSERS",
    "COOKIE_IMPORT_PATHS",
    "INCLUDE_DOMAINS_ALL",
    "OS_COOKIE_STORE_BROWSERS",
    "FAMILY_CHROMIUM",
    "FAMILY_FIREFOX",
    "FAMILY_SAFARI",
    "normalize_browser",
    "browser_family",
    "parse_include_domains",
    "CookieStoreLocation",
    "resolve_cookie_store",
    "CookieExtraction",
    "redacted_summary",
    "extract_chromium",
    "extract_firefox",
    "extract_safari",
    "parse_binarycookies",
    "extract_cookies",
    "select_account",
    "account_summary",
    "import_to_storage_state",
    "inspect_cookie_store",
    "resolve_live_cookie_store",
    "import_live_browser_to_storage_state",
    "enumerate_live_browser_accounts",
    "inspect_live_cookie_store",
    "AUTH_SOURCE_SCHEMA",
    "SOURCE_KIND_COOKIE_STORE",
    "SOURCE_KIND_FIXTURE_ROOT",
    "SOURCE_KIND_LIVE_BROWSER",
    "SOURCE_KINDS",
    "AUTH_SOURCE_KEYS",
    "build_auth_source_metadata",
    "validate_auth_source_metadata",
    "read_auth_source",
    "write_auth_source",
    "refresh_browser_cookies",
]


# --------------------------------------------------------------------------- #
# Browser normalization + family classification
# --------------------------------------------------------------------------- #


def normalize_browser(name: str) -> str:
    """Return the canonical cookie-browser name, or raise ``ValidationError``."""

    if not isinstance(name, str) or not name.strip():
        raise ValidationError("browser name must be a non-empty string")
    key = name.strip().lower()
    key = _BROWSER_ALIASES.get(key, key)
    if key not in COOKIE_BROWSERS:
        raise ValidationError(
            f"unsupported cookie browser: {name!r} "
            f"(supported: {', '.join(COOKIE_BROWSERS)})"
        )
    return key


def browser_family(name: str) -> str:
    """Return the cookie-store family for a (possibly aliased) browser name."""

    canon = normalize_browser(name)
    if canon in CHROMIUM_FAMILY:
        return FAMILY_CHROMIUM
    if canon == FIREFOX:
        return FAMILY_FIREFOX
    return FAMILY_SAFARI


def parse_include_domains(values: Sequence[str] | None) -> set[str]:
    labels: set[str] = set()
    for raw in values or ():
        for part in str(raw).split(","):
            label = part.strip().lower()
            if label:
                labels.add(label)
    if not labels:
        return labels
    valid = set(_auth.OPTIONAL_COOKIE_DOMAINS_BY_LABEL) | {INCLUDE_DOMAINS_ALL}
    bad = labels - valid
    if bad:
        supported = ", ".join(sorted(valid))
        raise ValidationError(
            f"unknown --include-domains label(s): {', '.join(sorted(bad))}. Supported: {supported}."
        )
    return labels


def _included_cookie_domains(labels: set[str] | None) -> set[str]:
    domains = set(_auth.REQUIRED_COOKIE_DOMAINS)
    if labels:
        if INCLUDE_DOMAINS_ALL in labels:
            domains.update(_auth.OPTIONAL_COOKIE_DOMAINS)
        else:
            for label in labels:
                domains.update(_auth.OPTIONAL_COOKIE_DOMAINS_BY_LABEL[label])
    for cctld in _auth.GOOGLE_REGIONAL_CCTLDS:
        domains.add(f".google.{cctld}")
    return domains


def _is_included_cookie_domain(domain: Any, labels: set[str] | None) -> bool:
    return isinstance(domain, str) and domain in _included_cookie_domains(labels)


def _canon_os(os_name: Optional[str]) -> str:
    if os_name is None:
        return "macos"
    return _OS_CANON.get(os_name, _OS_CANON.get(os_name.lower(), "macos"))


def _chromium_utc_to_unix(utc: Any) -> Optional[int]:
    try:
        utc = int(utc)
    except (TypeError, ValueError):
        return None
    if utc <= 0:
        return None
    secs = utc / 1_000_000 - _CHROMIUM_EPOCH_OFFSET
    return int(secs) if secs > 0 else None


def _mac_abs_to_unix(abs_secs: Any) -> Optional[int]:
    try:
        secs = float(abs_secs) + _MAC_EPOCH_OFFSET
    except (TypeError, ValueError):
        return None
    return int(secs) if secs > 0 else None


def _chromium_samesite(value: Any) -> Optional[str]:
    mapping = {0: "None", 1: "Lax", 2: "Strict"}
    try:
        return mapping.get(int(value))
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Deterministic cookie-store path resolution (explicit fixture root only)
# --------------------------------------------------------------------------- #

# Chromium user-data directory relative to the synthetic home (fixture root).
_CHROMIUM_DATA_DIR = {
    "macos": {
        "chrome": ("Library", "Application Support", "Google", "Chrome"),
        "chromium": ("Library", "Application Support", "Chromium"),
        "brave": ("Library", "Application Support", "BraveSoftware", "Brave-Browser"),
        "edge": ("Library", "Application Support", "Microsoft Edge"),
        "opera": ("Library", "Application Support", "com.operasoftware.Opera"),
        "opera-gx": ("Library", "Application Support", "com.operasoftware.OperaGX"),
        "vivaldi": ("Library", "Application Support", "Vivaldi"),
        "arc": ("Library", "Application Support", "Arc", "User Data"),
    },
    "linux": {
        "chrome": (".config", "google-chrome"),
        "chromium": (".config", "chromium"),
        "brave": (".config", "BraveSoftware", "Brave-Browser"),
        "edge": (".config", "microsoft-edge"),
        "opera": (".config", "opera"),
        "opera-gx": (".config", "opera-gx"),
        "vivaldi": (".config", "vivaldi"),
    },
    "windows": {
        "chrome": ("AppData", "Local", "Google", "Chrome", "User Data"),
        "chromium": ("AppData", "Local", "Chromium", "User Data"),
        "brave": ("AppData", "Local", "BraveSoftware", "Brave-Browser", "User Data"),
        "edge": ("AppData", "Local", "Microsoft", "Edge", "User Data"),
        "opera": ("AppData", "Roaming", "Opera Software", "Opera Stable"),
        "opera-gx": ("AppData", "Roaming", "Opera Software", "Opera GX Stable"),
        "vivaldi": ("AppData", "Local", "Vivaldi", "User Data"),
    },
}

# Firefox profiles root relative to the synthetic home.
_FIREFOX_DATA_DIR = {
    "macos": ("Library", "Application Support", "Firefox"),
    "linux": (".mozilla", "firefox"),
    "windows": ("AppData", "Roaming", "Mozilla", "Firefox"),
}

# Safari cookie-file candidates relative to the synthetic home (macOS only). Used
# for explicit ``fixture_root`` resolution.
_SAFARI_CANDIDATES = (
    ("Library", "Cookies", "Cookies.binarycookies"),
    (
        "Library",
        "Containers",
        "com.apple.Safari",
        "Data",
        "Library",
        "Cookies",
        "Cookies.binarycookies",
    ),
)

# Live Safari cookie-file candidates relative to the real macOS home, in resolver
# precedence order: the legacy loose store, the sandboxed Safari container, then the
# shared App Group container. Extends (never mutates) ``_SAFARI_CANDIDATES`` so the
# explicit fixture-root layout above is unchanged.
_SAFARI_LIVE_CANDIDATES = _SAFARI_CANDIDATES + (
    (
        "Library",
        "Group Containers",
        "group.com.apple.Safari",
        "Library",
        "Cookies",
        "Cookies.binarycookies",
    ),
)


@dataclass
class CookieStoreLocation:
    """A resolved (or unresolvable) cookie-store location. Carries no secrets."""

    browser: str
    family: str
    os: str
    supported: bool
    exists: bool
    path: Optional[str] = None
    reason: Optional[str] = None
    browser_profile: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "browser": self.browser,
            "family": self.family,
            "os": self.os,
            "supported": self.supported,
            "exists": self.exists,
            "path": self.path,
            "reason": self.reason,
            "browser_profile": self.browser_profile,
        }


def _first_existing(candidates: Sequence[Path]) -> tuple[Path, bool]:
    """Return ``(path, exists)``: the first existing candidate, else the first."""

    for cand in candidates:
        if cand.exists():
            return cand, True
    return candidates[0], False


def _resolve_firefox(
    root: Path, os_key: str, browser_profile: Optional[str]
) -> tuple[Path, bool, Optional[str]]:
    data_dir = root.joinpath(*_FIREFOX_DATA_DIR[os_key])
    # 1) explicit profile name wins.
    if browser_profile:
        layouts = [data_dir / "Profiles" / browser_profile, data_dir / browser_profile]
        if os_key == "linux":
            layouts.reverse()
        cand = [p / "cookies.sqlite" for p in layouts]
        path, exists = _first_existing(cand)
        return path, exists, browser_profile
    # 2) parse profiles.ini for the default profile (the "profile-select" path).
    ini_path = data_dir / "profiles.ini"
    if ini_path.is_file():
        prof = _firefox_default_profile(ini_path, data_dir)
        if prof is not None:
            store = prof / "cookies.sqlite"
            return store, store.exists(), prof.name
    # 3) fall back to the conventional default profile name.
    cand = [
        data_dir / "Profiles" / "default-release" / "cookies.sqlite",
        data_dir / "default-release" / "cookies.sqlite",
    ]
    path, exists = _first_existing(cand)
    return path, exists, path.parent.name


def _resolve_live_firefox(
    root: Path, os_key: str, browser_profile: Optional[str]
) -> tuple[Path, bool, Optional[str]]:
    conventional = _resolve_firefox(root, os_key, browser_profile)
    if os_key != "linux" or conventional[1]:
        return conventional
    snap = _resolve_firefox(
        root / "snap" / "firefox" / "common", os_key, browser_profile
    )
    return snap if snap[1] else conventional


def _firefox_default_profile(ini_path: Path, data_dir: Path) -> Optional[Path]:
    parser = configparser.ConfigParser()
    try:
        parser.read(ini_path, encoding="utf-8")
    except (configparser.Error, OSError):
        return None

    def _resolve(section: str) -> Optional[Path]:
        path = parser[section].get("Path")
        if not path:
            return None
        is_relative = parser[section].get("IsRelative", "1") == "1"
        return (data_dir / path) if is_relative else Path(path)

    # Prefer an [Install*] section's Default= pointer, then a Profile with Default=1.
    for section in parser.sections():
        if section.startswith("Install"):
            default = parser[section].get("Default")
            if default:
                return data_dir / default
    for section in parser.sections():
        if section.startswith("Profile") and parser[section].get("Default") == "1":
            return _resolve(section)
    for section in parser.sections():
        if section.startswith("Profile"):
            return _resolve(section)
    return None


def _chromium_cookie_candidates(profile_dir: Path) -> list[Path]:
    """Live Chromium candidate order: modern Network/Cookies, then legacy Cookies."""

    return [profile_dir / "Network" / "Cookies", profile_dir / "Cookies"]


def _chromium_profile_store(data_dir: Path, profile: str) -> tuple[Path, bool]:
    return _first_existing(_chromium_cookie_candidates(data_dir / profile))


def _chromium_local_state_last_used(data_dir: Path) -> Optional[str]:
    """Return a safe Chromium Local State profile.last_used value, if present.

    Only the profile routing field is read; ``os_crypt`` and profile info-cache data
    are deliberately ignored so this helper cannot become a credential/decryptor
    boundary by accident.
    """

    try:
        data = json.loads((data_dir / "Local State").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    profile = data.get("profile")
    if not isinstance(profile, dict):
        return None
    last_used = profile.get("last_used")
    if isinstance(last_used, str) and _looks_like_safe_browser_profile(last_used):
        return last_used
    return None


def _resolve_chromium(
    root: Path, os_key: str, browser: str, browser_profile: Optional[str]
) -> tuple[Path, bool, Optional[str]]:
    data_dir = root.joinpath(*_CHROMIUM_DATA_DIR[os_key][browser])
    return _resolve_chromium_data_dir(data_dir, browser_profile)


def _resolve_chromium_data_dir(
    data_dir: Path, browser_profile: Optional[str]
) -> tuple[Path, bool, Optional[str]]:
    if browser_profile:
        if not _looks_like_safe_browser_profile(browser_profile):
            raise ValidationError("unsafe chromium browser profile name")
        path, exists = _chromium_profile_store(data_dir, browser_profile)
        return path, exists, browser_profile

    # 1) Chromium's Local State last_used wins, but only when safe and backed by an
    # actual cookie DB. Ignore unsafe/missing values instead of echoing them.
    last_used = _chromium_local_state_last_used(data_dir)
    if last_used:
        path, exists = _chromium_profile_store(data_dir, last_used)
        if exists:
            return path, True, last_used

    # 2) Conventional default profile.
    path, exists = _chromium_profile_store(data_dir, "Default")
    if exists:
        return path, True, "Default"

    # 3) Stable first safe profile with a cookie DB.
    profiles: list[tuple[str, Path]] = []
    try:
        children = list(data_dir.iterdir())
    except OSError:
        children = []
    for child in children:
        if not child.is_dir() or not _looks_like_safe_browser_profile(child.name):
            continue
        cand, cand_exists = _chromium_profile_store(data_dir, child.name)
        if cand_exists:
            profiles.append((child.name, cand))
    if profiles:
        name, cand = sorted(profiles, key=lambda item: item[0].lower())[0]
        return cand, True, name

    # Deterministic absent-store fallback; no live path is emitted by public live
    # summaries, but the internal path helps callers test existence.
    fallback, exists = _chromium_profile_store(data_dir, "Default")
    return fallback, exists, "Default"


def _resolve_live_chromium(
    root: Path, os_key: str, browser: str, browser_profile: Optional[str]
) -> tuple[Path, bool, Optional[str]]:
    conventional = _resolve_chromium(root, os_key, browser, browser_profile)
    if os_key != "linux" or browser != "chromium" or conventional[1]:
        return conventional
    snap = _resolve_chromium_data_dir(
        root / "snap" / "chromium" / "common" / "chromium", browser_profile
    )
    return snap if snap[1] else conventional


def resolve_cookie_store(
    browser: str,
    *,
    fixture_root: str | Path | None = None,
    cookie_store: str | Path | None = None,
    os_name: Optional[str] = None,
    browser_profile: Optional[str] = None,
) -> CookieStoreLocation:
    """Resolve a browser cookie store from an explicit fixture root or path.

    With neither ``fixture_root`` nor ``cookie_store`` this returns an unsupported
    result and **never** inspects the real machine, ``~``, or any OS credential
    store. The path layout under ``fixture_root`` mirrors the real per-OS browser
    layout, so a hermetic fixture can reproduce it under a temp directory.
    """

    canon = normalize_browser(browser)
    family = browser_family(canon)
    os_key = _canon_os(os_name)
    os_label = {"macos": MACOS, "linux": LINUX, "windows": WINDOWS}[os_key]

    # An explicit cookie-store path bypasses layout resolution entirely.
    if cookie_store is not None:
        p = Path(cookie_store)
        return CookieStoreLocation(
            browser=canon,
            family=family,
            os=os_label,
            supported=True,
            exists=p.exists(),
            path=str(p),
        )

    if fixture_root is None:
        return CookieStoreLocation(
            browser=canon,
            family=family,
            os=os_label,
            supported=False,
            exists=False,
            path=None,
            reason=(
                "no explicit cookie store: pass --cookie-store or --fixture-root; "
                "reading a real browser store is a later parity slice"
            ),
        )

    root = Path(fixture_root)

    if family == FAMILY_CHROMIUM:
        if canon not in OS_COOKIE_STORE_BROWSERS[os_key]:
            return CookieStoreLocation(
                browser=canon,
                family=family,
                os=os_label,
                supported=False,
                exists=False,
                path=None,
                reason=f"{canon} has no documented cookie store on {os_label}",
            )
        data_dir = root.joinpath(*_CHROMIUM_DATA_DIR[os_key][canon])
        profile = browser_profile or "Default"
        profile_dir = data_dir / profile
        if os_key == "windows":
            candidates = [profile_dir / "Network" / "Cookies", profile_dir / "Cookies"]
        else:
            candidates = [profile_dir / "Cookies", profile_dir / "Network" / "Cookies"]
        path, exists = _first_existing(candidates)
        return CookieStoreLocation(
            browser=canon,
            family=family,
            os=os_label,
            supported=True,
            exists=exists,
            path=str(path),
        )

    if family == FAMILY_FIREFOX:
        path, exists, resolved_profile = _resolve_firefox(root, os_key, browser_profile)
        return CookieStoreLocation(
            browser=canon,
            family=family,
            os=os_label,
            supported=True,
            exists=exists,
            path=str(path),
            browser_profile=resolved_profile,
        )

    # Safari: only macOS has a cookie store.
    if os_key != "macos":
        return CookieStoreLocation(
            browser=canon,
            family=family,
            os=os_label,
            supported=False,
            exists=False,
            path=None,
            reason="safari cookie stores exist only on macOS",
        )
    candidates = [root.joinpath(*parts) for parts in _SAFARI_CANDIDATES]
    path, exists = _first_existing(candidates)
    return CookieStoreLocation(
        browser=canon,
        family=family,
        os=os_label,
        supported=True,
        exists=exists,
        path=str(path),
    )


def resolve_live_cookie_store(
    browser: str,
    *,
    os_name: Optional[str] = None,
    browser_profile: Optional[str] = None,
    home: str | Path | None = None,
) -> CookieStoreLocation:
    """Resolve an explicitly authorized live browser cookie store.

    Supports live Firefox and macOS Safari for import/inspect/refresh, plus
    bounded Chromium-family inspect and explicit supported-OS import/refresh.
    ``home`` exists for deterministic tests. With ``home=None`` this is the only
    helper in this module that may consult :func:`Path.home` — and it does so only
    after confirming a supported live lane/OS combination, so unsupported browsers
    refuse without ever touching the real home.
    """

    canon = normalize_browser(browser)
    family = browser_family(canon)
    os_key = _canon_os(os_name)
    os_label = {"macos": MACOS, "linux": LINUX, "windows": WINDOWS}[os_key]

    if family == FAMILY_CHROMIUM:
        if canon not in OS_COOKIE_STORE_BROWSERS[os_key]:
            return CookieStoreLocation(
                browser=canon,
                family=family,
                os=os_label,
                supported=False,
                exists=False,
                path=None,
                reason=f"{canon} has no documented cookie store on {os_label}",
            )
        root = Path.home() if home is None else Path(home)
        path, exists, resolved_profile = _resolve_live_chromium(
            root, os_key, canon, browser_profile
        )
        return CookieStoreLocation(
            browser=canon,
            family=family,
            os=os_label,
            supported=True,
            exists=exists,
            path=str(path),
            browser_profile=resolved_profile,
        )

    if canon == FIREFOX:
        root = Path.home() if home is None else Path(home)
        path, exists, resolved_profile = _resolve_live_firefox(
            root, os_key, browser_profile
        )
        return CookieStoreLocation(
            browser=canon,
            family=family,
            os=os_label,
            supported=True,
            exists=exists,
            path=str(path),
            browser_profile=resolved_profile,
        )

    if canon == SAFARI:
        # Safari only ships a cookie store on macOS; refuse other OS layouts before
        # consulting Path.home.
        if os_key != "macos":
            return CookieStoreLocation(
                browser=canon,
                family=family,
                os=os_label,
                supported=False,
                exists=False,
                path=None,
                reason="safari cookie stores exist only on macOS",
            )
        root = Path.home() if home is None else Path(home)
        candidates = [root.joinpath(*parts) for parts in _SAFARI_LIVE_CANDIDATES]
        path, exists = _first_existing(candidates)
        # Safari has no browser-profile concept; a requested ``browser_profile`` is
        # deliberately ignored and never echoed or persisted.
        return CookieStoreLocation(
            browser=canon,
            family=family,
            os=os_label,
            supported=True,
            exists=exists,
            path=str(path),
            browser_profile=None,
        )

    return CookieStoreLocation(
        browser=canon,
        family=family,
        os=os_label,
        supported=False,
        exists=False,
        path=None,
        reason="live browser-store discovery is implemented only for firefox and safari",
    )


def _looks_like_safe_browser_profile(value: str) -> bool:
    if not value or any(sep in value for sep in ("/", "\\", "@")):
        return False
    lowered = value.lower()
    oauth_access_prefix = "ya" + "29."
    oauth_refresh_prefix = "1" + "//"
    if (
        lowered.startswith("bearer ")
        or value.startswith(oauth_access_prefix)
        or value.startswith(oauth_refresh_prefix)
    ):
        return False
    if lowered in {"default", "default-release", "default-esr", "profile 1"}:
        return True
    if lowered.startswith("profile ") and lowered[8:].isdigit():
        return True
    if any(
        lowered.endswith(suffix)
        for suffix in (
            ".default",
            ".default-release",
            ".default-esr",
            ".dev-edition",
            ".dev-edition-default",
        )
    ):
        return True
    if "." in value and all(ch.isalnum() or ch in "._-" for ch in value):
        before, after = value.split(".", 1)
        return bool(before and after)
    return False


# --------------------------------------------------------------------------- #
# Extraction result + redacted summary
# --------------------------------------------------------------------------- #


@dataclass
class CookieExtraction:
    """The result of extracting cookies from a single store.

    ``cookies`` carries normalized cookies *with values* (only ever written to a
    user-owned ``storage_state.json``). ``blocked`` carries metadata only — name,
    host, and a reason — and never the encrypted bytes or any value.
    """

    browser: str
    family: str
    source_path: Optional[str]
    source_present: bool
    cookies: list[dict[str, Any]] = field(default_factory=list)
    blocked: list[dict[str, Any]] = field(default_factory=list)

    def redacted_summary(self) -> dict[str, Any]:
        return {
            "browser": self.browser,
            "family": self.family,
            "source_present": self.source_present,
            **redacted_summary(self.cookies, self.blocked),
        }


def redacted_summary(
    cookies: Iterable[dict[str, Any]], blocked: Iterable[dict[str, Any]] = ()
) -> dict[str, Any]:
    """Return value-free counts/names for a set of extracted/blocked cookies."""

    cookie_list = list(cookies)
    blocked_list = list(blocked)
    names = sorted(c["name"] for c in cookie_list)
    present = set(names)
    required = {
        name: (name in present) for name in sorted(_cookies.MINIMUM_REQUIRED_COOKIES)
    }
    return {
        "cookie_count": len(cookie_list),
        "cookie_names": names,
        "domains": sorted({c.get("domain") for c in cookie_list if c.get("domain")}),
        "blocked_count": len(blocked_list),
        "blocked_names": sorted(b["name"] for b in blocked_list),
        "required_cookies": required,
        "has_required_cookies": all(required.values()),
    }


def _normalized(raw: dict[str, Any]) -> dict[str, Any]:
    return _cookies.normalize_cookie(raw)


# --------------------------------------------------------------------------- #
# Chromium-family SQLite extraction
# --------------------------------------------------------------------------- #


def _open_sqlite_readonly(path: Path) -> Optional[sqlite3.Connection]:
    """Open a SQLite DB strictly read-only/immutable, or ``None`` if absent.

    ``immutable=1`` guarantees the fixture is never modified and that no ``-wal`` /
    ``-shm`` / lock sidecar files are created next to it.
    """

    if not path.is_file():
        return None
    uri = f"{path.resolve().as_uri()}?mode=ro&immutable=1"
    try:
        return sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        return None


def _copy_sqlite_for_read(
    path: Path,
) -> tuple[tempfile.TemporaryDirectory[str], Path] | None:
    """Copy a potentially locked SQLite DB plus sidecars to a private temp dir."""

    if not path.is_file():
        return None
    tmp = tempfile.TemporaryDirectory(prefix="notebooklm-cookies-")
    dest = Path(tmp.name) / path.name
    try:
        shutil.copy2(path, dest)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(path) + suffix)
            if sidecar.is_file():
                shutil.copy2(sidecar, Path(str(dest) + suffix))
    except OSError:
        tmp.cleanup()
        return None
    return tmp, dest


_BLOCKED_REASON = "encrypted value not decrypted: " + _oscreds.REASON_NO_STDLIB_AES


def extract_chromium(
    path: str | Path, *, decryptor=None, browser: str = "chromium"
) -> CookieExtraction:
    """Extract cookies from a Chromium-family ``Cookies`` SQLite DB (read-only).

    Plaintext ``value`` rows are used directly; rows that carry only an
    ``encrypted_value`` are routed through ``decryptor(blob, host=..., name=...)``.
    With no decryptor (or one that returns ``None``) an encrypted row is reported
    as *blocked* without ever exposing the encrypted bytes.
    """

    p = Path(path)
    conn = _open_sqlite_readonly(p)
    copied: tempfile.TemporaryDirectory[str] | None = None
    if conn is None:
        copied_db = _copy_sqlite_for_read(p)
        if copied_db is not None:
            copied, copied_path = copied_db
            conn = _open_sqlite_readonly(copied_path)
    if conn is None:
        if copied is not None:
            copied.cleanup()
        return CookieExtraction(
            browser=browser,
            family=FAMILY_CHROMIUM,
            source_path=str(p),
            source_present=False,
        )

    cookies: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    try:
        cur = conn.execute("SELECT * FROM cookies")
        columns = [d[0] for d in cur.description]
        for row in cur.fetchall():
            rec = dict(zip(columns, row))
            host = rec.get("host_key") or rec.get("host") or ""
            name = rec.get("name") or ""
            plaintext = rec.get("value") or ""
            enc = rec.get("encrypted_value") or b""
            if isinstance(enc, str):
                enc = enc.encode("latin-1")

            if not plaintext and enc:
                value = None
                if decryptor is not None:
                    try:
                        value = decryptor(enc, host=host, name=name)
                    except Exception:  # noqa: BLE001 - a bad decryptor blocks, never crashes
                        value = None
                if value is None:
                    blocked.append(
                        {"name": name, "host": host, "reason": _BLOCKED_REASON}
                    )
                    continue
                plaintext = value

            cookies.append(
                _normalized(
                    {
                        "name": name,
                        "value": plaintext,
                        "domain": host,
                        "path": rec.get("path") or "/",
                        "expires": _chromium_utc_to_unix(rec.get("expires_utc")),
                        "secure": bool(rec.get("is_secure") or rec.get("secure")),
                        "http_only": bool(
                            rec.get("is_httponly") or rec.get("httponly")
                        ),
                        "same_site": _chromium_samesite(rec.get("samesite")),
                    }
                )
            )
    except sqlite3.Error as exc:
        raise ValidationError(f"could not read chromium cookie store: {exc}") from exc
    finally:
        conn.close()
        if copied is not None:
            copied.cleanup()

    return CookieExtraction(
        browser=browser,
        family=FAMILY_CHROMIUM,
        source_path=str(p),
        source_present=True,
        cookies=cookies,
        blocked=blocked,
    )


# --------------------------------------------------------------------------- #
# Firefox moz_cookies extraction
# --------------------------------------------------------------------------- #


def extract_firefox(path: str | Path, *, browser: str = FIREFOX) -> CookieExtraction:
    """Extract cookies from a Firefox ``cookies.sqlite`` (``moz_cookies``) DB."""

    p = Path(path)
    conn = _open_sqlite_readonly(p)
    if conn is None:
        return CookieExtraction(
            browser=browser,
            family=FAMILY_FIREFOX,
            source_path=str(p),
            source_present=False,
        )

    cookies: list[dict[str, Any]] = []
    try:
        cur = conn.execute("SELECT * FROM moz_cookies")
        columns = [d[0] for d in cur.description]
        for row in cur.fetchall():
            rec = dict(zip(columns, row))
            expiry = rec.get("expiry")
            expires = int(expiry) if expiry not in (None, 0) else None
            cookies.append(
                _normalized(
                    {
                        "name": rec.get("name") or "",
                        "value": rec.get("value") or "",
                        "domain": rec.get("host") or "",
                        "path": rec.get("path") or "/",
                        "expires": expires,
                        "secure": bool(rec.get("isSecure")),
                        "http_only": bool(rec.get("isHttpOnly")),
                    }
                )
            )
    except sqlite3.Error as exc:
        raise ValidationError(f"could not read firefox cookie store: {exc}") from exc
    finally:
        conn.close()

    return CookieExtraction(
        browser=browser,
        family=FAMILY_FIREFOX,
        source_path=str(p),
        source_present=True,
        cookies=cookies,
    )


# --------------------------------------------------------------------------- #
# Safari binarycookies parsing
# --------------------------------------------------------------------------- #

_MAX_BINARYCOOKIE_PAGES = 1024
_MAX_BINARYCOOKIE_COOKIES_PER_PAGE = 4096


def _cstring(buf: bytes, offset: int) -> str:
    end = buf.index(b"\x00", offset)
    return buf[offset:end].decode("utf-8", "replace")


def _parse_binarycookie(page: bytes, offset: int) -> dict[str, Any]:
    size = struct.unpack_from("<I", page, offset)[0]
    rec = page[offset : offset + size]
    flags = struct.unpack_from("<I", rec, 8)[0]
    url_off, name_off, path_off, value_off = struct.unpack_from("<IIII", rec, 16)
    expiry_abs = struct.unpack_from("<d", rec, 40)[0]
    return {
        "host": _cstring(rec, url_off),
        "name": _cstring(rec, name_off),
        "path": _cstring(rec, path_off),
        "value": _cstring(rec, value_off),
        "secure": bool(flags & 0x1),
        "http_only": bool(flags & 0x4),
        "expiry_abs": expiry_abs,
    }


def parse_binarycookies(data: bytes) -> list[dict[str, Any]]:
    """Parse a Safari ``Cookies.binarycookies`` blob into raw cookie records.

    Returns dicts with ``host``/``name``/``path``/``value``/``secure``/
    ``http_only``/``expiry_abs`` (Mac absolute seconds). Raises ``ValidationError``
    on a malformed/incomplete file rather than fabricating cookies.
    """

    if not isinstance(data, (bytes, bytearray)):
        raise ValidationError("binarycookies data must be bytes")
    data = bytes(data)
    if data[:4] != b"cook":
        raise ValidationError("not a Safari binarycookies file (bad magic)")
    try:
        num_pages = struct.unpack_from(">I", data, 4)[0]
        if num_pages > _MAX_BINARYCOOKIE_PAGES:
            raise ValidationError("too many pages in Safari binarycookies file")
        page_sizes = [
            struct.unpack_from(">I", data, 8 + 4 * i)[0] for i in range(num_pages)
        ]
        cookies: list[dict[str, Any]] = []
        cursor = 8 + 4 * num_pages
        for size in page_sizes:
            page = data[cursor : cursor + size]
            cursor += size
            count = struct.unpack_from("<I", page, 4)[0]
            if count > _MAX_BINARYCOOKIE_COOKIES_PER_PAGE:
                raise ValidationError("too many cookies in Safari binarycookies page")
            offsets = [
                struct.unpack_from("<I", page, 8 + 4 * i)[0] for i in range(count)
            ]
            for off in offsets:
                cookies.append(_parse_binarycookie(page, off))
    except (struct.error, IndexError, ValueError) as exc:
        raise ValidationError(f"malformed binarycookies file: {exc}") from exc
    return cookies


def extract_safari(path: str | Path, *, browser: str = SAFARI) -> CookieExtraction:
    """Extract cookies from a Safari ``Cookies.binarycookies`` file."""

    p = Path(path)
    if not p.is_file():
        return CookieExtraction(
            browser=browser,
            family=FAMILY_SAFARI,
            source_path=str(p),
            source_present=False,
        )
    try:
        data = p.read_bytes()
    except OSError as exc:
        raise ValidationError("Safari cookie store could not be read") from exc
    parsed = parse_binarycookies(data)
    cookies = [
        _normalized(
            {
                "name": c["name"],
                "value": c["value"],
                "domain": c["host"],
                "path": c["path"],
                "expires": _mac_abs_to_unix(c["expiry_abs"]),
                "secure": c["secure"],
                "http_only": c["http_only"],
            }
        )
        for c in parsed
    ]
    return CookieExtraction(
        browser=browser,
        family=FAMILY_SAFARI,
        source_path=str(p),
        source_present=True,
        cookies=cookies,
    )


# --------------------------------------------------------------------------- #
# Dispatch: resolve + extract by family
# --------------------------------------------------------------------------- #


def extract_cookies(
    browser: str,
    *,
    fixture_root: str | Path | None = None,
    cookie_store: str | Path | None = None,
    os_name: Optional[str] = None,
    browser_profile: Optional[str] = None,
    decryptor=None,
) -> CookieExtraction:
    """Resolve a store from an explicit fixture/path, then extract by family."""

    canon = normalize_browser(browser)
    family = browser_family(canon)
    loc = resolve_cookie_store(
        canon,
        fixture_root=fixture_root,
        cookie_store=cookie_store,
        os_name=os_name,
        browser_profile=browser_profile,
    )
    if not loc.supported or loc.path is None:
        return CookieExtraction(
            browser=canon, family=family, source_path=loc.path, source_present=False
        )
    if family == FAMILY_CHROMIUM:
        return extract_chromium(loc.path, decryptor=decryptor, browser=canon)
    if family == FAMILY_FIREFOX:
        return extract_firefox(loc.path, browser=canon)
    return extract_safari(loc.path, browser=canon)


# --------------------------------------------------------------------------- #
# Account selection / filtering (never prints emails)
# --------------------------------------------------------------------------- #


def select_account(
    accounts: Sequence[dict[str, Any]],
    *,
    email: Optional[str] = None,
    authuser: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    """Select one account record by email, then authuser, then the default.

    Returns the raw record (callers must redact before output) or ``None``.
    """

    records = [a for a in (accounts or []) if isinstance(a, dict)]
    if email is not None:
        wanted = email.strip().casefold()
        return next(
            (
                account
                for account in records
                if isinstance(account.get("email"), str)
                and account["email"].casefold() == wanted
            ),
            None,
        )
    if authuser is not None:
        return next((a for a in records if a.get("authuser") == authuser), None)
    default = next((a for a in records if a.get("is_default")), None)
    if default is not None:
        return default
    return records[0] if records else None


def account_summary(
    accounts: Sequence[dict[str, Any]], selected: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    """Return a redacted account-selection summary (counts/indices, no emails)."""

    records = [a for a in (accounts or []) if isinstance(a, dict)]
    default = next((a.get("authuser", 0) for a in records if a.get("is_default")), None)
    return {
        "count": len(records),
        "email_present_count": sum(1 for a in records if a.get("email")),
        "default_authuser": default,
        "selected_authuser": selected.get("authuser")
        if isinstance(selected, dict)
        else None,
    }


# --------------------------------------------------------------------------- #
# Import to storage_state.json + redacted inspection
# --------------------------------------------------------------------------- #


def _extract_and_filter(
    browser: str,
    *,
    fixture_root: str | Path | None,
    cookie_store: str | Path | None,
    os_name: Optional[str],
    browser_profile: Optional[str],
    decryptor,
    google_only: bool,
    include_domains: set[str] | None = None,
) -> tuple[str, str, "CookieExtraction", list[dict[str, Any]]]:
    """Resolve + extract an explicit store, then apply the Google-only filter.

    Shared by :func:`import_to_storage_state` and :func:`refresh_browser_cookies`.
    Raises ``ValidationError`` if the explicit source is absent (never a live read).
    """

    canon = normalize_browser(browser)
    family = browser_family(canon)
    extraction = extract_cookies(
        canon,
        fixture_root=fixture_root,
        cookie_store=cookie_store,
        os_name=os_name,
        browser_profile=browser_profile,
        decryptor=decryptor,
    )
    if not extraction.source_present:
        raise ValidationError(
            f"browser cookie store not found for {canon} "
            f"(resolved path: {extraction.source_path})"
        )
    cookies = extraction.cookies
    if google_only:
        cookies = [
            c
            for c in cookies
            if _is_included_cookie_domain(c["domain"], include_domains)
        ]
    return canon, family, extraction, cookies


def _browser_cookie_storage_state(
    cookies: list[dict[str, Any]], *, google_only: bool
) -> dict[str, Any]:
    if google_only:
        return _auth.convert_rookiepy_cookies_to_storage_state(cookies)
    return _cookies.build_storage_state(cookies)


def import_to_storage_state(
    browser: str,
    *,
    dest_path: str | Path,
    fixture_root: str | Path | None = None,
    cookie_store: str | Path | None = None,
    os_name: Optional[str] = None,
    browser_profile: Optional[str] = None,
    decryptor=None,
    google_only: bool = True,
    include_domains: set[str] | None = None,
    accounts: Optional[Sequence[dict[str, Any]]] = None,
    account_email: Optional[str] = None,
    authuser: Optional[int] = None,
    all_accounts: bool = False,
) -> dict[str, Any]:
    """Import an explicit fixture/store into ``dest_path`` (``storage_state.json``).

    Returns a redacted summary (counts/names/required-presence/blocked-count and a
    redacted account summary). Never returns or persists a cookie value in the
    summary; values are written only into the user-owned storage state file.
    """

    canon, family, extraction, cookies = _extract_and_filter(
        browser,
        fixture_root=fixture_root,
        cookie_store=cookie_store,
        os_name=os_name,
        browser_profile=browser_profile,
        decryptor=decryptor,
        google_only=google_only,
        include_domains=include_domains,
    )

    state = _browser_cookie_storage_state(cookies, google_only=google_only)

    selected = None
    records = [a for a in (accounts or []) if isinstance(a, dict)]
    if records:
        state["accounts"] = records
        if not all_accounts:
            selected = select_account(records, email=account_email, authuser=authuser)
            if selected is not None:
                state["account"] = selected
    _cookies.save_storage_state(dest_path, state)

    summary = {
        "browser": canon,
        "family": family,
        "storage_path": str(dest_path),
        "written": True,
        "imported": len(cookies),
        "account": account_summary(records, selected),
        **redacted_summary(cookies, extraction.blocked),
    }
    return summary


def _extract_live_and_filter(
    browser: str,
    *,
    os_name: Optional[str],
    browser_profile: Optional[str],
    google_only: bool,
    include_domains: set[str] | None = None,
    decryptor=None,
) -> tuple[str, str, CookieStoreLocation, CookieExtraction, list[dict[str, Any]]]:
    """Resolve an authorized live browser store, extract it, then filter cookies.

    The live path is used only internally to read the user-authorized store. Public
    summaries and persisted metadata redact it and retain only browser/profile/OS
    routing metadata.
    """

    canon = normalize_browser(browser)
    loc = resolve_live_cookie_store(
        canon, os_name=os_name, browser_profile=browser_profile
    )
    if not loc.supported:
        raise ValidationError(
            loc.reason or f"live browser store not supported for {canon}"
        )
    if loc.path is None or not loc.exists:
        raise ValidationError(f"live {canon} cookie store not found")
    extraction = extract_cookies(canon, cookie_store=loc.path, decryptor=decryptor)
    if not extraction.source_present:
        raise ValidationError(f"live {canon} cookie store not found")
    cookies = extraction.cookies
    if google_only:
        cookies = [
            c
            for c in cookies
            if _is_included_cookie_domain(c["domain"], include_domains)
        ]
    return canon, browser_family(canon), loc, extraction, cookies


def _live_chromium_decryptor(
    browser: str,
    *,
    os_name: Optional[str],
    browser_profile: Optional[str] = None,
    decryptor,
    google_only: bool,
    include_domains: set[str] | None = None,
    action: str,
    use_keychain: bool = False,
):
    """Validate and wrap the live-Chromium decryptor boundary.

    The macOS Keychain, Windows DPAPI, and explicit Linux legacy-fallback gates
    stay constrained to Google-domain rows and redacted errors.
    """

    canon = normalize_browser(browser)
    if browser_family(canon) != FAMILY_CHROMIUM:
        return decryptor
    os_key = _canon_os(os_name)
    if os_key not in {"linux", "macos", "windows"}:
        raise ValidationError(
            f"live Chromium {action} with a decryptor is implemented only for "
            "Linux legacy fallback, macOS Keychain, and Windows DPAPI"
        )
    if not google_only:
        raise ValidationError(
            f"live Chromium {action} with --include-all-domains is not implemented in "
            "the live credential gate; refusing to decrypt broad browsing-history domains"
        )
    if decryptor is None:
        if os_key == "linux":
            decryptor = _live_linux_chromium_decryptor(
                action=action, use_keychain=use_keychain
            )
        elif os_key == "macos":
            decryptor = _live_macos_chromium_decryptor(
                canon, action=action, use_keychain=use_keychain
            )
        else:
            decryptor = _live_windows_chromium_decryptor(
                canon,
                os_name=os_name,
                browser_profile=browser_profile,
                action=action,
                use_keychain=use_keychain,
            )

    def google_only_decryptor(encrypted_value, *, host: str, name: str):
        if not _is_included_cookie_domain(host or "", include_domains):
            return None
        return decryptor(encrypted_value, host=host, name=name)

    return google_only_decryptor


def _live_macos_chromium_decryptor(canon: str, *, action: str, use_keychain: bool):
    if not use_keychain:
        raise ValidationError(
            f"live Chromium {action} requires an explicit decryptor or the "
            "macOS Keychain/Windows DPAPI credential gate"
        )
    if _oscreds.macos_chromium_keychain_service(canon) is None:
        raise ValidationError(
            "unsupported macOS Chromium Keychain browser; Safe Storage service "
            "mapping is unavailable"
        )
    try:
        secret = _oscreds.macos_chromium_keychain_password(canon)
    except _oscreds.CredentialUnavailableError:
        raise ValidationError(
            "macOS Chromium Keychain Safe Storage password unavailable"
        ) from None
    except Exception:
        raise ValidationError(
            "macOS Chromium Keychain Safe Storage password unavailable"
        ) from None
    try:
        decryptor = _oscreds.resolve_decryptor(
            "macOS",
            canon,
            safe_storage_password=secret,
        )
    except Exception:
        raise ValidationError("macOS Chromium Keychain decryptor unavailable") from None
    if decryptor is None:
        raise ValidationError("macOS Chromium Keychain decryptor unavailable")
    return decryptor


def _live_linux_chromium_decryptor(*, action: str, use_keychain: bool):
    if not use_keychain:
        raise ValidationError(
            f"live Chromium {action} requires an explicit decryptor or the "
            "Linux legacy fallback gate"
        )

    def decryptor(encrypted_value, *, host: str, name: str):
        return _oscreds.linux_chromium_decrypt_cookie_value(
            encrypted_value,
            host=host,
            safe_storage_password=_oscreds.LINUX_CHROMIUM_FALLBACK_PASSWORD,
            require_host_digest=True,
        )

    return decryptor


def _live_windows_chromium_decryptor(
    canon: str,
    *,
    os_name: Optional[str],
    browser_profile: Optional[str],
    action: str,
    use_keychain: bool,
):
    if not use_keychain:
        raise ValidationError(
            f"live Chromium {action} requires an explicit decryptor or the "
            "Windows DPAPI credential gate"
        )
    try:
        loc = resolve_live_cookie_store(
            canon, os_name=os_name, browser_profile=browser_profile
        )
        if not loc.supported or loc.path is None or not loc.exists:
            raise ValidationError(f"live {canon} cookie store not found")
        cookie_path = Path(loc.path)
        profile_dir = (
            cookie_path.parent.parent
            if cookie_path.parent.name.lower() == "network"
            else cookie_path.parent
        )
        local_state = (profile_dir.parent / "Local State").read_text(encoding="utf-8")
    except ValidationError:
        raise
    except Exception:
        raise ValidationError("Windows Chromium Local State unavailable") from None
    try:
        decryptor = _oscreds.resolve_decryptor(
            "Windows-11",
            canon,
            windows_local_state=local_state,
        )
    except Exception:
        raise ValidationError("Windows Chromium DPAPI decryptor unavailable") from None
    if decryptor is None:
        raise ValidationError("Windows Chromium DPAPI decryptor unavailable")
    return decryptor


def _live_summary_blocked(
    extraction: CookieExtraction,
    *,
    family: str,
    google_only: bool,
    include_domains: set[str] | None = None,
) -> list[dict[str, Any]]:
    blocked = extraction.blocked
    if family == FAMILY_CHROMIUM and google_only:
        blocked = [
            b
            for b in blocked
            if _is_included_cookie_domain(b.get("host", ""), include_domains)
        ]
    return blocked


def _live_meta_browser_profile(
    loc: CookieStoreLocation, requested: Optional[str]
) -> Optional[str]:
    """Browser-profile value to persist for a live source.

    Safari has no browser-profile concept, so a requested value is never persisted
    or echoed (always ``None``). Firefox keeps the resolved profile name, falling
    back to the requested name only if resolution produced none.
    """

    if loc.family == FAMILY_SAFARI:
        return None
    return loc.browser_profile or requested


def import_live_browser_to_storage_state(
    browser: str,
    *,
    dest_path: str | Path,
    os_name: Optional[str] = None,
    browser_profile: Optional[str] = None,
    google_only: bool = True,
    include_domains: set[str] | None = None,
    accounts: Optional[Sequence[dict[str, Any]]] = None,
    account_email: Optional[str] = None,
    authuser: Optional[int] = None,
    all_accounts: bool = False,
    decryptor=None,
    use_keychain: bool = False,
) -> dict[str, Any]:
    """Import from an authorized live browser store into ``storage_state.json``.

    Firefox and Safari live lanes read plaintext browser stores. Chromium-family
    live import is wired through either C2B's explicit injected decryptor boundary,
    C2C's opt-in macOS Keychain gate, C3A's opt-in Windows DPAPI gate, or the
    bounded Linux legacy fallback. Public summaries and live metadata remain
    pathless and value-free.
    """

    if all_accounts and accounts is None:
        raise ValidationError(
            "live --all-accounts is not supported without an explicit accounts fixture"
        )
    if accounts is None and (account_email is not None or authuser is not None):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise ValidationError(
                "live browser account discovery cannot run from an active event loop"
            )

    canon = normalize_browser(browser)
    live_decryptor = _live_chromium_decryptor(
        canon,
        os_name=os_name,
        browser_profile=browser_profile,
        decryptor=decryptor,
        google_only=google_only,
        include_domains=include_domains,
        action="import",
        use_keychain=use_keychain,
    )
    canon, family, loc, extraction, cookies = _extract_live_and_filter(
        canon,
        os_name=os_name,
        browser_profile=browser_profile,
        google_only=google_only,
        include_domains=include_domains,
        decryptor=live_decryptor,
    )
    state = _browser_cookie_storage_state(cookies, google_only=google_only)
    selected = None
    records = [a for a in (accounts or []) if isinstance(a, dict)]
    implicit_discovery = accounts is None and (
        account_email is not None or authuser is not None
    )
    if implicit_discovery:
        discovered = asyncio.run(
            _auth.enumerate_accounts(_auth._cookie_jar_from_storage_state(state))
        )
        records = [
            {
                "authuser": account.authuser,
                "email": account.email,
                "is_default": account.is_default,
            }
            for account in discovered
        ]
    if records:
        if not implicit_discovery:
            state["accounts"] = records
        if not all_accounts:
            selected = select_account(records, email=account_email, authuser=authuser)
            if selected is None and (account_email is not None or authuser is not None):
                raise ValidationError("requested browser account was not found")
            if selected is not None and not implicit_discovery:
                state["account"] = selected
    # Validate live-source metadata before writing storage_state.json, so an
    # unexpected profile-name validation failure cannot leave half-written auth.
    build_auth_source_metadata(
        canon,
        source_kind=SOURCE_KIND_LIVE_BROWSER,
        os_name=os_name,
        browser_profile=_live_meta_browser_profile(loc, browser_profile),
        google_only=google_only,
        selected_authuser=(
            selected.get("authuser") if isinstance(selected, dict) else None
        ),
    )
    _cookies.save_storage_state(dest_path, state)
    if implicit_discovery and selected is not None:
        _auth.write_account_metadata(
            dest_path,
            authuser=selected["authuser"],
            email=selected.get("email"),
        )
    elif accounts is None:
        _auth.clear_account_metadata(dest_path)
    summary = {
        "browser": canon,
        "family": family,
        "os": loc.os,
        "browser_profile": loc.browser_profile,
        "source_kind": SOURCE_KIND_LIVE_BROWSER,
        "source_path": None,
        "storage_path": str(dest_path),
        "written": True,
        "imported": len(cookies),
        "account": account_summary(records, selected),
        **redacted_summary(
            cookies,
            _live_summary_blocked(
                extraction,
                family=family,
                google_only=google_only,
                include_domains=include_domains,
            ),
        ),
    }
    return summary


def _live_chromium_profiles(data_dir: Path) -> list[str]:
    """Return populated conventional Chromium profiles in pinned stable order."""

    profiles: list[str] = []
    try:
        children = list(data_dir.iterdir())
    except OSError:
        return profiles
    for child in children:
        if child.name == "Default" or re.fullmatch(r"Profile [0-9]+", child.name):
            _, exists = _chromium_profile_store(data_dir, child.name)
            if exists:
                profiles.append(child.name)
    return sorted(
        profiles,
        key=lambda name: (name != "Default", int(name[8:]) if name != "Default" else 0),
    )


def enumerate_live_browser_accounts(
    browser: str,
    *,
    os_name: Optional[str] = None,
    browser_profile: Optional[str] = None,
    include_domains: set[str] | None = None,
    use_keychain: bool = False,
) -> list[dict[str, Any]]:
    """List accounts from one live store, or bare Chromium's populated profiles."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise ValidationError(
            "live browser account discovery cannot run from an active event loop"
        )

    canon = normalize_browser(browser)
    profiles: list[str] = []
    if browser_family(canon) == FAMILY_CHROMIUM and browser_profile is None:
        location = resolve_live_cookie_store(canon, os_name=os_name)
        if not location.supported:
            raise ValidationError(
                location.reason or f"live browser store not supported for {canon}"
            )
        cookie_path = Path(location.path or "")
        profile_dir = (
            cookie_path.parent.parent
            if cookie_path.parent.name.lower() == "network"
            else cookie_path.parent
        )
        profiles = _live_chromium_profiles(profile_dir.parent)
    if len(profiles) <= 1:
        profiles = [browser_profile]
    accounts: list[dict[str, Any]] = []
    seen_emails: set[str] = set()
    default_assigned = False
    fanout = len(profiles) > 1
    for profile in profiles:
        try:
            decryptor = None
            if browser_family(canon) == FAMILY_CHROMIUM and use_keychain:
                decryptor = _live_chromium_decryptor(
                    canon,
                    os_name=os_name,
                    browser_profile=profile,
                    decryptor=None,
                    google_only=True,
                    include_domains=include_domains,
                    action="inspect",
                    use_keychain=True,
                )
            _, _, loc, _, cookies = _extract_live_and_filter(
                canon,
                os_name=os_name,
                browser_profile=profile,
                google_only=True,
                include_domains=include_domains,
                decryptor=decryptor,
            )
            discovered = asyncio.run(
                _auth.enumerate_accounts(
                    _auth._cookie_jar_from_storage_state(
                        _browser_cookie_storage_state(cookies, google_only=True)
                    )
                )
            )
        except (OSError, ValueError, ValidationError):
            if not fanout:
                raise
            continue
        for account in discovered:
            if account.email in seen_emails:
                continue
            seen_emails.add(account.email)
            is_default = account.is_default and not default_assigned
            default_assigned = default_assigned or is_default
            accounts.append(
                {
                    "email": account.email,
                    "is_default": is_default,
                    "browser_profile": loc.browser_profile
                    if fanout or browser_profile is not None
                    else None,
                }
            )
    if fanout and not accounts:
        raise ValidationError("no signed-in Chromium profile could be inspected")
    return accounts


def inspect_live_cookie_store(
    browser: str,
    *,
    os_name: Optional[str] = None,
    browser_profile: Optional[str] = None,
    google_only: bool = False,
    include_domains: set[str] | None = None,
    use_keychain: bool = False,
) -> dict[str, Any]:
    """Return a redacted, pathless inspection of an authorized live browser store."""

    canon = normalize_browser(browser)
    decryptor = None
    if browser_family(canon) == FAMILY_CHROMIUM and use_keychain:
        decryptor = _live_chromium_decryptor(
            canon,
            os_name=os_name,
            browser_profile=browser_profile,
            decryptor=None,
            google_only=google_only,
            include_domains=include_domains,
            action="inspect",
            use_keychain=True,
        )
    canon, family, loc, extraction, _ = _extract_live_and_filter(
        canon,
        os_name=os_name,
        browser_profile=browser_profile,
        google_only=False,
        decryptor=decryptor,
    )
    redacted_cookies = extraction.cookies
    redacted_blocked = extraction.blocked
    # Chromium live stores may contain broad browsing-history cookie names/domains.
    # For the inspect-only foothold, expose only Google-domain rows; values and
    # encrypted bytes remain redacted/blocked as before.
    if google_only:
        redacted_cookies = [
            c
            for c in extraction.cookies
            if _is_included_cookie_domain(c["domain"], include_domains)
        ]
        redacted_blocked = [
            b
            for b in extraction.blocked
            if _is_included_cookie_domain(b.get("host", ""), include_domains)
        ]
    redacted = [_cookies.redact_cookie(c) for c in redacted_cookies]
    return {
        "browser": canon,
        "family": family,
        "os": loc.os,
        "browser_profile": loc.browser_profile,
        "source_kind": SOURCE_KIND_LIVE_BROWSER,
        "source_path": None,
        "supported": True,
        "source_present": extraction.source_present,
        "cookie_count": len(redacted_cookies),
        "cookie_names": sorted(c["name"] for c in redacted_cookies),
        "cookies": redacted,
        "blocked_count": len(redacted_blocked),
        "blocked_names": sorted(b["name"] for b in redacted_blocked),
        "domains": sorted({c["domain"] for c in redacted_cookies if c["domain"]}),
    }


# --------------------------------------------------------------------------- #
# Explicit/live source metadata: build / validate / persist (redacted)
# --------------------------------------------------------------------------- #


def build_auth_source_metadata(
    browser: str,
    *,
    source_kind: str,
    fixture_root: str | Path | None = None,
    cookie_store: str | Path | None = None,
    os_name: Optional[str] = None,
    browser_profile: Optional[str] = None,
    google_only: bool = True,
    selected_authuser: Optional[int] = None,
) -> dict[str, Any]:
    """Build redacted ``auth_source.json`` metadata for an explicit import source.

    Records only what ``auth refresh`` needs to re-import: browser, source kind,
    explicit path/root for explicit sources, OS layout, browser profile,
    google-only choice, and an optional selected ``authuser`` *index*. Live-browser
    sources deliberately store no cookie-store path and are re-resolved later.
    It never records a cookie value, encrypted blob, token, raw email, or the
    ``account_email`` selector. The result is validated before return.
    """

    canon = normalize_browser(browser)
    if source_kind not in SOURCE_KINDS:
        raise ValidationError(
            f"unsupported source kind: {source_kind!r} (expected one of {SOURCE_KINDS})"
        )
    if source_kind == SOURCE_KIND_COOKIE_STORE and not cookie_store:
        raise ValidationError(
            "cookie_store source requires an explicit --cookie-store path"
        )
    if source_kind == SOURCE_KIND_FIXTURE_ROOT and not fixture_root:
        raise ValidationError(
            "fixture_root source requires an explicit --fixture-root path"
        )
    if source_kind == SOURCE_KIND_LIVE_BROWSER and (
        cookie_store is not None or fixture_root is not None
    ):
        raise ValidationError(
            "live_browser source must not persist a cookie_store or fixture_root path"
        )

    authuser: Optional[int] = None
    if selected_authuser is not None and not isinstance(selected_authuser, bool):
        try:
            authuser = int(selected_authuser)
        except (TypeError, ValueError):
            authuser = None

    meta = {
        "schema": AUTH_SOURCE_SCHEMA,
        "browser": canon,
        "family": browser_family(canon),
        "source_kind": source_kind,
        "cookie_store": str(cookie_store) if cookie_store is not None else None,
        "fixture_root": str(fixture_root) if fixture_root is not None else None,
        "os_name": os_name,
        "browser_profile": browser_profile,
        "google_only": bool(google_only),
        "selected_authuser": authuser,
    }
    return validate_auth_source_metadata(meta)


def validate_auth_source_metadata(meta: Any) -> dict[str, Any]:
    """Validate persisted source metadata: whitelisted keys, no secret-looking values.

    Raises ``ValidationError`` for an unexpected key, an invalid source kind/path, a
    bad ``selected_authuser``, or any string value that looks like a credential —
    so a tampered file can never smuggle a secret into refresh output.
    """

    if not isinstance(meta, dict):
        raise ValidationError("auth source metadata must be a JSON object")
    extra = set(meta) - AUTH_SOURCE_KEYS
    if extra:
        raise ValidationError(
            f"auth source metadata has unexpected key(s): {sorted(extra)}"
        )
    canon = normalize_browser(meta.get("browser"))  # raises ValidationError if bad
    kind = meta.get("source_kind")
    if kind not in SOURCE_KINDS:
        raise ValidationError(f"auth source metadata has invalid source_kind: {kind!r}")
    if kind == SOURCE_KIND_LIVE_BROWSER:
        if meta.get("cookie_store") is not None or meta.get("fixture_root") is not None:
            raise ValidationError(
                "live_browser auth source must not contain a cookie_store or fixture_root path"
            )
    else:
        path_field = (
            "cookie_store" if kind == SOURCE_KIND_COOKIE_STORE else "fixture_root"
        )
        if not isinstance(meta.get(path_field), str) or not meta[path_field]:
            raise ValidationError(
                f"auth source metadata for kind {kind!r} requires a non-empty {path_field}"
            )
    su = meta.get("selected_authuser")
    if su is not None and (isinstance(su, bool) or not isinstance(su, int) or su < 0):
        raise ValidationError(
            "auth source metadata selected_authuser must be a non-negative integer or null"
        )
    # Defense in depth: no value may look like a credential/token. Explicit source
    # paths contain a path separator and are excluded by ``looks_like_secret``.
    for key, value in meta.items():
        if (
            key == "browser_profile"
            and isinstance(value, str)
            and _looks_like_safe_browser_profile(value)
        ):
            continue
        if isinstance(value, str) and _cookies.looks_like_secret(value):
            raise ValidationError(
                f"auth source metadata field {key!r} looks like a secret"
            )
    # Normalize the recorded browser to its canonical form.
    meta = dict(meta)
    meta["browser"] = canon
    return meta


def read_auth_source(meta_path: str | Path) -> Optional[dict[str, Any]]:
    """Read + validate persisted source metadata, or ``None`` if the file is absent."""

    data = _profiles.read_json(meta_path)
    if data is None:
        return None
    return validate_auth_source_metadata(data)


def write_auth_source(meta_path: str | Path, meta: dict[str, Any]) -> None:
    """Validate and atomically persist source metadata to ``meta_path``."""

    _profiles.write_json_atomic(meta_path, validate_auth_source_metadata(dict(meta)))


# --------------------------------------------------------------------------- #
# Offline refresh: re-import from an explicit source or persisted metadata
# --------------------------------------------------------------------------- #


def _existing_accounts(
    dest_path: str | Path,
) -> tuple[list[dict[str, Any]], Optional[dict[str, Any]]]:
    """Return ``(accounts, selected)`` preserved from an existing storage state.

    Reads only the user-owned ``storage_state.json``; tolerates absence/corruption
    by returning empties. Never emits anything — the caller redacts before output.
    """

    p = Path(dest_path)
    if not p.is_file():
        return [], None
    try:
        state = _cookies.load_storage_state(p)
    except ValidationError:
        return [], None
    raw = state.get("accounts")
    accounts = [a for a in raw if isinstance(a, dict)] if isinstance(raw, list) else []
    selected = _auth.read_account_metadata(p)
    if not selected:
        selected = (
            state.get("account") if isinstance(state.get("account"), dict) else None
        )
    if selected is None and accounts:
        selected = next((a for a in accounts if a.get("is_default")), None)
    return accounts, selected


def refresh_browser_cookies(
    browser: str,
    *,
    dest_path: str | Path,
    meta_path: str | Path,
    fixture_root: str | Path | None = None,
    cookie_store: str | Path | None = None,
    os_name: Optional[str] = None,
    browser_profile: Optional[str] = None,
    include_all_domains: bool = False,
    include_domains: set[str] | None = None,
    decryptor=None,
    use_keychain: bool = False,
) -> dict[str, Any]:
    """Re-import a profile's cookies from an explicit source or persisted metadata.

    Source resolution: an explicit ``--cookie-store``/``--fixture-root`` wins;
    otherwise the persisted ``auth_source.json`` for the profile is used. With
    neither, this raises ``ValidationError`` — it never falls back to a real
    machine browser store. Safe account metadata is preserved from the existing
    ``storage_state.json``, the metadata file is refreshed in lockstep, and a
    redacted summary (``refreshed: True`` + counts/names/required-presence) is
    returned. No cookie value, email, or encrypted blob is ever emitted.
    """

    canon = normalize_browser(browser)
    explicit = fixture_root is not None or cookie_store is not None

    if explicit:
        source_kind = (
            SOURCE_KIND_COOKIE_STORE
            if cookie_store is not None
            else SOURCE_KIND_FIXTURE_ROOT
        )
        src_fixture_root = fixture_root
        src_cookie_store = cookie_store
        src_os_name = os_name
        src_browser_profile = browser_profile
        google_only = not include_all_domains
    else:
        meta = read_auth_source(meta_path)
        if meta is None:
            raise ValidationError(
                "no explicit cookie source and no persisted browser-cookie source for "
                "this profile; pass --cookie-store or --fixture-root, or run "
                "'login --browser-cookies' first (refusing to read a real browser store)"
            )
        if normalize_browser(meta["browser"]) != canon:
            raise ValidationError(
                f"persisted browser-cookie source is for {meta['browser']!r}, not "
                f"{canon!r}; pass an explicit --cookie-store/--fixture-root to refresh "
                f"a different browser"
            )
        persisted_browser_profile = meta.get("browser_profile")
        if (
            meta["source_kind"] == SOURCE_KIND_LIVE_BROWSER
            and browser_profile is not None
            and browser_profile != persisted_browser_profile
        ):
            raise ValidationError(
                "requested browser profile does not match the persisted browser profile"
            )
        source_kind = meta["source_kind"]
        src_fixture_root = meta.get("fixture_root")
        src_cookie_store = meta.get("cookie_store")
        src_os_name = meta.get("os_name")
        src_browser_profile = persisted_browser_profile
        # An explicit --include-all-domains overrides the persisted choice; else the
        # persisted ``google_only`` is honored.
        google_only = (
            False if include_all_domains else bool(meta.get("google_only", True))
        )

    accounts, selected = _existing_accounts(dest_path)
    selected_authuser = selected.get("authuser") if isinstance(selected, dict) else None

    if source_kind == SOURCE_KIND_LIVE_BROWSER:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise ValidationError(
                "live browser account discovery cannot run from an active event loop"
            )
        if src_fixture_root is not None or src_cookie_store is not None:
            raise ValidationError(
                "live_browser auth source must not contain explicit source paths"
            )
        live_decryptor = _live_chromium_decryptor(
            canon,
            os_name=src_os_name,
            browser_profile=src_browser_profile,
            decryptor=decryptor,
            google_only=google_only,
            include_domains=include_domains,
            action="refresh",
            use_keychain=use_keychain,
        )
        canon, family, loc, extraction, cookies = _extract_live_and_filter(
            canon,
            os_name=src_os_name,
            browser_profile=src_browser_profile,
            google_only=google_only,
            include_domains=include_domains,
            decryptor=live_decryptor,
        )
        state = _browser_cookie_storage_state(cookies, google_only=google_only)
        discovered = asyncio.run(
            _auth.enumerate_accounts(
                _auth._cookie_jar_from_storage_state(state)
            )
        )
        live_accounts = [
            {
                "authuser": account.authuser,
                "email": account.email,
                "is_default": account.is_default,
            }
            for account in discovered
        ]
        stored_selected = selected
        selected = None
        if isinstance(stored_selected, dict):
            email = stored_selected.get("email")
            if isinstance(email, str) and email.strip():
                selected = select_account(live_accounts, email=email)
                if selected is None:
                    raise ValidationError("stored browser account was not found")
            else:
                authuser = stored_selected.get("authuser")
                if isinstance(authuser, int) and authuser >= 0:
                    selected = select_account(live_accounts, authuser=authuser)
            if selected is None:
                raise ValidationError("stored browser account was not found")
        else:
            selected = select_account(live_accounts)
        if selected is None:
            raise ValidationError("browser account discovery returned no accounts")
        refreshed_meta = build_auth_source_metadata(
            canon,
            source_kind=SOURCE_KIND_LIVE_BROWSER,
            os_name=src_os_name,
            browser_profile=_live_meta_browser_profile(loc, src_browser_profile),
            google_only=google_only,
            selected_authuser=selected["authuser"],
        )
        _cookies.save_storage_state(dest_path, state)
        _auth.write_account_metadata(
            dest_path, authuser=selected["authuser"], email=selected.get("email")
        )
        write_auth_source(meta_path, refreshed_meta)
        summary = {
            "browser": canon,
            "family": family,
            "os": loc.os,
            "browser_profile": loc.browser_profile,
            "source_path": None,
            "storage_path": str(dest_path),
            "refreshed": True,
            "written": True,
            "source_kind": SOURCE_KIND_LIVE_BROWSER,
            "from_persisted_source": not explicit,
            "google_only": google_only,
            "imported": len(cookies),
            "account": account_summary(live_accounts, selected),
            "decryptor": _oscreds.decryptor_capability(
                str(src_os_name or loc.os), canon
            ),
            **redacted_summary(
                cookies,
                _live_summary_blocked(
                    extraction,
                    family=family,
                    google_only=google_only,
                    include_domains=include_domains,
                ),
            ),
        }
        return summary

    # Explicit/offline sources still require an explicit path/root by now.
    if src_fixture_root is None and src_cookie_store is None:
        raise ValidationError(
            "refresh requires an explicit cookie store or fixture root (refusing to "
            "read a real browser store)"
        )

    canon, family, extraction, cookies = _extract_and_filter(
        canon,
        fixture_root=src_fixture_root,
        cookie_store=src_cookie_store,
        os_name=src_os_name,
        browser_profile=src_browser_profile,
        decryptor=decryptor,
        google_only=google_only,
        include_domains=include_domains,
    )

    state = _browser_cookie_storage_state(cookies, google_only=google_only)
    if accounts:
        state["accounts"] = accounts
    if selected is not None:
        state["account"] = selected
    _cookies.save_storage_state(dest_path, state)

    refreshed_meta = build_auth_source_metadata(
        canon,
        source_kind=source_kind,
        fixture_root=src_fixture_root,
        cookie_store=src_cookie_store,
        os_name=src_os_name,
        browser_profile=src_browser_profile,
        google_only=google_only,
        selected_authuser=selected_authuser,
    )
    write_auth_source(meta_path, refreshed_meta)

    summary = {
        "browser": canon,
        "family": family,
        "storage_path": str(dest_path),
        "refreshed": True,
        "written": True,
        "source_kind": source_kind,
        "from_persisted_source": not explicit,
        "google_only": google_only,
        "imported": len(cookies),
        "account": account_summary(accounts, selected),
        "decryptor": _oscreds.decryptor_capability(src_os_name, canon),
        **redacted_summary(cookies, extraction.blocked),
    }
    return summary


def inspect_cookie_store(
    browser: str,
    *,
    fixture_root: str | Path | None = None,
    cookie_store: str | Path | None = None,
    os_name: Optional[str] = None,
    browser_profile: Optional[str] = None,
    google_only: bool = False,
    include_domains: set[str] | None = None,
    decryptor=None,
) -> dict[str, Any]:
    """Return a redacted inspection of an explicit browser cookie store.

    Reports per-cookie *metadata* (name, domain, flags, expiry, value length/
    presence) and counts, but never a value or an encrypted blob.
    """

    canon = normalize_browser(browser)
    loc = resolve_cookie_store(
        canon,
        fixture_root=fixture_root,
        cookie_store=cookie_store,
        os_name=os_name,
        browser_profile=browser_profile,
    )
    base: dict[str, Any] = {
        "browser": canon,
        "family": browser_family(canon),
        "os": loc.os,
        "source_path": loc.path,
        "supported": loc.supported,
        "source_present": False,
        "reason": loc.reason,
    }
    if not loc.supported or loc.path is None:
        base.update(
            {
                "cookie_count": 0,
                "cookie_names": [],
                "cookies": [],
                "blocked_count": 0,
                "domains": [],
            }
        )
        return base

    extraction = extract_cookies(
        canon,
        fixture_root=fixture_root,
        cookie_store=cookie_store,
        os_name=os_name,
        browser_profile=browser_profile,
        decryptor=decryptor,
    )
    cookies = extraction.cookies
    blocked = extraction.blocked
    if google_only:
        cookies = [
            c
            for c in cookies
            if _is_included_cookie_domain(c["domain"], include_domains)
        ]
        blocked = [
            b
            for b in blocked
            if _is_included_cookie_domain(b.get("host", ""), include_domains)
        ]
    redacted = [_cookies.redact_cookie(c) for c in cookies]
    base.update(
        {
            "source_present": extraction.source_present,
            "cookie_count": len(cookies),
            "cookie_names": sorted(c["name"] for c in cookies),
            "cookies": redacted,
            "blocked_count": len(blocked),
            "blocked_names": sorted(b["name"] for b in blocked),
            "domains": sorted({c["domain"] for c in cookies if c["domain"]}),
        }
    )
    return base
