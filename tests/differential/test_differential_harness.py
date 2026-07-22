"""Direct differential probe harness — second prong of the exit gate.

The Phase 0 exit gate requires a differential harness that can (a) **run the
upstream probe** — load and assert the frozen upstream surface captured offline in
``compat/`` from the pinned ``notebooklm-py==0.7.2`` wheel — and (b) record the
**expected bare-side failures** until the bare runtime exists.

Phase 3B17 promotes the bare-side runtime and parser shims into real offline
comparisons. The harness now verifies frozen upstream CLI/API/Auth/RPC artifacts
against live bare package exports and sanitized fixture decoders.

This module imports only stdlib + the committed ``compat/`` artifacts (through the
shared ``tests/conftest.py`` fixtures). It never imports the upstream ``notebooklm``
oracle, never touches the network / a Google account / cookies, and never
implements any bare runtime.
"""

from __future__ import annotations

import importlib
import json

import pytest

# The full differential runtime remains absent even after Phase 1A creates a
# package skeleton and a single-file help surface. Imported by *string* so this
# module carries no static dependency on a future parity runtime.
BARE_RUNTIME_MODULE = "notebooklm._parity_runtime"

# Direct-comparison categories now have an offline bare-side runtime in Phase 3B17.
PARITY_CATEGORIES = ("cli", "api", "auth", "rpc", "offline", "self-test")

XSSI_PREFIX = ")]}'"


def _bare_runtime():
    """Import the Phase 3B17 offline direct-comparison runtime."""
    return importlib.import_module(BARE_RUNTIME_MODULE)


def _reference_decode_response(text: str):
    """Reference (test-oracle) decoder modelling the upstream batchexecute wire
    contract for the probe's *expected-output* side.

    This is deliberately **not** the bare runtime parser (that is Phase 1+ and
    asserted absent below); it is the harness's own checker. It strips the XSSI
    guard, parses the outer envelope, selects the ``wrb.fr`` row(s), and parses the
    nested JSON-in-string payload a second time.
    """
    assert text.startswith(XSSI_PREFIX), "response missing XSSI guard prefix"
    outer = json.loads(text[len(XSSI_PREFIX) :])
    rows = [r for r in outer if r and r[0] == "wrb.fr"]
    assert rows, "no wrb.fr row in batchexecute envelope"
    return [json.loads(r[2]) for r in rows]


# --------------------------------------------------------------------------- #
# Upstream prong: the probe loads and asserts the frozen upstream surface.
# These PASS in Phase 0 — they prove the harness can run the upstream side.
# --------------------------------------------------------------------------- #


def test_upstream_probe_cli_surface(cli_surface):
    leaves = cli_surface["leaf_commands"]
    assert len(leaves) == 90
    assert all(leaf.startswith("notebooklm ") for leaf in leaves)
    assert len(cli_surface["nodes"]) == 103


def test_upstream_probe_python_api(python_api):
    assert python_api["root_all_count"] >= 105
    assert len(python_api["subclients"]) == 9
    assert len(python_api["exception_hierarchy"]) >= 40


def test_upstream_probe_auth_matrix(auth_matrix):
    counts = auth_matrix["counts"]
    assert counts["interactive_login_rows"] == 45
    assert counts["browser_cookie_import_rows"] == 101


def test_upstream_probe_rpc_response_decodes(compat_dir):
    body = (compat_dir / "rpc_fixtures" / "list_notebooks.response.txt").read_text(
        encoding="utf-8"
    )
    payloads = _reference_decode_response(body)
    # The synthetic upstream payload nests exactly one notebook tuple.
    assert payloads and payloads[0][0][0][0] == "fake-notebook-0001"


# --------------------------------------------------------------------------- #
# Bare prong: Phase 3B17 promotes these from strict-xfail sentinels into real
# offline direct comparisons against the frozen compat artifacts.
# --------------------------------------------------------------------------- #


def test_differential_cli_leaves_vs_bare(cli_surface):
    bare = _bare_runtime()
    upstream = set(cli_surface["leaf_commands"])
    assert set(bare.cli_leaf_commands()) == upstream


def test_differential_python_api_vs_bare(python_api):
    bare = _bare_runtime()
    assert set(bare.public_names()) == set(python_api["root_all"])


def test_differential_auth_matrix_vs_bare(auth_matrix):
    bare = _bare_runtime()
    assert bare.auth_matrix() == auth_matrix


def test_differential_rpc_decode_vs_bare(compat_dir):
    bare = _bare_runtime()
    body = (compat_dir / "rpc_fixtures" / "list_notebooks.response.txt").read_text(
        encoding="utf-8"
    )
    assert bare.rpc.decode_response(body) == _reference_decode_response(body)


@pytest.mark.parametrize("category", PARITY_CATEGORIES)
def test_differential_every_parity_category_supported_by_bare(category):
    """Each direct-comparison category is exposed by the Phase 3B17 runtime."""
    bare = _bare_runtime()
    assert bare.supports_category(category)
