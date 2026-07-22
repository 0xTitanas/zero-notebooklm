#!/usr/bin/env python3
"""Phase 8 fixture-backed CLI behavior parity audit.

This audit intentionally does *not* promote the CLI parity row.  It proves that
zero-notebooklm has one safe executable behavior scenario for every pinned
notebooklm-py==0.7.2 CLI leaf command while preserving the live/auth/RPC parity
closure boundary for later phases.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

NOTEBOOK_ID = "fake-notebook-0001"
SOURCE_ID = "fake-source-0001"
SECOND_SOURCE_ID = "fake-source-0002"
NOTE_ID = "fake-note-0001"
AUDIO_ARTIFACT_ID = "fake-artifact-audio-0001"
REPORT_ARTIFACT_ID = "fake-artifact-report-0001"
QUIZ_ARTIFACT_ID = "fake-artifact-quiz-0001"
FAILED_ARTIFACT_ID = "fake-artifact-failed-0001"
RESEARCH_QUERY = "Synthetic NotebookLM parity research"
ASK_QUESTION = "Phase 0 synthetic question."


@dataclass(frozen=True)
class ScenarioContext:
    root: Path
    home: Path
    storage: Path
    cwd: Path


@dataclass(frozen=True)
class Scenario:
    leaf: str
    argv: tuple[str, ...] | Callable[[ScenarioContext], Sequence[str]]
    expected_exit_codes: frozenset[int] = frozenset({0})
    expect_json: bool = True
    kind: str = "fixture_success"
    markers: tuple[str, ...] = ()
    blocked_reason: str | None = None

    def build_argv(self, ctx: ScenarioContext) -> list[str]:
        raw = self.argv(ctx) if callable(self.argv) else self.argv
        return ["--storage", str(ctx.storage), *list(raw)]


def _scenario(leaf: str, *argv: str, **kwargs: object) -> Scenario:
    return Scenario(leaf=leaf, argv=tuple(argv), **kwargs)  # type: ignore[arg-type]


def _source_text_file(ctx: ScenarioContext) -> str:
    path = ctx.cwd / "synthetic-source.txt"
    path.write_text("Synthetic local source content for Phase 8.\n", encoding="utf-8")
    return str(path)


def _fulltext_output(ctx: ScenarioContext) -> str:
    return str(ctx.cwd / "fulltext.md")


def _missing_cookie_store(ctx: ScenarioContext) -> str:
    return str(ctx.cwd / "missing-cookies.sqlite")


def _download_output(ctx: ScenarioContext, suffix: str) -> str:
    return str(ctx.cwd / f"downloaded{suffix}")


def build_scenarios() -> tuple[Scenario, ...]:
    """Return exactly one safe behavior scenario per pinned CLI leaf."""

    scenarios: list[Scenario] = [
        _scenario(
            "notebooklm agent show",
            "agent",
            "show",
            "codex",
            expect_json=False,
            markers=("Repository Guidelines",),
        ),
        _scenario(
            "notebooklm artifact delete",
            "artifact",
            "delete",
            AUDIO_ARTIFACT_ID,
            "-n",
            NOTEBOOK_ID,
            "--yes",
            "--json",
        ),
        _scenario(
            "notebooklm artifact export",
            "artifact",
            "export",
            AUDIO_ARTIFACT_ID,
            "-n",
            NOTEBOOK_ID,
            "--title",
            "Phase 8 Export",
            "--json",
        ),
        _scenario(
            "notebooklm artifact get",
            "artifact",
            "get",
            AUDIO_ARTIFACT_ID,
            "-n",
            NOTEBOOK_ID,
            "--json",
        ),
        _scenario(
            "notebooklm artifact list",
            "artifact",
            "list",
            "-n",
            NOTEBOOK_ID,
            "--type",
            "all",
            "--limit",
            "2",
            "--json",
        ),
        _scenario(
            "notebooklm artifact poll",
            "artifact",
            "poll",
            AUDIO_ARTIFACT_ID,
            "-n",
            NOTEBOOK_ID,
            "--json",
        ),
        _scenario(
            "notebooklm artifact rename",
            "artifact",
            "rename",
            AUDIO_ARTIFACT_ID,
            "Renamed Audio",
            "-n",
            NOTEBOOK_ID,
            "--json",
        ),
        _scenario(
            "notebooklm artifact retry",
            "artifact",
            "retry",
            AUDIO_ARTIFACT_ID,
            "-n",
            NOTEBOOK_ID,
            "--json",
        ),
        _scenario(
            "notebooklm artifact suggestions",
            "artifact",
            "suggestions",
            "-n",
            NOTEBOOK_ID,
            "--json",
        ),
        _scenario(
            "notebooklm artifact wait",
            "artifact",
            "wait",
            AUDIO_ARTIFACT_ID,
            "-n",
            NOTEBOOK_ID,
            "--json",
        ),
        _scenario(
            "notebooklm ask",
            "ask",
            ASK_QUESTION,
            "-n",
            NOTEBOOK_ID,
            "--source",
            SOURCE_ID,
            "--new",
            "--yes",
            "--json",
        ),
        _scenario(
            "notebooklm auth check",
            "auth",
            "check",
            "--json",
            expected_exit_codes=frozenset({1}),
            kind="safe_fail_closed",
            blocked_reason="fresh temp profile has no auth file",
        ),
        _scenario(
            "notebooklm auth inspect",
            "auth",
            "inspect",
            "--json",
            expected_exit_codes=frozenset({1}),
            kind="safe_fail_closed",
            blocked_reason="zero-dependency install has no rookiepy browser-cookie reader",
        ),
        _scenario("notebooklm auth logout", "auth", "logout", "--json"),
        _scenario(
            "notebooklm auth refresh",
            "auth",
            "refresh",
            "--json",
            expected_exit_codes=frozenset({77}),
            expect_json=False,
            kind="safe_fail_closed",
            blocked_reason="temp profile has no refreshable stored auth cookies",
        ),
        _scenario("notebooklm clear", "clear", expect_json=False),
        _scenario(
            "notebooklm completion",
            "completion",
            "bash",
            expect_json=False,
            markers=("_NOTEBOOKLM_COMPLETE",),
        ),
        _scenario(
            "notebooklm configure",
            "configure",
            "-n",
            NOTEBOOK_ID,
            "--mode",
            "learning-guide",
            "--json",
        ),
        _scenario("notebooklm create", "create", "Phase 8 Notebook", "--use", "--json"),
        _scenario("notebooklm delete", "delete", "-n", NOTEBOOK_ID, "--yes", "--json"),
        _scenario("notebooklm doctor", "doctor", "--auth-matrix", "--json"),
        Scenario(
            "notebooklm download audio",
            lambda ctx: [
                "download",
                "audio",
                _download_output(ctx, ".mp3"),
                "-n",
                NOTEBOOK_ID,
                "--latest",
                "--dry-run",
                "--json",
            ],
        ),
        _scenario(
            "notebooklm download cinematic-video",
            "download",
            "cinematic-video",
            "-n",
            NOTEBOOK_ID,
            "--dry-run",
            "--json",
            expected_exit_codes=frozenset({64}),
            expect_json=False,
            kind="safe_fail_closed",
            blocked_reason="no completed cinematic video artifact in committed fixture",
        ),
        _scenario(
            "notebooklm download data-table",
            "download",
            "data-table",
            "-n",
            NOTEBOOK_ID,
            "--dry-run",
            "--json",
            expected_exit_codes=frozenset({64}),
            expect_json=False,
            kind="safe_fail_closed",
            blocked_reason="no completed data-table artifact in committed fixture",
        ),
        _scenario(
            "notebooklm download flashcards",
            "download",
            "flashcards",
            "-n",
            NOTEBOOK_ID,
            "--dry-run",
            "--json",
            expected_exit_codes=frozenset({64}),
            expect_json=False,
            kind="safe_fail_closed",
            blocked_reason="no completed flashcards artifact in committed fixture",
        ),
        _scenario(
            "notebooklm download infographic",
            "download",
            "infographic",
            "-n",
            NOTEBOOK_ID,
            "--dry-run",
            "--json",
            expected_exit_codes=frozenset({64}),
            expect_json=False,
            kind="safe_fail_closed",
            blocked_reason="no completed infographic artifact in committed fixture",
        ),
        _scenario(
            "notebooklm download mind-map",
            "download",
            "mind-map",
            "-n",
            NOTEBOOK_ID,
            "--dry-run",
            "--json",
            expected_exit_codes=frozenset({64}),
            expect_json=False,
            kind="safe_fail_closed",
            blocked_reason="no completed mind-map artifact in committed fixture",
        ),
        Scenario(
            "notebooklm download quiz",
            lambda ctx: [
                "download",
                "quiz",
                _download_output(ctx, ".json"),
                "-n",
                NOTEBOOK_ID,
                "--artifact",
                QUIZ_ARTIFACT_ID,
                "--dry-run",
                "--json",
            ],
            expected_exit_codes=frozenset({64}),
            expect_json=False,
            kind="safe_fail_closed",
            blocked_reason="quiz artifact fixture is not completed and is not downloadable",
        ),
        Scenario(
            "notebooklm download report",
            lambda ctx: [
                "download",
                "report",
                _download_output(ctx, ".md"),
                "-n",
                NOTEBOOK_ID,
                "--artifact",
                REPORT_ARTIFACT_ID,
                "--dry-run",
                "--json",
            ],
        ),
        _scenario(
            "notebooklm download slide-deck",
            "download",
            "slide-deck",
            "-n",
            NOTEBOOK_ID,
            "--dry-run",
            "--json",
            expected_exit_codes=frozenset({64}),
            expect_json=False,
            kind="safe_fail_closed",
            blocked_reason="no completed slide-deck artifact in committed fixture",
        ),
        _scenario(
            "notebooklm download video",
            "download",
            "video",
            "-n",
            NOTEBOOK_ID,
            "--dry-run",
            "--json",
            expected_exit_codes=frozenset({64}),
            expect_json=False,
            kind="safe_fail_closed",
            blocked_reason="no completed video artifact in committed fixture",
        ),
        _scenario(
            "notebooklm generate audio",
            "generate",
            "audio",
            "Brief audio",
            "-n",
            NOTEBOOK_ID,
            "--language",
            "en",
            "--json",
        ),
        _scenario(
            "notebooklm generate cinematic-video",
            "generate",
            "cinematic-video",
            "Cinematic",
            "-n",
            NOTEBOOK_ID,
            "--format",
            "cinematic",
            "--language",
            "en",
            "--json",
        ),
        _scenario(
            "notebooklm generate data-table",
            "generate",
            "data-table",
            "Make a table",
            "-n",
            NOTEBOOK_ID,
            "--language",
            "en",
            "--json",
        ),
        _scenario(
            "notebooklm generate flashcards",
            "generate",
            "flashcards",
            "Make cards",
            "-n",
            NOTEBOOK_ID,
            "--quantity",
            "fewer",
            "--json",
        ),
        _scenario(
            "notebooklm generate infographic",
            "generate",
            "infographic",
            "Make infographic",
            "-n",
            NOTEBOOK_ID,
            "--language",
            "en",
            "--orientation",
            "landscape",
            "--json",
        ),
        _scenario(
            "notebooklm generate mind-map",
            "generate",
            "mind-map",
            "-n",
            NOTEBOOK_ID,
            "--language",
            "en",
            "--instructions",
            "Map it",
            "--json",
        ),
        _scenario(
            "notebooklm generate quiz",
            "generate",
            "quiz",
            "Make quiz",
            "-n",
            NOTEBOOK_ID,
            "--quantity",
            "fewer",
            "--json",
        ),
        _scenario(
            "notebooklm generate report",
            "generate",
            "report",
            "Make report",
            "-n",
            NOTEBOOK_ID,
            "--language",
            "en",
            "--json",
        ),
        _scenario(
            "notebooklm generate revise-slide",
            "generate",
            "revise-slide",
            "Improve slide",
            "-n",
            NOTEBOOK_ID,
            "-a",
            REPORT_ARTIFACT_ID,
            "--slide",
            "1",
            "--json",
        ),
        _scenario(
            "notebooklm generate slide-deck",
            "generate",
            "slide-deck",
            "Make slides",
            "-n",
            NOTEBOOK_ID,
            "--language",
            "en",
            "--json",
        ),
        _scenario(
            "notebooklm generate video",
            "generate",
            "video",
            "Make video",
            "-n",
            NOTEBOOK_ID,
            "--language",
            "en",
            "--json",
        ),
        _scenario(
            "notebooklm history", "history", "-n", NOTEBOOK_ID, "--limit", "1", "--json"
        ),
        _scenario("notebooklm language get", "language", "get", "--json"),
        _scenario("notebooklm language list", "language", "list", "--json"),
        _scenario(
            "notebooklm language set", "language", "set", "en", "--local", "--json"
        ),
        _scenario("notebooklm list", "list", "--limit", "1", "--json"),
        Scenario(
            "notebooklm login",
            lambda ctx: ["login", "--account", "redacted@example.test"],
            expected_exit_codes=frozenset({1}),
            expect_json=False,
            kind="safe_fail_closed",
            blocked_reason="upstream rejects account selection without --browser-cookies before any live browser access",
        ),
        _scenario("notebooklm metadata", "metadata", "-n", NOTEBOOK_ID, "--json"),
        _scenario(
            "notebooklm note create",
            "note",
            "create",
            "Phase 8 note content",
            "-n",
            NOTEBOOK_ID,
            "--title",
            "Phase 8 Note",
            "--json",
        ),
        _scenario(
            "notebooklm note delete",
            "note",
            "delete",
            NOTE_ID,
            "-n",
            NOTEBOOK_ID,
            "--yes",
            "--json",
        ),
        _scenario(
            "notebooklm note get", "note", "get", NOTE_ID, "-n", NOTEBOOK_ID, "--json"
        ),
        _scenario(
            "notebooklm note list",
            "note",
            "list",
            "-n",
            NOTEBOOK_ID,
            "--limit",
            "1",
            "--json",
        ),
        _scenario(
            "notebooklm note rename",
            "note",
            "rename",
            NOTE_ID,
            "Renamed Note",
            "-n",
            NOTEBOOK_ID,
            "--json",
        ),
        _scenario(
            "notebooklm note save",
            "note",
            "save",
            NOTE_ID,
            "-n",
            NOTEBOOK_ID,
            "--title",
            "Saved Note",
            "--json",
        ),
        _scenario(
            "notebooklm profile create",
            "profile",
            "create",
            "phase8",
            expect_json=False,
            markers=("created",),
        ),
        _scenario(
            "notebooklm profile delete",
            "profile",
            "delete",
            "missing",
            "--yes",
            expected_exit_codes=frozenset({64}),
            expect_json=False,
            kind="safe_fail_closed",
            blocked_reason="missing temp profile is rejected without touching real storage",
        ),
        _scenario("notebooklm profile list", "profile", "list", "--json"),
        _scenario(
            "notebooklm profile rename",
            "profile",
            "rename",
            "default",
            "phase8",
            expected_exit_codes=frozenset({64}),
            expect_json=False,
            kind="safe_fail_closed",
            blocked_reason="fresh temp storage has no default profile to rename",
        ),
        _scenario(
            "notebooklm profile switch",
            "profile",
            "switch",
            "default",
            expected_exit_codes=frozenset({64}),
            expect_json=False,
            kind="safe_fail_closed",
            blocked_reason="fresh temp storage has no default profile to switch to",
        ),
        _scenario(
            "notebooklm rename",
            "rename",
            "Phase 8 Renamed Notebook",
            "-n",
            NOTEBOOK_ID,
            "--json",
        ),
        _scenario(
            "notebooklm research status",
            "research",
            "status",
            "-n",
            NOTEBOOK_ID,
            "--json",
        ),
        _scenario(
            "notebooklm research wait",
            "research",
            "wait",
            "-n",
            NOTEBOOK_ID,
            "--timeout",
            "5",
            "--interval",
            "1",
            "--json",
        ),
        _scenario(
            "notebooklm share add",
            "share",
            "add",
            "fixture.new@example.test",
            "-n",
            NOTEBOOK_ID,
            "--permission",
            "viewer",
            "--no-notify",
            "--json",
        ),
        _scenario(
            "notebooklm share public",
            "share",
            "public",
            "-n",
            NOTEBOOK_ID,
            "--disable",
            "--json",
        ),
        _scenario(
            "notebooklm share remove",
            "share",
            "remove",
            "fixture.viewer@example.test",
            "-n",
            NOTEBOOK_ID,
            "--yes",
            "--json",
        ),
        _scenario(
            "notebooklm share status", "share", "status", "-n", NOTEBOOK_ID, "--json"
        ),
        _scenario(
            "notebooklm share update",
            "share",
            "update",
            "fixture.viewer@example.test",
            "-n",
            NOTEBOOK_ID,
            "--permission",
            "editor",
            "--json",
        ),
        _scenario(
            "notebooklm share view-level",
            "share",
            "view-level",
            "chat",
            "-n",
            NOTEBOOK_ID,
            "--json",
        ),
        _scenario(
            "notebooklm skill install",
            "skill",
            "install",
            "--scope",
            "project",
            "--target",
            "claude",
            "--dry-run",
            expect_json=False,
            markers=("Dry run", "Would"),
        ),
        _scenario(
            "notebooklm skill show",
            "skill",
            "show",
            "--target",
            "source",
            expect_json=False,
            markers=("name: notebooklm",),
        ),
        _scenario(
            "notebooklm skill status",
            "skill",
            "status",
            "--scope",
            "project",
            expect_json=False,
            markers=("NotebookLM skill status",),
        ),
        _scenario(
            "notebooklm skill uninstall",
            "skill",
            "uninstall",
            "--scope",
            "project",
            "--target",
            "claude",
            expect_json=False,
            markers=("Skill not installed",),
        ),
        _scenario(
            "notebooklm source add",
            "source",
            "add",
            "Synthetic pasted text",
            "-n",
            NOTEBOOK_ID,
            "--type",
            "text",
            "--title",
            "Phase 8 Text",
            "--json",
        ),
        _scenario(
            "notebooklm source add-drive",
            "source",
            "add-drive",
            "drive-file-123",
            "Synthetic Drive Doc",
            "-n",
            NOTEBOOK_ID,
            "--mime-type",
            "google-doc",
            "--json",
        ),
        _scenario(
            "notebooklm source add-research",
            "source",
            "add-research",
            RESEARCH_QUERY,
            "-n",
            NOTEBOOK_ID,
            "--no-wait",
            "--json",
        ),
        _scenario(
            "notebooklm source clean",
            "source",
            "clean",
            "-n",
            NOTEBOOK_ID,
            "--dry-run",
            "--json",
        ),
        _scenario(
            "notebooklm source delete",
            "source",
            "delete",
            SOURCE_ID,
            "-n",
            NOTEBOOK_ID,
            "--yes",
            "--json",
        ),
        _scenario(
            "notebooklm source delete-by-title",
            "source",
            "delete-by-title",
            "Synthetic Web Source",
            "-n",
            NOTEBOOK_ID,
            "--yes",
            "--json",
        ),
        Scenario(
            "notebooklm source fulltext",
            lambda ctx: [
                "source",
                "fulltext",
                SOURCE_ID,
                "-n",
                NOTEBOOK_ID,
                "--format",
                "markdown",
                "-o",
                _fulltext_output(ctx),
                "--json",
            ],
        ),
        _scenario(
            "notebooklm source get",
            "source",
            "get",
            SOURCE_ID,
            "-n",
            NOTEBOOK_ID,
            "--json",
        ),
        _scenario(
            "notebooklm source guide",
            "source",
            "guide",
            SOURCE_ID,
            "-n",
            NOTEBOOK_ID,
            "--json",
        ),
        _scenario(
            "notebooklm source list",
            "source",
            "list",
            "-n",
            NOTEBOOK_ID,
            "--limit",
            "1",
            "--json",
        ),
        _scenario(
            "notebooklm source refresh",
            "source",
            "refresh",
            SOURCE_ID,
            "-n",
            NOTEBOOK_ID,
            "--json",
        ),
        _scenario(
            "notebooklm source rename",
            "source",
            "rename",
            SOURCE_ID,
            "Renamed Source",
            "-n",
            NOTEBOOK_ID,
            "--json",
        ),
        _scenario(
            "notebooklm source stale",
            "source",
            "stale",
            SECOND_SOURCE_ID,
            "-n",
            NOTEBOOK_ID,
            "--json",
        ),
        _scenario(
            "notebooklm source wait",
            "source",
            "wait",
            SOURCE_ID,
            "-n",
            NOTEBOOK_ID,
            "--timeout",
            "5",
            "--interval",
            "1",
            "--json",
        ),
        _scenario("notebooklm status", "status", "--json"),
        _scenario(
            "notebooklm summary", "summary", "-n", NOTEBOOK_ID, "--topics", "--json"
        ),
        _scenario("notebooklm use", "use", NOTEBOOK_ID, "--json"),
    ]
    return tuple(scenarios)


@contextlib.contextmanager
def _scenario_environment(ctx: ScenarioContext) -> Iterator[None]:
    old_cwd = Path.cwd()
    old_env = os.environ.copy()
    ctx.cwd.mkdir(parents=True, exist_ok=True)
    ctx.home.mkdir(parents=True, exist_ok=True)
    try:
        os.environ.update(
            {
                "HOME": str(ctx.home),
                "USERPROFILE": str(ctx.home),
                "XDG_CONFIG_HOME": str(ctx.home / ".config"),
                "XDG_CACHE_HOME": str(ctx.home / ".cache"),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        os.chdir(ctx.cwd)
        yield
    finally:
        os.chdir(old_cwd)
        os.environ.clear()
        os.environ.update(old_env)


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _json_summary(text: str) -> dict[str, object]:
    data = json.loads(text)
    if isinstance(data, dict):
        return {"type": "object", "keys": sorted(data)[:20], "key_count": len(data)}
    if isinstance(data, list):
        return {"type": "array", "length": len(data)}
    return {"type": type(data).__name__}


def _run_one(scenario: Scenario) -> dict[str, object]:
    from notebooklm import cli

    with tempfile.TemporaryDirectory(prefix="phase8-cli-audit-") as tmp:
        root = Path(tmp)
        ctx = ScenarioContext(
            root=root,
            home=root / "home",
            storage=root / "profiles",
            cwd=root / "cwd",
        )
        argv = scenario.build_argv(ctx)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            _scenario_environment(ctx),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            exit_code = cli.console(argv)
        out = stdout.getvalue()
        err = stderr.getvalue()
        errors: list[str] = []
        json_info: dict[str, object] | None = None
        if exit_code not in scenario.expected_exit_codes:
            errors.append(
                f"exit code {exit_code} not in expected {sorted(scenario.expected_exit_codes)}"
            )
        if scenario.expect_json:
            try:
                json_info = _json_summary(out)
            except Exception as exc:  # noqa: BLE001 - report stable audit error
                errors.append(f"stdout did not parse as JSON: {exc.__class__.__name__}")
        for marker in scenario.markers:
            if marker not in out and marker not in err:
                errors.append(f"missing marker {marker!r}")
        output_files = [
            str(path.relative_to(ctx.cwd))
            for path in ctx.cwd.rglob("*")
            if path.is_file()
        ]
        return {
            "leaf": scenario.leaf,
            "kind": scenario.kind,
            "passed": not errors,
            "exit_code": exit_code,
            "expected_exit_codes": sorted(scenario.expected_exit_codes),
            "expect_json": scenario.expect_json,
            "json": json_info,
            "stdout_sha256": _hash_text(out),
            "stderr_sha256": _hash_text(err),
            "stdout_bytes": len(out.encode("utf-8")),
            "stderr_bytes": len(err.encode("utf-8")),
            "marker_count": len(scenario.markers),
            "temp_output_files": sorted(output_files),
            "blocked_reason": scenario.blocked_reason,
            "errors": errors,
        }


def _load_pinned_leaves(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    leaves = data.get("leaf_commands")
    if not isinstance(leaves, list) or not all(
        isinstance(item, str) for item in leaves
    ):
        raise SystemExit(f"invalid cli surface leaf_commands in {path}")
    return leaves


def _parity_matrix_cli_state(path: Path) -> str | None:
    for line in path.read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and cells[0] == "cli" and len(cells) >= 4:
            return cells[3]
    return None


def run_audit(
    *, surface_path: Path | None = None, matrix_path: Path | None = None
) -> dict[str, object]:
    surface_path = surface_path or REPO_ROOT / "compat" / "cli_surface.json"
    matrix_path = matrix_path or REPO_ROOT / "compat" / "parity_matrix.md"
    pinned = _load_pinned_leaves(surface_path)
    scenarios = build_scenarios()
    scenario_leaves = [scenario.leaf for scenario in scenarios]
    counts = Counter(scenario_leaves)
    missing = sorted(set(pinned) - set(scenario_leaves))
    extra = sorted(set(scenario_leaves) - set(pinned))
    duplicates = sorted(leaf for leaf, count in counts.items() if count != 1)

    results = [_run_one(scenario) for scenario in scenarios]
    failures = [result for result in results if not result["passed"]]
    cli_state = _parity_matrix_cli_state(matrix_path)
    matrix_errors = []
    if cli_state not in {"open", "pass"}:
        matrix_errors.append(
            f"compat/parity_matrix.md cli row is {cli_state!r}, expected 'open' or 'pass'"
        )

    passed = not (missing or extra or duplicates or failures or matrix_errors)
    return {
        "phase": "8",
        "audit": "cli_behavior_parity",
        "status": "passed" if passed else "failed",
        "pinned_leaf_total": len(pinned),
        "scenario_total": len(scenarios),
        "covered_leaf_total": len(set(scenario_leaves) & set(pinned)),
        "missing_leaf_commands": missing,
        "extra_leaf_commands": extra,
        "duplicate_leaf_commands": duplicates,
        "passed_scenario_count": sum(1 for result in results if result["passed"]),
        "failed_scenario_count": len(failures),
        "failures": failures,
        "scenarios": results,
        "cli_category_state": cli_state,
        "category_promotion": {"cli": cli_state == "pass"},
        "blocked_reason": (
            "Phase 8 proves safe local CLI behavior scenarios; Phase 19 direct "
            "offline evidence promotes the CLI category when parity_matrix.md records pass."
            if cli_state == "pass"
            else "Phase 8 proves safe local CLI behavior scenarios only; upstream-vs-bare "
            "live/differential CLI parity remains a later closure gate."
        ),
        "matrix_errors": matrix_errors,
    }


def _human(report: dict[str, object]) -> str:
    lines = [
        "Phase 8 CLI behavior parity audit",
        f"status: {report['status']}",
        f"pinned leaves: {report['pinned_leaf_total']}",
        f"scenarios: {report['scenario_total']}",
        f"covered leaves: {report['covered_leaf_total']}",
        f"passed scenarios: {report['passed_scenario_count']}",
        f"failed scenarios: {report['failed_scenario_count']}",
        f"cli category state: {report['cli_category_state']}",
        "category promotion: cli="
        + str(report.get("category_promotion", {}).get("cli", False)).lower(),
        f"blocked reason: {report['blocked_reason']}",
    ]
    missing = report["missing_leaf_commands"]
    extra = report["extra_leaf_commands"]
    duplicates = report["duplicate_leaf_commands"]
    if isinstance(missing, list) and missing:
        lines.append("missing: " + ", ".join(str(item) for item in missing))
    if isinstance(extra, list) and extra:
        lines.append("extra: " + ", ".join(str(item) for item in extra))
    if isinstance(duplicates, list) and duplicates:
        lines.append("duplicates: " + ", ".join(str(item) for item in duplicates))
    failures = report.get("failures") or []
    if isinstance(failures, list) and failures:
        lines.append("failures:")
        for failure in failures[:20]:
            if isinstance(failure, dict):
                lines.append(f"  - {failure.get('leaf')}: {failure.get('errors')}")
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    parser.add_argument(
        "--strict", action="store_true", help="exit nonzero unless audit passes"
    )
    parser.add_argument(
        "--surface",
        type=Path,
        default=None,
        help="explicit compat/cli_surface.json path",
    )
    parser.add_argument(
        "--matrix",
        type=Path,
        default=None,
        help="explicit compat/parity_matrix.md path",
    )
    ns = parser.parse_args(argv)
    report = run_audit(surface_path=ns.surface, matrix_path=ns.matrix)
    if ns.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        sys.stdout.write(_human(report))
    if ns.strict and report["status"] != "passed":
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by subprocess tests.
    raise SystemExit(main())
