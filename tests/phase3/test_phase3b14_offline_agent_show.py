"""Phase 3B14 offline ``agent show`` CLI parity.

This batch promotes the pinned notebooklm-py==0.7.2 ``agent show`` leaf for
static bundled agent instructions only. It does not read auth/browser/home
state, make live RPC/network calls, or mutate NotebookLM data.
"""

from __future__ import annotations

import hashlib
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

EXPECTED_CODEX_SHA256 = (
    "1de94aff3590c5be6610dc26258810b44c64d6d9d36d242eafb0c70516cbc21e"
)
EXPECTED_SKILL_SHA256 = (
    "380fa138338ae04080211d34f66626432e64767a0af8be6d564e8a6a18bca634"
)


def _poison_home(monkeypatch):
    monkeypatch.setattr(
        Path,
        "home",
        staticmethod(lambda: (_ for _ in ()).throw(AssertionError("real home access"))),
    )


def _load(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))
    return SimpleNamespace(cli=importlib.import_module("notebooklm.cli"))


def _run(mods, capsys, argv):
    code = mods.cli.console(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.fixture()
def mods(repo_root, monkeypatch):
    _poison_home(monkeypatch)
    return _load(repo_root, monkeypatch)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_cli_agent_show_codex_matches_committed_pinned_template(
    mods, capsys, repo_root
):
    assert "agent" in mods.cli.IMPLEMENTED_COMMANDS

    code, out, err = _run(mods, capsys, ["agent", "show", "codex"])

    assert code == 0
    assert err == ""
    expected = (repo_root / "notebooklm" / "data" / "CODEX.md").read_text(
        encoding="utf-8"
    )
    assert out == expected.rstrip() + "\n"
    assert _sha256_text(out.rstrip() + "\n") == EXPECTED_CODEX_SHA256
    assert "# Repository Guidelines" in out
    assert "NOTEBOOKLM_PROFILE=agent-<id>" in out


def test_cli_agent_show_claude_matches_committed_pinned_skill_template(
    mods, capsys, repo_root
):
    code, out, err = _run(mods, capsys, ["agent", "show", "claude"])

    assert code == 0
    assert err == ""
    expected = (repo_root / "notebooklm" / "data" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert out == expected.rstrip() + "\n"
    assert _sha256_text(out.rstrip() + "\n") == EXPECTED_SKILL_SHA256
    assert "name: notebooklm" in out
    assert "# NotebookLM Automation" in out


def test_cli_agent_show_is_case_insensitive_and_has_help(mods, capsys):
    code, out, err = _run(mods, capsys, ["agent", "show", "CoDeX"])
    assert code == 0
    assert err == ""
    assert out.startswith("# Repository Guidelines")

    with pytest.raises(SystemExit) as excinfo:
        mods.cli.console(["agent", "--help"])
    assert excinfo.value.code == 0
    help_text = capsys.readouterr().out
    assert "Show bundled instructions for supported agent environments." in help_text
    assert "show" in help_text

    with pytest.raises(SystemExit) as excinfo:
        mods.cli.console(["agent", "show", "--help"])
    assert excinfo.value.code == 0
    help_text = capsys.readouterr().out
    assert "Display instructions for Codex or Claude Code." in help_text
    assert "{codex|claude}" in help_text


def test_cli_agent_show_rejects_missing_or_unknown_target(mods, capsys):
    code, out, err = _run(mods, capsys, ["agent", "show"])
    assert code == 2
    assert out == ""
    golden = (
        Path(__file__).resolve().parents[2]
        / "compat"
        / "cli_golden"
        / "error_notebooklm_agent_show.txt"
    ).read_text(encoding="utf-8")
    assert err == golden

    code, out, err = _run(mods, capsys, ["agent", "show", "gemini"])
    assert code == 64
    assert out == ""
    assert "codex" in err and "claude" in err
