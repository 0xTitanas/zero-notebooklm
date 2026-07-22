"""Phase 3A3 offline fake RPC client seam tests.

This slice connects committed *synthetic* batchexecute fixtures to the Phase 3A1
parser and Phase 3A2 notebook metadata service. It intentionally remains an
offline fake seam only: no live RPC sending, CLI notebook commands, browser
access, credentials, mutating source/artifact work, or parity-row promotion. Later
Phase 3A7/3A8/3A9/3A12 slices extend the same fake seam with synthetic chat,
source, note, and artifact request keys while preserving the no-live-RPC boundary.
"""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import import_origin_audit  # noqa: E402  (placed on sys.path by tests/conftest.py)
from notebooklm import cli, notebooks, output
from notebooklm.rpc import decoder as rpc
from notebooklm.errors import ValidationError

LIST_REQUEST_FIXTURE = "list_notebooks.request.txt"
LIST_RESPONSE_FIXTURE = "list_notebooks.response.txt"
LIST_RPCID = "wXbhsf"
EXPECTED_NOTEBOOK_DICT = {
    "created_at": "2025-06-15T15:06:40+00:00",
    "id": "fake-notebook-0001",
    "is_owner": True,
    "sources_count": 2,
    "title": "Phase 0 Synthetic Notebook",
}


def _fixtures_dir(compat_dir: Path) -> Path:
    return compat_dir / "rpc_fixtures"


def _fake_rpc():
    from notebooklm import fake_rpc

    return fake_rpc


def _request_body(compat_dir: Path) -> str:
    return (_fixtures_dir(compat_dir) / LIST_REQUEST_FIXTURE).read_text(
        encoding="utf-8"
    )


def _client(compat_dir: Path):
    fake_rpc = _fake_rpc()
    return fake_rpc.OfflineFixtureRpcClient.from_fixture_dir(_fixtures_dir(compat_dir))


def test_fake_rpc_public_surface_is_offline_fixture_only():
    fake_rpc = _fake_rpc()
    assert set(fake_rpc.__all__) == {
        "CHAT_ASK_RPCID",
        "LIST_ARTIFACTS_RPCID",
        "LIST_NOTEBOOKS_RPCID",
        "LIST_NOTES_RPCID",
        "LIST_SOURCES_RPCID",
        "FakeRpcRequest",
        "OfflineFixtureRpcClient",
        "chat_ask_request",
        "list_artifacts_request",
        "list_notes_request",
        "list_sources_request",
        "request_from_decoded",
    }
    assert fake_rpc.LIST_NOTEBOOKS_RPCID == LIST_RPCID
    for absent in (
        "send",
        "post",
        "request",
        "session",
        "authenticate",
        "cookies",
        "create",
        "delete",
        "rename",
        "share",
        "NotebookLMClient",
        "MCP",
    ):
        assert not hasattr(fake_rpc, absent)


def test_fake_rpc_module_has_no_denylisted_imports():
    assert import_origin_audit.audit(roots=("notebooklm/fake_rpc.py",)) == []


def test_fake_rpc_module_avoids_live_io_and_ambient_state(repo_root):
    src = (repo_root / "notebooklm" / "fake_rpc.py").read_text(encoding="utf-8")
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
        "http_std",
        "Network.",
        "DevTools",
        "keyring",
        "secretstorage",
        "win32crypt",
        "browser_cookie3",
        "browsercookie",
    }
    hits = sorted(token for token in forbidden if token in src)
    assert hits == []


def test_request_from_decoded_preserves_list_notebooks_rpc_shape(compat_dir):
    fake_rpc = _fake_rpc()
    decoded = rpc.decode_batchexecute_request(_request_body(compat_dir))
    request = fake_rpc.request_from_decoded(decoded)
    assert request == fake_rpc.FakeRpcRequest(
        rpcid=LIST_RPCID,
        payload="[null,1]",
        kind="generic",
    )
    with pytest.raises(FrozenInstanceError):
        request.rpcid = "mutated"  # type: ignore[misc]


