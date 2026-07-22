"""Batch 3B3: offline fixture-backed ``notebooklm generate`` CLI parity.

This batch promotes the reference ``generate`` command group over the already
reviewed synthetic artifact generation seam. It remains offline-only: no live
NotebookLM RPC, browser/home reads, credentials, downloads, public sharing, or
real artifact mutation are authorized here.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

SYNTHETIC_NOTEBOOK_ID = "fake-notebook-0001"
SYNTHETIC_SOURCE_ID = "fake-source-0001"
SYNTHETIC_ARTIFACT_ID = "fake-artifact-audio-0001"


def _poison_home(monkeypatch, tmp_path: Path) -> None:
    poisoned = tmp_path / "poisoned-home"
    monkeypatch.setenv("HOME", str(poisoned))
    monkeypatch.setenv("NOTEBOOKLM_CONFIG", str(poisoned / "config.json"))
    monkeypatch.setattr(
        Path,
        "home",
        staticmethod(lambda: (_ for _ in ()).throw(AssertionError("real home access"))),
    )


def _mods(repo_root, monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(repo_root))
    _poison_home(monkeypatch, tmp_path)
    modules = {}
    for name in ["notebooklm.artifacts", "notebooklm.cli"]:
        modules[name.rsplit(".", 1)[-1]] = importlib.reload(
            importlib.import_module(name)
        )
    return modules


def _run(mods, capsys, argv):
    code = mods["cli"].console(argv)
    out = capsys.readouterr()
    return code, out.out.strip(), out.err.strip()


def test_phase3b3_promotes_generate_cli_group_before_download_batch(
    repo_root, monkeypatch, tmp_path
):
    mods = _mods(repo_root, monkeypatch, tmp_path)
    cli = mods["cli"]

    assert "generate" in cli.IMPLEMENTED_COMMANDS
    assert "download" in cli.IMPLEMENTED_COMMANDS
    assert "share" in cli.IMPLEMENTED_COMMANDS


_GENERATION_CASES = [
    (
        [
            "generate",
            "audio",
            "deep dive",
            "--format",
            "debate",
            "--length",
            "short",
            "-n",
            SYNTHETIC_NOTEBOOK_ID,
            "--language",
            "es",
            "-s",
            SYNTHETIC_SOURCE_ID,
            "--wait",
            "--json",
        ],
        "offline-audio-",
    ),
    (
        [
            "generate",
            "video",
            "visual overview",
            "--format",
            "explainer",
            "--style",
            "custom",
            "--style-prompt",
            "hand drawn",
            "-n",
            SYNTHETIC_NOTEBOOK_ID,
            "--json",
        ],
        "offline-video-",
    ),
    (
        [
            "generate",
            "cinematic-video",
            "documentary",
            "-n",
            SYNTHETIC_NOTEBOOK_ID,
            "--json",
        ],
        "offline-video-",
    ),
    (
        [
            "generate",
            "slide-deck",
            "executive summary",
            "--format",
            "presenter",
            "--length",
            "short",
            "-n",
            SYNTHETIC_NOTEBOOK_ID,
            "--json",
        ],
        "offline-slide-deck-",
    ),
    (
        [
            "generate",
            "quiz",
            "vocabulary",
            "--quantity",
            "more",
            "--difficulty",
            "hard",
            "-s",
            SYNTHETIC_SOURCE_ID,
            "-n",
            SYNTHETIC_NOTEBOOK_ID,
            "--json",
        ],
        "offline-quiz-",
    ),
    (
        [
            "generate",
            "flashcards",
            "terms",
            "--quantity",
            "fewer",
            "--difficulty",
            "easy",
            "--json",
        ],
        "offline-flashcards-",
    ),
    (
        [
            "generate",
            "infographic",
            "key findings",
            "--orientation",
            "portrait",
            "--detail",
            "detailed",
            "--style",
            "professional",
            "--language",
            "fr",
            "--json",
        ],
        "offline-infographic-",
    ),
    (
        ["generate", "data-table", "timeline", "--language", "de", "--json"],
        "offline-data-table-",
    ),
    (
        [
            "generate",
            "report",
            "white paper",
            "--format",
            "study-guide",
            "--append",
            "for beginners",
            "--language",
            "it",
            "--json",
        ],
        "offline-report-",
    ),
]


def test_generate_leaf_commands_return_fixture_backed_status_json(
    repo_root, monkeypatch, tmp_path, capsys
):
    mods = _mods(repo_root, monkeypatch, tmp_path)

    for argv, prefix in _GENERATION_CASES:
        code, out, err = _run(mods, capsys, argv)
        assert code == 0, (argv, err)
        assert err == ""
        payload = json.loads(out)
        assert payload["status"] == "completed"
        assert payload["task_id"].startswith(prefix)
        assert payload["url"].endswith(payload["task_id"])


def test_generate_prompt_file_stdin_and_mind_map_json_are_offline(
    repo_root, monkeypatch, tmp_path, capsys
):
    mods = _mods(repo_root, monkeypatch, tmp_path)
    import sys
    from io import StringIO

    monkeypatch.setattr(sys, "stdin", StringIO("comparison from stdin\n"))
    code, out, err = _run(
        mods, capsys, ["generate", "data-table", "--prompt-file", "-", "--json"]
    )
    assert code == 0
    assert err == ""
    assert json.loads(out)["task_id"].startswith("offline-data-table-")

    code, out, err = _run(mods, capsys, ["generate", "data-table", "--json"])
    assert code == 0
    assert err == ""
    assert json.loads(out)["task_id"].startswith("offline-data-table-")

    code, out, err = _run(
        mods,
        capsys,
        [
            "generate",
            "mind-map",
            "--kind",
            "note-backed",
            "--instructions",
            "cluster themes",
            "-n",
            SYNTHETIC_NOTEBOOK_ID,
            "--language",
            "en",
            "--json",
        ],
    )
    assert code == 0
    assert err == ""
    payload = json.loads(out)
    assert payload == {
        "mind_map": {"name": "Synthetic Mind Map", "children": []},
        "note_id": "offline-mind-map-0001",
        "kind": "note_backed",
    }


def test_generate_mind_map_default_kind_notice_and_quiet_env_match_upstream(
    repo_root, monkeypatch, tmp_path, capsys
):
    mods = _mods(repo_root, monkeypatch, tmp_path)

    code, out, err = _run(
        mods,
        capsys,
        ["generate", "mind-map", "-n", SYNTHETIC_NOTEBOOK_ID],
    )

    assert code == 0
    assert "mind_map:" in out
    assert "defaults to the note-backed kind today" in err

    monkeypatch.setenv("NOTEBOOKLM_QUIET_DEPRECATIONS", "1")
    code, _, err = _run(
        mods,
        capsys,
        ["generate", "mind-map", "-n", SYNTHETIC_NOTEBOOK_ID],
    )

    assert code == 0
    assert err == ""


def test_generate_mind_map_interactive_drops_instructions_warning_even_json(
    repo_root, monkeypatch, tmp_path, capsys
):
    mods = _mods(repo_root, monkeypatch, tmp_path)

    code, out, err = _run(
        mods,
        capsys,
        [
            "generate",
            "mind-map",
            "--kind",
            "interactive",
            "--instructions",
            "drop me",
            "-n",
            SYNTHETIC_NOTEBOOK_ID,
            "--json",
        ],
    )

    assert code == 0
    assert "--instructions is ignored for interactive mind maps" in err
    payload = json.loads(out)
    assert payload["kind"] == "interactive"
    assert payload["note_id"] == "offline-mind-map-0001"
    assert payload["mind_map"]["name"] == "Synthetic Mind Map"
    assert "instructions" not in payload["mind_map"]


def test_generate_revise_slide_is_fixture_backed_and_validates_inputs(
    repo_root, monkeypatch, tmp_path, capsys
):
    mods = _mods(repo_root, monkeypatch, tmp_path)

    code, out, err = _run(
        mods,
        capsys,
        [
            "generate",
            "revise-slide",
            "Move the title up",
            "--artifact",
            SYNTHETIC_ARTIFACT_ID,
            "--slide",
            "0",
            "-n",
            SYNTHETIC_NOTEBOOK_ID,
            "--json",
        ],
    )
    assert code == 0
    assert err == ""
    payload = json.loads(out)
    assert payload == {
        "task_id": f"{SYNTHETIC_ARTIFACT_ID}-slide-0-revision",
        "status": "completed",
        "url": f"https://example.test/notebooklm-bare/generated/{SYNTHETIC_ARTIFACT_ID}-slide-0-revision",
    }

    code, out, err = _run(
        mods,
        capsys,
        [
            "generate",
            "revise-slide",
            "Move",
            "--artifact",
            SYNTHETIC_ARTIFACT_ID,
            "--slide",
            "-1",
            "--json",
        ],
    )
    assert code == 64
    assert out == ""
    assert "slide index must be non-negative" in err

    secret_artifact = "ya29." + "A" * 24
    raw_path = "/".join(("", "Users", "example", "NotebookLM", "slide-deck"))
    code, out, err = _run(
        mods,
        capsys,
        [
            "generate",
            "revise-slide",
            "Move",
            "--artifact",
            secret_artifact,
            "--slide",
            "0",
            "--json",
        ],
    )
    assert code == 64
    assert out == ""
    assert "artifact not found" in err
    assert secret_artifact not in err
    assert raw_path not in err

    code, out, err = _run(
        mods,
        capsys,
        [
            "generate",
            "revise-slide",
            "--artifact",
            SYNTHETIC_ARTIFACT_ID,
            "--slide",
            "0",
            "--json",
        ],
    )
    assert code == 64
    assert out == ""
    assert "description is required" in err

    with pytest.raises(SystemExit) as excinfo:
        mods["cli"].console(
            [
                "generate",
                "revise-slide",
                "Move",
                "--artifact",
                SYNTHETIC_ARTIFACT_ID,
                "--slide",
                "0",
                "-s",
                SYNTHETIC_SOURCE_ID,
                "--json",
            ]
        )
    assert excinfo.value.code == 2
    rejected = capsys.readouterr()
    assert "unrecognized arguments" in rejected.err
    assert "-s" in rejected.err


def test_generate_closed_boundaries_remain_explicit(
    repo_root, monkeypatch, tmp_path, capsys
):
    mods = _mods(repo_root, monkeypatch, tmp_path)

    code, out, err = _run(
        mods,
        capsys,
        ["research", "wait", "-n", SYNTHETIC_NOTEBOOK_ID, "--import-all", "--json"],
    )
    assert code == 0
    assert err == ""
    assert json.loads(out)["imported"] == 1

    code, out, err = _run(
        mods, capsys, ["generate", "audio", "--language", "not-a-language", "--json"]
    )
    assert code == 64
    assert out == ""
    assert "unknown language code" in err
