"""Phase 2E-C1 tests: authorized live Chromium-family discovery + inspect.

The suite stays hermetic. It models live Chromium user-data directories under
``tmp_path`` and monkeypatches ``Path.home()`` only inside commands that explicitly
request a live Chromium browser-cookie inspection. No test touches the real
browser, network, keychain/DPAPI/libsecret, or ``~/.notebooklm``.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

SECRET_SID = "phase2eChromiumSyntheticSidValue1234567890"
SECRET_TRACKER = "phase2eChromiumSyntheticTrackerValue987654321"
ENC_BLOB = b"v10phase2eChromiumEncryptedFixtureBytesOnly"
_UNIX_FAR_FUTURE = 1893456000
_CHROMIUM_EPOCH_OFFSET = 11644473600
CHROMIUM_EXPIRES_UTC = (_UNIX_FAR_FUTURE + _CHROMIUM_EPOCH_OFFSET) * 1_000_000


@pytest.fixture
def home(tmp_path) -> Path:
    return tmp_path / "nlm-home"


@pytest.fixture
def live_home(tmp_path) -> Path:
    return tmp_path / "live-home"


@pytest.fixture
def mods(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))
    import importlib
    import types

    return types.SimpleNamespace(
        browser_cookies=importlib.import_module("notebooklm.browser_cookies"),
        cli=importlib.import_module("notebooklm.cli"),
        profiles=importlib.import_module("notebooklm.profiles"),
        errors=importlib.import_module("notebooklm.errors"),
    )


def _build_chromium_db(path: Path, rows: list[dict]) -> None:
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
        for i, row in enumerate(rows, start=1):
            con.execute(
                "INSERT INTO cookies (creation_utc, host_key, name, value, path, "
                "expires_utc, is_secure, is_httponly, encrypted_value, samesite, "
                "is_persistent) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    i,
                    row.get("host", ".google.com"),
                    row["name"],
                    row.get("value", ""),
                    row.get("path", "/"),
                    row.get("expires_utc", CHROMIUM_EXPIRES_UTC),
                    int(row.get("secure", True)),
                    int(row.get("http_only", False)),
                    row.get("encrypted_value", b""),
                    row.get("samesite", -1),
                    1,
                ),
            )
        con.commit()
    finally:
        con.close()


def _data_dir(
    mods, root: Path, browser: str = "chrome", os_name: str = "macOS"
) -> Path:
    os_key = {"macOS": "macos", "Ubuntu-LTS-Linux": "linux", "Windows-11": "windows"}[
        os_name
    ]
    return root.joinpath(*mods.browser_cookies._CHROMIUM_DATA_DIR[os_key][browser])


def _cookie_store(
    mods,
    root: Path,
    *,
    browser: str = "chrome",
    os_name: str = "macOS",
    profile: str = "Default",
    modern: bool = True,
) -> Path:
    base = _data_dir(mods, root, browser=browser, os_name=os_name) / profile
    return base / "Network" / "Cookies" if modern else base / "Cookies"


def _write_local_state(
    mods, root: Path, *, last_used: str, browser: str = "chrome", os_name: str = "macOS"
) -> None:
    data_dir = _data_dir(mods, root, browser=browser, os_name=os_name)
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "Local State").write_text(
        json.dumps(
            {
                "profile": {"last_used": last_used},
                "os_crypt": {"encrypted_key": "do-not-read"},
            }
        ),
        encoding="utf-8",
    )


def _rows():
    return [
        {"name": "SID", "value": SECRET_SID, "host": ".google.com", "secure": True},
        {
            "name": "__Secure-1PSIDTS",
            "value": "",
            "host": ".google.com",
            "encrypted_value": ENC_BLOB,
            "secure": True,
        },
        {
            "name": "tracker",
            "value": SECRET_TRACKER,
            "host": ".tracker.example",
            "secure": False,
        },
    ]


def _run(mods, capsys, argv):
    code = mods.cli.console(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_resolve_live_chromium_prefers_safe_local_state_profile(
    mods, live_home, monkeypatch
):
    default_store = _cookie_store(mods, live_home, profile="Default", modern=False)
    selected_store = _cookie_store(mods, live_home, profile="Profile 2", modern=True)
    _build_chromium_db(default_store, [{"name": "SID", "value": "default"}])
    _build_chromium_db(selected_store, _rows())
    _write_local_state(mods, live_home, last_used="Profile 2")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: live_home))

    loc = mods.browser_cookies.resolve_live_cookie_store("chrome", os_name="macOS")

    assert loc.supported is True
    assert loc.exists is True
    assert Path(loc.path) == selected_store
    assert loc.browser_profile == "Profile 2"


@pytest.mark.parametrize(
    ("os_name", "browser", "profile"),
    [
        ("macOS", "arc", "Default"),
        ("Ubuntu-LTS-Linux", "brave", "Profile 1"),
        ("Windows-11", "edge", "Profile 2"),
    ],
)
def test_resolve_live_chromium_supports_documented_layouts(
    mods, live_home, monkeypatch, os_name, browser, profile
):
    store = _cookie_store(
        mods, live_home, browser=browser, os_name=os_name, profile=profile, modern=True
    )
    _build_chromium_db(store, _rows())
    monkeypatch.setattr(Path, "home", staticmethod(lambda: live_home))

    loc = mods.browser_cookies.resolve_live_cookie_store(
        browser, os_name=os_name, browser_profile=profile
    )

    assert loc.supported is True
    assert loc.exists is True
    assert Path(loc.path) == store
    assert loc.browser_profile == profile


def test_resolve_live_chromium_falls_back_to_snap_with_conventional_precedence(
    mods, live_home, monkeypatch
):
    snap_store = (
        live_home
        / "snap"
        / "chromium"
        / "common"
        / "chromium"
        / "Default"
        / "Network"
        / "Cookies"
    )
    _build_chromium_db(snap_store, _rows())
    monkeypatch.setattr(Path, "home", staticmethod(lambda: live_home))

    snap = mods.browser_cookies.resolve_live_cookie_store(
        "chromium", os_name="Ubuntu-LTS-Linux"
    )

    assert snap.exists is True
    assert Path(snap.path) == snap_store

    conventional_store = _cookie_store(
        mods,
        live_home,
        browser="chromium",
        os_name="Ubuntu-LTS-Linux",
        profile="Default",
    )
    _build_chromium_db(conventional_store, _rows())

    conventional = mods.browser_cookies.resolve_live_cookie_store(
        "chromium", os_name="Ubuntu-LTS-Linux"
    )

    assert conventional.exists is True
    assert Path(conventional.path) == conventional_store


def test_resolve_live_chromium_unsupported_layout_does_not_touch_home(
    mods, monkeypatch
):
    def boom():
        raise AssertionError("Path.home must not be consulted for unsupported layout")

    monkeypatch.setattr(Path, "home", staticmethod(boom))

    loc = mods.browser_cookies.resolve_live_cookie_store(
        "arc", os_name="Ubuntu-LTS-Linux"
    )

    assert loc.supported is False
    assert loc.path is None
    assert "no documented cookie store" in loc.reason


def test_resolve_live_chromium_ignores_unsafe_local_state_and_uses_default(
    mods, live_home, monkeypatch
):
    default_store = _cookie_store(mods, live_home, profile="Default", modern=True)
    _build_chromium_db(default_store, _rows())
    _write_local_state(mods, live_home, last_used="../escape")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: live_home))

    loc = mods.browser_cookies.resolve_live_cookie_store("chrome", os_name="macOS")

    assert loc.supported is True
    assert loc.exists is True
    assert Path(loc.path) == default_store
    assert loc.browser_profile == "Default"
    assert "escape" not in (loc.browser_profile or "")


def test_resolve_live_chromium_falls_back_to_stable_safe_profile(
    mods, live_home, monkeypatch
):
    z_store = _cookie_store(mods, live_home, profile="zeta.default", modern=True)
    a_store = _cookie_store(mods, live_home, profile="alpha.default", modern=True)
    _build_chromium_db(z_store, [{"name": "SID", "value": "zeta"}])
    _build_chromium_db(a_store, _rows())
    monkeypatch.setattr(Path, "home", staticmethod(lambda: live_home))

    loc = mods.browser_cookies.resolve_live_cookie_store("chrome", os_name="macOS")

    assert loc.exists is True
    assert Path(loc.path) == a_store
    assert loc.browser_profile == "alpha.default"


def test_live_chromium_inspect_is_redacted_pathless_and_blocks_encrypted(
    mods, home, live_home, capsys, monkeypatch
):
    store = _cookie_store(mods, live_home, profile="Default", modern=True)
    _build_chromium_db(store, _rows())
    _write_local_state(mods, live_home, last_used="Default")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: live_home))
    async def discovered(jar):
        return [mods.browser_cookies._auth.Account(0, "chrome@example.com", True)]

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
            "chrome",
            "--json",
        ],
    )

    assert code == 0, err
    data = json.loads(out)
    assert data == {
        "browser": "chrome",
        "accounts": [
            {
                "email": "chrome@example.com",
                "is_default": True,
                "browser_profile": None,
            }
        ],
    }
    assert str(live_home) not in out
    assert str(store) not in out
    assert ".tracker.example" not in out
    assert SECRET_SID not in out
    assert SECRET_TRACKER not in out
    assert ENC_BLOB.hex() not in out
    assert "do-not-read" not in out


def test_live_chromium_login_still_refuses_and_writes_no_auth_files(
    mods, home, live_home, capsys, monkeypatch
):
    store = _cookie_store(mods, live_home, profile="Default", modern=True)
    _build_chromium_db(store, _rows())
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
            "chrome",
            "--json",
        ],
    )

    assert code != 0
    assert out == ""
    assert "firefox" in err and "safari" in err
    profile_store = mods.profiles.ProfileStore(home)
    assert not profile_store.storage_state_path("default").exists()
    assert not profile_store.auth_source_path("default").exists()
    assert str(live_home) not in err


def test_live_chromium_library_import_and_refresh_refuse_before_home(
    mods, home, monkeypatch
):
    def boom():
        raise AssertionError(
            "Path.home must not be consulted by refused Chromium import/refresh"
        )

    monkeypatch.setattr(Path, "home", staticmethod(boom))
    dest = home / "profile" / "storage_state.json"
    meta = home / "profile" / "auth_source.json"
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text(
        json.dumps(
            mods.browser_cookies.build_auth_source_metadata(
                "chrome",
                source_kind="live_browser",
                os_name="macOS",
                browser_profile="Default",
            )
        ),
        encoding="utf-8",
    )

    with pytest.raises(mods.errors.ValidationError, match="live Chromium import"):
        mods.browser_cookies.import_live_browser_to_storage_state(
            "chrome", dest_path=dest
        )
    assert not dest.exists()

    with pytest.raises(mods.errors.ValidationError, match="live Chromium refresh"):
        mods.browser_cookies.refresh_browser_cookies(
            "chrome", dest_path=dest, meta_path=meta
        )
    assert not dest.exists()


def test_explicit_chromium_inspect_remains_home_free(
    mods, home, live_home, capsys, monkeypatch
):
    explicit = home / "fixture" / "Cookies"
    _build_chromium_db(explicit, _rows())

    def boom():
        raise AssertionError("Path.home must not be consulted for explicit source")

    monkeypatch.setattr(Path, "home", staticmethod(boom))
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
            str(explicit),
            "--json",
        ],
    )

    assert code == 0, err
    data = json.loads(out)
    assert data["source_path"] == str(explicit)
    assert data["blocked_count"] == 1
    assert str(live_home) not in out


def test_live_chromium_inspect_does_not_mutate_auth_matrix(
    mods, repo_root, home, live_home, capsys, monkeypatch
):
    matrix = repo_root / "compat" / "auth_matrix.json"
    before = hashlib.sha256(matrix.read_bytes()).hexdigest()
    store = _cookie_store(mods, live_home, profile="Default", modern=True)
    _build_chromium_db(store, _rows())
    monkeypatch.setattr(Path, "home", staticmethod(lambda: live_home))
    async def discovered(jar):
        return [mods.browser_cookies._auth.Account(0, "chrome@example.com", True)]

    monkeypatch.setattr(mods.browser_cookies._auth, "enumerate_accounts", discovered)

    code, _out, err = _run(
        mods,
        capsys,
        [
            "--storage",
            str(home),
            "auth",
            "inspect",
            "--browser",
            "chrome",
            "--json",
        ],
    )

    assert code == 0, err
    after = hashlib.sha256(matrix.read_bytes()).hexdigest()
    assert after == before
