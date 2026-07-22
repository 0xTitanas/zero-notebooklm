"""Import-origin denylist audit for NotebookLM Bare project code (JMC-NLB-002).

Statically scans project ``.py`` files for ``import``/``from ... import``
statements whose top-level module is on the third-party runtime denylist
(``httpx``, ``click``, ``rich``, ``rookiepy``, ``playwright``, ``markdownify``,
…). The bare runtime must resolve to Python stdlib or project-local modules only.

Phase 0 note: the only project code is the harness itself. ``introspect_upstream.py``
imports the upstream ``notebooklm`` oracle (allowed — it is the frozen target, not
a bare runtime dependency) and reaches Click through ``sys.modules`` rather than an
``import click`` statement, so it passes this audit with no carve-out. As bare
runtime modules land in later phases they fall under the same scan.

This module performs static analysis only (the stdlib ``ast`` module); it never
imports the scanned files, so it is safe to run on any interpreter without the
upstream venv.
"""

from __future__ import annotations

import argparse
import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _phase0_constants as C  # noqa: E402


def _iter_python_files(roots):
    for root in roots:
        root_path = C.REPO_ROOT / root
        if not root_path.exists():
            continue
        if root_path.is_file():
            if root_path.name.endswith(".py"):
                yield str(root_path)
            continue
        for dirpath, dirnames, filenames in os.walk(root_path):
            # Skip caches / virtualenvs / build dirs defensively.
            dirnames[:] = [
                d
                for d in dirnames
                if d
                not in {
                    "__pycache__",
                    ".venv",
                    "venv",
                    ".pytest_cache",
                    "build",
                    "dist",
                }
                and not d.endswith(".egg-info")
            ]
            for fn in sorted(filenames):
                if fn.endswith(".py"):
                    yield os.path.join(dirpath, fn)


def _top_level(module_name: str) -> str:
    return (module_name or "").split(".", 1)[0]


def scan_file(path: str, denylist) -> list[dict]:
    try:
        source = open(path, "r", encoding="utf-8").read()
    except OSError as exc:  # pragma: no cover - defensive
        return [{"file": path, "line": 0, "module": None, "error": str(exc)}]
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        return [
            {
                "file": path,
                "line": exc.lineno or 0,
                "module": None,
                "error": f"syntax: {exc}",
            }
        ]

    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = _top_level(alias.name)
                if top in denylist:
                    violations.append(
                        {
                            "file": path,
                            "line": node.lineno,
                            "module": alias.name,
                            "statement": "import",
                        }
                    )
        elif isinstance(node, ast.ImportFrom):
            # level>0 means a relative import (always project-local) -> never denylisted.
            if node.level and node.level > 0:
                continue
            top = _top_level(node.module or "")
            if top in denylist:
                violations.append(
                    {
                        "file": path,
                        "line": node.lineno,
                        "module": node.module,
                        "statement": "from-import",
                    }
                )
    return violations


def audit(roots=None, denylist=None) -> list[dict]:
    roots = roots or C.AUDIT_ROOTS
    denylist = set(denylist or C.DENYLISTED_RUNTIME_IMPORTS)
    found = []
    for path in _iter_python_files(roots):
        found.extend(scan_file(path, denylist))
    return found


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Third-party runtime import denylist audit"
    )
    parser.add_argument(
        "--root",
        action="append",
        dest="roots",
        help="Repo-relative directory or .py file to scan (repeatable). Default: scripts, tests, notebooklm, notebooklm_bare.py.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args(argv)

    violations = audit(roots=args.roots)
    if args.json:
        import json

        print(json.dumps({"violations": violations}, indent=2, sort_keys=True))
    else:
        if not violations:
            scanned = list(_iter_python_files(args.roots or C.AUDIT_ROOTS))
            print(
                f"PASS: no denylisted third-party imports in {len(scanned)} project file(s)."
            )
        else:
            print(f"FAIL: {len(violations)} denylisted import(s) found:")
            for v in violations:
                rel = os.path.relpath(v["file"], C.REPO_ROOT)
                detail = v.get("module") or v.get("error")
                print(f"  {rel}:{v['line']}: {detail}")
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
