"""Phase 2E-B tests: authorized live Safari browser-store discovery.

This narrow slice adds a *live* Safari ``Cookies.binarycookies`` lane alongside the
Phase 2E-A live Firefox lane. The committed suite stays hermetic: it models a live
Safari home under ``tmp_path`` (synthetic ``Cookies.binarycookies`` blobs only) and
monkeypatches ``Path.home()`` exclusively inside commands that explicitly request a
live Safari browser-cookie read. No test touches the real Safari store, the
network, the Keychain/DPAPI/Secret-Service, ``~/.notebooklm``, or operator-local config.

The synthetic ``binarycookies`` encoder is the exact inverse of
``browser_cookies.parse_binarycookies`` (inlined from the Phase 2B suite), so the
real stdlib parser is exercised against a real-format fixture without ever reading
a live store.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

# Synthetic, non-real cookie values shaped like the real thing so the "no values
# ever leak" assertions are meaningful. None is a real credential and none embeds a
# real credential *format* literal (no OAuth prefix, no ``NAME=value`` pair), so the
# repo secret scanner stays clean.
SECRET_SID = "phase2ebSafariSyntheticSidValue1234567890abcdef"
SECRET_PSIDTS = "phase2ebSafariSyntheticPsidtsValueabcdef1234567890"
SECRET_HSID = "phase2ebSafariSyntheticHsidValue0011223344556677"

_UNIX_FAR_FUTURE = 1893456000  # 2030-01-01T00:00:00Z (no wall-clock dependence)
_MAC_EPOCH_OFFSET = 978307200  # seconds 1970-01-01 -> 2001-01-01
SAFARI_EXPIRY_ABS = float(_UNIX_FAR_FUTURE - _MAC_EPOCH_OFFSET)

# The three live Safari cookie-file candidate locations, in resolver precedence.
_SAFARI_CANDIDATE_PARTS = {
    "loose": ("Library", "Cookies", "Cookies.binarycookies"),
    "container": (
        "Library",
        "Containers",
        "com.apple.Safari",
        "Data",
        "Library",
        "Cookies",
        "Cookies.binarycookies",
    ),
    "group": (
        "Library",
        "Group Containers",
        "group.com.apple.Safari",
        "Library",
        "Cookies",
        "Cookies.binarycookies",
    ),
}


@pytest.fixture
def home(tmp_path) -> Path:
    return tmp_path / "nlm-home"


@pytest.fixture
def live_home(tmp_path) -> Path:
    return tmp_path / "live-home"


@pytest.fixture
def mods(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))
    from notebooklm import auth

    async def discovered(_jar):
        return [auth.Account(0, "default@example.com", True)]

    monkeypatch.setattr(auth, "enumerate_accounts", discovered)
    import importlib
    import types

    return types.SimpleNamespace(
        browser_cookies=importlib.import_module("notebooklm.browser_cookies"),
        cli=importlib.import_module("notebooklm.cli"),
        profiles=importlib.import_module("notebooklm.profiles"),
        errors=importlib.import_module("notebooklm.errors"),
    )


# --------------------------------------------------------------------------- #
# Synthetic Safari binarycookies fixtures (never a real Safari store)
# --------------------------------------------------------------------------- #


def _build_binarycookies(cookies: list[dict]) -> bytes:
    """Encode a minimal but valid Safari ``Cookies.binarycookies`` blob.

    Exact inverse of ``browser_cookies.parse_binarycookies`` (inlined from the
    Phase 2B suite) so the parser is exercised against a real-format fixture.
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


def _safari_store_path(root: Path, candidate: str) -> Path:
    return root.joinpath(*_SAFARI_CANDIDATE_PARTS[candidate])


def _write_safari_store(
    root: Path, cookies: list[dict], *, candidate: str = "container"
) -> Path:
    path = _safari_store_path(root, candidate)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_build_binarycookies(cookies))
    return path


def _rows():
    return [
        {"name": "SID", "value": SECRET_SID, "host": ".google.com", "secure": True},
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
        {
            "name": "tracker",
            "value": "noiseValueNotGoogle",
            "host": ".tracker.example",
            "secure": False,
        },
    ]


def _poison_home(monkeypatch):
    monkeypatch.setattr(
        Path,
        "home",
        staticmethod(lambda: (_ for _ in ()).throw(AssertionError("real home access"))),
    )


