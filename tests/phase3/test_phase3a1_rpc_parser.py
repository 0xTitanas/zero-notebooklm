"""Phase 3A1 batchexecute fixture parser tests (RED->GREEN, stdlib + fixtures only).

These tests target only the Phase 3A1 slice: the tiny, fail-closed batchexecute
decoder in ``notebooklm.rpc`` that reads the committed *synthetic* fixtures under
``compat/rpc_fixtures/``. They assert that:

  * the happy-path decode of the committed ``list_notebooks`` request/response
    fixtures matches an inline reference oracle and the known synthetic values
    (XSSI guard stripped, ``wrb.fr`` envelope selected, nested payload re-parsed);
  * every malformed shape fails closed with the project-local
    :class:`notebooklm.errors.ValidationError`; and
  * those errors are deterministic and *redacted* -- the raised message never
    echoes the decoded input, so no synthetic cookie/token/URL/path can leak.

They never touch the network, a real Google/NotebookLM account, cookies, a
browser, an OS keychain, or any credential -- only the stdlib and the committed
fixtures. The bare RPC *encoder*, CLI surface, and notebook service are out of
scope for this slice and are intentionally not exercised here.
"""

from __future__ import annotations

import json
import urllib.parse

import pytest

import import_origin_audit  # noqa: E402  (placed on sys.path by tests/conftest.py)

from notebooklm.rpc import decoder as rpc
from notebooklm.errors import NotebookLMError, ValidationError, exit_code_for

RESPONSE_FIXTURE = "list_notebooks.response.txt"
REQUEST_FIXTURE = "list_notebooks.request.txt"

# Literal XSSI guard, declared here independently of the module under test so that
# malformed-input construction below does not merely restate the implementation.
PFX = ")]}'"

# Known-good decoded values for the committed synthetic fixtures. Independently
# recomputed by the inline reference oracle (``_reference_decode_*``) below.
EXPECTED_RESPONSE_PAYLOADS = [
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
EXPECTED_REQUEST_FREQ = [[["wXbhsf", "[null,1]", None, "generic"]]]


def _fixture(compat_dir, name: str) -> str:
    return (compat_dir / "rpc_fixtures" / name).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Inline reference oracle (NOT the parser under test): models the same wire
# contract so happy-path assertions don't merely echo the implementation.
# --------------------------------------------------------------------------- #


def _reference_decode_response(text: str):
    assert text.startswith(PFX)
    outer = json.loads(text[len(PFX) :])
    return [json.loads(r[2]) for r in outer if r and r[0] == "wrb.fr"]


def _reference_decode_request(text: str):
    fields = urllib.parse.parse_qs(text.strip(), keep_blank_values=True)
    return json.loads(fields["f.req"][0])


# Synthetic sentinels shaped like sensitive material, ASSEMBLED AT RUNTIME so no
# real credential *format literal* appears in this source file (keeps any repo
# secret scan clean). Used only to prove the decoder's errors never echo input.
def _sentinels() -> dict[str, str]:
    return {
        "cookie": "__Secure-3PSID" + "=" + "S" * 40,
        "oauth": "ya" + "29" + "." + "Z" * 40,
        "url": "https://notebooklm.google.com/batchexecute?at=" + "T" * 30,
        "path": "/".join(("", "Users", "example", ".notebooklm", "profiles", "work", "cookies.json")),
    }


# --------------------------------------------------------------------------- #
# Public surface / constants
# --------------------------------------------------------------------------- #


def test_xssi_prefix_constant():
    assert rpc.XSSI_PREFIX == PFX


def test_legacy_fixture_decoder_helpers_remain_available():
    assert callable(rpc.decode_batchexecute_response)
    assert callable(rpc.decode_batchexecute_request)
    # Foothold parser only: no network / notebook-service surface here.
    for absent in ("encode_response", "send", "request", "Notebook"):
        assert not hasattr(rpc, absent)


def test_rpc_module_has_no_denylisted_imports():
    # Ties the slice to the stdlib-only contract at the module granularity.
    violations = import_origin_audit.audit(roots=("notebooklm/rpc.py",))
    assert violations == []


# --------------------------------------------------------------------------- #
# Happy path: committed synthetic fixtures decode to the known values.
# --------------------------------------------------------------------------- #


def test_decode_response_matches_reference_and_known_values(compat_dir):
    body = _fixture(compat_dir, RESPONSE_FIXTURE)
    payloads = rpc.decode_batchexecute_response(body)
    assert isinstance(payloads, list)
    assert payloads == EXPECTED_RESPONSE_PAYLOADS
    assert payloads == _reference_decode_response(body)


def test_decode_response_double_parses_nested_payload(compat_dir):
    body = _fixture(compat_dir, RESPONSE_FIXTURE)
    payloads = rpc.decode_batchexecute_response(body)
    assert len(payloads) == 1
    payload = payloads[0]
    # The second JSON parse already ran: the nested payload is structured data,
    # not the raw JSON-in-string still awaiting a parse.
    assert isinstance(payload, list)
    assert payload[0][0][0] == "fake-notebook-0001"


def test_decode_request_matches_reference_and_known_values(compat_dir):
    body = _fixture(compat_dir, REQUEST_FIXTURE)
    req = rpc.decode_batchexecute_request(body)
    assert req == EXPECTED_REQUEST_FREQ
    assert req == _reference_decode_request(body)
    # rpcid is preserved as the first inner field.
    assert req[0][0][0] == "wXbhsf"


# --------------------------------------------------------------------------- #
# Fail-closed: malformed shapes raise the project-local ValidationError.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param('[["wrb.fr","id","[]",null]]', id="missing-xssi-prefix"),
        pytest.param(PFX + "\n" + "not json{", id="envelope-not-json"),
        pytest.param(PFX + "\n" + "42", id="envelope-not-list"),
        pytest.param(PFX + "\n" + '"a string"', id="envelope-is-string"),
        pytest.param(PFX + "\n" + "[]", id="empty-envelope"),
        pytest.param(PFX + "\n" + '[["er","boom"]]', id="no-wrb-fr-row"),
        pytest.param(PFX + "\n" + '[["wrb.fr","id"]]', id="wrb-fr-payload-absent"),
        pytest.param(
            PFX + "\n" + '[["wrb.fr","id",123,null]]', id="wrb-fr-payload-not-string"
        ),
        pytest.param(
            PFX + "\n" + '[["wrb.fr","id","not json{",null]]',
            id="wrb-fr-payload-not-json",
        ),
    ],
)
def test_decode_response_fails_closed(bad):
    with pytest.raises(ValidationError):
        rpc.decode_batchexecute_response(bad)


