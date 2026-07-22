"""Phase 2D offline auth-matrix readiness reporting.

This module is a *pure classifier*. It consumes the pinned
``compat/auth_matrix.json`` shape — from an explicit caller-provided path or an
in-memory data object — and reports, honestly and without overclaiming, how the
current Phase 2 foundation (Phase 2A profiles/auth, Phase 2B browser-cookie
import/inspect, Phase 2C refresh + OS-credential boundary, Phase 2F interactive
login primitives, and Phase 2G profile-backed network token refresh foundation)
maps onto the upstream
auth matrix.

It exists so Core can answer "what is actually covered offline, and what is still
blocked behind live access?" without re-deriving it by hand — and so the project
never silently claims auth parity it has not proven.

Hard guarantees
---------------
* **Never marks a row ``pass``.** Each row's ``parity_state`` is copied verbatim
  from the source matrix; an offline foundation (even a fully-implemented one)
  never upgrades an ``open`` row to ``pass``. Parity closure requires a real
  upstream-vs-bare differential, which this module does not perform.
* **Never mutates the source.** It only *reads* the matrix (an explicit path or a
  dict). It writes nothing — least of all ``compat/auth_matrix.json``.
* **No live access.** Pure data classification: no network, no real browser-store
  discovery, no OS credential backend, no ``~`` / environment discovery, no
  external-process calls. The only I/O is reading an explicit matrix file the
  caller named.
* **Carries no secrets.** The report is built from non-sensitive matrix columns
  (browser, OS, selector, parity state); it never contains a cookie, token, or
  email. Stray fields on a source row are ignored, not echoed.

The honest classification of the two upstream matrices:

* An ``interactive_login`` row still marked ``open`` is **live-only** for parity
  closure. Phase
  2F adds a partial foundation for the ``login`` flow: stdlib browser launch,
  loopback DevTools probing, a WebSocket/CDP ``Network.getAllCookies`` command,
  storage_state capture, public CLI wiring, and a wait-for-required-cookies loop
  with explicit ``--fresh`` isolated-profile reset. Phase 2G adds a partial
  profile-backed network-token refresh foundation for the ``refresh`` flow:
  ``auth refresh``/``auth check --test`` can perform a stdlib RotateCookies +
  NotebookLM homepage token probe against stored cookies. However live
  browser/network authorization and upstream-vs-bare differential remain
  unproven. Other interactive flows still have no implementation foundation in
  this release. ``status``/``auth logout``/``doctor`` against stored state
  operate on imported/stored sessions, not an interactively-established one, so
  they do not close these rows.)
* ``browser_cookie_import`` rows have an offline, **fixture-only** foundation for
  the ``import`` / ``inspect`` / ``refresh`` paths, and only *partial* support for
  ``profile-select`` / ``account-select`` (explicit fixture profile selection and
  explicit ``--accounts-file`` account selection — no live profile enumeration or
  store-derived account discovery). A source row still marked ``open`` stays open:
  reaching real browsers needs live store discovery, chromium values need the OS
  credential backend decryptor, and parity needs a live differential. A source row
  already backed by pass evidence is not reported as live-blocked.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from . import os_credentials as _oscreds
from .errors import ValidationError

SCHEMA_VERSION = "auth_readiness/1"

# Offline-foundation coverage classes (honest, conservative).
FOUNDATION_IMPLEMENTED = "implemented_offline_fixture_only"
FOUNDATION_PARTIAL = "partial_offline_fixture_only"
FOUNDATION_NONE = "none"
FOUNDATION_STATES = (FOUNDATION_IMPLEMENTED, FOUNDATION_PARTIAL, FOUNDATION_NONE)

# Blocker reason codes — what a row still needs before it can be used for real /
# closed as a differential ``pass``. Stable string codes, safe to surface.
BLOCK_LIVE_LOGIN = "live_browser_network_authorization"
BLOCK_NETWORK_REFRESH = "live_network_token_refresh"
BLOCK_BROWSER_DISCOVERY = "live_browser_store_discovery"
BLOCK_OS_CREDENTIAL = "os_credential_backend_decryptor"
BLOCK_DIFFERENTIAL = "live_upstream_differential_unproven"
KNOWN_BLOCKERS = frozenset(
    {
        BLOCK_LIVE_LOGIN,
        BLOCK_NETWORK_REFRESH,
        BLOCK_BROWSER_DISCOVERY,
        BLOCK_OS_CREDENTIAL,
        BLOCK_DIFFERENTIAL,
    }
)

# Valid upstream closure states (mirrors compat/auth_matrix.json ``closure_states``).
_CLOSURE_STATES = ("pass", "open", "blocked")

# Which browser-cookie selector paths the offline foundation implements, and the
# code that backs each. ``import``/``inspect``/``refresh`` are fixture-backed and
# implemented; the two ``*-select`` paths are honestly only partial.
_COOKIE_PATH_FOUNDATION = {
    "import": FOUNDATION_IMPLEMENTED,
    "inspect": FOUNDATION_IMPLEMENTED,
    "refresh": FOUNDATION_IMPLEMENTED,
    "profile-select": FOUNDATION_PARTIAL,
    "account-select": FOUNDATION_PARTIAL,
}
_COOKIE_PATH_REF = {
    "import": "browser_cookies.import_to_storage_state (Phase 2B, explicit fixture/store)",
    "inspect": "browser_cookies.inspect_cookie_store (Phase 2B, explicit fixture/store)",
    "refresh": "browser_cookies.refresh_browser_cookies (Phase 2C, explicit/persisted source)",
    "profile-select": (
        "browser_cookies.resolve_cookie_store(browser_profile=...) — fixture-only; "
        "no live profile enumeration"
    ),
    "account-select": (
        "browser_cookies.select_account via explicit --accounts-file — fixture-only; "
        "no store-derived account discovery"
    ),
}
_LOGIN_FLOW_FOUNDATION = {
    "login": FOUNDATION_PARTIAL,
    "refresh": FOUNDATION_PARTIAL,
}
_LOGIN_FLOW_REF = {
    "login": (
        "interactive_login Phase 2F-D — public CLI login wiring over stdlib "
        "loopback browser launch, DevTools probe, CDP cookie command, wait-for-"
        "required-cookies capture, --fresh isolated-profile reset, and "
        "storage_state persistence; live authorization/differential still open"
    ),
    "refresh": (
        "auth Phase 2G — stdlib profile-backed RotateCookies + NotebookLM "
        "homepage WIZ token-fetch probe for auth refresh/check --test; live "
        "differential still open"
    ),
}

__all__ = [
    "SCHEMA_VERSION",
    "FOUNDATION_IMPLEMENTED",
    "FOUNDATION_PARTIAL",
    "FOUNDATION_NONE",
    "FOUNDATION_STATES",
    "BLOCK_LIVE_LOGIN",
    "BLOCK_NETWORK_REFRESH",
    "BLOCK_BROWSER_DISCOVERY",
    "BLOCK_OS_CREDENTIAL",
    "BLOCK_DIFFERENTIAL",
    "KNOWN_BLOCKERS",
    "default_auth_matrix_path",
    "load_auth_matrix",
    "analyze_auth_matrix",
    "build_report",
    "human_view",
]


# --------------------------------------------------------------------------- #
# Loading (explicit path only; never the real machine)
# --------------------------------------------------------------------------- #


def default_auth_matrix_path() -> Path:
    """Path to the committed ``compat/auth_matrix.json`` in this checkout.

    Used as the CLI default when no explicit path is supplied. It points at a
    committed, non-sensitive repo artifact — not user state.
    """

    checkout = Path(__file__).resolve().parents[1] / "compat" / "auth_matrix.json"
    return checkout if checkout.is_file() else Path(__file__).with_name("data") / "auth_matrix.json"


def load_auth_matrix(path: str | Path) -> dict[str, Any]:
    """Read + parse an auth-matrix JSON document from an explicit path.

    Raises ``ValidationError`` for a missing file, invalid JSON, or a non-object
    top level. This is the only I/O the module performs, and only against the
    explicit path the caller named.
    """

    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ValidationError(f"auth matrix not found: {p}") from exc
    except OSError as exc:  # pragma: no cover - defensive
        raise ValidationError(f"could not read auth matrix {p}: {exc}") from exc
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValidationError(f"invalid auth matrix JSON in {p}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValidationError("auth matrix must be a JSON object")
    return data


# --------------------------------------------------------------------------- #
# Per-row classification
# --------------------------------------------------------------------------- #


def _canon_browser(browser: Any) -> str:
    return (browser if isinstance(browser, str) else "").strip().lower()


def _requires_os_credential(browser: Any) -> bool:
    """True if this browser's cookie values are OS-encrypted (chromium family).

    Reuses :data:`notebooklm.os_credentials.ENCRYPTED_COOKIE_BROWSERS` so the
    encryption partition has a single source of truth (a Phase 2C drift-guard test
    keeps it aligned with ``browser_cookies``). Firefox/Safari are plaintext.
    """

    return _canon_browser(browser) in set(_oscreds.ENCRYPTED_COOKIE_BROWSERS)


def _parity_state(row: dict[str, Any]) -> str:
    """Copy the source row's parity state verbatim (defaulting unknown -> open).

    This module never invents a ``pass``: an absent/invalid state is reported as
    ``open`` (never a success state), and a genuine source state is echoed as-is.
    """

    state = row.get("parity_state", "open")
    return state if state in _CLOSURE_STATES else "open"


def _classify_cookie_row(row: dict[str, Any]) -> dict[str, Any]:
    path = row.get("path")
    foundation = _COOKIE_PATH_FOUNDATION.get(path, FOUNDATION_NONE)
    requires_decryptor = _requires_os_credential(row.get("browser"))
    parity_state = _parity_state(row)

    blocked_by = {BLOCK_BROWSER_DISCOVERY, BLOCK_DIFFERENTIAL}
    if requires_decryptor:
        blocked_by.add(BLOCK_OS_CREDENTIAL)
    if parity_state == "pass":
        blocked_by.clear()

    return {
        "matrix": "browser_cookie_import",
        "browser": row.get("browser"),
        "os": row.get("os"),
        "selector": path,
        "selector_kind": "path",
        "parity_state": parity_state,
        "foundation_state": foundation,
        "foundation_covered": foundation == FOUNDATION_IMPLEMENTED,
        "foundation_ref": _COOKIE_PATH_REF.get(path),
        "requires_os_credential_decryptor": requires_decryptor,
        # Foundation coverage never promotes an open row, while an evidence-backed
        # source pass is no longer a live blocker.
        "blocked_live": parity_state != "pass",
        "blocked_by": sorted(blocked_by),
    }


def _classify_login_row(row: dict[str, Any]) -> dict[str, Any]:
    raw_flow = row.get("flow")
    flow = raw_flow if isinstance(raw_flow, str) else ""
    foundation = _LOGIN_FLOW_FOUNDATION.get(flow, FOUNDATION_NONE)
    parity_state = _parity_state(row)
    blocked_by = {BLOCK_LIVE_LOGIN, BLOCK_DIFFERENTIAL}
    if flow == "refresh":
        blocked_by.add(BLOCK_NETWORK_REFRESH)
    if parity_state == "pass":
        blocked_by.clear()

    return {
        "matrix": "interactive_login",
        "browser": row.get("browser"),
        "os": row.get("os"),
        "selector": flow,
        "selector_kind": "flow",
        "parity_state": parity_state,
        "foundation_state": foundation,
        "foundation_covered": False,
        "foundation_ref": _LOGIN_FLOW_REF.get(flow),
        "requires_os_credential_decryptor": False,
        "blocked_live": parity_state != "pass",
        "blocked_by": sorted(blocked_by),
    }


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #


def _matrix_aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "rows": len(rows),
        "foundation_covered": sum(
            1 for r in rows if r["foundation_state"] == FOUNDATION_IMPLEMENTED
        ),
        "foundation_partial": sum(
            1 for r in rows if r["foundation_state"] == FOUNDATION_PARTIAL
        ),
        "foundation_none": sum(
            1 for r in rows if r["foundation_state"] == FOUNDATION_NONE
        ),
        "blocked_live": sum(1 for r in rows if r["blocked_live"]),
        "parity_pass": sum(1 for r in rows if r["parity_state"] == "pass"),
        "parity_open": sum(1 for r in rows if r["parity_state"] == "open"),
        "parity_blocked": sum(1 for r in rows if r["parity_state"] == "blocked"),
    }


def _profile_exclusion_summary(raw: Any) -> dict[str, Any]:
    entries = raw if isinstance(raw, list) else []
    reason_counts: dict[str, int] = {}
    path_count = 0
    entry_count = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_count += 1
        count = 1 if entry.get("path") else 5
        path_count += count
        reason = entry.get("reason")
        if isinstance(reason, str) and reason:
            reason_counts[reason] = reason_counts.get(reason, 0) + count
    return {
        "entry_count": entry_count,
        "path_count": path_count,
        "reason_counts": dict(sorted(reason_counts.items())),
        "deferred_future_release_path_count": reason_counts.get(
            "deferred_to_future_release", 0
        ),
    }


def _build_summary(
    login_rows: list[dict[str, Any]],
    cookie_rows: list[dict[str, Any]],
    profile_exclusions: Any = None,
) -> dict[str, Any]:
    rows = login_rows + cookie_rows
    total = len(rows)
    parity_pass = sum(1 for r in rows if r["parity_state"] == "pass")
    parity_open = sum(1 for r in rows if r["parity_state"] == "open")
    parity_blocked = sum(1 for r in rows if r["parity_state"] == "blocked")
    covered = sum(1 for r in rows if r["foundation_state"] == FOUNDATION_IMPLEMENTED)
    partial = sum(1 for r in rows if r["foundation_state"] == FOUNDATION_PARTIAL)
    none = sum(1 for r in rows if r["foundation_state"] == FOUNDATION_NONE)
    blocked_live = sum(1 for r in rows if r["blocked_live"])
    blockers = sorted({code for r in rows for code in r["blocked_by"]})
    exclusions = _profile_exclusion_summary(profile_exclusions)

    # Selected-profile closure is not a universal release claim while explicit
    # paths remain outside that profile.
    release_blocked = parity_pass < total or exclusions["path_count"] > 0

    login_partial = sum(
        1 for r in login_rows if r["foundation_state"] == FOUNDATION_PARTIAL
    )
    login_none = sum(1 for r in login_rows if r["foundation_state"] == FOUNDATION_NONE)
    cookie_partial = sum(
        1 for r in cookie_rows if r["foundation_state"] == FOUNDATION_PARTIAL
    )
    if parity_open or parity_blocked:
        closure_reason = (
            f"{parity_open + parity_blocked} of {total} selected auth-matrix rows "
            f"are not pass (parity_pass_count={parity_pass}), so selected-profile "
            "auth parity is not closed"
        )
    elif exclusions["path_count"]:
        closure_reason = (
            f"All {total} selected current-release auth rows pass; "
            f"{exclusions['path_count']} explicit profile exclusions remain outside "
            f"that row set, including "
            f"{exclusions['deferred_future_release_path_count']} paths deferred to a "
            "future release. Selected-profile closure does not establish universal "
            "auth parity or release readiness"
        )
    else:
        closure_reason = f"All {total} auth-matrix rows pass with no profile exclusions"

    reasons = [
        closure_reason,
        f"{login_partial} interactive-login rows have a partial Phase 2F/2G "
        f"foundation (login browser/CDP loop and profile-backed refresh token "
        f"probe), while {login_none} interactive rows have no offline interactive-"
        "session foundation; these foundation labels do not reopen pass rows",
        f"{covered} browser-cookie import/inspect/refresh rows have an offline "
        "fixture-only foundation; these foundation labels do not reopen pass rows",
        f"{cookie_partial} profile-select/account-select rows have only partial offline "
        f"support (explicit fixture profile / accounts-file selection; no live "
        f"profile enumeration or store-derived account discovery); this does not "
        "change row-specific pass evidence",
    ]

    if parity_open or parity_blocked:
        next_required_authorization = (
            "Explicit authorization is required before reading real browser cookie "
            "stores, using OS credential decryption, or running a disposable live "
            "Google/NotebookLM differential for selected rows that are not pass."
        )
    elif exclusions["path_count"]:
        other_exclusions = (
            exclusions["path_count"]
            - exclusions["deferred_future_release_path_count"]
        )
        next_required_authorization = (
            "No additional live/browser/credential work is required for the selected "
            "current-release auth profile. Revisit the "
            f"{exclusions['deferred_future_release_path_count']} deferred paths only "
            "in a separately scoped future release; "
            f"{other_exclusions} other paths remain outside the profile. Universal "
            "release claims remain blocked while exclusions exist."
        )
    else:
        next_required_authorization = (
            "No additional auth-matrix authorization is required; all included rows "
            "pass and no profile exclusions remain."
        )

    return {
        "total_rows": total,
        "interactive_login_rows": len(login_rows),
        "browser_cookie_import_rows": len(cookie_rows),
        "parity_pass_count": parity_pass,
        "parity_open_count": parity_open,
        "parity_blocked_count": parity_blocked,
        "foundation_covered_count": covered,
        "foundation_partial_count": partial,
        "foundation_none_count": none,
        "blocked_live_count": blocked_live,
        "blockers": blockers,
        "profile_exclusion_entry_count": exclusions["entry_count"],
        "profile_exclusion_path_count": exclusions["path_count"],
        "profile_exclusion_reason_counts": exclusions["reason_counts"],
        "deferred_future_release_path_count": exclusions[
            "deferred_future_release_path_count"
        ],
        "release_blocked": release_blocked,
        "os_key_backends": _oscreds.supported_backends(),
        "reasons": reasons,
        "next_required_authorization": next_required_authorization,
    }


# --------------------------------------------------------------------------- #
# Public entry points
# --------------------------------------------------------------------------- #


def analyze_auth_matrix(
    matrix: dict[str, Any], *, matrix_path: Optional[str] = None
) -> dict[str, Any]:
    """Classify an auth-matrix data object into an offline-readiness report.

    ``matrix`` must be a dict carrying ``interactive_login_matrix`` and/or
    ``browser_cookie_import_matrix`` lists (the pinned shape). The returned report
    has a Core-facing ``summary``, per-``matrices`` aggregates, and a ``rows`` list
    of per-row classifications. Pure: it reads ``matrix`` and returns a new dict;
    it mutates nothing.
    """

    if not isinstance(matrix, dict):
        raise ValidationError("auth matrix must be a JSON object")
    login_src = matrix.get("interactive_login_matrix")
    cookie_src = matrix.get("browser_cookie_import_matrix")
    if not isinstance(login_src, list) and not isinstance(cookie_src, list):
        raise ValidationError(
            "not an auth matrix: expected an 'interactive_login_matrix' and/or "
            "'browser_cookie_import_matrix' list"
        )

    login_rows = [
        _classify_login_row(r) for r in (login_src or []) if isinstance(r, dict)
    ]
    cookie_rows = [
        _classify_cookie_row(r) for r in (cookie_src or []) if isinstance(r, dict)
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "auth_matrix_path": matrix_path,
        "target": matrix.get("target"),
        "source_generated_at": matrix.get("generated_at"),
        "summary": _build_summary(
            login_rows, cookie_rows, matrix.get("profile_exclusions")
        ),
        "matrices": {
            "interactive_login": _matrix_aggregate(login_rows),
            "browser_cookie_import": _matrix_aggregate(cookie_rows),
        },
        "rows": login_rows + cookie_rows,
    }


def build_report(path: str | Path) -> dict[str, Any]:
    """Load an auth matrix from an explicit path and analyze it.

    Records the explicit source path in the report. Raises ``ValidationError`` if
    the file is missing or malformed (the CLI maps this to a clear usage error).
    """

    p = Path(path)
    matrix = load_auth_matrix(p)
    return analyze_auth_matrix(matrix, matrix_path=str(p))


def human_view(report: dict[str, Any]) -> dict[str, Any]:
    """Return a compact, path-redacted view of a report for human (non-JSON) output.

    Drops the verbose per-row list and reduces the matrix path to its basename, so
    human output never prints a full filesystem path. The JSON view keeps the
    explicit path and full per-row detail.
    """

    path = report.get("auth_matrix_path")
    view = {k: v for k, v in report.items() if k != "rows"}
    view["auth_matrix_path"] = Path(path).name if path else None
    view["rows_omitted"] = len(report.get("rows", []))
    return view
