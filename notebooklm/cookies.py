"""Phase 2A cookie / storage formats and deterministic redaction.

This module is the stdlib-only foundation for ZeroNotebookLM's cookie/session
handling. It supports the three on-disk formats upstream interoperates with:

  * Playwright-style ``storage_state.json`` (``cookies`` + ``origins`` fields);
  * Netscape ``cookies.txt`` import/export (with the ``#HttpOnly_`` convention);
  * explicit cookie JSON (a list of cookie objects or a flat name→value map).

It also provides deterministic redaction helpers for diagnostics. The redaction
helpers never emit cookie values, auth headers, or OAuth-looking tokens — they
report only non-sensitive metadata (names, domains, presence, lengths).

Nothing here reads a real browser cookie store, an OS keychain, or the network.
Cookie *values* only ever appear in the explicit import/export paths (which write
to user-owned, git-ignored files); they are never printed by the redaction or
diagnostic helpers.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from .errors import ValidationError
from .profiles import read_json, write_json_atomic

# --------------------------------------------------------------------------- #
# Cookie policy constants (mirrors the pinned upstream auth surface)
# --------------------------------------------------------------------------- #

REDACTED = "<redacted>"

# The two cookies upstream treats as the minimum required NotebookLM auth set.
MINIMUM_REQUIRED_COOKIES = frozenset({"SID", "__Secure-1PSIDTS"})

# Google cookie domains accepted by the upstream auth path (copied from the
# pinned 0.7.2 oracle so domain checks match without importing upstream).
ALLOWED_COOKIE_DOMAINS = frozenset(
    {
        "accounts.google.com",
        ".accounts.google.com",
        "google.com",
        ".google.com",
        "drive.google.com",
        ".drive.google.com",
        "docs.google.com",
        ".docs.google.com",
        "mail.google.com",
        ".mail.google.com",
        "myaccount.google.com",
        ".myaccount.google.com",
        "notebooklm.google.com",
        ".notebooklm.google.com",
        "notebooklm.cloud.google.com",
        ".notebooklm.cloud.google.com",
        "youtube.com",
        ".youtube.com",
        "accounts.youtube.com",
        ".accounts.youtube.com",
        ".googleusercontent.com",
    }
)
GOOGLE_REGIONAL_CCTLDS = frozenset(
    {
        "com.sg",
        "com.au",
        "com.br",
        "com.mx",
        "com.ar",
        "com.hk",
        "com.tw",
        "com.my",
        "com.ph",
        "com.vn",
        "com.pk",
        "com.bd",
        "com.ng",
        "com.eg",
        "com.tr",
        "com.ua",
        "com.co",
        "com.pe",
        "com.sa",
        "com.ae",
        "co.uk",
        "co.jp",
        "co.in",
        "co.kr",
        "co.za",
        "co.nz",
        "co.id",
        "co.th",
        "co.il",
        "co.ve",
        "co.cr",
        "co.ke",
        "co.ug",
        "co.tz",
        "co.ma",
        "co.ao",
        "co.mz",
        "co.zw",
        "co.bw",
        "cn",
        "de",
        "fr",
        "it",
        "es",
        "nl",
        "pl",
        "ru",
        "ca",
        "be",
        "at",
        "ch",
        "se",
        "no",
        "dk",
        "fi",
        "pt",
        "gr",
        "cz",
        "ro",
        "hu",
        "ie",
        "sk",
        "bg",
        "hr",
        "si",
        "lt",
        "lv",
        "ee",
        "lu",
        "cl",
        "cat",
    }
)

# Cookie names whose *values* are sensitive auth material and must be redacted.
SENSITIVE_COOKIE_NAMES = frozenset(
    {
        "SID",
        "HSID",
        "SSID",
        "APISID",
        "SAPISID",
        "SIDCC",
        "NID",
        "OSID",
        "LSID",
        "ACCOUNT_CHOOSER",
        "GAPS",
        "__Secure-1PSID",
        "__Secure-3PSID",
        "__Secure-1PSIDTS",
        "__Secure-3PSIDTS",
        "__Secure-1PSIDCC",
        "__Secure-3PSIDCC",
        "__Secure-1PAPISID",
        "__Secure-3PAPISID",
        "__Secure-OSID",
        "__Secure-ENID",
    }
)

# Substrings that mark a mapping key as carrying sensitive material in scrub().
_SENSITIVE_KEY_HINTS = (
    "cookie",
    "authorization",
    "auth_header",
    "token",
    "secret",
    "password",
    "passwd",
    "credential",
    "bearer",
    "session_id",
    "csrf",
    "psid",
    "sapisid",
    "apisid",
    "sid_value",
    "access_token",
    "refresh_token",
    "id_token",
)

_SECRET_VALUE_RE = re.compile(r"^[A-Za-z0-9_\-./+=]+$")
_PEM_RE = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
_GOOGLE_OAUTH_ACCESS_PREFIX = "ya" + "29."
_GOOGLE_OAUTH_REFRESH_PREFIX = "1" + "//"

__all__ = [
    "REDACTED",
    "MINIMUM_REQUIRED_COOKIES",
    "ALLOWED_COOKIE_DOMAINS",
    "SENSITIVE_COOKIE_NAMES",
    "is_allowed_google_domain",
    "normalize_cookie",
    "cookies_from_storage_state",
    "build_storage_state",
    "load_storage_state",
    "save_storage_state",
    "storage_state_cookie_names",
    "parse_netscape",
    "format_netscape",
    "import_netscape",
    "export_netscape",
    "parse_cookie_json",
    "dump_cookie_json",
    "looks_like_secret",
    "is_sensitive_cookie_name",
    "redact_cookie",
    "redact_storage_state",
    "scrub",
]

_NORMALIZED_FIELDS = (
    "name",
    "value",
    "domain",
    "path",
    "expires",
    "secure",
    "http_only",
    "same_site",
)


# --------------------------------------------------------------------------- #
# Domain policy
# --------------------------------------------------------------------------- #


def is_allowed_google_domain(domain: Any) -> bool:
    """Return ``True`` if ``domain`` is within the allowed Google auth set.

    Accepts the exact pinned domains plus the ``google.com`` /
    ``googleusercontent.com`` / ``youtube.com`` suffix families (with or without a
    leading dot). This is the single source of truth shared by the offline auth
    check and the browser-cookie import filter.
    """

    if not isinstance(domain, str) or not domain:
        return False
    if domain in ALLOWED_COOKIE_DOMAINS:
        return True
    bare = domain.lstrip(".")
    if bare.startswith("google.") and bare[7:] in GOOGLE_REGIONAL_CCTLDS:
        return True
    if ".google." in bare:
        suffix = bare.rsplit(".google.", 1)[1]
        if suffix in GOOGLE_REGIONAL_CCTLDS:
            return True
    return (
        bare == "google.com"
        or bare.endswith(".google.com")
        or bare.endswith(".googleusercontent.com")
        or bare == "youtube.com"
        or bare.endswith(".youtube.com")
    )


# --------------------------------------------------------------------------- #
# Normalization
# --------------------------------------------------------------------------- #


def normalize_cookie(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Return a canonical cookie dict from a Playwright- or Netscape-shaped map.

    Canonical keys: ``name``, ``value``, ``domain``, ``path``, ``expires``,
    ``secure``, ``http_only``, ``same_site``.
    """

    if not isinstance(raw, Mapping):
        raise ValidationError("cookie must be a mapping")
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise ValidationError("cookie is missing a 'name'")
    expires = raw.get("expires", raw.get("expiry"))
    if isinstance(expires, bool):  # guard: bools are ints in Python
        expires = None
    if expires in ("", -1, 0):
        expires = None
    http_only = raw.get("http_only", raw.get("httpOnly", False))
    same_site = raw.get("same_site", raw.get("sameSite"))
    return {
        "name": name,
        "value": "" if raw.get("value") is None else str(raw.get("value")),
        "domain": raw.get("domain", ""),
        "path": raw.get("path", "/"),
        "expires": expires,
        "secure": bool(raw.get("secure", False)),
        "http_only": bool(http_only),
        "same_site": same_site,
    }


