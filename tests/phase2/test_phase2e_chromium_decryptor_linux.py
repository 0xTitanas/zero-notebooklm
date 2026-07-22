"""Phase 2E-C3B: Linux Chromium libsecret / Secret Service primitives.

These tests are hermetic. They do not read real Secret Service, D-Bus,
KWallet, browser DBs, network, home directories, or live paths. Injected lookup
and AES providers model the OS/crypto boundaries so production code can be
verified without credential access.
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import json
import sys
import types
from pathlib import Path

import pytest

SECRET_STORAGE_PASSWORD = "phase2e-c3b-synthetic-linux-safe-storage"
SECRET_COOKIE_VALUE = "phase2e-c3b-synthetic-cookie-value"
SECRET_COOKIE_BYTES = SECRET_COOKIE_VALUE.encode("utf-8")
SYNTHETIC_BLOB = b"v10" + b"synthetic-linux-ciphertext"

# NIST SP 800-38A AES-128-CBC example, with an extra full PKCS#7 block.
AES_KEY = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
AES_IV = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
AES_CIPHERTEXT = bytes.fromhex(
    "7649abac8119b246cee98e9b12e9197d8964e0b149c10b7b682e6e39aaeb731c"
)
AES_PLAINTEXT = bytes.fromhex("6bc1bee22e409f96e93d7e117393172a")


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


@pytest.fixture
def mods(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))
    for name in ("notebooklm.os_credentials", "notebooklm.browser_cookies"):
        sys.modules.pop(name, None)
    return types.SimpleNamespace(
        os_credentials=importlib.import_module("notebooklm.os_credentials"),
        import_origin_audit=importlib.import_module("import_origin_audit"),
    )


def test_linux_chromium_derive_key_matches_pbkdf2_one_iteration_and_peanuts(mods):
    oc = mods.os_credentials
    expected = hashlib.pbkdf2_hmac(
        "sha1", SECRET_STORAGE_PASSWORD.encode("utf-8"), b"saltysalt", 1, 16
    )
    peanuts_expected = hashlib.pbkdf2_hmac("sha1", b"peanuts", b"saltysalt", 1, 16)

    assert oc.linux_chromium_derive_key(SECRET_STORAGE_PASSWORD) == expected
    assert (
        oc.linux_chromium_derive_key(SECRET_STORAGE_PASSWORD.encode("utf-8"))
        == expected
    )
    assert oc.linux_chromium_peanuts_key() == peanuts_expected


def test_linux_aes128_cbc_decrypt_known_vector(mods):
    oc = mods.os_credentials

    assert (
        oc.linux_aes128_cbc_decrypt(AES_CIPHERTEXT, AES_KEY, iv=AES_IV) == AES_PLAINTEXT
    )
    assert oc.linux_aes128_cbc_decrypt(AES_CIPHERTEXT[:-1], AES_KEY, iv=AES_IV) is None
    assert oc.linux_aes128_cbc_decrypt(AES_CIPHERTEXT, b"short", iv=AES_IV) is None


def test_linux_v10_decoder_strips_matching_host_digest_with_injected_aes(mods):
    oc = mods.os_credentials
    calls = []

    def fake_aes(ciphertext: bytes, key: bytes, *, iv=oc.LINUX_CHROMIUM_AES_IV):
        calls.append((ciphertext, key, iv))
        return hashlib.sha256(b".google.com").digest() + SECRET_COOKIE_BYTES

    assert (
        oc.linux_chromium_decrypt_cookie_value(
            SYNTHETIC_BLOB,
            host=".google.com",
            safe_storage_password=SECRET_STORAGE_PASSWORD,
            aes_cbc_decrypt_func=fake_aes,
        )
        == SECRET_COOKIE_VALUE
    )
    assert calls == [
        (
            SYNTHETIC_BLOB[3:],
            oc.linux_chromium_derive_key(SECRET_STORAGE_PASSWORD),
            oc.LINUX_CHROMIUM_AES_IV,
        )
    ]


def test_linux_v10_decoder_rejects_malformed_wrong_digest_and_bad_utf8(mods):
    oc = mods.os_credentials

    def wrong_digest(ciphertext: bytes, key: bytes, *, iv=oc.LINUX_CHROMIUM_AES_IV):
        return hashlib.sha256(b".evil.test").digest() + SECRET_COOKIE_BYTES

    assert (
        oc.linux_chromium_decrypt_cookie_value(
            b"",
            host=".google.com",
            safe_storage_password=SECRET_STORAGE_PASSWORD,
            aes_cbc_decrypt_func=wrong_digest,
        )
        is None
    )
    assert (
        oc.linux_chromium_decrypt_cookie_value(
            b"v11bad",
            host=".google.com",
            safe_storage_password=SECRET_STORAGE_PASSWORD,
            aes_cbc_decrypt_func=wrong_digest,
        )
        is None
    )
    assert (
        oc.linux_chromium_decrypt_cookie_value(
            SYNTHETIC_BLOB,
            host=".google.com",
            safe_storage_password=SECRET_STORAGE_PASSWORD,
            aes_cbc_decrypt_func=wrong_digest,
        )
        is None
    )

    def bad_utf8(ciphertext: bytes, key: bytes, *, iv=oc.LINUX_CHROMIUM_AES_IV):
        return hashlib.sha256(b".google.com").digest() + b"\xff\xfe"

    assert (
        oc.linux_chromium_decrypt_cookie_value(
            SYNTHETIC_BLOB,
            host=".google.com",
            safe_storage_password=SECRET_STORAGE_PASSWORD,
            aes_cbc_decrypt_func=bad_utf8,
        )
        is None
    )

    def exploding(ciphertext: bytes, key: bytes, *, iv=oc.LINUX_CHROMIUM_AES_IV):
        raise RuntimeError("leaked " + SECRET_COOKIE_VALUE)

    assert (
        oc.linux_chromium_decrypt_cookie_value(
            SYNTHETIC_BLOB,
            host=".google.com",
            safe_storage_password=SECRET_STORAGE_PASSWORD,
            aes_cbc_decrypt_func=exploding,
        )
        is None
    )


def test_linux_v10_decoder_can_require_host_digest(mods):
    oc = mods.os_credentials

    def valid_utf8_wrong_digest(
        ciphertext: bytes, key: bytes, *, iv=oc.LINUX_CHROMIUM_AES_IV
    ):
        return b"x" * 32 + SECRET_COOKIE_BYTES

    assert (
        oc.linux_chromium_decrypt_cookie_value(
            SYNTHETIC_BLOB,
            host=".google.com",
            safe_storage_password=SECRET_STORAGE_PASSWORD,
            aes_cbc_decrypt_func=valid_utf8_wrong_digest,
        )
        == "x" * 32 + SECRET_COOKIE_VALUE
    )
    assert (
        oc.linux_chromium_decrypt_cookie_value(
            SYNTHETIC_BLOB,
            host=".google.com",
            safe_storage_password=SECRET_STORAGE_PASSWORD,
            aes_cbc_decrypt_func=valid_utf8_wrong_digest,
            require_host_digest=True,
        )
        is None
    )


def test_linux_secret_service_metadata_matches_chromium_libsecret_schema(mods):
    oc = mods.os_credentials

    chrome = oc.linux_chromium_secret_service_metadata("chrome")
    chromium = oc.linux_chromium_secret_service_metadata("chromium")
    assert chrome == {
        "schema": "chrome_libsecret_os_crypt_password_v2",
        "application": "chrome",
        "label": "Chrome Safe Storage",
        "folder": "Chrome Keys",
    }
    assert chromium == {
        "schema": "chrome_libsecret_os_crypt_password_v2",
        "application": "chromium",
        "label": "Chromium Safe Storage",
        "folder": "Chromium Keys",
    }
    assert oc.linux_chromium_secret_service_metadata("brave") is None


def test_linux_secret_service_password_uses_injected_lookup_and_redacts(mods):
    oc = mods.os_credentials
    calls = []

    def lookup(metadata):
        calls.append(metadata)
        return SECRET_STORAGE_PASSWORD

    assert (
        oc.linux_chromium_secret_service_password("chrome", lookup_func=lookup)
        == SECRET_STORAGE_PASSWORD
    )
    assert calls == [oc.linux_chromium_secret_service_metadata("chrome")]

    with pytest.raises(oc.CredentialUnavailableError) as missing_lookup:
        oc.linux_chromium_secret_service_password("chrome")
    assert SECRET_STORAGE_PASSWORD not in str(missing_lookup.value)
    assert "secret service password unavailable" in str(missing_lookup.value).lower()

    def denied(metadata):
        raise RuntimeError("denied " + SECRET_STORAGE_PASSWORD)

    with pytest.raises(oc.CredentialUnavailableError) as denied_exc:
        oc.linux_chromium_secret_service_password("chrome", lookup_func=denied)
    message = str(denied_exc.value)
    assert SECRET_STORAGE_PASSWORD not in message
    assert "secret service password unavailable" in message.lower()

    with pytest.raises(oc.CredentialUnavailableError) as unsupported:
        oc.linux_chromium_secret_service_password("brave", lookup_func=lookup)
    assert (
        "unsupported linux chromium secret service browser"
        in str(unsupported.value).lower()
    )
    assert "brave safe storage" not in str(unsupported.value).lower()


def test_resolve_decryptor_returns_linux_chrome_decryptor_only_with_explicit_secret(
    mods,
):
    oc = mods.os_credentials
    calls = []

    def fake_aes(ciphertext: bytes, key: bytes, *, iv=oc.LINUX_CHROMIUM_AES_IV):
        calls.append((ciphertext, key, iv))
        return hashlib.sha256(b".google.com").digest() + SECRET_COOKIE_BYTES

    decryptor = oc.resolve_decryptor(
        "Ubuntu-LTS-Linux",
        "chrome",
        linux_safe_storage_password=SECRET_STORAGE_PASSWORD,
        linux_aes_cbc_decrypt_func=fake_aes,
    )
    assert callable(decryptor)
    assert (
        decryptor(SYNTHETIC_BLOB, host=".google.com", name="SID") == SECRET_COOKIE_VALUE
    )
    assert calls == [
        (
            SYNTHETIC_BLOB[3:],
            oc.linux_chromium_derive_key(SECRET_STORAGE_PASSWORD),
            oc.LINUX_CHROMIUM_AES_IV,
        )
    ]

    assert oc.resolve_decryptor("Ubuntu-LTS-Linux", "chrome") is None
    assert (
        oc.resolve_decryptor(
            "Ubuntu-LTS-Linux",
            "brave",
            linux_safe_storage_password=SECRET_STORAGE_PASSWORD,
            linux_aes_cbc_decrypt_func=fake_aes,
        )
        is None
    )
    assert (
        oc.resolve_decryptor(
            "macOS",
            "chrome",
            linux_safe_storage_password=SECRET_STORAGE_PASSWORD,
            linux_aes_cbc_decrypt_func=fake_aes,
        )
        is None
    )


def test_capability_reports_linux_secret_service_primitives_without_claiming_auto_access(
    mods,
):
    oc = mods.os_credentials

    cap = oc.decryptor_capability("Ubuntu-LTS-Linux", "chrome")
    assert cap["requires_decryptor"] is True
    assert cap["automatic_available"] is False
    assert cap["primitive_available"] is True
    assert cap["secret_service_schema_known"] is True
    assert cap["uses_secret_service"] is True
    assert cap["uses_linux_aes"] is True
    assert cap["uses_peanuts_fallback"] is True
    assert SECRET_STORAGE_PASSWORD not in json.dumps(cap, sort_keys=True)

    brave = oc.decryptor_capability("Ubuntu-LTS-Linux", "brave")
    assert brave["requires_decryptor"] is True
    assert brave["primitive_available"] is False
    assert brave["secret_service_schema_known"] is False


def test_c3b_keeps_compat_and_dependency_boundary_clean(repo_root, mods):
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
        "jeepney",
        "dbus",
        "gi",
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
    assert "secret-tool" not in src
