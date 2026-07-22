"""Phase 3A16 fixture-backed CLI history promotion.

This historical slice promoted read-only ``notebooklm history`` over the
committed synthetic chat fixture seam. Phase 3B6 later promotes safe
fixture-backed clear/save behavior while live RPC, auth/browser/home state,
credentials, and real NotebookLM mutation remain closed.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import import_origin_audit  # noqa: E402  (placed on sys.path by tests/conftest.py)

SYNTHETIC_NOTEBOOK_ID = "fake-notebook-0001"
SYNTHETIC_HISTORY_QUESTION = "Phase 0 synthetic question."
SYNTHETIC_HISTORY_ANSWER = "Phase 0 synthetic answer chunk."
EXPECTED_HISTORY = [
    {
        "turn_number": 1,
        "question": SYNTHETIC_HISTORY_QUESTION,
        "answer": SYNTHETIC_HISTORY_ANSWER,
    }
]


def _load(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))
    return SimpleNamespace(cli=importlib.import_module("notebooklm.cli"))


def _run(mods, capsys, argv):
    code = mods.cli.console(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _poison_home(monkeypatch):
    monkeypatch.setattr(
        Path,
        "home",
        staticmethod(lambda: (_ for _ in ()).throw(AssertionError("real home access"))),
    )


def test_cli_history_json_uses_committed_fixtures_without_home(
    repo_root, monkeypatch, capsys
):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)

    code, out, err = _run(
        mods, capsys, ["history", "--notebook", SYNTHETIC_NOTEBOOK_ID, "--json"]
    )

    assert code == 0
    assert err == ""
    assert json.loads(out) == EXPECTED_HISTORY


def test_cli_history_defaults_to_first_synthetic_notebook(
    repo_root, monkeypatch, capsys
):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)

    code, out, err = _run(mods, capsys, ["history", "--json"])

    assert code == 0
    assert err == ""
    assert json.loads(out) == EXPECTED_HISTORY


def test_cli_history_limit_zero_returns_empty_json(repo_root, monkeypatch, capsys):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)

    code, out, err = _run(
        mods, capsys, ["history", "-n", "fake-notebook", "--limit", "0", "--json"]
    )

    assert code == 0
    assert err == ""
    assert json.loads(out) == []


def test_cli_history_plain_output_shows_preview_by_default(
    repo_root, monkeypatch, capsys
):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)

    code, out, err = _run(mods, capsys, ["history", "-n", SYNTHETIC_NOTEBOOK_ID])

    assert code == 0
    assert err == ""
    assert "turn_number: 1" in out
    assert "question: Phase 0 synthetic question." in out
    assert "answer: Phase 0 synthetic answer chunk." in out


def test_cli_history_show_all_and_no_truncate_preserve_full_question(
    repo_root, monkeypatch, capsys
):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)

    code, out, err = _run(
        mods,
        capsys,
        ["history", "-n", SYNTHETIC_NOTEBOOK_ID, "--show-all", "--no-truncate"],
    )

    assert code == 0
    assert err == ""
    assert SYNTHETIC_HISTORY_QUESTION in out
    assert SYNTHETIC_HISTORY_ANSWER in out


def test_phase3a16_history_preview_truncation_respects_display_flags(
    repo_root, monkeypatch
):
    mods = _load(repo_root, monkeypatch)
    long_question = "Q" * 60
    long_answer = "A" * 61
    turns = [{"turn_number": 1, "question": long_question, "answer": long_answer}]

    previewed = mods.cli._preview_history_turns(
        turns, show_all=False, no_truncate=False
    )
    assert previewed == [
        {
            "turn_number": 1,
            "question": ("Q" * 47) + "...",
            "answer": ("A" * 47) + "...",
        }
    ]
    assert (
        mods.cli._preview_history_turns(turns, show_all=True, no_truncate=False)
        == turns
    )
    assert (
        mods.cli._preview_history_turns(turns, show_all=False, no_truncate=True)
        == turns
    )


def test_cli_history_missing_selector_is_redacted(repo_root, monkeypatch, capsys):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)
    secret = "ya" + "29." + "S" * 40
    synthetic_home = "/".join(("", "Users", "example"))
    private_selector = f"private {secret} {synthetic_home}/notebook"

    code, out, err = _run(mods, capsys, ["history", "-n", private_selector, "--json"])

    assert code == 64
    assert out == ""
    assert "notebook selector not found" in err
    assert secret not in err
    assert synthetic_home not in err


@pytest.mark.parametrize(
    "argv",
    [
        ["history", "--clear"],
        ["history", "--save"],
        ["history", "--save", "--note-title", "Summary"],
    ],
)
def test_cli_history_preserves_later_fixture_backed_promotions(
    repo_root, monkeypatch, capsys, argv
):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)

    code, out, err = _run(mods, capsys, argv)

    assert code == 0
    assert out
    assert "later parity phase" not in err


def test_phase3a16_promotes_readonly_history_but_keeps_generation_closed(
    repo_root, monkeypatch
):
    _load(repo_root, monkeypatch)
    cli = importlib.import_module("notebooklm.cli")

    assert {
        "list",
        "use",
        "note",
        "source",
        "artifact",
        "ask",
        "metadata",
        "history",
        "summary",
    } <= set(cli.IMPLEMENTED_COMMANDS)
    assert "download" in cli.IMPLEMENTED_COMMANDS
    assert "generate" in cli.IMPLEMENTED_COMMANDS


def test_phase3a16_history_flags_match_pinned_oracle(repo_root):
    cli_surface = json.loads(
        (repo_root / "compat" / "cli_surface.json").read_text(encoding="utf-8")
    )
    history_node = next(
        node
        for node in cli_surface["nodes"]
        if node.get("command") == "notebooklm history"
    )
    opts = {
        opt
        for param in history_node["params"]
        for opt in param.get("opts", [])
        if opt.startswith("-")
    }

    assert opts == {
        "-n",
        "--notebook",
        "-l",
        "--limit",
        "--clear",
        "--save",
        "-t",
        "--note-title",
        "--json",
        "--show-all",
        "--no-truncate",
    }


def test_phase3a16_live_history_parser_exposes_oracle_flags(
    repo_root, monkeypatch, capsys
):
    mods = _load(repo_root, monkeypatch)

    with pytest.raises(SystemExit) as excinfo:
        mods.cli.console(["history", "--help"])

    assert excinfo.value.code == 0
    help_text = capsys.readouterr().out
    for option in {
        "-n",
        "--notebook",
        "-l",
        "--limit",
        "--clear",
        "--save",
        "-t",
        "--note-title",
        "--json",
        "--show-all",
        "--no-truncate",
    }:
        assert option in help_text
    assert "--fixture-dir" not in help_text


def test_phase3a16_cli_history_wiring_is_stdlib_and_offline_only(repo_root):
    assert (
        import_origin_audit.audit(
            roots=(
                "notebooklm/cli.py",
                "notebooklm/fake_rpc.py",
                "notebooklm/chat.py",
                "notebooklm/notebooks.py",
            ),
        )
        == []
    )
    src = "\n".join(
        (repo_root / "notebooklm" / name).read_text(encoding="utf-8")
        for name in ("fake_rpc.py", "chat.py", "notebooks.py")
    )
    forbidden = {
        "sock" + "et",
        "http" + ".client",
        "urllib" + ".request",
        "url" + "open",
        "sub" + "process",
        "Path" + ".home",
        "expand" + "user",
        "os" + ".environ",
        "browser" + "_cookies",
        "interactive" + "_login",
        "Network" + ".",
        "Dev" + "Tools",
        "key" + "ring",
        "secret" + "storage",
        "win32" + "crypt",
        "browser" + "_cookie3",
        "browser" + "cookie",
    }
    assert sorted(token for token in forbidden if token in src) == []
