"""Phase 3B11 offline local-file source parity.

This batch promotes only deterministic local-file ingestion through the existing
``OfflineSourceService``. It reads caller-provided files from test/tmp paths and
never performs live upload RPC, auth/browser/home access, network calls, or real
NotebookLM mutation.
"""

from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SYNTHETIC_NOTEBOOK_ID = "fake-notebook-0001"


def _client():
    from notebooklm import AuthTokens, NotebookLMClient

    return NotebookLMClient(
        AuthTokens(cookies={}, csrf_token="synthetic", session_id="synthetic")
    )


def _poison_home(monkeypatch):
    monkeypatch.setattr(
        Path,
        "home",
        staticmethod(lambda: (_ for _ in ()).throw(AssertionError("real home access"))),
    )


def _load(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))
    return SimpleNamespace(cli=importlib.import_module("notebooklm.cli"))


def _run(mods, capsys, argv):
    code = mods.cli.console(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.fixture()
def local_markdown(tmp_path):
    path = tmp_path / "brief.md"
    path.write_text("# Local Brief\n\nFixture-backed file content.\n", encoding="utf-8")
    return path


def test_sources_add_file_ingests_local_file_without_upload_or_path_leak(
    monkeypatch, local_markdown
):
    _poison_home(monkeypatch)
    progress: list[tuple[int, int]] = []

    async def scenario():
        client = _client()
        source = await client.sources.add_file(
            SYNTHETIC_NOTEBOOK_ID,
            local_markdown,
            mime_type="text/markdown",
            title="Offline Brief",
            wait=True,
            on_progress=lambda sent, total: progress.append((sent, total)),
        )
        assert source.id.startswith("offline-source-")
        assert source.title == "Offline Brief"
        assert source.url is None
        assert source._type_code is None
        assert source.kind().name == "UNKNOWN"
        assert str(local_markdown) not in json.dumps(source.as_dict())

        listed = await client.sources.list(SYNTHETIC_NOTEBOOK_ID)
        assert listed[-1].id == source.id
        fulltext = await client.sources.get_fulltext(SYNTHETIC_NOTEBOOK_ID, source.id)
        assert fulltext.content == "# Local Brief\n\nFixture-backed file content.\n"
        assert fulltext.char_count == len(fulltext.content)
        guide = await client.sources.get_guide(SYNTHETIC_NOTEBOOK_ID, source.id)
        assert type(guide).__module__ == "notebooklm.types"
        assert "Offline Brief" in guide.summary

    asyncio.run(scenario())
    size = local_markdown.stat().st_size
    assert progress == [(size, size)]


def test_sources_add_file_uses_filename_title_by_default_and_validates_inputs(tmp_path):
    from notebooklm.errors import ValidationError

    file_path = tmp_path / "default-title.txt"
    file_path.write_text("plain text", encoding="utf-8")
    client = _client()

    source = asyncio.run(client.sources.add_file(SYNTHETIC_NOTEBOOK_ID, str(file_path)))
    assert source.title == "default-title.txt"

    with pytest.raises(ValidationError, match="file path must exist"):
        asyncio.run(
            client.sources.add_file(SYNTHETIC_NOTEBOOK_ID, tmp_path / "missing.md")
        )
    with pytest.raises(ValidationError, match="file path must be a regular file"):
        asyncio.run(client.sources.add_file(SYNTHETIC_NOTEBOOK_ID, tmp_path))
    with pytest.raises(ValidationError, match="source title must be non-empty text"):
        asyncio.run(client.sources.add_file(SYNTHETIC_NOTEBOOK_ID, file_path, title=""))
    with pytest.raises(ValidationError, match="mime_type cannot be empty"):
        asyncio.run(
            client.sources.add_file(SYNTHETIC_NOTEBOOK_ID, file_path, mime_type="  ")
        )


def test_cli_source_add_type_file_reads_file_contents_and_keeps_output_redacted(
    repo_root, monkeypatch, capsys, local_markdown
):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)

    code, out, err = _run(
        mods,
        capsys,
        [
            "source",
            "add",
            str(local_markdown),
            "--type",
            "file",
            "--title",
            "CLI File",
            "--json",
        ],
    )

    assert code == 0
    assert err == ""
    payload = json.loads(out)
    assert payload["added"] is True
    assert payload["source_type"] == "file"
    assert payload["source"]["title"] == "CLI File"
    assert payload["source"]["url"] is None
    assert payload["source"]["type_code"] is None
    assert str(local_markdown) not in out


