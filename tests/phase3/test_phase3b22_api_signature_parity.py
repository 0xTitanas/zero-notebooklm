"""Phase 3B22 strict API signature parity guard.

The golden surface already records constructor and sub-client method signatures from
notebooklm-py==0.7.2. This regression ensures bare-metal parity does not stop at
name presence: dataclass constructor annotations and async sub-client method
signatures must retain the pinned call shape while remaining stdlib-only at
runtime.
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib
import inspect
import json
import types
import typing
from pathlib import Path


def _load_signatures(repo_root: Path) -> dict:
    return json.loads((repo_root / "compat/api_golden/signatures.json").read_text())


def _field_names(cls: type) -> list[str]:
    return [field.name for field in dataclasses.fields(cls)]


def test_dataclass_constructor_signatures_match_golden(repo_root):
    signatures = _load_signatures(repo_root)

    mismatches = []
    for name, expected in signatures["dataclasses"].items():
        module = importlib.import_module(expected["module"])
        cls = getattr(module, name, None)
        if cls is None or not dataclasses.is_dataclass(cls):
            mismatches.append((name, "missing dataclass", expected["module"]))
            continue
        actual_fields = _field_names(cls)
        if actual_fields != expected["fields"]:
            mismatches.append((name, "fields", expected["fields"], actual_fields))
        actual_signature = str(inspect.signature(cls))
        if actual_signature != expected["init_signature"]:
            mismatches.append(
                (name, "signature", expected["init_signature"], actual_signature)
            )

    assert not mismatches


def _split_signature_parameters(signature_text: str) -> list[str]:
    body = signature_text[signature_text.find("(") + 1 : signature_text.rfind(")")]
    params: list[str] = []
    current = ""
    depth = 0
    quote: str | None = None
    for char in body:
        if quote is not None:
            current += char
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
            current += char
        elif char in "([{<":
            depth += 1
            current += char
        elif char in ")]}>":
            depth -= 1
            current += char
        elif char == "," and depth == 0:
            params.append(current.strip())
            current = ""
        else:
            current += char
    if current.strip():
        params.append(current.strip())
    return params


def _normalize_default(value: str | None) -> str | None:
    if value is None:
        return None
    if value.startswith("<object object at 0x"):
        return "<object object>"
    return value


def _shape_from_text(signature_text: str) -> list[tuple[str, str, str | None]]:
    shape: list[tuple[str, str, str | None]] = []
    keyword_only = False
    for raw in _split_signature_parameters(signature_text):
        if raw == "*":
            keyword_only = True
            continue
        if raw.startswith("**"):
            name = raw[2:].split(":", 1)[0].split("=", 1)[0].strip()
            kind = "VAR_KEYWORD"
        elif raw.startswith("*"):
            name = raw[1:].split(":", 1)[0].split("=", 1)[0].strip()
            kind = "VAR_POSITIONAL"
        else:
            name = raw.split(":", 1)[0].split("=", 1)[0].strip()
            kind = "KEYWORD_ONLY" if keyword_only else "POSITIONAL_OR_KEYWORD"
        default = raw.split("=", 1)[1].strip() if "=" in raw else None
        shape.append((name, kind, _normalize_default(default)))
    return shape


def _shape_from_callable(func) -> list[tuple[str, str, str | None]]:
    shape: list[tuple[str, str, str | None]] = []
    for name, param in inspect.signature(func).parameters.items():
        default = None if param.default is inspect._empty else repr(param.default)
        shape.append((name, param.kind.name, _normalize_default(default)))
    return shape


def test_client_and_subclient_async_signature_shapes_match_golden(repo_root):
    signatures = _load_signatures(repo_root)
    notebooklm = importlib.import_module("notebooklm")
    client_module = importlib.import_module("notebooklm.client")

    client_cls = notebooklm.NotebookLMClient
    assert (
        str(inspect.signature(client_cls.__init__))
        == signatures["client"]["init_signature"]["text"]
    )
    assert (
        sorted(
            name
            for name, value in inspect.getmembers(
                client_cls, inspect.iscoroutinefunction
            )
            if not name.startswith("_")
        )
        == signatures["client"]["async_public_methods"]
    )
    assert (
        sorted(
            name
            for name, value in inspect.getmembers(client_cls)
            if isinstance(value, property)
        )
        == signatures["client"]["properties"]
    )

    mismatches = []
    for subclient, expected in signatures["subclients"].items():
        cls = getattr(client_module, expected["class"], None) or getattr(
            notebooklm, expected["class"], None
        )
        if cls is None:
            mismatches.append((subclient, "missing class", expected["class"]))
            continue
        async_methods = sorted(
            name
            for name, value in inspect.getmembers(cls, inspect.iscoroutinefunction)
            if not name.startswith("_")
        )
        if async_methods != expected["async_methods"]:
            mismatches.append(
                (subclient, "async methods", expected["async_methods"], async_methods)
            )
        golden_method_signatures = expected.get("method_signatures", {})
        for method_name in expected["async_methods"]:
            actual = _shape_from_callable(getattr(cls, method_name))
            golden = _shape_from_text(golden_method_signatures[method_name])
            if actual != golden:
                mismatches.append((subclient, method_name, golden, actual))

    assert not mismatches


def test_root_auth_tokens_type_hints_resolve_without_httpx_dependency(
    repo_root, monkeypatch
):
    monkeypatch.syspath_prepend(str(repo_root))
    notebooklm = importlib.import_module("notebooklm")

    hints = typing.get_type_hints(notebooklm.AuthTokens)

    assert "cookies" in hints
    assert "cookie_snapshot" in hints
    assert "cookie_jar" in hints


def test_generate_cli_bridge_forwards_only_bound_method_kwargs(
    repo_root, monkeypatch, capsys
):
    monkeypatch.syspath_prepend(str(repo_root))
    cli = importlib.reload(importlib.import_module("notebooklm.cli"))
    types_module = importlib.import_module("notebooklm.types")
    captured: dict[str, dict[str, object]] = {}

    class FakeArtifactsAPI:
        def __init__(self, **kwargs):
            pass

        async def generate_infographic(
            self,
            notebook_id: str,
            source_ids: list[str] | None = None,
            language: str | None = "en",
            instructions: str | None = None,
            orientation: object | None = None,
            detail_level: object | None = None,
            style: object | None = None,
        ):
            captured["infographic"] = {
                "notebook_id": notebook_id,
                "source_ids": source_ids,
                "language": language,
                "instructions": instructions,
                "orientation": orientation,
                "detail_level": detail_level,
                "style": style,
            }
            return types_module.GenerationStatus(
                task_id="infographic-task", status="completed"
            )

        async def generate_cinematic_video(
            self,
            notebook_id: str,
            source_ids: list[str] | None = None,
            language: str | None = "en",
            instructions: str | None = None,
        ):
            captured["cinematic"] = {
                "notebook_id": notebook_id,
                "source_ids": source_ids,
                "language": language,
                "instructions": instructions,
            }
            return types_module.GenerationStatus(
                task_id="cinematic-task", status="completed"
            )

    monkeypatch.setattr(cli._artifacts, "ArtifactsAPI", FakeArtifactsAPI)
    monkeypatch.setattr(
        cli, "_offline_artifact_services", lambda fixture_dir: (object(), object())
    )
    monkeypatch.setattr(
        cli,
        "_resolve_note_notebook",
        lambda notebook_service, selector: types.SimpleNamespace(id="fake-notebook"),
    )

    infographic_ns = argparse.Namespace(
        fixture_dir=None,
        notebook=None,
        source_ids=["source-1"],
        language="fr",
        timeout=300,
        interval=2,
        retry=0,
        orientation="portrait",
        detail="detailed",
        style="professional",
        json=True,
    )
    assert (
        cli._run_generate_status(
            infographic_ns,
            "generate_infographic",
            description="make it visual",
        )
        == 0
    )
    capsys.readouterr()
    assert captured["infographic"]["style"] == "professional"
    assert captured["infographic"]["instructions"] == "make it visual"
    assert "video_style" not in captured["infographic"]

    cinematic_ns = argparse.Namespace(
        fixture_dir=None,
        notebook=None,
        source_ids=None,
        language="en",
        timeout=3600,
        interval=2,
        retry=0,
        video_format="cinematic",
        style="classic",
        style_prompt="hand drawn",
        json=True,
    )
    assert (
        cli._run_generate_status(
            cinematic_ns,
            "generate_cinematic_video",
            description="documentary",
        )
        == 0
    )
    capsys.readouterr()
    assert captured["cinematic"] == {
        "notebook_id": "fake-notebook",
        "source_ids": None,
        "language": "en",
        "instructions": "documentary",
    }
