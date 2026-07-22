#!/usr/bin/env python3
"""Stdlib-only live-readonly differential gate for NotebookLM Bare.

Default (no args/env): skips with strict_exit_code=77. Live differential
requires NOTEBOOKLM_BARE_LIVE_DIFFERENTIAL=1 AND --allow-live AND explicit
--storage-state, --notebook-id, --upstream-command, --bare-command.

Intent without complete args → status=error, strict_exit_code=64.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "live_readonly_differential/1"
TARGET = "notebooklm-py==0.7.2"
ALLOW_ENV = "NOTEBOOKLM_BARE_LIVE_DIFFERENTIAL"

# Probe request operation allowlist.
# None of these contain: create, delete, update, mutate, mutation, generate,
# upload, import, refresh, chat, ask, share_add, share_remove, share_update,
# public.
READONLY_OPERATIONS = [
    "list_notebooks",
    "get_notebook",
    "list_sources",
    "get_source",
    "list_notes",
    "get_note",
    "list_artifacts",
    "get_artifact",
    "get_status",
    "check_auth",
    "inspect_auth",
]

_DENYWORDS = frozenset(
    {
        "create",
        "delete",
        "update",
        "mutate",
        "mutation",
        "generate",
        "upload",
        "import",
        "refresh",
        "chat",
        "ask",
        "share_add",
        "share_remove",
        "share_update",
        "public",
    }
)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--allow-live", action="store_true")
    p.add_argument("--storage-state", metavar="PATH")
    p.add_argument("--notebook-id", metavar="ID")
    p.add_argument("--upstream-command", metavar="CMD")
    p.add_argument("--bare-command", metavar="CMD")
    p.add_argument("--network-auth", action="store_true")
    p.add_argument("--probe-timeout", type=int, default=30, metavar="SECS")
    p.add_argument("--json", action="store_true", dest="json_out")
    p.add_argument("--strict", action="store_true")
    return p


def _shape(value: Any) -> Any:
    """Shape-redact strings, dictionary keys, and scalar leaves."""
    if isinstance(value, str):
        return {"type": "str", "length": len(value), "empty": len(value) == 0}
    if isinstance(value, bool):
        return {"type": "bool"}
    if value is None:
        return {"type": "null"}
    if isinstance(value, int):
        return {"type": "int"}
    if isinstance(value, float):
        return {"type": "float"}
    if isinstance(value, dict):
        entries = []
        for key in sorted(value, key=lambda item: (type(item).__name__, str(item))):
            entries.append(
                {
                    "key": _shape(str(key)),
                    "value": _shape(value[key]),
                }
            )
        return {"type": "dict", "size": len(value), "entries": entries}
    if isinstance(value, list):
        return [_shape(item) for item in value]
    return value


def _category_states_from_matrix() -> dict[str, str]:
    states = {"cli": "open", "api": "open", "auth": "open", "rpc": "open"}
    matrix_path = Path(__file__).resolve().parents[1] / "compat" / "parity_matrix.md"
    try:
        for line in matrix_path.read_text(encoding="utf-8").splitlines():
            if not line.startswith("|") or "| ---" in line:
                continue
            cells = [c.strip().strip("`") for c in line.strip().strip("|").split("|")]
            if len(cells) == 4 and cells[0] in states:
                states[cells[0]] = cells[3]
    except OSError:
        pass
    return states


def _promotion_fields() -> dict[str, Any]:
    return {
        "category_promotion": {
            "cli": False,
            "api": False,
            "auth": False,
            "rpc": False,
        },
        "category_states": _category_states_from_matrix(),
    }


def _skip_payload() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "target": TARGET,
        "status": "skipped",
        "strict_exit_code": 77,
        "live_enabled": False,
        "read_only": True,
        "mutation_allowed": False,
        "blockers": ["live_differential_not_authorized"],
        "read_only_operations": list(READONLY_OPERATIONS),
        "storage_state": None,
        "notebook_id": None,
        **_promotion_fields(),
    }


def _error_payload(blockers: list[str]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "target": TARGET,
        "status": "error",
        "strict_exit_code": 64,
        "live_enabled": True,
        "read_only": True,
        "mutation_allowed": False,
        "blockers": blockers,
        "read_only_operations": list(READONLY_OPERATIONS),
        **_promotion_fields(),
    }


def _storage_bytes_preserved(path: Path, expected: bytes) -> bool:
    try:
        return path.read_bytes() == expected
    except OSError:
        return False


def _load_smoke_module() -> Any:
    smoke_path = Path(__file__).resolve().parent / "live_readonly_smoke.py"
    spec = importlib.util.spec_from_file_location("_live_smoke_inner", smoke_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load smoke module: {smoke_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _command_requests_auth_check_test(command: str) -> bool:
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    if "--test" not in parts:
        return False
    return any(
        part == "auth" and parts[index + 1 : index + 2] == ["check"]
        for index, part in enumerate(parts)
    )


def _run_probe(command: str, request_json: str, timeout: int) -> tuple[bool, Any, str]:
    """Run one probe subprocess. Returns (ok, observations_or_None, error_hint)."""
    try:
        proc = subprocess.run(
            shlex.split(command),
            input=request_json,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, None, "timeout"
    except Exception as exc:
        return False, None, f"spawn-error:{type(exc).__name__}"

    if proc.returncode != 0:
        return False, None, f"nonzero-rc:{proc.returncode}"

    stdout = proc.stdout.strip()
    if not stdout:
        return False, None, "empty-stdout"

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return False, None, "json-parse-error"

    if not isinstance(data, dict):
        return False, None, "non-object-json"

    obs = data.get("observations")
    if obs is None:
        return False, None, "missing-observations"

    return True, obs, ""


def build_report(
    argv: list[str] | None = None,
    *,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build the differential report. Skip path never calls Path.home()."""
    parser = _parser()
    args = parser.parse_args(list(argv or []))
    live_env: dict[str, str] | os.Mapping[str, str]
    live_env = env if env is not None else os.environ

    env_gate = live_env.get(ALLOW_ENV) == "1"
    allow_gate = bool(args.allow_live)
    live_intent = env_gate or allow_gate

    if not live_intent:
        return _skip_payload()

    # Live intent — both authorization gates and all explicit args are required.
    blockers: list[str] = []
    if not env_gate:
        blockers.append(f"{ALLOW_ENV}=1")
    if not allow_gate:
        blockers.append("--allow-live")
    if not args.storage_state:
        blockers.append("--storage-state")
    if not args.notebook_id:
        blockers.append("--notebook-id")
    if not args.upstream_command:
        blockers.append("--upstream-command")
    if not args.bare_command:
        blockers.append("--bare-command")

    if blockers:
        return _error_payload(blockers)

    storage_path = Path(args.storage_state)
    if not storage_path.is_file():
        return _error_payload(["--storage-state: file not found"])

    if args.network_auth and (
        _command_requests_auth_check_test(args.upstream_command)
        or _command_requests_auth_check_test(args.bare_command)
    ):
        return _error_payload(["duplicate_network_auth_probe"])

    storage_bytes_before = storage_path.read_bytes()
    read_only_operations = [
        op for op in READONLY_OPERATIONS if not (args.network_auth and op == "check_auth")
    ]

    # Run read-only smoke gate before probes
    smoke_mod = _load_smoke_module()
    smoke_argv = ["--allow-live", "--storage-state", str(storage_path)]
    if args.network_auth:
        smoke_argv.append("--network-auth")
    smoke_env = dict(live_env)
    smoke_env["NOTEBOOKLM_BARE_LIVE_SMOKE"] = "1"
    smoke_code, smoke_payload = smoke_mod.run(argv=smoke_argv, env=smoke_env)
    smoke_summary = {
        "exit_code": smoke_code,
        "status": smoke_payload.get("status"),
    }
    storage_preserved_after_smoke = _storage_bytes_preserved(
        storage_path, storage_bytes_before
    )
    smoke_passed = smoke_code == 0 and smoke_summary["status"] in {"pass", "passed"}
    pre_probe_blockers: list[str] = []
    if not smoke_passed:
        pre_probe_blockers.append("live_smoke_failed")
    if not storage_preserved_after_smoke:
        pre_probe_blockers.append("storage_state_modified")
    if pre_probe_blockers:
        return {
            "schema_version": SCHEMA_VERSION,
            "target": TARGET,
            "status": "fail",
            "strict_exit_code": 77,
            "live_enabled": True,
            "read_only": True,
            "mutation_allowed": False,
            **_promotion_fields(),
            "storage_state": "set",
            "notebook_id": "set",
            "read_only_operations": list(read_only_operations),
            "smoke": smoke_summary,
            "storage_preserved": storage_preserved_after_smoke,
            "blockers": pre_probe_blockers,
            "shape_match": False,
            "observations": {
                "upstream_shape": None,
                "bare_shape": None,
            },
        }

    network_auth = smoke_payload.get("checks", {}).get("network_auth", {})
    network_auth_proof = {
        "source": "live_readonly_smoke" if args.network_auth else None,
        "token_fetch_ok": bool(
            isinstance(network_auth, dict) and network_auth.get("token_fetch_ok")
        ),
    }

    # Build probe request; raw storage path and notebook_id go to probe via
    # stdin only and are never written to the returned report.
    request = {
        "schema_version": SCHEMA_VERSION,
        "storage_state": str(storage_path),
        "notebook_id": args.notebook_id,
        "readonly_operations": read_only_operations,
        "network_auth_proof": network_auth_proof,
    }
    request_json = json.dumps(request)

    timeout = args.probe_timeout
    upstream_ok, upstream_obs, upstream_err = _run_probe(
        args.upstream_command, request_json, timeout
    )
    bare_ok, bare_obs, bare_err = _run_probe(args.bare_command, request_json, timeout)

    storage_preserved = _storage_bytes_preserved(storage_path, storage_bytes_before)

    if upstream_ok and bare_ok:
        upstream_shape = _shape(upstream_obs)
        bare_shape = _shape(bare_obs)
        shape_match = upstream_shape == bare_shape
    else:
        upstream_shape = None
        bare_shape = None
        shape_match = False

    blockers = []
    if not upstream_ok:
        blockers.append("upstream_probe_failed")
    if not bare_ok:
        blockers.append("bare_probe_failed")
    if not shape_match:
        blockers.append("shape_mismatch")
    if not storage_preserved:
        blockers.append("storage_state_modified")

    status = "pass" if (shape_match and storage_preserved) else "fail"
    strict_exit_code = 0 if status == "pass" else 77

    return {
        "schema_version": SCHEMA_VERSION,
        "target": TARGET,
        "status": status,
        "strict_exit_code": strict_exit_code,
        "live_enabled": True,
        "read_only": True,
        "mutation_allowed": False,
        **_promotion_fields(),
        # Raw storage path and notebook ID are never emitted in the report
        "storage_state": "set",
        "notebook_id": "set",
        "read_only_operations": list(read_only_operations),
        "smoke": {
            "exit_code": smoke_code,
            "status": smoke_payload.get("status"),
        },
        "storage_preserved": storage_preserved,
        "blockers": blockers,
        "upstream_probe": {"ok": upstream_ok, "error": upstream_err},
        "bare_probe": {"ok": bare_ok, "error": bare_err},
        "shape_match": shape_match,
        "observations": {
            "upstream_shape": upstream_shape,
            "bare_shape": bare_shape,
        },
    }


def _human_text(report: dict[str, Any]) -> str:
    status = report["status"]
    promo = report.get("category_promotion", {})
    promo_str = "yes" if any(promo.values()) else "no"
    lines = [
        f"ZeroNotebookLM live-readonly differential: {status}",
        (
            f"live_enabled: {str(report['live_enabled']).lower()}"
            f"  read_only: {str(report['read_only']).lower()}"
            f"  mutation_allowed: {str(report['mutation_allowed']).lower()}"
        ),
        f"shape_match: {report.get('shape_match', 'n/a')}",
        f"category promotion: {promo_str}",
        "no category promoted by this live-readonly gate; consult parity_matrix.md for current ledger states",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    actual_argv = list(sys.argv[1:] if argv is None else argv)

    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--json", action="store_true", dest="json_out")
    pre.add_argument("--strict", action="store_true")
    pre_args, _ = pre.parse_known_args(actual_argv)

    report = build_report(actual_argv)

    if pre_args.json_out:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(_human_text(report))

    return report["strict_exit_code"] if pre_args.strict else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
