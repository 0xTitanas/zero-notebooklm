"""Phase 2E-C2C: macOS Keychain-gated live Chromium CLI import/refresh.

Tests stay hermetic: synthetic Chromium homes, fake Keychain password provider,
fake macOS decrypt primitive, no real Keychain, no real browser DB, no network, no
browser automation, and no values/paths/secrets in public CLI output.
"""

from __future__ import annotations

import ast
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

SECRET_PASSWORD = "phase2eC2cSyntheticSafeStoragePassword"
SECRET_SID = "phase2eC2cSyntheticSidValue1234567890"
SECRET_PSIDTS = "phase2eC2cSyntheticPsidtsValue1234567890"
SECRET_TRACKER = "phase2eC2cSyntheticTrackerValue987654321"
ENC_SID = b"v10phase2e-c2c-encrypted-sid"
ENC_PSIDTS = b"v10phase2e-c2c-encrypted-psidts"
ENC_TRACKER = b"v10phase2e-c2c-encrypted-tracker"
ENC_MAIL = b"v10phase2e-c2c-encrypted-mail"
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
    from notebooklm import auth

    async def discovered(_jar):
        return [auth.Account(0, "default@example.com", True)]

    monkeypatch.setattr(auth, "enumerate_accounts", discovered)
    import importlib
    import sys
    import types

    for name in (
        "notebooklm.browser_cookies",
        "notebooklm.os_credentials",
        "notebooklm.profiles",
        "notebooklm.cookies",
        "notebooklm.cli",
    ):
        sys.modules.pop(name, None)
    return types.SimpleNamespace(
        browser_cookies=importlib.import_module("notebooklm.browser_cookies"),
        cookies=importlib.import_module("notebooklm.cookies"),
        os_credentials=importlib.import_module("notebooklm.os_credentials"),
        profiles=importlib.import_module("notebooklm.profiles"),
        errors=importlib.import_module("notebooklm.errors"),
        cli=importlib.import_module("notebooklm.cli"),
        import_origin_audit=importlib.import_module("import_origin_audit"),
    )


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
    mods, root: Path, *, last_used: str = "Default", browser: str = "chrome"
) -> None:
    data_dir = _data_dir(mods, root, browser=browser, os_name="macOS")
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "Local State").write_text(
        json.dumps(
            {
                "profile": {"last_used": last_used},
                "os_crypt": {"encrypted_key": "must-not-leak"},
            }
        ),
        encoding="utf-8",
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
                    int(row.get("http_only", True)),
                    row.get("encrypted_value", b""),
                    row.get("samesite", -1),
                    1,
                ),
            )
        con.commit()
    finally:
        con.close()


def _encrypted_rows() -> list[dict]:
    return [
        {"host": ".google.com", "name": "SID", "encrypted_value": ENC_SID},
        {
            "host": ".google.com",
            "name": "__Secure-1PSIDTS",
            "encrypted_value": ENC_PSIDTS,
        },
        {"host": "mail.google.com", "name": "MAIL", "encrypted_value": ENC_MAIL},
        {"host": ".tracker.example", "name": "tracker", "encrypted_value": ENC_TRACKER},
    ]


def _install_fake_keychain(mods, monkeypatch, calls: list[tuple]):
    def fake_keychain_password(browser):
        calls.append(("keychain", browser))
        return SECRET_PASSWORD

    def fake_decrypt_cookie_value(encrypted_value, *, host, safe_storage_password):
        blob = bytes(encrypted_value)
        calls.append(("decrypt", host, safe_storage_password, blob))
        assert safe_storage_password == SECRET_PASSWORD
        assert host == ".google.com", (
            "non-Google rows must not reach the real decrypt primitive"
        )
        values = {ENC_SID: SECRET_SID, ENC_PSIDTS: SECRET_PSIDTS}
        return values.get(blob)

    monkeypatch.setattr(
        mods.os_credentials, "macos_chromium_keychain_password", fake_keychain_password
    )
    monkeypatch.setattr(
        mods.os_credentials,
        "macos_chromium_decrypt_cookie_value",
        fake_decrypt_cookie_value,
    )
    monkeypatch.setattr(
        mods.browser_cookies._oscreds,
        "macos_chromium_keychain_password",
        fake_keychain_password,
    )
    monkeypatch.setattr(
        mods.browser_cookies._oscreds,
        "macos_chromium_decrypt_cookie_value",
        fake_decrypt_cookie_value,
    )


