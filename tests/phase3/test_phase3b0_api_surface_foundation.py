"""Phase 3B0 public API surface parity foundation tests.

These tests intentionally use the Phase 0 oracle/goldens as the source of
truth. They are offline-only and prove that notebooklm-bare exposes the public
import/type/exception/subclient skeleton required before later behavior parity
batches can safely claim 1:1 compatibility with notebooklm-py==0.7.2.
"""

from __future__ import annotations

import dataclasses
import importlib
import inspect
import json
from enum import Enum
from pathlib import Path
from typing import cast

import pytest


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _api_surface() -> dict:
    return json.loads((_repo_root() / "compat" / "python_api_surface.json").read_text())


def _signatures() -> dict:
    return json.loads(
        (_repo_root() / "compat" / "api_golden" / "signatures.json").read_text()
    )


def _enums() -> dict[str, dict[str, object]]:
    return json.loads(
        (_repo_root() / "compat" / "api_golden" / "enums.json").read_text()
    )["enums"]


def _exceptions() -> list[dict[str, object]]:
    return json.loads(
        (_repo_root() / "compat" / "api_golden" / "exceptions.json").read_text()
    )["exceptions"]


def test_public_root_all_matches_oracle_names():
    notebooklm = importlib.import_module("notebooklm")
    expected = _api_surface()["root_all"]

    assert list(notebooklm.__all__) == expected
    for name in expected:
        assert hasattr(notebooklm, name), name


@pytest.mark.parametrize("module_name", sorted(_api_surface()["modules"]))
def test_oracle_public_modules_are_importable(module_name):
    importlib.import_module(module_name)


def test_target_dataclasses_are_exported_from_oracle_modules_with_oracle_fields():
    root_names = set(_api_surface()["root_all"])
    dataclass_specs = _signatures()["dataclasses"]

    missing = []
    field_mismatches = []
    for name, spec in dataclass_specs.items():
        module_name = "notebooklm" if name in root_names else cast(str, spec["module"])
        module = importlib.import_module(module_name)
        cls = getattr(module, name, None)
        if cls is None:
            missing.append((module_name, name))
            continue
        if not dataclasses.is_dataclass(cls):
            field_mismatches.append((name, "not a dataclass"))
            continue
        actual_fields = [field.name for field in dataclasses.fields(cls)]
        if actual_fields != spec["fields"]:
            field_mismatches.append((name, actual_fields, spec["fields"]))

    assert missing == []
    assert field_mismatches == []


def test_target_enums_are_exported_from_root_or_rpc_types_with_oracle_values():
    root_names = set(_api_surface()["root_all"])
    mismatches = []

    for name, members in _enums().items():
        module_name = "notebooklm" if name in root_names else "notebooklm.rpc.types"
        module = importlib.import_module(module_name)
        enum_cls = getattr(module, name, None)
        if enum_cls is None:
            mismatches.append((module_name, name, "missing"))
            continue
        if not issubclass(enum_cls, Enum):
            mismatches.append((module_name, name, "not an Enum"))
            continue
        actual = {member.name: member.value for member in enum_cls}
        if actual != members:
            mismatches.append((module_name, name, actual, members))

    assert mismatches == []


def test_target_exceptions_are_exported_from_root_or_exceptions_module_with_oracle_mro():
    root_names = set(_api_surface()["root_all"])
    mismatches = []

    for spec in _exceptions():
        name = cast(str, spec["name"])
        module_name = "notebooklm" if name in root_names else "notebooklm.exceptions"
        module = importlib.import_module(module_name)
        exc_cls = getattr(module, name, None)
        if exc_cls is None:
            mismatches.append((name, "missing"))
            continue
        if not issubclass(exc_cls, Exception):
            mismatches.append((name, "not exception"))
            continue
        actual_mro = [
            f"{cls.__module__}.{cls.__name__}"
            for cls in exc_cls.__mro__
            if cls is not object
        ]
        if actual_mro != spec["mro"]:
            mismatches.append((name, actual_mro, spec["mro"]))

    assert mismatches == []


def test_missing_subclient_shells_are_attached_and_phase_gated():
    from notebooklm import AuthTokens, NotebookLMClient
    from notebooklm.errors import NotImplementedInPhaseError

    client = NotebookLMClient(AuthTokens(cookies={}, csrf_token="", session_id=""))
    expected_attrs = {
        "mind_maps": "MindMapsAPI",
        "research": "ResearchAPI",
        "settings": "SettingsAPI",
        "sharing": "SharingAPI",
    }

    for attr, class_name in expected_attrs.items():
        api = getattr(client, attr)
        assert api.__class__.__name__ == class_name

    async_methods = {
        key: set(spec["async_methods"])
        for key, spec in _signatures()["subclients"].items()
        if key in expected_attrs
    }
    phase3b_promoted = {
        "mind_maps": {
            "delete",
            "generate",
            "get",
            "get_or_none",
            "get_tree",
            "list",
            "rename",
        },
        "research": {
            "import_sources",
            "import_sources_with_verification",
            "poll",
            "start",
            "wait_for_completion",
        },
        "settings": {
            "get_account_limits",
            "get_account_tier",
            "get_output_language",
            "set_output_language",
        },
        "sharing": {
            "add_user",
            "get_status",
            "remove_user",
            "set_public",
            "set_view_level",
            "update_user",
        },
    }
    for attr, method_names in async_methods.items():
        api = getattr(client, attr)
        for method_name in method_names:
            method = getattr(api, method_name)
            assert inspect.iscoroutinefunction(method)
            if method_name in phase3b_promoted.get(attr, set()):
                continue
            with pytest.raises(NotImplementedInPhaseError):
                method_signature = inspect.signature(method)
                required_args = [
                    object()
                    for param in method_signature.parameters.values()
                    if param.default is inspect._empty
                    and param.kind
                    in (
                        inspect.Parameter.POSITIONAL_ONLY,
                        inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    )
                ]
                method(*required_args).send(None)


def test_existing_rpc_parser_functions_survive_rpc_package_transition():
    rpc = importlib.import_module("notebooklm.rpc")
    decoder = importlib.import_module("notebooklm.rpc.decoder")
    encoder = importlib.import_module("notebooklm.rpc.encoder")
    rpc_types = importlib.import_module("notebooklm.rpc.types")
    overrides = importlib.import_module("notebooklm.rpc.overrides")

    assert not hasattr(rpc, "parse_batchexecute_response")
    assert decoder.parse_batchexecute_response is decoder.decode_batchexecute_response
    assert callable(encoder.encode_rpc_request)
    assert callable(overrides.resolve_rpc_id)
    assert rpc_types.RPCMethod.LIST_NOTEBOOKS.value == "wXbhsf"
