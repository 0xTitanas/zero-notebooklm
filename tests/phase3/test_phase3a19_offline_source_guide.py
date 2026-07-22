"""Phase 3A19 fixture-backed source guide promotion.

This slice promotes only read-only ``source guide`` over committed synthetic
source fixtures. It does not promote source mutation/wait/refresh,
generation/download, live RPC, auth/browser/home reads, credentials,
NotebookLM mutation, public sharing, or parity rows.
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
EXPECTED_SUMMARY = (
    "Synthetic source guide for Synthetic Web Source from "
    "https://example.test/notebooklm-bare/source."
)
EXPECTED_KEYWORDS = ["synthetic", "web", "source", "notebooklm-bare"]


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


def test_sources_api_get_guide_returns_synthetic_summary_and_keywords(
    repo_root, monkeypatch
):
    monkeypatch.syspath_prepend(str(repo_root))
    from notebooklm.client import NotebookLMClient
    from notebooklm.auth import AuthTokens

    client = NotebookLMClient(AuthTokens(cookies={}, csrf_token="", session_id=""))

    guide = asyncio.run(
        client.sources.get_guide(SYNTHETIC_NOTEBOOK_ID, SYNTHETIC_SOURCE_ID)
    )

    assert type(guide).__module__ == "notebooklm.types"
    assert guide.as_dict() == {
        "summary": EXPECTED_SUMMARY,
        "keywords": EXPECTED_KEYWORDS,
    }


def test_sources_api_get_guide_missing_source_fails_closed(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))
    from notebooklm.client import NotebookLMClient
    from notebooklm.auth import AuthTokens
    from notebooklm.errors import ValidationError

    client = NotebookLMClient(AuthTokens(cookies={}, csrf_token="", session_id=""))

    with pytest.raises(ValidationError, match="source not found"):
        asyncio.run(client.sources.get_guide(SYNTHETIC_NOTEBOOK_ID, "missing-source"))


def test_cli_source_guide_json_uses_committed_fixtures_without_home(
    repo_root, monkeypatch, capsys
):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)

    code, out, err = _run(
        mods,
        capsys,
        [
            "source",
            "guide",
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
    assert data["summary"] == EXPECTED_SUMMARY
    assert data["keywords"] == EXPECTED_KEYWORDS
    assert data["kind"] == "WEB_PAGE"


def test_cli_source_guide_plain_output_is_human_readable(
    repo_root, monkeypatch, capsys
):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)

    code, out, err = _run(
        mods,
        capsys,
        ["source", "guide", SYNTHETIC_SOURCE_ID, "-n", "fake-notebook"],
    )

    assert code == 0
    assert err == ""
    assert "Source Guide: Synthetic Web Source" in out
    assert EXPECTED_SUMMARY in out
    assert "Keywords: synthetic, web, source, notebooklm-bare" in out


def test_cli_source_guide_missing_source_is_redacted(repo_root, monkeypatch, capsys):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)
    secret = "ya" + "29." + "G" * 40
    raw_path = "/".join(("", "Users", "example", "guide"))
    private_selector = f"missing {secret} {raw_path}"

    code, out, err = _run(mods, capsys, ["source", "guide", private_selector, "--json"])

    assert code == 64
    assert out == ""
    assert "source not found" in err
    assert secret not in err
    assert raw_path not in err


def test_phase3a19_source_guide_flags_match_pinned_oracle(repo_root):
    cli_surface = json.loads(
        (repo_root / "compat" / "cli_surface.json").read_text(encoding="utf-8")
    )
    guide_node = next(
        node
        for node in cli_surface["nodes"]
        if node.get("command") == "notebooklm source guide"
    )
    opts = {
        opt
        for param in guide_node["params"]
        for opt in param.get("opts", [])
        if opt.startswith("-")
    }

    assert opts == {"-n", "--notebook", "--json"}


def test_phase3a19_live_source_guide_parser_exposes_oracle_flags(
    repo_root, monkeypatch, capsys
):
    mods = _load(repo_root, monkeypatch)

    with pytest.raises(SystemExit) as excinfo:
        mods.cli.console(["source", "guide", "--help"])

    assert excinfo.value.code == 0
    help_text = capsys.readouterr().out
    for option in {"-n", "--notebook", "--json"}:
        assert option in help_text
    assert "--fixture-dir" not in help_text


def test_phase3a19_source_guide_wiring_is_stdlib_and_offline_only(repo_root):
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
