#!/usr/bin/env python3
"""Opt-in read-only live smoke gate for NotebookLM Bare.

The committed test suite stays hermetic: importing this module and running it with
no flags never reads ``Path.home()``, auth storage, browser stores, keychains, or
the network. A live probe requires all of:

* ``NOTEBOOKLM_BARE_LIVE_SMOKE=1`` in the environment;
* ``--allow-live`` on the command line; and
* an explicit ``--storage-state PATH``.

Even then, this runner is read-only. The optional network auth probe calls the
existing token-fetch diagnostic with ``persist=False`` so observed cookie rotation
is not written back to the supplied storage file.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

# Support direct execution from scripts/ without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from notebooklm import auth  # noqa: E402
from notebooklm.errors import NotebookLMError, RateLimitError, exit_code_for  # noqa: E402

ALLOW_ENV = "NOTEBOOKLM_BARE_LIVE_SMOKE"
ROTATE_429_COOLDOWN_SECONDS = 3600

Fetcher = Callable[..., Mapping[str, Any]]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-live",
        action="store_true",
        help="acknowledge that this run may read the explicit storage-state and optionally use network auth",
    )
    parser.add_argument(
        "--storage-state",
        help="explicit Playwright storage_state.json path; no default/home lookup is used",
    )
    parser.add_argument(
        "--network-auth",
        action="store_true",
        help="also perform the read-only NotebookLM token-fetch probe with persist=False",
    )
    return parser


def _skip_payload(args: argparse.Namespace, env: Mapping[str, str]) -> dict[str, Any]:
    reasons = []
    if env.get(ALLOW_ENV) != "1":
        reasons.append(f"{ALLOW_ENV}=1 not set")
    if not args.allow_live:
        reasons.append("--allow-live not set")
    return {
        "status": "skipped",
        "reason": "; ".join(reasons) or "live smoke disabled",
        "live_enabled": False,
        "read_only": True,
        "mutation_allowed": False,
        "network_auth": False,
        "storage_state": None,
        "checks": {},
    }


def _redact_offline_check(summary: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "ok": bool(summary.get("ok")),
        "exists": bool(summary.get("exists")),
        "readable": bool(summary.get("readable")),
        "valid_json": bool(summary.get("valid_json")),
        "cookie_count": int(summary.get("cookie_count") or 0),
        "cookie_names": list(summary.get("cookie_names") or []),
        "missing_cookies": list(summary.get("missing_cookies") or []),
        "has_required_cookies": bool(summary.get("has_required_cookies")),
        "domains_ok": bool(summary.get("domains_ok")),
        "unexpected_domains_count": len(summary.get("unexpected_domains") or []),
    }


def _redact_network(summary: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {
        "ok",
        "network_test",
        "token_fetch_ok",
        "csrf_token_present",
        "session_id_present",
        "rotated_cookie_names",
        "cookie_count",
        "cookie_names",
    }
    return {key: summary[key] for key in allowed if key in summary}


def _error_payload(reason: str, *, code: int = 64) -> tuple[int, dict[str, Any]]:
    return code, {
        "status": "error",
        "reason": reason,
        "live_enabled": True,
        "read_only": True,
        "mutation_allowed": False,
        "network_auth": False,
        "storage_state": None,
        "checks": {},
    }


def _rotate_429_cooldown_path(storage: Path) -> Path:
    return storage.with_name(storage.name + ".rotatecookies-429-cooldown.json")


def _cooldown_active(path: Path, *, now: float | None = None) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    expires_at = data.get("expires_at") if isinstance(data, dict) else None
    return isinstance(expires_at, (int, float)) and expires_at > (
        time.time() if now is None else now
    )


def _write_rotate_429_cooldown(storage: Path) -> bool:
    marker = _rotate_429_cooldown_path(storage)
    payload = {
        "schema_version": "rotatecookies_429_cooldown/1",
        "reason": "rotatecookies_http_429",
        "created_at": time.time(),
        "expires_at": time.time() + ROTATE_429_COOLDOWN_SECONDS,
    }
    try:
        marker.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        marker.chmod(0o600)
        return True
    except OSError:
        return False


def _is_rotate_429(exc: BaseException) -> bool:
    return isinstance(exc, RateLimitError) and "RotateCookies" in str(exc)


def run(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    fetcher: Fetcher | None = None,
) -> tuple[int, dict[str, Any]]:
    """Run the smoke gate and return ``(exit_code, redacted_payload)``."""

    parser = _parser()
    args = parser.parse_args(list(argv or []))
    live_env = env if env is not None else os.environ

    if live_env.get(ALLOW_ENV) != "1" or not args.allow_live:
        return 0, _skip_payload(args, live_env)

    if not args.storage_state:
        return _error_payload("--storage-state is required when live smoke is enabled")

    storage = Path(args.storage_state)
    if not storage.is_file():
        return _error_payload("--storage-state must point to an existing file")

    checks: dict[str, Any] = {}
    offline = auth.check_storage(storage)
    checks["offline_auth"] = _redact_offline_check(offline)
    payload: dict[str, Any] = {
        "status": "passed" if offline.get("ok") else "failed",
        "live_enabled": True,
        "read_only": True,
        "mutation_allowed": False,
        "network_auth": bool(args.network_auth),
        "storage_state": "set",
        "checks": checks,
    }
    if not offline.get("ok"):
        checks["network_auth"] = {
            "skipped": True,
            "reason": "offline auth check failed",
        }
        return 77, payload

    if not args.network_auth:
        checks["network_auth"] = {"skipped": True, "reason": "--network-auth not set"}
        return 0, payload

    if _cooldown_active(_rotate_429_cooldown_path(storage)):
        checks["network_auth"] = {
            "ok": False,
            "skipped": True,
            "reason": "rotatecookies_429_cooldown_active",
            "cooldown_active": True,
        }
        payload["status"] = "failed"
        return 77, payload

    token_fetcher = fetcher or auth.fetch_tokens_from_storage
    try:
        network = token_fetcher(storage, persist=False)
    except NotebookLMError as exc:
        checks["network_auth"] = {
            "ok": False,
            "error_type": type(exc).__name__,
            "reason": str(exc),
        }
        if _is_rotate_429(exc):
            checks["network_auth"]["cooldown_active"] = _write_rotate_429_cooldown(
                storage
            )
        payload["status"] = "failed"
        return exit_code_for(exc), payload
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        checks["network_auth"] = {
            "ok": False,
            "error_type": type(exc).__name__,
            "reason": "unexpected live smoke failure",
        }
        payload["status"] = "failed"
        return 70, payload

    checks["network_auth"] = _redact_network(network)
    if not checks["network_auth"].get("token_fetch_ok", False):
        payload["status"] = "failed"
        return 77, payload
    return 0, payload


def main(argv: Sequence[str] | None = None) -> int:
    actual_argv = sys.argv[1:] if argv is None else argv
    code, payload = run(actual_argv)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":  # pragma: no cover - exercised by subprocess users
    raise SystemExit(main())
