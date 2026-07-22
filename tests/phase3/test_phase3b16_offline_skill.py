"""Phase 3B16 offline ``skill`` CLI parity.

This batch promotes the pinned notebooklm-py==0.7.2 ``skill`` command group
against temp-scoped filesystem fixtures only. Tests monkeypatch `Path.home()`
and `cwd` so no real user/local agent skill directories are touched.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))
    return SimpleNamespace(cli=importlib.import_module("notebooklm.cli"))


def _run(mods, capsys, argv):
    code = mods.cli.console(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.fixture()
def sandbox(repo_root, monkeypatch, tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.chdir(project)
    return SimpleNamespace(
        home=home,
        project=project,
        mods=_load(repo_root, monkeypatch),
        source=(repo_root / "notebooklm" / "data" / "SKILL.md").read_text(
            encoding="utf-8"
        ),
    )


def _skill_path(root: Path, target: str) -> Path:
    if target == "claude":
        return root / ".claude" / "skills" / "notebooklm" / "SKILL.md"
    if target == "agents":
        return root / ".agents" / "skills" / "notebooklm" / "SKILL.md"
    raise AssertionError(target)


def test_cli_skill_show_source_reads_packaged_skill(sandbox, capsys):
    assert "skill" in sandbox.mods.cli.IMPLEMENTED_COMMANDS

    code, out, err = _run(sandbox.mods, capsys, ["skill", "show"])

    assert code == 0
    assert err == ""
    assert out == sandbox.source.rstrip() + "\n"
    assert "name: notebooklm" in out


def test_cli_skill_install_status_show_and_uninstall_user_scope(sandbox, capsys):
    code, out, err = _run(sandbox.mods, capsys, ["skill", "install", "--scope", "user"])
    assert code == 0
    assert err == ""
    assert "Installed" in out
    assert "Scope:" in out and "user" in out

    for target in ("claude", "agents"):
        path = _skill_path(sandbox.home, target)
        assert path.exists(), path
        content = path.read_text(encoding="utf-8")
        assert content.startswith("---")
        assert "<!-- notebooklm-py v" in content
        assert "name: notebooklm" in content

    code, out, err = _run(sandbox.mods, capsys, ["skill", "status", "--scope", "user"])
    assert code == 0
    assert err == ""
    assert "NotebookLM skill status (user scope)" in out
    assert "Claude Code: Installed" in out
    assert "Agent Skills: Installed" in out
    assert "Skill version:" in out

    code, out, err = _run(
        sandbox.mods, capsys, ["skill", "show", "--scope", "user", "--target", "claude"]
    )
    assert code == 0
    assert err == ""
    assert out.startswith("---")
    assert "<!-- notebooklm-py v" in out

    code, out, err = _run(
        sandbox.mods, capsys, ["skill", "uninstall", "--scope", "user"]
    )
    assert code == 0
    assert err == ""
    assert "Uninstalled" in out
    assert not _skill_path(sandbox.home, "claude").exists()
    assert not _skill_path(sandbox.home, "agents").exists()

    code, out, err = _run(sandbox.mods, capsys, ["skill", "status", "--scope", "user"])
    assert code == 0
    assert err == ""
    assert "Not installed" in out
    assert "Run notebooklm skill install" in out


def test_cli_skill_project_dry_run_no_clobber_and_force_boundaries(sandbox, capsys):
    code, out, err = _run(
        sandbox.mods,
        capsys,
        ["skill", "install", "--scope", "project", "--target", "claude", "--dry-run"],
    )
    assert code == 0
    assert err == ""
    assert "Dry run" in out
    assert "Would create" in out
    assert not _skill_path(sandbox.project, "claude").exists()

    skill_path = _skill_path(sandbox.project, "claude")
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("local custom skill\n", encoding="utf-8")

    code, out, err = _run(
        sandbox.mods,
        capsys,
        ["skill", "install", "--scope", "project", "--target", "claude"],
    )
    assert code == 1
    assert err == ""
    assert "Refusing to overwrite" in out
    assert skill_path.read_text(encoding="utf-8") == "local custom skill\n"

    code, out, err = _run(
        sandbox.mods,
        capsys,
        [
            "skill",
            "install",
            "--scope",
            "project",
            "--target",
            "claude",
            "--no-clobber",
        ],
    )
    assert code == 0
    assert err == ""
    assert "Skipped" in out
    assert skill_path.read_text(encoding="utf-8") == "local custom skill\n"

    code, out, err = _run(
        sandbox.mods,
        capsys,
        ["skill", "install", "--scope", "project", "--target", "claude", "--force"],
    )
    assert code == 0
    assert err == ""
    assert "Installed" in out
    assert "<!-- notebooklm-py v" in skill_path.read_text(encoding="utf-8")


def test_cli_skill_rejects_user_scope_hardening_flags_and_bad_target(sandbox, capsys):
    for flag in ("--dry-run", "--no-clobber", "--force"):
        code, out, err = _run(
            sandbox.mods, capsys, ["skill", "install", "--scope", "user", flag]
        )
        assert code == 1
        assert err == ""
        assert "require --scope project" in out

    code, out, err = _run(
        sandbox.mods,
        capsys,
        ["skill", "install", "--scope", "project", "--force", "--no-clobber"],
    )
    assert code == 1
    assert err == ""
    assert "mutually exclusive" in out

    code, out, err = _run(sandbox.mods, capsys, ["skill", "show", "--target", "bogus"])
    assert code == 64
    assert out == ""
    assert "source" in err and "claude" in err and "agents" in err
