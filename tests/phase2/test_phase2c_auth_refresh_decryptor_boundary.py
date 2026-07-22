"""Phase 2C tests: explicit browser-cookie source metadata, offline ``auth
refresh``, and a redacted OS decryptor capability/status surface.

These tests target only the Phase 2C offline/fixture-backed slice that builds on
Phase 2A (profiles/auth/cookies) and Phase 2B (browser-cookie import):

  * ``login --browser-cookies`` persists *explicit* source metadata
    (``auth_source.json``) next to ``storage_state.json`` after a successful
    import — recording browser, source kind/path, OS, profile, google-only, and an
    optional selected ``authuser`` index, but never a cookie value, encrypted
    blob, token, raw email, or the ``account_email`` selector;
  * the metadata builder/validator whitelists keys and refuses secret-looking
    values, so a tampered file cannot smuggle a credential into refresh output;
  * ``auth refresh --browser-cookies BROWSER`` re-imports from an explicit
    ``--cookie-store``/``--fixture-root`` or, with neither, from the persisted
    explicit-source metadata; it preserves safe account metadata, never falls back
    to a real machine browser store, and refuses deterministically when no
    explicit source and no persisted metadata exist;
  * ``auth refresh`` *without* ``--browser-cookies`` stays a pre-profile-resolution
    exit-78 stub (the Phase 2A live-auth privacy regression is preserved);
  * encrypted Chromium cookies remain *blocked* across a refresh with no
    decryptor, and no encrypted bytes leak into any rendering;
  * :mod:`notebooklm.os_credentials` exposes deterministic, redacted decryptor
    capability/matrix helpers while the automatic decryptor stays unavailable and
    side-effect-free.

They never touch the network, a real Google/NotebookLM account, a real browser
cookie store, an OS keychain/DPAPI/secret store, or ``~/.notebooklm``. Every
filesystem read/write is confined to ``tmp_path``.
"""

from __future__ import annotations

import ast
import importlib
import importlib.abc
import json
import sqlite3
import types
from pathlib import Path

import pytest

import _phase0_constants as C  # noqa: E402  (placed on sys.path by tests/conftest.py)
import import_origin_audit  # noqa: E402

DENYLIST = set(C.DENYLISTED_RUNTIME_IMPORTS) | {"aiohttp", "urllib3"}

# Synthetic, non-real cookie values shaped like the real thing so the "no values
# ever leak" assertions are meaningful. None is a real credential and none embeds
# a contiguous real credential *format* literal (a Google OAuth access-token
# prefix, a refresh-token prefix, or a ``NAME=value`` cookie pair), so the
# repo-wide secret scanner stays clean.
SECRET_SID_A = "averysecretsidvalueAAAA1234567890abcdef"
SECRET_SID_B = "averysecretsidvalueBBBB0987654321zyxwvu"
SECRET_PSIDTS = "sidtsRotatingSyntheticTokenValue000111222333"
SECRET_HSID = "hsidSyntheticValue99887766554433221100"
ENC_PLAINTEXT = "decryptedSecure1PSIDsyntheticValue0123456789ab"
# Encrypted-blob bytes: a Chromium "v10" prefix plus a clearly-non-secret byte
# run. If these bytes ever appeared in a diagnostic they would be obvious.
ENC_BLOB = b"v10" + bytes(range(48, 80))

# Account emails (never credentials; the secret scanner does not flag plain
# emails). Used to prove metadata never records an email/selector.
EMAIL_DEFAULT = "alice@example.com"
EMAIL_OTHER = "bob@work.example"

# Chromium stores expiry as microseconds since 1601-01-01; pick a fixed far-future
# Unix time and convert (avoids any wall-clock dependence in the suite).
_UNIX_FAR_FUTURE = 1893456000  # 2030-01-01T00:00:00Z
_CHROMIUM_EPOCH_OFFSET = 11644473600  # seconds 1601-01-01 -> 1970-01-01
CHROMIUM_EXPIRES_UTC = (_UNIX_FAR_FUTURE + _CHROMIUM_EPOCH_OFFSET) * 1_000_000


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
    """Import the Phase 2A/2B/2C modules from the checkout, guarding against any
    denylisted third-party runtime import at import time."""
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


