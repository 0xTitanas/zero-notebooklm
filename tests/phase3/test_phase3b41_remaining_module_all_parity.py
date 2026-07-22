"""Remaining public module ``__all__`` parity."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

REFERENCE_ROOT = Path("notebooklm-py-reference/src/notebooklm")


def _upstream_all(relative: str) -> list[str] | None:
    tree = ast.parse((REFERENCE_ROOT / relative).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    return ast.literal_eval(node.value)
    return None


def test_remaining_module_all_matches_upstream():
    modules = {
        "notebooklm.auth": "auth.py",
        "notebooklm.config": "config.py",
        "notebooklm.exceptions": "exceptions.py",
    }

    for module_name, relative in modules.items():
        assert importlib.import_module(module_name).__all__ == _upstream_all(relative)


def test_rpc_overrides_has_no_dunder_all_like_upstream():
    import notebooklm.rpc.overrides as overrides

    assert _upstream_all("rpc/overrides.py") is None
    assert not hasattr(overrides, "__all__")
