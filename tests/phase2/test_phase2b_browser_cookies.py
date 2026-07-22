"""Phase 2B browser-cookie import foundation tests.

These tests target only the Phase 2B offline/fixture-backed slice:

  * the generated auth-matrix constants (cookie browsers, OS rows, import paths)
    are mirrored in code and cross-checked against ``compat/auth_matrix.json``;
  * deterministic browser/profile cookie-store *path resolution* from an explicit
    fixture root only — with no explicit root/store, a redacted unsupported result
    (the real machine, the OS keychain, and ``~`` are never inspected);
  * fixture-backed Chromium-family SQLite extraction (stdlib ``sqlite3``) covering
    plaintext ``value`` and encrypted ``encrypted_value`` columns, where encrypted
    rows route through an explicit decryptor callback and are *blocked* (never
    byte-exposed) when no decryptor is supplied;
  * fixture-backed Firefox ``moz_cookies`` extraction;
  * a stdlib Safari ``binarycookies`` parser exercised against a synthetic blob;
  * account selection/filtering by email/authuser that never prints emails;
  * importing an explicit fixture store into a profile ``storage_state.json`` and
    redacted ``auth inspect`` over an explicit browser store;
  * CLI wiring for ``login --browser-cookies`` and ``auth inspect`` that keeps the
    Phase 2A surface intact and refuses to live-read without an explicit store.

They never touch the network, a real Google/NotebookLM account, a real browser
cookie store, an OS keychain/DPAPI/secret-store, or ``~/.notebooklm``. Every
filesystem read/write is confined to ``tmp_path``.
"""

from __future__ import annotations

import importlib
import importlib.abc
import json
import sqlite3
import struct
import types
from pathlib import Path

import pytest

import _phase0_constants as C  # noqa: E402  (placed on sys.path by tests/conftest.py)
import import_origin_audit  # noqa: E402

DENYLIST = set(C.DENYLISTED_RUNTIME_IMPORTS) | {"aiohttp", "urllib3"}

# Synthetic, non-real cookie/token values shaped like the real thing so the
# "no values ever leak" assertions are meaningful. None is a real credential and
# none embeds a contiguous real credential *format* literal (for example, a
# Google OAuth access-token prefix or a ``NAME=value`` cookie pair), so the
# repo-wide secret scanner stays clean.
SECRET_SID = "averysecretsidvalue1234567890ABCDEF"
SECRET_PSIDTS = "sidtsCjEBSecretRotatingTokenValue000111222333"
SECRET_HSID = "hsidSyntheticValue99887766554433221100"
ENC_PLAINTEXT = "decryptedSecure1PSIDvalue0123456789abcdef"
# Encrypted-blob bytes: a Chromium "v10" prefix plus a clearly-non-secret-name
# byte run. If these bytes ever appeared in a diagnostic they would be obvious.
ENC_BLOB = b"v10" + bytes(range(48, 80))

# Chromium stores expiry as microseconds since 1601-01-01; pick a fixed far
# future Unix time and convert (avoids any wall-clock dependence in the suite).
_UNIX_FAR_FUTURE = 1893456000  # 2030-01-01T00:00:00Z
_CHROMIUM_EPOCH_OFFSET = 11644473600  # seconds 1601-01-01 -> 1970-01-01
_MAC_EPOCH_OFFSET = 978307200  # seconds 1970-01-01 -> 2001-01-01
CHROMIUM_EXPIRES_UTC = (_UNIX_FAR_FUTURE + _CHROMIUM_EPOCH_OFFSET) * 1_000_000
SAFARI_EXPIRY_ABS = float(_UNIX_FAR_FUTURE - _MAC_EPOCH_OFFSET)


# --------------------------------------------------------------------------- #
# Module import fixture (guards against denylisted third-party imports)
# --------------------------------------------------------------------------- #


class _DenyThirdPartyFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):  # noqa: D401
        if fullname.split(".", 1)[0] in DENYLIST:
            raise AssertionError(f"denylisted runtime import attempted: {fullname}")
        return None


@pytest.fixture
def mods(repo_root, monkeypatch):
    """Import the Phase 2B modules (and their Phase 2A deps) from the checkout,
    guarding against any denylisted third-party runtime import at import time."""
    monkeypatch.syspath_prepend(str(repo_root))
    import sys

    finder = _DenyThirdPartyFinder()
    sys.meta_path.insert(0, finder)
    try:
        ns = types.SimpleNamespace(
            browser_cookies=importlib.import_module("notebooklm.browser_cookies"),
            os_credentials=importlib.import_module("notebooklm.os_credentials"),
            cookies=importlib.import_module("notebooklm.cookies"),
            profiles=importlib.import_module("notebooklm.profiles"),
            auth=importlib.import_module("notebooklm.auth"),
            cli=importlib.import_module("notebooklm.cli"),
            errors=importlib.import_module("notebooklm.errors"),
        )
    finally:
        sys.meta_path.remove(finder)
    return ns


@pytest.fixture
def home(tmp_path) -> Path:
    return tmp_path / "nlm-home"


# --------------------------------------------------------------------------- #
# Fixture builders (synthetic cookie stores; never a real browser store)
# --------------------------------------------------------------------------- #


def _build_chromium_db(path: Path, rows: list[dict]) -> None:
    """Create a fixture Chromium ``Cookies`` SQLite DB with the upstream shape."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    try:
        con.execute(
            """
            CREATE TABLE cookies (
                creation_utc INTEGER NOT NULL,
                host_key TEXT NOT NULL,
                name TEXT NOT NULL,
                value TEXT NOT NULL,
                path TEXT NOT NULL,
                expires_utc INTEGER NOT NULL,
                is_secure INTEGER NOT NULL,
                is_httponly INTEGER NOT NULL,
                encrypted_value BLOB DEFAULT '',
                samesite INTEGER NOT NULL DEFAULT -1,
                is_persistent INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        for i, r in enumerate(rows):
            con.execute(
                "INSERT INTO cookies (creation_utc, host_key, name, value, path, "
                "expires_utc, is_secure, is_httponly, encrypted_value, samesite, "
                "is_persistent) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    i + 1,
                    r.get("host", ".google.com"),
                    r["name"],
                    r.get("value", ""),
                    r.get("path", "/"),
                    r.get("expires_utc", CHROMIUM_EXPIRES_UTC),
                    int(r.get("secure", True)),
                    int(r.get("http_only", False)),
                    r.get("encrypted_value", b""),
                    r.get("samesite", -1),
                    1,
                ),
            )
        con.commit()
    finally:
        con.close()


