"""Phase 3A8 offline Python sources API over committed synthetic fixtures.

This slice promotes only a read-only, fixture-backed ``client.sources`` foothold.
It decodes committed synthetic list-sources fixtures, links them to the existing
synthetic notebook metadata, and keeps all source mutation, source upload,
guide generation, CLI ``source`` promotion, live RPC, browser/auth, and
credential access out of scope.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import import_origin_audit  # noqa: E402  (placed on sys.path by tests/conftest.py)

SYNTHETIC_NOTEBOOK_ID = "fake-notebook-0001"
EXPECTED_NOTEBOOK_DICT = {
    "created_at": "2025-06-15T15:06:40+00:00",
    "id": SYNTHETIC_NOTEBOOK_ID,
    "is_owner": True,
    "sources_count": 2,
    "title": "Phase 0 Synthetic Notebook",
}
EXPECTED_SOURCE_DICTS = [
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
EXPECTED_SOURCE_SUMMARIES = [
    {
        "kind": "WEB_PAGE",
        "title": "Synthetic Web Source",
        "url": "https://example.test/notebooklm-bare/source",
    },
    {"kind": "PASTED_TEXT", "title": "Synthetic Pasted Text Source", "url": None},
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


def test_root_exports_offline_sources_api_models_and_golden_methods(python_api):
    import notebooklm

    required = {
        "Source",
        "SourceStatus",
        "SourceSummary",
        "SourceType",
    }
    for name in required:
        assert hasattr(notebooklm, name)
    assert not hasattr(notebooklm, "SourcesAPI")

    assert python_api["subclients"]["sources"]["async_methods"] == [
        "add_drive",
        "add_file",
        "add_text",
        "add_url",
        "check_freshness",
        "delete",
        "get",
        "get_fulltext",
        "get_guide",
        "get_or_none",
        "list",
        "refresh",
        "rename",
        "wait_for_sources",
        "wait_until_ready",
        "wait_until_registered",
    ]


def test_source_fixture_pair_is_committed_and_decoded_through_fake_rpc(compat_dir):
    from notebooklm.fake_rpc import (
        LIST_SOURCES_RPCID,
        OfflineFixtureRpcClient,
        list_sources_request,
    )

    fixture_dir = compat_dir / "rpc_fixtures"
    assert (fixture_dir / "list_sources.request.txt").is_file()
    assert (fixture_dir / "list_sources.response.txt").is_file()

    client = OfflineFixtureRpcClient.from_fixture_dir(fixture_dir)
    request = list_sources_request(SYNTHETIC_NOTEBOOK_ID)
    assert request.rpcid == LIST_SOURCES_RPCID
    payload = client.list_sources_payload(SYNTHETIC_NOTEBOOK_ID)
    assert payload[0][0] == "fake-source-0001"
    assert payload[1][0] == "fake-source-0002"


def test_offline_client_lists_and_gets_sources_without_home(monkeypatch):
    _poison_home(monkeypatch)

    async def scenario():
        client = _client()
        sources = await client.sources.list(SYNTHETIC_NOTEBOOK_ID)
        assert [source.as_dict() for source in sources] == EXPECTED_SOURCE_DICTS
        assert await client.notebooks.get_source_ids(SYNTHETIC_NOTEBOOK_ID) == [
            "fake-source-0001",
            "fake-source-0002",
        ]

        first = await client.sources.get(SYNTHETIC_NOTEBOOK_ID, "fake-source-0001")
        assert first is not None
        assert first.as_dict() == EXPECTED_SOURCE_DICTS[0]
        assert (
            await client.sources.get_or_none(SYNTHETIC_NOTEBOOK_ID, "missing-source")
        ) is None
        assert (
            await client.sources.get(SYNTHETIC_NOTEBOOK_ID, "missing-source")
        ) is None

    asyncio.run(scenario())


def test_notebook_metadata_embeds_source_summaries(monkeypatch):
    _poison_home(monkeypatch)

    async def scenario():
        client = _client()
        metadata = await client.notebooks.get_metadata(SYNTHETIC_NOTEBOOK_ID)
        assert metadata.as_dict() == {
            "notebook": EXPECTED_NOTEBOOK_DICT,
            "sources": EXPECTED_SOURCE_SUMMARIES,
        }

    asyncio.run(scenario())


def test_source_models_match_pinned_enum_names_and_redacted_dict_shape():
    from notebooklm import Source, SourceStatus, SourceSummary, SourceType

    assert SourceStatus.READY.value == 2
    assert SourceType.WEB_PAGE.value == "web_page"
    assert SourceType.PASTED_TEXT.value == "pasted_text"

    source = Source(
        "custom-source",
        title="Custom",
        url="https://example.test/custom",
        _type_code=1,
        status=SourceStatus.READY,
    )
    assert source.as_dict() == {
        "id": "custom-source",
        "title": "Custom",
        "url": "https://example.test/custom",
        "type_code": 1,
        "created_at": None,
        "status": "READY",
    }
    assert SourceSummary(
        SourceType.WEB_PAGE, "Custom", "https://example.test/custom"
    ).as_dict() == {
        "kind": "WEB_PAGE",
        "title": "Custom",
        "url": "https://example.test/custom",
    }


def test_sources_list_strict_missing_notebook_fails_without_echoing_private_input(
    monkeypatch,
):
    _poison_home(monkeypatch)
    synthetic_home = "/".join(("", "Users", "example"))
    private_selector = "private ya" + "29." + "S" * 40 + f" {synthetic_home}/notebook"

    async def scenario():
        client = _client()
        assert await client.sources.list(private_selector) == []
        with pytest.raises(Exception) as excinfo:
            await client.sources.list(private_selector, strict=True)
        message = str(excinfo.value)
        assert message == "source notebook not found"
        assert "ya29." not in message
        assert synthetic_home not in message
        assert excinfo.value.__context__ is None
        assert excinfo.value.__cause__ is None

    asyncio.run(scenario())


def test_source_add_file_is_local_offline_only(tmp_path):
    source_file = tmp_path / "local-file.pdf"
    source_file.write_text("offline pdf stand-in", encoding="utf-8")
    source = asyncio.run(
        _client().sources.add_file(
            SYNTHETIC_NOTEBOOK_ID,
            source_file,
            mime_type="application/pdf",
            title="Local PDF",
        )
    )

    assert source.title == "Local PDF"
    assert source.url is None
    assert source._type_code is None


def test_source_add_drive_is_fixture_backed():
    source = asyncio.run(
        _client().sources.add_drive(
            SYNTHETIC_NOTEBOOK_ID, "drive-file-id", "Drive Title"
        )
    )

    assert source.title == "Drive Title"
    assert source.url == "gdrive://drive-file-id"
    assert source.kind().name == "GOOGLE_DOCS"

    slides = asyncio.run(
        _client().sources.add_drive(
            SYNTHETIC_NOTEBOOK_ID,
            "slides-file-id",
            "Slides Title",
            "application/vnd.google-apps.presentation",
        )
    )

    assert slides.title == "Slides Title"
    assert slides.url == "gdrive://slides-file-id"
    assert slides.kind().name == "GOOGLE_SLIDES"


def test_phase3a8_keeps_unrelated_cli_surfaces_unpromoted():
    from notebooklm import cli

    assert {"list", "use", "artifact", "ask", "metadata", "summary"} <= set(
        cli.IMPLEMENTED_COMMANDS
    )
    assert "download" in cli.IMPLEMENTED_COMMANDS
    assert "generate" in cli.IMPLEMENTED_COMMANDS


def test_phase3a8_python_sources_wiring_is_stdlib_and_offline_only(repo_root):
    assert (
        import_origin_audit.audit(
            roots=(
                "notebooklm/__init__.py",
                "notebooklm/client.py",
                "notebooklm/fake_rpc.py",
                "notebooklm/notebooks.py",
                "notebooklm/sources.py",
            ),
        )
        == []
    )
    src = "\n".join(
        (repo_root / "notebooklm" / name).read_text(encoding="utf-8")
        for name in ("client.py", "fake_rpc.py", "notebooks.py", "sources.py")
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