def test_sources_add_file_rejects_symlink_by_default_and_redacts_path(
    tmp_path, local_markdown
):
    from notebooklm.errors import ValidationError

    link = tmp_path / "linked.md"
    try:
        link.symlink_to(local_markdown)
    except OSError as exc:  # pragma: no cover - platform/filesystem guard
        pytest.skip(f"symlink unavailable: {exc}")

    with pytest.raises(ValidationError) as excinfo:
        asyncio.run(_client().sources.add_file(SYNTHETIC_NOTEBOOK_ID, link))

    message = str(excinfo.value)
    assert "symlink" in message
    assert str(link) not in message
    assert str(local_markdown) not in message


def test_sources_add_file_wraps_pre_read_path_check_failures_without_path_leak(
    monkeypatch, local_markdown
):
    from notebooklm.errors import ValidationError

    def fail_exists(_path):
        raise PermissionError(f"blocked {local_markdown}")

    monkeypatch.setattr(Path, "exists", fail_exists)

    with pytest.raises(ValidationError) as excinfo:
        asyncio.run(_client().sources.add_file(SYNTHETIC_NOTEBOOK_ID, local_markdown))

    message = str(excinfo.value)
    assert message == "file path could not be inspected"
    assert str(local_markdown) not in message


def test_cli_source_add_type_file_wraps_pre_read_path_check_failures_without_path_leak(
    repo_root, monkeypatch, capsys, local_markdown
):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)

    def fail_is_file(_path):
        raise PermissionError(f"blocked {local_markdown}")

    monkeypatch.setattr(Path, "is_file", fail_is_file)

    code, out, err = _run(
        mods,
        capsys,
        ["source", "add", str(local_markdown), "--type", "file", "--json"],
    )

    assert code == 64
    assert out == ""
    assert "file path could not be inspected" in err
    assert str(local_markdown) not in err


def test_sources_add_file_wraps_read_failures_without_path_leak(
    monkeypatch, local_markdown
):
    from notebooklm.errors import ValidationError

    def fail_read(_path):
        raise OSError(f"boom {local_markdown}")

    monkeypatch.setattr(Path, "read_bytes", fail_read)

    with pytest.raises(ValidationError) as excinfo:
        asyncio.run(_client().sources.add_file(SYNTHETIC_NOTEBOOK_ID, local_markdown))

    message = str(excinfo.value)
    assert message == "file could not be read"
    assert str(local_markdown) not in message


def test_cli_source_add_type_file_rejects_symlink_unless_flag_is_passed(
    repo_root, monkeypatch, capsys, tmp_path, local_markdown
):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)
    link = tmp_path / "linked.md"
    try:
        link.symlink_to(local_markdown)
    except OSError as exc:  # pragma: no cover - platform/filesystem guard
        pytest.skip(f"symlink unavailable: {exc}")

    code, out, err = _run(
        mods,
        capsys,
        ["source", "add", str(link), "--type", "file", "--json"],
    )
    assert code == 64
    assert out == ""
    assert "symlink" in err
    assert str(link) not in err
    assert str(local_markdown) not in err

    code, out, err = _run(
        mods,
        capsys,
        [
            "source",
            "add",
            str(link),
            "--type",
            "file",
            "--follow-symlinks",
            "--json",
        ],
    )
    assert code == 0
    assert err == ""
    payload = json.loads(out)
    assert payload["source"]["title"] == "linked.md"
    assert payload["source"]["url"] is None
    assert str(link) not in out
    assert str(local_markdown) not in out


def test_cli_source_add_type_file_rejects_missing_file(
    repo_root, monkeypatch, capsys, tmp_path
):
    _poison_home(monkeypatch)
    mods = _load(repo_root, monkeypatch)
    missing = tmp_path / "missing.md"

    code, out, err = _run(
        mods,
        capsys,
        ["source", "add", str(missing), "--type", "file", "--json"],
    )

    assert code == 64
    assert out == ""
    assert "file path must exist" in err
    assert str(missing) not in err
