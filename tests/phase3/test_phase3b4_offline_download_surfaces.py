"""Batch 3B4: offline fixture-backed download API and CLI parity.

This batch promotes NotebookLM's generated-content download surfaces over local
synthetic artifacts only. Downloads write deterministic offline files under the
caller-supplied path or pytest temp directories; they never perform live RPC,
auth/browser/home reads, network downloads, public sharing, or real NotebookLM
data mutation.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

SYNTHETIC_NOTEBOOK_ID = "fake-notebook-0001"


def _poison_home(monkeypatch, tmp_path: Path) -> None:
    poisoned = tmp_path / "poisoned-home"
    monkeypatch.setenv("HOME", str(poisoned))
    monkeypatch.setenv("NOTEBOOKLM_CONFIG", str(poisoned / "config.json"))
    monkeypatch.setattr(
        Path,
        "home",
        staticmethod(lambda: (_ for _ in ()).throw(AssertionError("real home access"))),
    )


def _mods(repo_root, monkeypatch, tmp_path: Path):
    monkeypatch.syspath_prepend(str(repo_root))
    _poison_home(monkeypatch, tmp_path)
    modules = {}
    for name in ["notebooklm", "notebooklm.artifacts", "notebooklm.cli"]:
        modules[name.rsplit(".", 1)[-1]] = importlib.import_module(name)
    return modules


def _run(mods, capsys, argv):
    code = mods["cli"].console(argv)
    out = capsys.readouterr()
    return code, out.out.strip(), out.err.strip()


def _artifact_rows() -> list[list[object]]:
    return [
        [
            "artifact-audio-old",
            "Alpha Audio",
            1,
            3,
            1750000100,
            "https://example.test/audio-old.mp3",
            None,
        ],
        [
            "artifact-audio-new",
            "Omega Audio",
            1,
            3,
            1750000900,
            "https://example.test/audio-new.mp3",
            None,
        ],
        [
            "artifact-video",
            "Synthetic Video",
            3,
            3,
            1750000200,
            "https://example.test/video.mp4",
            None,
        ],
        ["artifact-report", "Synthetic Report", 2, 3, 1750000300, None, None],
        ["artifact-mind-map", "Synthetic Mind Map", 5, 3, 1750000400, None, None],
        ["artifact-data-table", "Synthetic Data Table", 9, 3, 1750000500, None, None],
        ["artifact-slide-deck", "Synthetic Slide Deck", 8, 3, 1750000600, None, None],
        ["artifact-infographic", "Synthetic Infographic", 7, 3, 1750000700, None, None],
        ["artifact-quiz", "Synthetic Quiz", 4, 3, 1750000800, None, 2],
        ["artifact-flashcards", "Synthetic Flashcards", 4, 3, 1750000850, None, 1],
        ["artifact-pending-quiz", "Pending Quiz", 4, 2, 1750000950, None, 2],
    ]


def _write_all_artifact_fixture(repo_root: Path, tmp_path: Path) -> Path:
    src = repo_root / "compat" / "rpc_fixtures"
    dest = tmp_path / "rpc-fixtures"
    shutil.copytree(src, dest)
    payload = json.dumps(_artifact_rows(), separators=(",", ":"))
    response = ")]}'\n\n" + json.dumps(
        [["wrb.fr", "gArtLc", payload, None, None, None, "generic"]],
        separators=(",", ":"),
    )
    (dest / "list_artifacts.response.txt").write_text(response, encoding="utf-8")
    return dest


def test_phase3b4_promotes_download_group_but_keeps_live_boundaries(
    repo_root, monkeypatch, tmp_path
):
    mods = _mods(repo_root, monkeypatch, tmp_path)
    cli = mods["cli"]

    assert cli.PHASE3B4_COMMANDS == frozenset({"download"})
    assert "download" in cli.IMPLEMENTED_COMMANDS
    assert {"generate", "share"} <= cli.IMPLEMENTED_COMMANDS


def test_artifacts_api_download_methods_write_deterministic_local_files(
    repo_root, monkeypatch, tmp_path
):
    _mods(repo_root, monkeypatch, tmp_path)

    async def scenario():
        from notebooklm import Artifact, ArtifactType
        from notebooklm._artifacts_impl import (
            ArtifactsAPI,
            OfflineArtifactService,
        )
        from notebooklm.types import (
            ArtifactStatus,
        )
        from notebooklm.errors import ValidationError

        def artifact(
            artifact_id: str, title: str, kind: ArtifactType, variant: int | None = None
        ):
            return Artifact(
                id=artifact_id,
                title=title,
                _artifact_type={
                    ArtifactType.AUDIO: 1,
                    ArtifactType.REPORT: 2,
                    ArtifactType.VIDEO: 3,
                    ArtifactType.QUIZ: 4,
                    ArtifactType.FLASHCARDS: 4,
                    ArtifactType.MIND_MAP: 5,
                    ArtifactType.INFOGRAPHIC: 7,
                    ArtifactType.SLIDE_DECK: 8,
                    ArtifactType.DATA_TABLE: 9,
                }[kind],
                status=ArtifactStatus.COMPLETED.value,
                created_at=datetime.fromtimestamp(1750000000, timezone.utc),
                url=f"https://example.test/{artifact_id}",
                _variant=variant,
            )

        api = ArtifactsAPI(
            artifacts=OfflineArtifactService(
                {
                    SYNTHETIC_NOTEBOOK_ID: [
                        artifact("api-audio", "API Audio", ArtifactType.AUDIO),
                        artifact("api-report", "API Report", ArtifactType.REPORT),
                        artifact("api-video", "API Video", ArtifactType.VIDEO),
                        artifact("api-slide", "API Slide", ArtifactType.SLIDE_DECK),
                        artifact(
                            "api-infographic",
                            "API Infographic",
                            ArtifactType.INFOGRAPHIC,
                        ),
                        artifact("api-mind-map", "API Mind Map", ArtifactType.MIND_MAP),
                        artifact(
                            "api-data-table", "API Data Table", ArtifactType.DATA_TABLE
                        ),
                        artifact("api-quiz", "API Quiz", ArtifactType.QUIZ, variant=2),
                        artifact(
                            "api-flashcards",
                            "API Flashcards",
                            ArtifactType.FLASHCARDS,
                            variant=1,
                        ),
                    ]
                }
            )
        )

        cases = [
            (api.download_audio, "api-audio", "audio.mp3", (), {}),
            (api.download_report, "api-report", "report.md", (), {}),
            (api.download_video, "api-video", "video.mp4", (), {}),
            (api.download_slide_deck, "api-slide", "slides.pptx", ("pptx",), {}),
            (api.download_infographic, "api-infographic", "image.png", (), {}),
            (
                api.download_mind_map,
                "api-mind-map",
                "mind.json",
                (),
                {"mind_maps": [], "artifacts_data": []},
            ),
            (
                api.download_data_table,
                "api-data-table",
                "table.csv",
                (),
                {"artifacts_data": []},
            ),
            (
                api.download_quiz,
                "api-quiz",
                "quiz.md",
                ("markdown",),
                {"artifacts": []},
            ),
            (
                api.download_flashcards,
                "api-flashcards",
                "cards.html",
                ("html",),
                {"artifacts": []},
            ),
        ]
        for method, artifact_id, filename, positional, kwargs in cases:
            output_path = tmp_path / filename
            returned = await method(
                SYNTHETIC_NOTEBOOK_ID,
                str(output_path),
                artifact_id,
                *positional,
                **kwargs,
            )
            assert returned == str(output_path)
            text = output_path.read_text(encoding="utf-8")
            assert artifact_id in text
            assert SYNTHETIC_NOTEBOOK_ID in text
            assert "offline deterministic NotebookLM artifact download" in text

        generated = await api.generate_flashcards(SYNTHETIC_NOTEBOOK_ID)
        assert generated.task_id.startswith("offline-flashcards-")
        assert [
            artifact.id for artifact in await api.list_flashcards(SYNTHETIC_NOTEBOOK_ID)
        ] == [
            "api-flashcards",
            generated.task_id,
        ]
        generated_path = tmp_path / "generated-cards.json"
        assert await api.download_flashcards(
            SYNTHETIC_NOTEBOOK_ID,
            str(generated_path),
            generated.task_id,
        ) == str(generated_path)
        assert generated.task_id in generated_path.read_text(encoding="utf-8")

        token_like = "ya29." + "A" * 24
        with pytest.raises(ValidationError) as excinfo:
            await api.download_audio(
                SYNTHETIC_NOTEBOOK_ID,
                str(tmp_path / "missing.mp3"),
                artifact_id=token_like,
            )
        assert str(excinfo.value) == "artifact not found"
        assert token_like not in str(excinfo.value)

    asyncio.run(scenario())


def test_download_cli_single_selection_formats_and_conflict_policy(
    repo_root, monkeypatch, tmp_path, capsys
):
    mods = _mods(repo_root, monkeypatch, tmp_path)
    fixture_dir = _write_all_artifact_fixture(repo_root, tmp_path)

    target = tmp_path / "latest-audio.mp3"
    code, out, err = _run(
        mods,
        capsys,
        [
            "download",
            "audio",
            str(target),
            "--latest",
            "--json",
            "--fixture-dir",
            str(fixture_dir),
        ],
    )
    assert code == 0, err
    assert err == ""
    payload = json.loads(out)
    assert payload["operation"] == "download_single"
    assert payload["artifact"]["id"] == "artifact-audio-new"
    assert payload["output_path"] == str(target)
    assert target.read_text(encoding="utf-8").startswith(
        "# Offline NotebookLM artifact download"
    )

    dry_target = tmp_path / "slides.pptx"
    code, out, err = _run(
        mods,
        capsys,
        [
            "download",
            "slide-deck",
            str(dry_target),
            "--format",
            "pptx",
            "--dry-run",
            "--json",
            "--fixture-dir",
            str(fixture_dir),
        ],
    )
    assert code == 0
    dry = json.loads(out)
    assert dry["dry_run"] is True
    assert dry["output_path"].endswith("slides.pptx")
    assert not dry_target.exists()

    target.write_text("existing", encoding="utf-8")
    code, out, err = _run(
        mods,
        capsys,
        [
            "download",
            "audio",
            str(target),
            "--artifact",
            "artifact-audio-new",
            "--no-clobber",
            "--json",
            "--fixture-dir",
            str(fixture_dir),
        ],
    )
    assert code == 64
    assert out == ""
    assert "File exists" in err
    assert target.read_text(encoding="utf-8") == "existing"

    code, out, err = _run(
        mods,
        capsys,
        [
            "download",
            "audio",
            str(target),
            "--artifact",
            "artifact-audio-new",
            "--force",
            "--json",
            "--fixture-dir",
            str(fixture_dir),
        ],
    )
    assert code == 0
    assert "artifact-audio-new" in target.read_text(encoding="utf-8")


def test_download_cli_all_aliases_filters_and_format_outputs(
    repo_root, monkeypatch, tmp_path, capsys
):
    mods = _mods(repo_root, monkeypatch, tmp_path)
    fixture_dir = _write_all_artifact_fixture(repo_root, tmp_path)

    output_dir = tmp_path / "cards"
    code, out, err = _run(
        mods,
        capsys,
        [
            "download",
            "flashcards",
            str(output_dir),
            "--all",
            "--format",
            "markdown",
            "--json",
            "--fixture-dir",
            str(fixture_dir),
        ],
    )
    assert code == 0, err
    payload = json.loads(out)
    assert payload["operation"] == "download_all"
    assert payload["total"] == 1
    assert payload["succeeded_count"] == 1
    assert payload["artifacts"][0]["filename"].endswith(".md")
    downloaded = output_dir / payload["artifacts"][0]["filename"]
    assert downloaded.is_file()
    assert "artifact-flashcards" in downloaded.read_text(encoding="utf-8")

    video_target = tmp_path / "cinematic.mp4"
    code, out, err = _run(
        mods,
        capsys,
        [
            "download",
            "cinematic-video",
            str(video_target),
            "--name",
            "Video",
            "--json",
            "--fixture-dir",
            str(fixture_dir),
        ],
    )
    assert code == 0
    assert json.loads(out)["artifact"]["id"] == "artifact-video"
    assert video_target.is_file()

    code, out, err = _run(
        mods,
        capsys,
        [
            "download",
            "quiz",
            "--artifact",
            "artifact-pending-quiz",
            "--json",
            "--fixture-dir",
            str(fixture_dir),
        ],
    )
    assert code == 64
    assert out == ""
    assert "artifact not found" in err


def test_download_cli_rejects_flag_conflicts_and_redacts_private_inputs(
    repo_root, monkeypatch, tmp_path, capsys
):
    mods = _mods(repo_root, monkeypatch, tmp_path)
    fixture_dir = _write_all_artifact_fixture(repo_root, tmp_path)

    for argv, expected in [
        (
            ["download", "audio", "--force", "--no-clobber"],
            "Cannot specify both --force and --no-clobber",
        ),
        (
            ["download", "audio", "--latest", "--earliest"],
            "Cannot specify both --latest and --earliest",
        ),
        (
            ["download", "audio", "--all", "--artifact", "artifact-audio-new"],
            "Cannot specify both --all and --artifact",
        ),
    ]:
        code, out, err = _run(
            mods, capsys, [*argv, "--json", "--fixture-dir", str(fixture_dir)]
        )
        assert code == 64
        assert out == ""
        assert expected in err

    token_like = "ya29." + "B" * 24
    code, out, err = _run(
        mods,
        capsys,
        [
            "download",
            "audio",
            "--artifact",
            token_like,
            "--json",
            "--fixture-dir",
            str(fixture_dir),
        ],
    )
    assert code == 64
    assert out == ""
    assert "artifact not found" in err
    assert token_like not in err

    with pytest.raises(SystemExit) as excinfo:
        mods["cli"].console(["download", "audio", "--live"])
    assert excinfo.value.code == 2
    rejected = capsys.readouterr()
    assert "unrecognized arguments" in rejected.err
    assert "--live" in rejected.err
