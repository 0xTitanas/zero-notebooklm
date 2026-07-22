"""Phase 3A2 offline notebook metadata model/service tests.

This slice consumes already-decoded *synthetic* LIST_NOTEBOOKS payloads from the
committed fake-server fixtures. It intentionally does not add live RPC sending,
CLI notebook commands, browser access, credentials, source/chat/artifact work,
or parity-row promotion.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

import import_origin_audit  # noqa: E402  (placed on sys.path by tests/conftest.py)
from notebooklm import cli, output
from notebooklm.errors import ValidationError
from notebooklm.rpc import decoder as rpc

RESPONSE_FIXTURE = "list_notebooks.response.txt"
EXPECTED_CREATED_AT = datetime.fromtimestamp(1750000000, timezone.utc)
EXPECTED_NOTEBOOK_DICT = {
    "created_at": "2025-06-15T15:06:40+00:00",
    "id": "fake-notebook-0001",
    "is_owner": True,
    "sources_count": 2,
    "title": "Phase 0 Synthetic Notebook",
}


def _fixture_payload(compat_dir):
    body = (compat_dir / "rpc_fixtures" / RESPONSE_FIXTURE).read_text(encoding="utf-8")
    payloads = rpc.decode_batchexecute_response(body)
    assert len(payloads) == 1
    return payloads[0]


def _sample_notebooks_module():
    from notebooklm import notebooks

    return notebooks


def test_notebook_metadata_module_public_surface_is_offline_only():
    notebooks = _sample_notebooks_module()
    assert set(notebooks.__all__) == {
        "Notebook",
        "NotebookMetadata",
        "OfflineNotebookMetadataService",
        "parse_list_notebooks_payload",
        "resolve_notebook",
    }
    for absent in (
        "create",
        "delete",
        "rename",
        "share",
        "send",
        "request",
        "NotebookLMClient",
        "Source",
        "Artifact",
        "MCP",
    ):
        assert not hasattr(notebooks, absent)


def test_notebooks_module_has_no_denylisted_imports():
    assert import_origin_audit.audit(roots=("notebooklm/notebooks.py",)) == []


def test_notebooks_module_avoids_live_io_and_ambient_state(repo_root):
    src = (repo_root / "notebooklm" / "notebooks.py").read_text(encoding="utf-8")
    forbidden = {
        "socket",
        "http.client",
        "urllib",
        "subprocess",
        "sqlite3",
        "Path.home",
        "expanduser",
        "os.environ",
        "browser_cookies",
        "interactive_login",
        "http_std",
        "Network.",
        "DevTools",
    }
    hits = sorted(token for token in forbidden if token in src)
    assert hits == []


def test_parse_list_notebooks_payload_matches_fixture(compat_dir):
    notebooks = _sample_notebooks_module()
    parsed = notebooks.parse_list_notebooks_payload(_fixture_payload(compat_dir))
    assert parsed == [
        notebooks.Notebook(
            id="fake-notebook-0001",
            title="Phase 0 Synthetic Notebook",
            created_at=EXPECTED_CREATED_AT,
            sources_count=2,
            is_owner=True,
        )
    ]
    assert parsed[0].as_dict() == EXPECTED_NOTEBOOK_DICT


def test_service_lists_metadata_and_source_ids_from_fixture(compat_dir):
    notebooks = _sample_notebooks_module()
    service = notebooks.OfflineNotebookMetadataService.from_list_payload(
        _fixture_payload(compat_dir)
    )
    assert service.list() == notebooks.parse_list_notebooks_payload(
        _fixture_payload(compat_dir)
    )
    assert service.list_dicts() == [EXPECTED_NOTEBOOK_DICT]
    assert service.get_source_ids("fake-notebook-0001") == [
        "fake-source-0001",
        "fake-source-0002",
    ]
    metadata = service.get_metadata("fake-notebook-0001")
    assert metadata == notebooks.NotebookMetadata(
        notebook=service.resolve("fake-notebook-0001"), sources=[]
    )
    assert metadata.as_dict() == {
        "notebook": EXPECTED_NOTEBOOK_DICT,
        "sources": [],
    }


def test_notebook_model_uses_upstream_field_names_and_mutability(compat_dir):
    service = (
        _sample_notebooks_module().OfflineNotebookMetadataService.from_list_payload(
            _fixture_payload(compat_dir)
        )
    )
    nb = service.resolve("fake-notebook-0001")
    assert [field for field in nb.__dataclass_fields__] == [
        "id",
        "title",
        "created_at",
        "sources_count",
        "is_owner",
    ]
    nb.title = "mutated"
    assert nb.title == "mutated"


def test_resolve_notebook_exact_id_prefix_and_title():
    notebooks = _sample_notebooks_module()
    items = [
        notebooks.Notebook("abc123", "Alpha"),
        notebooks.Notebook("abc999", "abc123"),
        notebooks.Notebook("def456", "Beta"),
    ]
    # Exact ID wins before title/prefix matching.
    assert notebooks.resolve_notebook(items, "abc123").title == "Alpha"
    assert notebooks.resolve_notebook(items, "def").id == "def456"
    assert notebooks.resolve_notebook(items, "Beta").id == "def456"
    service = notebooks.OfflineNotebookMetadataService(items)
    assert service.resolve("def456").title == "Beta"


def test_resolve_notebook_fails_closed_for_missing_and_ambiguous_selectors():
    notebooks = _sample_notebooks_module()
    items = [
        notebooks.Notebook("abc123", "Same"),
        notebooks.Notebook("abc999", "Same"),
    ]
    for selector in ("abc", "Same", "missing"):
        with pytest.raises(ValidationError) as exc:
            notebooks.resolve_notebook(items, selector)
        assert str(exc.value) in {
            "notebook selector is ambiguous",
            "notebook selector not found",
        }
        assert selector not in str(exc.value)


def test_service_preserves_source_ids_without_exposing_them_on_notebook_dicts():
    notebooks = _sample_notebooks_module()
    service = notebooks.OfflineNotebookMetadataService.from_list_payload(
        [
            [
                ["nb-1", "With Sources", ["src-1", "src-2"], None],
            ]
        ]
    )
    assert service.list()[0].sources_count == 2
    assert "source_ids" not in service.list_dicts()[0]
    assert service.get_source_ids("nb-1") == ["src-1", "src-2"]


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        [],
        ["not rows"],
        [["not a notebook row"]],
        [[[]]],
        [[[123, "Title", [], 1]]],
        [
            [
                [
                    "id",
                    123,
                    [],
                    1,
                ]
            ]
        ],
        [[["id", "Title", "not-source-list", 1]]],
        [[["id", "Title", [123], 1]]],
        [[["id", "Title", [], "not-created-at"]]],
        [[["", "Title", [], 1]]],
        [[["id", "", [], 1]]],
    ],
)
def test_parse_list_notebooks_payload_fails_closed_for_malformed_shapes(payload):
    notebooks = _sample_notebooks_module()
    with pytest.raises(ValidationError) as exc:
        notebooks.parse_list_notebooks_payload(payload)
    assert str(exc.value).startswith("invalid list_notebooks payload:")


def test_parse_errors_are_deterministic_and_redacted():
    notebooks = _sample_notebooks_module()
    sensitive = "__Secure-3PSID=" + "S" * 40
    payload = [[["id", sensitive, "not-source-list", sensitive]]]
    messages = []
    for _ in range(2):
        with pytest.raises(ValidationError) as exc:
            notebooks.parse_list_notebooks_payload(payload)
        messages.append(str(exc.value))
    assert messages[0] == messages[1]
    assert sensitive not in messages[0]


def test_output_render_accepts_notebook_service_dicts(compat_dir):
    notebooks = _sample_notebooks_module()
    service = notebooks.OfflineNotebookMetadataService.from_list_payload(
        _fixture_payload(compat_dir)
    )
    rendered = output.render(service.list_dicts(), json_mode=True)
    decoded = json.loads(rendered)
    assert decoded == [EXPECTED_NOTEBOOK_DICT]
    assert "Notebook(" not in rendered


def test_phase3a2_preserves_later_notebook_command_promotions():
    assert {"metadata", "summary", "create", "delete", "rename"} <= set(
        cli.IMPLEMENTED_COMMANDS
    )
