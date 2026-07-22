"""Phase 3A15 fixture-backed CLI metadata promotion.

This slice promotes only read-only ``notebooklm metadata`` over the committed
synthetic notebook/source fixtures. It reuses the reviewed offline fake RPC seam
and keeps summary, history cache operations, live RPC, auth/browser/home reads,
credentials, NotebookLM mutation, and parity-row promotion out of scope.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import import_origin_audit  # noqa: E402  (placed on sys.path by tests/conftest.py)

SYNTHETIC_NOTEBOOK_ID = "fake-notebook-0001"
EXPECTED_METADATA = {
    "notebook": {
        "created_at": "2025-06-15T15:06:40+00:00",
        "id": SYNTHETIC_NOTEBOOK_ID,
        "is_owner": True,
        "sources_count": 2,
        "title": "Phase 0 Synthetic Notebook",
    },
    "sources": [
        {
            "kind": "WEB_PAGE",
            "title": "Synthetic Web Source",
            "url": "https://example.test/notebooklm-bare/source",
        },
        {
            "kind": "PASTED_TEXT",
            "title": "Synthetic Pasted Text Source",
            "url": None,
        },
    ],
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


def test_cli_metadata_json_uses_committed_fixtures_without_home(
    repo_root, monkeypatch, capsys
):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)

    code, out, err = _run(
        mods,
        capsys,
        ["metadata", "--notebook", SYNTHETIC_NOTEBOOK_ID, "--json"],
    )

    assert code == 0
    assert err == ""
    assert json.loads(out) == EXPECTED_METADATA


def test_cli_metadata_defaults_to_first_synthetic_notebook(
    repo_root, monkeypatch, capsys
):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)

    code, out, err = _run(mods, capsys, ["metadata", "--json"])

    assert code == 0
    assert err == ""
    assert json.loads(out) == EXPECTED_METADATA


def test_cli_metadata_plain_output_contains_notebook_and_source_summaries(
    repo_root, monkeypatch, capsys
):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)

    code, out, err = _run(
        mods,
        capsys,
        [
            "metadata",
            "-n",
            "fake-notebook",
            "--fixture-dir",
            str(repo_root / "compat" / "rpc_fixtures"),
        ],
    )

    assert code == 0
    assert err == ""
    assert "notebook:" in out
    assert "title: Phase 0 Synthetic Notebook" in out
    assert "sources:" in out
    assert "kind: WEB_PAGE" in out
    assert "Synthetic Pasted Text Source" in out


def test_cli_metadata_missing_selector_is_redacted(repo_root, monkeypatch, capsys):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)
    secret = "ya" + "29." + "S" * 40
    synthetic_home = "/".join(("", "Users", "example"))
    private_selector = f"private {secret} {synthetic_home}/notebook"

    code, out, err = _run(mods, capsys, ["metadata", "-n", private_selector, "--json"])

    assert code == 64
    assert out == ""
    assert "notebook selector not found" in err
    assert secret not in err
    assert synthetic_home not in err


def test_cli_metadata_allows_fixture_backed_research_wait_import(
    repo_root, monkeypatch, capsys
):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)

    code, out, err = _run(
        mods,
        capsys,
        ["research", "wait", "-n", SYNTHETIC_NOTEBOOK_ID, "--import-all", "--json"],
    )

    assert code == 0
    assert err == ""
    payload = json.loads(out)
    assert payload["imported"] == 1
    assert payload["imported_sources"][0]["title"] == "Synthetic research source one"


def test_phase3a15_promotes_metadata_but_keeps_generation_closed(
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


def test_phase3a15_metadata_flags_match_pinned_oracle(repo_root):
    cli_surface = json.loads(
        (repo_root / "compat" / "cli_surface.json").read_text(encoding="utf-8")
    )
    metadata_node = next(
        node
        for node in cli_surface["nodes"]
        if node.get("command") == "notebooklm metadata"
    )
    opts = {
        opt
        for param in metadata_node["params"]
        for opt in param.get("opts", [])
        if opt.startswith("-")
    }

    assert opts == {"-n", "--notebook", "--json"}


def test_phase3a15_live_metadata_parser_exposes_oracle_flags(
    repo_root, monkeypatch, capsys
):
    mods = _load(repo_root, monkeypatch)

    with pytest.raises(SystemExit) as excinfo:
        mods.cli.console(["metadata", "--help"])

    assert excinfo.value.code == 0
    help_text = capsys.readouterr().out
    for option in {"-n", "--notebook", "--json"}:
        assert option in help_text
    assert "--fixture-dir" not in help_text


def test_phase3a15_cli_metadata_wiring_is_stdlib_and_offline_only(repo_root):
    assert (
        import_origin_audit.audit(
            roots=(
                "notebooklm/cli.py",
                "notebooklm/fake_rpc.py",
                "notebooklm/notebooks.py",
                "notebooklm/sources.py",
            ),
        )
        == []
    )
    src = "\n".join(
        (repo_root / "notebooklm" / name).read_text(encoding="utf-8")
        for name in ("fake_rpc.py", "notebooks.py", "sources.py")
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
