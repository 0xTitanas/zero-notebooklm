#!/usr/bin/env python3
"""Phase 6 pre-live parity readiness report.

This script is intentionally repo-local. It reads committed compatibility artifacts,
executes the default-skip live-smoke gate, and reports whether ZeroNotebookLM is
ready for release/MCP/live parity promotion. It performs no live NotebookLM access,
no browser/keychain reads, no home discovery, and no matrix mutation.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from notebooklm import auth_readiness  # noqa: E402
import live_readonly_smoke  # noqa: E402

SCHEMA_VERSION = "parity_readiness/1"
STRICT_BLOCKED_EXIT = 77
CATEGORIES = ("cli", "api", "auth", "rpc", "offline", "self-test")
CLI_API_FOR_MCP = ("cli", "api")


def _compat_path(repo_root: Path, name: str) -> Path:
    return repo_root / "compat" / name


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _table_cells(line: str) -> list[str] | None:
    if not line.startswith("|") or "| ---" in line:
        return None
    cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
    return cells if cells else None


def _parse_parity_matrix(path: Path) -> dict[str, Any]:
    categories: dict[str, dict[str, Any]] = {}
    leaf_commands: dict[str, str] = {}
    api_subclients: dict[str, str] = {}

    for line in path.read_text(encoding="utf-8").splitlines():
        cells = _table_cells(line)
        if not cells:
            continue
        if len(cells) == 4 and cells[0] in CATEGORIES:
            categories[cells[0]] = {
                "scope": cells[1],
                "basis": cells[2],
                "state": cells[3],
            }
        elif len(cells) == 2 and cells[0].startswith("notebooklm "):
            leaf_commands[cells[0]] = cells[1]
        elif len(cells) == 3 and cells[0].startswith("client."):
            api_subclients[cells[0]] = cells[2]

    for category in CATEGORIES:
        categories.setdefault(
            category,
            {"scope": "missing", "basis": "missing", "state": "open"},
        )

    return {
        "categories": categories,
        "leaf_commands": leaf_commands,
        "api_subclients": api_subclients,
    }


def _count_states(states: Iterable[str]) -> dict[str, int]:
    counts = Counter(states)
    return {
        "pass": int(counts.get("pass", 0)),
        "open": int(counts.get("open", 0)),
        "blocked": int(counts.get("blocked", 0)),
    }


def _category_report(parsed: dict[str, Any]) -> dict[str, dict[str, Any]]:
    categories = dict(parsed["categories"])
    leaf_commands = parsed["leaf_commands"]
    api_subclients = parsed["api_subclients"]

    report: dict[str, dict[str, Any]] = {}
    for category, row in categories.items():
        item = dict(row)
        if category == "cli":
            item["open_leaf_commands"] = sum(
                1 for state in leaf_commands.values() if state == "open"
            )
            item["pass_leaf_commands"] = sum(
                1 for state in leaf_commands.values() if state == "pass"
            )
            item["total_leaf_commands"] = len(leaf_commands)
        elif category == "api":
            item["open_subclients"] = sum(
                1 for state in api_subclients.values() if state == "open"
            )
            item["pass_subclients"] = sum(
                1 for state in api_subclients.values() if state == "pass"
            )
            item["total_subclients"] = len(api_subclients)
        report[category] = item
    return report


def _blockers(categories: dict[str, dict[str, Any]]) -> list[str]:
    blockers: list[str] = []
    for category in ("cli", "api", "auth", "rpc"):
        if categories.get(category, {}).get("state") != "pass":
            blockers.append(f"{category}_category_open")
    if any(
        categories.get(category, {}).get("state") != "pass"
        for category in CLI_API_FOR_MCP
    ):
        blockers.append("mcp_deferred_until_cli_api_parity")
    blockers.append("live_smoke_not_authorized")
    return sorted(dict.fromkeys(blockers))


def _release_ready(categories: dict[str, dict[str, Any]]) -> bool:
    return all(categories[category]["state"] == "pass" for category in CATEGORIES)


def build_report(*, repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    """Return the offline Phase 6 readiness report.

    Pure relative to user state: reads committed repo artifacts only and invokes
    ``live_readonly_smoke.run([])`` in default-skip mode.
    """

    repo_root = Path(repo_root)
    parity = _parse_parity_matrix(_compat_path(repo_root, "parity_matrix.md"))
    categories = _category_report(parity)
    category_counts = _count_states(row["state"] for row in categories.values())
    open_categories = sorted(
        category for category, row in categories.items() if row["state"] == "open"
    )
    pass_categories = sorted(
        category for category, row in categories.items() if row["state"] == "pass"
    )
    auth_report = auth_readiness.build_report(
        _compat_path(repo_root, "auth_matrix.json")
    )
    auth_summary = auth_report["summary"]
    cli_surface = _load_json(_compat_path(repo_root, "cli_surface.json"))
    api_surface = _load_json(_compat_path(repo_root, "python_api_surface.json"))
    live_code, live_payload = live_readonly_smoke.run([], env={})
    ready = _release_ready(categories)
    blockers = _blockers(categories)

    return {
        "schema_version": SCHEMA_VERSION,
        "target": "notebooklm-py==0.7.2",
        "release_ready": ready,
        "strict_exit_code": 0 if ready else STRICT_BLOCKED_EXIT,
        "category_state_counts": category_counts,
        "categories": categories,
        "open_categories": open_categories,
        "pass_categories": pass_categories,
        "blockers": blockers,
        "live_authorization_required": True,
        "mcp_next_phase_allowed": all(
            categories[category]["state"] == "pass" for category in CLI_API_FOR_MCP
        ),
        "next_required_authorization": {
            "live_notebooklm_smoke": True,
            "browser_cookie_store_reads": False,
            "os_credential_backend_decrypt": False,
            "mutation_smoke": False,
        },
        "auth_readiness": {
            "schema_version": auth_report["schema_version"],
            "total_rows": auth_summary["total_rows"],
            "release_blocked": auth_summary["release_blocked"],
            "profile_exclusion_path_count": auth_summary[
                "profile_exclusion_path_count"
            ],
            "deferred_future_release_path_count": auth_summary[
                "deferred_future_release_path_count"
            ],
            "parity_pass_count": auth_summary["parity_pass_count"],
            "parity_open_count": auth_summary["parity_open_count"],
            "foundation_covered_count": auth_summary["foundation_covered_count"],
            "foundation_partial_count": auth_summary["foundation_partial_count"],
            "foundation_none_count": auth_summary["foundation_none_count"],
            "blockers": auth_summary["blockers"],
        },
        "oracle_counts": {
            "cli_nodes": cli_surface["counts"]["nodes"],
            "cli_leaf_commands": cli_surface["counts"]["leaves"],
            "api_public_names": api_surface["root_all_count"],
            "api_subclients": len(api_surface["subclients"]),
        },
        "live_smoke_default": {
            "exit_code": live_code,
            "status": live_payload.get("status"),
            "live_enabled": live_payload.get("live_enabled"),
            "read_only": live_payload.get("read_only"),
            "mutation_allowed": live_payload.get("mutation_allowed"),
            "network_auth": live_payload.get("network_auth"),
            "storage_state": live_payload.get("storage_state"),
        },
        "notes": [
            "This report is offline/repo-local and does not touch live NotebookLM, browser stores, keychains, or user home state.",
            "MCP prerequisites are satisfied once CLI and API parity categories are pass; implementation remains a separately gated phase.",
            "Live smoke remains gated behind explicit owner authorization and an explicit storage-state path.",
        ],
    }


def render_human(report: dict[str, Any]) -> str:
    status = "ready" if report["release_ready"] else "blocked"
    lines = [f"ZeroNotebookLM readiness: {status}"]
    lines.append("open categories: " + ", ".join(report["open_categories"]))
    lines.append("pass categories: " + ", ".join(report["pass_categories"]))
    lines.append(
        "MCP next phase: "
        + ("allowed" if report["mcp_next_phase_allowed"] else "blocked")
    )
    lines.append("blockers: " + ", ".join(report["blockers"]))
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="parity_readiness.py")
    parser.add_argument("--json", action="store_true", help="emit full JSON report")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero while release/MCP/live parity gates remain blocked",
    )
    args = parser.parse_args(argv)

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
