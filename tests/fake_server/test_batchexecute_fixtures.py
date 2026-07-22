"""Fake-server fixtures and direct ZeroNotebookLM parser probes.

Loads the sanitized batchexecute request/response fixture pairs under
``compat/rpc_fixtures/`` and exercises *real* decoding behavior against them with a
reference (test-oracle) decoder that models the upstream wire contract — the
``)]}'`` XSSI guard, the ``wrb.fr`` envelope, and the JSON-in-string double-parse.
This proves the committed fixtures are structurally faithful and fully sanitized,
which is what ``compat/rpc_fixtures/README.md`` promises.

The matching ZeroNotebookLM parser probes import ``notebooklm.rpc.decoder``. Phase
10 keeps the fake-server contract broad: every committed request/response pair is
exercised against the same sanitized fixtures.

stdlib + committed fixtures only. No upstream ``notebooklm`` import, no network, no
account / cookie / token, and no live runtime side effects.
"""

from __future__ import annotations

import importlib
import json
import re
import urllib.parse
from pathlib import Path

import pytest

ZERO_RPC_MODULE = "notebooklm.rpc.decoder"
XSSI_PREFIX = ")]}'"

# Imported by string so this module carries no static dependency on the runtime
# at collection time; Phase 10 verifies the decoder against every committed
# fake-server fixture pair.

RESPONSE_FIXTURES = (
    "chat_ask.streaming.response.txt",
    "list_artifacts.response.txt",
    "list_notebooks.response.txt",
    "list_notes.response.txt",
    "list_sources.response.txt",
)
REQUEST_FIXTURES = (
    "chat_ask.request.txt",
    "list_artifacts.request.txt",
    "list_notebooks.request.txt",
    "list_notes.request.txt",
    "list_sources.request.txt",
)

# Real Google credential/session formats. A sanitized fixture must match NONE of
# these (mirrors the suite-wide secret scan, applied locally to the fixtures the
# parser tests load).
SECRET_PATTERNS = (
    re.compile(r"ya29\.[A-Za-z0-9_\-]{20,}"),  # oauth access token
    re.compile(r"\b1//[A-Za-z0-9_\-]{30,}"),  # oauth refresh token
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),  # pem private key
    re.compile(
        r"\b(?:__Secure-[13]PSID|__Secure-[13]PAPISID|SAPISID|APISID|HSID|SSID|SIDCC|NID)"
        r"=[A-Za-z0-9_./+\-]{12,}"
    ),  # google auth cookies
)


def _fixtures_dir(compat_dir: Path) -> Path:
    return compat_dir / "rpc_fixtures"


def _zero_rpc():
    """Import the ZeroNotebookLM RPC decoder."""
    return importlib.import_module(ZERO_RPC_MODULE)


def _reference_decode_response(text: str):
    """Test-oracle decoder for a batchexecute response."""
    assert text.startswith(XSSI_PREFIX), "response missing XSSI guard prefix"
    outer = json.loads(text[len(XSSI_PREFIX) :])
    rows = [r for r in outer if r and r[0] == "wrb.fr"]
    assert rows, "no wrb.fr row in envelope"
    return [json.loads(r[2]) for r in rows]


def _reference_decode_request(text: str):
    """Test-oracle decoder for a form-encoded batchexecute request body."""
    fields = urllib.parse.parse_qs(text.strip(), keep_blank_values=True)
    assert "f.req" in fields, "request missing f.req field"
    return json.loads(fields["f.req"][0])


# --------------------------------------------------------------------------- #
# Upstream prong (PASS): the reference decoder really parses each fixture.
# --------------------------------------------------------------------------- #


def test_all_fixtures_present_and_paired(compat_dir):
    fx = _fixtures_dir(compat_dir)
    for name in RESPONSE_FIXTURES + REQUEST_FIXTURES:
        assert (fx / name).is_file(), f"missing fixture: {name}"
    assert (fx / "wire_shape.json").is_file()
    assert (fx / "README.md").is_file()


@pytest.mark.parametrize("name", RESPONSE_FIXTURES)
def test_response_fixture_decodes(compat_dir, name):
    body = (_fixtures_dir(compat_dir) / name).read_text(encoding="utf-8")
    payloads = _reference_decode_response(body)
    assert payloads, "no decodable wrb.fr payload"
    # Every nested payload is itself valid JSON (the second parse already ran).
    assert all(isinstance(p, (list, dict, str)) for p in payloads)


@pytest.mark.parametrize("name", REQUEST_FIXTURES)
def test_request_fixture_decodes(compat_dir, name):
    body = (_fixtures_dir(compat_dir) / name).read_text(encoding="utf-8")
    req = _reference_decode_request(body)
    # Outer batchexecute form is [[[rpcid, payload, ...]]].
    assert req and req[0] and req[0][0], "empty f.req batchexecute body"
    assert isinstance(req[0][0][0], str) and req[0][0][0], "missing rpcid"


def test_fixtures_are_sanitized(compat_dir):
    fx = _fixtures_dir(compat_dir)
    combined = "".join(
        (fx / n).read_text(encoding="utf-8")
        for n in RESPONSE_FIXTURES + REQUEST_FIXTURES
    )
    # Positive evidence the fixtures are synthetic placeholders…
    assert (
        ("SYNTHETIC" in combined)
        or ("synthetic" in combined)
        or ("fake-notebook" in combined)
    )
    # …and negative evidence: no real credential/session material.
    for rx in SECRET_PATTERNS:
        assert not rx.search(combined), f"fixture matched secret pattern {rx.pattern!r}"


# --------------------------------------------------------------------------- #
# ZeroNotebookLM prong: exercise the package decoder against every fixture.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", RESPONSE_FIXTURES)
def test_zero_parser_decodes_response(compat_dir, name):
    rpc = _zero_rpc()
    body = (_fixtures_dir(compat_dir) / name).read_text(encoding="utf-8")
    assert rpc.decode_batchexecute_response(body) == _reference_decode_response(body)


@pytest.mark.parametrize("name", REQUEST_FIXTURES)
def test_zero_decoder_roundtrips_request(compat_dir, name):
    rpc = _zero_rpc()
    body = (_fixtures_dir(compat_dir) / name).read_text(encoding="utf-8")
    decoded = rpc.decode_batchexecute_request(body)
    assert decoded == _reference_decode_request(body)
    encoded = "f.req=" + urllib.parse.quote(
        json.dumps(decoded, separators=(",", ":")), safe=""
    ) + "&at=SYNTHETIC_XSRF_TOKEN&\n"
    assert encoded == body
