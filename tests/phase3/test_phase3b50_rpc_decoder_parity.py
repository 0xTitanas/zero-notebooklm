from __future__ import annotations

import json

import pytest

from notebooklm.exceptions import RPCError, RateLimitError, UnknownRPCMethodError
from notebooklm.rpc import decoder

PFX = ")]}'"


def _body(*rows: list[object]) -> str:
    return PFX + "\n" + json.dumps(list(rows), separators=(",", ":"))


def test_decode_response_selects_requested_rpc_id_not_first_payload():
    raw = _body(
        ["wrb.fr", "otherRpc", json.dumps(["wrong"]), None],
        ["wrb.fr", "targetRpc", json.dumps(["right"]), None],
    )

    assert decoder.decode_response(raw, "targetRpc") == ["right"]


def test_decode_response_absent_requested_rpc_id_raises_unknown_even_when_null_allowed():
    raw = _body(["wrb.fr", "otherRpc", json.dumps(["wrong"]), None])

    with pytest.raises(UnknownRPCMethodError) as exc:
        decoder.decode_response(raw, "targetRpc", allow_null=True)

    assert exc.value.method_id == "targetRpc"
    assert exc.value.found_ids == ["otherRpc"]


def test_decode_response_present_null_honors_allow_null_but_missing_never_does():
    raw = _body(["wrb.fr", "targetRpc", None, None, None, None])

    assert decoder.decode_response(raw, "targetRpc", allow_null=True) is None
    with pytest.raises(RPCError) as exc:
        decoder.decode_response(raw, "targetRpc")
    assert exc.value.method_id == "targetRpc"
    assert exc.value.found_ids == ["targetRpc"]


def test_decode_response_prefers_later_non_null_frame_for_same_rpc_id():
    raw = _body(
        ["wrb.fr", "targetRpc", None, None, None, None],
        ["wrb.fr", "targetRpc", json.dumps(["final"]), None],
    )

    assert decoder.decode_response(raw, "targetRpc") == ["final"]


def test_decode_response_er_frame_maps_to_rpc_error_with_found_ids():
    raw = _body(["er", "targetRpc", 429])

    with pytest.raises(RPCError) as exc:
        decoder.decode_response(raw, "targetRpc")

    assert exc.value.method_id == "targetRpc"
    assert exc.value.rpc_code == 429
    assert exc.value.found_ids == ["targetRpc"]
    assert "rate limit" in str(exc.value).lower()


def test_decode_response_user_displayable_error_maps_to_rate_limit():
    raw = _body(
        [
            "wrb.fr",
            "targetRpc",
            None,
            None,
            None,
            [8, None, [["type.googleapis.com/google.rpc.UserDisplayableError"]]],
        ]
    )

    with pytest.raises(RateLimitError) as exc:
        decoder.decode_response(raw, "targetRpc")

    assert exc.value.method_id == "targetRpc"
    assert exc.value.rpc_code == "USER_DISPLAYABLE_ERROR"


def test_byte_count_mismatch_metric_increments_for_tolerated_chunk_frames():
    decoder.reset_byte_count_mismatch_total()

    assert decoder.parse_chunked_response("1\n[]\n") == [[]]

    assert decoder.byte_count_mismatch_total() == 1


def test_safe_index_raises_structured_unknown_rpc_method_error_on_missing_path():
    with pytest.raises(UnknownRPCMethodError) as exc:
        decoder.safe_index([["ok"]], 0, 1, method_id=123, source="unit.safe_index")

    err = exc.value
    assert "safe_index drift at path (0,)[1]" in str(err)
    assert err.method_id == 123
    assert err.path == (0,)
    assert err.source == "unit.safe_index"
    assert err.data_at_failure == "['ok']"
    assert isinstance(err.__cause__, IndexError)


def test_safe_index_allows_tuple_containers_and_string_leaves():
    assert (
        decoder.safe_index(([["leaf"]],), 0, 0, 0, method_id="rpc", source="unit")
        == "leaf"
    )


@pytest.mark.parametrize(
    ("value", "type_name", "data_at_failure"),
    [
        ("abc", "str", "'abc'"),
        (b"abc", "bytes", "b'abc'"),
        (bytearray(b"abc"), "bytearray", "bytearray(b'abc')"),
    ],
)
def test_safe_index_rejects_stringlike_intermediate_containers(
    value, type_name, data_at_failure
):
    with pytest.raises(UnknownRPCMethodError) as exc:
        decoder.safe_index([value], 0, 0, method_id="rpc", source="unit.safe_index")

    err = exc.value
    assert f"cannot index into {type_name}" in str(err)
    assert err.method_id == "rpc"
    assert err.path == (0,)
    assert err.source == "unit.safe_index"
    assert err.data_at_failure == data_at_failure
