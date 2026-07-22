"""Phase 3A11 fixture-backed CLI source list/get promotion.

This historical slice promoted read-only ``notebooklm source list`` and
``notebooklm source get`` over committed synthetic source fixtures. Later Phase 3
batches promote additional source leaves; this file still verifies the original
list/get contract and keeps live research/RPC, auth/browser/home reads,
credentials, and real NotebookLM mutation closed.
"""

from __future__ import annotations

import importlib
import json
import types
from pathlib import Path

import import_origin_audit  # noqa: E402  (placed on sys.path by tests/conftest.py)

SYNTHETIC_NOTEBOOK_ID = "fake-notebook-0001"
EXPECTED_SOURCES = [
    {
        "id": "fake-source-0001",
        "title": "Synthetic Web Source",
        "url": "https://example.test/notebooklm-bare/source",
        "type_code": 1,
        "created_at": "2025-06-15T15:08:20+00:00",
        "status": "READY",
    },
    {
        "id": "fake-source-0002",
        "title": "Synthetic Pasted Text Source",
        "url": None,
        "type_code": 2,
        "created_at": "2025-06-15T15:10:00+00:00",
        "status": "READY",
    },
]


def _poison_home(monkeypatch):
    monkeypatch.setattr(
        Path,
        "home",
        staticmethod(lambda: (_ for _ in ()).throw(AssertionError("real home access"))),
    )


def _run(mods, capsys, argv):
    code = mods.cli.console(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_cli_source_list_json_uses_committed_fixture_without_profile_home(
    repo_root, monkeypatch, capsys
):
    monkeypatch.syspath_prepend(str(repo_root))
    mods = types.SimpleNamespace(cli=importlib.import_module("notebooklm.cli"))
    _poison_home(monkeypatch)

    code, out, err = _run(mods, capsys, ["source", "list", "--json"])

    assert code == 0
    assert err == ""
    assert json.loads(out) == EXPECTED_SOURCES


def test_cli_source_list_accepts_notebook_limit_and_no_truncate(
    repo_root, monkeypatch, capsys
):
    monkeypatch.syspath_prepend(str(repo_root))
    mods = types.SimpleNamespace(cli=importlib.import_module("notebooklm.cli"))

    code, out, err = _run(
        mods,
        capsys,
        [
            "source",
            "list",
            "--notebook",
            SYNTHETIC_NOTEBOOK_ID,
            "--limit",
            "1",
            "--no-truncate",
            "--json",
        ],
    )

    assert code == 0
    assert err == ""
    assert json.loads(out) == EXPECTED_SOURCES[:1]


def test_cli_source_get_json_uses_committed_fixture_without_profile_home(
    repo_root, monkeypatch, capsys
):
    monkeypatch.syspath_prepend(str(repo_root))
    mods = types.SimpleNamespace(cli=importlib.import_module("notebooklm.cli"))
    _poison_home(monkeypatch)

    code, out, err = _run(mods, capsys, ["source", "get", "fake-source-0002", "--json"])

    assert code == 0
    assert err == ""
    assert json.loads(out) == EXPECTED_SOURCES[1]


def test_cli_source_get_missing_selector_is_redacted(repo_root, monkeypatch, capsys):
    monkeypatch.syspath_prepend(str(repo_root))
    mods = types.SimpleNamespace(cli=importlib.import_module("notebooklm.cli"))
    synthetic_home = "/".join(("", "Users", "example"))
    private_source = "private ya" + "29." + "S" * 40 + f" {synthetic_home}/source"

    code, out, err = _run(mods, capsys, ["source", "get", private_source, "--json"])

    assert code == 64
    assert out == ""
    assert "source not found" in err
    assert "ya29." not in err
    assert synthetic_home not in err


def test_cli_source_wait_import_workflow_is_fixture_backed(
    repo_root, monkeypatch, capsys
):
    monkeypatch.syspath_prepend(str(repo_root))
    mods = types.SimpleNamespace(cli=importlib.import_module("notebooklm.cli"))

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


def test_phase3a11_promotes_source_note_and_artifact_roots_not_chat(
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
        "summary",
    } <= set(cli.IMPLEMENTED_COMMANDS)
    assert "download" in cli.IMPLEMENTED_COMMANDS
    assert "generate" in cli.IMPLEMENTED_COMMANDS


def test_phase3a11_cli_source_wiring_is_stdlib_and_offline_only(repo_root):
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
