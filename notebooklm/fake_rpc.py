"""Offline fixture-backed RPC seam for synthetic Phase 3A tests.

This module validates committed synthetic batchexecute request fixtures, decodes
matching synthetic responses through :mod:`notebooklm.rpc`, and exposes tiny
read-only seams for offline notebook metadata and chat tests. It performs no live
network I/O, authentication, browser access, credential reads, or NotebookLM
mutation.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, NoReturn

from .errors import ValidationError
from .rpc import decoder as rpc

LIST_NOTEBOOKS_RPCID = "wXbhsf"
LIST_SOURCES_RPCID = "list-sources-rpc"
LIST_NOTES_RPCID = "list-notes-rpc"
LIST_ARTIFACTS_RPCID = "gArtLc"
CHAT_ASK_RPCID = "chat-rpc"
_LIST_NOTEBOOKS_REQUEST = "list_notebooks.request.txt"
_LIST_NOTEBOOKS_RESPONSE = "list_notebooks.response.txt"
_LIST_SOURCES_REQUEST = "list_sources.request.txt"
_LIST_SOURCES_RESPONSE = "list_sources.response.txt"
_LIST_NOTES_REQUEST = "list_notes.request.txt"
_LIST_NOTES_RESPONSE = "list_notes.response.txt"
_LIST_ARTIFACTS_REQUEST = "list_artifacts.request.txt"
_LIST_ARTIFACTS_RESPONSE = "list_artifacts.response.txt"
_CHAT_ASK_REQUEST = "chat_ask.request.txt"
_CHAT_ASK_RESPONSE = "chat_ask.streaming.response.txt"


def _fail_request(reason: str) -> NoReturn:
    """Raise a deterministic, input-redacted fake request validation error."""

    raise ValidationError(f"invalid fake rpc request: {reason}")


def _fail_lookup(message: str) -> NoReturn:
    """Raise a deterministic, input-redacted fake client lookup error."""

    raise ValidationError(message)


@dataclass(frozen=True)
class FakeRpcRequest:
    """Validated synthetic batchexecute request key."""

    rpcid: str
    payload: str
    kind: str = "generic"


def request_from_decoded(decoded: Any) -> FakeRpcRequest:
    """Build a fake request key from decoded batchexecute ``f.req`` data."""

    if not isinstance(decoded, list) or len(decoded) != 1:
        _fail_request("expected outer singleton list")
    batch = decoded[0]
    if not isinstance(batch, list) or len(batch) != 1:
        _fail_request("expected singleton request batch")
    row = batch[0]
    if not isinstance(row, list) or len(row) < 4:
        _fail_request("request row is malformed")
    rpcid, payload, _unused, kind = row[:4]
    if not isinstance(rpcid, str) or rpcid == "":
        _fail_request("rpcid must be non-empty text")
    if not isinstance(payload, str) or payload == "":
        _fail_request("payload must be non-empty text")
    if not isinstance(kind, str) or kind == "":
        _fail_request("kind must be non-empty text")
    return FakeRpcRequest(rpcid=rpcid, payload=payload, kind=kind)


def chat_ask_request(notebook_id: str, question: str) -> FakeRpcRequest:
    """Build the synthetic chat request key for the fake RPC seam."""

    if not isinstance(notebook_id, str) or notebook_id == "":
        _fail_request("notebook id must be non-empty text")
    if not isinstance(question, str) or question == "":
        _fail_request("question must be non-empty text")
    payload = json.dumps([notebook_id, question], separators=(",", ":"))
    return FakeRpcRequest(rpcid=CHAT_ASK_RPCID, payload=payload, kind="generic")


def list_sources_request(notebook_id: str) -> FakeRpcRequest:
    """Build the synthetic list-sources request key for the fake RPC seam."""

    if not isinstance(notebook_id, str) or notebook_id == "":
        _fail_request("notebook id must be non-empty text")
    payload = json.dumps([notebook_id], separators=(",", ":"))
    return FakeRpcRequest(rpcid=LIST_SOURCES_RPCID, payload=payload, kind="generic")


def list_notes_request(notebook_id: str) -> FakeRpcRequest:
    """Build the synthetic list-notes request key for the fake RPC seam."""

    if not isinstance(notebook_id, str) or notebook_id == "":
        _fail_request("notebook id must be non-empty text")
    payload = json.dumps([notebook_id], separators=(",", ":"))
    return FakeRpcRequest(rpcid=LIST_NOTES_RPCID, payload=payload, kind="generic")


def list_artifacts_request(notebook_id: str) -> FakeRpcRequest:
    """Build the synthetic list-artifacts request key for the fake RPC seam."""

    if not isinstance(notebook_id, str) or notebook_id == "":
        _fail_request("notebook id must be non-empty text")
    payload = json.dumps([notebook_id], separators=(",", ":"))
    return FakeRpcRequest(rpcid=LIST_ARTIFACTS_RPCID, payload=payload, kind="generic")


def _read_fixture(fixture_dir: Path, name: str) -> str:
    text: str | None = None
    try:
        text = (fixture_dir / name).read_text(encoding="utf-8")
    except OSError:
        pass
    if text is None:
        raise ValidationError("fake rpc fixture is unavailable")
    return text


class OfflineFixtureRpcClient:
    """Read-only fake RPC client over committed synthetic fixture pairs."""

    def __init__(self, responses_by_request: dict[FakeRpcRequest, str]) -> None:
        self._responses_by_request = dict(responses_by_request)

    @classmethod
    def from_fixture_dir(cls, fixture_dir: str | Path) -> "OfflineFixtureRpcClient":
        path = Path(fixture_dir)
        request_text = _read_fixture(path, _LIST_NOTEBOOKS_REQUEST)
        response_text = _read_fixture(path, _LIST_NOTEBOOKS_RESPONSE)
        request = request_from_decoded(rpc.decode_batchexecute_request(request_text))
        if request.rpcid != LIST_NOTEBOOKS_RPCID:
            _fail_request("list_notebooks fixture rpcid mismatch")

        chat_request_text = _read_fixture(path, _CHAT_ASK_REQUEST)
        chat_response_text = _read_fixture(path, _CHAT_ASK_RESPONSE)
        chat_request = request_from_decoded(
            rpc.decode_batchexecute_request(chat_request_text)
        )
        if chat_request.rpcid != CHAT_ASK_RPCID:
            _fail_request("chat ask fixture rpcid mismatch")

        sources_request_text = _read_fixture(path, _LIST_SOURCES_REQUEST)
        sources_response_text = _read_fixture(path, _LIST_SOURCES_RESPONSE)
        sources_request = request_from_decoded(
            rpc.decode_batchexecute_request(sources_request_text)
        )
        if sources_request.rpcid != LIST_SOURCES_RPCID:
            _fail_request("list sources fixture rpcid mismatch")

        notes_request_text = _read_fixture(path, _LIST_NOTES_REQUEST)
        notes_response_text = _read_fixture(path, _LIST_NOTES_RESPONSE)
        notes_request = request_from_decoded(
            rpc.decode_batchexecute_request(notes_request_text)
        )
        if notes_request.rpcid != LIST_NOTES_RPCID:
            _fail_request("list notes fixture rpcid mismatch")

        artifacts_request_text = _read_fixture(path, _LIST_ARTIFACTS_REQUEST)
        artifacts_response_text = _read_fixture(path, _LIST_ARTIFACTS_RESPONSE)
        artifacts_request = request_from_decoded(
            rpc.decode_batchexecute_request(artifacts_request_text)
        )
        if artifacts_request.rpcid != LIST_ARTIFACTS_RPCID:
            _fail_request("list artifacts fixture rpcid mismatch")

        return cls(
            {
                request: response_text,
                chat_request: chat_response_text,
                sources_request: sources_response_text,
                notes_request: notes_response_text,
                artifacts_request: artifacts_response_text,
            }
        )

    def call(self, request: FakeRpcRequest) -> list[Any]:
        if not isinstance(request, FakeRpcRequest):
            _fail_request("expected FakeRpcRequest")
        response_text = self._responses_by_request.get(request)
        if response_text is None:
            if request.rpcid == LIST_NOTEBOOKS_RPCID:
                _fail_lookup("fake rpc request is not supported")
            _fail_lookup("fake rpc request not found")
        return rpc.decode_batchexecute_response(response_text)

    def list_notebooks_payload(self) -> Any:
        request = FakeRpcRequest(
            rpcid=LIST_NOTEBOOKS_RPCID,
            payload="[null,1]",
            kind="generic",
        )
        payloads = self.call(request)
        if len(payloads) != 1:
            raise ValidationError("fake rpc response is not supported")
        return payloads[0]

    def chat_ask_payload(self, notebook_id: str, question: str) -> Any:
        payloads = self.call(chat_ask_request(notebook_id, question))
        if len(payloads) != 1:
            raise ValidationError("fake rpc response is not supported")
        return payloads[0]

    def list_sources_payload(self, notebook_id: str) -> Any:
        payloads = self.call(list_sources_request(notebook_id))
        if len(payloads) != 1:
            raise ValidationError("fake rpc response is not supported")
        return payloads[0]

    def list_notes_payload(self, notebook_id: str) -> Any:
        payloads = self.call(list_notes_request(notebook_id))
        if len(payloads) != 1:
            raise ValidationError("fake rpc response is not supported")
        return payloads[0]

    def list_artifacts_payload(self, notebook_id: str) -> Any:
        payloads = self.call(list_artifacts_request(notebook_id))
        if len(payloads) != 1:
            raise ValidationError("fake rpc response is not supported")
        return payloads[0]


__all__ = [
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
]