# --------------------------------------------------------------------------- #
# Playwright storage_state.json
# --------------------------------------------------------------------------- #


def cookies_from_storage_state(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return the normalized cookies contained in a storage_state mapping."""

    if not isinstance(state, Mapping):
        raise ValidationError("storage_state must be a mapping")
    raw = state.get("cookies", [])
    if not isinstance(raw, list):
        raise ValidationError("storage_state 'cookies' must be a list")
    return [normalize_cookie(c) for c in raw]


def build_storage_state(
    cookies: Iterable[Mapping[str, Any]], *, origins: list | None = None
) -> dict[str, Any]:
    """Build a Playwright-shaped storage_state from normalized/raw cookies."""

    out_cookies = []
    for c in cookies:
        nc = normalize_cookie(c)
        cookie: dict[str, Any] = {
            "name": nc["name"],
            "value": nc["value"],
            "domain": nc["domain"],
            "path": nc["path"],
            "httpOnly": nc["http_only"],
            "secure": nc["secure"],
            "sameSite": nc["same_site"] or "Lax",
        }
        if nc["expires"] is not None:
            cookie["expires"] = nc["expires"]
        out_cookies.append(cookie)
    return {"cookies": out_cookies, "origins": list(origins or [])}


def _validate_storage_state(state: Any) -> dict[str, Any]:
    if not isinstance(state, dict):
        raise ValidationError("storage_state must be a JSON object")
    cookies = state.get("cookies", [])
    if not isinstance(cookies, list):
        raise ValidationError("storage_state 'cookies' must be a list")
    origins = state.get("origins", [])
    if not isinstance(origins, list):
        raise ValidationError("storage_state 'origins' must be a list")
    return state


def load_storage_state(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Load and validate a Playwright storage_state.json file."""

    state = read_json(path)
    if state is None:
        raise ValidationError(f"storage_state not found: {path}")
    return _validate_storage_state(state)


def save_storage_state(path: str | os.PathLike[str], state: Mapping[str, Any]) -> None:
    """Validate and atomically persist a Playwright storage_state mapping."""

    validated = _validate_storage_state(dict(state))
    # Ensure both upstream-expected fields are always present on disk.
    validated.setdefault("cookies", [])
    validated.setdefault("origins", [])
    write_json_atomic(path, validated)


def storage_state_cookie_names(state: Mapping[str, Any]) -> set[str]:
    """Return the set of cookie names present in a storage_state mapping."""

    return {c["name"] for c in cookies_from_storage_state(state)}


# --------------------------------------------------------------------------- #
# Netscape cookies.txt
# --------------------------------------------------------------------------- #

_NETSCAPE_HEADER = (
    "# Netscape HTTP Cookie File\n# Generated by ZeroNotebookLM. Do not edit.\n\n"
)
_HTTPONLY_PREFIX = "#HttpOnly_"


def parse_netscape(text: str) -> list[dict[str, Any]]:
    """Parse Netscape ``cookies.txt`` content into normalized cookies."""

    cookies: list[dict[str, Any]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        http_only = False
        if stripped.startswith(_HTTPONLY_PREFIX):
            http_only = True
            line = line[len(_HTTPONLY_PREFIX) :]
        elif stripped.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != 7:
            # Tolerate trailing whitespace-only differences but skip malformed rows.
            continue
        domain, _include_sub, path, secure, expires, name, value = fields
        try:
            expires_val: Any = int(expires)
        except ValueError:
            expires_val = None
        if expires_val in (0,):
            expires_val = None
        cookies.append(
            normalize_cookie(
                {
                    "name": name,
                    "value": value,
                    "domain": domain,
                    "path": path or "/",
                    "secure": secure.strip().upper() == "TRUE",
                    "expires": expires_val,
                    "http_only": http_only,
                }
            )
        )
    return cookies


def format_netscape(cookies: Iterable[Mapping[str, Any]]) -> str:
    """Render normalized/raw cookies as Netscape ``cookies.txt`` content."""

    lines = [_NETSCAPE_HEADER.rstrip("\n")]
    lines.append("")
    for c in cookies:
        nc = normalize_cookie(c)
        domain = nc["domain"] or ""
        include_sub = "TRUE" if domain.startswith(".") else "FALSE"
        secure = "TRUE" if nc["secure"] else "FALSE"
        expires = int(nc["expires"]) if nc["expires"] is not None else 0
        prefix = _HTTPONLY_PREFIX if nc["http_only"] else ""
        row = "\t".join(
            [
                f"{prefix}{domain}",
                include_sub,
                nc["path"] or "/",
                secure,
                str(expires),
                nc["name"],
                nc["value"],
            ]
        )
        lines.append(row)
    return "\n".join(lines) + "\n"


def import_netscape(path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    return parse_netscape(Path(path).read_text(encoding="utf-8"))


def export_netscape(
    path: str | os.PathLike[str], cookies: Iterable[Mapping[str, Any]]
) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(format_netscape(cookies), encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:  # pragma: no cover - platform dependent
        pass


# --------------------------------------------------------------------------- #
# Explicit cookie JSON
# --------------------------------------------------------------------------- #


def parse_cookie_json(obj: Any) -> list[dict[str, Any]]:
    """Parse explicit cookie JSON into normalized cookies.

    Accepts a JSON string, a Playwright storage_state mapping, a list of cookie
    objects, a single cookie object, or a flat ``{name: value}`` mapping.
    """

    if isinstance(obj, (str, bytes, bytearray)):
        try:
            obj = json.loads(obj)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValidationError(f"invalid cookie JSON: {exc}") from exc
    if isinstance(obj, Mapping):
        if "cookies" in obj:
            return cookies_from_storage_state(obj)
        if "name" in obj:
            return [normalize_cookie(obj)]
        # flat {name: value} mapping
        cookies = []
        for name, value in obj.items():
            cookies.append(normalize_cookie({"name": name, "value": value}))
        return cookies
    if isinstance(obj, list):
        return [normalize_cookie(c) for c in obj]
    raise ValidationError("unsupported cookie JSON shape")


def dump_cookie_json(cookies: Iterable[Mapping[str, Any]]) -> str:
    """Serialize normalized cookies to a deterministic JSON document."""

    normalized = [normalize_cookie(c) for c in cookies]
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


# --------------------------------------------------------------------------- #
# Redaction
# --------------------------------------------------------------------------- #


def looks_like_secret(value: Any) -> bool:
    """Heuristically decide whether ``value`` looks like a credential/token."""

    if not isinstance(value, str):
        return False
    if _PEM_RE.search(value):
        return True
    lowered = value.lower()
    if lowered.startswith("bearer "):
        return True
    if value.startswith(_GOOGLE_OAUTH_ACCESS_PREFIX) or value.startswith(
        _GOOGLE_OAUTH_REFRESH_PREFIX
    ):
        return True
    if len(value) < 16:
        return False
    # Filesystem paths share the high-entropy charset but are not secrets; the
    # specific token shapes above are already handled, so exclude path-like text
    # to avoid redacting diagnostic paths (home dir, storage path, …).
    if "/" in value or "\\" in value:
        return False
    if not _SECRET_VALUE_RE.fullmatch(value):
        return False
    has_digit = any(ch.isdigit() for ch in value)
    has_alpha = any(ch.isalpha() for ch in value)
    # High-entropy-looking: long, charset-restricted, and mixes digits + letters.
    return bool(has_digit and has_alpha and len(value) >= 16)


def is_sensitive_cookie_name(name: Any) -> bool:
    """Return ``True`` for cookie names whose values must never be printed."""

    if not isinstance(name, str):
        return False
    if name in SENSITIVE_COOKIE_NAMES:
        return True
    return name.startswith("__Secure-") or name.startswith("__Host-")


def redact_cookie(cookie: Mapping[str, Any]) -> dict[str, Any]:
    """Return non-sensitive metadata for a cookie (never its value)."""

    nc = normalize_cookie(cookie)
    value = nc["value"] or ""
    return {
        "name": nc["name"],
        "domain": nc["domain"],
        "path": nc["path"],
        "secure": nc["secure"],
        "http_only": nc["http_only"],
        "expires": nc["expires"],
        "sensitive": is_sensitive_cookie_name(nc["name"]),
        "value_present": bool(value),
        "value_length": len(value),
    }


def redact_storage_state(state: Mapping[str, Any]) -> dict[str, Any]:
    """Return a value-free diagnostic view of a storage_state mapping."""

    cookies = cookies_from_storage_state(state)
    origins = state.get("origins", []) if isinstance(state, Mapping) else []
    redacted_origins = []
    if isinstance(origins, list):
        for origin in origins:
            if not isinstance(origin, Mapping):
                continue
            local = origin.get("localStorage", [])
            count = len(local) if isinstance(local, list) else 0
            redacted_origins.append(
                {
                    "origin": origin.get("origin"),
                    "local_storage_count": count,
                }
            )
    return {
        "cookie_count": len(cookies),
        "cookie_names": sorted(c["name"] for c in cookies),
        "cookies": [redact_cookie(c) for c in cookies],
        "origin_count": len(redacted_origins),
        "origins": redacted_origins,
    }


def scrub(obj: Any) -> Any:
    """Recursively redact sensitive values from an arbitrary diagnostic object.

    A mapping value is redacted when its key looks sensitive (e.g. ``cookie``,
    ``authorization``, ``token``) or when a string value itself looks like a
    secret. Structure, counts, and non-sensitive scalars are preserved.
    """

    if isinstance(obj, Mapping):
        out: dict[Any, Any] = {}
        for key, value in obj.items():
            key_l = str(key).lower()
            if any(hint in key_l for hint in _SENSITIVE_KEY_HINTS) and not isinstance(
                value, (Mapping, list)
            ):
                out[key] = REDACTED
            else:
                out[key] = scrub(value)
        return out
    if isinstance(obj, list):
        return [scrub(item) for item in obj]
    if isinstance(obj, str) and looks_like_secret(obj):
        return REDACTED
    return obj
