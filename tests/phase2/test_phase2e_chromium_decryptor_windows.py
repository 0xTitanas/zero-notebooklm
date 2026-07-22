"""Phase 2E-C3A: Windows Chromium DPAPI decryptor primitives.

These tests are hermetic. They do not read real Windows DPAPI, Local State files,
browser DBs, network, home directories, or live paths. Fake DPAPI/AES-GCM
providers model the OS boundary so production code can be verified without
credential access.
"""

from __future__ import annotations

import ast
import base64
import ctypes
import hashlib
import importlib
import json
import sqlite3
import sys
import types
from pathlib import Path

import pytest

SECRET_LOCAL_STATE_KEY = b"phase2e-c3a-synthetic-windows-aes-key!!"[:32]
SECRET_PROTECTED_KEY = b"phase2e-c3a-synthetic-dpapi-protected-key"
SECRET_COOKIE_VALUE = "phase2e-c3a-synthetic-cookie-value"
SECRET_COOKIE_BYTES = SECRET_COOKIE_VALUE.encode("utf-8")
SECRET_WRONG_VALUE = "phase2e-c3a-wrong-host-cookie-value"
NONCE = b"123456789012"
TAG = b"tag-for-test-1234"[:16]
CIPHERTEXT = b"ciphertext-for-test"
WINDOWS_BLOB = b"v10" + NONCE + CIPHERTEXT + TAG
WINDOWS_BLOB_V11 = b"v11" + NONCE + CIPHERTEXT + TAG
WINDOWS_PSIDTS_VALUE = "phase2e-c3a-synthetic-psidts-cookie-value"
WINDOWS_PSIDTS_BYTES = WINDOWS_PSIDTS_VALUE.encode("utf-8")
WINDOWS_PSIDTS_BLOB = b"v10" + NONCE + b"psidts-ciphertext" + TAG
_UNIX_FAR_FUTURE = 1893456000
_CHROMIUM_EPOCH_OFFSET = 11644473600
CHROMIUM_EXPIRES_UTC = (_UNIX_FAR_FUTURE + _CHROMIUM_EPOCH_OFFSET) * 1_000_000
LOCAL_STATE = {
    "os_crypt": {
        "encrypted_key": base64.b64encode(b"DPAPI" + SECRET_PROTECTED_KEY).decode(
            "ascii"
        )
    }
}


@pytest.fixture
def mods(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))
    for name in (
        "notebooklm.os_credentials",
        "notebooklm.browser_cookies",
        "notebooklm.profiles",
        "notebooklm.cli",
    ):
        sys.modules.pop(name, None)
    return types.SimpleNamespace(
        browser_cookies=importlib.import_module("notebooklm.browser_cookies"),
        cli=importlib.import_module("notebooklm.cli"),
        os_credentials=importlib.import_module("notebooklm.os_credentials"),
        profiles=importlib.import_module("notebooklm.profiles"),
        import_origin_audit=importlib.import_module("import_origin_audit"),
    )


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _local_state_json() -> str:
    return json.dumps(LOCAL_STATE, sort_keys=True)


def _windows_data_dir(mods, root: Path, browser: str = "edge") -> Path:
    return root.joinpath(*mods.browser_cookies._CHROMIUM_DATA_DIR["windows"][browser])


def _windows_cookie_store(
    mods, root: Path, *, browser: str = "edge", profile: str = "Default"
) -> Path:
    return _windows_data_dir(mods, root, browser) / profile / "Network" / "Cookies"


def _write_windows_local_state(mods, root: Path, *, browser: str = "edge") -> None:
    data_dir = _windows_data_dir(mods, root, browser)
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "Local State").write_text(_local_state_json(), encoding="utf-8")


def _build_chromium_db(path: Path) -> None:
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
        for i, (name, blob) in enumerate(
            (("SID", WINDOWS_BLOB), ("__Secure-1PSIDTS", WINDOWS_PSIDTS_BLOB)),
            start=1,
        ):
            con.execute(
                "INSERT INTO cookies (creation_utc, host_key, name, value, path, "
                "expires_utc, is_secure, is_httponly, encrypted_value, samesite, "
                "is_persistent) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    i,
                    ".google.com",
                    name,
                    "",
                    "/",
                    CHROMIUM_EXPIRES_UTC,
                    1,
                    1,
                    blob,
                    -1,
                    1,
                ),
            )
        con.commit()
    finally:
        con.close()


