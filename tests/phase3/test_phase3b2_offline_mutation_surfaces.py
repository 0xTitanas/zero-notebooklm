"""Batch 3B2: offline fixture-backed mutation surfaces.

These tests deliberately exercise only in-memory/synthetic mutation seams. They do
not authorize live NotebookLM RPC, real notebook/source/note/artifact mutation,
public sharing, browser/home reads, or credential access.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import inspect
import json
from pathlib import Path

import pytest

from notebooklm.auth import AuthTokens


SYNTHETIC_NOTEBOOK_ID = "fake-notebook-0001"
SYNTHETIC_SOURCE_ID = "fake-source-0001"
SYNTHETIC_NOTE_ID = "fake-note-0001"
SYNTHETIC_ARTIFACT_ID = "fake-artifact-audio-0001"
FIXTURE_FILES = (
    Path("compat/rpc_fixtures/wire_shape.json"),
    Path("compat/offline_status_fixtures/phase3b1_readonly_status.json"),
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _poison_home(monkeypatch, tmp_path: Path) -> None:
    poisoned = tmp_path / "poisoned-home"
    monkeypatch.setenv("HOME", str(poisoned))
    monkeypatch.setenv("NOTEBOOKLM_CONFIG", str(poisoned / "config.json"))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    _poison_home(monkeypatch, tmp_path)
    from notebooklm import NotebookLMClient

    auth = AuthTokens(cookies={}, csrf_token="", session_id="", storage_path=tmp_path)
    return NotebookLMClient(auth)


@pytest.fixture()
def cli_mods(tmp_path, monkeypatch):
    _poison_home(monkeypatch, tmp_path)
    modules = {}
    for name in [
        "notebooklm.cli",
        "notebooklm.notebooks",
        "notebooklm.notes",
        "notebooklm.sources",
        "notebooklm.artifacts",
    ]:
        modules[name.rsplit(".", 1)[-1]] = importlib.reload(
            importlib.import_module(name)
        )
    return modules


def _run_cli(mods, capsys, argv):
    code = mods["cli"].main(argv)
    captured = capsys.readouterr()
    return code, captured.out.strip(), captured.err.strip()


def test_mutations_are_in_memory_and_do_not_rewrite_committed_fixtures(client):
    before = {str(path): _sha(path) for path in FIXTURE_FILES}

    created_note = asyncio.run(
        client.notes.create(SYNTHETIC_NOTEBOOK_ID, "Scratch", "Offline content")
    )
    asyncio.run(
        client.notes.update(
            SYNTHETIC_NOTEBOOK_ID, created_note.id, "Updated", "Scratch v2"
        )
    )
    assert (
        asyncio.run(client.notes.get(SYNTHETIC_NOTEBOOK_ID, created_note.id)).title
        == "Scratch v2"
    )
    asyncio.run(client.notes.delete(SYNTHETIC_NOTEBOOK_ID, created_note.id))
    assert (
        asyncio.run(client.notes.get_or_none(SYNTHETIC_NOTEBOOK_ID, created_note.id))
        is None
    )

    created_source = asyncio.run(
        client.sources.add_text(
            SYNTHETIC_NOTEBOOK_ID, "Offline Source", "Synthetic content", wait=True
        )
    )
    renamed_source = asyncio.run(
        client.sources.rename(
            SYNTHETIC_NOTEBOOK_ID, created_source.id, "Renamed Source"
        )
    )
    assert renamed_source is not None
    assert renamed_source.title == "Renamed Source"
    assert (
        asyncio.run(client.sources.refresh(SYNTHETIC_NOTEBOOK_ID, created_source.id))
        is True
    )
    asyncio.run(client.sources.delete(SYNTHETIC_NOTEBOOK_ID, created_source.id))
    assert (
        asyncio.run(
            client.sources.get_or_none(SYNTHETIC_NOTEBOOK_ID, created_source.id)
        )
        is None
    )

    created_notebook = asyncio.run(client.notebooks.create("Offline Notebook"))
    assert created_notebook.id.startswith("offline-notebook-")
    renamed_notebook = asyncio.run(
        client.notebooks.rename(created_notebook.id, "Renamed Notebook")
    )
    assert renamed_notebook.title == "Renamed Notebook"
    asyncio.run(client.notebooks.remove_from_recent(created_notebook.id))
    asyncio.run(client.notebooks.delete(created_notebook.id))
    assert asyncio.run(client.notebooks.get_or_none(created_notebook.id)) is None

    artifact = asyncio.run(
        client.artifacts.rename(
            SYNTHETIC_NOTEBOOK_ID, SYNTHETIC_ARTIFACT_ID, "Renamed Audio"
        )
    )
    assert artifact is not None
    assert artifact.title == "Renamed Audio"
    asyncio.run(
        client.artifacts.retry_failed(SYNTHETIC_NOTEBOOK_ID, SYNTHETIC_ARTIFACT_ID)
    )
    asyncio.run(client.artifacts.delete(SYNTHETIC_NOTEBOOK_ID, SYNTHETIC_ARTIFACT_ID))
    assert (
        asyncio.run(
            client.artifacts.get_or_none(SYNTHETIC_NOTEBOOK_ID, SYNTHETIC_ARTIFACT_ID)
        )
        is None
    )

    assert asyncio.run(client.settings.set_output_language("es")) == "es"
    assert asyncio.run(client.settings.get_output_language()) == "es"

    after = {str(path): _sha(path) for path in FIXTURE_FILES}
    assert after == before


def test_fixture_sharing_and_notebooks_share_wrapper_are_promoted(client):
    from notebooklm.rpc.types import ShareAccess, SharePermission

    public = asyncio.run(client.sharing.set_public(SYNTHETIC_NOTEBOOK_ID, True))
    assert public.is_public is True
    assert public.access is ShareAccess.ANYONE_WITH_LINK

    shared = asyncio.run(
        client.sharing.add_user(
            SYNTHETIC_NOTEBOOK_ID, "reader@example.com", SharePermission.VIEWER
        )
    )
    assert any(user.email == "reader@example.com" for user in shared.shared_users)

    wrapper = asyncio.run(client.notebooks.share(SYNTHETIC_NOTEBOOK_ID, public=True))
    assert wrapper == {
        "public": True,
        "url": "https://notebooklm.google.com/notebook/fake-notebook-0001",
        "artifact_id": None,
    }


def test_promoted_mutation_method_signatures_keep_stable_public_shapes():
    from notebooklm._artifacts_impl import ArtifactsAPI
    from notebooklm.client import NotebooksAPI, SettingsAPI
    from notebooklm.notes import NotesAPI
    from notebooklm.sources import SourcesAPI

    expected = {
        "notebooks.create": "(self, title: 'str') -> 'Notebook'",
        "notebooks.delete": "(self, notebook_id: 'str') -> 'None'",
        "notebooks.rename": "(self, notebook_id: 'str', new_title: 'str') -> 'Notebook'",
        "notes.create": "(self, notebook_id: 'str', title: 'str' = 'New Note', content: 'str' = '') -> 'Note'",
        "notes.delete": "(self, notebook_id: 'str', note_id: 'str') -> 'None'",
        "notes.update": "(self, notebook_id: 'str', note_id: 'str', content: 'str', title: 'str') -> 'None'",
        "sources.add_text": "(self, notebook_id: 'str', title: 'str', content: 'str', *, wait: 'bool' = False, wait_timeout: 'float' = 120.0, idempotent: 'bool' = False) -> 'Source'",
        "sources.add_url": "(self, notebook_id: 'str', url: 'str', *, wait: 'bool' = False, wait_timeout: 'float' = 120.0) -> 'Source'",
        "sources.delete": "(self, notebook_id: 'str', source_id: 'str') -> 'None'",
        "sources.refresh": "(self, notebook_id: 'str', source_id: 'str') -> 'bool'",
        "sources.rename": "(self, notebook_id: 'str', source_id: 'str', new_title: 'str', *, return_object: 'bool' = True) -> 'Source | None'",
        "artifacts.delete": "(self, notebook_id: 'str', artifact_id: 'str') -> 'None'",
        "artifacts.rename": "(self, notebook_id: 'str', artifact_id: 'str', new_title: 'str', *, return_object: 'bool' = True) -> 'Artifact | None'",
        "settings.set_output_language": "(self, language: 'str') -> 'str | None'",
    }
    actual = {
        "notebooks.create": str(inspect.signature(NotebooksAPI.create)),
        "notebooks.delete": str(inspect.signature(NotebooksAPI.delete)),
        "notebooks.rename": str(inspect.signature(NotebooksAPI.rename)),
        "notes.create": str(inspect.signature(NotesAPI.create)),
        "notes.delete": str(inspect.signature(NotesAPI.delete)),
        "notes.update": str(inspect.signature(NotesAPI.update)),
        "sources.add_text": str(inspect.signature(SourcesAPI.add_text)),
        "sources.add_url": str(inspect.signature(SourcesAPI.add_url)),
        "sources.delete": str(inspect.signature(SourcesAPI.delete)),
        "sources.refresh": str(inspect.signature(SourcesAPI.refresh)),
        "sources.rename": str(inspect.signature(SourcesAPI.rename)),
        "artifacts.delete": str(inspect.signature(ArtifactsAPI.delete)),
        "artifacts.rename": str(inspect.signature(ArtifactsAPI.rename)),
        "settings.set_output_language": str(
            inspect.signature(SettingsAPI.set_output_language)
        ),
    }
    assert actual == expected


def test_cli_root_notebook_mutations_are_fixture_backed(cli_mods, capsys):
    code, out, err = _run_cli(
        cli_mods,
        capsys,
        ["create", "Scratch Notebook", "--json"],
    )
    assert code == 0
    assert err == ""
    created = json.loads(out)
    assert created["notebook"]["id"].startswith("offline-notebook-")
    assert created["notebook"]["title"] == "Scratch Notebook"
    assert created["notebook"]["created_at"].startswith("1970-01-01T00:00:01")

    code, out, err = _run_cli(
        cli_mods,
        capsys,
        ["rename", "Renamed Synthetic Notebook", "-n", SYNTHETIC_NOTEBOOK_ID, "--json"],
    )
    assert code == 0
    assert err == ""
    assert json.loads(out) == {
        "notebook_id": SYNTHETIC_NOTEBOOK_ID,
        "title": "Renamed Synthetic Notebook",
        "success": True,
    }

    code, out, err = _run_cli(
        cli_mods,
        capsys,
        ["delete", "-n", SYNTHETIC_NOTEBOOK_ID, "--yes", "--json"],
    )
    assert code == 0
    assert err == ""
    assert json.loads(out) == {"notebook_id": SYNTHETIC_NOTEBOOK_ID, "success": True}


def test_cli_note_mutations_are_fixture_backed(cli_mods, capsys):
    code, out, err = _run_cli(
        cli_mods,
        capsys,
        [
            "note",
            "create",
            "Offline content",
            "--title",
            "Scratch",
            "-n",
            SYNTHETIC_NOTEBOOK_ID,
            "--json",
        ],
    )
    assert code == 0
    assert err == ""
    created = json.loads(out)
    assert created["created"] is True
    assert created["notebook_id"] == SYNTHETIC_NOTEBOOK_ID
    assert created["id"].startswith("offline-note-")

    code, out, err = _run_cli(
        cli_mods,
        capsys,
        [
            "note",
            "save",
            SYNTHETIC_NOTE_ID,
            "--title",
            "Renamed",
            "--content",
            "Updated",
            "-n",
            SYNTHETIC_NOTEBOOK_ID,
            "--json",
        ],
    )
    assert code == 0
    saved = json.loads(out)
    assert saved == {
        "id": SYNTHETIC_NOTE_ID,
        "notebook_id": SYNTHETIC_NOTEBOOK_ID,
        "saved": True,
        "title": "Renamed",
        "content": "Updated",
    }

    code, out, err = _run_cli(
        cli_mods,
        capsys,
        [
            "note",
            "rename",
            SYNTHETIC_NOTE_ID,
            "Renamed Again",
            "-n",
            SYNTHETIC_NOTEBOOK_ID,
            "--json",
        ],
    )
    assert code == 0
    assert json.loads(out)["renamed"] is True

    code, out, err = _run_cli(
        cli_mods,
        capsys,
        [
            "note",
            "delete",
            SYNTHETIC_NOTE_ID,
            "-n",
            SYNTHETIC_NOTEBOOK_ID,
            "--yes",
            "--json",
        ],
    )
    assert code == 0
    assert json.loads(out)["deleted"] is True


def test_cli_source_add_drive_delete_by_title_and_clean_are_fixture_backed(
    cli_mods, capsys
):
    code, out, err = _run_cli(
        cli_mods,
        capsys,
        [
            "source",
            "add",
            "https://example.test/new-source",
            "--type",
            "url",
            "-n",
            SYNTHETIC_NOTEBOOK_ID,
            "--json",
        ],
    )
    assert code == 0
    assert err == ""
    added_url = json.loads(out)
    assert added_url["added"] is True
    assert added_url["notebook_id"] == SYNTHETIC_NOTEBOOK_ID
    assert added_url["source"]["id"].startswith("offline-source-")
    assert added_url["source"]["title"] == "https://example.test/new-source"
    assert added_url["source"]["url"] == "https://example.test/new-source"

    code, out, err = _run_cli(
        cli_mods,
        capsys,
        [
            "source",
            "add",
            "Inline source body",
            "--type",
            "text",
            "--title",
            "Inline Source",
            "-n",
            SYNTHETIC_NOTEBOOK_ID,
            "--json",
        ],
    )
    assert code == 0
    assert json.loads(out)["source"]["title"] == "Inline Source"

    code, out, err = _run_cli(
        cli_mods,
        capsys,
        [
            "source",
            "add-drive",
            "drive-file-123",
            "Drive Source",
            "-n",
            SYNTHETIC_NOTEBOOK_ID,
            "--json",
        ],
    )
    assert code == 0
    drive = json.loads(out)
    assert drive["added"] is True
    assert drive["source"]["title"] == "Drive Source"
    assert drive["source"]["url"] == "gdrive://drive-file-123"

    code, out, err = _run_cli(
        cli_mods,
        capsys,
        [
            "source",
            "delete-by-title",
            "Synthetic Web Source",
            "-n",
            SYNTHETIC_NOTEBOOK_ID,
            "--yes",
            "--json",
        ],
    )
    assert code == 0
    assert json.loads(out) == {
        "source_id": SYNTHETIC_SOURCE_ID,
        "title": "Synthetic Web Source",
        "notebook_id": SYNTHETIC_NOTEBOOK_ID,
        "deleted": True,
    }

    code, out, err = _run_cli(
        cli_mods,
        capsys,
        ["source", "clean", "-n", SYNTHETIC_NOTEBOOK_ID, "--dry-run", "--json"],
    )
    assert code == 0
    clean = json.loads(out)
    assert clean == {"candidates": [], "deleted": 0, "dry_run": True, "total": 0}


def test_artifact_generation_export_api_are_fixture_backed(client):
    generated = asyncio.run(client.artifacts.generate_report(SYNTHETIC_NOTEBOOK_ID))
    assert generated.status == "completed"
    assert generated.task_id.startswith("offline-report-")
    assert (
        generated.url
        == f"https://example.test/notebooklm-bare/generated/{generated.task_id}"
    )

    for method_name, prefix in (
        ("generate_audio", "offline-audio-"),
        ("generate_cinematic_video", "offline-video-"),
        ("generate_data_table", "offline-data-table-"),
        ("generate_flashcards", "offline-flashcards-"),
        ("generate_infographic", "offline-infographic-"),
        ("generate_quiz", "offline-quiz-"),
        ("generate_slide_deck", "offline-slide-deck-"),
        ("generate_study_guide", "offline-report-"),
        ("generate_video", "offline-video-"),
    ):
        status = asyncio.run(
            getattr(client.artifacts, method_name)(SYNTHETIC_NOTEBOOK_ID)
        )
        assert status.status == "completed"
        assert status.task_id.startswith(prefix)
        assert status.url.endswith(status.task_id)

    exported = asyncio.run(
        client.artifacts.export_report(
            SYNTHETIC_NOTEBOOK_ID,
            SYNTHETIC_ARTIFACT_ID,
            "Synthetic Export",
        )
    )
    assert exported == {
        "artifact_id": SYNTHETIC_ARTIFACT_ID,
        "content": None,
        "export_type": "DOCS",
        "exported": True,
        "notebook_id": SYNTHETIC_NOTEBOOK_ID,
        "title": "Synthetic Export",
        "url": f"https://example.test/notebooklm-bare/export/{SYNTHETIC_ARTIFACT_ID}",
    }

    mind_map = asyncio.run(client.artifacts.generate_mind_map(SYNTHETIC_NOTEBOOK_ID))
    assert mind_map.note_id.startswith("offline-mind-map-")
    assert mind_map.mind_map == {"name": "Synthetic Mind Map", "children": []}


def test_cli_artifact_export_retry_are_fixture_backed(cli_mods, capsys):
    code, out, err = _run_cli(
        cli_mods,
        capsys,
        [
            "artifact",
            "export",
            SYNTHETIC_ARTIFACT_ID,
            "--title",
            "CLI Export",
            "-n",
            SYNTHETIC_NOTEBOOK_ID,
            "--json",
        ],
    )
    assert code == 0
    assert err == ""
    exported = json.loads(out)
    assert exported["exported"] is True
    assert exported["artifact_id"] == SYNTHETIC_ARTIFACT_ID
    assert exported["title"] == "CLI Export"
    assert exported["url"].endswith(SYNTHETIC_ARTIFACT_ID)

    code, out, err = _run_cli(
        cli_mods,
        capsys,
        [
            "artifact",
            "retry",
            SYNTHETIC_ARTIFACT_ID,
            "-n",
            SYNTHETIC_NOTEBOOK_ID,
            "--json",
        ],
    )
    assert code == 0
    assert err == ""
    retried = json.loads(out)
    assert retried["task_id"] == SYNTHETIC_ARTIFACT_ID
    assert retried["status"] == "completed"


def test_cli_source_and_artifact_mutations_are_fixture_backed(cli_mods, capsys):
    code, out, err = _run_cli(
        cli_mods,
        capsys,
        [
            "source",
            "rename",
            SYNTHETIC_SOURCE_ID,
            "Renamed Source",
            "-n",
            SYNTHETIC_NOTEBOOK_ID,
            "--json",
        ],
    )
    assert code == 0
    renamed_source = json.loads(out)
    assert renamed_source["source"]["title"] == "Renamed Source"

    code, out, err = _run_cli(
        cli_mods,
        capsys,
        [
            "source",
            "refresh",
            SYNTHETIC_SOURCE_ID,
            "-n",
            SYNTHETIC_NOTEBOOK_ID,
            "--json",
        ],
    )
    assert code == 0
    assert json.loads(out) == {
        "source_id": SYNTHETIC_SOURCE_ID,
        "notebook_id": SYNTHETIC_NOTEBOOK_ID,
        "refreshed": True,
    }

    code, out, err = _run_cli(
        cli_mods,
        capsys,
        [
            "source",
            "delete",
            SYNTHETIC_SOURCE_ID,
            "-n",
            SYNTHETIC_NOTEBOOK_ID,
            "--yes",
            "--json",
        ],
    )
    assert code == 0
    assert json.loads(out) == {
        "source_id": SYNTHETIC_SOURCE_ID,
        "notebook_id": SYNTHETIC_NOTEBOOK_ID,
        "deleted": True,
    }

    code, out, err = _run_cli(
        cli_mods,
        capsys,
        [
            "artifact",
            "rename",
            SYNTHETIC_ARTIFACT_ID,
            "Renamed Audio",
            "-n",
            SYNTHETIC_NOTEBOOK_ID,
            "--json",
        ],
    )
    assert code == 0
    assert json.loads(out)["artifact"]["title"] == "Renamed Audio"

    code, out, err = _run_cli(
        cli_mods,
        capsys,
        [
            "artifact",
            "delete",
            SYNTHETIC_ARTIFACT_ID,
            "-n",
            SYNTHETIC_NOTEBOOK_ID,
            "--yes",
            "--json",
        ],
    )
    assert code == 0
    assert json.loads(out) == {
        "artifact_id": SYNTHETIC_ARTIFACT_ID,
        "notebook_id": SYNTHETIC_NOTEBOOK_ID,
        "deleted": True,
    }
