"""OS-credential / cookie-decryptor boundary for browser-cookie auth.

Chromium-family browsers store their cookie *values* encrypted in the ``Cookies``
SQLite database (the ``encrypted_value`` column). Recovering plaintext requires a
browser/OS-specific crypto primitive and OS credential material: macOS Chromium
uses a Keychain-derived AES-128-CBC key, Windows Chromium uses a DPAPI-unwrapped
AES-GCM key from Local State, and Linux Chromium uses a Secret Service / keyring
password-derived AES-128-CBC key.

This module exposes the extractor's decryptor callback contract and, as of Phase
2E-C3B, the **macOS, Windows, and Linux primitive foundations**:

  * PBKDF2-HMAC-SHA1 key derivation for verified Chromium-family Safe Storage
    material;
  * AES-128-CBC via macOS CommonCrypto reached through stdlib ``ctypes``;
  * Linux AES-128-CBC via a stdlib pure-Python primitive, plus deterministic
    Chromium libsecret schema metadata and injected Secret Service lookup seam;
  * a redacted, test-injectable ``/usr/bin/security`` command wrapper for live
    macOS Keychain use;
  * Windows Local State `DPAPI` key unwrap and AES-GCM cookie-value decoding via
    stdlib ``ctypes`` CNG/DPAPI boundaries.

It performs no implicit credential lookup during capability checks or ordinary
resolver calls. Live Chromium import/refresh is separately gated by caller scope;
callers must pass explicit credential material to obtain a decryptor. No
key/password, derived key, cookie value, encrypted blob, token, email, or live
path is printed, persisted, or included in diagnostics by this module.

The ``Decryptor`` callable contract is::

    decryptor(encrypted_value: bytes, *, host: str, name: str) -> str | None

returning the decrypted cookie value, or ``None`` when the row cannot be
decrypted (the extractor then reports the row as ``blocked`` without ever
exposing the encrypted bytes).
"""

from __future__ import annotations

import base64
import ctypes
import ctypes.util
import hashlib
import json
import subprocess
from functools import lru_cache
from typing import Callable, Optional

# A decryptor turns an ``encrypted_value`` blob into a plaintext cookie value, or
# returns ``None`` if it cannot (e.g. wrong/absent key). It must never raise on
# undecryptable input — it returns ``None`` so the caller can mark the row blocked.
Decryptor = Callable[..., Optional[str]]

MACOS_CHROMIUM_SALT = b"saltysalt"
MACOS_CHROMIUM_PBKDF2_ITERATIONS = 1003
MACOS_CHROMIUM_KEY_LENGTH = 16
MACOS_CHROMIUM_AES_IV = b" " * 16
LINUX_CHROMIUM_SALT = MACOS_CHROMIUM_SALT
LINUX_CHROMIUM_PBKDF2_ITERATIONS = 1
LINUX_CHROMIUM_KEY_LENGTH = 16
LINUX_CHROMIUM_AES_IV = MACOS_CHROMIUM_AES_IV
LINUX_CHROMIUM_FALLBACK_PASSWORD = "peanuts"
_CHROMIUM_ENCRYPTED_VALUE_PREFIX = b"v10"
_WINDOWS_CHROMIUM_ENCRYPTED_VALUE_PREFIXES = (b"v10", b"v11")
_WINDOWS_CHROMIUM_DPAPI_KEY_PREFIX = b"DPAPI"
_WINDOWS_CHROMIUM_APP_BOUND_KEY_PREFIX = b"APPB"
_WINDOWS_CHROMIUM_GCM_NONCE_LENGTH = 12
_WINDOWS_CHROMIUM_GCM_TAG_LENGTH = 16
_KEYCHAIN_TIMEOUT_SECONDS = 10

_MACOS_CHROMIUM_KEYCHAIN = {
    "arc": ("Arc Safe Storage", "Arc"),
    "brave": ("Brave Safe Storage", "Brave"),
    "chrome": ("Chrome Safe Storage", "Chrome"),
    "chromium": ("Chromium Safe Storage", "Chromium"),
    "edge": ("Microsoft Edge Safe Storage", "Microsoft Edge"),
    "opera": ("Opera Safe Storage", "Opera"),
    "opera-gx": ("Opera Safe Storage", "Opera"),
    "vivaldi": ("Vivaldi Safe Storage", "Vivaldi"),
}

_LINUX_CHROMIUM_LIBSECRET_SCHEMA = "chrome_libsecret_os_crypt_password_v2"
_LINUX_CHROMIUM_SECRET_SERVICE = {
    "chrome": {
        "schema": _LINUX_CHROMIUM_LIBSECRET_SCHEMA,
        "application": "chrome",
        "label": "Chrome Safe Storage",
        "folder": "Chrome Keys",
    },
    "chromium": {
        "schema": _LINUX_CHROMIUM_LIBSECRET_SCHEMA,
        "application": "chromium",
        "label": "Chromium Safe Storage",
        "folder": "Chromium Keys",
    },
}


class CredentialUnavailableError(RuntimeError):
    """Raised when an OS credential boundary cannot provide safe redacted data."""


# Honest, machine-stable reasons for why no automatic decryptor exists. These are
# safe to surface in diagnostics; they describe a *capability boundary*, never a
# secret.
REASON_NO_STDLIB_AES = (
    "chromium cookie values are AES-encrypted; the Python standard library "
    "provides no AES primitive, so no stdlib-only decryptor is available"
)
REASON_NO_KEYCHAIN_ACCESS = (
    "the decryption key lives behind the OS credential store (macOS Keychain, "
    "Windows DPAPI, or Linux Secret Service), which the offline foundation does "
    "not access"
)
# Firefox and Safari store their cookie *values* in plaintext, so no decryptor is
# ever required to read them — this is a capability statement, never a secret.
REASON_PLAINTEXT_VALUES = (
    "this browser stores cookie values in plaintext, so no decryptor is required"
)
# An unrecognized browser cannot be classified, so capability is reported unknown.
REASON_UNKNOWN_BROWSER = (
    "unrecognized cookie browser; cookie-value encryption could not be classified"
)