def _install_fake_windows_dpapi(mods, monkeypatch, calls: list[tuple]):
    def fake_unprotect(protected: bytes):
        calls.append(("unprotect", protected))
        return SECRET_LOCAL_STATE_KEY

    def fake_aes(key: bytes, nonce: bytes, ciphertext: bytes, tag: bytes):
        calls.append(("aes", key, nonce, ciphertext, tag))
        if ciphertext == CIPHERTEXT:
            value = SECRET_COOKIE_BYTES
        else:
            value = WINDOWS_PSIDTS_BYTES
        return hashlib.sha256(b".google.com").digest() + value

    monkeypatch.setattr(mods.os_credentials, "windows_dpapi_unprotect", fake_unprotect)
    monkeypatch.setattr(mods.os_credentials, "windows_aes_gcm_decrypt", fake_aes)
    monkeypatch.setattr(
        mods.browser_cookies._oscreds, "windows_dpapi_unprotect", fake_unprotect
    )
    monkeypatch.setattr(
        mods.browser_cookies._oscreds, "windows_aes_gcm_decrypt", fake_aes
    )


def test_windows_local_state_unwraps_dpapi_key_with_injected_unprotect(mods):
    oc = mods.os_credentials
    calls = []

    def fake_unprotect(protected: bytes):
        calls.append(protected)
        return SECRET_LOCAL_STATE_KEY

    assert (
        oc.windows_chromium_key_from_local_state(
            _local_state_json(), unprotect_func=fake_unprotect
        )
        == SECRET_LOCAL_STATE_KEY
    )
    assert calls == [SECRET_PROTECTED_KEY]

    assert (
        oc.windows_chromium_key_from_local_state(
            LOCAL_STATE, unprotect_func=fake_unprotect
        )
        == SECRET_LOCAL_STATE_KEY
    )


def test_windows_local_state_key_rejects_malformed_or_unsupported_without_leaking(mods):
    oc = mods.os_credentials
    calls = []

    def fake_unprotect(protected: bytes):
        calls.append(protected)
        return SECRET_LOCAL_STATE_KEY

    malformed_inputs = [
        {},
        {"os_crypt": {}},
        {"os_crypt": {"encrypted_key": "not-base64!!!"}},
        {"os_crypt": {"encrypted_key": base64.b64encode(b"not-dpapi").decode("ascii")}},
        {
            "os_crypt": {
                "encrypted_key": base64.b64encode(
                    b"APPB" + SECRET_PROTECTED_KEY
                ).decode("ascii")
            }
        },
    ]
    for value in malformed_inputs:
        assert (
            oc.windows_chromium_key_from_local_state(
                value, unprotect_func=fake_unprotect
            )
            is None
        )
    assert calls == []

    def unavailable(protected: bytes):
        raise oc.CredentialUnavailableError("dpapi unavailable " + SECRET_COOKIE_VALUE)

    assert (
        oc.windows_chromium_key_from_local_state(
            LOCAL_STATE, unprotect_func=unavailable
        )
        is None
    )


def test_windows_cookie_decrypt_uses_aes_gcm_and_strips_matching_host_digest(mods):
    oc = mods.os_credentials
    calls = []

    def fake_aes(key: bytes, nonce: bytes, ciphertext: bytes, tag: bytes):
        calls.append((key, nonce, ciphertext, tag))
        return hashlib.sha256(b".google.com").digest() + SECRET_COOKIE_BYTES

    assert (
        oc.windows_chromium_decrypt_cookie_value(
            WINDOWS_BLOB,
            host=".google.com",
            aes_key=SECRET_LOCAL_STATE_KEY,
            aes_gcm_decrypt_func=fake_aes,
        )
        == SECRET_COOKIE_VALUE
    )
    assert calls == [(SECRET_LOCAL_STATE_KEY, NONCE, CIPHERTEXT, TAG)]

    assert (
        oc.windows_chromium_decrypt_cookie_value(
            WINDOWS_BLOB_V11,
            host=".google.com",
            aes_key=SECRET_LOCAL_STATE_KEY,
            aes_gcm_decrypt_func=fake_aes,
        )
        == SECRET_COOKIE_VALUE
    )


def test_windows_bcrypt_auth_info_uses_fixed_windows_field_sizes(mods):
    oc = mods.os_credentials

    fields = dict(oc._BcryptAuthInfo._fields_)
    assert fields["cbSize"] is ctypes.c_uint32
    assert fields["cbData"] is ctypes.c_uint64
    assert fields["dwFlags"] is ctypes.c_uint32


