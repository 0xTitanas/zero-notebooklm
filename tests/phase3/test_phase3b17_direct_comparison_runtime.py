"""Phase 3B17 direct comparison runtime promotion.

This batch opens the previously strict-xfailed direct-comparison shims used by
``tests/differential`` and ``tests/fake_server``. It is offline only: the runtime
loads committed compat artifacts and live bare package metadata, never auth/browser
state, network, or real NotebookLM data.
"""

from __future__ import annotations

import importlib
import json
import urllib.parse


PARITY_CATEGORIES = ("cli", "api", "auth", "rpc", "offline", "self-test")


def test_parity_runtime_compares_live_bare_exports_to_frozen_api_surface(
    repo_root, monkeypatch
):
    monkeypatch.syspath_prepend(str(repo_root))
    runtime = importlib.import_module("notebooklm._parity_runtime")
    notebooklm = importlib.import_module("notebooklm")
    python_api = json.loads(
        (repo_root / "compat" / "python_api_surface.json").read_text()
    )

    assert set(runtime.public_names()) == set(notebooklm.__all__)
    assert set(runtime.public_names()) == set(python_api["root_all"])


def test_parity_runtime_compares_cli_leaf_and_auth_artifacts(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))
    runtime = importlib.import_module("notebooklm._parity_runtime")
    cli_surface = json.loads((repo_root / "compat" / "cli_surface.json").read_text())
    auth_matrix = json.loads((repo_root / "compat" / "auth_matrix.json").read_text())

    assert runtime.cli_leaf_commands() == cli_surface["leaf_commands"]
    assert runtime.auth_matrix() == auth_matrix
    assert all(runtime.supports_category(category) for category in PARITY_CATEGORIES)


def test_parity_runtime_rpc_decode_matches_fixture_reference(repo_root, monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root))
    runtime = importlib.import_module("notebooklm._parity_runtime")
    body = (
        repo_root / "compat" / "rpc_fixtures" / "list_notebooks.response.txt"
    ).read_text(encoding="utf-8")

    payloads = runtime.rpc.decode_response(body)

    assert payloads[0][0][0][0] == "fake-notebook-0001"


def test_zero_notebooklm_rpc_decodes_and_reencodes_sanitized_fixtures(
    repo_root, monkeypatch
):
    monkeypatch.syspath_prepend(str(repo_root))
    rpc = importlib.import_module("notebooklm.rpc.decoder")
    fixtures = repo_root / "compat" / "rpc_fixtures"

    response = (fixtures / "chat_ask.streaming.response.txt").read_text(
        encoding="utf-8"
    )
    decoded_response = rpc.decode_batchexecute_response(response)
    assert decoded_response and isinstance(decoded_response[0], list)

    for name in ("list_notebooks.request.txt", "chat_ask.request.txt"):
        body = (fixtures / name).read_text(encoding="utf-8")
        decoded_request = rpc.decode_batchexecute_request(body)
        encoded = "f.req=" + urllib.parse.quote(
            json.dumps(decoded_request, separators=(",", ":")), safe=""
        ) + "&at=SYNTHETIC_XSRF_TOKEN&\n"
        assert encoded == body
