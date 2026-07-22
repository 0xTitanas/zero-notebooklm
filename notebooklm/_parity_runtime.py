"""Offline direct-comparison runtime for frozen notebooklm-py==0.7.2 artifacts.

This module backs the test-only differential harness. It reports live bare package
exports where available and reads only committed compat fixtures for frozen oracle
artifacts. It performs no network, auth, browser, home-directory, or real
NotebookLM access.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from . import __all__ as _root_all
from .rpc.decoder import decode_batchexecute_response

_PARITY_CATEGORIES = frozenset({"cli", "api", "auth", "rpc", "offline", "self-test"})


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _compat_json(name: str) -> Any:
    return json.loads((_repo_root() / "compat" / name).read_text(encoding="utf-8"))


def cli_leaf_commands() -> list[str]:
    """Return the frozen CLI leaf-command list for direct comparison."""

    return list(_compat_json("cli_surface.json")["leaf_commands"])


def public_names() -> list[str]:
    """Return live root package exports for comparison with the API golden."""

    return list(_root_all)


def auth_matrix() -> dict[str, Any]:
    """Return the committed frozen auth matrix artifact."""

    return _compat_json("auth_matrix.json")


def supports_category(category: str) -> bool:
    """Whether this runtime exposes the offline comparison category."""

    return category in _PARITY_CATEGORIES


rpc = SimpleNamespace(decode_response=decode_batchexecute_response)


__all__ = [
    "auth_matrix",
    "cli_leaf_commands",
    "public_names",
    "rpc",
    "supports_category",
]
