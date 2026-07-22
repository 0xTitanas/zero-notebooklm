#!/usr/bin/env python3
"""Single-file help surface for NotebookLM Bare Phase 1A.

This file is deliberately self-contained so ``python -I -S notebooklm_bare.py
--help`` works without site-packages or package import side effects. It is a help
surface only, not the future single-file runtime implementation.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

# Allow `import notebooklm_bare.rpc` while preserving this file as an executable
# single-file CLI help shim (`python notebooklm_bare.py --help`).
__path__ = [str(Path(__file__).with_suffix(""))]

VERSION = "0.7.2"
ROOT_COMMANDS = (
    "agent",
    "artifact",
    "ask",
    "auth",
    "clear",
    "completion",
    "configure",
    "create",
    "delete",
    "doctor",
    "download",
    "generate",
    "history",
    "language",
    "list",
    "login",
    "metadata",
    "note",
    "profile",
    "rename",
    "research",
    "share",
    "skill",
    "source",
    "status",
    "summary",
    "use",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="notebooklm-bare",
        description=(
            "NotebookLM Bare Phase 1 single-file help scaffold. Help/global "
            "options are available; live NotebookLM behavior is not implemented."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    parser.add_argument(
        "--storage", default=None, help="profile storage directory (scaffold only)"
    )
    parser.add_argument(
        "-p", "--profile", default=None, help="profile name (scaffold only)"
    )
    parser.add_argument(
        "-v", "--verbose", action="count", default=0, help="increase verbosity"
    )
    parser.add_argument(
        "--quiet", action="store_true", help="suppress non-essential output"
    )
    parser.add_argument(
        "command", nargs="?", choices=ROOT_COMMANDS, help="future command stub"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    ns = parser.parse_args(argv)
    if ns.command is None:
        parser.print_help()
        return 0
    print(f"command '{ns.command}' is not implemented in Phase 1", file=sys.stderr)
    return 78


if __name__ == "__main__":
    raise SystemExit(main())
