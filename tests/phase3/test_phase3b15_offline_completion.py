"""Phase 3B15 offline ``completion`` CLI parity.

This batch promotes the pinned notebooklm-py==0.7.2 ``completion`` command
as static shell-completion script output. It does not read auth/browser/home
state, make live RPC/network calls, or mutate NotebookLM data.
"""

from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

EXPECTED_COMPLETION_SHA256 = {
    "bash": "c5633c763c6615cd2c3ed7fdbef90bdcb08113cc2670e86514739564bcdf602a",
    "zsh": "2de7c7463acfea181b4dc35ef59bd88a81d6ccbba2a2320076ba5652eb63966d",
    "fish": "85a1718c2d37933f611bb7616178d72c0646f5f2a1653aae17fe8ac6832dc894",
}


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


def _committed_completion_script(repo_root, shell: str) -> str:
    data_dir = repo_root / "notebooklm" / "data"
    text_path = data_dir / f"completion_{shell}.txt"
    if text_path.exists():
        return text_path.read_text(encoding="utf-8")
    return json.loads(
        (data_dir / f"completion_{shell}.json").read_text(encoding="utf-8")
    )


@pytest.mark.parametrize(
    ("shell", "first_marker"),
    [
        ("bash", "_notebooklm_completion()"),
        ("zsh", "#compdef notebooklm"),
        ("fish", "function _notebooklm_completion;"),
    ],
)
def test_cli_completion_outputs_committed_pinned_script(
    mods, capsys, repo_root, shell, first_marker
):
    assert "completion" in mods.cli.IMPLEMENTED_COMMANDS

    code, out, err = _run(mods, capsys, ["completion", shell])

    assert code == 0
    assert err == ""
    expected = _committed_completion_script(repo_root, shell)
    assert out == expected.rstrip() + "\n"
    assert _sha256_text(out.rstrip() + "\n") == EXPECTED_COMPLETION_SHA256[shell]
    assert first_marker in out
    assert "_NOTEBOOKLM_COMPLETE" in out


def test_cli_completion_help_matches_pinned_surface(mods, capsys):
    with pytest.raises(SystemExit) as excinfo:
        mods.cli.console(["completion", "--help"])
    assert excinfo.value.code == 0
    help_text = capsys.readouterr().out
    assert "Print the shell completion script for SHELL." in help_text
    assert "{bash|zsh|fish}" in help_text
    assert "notebooklm completion bash" in help_text
    assert "notebooklm completion zsh" in help_text
    assert "notebooklm completion fish" in help_text


def test_cli_completion_rejects_missing_or_unknown_shell(mods, capsys):
    code, out, err = _run(mods, capsys, ["completion"])
    assert code == 64
    assert out == ""
    assert "shell" in err.lower()
    assert "bash" in err and "zsh" in err and "fish" in err

    code, out, err = _run(mods, capsys, ["completion", "powershell"])
    assert code == 64
    assert out == ""
    assert "bash" in err and "zsh" in err and "fish" in err
