"""Phase 2E-C2B: synthetic live Chromium import/refresh wiring.

Hermetic only: synthetic Chromium homes, fake decryptors, no real Keychain, no
browser DB outside tmp_path, no network, no browser automation, no live paths in
public summaries or persisted live metadata.
"""

from __future__ import annotations

import ast
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

SECRET_SID = "phase2eC2bSyntheticSidValue1234567890"
SECRET_PSIDTS = "phase2eC2bSyntheticPsidtsValue1234567890"
SECRET_TRACKER = "phase2eC2bSyntheticTrackerValue987654321"
ENC_SID = b"v10phase2e-c2b-encrypted-sid"
ENC_PSIDTS = b"v10phase2e-c2b-encrypted-psidts"
ENC_TRACKER = b"v10phase2e-c2b-encrypted-tracker"
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
        {"host": ".tracker.example", "name": "tracker", "encrypted_value": ENC_TRACKER},
    ]


def _fake_google_decryptor(calls: list[tuple[str, str, bytes]]):
    def decryptor(blob, *, host, name):
        calls.append((host, name, bytes(blob)))
        assert host == ".google.com", (
            "non-Google rows must not be decrypted in live Chromium wiring"
        )
        values = {"SID": SECRET_SID, "__Secure-1PSIDTS": SECRET_PSIDTS}
        return values.get(name)

    return decryptor


def test_live_chromium_import_with_injected_decryptor_writes_storage_and_pathless_metadata(
    mods, home, live_home, monkeypatch
):
    store = _cookie_store(mods, live_home, profile="Default", modern=True)
    _build_chromium_db(store, _encrypted_rows())
    _write_local_state(mods, live_home, last_used="Default")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: live_home))
    profile_store = mods.profiles.ProfileStore(home)
    profile_store.create_profile("default")
    calls: list[tuple[str, str, bytes]] = []

    summary = mods.browser_cookies.import_live_browser_to_storage_state(
        "chrome",
        dest_path=profile_store.storage_state_path("default"),
        os_name="macOS",
        decryptor=_fake_google_decryptor(calls),
    )
    metadata = mods.browser_cookies.build_auth_source_metadata(
        "chrome",
        source_kind=mods.browser_cookies.SOURCE_KIND_LIVE_BROWSER,
        os_name="macOS",
        browser_profile=summary["browser_profile"],
        google_only=True,
    )
    mods.browser_cookies.write_auth_source(
        profile_store.auth_source_path("default"), metadata
    )

    assert summary["source_kind"] == "live_browser"
    assert summary["source_path"] is None
    assert summary["browser_profile"] == "Default"
    assert summary["imported"] == 2
    assert summary["has_required_cookies"] is True
    assert summary["blocked_count"] == 0
    assert sorted((host, name) for host, name, _ in calls) == [
        (".google.com", "SID"),
        (".google.com", "__Secure-1PSIDTS"),
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
    assert str(live_home) not in json.dumps(summary)
    assert str(store) not in json.dumps(summary)
    assert str(live_home) not in json.dumps(persisted)
    assert SECRET_SID not in json.dumps(summary)
    assert SECRET_PSIDTS not in json.dumps(summary)
    assert SECRET_TRACKER not in json.dumps(summary)
    assert ENC_TRACKER.hex() not in json.dumps(summary)
    assert "must-not-leak" not in json.dumps(summary)


def test_live_chromium_refresh_from_pathless_metadata_re_resolves_with_injected_decryptor(
    mods, home, live_home, monkeypatch
):
    first_store = _cookie_store(mods, live_home, profile="Default", modern=True)
    _build_chromium_db(first_store, _encrypted_rows())
    _write_local_state(mods, live_home, last_used="Default")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: live_home))
    profile_store = mods.profiles.ProfileStore(home)
    profile_store.create_profile("default")
    calls: list[tuple[str, str, bytes]] = []

    first = mods.browser_cookies.import_live_browser_to_storage_state(
        "chrome",
        dest_path=profile_store.storage_state_path("default"),
        os_name="macOS",
        decryptor=_fake_google_decryptor(calls),
    )
    mods.browser_cookies.write_auth_source(
        profile_store.auth_source_path("default"),
        mods.browser_cookies.build_auth_source_metadata(
            "chrome",
            source_kind=mods.browser_cookies.SOURCE_KIND_LIVE_BROWSER,
            os_name="macOS",
            browser_profile=first["browser_profile"],
            google_only=True,
        ),
    )

    refreshed = mods.browser_cookies.refresh_browser_cookies(
        "chrome",
        dest_path=profile_store.storage_state_path("default"),
        meta_path=profile_store.auth_source_path("default"),
        decryptor=_fake_google_decryptor(calls),
    )

    assert refreshed["refreshed"] is True
    assert refreshed["from_persisted_source"] is True
    assert refreshed["source_kind"] == "live_browser"
    assert refreshed["source_path"] is None
    assert refreshed["imported"] == 2
    assert refreshed["has_required_cookies"] is True
    assert str(live_home) not in json.dumps(refreshed)
    assert SECRET_SID not in json.dumps(refreshed)
    persisted = json.loads(
        profile_store.auth_source_path("default").read_text(encoding="utf-8")
    )
    assert persisted["cookie_store"] is None
    assert persisted["fixture_root"] is None