def test_windows_cookie_decrypt_rejects_malformed_bad_digest_or_undecodable(mods):
    oc = mods.os_credentials

    def fake_wrong_digest(key: bytes, nonce: bytes, ciphertext: bytes, tag: bytes):
        return hashlib.sha256(b".evil.test").digest() + SECRET_WRONG_VALUE.encode(
            "utf-8"
        )

    assert (
        oc.windows_chromium_decrypt_cookie_value(
            b"",
            host=".google.com",
            aes_key=SECRET_LOCAL_STATE_KEY,
            aes_gcm_decrypt_func=fake_wrong_digest,
        )
        is None
    )
    assert (
        oc.windows_chromium_decrypt_cookie_value(
            b"v10short",
            host=".google.com",
            aes_key=SECRET_LOCAL_STATE_KEY,
            aes_gcm_decrypt_func=fake_wrong_digest,
        )
        is None
    )
    assert (
        oc.windows_chromium_decrypt_cookie_value(
            WINDOWS_BLOB,
            host=".google.com",
            aes_key=b"",
            aes_gcm_decrypt_func=fake_wrong_digest,
        )
        is None
    )
    assert (
        oc.windows_chromium_decrypt_cookie_value(
            WINDOWS_BLOB,
            host=".google.com",
            aes_key=SECRET_LOCAL_STATE_KEY,
            aes_gcm_decrypt_func=fake_wrong_digest,
        )
        is None
    )

    def fake_bad_utf8(key: bytes, nonce: bytes, ciphertext: bytes, tag: bytes):
        return hashlib.sha256(b".google.com").digest() + b"\xff\xfe"

    assert (
        oc.windows_chromium_decrypt_cookie_value(
            WINDOWS_BLOB,
            host=".google.com",
            aes_key=SECRET_LOCAL_STATE_KEY,
            aes_gcm_decrypt_func=fake_bad_utf8,
        )
        is None
    )

    def fake_unavailable(key: bytes, nonce: bytes, ciphertext: bytes, tag: bytes):
        raise RuntimeError("aes-gcm leaked " + SECRET_COOKIE_VALUE)

    assert (
        oc.windows_chromium_decrypt_cookie_value(
            WINDOWS_BLOB,
            host=".google.com",
            aes_key=SECRET_LOCAL_STATE_KEY,
            aes_gcm_decrypt_func=fake_unavailable,
        )
        is None
    )


def test_resolve_decryptor_returns_windows_chromium_decryptor_only_with_explicit_local_state(
    mods,
):
    oc = mods.os_credentials
    calls = []

    def fake_unprotect(protected: bytes):
        calls.append(("unprotect", protected))
        return SECRET_LOCAL_STATE_KEY

    def fake_aes(key: bytes, nonce: bytes, ciphertext: bytes, tag: bytes):
        calls.append(("aes", key, nonce, ciphertext, tag))
        return hashlib.sha256(b".google.com").digest() + SECRET_COOKIE_BYTES

    decryptor = oc.resolve_decryptor(
        "Windows-11",
        "chrome",
        windows_local_state=_local_state_json(),
        windows_unprotect_func=fake_unprotect,
        windows_aes_gcm_decrypt_func=fake_aes,
    )
    assert callable(decryptor)
    assert (
        decryptor(WINDOWS_BLOB, host=".google.com", name="SID") == SECRET_COOKIE_VALUE
    )
    assert calls == [
        ("unprotect", SECRET_PROTECTED_KEY),
        ("aes", SECRET_LOCAL_STATE_KEY, NONCE, CIPHERTEXT, TAG),
    ]

    assert oc.resolve_decryptor("Windows-11", "chrome") is None
    assert (
        oc.resolve_decryptor(
            "Windows-11",
            "firefox",
            windows_local_state=_local_state_json(),
            windows_unprotect_func=fake_unprotect,
            windows_aes_gcm_decrypt_func=fake_aes,
        )
        is None
    )
    assert (
        oc.resolve_decryptor(
            "Ubuntu-LTS-Linux",
            "chrome",
            windows_local_state=_local_state_json(),
            windows_unprotect_func=fake_unprotect,
            windows_aes_gcm_decrypt_func=fake_aes,
        )
        is None
    )