# Per-OS description of the credential store that would be required. Used only to
# explain the boundary in diagnostics — nothing here is read.
_OS_KEY_BACKEND = {
    "macos": "macOS Keychain (Chrome Safe Storage)",
    "macOS": "macOS Keychain (Chrome Safe Storage)",
    "linux": "Linux Secret Service / kwallet (or a hardcoded 'peanuts' key)",
    "Ubuntu-LTS-Linux": "Linux Secret Service / kwallet (or a hardcoded 'peanuts' key)",
    "windows": "Windows DPAPI (Local State app-bound key)",
    "Windows-11": "Windows DPAPI (Local State app-bound key)",
}

# Canonical OS labels (mirrors compat/auth_matrix.json ``os_rows``) used to build
# the deterministic capability matrix. Pure constants — nothing here is probed.
OS_LABELS = ("macOS", "Ubuntu-LTS-Linux", "Windows-11")

# Per-OS credential backend a *future* real decryptor would have to reach. These
# describe the boundary only; this module never opens any of them.
OS_KEY_BACKENDS = {
    "macOS": _OS_KEY_BACKEND["macOS"],
    "Ubuntu-LTS-Linux": _OS_KEY_BACKEND["Ubuntu-LTS-Linux"],
    "Windows-11": _OS_KEY_BACKEND["Windows-11"],
}

# Cookie-value encryption partition. These mirror
# :data:`notebooklm.browser_cookies.CHROMIUM_FAMILY` / ``FIREFOX`` / ``SAFARI``;
# they are duplicated here (rather than imported) to avoid an import cycle, and a
# Phase 2C test cross-checks the two sets so they cannot silently drift.
ENCRYPTED_COOKIE_BROWSERS = (
    "arc",
    "brave",
    "chrome",
    "chromium",
    "edge",
    "opera",
    "opera-gx",
    "vivaldi",
)
PLAINTEXT_COOKIE_BROWSERS = ("firefox", "safari")
COOKIE_BROWSERS = tuple(sorted(ENCRYPTED_COOKIE_BROWSERS + PLAINTEXT_COOKIE_BROWSERS))

# A few common aliases so capability lookups accept upstream-style names without
# importing the browser-cookie normalizer (kept intentionally tiny).
_BROWSER_ALIASES = {
    "google-chrome": "chrome",
    "google chrome": "chrome",
    "msedge": "edge",
    "microsoft-edge": "edge",
    "operagx": "opera-gx",
    "opera_gx": "opera-gx",
    "ff": "firefox",
    "mozilla": "firefox",
    "mozilla-firefox": "firefox",
}

__all__ = [
    "Decryptor",
    "CredentialUnavailableError",
    "MACOS_CHROMIUM_SALT",
    "MACOS_CHROMIUM_PBKDF2_ITERATIONS",
    "MACOS_CHROMIUM_KEY_LENGTH",
    "MACOS_CHROMIUM_AES_IV",
    "LINUX_CHROMIUM_SALT",
    "LINUX_CHROMIUM_PBKDF2_ITERATIONS",
    "LINUX_CHROMIUM_KEY_LENGTH",
    "LINUX_CHROMIUM_AES_IV",
    "LINUX_CHROMIUM_FALLBACK_PASSWORD",
    "REASON_NO_STDLIB_AES",
    "REASON_NO_KEYCHAIN_ACCESS",
    "REASON_PLAINTEXT_VALUES",
    "REASON_UNKNOWN_BROWSER",
    "OS_LABELS",
    "OS_KEY_BACKENDS",
    "ENCRYPTED_COOKIE_BROWSERS",
    "PLAINTEXT_COOKIE_BROWSERS",
    "COOKIE_BROWSERS",
    "macos_commoncrypto_available",
    "macos_aes128_cbc_decrypt",
    "macos_chromium_derive_key",
    "macos_chromium_decrypt_cookie_value",
    "macos_chromium_keychain_service",
    "macos_chromium_keychain_password",
    "linux_aes128_cbc_decrypt",
    "linux_chromium_derive_key",
    "linux_chromium_peanuts_key",
    "linux_chromium_decrypt_cookie_value",
    "linux_chromium_secret_service_metadata",
    "linux_chromium_secret_service_password",
    "windows_dpapi_available",
    "windows_dpapi_unprotect",
    "windows_aes_gcm_decrypt",
    "windows_chromium_key_from_local_state",
    "windows_chromium_decrypt_cookie_value",
    "resolve_decryptor",
    "decryptor_status",
    "supported_backends",
    "decryptor_capability",
    "decryptor_matrix",
]


