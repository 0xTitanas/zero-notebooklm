"""Phase 1A output helpers for JSON and plain human text."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any


def render_json(value: Any) -> str:
    """Render deterministic UTF-8 friendly JSON with a trailing newline."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _plain(value: Any, indent: int = 0) -> list[str]:
    pad = "  " * indent
    if isinstance(value, Mapping):
        lines: list[str] = []
        for key, item in value.items():
            if isinstance(item, (Mapping, list, tuple)):
                lines.append(f"{pad}{key}:")
                lines.extend(_plain(item, indent + 1))
            else:
                lines.append(f"{pad}{key}: {item}")
        return lines
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        lines = []
        for item in value:
            if isinstance(item, (Mapping, list, tuple)):
                lines.append(f"{pad}-")
                lines.extend(_plain(item, indent + 1))
            else:
                lines.append(f"{pad}- {item}")
        return lines
    return [f"{pad}{value}"]


def render_plain(value: Any) -> str:
    """Render a deterministic plain-text representation with a trailing newline."""

    return "\n".join(_plain(value)) + "\n"


def render(value: Any, *, json_mode: bool = False) -> str:
    """Render ``value`` in either JSON or plain mode."""

    return render_json(value) if json_mode else render_plain(value)


__all__ = ["render", "render_json", "render_plain"]