def test_live_windows_chromium_import_uses_local_state_dpapi_gate(
    mods, tmp_path, monkeypatch
):
    live_home = tmp_path / "live-home"
    storage = tmp_path / "profile" / "storage_state.json"
    store = _windows_cookie_store(mods, live_home)
    _build_chromium_db(store)
    _write_windows_local_state(mods, live_home)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: live_home))
    calls: list[tuple] = []
    _install_fake_windows_dpapi(mods, monkeypatch, calls)

    summary = mods.browser_cookies.import_live_browser_to_storage_state(
        "edge",
        dest_path=storage,
        os_name="Windows-11",
        use_keychain=True,
    )

    assert summary["source_kind"] == "live_browser"
    assert summary["source_path"] is None
    assert summary["browser_profile"] == "Default"
    assert summary["imported"] == 2
    assert summary["has_required_cookies"] is True
    assert [c for c in calls if c[0] == "unprotect"] == [
        ("unprotect", SECRET_PROTECTED_KEY)
    ]
    assert len([c for c in calls if c[0] == "aes"]) == 2
    state = json.loads(storage.read_text(encoding="utf-8"))
    by_name = {c["name"]: c for c in state["cookies"]}
    assert by_name["SID"]["value"] == SECRET_COOKIE_VALUE
    assert by_name["__Secure-1PSIDTS"]["value"] == WINDOWS_PSIDTS_VALUE
    public = json.dumps(summary, sort_keys=True)
    assert str(live_home) not in public
    assert str(store) not in public
    assert SECRET_COOKIE_VALUE not in public
    assert WINDOWS_PSIDTS_VALUE not in public


def test_cli_live_windows_chromium_inspect_uses_dpapi_gate(
    mods, tmp_path, capsys, monkeypatch
):
    home = tmp_path / "nlm-home"
    live_home = tmp_path / "live-home"
    store = _windows_cookie_store(mods, live_home)
    _build_chromium_db(store)
    _write_windows_local_state(mods, live_home)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: live_home))
    calls: list[tuple] = []
    _install_fake_windows_dpapi(mods, monkeypatch, calls)

    async def discovered(jar):
        return [mods.browser_cookies._auth.Account(0, "edge@example.com", True)]

    monkeypatch.setattr(mods.browser_cookies._auth, "enumerate_accounts", discovered)

    code = mods.cli.console(
        [
            "--storage",
            str(home),
            "auth",
            "inspect",
            "--browser",
            "edge",
            "--os",
            "Windows-11",
            "--json",
        ]
    )
    out = capsys.readouterr()

    assert code == 0, out.err
    payload = json.loads(out.out)
    assert payload == {
        "browser": "edge",
        "accounts": [
            {
                "email": "edge@example.com",
                "is_default": True,
                "browser_profile": None,
            }
        ],
    }
    public = json.dumps(payload, sort_keys=True)
    assert str(live_home) not in public
    assert str(store) not in public
    assert SECRET_COOKIE_VALUE not in public
    assert WINDOWS_PSIDTS_VALUE not in public


def test_capability_reports_windows_dpapi_primitives_without_claiming_auto_access(mods):
    oc = mods.os_credentials

    cap = oc.decryptor_capability("Windows-11", "chrome")
    assert cap["requires_decryptor"] is True
    assert cap["automatic_available"] is False
    assert cap["primitive_available"] is True
    assert cap["uses_dpapi"] is True
    assert cap["uses_cng"] is True
    assert cap["dpapi_available"] is oc.windows_dpapi_available()
    assert SECRET_COOKIE_VALUE not in json.dumps(cap, sort_keys=True)

    linux = oc.decryptor_capability("Ubuntu-LTS-Linux", "chrome")
    assert linux["primitive_available"] is True
    assert linux.get("uses_dpapi") is False
    assert linux.get("uses_secret_service") is True


def test_c3a_keeps_compat_and_dependency_boundary_clean(repo_root, mods):
    matrix = repo_root / "compat" / "auth_matrix.json"
    before = hashlib.sha256(matrix.read_bytes()).hexdigest()

    assert mods.import_origin_audit.audit(roots=("notebooklm",)) == []
    assert hashlib.sha256(matrix.read_bytes()).hexdigest() == before

    src = (repo_root / "notebooklm" / "os_credentials.py").read_text(encoding="utf-8")
    tree = ast.parse(src, filename="os_credentials.py")
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
        "requests",
        "httpx",
        "urllib3",
        "aiohttp",
    )
    for module in imported:
        assert not any(
            module == token or module.startswith(token + ".") for token in forbidden
        )
    assert "Path.home" not in src and "expanduser" not in src
