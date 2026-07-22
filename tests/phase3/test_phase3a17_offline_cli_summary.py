"""Phase 3A17 fixture-backed CLI summary promotion.

This slice promotes only read-only ``notebooklm summary`` over committed
synthetic notebook/source fixtures. It does not enter live RPC, generate remote
AI content, read auth/browser/home state, touch credentials, mutate NotebookLM,
or promote parity rows.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import import_origin_audit  # noqa: E402  (placed on sys.path by tests/conftest.py)

SYNTHETIC_NOTEBOOK_ID = "fake-notebook-0001"
SYNTHETIC_TITLE = "Phase 0 Synthetic Notebook"
SOURCE_TITLES = ["Synthetic Web Source", "Synthetic Pasted Text Source"]
EXPECTED_SUMMARY_TEXT = (
    "Phase 0 Synthetic Notebook has 2 ready sources: "
    "Synthetic Web Source; Synthetic Pasted Text Source."
)
EXPECTED_SUMMARY = {
    "notebook_id": SYNTHETIC_NOTEBOOK_ID,
    "title": SYNTHETIC_TITLE,
    "source_count": 2,
    "summary": EXPECTED_SUMMARY_TEXT,
}


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


def test_cli_summary_json_uses_committed_fixtures_without_home(
    repo_root, monkeypatch, capsys
):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)

    code, out, err = _run(
        mods, capsys, ["summary", "--notebook", SYNTHETIC_NOTEBOOK_ID, "--json"]
    )

    assert code == 0
    assert err == ""
    assert json.loads(out) == EXPECTED_SUMMARY


def test_cli_summary_defaults_to_first_synthetic_notebook(
    repo_root, monkeypatch, capsys
):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)

    code, out, err = _run(mods, capsys, ["summary", "--json"])

    assert code == 0
    assert err == ""
    assert json.loads(out) == EXPECTED_SUMMARY


def test_cli_summary_topics_are_opt_in_and_source_ordered(
    repo_root, monkeypatch, capsys
):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)

    code, out, err = _run(
        mods, capsys, ["summary", "-n", "fake-notebook", "--topics", "--json"]
    )

    assert code == 0
    assert err == ""
    data = json.loads(out)
    assert data == {**EXPECTED_SUMMARY, "suggested_topics": SOURCE_TITLES}


def test_cli_summary_plain_output_is_deterministic(repo_root, monkeypatch, capsys):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)

    code, out, err = _run(mods, capsys, ["summary", "-n", SYNTHETIC_NOTEBOOK_ID])

    assert code == 0
    assert err == ""
    assert f"notebook_id: {SYNTHETIC_NOTEBOOK_ID}" in out
    assert f"title: {SYNTHETIC_TITLE}" in out
    assert "source_count: 2" in out
    assert f"summary: {EXPECTED_SUMMARY_TEXT}" in out
    assert "suggested_topics" not in out


def test_cli_summary_missing_selector_is_redacted(repo_root, monkeypatch, capsys):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)
    secret = "ya" + "29." + "T" * 40
    raw_path = "/".join(("", "Users", "example", "notebook"))
    private_selector = f"private {secret} {raw_path}"

    code, out, err = _run(mods, capsys, ["summary", "-n", private_selector, "--json"])

    assert code == 64
    assert out == ""
    assert "notebook selector not found" in err
    assert secret not in err
    assert raw_path not in err


@pytest.mark.parametrize(
    "argv",
    [
        ["history", "--clear"],
        ["history", "--save"],
    ],
)
def test_cli_summary_preserves_later_history_promotions(
    repo_root, monkeypatch, capsys, argv
):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)

    code, out, err = _run(mods, capsys, argv)

    assert code == 0
    assert out
    assert "later parity phase" not in err


def test_phase3a17_promotes_readonly_summary_and_later_roots(repo_root, monkeypatch):
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
    assert {"create", "delete", "rename"} <= set(cli.IMPLEMENTED_COMMANDS)
    assert "generate" in cli.IMPLEMENTED_COMMANDS


def test_phase3a17_summary_flags_match_pinned_oracle(repo_root):
    cli_surface = json.loads(
        (repo_root / "compat" / "cli_surface.json").read_text(encoding="utf-8")
    )
    summary_node = next(
        node
        for node in cli_surface["nodes"]
        if node.get("command") == "notebooklm summary"
    )
    opts = {
        opt
        for param in summary_node["params"]
        for opt in param.get("opts", [])
        if opt.startswith("-")
    }

    assert opts == {"-n", "--notebook", "--topics", "--json"}


def test_phase3a17_live_summary_parser_exposes_oracle_flags(
    repo_root, monkeypatch, capsys
):
    mods = _load(repo_root, monkeypatch)

    with pytest.raises(SystemExit) as excinfo:
        mods.cli.console(["summary", "--help"])

    assert excinfo.value.code == 0
    help_text = capsys.readouterr().out
    for option in {"-n", "--notebook", "--topics", "--json"}:
        assert option in help_text
    assert "--fixture-dir" not in help_text


def test_phase3a17_cli_summary_wiring_is_stdlib_and_offline_only(repo_root):
    assert (
        import_origin_audit.audit(
            roots=(
                "notebooklm/cli.py",
                "notebooklm/fake_rpc.py",
                "notebooklm/sources.py",
                "notebooklm/notebooks.py",
            ),
        )
        == []
    )
    src = "\n".join(
        (repo_root / "notebooklm" / name).read_text(encoding="utf-8")
        for name in ("fake_rpc.py", "sources.py", "notebooks.py")
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
