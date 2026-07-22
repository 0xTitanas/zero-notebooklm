"""Stdlib-only batchexecute request encoder surface."""

from __future__ import annotations

import json
import urllib.parse
from typing import Any

from .types import RPCMethod


def encode_rpc_request(
    method: RPCMethod, params: list[Any], rpc_id_override: str | None = None
) -> list[Any]:
    rpc_id = rpc_id_override or method.value
    inner = [rpc_id, json.dumps(params, separators=(",", ":")), None, "generic"]
    return [[inner]]


def build_request_body(
    rpc_request: list[Any], csrf_token: str | None = None, session_id: str | None = None
) -> str:
    fields = {"f.req": json.dumps(rpc_request, separators=(",", ":"))}
    if csrf_token is not None:
        fields["at"] = csrf_token
    # The session id belongs in the batchexecute URL (``f.sid``), not in the
    # form body. Keep the parameter for upstream-compatible call signatures.
    _ = session_id
    return urllib.parse.urlencode(fields) + "&"


def nest_source_ids(ids: list[str] | None, depth: int) -> list[Any]:
    if depth < 1:
        raise ValueError(f"depth must be >= 1, got {depth}")
    if not ids:
        return []
    result: list[Any] = list(ids)
    for _ in range(depth):
        result = [[item] for item in result]
    return result
