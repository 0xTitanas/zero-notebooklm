#!/usr/bin/env python3
"""Phase 10 RPC drift containment / fake-server closure audit.

This gate exercises the committed sanitized batchexecute fake-server fixtures
against an independent reference parser, the live package RPC decoder,
the package decoder, the offline parity runtime, and the fake RPC
client seam. It performs no live NotebookLM calls, browser/profile access,
credential reads, home-directory lookup, or parity-row promotion.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import urllib.parse
from typing import Any

TARGET = "notebooklm-py==0.7.2"
SCHEMA_VERSION = "rpc_drift_audit/1"
XSSI_PREFIX = ")]}'"
CHAT_FIXTURE_QUESTION = "Phase 0 synthetic question."

SECRET_PATTERNS = (
    re.compile(r"ya29\.[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\b1//[A-Za-z0-9_\-]{30,}"),
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    re.compile(
        r"\b(?:__Secure-[13]PSID|__Secure-[13]PAPISID|SAPISID|APISID|HSID|SSID|SIDCC|NID)"
        r"=[A-Za-z0-9_./+\-]{12,}"
    ),
    re.compile(r"github" r"_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
)

SYNTHETIC_MARKERS = (
    "SYNTHETIC",
    "synthetic",
    "fake-notebook",
    "fake-source",
    "fake-note",
    "fake-artifact",
    "example.test",
)


def _repo_root_from_here() -> Path:
    return Path(__file__).resolve().parents[1]


def _prepare_imports(repo_root: Path) -> None:
    root = str(repo_root)
    if root not in sys.path:
        sys.path.insert(0, root)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _matrix_state(repo_root: Path, category: str) -> str:
    matrix = repo_root / "compat" / "parity_matrix.md"
    for line in matrix.read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 4 and cells[0] == category:
            return cells[3]
    return "missing"


def _fixture_dir(repo_root: Path) -> Path:
    return repo_root / "compat" / "rpc_fixtures"


def _fixture_id_from_request(path: Path) -> str:
    suffix = ".request.txt"
    if not path.name.endswith(suffix):
        raise ValueError(f"unexpected request fixture suffix: {path.name}")
    return path.name[: -len(suffix)]


def _response_for_id(fixture_dir: Path, fixture_id: str) -> Path:
    candidates = (
        fixture_dir / f"{fixture_id}.response.txt",
        fixture_dir / f"{fixture_id}.streaming.response.txt",
    )
    existing = [path for path in candidates if path.is_file()]
    if len(existing) != 1:
        raise RuntimeError(f"fixture pair is missing or ambiguous: {fixture_id}")
    return existing[0]


def _fixture_pairs(fixture_dir: Path) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    for request_path in sorted(fixture_dir.glob("*.request.txt")):
        fixture_id = _fixture_id_from_request(request_path)
        response_path = _response_for_id(fixture_dir, fixture_id)
        pairs.append(
            {
                "fixture_id": fixture_id,
                "request_path": request_path,
                "response_path": response_path,
                "streaming": response_path.name.endswith(".streaming.response.txt"),
            }
        )
    return sorted(pairs, key=lambda pair: str(pair["fixture_id"]))


def _loads_json(text: str) -> Any:
    return json.loads(text)


def _reference_decode_request(text: str) -> Any:
    fields = urllib.parse.parse_qs(text.strip(), keep_blank_values=True)
    values = fields.get("f.req")
    if not values or values[0] == "":
        raise ValueError("request missing f.req")
    return _loads_json(values[0])


def _reference_decode_chunk_frames(body: str) -> list[Any]:
    data = body.lstrip("\r\n").encode("utf-8")
    chunks: list[Any] = []
    pos = 0
    while pos < len(data):
        line_end = data.find(b"\n", pos)
        if line_end < 0:
            raise ValueError("missing chunk length")
        length_text = data[pos:line_end].strip(b"\r")
        if not length_text or not length_text.isdigit():
            raise ValueError("malformed chunk length")
        length = int(length_text)
        pos = line_end + 1
        if length == 0:
            if data[pos:].strip(b"\r\n\t "):
                raise ValueError("trailing chunk data")
            return chunks
        if pos + length > len(data):
            raise ValueError("chunk byte count mismatch")
        chunk_text = data[pos : pos + length].decode("utf-8")
        pos += length
        if pos < len(data) and data[pos : pos + 2] == b"\r\n":
            pos += 2
        elif pos < len(data) and data[pos : pos + 1] == b"\n":
            pos += 1
        elif pos < len(data):
            raise ValueError("chunk byte count mismatch")
        chunk = _loads_json(chunk_text)
        if not isinstance(chunk, list):
            raise ValueError("chunk envelope must be list")
        chunks.extend(chunk)
    raise ValueError("missing terminal chunk")


def _reference_response_rows(text: str) -> list[Any]:
    if not text.startswith(XSSI_PREFIX):
        raise ValueError("response missing XSSI guard")
    body = text[len(XSSI_PREFIX) :]
    stripped = body.lstrip("\r\n")
    if stripped[:1] and "0" <= stripped[0] <= "9":
        return _reference_decode_chunk_frames(body)
    envelope = _loads_json(body)
    if not isinstance(envelope, list):
        raise ValueError("response envelope must be list")
    return envelope


def _reference_decode_response(text: str) -> list[Any]:
    payloads: list[Any] = []
    for row in _reference_response_rows(text):
        if not isinstance(row, list) or not row or row[0] != "wrb.fr":
            continue
        if len(row) < 3 or not isinstance(row[2], str):
            raise ValueError("wrb.fr payload missing")
        payloads.append(_loads_json(row[2]))
    if not payloads:
        raise ValueError("response missing wrb.fr payload")
    return payloads


def _summarize(value: Any) -> str:
    if value is None:
        return "none"
    if isinstance(value, list):
        return f"list[{len(value)}]"
    if isinstance(value, tuple):
        return f"tuple[{len(value)}]"
    if isinstance(value, dict):
        return f"dict[{len(value)}]"
    return value.__class__.__name__


def _fixture_contract(repo_root: Path, pairs: list[dict[str, Any]]) -> dict[str, Any]:
    fixture_dir = _fixture_dir(repo_root)
    wire_shape = _load_json(fixture_dir / "wire_shape.json")
    request_decoded = 0
    response_decoded = 0
    roundtrips = 0
    rpcids: dict[str, str] = {}
    response_payload_summaries: dict[str, str] = {}

    for pair in pairs:
        request_text = pair["request_path"].read_text(encoding="utf-8")
        response_text = pair["response_path"].read_text(encoding="utf-8")
        decoded_request = _reference_decode_request(request_text)
        decoded_response = _reference_decode_response(response_text)
        request_decoded += 1
        response_decoded += 1
        if _canonical_fixture_request(decoded_request) == request_text:
            roundtrips += 1
        rpcids[str(pair["fixture_id"])] = decoded_request[0][0][0]
        response_payload_summaries[str(pair["fixture_id"])] = _summarize(
            decoded_response[0]
        )

    combined = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(fixture_dir.glob("*.txt"))
    )
    secret_hits = [rx.pattern for rx in SECRET_PATTERNS if rx.search(combined)]
    synthetic_markers = sum(1 for marker in SYNTHETIC_MARKERS if marker in combined)

    return {
        "wire_shape": {
            "xssi_prefix": wire_shape.get("xssi_prefix"),
            "batchexecute_markers": wire_shape.get("batchexecute_markers"),
            "host_literals": wire_shape.get("host_literals"),
            "endpoint_path_literals": wire_shape.get("endpoint_path_literals"),
            "rpc_modules": wire_shape.get("rpc_modules"),
        },
        "pairs": {
            "fixture_ids": [str(pair["fixture_id"]) for pair in pairs],
            "total": len(pairs),
            "requests_decoded": request_decoded,
            "responses_decoded": response_decoded,
            "roundtrips": roundtrips,
            "streaming_responses": [
                str(pair["fixture_id"]) for pair in pairs if pair["streaming"]
            ],
            "rpcids": rpcids,
            "response_payload_summaries": response_payload_summaries,
        },
        "sanitization": {
            "status": "pass" if not secret_hits and synthetic_markers >= 3 else "fail",
            "secret_hits": secret_hits,
            "synthetic_markers": synthetic_markers,
        },
    }


def _canonical_fixture_request(decoded: Any) -> str:
    return (
        "f.req="
        + urllib.parse.quote(json.dumps(decoded, separators=(",", ":")), safe="")
        + "&at=SYNTHETIC_XSRF_TOKEN&\n"
    )


def _parser_contract(repo_root: Path, pairs: list[dict[str, Any]]) -> dict[str, Any]:
    _prepare_imports(repo_root)
    from notebooklm import _parity_runtime
    from notebooklm.rpc.decoder import (
        decode_batchexecute_request,
        decode_batchexecute_response,
    )
    package_vs_runtime = 0
    canonical_roundtrips = 0
    package_request_matches = 0
    package_response_matches = 0

    for pair in pairs:
        request_text = pair["request_path"].read_text(encoding="utf-8")
        response_text = pair["response_path"].read_text(encoding="utf-8")
        expected_request = _reference_decode_request(request_text)
        expected_response = _reference_decode_response(response_text)

        package_request = decode_batchexecute_request(request_text)
        package_response = decode_batchexecute_response(response_text)
        runtime_response = _parity_runtime.rpc.decode_response(response_text)

        if package_request == expected_request:
            package_request_matches += 1
        if package_response == expected_response:
            package_response_matches += 1
        if package_response == expected_response == runtime_response:
            package_vs_runtime += 1
        if _canonical_fixture_request(expected_request) == request_text:
            canonical_roundtrips += 1

    fail_closed = _fail_closed_checks(
        decode_batchexecute_request, decode_batchexecute_response
    )

    return {
        "package_decoder": "pass" if package_request_matches == len(pairs) else "fail",
        "zero_rpc_surface": "pass"
        if package_response_matches == len(pairs)
        else "fail",
        "parity_runtime_rpc": "pass" if package_vs_runtime == len(pairs) else "fail",
        "package_request_matches": package_request_matches,
        "package_response_matches": package_response_matches,
        "package_vs_runtime_response_matches": package_vs_runtime,
        "canonical_encode_roundtrips": canonical_roundtrips,
        "fail_closed_cases": fail_closed["cases"],
        "redacted_error_messages": fail_closed["redacted"],
    }


def _fail_closed_checks(decode_request: Any, decode_response: Any) -> dict[str, Any]:
    sentinel = "SECRET_SENTINEL_SHOULD_NOT_ECHO"
    cases = (
        lambda: decode_response(sentinel),
        lambda: decode_response(XSSI_PREFIX + "\n" + sentinel),
        lambda: decode_response(XSSI_PREFIX + "\n[]"),
        lambda: decode_response(XSSI_PREFIX + '\n[["wrb.fr","x","' + sentinel + '"]]'),
        lambda: decode_request("x=" + sentinel),
        lambda: decode_request("f.req=%5B" + sentinel),
        lambda: decode_request(None),
    )
    caught = 0
    messages: list[str] = []
    for case in cases:
        try:
            case()
        except Exception as exc:  # noqa: BLE001 - intentional fail-closed probe
            caught += 1
            messages.append(str(exc))
    redacted = caught == len(cases) and all(sentinel not in msg for msg in messages)
    return {"cases": caught, "redacted": redacted, "messages": messages}


def _fake_rpc_contract(repo_root: Path) -> dict[str, Any]:
    _prepare_imports(repo_root)
    from notebooklm.fake_rpc import FakeRpcRequest, OfflineFixtureRpcClient

    fixture_dir = _fixture_dir(repo_root)
    client = OfflineFixtureRpcClient.from_fixture_dir(fixture_dir)
    operations: dict[str, str] = {}
    payloads: dict[str, Any] = {}

    try:
        payloads["notebooks"] = client.list_notebooks_payload()
        operations["list_notebooks_payload"] = "pass"
    except Exception:  # noqa: BLE001 - audit status capture
        operations["list_notebooks_payload"] = "fail"
    try:
        payloads["sources"] = client.list_sources_payload("fake-notebook-0001")
        operations["list_sources_payload"] = "pass"
    except Exception:  # noqa: BLE001 - audit status capture
        operations["list_sources_payload"] = "fail"
    try:
        payloads["notes"] = client.list_notes_payload("fake-notebook-0001")
        operations["list_notes_payload"] = "pass"
    except Exception:  # noqa: BLE001 - audit status capture
        operations["list_notes_payload"] = "fail"
    try:
        payloads["artifacts"] = client.list_artifacts_payload("fake-notebook-0001")
        operations["list_artifacts_payload"] = "pass"
    except Exception:  # noqa: BLE001 - audit status capture
        operations["list_artifacts_payload"] = "fail"
    try:
        payloads["chat"] = client.chat_ask_payload(
            "fake-notebook-0001", CHAT_FIXTURE_QUESTION
        )
        operations["chat_ask_payload"] = "pass"
    except Exception:  # noqa: BLE001 - audit status capture
        operations["chat_ask_payload"] = "fail"

    try:
        client.call(
            FakeRpcRequest(
                rpcid="unsupported-rpc", payload='["SECRET_SENTINEL"]', kind="generic"
            )
        )
    except Exception as exc:  # noqa: BLE001 - expected fail-closed path
        operations["unsupported_request_fail_closed"] = (
            "pass" if "SECRET_SENTINEL" not in str(exc) else "fail"
        )
    else:
        operations["unsupported_request_fail_closed"] = "fail"

    return {
        "status": "pass" if all(v == "pass" for v in operations.values()) else "fail",
        "operations": operations,
        "payload_summaries": {
            key: _summarize(value) for key, value in payloads.items()
        },
    }


def build_report(repo_root: str | Path | None = None) -> dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root_from_here()
    fixture_dir = _fixture_dir(root)
    pairs = _fixture_pairs(fixture_dir)
    fixture_contract = _fixture_contract(root, pairs)
    parser_contract = _parser_contract(root, pairs)
    fake_rpc_contract = _fake_rpc_contract(root)
    rpc_state = _matrix_state(root, "rpc")

    status_checks = [
        fixture_contract["pairs"]["total"] == 5,
        fixture_contract["pairs"]["requests_decoded"] == len(pairs),
        fixture_contract["pairs"]["responses_decoded"] == len(pairs),
        fixture_contract["pairs"]["roundtrips"] == len(pairs),
        fixture_contract["sanitization"]["status"] == "pass",
        parser_contract["package_decoder"] == "pass",
        parser_contract["zero_rpc_surface"] == "pass",
        parser_contract["parity_runtime_rpc"] == "pass",
        parser_contract["package_response_matches"] == len(pairs),
        parser_contract["package_vs_runtime_response_matches"] == len(pairs),
        parser_contract["canonical_encode_roundtrips"] == len(pairs),
        parser_contract["fail_closed_cases"] == 7,
        parser_contract["redacted_error_messages"] is True,
        fake_rpc_contract["status"] == "pass",
        rpc_state in {"open", "pass"},
    ]
    overall_status = "pass" if all(status_checks) else "fail"

    return {
        "schema_version": SCHEMA_VERSION,
        "target": TARGET,
        "overall_status": overall_status,
        "strict_exit_code": 0 if overall_status == "pass" else 1,
        "live_access": False,
        "credential_access": False,
        "category_promotion": {"rpc": rpc_state == "pass"},
        "category_states": {"rpc": rpc_state},
        "fixture_contract": fixture_contract,
        "parser_contract": parser_contract,
        "fake_rpc_contract": fake_rpc_contract,
        "notes": [
            "Offline fake-server drift-containment gate only; no live NotebookLM access.",
            "RPC parity row respects parity_matrix-driven promotion status.",
        ],
    }


def _print_human(report: dict[str, Any]) -> None:
    pairs = report["fixture_contract"]["pairs"]
    parser = report["parser_contract"]
    parser_status = (
        "pass"
        if parser["package_response_matches"] == pairs["total"]
        and parser["package_vs_runtime_response_matches"] == pairs["total"]
        and parser["canonical_encode_roundtrips"] == pairs["total"]
        else "fail"
    )
    print(f"ZeroNotebookLM RPC drift audit: {report['overall_status']}")
    print(f"fixture pairs: {pairs['roundtrips']}/{pairs['total']}")
    print(f"parser agreement: {parser_status}")
    print(f"fake RPC seam: {report['fake_rpc_contract']['status']}")
    print(
        "category promotion: "
        + ("pass" if report["category_promotion"]["rpc"] else "no")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rpc_drift_audit.py")
    parser.add_argument("--json", action="store_true", help="emit JSON report")
    parser.add_argument("--strict", action="store_true", help="exit nonzero on failure")
    args = parser.parse_args(argv)

    report = build_report()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_human(report)
    if args.strict:
        return int(report["strict_exit_code"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