def _run(mods, capsys, argv):
    code = mods.cli.console(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.mark.parametrize("browser", ["chrome", "brave"])
def test_cli_live_chromium_inspect_uses_macos_keychain_and_stays_pathless(
    mods, home, live_home, capsys, monkeypatch, browser
):
    store = _cookie_store(
        mods, live_home, browser=browser, profile="Default", modern=True
    )
    _build_chromium_db(store, _encrypted_rows())
    _write_local_state(mods, live_home, last_used="Default", browser=browser)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: live_home))
    calls: list[tuple] = []
    _install_fake_keychain(mods, monkeypatch, calls)

    async def discovered(jar):
        return [mods.browser_cookies._auth.Account(0, f"{browser}@example.test", True)]

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
            browser,
            "--os",
            "macOS",
            "--json",
        ],
    )

    assert code == 0, err
    assert err == ""
    payload = json.loads(out)
    assert payload == {
        "browser": browser,
        "accounts": [
            {
                "email": f"{browser}@example.test",
                "is_default": True,
                "browser_profile": None,
            }
        ],
    }
    assert [c for c in calls if c[0] == "keychain"] == [("keychain", browser)]
    assert sorted((c[1], c[3]) for c in calls if c[0] == "decrypt") == [
        (".google.com", ENC_PSIDTS),
        (".google.com", ENC_SID),
    ]

    public = json.dumps(payload, sort_keys=True)
    assert str(live_home) not in public
    assert str(store) not in public
    assert SECRET_PASSWORD not in public
    assert SECRET_SID not in public
    assert SECRET_PSIDTS not in public
    assert SECRET_TRACKER not in public
    assert "MAIL" not in public


@pytest.mark.parametrize(
    "browser",
    ["arc", "brave", "chrome", "chromium", "edge", "opera", "opera-gx", "vivaldi"],
)
def test_cli_live_chromium_family_login_uses_keychain_and_writes_pathless_auth(
    mods, home, live_home, capsys, monkeypatch, browser
):
    store = _cookie_store(
        mods, live_home, browser=browser, profile="Default", modern=True
    )
    _build_chromium_db(store, _encrypted_rows())
    _write_local_state(mods, live_home, last_used="Default", browser=browser)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: live_home))
    profile_store = mods.profiles.ProfileStore(home)
    profile_store.create_profile("default")
    calls: list[tuple] = []
    _install_fake_keychain(mods, monkeypatch, calls)

    code, out, err = _run(
        mods,
        capsys,
        [
            "--storage",
            str(home),
            "login",
            "--browser-cookies",
            browser,
            "--os",
            "macOS",
            "--json",
        ],
    )

    assert code == 0, err
    assert err == ""
    payload = json.loads(out)
    assert payload["source_kind"] == "live_browser"
    assert payload["source_path"] is None
    assert payload["browser_profile"] == "Default"
    assert payload["imported"] == 2
    assert payload["has_required_cookies"] is True
    assert payload["blocked_count"] == 0
    assert [c for c in calls if c[0] == "keychain"] == [("keychain", browser)]
    assert sorted((c[1], c[3]) for c in calls if c[0] == "decrypt") == [
        (".google.com", ENC_PSIDTS),
        (".google.com", ENC_SID),
    ]

    state = json.loads(
        profile_store.storage_state_path("default").read_text(encoding="utf-8")
    )
    by_name = {c["name"]: c for c in state["cookies"]}
    assert by_name["SID"]["value"] == SECRET_SID
    assert by_name["__Secure-1PSIDTS"]["value"] == SECRET_PSIDTS
    assert "tracker" not in by_name

    persisted = json.loads(
        profile_store.auth_source_path("default").read_text(encoding="utf-8")
    )
    assert persisted["source_kind"] == "live_browser"
    assert persisted["cookie_store"] is None
    assert persisted["fixture_root"] is None
    assert persisted["browser_profile"] == "Default"

    public = json.dumps(payload, sort_keys=True)
    assert str(live_home) not in public
    assert str(store) not in public
    assert SECRET_PASSWORD not in public
    assert SECRET_SID not in public
    assert SECRET_PSIDTS not in public
    assert SECRET_TRACKER not in public
    assert ENC_TRACKER.hex() not in public
    assert "must-not-leak" not in public
    assert "MAIL" not in public
    assert ".tracker.example" not in public


