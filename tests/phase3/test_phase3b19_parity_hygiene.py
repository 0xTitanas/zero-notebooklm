"""Phase 3B19 parity-hygiene checks.

This batch does not open new live or destructive surfaces. It removes stale
post-parity scaffolding that can mislead future work after the promoted offline
CLI/API/runtime batches:

* dead ``_closed`` helper definitions in modules whose public methods no longer
  call them;
* root help text that still says every other command is "not implemented yet";
* bounded-surface oracle naming/comments that should reflect Phase 3B18.
"""

from __future__ import annotations


def test_no_dead_closed_helpers_remain_in_promoted_offline_modules(repo_root):
    for rel in (
        "notebooklm/artifacts.py",
        "notebooklm/chat.py",
        "notebooklm/notes.py",
        "notebooklm/sources.py",
        "notebooklm/client.py",
    ):
        text = (repo_root / rel).read_text(encoding="utf-8")
        assert "def _closed(" not in text
        assert "def _closed_surface(" not in text


def test_root_help_no_longer_claims_broad_unimplemented_surface(
    repo_root, monkeypatch, capsys
):
    monkeypatch.syspath_prepend(str(repo_root))
    from notebooklm import cli

    code = cli.console([])
    out = capsys.readouterr().out

    assert code == 0
    assert "not implemented yet" not in out.lower()
    assert "functional parity is not implemented" not in out.lower()
    assert "NotebookLM CLI." in out


def test_bounded_surface_oracle_name_tracks_current_runtime_phase(repo_root):
    text = (repo_root / "tests" / "test_phase0_oracle.py").read_text(encoding="utf-8")
    assert "test_runtime_surfaces_are_limited_to_phase3b18" in text
    assert "test_runtime_surfaces_are_limited_to_phase3b17" not in text
