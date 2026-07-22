"""Compatibility RPC alias for the frozen direct-comparison harness."""

from __future__ import annotations

import json
import urllib.parse
from typing import Any

from notebooklm.rpc.decoder import (
    XSSI_PREFIX,
    decode_batchexecute_request as decode_request,
    decode_batchexecute_response as decode_response,
)


def encode_request(decoded: Any) -> str:
    """Encode a decoded batchexecute request back to canonical fixture form."""

    return (
        "f.req="
        + urllib.parse.quote(json.dumps(decoded, separators=(",", ":")), safe="")
        + "&at=SYNTHETIC_XSRF_TOKEN&\n"
    )


__all__ = ["XSSI_PREFIX", "decode_request", "decode_response", "encode_request"]