def test_live_chromium_import_and_refresh_require_decryptor_before_home(
    mods, home, monkeypatch
):
    def boom():
        raise AssertionError(
            "Path.home must not be consulted without an explicit decryptor"
        )

    monkeypatch.setattr(Path, "home", staticmethod(boom))
    dest = home / "profile" / "storage_state.json"
    meta = home / "profile" / "auth_source.json"
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text(
        json.dumps(
            mods.browser_cookies.build_auth_source_metadata(
                "chrome",
                source_kind=mods.browser_cookies.SOURCE_KIND_LIVE_BROWSER,
                os_name="macOS",
                browser_profile="Default",
            )
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        mods.errors.ValidationError, match="requires an explicit decryptor"
    ):
        mods.browser_cookies.import_live_browser_to_storage_state(
            "chrome", dest_path=dest, os_name="macOS"
        )
    with pytest.raises(
        mods.errors.ValidationError, match="requires an explicit decryptor"
    ):
        mods.browser_cookies.refresh_browser_cookies(
            "chrome", dest_path=dest, meta_path=meta
        )
    assert not dest.exists()


@pytest.mark.parametrize(
    "browser", ["brave", "chrome", "chromium", "edge", "opera", "opera-gx", "vivaldi"]
)
def test_live_linux_chromium_import_accepts_injected_decryptor(
    mods, home, live_home, monkeypatch, browser
):
    store = _cookie_store(mods, live_home, browser=browser, os_name="Ubuntu-LTS-Linux")
    _build_chromium_db(store, _encrypted_rows())
    monkeypatch.setattr(Path, "home", staticmethod(lambda: live_home))
    calls: list[tuple[str, str, bytes]] = []

    summary = mods.browser_cookies.import_live_browser_to_storage_state(
        browser,
        dest_path=home / "storage_state.json",
        os_name="Ubuntu-LTS-Linux",
        decryptor=_fake_google_decryptor(calls),
    )

    assert summary["imported"] == 2
    assert [name for _, name, _ in calls] == ["SID", "__Secure-1PSIDTS"]


def test_live_linux_chromium_fallback_wires_peanuts_without_secret_service(
    mods, home, live_home, capsys, monkeypatch
):
    store = _cookie_store(mods, live_home, browser="chrome", os_name="Ubuntu-LTS-Linux")
    _build_chromium_db(store, _encrypted_rows())
    monkeypatch.setattr(Path, "home", staticmethod(lambda: live_home))
    calls = []

    def no_secret_service(*args, **kwargs):
        raise AssertionError("Secret Service must not be touched")

    def fallback(blob, *, host, safe_storage_password, **kwargs):
        calls.append((host, safe_storage_password, bytes(blob)))
        return {ENC_SID: SECRET_SID, ENC_PSIDTS: SECRET_PSIDTS}.get(bytes(blob))

    monkeypatch.setattr(
        mods.browser_cookies._oscreds,
        "linux_chromium_secret_service_password",
        no_secret_service,
    )
    monkeypatch.setattr(
        mods.browser_cookies._oscreds,
        "linux_chromium_decrypt_cookie_value",
        fallback,
    )
    profile_store = mods.profiles.ProfileStore(home)
    profile_store.create_profile("default")

    imported = mods.browser_cookies.import_live_browser_to_storage_state(
        "chrome",
        dest_path=profile_store.storage_state_path("default"),
        os_name="Ubuntu-LTS-Linux",
        use_keychain=True,
    )
    mods.browser_cookies.write_auth_source(
        profile_store.auth_source_path("default"),
        mods.browser_cookies.build_auth_source_metadata(
            "chrome",
            source_kind=mods.browser_cookies.SOURCE_KIND_LIVE_BROWSER,
            os_name="Ubuntu-LTS-Linux",
            browser_profile=imported["browser_profile"],
            google_only=True,
        ),
    )
    refreshed = mods.browser_cookies.refresh_browser_cookies(
        "chrome",
        dest_path=profile_store.storage_state_path("default"),
        meta_path=profile_store.auth_source_path("default"),
        use_keychain=True,
    )

    async def discovered(jar):
        return [mods.browser_cookies._auth.Account(0, "chrome@example.com", True)]

    monkeypatch.setattr(mods.browser_cookies._auth, "enumerate_accounts", discovered)
    code = mods.cli.console(
        [
            "--storage",
            str(home),
            "auth",
            "inspect",
            "--browser",
            "chrome",
            "--os",
            "Ubuntu-LTS-Linux",
            "--json",
        ]
    )
    output = capsys.readouterr()
    out, err = output.out, output.err

    assert imported["imported"] == refreshed["imported"] == 2
    assert code == 0, err
    assert json.loads(out) == {
        "browser": "chrome",
        "accounts": [
            {
                "email": "chrome@example.com",
                "is_default": True,
                "browser_profile": None,
            }
        ],
    }
    assert {(host, password) for host, password, _ in calls} == {
        (".google.com", mods.browser_cookies._oscreds.LINUX_CHROMIUM_FALLBACK_PASSWORD)
    }
    assert all(blob != ENC_TRACKER for _, _, blob in calls)
    assert str(live_home) not in out
    assert SECRET_SID not in out


def test_live_linux_chromium_fallback_none_fails_closed(
    mods, home, live_home, monkeypatch
):
    store = _cookie_store(mods, live_home, browser="chrome", os_name="Ubuntu-LTS-Linux")
    _build_chromium_db(store, _encrypted_rows())
    monkeypatch.setattr(Path, "home", staticmethod(lambda: live_home))
    calls = []

    def fallback(blob, *, host, safe_storage_password, **kwargs):
        calls.append((host, safe_storage_password, bytes(blob)))
        return None

    monkeypatch.setattr(
        mods.browser_cookies._oscreds,
        "linux_chromium_decrypt_cookie_value",
        fallback,
    )

    summary = mods.browser_cookies.import_live_browser_to_storage_state(
        "chrome",
        dest_path=home / "storage_state.json",
        os_name="Ubuntu-LTS-Linux",
        use_keychain=True,
    )

    assert summary["imported"] == 0
    assert summary["blocked_count"] == 2
    assert {(host, password) for host, password, _ in calls} == {
        (".google.com", mods.browser_cookies._oscreds.LINUX_CHROMIUM_FALLBACK_PASSWORD)
    }


def test_live_linux_chromium_fallback_requires_host_digest(
    mods, home, live_home, monkeypatch
):
    store = _cookie_store(mods, live_home, browser="chrome", os_name="Ubuntu-LTS-Linux")
    _build_chromium_db(store, _encrypted_rows())
    monkeypatch.setattr(Path, "home", staticmethod(lambda: live_home))
    calls = []

    def fallback(blob, *, host, safe_storage_password, **kwargs):
        calls.append(kwargs)
        return {ENC_SID: SECRET_SID, ENC_PSIDTS: SECRET_PSIDTS}.get(bytes(blob))

    monkeypatch.setattr(
        mods.browser_cookies._oscreds,
        "linux_chromium_decrypt_cookie_value",
        fallback,
    )

    summary = mods.browser_cookies.import_live_browser_to_storage_state(
        "chrome",
        dest_path=home / "storage_state.json",
        os_name="Ubuntu-LTS-Linux",
        use_keychain=True,
    )

    assert summary["imported"] == 2
    assert calls == [{"require_host_digest": True}] * 2


def test_cli_live_linux_chromium_login_then_refresh_uses_pathless_peanuts_metadata(
    mods, home, live_home, capsys, monkeypatch
):
    store = _cookie_store(mods, live_home, browser="chrome", os_name="Ubuntu-LTS-Linux")
    _build_chromium_db(store, _encrypted_rows())
    monkeypatch.setattr(Path, "home", staticmethod(lambda: live_home))
    mods.profiles.ProfileStore(home).create_profile("default")
    calls = []

    def poisoned_secret_service(*args, **kwargs):
        raise AssertionError("Secret Service must not be touched")

    def fallback(blob, *, host, safe_storage_password, **kwargs):
        calls.append((host, safe_storage_password, kwargs, bytes(blob)))
        return {ENC_SID: SECRET_SID, ENC_PSIDTS: SECRET_PSIDTS}.get(bytes(blob))

    monkeypatch.setattr(
        mods.browser_cookies._oscreds,
        "linux_chromium_secret_service_password",
        poisoned_secret_service,
    )
    monkeypatch.setattr(
        mods.browser_cookies._oscreds,
        "linux_chromium_decrypt_cookie_value",
        fallback,
    )

    login_code = mods.cli.console(
        [
            "--storage",
            str(home),
            "login",
            "--browser-cookies",
            "chrome",
            "--os",
            "Ubuntu-LTS-Linux",
            "--json",
        ]
    )
    login = capsys.readouterr()
    refresh_code = mods.cli.console(
        [
            "--storage",
            str(home),
            "auth",
            "refresh",
            "--browser-cookies",
            "chrome",
            "--json",
        ]
    )
    refresh = capsys.readouterr()

    assert login_code == refresh_code == 0
    assert login.err == refresh.err == ""
    login_payload = json.loads(login.out)
    refresh_payload = json.loads(refresh.out)
    assert login_payload["source_path"] is None
    assert refresh_payload["refreshed"] is True
    assert refresh_payload["from_persisted_source"] is True
    assert refresh_payload["source_path"] is None
    assert all(
        (host, password, options)
        == (
            ".google.com",
            mods.browser_cookies._oscreds.LINUX_CHROMIUM_FALLBACK_PASSWORD,
            {"require_host_digest": True},
        )
        for host, password, options, _ in calls
    )
    public = login.out + refresh.out
    persisted = (home / "profiles" / "default" / "auth_source.json").read_text(
        encoding="utf-8"
    )
    for value in (
        str(live_home),
        str(store),
        SECRET_SID,
        SECRET_PSIDTS,
        SECRET_TRACKER,
    ):
        assert value not in public
        assert value not in persisted


def test_live_chromium_include_all_domains_refuses_to_decrypt_browsing_history(
    mods, home, live_home, monkeypatch
):
    store = _cookie_store(mods, live_home, profile="Default", modern=True)
    _build_chromium_db(store, _encrypted_rows())
    monkeypatch.setattr(Path, "home", staticmethod(lambda: live_home))

    with pytest.raises(mods.errors.ValidationError, match="include-all-domains"):
        mods.browser_cookies.import_live_browser_to_storage_state(
            "chrome",
            dest_path=home / "storage_state.json",
            os_name="macOS",
            google_only=False,
            decryptor=_fake_google_decryptor([]),
        )


def test_live_chromium_cli_still_refuses_without_keychain_gate(
    mods, home, live_home, capsys, monkeypatch
):
    store = _cookie_store(mods, live_home, profile="Default", modern=True)
    _build_chromium_db(store, _encrypted_rows())
    monkeypatch.setattr(Path, "home", staticmethod(lambda: live_home))
    mods.profiles.ProfileStore(home).create_profile("default")

    code = mods.cli.console(
        [
            "--storage",
            str(home),
            "login",
            "--browser-cookies",
            "chrome",
            "--json",
        ]
    )
    out = capsys.readouterr()

    assert code != 0
    assert out.out == ""
    assert (
        "explicit decryptor" in out.err
        or "keychain" in out.err.lower()
        or "firefox" in out.err
    )
    profile_store = mods.profiles.ProfileStore(home)
    assert not profile_store.storage_state_path("default").exists()
    assert not profile_store.auth_source_path("default").exists()
    assert str(live_home) not in out.err


def test_c2b_keeps_compat_and_dependency_boundary_clean(repo_root, mods):
    matrix = repo_root / "compat" / "auth_matrix.json"
    before = hashlib.sha256(matrix.read_bytes()).hexdigest()

    assert mods.import_origin_audit.audit(roots=("notebooklm",)) == []
    assert hashlib.sha256(matrix.read_bytes()).hexdigest() == before

    src = (repo_root / "notebooklm" / "browser_cookies.py").read_text(encoding="utf-8")
    tree = ast.parse(src, filename="browser_cookies.py")
    imported = []
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
    )
    for module in imported:
        assert not any(
            module == token or module.startswith(token + ".") for token in forbidden
        )
