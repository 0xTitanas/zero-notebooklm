"""Public module ``__all__`` parity for selected small export surfaces."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

REFERENCE_ROOT = Path("notebooklm-py-reference/src/notebooklm")


def _upstream_all(relative: str) -> list[str]:
    tree = ast.parse((REFERENCE_ROOT / relative).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    return ast.literal_eval(node.value)
    raise AssertionError(f"{relative} has no __all__")


def test_client_auth_config_all_match_upstream_order():
    modules = {
        "notebooklm.auth": "auth.py",
        "notebooklm.client": "client.py",
        "notebooklm.config": "config.py",
    }

    for module_name, relative in modules.items():
        assert importlib.import_module(module_name).__all__ == _upstream_all(relative)