@pytest.mark.parametrize("browser", ["chrome", "brave"])
def test_cli_live_chromium_refresh_uses_keychain_from_pathless_metadata(
    mods, home, live_home, capsys, monkeypatch, browser
):
    store = _cookie_store(
        mods, live_home, browser=browser, profile="Default", modern=True
    )
    _build_chromium_db(store, _encrypted_rows())
    _write_local_state(mods, live_home, last_used="Default", browser=browser)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: live_home))
    profile_store = mods.profiles.ProfileStore(home)
    profile_store.create_profile("default")
    calls: list[tuple] = []
    _install_fake_keychain(mods, monkeypatch, calls)

    code, _out, err = _run(
        mods,
        capsys,
        [
            "--storage",
            str(home),
            "login",
            "--browser-cookies",
            browser,
            "--os",
            "macOS",
            "--json",
        ],
    )
    assert code == 0, err

    code, out, err = _run(
        mods,
        capsys,
        [
            "--storage",
            str(home),
            "auth",
            "refresh",
            "--browser-cookies",
            browser,
            "--json",
        ],
    )

    assert code == 0, err
    assert err == ""
    payload = json.loads(out)
    assert payload["refreshed"] is True
    assert payload["from_persisted_source"] is True
    assert payload["source_kind"] == "live_browser"
    assert payload["source_path"] is None
    assert payload["imported"] == 2
    assert payload["has_required_cookies"] is True
    assert [c for c in calls if c[0] == "keychain"] == [
        ("keychain", browser),
        ("keychain", browser),
    ]
    public = json.dumps(payload, sort_keys=True)
    assert str(live_home) not in public
    assert str(store) not in public
    assert SECRET_PASSWORD not in public
    assert SECRET_SID not in public
    assert SECRET_PSIDTS not in public
    assert "MAIL" not in public
    assert ".tracker.example" not in public


def test_cli_live_chrome_keychain_unavailable_is_redacted_and_writes_no_auth(
    mods, home, live_home, capsys, monkeypatch
):
    def boom_home():
        raise AssertionError(
            "Path.home must not be consulted when Keychain is unavailable"
        )

    def unavailable(browser):
        raise mods.browser_cookies._oscreds.CredentialUnavailableError(
            "synthetic unavailable " + SECRET_PASSWORD
        )

    monkeypatch.setattr(Path, "home", staticmethod(boom_home))
    monkeypatch.setattr(
        mods.os_credentials, "macos_chromium_keychain_password", unavailable
    )
    monkeypatch.setattr(
        mods.browser_cookies._oscreds, "macos_chromium_keychain_password", unavailable
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
            "chrome",
            "--os",
            "macOS",
            "--json",
        ],
    )

    assert code != 0
    assert out == ""
    assert "keychain" in err.lower() or "safe storage" in err.lower()
    assert SECRET_PASSWORD not in err
    assert not (home / "profiles" / "default" / "storage_state.json").exists()
    assert not (home / "profiles" / "default" / "auth_source.json").exists()


def test_cli_live_chrome_unexpected_keychain_or_decryptor_errors_are_redacted(
    mods, home, live_home, capsys, monkeypatch
):
    def boom_home():
        raise AssertionError(
            "Path.home must not be consulted before redacted credential failure"
        )

    def unexpected_keychain(browser):
        raise RuntimeError("unexpected keychain failure " + SECRET_PASSWORD)

    monkeypatch.setattr(Path, "home", staticmethod(boom_home))
    monkeypatch.setattr(
        mods.os_credentials, "macos_chromium_keychain_password", unexpected_keychain
    )
    monkeypatch.setattr(
        mods.browser_cookies._oscreds,
        "macos_chromium_keychain_password",
        unexpected_keychain,
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
            "chrome",
            "--os",
            "macOS",
            "--json",
        ],
    )

    assert code != 0
    assert out == ""
    assert "keychain" in err.lower() or "safe storage" in err.lower()
    assert SECRET_PASSWORD not in err
    assert not (home / "profiles" / "default" / "storage_state.json").exists()
    assert not (home / "profiles" / "default" / "auth_source.json").exists()

    store = _cookie_store(mods, live_home, profile="Default", modern=True)
    _build_chromium_db(store, _encrypted_rows())
    _write_local_state(mods, live_home, last_used="Default")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: live_home))

    def fake_keychain(browser):
        return SECRET_PASSWORD

    def exploding_resolver(os_name, browser, *, safe_storage_password=None):
        raise RuntimeError("resolver leaked " + str(safe_storage_password))

    monkeypatch.setattr(
        mods.os_credentials, "macos_chromium_keychain_password", fake_keychain
    )
    monkeypatch.setattr(
        mods.browser_cookies._oscreds, "macos_chromium_keychain_password", fake_keychain
    )
    monkeypatch.setattr(mods.os_credentials, "resolve_decryptor", exploding_resolver)
    monkeypatch.setattr(
        mods.browser_cookies._oscreds, "resolve_decryptor", exploding_resolver
    )
    home2 = home.parent / "nlm-home-resolver"
    mods.profiles.ProfileStore(home2).create_profile("default")

    code, out, err = _run(
        mods,
        capsys,
        [
            "--storage",
            str(home2),
            "login",
            "--browser-cookies",
            "chrome",
            "--os",
            "macOS",
            "--json",
        ],
    )

    assert code != 0
    assert out == ""
    assert "decryptor" in err.lower() or "safe storage" in err.lower()
    assert SECRET_PASSWORD not in err
    assert not (home2 / "profiles" / "default" / "storage_state.json").exists()
    assert not (home2 / "profiles" / "default" / "auth_source.json").exists()


