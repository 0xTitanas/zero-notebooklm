"""Package-contained offline self-test over committed synthetic fixtures.

This module is intentionally local-only. It validates the packaged fixture bundle
and the fake RPC/parser seams without reading NotebookLM live state, browser
stores, OS credential backends, or ambient profile/home data.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from . import _parity_runtime
from . import _artifacts_impl as _artifacts
from . import chat as _chat
from . import fake_rpc as _fake_rpc
from . import notebooks as _notebooks
from . import notes as _notes
from . import sources as _sources

SYNTHETIC_NOTEBOOK_ID = "fake-notebook-0001"
SYNTHETIC_QUESTION = "Phase 0 synthetic question."
_REQUIRED_FIXTURES = (
    "README.md",
    "wire_shape.json",
    "list_notebooks.request.txt",
    "list_notebooks.response.txt",
    "list_sources.request.txt",
    "list_sources.response.txt",
    "list_notes.request.txt",
    "list_notes.response.txt",
    "list_artifacts.request.txt",
    "list_artifacts.response.txt",
    "chat_ask.request.txt",
    "chat_ask.streaming.response.txt",
)
_REQUIRED_CATEGORIES = ("cli", "api", "auth", "rpc", "offline", "self-test")


def _fixture_dir() -> Path:
    return Path(__file__).resolve().parent / "data" / "rpc_fixtures"


def _home_touched() -> bool:
    for key in ("NOTEBOOKLM_HOME",):
        raw = os.environ.get(key)
        if raw and Path(raw).exists():
            return True
    return False


def _check(ok: bool, **extra: Any) -> dict[str, Any]:
    return {"ok": bool(ok), **extra}


def run_offline_self_test() -> dict[str, Any]:
    """Run the bundled fixture self-test and return a JSON-safe payload."""

    checks: dict[str, dict[str, Any]] = {}
    fixture_dir = _fixture_dir()
    fixture_names = sorted(
        path.name for path in fixture_dir.iterdir() if path.is_file()
    )
    missing = [name for name in _REQUIRED_FIXTURES if name not in fixture_names]
    checks["packaged_rpc_fixtures"] = _check(
        not missing,
        count=len(fixture_names),
        required=len(_REQUIRED_FIXTURES),
        missing=missing,
    )

    try:
        rpc = _fake_rpc.OfflineFixtureRpcClient.from_fixture_dir(fixture_dir)
        notebooks = _notebooks.parse_list_notebooks_payload(
            rpc.list_notebooks_payload()
        )
        notebook = notebooks[0]
        sources = _sources.parse_list_sources_payload(
            rpc.list_sources_payload(notebook.id)
        )
        notes = _notes.parse_list_notes_payload(rpc.list_notes_payload(notebook.id))
        artifacts = _artifacts.parse_list_artifacts_payload(
            rpc.list_artifacts_payload(notebook.id)
        )
        checks["fake_rpc_round_trip"] = _check(
            notebook.id == SYNTHETIC_NOTEBOOK_ID
            and bool(sources)
            and bool(notes)
            and bool(artifacts),
            notebooks=len(notebooks),
            sources=len(sources),
            notes=len(notes),
            artifacts=len(artifacts),
        )
        answer, references = _chat.parse_chat_ask_payload(
            rpc.chat_ask_payload(notebook.id, SYNTHETIC_QUESTION)
        )
        checks["chat_fixture"] = _check(
            bool(answer),
            references=len(references),
        )
        notebook_id = notebook.id
    except Exception as exc:  # pragma: no cover - defensive command boundary.
        checks["fake_rpc_round_trip"] = _check(False, error=type(exc).__name__)
        checks["chat_fixture"] = _check(False, error=type(exc).__name__)
        notebook_id = ""

    supported = [
        name for name in _REQUIRED_CATEGORIES if _parity_runtime.supports_category(name)
    ]
    checks["parity_runtime_categories"] = _check(
        tuple(supported) == _REQUIRED_CATEGORIES,
        supported=supported,
    )
    checks["question"] = _check(True, value=SYNTHETIC_QUESTION)

    passed = all(check.get("ok") is True for check in checks.values())
    return {
        "status": "passed" if passed else "failed",
        "package": "notebooklm",
        "fixture_source": "packaged",
        "live_enabled": False,
        "read_only": True,
        "home_touched": _home_touched(),
        "notebook_id": notebook_id,
        "checks": checks,
    }


def _render_human(payload: dict[str, Any]) -> str:
    lines = [f"ZeroNotebookLM offline self-test: {payload['status']}"]
    checks = payload.get("checks", {})
    if isinstance(checks, dict):
        for name, check in checks.items():
            state = "ok" if isinstance(check, dict) and check.get("ok") else "failed"
            lines.append(f"- {name}: {state}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m notebooklm.self_test",
        description="Run ZeroNotebookLM's offline packaged-fixture self-test.",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON payload")
    args = parser.parse_args(argv)

    payload = run_offline_self_test()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(_render_human(payload), end="")
    return 0 if payload["status"] == "passed" else 70


if __name__ == "__main__":  # pragma: no cover - exercised by subprocess tests.
    raise SystemExit(main())


__all__ = [
    "SYNTHETIC_NOTEBOOK_ID",
    "SYNTHETIC_QUESTION",
    "main",
    "run_offline_self_test",
]