def resolve_decryptor(
    os_name: str,
    browser: str,
    *,
    safe_storage_password: str | bytes | None = None,
    linux_safe_storage_password: str | bytes | None = None,
    linux_aes_cbc_decrypt_func=None,
    windows_local_state=None,
    windows_unprotect_func=None,
    windows_aes_gcm_decrypt_func=None,
) -> Optional[Decryptor]:
    """Return an automatic cookie-value decryptor for ``(os_name, browser)``.

    This resolver still performs no ambient OS credential lookup. macOS callers
    must pass explicit Safe Storage material obtained at the separately gated
    Keychain boundary. Linux callers must pass explicit Secret Service material;
    the lookup seam is injectable and never consulted ambiently. Windows callers
    must pass explicit Chromium ``Local State`` material; DPAPI/AES-GCM providers
    are injectable for hermetic tests and fall closed when unavailable.
    """

    canon = _canon_browser(browser)
    if os_name in {"macos", "macOS"}:
        if (
            safe_storage_password is None
            or macos_chromium_keychain_service(canon) is None
        ):
            return None

        def decryptor(encrypted_value: bytes, *, host: str, name: str) -> Optional[str]:
            return macos_chromium_decrypt_cookie_value(
                encrypted_value,
                host=host,
                safe_storage_password=safe_storage_password,
            )

        return decryptor

    if os_name in {"linux", "Ubuntu-LTS-Linux"} and canon in ENCRYPTED_COOKIE_BROWSERS:
        if (
            linux_safe_storage_password is None
            or linux_chromium_secret_service_metadata(canon) is None
        ):
            return None
        aes_func = linux_aes_cbc_decrypt_func or linux_aes128_cbc_decrypt

        def decryptor(encrypted_value: bytes, *, host: str, name: str) -> Optional[str]:
            return linux_chromium_decrypt_cookie_value(
                encrypted_value,
                host=host,
                safe_storage_password=linux_safe_storage_password,
                aes_cbc_decrypt_func=aes_func,
            )

        return decryptor

    if os_name in {"windows", "Windows-11"} and canon in ENCRYPTED_COOKIE_BROWSERS:
        if windows_local_state is None:
            return None
        key = windows_chromium_key_from_local_state(
            windows_local_state,
            unprotect_func=windows_unprotect_func or windows_dpapi_unprotect,
        )
        if key is None:
            return None
        aes_func = windows_aes_gcm_decrypt_func or windows_aes_gcm_decrypt

        def decryptor(encrypted_value: bytes, *, host: str, name: str) -> Optional[str]:
            return windows_chromium_decrypt_cookie_value(
                encrypted_value,
                host=host,
                aes_key=key,
                aes_gcm_decrypt_func=aes_func,
            )

        return decryptor

    return None


def decryptor_status(os_name: str, browser: str) -> dict:
    """Return a redacted, deterministic description of the decryptor boundary."""

    cap = decryptor_capability(os_name, browser)
    backend = _OS_KEY_BACKEND.get(os_name, "an OS-specific credential store")
    if cap.get("primitive_available"):
        reason = REASON_NO_KEYCHAIN_ACCESS
        requires = [REASON_NO_KEYCHAIN_ACCESS]
    elif cap.get("requires_decryptor") is False:
        reason = REASON_PLAINTEXT_VALUES
        requires = [REASON_PLAINTEXT_VALUES]
    elif cap.get("requires_decryptor") is None:
        reason = REASON_UNKNOWN_BROWSER
        requires = [REASON_UNKNOWN_BROWSER]
    else:
        reason = REASON_NO_STDLIB_AES
        requires = [REASON_NO_STDLIB_AES, REASON_NO_KEYCHAIN_ACCESS]
    return {
        "available": False,
        "reason": reason,
        "requires": requires,
        "os_key_backend": backend,
        "primitive_available": bool(cap.get("primitive_available")),
        "keychain_service_known": bool(cap.get("keychain_service_known")),
    }


def _canon_browser(browser: str) -> str:
    key = (browser or "").strip().lower()
    return _BROWSER_ALIASES.get(key, key)


@lru_cache(maxsize=1)
def _commoncrypto_lib():
    """Load macOS CommonCrypto through the platform dynamic loader.

    This is a first-party macOS crypto provider reached through stdlib ``ctypes``;
    no third-party Python crypto dependency is imported. ``None`` means the host is
    not macOS/CommonCrypto-capable, and callers fail closed.
    """

    names = []
    found = ctypes.util.find_library("CommonCrypto")
    if found:
        names.append(found)
    names.extend(
        (
            "/usr/lib/system/libcommonCrypto.dylib",
            "libcommonCrypto.dylib",
        )
    )
    for name in names:
        try:
            lib = ctypes.CDLL(name)
        except OSError:
            continue
        try:
            cccrypt = lib.CCCrypt
        except AttributeError:
            continue
        cccrypt.argtypes = [
            ctypes.c_uint32,  # CCOperation
            ctypes.c_uint32,  # CCAlgorithm
            ctypes.c_uint32,  # CCOptions
            ctypes.c_void_p,  # key
            ctypes.c_size_t,  # keyLength
            ctypes.c_void_p,  # iv
            ctypes.c_void_p,  # dataIn
            ctypes.c_size_t,  # dataInLength
            ctypes.c_void_p,  # dataOut
            ctypes.c_size_t,  # dataOutAvailable
            ctypes.POINTER(ctypes.c_size_t),  # dataOutMoved
        ]
        cccrypt.restype = ctypes.c_int
        return lib
    return None


def macos_commoncrypto_available() -> bool:
    """Return whether CommonCrypto can be loaded; never probes credentials."""

    return _commoncrypto_lib() is not None