def test_fixture_client_returns_decoded_list_notebooks_payload(compat_dir):
    fake_rpc = _fake_rpc()
    client = _client(compat_dir)
    decoded_request = rpc.decode_batchexecute_request(_request_body(compat_dir))
    payloads = client.call(fake_rpc.request_from_decoded(decoded_request))
    assert payloads == rpc.decode_batchexecute_response(
        (_fixtures_dir(compat_dir) / LIST_RESPONSE_FIXTURE).read_text(encoding="utf-8")
    )
    assert payloads == [
        [
            [
                [
                    "fake-notebook-0001",
                    "Phase 0 Synthetic Notebook",
                    ["fake-source-0001", "fake-source-0002"],
                    1750000000,
                ]
            ]
        ]
    ]


def test_fixture_client_feeds_offline_notebook_metadata_service(compat_dir):
    client = _client(compat_dir)
    payload = client.list_notebooks_payload()
    service = notebooks.OfflineNotebookMetadataService.from_list_payload(payload)
    assert service.list_dicts() == [EXPECTED_NOTEBOOK_DICT]
    rendered = output.render(service.list_dicts(), json_mode=True)
    assert json.loads(rendered) == [EXPECTED_NOTEBOOK_DICT]


def test_fixture_client_validates_request_shape_not_just_rpcid(compat_dir):
    fake_rpc = _fake_rpc()
    client = _client(compat_dir)
    for request in (
        fake_rpc.FakeRpcRequest(rpcid=LIST_RPCID, payload="[null,2]", kind="generic"),
        fake_rpc.FakeRpcRequest(rpcid=LIST_RPCID, payload="[null,1]", kind="stream"),
        fake_rpc.FakeRpcRequest(rpcid="chat-rpc", payload="[null,1]", kind="generic"),
    ):
        with pytest.raises(ValidationError) as exc:
            client.call(request)
        assert str(exc.value) in {
            "fake rpc request not found",
            "fake rpc request is not supported",
        }


def test_missing_fixture_errors_are_path_redacted_and_drop_context(tmp_path):
    fake_rpc = _fake_rpc()
    missing_dir = tmp_path / "private-profile-name"
    with pytest.raises(ValidationError) as exc:
        fake_rpc.OfflineFixtureRpcClient.from_fixture_dir(missing_dir)
    assert str(exc.value) == "fake rpc fixture is unavailable"
    assert "private-profile-name" not in str(exc.value)
    assert str(tmp_path) not in str(exc.value)
    assert exc.value.__context__ is None
    assert exc.value.__cause__ is None


@pytest.mark.parametrize(
    "decoded",
    [
        None,
        {},
        [],
        [[]],
        [[[]]],
        [[[""]]],
        [[[LIST_RPCID]]],
        [[[123, "[null,1]", None, "generic"]]],
        [[[LIST_RPCID, 123, None, "generic"]]],
        [[[LIST_RPCID, "[null,1]", None, 123]]],
    ],
)
def test_request_from_decoded_fails_closed_for_malformed_shapes(decoded):
    fake_rpc = _fake_rpc()
    with pytest.raises(ValidationError) as exc:
        fake_rpc.request_from_decoded(decoded)
    assert str(exc.value).startswith("invalid fake rpc request:")


def test_fake_rpc_errors_are_deterministic_and_redacted(compat_dir):
    fake_rpc = _fake_rpc()
    sensitive = "__Secure-3PSID=" + "S" * 40
    client = _client(compat_dir)
    messages = []
    for _ in range(2):
        with pytest.raises(ValidationError) as exc:
            client.call(
                fake_rpc.FakeRpcRequest(
                    rpcid=sensitive, payload=sensitive, kind="generic"
                )
            )
        messages.append(str(exc.value))
    assert messages[0] == messages[1]
    assert sensitive not in messages[0]


def test_phase3a3_preserves_later_notebook_command_promotions():
    assert {"metadata", "summary", "create", "delete", "rename"} <= set(
        cli.IMPLEMENTED_COMMANDS
    )
