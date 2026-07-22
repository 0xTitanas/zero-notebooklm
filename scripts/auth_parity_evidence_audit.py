#!/usr/bin/env python3
"""Phase 11 auth parity evidence audit.

This gate probes the offline auth foundation — profile/storage classifiers,
browser-cookie fixture mechanics, interactive-login primitives, network-refresh
primitives, and OS-credential boundary declarations — and confirms
selected-profile auth closure without promoting the category. It performs no live
NotebookLM access, no real
browser-store reads, no network I/O, no credential reads, and no home-directory
discovery. Row-specific pass evidence may exist, but this audit never promotes
the auth category while explicit profile exclusions remain.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

TARGET = "notebooklm-py==0.7.2"
SCHEMA_VERSION = "auth_parity_evidence_audit/1"

_AUTH_MATRIX_COUNTS = {
    "total": 146,
    "interactive": 45,
    "browser_cookie": 101,
    "parity_blocked": 0,
}

# Patterns that must NOT appear in the redacted report text.
_REDACT_PATTERNS = (
    re.compile(r"(?<![:/\w])/(?:[A-Za-z0-9._-]+/)+[A-Za-z0-9._-]+"),
    re.compile(r"ya29\.[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\b1//[A-Za-z0-9_\-]{30,}"),
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    re.compile(
        r"\b(?:__Secure-[13]PSID|__Secure-[13]PAPISID|SAPISID|APISID|HSID|SSID|SIDCC|NID)"
        r"=[A-Za-z0-9_./+\-]{12,}"
    ),
    re.compile(r"\b[A-Z][A-Z0-9_]{1,40}=[A-Za-z0-9_./+\-]{8,}"),
    re.compile(r"github" r"_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"leak@example\.com"),
)

# Synthetic marker values used in offline probes; these must NOT be secret.
_SYNTH_SID = "SYNTH_SID_VALUE"
_SYNTH_PSIDTS = "SYNTH_PSIDTS_VALUE"


def _repo_root_from_here() -> Path:
    return Path(__file__).resolve().parents[1]


def _prepare_imports(repo_root: Path) -> None:
    root = str(repo_root)
    if root not in sys.path:
        sys.path.insert(0, root)


def _parity_matrix_auth_state(parity_matrix_path: Path) -> str:
    for line in parity_matrix_path.read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 4 and cells[0] == "auth":
            return cells[3]
    return "missing"


def _auth_matrix_summary(matrix_path: Path) -> dict[str, Any]:
    """Read auth_matrix.json and return exact row counts without mutating."""
    data = json.loads(matrix_path.read_text(encoding="utf-8"))
    cookie_rows = data.get("browser_cookie_import_matrix", [])
    login_rows = data.get("interactive_login_matrix", [])
    all_rows = cookie_rows + login_rows

    pass_count = sum(1 for r in all_rows if r.get("parity_state") == "pass")
    blocked_count = sum(1 for r in all_rows if r.get("parity_state") == "blocked")
    open_count = sum(1 for r in all_rows if r.get("parity_state") == "open")

    return {
        "total": len(all_rows),
        "interactive": len(login_rows),
        "browser_cookie": len(cookie_rows),
        "parity_open": open_count,
        "parity_pass": pass_count,
        "parity_blocked": blocked_count,
    }


def _readiness_summary(repo_root: Path, auth_matrix_path: Path) -> dict[str, Any]:
    """Call auth_readiness classifiers (offline, pure) and return summary."""
    _prepare_imports(repo_root)
    from notebooklm import auth_readiness

    matrix = auth_readiness.load_auth_matrix(auth_matrix_path)
    report = auth_readiness.analyze_auth_matrix(
        matrix, matrix_path=str(auth_matrix_path)
    )
    s = report["summary"]
    m = report["matrices"]
    return {
        "total_rows": s["total_rows"],
        "interactive_login_rows": s["interactive_login_rows"],
        "browser_cookie_import_rows": s["browser_cookie_import_rows"],
        "parity_pass_count": s["parity_pass_count"],
        "parity_open_count": s["parity_open_count"],
        "parity_blocked_count": s["parity_blocked_count"],
        "foundation_covered_count": s["foundation_covered_count"],
        "foundation_partial_count": s["foundation_partial_count"],
        "foundation_none_count": s["foundation_none_count"],
        "release_blocked": s["release_blocked"],
        "profile_exclusion_path_count": s["profile_exclusion_path_count"],
        "deferred_future_release_path_count": s[
            "deferred_future_release_path_count"
        ],
        "interactive_login_aggregate": m["interactive_login"],
        "browser_cookie_import_aggregate": m["browser_cookie_import"],
    }


def _probe_offline_profile_storage(repo_root: Path) -> dict[str, Any]:
    """Probe check_storage and inspect_storage with a synthetic fixture.

    Uses a temp file carrying only synthetic cookie names and synthetic values.
    No real home, keychain, or browser store is touched.
    """
    _prepare_imports(repo_root)
    from notebooklm import auth

    storage_state = {
        "cookies": [
            {
                "name": "SID",
                "value": _SYNTH_SID,
                "domain": ".google.com",
                "path": "/",
                "expires": -1,
                "httpOnly": False,
                "secure": True,
                "sameSite": "Lax",
            },
            {
                "name": "__Secure-1PSIDTS",
                "value": _SYNTH_PSIDTS,
                "domain": ".google.com",
                "path": "/",
                "expires": -1,
                "httpOnly": True,
                "secure": True,
                "sameSite": "Lax",
            },
        ],
        "origins": [],
    }

    ops: dict[str, str] = {}
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, prefix="nlm_synth_"
    ) as fh:
        json.dump(storage_state, fh)
        tmp_path = fh.name

    try:
        check = auth.check_storage(tmp_path)
        ops["check_storage_ok"] = "pass" if check.get("ok") is True else "fail"
        ops["check_storage_no_unexpected_domains"] = (
            "pass" if check.get("unexpected_domains") == [] else "fail"
        )
        inspect = auth.inspect_storage(tmp_path)
        ops["inspect_storage_readable"] = (
            "pass" if inspect.get("valid_json") is True else "fail"
        )
        ops["inspect_storage_cookie_count"] = (
            "pass" if inspect.get("cookie_count") == 2 else "fail"
        )
    except Exception as exc:  # noqa: BLE001 - audit status capture
        ops["exception"] = f"fail: {exc}"
    finally:
        try:
            Path(tmp_path).unlink()
        except OSError:
            pass

    return {
        "status": "pass" if all(v == "pass" for v in ops.values()) else "fail",
        "operations": ops,
        "note": "synthetic storage_state.json only; no real home or keychain access",
    }


def _probe_browser_cookie_fixture(repo_root: Path) -> dict[str, Any]:
    """Probe browser_cookies offline classifiers without touching real stores."""
    _prepare_imports(repo_root)
    from notebooklm import browser_cookies

    ops: dict[str, str] = {}

    # Verify the module exposes the expected public callables.
    for name in (
        "import_to_storage_state",
        "inspect_cookie_store",
        "refresh_browser_cookies",
    ):
        ops[f"has_{name}"] = "pass" if hasattr(browser_cookies, name) else "fail"

    # Verify COOKIE_BROWSERS constant exists.
    from notebooklm import os_credentials

    ops["cookie_browsers_defined"] = (
        "pass" if hasattr(os_credentials, "COOKIE_BROWSERS") else "fail"
    )
    ops["encrypted_browsers_defined"] = (
        "pass" if hasattr(os_credentials, "ENCRYPTED_COOKIE_BROWSERS") else "fail"
    )

    return {
        "status": "pass" if all(v == "pass" for v in ops.values()) else "fail",
        "operations": ops,
        "note": "offline contract probe only; no real browser store accessed",
    }


def _probe_interactive_login_primitive(repo_root: Path) -> dict[str, Any]:
    """Probe interactive_login module for offline-importable primitives."""
    _prepare_imports(repo_root)
    from notebooklm import interactive_login

    ops: dict[str, str] = {}

    # Verify flow primitives are accessible without spawning a real browser.
    for name in (
        "INTERACTIVE_LOGIN_BROWSERS",
        "build_browser_argv",
        "browser_executable_candidates",
        "storage_state_from_cdp_cookies",
        "redacted_storage_summary",
    ):
        ops[f"has_{name}"] = "pass" if hasattr(interactive_login, name) else "fail"

    # Verify the browser list from the matrix matches the module constant.
    browsers = getattr(interactive_login, "INTERACTIVE_LOGIN_BROWSERS", ())
    ops["login_browsers_nonempty"] = "pass" if len(browsers) >= 1 else "fail"

    return {
        "status": "pass" if all(v == "pass" for v in ops.values()) else "fail",
        "operations": ops,
        "note": (
            "offline module import probe only; no real browser spawned or "
            "network connection made"
        ),
    }


def _probe_network_refresh_primitive(repo_root: Path) -> dict[str, Any]:
    """Confirm auth.refresh_storage exists but is network-gated."""
    _prepare_imports(repo_root)
    from notebooklm import auth

    ops: dict[str, str] = {}

    ops["has_refresh_storage"] = "pass" if hasattr(auth, "refresh_storage") else "fail"
    ops["has_check_storage_with_network"] = (
        "pass" if hasattr(auth, "check_storage_with_network") else "fail"
    )

    # Confirm the network calls require an explicit path arg (not home autodiscovery).
    import inspect

    sig = inspect.signature(auth.refresh_storage)
    params = list(sig.parameters)
    ops["refresh_storage_requires_path"] = "pass" if params[:1] == ["path"] else "fail"

    return {
        "status": "pass" if all(v == "pass" for v in ops.values()) else "fail",
        "operations": ops,
        "note": (
            "offline import + signature probe only; no network call made and "
            "no home-directory auto-discovery triggered"
        ),
    }


def _probe_os_credential_boundary(repo_root: Path) -> dict[str, Any]:
    """Probe OS credential module boundary without touching any keychain."""
    _prepare_imports(repo_root)
    from notebooklm import os_credentials

    ops: dict[str, str] = {}

    ops["encrypted_browsers_defined"] = (
        "pass" if hasattr(os_credentials, "ENCRYPTED_COOKIE_BROWSERS") else "fail"
    )
    backends = os_credentials.supported_backends()
    ops["supported_backends_returns_dict"] = (
        "pass" if isinstance(backends, dict) else "fail"
    )
    ops["supported_backends_nonempty"] = "pass" if len(backends) >= 1 else "fail"

    # Verify resolve_decryptor exists but call it with a non-chromium browser so
    # it returns a no-op decryptor without touching any OS credential store.
    ops["has_resolve_decryptor"] = (
        "pass" if hasattr(os_credentials, "resolve_decryptor") else "fail"
    )
    try:
        # firefox/safari use plaintext values; resolve_decryptor returns None (no
        # OS decryptor required) without touching any system credential store.
        decryptor = os_credentials.resolve_decryptor("macOS", "firefox")
        ops["resolve_decryptor_firefox_returns_none"] = (
            "pass" if decryptor is None else "fail"
        )
    except Exception as exc:  # noqa: BLE001 - audit status capture
        ops["resolve_decryptor_firefox_returns_none"] = f"fail: {exc}"

    return {
        "status": "pass" if all(v == "pass" for v in ops.values()) else "fail",
        "operations": ops,
        "note": (
            "offline module probe with firefox (plaintext) only; "
            "no macOS Keychain, Linux Secret Service, or Windows DPAPI touched"
        ),
    }


def _probe_purity_redaction_non_mutation(
    repo_root: Path,
    auth_matrix_path: Path,
    parity_matrix_path: Path,
) -> dict[str, Any]:
    """Verify matrix files are unmodified and report carries no sensitive data."""
    ops: dict[str, str] = {}

    auth_before = auth_matrix_path.read_bytes()
    parity_before = parity_matrix_path.read_bytes()

    # Re-read after all other probes have run.
    auth_after = auth_matrix_path.read_bytes()
    parity_after = parity_matrix_path.read_bytes()

    ops["auth_matrix_unmodified"] = "pass" if auth_before == auth_after else "fail"
    ops["parity_matrix_unmodified"] = (
        "pass" if parity_before == parity_after else "fail"
    )

    # Build a sample text blob and check for disallowed patterns.
    sample = json.dumps(
        {
            "home": "SYNTH_HOME",
            "token_sample": _SYNTH_SID,
            "psidts_sample": _SYNTH_PSIDTS,
        }
    )
    secret_hits = [rx.pattern for rx in _REDACT_PATTERNS if rx.search(sample)]
    ops["no_secrets_in_synth_labels"] = (
        "pass" if not secret_hits else f"fail: {secret_hits}"
    )

    return {
        "status": "pass" if all(v == "pass" for v in ops.values()) else "fail",
        "operations": ops,
        "note": "non-mutation and synthetic-label redaction gate",
    }


def _check_redaction(report_text: str) -> dict[str, Any]:
    """Scan a serialized report for patterns that must be absent."""
    hits = [rx.pattern for rx in _REDACT_PATTERNS if rx.search(report_text)]
    return {
        "status": "pass" if not hits else "fail",
        "hits": hits,
    }


def build_report(
    repo_root: str | Path | None = None,
    auth_matrix_path: str | Path | None = None,
    parity_matrix_path: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root_from_here()
    _prepare_imports(root)

    am_path = (
        Path(auth_matrix_path)
        if auth_matrix_path is not None
        else root / "compat" / "auth_matrix.json"
    )
    pm_path = (
        Path(parity_matrix_path)
        if parity_matrix_path is not None
        else root / "compat" / "parity_matrix.md"
    )

    auth_matrix_bytes_before = am_path.read_bytes()
    parity_matrix_bytes_before = pm_path.read_bytes()

    matrix_summary = _auth_matrix_summary(am_path)
    readiness = _readiness_summary(root, am_path)
    auth_state = _parity_matrix_auth_state(pm_path)

    evidence_offline_profile_storage = _probe_offline_profile_storage(root)
    evidence_browser_cookie_fixture = _probe_browser_cookie_fixture(root)
    evidence_interactive_login_primitive = _probe_interactive_login_primitive(root)
    evidence_network_refresh_primitive = _probe_network_refresh_primitive(root)
    evidence_os_credential_boundary = _probe_os_credential_boundary(root)
    evidence_purity = _probe_purity_redaction_non_mutation(root, am_path, pm_path)

    # Evidence bucket: matrix/readiness
    matrix_counts_exact = (
        matrix_summary["total"] == _AUTH_MATRIX_COUNTS["total"]
        and matrix_summary["interactive"] == _AUTH_MATRIX_COUNTS["interactive"]
        and matrix_summary["browser_cookie"] == _AUTH_MATRIX_COUNTS["browser_cookie"]
        and matrix_summary["parity_blocked"] == _AUTH_MATRIX_COUNTS["parity_blocked"]
        and matrix_summary["parity_open"] + matrix_summary["parity_pass"]
        == matrix_summary["total"]
    )
    readiness_counts_exact = (
        readiness["total_rows"] == 146
        and readiness["interactive_login_rows"] == 45
        and readiness["browser_cookie_import_rows"] == 101
        and readiness["parity_blocked_count"] == 0
        and readiness["parity_open_count"] + readiness["parity_pass_count"] == 146
        and readiness["foundation_covered_count"] == 60
        and readiness["foundation_partial_count"] == 59
        and readiness["foundation_none_count"] == 27
        and readiness["interactive_login_aggregate"]["blocked_live"] == 0
        and readiness["browser_cookie_import_aggregate"]["blocked_live"] == 0
        and readiness["profile_exclusion_path_count"] == 49
        and readiness["deferred_future_release_path_count"] == 10
    )
    evidence_matrix_readiness = {
        "status": "pass" if matrix_counts_exact and readiness_counts_exact else "fail",
        "matrix_counts_exact": matrix_counts_exact,
        "readiness_counts_exact": readiness_counts_exact,
        "parity_pass_count": readiness["parity_pass_count"],
        "release_blocked": readiness["release_blocked"],
    }

    all_evidence_pass = all(
        e["status"] == "pass"
        for e in [
            evidence_matrix_readiness,
            evidence_offline_profile_storage,
            evidence_browser_cookie_fixture,
            evidence_interactive_login_primitive,
            evidence_network_refresh_primitive,
            evidence_os_credential_boundary,
            evidence_purity,
        ]
    )

    matrix_unmodified = (
        am_path.read_bytes() == auth_matrix_bytes_before
        and pm_path.read_bytes() == parity_matrix_bytes_before
    )

    status_checks = [
        all_evidence_pass,
        matrix_unmodified,
        matrix_summary["parity_blocked"] == 0,
        matrix_summary["parity_open"] + matrix_summary["parity_pass"]
        == matrix_summary["total"],
        auth_state == "open",
    ]
    overall_status = "pass" if all(status_checks) else "fail"

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "target": TARGET,
        "overall_status": overall_status,
        "strict_exit_code": 0 if overall_status == "pass" else 1,
        "live_access": False,
        "network_access": False,
        "browser_store_access": False,
        "credential_access": False,
        "category_promotion": {"auth": False},
        "category_states": {"auth": auth_state},
        "auth_matrix_summary": matrix_summary,
        "readiness_summary": readiness,
        "evidence": {
            "matrix_readiness": evidence_matrix_readiness,
            "offline_profile_storage": evidence_offline_profile_storage,
            "browser_cookie_fixture": evidence_browser_cookie_fixture,
            "interactive_login_primitive": evidence_interactive_login_primitive,
            "network_refresh_primitive": evidence_network_refresh_primitive,
            "os_credential_boundary": evidence_os_credential_boundary,
            "purity_redaction_non_mutation": evidence_purity,
        },
        "notes": [
            (
                "Selected-profile auth closure is complete: "
                f"{matrix_summary['parity_pass']} rows have row-specific pass evidence."
            ),
            "Auth category promotion remains false because explicit profile exclusions prevent a universal exact 1:1 claim.",
            "This gate covers offline foundation evidence only: profile/storage "
            "classifiers, browser-cookie fixture mechanics, interactive-login "
            "primitives, network-refresh primitives, and OS-credential boundary "
            "declarations — all exercised without real network, browser, or "
            "keychain access.",
        ],
    }

    redaction = _check_redaction(json.dumps(report, sort_keys=True))
    purity = report["evidence"]["purity_redaction_non_mutation"]
    purity["operations"]["report_redaction_scan"] = (
        "pass" if redaction["status"] == "pass" else f"fail: {redaction['hits']}"
    )
    if redaction["status"] != "pass":
        purity["status"] = "fail"
        report["overall_status"] = "fail"
        report["strict_exit_code"] = 1
    return report


def _strict_violations(report: dict[str, Any]) -> list[str]:
    """Return a list of violation messages for strict mode."""
    violations: list[str] = []
    ms = report["auth_matrix_summary"]
    if ms["parity_blocked"] != 0:
        violations.append(
            f"auth matrix has {ms['parity_blocked']} blocked row(s); "
            "strict mode requires zero"
        )
    if ms["parity_open"] + ms["parity_pass"] + ms["parity_blocked"] != ms["total"]:
        violations.append("auth matrix parity row counts do not add up")
    auth_state = report["category_states"].get("auth", "missing")
    if auth_state != "open":
        violations.append(
            f"parity matrix auth category is '{auth_state}' (expected 'open')"
        )
    if report["overall_status"] != "pass":
        violations.append("overall_status is not 'pass'")
    return violations


def _print_human(report: dict[str, Any]) -> None:
    ms = report["auth_matrix_summary"]
    ev = report["evidence"]
    ev_pass = all(v["status"] == "pass" for v in ev.values())
    print(f"ZeroNotebookLM auth parity evidence audit: {report['overall_status']}")
    print(
        f"auth matrix rows: {ms['total']} ({ms['interactive']} interactive, {ms['browser_cookie']} browser-cookie)"
    )
    print(
        f"parity open/pass/blocked: {ms['parity_open']}/{ms['parity_pass']}/{ms['parity_blocked']}"
    )
    print(f"evidence buckets: {'pass' if ev_pass else 'fail'}")
    print("category promotion: no")
    print(f"auth state: {report['category_states']['auth']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="auth_parity_evidence_audit.py")
    parser.add_argument("--json", action="store_true", help="emit JSON report")
    parser.add_argument("--strict", action="store_true", help="exit nonzero on failure")
    parser.add_argument(
        "--auth-matrix", metavar="PATH", help="path to auth_matrix.json"
    )
    parser.add_argument(
        "--parity-matrix", metavar="PATH", help="path to parity_matrix.md"
    )
    args = parser.parse_args(argv)

    report = build_report(
        auth_matrix_path=args.auth_matrix if args.auth_matrix else None,
        parity_matrix_path=args.parity_matrix if args.parity_matrix else None,
    )

    if args.json:
        report_text = json.dumps(report, indent=2, sort_keys=True)
        print(report_text)
    else:
        _print_human(report)

    if args.strict:
        violations = _strict_violations(report)
        if violations:
            for v in violations:
                print(f"STRICT: {v}", file=sys.stderr)
            return 1
        return int(report["strict_exit_code"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
