"""Phase 3A18 fixture-backed CLI/source fulltext promotion.

This slice promotes only read-only ``source fulltext`` over committed synthetic
source fixtures. It does not promote source mutation/wait, live RPC,
auth/browser/home reads, credentials, NotebookLM mutation, or parity rows.
"""

from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import import_origin_audit  # noqa: E402  (placed on sys.path by tests/conftest.py)

SYNTHETIC_NOTEBOOK_ID = "fake-notebook-0001"
SYNTHETIC_SOURCE_ID = "fake-source-0001"
SYNTHETIC_TITLE = "Synthetic Web Source"
EXPECTED_TEXT = "Synthetic full text for Synthetic Web Source from https://example.test/notebooklm-bare/source."
EXPECTED_MARKDOWN = "# Synthetic Web Source\n\nSynthetic full text for Synthetic Web Source from https://example.test/notebooklm-bare/source."


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


def test_sources_api_get_fulltext_returns_synthetic_text(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))
    from notebooklm.client import NotebookLMClient
    from notebooklm.auth import AuthTokens

    client = NotebookLMClient(AuthTokens(cookies={}, csrf_token="", session_id=""))

    fulltext = asyncio.run(
        client.sources.get_fulltext(SYNTHETIC_NOTEBOOK_ID, SYNTHETIC_SOURCE_ID)
    )

    assert fulltext.as_dict() == {
        "source_id": SYNTHETIC_SOURCE_ID,
        "title": SYNTHETIC_TITLE,
        "content": EXPECTED_TEXT,
        "type_code": 1,
        "url": "https://example.test/notebooklm-bare/source",
        "char_count": len(EXPECTED_TEXT),
        "kind": "WEB_PAGE",
    }


def test_sources_api_get_fulltext_markdown_format(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))
    from notebooklm.client import NotebookLMClient
    from notebooklm.auth import AuthTokens

    client = NotebookLMClient(AuthTokens(cookies={}, csrf_token="", session_id=""))

    fulltext = asyncio.run(
        client.sources.get_fulltext(
            SYNTHETIC_NOTEBOOK_ID, SYNTHETIC_SOURCE_ID, output_format="markdown"
        )
    )

    assert fulltext.content == EXPECTED_MARKDOWN
    assert fulltext.char_count == len(EXPECTED_MARKDOWN)


def test_cli_source_fulltext_json_uses_committed_fixtures_without_home(
    repo_root, monkeypatch, capsys
):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)

    code, out, err = _run(
        mods,
        capsys,
        [
            "source",
            "fulltext",
            SYNTHETIC_SOURCE_ID,
            "--notebook",
            SYNTHETIC_NOTEBOOK_ID,
            "--json",
        ],
    )

    assert code == 0
    assert err == ""
    data = json.loads(out)
    assert data["source_id"] == SYNTHETIC_SOURCE_ID
    assert data["title"] == SYNTHETIC_TITLE
    assert data["content"] == EXPECTED_TEXT
    assert data["kind"] == "WEB_PAGE"


def test_cli_source_fulltext_plain_markdown_output(repo_root, monkeypatch, capsys):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)

    code, out, err = _run(
        mods,
        capsys,
        [
            "source",
            "fulltext",
            SYNTHETIC_SOURCE_ID,
            "-n",
            "fake-notebook",
            "--format",
            "markdown",
        ],
    )

    assert code == 0
    assert err == ""
    assert out == EXPECTED_MARKDOWN + "\n"


def test_cli_source_fulltext_output_file_json_envelope(
    repo_root, monkeypatch, capsys, tmp_path
):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)
    output_path = tmp_path / "fulltext.txt"

    code, out, err = _run(
        mods,
        capsys,
        [
            "source",
            "fulltext",
            SYNTHETIC_SOURCE_ID,
            "-n",
            SYNTHETIC_NOTEBOOK_ID,
            "--output",
            str(output_path),
            "--json",
        ],
    )

    assert code == 0
    assert err == ""
    assert output_path.read_text(encoding="utf-8") == EXPECTED_TEXT
    envelope = json.loads(out)
    assert envelope["source_id"] == SYNTHETIC_SOURCE_ID
    assert envelope["title"] == SYNTHETIC_TITLE
    assert envelope["kind"] == "WEB_PAGE"
    assert envelope["bytes"] == len(EXPECTED_TEXT.encode("utf-8"))
    assert envelope["path"].endswith("fulltext.txt")