def macos_aes128_cbc_decrypt(
    ciphertext: bytes, key: bytes, *, iv: bytes = MACOS_CHROMIUM_AES_IV
) -> Optional[bytes]:
    """Decrypt AES-128-CBC with PKCS#7 padding using macOS CommonCrypto.

    Returns ``None`` for unavailable CommonCrypto, malformed inputs, bad padding,
    or crypto failure. It never raises with caller-provided bytes in the message.
    """

    data = bytes(ciphertext or b"")
    key_b = bytes(key or b"")
    iv_b = bytes(iv or b"")
    if not data or len(data) % 16 != 0 or len(key_b) != 16 or len(iv_b) != 16:
        return None
    lib = _commoncrypto_lib()
    if lib is None:
        return None

    # CommonCrypto constants.
    k_cc_decrypt = 1
    k_cc_algorithm_aes = 0
    k_cc_option_pkcs7_padding = 1

    key_buf = ctypes.create_string_buffer(key_b, len(key_b))
    iv_buf = ctypes.create_string_buffer(iv_b, len(iv_b))
    data_buf = ctypes.create_string_buffer(data, len(data))
    out_buf = ctypes.create_string_buffer(len(data) + 16)
    moved = ctypes.c_size_t(0)

    status = lib.CCCrypt(
        k_cc_decrypt,
        k_cc_algorithm_aes,
        k_cc_option_pkcs7_padding,
        key_buf,
        len(key_b),
        iv_buf,
        data_buf,
        len(data),
        out_buf,
        len(out_buf),
        ctypes.byref(moved),
    )
    if status != 0:
        return None
    return out_buf.raw[: moved.value]


def macos_chromium_derive_key(safe_storage_password: str | bytes) -> bytes:
    """Derive the macOS Chromium AES-128-CBC key from Safe Storage text."""

    if isinstance(safe_storage_password, bytes):
        secret = safe_storage_password
    else:
        secret = str(safe_storage_password).encode("utf-8")
    return hashlib.pbkdf2_hmac(
        "sha1",
        secret,
        MACOS_CHROMIUM_SALT,
        MACOS_CHROMIUM_PBKDF2_ITERATIONS,
        MACOS_CHROMIUM_KEY_LENGTH,
    )


def _decode_chromium_plaintext(
    payload: bytes, host: str, *, require_host_digest: bool = False
) -> Optional[str]:
    host_digest = hashlib.sha256(str(host or "").encode("utf-8")).digest()
    if payload.startswith(host_digest):
        # Chrome DB v24+ prefixes SHA256(host_key). Older macOS Chromium cookies
        # do not, so only strip on a positive match; non-matching payloads remain
        # legacy candidates and are accepted when they decode cleanly.
        payload = payload[len(host_digest) :]
    elif require_host_digest:
        return None
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError:
        return None


def macos_chromium_decrypt_cookie_value(
    encrypted_value: bytes, *, host: str, safe_storage_password: str | bytes
) -> Optional[str]:
    """Decode a macOS Chromium ``v10`` encrypted cookie value.

    This primitive is intentionally quiet: malformed/unsupported blobs, unavailable
    CommonCrypto, wrong keys, bad padding, and undecodable plaintext return ``None``.
    """

    blob = bytes(encrypted_value or b"")
    if not blob.startswith(_CHROMIUM_ENCRYPTED_VALUE_PREFIX) or len(blob) <= 3:
        return None
    key = macos_chromium_derive_key(safe_storage_password)
    payload = macos_aes128_cbc_decrypt(
        blob[len(_CHROMIUM_ENCRYPTED_VALUE_PREFIX) :],
        key,
        iv=MACOS_CHROMIUM_AES_IV,
    )
    if payload is None:
        return None
    return _decode_chromium_plaintext(payload, host)


def macos_chromium_keychain_service(browser: str) -> Optional[tuple[str, str]]:
    """Return verified ``(service, account)`` for macOS Chromium Safe Storage."""

    return _MACOS_CHROMIUM_KEYCHAIN.get(_canon_browser(browser))