def test_decode_response_rejects_non_text():
    with pytest.raises(ValidationError):
        rpc.decode_batchexecute_response(b")]}'\n[]")  # bytes, not str


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param("at=SYNTHETIC_XSRF_TOKEN&", id="missing-f.req"),
        pytest.param("", id="empty-body"),
        pytest.param("notaform", id="no-fields"),
        pytest.param("f.req=not%20json%7B&at=X", id="f.req-not-json"),
    ],
)
def test_decode_request_fails_closed(bad):
    with pytest.raises(ValidationError):
        rpc.decode_batchexecute_request(bad)


def test_decode_request_rejects_non_text():
    with pytest.raises(ValidationError):
        rpc.decode_batchexecute_request(b"f.req=%5B%5D")  # bytes, not str


def test_validation_error_is_project_local():
    assert issubclass(ValidationError, NotebookLMError)
    # Deterministic CLI exit-code mapping is preserved (EX_USAGE-style).
    assert exit_code_for(ValidationError) == 64
    with pytest.raises(ValidationError) as exc:
        rpc.decode_batchexecute_response("no prefix at all")
    assert isinstance(exc.value, NotebookLMError)


# --------------------------------------------------------------------------- #
# Deterministic + redacted errors: same input -> same message; no input echoed.
# --------------------------------------------------------------------------- #


def test_response_errors_are_deterministic():
    bad = "missing the guard prefix entirely"
    with pytest.raises(ValidationError) as first:
        rpc.decode_batchexecute_response(bad)
    with pytest.raises(ValidationError) as second:
        rpc.decode_batchexecute_response(bad)
    assert str(first.value) == str(second.value)
    assert "batchexecute" in str(first.value).lower()


def test_request_errors_are_deterministic():
    bad = "at=onlytoken"
    with pytest.raises(ValidationError) as first:
        rpc.decode_batchexecute_request(bad)
    with pytest.raises(ValidationError) as second:
        rpc.decode_batchexecute_request(bad)
    assert str(first.value) == str(second.value)
    assert "batchexecute" in str(first.value).lower()


def test_response_errors_redact_decoded_input():
    s = _sentinels()
    # Each malformed body embeds synthetic secrets the decoder reaches before it
    # fails; the raised message must echo none of them. Hits every response branch.
    bodies = [
        s["url"] + " " + s["cookie"],  # missing XSSI prefix
        PFX + "\n" + '"' + s["oauth"] + '"',  # envelope not a list
        PFX + "\n" + '[["er","' + s["oauth"] + '"]]',  # no wrb.fr row
        PFX
        + "\n"
        + '[["wrb.fr","id",123,"'
        + s["path"]
        + '"]]',  # payload not a string
        PFX
        + "\n"
        + '[["wrb.fr","id","{ broken '
        + s["cookie"]
        + '",null]]',  # payload not valid JSON
    ]
    for body in bodies:
        assert any(
            secret in body for secret in s.values()
        )  # the secret really is present
        with pytest.raises(ValidationError) as exc:
            rpc.decode_batchexecute_response(body)
        msg = str(exc.value)
        for secret in s.values():
            assert secret not in msg


def test_request_errors_redact_decoded_input():
    s = _sentinels()
    bodies = [
        # 'at' XSRF token present but f.req missing -> must not echo the token.
        "at=" + s["oauth"] + "&x=" + urllib.parse.quote(s["cookie"]),
        # f.req present but not valid JSON, carrying a synthetic secret/path.
        "f.req=" + urllib.parse.quote("{ broken " + s["path"]) + "&at=" + s["oauth"],
    ]
    for body in bodies:
        with pytest.raises(ValidationError) as exc:
            rpc.decode_batchexecute_request(body)
        msg = str(exc.value)
        for secret in s.values():
            assert secret not in msg


def test_json_decode_errors_do_not_retain_raw_input_context():
    s = _sentinels()
    cases = [
        lambda body: rpc.decode_batchexecute_response(body),
        lambda body: rpc.decode_batchexecute_response(body),
        lambda body: rpc.decode_batchexecute_request(body),
    ]
    bodies = [
        PFX + "\n" + "{ broken " + s["oauth"],
        PFX + "\n" + '[["wrb.fr","id","{ broken ' + s["cookie"] + '",null]]',
        "f.req=" + urllib.parse.quote("{ broken " + s["path"]) + "&at=" + s["oauth"],
    ]
    for call, body in zip(cases, bodies, strict=True):
        with pytest.raises(ValidationError) as exc:
            call(body)
        assert exc.value.__context__ is None
        assert exc.value.__cause__ is None
        assert exc.value.__suppress_context__ is False