def _poison_home(monkeypatch):
    """Make any real ``Path.home()`` access an immediate, loud failure."""
    monkeypatch.setattr(
        Path,
        "home",
        staticmethod(lambda: (_ for _ in ()).throw(AssertionError("real home access"))),
    )


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


def _rows(sid_value: str, *, extra: list[dict] | None = None) -> list[dict]:
    rows = [
        {
            "name": "SID",
            "value": sid_value,
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
        # A non-Google cookie the Google-only filter must drop on import/refresh.
        {
            "name": "tracker",
            "value": "noise123value456",
            "host": ".tracker.example",
            "secure": False,
            "http_only": False,
        },
    ]
    if extra:
        rows.extend(extra)
    return rows


def _accounts() -> list[dict]:
    return [
        {"authuser": 0, "email": EMAIL_DEFAULT, "is_default": True},
        {"authuser": 1, "email": EMAIL_OTHER, "is_default": False},
    ]


def _read_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _state_names(path: Path) -> set[str]:
    return {c["name"] for c in _read_json(path)["cookies"]}


def _state_value(path: Path, name: str) -> str:
    for c in _read_json(path)["cookies"]:
        if c["name"] == name:
            return c["value"]
    raise KeyError(name)


def _run(mods, capsys, argv):
    code = mods.cli.console(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


def _no_secrets(blob: str) -> None:
    for secret in (
        SECRET_SID_A,
        SECRET_SID_B,
        SECRET_PSIDTS,
        SECRET_HSID,
        ENC_PLAINTEXT,
    ):
        assert secret not in blob
    for email in (EMAIL_DEFAULT, EMAIL_OTHER):
        assert email not in blob


ALLOWED_META_KEYS = {
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
FORBIDDEN_META_KEYS = {
    "email",
    "account_email",
    "account",
    "accounts",
    "value",
    "cookies",
    "token",
    "secret",
    "password",
    "credential",
    "encrypted_value",
}


# --------------------------------------------------------------------------- #
# 1) ProfileStore.auth_source_path
# --------------------------------------------------------------------------- #


def test_auth_source_path_is_under_profile(mods, home):
    store = mods.profiles.ProfileStore(home)
    p = store.auth_source_path("work")
    assert Path(p).name == "auth_source.json"
    assert Path(p).parent == store.profile_dir("work")
    assert Path(p).is_relative_to(home)
    # It sits next to storage_state.json (same profile dir).
    assert Path(p).parent == Path(store.storage_state_path("work")).parent
    # path_info advertises it and keeps it under the home root.
    info = store.path_info("work")
    assert "auth_source" in info
    assert Path(info["auth_source"]).is_relative_to(home)


# --------------------------------------------------------------------------- #
# 2) Metadata build / validation (redacted, whitelisted, secret-refusing)
# --------------------------------------------------------------------------- #


def test_build_metadata_cookie_store(mods, tmp_path):
    bc = mods.browser_cookies
    store = tmp_path / "Cookies"
    meta = bc.build_auth_source_metadata(
        "chrome",
        source_kind=bc.SOURCE_KIND_COOKIE_STORE,
        cookie_store=store,
        os_name="macOS",
        browser_profile="Default",
        google_only=True,
    )
    assert meta["browser"] == "chrome"
    assert meta["family"] == "chromium"
    assert meta["source_kind"] == "cookie_store"
    assert meta["cookie_store"] == str(store)
    assert meta["fixture_root"] is None
    assert meta["os_name"] == "macOS"
    assert meta["google_only"] is True
    assert meta["selected_authuser"] is None
    assert set(meta) <= ALLOWED_META_KEYS
    assert not (set(meta) & FORBIDDEN_META_KEYS)


def test_build_metadata_requires_matching_path(mods):
    bc = mods.browser_cookies
    with pytest.raises(mods.errors.ValidationError):
        bc.build_auth_source_metadata("chrome", source_kind=bc.SOURCE_KIND_COOKIE_STORE)
    with pytest.raises(mods.errors.ValidationError):
        bc.build_auth_source_metadata("chrome", source_kind=bc.SOURCE_KIND_FIXTURE_ROOT)
    with pytest.raises(mods.errors.ValidationError):
        bc.build_auth_source_metadata(
            "chrome", source_kind="live-machine", cookie_store="/x/Cookies"
        )


def test_validate_metadata_rejects_unexpected_keys_and_secrets(mods, tmp_path):
    bc = mods.browser_cookies
    good = bc.build_auth_source_metadata(
        "chrome",
        source_kind=bc.SOURCE_KIND_COOKIE_STORE,
        cookie_store=tmp_path / "Cookies",
    )
    # An email/selector key is not in the whitelist -> rejected.
    with pytest.raises(mods.errors.ValidationError):
        bc.validate_auth_source_metadata({**good, "account_email": EMAIL_OTHER})
    with pytest.raises(mods.errors.ValidationError):
        bc.validate_auth_source_metadata({**good, "email": EMAIL_DEFAULT})
    # A secret-looking value in an allowed field is rejected too.
    with pytest.raises(mods.errors.ValidationError):
        bc.validate_auth_source_metadata({**good, "browser_profile": SECRET_PSIDTS})
    # A bogus source_kind is rejected.
    with pytest.raises(mods.errors.ValidationError):
        bc.validate_auth_source_metadata({**good, "source_kind": "live"})
    # selected_authuser must be a non-negative int (or None).
    with pytest.raises(mods.errors.ValidationError):
        bc.validate_auth_source_metadata({**good, "selected_authuser": -3})


def test_metadata_round_trip_through_disk(mods, tmp_path):
    bc = mods.browser_cookies
    meta_path = tmp_path / "prof" / "auth_source.json"
    meta = bc.build_auth_source_metadata(
        "firefox",
        source_kind=bc.SOURCE_KIND_FIXTURE_ROOT,
        fixture_root=tmp_path / "ffroot",
        os_name="Ubuntu-LTS-Linux",
    )
    bc.write_auth_source(meta_path, meta)
    assert bc.read_auth_source(meta_path) == meta
    assert bc.read_auth_source(tmp_path / "absent.json") is None


# --------------------------------------------------------------------------- #
# 3) login persists explicit source metadata (redacted)
# --------------------------------------------------------------------------- #


def test_login_persists_cookie_store_metadata(mods, home, capsys, tmp_path):
    s = str(home)
    store = tmp_path / "Cookies"
    _build_chromium_db(store, _rows(SECRET_SID_A))
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
    _no_secrets(out)

    meta_path = mods.profiles.ProfileStore(home).auth_source_path("default")
    meta = _read_json(meta_path)
    assert meta["source_kind"] == "cookie_store"
    assert meta["cookie_store"] == str(store)
    assert meta["browser"] == "chrome"
    assert meta["google_only"] is True
    assert set(meta) <= ALLOWED_META_KEYS
    assert not (set(meta) & FORBIDDEN_META_KEYS)
    _no_secrets(json.dumps(meta))


def test_login_persists_fixture_root_metadata(mods, home, capsys, tmp_path):
    s = str(home)
    root = tmp_path / "fixroot"
    cookies = root.joinpath(
        "Library", "Application Support", "Google", "Chrome", "Default", "Cookies"
    )
    _build_chromium_db(cookies, _rows(SECRET_SID_A))
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
    meta = _read_json(mods.profiles.ProfileStore(home).auth_source_path("default"))
    assert meta["source_kind"] == "fixture_root"
    assert meta["fixture_root"] == str(root)
    assert meta["cookie_store"] is None
    assert meta["os_name"] == "macOS"


def test_login_metadata_records_authuser_not_email(mods, home, capsys, tmp_path):
    s = str(home)
    store = tmp_path / "Cookies"
    _build_chromium_db(store, _rows(SECRET_SID_A))
    accounts_file = tmp_path / "accounts.json"
    accounts_file.write_text(json.dumps(_accounts()), encoding="utf-8")
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
            "--accounts-file",
            str(accounts_file),
            "--account-email",
            EMAIL_OTHER,
            "--json",
        ],
    )
    assert code == 0
    assert EMAIL_OTHER not in out and EMAIL_DEFAULT not in out

    meta = _read_json(mods.profiles.ProfileStore(home).auth_source_path("default"))
    # The selected account is recorded only as a safe integer index.
    assert meta["selected_authuser"] == 1
    blob = json.dumps(meta)
    assert EMAIL_OTHER not in blob and EMAIL_DEFAULT not in blob
    assert "account_email" not in meta and "email" not in meta


def test_login_import_unchanged_when_no_meta_path(mods, tmp_path):
    # The Phase 2B import API still works without writing metadata.
    bc = mods.browser_cookies
    store = tmp_path / "Cookies"
    _build_chromium_db(store, _rows(SECRET_SID_A))
    dest = tmp_path / "p" / "storage_state.json"
    summary = bc.import_to_storage_state("chrome", dest_path=dest, cookie_store=store)
    assert summary["has_required_cookies"] is True
    assert {"SID", "__Secure-1PSIDTS", "HSID"} <= _state_names(dest)
    assert "tracker" not in _state_names(dest)


# --------------------------------------------------------------------------- #
# 4) auth refresh --browser-cookies from an explicit source
# --------------------------------------------------------------------------- #


def test_refresh_explicit_cookie_store_updates_cookies(mods, home, capsys, tmp_path):
    s = str(home)
    store_v1 = tmp_path / "v1" / "Cookies"
    store_v2 = tmp_path / "v2" / "Cookies"
    _build_chromium_db(store_v1, _rows(SECRET_SID_A))
    _build_chromium_db(
        store_v2,
        _rows(
            SECRET_SID_B,
            extra=[
                {
                    "name": "SSID",
                    "value": "ssidSyntheticValue1234567890abcd",
                    "host": ".google.com",
                    "secure": True,
                    "http_only": True,
                },
            ],
        ),
    )
    mods.profiles.ProfileStore(home).create_profile("default")
    state_path = mods.profiles.ProfileStore(home).storage_state_path("default")

    _run(
        mods,
        capsys,
        [
            "--storage",
            s,
            "login",
            "--browser-cookies",
            "chrome",
            "--cookie-store",
            str(store_v1),
            "--json",
        ],
    )
    assert _state_value(state_path, "SID") == SECRET_SID_A

    code, out, _ = _run(
        mods,
        capsys,
        [
            "--storage",
            s,
            "auth",
            "refresh",
            "--browser-cookies",
            "chrome",
            "--cookie-store",
            str(store_v2),
            "--json",
        ],
    )
    assert code == 0
    data = json.loads(out)
    assert data["refreshed"] is True
    assert data["source_kind"] == "cookie_store"
    assert data["browser"] == "chrome"
    assert data["has_required_cookies"] is True
    _no_secrets(out)
    # storage_state now reflects the new source: SID rotated, SSID appeared.
    assert _state_value(state_path, "SID") == SECRET_SID_B
    assert "SSID" in _state_names(state_path)


def test_refresh_include_domains_filters_optional_domains_like_upstream(
    mods, home, capsys, tmp_path
):
    s = str(home)
    store_v1 = tmp_path / "v1" / "Cookies"
    store_v2 = tmp_path / "v2" / "Cookies"
    _build_chromium_db(store_v1, _rows(SECRET_SID_A))
    _build_chromium_db(
        store_v2,
        _rows(
            SECRET_SID_B,
            extra=[
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
            ],
        ),
    )
    mods.profiles.ProfileStore(home).create_profile("default")
    state_path = mods.profiles.ProfileStore(home).storage_state_path("default")
    _run(
        mods,
        capsys,
        [
            "--storage",
            s,
            "login",
            "--browser-cookies",
            "chrome",
            "--cookie-store",
            str(store_v1),
            "--json",
        ],
    )

    code, out, err = _run(
        mods,
        capsys,
        [
            "--storage",
            s,
            "auth",
            "refresh",
            "--browser-cookies",
            "chrome",
            "--cookie-store",
            str(store_v2),
            "--include-domains",
            "youtube",
            "--json",
        ],
    )
    assert code == 0, err
    assert json.loads(out)["has_required_cookies"] is True
    assert _state_names(state_path) == {"SID", "__Secure-1PSIDTS", "HSID", "YSC"}
    assert "youtube-cookie-value" not in out
    assert "mail-cookie-value" not in out


def test_refresh_fixture_root_cli(mods, home, capsys, tmp_path):
    s = str(home)
    root = tmp_path / "fixroot"
    cookies = root.joinpath(
        "Library", "Application Support", "Google", "Chrome", "Default", "Cookies"
    )
    _build_chromium_db(cookies, _rows(SECRET_SID_A))
    mods.profiles.ProfileStore(home).create_profile("default")

    code, out, _ = _run(
        mods,
        capsys,
        [
            "--storage",
            s,
            "auth",
            "refresh",
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
    data = json.loads(out)
    assert data["refreshed"] is True
    assert data["source_kind"] == "fixture_root"
    assert data["has_required_cookies"] is True


# --------------------------------------------------------------------------- #
# 5) auth refresh from persisted metadata (no explicit source)
# --------------------------------------------------------------------------- #


def test_refresh_from_persisted_metadata(mods, home, capsys, tmp_path):
    s = str(home)
    store = tmp_path / "Cookies"
    _build_chromium_db(store, _rows(SECRET_SID_A))
    mods.profiles.ProfileStore(home).create_profile("default")
    state_path = mods.profiles.ProfileStore(home).storage_state_path("default")

    # login persists the explicit source metadata.
    _run(
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
    assert _state_value(state_path, "SID") == SECRET_SID_A

    # The source content rotates at the SAME path; refresh must re-read it.
    store.unlink()
    _build_chromium_db(store, _rows(SECRET_SID_B))

    code, out, _ = _run(
        mods,
        capsys,
        [
            "--storage",
            s,
            "auth",
            "refresh",
            "--browser-cookies",
            "chrome",
            "--json",
        ],
    )
    assert code == 0
    data = json.loads(out)
    assert data["refreshed"] is True
    assert data["from_persisted_source"] is True
    assert data["source_kind"] == "cookie_store"
    _no_secrets(out)
    assert _state_value(state_path, "SID") == SECRET_SID_B


def test_refresh_without_source_or_metadata_refuses(mods, home, capsys, monkeypatch):
    # No persisted metadata and no explicit source -> deterministic refusal, and
    # the real machine home must never be consulted to "discover" a store.
    _poison_home(monkeypatch)
    mods.profiles.ProfileStore(home).create_profile("default")
    code, _, err = _run(
        mods,
        capsys,
        [
            "--storage",
            str(home),
            "auth",
            "refresh",
            "--browser-cookies",
            "chrome",
        ],
    )
    assert code == 64  # ValidationError / EX_USAGE — not a live read, not a crash
    low = err.lower()
    assert "persist" in low or "explicit" in low or "source" in low


def test_refresh_browser_mismatch_with_metadata_refuses(mods, home, capsys, tmp_path):
    s = str(home)
    store = tmp_path / "Cookies"
    _build_chromium_db(store, _rows(SECRET_SID_A))
    mods.profiles.ProfileStore(home).create_profile("default")
    _run(
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
    # Persisted source is chrome; refreshing firefox from metadata must refuse
    # rather than silently use the chrome source or read a live firefox store.
    code, _, err = _run(
        mods,
        capsys,
        [
            "--storage",
            s,
            "auth",
            "refresh",
            "--browser-cookies",
            "firefox",
        ],
    )
    assert code == 64


# --------------------------------------------------------------------------- #
# 6) auth refresh WITHOUT --browser-cookies is Phase 2G network keepalive
# --------------------------------------------------------------------------- #


def _phase2g_storage_state() -> dict:
    return {
        "cookies": [
            {
                "name": "SID",
                "value": SECRET_SID_A,
                "domain": ".google.com",
                "path": "/",
                "secure": True,
            },
            {
                "name": "__Secure-1PSIDTS",
                "value": SECRET_PSIDTS,
                "domain": ".google.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
            },
        ],
        "origins": [],
    }


def _install_phase2g_fake_transport(mods, monkeypatch):
    def fake_post(url, **_kwargs):
        return mods.auth._http_std.Response(
            status=200,
            url=url,
            headers={
                "set-cookie": "__Secure-1PSIDTS="
                + SECRET_SID_B
                + "; Domain=.google.com; Path=/; Secure; HttpOnly"
            },
            body=b"[]",
        )

    def fake_get(url, **_kwargs):
        return mods.auth._http_std.Response(
            status=200,
            url=url,
            headers={},
            body=b'<script>{"SNlM0e":"csrfSyntheticPhase2GValue","FdrFJe":"sessionSyntheticPhase2GValue"}</script>',
        )

    monkeypatch.setattr(mods.auth, "_default_post", fake_post)
    monkeypatch.setattr(mods.auth, "_default_get", fake_get)


def test_refresh_without_browser_cookies_uses_stored_profile_network_path(
    mods, capsys, home, monkeypatch
):
    store = mods.profiles.ProfileStore(home)
    store.create_profile("default")
    state_path = store.storage_state_path("default")
    mods.cookies.save_storage_state(state_path, _phase2g_storage_state())
    import os as _os
    import time as _time
    mt = _time.time() - 120
    _os.utime(state_path, (mt, mt))
    _install_phase2g_fake_transport(mods, monkeypatch)

    code, out, err = _run(
        mods, capsys, ["--storage", str(home), "auth", "refresh", "--json"]
    )
    assert code == 0, err
    data = json.loads(out)
    assert data["token_fetch_ok"] is True
    assert _state_value(state_path, "__Secure-1PSIDTS") == SECRET_SID_B
    _no_secrets(out)
    assert "csrfSyntheticPhase2GValue" not in out
    assert "sessionSyntheticPhase2GValue" not in out


def test_refresh_network_path_respects_explicit_storage_without_default_home(
    mods, capsys, monkeypatch, home
):
    store = mods.profiles.ProfileStore(home)
    store.create_profile("default")
    mods.cookies.save_storage_state(
        store.storage_state_path("default"), _phase2g_storage_state()
    )
    _install_phase2g_fake_transport(mods, monkeypatch)
    _poison_home(monkeypatch)

    code, out, err = _run(
        mods, capsys, ["--storage", str(home), "auth", "refresh", "--json"]
    )
    assert code == 0, err
    assert json.loads(out)["ok"] is True
    assert "csrfSyntheticPhase2GValue" not in out
    assert "sessionSyntheticPhase2GValue" not in out


# --------------------------------------------------------------------------- #
# 7) refresh preserves safe account metadata; never leaks email
# --------------------------------------------------------------------------- #


def test_refresh_preserves_accounts(mods, home, capsys, tmp_path):
    s = str(home)
    store = tmp_path / "Cookies"
    _build_chromium_db(store, _rows(SECRET_SID_A))
    accounts_file = tmp_path / "accounts.json"
    accounts_file.write_text(json.dumps(_accounts()), encoding="utf-8")
    mods.profiles.ProfileStore(home).create_profile("default")
    state_path = mods.profiles.ProfileStore(home).storage_state_path("default")

    _run(
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
            "--accounts-file",
            str(accounts_file),
            "--account-email",
            EMAIL_OTHER,
            "--json",
        ],
    )
    assert _read_json(state_path)["account"]["authuser"] == 1

    store.unlink()
    _build_chromium_db(store, _rows(SECRET_SID_B))
    code, out, _ = _run(
        mods,
        capsys,
        [
            "--storage",
            s,
            "auth",
            "refresh",
            "--browser-cookies",
            "chrome",
            "--json",
        ],
    )
    assert code == 0
    data = json.loads(out)
    # The selected account survives the refresh, surfaced only as an index.
    assert data["account"]["selected_authuser"] == 1
    assert EMAIL_OTHER not in out and EMAIL_DEFAULT not in out
    state = _read_json(state_path)
    assert state["account"]["authuser"] == 1
    assert {a["authuser"] for a in state["accounts"]} == {0, 1}
    assert _state_value(state_path, "SID") == SECRET_SID_B


# --------------------------------------------------------------------------- #
# 8) encrypted Chromium cookies stay blocked across a refresh (no leak)
# --------------------------------------------------------------------------- #


def test_refresh_keeps_encrypted_blocked_without_decryptor(
    mods, home, capsys, tmp_path
):
    s = str(home)
    store = tmp_path / "Cookies"
    _build_chromium_db(
        store,
        _rows(
            SECRET_SID_A,
            extra=[
                {
                    "name": "__Secure-3PSID",
                    "value": "",
                    "encrypted_value": ENC_BLOB,
                    "host": ".google.com",
                    "secure": True,
                    "http_only": True,
                },
            ],
        ),
    )
    mods.profiles.ProfileStore(home).create_profile("default")
    state_path = mods.profiles.ProfileStore(home).storage_state_path("default")

    _run(
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
    code, out, _ = _run(
        mods,
        capsys,
        [
            "--storage",
            s,
            "auth",
            "refresh",
            "--browser-cookies",
            "chrome",
            "--json",
        ],
    )
    assert code == 0
    data = json.loads(out)
    assert data["blocked_count"] >= 1
    assert "__Secure-3PSID" in data["blocked_names"]
    # The encrypted cookie never lands in storage_state with a value.
    assert "__Secure-3PSID" not in _state_names(state_path)
    # Neither the encrypted bytes nor a fabricated plaintext leak anywhere.
    blob = out + state_path.read_text(encoding="utf-8")
    assert ENC_BLOB.hex() not in blob
    assert ENC_BLOB.decode("latin-1") not in blob
    assert ENC_PLAINTEXT not in blob


# --------------------------------------------------------------------------- #
# 9) refresh_browser_cookies unit behavior (direct, no CLI)
# --------------------------------------------------------------------------- #


def test_refresh_unit_requires_explicit_or_metadata(mods, tmp_path, monkeypatch):
    _poison_home(monkeypatch)
    bc = mods.browser_cookies
    dest = tmp_path / "storage_state.json"
    meta = tmp_path / "auth_source.json"
    with pytest.raises(mods.errors.ValidationError):
        bc.refresh_browser_cookies("chrome", dest_path=dest, meta_path=meta)


def test_refresh_unit_include_all_domains_overrides(mods, tmp_path):
    bc = mods.browser_cookies
    store = tmp_path / "Cookies"
    _build_chromium_db(store, _rows(SECRET_SID_A))
    dest = tmp_path / "storage_state.json"
    meta = tmp_path / "auth_source.json"
    summary = bc.refresh_browser_cookies(
        "chrome",
        dest_path=dest,
        meta_path=meta,
        cookie_store=store,
        include_all_domains=True,
    )
    assert summary["refreshed"] is True
    assert "tracker" in _state_names(dest)
    # Refresh persists/updates the metadata, recording the all-domains choice.
    persisted = bc.read_auth_source(meta)
    assert persisted["google_only"] is False
    assert persisted["source_kind"] == "cookie_store"


# --------------------------------------------------------------------------- #
# 10) OS decryptor capability / matrix (deterministic, redacted, no I/O)
# --------------------------------------------------------------------------- #


def test_decryptor_status_still_unavailable(mods):
    oc = mods.os_credentials
    for os_name in ("macOS", "Ubuntu-LTS-Linux", "Windows-11"):
        assert oc.resolve_decryptor(os_name, "chrome") is None
        status = oc.decryptor_status(os_name, "chrome")
        assert status["available"] is False
        assert status["reason"]


def test_decryptor_capability_encrypted_browsers(mods):
    oc = mods.os_credentials
    for browser in ("chrome", "brave", "edge", "arc", "vivaldi", "opera", "opera-gx"):
        cap = oc.decryptor_capability("macOS", browser)
        assert cap["automatic_available"] is False
        assert cap["requires_decryptor"] is True
        assert cap["reasons"]
        assert cap["os_key_backend"]
        _no_secrets(json.dumps(cap))


def test_decryptor_capability_plaintext_browsers(mods):
    oc = mods.os_credentials
    for browser in ("firefox", "safari"):
        cap = oc.decryptor_capability("macOS", browser)
        assert cap["automatic_available"] is False
        assert cap["requires_decryptor"] is False
        reason_blob = " ".join(cap["reasons"]).lower()
        assert "plaintext" in reason_blob or "plain text" in reason_blob


def test_decryptor_matrix_deterministic_and_redacted(mods, monkeypatch):
    oc = mods.os_credentials
    # Capability/matrix helpers must be pure: no real home/keychain access.
    _poison_home(monkeypatch)
    m1 = oc.decryptor_matrix()
    m2 = oc.decryptor_matrix()
    assert m1 == m2  # deterministic
    assert m1, "matrix is non-empty"
    assert all(row["automatic_available"] is False for row in m1)
    # Covers every documented OS x cookie browser pair.
    pairs = {(row["os"], row["browser"]) for row in m1}
    assert len(pairs) == len(m1)  # no duplicate cells
    assert ("macOS", "chrome") in pairs
    _no_secrets(json.dumps(m1))


def test_os_credentials_browser_sets_match_browser_cookies(mods):
    oc = mods.os_credentials
    bc = mods.browser_cookies
    # Drift guard: the encrypted/plaintext partition must mirror browser_cookies.
    assert set(oc.ENCRYPTED_COOKIE_BROWSERS) == set(bc.CHROMIUM_FAMILY)
    assert set(oc.PLAINTEXT_COOKIE_BROWSERS) == {bc.FIREFOX, bc.SAFARI}
    assert set(oc.COOKIE_BROWSERS) == set(bc.COOKIE_BROWSERS)


# --------------------------------------------------------------------------- #
# 11) Boundary / live-source audit for the Phase 2C surface
# --------------------------------------------------------------------------- #


def test_phase2c_modules_have_no_denylisted_imports(repo_root):
    violations = import_origin_audit.audit(roots=("notebooklm",))
    assert violations == []


def test_browser_and_decryptor_source_have_no_unscoped_live_discovery(repo_root):
    # Phase 2E-A permits Path.home only inside the explicit live Firefox resolver.
    # Everything else in browser-cookie/decryptor code must remain free of ambient
    # home/env discovery primitives.
    forbidden_text = ("expanduser", "os.environ", "os.getenv", "getenv(")
    allowed_home_functions = {"resolve_live_cookie_store"}
    for name in ("browser_cookies.py", "os_credentials.py"):
        src = (repo_root / "notebooklm" / name).read_text(encoding="utf-8")
        for token in forbidden_text:
            assert token not in src, f"{name} uses live-discovery primitive {token!r}"
        tree = ast.parse(src, filename=name)

        class LiveDiscoveryVisitor(ast.NodeVisitor):
            def __init__(self):
                self.function_stack = []
                self.violations = []

            def visit_FunctionDef(self, node):
                self.function_stack.append(node.name)
                self.generic_visit(node)
                self.function_stack.pop()

            def visit_AsyncFunctionDef(self, node):
                self.function_stack.append(node.name)
                self.generic_visit(node)
                self.function_stack.pop()

            def visit_Attribute(self, node):
                if node.attr in {"expanduser", "environ", "getenv"}:
                    self.violations.append(
                        (
                            node.attr,
                            self.function_stack[-1] if self.function_stack else None,
                        )
                    )
                if node.attr == "home":
                    func = self.function_stack[-1] if self.function_stack else None
                    if (
                        name != "browser_cookies.py"
                        or func not in allowed_home_functions
                    ):
                        self.violations.append((node.attr, func))
                self.generic_visit(node)

        visitor = LiveDiscoveryVisitor()
        visitor.visit(tree)
        assert visitor.violations == [], (
            f"{name} live-discovery violations: {visitor.violations}"
        )


def test_phase2c_end_to_end_no_secret_or_email_leak(mods, home, capsys, tmp_path):
    s = str(home)
    store = tmp_path / "Cookies"
    _build_chromium_db(
        store,
        _rows(
            SECRET_SID_A,
            extra=[
                {
                    "name": "__Secure-3PSID",
                    "value": "",
                    "encrypted_value": ENC_BLOB,
                    "host": ".google.com",
                    "secure": True,
                    "http_only": True,
                },
            ],
        ),
    )
    accounts_file = tmp_path / "accounts.json"
    accounts_file.write_text(json.dumps(_accounts()), encoding="utf-8")
    mods.profiles.ProfileStore(home).create_profile("default")

    flows = (
        [
            "login",
            "--browser-cookies",
            "chrome",
            "--cookie-store",
            str(store),
            "--accounts-file",
            str(accounts_file),
            "--account-email",
            EMAIL_OTHER,
            "--json",
        ],
        ["auth", "refresh", "--browser-cookies", "chrome", "--json"],
        [
            "auth",
            "refresh",
            "--browser-cookies",
            "chrome",
            "--cookie-store",
            str(store),
            "--json",
        ],
    )
    for argv in flows:
        _, out, err = _run(mods, capsys, ["--storage", s, *argv])
        combined = out + err
        _no_secrets(combined)
        assert ENC_BLOB.hex() not in combined
    # The persisted metadata file itself never carries a secret or an email.
    meta = mods.profiles.ProfileStore(home).auth_source_path("default")
    _no_secrets(meta.read_text(encoding="utf-8"))
