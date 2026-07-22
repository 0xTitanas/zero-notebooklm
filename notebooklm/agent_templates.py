"""Shared static agent instruction loading helpers.

Mirrors notebooklm-py==0.7.2 ``cli.agent_templates`` for the Zero stdlib
CLI: `codex` maps to packaged ``CODEX.md`` and `claude` maps to packaged
``SKILL.md``. Source-checkout root fallbacks are intentionally omitted here
because zero-notebooklm vendors deterministic package data directly.
"""

from __future__ import annotations

from importlib import resources

AGENT_TEMPLATE_FILES = {
    "claude": "SKILL.md",
    "codex": "CODEX.md",
}


def get_agent_source_content(target: str) -> str | None:
    """Return bundled instructions for a supported agent target."""

    filename = AGENT_TEMPLATE_FILES.get(target.lower())
    if filename is None:
        return None
    try:
        return (resources.files("notebooklm") / "data" / filename).read_text(
            encoding="utf-8"
        )
    except (FileNotFoundError, TypeError, ModuleNotFoundError):
        return None


__all__ = ["AGENT_TEMPLATE_FILES", "get_agent_source_content"]