@pytest.mark.parametrize("browser", ["octo"])
def test_cli_unsupported_macos_chromium_keychain_browsers_refuse_before_home(
    mods, home, capsys, monkeypatch, browser
):
    def boom_home():
        raise AssertionError(
            "Path.home must not be consulted for unsupported Keychain browser"
        )

    def keychain_called(name):
        raise AssertionError("unsupported browser must not call Keychain")

    monkeypatch.setattr(Path, "home", staticmethod(boom_home))
    monkeypatch.setattr(
        mods.os_credentials, "macos_chromium_keychain_password", keychain_called
    )
    monkeypatch.setattr(
        mods.browser_cookies._oscreds,
        "macos_chromium_keychain_password",
        keychain_called,
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
            browser,
            "--os",
            "macOS",
            "--json",
        ],
    )

    assert code != 0
    assert "rookiepy is not installed" in out
    assert err == ""
    assert str(home) not in err
    profile_store = mods.profiles.ProfileStore(home)
    assert not profile_store.storage_state_path("default").exists()
    assert not profile_store.auth_source_path("default").exists()


def test_cli_live_chrome_without_explicit_os_refuses_before_home_or_keychain(
    mods, home, capsys, monkeypatch
):
    def boom_home():
        raise AssertionError(
            "Path.home must not be consulted without an explicit Chromium OS lane"
        )

    def keychain_called(name):
        raise AssertionError(
            "unspecified Chromium OS lane must not call macOS Keychain"
        )

    monkeypatch.setattr(Path, "home", staticmethod(boom_home))
    monkeypatch.setattr(
        mods.os_credentials, "macos_chromium_keychain_password", keychain_called
    )
    monkeypatch.setattr(
        mods.browser_cookies._oscreds,
        "macos_chromium_keychain_password",
        keychain_called,
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
            "chrome",
            "--json",
        ],
    )

    assert code != 0
    assert out == ""
    assert "--os macos" in err.lower()
    assert str(home) not in err


def test_c2c_keeps_compat_and_dependency_boundary_clean(repo_root, mods):
    matrix = repo_root / "compat" / "auth_matrix.json"
    before = hashlib.sha256(matrix.read_bytes()).hexdigest()

    assert mods.import_origin_audit.audit(roots=("notebooklm",)) == []
    assert hashlib.sha256(matrix.read_bytes()).hexdigest() == before

    imported = []
    for rel in (
        "notebooklm/browser_cookies.py",
        "notebooklm/os_credentials.py",
        "notebooklm/cli.py",
    ):
        src = (repo_root / rel).read_text(encoding="utf-8")
        tree = ast.parse(src, filename=rel)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
    forbidden = (
        "cryptography",
        "Crypto",
        "pycryptodome",
        "keyring",
        "secretstorage",
        "win32crypt",
        "browser_cookie3",
        "rookiepy",
        "playwright",
        "selenium",
        "requests",
        "httpx",
        "urllib3",
        "aiohttp",
    )
    for module in imported:
        assert not any(
            module == token or module.startswith(token + ".") for token in forbidden
        )
