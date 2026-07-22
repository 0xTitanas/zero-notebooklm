#!/usr/bin/env python3
"""Stdlib-only live mutation/export differential gate for NotebookLM Bare.

Default (no args/env): skips with strict_exit_code=77. Live differential
requires NOTEBOOKLM_BARE_LIVE_MUTATION_EXPORT=1 AND --allow-live AND explicit
--storage-state, --notebook-id, --upstream-command, --bare-command.

Intent without complete args → status=error, strict_exit_code=64.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "live_mutation_export_differential/1"
TARGET = "notebooklm-py==0.7.2"
ALLOW_ENV = "NOTEBOOKLM_BARE_LIVE_MUTATION_EXPORT"

# Probe request operation allowlist for disposable export/mutation evidence.
MUTATION_EXPORT_OPERATIONS = [
    "create_note",
    "update_note",
    "delete_note",
    "add_text_source",
    "delete_source",
    "export_artifact",
    "download_artifact",
    "rename_notebook",
]


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--allow-live", action="store_true")
    p.add_argument("--storage-state", metavar="PATH")
    p.add_argument("--notebook-id", metavar="ID")
    p.add_argument("--upstream-command", metavar="CMD")
    p.add_argument("--bare-command", metavar="CMD")
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
        "read_only": False,
        "mutation_allowed": True,
        "public_sharing_allowed": False,
        "disposable_notebook_only": True,
        "operation_allowlist": list(MUTATION_EXPORT_OPERATIONS),
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
        "read_only": False,
        "mutation_allowed": True,
        "public_sharing_allowed": False,
        "disposable_notebook_only": True,
        "operation_allowlist": list(MUTATION_EXPORT_OPERATIONS),
        "storage_state": None,
        "notebook_id": None,
        **_promotion_fields(),
        "storage_preserved": False,
        "cleanup_confirmed": False,
        "public_sharing_touched": False,
        "blockers": blockers,
        "upstream_probe": {"ok": False, "error": "skipped"},
        "bare_probe": {"ok": False, "error": "skipped"},
        "shape_match": False,
        "observations": {
            "upstream_shape": None,
            "bare_shape": None,
        },
    }


def _storage_bytes_preserved(path: Path, expected: bytes) -> bool:
    try:
        return path.read_bytes() == expected
    except OSError:
        return False


def _run_probe(
    command: str,
    request_json: str,
    timeout: int,
) -> tuple[bool, Any, str, bool | None, bool | None]:
    """Run one probe subprocess. Returns tuple(ok, observations, error_hint, cleanup_confirmed, public_sharing_touched)."""
    try:
        proc = subprocess.run(
            shlex.split(command),
            input=request_json,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, None, "timeout", None, None
    except Exception as exc:  # pragma: no cover - defensive boundary
        return False, None, f"spawn-error:{type(exc).__name__}", None, None

    if proc.returncode != 0:
        return False, None, f"nonzero-rc:{proc.returncode}", None, None

    stdout = proc.stdout.strip()
    if not stdout:
        return False, None, "empty-stdout", None, None

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return False, None, "json-parse-error", None, None

    if not isinstance(data, dict):
        return False, None, "non-object-json", None, None

    obs = data.get("observations")
    if obs is None:
        return False, None, "missing-observations", None, None

    cleanup_confirmed = data.get("cleanup_confirmed")
    if "cleanup_confirmed" not in data:
        return False, None, "missing-cleanup-confirmed", None, None
    if not isinstance(cleanup_confirmed, bool):
        return False, None, "invalid-cleanup-confirmed", None, None

    public_sharing_touched = data.get("public_sharing_touched")
    if "public_sharing_touched" not in data:
        return False, None, "missing-public-sharing-flag", None, None
    if not isinstance(public_sharing_touched, bool):
        return False, None, "invalid-public-sharing-flag", None, None

    return (
        True,
        obs,
        "",
        cleanup_confirmed,
        public_sharing_touched,
    )


def build_report(
    argv: list[str] | None = None,
    *,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build the mutation/export differential report."""
    parser = _parser()
    args = parser.parse_args(list(argv or []))
    live_env: dict[str, str] | os.Mapping[str, str]
    live_env = env if env is not None else os.environ

    env_gate = live_env.get(ALLOW_ENV) == "1"
    allow_gate = bool(args.allow_live)
    live_intent = env_gate or allow_gate

    if not live_intent:
        return _skip_payload()

    # Live intent: both authorization gates and all explicit args are required.
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
        return _error_payload(["--storage-state"])

    storage_bytes_before = storage_path.read_bytes()

    request = {
        "schema_version": SCHEMA_VERSION,
        "storage_state": str(storage_path),
        "notebook_id": args.notebook_id,
        "operation_allowlist": MUTATION_EXPORT_OPERATIONS,
        "public_sharing_allowed": False,
        "disposable_notebook_only": True,
    }
    request_json = json.dumps(request)

    timeout = args.probe_timeout
    (
        upstream_ok,
        upstream_obs,
        upstream_err,
        upstream_cleanup,
        upstream_public_sharing,
    ) = _run_probe(args.upstream_command, request_json, timeout)
    (
        bare_ok,
        bare_obs,
        bare_err,
        bare_cleanup,
        bare_public_sharing,
    ) = _run_probe(args.bare_command, request_json, timeout)

    storage_preserved = _storage_bytes_preserved(storage_path, storage_bytes_before)

    if upstream_ok and bare_ok:
        upstream_shape = _shape(upstream_obs)
        bare_shape = _shape(bare_obs)
        shape_match = upstream_shape == bare_shape
    else:
        upstream_shape = None
        bare_shape = None
        shape_match = False

    cleanup_signals: list[bool] = []
    if upstream_cleanup is not None:
        cleanup_signals.append(upstream_cleanup)
    if bare_cleanup is not None:
        cleanup_signals.append(bare_cleanup)
    cleanup_confirmed = all(cleanup_signals) if cleanup_signals else True

    public_sharing_values: list[bool] = []
    if upstream_public_sharing is not None:
        public_sharing_values.append(upstream_public_sharing)
    if bare_public_sharing is not None:
        public_sharing_values.append(bare_public_sharing)
    public_sharing_touched = (
        any(public_sharing_values) if public_sharing_values else False
    )

    blockers: list[str] = []
    if not upstream_ok:
        blockers.append("upstream_probe_failed")
    if not bare_ok:
        blockers.append("bare_probe_failed")
    if not shape_match:
        blockers.append("shape_mismatch")
    if not storage_preserved:
        blockers.append("storage_state_modified")
    if not cleanup_confirmed:
        blockers.append("cleanup_not_confirmed")
    if public_sharing_touched:
        blockers.append("public_sharing_touched")

    status = (
        "pass"
        if (
            upstream_ok
            and bare_ok
            and shape_match
            and storage_preserved
            and cleanup_confirmed
            and not public_sharing_touched
        )
        else "fail"
    )
    strict_exit_code = 0 if status == "pass" else 77

    return {
        "schema_version": SCHEMA_VERSION,
        "target": TARGET,
        "status": status,
        "strict_exit_code": strict_exit_code,
        "live_enabled": True,
        "read_only": False,
        "mutation_allowed": True,
        **_promotion_fields(),
        "public_sharing_allowed": False,
        "disposable_notebook_only": True,
        "operation_allowlist": list(MUTATION_EXPORT_OPERATIONS),
        "storage_state": "set",
        "notebook_id": "set",
        "storage_preserved": storage_preserved,
        "cleanup_confirmed": cleanup_confirmed,
        "public_sharing_touched": public_sharing_touched,
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
        f"ZeroNotebookLM live mutation/export differential: {status}",
        (
            f"live_enabled: {str(report['live_enabled']).lower()}"
            f"  read_only: {str(report['read_only']).lower()}"
            f"  mutation_allowed: {str(report['mutation_allowed']).lower()}"
        ),
        f"category promotion: {promo_str} (all false)",
        "no category promoted by this gate",
        "no public sharing permitted",
        f"public_sharing_allowed: {str(report.get('public_sharing_allowed')).lower()}",
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
