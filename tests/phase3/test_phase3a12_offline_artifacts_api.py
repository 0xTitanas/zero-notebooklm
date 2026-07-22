"""Phase 3A12 offline Python artifacts API over committed synthetic fixtures.

This slice promotes only a read-only, fixture-backed ``client.artifacts``
foothold. It decodes committed synthetic list-artifacts fixtures and keeps
artifact generation, download/export, rename/delete/retry, polling/wait flows,
CLI ``artifact`` promotion, live RPC, browser/auth, credential access, and real
NotebookLM data mutation out of scope.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

import import_origin_audit  # noqa: E402  (placed on sys.path by tests/conftest.py)
import _phase0_constants as C  # noqa: E402  (placed on sys.path by tests/conftest.py)

SYNTHETIC_NOTEBOOK_ID = "fake-notebook-0001"
EXPECTED_ARTIFACT_DICTS = [
    {
        "id": "fake-artifact-audio-0001",
        "title": "Synthetic Audio Overview",
        "type_code": 1,
        "artifact_type": "AUDIO",
        "status": "COMPLETED",
        "created_at": "2025-06-15T15:15:00+00:00",
        "url": "https://example.test/notebooklm-bare/audio.mp3",
        "variant": None,
    },
    {
        "id": "fake-artifact-report-0001",
        "title": "Synthetic Briefing Doc",
        "type_code": 2,
        "artifact_type": "REPORT",
        "status": "COMPLETED",
        "created_at": "2025-06-15T15:16:40+00:00",
        "url": None,
        "variant": None,
    },
    {
        "id": "fake-artifact-quiz-0001",
        "title": "Synthetic Quiz",
        "type_code": 4,
        "artifact_type": "QUIZ",
        "status": "PENDING",
        "created_at": None,
        "url": None,
        "variant": 2,
    },
]


def _poison_home(monkeypatch):
    monkeypatch.setattr(
        Path,
        "home",
        staticmethod(lambda: (_ for _ in ()).throw(AssertionError("real home access"))),
    )


def _client():
    from notebooklm import AuthTokens, NotebookLMClient

    return NotebookLMClient(
        AuthTokens(cookies={}, csrf_token="synthetic", session_id="synthetic")
    )


def test_root_exports_artifact_models_and_golden_methods(python_api):
    import notebooklm
    from notebooklm._artifacts_impl import ArtifactsAPI
    from notebooklm.types import ArtifactStatus

    for name in {"Artifact", "ArtifactType"}:
        assert hasattr(notebooklm, name)
    assert not hasattr(notebooklm, "ArtifactsAPI")
    assert not hasattr(notebooklm, "ArtifactStatus")
    assert ArtifactsAPI.__name__ == "ArtifactsAPI"
    assert ArtifactStatus.COMPLETED.value == 3

    assert python_api["subclients"]["artifacts"]["async_methods"] == [
        "delete",
        "download_audio",
        "download_data_table",
        "download_flashcards",
        "download_infographic",
        "download_mind_map",
        "download_quiz",
        "download_report",
        "download_slide_deck",
        "download_video",
        "export",
        "export_data_table",
        "export_report",
        "generate_audio",
        "generate_cinematic_video",
        "generate_data_table",
        "generate_flashcards",
        "generate_infographic",
        "generate_mind_map",
        "generate_quiz",
        "generate_report",
        "generate_slide_deck",
        "generate_study_guide",
        "generate_video",
        "get",
        "get_or_none",
        "list",
        "list_audio",
        "list_data_tables",
        "list_flashcards",
        "list_infographics",
        "list_quizzes",
        "list_reports",
        "list_slide_decks",
        "list_video",
        "poll_status",
        "rename",
        "retry_failed",
        "revise_slide",
        "suggest_reports",
        "wait_for_completion",
    ]


def test_artifact_fixture_pair_is_committed_and_decoded_through_fake_rpc(compat_dir):
    from notebooklm.fake_rpc import (
        LIST_ARTIFACTS_RPCID,
        OfflineFixtureRpcClient,
        list_artifacts_request,
    )

    fixture_dir = compat_dir / "rpc_fixtures"
    assert (fixture_dir / "list_artifacts.request.txt").is_file()
    assert (fixture_dir / "list_artifacts.response.txt").is_file()

    client = OfflineFixtureRpcClient.from_fixture_dir(fixture_dir)
    request = list_artifacts_request(SYNTHETIC_NOTEBOOK_ID)
    assert request.rpcid == LIST_ARTIFACTS_RPCID
    payload = client.list_artifacts_payload(SYNTHETIC_NOTEBOOK_ID)
    assert payload[0][0] == "fake-artifact-audio-0001"
    assert payload[1][0] == "fake-artifact-report-0001"
    assert payload[2][0] == "fake-artifact-quiz-0001"


def test_offline_client_lists_filters_and_gets_artifacts_without_home(monkeypatch):
    _poison_home(monkeypatch)

    async def scenario():
        from notebooklm import ArtifactType

        client = _client()
        artifacts = await client.artifacts.list(SYNTHETIC_NOTEBOOK_ID)
        assert [artifact.as_dict() for artifact in artifacts] == EXPECTED_ARTIFACT_DICTS

        audio = await client.artifacts.list(SYNTHETIC_NOTEBOOK_ID, ArtifactType.AUDIO)
        assert [artifact.id for artifact in audio] == ["fake-artifact-audio-0001"]
        assert [
            artifact.id
            for artifact in await client.artifacts.list_audio(SYNTHETIC_NOTEBOOK_ID)
        ] == ["fake-artifact-audio-0001"]
        assert [
            artifact.id
            for artifact in await client.artifacts.list_reports(SYNTHETIC_NOTEBOOK_ID)
        ] == ["fake-artifact-report-0001"]
        assert [
            artifact.id
            for artifact in await client.artifacts.list_quizzes(SYNTHETIC_NOTEBOOK_ID)
        ] == ["fake-artifact-quiz-0001"]

        first = await client.artifacts.get(
            SYNTHETIC_NOTEBOOK_ID, "fake-artifact-audio-0001"
        )
        assert first is not None
        assert first.as_dict() == EXPECTED_ARTIFACT_DICTS[0]
        assert (
            await client.artifacts.get(SYNTHETIC_NOTEBOOK_ID, "missing-artifact")
        ) is None
        assert (
            await client.artifacts.get_or_none(
                SYNTHETIC_NOTEBOOK_ID, "missing-artifact"
            )
        ) is None
        assert await client.artifacts.list("missing-notebook") == []

    asyncio.run(scenario())


def test_artifact_model_matches_pinned_shape_and_redacted_dict():
    from notebooklm import Artifact, ArtifactType
    from notebooklm.types import ArtifactStatus

    created_at = datetime.fromtimestamp(1750000600, timezone.utc)
    artifact = Artifact(
        id="custom-artifact",
        title="Custom Artifact",
        _artifact_type=2,
        status=3,
        created_at=created_at,
        url=None,
        _variant=7,
    )
    assert artifact.kind() is ArtifactType.REPORT
    assert artifact.state() is ArtifactStatus.COMPLETED
    assert artifact.as_dict() == {
        "id": "custom-artifact",
        "title": "Custom Artifact",
        "type_code": 2,
        "artifact_type": "REPORT",
        "status": "COMPLETED",
        "created_at": "2025-06-15T15:16:40+00:00",
        "url": None,
        "variant": 7,
    }


def test_list_artifacts_payload_validation_is_strict_and_redacted():
    from notebooklm._artifacts_impl import parse_list_artifacts_payload
    from notebooklm.errors import ValidationError

    private_payload = [["fake-artifact", object(), 1, 3, 1750000500, None, None]]
    with pytest.raises(ValidationError) as excinfo:
        parse_list_artifacts_payload(private_payload)
    assert (
        str(excinfo.value)
        == "invalid list_artifacts payload: artifact title must be text"
    )
    assert "object at" not in str(excinfo.value)
    assert excinfo.value.__context__ is None
    assert excinfo.value.__cause__ is None


def test_list_artifacts_created_at_out_of_range_drops_exception_context():
    from notebooklm._artifacts_impl import parse_list_artifacts_payload
    from notebooklm.errors import ValidationError

    payload = [["fake-artifact", "Title", 1, 3, 10**1000, None, None]]
    with pytest.raises(ValidationError) as excinfo:
        parse_list_artifacts_payload(payload)
    assert (
        str(excinfo.value)
        == "invalid list_artifacts payload: created_at is out of range"
    )
    assert excinfo.value.__context__ is None
    assert excinfo.value.__cause__ is None


def test_artifact_download_surfaces_write_local_fixture_files(monkeypatch, tmp_path):
    _poison_home(monkeypatch)

    async def scenario():
        from notebooklm.errors import ValidationError

        client = _client()
        renamed = await client.artifacts.rename(
            SYNTHETIC_NOTEBOOK_ID,
            "fake-artifact-audio-0001",
            "Renamed Audio",
        )
        assert renamed is not None
        assert renamed.title == "Renamed Audio"
        retry = await client.artifacts.retry_failed(
            SYNTHETIC_NOTEBOOK_ID,
            "fake-artifact-audio-0001",
        )
        assert retry.status == "completed"

        output_path = tmp_path / "renamed-audio.mp3"
        returned = await client.artifacts.download_audio(
            SYNTHETIC_NOTEBOOK_ID,
            str(output_path),
            artifact_id="fake-artifact-audio-0001",
        )
        assert returned == str(output_path)
        assert "fake-artifact-audio-0001" in output_path.read_text(encoding="utf-8")

        await client.artifacts.delete(SYNTHETIC_NOTEBOOK_ID, "fake-artifact-audio-0001")
        assert (
            await client.artifacts.get(
                SYNTHETIC_NOTEBOOK_ID, "fake-artifact-audio-0001"
            )
            is None
        )
        with pytest.raises(ValidationError) as excinfo:
            await client.artifacts.download_audio(
                SYNTHETIC_NOTEBOOK_ID,
                str(tmp_path / "missing.mp3"),
                artifact_id="fake-artifact-audio-0001",
            )
        assert str(excinfo.value) == "artifact not found"

        generated = await client.artifacts.generate_report(SYNTHETIC_NOTEBOOK_ID)
        assert generated.status == "completed"
        assert generated.task_id.startswith("offline-report-")

        # Batch 3B1 promotes fixture-backed read/status polling; Batch 3B2 adds
        # in-memory artifact mutation plus synthetic generation; Batch 3B4 adds
        # deterministic local downloads over the same synthetic artifacts.
        status = await client.artifacts.poll_status(
            SYNTHETIC_NOTEBOOK_ID, "fake-artifact-audio-0001"
        )
        assert status.status == "completed"
        assert status.url == "https://example.test/notebooklm-bare/audio.mp3"

    asyncio.run(scenario())


def test_phase3a12_runtime_surface_is_artifacts_api_plus_cli_read_surface(
    repo_root, python_api
):
    assert "Artifact" in python_api["root_all"]
    assert (
        "ArtifactsAPI" not in python_api["root_all"]
    )  # sub-client class, not root export upstream

    test_file = (
        repo_root / "tests" / "phase3" / "test_phase3a12_offline_artifacts_api.py"
    )
    touched = [
        repo_root / "notebooklm" / "artifacts.py",
        repo_root / "notebooklm" / "client.py",
        repo_root / "notebooklm" / "fake_rpc.py",
        repo_root / "notebooklm" / "__init__.py",
        test_file,
    ]
    for path in touched:
        assert path.is_file()
        assert (
            import_origin_audit.scan_file(str(path), C.DENYLISTED_RUNTIME_IMPORTS) == []
        )

    fixture_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (repo_root / "compat" / "rpc_fixtures").glob("list_artifacts.*.txt")
    )
    assert "SYNTHETIC_XSRF_TOKEN" in fixture_text
    session_cookie_prefix = "S" + "ID="
    secure_cookie_prefix = "__" + "Secure"
    assert session_cookie_prefix not in fixture_text
    assert secure_cookie_prefix not in fixture_text
    assert "/".join(("", "Users", "")) not in fixture_text
    assert "notebooklm.google.com" not in fixture_text

    from notebooklm import cli

    assert {"list", "use", "note", "source", "artifact", "metadata"} <= set(
        cli.IMPLEMENTED_COMMANDS
    )