def _run(mods, capsys, argv):
    code = mods.cli.console(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


# --------------------------------------------------------------------------- #
# 1) Resolver: macOS-only, candidate precedence, profile-less
# --------------------------------------------------------------------------- #


def test_resolve_live_safari_prefers_first_existing_candidate(mods, live_home):
    bc = mods.browser_cookies
    # All three present -> the loose Library/Cookies path (first candidate) wins.
    p_loose = _write_safari_store(live_home, _rows(), candidate="loose")
    _write_safari_store(live_home, _rows(), candidate="container")
    _write_safari_store(live_home, _rows(), candidate="group")

    loc = bc.resolve_live_cookie_store("safari", os_name="macOS", home=live_home)

    assert loc.supported is True
    assert loc.exists is True
    assert loc.family == "safari"
    assert Path(loc.path) == p_loose
    assert loc.browser_profile is None


def test_resolve_live_safari_skips_to_container_when_loose_absent(mods, live_home):
    bc = mods.browser_cookies
    p_container = _write_safari_store(live_home, _rows(), candidate="container")

    loc = bc.resolve_live_cookie_store("safari", os_name="macOS", home=live_home)

    assert loc.exists is True
    assert Path(loc.path) == p_container
    assert loc.browser_profile is None


def test_resolve_live_safari_falls_back_to_group_container(mods, live_home):
    bc = mods.browser_cookies
    p_group = _write_safari_store(live_home, _rows(), candidate="group")

    loc = bc.resolve_live_cookie_store("safari", os_name="macOS", home=live_home)

    assert loc.exists is True
    assert Path(loc.path) == p_group
    assert loc.browser_profile is None


def test_resolve_live_safari_absent_returns_first_candidate(mods, live_home):
    bc = mods.browser_cookies
    loc = bc.resolve_live_cookie_store("safari", os_name="macOS", home=live_home)

    assert loc.supported is True
    assert loc.exists is False
    assert Path(loc.path) == _safari_store_path(live_home, "loose")
    assert loc.browser_profile is None


def test_resolve_live_safari_ignores_browser_profile(mods, live_home):
    bc = mods.browser_cookies
    _write_safari_store(live_home, _rows(), candidate="container")

    loc = bc.resolve_live_cookie_store(
        "safari",
        os_name="macOS",
        home=live_home,
        browser_profile="../../synthetic-unsafe-profile",
    )

    assert loc.browser_profile is None
    assert "synthetic-unsafe-profile" not in (loc.path or "")


def test_resolve_live_safari_unsupported_off_macos_before_home(mods, monkeypatch):
    bc = mods.browser_cookies
    # An off-macOS resolve must return unsupported WITHOUT consulting Path.home.
    _poison_home(monkeypatch)

    loc = bc.resolve_live_cookie_store("safari", os_name="Windows-11")

    assert loc.supported is False
    assert loc.exists is False
    assert loc.path is None
    assert "macos" in (loc.reason or "").lower()


# --------------------------------------------------------------------------- #
# 2) Live login: pathless, redacted live_browser metadata
# --------------------------------------------------------------------------- #


def test_live_safari_login_persists_pathless_live_metadata(
    mods, home, live_home, capsys, monkeypatch
):
    _write_safari_store(live_home, _rows(), candidate="container")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: live_home))
    mods.profiles.ProfileStore(home).create_profile("default")

    code, out, err = _run(
        mods,
        capsys,
        [
            "--storage",
            str(home),
            "login",
            "--browser-cookies",
            "safari",
            "--json",
        ],
    )

    assert code == 0, err
    data = json.loads(out)
    assert data["source_kind"] == "live_browser"
    assert data["family"] == "safari"
    assert data["browser_profile"] is None
    assert data["has_required_cookies"] is True
    assert data["cookie_count"] == 3  # google-only filter drops the tracker cookie
    assert str(live_home) not in out
    for secret in (SECRET_SID, SECRET_PSIDTS, SECRET_HSID):
        assert secret not in out

    profile_store = mods.profiles.ProfileStore(home)
    state = json.loads(
        profile_store.storage_state_path("default").read_text(encoding="utf-8")
    )
    assert {"SID", "__Secure-1PSIDTS", "HSID"} <= {c["name"] for c in state["cookies"]}

    meta = json.loads(
        profile_store.auth_source_path("default").read_text(encoding="utf-8")
    )
    assert meta["source_kind"] == "live_browser"
    assert meta["browser"] == "safari"
    assert meta["cookie_store"] is None
    assert meta["fixture_root"] is None
    assert meta["browser_profile"] is None
    assert str(live_home) not in json.dumps(meta)


def test_live_safari_login_ignores_unsafe_browser_profile(
    mods, home, live_home, capsys, monkeypatch
):
    _write_safari_store(live_home, _rows(), candidate="container")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: live_home))
    mods.profiles.ProfileStore(home).create_profile("default")
    unsafe = "../../synthetic-unsafe-profile"

    code, out, err = _run(
        mods,
        capsys,
        [
            "--storage",
            str(home),
            "login",
            "--browser-cookies",
            "safari",
            "--browser-profile",
            unsafe,
            "--json",
        ],
    )

    assert code == 0, err
    data = json.loads(out)
    assert data["browser_profile"] is None
    assert data["source_kind"] == "live_browser"
    assert unsafe not in out

    meta = json.loads(
        mods.profiles.ProfileStore(home)
        .auth_source_path("default")
        .read_text(encoding="utf-8")
    )
    assert meta["browser_profile"] is None
    assert unsafe not in json.dumps(meta)