def macos_chromium_keychain_password(
    browser: str,
    *,
    runner=subprocess.run,
    timeout: int | float = _KEYCHAIN_TIMEOUT_SECONDS,
) -> str:
    """Read a macOS Chromium Safe Storage password via ``/usr/bin/security``.

    The command shape is deterministic, shell-free, and test-injectable. Exceptions
    are deliberately redacted: stdout/stderr and the password never appear in the
    message.
    """

    mapping = macos_chromium_keychain_service(browser)
    if mapping is None:
        raise CredentialUnavailableError(
            "unsupported macOS Chromium Keychain browser; Safe Storage service "
            "mapping is unavailable"
        )
    service, account = mapping
    argv = [
        "/usr/bin/security",
        "find-generic-password",
        "-w",
        "-s",
        service,
        "-a",
        account,
    ]
    try:
        proc = runner(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise CredentialUnavailableError(
            "macOS Chromium safe storage password unavailable"
        ) from None
    if getattr(proc, "returncode", 1) != 0:
        raise CredentialUnavailableError(
            "macOS Chromium safe storage password unavailable"
        )
    secret = (getattr(proc, "stdout", "") or "").strip()
    if not secret:
        raise CredentialUnavailableError(
            "macOS Chromium safe storage password unavailable"
        )
    return secret


def _rotl8(value: int, shift: int) -> int:
    return ((value << shift) | (value >> (8 - shift))) & 0xFF


def _gf_mul(left: int, right: int) -> int:
    result = 0
    a = int(left) & 0xFF
    b = int(right) & 0xFF
    for _ in range(8):
        if b & 1:
            result ^= a
        carry = a & 0x80
        a = (a << 1) & 0xFF
        if carry:
            a ^= 0x1B
        b >>= 1
    return result & 0xFF


def _gf_pow(value: int, exponent: int) -> int:
    result = 1
    base = int(value) & 0xFF
    n = int(exponent)
    while n:
        if n & 1:
            result = _gf_mul(result, base)
        base = _gf_mul(base, base)
        n >>= 1
    return result & 0xFF


@lru_cache(maxsize=1)
def _aes_sboxes() -> tuple[tuple[int, ...], tuple[int, ...]]:
    sbox = []
    inverse = [0] * 256
    for value in range(256):
        inv = 0 if value == 0 else _gf_pow(value, 254)
        sub = (
            inv
            ^ _rotl8(inv, 1)
            ^ _rotl8(inv, 2)
            ^ _rotl8(inv, 3)
            ^ _rotl8(inv, 4)
            ^ 0x63
        )
        sub &= 0xFF
        sbox.append(sub)
        inverse[sub] = value
    return tuple(sbox), tuple(inverse)


def _aes_add_round_key(state: list[int], round_key: bytes) -> None:
    for index, value in enumerate(round_key):
        state[index] ^= value


def _aes_inv_sub_bytes(state: list[int]) -> None:
    _, inverse = _aes_sboxes()
    for index, value in enumerate(state):
        state[index] = inverse[value]


def _aes_inv_shift_rows(state: list[int]) -> None:
    for row in range(1, 4):
        values = [state[4 * column + row] for column in range(4)]
        values = values[-row:] + values[:-row]
        for column, value in enumerate(values):
            state[4 * column + row] = value


def _aes_inv_mix_columns(state: list[int]) -> None:
    for column in range(4):
        offset = 4 * column
        a0, a1, a2, a3 = state[offset : offset + 4]
        state[offset + 0] = (
            _gf_mul(14, a0) ^ _gf_mul(11, a1) ^ _gf_mul(13, a2) ^ _gf_mul(9, a3)
        ) & 0xFF
        state[offset + 1] = (
            _gf_mul(9, a0) ^ _gf_mul(14, a1) ^ _gf_mul(11, a2) ^ _gf_mul(13, a3)
        ) & 0xFF
        state[offset + 2] = (
            _gf_mul(13, a0) ^ _gf_mul(9, a1) ^ _gf_mul(14, a2) ^ _gf_mul(11, a3)
        ) & 0xFF
        state[offset + 3] = (
            _gf_mul(11, a0) ^ _gf_mul(13, a1) ^ _gf_mul(9, a2) ^ _gf_mul(14, a3)
        ) & 0xFF


def _aes128_round_keys(key: bytes) -> tuple[bytes, ...]:
    key_b = bytes(key)
    if len(key_b) != 16:
        raise ValueError("AES-128 key must be 16 bytes")
    sbox, _ = _aes_sboxes()
    rcon = (0x00, 0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36)
    words = [list(key_b[index : index + 4]) for index in range(0, 16, 4)]
    for index in range(4, 44):
        temp = words[index - 1][:]
        if index % 4 == 0:
            temp = temp[1:] + temp[:1]
            temp = [sbox[value] for value in temp]
            temp[0] ^= rcon[index // 4]
        words.append([words[index - 4][pos] ^ temp[pos] for pos in range(4)])
    return tuple(
        bytes(sum(words[round_index * 4 : round_index * 4 + 4], []))
        for round_index in range(11)
    )


def _aes128_decrypt_block(block: bytes, round_keys: tuple[bytes, ...]) -> bytes:
    if len(block) != 16 or len(round_keys) != 11:
        raise ValueError("invalid AES block or round keys")
    state = list(block)
    _aes_add_round_key(state, round_keys[10])
    for round_index in range(9, 0, -1):
        _aes_inv_shift_rows(state)
        _aes_inv_sub_bytes(state)
        _aes_add_round_key(state, round_keys[round_index])
        _aes_inv_mix_columns(state)
    _aes_inv_shift_rows(state)
    _aes_inv_sub_bytes(state)
    _aes_add_round_key(state, round_keys[0])
    return bytes(state)


def linux_aes128_cbc_decrypt(
    ciphertext: bytes, key: bytes, *, iv: bytes = LINUX_CHROMIUM_AES_IV
) -> Optional[bytes]:
    """Decrypt AES-128-CBC with PKCS#7 padding using stdlib-only Python.

    Linux has no first-party Python AES primitive comparable to CommonCrypto/CNG.
    This small AES-128 decrypt path exists only for Chromium's legacy ``v10`` CBC
    cookie format and fails closed on malformed input, bad padding, or wrong keys.
    """

    data = bytes(ciphertext or b"")
    key_b = bytes(key or b"")
    iv_b = bytes(iv or b"")
    if not data or len(data) % 16 != 0 or len(key_b) != 16 or len(iv_b) != 16:
        return None
    try:
        round_keys = _aes128_round_keys(key_b)
        previous = iv_b
        plaintext = bytearray()
        for offset in range(0, len(data), 16):
            block = data[offset : offset + 16]
            decrypted = _aes128_decrypt_block(block, round_keys)
            plaintext.extend(left ^ right for left, right in zip(decrypted, previous))
            previous = block
        pad = plaintext[-1]
        if pad < 1 or pad > 16:
            return None
        if bytes(plaintext[-pad:]) != bytes([pad]) * pad:
            return None
        return bytes(plaintext[:-pad])
    except Exception:
        return None


def linux_chromium_derive_key(safe_storage_password: str | bytes) -> bytes:
    """Derive Linux Chromium's AES-128-CBC key from Secret Service text."""

    if isinstance(safe_storage_password, bytes):
        secret = safe_storage_password
    else:
        secret = str(safe_storage_password).encode("utf-8")
    return hashlib.pbkdf2_hmac(
        "sha1",
        secret,
        LINUX_CHROMIUM_SALT,
        LINUX_CHROMIUM_PBKDF2_ITERATIONS,
        LINUX_CHROMIUM_KEY_LENGTH,
    )


def linux_chromium_peanuts_key() -> bytes:
    """Return Chromium's legacy Linux ``peanuts`` fallback key."""

    return linux_chromium_derive_key(LINUX_CHROMIUM_FALLBACK_PASSWORD)


def linux_chromium_decrypt_cookie_value(
    encrypted_value: bytes,
    *,
    host: str,
    safe_storage_password: str | bytes,
    aes_cbc_decrypt_func=linux_aes128_cbc_decrypt,
    require_host_digest: bool = False,
) -> Optional[str]:
    """Decode a Linux Chromium ``v10`` AES-CBC encrypted cookie value.

    Set ``require_host_digest`` for callers that must reject legacy payloads.
    """

    blob = bytes(encrypted_value or b"")
    if not blob.startswith(_CHROMIUM_ENCRYPTED_VALUE_PREFIX) or len(blob) <= 3:
        return None
    key = linux_chromium_derive_key(safe_storage_password)
    try:
        payload = aes_cbc_decrypt_func(
            blob[len(_CHROMIUM_ENCRYPTED_VALUE_PREFIX) :],
            key,
            iv=LINUX_CHROMIUM_AES_IV,
        )
    except Exception:
        return None
    if payload is None:
        return None
    try:
        return _decode_chromium_plaintext(
            bytes(payload), host, require_host_digest=require_host_digest
        )
    except Exception:
        return None


def linux_chromium_secret_service_metadata(browser: str) -> Optional[dict]:
    """Return Chromium libsecret schema metadata for verified Linux browsers."""

    value = _LINUX_CHROMIUM_SECRET_SERVICE.get(_canon_browser(browser))
    return dict(value) if value is not None else None


def linux_chromium_secret_service_password(browser: str, *, lookup_func=None) -> str:
    """Read a Linux Chromium Safe Storage password through an injected lookup seam.

    The default performs no D-Bus/libsecret/KWallet access. A future live Linux
    slice may supply a Secret Service implementation; until then callers can pass
    a test or platform provider explicitly. Errors are redacted.
    """

    metadata = linux_chromium_secret_service_metadata(browser)
    if metadata is None:
        raise CredentialUnavailableError(
            "unsupported Linux Chromium Secret Service browser; Safe Storage "
            "mapping is unavailable"
        )
    if lookup_func is None:
        raise CredentialUnavailableError(
            "Linux Chromium secret service password unavailable"
        )
    try:
        secret = lookup_func(dict(metadata))
    except Exception:
        raise CredentialUnavailableError(
            "Linux Chromium secret service password unavailable"
        ) from None
    if isinstance(secret, bytes):
        try:
            secret = secret.decode("utf-8")
        except UnicodeDecodeError:
            raise CredentialUnavailableError(
                "Linux Chromium secret service password unavailable"
            ) from None
    secret = str(secret or "").strip()
    if not secret:
        raise CredentialUnavailableError(
            "Linux Chromium secret service password unavailable"
        )
    return secret


class _WindowsDataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.c_uint32),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _windows_dll(name: str):
    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return None
    try:
        return getattr(windll, name)
    except Exception:
        return None


def windows_dpapi_available() -> bool:
    """Return whether Windows DPAPI can be reached on this host; no data read."""

    return _windows_dll("crypt32") is not None and _windows_dll("kernel32") is not None


def windows_dpapi_unprotect(protected: bytes) -> Optional[bytes]:
    """Unwrap a DPAPI-protected byte string through Windows ``CryptUnprotectData``.

    Returns ``None`` on non-Windows hosts, unavailable APIs, malformed input, user
    denial, or API failure. It never includes caller bytes or Windows stderr in an
    exception/message.
    """

    data = bytes(protected or b"")
    if not data:
        return None
    try:
        crypt32 = _windows_dll("crypt32")
        kernel32 = _windows_dll("kernel32")
        if crypt32 is None or kernel32 is None:
            return None
    except Exception:
        return None

    try:
        crypt32.CryptUnprotectData.argtypes = [
            ctypes.POINTER(_WindowsDataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(_WindowsDataBlob),
        ]
        crypt32.CryptUnprotectData.restype = ctypes.c_int
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
    except Exception:
        return None

    in_buf = ctypes.create_string_buffer(data, len(data))
    in_blob = _WindowsDataBlob(
        len(data), ctypes.cast(in_buf, ctypes.POINTER(ctypes.c_ubyte))
    )
    out_blob = _WindowsDataBlob()
    try:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
        )
        if not ok or not out_blob.pbData or out_blob.cbData <= 0:
            return None
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    except Exception:
        return None
    finally:
        try:
            if out_blob.pbData:
                kernel32.LocalFree(ctypes.cast(out_blob.pbData, ctypes.c_void_p))
        except Exception:
            pass


def _parse_local_state(local_state) -> Optional[dict]:
    if isinstance(local_state, dict):
        return local_state
    if isinstance(local_state, bytes):
        text = local_state.decode("utf-8", "strict")
    else:
        text = str(local_state)
    try:
        data = json.loads(text)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def windows_chromium_key_from_local_state(
    local_state, *, unprotect_func=windows_dpapi_unprotect
) -> Optional[bytes]:
    """Return a decrypted Windows Chromium AES key from redacted Local State data.

    ``local_state`` is a caller-supplied dict/JSON string. This helper does not
    resolve paths, read files, consult the home directory, or inspect a live browser. It
    supports the classic ``DPAPI`` Local State key prefix only; app-bound ``APPB``
    keys are a later parity slice and fail closed here.
    """

    try:
        data = _parse_local_state(local_state)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    encrypted_key = (data.get("os_crypt") or {}).get("encrypted_key")
    if not isinstance(encrypted_key, str) or not encrypted_key:
        return None
    try:
        decoded = base64.b64decode(encrypted_key.encode("ascii"), validate=True)
    except Exception:
        return None
    if decoded.startswith(_WINDOWS_CHROMIUM_APP_BOUND_KEY_PREFIX):
        return None
    if not decoded.startswith(_WINDOWS_CHROMIUM_DPAPI_KEY_PREFIX):
        return None
    protected = decoded[len(_WINDOWS_CHROMIUM_DPAPI_KEY_PREFIX) :]
    if not protected:
        return None
    try:
        key = unprotect_func(protected)
    except Exception:
        return None
    if not isinstance(key, (bytes, bytearray)):
        return None
    key_b = bytes(key)
    if len(key_b) not in {16, 24, 32}:
        return None
    return key_b


class _BcryptAuthInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint32),
        ("dwInfoVersion", ctypes.c_uint32),
        ("pbNonce", ctypes.POINTER(ctypes.c_ubyte)),
        ("cbNonce", ctypes.c_uint32),
        ("pbAuthData", ctypes.POINTER(ctypes.c_ubyte)),
        ("cbAuthData", ctypes.c_uint32),
        ("pbTag", ctypes.POINTER(ctypes.c_ubyte)),
        ("cbTag", ctypes.c_uint32),
        ("pbMacContext", ctypes.POINTER(ctypes.c_ubyte)),
        ("cbMacContext", ctypes.c_uint32),
        ("cbAAD", ctypes.c_uint32),
        ("cbData", ctypes.c_uint64),
        ("dwFlags", ctypes.c_uint32),
    ]