def test_cli_source_fulltext_no_clobber_refuses_existing_file(
    repo_root, monkeypatch, capsys, tmp_path
):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)
    output_path = tmp_path / "fulltext.txt"
    output_path.write_text("existing", encoding="utf-8")

    code, out, err = _run(
        mods,
        capsys,
        [
            "source",
            "fulltext",
            SYNTHETIC_SOURCE_ID,
            "-o",
            str(output_path),
            "--no-clobber",
        ],
    )

    assert code == 64
    assert out == ""
    assert "output file exists" in err
    assert output_path.read_text(encoding="utf-8") == "existing"


def test_cli_source_fulltext_auto_renames_existing_file(
    repo_root, monkeypatch, capsys, tmp_path
):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)
    output_path = tmp_path / "fulltext.txt"
    output_path.write_text("existing", encoding="utf-8")

    code, out, err = _run(
        mods,
        capsys,
        ["source", "fulltext", SYNTHETIC_SOURCE_ID, "-o", str(output_path), "--json"],
    )

    assert code == 0
    assert err == ""
    renamed_path = tmp_path / "fulltext-1.txt"
    assert output_path.read_text(encoding="utf-8") == "existing"
    assert renamed_path.read_text(encoding="utf-8") == EXPECTED_TEXT
    envelope = json.loads(out)
    assert envelope["path"] == str(renamed_path)
    assert envelope["bytes"] == len(EXPECTED_TEXT.encode("utf-8"))


def test_cli_source_fulltext_force_overwrites_existing_file(
    repo_root, monkeypatch, capsys, tmp_path
):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)
    output_path = tmp_path / "fulltext.txt"
    output_path.write_text("existing", encoding="utf-8")

    code, out, err = _run(
        mods,
        capsys,
        [
            "source",
            "fulltext",
            SYNTHETIC_SOURCE_ID,
            "-o",
            str(output_path),
            "--force",
            "--json",
        ],
    )

    assert code == 0
    assert err == ""
    assert output_path.read_text(encoding="utf-8") == EXPECTED_TEXT
    assert json.loads(out)["path"].endswith("fulltext.txt")


def test_cli_source_fulltext_missing_source_is_redacted(repo_root, monkeypatch, capsys):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)
    secret = "ya" + "29." + "U" * 40
    raw_path = "/".join(("", "Users", "example", "source"))
    private_selector = f"missing {secret} {raw_path}"

    code, out, err = _run(
        mods, capsys, ["source", "fulltext", private_selector, "--json"]
    )

    assert code == 64
    assert out == ""
    assert "source not found" in err
    assert secret not in err
    assert raw_path not in err


def test_phase3a18_source_fulltext_flags_match_pinned_oracle(repo_root):
    cli_surface = json.loads(
        (repo_root / "compat" / "cli_surface.json").read_text(encoding="utf-8")
    )
    fulltext_node = next(
        node
        for node in cli_surface["nodes"]
        if node.get("command") == "notebooklm source fulltext"
    )
    opts = {
        opt
        for param in fulltext_node["params"]
        for opt in param.get("opts", [])
        if opt.startswith("-")
    }

    assert opts == {
        "-n",
        "--notebook",
        "--json",
        "-o",
        "--output",
        "--no-clobber",
        "--force",
        "-f",
        "--format",
    }


def test_phase3a18_live_source_fulltext_parser_exposes_oracle_flags(
    repo_root, monkeypatch, capsys
):
    mods = _load(repo_root, monkeypatch)

    with pytest.raises(SystemExit) as excinfo:
        mods.cli.console(["source", "fulltext", "--help"])

    assert excinfo.value.code == 0
    help_text = capsys.readouterr().out
    for option in {
        "-n",
        "--notebook",
        "--json",
        "-o",
        "--output",
        "--no-clobber",
        "--force",
        "-f",
        "--format",
    }:
        assert option in help_text
    assert "--fixture-dir" not in help_text


def test_phase3a18_source_fulltext_wiring_is_stdlib_and_offline_only(repo_root):
    assert (
        import_origin_audit.audit(
            roots=(
                "notebooklm/cli.py",
                "notebooklm/fake_rpc.py",
                "notebooklm/sources.py",
            ),
        )
        == []
    )
    src = "\n".join(
        (repo_root / "notebooklm" / name).read_text(encoding="utf-8")
        for name in ("fake_rpc.py", "sources.py")
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
