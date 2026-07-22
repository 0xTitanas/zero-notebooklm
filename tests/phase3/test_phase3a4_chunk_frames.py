"""Phase 3A4 offline batchexecute chunk-frame parser hardening.

This slice stays inside the offline parser boundary. It teaches the Phase 3A1
``decode_batchexecute_response`` foothold to accept the length-prefixed chunk
shape used by batchexecute ``rt=c`` streaming responses, while preserving the
existing simple XSSI+JSON envelope fixture behavior.

No live NotebookLM RPC, browser/auth/cookie, credential, CLI notebook command,
mutation, or parity-row promotion belongs in this slice.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from notebooklm import cli
from notebooklm.errors import ValidationError
from notebooklm.rpc import decoder as rpc

PFX = ")]}'"
CHAT_RPCID = "chat-rpc"


def _fixture(compat_dir: Path, name: str) -> str:
    return (compat_dir / "rpc_fixtures" / name).read_text(encoding="utf-8")


def _wrb_chunk(rpcid: str, payload) -> str:
    return json.dumps(
        [["wrb.fr", rpcid, json.dumps(payload), None, None, None, "generic"]],
        separators=(",", ":"),
    )


def _length_framed(*chunks: str) -> str:
    body = PFX + "\n"
    for chunk in chunks:
        body += f"{len(chunk.encode('utf-8'))}\n{chunk}\n"
    body += "0\n"
    return body


def _declared_length_frame(length: int, chunk: str, *, trailer: str = "0\n") -> str:
    return PFX + f"\n{length}\n{chunk}\n" + trailer


def test_decode_response_still_accepts_existing_simple_streaming_fixture(compat_dir):
    body = _fixture(compat_dir, "chat_ask.streaming.response.txt")
    assert rpc.decode_batchexecute_response(body) == [
        ["Phase 0 synthetic answer chunk.", [], []]
    ]


def test_decode_response_accepts_length_prefixed_chat_chunk_frame():
    chunk = _wrb_chunk(CHAT_RPCID, ["Phase 3A4 synthetic answer chunk.", [], []])
    body = _length_framed(chunk)

    assert rpc.decode_batchexecute_response(body) == [
        ["Phase 3A4 synthetic answer chunk.", [], []]
    ]


def test_decode_response_accepts_multiple_length_prefixed_chunks_in_order():
    first = _wrb_chunk(CHAT_RPCID, ["first synthetic chunk", [], []])
    second = _wrb_chunk(CHAT_RPCID, ["second synthetic chunk", [], []])

    assert rpc.decode_batchexecute_response(_length_framed(first, second)) == [
        ["first synthetic chunk", [], []],
        ["second synthetic chunk", [], []],
    ]


def test_decode_response_chunk_frames_fail_closed_and_redacted():
    secret = "__Secure-3PSID" + "=" + "S" * 40
    valid_first = _wrb_chunk(CHAT_RPCID, ["first synthetic chunk " + secret, [], []])
    malformed_json = "not-json-" + secret
    cases = {
        "non-numeric-second-length": (
            PFX
            + f"\n{len(valid_first.encode('utf-8'))}\n{valid_first}\n"
            + "not-a-length-"
            + secret
            + "\n[]\n",
            "malformed chunk length",
        ),
        "malformed-chunk-json": (
            _length_framed(malformed_json),
            "malformed envelope JSON",
        ),
        "byte-count-mismatch-with-malformed-json": (
            PFX + "\n999\n[]" + secret + "\n0\n",
            "malformed envelope JSON",
        ),
        "trailing-after-zero": (PFX + "\n0\n" + secret, "trailing chunk data"),
        "malformed-chunk-text": (
            _declared_length_frame(1, "é"),
            "malformed envelope JSON",
        ),
        "empty-terminal-frame-no-payload": (PFX + "\n0\n", "missing wrb.fr payload"),
    }
    for case, (body, reason) in cases.items():
        with pytest.raises(ValidationError) as exc:
            rpc.decode_batchexecute_response(body)
        msg = str(exc.value)
        assert reason in msg, case
        assert secret not in msg, case
        assert "batchexecute" in msg.lower(), case


def test_phase3a4_preserves_later_notebook_command_promotions():
    assert "metadata" in cli.IMPLEMENTED_COMMANDS
    assert "summary" in cli.IMPLEMENTED_COMMANDS
    assert "share" in cli.IMPLEMENTED_COMMANDS
    assert {"create", "delete", "rename"} <= set(cli.IMPLEMENTED_COMMANDS)
