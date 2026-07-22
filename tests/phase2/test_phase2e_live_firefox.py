"""Phase 2E-A tests: authorized live Firefox browser-store discovery.

The committed suite remains hermetic: it models a *live* Firefox home under
``tmp_path`` and monkeypatches ``Path.home()`` only inside commands that explicitly
request live Firefox browser-cookie reads. No test touches the real browser,
network, keychain/DPAPI/libsecret, or ``~/.notebooklm``.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

SECRET_SID = "phase2eSyntheticSidValue1234567890abcdef"
SECRET_PSIDTS = "phase2eSyntheticPsidtsValueabcdef1234567890"
SECRET_HSID = "phase2eSyntheticHsidValue0011223344556677"
_UNIX_FAR_FUTURE = 1893456000


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


def _build_firefox_db(path: Path, rows: list[dict]) -> None:
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
        for i, row in enumerate(rows, start=1):
            con.execute(
                "INSERT INTO moz_cookies (id, host, name, value, path, expiry, "
                "isSecure, isHttpOnly, sameSite) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    i,
                    row.get("host", ".google.com"),
                    row["name"],
                    row.get("value", ""),
                    row.get("path", "/"),
                    row.get("expires_unix", _UNIX_FAR_FUTURE),
                    int(row.get("secure", True)),
                    int(row.get("http_only", False)),
                    row.get("samesite", 0),
                ),
            )
        con.commit()
    finally:
        con.close()


def _append_firefox_cookie(path: Path, name: str, value: str) -> None:
    con = sqlite3.connect(str(path))
    try:
        next_id = con.execute(
            "SELECT COALESCE(MAX(id), 0) + 1 FROM moz_cookies"
        ).fetchone()[0]
        con.execute(
            "INSERT INTO moz_cookies (id, host, name, value, path, expiry, isSecure, isHttpOnly, sameSite) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (next_id, ".google.com", name, value, "/", _UNIX_FAR_FUTURE, 1, 1, 0),
        )
        con.commit()
    finally:
        con.close()


def _firefox_home(
    root: Path,
    *,
    profile: str = "abcd1234.default-release",
    install_default: bool = True,
) -> tuple[Path, Path]:
    data_dir = root / "Library" / "Application Support" / "Firefox"
    profile_dir = data_dir / "Profiles" / profile
    store = profile_dir / "cookies.sqlite"
    profile_dir.mkdir(parents=True, exist_ok=True)
    ini = "[Profile0]\nName=default-release\nIsRelative=1\nPath=Profiles/{profile}\nDefault=1\n".format(
        profile=profile
    )
    if install_default:
        ini += "[InstallABCDEF]\nDefault=Profiles/{profile}\nLocked=1\n".format(
            profile=profile
        )
    (data_dir / "profiles.ini").write_text(ini, encoding="utf-8")
    return data_dir, store


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


def _run(mods, capsys, argv):
    code = mods.cli.console(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_resolve_live_firefox_uses_profiles_ini_default(mods, live_home, monkeypatch):
    _, store = _firefox_home(live_home, profile="zz.default-release")
    _build_firefox_db(store, _rows())
    monkeypatch.setattr(Path, "home", staticmethod(lambda: live_home))

    loc = mods.browser_cookies.resolve_live_cookie_store("firefox", os_name="macOS")

    assert loc.supported is True
    assert loc.exists is True
    assert Path(loc.path) == store
    assert loc.browser_profile == "zz.default-release"


def _linux_firefox_profile(root: Path, profile: str) -> Path:
    profile_dir = root / profile
    profile_dir.mkdir(parents=True)
    (root / "profiles.ini").write_text(
        f"[Profile0]\nName=default-release\nIsRelative=1\nPath={profile}\nDefault=1\n",
        encoding="utf-8",
    )
    store = profile_dir / "cookies.sqlite"
    store.touch()
    return store


def test_resolve_live_firefox_falls_back_to_ubuntu_snap_profile(mods, live_home):
    store = _linux_firefox_profile(
        live_home / "snap" / "firefox" / "common" / ".mozilla" / "firefox",
        "snap.default-release",
    )

    loc = mods.browser_cookies.resolve_live_cookie_store(
        "firefox", os_name="Ubuntu-LTS-Linux", home=live_home
    )

    assert loc.exists is True
    assert Path(loc.path) == store
    assert loc.browser_profile == "snap.default-release"


def test_resolve_live_firefox_prefers_conventional_linux_profile(mods, live_home):
    conventional_store = _linux_firefox_profile(
        live_home / ".mozilla" / "firefox", "conventional.default-release"
    )
    _linux_firefox_profile(
        live_home / "snap" / "firefox" / "common" / ".mozilla" / "firefox",
        "snap.default-release",
    )

    loc = mods.browser_cookies.resolve_live_cookie_store(
        "firefox", os_name="Ubuntu-LTS-Linux", home=live_home
    )

    assert loc.exists is True
    assert Path(loc.path) == conventional_store
    assert loc.browser_profile == "conventional.default-release"


def test_live_firefox_login_persists_redacted_live_metadata(
    mods, home, live_home, capsys, monkeypatch
):
    _, store = _firefox_home(live_home, profile="k7p2x9q3.dev-edition-default")
    _build_firefox_db(store, _rows())
    monkeypatch.setattr(Path, "home", staticmethod(lambda: live_home))
    mods.profiles.ProfileStore(home).create_profile("default")

    code, out, err = _run(
        mods,
        capsys,
        ["--storage", str(home), "login", "--browser-cookies", "firefox", "--json"],
    )

    assert code == 0, err
    data = json.loads(out)
    assert data["source_kind"] == "live_browser"
    assert data["has_required_cookies"] is True
    assert data["cookie_count"] == 3
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
    assert meta["browser"] == "firefox"
    assert meta["cookie_store"] is None
    assert meta["fixture_root"] is None
    assert str(live_home) not in json.dumps(meta)


def test_live_firefox_custom_profile_metadata_stays_valid(
    mods, home, live_home, capsys, monkeypatch
):
    profile = "q1w2e3r4.work-backup-2024"
    _, store = _firefox_home(live_home, profile=profile)
    _build_firefox_db(store, _rows())
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
            "firefox",
            "--json",
        ],
    )

    assert code == 0, err
    data = json.loads(out)
    assert data["browser_profile"] == profile
    meta_path = mods.profiles.ProfileStore(home).auth_source_path("default")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["browser_profile"] == profile
    assert meta["source_kind"] == "live_browser"
    assert meta["cookie_store"] is None and meta["fixture_root"] is None


def test_live_firefox_hint_word_profile_metadata_stays_valid_and_refreshes(
    mods, home, live_home, capsys, monkeypatch
):
    profile = "a1b2c3d4.oauth-testing"
    _, store = _firefox_home(live_home, profile=profile)
    _build_firefox_db(store, _rows()[:2])
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
            "firefox",
            "--json",
        ],
    )
    assert code == 0, err
    assert json.loads(out)["browser_profile"] == profile
    meta_path = mods.profiles.ProfileStore(home).auth_source_path("default")
    assert meta_path.is_file()
    assert (
        json.loads(meta_path.read_text(encoding="utf-8"))["browser_profile"] == profile
    )

    _append_firefox_cookie(store, "HSID", SECRET_HSID)
    code, out, err = _run(
        mods,
        capsys,
        [
            "--storage",
            str(home),
            "auth",
            "refresh",
            "--browser-cookies",
            "firefox",
            "--json",
        ],
    )
    assert code == 0, err
    data = json.loads(out)
    assert data["browser_profile"] == profile
    assert data["from_persisted_source"] is True
    assert data["has_required_cookies"] is True


def test_live_firefox_inspect_is_redacted_and_pathless(
    mods, home, live_home, capsys, monkeypatch
):
    _, store = _firefox_home(live_home)
    _build_firefox_db(store, _rows())
    monkeypatch.setattr(Path, "home", staticmethod(lambda: live_home))

    async def discovered(jar):
        return [mods.browser_cookies._auth.Account(0, "firefox@example.com", True)]

    monkeypatch.setattr(mods.browser_cookies._auth, "enumerate_accounts", discovered)

    code, out, err = _run(
        mods,
        capsys,
        ["--storage", str(home), "auth", "inspect", "--browser", "firefox", "--json"],
    )

    assert code == 0, err
    data = json.loads(out)
    assert data == {
        "browser": "firefox",
        "accounts": [
            {
                "email": "firefox@example.com",
                "is_default": True,
                "browser_profile": None,
            }
        ],
    }
    assert str(live_home) not in out
    for secret in (SECRET_SID, SECRET_PSIDTS, SECRET_HSID):
        assert secret not in out


def test_live_firefox_refresh_reresolves_from_live_metadata(
    mods, home, live_home, capsys, monkeypatch
):
    _, store = _firefox_home(live_home)
    _build_firefox_db(store, _rows()[:2])
    monkeypatch.setattr(Path, "home", staticmethod(lambda: live_home))
    mods.profiles.ProfileStore(home).create_profile("default")

    code, _, err = _run(
        mods,
        capsys,
        ["--storage", str(home), "login", "--browser-cookies", "firefox", "--json"],
    )
    assert code == 0, err
    _append_firefox_cookie(store, "HSID", SECRET_HSID)

    code, out, err = _run(
        mods,
        capsys,
        [
            "--storage",
            str(home),
            "auth",
            "refresh",
            "--browser-cookies",
            "firefox",
            "--json",
        ],
    )

    assert code == 0, err
    data = json.loads(out)
    assert data["source_kind"] == "live_browser"
    assert data["from_persisted_source"] is True
    assert data["has_required_cookies"] is True
    assert data["cookie_count"] == 3
    assert str(live_home) not in out
    for secret in (SECRET_SID, SECRET_PSIDTS, SECRET_HSID):
        assert secret not in out


def test_explicit_cookie_store_still_avoids_path_home(
    mods, home, tmp_path, capsys, monkeypatch
):
    store = tmp_path / "cookies.sqlite"
    _build_firefox_db(store, _rows())
    monkeypatch.setattr(
        Path,
        "home",
        staticmethod(lambda: (_ for _ in ()).throw(AssertionError("real home access"))),
    )
    mods.profiles.ProfileStore(home).create_profile("default")

    code, out, err = _run(
        mods,
        capsys,
        [
            "--storage",
            str(home),
            "login",
            "--browser-cookies",
            "firefox",
            "--cookie-store",
            str(store),
            "--json",
        ],
    )

    assert code == 0, err
    assert json.loads(out)["source_kind"] == "cookie_store"


def test_non_firefox_live_read_still_refuses_without_home_access(
    mods, home, capsys, monkeypatch
):
    monkeypatch.setattr(
        Path,
        "home",
        staticmethod(lambda: (_ for _ in ()).throw(AssertionError("real home access"))),
    )
    code, _, err = _run(
        mods,
        capsys,
        ["--storage", str(home), "login", "--browser-cookies", "chrome", "--json"],
    )
    assert code == 78
    assert "later parity" in err.lower() or "explicit" in err.lower()
