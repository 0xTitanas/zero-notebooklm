"""Phase 2E-C2A: macOS Chromium decryptor primitives.

These tests are hermetic. They do not read the real Keychain, browser DBs,
network, home directory, or live paths. Fake runners/providers model the OS
boundary so the production code can be verified without credential access.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

SECRET_PASSWORD = "synthetic-safe-storage-password"
SECRET_PASSWORD_BYTES = SECRET_PASSWORD.encode("utf-8")
SECRET_COOKIE_VALUE = "synthetic-cookie-value"
SECRET_COOKIE_BYTES = SECRET_COOKIE_VALUE.encode("utf-8")
SYNTHETIC_BLOB = b"v10" + b"synthetic-ciphertext-blocks"

# NIST SP 800-38A AES-128-CBC example, with an extra full PKCS#7 block.
# Key: 2b7e151628aed2a6abf7158809cf4f3c
# IV:  000102030405060708090a0b0c0d0e0f
# Plaintext: 6bc1bee22e409f96e93d7e117393172a + 16 bytes of 0x10 padding
# Ciphertext produced with AES-128-CBC + PKCS#7 padding semantics.
AES_KEY = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
AES_IV = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
AES_CIPHERTEXT = bytes.fromhex(
    "7649abac8119b246cee98e9b12e9197d8964e0b149c10b7b682e6e39aaeb731c"
)
AES_PLAINTEXT = bytes.fromhex("6bc1bee22e409f96e93d7e117393172a")


@pytest.fixture
def mods():
    names = ["notebooklm.os_credentials", "notebooklm.browser_cookies"]
    for name in names:
        sys.modules.pop(name, None)
    return types.SimpleNamespace(
        os_credentials=importlib.import_module("notebooklm.os_credentials"),
        import_origin_audit=importlib.import_module("import_origin_audit"),
    )


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_macos_chromium_derive_key_matches_chromium_pbkdf2(mods):
    oc = mods.os_credentials
    expected = hashlib.pbkdf2_hmac(
        "sha1", SECRET_PASSWORD_BYTES, b"saltysalt", 1003, 16
    )

    assert oc.macos_chromium_derive_key(SECRET_PASSWORD) == expected
    assert oc.macos_chromium_derive_key(SECRET_PASSWORD_BYTES) == expected


def test_commoncrypto_aes128_cbc_decrypt_known_vector(mods):
    oc = mods.os_credentials

    if not oc.macos_commoncrypto_available():
        pytest.skip("CommonCrypto is not available on this host")

    assert (
        oc.macos_aes128_cbc_decrypt(AES_CIPHERTEXT, AES_KEY, iv=AES_IV) == AES_PLAINTEXT
    )


def test_v10_decoder_strips_matching_host_digest_with_fake_aes(mods, monkeypatch):
    oc = mods.os_credentials
    payload = hashlib.sha256(b".google.com").digest() + SECRET_COOKIE_BYTES

    def fake_aes(ciphertext, key, *, iv=oc.MACOS_CHROMIUM_AES_IV):
        assert ciphertext == SYNTHETIC_BLOB[3:]
        assert key == oc.macos_chromium_derive_key(SECRET_PASSWORD)
        assert iv == oc.MACOS_CHROMIUM_AES_IV
        return payload

    monkeypatch.setattr(oc, "macos_aes128_cbc_decrypt", fake_aes)

    assert (
        oc.macos_chromium_decrypt_cookie_value(
            SYNTHETIC_BLOB, host=".google.com", safe_storage_password=SECRET_PASSWORD
        )
        == SECRET_COOKIE_VALUE
    )


def test_v10_decoder_accepts_legacy_payload_without_host_digest(mods, monkeypatch):
    oc = mods.os_credentials
    long_legacy_value = "legacy-cookie-value-longer-than-thirty-two-bytes"

    monkeypatch.setattr(
        oc,
        "macos_aes128_cbc_decrypt",
        lambda ciphertext, key, *, iv=oc.MACOS_CHROMIUM_AES_IV: (
            long_legacy_value.encode("utf-8")
        ),
    )

    assert (
        oc.macos_chromium_decrypt_cookie_value(
            SYNTHETIC_BLOB, host=".google.com", safe_storage_password=SECRET_PASSWORD
        )
        == long_legacy_value
    )


def test_v10_decoder_returns_none_for_malformed_unsupported_or_bad_digest(
    mods, monkeypatch
):
    oc = mods.os_credentials

    assert (
        oc.macos_chromium_decrypt_cookie_value(
            b"", host=".google.com", safe_storage_password=SECRET_PASSWORD
        )
        is None
    )
    assert (
        oc.macos_chromium_decrypt_cookie_value(
            b"v11bad", host=".google.com", safe_storage_password=SECRET_PASSWORD
        )
        is None
    )

    wrong_digest_payload = hashlib.sha256(b".evil.test").digest() + SECRET_COOKIE_BYTES
    monkeypatch.setattr(
        oc,
        "macos_aes128_cbc_decrypt",
        lambda ciphertext, key, *, iv=oc.MACOS_CHROMIUM_AES_IV: wrong_digest_payload,
    )
    assert (
        oc.macos_chromium_decrypt_cookie_value(
            SYNTHETIC_BLOB, host=".google.com", safe_storage_password=SECRET_PASSWORD
        )
        is None
    )

    monkeypatch.setattr(
        oc,
        "macos_aes128_cbc_decrypt",
        lambda ciphertext, key, *, iv=oc.MACOS_CHROMIUM_AES_IV: None,
    )
    assert (
        oc.macos_chromium_decrypt_cookie_value(
            SYNTHETIC_BLOB, host=".google.com", safe_storage_password=SECRET_PASSWORD
        )
        is None
    )


@pytest.mark.parametrize(
    ("browser", "service", "account"),
    [
        ("arc", "Arc Safe Storage", "Arc"),
        ("brave", "Brave Safe Storage", "Brave"),
        ("chrome", "Chrome Safe Storage", "Chrome"),
        ("chromium", "Chromium Safe Storage", "Chromium"),
        ("edge", "Microsoft Edge Safe Storage", "Microsoft Edge"),
        ("opera", "Opera Safe Storage", "Opera"),
        ("opera-gx", "Opera Safe Storage", "Opera"),
        ("vivaldi", "Vivaldi Safe Storage", "Vivaldi"),
    ],
)
def test_keychain_password_uses_absolute_security_argv_no_shell_timeout_and_redacts(
    mods, browser, service, account
):
    oc = mods.os_credentials
    calls = []

    def fake_runner(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(
            argv, 0, stdout=SECRET_PASSWORD + "\n", stderr=""
        )

    assert (
        oc.macos_chromium_keychain_password(browser, runner=fake_runner)
        == SECRET_PASSWORD
    )
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv == [
        "/usr/bin/security",
        "find-generic-password",
        "-w",
        "-s",
        service,
        "-a",
        account,
    ]
    assert kwargs.get("shell") in (None, False)
    assert isinstance(kwargs.get("timeout"), (int, float)) and kwargs["timeout"] > 0
    assert kwargs.get("text") is True
    assert kwargs.get("capture_output") is True

    def denied_runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv, 44, stdout=SECRET_PASSWORD, stderr=SECRET_PASSWORD
        )

    with pytest.raises(oc.CredentialUnavailableError) as exc:
        oc.macos_chromium_keychain_password(browser, runner=denied_runner)
    message = str(exc.value)
    assert SECRET_PASSWORD not in message
    assert "safe storage password unavailable" in message.lower()


def test_unknown_chromium_browser_keychain_mapping_is_blocked_not_guessed(mods):
    oc = mods.os_credentials

    assert oc.macos_chromium_keychain_service("octo") is None
    with pytest.raises(oc.CredentialUnavailableError) as exc:
        oc.macos_chromium_keychain_password("octo", runner=lambda *a, **k: None)
    assert "unsupported macos chromium keychain browser" in str(exc.value).lower()
    assert "octo safe storage" not in str(exc.value).lower()


def test_resolve_decryptor_returns_macos_chrome_decryptor_only_with_explicit_password(
    mods,
):
    oc = mods.os_credentials

    decryptor = oc.resolve_decryptor(
        "macOS", "chrome", safe_storage_password=SECRET_PASSWORD
    )
    assert callable(decryptor)

    assert callable(
        oc.resolve_decryptor("macOS", "brave", safe_storage_password=SECRET_PASSWORD)
    )
    assert (
        oc.resolve_decryptor(
            "Windows-11", "chrome", safe_storage_password=SECRET_PASSWORD
        )
        is None
    )
    assert (
        oc.resolve_decryptor(
            "Ubuntu-LTS-Linux", "chrome", safe_storage_password=SECRET_PASSWORD
        )
        is None
    )
    assert oc.resolve_decryptor("macOS", "chrome") is None


def test_capability_reports_macos_primitives_without_claiming_live_auto_access(mods):
    oc = mods.os_credentials

    cap = oc.decryptor_capability("macOS", "chrome")
    assert cap["requires_decryptor"] is True
    assert cap["automatic_available"] is False
    assert cap["primitive_available"] is True
    assert cap["keychain_service_known"] is True
    assert cap["uses_keychain"] is True
    assert cap["uses_commoncrypto"] is True
    assert cap["commoncrypto_available"] is oc.macos_commoncrypto_available()
    assert SECRET_PASSWORD not in json.dumps(cap)


def test_c2a_does_not_mutate_auth_matrix_or_add_forbidden_runtime_dependencies(
    repo_root, mods
):
    matrix = repo_root / "compat" / "auth_matrix.json"
    before = matrix.read_bytes()

    assert mods.import_origin_audit.audit(roots=("notebooklm",)) == []
    assert matrix.read_bytes() == before

    src = (repo_root / "notebooklm" / "os_credentials.py").read_text(encoding="utf-8")
    import ast

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
        "browser_cookie3",
        "rookiepy",
    )
    for module in imported:
        assert not any(
            module == token or module.startswith(token + ".") for token in forbidden
        )
    assert "Path.home" not in src and "expanduser" not in src
