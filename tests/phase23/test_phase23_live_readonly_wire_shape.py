"""Phase 23 live-readonly wire-shape parity regressions.

These tests stay fully offline: they use the committed sanitized batchexecute
fixture as the oracle for the request shape needed by the live-readonly
upstream-vs-bare differential probe. No network, browser, keychain, home
credential discovery, or NotebookLM mutation occurs here.
"""

from __future__ import annotations

from notebooklm.rpc.decoder import decode_batchexecute_response
from notebooklm.rpc.encoder import build_request_body, encode_rpc_request
from notebooklm.rpc.types import RPCMethod


def test_rpc_encoder_matches_pinned_upstream_batchexecute_request_shape(compat_dir):
    request = encode_rpc_request(RPCMethod.LIST_NOTEBOOKS, [None, 1])

    assert request == [[["wXbhsf", "[null,1]", None, "generic"]]]

    body = build_request_body(request, "SYNTHETIC_XSRF_TOKEN")
    expected = (compat_dir / "rpc_fixtures" / "list_notebooks.request.txt").read_text(
        encoding="utf-8"
    )
    assert body == expected.strip()


def test_rpc_request_body_keeps_session_id_out_of_form_body():
    request = [[["wXbhsf", "[null,1]", None, "generic"]]]

    body = build_request_body(
        request,
        csrf_token="SYNTHETIC_XSRF_TOKEN",
        session_id="SYNTHETIC_SESSION_ID",
    )

    assert "SYNTHETIC_SESSION_ID" not in body
    assert "bl=" not in body
    assert body.endswith("&")


def test_live_chunk_decoder_tolerates_valid_json_with_byte_count_mismatch():
    body = ')]}\'\n1\n[["wrb.fr","wXbhsf","[]",null]]\n0\n'

    assert decode_batchexecute_response(body) == [[]]


def test_live_chunk_decoder_accepts_end_of_stream_without_terminal_zero():
    body = ')]}\'\n1\n[["wrb.fr","wXbhsf","[]",null]]\n'

    assert decode_batchexecute_response(body) == [[]]