def _build_firefox_db(path: Path, rows: list[dict]) -> None:
    """Create a fixture Firefox ``cookies.sqlite`` with the ``moz_cookies`` shape."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    try:
        con.execute(
            """
            CREATE TABLE moz_cookies (
                id INTEGER PRIMARY KEY,
                host TEXT,
                name TEXT,
                value TEXT,
                path TEXT,
                expiry INTEGER,
                isSecure INTEGER,
                isHttpOnly INTEGER,
                sameSite INTEGER
            )
            """
        )
        for i, r in enumerate(rows):
            con.execute(
                "INSERT INTO moz_cookies (id, host, name, value, path, expiry, "
                "isSecure, isHttpOnly, sameSite) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    i + 1,
                    r.get("host", ".google.com"),
                    r["name"],
                    r.get("value", ""),
                    r.get("path", "/"),
                    r.get("expires_unix", _UNIX_FAR_FUTURE),
                    int(r.get("secure", True)),
                    int(r.get("http_only", False)),
                    r.get("samesite", 0),
                ),
            )
        con.commit()
    finally:
        con.close()


def _build_binarycookies(cookies: list[dict]) -> bytes:
    """Encode a minimal but valid Safari ``Cookies.binarycookies`` blob.

    Exact inverse of ``browser_cookies.parse_binarycookies`` so the parser is
    exercised against a real-format fixture (no real Safari store is touched).
    """

    def _cookie_record(c: dict) -> bytes:
        url = c.get("host", ".google.com").encode() + b"\x00"
        name = c["name"].encode() + b"\x00"
        path = c.get("path", "/").encode() + b"\x00"
        value = c.get("value", "").encode() + b"\x00"
        header_len = 56  # 8*uint32 + end(8) + expiry(8) + creation(8)
        url_off = header_len
        name_off = url_off + len(url)
        path_off = name_off + len(name)
        value_off = path_off + len(path)
        size = value_off + len(value)
        flags = (0x1 if c.get("secure") else 0) | (0x4 if c.get("http_only") else 0)
        rec = struct.pack(
            "<IIIIIIII",
            size,
            0,
            flags,
            0,
            url_off,
            name_off,
            path_off,
            value_off,
        )
        rec += struct.pack("<Q", 0)  # end-of-header marker
        rec += struct.pack("<d", c.get("expiry_abs", SAFARI_EXPIRY_ABS))
        rec += struct.pack("<d", c.get("expiry_abs", SAFARI_EXPIRY_ABS))
        rec += url + name + path + value
        assert len(rec) == size
        return rec

    records = [_cookie_record(c) for c in cookies]
    n = len(records)
    header = b"\x00\x00\x01\x00" + struct.pack("<I", n)
    offsets_start = 4 + 4 + 4 * n + 4  # page header + count + offsets + footer
    offsets = b""
    cursor = offsets_start
    for rec in records:
        offsets += struct.pack("<I", cursor)
        cursor += len(rec)
    page = header + offsets + struct.pack("<I", 0) + b"".join(records)

    out = b"cook" + struct.pack(">I", 1) + struct.pack(">I", len(page)) + page
    out += struct.pack(">Q", 0x071720050000004B)
    return out


def _chromium_rows():
    return [
        {
            "name": "SID",
            "value": SECRET_SID,
            "host": ".google.com",
            "secure": True,
            "http_only": False,
        },
        {
            "name": "__Secure-1PSIDTS",
            "value": SECRET_PSIDTS,
            "host": ".google.com",
            "secure": True,
            "http_only": True,
        },
        {
            "name": "HSID",
            "value": SECRET_HSID,
            "host": ".google.com",
            "secure": True,
            "http_only": True,
        },
        # A non-Google cookie that the Google-only filter must drop on import.
        {
            "name": "tracker",
            "value": "noise123value456",
            "host": ".tracker.example",
            "secure": False,
            "http_only": False,
        },
    ]


# --------------------------------------------------------------------------- #
# 1) Generated auth-matrix constants are mirrored and cross-checked
# --------------------------------------------------------------------------- #


def test_matrix_constants_match_auth_matrix_json(mods, repo_root):
    bc = mods.browser_cookies
    matrix = json.loads(
        (repo_root / "compat" / "auth_matrix.json").read_text(encoding="utf-8")
    )
    counts = matrix["counts"]
    src = matrix["sources_from_upstream"]

    assert set(bc.COOKIE_BROWSERS) == set(counts["cookie_browsers"])
    assert len(bc.COOKIE_BROWSERS) == 10
    assert set(bc.OS_ROWS) == set(counts["os_rows"])
    assert set(bc.CHROMIUM_FAMILY) == set(src["chromium_family_cookie_browsers"])
    assert bc.FIREFOX in bc.COOKIE_BROWSERS and bc.SAFARI in bc.COOKIE_BROWSERS

    # import paths derived from the cookie matrix rows.
    rows = matrix["browser_cookie_import_matrix"]
    assert set(bc.COOKIE_IMPORT_PATHS) == {r["path"] for r in rows}

    # OS-specific cookie-store browser keys mirror the upstream-sourced mapping.
    for os_key, browsers in src["os_cookie_store_path_keys"].items():
        assert set(bc.OS_COOKIE_STORE_BROWSERS[os_key]) == set(browsers)


def test_normalize_browser_and_family(mods):
    bc = mods.browser_cookies
    assert bc.normalize_browser("Chrome") == "chrome"
    assert bc.normalize_browser("msedge") == "edge"
    assert bc.normalize_browser("opera_gx") == "opera-gx"
    assert bc.browser_family("brave") == "chromium"
    assert bc.browser_family("firefox") == "firefox"
    assert bc.browser_family("safari") == "safari"
    with pytest.raises(mods.errors.ValidationError):
        bc.normalize_browser("internet-explorer")


# --------------------------------------------------------------------------- #
# 2) Deterministic path resolution from an explicit fixture root only
# --------------------------------------------------------------------------- #


def test_resolve_requires_explicit_root_or_store(mods, monkeypatch):
    bc = mods.browser_cookies
    # Path.home must never be consulted when no explicit root/store is given.
    monkeypatch.setattr(
        Path,
        "home",
        staticmethod(lambda: (_ for _ in ()).throw(AssertionError("real home access"))),
    )
    loc = bc.resolve_cookie_store("chrome")
    assert loc.supported is False
    assert loc.path is None
    assert loc.reason  # a redacted, explanatory reason
    blob = json.dumps(loc.as_dict())
    assert "/".join(("", "Users", "")) not in blob
    assert "/".join(("", "home", "")) not in blob


@pytest.mark.parametrize(
    "os_name, browser, tail",
    [
        (
            "macOS",
            "chrome",
            "Library/Application Support/Google/Chrome/Default/Cookies",
        ),
        ("Ubuntu-LTS-Linux", "chrome", ".config/google-chrome/Default/Cookies"),
        (
            "Windows-11",
            "chrome",
            "AppData/Local/Google/Chrome/User Data/Default/Network/Cookies",
        ),
        (
            "Windows-11",
            "opera",
            "AppData/Roaming/Opera Software/Opera Stable/Default/Network/Cookies",
        ),
        (
            "Windows-11",
            "opera-gx",
            "AppData/Roaming/Opera Software/Opera GX Stable/Default/Network/Cookies",
        ),
        (
            "macOS",
            "brave",
            "Library/Application Support/BraveSoftware/Brave-Browser/Default/Cookies",
        ),
    ],
)
def test_resolve_chromium_paths_under_fixture_root(
    mods, tmp_path, os_name, browser, tail
):
    bc = mods.browser_cookies
    root = tmp_path / "fixroot"
    loc = bc.resolve_cookie_store(browser, fixture_root=root, os_name=os_name)
    assert loc.supported is True
    assert Path(loc.path) == root.joinpath(*tail.split("/"))
    assert Path(loc.path).is_relative_to(root)
    assert loc.exists is False  # nothing created yet


def test_resolve_explicit_cookie_store_wins(mods, tmp_path):
    bc = mods.browser_cookies
    store = tmp_path / "Cookies"
    _build_chromium_db(store, _chromium_rows())
    loc = bc.resolve_cookie_store("chrome", cookie_store=store)
    assert Path(loc.path) == store
    assert loc.exists is True and loc.supported is True


def test_resolve_safari_unsupported_off_macos(mods, tmp_path):
    bc = mods.browser_cookies
    loc = bc.resolve_cookie_store("safari", fixture_root=tmp_path, os_name="Windows-11")
    assert loc.supported is False
    assert (
        "safari" in (loc.reason or "").lower() or "macos" in (loc.reason or "").lower()
    )


# --------------------------------------------------------------------------- #
# 3) Chromium SQLite extraction: plaintext, encrypted+decryptor, blocked
# --------------------------------------------------------------------------- #


def test_extract_chromium_plaintext(mods, tmp_path):
    bc = mods.browser_cookies
    store = tmp_path / "Cookies"
    _build_chromium_db(store, _chromium_rows())
    res = bc.extract_chromium(store)
    by_name = {c["name"]: c for c in res.cookies}
    assert {"SID", "__Secure-1PSIDTS", "HSID", "tracker"} <= set(by_name)
    assert by_name["SID"]["value"] == SECRET_SID
    assert by_name["__Secure-1PSIDTS"]["http_only"] is True
    # Chromium epoch converted to Unix seconds.
    assert by_name["SID"]["expires"] == _UNIX_FAR_FUTURE
    assert res.blocked == []


def test_extract_chromium_copies_store_when_direct_open_fails(
    mods, tmp_path, monkeypatch
):
    bc = mods.browser_cookies
    store = tmp_path / "Cookies"
    _build_chromium_db(store, _chromium_rows())
    original_open = bc._open_sqlite_readonly

    def flaky_open(path):
        if Path(path) == store:
            return None
        return original_open(path)

    monkeypatch.setattr(bc, "_open_sqlite_readonly", flaky_open)
    res = bc.extract_chromium(store)

    by_name = {c["name"]: c for c in res.cookies}
    assert by_name["SID"]["value"] == SECRET_SID
    assert by_name["__Secure-1PSIDTS"]["value"] == SECRET_PSIDTS
    assert res.source_path == str(store)


def test_extract_chromium_encrypted_routes_through_decryptor(mods, tmp_path):
    bc = mods.browser_cookies
    store = tmp_path / "Cookies"
    _build_chromium_db(
        store,
        [
            {
                "name": "__Secure-1PSID",
                "value": "",
                "encrypted_value": ENC_BLOB,
                "host": ".google.com",
                "secure": True,
                "http_only": True,
            },
        ],
    )

    calls = []

    def decryptor(blob, *, host, name):
        calls.append((bytes(blob), host, name))
        assert bytes(blob) == ENC_BLOB
        return ENC_PLAINTEXT

    res = bc.extract_chromium(store, decryptor=decryptor)
    assert calls and calls[0][2] == "__Secure-1PSID"
    by_name = {c["name"]: c for c in res.cookies}
    assert by_name["__Secure-1PSID"]["value"] == ENC_PLAINTEXT
    assert res.blocked == []


def test_extract_chromium_encrypted_blocked_without_decryptor(mods, tmp_path):
    bc = mods.browser_cookies
    store = tmp_path / "Cookies"
    _build_chromium_db(
        store,
        [
            {
                "name": "__Secure-1PSID",
                "value": "",
                "encrypted_value": ENC_BLOB,
                "host": ".google.com",
                "secure": True,
                "http_only": True,
            },
        ],
    )
    res = bc.extract_chromium(store)  # no decryptor
    assert res.cookies == []  # nothing extracted with a value
    assert len(res.blocked) == 1
    blocked = res.blocked[0]
    assert blocked["name"] == "__Secure-1PSID"
    assert "encrypt" in blocked["reason"].lower()
    # The encrypted bytes must never be exposed in any rendering.
    blob = json.dumps(res.redacted_summary())
    assert ENC_BLOB.hex() not in blob
    assert ENC_BLOB.decode("latin-1") not in blob
    assert ENC_PLAINTEXT not in blob


def test_extract_chromium_missing_store(mods, tmp_path):
    bc = mods.browser_cookies
    res = bc.extract_chromium(tmp_path / "nope" / "Cookies")
    assert res.cookies == []
    assert res.source_present is False


# --------------------------------------------------------------------------- #
# 4) Firefox moz_cookies extraction (plaintext fixture)
# --------------------------------------------------------------------------- #


def test_extract_firefox_plaintext(mods, tmp_path):
    bc = mods.browser_cookies
    store = tmp_path / "cookies.sqlite"
    _build_firefox_db(
        store,
        [
            {"name": "SID", "value": SECRET_SID, "host": ".google.com", "secure": True},
            {
                "name": "__Secure-1PSIDTS",
                "value": SECRET_PSIDTS,
                "host": ".google.com",
                "secure": True,
                "http_only": True,
            },
        ],
    )
    res = bc.extract_firefox(store)
    by_name = {c["name"]: c for c in res.cookies}
    assert set(by_name) == {"SID", "__Secure-1PSIDTS"}
    assert by_name["SID"]["value"] == SECRET_SID
    assert by_name["SID"]["expires"] == _UNIX_FAR_FUTURE
    assert res.blocked == []


def test_resolve_firefox_profile_via_profiles_ini(mods, tmp_path):
    bc = mods.browser_cookies
    root = tmp_path / "ffroot"
    # macOS firefox data dir under the synthetic home.
    data_dir = root / "Library" / "Application Support" / "Firefox"
    prof = "abcd1234.default-release"
    (data_dir / "Profiles" / prof).mkdir(parents=True)
    (data_dir / "profiles.ini").write_text(
        "[Profile0]\nName=default-release\nIsRelative=1\n"
        f"Path=Profiles/{prof}\nDefault=1\n",
        encoding="utf-8",
    )
    store = data_dir / "Profiles" / prof / "cookies.sqlite"
    _build_firefox_db(store, [{"name": "SID", "value": SECRET_SID}])
    loc = bc.resolve_cookie_store("firefox", fixture_root=root, os_name="macOS")
    assert Path(loc.path) == store
    assert loc.exists is True


# --------------------------------------------------------------------------- #
# 5) Safari binarycookies parser (synthetic fixture; honest about scope)
# --------------------------------------------------------------------------- #


def test_parse_binarycookies_round_trip(mods):
    bc = mods.browser_cookies
    blob = _build_binarycookies(
        [
            {
                "name": "SID",
                "value": SECRET_SID,
                "host": ".google.com",
                "secure": True,
                "http_only": False,
            },
            {
                "name": "__Secure-1PSIDTS",
                "value": SECRET_PSIDTS,
                "host": ".google.com",
                "secure": True,
                "http_only": True,
            },
        ]
    )
    parsed = bc.parse_binarycookies(blob)
    by_name = {c["name"]: c for c in parsed}
    assert set(by_name) == {"SID", "__Secure-1PSIDTS"}
    assert by_name["SID"]["value"] == SECRET_SID
    assert by_name["__Secure-1PSIDTS"]["http_only"] is True


def test_parse_binarycookies_rejects_bad_magic(mods):
    bc = mods.browser_cookies
    with pytest.raises(mods.errors.ValidationError):
        bc.parse_binarycookies(b"NOTACOOKIEFILE")


def test_parse_binarycookies_rejects_excessive_header_counts(mods):
    bc = mods.browser_cookies
    too_many_pages = b"cook" + struct.pack(">I", 1025) + (b"\x00\x00\x00\x00" * 1025)
    with pytest.raises(mods.errors.ValidationError, match="too many pages"):
        bc.parse_binarycookies(too_many_pages)

    page = b"\x00\x00\x00\x00" + struct.pack("<I", 4097) + (b"\x00\x00\x00\x00" * 4097)
    too_many_cookies = (
        b"cook" + struct.pack(">I", 1) + struct.pack(">I", len(page)) + page
    )
    with pytest.raises(mods.errors.ValidationError, match="too many cookies"):
        bc.parse_binarycookies(too_many_cookies)


def test_extract_safari_from_fixture(mods, tmp_path):
    bc = mods.browser_cookies
    store = tmp_path / "Cookies.binarycookies"
    store.write_bytes(
        _build_binarycookies(
            [
                {
                    "name": "SID",
                    "value": SECRET_SID,
                    "host": ".google.com",
                    "secure": True,
                },
            ]
        )
    )
    res = bc.extract_safari(store)
    by_name = {c["name"]: c for c in res.cookies}
    assert by_name["SID"]["value"] == SECRET_SID
    assert by_name["SID"]["expires"] == _UNIX_FAR_FUTURE


# --------------------------------------------------------------------------- #
# 6) Account selection / filtering (never prints emails)
# --------------------------------------------------------------------------- #


def _accounts():
    return [
        {"authuser": 0, "email": "alice@example.com", "is_default": True},
        {"authuser": 1, "email": "bob@work.example", "is_default": False},
    ]


def test_select_account_by_email_and_authuser(mods):
    bc = mods.browser_cookies
    accts = _accounts()
    by_email = bc.select_account(accts, email="bob@work.example")
    assert by_email["authuser"] == 1
    by_user = bc.select_account(accts, authuser=0)
    assert by_user["email"] == "alice@example.com"
    assert bc.select_account(accts, email="nobody@nowhere.example") is None
    # default selection when nothing specified
    assert bc.select_account(accts)["authuser"] == 0


def test_account_summary_is_redacted(mods):
    bc = mods.browser_cookies
    accts = _accounts()
    summary = bc.account_summary(accts, selected=bc.select_account(accts, authuser=1))
    blob = json.dumps(summary)
    assert "alice@example.com" not in blob and "bob@work.example" not in blob
    assert summary["count"] == 2
    assert summary["email_present_count"] == 2
    assert summary["selected_authuser"] == 1


# --------------------------------------------------------------------------- #
# 7) Import an explicit fixture store into a profile storage_state.json
# --------------------------------------------------------------------------- #


def test_import_to_storage_state_writes_and_redacts(mods, tmp_path):
    bc = mods.browser_cookies
    store = tmp_path / "Cookies"
    _build_chromium_db(store, _chromium_rows())
    dest = tmp_path / "profile" / "storage_state.json"

    summary = bc.import_to_storage_state("chrome", dest_path=dest, cookie_store=store)

    # File written with the Google auth cookies only (tracker.example dropped).
    state = json.loads(dest.read_text(encoding="utf-8"))
    names = {c["name"] for c in state["cookies"]}
    assert {"SID", "__Secure-1PSIDTS", "HSID"} <= names
    assert "tracker" not in names

    # Summary is redacted: names/counts yes, cookie values never.
    blob = json.dumps(summary)
    for secret in (SECRET_SID, SECRET_PSIDTS, SECRET_HSID):
        assert secret not in blob
    assert summary["has_required_cookies"] is True
    assert summary["imported"] >= 3
    assert "SID" in summary["cookie_names"]


def test_import_all_domains_keeps_noogle(mods, tmp_path):
    bc = mods.browser_cookies
    store = tmp_path / "Cookies"
    _build_chromium_db(store, _chromium_rows())
    dest = tmp_path / "p2" / "storage_state.json"
    bc.import_to_storage_state(
        "chrome", dest_path=dest, cookie_store=store, google_only=False
    )
    names = {c["name"] for c in json.loads(dest.read_text())["cookies"]}
    assert "tracker" in names


def test_import_missing_store_raises(mods, tmp_path):
    bc = mods.browser_cookies
    with pytest.raises(mods.errors.ValidationError):
        bc.import_to_storage_state(
            "chrome",
            dest_path=tmp_path / "s.json",
            cookie_store=tmp_path / "absent" / "Cookies",
        )


def test_import_with_accounts_selection(mods, tmp_path):
    bc = mods.browser_cookies
    store = tmp_path / "Cookies"
    _build_chromium_db(store, _chromium_rows())
    dest = tmp_path / "p3" / "storage_state.json"
    summary = bc.import_to_storage_state(
        "chrome",
        dest_path=dest,
        cookie_store=store,
        accounts=_accounts(),
        account_email="bob@work.example",
    )
    # The chosen account is recorded in storage (owned file) but never printed.
    state = json.loads(dest.read_text(encoding="utf-8"))
    assert state["account"]["authuser"] == 1
    assert "bob@work.example" not in json.dumps(summary)
    assert summary["account"]["selected_authuser"] == 1


# --------------------------------------------------------------------------- #
# 8) Redacted inspection over an explicit browser store
# --------------------------------------------------------------------------- #


def test_inspect_cookie_store_is_redacted(mods, tmp_path):
    bc = mods.browser_cookies
    store = tmp_path / "Cookies"
    _build_chromium_db(store, _chromium_rows())
    report = bc.inspect_cookie_store("chrome", cookie_store=store)
    blob = json.dumps(report)
    for secret in (SECRET_SID, SECRET_PSIDTS, SECRET_HSID):
        assert secret not in blob
    assert report["cookie_count"] >= 3
    assert "SID" in report["cookie_names"]
    # No raw value field anywhere in the per-cookie metadata.
    for c in report["cookies"]:
        assert "value" not in c


# --------------------------------------------------------------------------- #
# 9) OS-credential decryptor boundary (no keychain/DPAPI/secret-store access)
# --------------------------------------------------------------------------- #


def test_os_credentials_decryptor_unavailable_in_phase(mods):
    oc = mods.os_credentials
    for os_name in ("macOS", "Ubuntu-LTS-Linux", "Windows-11"):
        assert oc.resolve_decryptor(os_name, "chrome") is None
        status = oc.decryptor_status(os_name, "chrome")
        assert status["available"] is False
        assert status["reason"]


# --------------------------------------------------------------------------- #
# 10) CLI wiring: login --browser-cookies + auth inspect (Phase 2A intact)
# --------------------------------------------------------------------------- #


def _run(mods, capsys, argv):
    code = mods.cli.console(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_cli_login_account_flags_require_browser_cookies_like_upstream(
    mods, home, capsys
):
    s = str(home)

    for flag_args in (
        ["--account", "redacted@example.test"],
        ["--profile-name", "work"],
        ["--all-accounts"],
        ["--update", "--all-accounts"],
    ):
        code, out, err = _run(mods, capsys, ["--storage", s, "login", *flag_args])
        assert code == 1
        assert (
            out
            == "Error: --account, --all-accounts, and --profile-name require --browser-cookies.\n"
        )
        assert err == ""

    code, out, err = _run(mods, capsys, ["--storage", s, "login", "--update"])
    assert code == 1
    assert out == "Error: --update only applies to --all-accounts.\n"
    assert err == ""


@pytest.mark.parametrize("argv", [["login"], ["login", "--browser-cookies", "chrome"]])
def test_cli_login_rejects_auth_json_before_login_paths_like_upstream(
    mods, home, capsys, monkeypatch, argv
):
    def boom(*_args, **_kwargs):
        raise AssertionError("login path must not run when NOTEBOOKLM_AUTH_JSON is set")

    monkeypatch.setenv("NOTEBOOKLM_AUTH_JSON", "{}")
    monkeypatch.setattr(mods.cli, "_run_interactive_login", boom)
    monkeypatch.setattr(mods.cli._bc, "import_to_storage_state", boom)

    code, out, err = _run(mods, capsys, ["--storage", str(home), *argv])

    assert code == 1
    assert err == ""
    assert (
        out
        == "Error: Cannot run 'login' when NOTEBOOKLM_AUTH_JSON is set.\n"
        "The NOTEBOOKLM_AUTH_JSON environment variable provides inline authentication,\n"
        "which conflicts with browser-based login that saves to a file.\n\n"
        "Either:\n"
        "  1. Unset NOTEBOOKLM_AUTH_JSON and run 'login' again\n"
        "  2. Continue using NOTEBOOKLM_AUTH_JSON for authentication\n"
    )


def test_cli_login_accepts_command_local_storage_like_upstream(
    mods, capsys, tmp_path
):
    source = tmp_path / "Cookies"
    dest = tmp_path / "custom-storage.json"
    _build_chromium_db(source, _chromium_rows())

    code, out, err = _run(
        mods,
        capsys,
        [
            "login",
            "--storage",
            str(dest),
            "--browser-cookies",
            "chrome",
            "--cookie-store",
            str(source),
            "--json",
        ],
    )

    assert code == 0, err
    assert json.loads(out)["storage_path"] == str(dest)
    assert dest.is_file()
    assert not (tmp_path / "profiles" / "default" / "storage_state.json").exists()


def test_cli_login_browser_cookie_unknown_browser_fails_like_upstream(
    mods, home, capsys, monkeypatch
):
    def boom(*_args, **_kwargs):
        raise AssertionError("unknown browser must fail before cookie import")

    monkeypatch.setattr(mods.cli._bc, "import_to_storage_state", boom)
    monkeypatch.setattr(mods.cli._bc, "import_live_browser_to_storage_state", boom)
    expected_message = (
        "Unknown browser: 'not-a-browser'\n"
        "Supported: arc, brave, chrome, chromium, edge, firefox, ie, "
        "librewolf, octo, opera, opera-gx, opera_gx, safari, vivaldi, zen"
    )

    code, out, err = _run(
        mods,
        capsys,
        ["--storage", str(home), "login", "--browser-cookies", "not-a-browser"],
    )

    assert code == 1
    assert out == expected_message + "\n"
    assert err == ""


def test_cli_login_browser_cookie_upstream_rookiepy_alias_fails_before_import(
    mods, home, capsys, monkeypatch
):
    def boom(*_args, **_kwargs):
        raise AssertionError("unsupported stdlib alias must fail before cookie import")

    monkeypatch.setattr(mods.cli._bc, "import_to_storage_state", boom)
    monkeypatch.setattr(mods.cli._bc, "import_live_browser_to_storage_state", boom)

    code, out, err = _run(
        mods,
        capsys,
        ["--storage", str(home), "login", "--browser-cookies", "librewolf"],
    )

    assert code == 1
    assert out.startswith("rookiepy is not installed.\n")
    assert err == ""


def test_cli_login_browser_cookie_conflicts_fail_before_cookie_access_like_upstream(
    mods, home, capsys, monkeypatch
):
    def boom(*_args, **_kwargs):
        raise AssertionError("cookie store must not be touched for flag conflicts")

    monkeypatch.setattr(mods.cli._bc, "import_to_storage_state", boom)
    monkeypatch.setattr(mods.cli._bc, "import_live_browser_to_storage_state", boom)

    cases = (
        (
            ["--browser-cookies", "chrome", "--all-accounts", "--account", "x@y.test"],
            "Error: --all-accounts cannot be combined with --account or --profile-name.\n",
        ),
        (
            ["--browser-cookies", "chrome", "--all-accounts", "--profile-name", "work"],
            "Error: --all-accounts cannot be combined with --account or --profile-name.\n",
        ),
        (
            ["--browser-cookies", "chrome", "--all-accounts"],
            "Error: --all-accounts writes one profile per account and cannot be combined with --storage.\n",
        ),
    )

    for flag_args, expected in cases:
        code, out, err = _run(mods, capsys, ["--storage", str(home), "login", *flag_args])
        assert code == 1
        assert out == expected
        assert err == ""


def test_cli_login_include_domains_filters_optional_domains_like_upstream(
    mods, home, capsys, tmp_path
):
    rows = [
        *_chromium_rows(),
        {
            "name": "YSC",
            "value": "youtube-cookie-value",
            "host": ".youtube.com",
            "secure": True,
            "http_only": True,
        },
        {
            "name": "MAIL",
            "value": "mail-cookie-value",
            "host": "mail.google.com",
            "secure": True,
            "http_only": True,
        },
    ]

    store = tmp_path / "Cookies"
    _build_chromium_db(store, rows)

    code, out, err = _run(
        mods,
        capsys,
        [
            "--storage",
            str(home),
            "login",
            "--browser-cookies",
            "chrome",
            "--cookie-store",
            str(store),
            "--json",
        ],
    )
    assert code == 0, err
    state_path = mods.profiles.ProfileStore(home).storage_state_path("default")
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert {c["name"] for c in saved["cookies"]} == {
        "SID",
        "__Secure-1PSIDTS",
        "HSID",
    }
    assert "youtube-cookie-value" not in out
    assert "mail-cookie-value" not in out

    code, out, err = _run(
        mods,
        capsys,
        [
            "--storage",
            str(home),
            "login",
            "--browser-cookies",
            "chrome",
            "--cookie-store",
            str(store),
            "--include-domains",
            "youtube,mail",
            "--json",
        ],
    )
    assert code == 0, err
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert {c["name"] for c in saved["cookies"]} == {
        "SID",
        "__Secure-1PSIDTS",
        "HSID",
        "YSC",
        "MAIL",
    }
    assert "noise123value456" not in out


def test_cli_login_include_domains_rejects_unknown_labels_before_cookie_access(
    mods, home, capsys, monkeypatch
):
    def boom(*_args, **_kwargs):
        raise AssertionError("cookie store must not be touched for invalid labels")

    monkeypatch.setattr(mods.cli._bc, "import_to_storage_state", boom)
    code, out, err = _run(
        mods,
        capsys,
        [
            "--storage",
            str(home),
            "login",
            "--browser-cookies",
            "chrome",
            "--include-domains",
            "calendar",
        ],
    )
    assert code == 2
    assert out == ""
    assert (
        err
        == "Error: Invalid value: unknown --include-domains label(s): calendar. Supported: all, docs, mail, myaccount, youtube.\n"
    )


def test_cli_login_browser_cookies_from_cookie_store(mods, home, capsys, tmp_path):
    s = str(home)
    store = tmp_path / "Cookies"
    _build_chromium_db(store, _chromium_rows())
    mods.profiles.ProfileStore(home).create_profile("default")

    code, out, _ = _run(
        mods,
        capsys,
        [
            "--storage",
            s,
            "login",
            "--browser-cookies",
            "chrome",
            "--cookie-store",
            str(store),
            "--json",
        ],
    )
    assert code == 0
    data = json.loads(out)
    assert data["has_required_cookies"] is True
    for secret in (SECRET_SID, SECRET_PSIDTS, SECRET_HSID):
        assert secret not in out
    # storage_state.json now exists for the profile and carries the auth cookies.
    state_path = mods.profiles.ProfileStore(home).storage_state_path("default")
    names = {c["name"] for c in json.loads(state_path.read_text())["cookies"]}
    assert {"SID", "__Secure-1PSIDTS"} <= names


def test_cli_login_browser_cookies_from_fixture_root(mods, home, capsys, tmp_path):
    s = str(home)
    root = tmp_path / "fixroot"
    store = root.joinpath(
        "Library", "Application Support", "Google", "Chrome", "Default", "Cookies"
    )
    _build_chromium_db(store, _chromium_rows())
    mods.profiles.ProfileStore(home).create_profile("default")

    code, out, _ = _run(
        mods,
        capsys,
        [
            "--storage",
            s,
            "login",
            "--browser-cookies",
            "chrome",
            "--fixture-root",
            str(root),
            "--os",
            "macOS",
            "--json",
        ],
    )
    assert code == 0
    assert json.loads(out)["has_required_cookies"] is True


def test_cli_login_without_browser_cookies_uses_interactive_path(
    mods, home, capsys, monkeypatch
):
    monkeypatch.setattr(
        mods.cli._il,
        "launch_browser_session",
        lambda browser, **kw: {
            "source_kind": "interactive_browser",
            "browser": browser,
            "debugging_host": "127.0.0.1",
            "debugging_port": kw["debugging_port"],
            "profile_prepared": True,
            "process_id": None,
            "url_opened": True,
        },
    )
    monkeypatch.setattr(
        mods.cli._il,
        "read_devtools_page_websocket_url",
        lambda port, **kw: "ws://127.0.0.1:9222/devtools/browser/session-id",
    )
    monkeypatch.setattr(
        mods.cli._il,
        "read_cdp_all_cookies",
        lambda ws: [
            {"name": "SID", "value": SECRET_SID, "domain": ".google.com", "path": "/"},
            {
                "name": "__Secure-1PSIDTS",
                "value": SECRET_PSIDTS,
                "domain": ".google.com",
                "path": "/",
            },
        ],
    )

    code, out, err = _run(mods, capsys, ["--storage", str(home), "login", "--json"])

    assert code == 0, err
    data = json.loads(out)
    assert data["source_kind"] == "interactive_browser"
    assert data["browser"] == "chromium"
    assert data["cookie_count"] == 2
    assert data["auth_source_written"] is False
    assert SECRET_SID not in out
    assert SECRET_PSIDTS not in out


def test_cli_login_browser_cookies_without_store_refuses_live_read(
    mods, home, capsys, monkeypatch
):
    # Refuses to read a real browser store; must not consult Path.home to do so.
    monkeypatch.setattr(
        Path,
        "home",
        staticmethod(lambda: (_ for _ in ()).throw(AssertionError("real home access"))),
    )
    code, _, err = _run(
        mods, capsys, ["--storage", str(home), "login", "--browser-cookies", "chrome"]
    )
    assert code == 78
    assert "later parity slice" in err.lower() or "explicit" in err.lower()


def test_cli_auth_inspect_browser_store_redacted(mods, home, capsys, tmp_path):
    s = str(home)
    store = tmp_path / "Cookies"
    _build_chromium_db(store, _chromium_rows())
    code, out, _ = _run(
        mods,
        capsys,
        [
            "--storage",
            s,
            "auth",
            "inspect",
            "--browser",
            "chrome",
            "--cookie-store",
            str(store),
            "--json",
        ],
    )
    assert code == 0
    data = json.loads(out)
    assert data["cookie_count"] >= 3
    for secret in (SECRET_SID, SECRET_PSIDTS, SECRET_HSID):
        assert secret not in out


def test_cli_auth_inspect_include_domains_filters_optional_domains_like_upstream(
    mods, home, capsys, tmp_path
):
    rows = [
        *_chromium_rows(),
        {
            "name": "YSC",
            "value": "youtube-cookie-value",
            "host": ".youtube.com",
            "secure": True,
            "http_only": True,
        },
        {
            "name": "MAIL",
            "value": "mail-cookie-value",
            "host": "mail.google.com",
            "secure": True,
            "http_only": True,
        },
    ]
    store = tmp_path / "Cookies"
    _build_chromium_db(store, rows)

    code, out, err = _run(
        mods,
        capsys,
        [
            "--storage",
            str(home),
            "auth",
            "inspect",
            "--browser",
            "chrome",
            "--cookie-store",
            str(store),
            "--json",
        ],
    )
    assert code == 0, err
    data = json.loads(out)
    assert set(data["cookie_names"]) == {"SID", "__Secure-1PSIDTS", "HSID"}

    code, out, err = _run(
        mods,
        capsys,
        [
            "--storage",
            str(home),
            "auth",
            "inspect",
            "--browser",
            "chrome",
            "--cookie-store",
            str(store),
            "--include-domains",
            "youtube",
            "--json",
        ],
    )
    assert code == 0, err
    data = json.loads(out)
    assert set(data["cookie_names"]) == {"SID", "__Secure-1PSIDTS", "HSID", "YSC"}
    assert "youtube-cookie-value" not in out
    assert "mail-cookie-value" not in out
    assert "noise123value456" not in out


def test_cli_auth_inspect_include_domains_rejects_unknown_labels_before_cookie_access(
    mods, home, capsys, monkeypatch
):
    def boom(*_args, **_kwargs):
        raise AssertionError("cookie store must not be touched for invalid labels")

    monkeypatch.setattr(mods.cli._bc, "inspect_cookie_store", boom)
    code, out, err = _run(
        mods,
        capsys,
        [
            "--storage",
            str(home),
            "auth",
            "inspect",
            "--browser",
            "chrome",
            "--cookie-store",
            str(home / "Cookies"),
            "--include-domains",
            "calendar",
        ],
    )
    assert code == 2
    assert out == ""
    assert (
        err
        == "Error: Invalid value: unknown --include-domains label(s): calendar. Supported: all, docs, mail, myaccount, youtube.\n"
    )


def test_cli_auth_inspect_accepts_verbose_flag_like_upstream(mods, home, capsys, tmp_path):
    store = tmp_path / "Cookies"
    _build_chromium_db(store, _chromium_rows())

    code, out, err = _run(
        mods,
        capsys,
        [
            "--storage",
            str(home),
            "auth",
            "inspect",
            "--browser",
            "chrome",
            "--cookie-store",
            str(store),
            "-v",
            "--json",
        ],
    )
    assert code == 0, err
    assert json.loads(out)["browser"] == "chrome"


def test_cli_auth_inspect_auto_json_reports_rookiepy_missing_like_upstream(
    mods, home, capsys, monkeypatch
):
    def boom(*_args, **_kwargs):
        raise AssertionError("auto discovery must fail before live store access")

    monkeypatch.setattr(mods.cli._bc, "inspect_live_cookie_store", boom)
    code, out, err = _run(
        mods,
        capsys,
        [
            "--storage",
            str(home),
            "auth",
            "inspect",
            "--browser",
            "auto",
            "--json",
        ],
    )
    assert code == 1
    assert err == ""
    data = json.loads(out)
    assert data["error"] is True
    assert data["code"] == "ROOKIEPY_NOT_INSTALLED"
    assert data["message"].startswith("rookiepy is not installed.")


def test_cli_auth_inspect_unknown_browser_reports_upstream_envelope(
    mods, home, capsys, monkeypatch
):
    def boom(*_args, **_kwargs):
        raise AssertionError("unknown browser must fail before live store access")

    monkeypatch.setattr(mods.cli._bc, "inspect_live_cookie_store", boom)
    code, out, err = _run(
        mods,
        capsys,
        [
            "--storage",
            str(home),
            "auth",
            "inspect",
            "--browser",
            "not-a-browser",
            "--json",
        ],
    )
    assert code == 1
    assert err == ""
    data = json.loads(out)
    expected_message = (
        "Unknown browser: 'not-a-browser'\n"
        "Supported: arc, brave, chrome, chromium, edge, firefox, ie, "
        "librewolf, octo, opera, opera-gx, opera_gx, safari, vivaldi, zen"
    )
    assert data == {
        "error": True,
        "code": "UNKNOWN_BROWSER",
        "message": expected_message,
        "browser": "not-a-browser",
        "supported": [
            "arc",
            "brave",
            "chrome",
            "chromium",
            "edge",
            "firefox",
            "ie",
            "librewolf",
            "octo",
            "opera",
            "opera-gx",
            "opera_gx",
            "safari",
            "vivaldi",
            "zen",
        ],
    }

    code, out, err = _run(
        mods,
        capsys,
        [
            "--storage",
            str(home),
            "auth",
            "inspect",
            "--browser",
            "not-a-browser",
        ],
    )
    assert code == 1
    assert out == expected_message + "\n"
    assert err == ""


def test_cli_auth_inspect_upstream_rookiepy_alias_fails_before_live_store(
    mods, home, capsys, monkeypatch
):
    def boom(*_args, **_kwargs):
        raise AssertionError("unsupported stdlib alias must fail before live store access")

    monkeypatch.setattr(mods.cli._bc, "inspect_live_cookie_store", boom)
    code, out, err = _run(
        mods,
        capsys,
        [
            "--storage",
            str(home),
            "auth",
            "inspect",
            "--browser",
            "librewolf",
            "--json",
        ],
    )
    assert code == 1
    assert err == ""
    data = json.loads(out)
    assert data["code"] == "ROOKIEPY_NOT_INSTALLED"
    assert data["message"].startswith("rookiepy is not installed.")


def test_cli_auth_inspect_defaults_to_browser_auto_like_upstream(
    mods, home, capsys, tmp_path
):
    s = str(home)
    store = mods.profiles.ProfileStore(home)
    store.create_profile("default")
    state = {
        "cookies": [
            {
                "name": "SID",
                "value": SECRET_SID,
                "domain": ".google.com",
                "path": "/",
                "secure": True,
                "httpOnly": False,
            },
        ],
        "accounts": [{"authuser": 0, "email": "alice@example.com", "is_default": True}],
    }
    mods.cookies.save_storage_state(store.storage_state_path("default"), state)
    code, out, _ = _run(mods, capsys, ["--storage", s, "auth", "inspect", "--json"])
    assert code == 1
    data = json.loads(out)
    assert data["code"] == "ROOKIEPY_NOT_INSTALLED"
    assert "alice@example.com" not in out


def test_cli_auth_inspect_unsupported_live_layout_refuses_before_home(
    mods, home, capsys, monkeypatch
):
    def boom():
        raise AssertionError(
            "Path.home must not be consulted for unsupported live layout"
        )

    monkeypatch.setattr(Path, "home", staticmethod(boom))
    code, _, err = _run(
        mods,
        capsys,
        [
            "--storage",
            str(home),
            "auth",
            "inspect",
            "--browser",
            "arc",
            "--os",
            "Ubuntu-LTS-Linux",
        ],
    )
    assert code != 0
    assert "no documented cookie store" in err.lower()


def test_cli_login_redaction_end_to_end(mods, home, capsys, tmp_path):
    s = str(home)
    store = tmp_path / "Cookies"
    _build_chromium_db(store, _chromium_rows())
    mods.profiles.ProfileStore(home).create_profile("default")
    for argv in (
        [
            "login",
            "--browser-cookies",
            "chrome",
            "--cookie-store",
            str(store),
            "--json",
        ],
        [
            "auth",
            "inspect",
            "--browser",
            "chrome",
            "--cookie-store",
            str(store),
            "--json",
        ],
    ):
        _, out, err = _run(mods, capsys, ["--storage", s, *argv])
        combined = out + err
        for secret in (SECRET_SID, SECRET_PSIDTS, SECRET_HSID):
            assert secret not in combined, f"secret leaked via {argv}"


# --------------------------------------------------------------------------- #
# 11) Boundary / import-origin audit for the new modules
# --------------------------------------------------------------------------- #


def test_phase2b_modules_exist_in_package(repo_root):
    pkg = repo_root / "notebooklm"
    for name in ("browser_cookies.py", "os_credentials.py"):
        assert (pkg / name).is_file(), f"missing Phase 2B module: notebooklm/{name}"


def test_phase2b_modules_have_no_denylisted_imports(repo_root):
    violations = import_origin_audit.audit(roots=("notebooklm",))
    assert violations == []
