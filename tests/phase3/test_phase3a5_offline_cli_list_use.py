"""Phase 3A5 offline CLI list/use wiring over the fake RPC seam.

This slice is still fixture-only. It promotes only ``notebooklm list`` and the
existing ``notebooklm use`` command to resolve against the committed synthetic
list-notebooks fixture by default. It does not enter live NotebookLM RPC, read
browser/auth/credential state, mutate remote notebooks, or promote parity rows.
"""

from __future__ import annotations

import importlib
import json
import types
from pathlib import Path

import pytest

import import_origin_audit  # noqa: E402  (placed on sys.path by tests/conftest.py)

EXPECTED_NOTEBOOK_DICT = {
    "created_at": "2025-06-15T15:06:40+00:00",
    "id": "fake-notebook-0001",
    "is_owner": True,
    "sources_count": 2,
    "title": "Phase 0 Synthetic Notebook",
}


@pytest.fixture
def mods(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))
    return types.SimpleNamespace(
        cli=importlib.import_module("notebooklm.cli"),
        profiles=importlib.import_module("notebooklm.profiles"),
    )


@pytest.fixture
def home(tmp_path) -> Path:
    return tmp_path / "nlm-home"


def _run(mods, capsys, argv):
    code = mods.cli.console(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


def _poison_home(monkeypatch):
    monkeypatch.setattr(
        Path,
        "home",
        staticmethod(lambda: (_ for _ in ()).throw(AssertionError("real home access"))),
    )


def test_cli_list_json_uses_committed_fixture_without_profile_home(
    mods, capsys, monkeypatch
):
    _poison_home(monkeypatch)

    code, out, err = _run(mods, capsys, ["list", "--json"])

    assert code == 0
    assert err == ""
    expected = {
        "notebooks": [
            {
                "index": 1,
                "id": EXPECTED_NOTEBOOK_DICT["id"],
                "title": EXPECTED_NOTEBOOK_DICT["title"],
                "is_owner": EXPECTED_NOTEBOOK_DICT["is_owner"],
                "created_at": EXPECTED_NOTEBOOK_DICT["created_at"],
            }
        ],
        "count": 1,
    }
    assert json.loads(out) == expected


def test_cli_list_accepts_limit_and_no_truncate(mods, capsys):
    code, out, err = _run(
        mods, capsys, ["list", "--limit", "0", "--no-truncate", "--json"]
    )

    assert code == 0
    assert err == ""
    assert json.loads(out) == {"notebooks": [], "count": 0}


def test_cli_use_resolves_fixture_selector_and_persists_verified_context(
    mods, home, capsys
):
    code, out, err = _run(
        mods, capsys, ["--storage", str(home), "use", "fake-notebook", "--json"]
    )

    assert code == 0
    assert err == ""
    payload = json.loads(out)
    assert payload == {
        "profile": "default",
        "notebook_id": "fake-notebook-0001",
        "notebook_title": "Phase 0 Synthetic Notebook",
        "verified": True,
    }

    code, out, _ = _run(mods, capsys, ["--storage", str(home), "status", "--json"])
    assert code == 0
    assert json.loads(out) == {
        "conversation_id": None,
        "has_context": True,
        "notebook": {
            "id": "fake-notebook-0001",
            "is_owner": True,
            "title": "Phase 0 Synthetic Notebook",
        },
    }


def test_cli_use_rejects_missing_selector_unless_forced(mods, home, capsys):
    code, _, err = _run(
        mods, capsys, ["--storage", str(home), "use", "missing-private-selector"]
    )

    assert code == 64
    assert "notebook selector not found" in err
    assert "missing-private-selector" not in err

    code, out, err = _run(
        mods,
        capsys,
        [
            "--storage",
            str(home),
            "use",
            "missing-private-selector",
            "--force",
            "--json",
        ],
    )

    assert code == 0
    assert err == ""
    payload = json.loads(out)
    assert payload["notebook_id"] == "missing-private-selector"
    assert payload["verified"] is False


def test_phase3a5_preserves_later_notebook_command_promotions(mods):
    assert "list" in mods.cli.IMPLEMENTED_COMMANDS
    assert "use" in mods.cli.IMPLEMENTED_COMMANDS
    assert "metadata" in mods.cli.IMPLEMENTED_COMMANDS
    assert "summary" in mods.cli.IMPLEMENTED_COMMANDS
    assert "share" in mods.cli.IMPLEMENTED_COMMANDS
    assert {"create", "delete", "rename"} <= set(mods.cli.IMPLEMENTED_COMMANDS)


def test_phase3a5_cli_wiring_has_no_denylisted_imports():
    assert (
        import_origin_audit.audit(
            roots=(
                "notebooklm/cli.py",
                "notebooklm/fake_rpc.py",
                "notebooklm/notebooks.py",
            ),
        )
        == []
    )