def _bcrypt_success(status) -> bool:
    return int(status) >= 0


def windows_aes_gcm_decrypt(
    key: bytes, nonce: bytes, ciphertext: bytes, tag: bytes
) -> Optional[bytes]:
    """Decrypt AES-GCM using Windows CNG (``bcrypt.dll``) via stdlib ``ctypes``.

    Returns ``None`` when run off Windows, when CNG is unavailable, or when the tag
    does not authenticate. No caller bytes appear in raised messages because this
    helper does not raise for caller-controlled data.
    """

    key_b = bytes(key or b"")
    nonce_b = bytes(nonce or b"")
    cipher_b = bytes(ciphertext or b"")
    tag_b = bytes(tag or b"")
    if (
        len(key_b) not in {16, 24, 32}
        or len(nonce_b) != _WINDOWS_CHROMIUM_GCM_NONCE_LENGTH
    ):
        return None
    if not cipher_b or len(tag_b) != _WINDOWS_CHROMIUM_GCM_TAG_LENGTH:
        return None
    try:
        bcrypt = _windows_dll("bcrypt")
        if bcrypt is None:
            return None
    except Exception:
        return None

    alg = ctypes.c_void_p()
    key_handle = ctypes.c_void_p()
    key_object = None
    try:
        if not _bcrypt_success(
            bcrypt.BCryptOpenAlgorithmProvider(
                ctypes.byref(alg), ctypes.c_wchar_p("AES"), None, 0
            )
        ):
            return None
        mode = ctypes.create_unicode_buffer("ChainingModeGCM")
        if not _bcrypt_success(
            bcrypt.BCryptSetProperty(
                alg, ctypes.c_wchar_p("ChainingMode"), mode, ctypes.sizeof(mode), 0
            )
        ):
            return None
        obj_len = ctypes.c_ulong(0)
        moved = ctypes.c_ulong(0)
        if not _bcrypt_success(
            bcrypt.BCryptGetProperty(
                alg,
                ctypes.c_wchar_p("ObjectLength"),
                ctypes.byref(obj_len),
                ctypes.sizeof(obj_len),
                ctypes.byref(moved),
                0,
            )
        ):
            return None
        key_object = ctypes.create_string_buffer(obj_len.value)
        key_buf = ctypes.create_string_buffer(key_b, len(key_b))
        if not _bcrypt_success(
            bcrypt.BCryptGenerateSymmetricKey(
                alg,
                ctypes.byref(key_handle),
                key_object,
                obj_len.value,
                key_buf,
                len(key_b),
                0,
            )
        ):
            return None
        nonce_buf = ctypes.create_string_buffer(nonce_b, len(nonce_b))
        tag_buf = ctypes.create_string_buffer(tag_b, len(tag_b))
        cipher_buf = ctypes.create_string_buffer(cipher_b, len(cipher_b))
        out_buf = ctypes.create_string_buffer(len(cipher_b))
        out_len = ctypes.c_ulong(0)
        auth = _BcryptAuthInfo()
        auth.cbSize = ctypes.sizeof(_BcryptAuthInfo)
        auth.dwInfoVersion = 1
        auth.pbNonce = ctypes.cast(nonce_buf, ctypes.POINTER(ctypes.c_ubyte))
        auth.cbNonce = len(nonce_b)
        auth.pbAuthData = None
        auth.cbAuthData = 0
        auth.pbTag = ctypes.cast(tag_buf, ctypes.POINTER(ctypes.c_ubyte))
        auth.cbTag = len(tag_b)
        auth.pbMacContext = None
        auth.cbMacContext = 0
        auth.cbAAD = 0
        auth.cbData = 0
        auth.dwFlags = 0
        if not _bcrypt_success(
            bcrypt.BCryptDecrypt(
                key_handle,
                cipher_buf,
                len(cipher_b),
                ctypes.byref(auth),
                None,
                0,
                out_buf,
                len(out_buf),
                ctypes.byref(out_len),
                0,
            )
        ):
            return None
        return out_buf.raw[: out_len.value]
    except Exception:
        return None
    finally:
        try:
            if key_handle:
                bcrypt.BCryptDestroyKey(key_handle)
        except Exception:
            pass
        try:
            if alg:
                bcrypt.BCryptCloseAlgorithmProvider(alg, 0)
        except Exception:
            pass


