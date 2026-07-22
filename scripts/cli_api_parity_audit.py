#!/usr/bin/env python3
"""Phase 7 CLI/API parity audit.

This repo-local audit checks the breadth of the bare CLI/API surface against the
pinned notebooklm-py==0.7.2 oracle without promoting parity rows and without
performing live NotebookLM, browser, keychain, or home-state access.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import inspect
import io
import json
import sys
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SCHEMA_VERSION = "cli_api_parity_audit/1"
TARGET = "notebooklm-py==0.7.2"
CATEGORIES = ("cli", "api")


def _compat_path(repo_root: Path, name: str) -> Path:
    return repo_root / "compat" / name


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _table_cells(line: str) -> list[str] | None:
    if not line.startswith("|") or "| ---" in line:
        return None
    return [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]


def _category_states(matrix_path: Path) -> dict[str, str]:
    states: dict[str, str] = {}
    for line in matrix_path.read_text(encoding="utf-8").splitlines():
        cells = _table_cells(line)
        if cells and len(cells) == 4 and cells[0] in CATEGORIES:
            states[cells[0]] = cells[3]
    return {category: states.get(category, "open") for category in CATEGORIES}


def _help_rc_for_leaf(cli_module: Any, leaf_command: str) -> tuple[int, str, str]:
    args = leaf_command.split()[1:] + ["--help"]
    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = cli_module.console(args)
        rc = 0 if result is None else int(result)
    except SystemExit as exc:
        rc = 0 if exc.code is None else int(exc.code)
    except BaseException as exc:  # pragma: no cover - defensive report path.
        return 1, stdout.getvalue(), repr(exc)
    return rc, stdout.getvalue(), stderr.getvalue()


def _audit_cli(cli_surface: dict[str, Any]) -> dict[str, Any]:
    cli_module = importlib.import_module("notebooklm.cli")
    leaf_commands = list(cli_surface["leaf_commands"])
    failures: list[dict[str, Any]] = []
    passed_commands: list[str] = []

    for command in leaf_commands:
        rc, stdout, stderr = _help_rc_for_leaf(cli_module, command)
        combined = stdout + stderr
        ok = (
            rc == 0
            and "usage:" in stdout.lower()
            and "outside the current offline fixture-backed parity surface"
            not in combined
        )
        if ok:
            passed_commands.append(command)
        else:
            failures.append(
                {
                    "command": command,
                    "returncode": rc,
                    "stderr_excerpt": stderr[:160],
                    "stdout_excerpt": stdout[:160],
                }
            )

    return {
        "oracle_nodes": int(cli_surface["counts"]["nodes"]),
        "oracle_groups": int(cli_surface["counts"]["groups"]),
        "oracle_leaf_commands": len(leaf_commands),
        "root_command_count": len(getattr(cli_module, "ROOT_COMMANDS")),
        "implemented_root_command_count": len(
            getattr(cli_module, "IMPLEMENTED_COMMANDS")
        ),
        "help_probe": {
            "total": len(leaf_commands),
            "passed": len(passed_commands),
            "failed": len(failures),
            "commands": leaf_commands,
            "failure_commands": [failure["command"] for failure in failures],
            "failures": failures,
        },
    }


def _split_signature_parameters(signature_text: str) -> list[str]:
    body = signature_text[signature_text.find("(") + 1 : signature_text.rfind(")")]
    params: list[str] = []
    current = ""
    depth = 0
    quote: str | None = None
    for char in body:
        if quote is not None:
            current += char
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
            current += char
        elif char in "([{<":
            depth += 1
            current += char
        elif char in ")]}>":
            depth -= 1
            current += char
        elif char == "," and depth == 0:
            params.append(current.strip())
            current = ""
        else:
            current += char
    if current.strip():
        params.append(current.strip())
    return params


def _normalize_default(value: str | None) -> str | None:
    if value is None:
        return None
    if value.startswith("<object object at 0x"):
        return "<object object>"
    return value


def _shape_from_text(signature_text: str) -> list[tuple[str, str, str | None]]:
    shape: list[tuple[str, str, str | None]] = []
    keyword_only = False
    for raw in _split_signature_parameters(signature_text):
        if raw == "*":
            keyword_only = True
            continue
        if raw.startswith("**"):
            name = raw[2:].split(":", 1)[0].split("=", 1)[0].strip()
            kind = "VAR_KEYWORD"
        elif raw.startswith("*"):
            name = raw[1:].split(":", 1)[0].split("=", 1)[0].strip()
            kind = "VAR_POSITIONAL"
        else:
            name = raw.split(":", 1)[0].split("=", 1)[0].strip()
            kind = "KEYWORD_ONLY" if keyword_only else "POSITIONAL_OR_KEYWORD"
        default = raw.split("=", 1)[1].strip() if "=" in raw else None
        shape.append((name, kind, _normalize_default(default)))
    return shape


def _shape_from_callable(func: Any) -> list[tuple[str, str, str | None]]:
    shape: list[tuple[str, str, str | None]] = []
    for name, param in inspect.signature(func).parameters.items():
        default = None if param.default is inspect._empty else repr(param.default)
        shape.append((name, param.kind.name, _normalize_default(default)))
    return shape


def _audit_subclients(signatures: dict[str, Any]) -> dict[str, Any]:
    notebooklm = importlib.import_module("notebooklm")
    client_module = importlib.import_module("notebooklm.client")
    reports: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, Any]] = []

    for subclient, expected in signatures["subclients"].items():
        cls = getattr(client_module, expected["class"], None) or getattr(
            notebooklm, expected["class"], None
        )
        if cls is None:
            failure = {
                "subclient": subclient,
                "reason": "missing_class",
                "expected_class": expected["class"],
            }
            failures.append(failure)
            reports[subclient] = failure
            continue

        actual_methods = sorted(
            name
            for name, value in inspect.getmembers(cls, inspect.iscoroutinefunction)
            if not name.startswith("_")
        )
        method_failures: list[dict[str, Any]] = []
        if actual_methods != expected["async_methods"]:
            method_failures.append(
                {
                    "reason": "async_methods",
                    "expected": expected["async_methods"],
                    "actual": actual_methods,
                }
            )
        golden_method_signatures = expected.get("method_signatures", {})
        for method_name in expected["async_methods"]:
            if not hasattr(cls, method_name):
                method_failures.append(
                    {"reason": "missing_method", "method": method_name}
                )
                continue
            actual_shape = _shape_from_callable(getattr(cls, method_name))
            golden_shape = _shape_from_text(golden_method_signatures[method_name])
            if actual_shape != golden_shape:
                method_failures.append(
                    {
                        "reason": "signature_shape",
                        "method": method_name,
                        "expected": golden_shape,
                        "actual": actual_shape,
                    }
                )
        report = {
            "class": expected["class"],
            "async_methods": actual_methods,
            "passed": not method_failures,
            "failures": method_failures,
        }
        reports[subclient] = report
        if method_failures:
            failures.append({"subclient": subclient, "failures": method_failures})

    return {
        "total": len(signatures["subclients"]),
        "passed": len(signatures["subclients"]) - len(failures),
        "failed": len(failures),
        "names": sorted(f"client.{name}" for name in signatures["subclients"]),
        "failure_names": [f"client.{failure['subclient']}" for failure in failures],
        "failures": failures,
        "details": reports,
    }


def _audit_api(
    api_surface: dict[str, Any], signatures: dict[str, Any]
) -> dict[str, Any]:
    notebooklm = importlib.import_module("notebooklm")
    expected_public = set(api_surface["root_all"])
    actual_public = set(getattr(notebooklm, "__all__"))
    missing = sorted(expected_public - actual_public)
    extra = sorted(actual_public - expected_public)
    return {
        "oracle_public_names": len(expected_public),
        "actual_public_names": len(actual_public),
        "missing_public_names": missing,
        "extra_public_names": extra,
        "subclients": _audit_subclients(signatures),
    }


def _overall_status(cli_report: dict[str, Any], api_report: dict[str, Any]) -> str:
    if cli_report["help_probe"]["failed"]:
        return "fail"
    if api_report["missing_public_names"] or api_report["extra_public_names"]:
        return "fail"
    if api_report["subclients"]["failed"]:
        return "fail"
    return "pass"


def build_report(*, repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    """Build the Phase 7 CLI/API audit report from committed oracle artifacts."""

    repo_root = Path(repo_root)
    category_states = _category_states(_compat_path(repo_root, "parity_matrix.md"))
    cli_report = _audit_cli(_load_json(_compat_path(repo_root, "cli_surface.json")))
    api_report = _audit_api(
        _load_json(_compat_path(repo_root, "python_api_surface.json")),
        _load_json(_compat_path(repo_root, "api_golden/signatures.json")),
    )
    status = _overall_status(cli_report, api_report)
    return {
        "schema_version": SCHEMA_VERSION,
        "target": TARGET,
        "overall_status": status,
        "strict_exit_code": 0 if status == "pass" else 1,
        "category_promotion": {
            "cli": category_states.get("cli") == "pass",
            "api": category_states.get("api") == "pass",
        },
        "category_states": category_states,
        "live_access": False,
        "mcp_implementation": False,
        "cli": cli_report,
        "api": api_report,
        "notes": [
            "This audit proves breadth of local CLI/API surface coverage; Phase 19 may mark CLI/API categories promoted only when row-specific direct offline evidence is present.",
            "No live NotebookLM, browser store, keychain, credential backend, or user home state is accessed.",
        ],
    }


def render_human(report: dict[str, Any]) -> str:
    cli = report["cli"]["help_probe"]
    api = report["api"]["subclients"]
    lines = [f"ZeroNotebookLM CLI/API audit: {report['overall_status']}"]
    lines.append(f"CLI leaf help: {cli['passed']}/{cli['total']}")
    lines.append(
        f"API public names: {report['api']['actual_public_names']}/{report['api']['oracle_public_names']}"
    )
    lines.append(f"API subclients: {api['passed']}/{api['total']}")
    lines.append(
        "category promotion: "
        + ", ".join(
            f"{cat}={str(report['category_promotion'].get(cat, False)).lower()}"
            for cat in ("cli", "api")
        )
    )
    return "\n".join(lines) + "\n"


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cli_api_parity_audit.py")
    parser.add_argument("--json", action="store_true", help="emit full JSON report")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero if the local CLI/API audit fails",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    report = build_report()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_human(report), end="")
    if args.strict:
        return int(report["strict_exit_code"])
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by subprocess tests.
    raise SystemExit(main())
