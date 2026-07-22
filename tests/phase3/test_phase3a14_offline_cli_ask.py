"""Phase 3A14 fixture-backed CLI ask promotion.

This historical slice promoted read-only ``notebooklm ask`` over committed
synthetic ``chat_ask`` fixtures and the Phase 3A7 ``ChatAPI`` foothold. Phase
3B6 later promotes the safe fixture-backed ``--new``, source-filter, and
save-as-note flags while live RPC, auth/browser/home reads, and real NotebookLM
mutation remain closed.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import import_origin_audit  # noqa: E402  (placed on sys.path by tests/conftest.py)

SYNTHETIC_NOTEBOOK_ID = "fake-notebook-0001"
SYNTHETIC_QUESTION = "Phase 0 synthetic question."
SYNTHETIC_ANSWER = "Phase 0 synthetic answer chunk."


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


def test_cli_ask_plain_uses_committed_fixture_without_home(
    repo_root, monkeypatch, capsys
):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)

    code, out, err = _run(
        mods,
        capsys,
        ["ask", SYNTHETIC_QUESTION, "-n", SYNTHETIC_NOTEBOOK_ID],
    )

    assert code == 0
    assert out == f"{SYNTHETIC_ANSWER}\n"
    assert err == ""


def test_cli_ask_json_includes_offline_conversation_metadata(
    repo_root, monkeypatch, capsys
):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)

    code, out, err = _run(
        mods,
        capsys,
        [
            "ask",
            SYNTHETIC_QUESTION,
            "--notebook",
            SYNTHETIC_NOTEBOOK_ID,
            "--conversation-id",
            "conversation-1",
            "--json",
        ],
    )

    assert code == 0
    assert err == ""
    assert json.loads(out) == {
        "answer": SYNTHETIC_ANSWER,
        "conversation_id": "conversation-1",
        "is_follow_up": False,
        "raw_response": "",
        "references": [],
        "turn_number": 1,
    }


def test_cli_ask_supports_explicit_prompt_file(
    repo_root, monkeypatch, capsys, tmp_path
):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text(SYNTHETIC_QUESTION, encoding="utf-8")

    code, out, err = _run(
        mods,
        capsys,
        ["ask", "--prompt-file", str(prompt_file), "-n", SYNTHETIC_NOTEBOOK_ID],
    )

    assert code == 0
    assert out == f"{SYNTHETIC_ANSWER}\n"
    assert err == ""

    code, out, err = _run(
        mods,
        capsys,
        [
            "ask",
            "--prompt-file",
            str(tmp_path / "missing.txt"),
            "-n",
            SYNTHETIC_NOTEBOOK_ID,
        ],
    )
    assert code == 64
    assert out == ""
    assert "prompt file could not be read" in err


def test_cli_ask_rejects_unsupported_question_without_echoing_secret(
    repo_root, monkeypatch, capsys
):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)
    secret = "ya" + "29." + "S" * 40
    synthetic_home = "/".join(("", "Users", "example"))
    private_question = f"private {secret} {synthetic_home}/notebook"

    code, out, err = _run(
        mods,
        capsys,
        ["ask", private_question, "-n", SYNTHETIC_NOTEBOOK_ID],
    )

    assert code == 64
    assert out == ""
    assert "fake rpc request not found" in err
    assert secret not in err
    assert synthetic_home not in err


@pytest.mark.parametrize(
    "argv",
    [
        [
            "ask",
            SYNTHETIC_QUESTION,
            "-n",
            SYNTHETIC_NOTEBOOK_ID,
            "--source",
            "source-1",
        ],
        ["ask", SYNTHETIC_QUESTION, "-n", SYNTHETIC_NOTEBOOK_ID, "--new", "--yes"],
        ["ask", SYNTHETIC_QUESTION, "-n", SYNTHETIC_NOTEBOOK_ID, "--save-as-note"],
    ],
)
def test_cli_ask_preserves_later_fixture_backed_promotions(
    repo_root, monkeypatch, capsys, argv
):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)

    code, out, err = _run(mods, capsys, argv)

    assert code == 0
    assert SYNTHETIC_ANSWER in out
    assert "later parity phase" not in err


def test_phase3a14_promotes_ask_and_preserves_later_root_promotions(
    repo_root, monkeypatch
):
    monkeypatch.syspath_prepend(str(repo_root))
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


def test_phase3a14_ask_flags_match_pinned_oracle(repo_root):
    cli_surface = json.loads(
        (repo_root / "compat" / "cli_surface.json").read_text(encoding="utf-8")
    )
    ask_node = next(
        node for node in cli_surface["nodes"] if node.get("command") == "notebooklm ask"
    )
    opts = {
        opt
        for param in ask_node["params"]
        for opt in param.get("opts", [])
        if opt.startswith("-")
    }

    expected_opts = {
        "--prompt-file",
        "-n",
        "--notebook",
        "--conversation-id",
        "-c",
        "--new",
        "--yes",
        "-y",
        "--source",
        "-s",
        "--json",
        "--save-as-note",
        "-t",
        "--note-title",
        "--request-timeout",
        "--timeout",
    }
    assert opts == expected_opts


def test_phase3a14_live_ask_parser_exposes_oracle_flags(repo_root, monkeypatch, capsys):
    mods = _load(repo_root, monkeypatch)

    with pytest.raises(SystemExit) as excinfo:
        mods.cli.console(["ask", "--help"])

    assert excinfo.value.code == 0
    help_text = capsys.readouterr().out
    for option in {
        "--prompt-file",
        "--notebook",
        "--conversation-id",
        "--new",
        "--yes",
        "--source",
        "--json",
        "--save-as-note",
        "--note-title",
        "--request-timeout",
        "--timeout",
    }:
        assert option in help_text
    assert "--fixture-dir" not in help_text


def test_phase3a14_cli_ask_wiring_is_stdlib_and_offline_only(repo_root):
    assert (
        import_origin_audit.audit(
            roots=("notebooklm/cli.py", "notebooklm/fake_rpc.py", "notebooklm/chat.py"),
        )
        == []
    )
    src = "\n".join(
        (repo_root / "notebooklm" / name).read_text(encoding="utf-8")
        for name in ("chat.py", "fake_rpc.py")
    )
    forbidden = {
        "socket",
        "http.client",
        "urllib.request",
        "urlopen",
        "subprocess",
        "Path.home",
        "expanduser",
        "os.environ",
        "browser_cookies",
        "interactive_login",
        "Network.",
        "DevTools",
        "keyring",
        "secretstorage",
        "win32crypt",
        "browser_cookie3",
        "browsercookie",
    }
    assert sorted(token for token in forbidden if token in src) == []