def windows_chromium_decrypt_cookie_value(
    encrypted_value: bytes,
    *,
    host: str,
    aes_key: bytes,
    aes_gcm_decrypt_func=windows_aes_gcm_decrypt,
) -> Optional[str]:
    """Decode a Windows Chromium ``v10``/``v11`` AES-GCM encrypted cookie value."""

    blob = bytes(encrypted_value or b"")
    if not any(
        blob.startswith(prefix) for prefix in _WINDOWS_CHROMIUM_ENCRYPTED_VALUE_PREFIXES
    ):
        return None
    min_len = (
        3 + _WINDOWS_CHROMIUM_GCM_NONCE_LENGTH + _WINDOWS_CHROMIUM_GCM_TAG_LENGTH + 1
    )
    if len(blob) < min_len:
        return None
    key_b = bytes(aes_key or b"")
    if len(key_b) not in {16, 24, 32}:
        return None
    nonce_start = 3
    nonce_end = nonce_start + _WINDOWS_CHROMIUM_GCM_NONCE_LENGTH
    nonce = blob[nonce_start:nonce_end]
    tag = blob[-_WINDOWS_CHROMIUM_GCM_TAG_LENGTH:]
    ciphertext = blob[nonce_end:-_WINDOWS_CHROMIUM_GCM_TAG_LENGTH]
    if not ciphertext:
        return None
    try:
        payload = aes_gcm_decrypt_func(key_b, nonce, ciphertext, tag)
    except Exception:
        return None
    if payload is None:
        return None
    try:
        return _decode_chromium_plaintext(bytes(payload), host)
    except Exception:
        return None


