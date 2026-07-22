#!/usr/bin/env python3
"""Generate redacted auth-row proof records from local live auth probes.

Run this on the target OS. It never writes raw command output, cookies, tokens,
account emails, notebook IDs, or browser profile paths; only row status and
redacted proof tokens are persisted under ``.ai-bridge`` by default.

This local probe emits session-credential evidence only. A separate two-sided
runner must supply live-differential proof before strict evidence validation.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, NamedTuple, Sequence

SCHEMA_VERSION = "live_auth_row_probe/1"
PROOF_SCHEMA_VERSION = "auth_row_proof_records/1"
TARGET = "notebooklm-py==0.7.2"
REPO_ROOT = Path(__file__).resolve().parents[1]
STRICT_BLOCKED_EXIT = 77

OS_SLUGS = {
    "macos": "macOS",
    "ubuntu": "Ubuntu-LTS-Linux",
    "windows11": "Windows-11",
}
COOKIE_BROWSERS = (
    "arc",
    "brave",
    "chrome",
    "chromium",
    "edge",
    "firefox",
    "opera",
    "opera_gx",
    "safari",
    "vivaldi",
)
INTERACTIVE_BROWSERS = ("chrome", "msedge", "chromium")
COOKIE_OPS = ("import", "profile_select", "account_select", "inspect", "refresh")
INTERACTIVE_OPS = ("login", "refresh", "status", "logout", "doctor")
LOCAL_PROOF_TOKENS = ("session_credential_evidence",)

CLI_BROWSER = {"opera_gx": "opera-gx"}
DEFAULT_PROFILE = {
    "arc": "Default",
    "brave": "Default",
    "chrome": "Default",
    "chromium": "Default",
    "edge": "Default",
    "firefox": "none",
    "msedge": "Default",
    "opera": "Default",
    "opera_gx": "Default",
    "vivaldi": "Default",
}


class CommandResult(NamedTuple):
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


Runner = Callable[
    [Sequence[str], Mapping[str, str], Path, int],
    CommandResult,
]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _detect_os_slug() -> str:
    system = platform.system()
    if system == "Windows":
        return "windows11"
    if system == "Linux":
        return "ubuntu"
    if system == "Darwin":
        return "macos"
    raise SystemExit(f"unsupported local OS for auth matrix probing: {system}")


def _split_csv(raw: str | None, default: Sequence[str] = ()) -> tuple[str, ...]:
    if raw is None:
        return tuple(default)
    if not raw.strip():
        return ()
    return tuple(item.strip().lower().replace("-", "_") for item in raw.split(",") if item.strip())


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer") from exc


def _profile_map(values: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in values:
        if "=" not in raw:
            raise SystemExit("--browser-profile entries must be browser=profile")
        browser, profile_name = raw.split("=", 1)
        browser = browser.strip().lower().replace("-", "_")
        profile_name = profile_name.strip()
        if not browser or not profile_name:
            raise SystemExit("--browser-profile entries must be browser=profile")
        result[browser] = profile_name
    return result


def _default_runner(
    args: Sequence[str],
    env: Mapping[str, str],
    cwd: Path,
    timeout: int,
) -> CommandResult:
    try:
        proc = subprocess.run(
            list(args),
            cwd=str(cwd),
            env=dict(env),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return CommandResult(124, stdout, stderr, True)
    return CommandResult(proc.returncode, proc.stdout, proc.stderr)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _json_payload(stdout: str) -> Any:
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return None


def _json_true(payload: Any, key: str) -> bool:
    return isinstance(payload, dict) and payload.get(key) is True


def _has_accounts(payload: Any) -> bool:
    if not isinstance(payload, dict) or set(payload) != {"browser", "accounts"}:
        return False
    accounts = payload.get("accounts")
    return (
        isinstance(payload.get("browser"), str)
        and isinstance(accounts, list)
        and bool(accounts)
        and all(
            isinstance(account, dict)
            and set(account) == {"email", "is_default", "browser_profile"}
            and isinstance(account.get("email"), str)
            and "@" in account["email"]
            and isinstance(account.get("is_default"), bool)
            and (
                account.get("browser_profile") is None
                or isinstance(account.get("browser_profile"), str)
            )
            for account in accounts
        )
    )


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value)


def _cli_browser(browser: str) -> str:
    return CLI_BROWSER.get(browser, browser)


def _row_id(kind: str, browser: str, os_slug: str, op: str) -> str:
    return f"auth.{kind}.{browser}.{os_slug}.{op}"


def _storage_path(scratch_dir: Path, label: str) -> Path:
    return scratch_dir / _safe_name(label) / "storage_state.json"


def _storage_root(scratch_dir: Path, label: str) -> Path:
    return scratch_dir / _safe_name(label)


def _default_profile_storage_path(storage_root: Path) -> Path:
    return storage_root / "profiles" / "default" / "storage_state.json"


def _display_path(path: Path, *, root: Path, output_dir: Path) -> str:
    for base in (root, output_dir):
        try:
            return str(path.relative_to(base))
        except ValueError:
            continue
    return path.name


def _cli(storage: Path | None, args: Sequence[str]) -> list[str]:
    cmd = [sys.executable, "-m", "notebooklm.cli"]
    if storage is not None:
        cmd.extend(["--storage", str(storage)])
    cmd.extend(args)
    return cmd


def _proofs(row_id: str, run_id: str) -> list[dict[str, Any]]:
    return [
        {
            "token": token,
            "evidence_id": f"{row_id}::{token}::{run_id}",
            "evidence_type": "session_credential",
            "status": "pass",
            "redacted": True,
        }
        for token in LOCAL_PROOF_TOKENS
    ]


def _passed(
    rows: list[dict[str, Any]],
    proofs: list[dict[str, Any]],
    row_id: str,
    *,
    browser: str,
    operation: str,
    run_id: str,
) -> None:
    rows.append(
        {
            "row_id": row_id,
            "browser": browser,
            "operation": operation,
            "status": "pass",
            "reason": "live command and auth check passed",
        }
    )
    proofs.append({"row_id": row_id, "proofs": _proofs(row_id, run_id)})


def _blocked(
    rows: list[dict[str, Any]],
    row_id: str,
    *,
    browser: str,
    operation: str,
    reason: str,
    command: str | None = None,
    returncode: int | None = None,
) -> None:
    row = {
        "row_id": row_id,
        "browser": browser,
        "operation": operation,
        "status": "blocked",
        "reason": reason,
    }
    if command:
        row["command"] = command
    if returncode is not None:
        row["returncode"] = returncode
    rows.append(row)


def _auth_check(
    runner: Runner,
    *,
    repo_root: Path,
    env: Mapping[str, str],
    timeout: int,
    storage: Path,
) -> tuple[bool, str]:
    result = runner(
        _cli(storage, ["auth", "check", "--test", "--json"]),
        env,
        repo_root,
        timeout,
    )
    payload = _json_payload(result.stdout)
    if result.returncode == 0 and not result.timed_out:
        return True, "auth check passed"
    if _json_true(payload, "error"):
        return False, "auth check returned error"
    if result.timed_out:
        return False, "auth check timed out"
    return False, f"auth check rc={result.returncode}"


def _login_cookie(
    runner: Runner,
    *,
    repo_root: Path,
    env: Mapping[str, str],
    timeout: int,
    storage: Path,
    browser: str,
    os_name: str,
    profile_name: str | None = None,
    account: str | None = None,
    authuser: int | None = None,
) -> tuple[bool, str]:
    args = ["login", "--browser-cookies", _cli_browser(browser), "--os", os_name, "--json"]
    if profile_name:
        args.extend(["--browser-profile", profile_name])
    if account:
        args.extend(["--account", account])
    elif authuser is not None:
        args.extend(["--authuser", str(authuser)])
    result = runner(_cli(storage, args), env, repo_root, timeout)
    if result.returncode != 0 or result.timed_out:
        reason = "login timed out" if result.timed_out else f"login rc={result.returncode}"
        return False, reason
    return _auth_check(runner, repo_root=repo_root, env=env, timeout=timeout, storage=storage)


def _probe_cookie_browser(
    *,
    runner: Runner,
    repo_root: Path,
    output_dir: Path,
    env: Mapping[str, str],
    timeout: int,
    rows: list[dict[str, Any]],
    proofs: list[dict[str, Any]],
    run_id: str,
    os_slug: str,
    os_name: str,
    browser: str,
    operations: Sequence[str],
    profiles: Mapping[str, str],
    account: str | None,
    authuser: int | None,
) -> None:
    cache: dict[tuple[str | None, str], tuple[bool, str, Path]] = {}

    def ensure_login(profile_name: str | None, selector: str) -> tuple[bool, str, Path]:
        key = (profile_name, selector)
        if key in cache:
            return cache[key]
        label = f"cookie-{browser}-{selector}-{profile_name or 'default'}"
        storage = _storage_path(output_dir, label)
        ok, reason = _login_cookie(
            runner,
            repo_root=repo_root,
            env=env,
            timeout=timeout,
            storage=storage,
            browser=browser,
            os_name=os_name,
            profile_name=profile_name,
            account=account if selector == "account" else None,
            authuser=authuser if selector == "account" and not account else None,
        )
        cache[key] = (ok, reason, storage)
        return cache[key]

    for op in operations:
        row_id = _row_id("cookie_import", browser, os_slug, op)
        if op == "inspect":
            result = runner(
                _cli(
                    None,
                    [
                        "auth",
                        "inspect",
                        "--browser",
                        _cli_browser(browser),
                        "--os",
                        os_name,
                        "--json",
                    ],
                ),
                env,
                repo_root,
                timeout,
            )
            payload = _json_payload(result.stdout)
            ok, login_reason, _ = ensure_login(None, "default")
            if (
                result.returncode == 0
                and not result.timed_out
                and _has_accounts(payload)
                and ok
            ):
                _passed(rows, proofs, row_id, browser=browser, operation=op, run_id=run_id)
            else:
                reason = (
                    "inspect did not report signed-in accounts"
                    if result.returncode == 0 and not result.timed_out
                    else ("inspect timed out" if result.timed_out else f"inspect rc={result.returncode}")
                )
                if ok is False:
                    reason = f"{reason}; {login_reason}"
                _blocked(
                    rows,
                    row_id,
                    browser=browser,
                    operation=op,
                    reason=reason,
                    command="auth inspect",
                    returncode=result.returncode,
                )
            continue

        if op == "profile_select":
            profile_name = profiles.get(browser, DEFAULT_PROFILE.get(browser))
            if not profile_name:
                _blocked(
                    rows,
                    row_id,
                    browser=browser,
                    operation=op,
                    reason="no profile selector configured for this browser",
                )
                continue
            ok, reason, _ = ensure_login(profile_name, "profile")
        elif op == "account_select":
            if not account and authuser is None:
                _blocked(
                    rows,
                    row_id,
                    browser=browser,
                    operation=op,
                    reason="missing --account or --authuser selector",
                )
                continue
            ok, reason, _ = ensure_login(None, "account")
        else:
            ok, reason, storage = ensure_login(None, "default")
            if ok and op == "refresh":
                result = runner(
                    _cli(
                        storage,
                        [
                            "auth",
                            "refresh",
                            "--browser-cookies",
                            _cli_browser(browser),
                            "--os",
                            os_name,
                            "--json",
                        ],
                    ),
                    env,
                    repo_root,
                    timeout,
                )
                if result.returncode == 0 and not result.timed_out:
                    ok, reason = _auth_check(
                        runner,
                        repo_root=repo_root,
                        env=env,
                        timeout=timeout,
                        storage=storage,
                    )
                else:
                    ok = False
                    reason = "refresh timed out" if result.timed_out else f"refresh rc={result.returncode}"

        if ok:
            _passed(rows, proofs, row_id, browser=browser, operation=op, run_id=run_id)
        else:
            _blocked(rows, row_id, browser=browser, operation=op, reason=reason)


def _probe_interactive_browser(
    *,
    runner: Runner,
    repo_root: Path,
    output_dir: Path,
    env: Mapping[str, str],
    timeout: int,
    rows: list[dict[str, Any]],
    proofs: list[dict[str, Any]],
    run_id: str,
    os_slug: str,
    browser: str,
    operations: Sequence[str],
    attach_devtools: bool,
    debugging_port: int,
    fresh: bool,
) -> None:
    storage = _storage_root(output_dir, f"interactive-{browser}-base")
    login_args = ["login", "--browser", browser, "--json"]
    if attach_devtools:
        login_args.append("--attach-devtools")
        login_args.extend(["--debugging-port", str(debugging_port)])
    if fresh:
        login_args.append("--fresh")

    login = runner(_cli(storage, login_args), env, repo_root, timeout)
    login_ok = login.returncode == 0 and not login.timed_out
    check_ok, check_reason = (
        _auth_check(runner, repo_root=repo_root, env=env, timeout=timeout, storage=storage)
        if login_ok
        else (False, "login timed out" if login.timed_out else f"login rc={login.returncode}")
    )

    for op in operations:
        row_id = _row_id("interactive", browser, os_slug, op)
        if not check_ok:
            _blocked(rows, row_id, browser=browser, operation=op, reason=check_reason)
            continue
        if op in {"login", "status"}:
            _passed(rows, proofs, row_id, browser=browser, operation=op, run_id=run_id)
        elif op == "refresh":
            result = runner(
                _cli(storage, ["auth", "refresh", "--json"]),
                env,
                repo_root,
                timeout,
            )
            ok = result.returncode == 0 and not result.timed_out
            if ok:
                ok, reason = _auth_check(
                    runner,
                    repo_root=repo_root,
                    env=env,
                    timeout=timeout,
                    storage=storage,
                )
            else:
                reason = "refresh timed out" if result.timed_out else f"refresh rc={result.returncode}"
            if ok:
                _passed(rows, proofs, row_id, browser=browser, operation=op, run_id=run_id)
            else:
                _blocked(rows, row_id, browser=browser, operation=op, reason=reason)
        elif op == "doctor":
            doctor_env = dict(env)
            doctor_env["NOTEBOOKLM_HOME"] = str(storage)
            result = runner(_cli(None, ["doctor", "--json"]), doctor_env, repo_root, timeout)
            if result.returncode == 0 and not result.timed_out:
                _passed(rows, proofs, row_id, browser=browser, operation=op, run_id=run_id)
            else:
                reason = "doctor timed out" if result.timed_out else f"doctor rc={result.returncode}"
                _blocked(rows, row_id, browser=browser, operation=op, reason=reason)
        elif op == "logout":
            logout_storage = _storage_path(output_dir, f"interactive-{browser}-logout")
            source_storage = _default_profile_storage_path(storage)
            logout_storage.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_storage, logout_storage)
            result = runner(_cli(logout_storage, ["auth", "logout"]), env, repo_root, timeout)
            if result.returncode == 0 and not result.timed_out and not logout_storage.exists():
                _passed(rows, proofs, row_id, browser=browser, operation=op, run_id=run_id)
            else:
                reason = "logout timed out" if result.timed_out else f"logout rc={result.returncode}"
                _blocked(rows, row_id, browser=browser, operation=op, reason=reason)


def _build_evidence_report(repo_root: Path, proofs_path: Path, output_path: Path) -> str:
    builder_path = repo_root / "scripts" / "auth_row_evidence_report_builder.py"
    spec = importlib.util.spec_from_file_location("_auth_row_evidence_report_builder", builder_path)
    if spec is None or spec.loader is None:
        return "builder_load_failed"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.build_report(proofs_path, output=output_path, repo_root=repo_root)
    return "written" if output_path.is_file() else "missing"


def build_report(
    argv: Sequence[str] | None = None,
    *,
    runner: Runner = _default_runner,
    repo_root: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-os", choices=["auto", *OS_SLUGS], default="auto")
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--cookie-browsers", default=",".join(COOKIE_BROWSERS))
    parser.add_argument("--cookie-ops", default=",".join(COOKIE_OPS))
    parser.add_argument("--interactive-browsers", default="")
    parser.add_argument("--interactive-ops", default=",".join(INTERACTIVE_OPS))
    parser.add_argument("--include-interactive", action="store_true")
    parser.add_argument("--browser-profile", action="append", default=[])
    parser.add_argument("--account", default=os.environ.get("ZERO_NOTEBOOKLM_AUTH_ACCOUNT"))
    parser.add_argument(
        "--authuser",
        type=int,
        default=_env_int("ZERO_NOTEBOOKLM_AUTHUSER", 0),
        help="account selector for account_select rows (default: 0)",
    )
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--allow-keepalive-poke", action="store_true")
    parser.add_argument("--attach-devtools", action="store_true")
    parser.add_argument("--debugging-port", type=int, default=9222)
    parser.add_argument("--fresh-interactive", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    root = Path(repo_root or args.repo_root).resolve()
    target_os = _detect_os_slug() if args.target_os == "auto" else args.target_os
    os_name = OS_SLUGS[target_os]
    stamp = _iso(now or _utc_now()).replace(":", "").replace("-", "")
    run_id = f"{target_os}-{stamp}"
    output_dir = (
        Path(args.output_dir)
        if args.output_dir
        else root / ".ai-bridge" / f"live-auth-row-probe-{run_id}"
    )
    if not output_dir.is_absolute():
        output_dir = root / output_dir
    proofs_path = output_dir / "auth_row_proofs.redacted.json"
    evidence_path = output_dir / "auth_row_evidence_report.redacted.json"
    summary_path = output_dir / "summary.redacted.json"
    evidence_path.unlink(missing_ok=True)

    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if not args.allow_keepalive_poke:
        env["NOTEBOOKLM_DISABLE_KEEPALIVE_POKE"] = "1"

    cookie_browsers = _split_csv(args.cookie_browsers)
    cookie_ops = _split_csv(args.cookie_ops, COOKIE_OPS)
    interactive_browsers = _split_csv(args.interactive_browsers)
    if args.include_interactive and not interactive_browsers:
        interactive_browsers = INTERACTIVE_BROWSERS
    interactive_ops = _split_csv(args.interactive_ops, INTERACTIVE_OPS)
    profiles = _profile_map(args.browser_profile)

    rows: list[dict[str, Any]] = []
    proofs: list[dict[str, Any]] = []
    scratch_dir = Path(tempfile.mkdtemp(prefix="zero-notebooklm-auth-"))
    try:
        os.chmod(scratch_dir, 0o700)
        for browser in cookie_browsers:
            _probe_cookie_browser(
                runner=runner,
                repo_root=root,
                output_dir=scratch_dir,
                env=env,
                timeout=args.timeout,
                rows=rows,
                proofs=proofs,
                run_id=run_id,
                os_slug=target_os,
                os_name=os_name,
                browser=browser,
                operations=cookie_ops,
                profiles=profiles,
                account=args.account,
                authuser=args.authuser,
            )

        for browser in interactive_browsers:
            _probe_interactive_browser(
                runner=runner,
                repo_root=root,
                output_dir=scratch_dir,
                env=env,
                timeout=args.timeout,
                rows=rows,
                proofs=proofs,
                run_id=run_id,
                os_slug=target_os,
                browser=browser,
                operations=interactive_ops,
                attach_devtools=args.attach_devtools,
                debugging_port=args.debugging_port,
                fresh=args.fresh_interactive,
            )
    finally:
        shutil.rmtree(scratch_dir)

    generated_at = now or _utc_now()
    proof_payload = {
        "schema_version": PROOF_SCHEMA_VERSION,
        "target": TARGET,
        "generated_at": _iso(generated_at),
        "expires_at": _iso(generated_at + timedelta(days=30)),
        "rows": sorted(proofs, key=lambda item: item["row_id"]),
    }
    _write_json(proofs_path, proof_payload)

    builder_status = "skipped_no_pass_rows"
    if proofs:
        try:
            builder_status = _build_evidence_report(root, proofs_path, evidence_path)
        except Exception as exc:  # pragma: no cover - defensive report path
            builder_status = f"failed:{type(exc).__name__}"

    passed = sum(1 for row in rows if row["status"] == "pass")
    blocked = sum(1 for row in rows if row["status"] == "blocked")
    summary = {
        "schema_version": SCHEMA_VERSION,
        "target": TARGET,
        "generated_at": _iso(generated_at),
        "target_os": target_os,
        "os_name": os_name,
        "selected_rows": len(rows),
        "passed_rows": passed,
        "blocked_rows": blocked,
        "proof_rows": len(proofs),
        "keepalive_poke": "enabled" if args.allow_keepalive_poke else "disabled",
        "proofs_path": _display_path(proofs_path, root=root, output_dir=output_dir),
        "evidence_report_path": (
            _display_path(evidence_path, root=root, output_dir=output_dir)
            if builder_status == "written"
            else ""
        ),
        "summary_path": _display_path(summary_path, root=root, output_dir=output_dir),
        "evidence_report_builder": builder_status,
        "rows": rows,
    }
    _write_json(summary_path, summary)
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    summary = build_report(argv)
    print(json.dumps(summary, indent=2, sort_keys=True))
    if summary["selected_rows"] == 0:
        return STRICT_BLOCKED_EXIT
    if "--strict" in (argv or sys.argv[1:]) and (
        summary["blocked_rows"] or summary["evidence_report_builder"] != "written"
    ):
        return STRICT_BLOCKED_EXIT
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