# --------------------------------------------------------------------------- #
# 3) Live inspect: redacted, pathless, profile-less
# --------------------------------------------------------------------------- #


def test_live_safari_inspect_is_redacted_and_pathless(
    mods, home, live_home, capsys, monkeypatch
):
    _write_safari_store(live_home, _rows(), candidate="container")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: live_home))

    async def discovered(jar):
        return [mods.browser_cookies._auth.Account(0, "safari@example.com", True)]

    monkeypatch.setattr(mods.browser_cookies._auth, "enumerate_accounts", discovered)

    code, out, err = _run(
        mods,
        capsys,
        [
            "--storage",
            str(home),
            "auth",
            "inspect",
            "--browser",
            "safari",
            "--json",
        ],
    )

    assert code == 0, err
    data = json.loads(out)
    assert data == {
        "browser": "safari",
        "accounts": [
            {
                "email": "safari@example.com",
                "is_default": True,
                "browser_profile": None,
            }
        ],
    }
    assert str(live_home) not in out
    for secret in (SECRET_SID, SECRET_PSIDTS, SECRET_HSID, "noiseValueNotGoogle"):
        assert secret not in out


# --------------------------------------------------------------------------- #
# 4) Live refresh: re-resolve from pathless live metadata
# --------------------------------------------------------------------------- #


def test_live_safari_refresh_reresolves_from_live_metadata(
    mods, home, live_home, capsys, monkeypatch
):
    _write_safari_store(live_home, _rows()[:2], candidate="container")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: live_home))
    mods.profiles.ProfileStore(home).create_profile("default")

    code, _, err = _run(
        mods,
        capsys,
        [
            "--storage",
            str(home),
            "login",
            "--browser-cookies",
            "safari",
            "--json",
        ],
    )
    assert code == 0, err

    # Safari stores are monolithic; rewrite with an extra google cookie to model a
    # store that gained a cookie since login.
    _write_safari_store(live_home, _rows()[:3], candidate="container")

    code, out, err = _run(
        mods,
        capsys,
        [
            "--storage",
            str(home),
            "auth",
            "refresh",
            "--browser-cookies",
            "safari",
            "--json",
        ],
    )

    assert code == 0, err
    data = json.loads(out)
    assert data["source_kind"] == "live_browser"
    assert data["family"] == "safari"
    assert data["from_persisted_source"] is True
    assert data["browser_profile"] is None
    assert data["has_required_cookies"] is True
    assert data["cookie_count"] == 3
    assert str(live_home) not in out
    for secret in (SECRET_SID, SECRET_PSIDTS, SECRET_HSID):
        assert secret not in out


def test_live_safari_read_permission_error_is_pathless(
    mods, home, live_home, capsys, monkeypatch
):
    store = _write_safari_store(live_home, _rows(), candidate="container")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: live_home))

    original_read_bytes = Path.read_bytes

    def denied(self):
        if self == store:
            raise PermissionError(f"denied secret path {store}")
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", denied)

    code, out, err = _run(
        mods,
        capsys,
        [
            "--storage",
            str(home),
            "auth",
            "inspect",
            "--browser",
            "safari",
            "--json",
        ],
    )

    assert code != 0
    assert str(live_home) not in out + err
    assert str(store) not in out + err
    assert "secret path" not in out + err
    assert "safari cookie store could not be read" in err.lower()


# --------------------------------------------------------------------------- #
# 5) Explicit source / non-live browsers never consult Path.home
# --------------------------------------------------------------------------- #


def test_explicit_cookie_store_still_avoids_path_home(
    mods, home, tmp_path, capsys, monkeypatch
):
    store = tmp_path / "Cookies.binarycookies"
    store.write_bytes(_build_binarycookies(_rows()))
    _poison_home(monkeypatch)
    mods.profiles.ProfileStore(home).create_profile("default")

    code, out, err = _run(
        mods,
        capsys,
        [
            "--storage",
            str(home),
            "login",
            "--browser-cookies",
            "safari",
            "--cookie-store",
            str(store),
            "--json",
        ],
    )

    assert code == 0, err
    assert json.loads(out)["source_kind"] == "cookie_store"


def test_non_firefox_safari_live_read_still_refuses_without_home_access(
    mods, home, capsys, monkeypatch
):
    _poison_home(monkeypatch)
    code, _, err = _run(
        mods,
        capsys,
        [
            "--storage",
            str(home),
            "login",
            "--browser-cookies",
            "chrome",
            "--json",
        ],
    )
    assert code == 78
    assert "explicit" in err.lower() or "later parity" in err.lower()
