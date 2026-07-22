"""Phase 3B5 grouped fixture-backed CLI/API parity promotion.

This batch opens root notebook mutation leaves, fixture-backed language set/local
get semantics, and the remaining safe source mutation/cleanup leaves without
reading real home/auth/browser state, contacting live RPC, uploading files, or
mutating real NotebookLM data.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SYNTHETIC_NOTEBOOK_ID = "fake-notebook-0001"
SYNTHETIC_SOURCE_ID = "fake-source-0001"


def _load(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))
    return SimpleNamespace(cli=importlib.import_module("notebooklm.cli"))


def _poison_home(monkeypatch):
    monkeypatch.setattr(
        Path,
        "home",
        staticmethod(lambda: (_ for _ in ()).throw(AssertionError("real home access"))),
    )


def _run(mods, capsys, argv):
    code = mods.cli.console(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.fixture()
def mods(repo_root, monkeypatch):
    _poison_home(monkeypatch)
    return _load(repo_root, monkeypatch)


def test_phase3b5_command_set_is_promoted(mods):
    assert {"create", "delete", "rename", "language", "source"} <= set(
        mods.cli.IMPLEMENTED_COMMANDS
    )
    assert "source" in mods.cli.IMPLEMENTED_COMMANDS


def test_phase3b5_language_get_local_uses_fixture(mods, capsys):
    code, out, err = _run(mods, capsys, ["language", "get", "--local", "--json"])

    assert code == 0
    assert err == ""
    assert json.loads(out) == {
        "language": "ja",
        "name": "日本語",
        "is_default": False,
        "synced_from_server": False,
    }


def test_phase3b5_language_set_server_json_is_fixture_backed(mods, capsys):
    code, out, err = _run(mods, capsys, ["language", "set", "es", "--json"])

    assert code == 0
    assert err == ""
    assert json.loads(out) == {
        "language": "es",
        "name": "Español",
        "message": "Language set successfully",
        "synced_to_server": True,
    }


def test_phase3b5_language_set_local_json_skips_server_sync(mods, capsys):
    code, out, err = _run(
        mods, capsys, ["language", "set", "zh_Hans", "--local", "--json"]
    )

    assert code == 0
    assert err == ""
    assert json.loads(out) == {
        "language": "zh_Hans",
        "name": "中文（简体）",
        "message": "Language set successfully",
        "synced_to_server": False,
    }


def test_phase3b5_root_create_rename_delete_are_fixture_backed(
    mods, capsys, monkeypatch
):
    code, out, err = _run(mods, capsys, ["create", "Scratch Notebook", "--json"])
    assert code == 0
    assert err == ""
    created = json.loads(out)
    assert created["notebook"]["id"].startswith("offline-notebook-")
    assert created["notebook"]["title"] == "Scratch Notebook"

    code, out, err = _run(
        mods,
        capsys,
        ["rename", "Renamed Notebook", "-n", SYNTHETIC_NOTEBOOK_ID, "--json"],
    )
    assert code == 0
    assert err == ""
    assert json.loads(out) == {
        "notebook_id": SYNTHETIC_NOTEBOOK_ID,
        "title": "Renamed Notebook",
        "success": True,
    }

    code, out, err = _run(
        mods,
        capsys,
        ["delete", "-n", SYNTHETIC_NOTEBOOK_ID, "--yes", "--json"],
    )
    assert code == 0
    assert err == ""
    assert json.loads(out) == {"notebook_id": SYNTHETIC_NOTEBOOK_ID, "success": True}

    monkeypatch.setenv("NOTEBOOKLM_NOTEBOOK", SYNTHETIC_NOTEBOOK_ID)
    code, out, err = _run(mods, capsys, ["rename", "Env Selected Notebook", "--json"])
    assert code == 0
    assert err == ""
    assert json.loads(out) == {
        "notebook_id": SYNTHETIC_NOTEBOOK_ID,
        "title": "Env Selected Notebook",
        "success": True,
    }

    code, out, err = _run(mods, capsys, ["delete", "--yes", "--json"])
    assert code == 0
    assert err == ""
    assert json.loads(out) == {"notebook_id": SYNTHETIC_NOTEBOOK_ID, "success": True}


def test_phase3b5_root_delete_requires_yes(mods, capsys):
    code, out, err = _run(
        mods, capsys, ["delete", "-n", SYNTHETIC_NOTEBOOK_ID, "--json"]
    )

    assert code == 64
    assert out == ""
    assert "pass --yes" in err


def test_phase3b5_source_add_url_and_text_are_fixture_backed(mods, capsys):
    code, out, err = _run(
        mods,
        capsys,
        ["source", "add", "https://example.test/new-source", "--json"],
    )
    assert code == 0
    assert err == ""
    url_payload = json.loads(out)
    assert url_payload["source_type"] == "url"
    assert url_payload["source"]["url"] == "https://example.test/new-source"

    code, out, err = _run(
        mods,
        capsys,
        [
            "source",
            "add",
            "Inline body",
            "--type",
            "text",
            "--title",
            "Inline Title",
            "--json",
        ],
    )
    assert code == 0
    assert err == ""
    text_payload = json.loads(out)
    assert text_payload["source_type"] == "text"
    assert text_payload["source"]["title"] == "Inline Title"


def test_phase3b5_source_add_drive_is_fixture_backed(mods, capsys):
    code, out, err = _run(
        mods,
        capsys,
        [
            "source",
            "add-drive",
            "drive-file-123",
            "Drive Title",
            "--mime-type",
            "google-slides",
            "--json",
        ],
    )

    assert code == 0
    assert err == ""
    payload = json.loads(out)
    assert payload["added"] is True
    assert payload["source"]["title"] == "Drive Title"
    assert payload["source"]["url"] == "gdrive://drive-file-123"
    assert payload["source"]["type_code"] == 4
    assert payload["source"]["status"] == "READY"


def test_phase3b5_source_delete_by_title_requires_yes_and_deletes(mods, capsys):
    code, out, err = _run(
        mods,
        capsys,
        ["source", "delete-by-title", "Synthetic Web Source", "--json"],
    )
    assert code == 64
    assert out == ""
    assert "pass --yes" in err

    code, out, err = _run(
        mods,
        capsys,
        ["source", "delete-by-title", "Synthetic Web Source", "--yes", "--json"],
    )
    assert code == 0
    assert err == ""
    assert json.loads(out) == {
        "source_id": SYNTHETIC_SOURCE_ID,
        "title": "Synthetic Web Source",
        "notebook_id": SYNTHETIC_NOTEBOOK_ID,
        "deleted": True,
    }


def test_phase3b5_source_clean_and_research_wait_import_boundary(mods, capsys):
    code, out, err = _run(mods, capsys, ["source", "clean", "--dry-run", "--json"])
    assert code == 0
    assert err == ""
    assert json.loads(out) == {
        "candidates": [],
        "deleted": 0,
        "dry_run": True,
        "total": 0,
    }

    code, out, err = _run(
        mods,
        capsys,
        ["research", "wait", "-n", SYNTHETIC_NOTEBOOK_ID, "--import-all", "--json"],
    )
    assert code == 0
    assert err == ""
    assert json.loads(out)["imported"] == 1