def _requires_decryptor(browser: str) -> Optional[bool]:
    """``True`` if cookie values are encrypted, ``False`` if plaintext, else ``None``.

    Pure classification — no I/O, no imports beyond this module's local constants.
    """

    canon = _canon_browser(browser)
    if canon in ENCRYPTED_COOKIE_BROWSERS:
        return True
    if canon in PLAINTEXT_COOKIE_BROWSERS:
        return False
    return None


def supported_backends() -> dict:
    """Return the per-OS credential backend a future real decryptor would need.

    Deterministic, side-effect-free, and redacted: it names the backend that holds
    the key material on each OS but never reads any of them.
    """

    return dict(OS_KEY_BACKENDS)


def decryptor_capability(os_name: str, browser: str) -> dict:
    """Describe the cookie-value decryptor capability for ``(os_name, browser)``.

    C2A adds macOS Chromium-family decryptor primitives; C2C wires an explicit
    Keychain gate; C3A adds Windows DPAPI/AES-GCM primitives; C3B adds Linux
    Secret Service metadata plus AES-128-CBC primitives. This capability report
    still performs no credential lookup. ``primitive_available`` is the
    deterministic target capability for verified Chromium-family pairs on the
    supported OS family. ``automatic_available`` remains ``False`` because there
    is no ambient auto-discovery path — callers must cross the OS credential
    boundary explicitly.
    """

    canon = _canon_browser(browser)
    requires = _requires_decryptor(canon)
    backend = _OS_KEY_BACKEND.get(os_name, "an OS-specific credential store")
    macos = os_name in {"macos", "macOS"}
    linux = os_name in {"linux", "Ubuntu-LTS-Linux"}
    windows = os_name in {"windows", "Windows-11"}
    service_known = bool(macos and macos_chromium_keychain_service(canon) is not None)
    secret_service_known = bool(
        linux and linux_chromium_secret_service_metadata(canon) is not None
    )
    macos_primitive = bool(requires is True and macos and service_known)
    linux_primitive = bool(requires is True and linux and secret_service_known)
    windows_primitive = bool(requires is True and windows)
    primitive_available = bool(macos_primitive or linux_primitive or windows_primitive)
    commoncrypto_available = bool(macos_primitive and macos_commoncrypto_available())
    dpapi_available = bool(windows_primitive and windows_dpapi_available())
    if requires is True:
        if primitive_available:
            reasons = [REASON_NO_KEYCHAIN_ACCESS]
        else:
            reasons = [REASON_NO_STDLIB_AES, REASON_NO_KEYCHAIN_ACCESS]
    elif requires is False:
        reasons = [REASON_PLAINTEXT_VALUES]
    else:
        reasons = [REASON_UNKNOWN_BROWSER]
    return {
        "os": os_name,
        "browser": canon,
        "automatic_available": False,
        "requires_decryptor": requires,
        "os_key_backend": backend,
        "primitive_available": primitive_available,
        "commoncrypto_available": commoncrypto_available,
        "dpapi_available": dpapi_available,
        "keychain_service_known": service_known,
        "secret_service_schema_known": secret_service_known,
        "uses_keychain": service_known,
        "uses_commoncrypto": macos_primitive,
        "uses_dpapi": windows_primitive,
        "uses_cng": windows_primitive,
        "uses_secret_service": linux_primitive,
        "uses_linux_aes": linux_primitive,
        "uses_peanuts_fallback": linux_primitive,
        "reasons": reasons,
    }


def decryptor_matrix(os_names=None, browsers=None) -> list:
    """Return a redacted capability matrix over OS x cookie browser.

    Rows are sorted by ``(os, browser)`` so the output is stable across calls on a
    host. The matrix performs no credential lookup and emits no secret; the
    `commoncrypto_available` field is the only host-local loadability signal.
    """

    oses = list(os_names) if os_names is not None else list(OS_LABELS)
    bros = list(browsers) if browsers is not None else list(COOKIE_BROWSERS)
    rows = [
        decryptor_capability(os_name, browser) for os_name in oses for browser in bros
    ]
    return sorted(rows, key=lambda r: (str(r["os"]), str(r["browser"])))
