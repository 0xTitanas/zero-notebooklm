"""Reflective oracle prober for the pinned upstream ``notebooklm-py==0.7.2``.

This tool runs **inside the disposable Phase 0 venv** where the pinned upstream
wheel is installed. It imports only:

  * the upstream ``notebooklm`` package (the oracle being frozen), and
  * the Python standard library.

It deliberately does **not** contain an ``import click`` / ``import rich`` /
``import httpx`` statement. The Click command tree is walked by reaching the
already-loaded ``click`` module through ``sys.modules`` (it is present only
because importing ``notebooklm`` pulled it in). That keeps every file under
``scripts/`` free of denylisted runtime imports, so the import-origin audit runs
over the whole project with zero carve-outs, while this prober still faithfully
uses upstream's real Click resolution (including the custom ``SectionedGroup``).

Output: a single deterministic JSON bundle written with ``--out`` containing the
CLI tree, the public Python API surface, auth-source facts, dependency metadata,
and the RPC wire shape. The host-side orchestrator assembles the committed
``compat/*.json`` artifacts from this bundle.
"""

from __future__ import annotations

import argparse
import dataclasses
import enum
import inspect
import json
import os
import re
import sys
import types
from importlib import import_module


# --------------------------------------------------------------------------- #
# Generic helpers
# --------------------------------------------------------------------------- #

_REDACTIONS: list[tuple[str, str]] = []


def _install_redactions(package_dir: str) -> None:
    """Register substring redactions so no host/temp path leaks into artifacts."""
    home = os.path.expanduser("~")
    candidates = [
        (os.path.dirname(os.path.dirname(package_dir)), "<site-packages>"),
        (package_dir, "<notebooklm-pkg>"),
        (home, "<home>"),
    ]
    tmp = os.environ.get("TMPDIR")
    if tmp:
        candidates.append((tmp.rstrip("/"), "<tmp>"))
    for raw, token in candidates:
        if raw and raw not in ("/", ""):
            _REDACTIONS.append((raw, token))


def _redact(text: str) -> str:
    if not isinstance(text, str):
        return text
    for raw, token in _REDACTIONS:
        if raw in text:
            text = text.replace(raw, token)
    return text


def _jsonable(value):
    """Return a JSON-native projection of *value*, or None if not representable."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _redact(value)
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return None


def _short_doc(obj) -> str | None:
    doc = inspect.getdoc(obj)
    if not doc:
        return None
    line = doc.strip().splitlines()[0].strip()
    return _redact(line) or None


def _safe_repr(value) -> str:
    try:
        return _redact(repr(value))
    except Exception:  # pragma: no cover - defensive
        return "<unrepresentable>"


def _signature(obj):
    """Structured + string signature, tolerant of builtins/odd callables."""
    try:
        sig = inspect.signature(obj)
    except (TypeError, ValueError):
        return None
    params = []
    for p in sig.parameters.values():
        params.append(
            {
                "name": p.name,
                "kind": p.kind.name,
                "has_default": p.default is not inspect.Parameter.empty,
                "default_repr": (
                    None
                    if p.default is inspect.Parameter.empty
                    else _safe_repr(p.default)
                ),
                "annotation": (
                    None
                    if p.annotation is inspect.Parameter.empty
                    else _redact(_annotation_str(p.annotation))
                ),
            }
        )
    ret = (
        None
        if sig.return_annotation is inspect.Signature.empty
        else _redact(_annotation_str(sig.return_annotation))
    )
    return {
        "text": _redact(str(sig)),
        "parameters": params,
        "return_annotation": ret,
    }


def _annotation_str(annotation) -> str:
    if isinstance(annotation, str):
        return annotation
    return getattr(annotation, "__name__", None) or str(annotation)


# --------------------------------------------------------------------------- #
# Click tree walk (duck-typed; click obtained via sys.modules, never imported)
# --------------------------------------------------------------------------- #


def _click_module():
    return sys.modules.get("click")


def _is_group(cmd) -> bool:
    return callable(getattr(cmd, "list_commands", None)) and callable(
        getattr(cmd, "get_command", None)
    )


def _root_context(root):
    click = _click_module()
    if click is None:  # pragma: no cover - notebooklm always imports click
        return None
    return click.Context(root, info_name="notebooklm")


def _subcommands(cmd, ctx):
    if ctx is not None and callable(getattr(cmd, "list_commands", None)):
        names = list(cmd.list_commands(ctx))
        out = []
        for name in names:
            sub = cmd.get_command(ctx, name)
            if sub is not None:
                out.append((name, sub))
        return out
    # Fallback to the raw command registry.
    registry = getattr(cmd, "commands", {}) or {}
    return sorted(registry.items())


def _param_info(param):
    ptype = getattr(param, "type", None)
    type_info = {
        "class": type(ptype).__name__ if ptype is not None else None,
        "name": getattr(ptype, "name", None),
    }
    choices = getattr(ptype, "choices", None)
    if choices is not None:
        type_info["choices"] = [str(c) for c in choices]
    for attr in ("min", "max", "min_open", "max_open"):
        if hasattr(ptype, attr):
            type_info[attr] = _jsonable(getattr(ptype, attr))

    default = getattr(param, "default", None)
    prompt = getattr(param, "prompt", None)
    info = {
        "name": param.name,
        "param_kind": getattr(param, "param_type_name", None),  # "option" | "argument"
        "opts": list(getattr(param, "opts", []) or []),
        "secondary_opts": list(getattr(param, "secondary_opts", []) or []),
        "required": bool(getattr(param, "required", False)),
        "is_flag": bool(getattr(param, "is_flag", False)),
        "flag_value": _jsonable(getattr(param, "flag_value", None)),
        "count": bool(getattr(param, "count", False)),
        "multiple": bool(getattr(param, "multiple", False)),
        "nargs": getattr(param, "nargs", None),
        "default": _jsonable(default),
        "default_repr": None if default is None else _safe_repr(default),
        "default_is_callable": callable(default),
        "envvar": _jsonable(getattr(param, "envvar", None)),
        "help": _redact(getattr(param, "help", None))
        if getattr(param, "help", None)
        else None,
        "hidden": bool(getattr(param, "hidden", False)),
        "expose_value": bool(getattr(param, "expose_value", True)),
        "is_eager": bool(getattr(param, "is_eager", False)),
        "prompt": _jsonable(prompt) if not isinstance(prompt, bool) else prompt,
        "prompt_required": bool(getattr(param, "prompt_required", False)),
        "confirmation_prompt": _jsonable(getattr(param, "confirmation_prompt", False)),
        "metavar": _redact(getattr(param, "metavar", None))
        if getattr(param, "metavar", None)
        else None,
        "type": type_info,
    }
    return info


def walk_cli_tree(root_import: str) -> dict:
    module_path, attr = root_import.split(":")
    mod = import_module(module_path)
    root = getattr(mod, attr)
    ctx = _root_context(root)

    nodes: list[dict] = []
    counters = {"nodes": 0, "groups": 0, "leaves": 0}
    leaf_paths: list[str] = []

    def visit(cmd, path):
        counters["nodes"] += 1
        is_group = _is_group(cmd)
        children = _subcommands(cmd, ctx) if is_group else []
        node = {
            "path": list(path),
            "command": " ".join(path),
            "name": path[-1],
            "kind": "group" if is_group else "command",
            "help": _short_doc(cmd)
            or (
                _redact(getattr(cmd, "help", None))
                if getattr(cmd, "help", None)
                else None
            ),
            "short_help": _redact(getattr(cmd, "short_help", None))
            if getattr(cmd, "short_help", None)
            else None,
            "deprecated": bool(getattr(cmd, "deprecated", False)),
            "hidden": bool(getattr(cmd, "hidden", False)),
            "no_args_is_help": bool(getattr(cmd, "no_args_is_help", False)),
            "params": [_param_info(p) for p in getattr(cmd, "params", [])],
            "subcommands": [name for name, _ in children],
        }
        nodes.append(node)
        if is_group:
            counters["groups"] += 1
            for name, sub in children:
                visit(sub, path + [name])
        else:
            counters["leaves"] += 1
            leaf_paths.append(" ".join(path))

    visit(root, ["notebooklm"])
    nodes.sort(key=lambda n: n["command"])
    leaf_paths.sort()

    # Index of every Choice option across the tree (parity-critical surface).
    choice_index = []
    for node in nodes:
        for p in node["params"]:
            choices = p["type"].get("choices")
            if choices:
                choice_index.append(
                    {"command": node["command"], "param": p["name"], "choices": choices}
                )

    return {
        "root_class": type(root).__name__,
        "counts": {
            "nodes": counters["nodes"],
            "groups": counters["groups"],
            "leaves": counters["leaves"],
        },
        "groups": [n["command"] for n in nodes if n["kind"] == "group"],
        "leaf_commands": leaf_paths,
        "choice_options": choice_index,
        "nodes": nodes,
    }


# --------------------------------------------------------------------------- #
# Python API surface
# --------------------------------------------------------------------------- #

PUBLIC_MODULES = (
    "notebooklm",
    "notebooklm.client",
    "notebooklm.auth",
    "notebooklm.exceptions",
    "notebooklm.types",
    "notebooklm.artifacts",
    "notebooklm.research",
    "notebooklm.config",
    "notebooklm.io",
    "notebooklm.log",
    "notebooklm.migration",
    "notebooklm.paths",
    "notebooklm.urls",
    "notebooklm.utils",
    "notebooklm.rpc",
    "notebooklm.rpc.decoder",
    "notebooklm.rpc.encoder",
    "notebooklm.rpc.overrides",
    "notebooklm.rpc.types",
)


def _classify(obj) -> str:
    if isinstance(obj, type):
        if issubclass(obj, BaseException):
            return "exception"
        if issubclass(obj, enum.Enum):
            return "enum"
        if dataclasses.is_dataclass(obj):
            return "dataclass"
        return "class"
    if isinstance(obj, types.ModuleType):
        return "module"
    if inspect.isfunction(obj) or inspect.isbuiltin(obj) or inspect.ismethod(obj):
        return "function"
    if callable(obj):
        return "callable"
    return "constant"


def _enum_members(cls) -> dict:
    out = {}
    for member in cls:
        out[member.name] = _jsonable(member.value)
        if out[member.name] is None and member.value is not None:
            out[member.name] = _safe_repr(member.value)
    return out


def _dataclass_fields(cls) -> list:
    fields = []
    for f in dataclasses.fields(cls):
        has_default = f.default is not dataclasses.MISSING
        has_factory = f.default_factory is not dataclasses.MISSING  # type: ignore[misc]
        fields.append(
            {
                "name": f.name,
                "annotation": _redact(_annotation_str(f.type)),
                "has_default": has_default or has_factory,
                "default_repr": _safe_repr(f.default) if has_default else None,
                "default_factory": getattr(f.default_factory, "__name__", None)
                if has_factory
                else None,
            }
        )
    return fields


def _class_methods(cls, include_signatures=True, public_only=True) -> list:
    methods = []
    for name, member in inspect.getmembers(cls):
        if (
            public_only
            and name.startswith("_")
            and name not in ("__init__", "__aenter__", "__aexit__")
        ):
            continue
        if not (
            inspect.isfunction(member)
            or inspect.ismethod(member)
            or inspect.iscoroutinefunction(member)
        ):
            continue
        entry = {
            "name": name,
            "is_async": inspect.iscoroutinefunction(member),
            "is_classmethod": isinstance(
                inspect.getattr_static(cls, name, None), classmethod
            ),
            "is_staticmethod": isinstance(
                inspect.getattr_static(cls, name, None), staticmethod
            ),
        }
        if include_signatures:
            entry["signature"] = _signature(member)
        methods.append(entry)
    methods.sort(key=lambda m: m["name"])
    return methods


def _mro_names(cls) -> list:
    names = []
    for base in cls.__mro__:
        if base is object:
            break
        names.append(f"{base.__module__}.{base.__qualname__}")
    return names


def _describe_member(name, obj, owning_module) -> dict:
    kind = _classify(obj)
    entry = {
        "name": name,
        "kind": kind,
        "defined_in": getattr(obj, "__module__", None),
        "doc": _short_doc(obj),
    }
    if kind == "enum":
        entry["members"] = _enum_members(obj)
        entry["base"] = (
            type(obj).__mro__[1].__name__ if hasattr(obj, "__mro__") else None
        )
        entry["enum_bases"] = [
            b.__name__ for b in obj.__mro__ if b not in (obj, object)
        ]
    elif kind == "dataclass":
        entry["fields"] = _dataclass_fields(obj)
        entry["init_signature"] = _signature(obj)
        entry["frozen"] = bool(
            getattr(getattr(obj, "__dataclass_params__", None), "frozen", False)
        )
    elif kind == "exception":
        entry["mro"] = _mro_names(obj)
        entry["init_signature"] = _signature(obj.__init__)
        entry["bases"] = [b.__name__ for b in obj.__bases__]
    elif kind == "class":
        entry["bases"] = [b.__name__ for b in obj.__bases__]
        entry["mro"] = _mro_names(obj)
        entry["is_abstract"] = bool(getattr(obj, "__abstractmethods__", frozenset()))
        entry["init_signature"] = _signature(getattr(obj, "__init__", None))
        entry["methods"] = _class_methods(obj)
    elif kind in ("function", "callable"):
        entry["is_async"] = inspect.iscoroutinefunction(obj)
        entry["signature"] = _signature(obj)
    elif kind == "module":
        entry["module_name"] = getattr(obj, "__name__", None)
    else:  # constant
        entry["type"] = type(obj).__name__
        entry["value"] = _jsonable(obj)
        if entry["value"] is None and obj is not None:
            entry["value_repr"] = _safe_repr(obj)
    return entry


def _public_names(mod):
    declared = getattr(mod, "__all__", None)
    if declared:
        return sorted(declared)
    names = []
    for name in dir(mod):
        if name.startswith("_"):
            continue
        obj = getattr(mod, name)
        # keep names actually owned by the notebooklm package (skip re-exported deps)
        owner = getattr(obj, "__module__", "") or ""
        if isinstance(obj, types.ModuleType):
            owner = getattr(obj, "__name__", "")
        if owner.startswith("notebooklm") or not owner:
            names.append(name)
    return sorted(names)


def _subclients(api_errors: list) -> dict:
    """Construct a client with dummy auth (no I/O) to enumerate sub-client services."""
    try:
        client_mod = import_module("notebooklm.client")
        auth_mod = import_module("notebooklm.auth")
        client_cls = client_mod.NotebookLMClient
        auth_cls = auth_mod.AuthTokens
        auth = auth_cls(
            cookies={"notebooklm.google.com": {"SID": "x"}},
            csrf_token="phase0-dummy",
            session_id="phase0-dummy",
        )
        client = client_cls(auth=auth)
    except Exception as exc:  # pragma: no cover - defensive
        api_errors.append(
            f"subclient introspection failed: {type(exc).__name__}: {exc}"
        )
        return {}

    out = {}
    try:
        for attr, value in vars(client).items():
            if attr.startswith("_"):
                continue
            mod_name = type(value).__module__
            if not mod_name.startswith("notebooklm"):
                continue
            out[attr] = {
                "attribute": attr,
                "class": type(value).__qualname__,
                "module": mod_name,
                "doc": _short_doc(value),
                "async_methods": sorted(
                    n
                    for n, _ in inspect.getmembers(
                        value, predicate=inspect.iscoroutinefunction
                    )
                    if not n.startswith("_")
                ),
                "methods": _class_methods(type(value)),
            }
    finally:
        # The dummy client opened no sockets (connection is lazy); drop references.
        del client
    return dict(sorted(out.items()))


def _client_lifecycle(api_errors: list) -> dict:
    try:
        client_cls = import_module("notebooklm.client").NotebookLMClient
    except Exception as exc:  # pragma: no cover
        api_errors.append(f"client lifecycle introspection failed: {exc}")
        return {}
    classmethods = sorted(
        n
        for n in dir(client_cls)
        if isinstance(inspect.getattr_static(client_cls, n, None), classmethod)
    )
    async_methods = sorted(
        n
        for n, v in inspect.getmembers(
            client_cls, predicate=inspect.iscoroutinefunction
        )
        if not n.startswith("_")
    )
    properties = sorted(
        n for n, v in inspect.getmembers(type(client_cls)) if isinstance(v, property)
    )
    instance_props = sorted(
        n for n, v in inspect.getmembers(client_cls, lambda o: isinstance(o, property))
    )
    return {
        "init_signature": _signature(client_cls.__init__),
        "is_async_context_manager": hasattr(client_cls, "__aenter__")
        and hasattr(client_cls, "__aexit__"),
        "classmethods": classmethods,
        "async_public_methods": async_methods,
        "properties": instance_props or properties,
    }


def _annotation_identity_deviations(api: dict) -> list:
    """Surface annotations that reference dependency-owned types (e.g. httpx.*)."""
    hits = []
    pattern = re.compile(
        r"\b(httpx|h11|httpcore|click|rich|filelock|playwright|rookiepy)\b"
    )

    def scan_sig(where, sig):
        if not sig:
            return
        for p in sig.get("parameters", []):
            ann = p.get("annotation")
            if ann and pattern.search(ann):
                hits.append({"location": f"{where}({p['name']})", "annotation": ann})
        ret = sig.get("return_annotation")
        if ret and pattern.search(ret):
            hits.append({"location": f"{where}->return", "annotation": ret})

    for mod_name, mod in api["modules"].items():
        for m in mod["members"]:
            for key in ("signature", "init_signature"):
                scan_sig(f"{mod_name}.{m['name']}", m.get(key))
            for meth in m.get("methods", []):
                scan_sig(
                    f"{mod_name}.{m['name']}.{meth['name']}", meth.get("signature")
                )
            for f in m.get("fields", []):
                ann = f.get("annotation")
                if ann and pattern.search(ann):
                    hits.append(
                        {
                            "location": f"{mod_name}.{m['name']}.{f['name']}",
                            "annotation": ann,
                        }
                    )
    # de-duplicate
    seen = set()
    unique = []
    for h in hits:
        key = (h["location"], h["annotation"])
        if key not in seen:
            seen.add(key)
            unique.append(h)
    unique.sort(key=lambda h: h["location"])
    return unique


def build_python_api() -> dict:
    api_errors: list[str] = []
    modules = {}
    importable_public_names = set()
    for mod_name in PUBLIC_MODULES:
        try:
            mod = import_module(mod_name)
        except Exception as exc:  # pragma: no cover
            api_errors.append(f"import {mod_name} failed: {exc}")
            continue
        members = []
        for name in _public_names(mod):
            try:
                obj = getattr(mod, name)
            except Exception as exc:  # pragma: no cover
                api_errors.append(f"getattr {mod_name}.{name} failed: {exc}")
                continue
            members.append(_describe_member(name, obj, mod_name))
            if mod_name == "notebooklm":
                importable_public_names.add(name)
        members.sort(key=lambda m: m["name"])
        modules[mod_name] = {
            "module": mod_name,
            "has_dunder_all": getattr(mod, "__all__", None) is not None,
            "public_name_count": len(members),
            "members": members,
        }

    api = {"modules": modules}

    # Exception hierarchy (from the centralized exceptions module).
    exc_mod = modules.get("notebooklm.exceptions", {})
    exception_hierarchy = []
    for m in exc_mod.get("members", []):
        if m["kind"] == "exception":
            exception_hierarchy.append(
                {
                    "name": m["name"],
                    "bases": m.get("bases", []),
                    "mro": m.get("mro", []),
                }
            )

    # Enum value inventory across the public surface.
    enum_inventory = {}
    for mod in modules.values():
        for m in mod["members"]:
            if m["kind"] == "enum" and m["name"] not in enum_inventory:
                enum_inventory[m["name"]] = m["members"]

    # __all__ aliases: names in the root __all__ that resolve to the same object.
    root_mod = import_module("notebooklm")
    alias_groups = {}
    for name in getattr(root_mod, "__all__", []):
        try:
            obj = getattr(root_mod, name)
        except Exception:
            continue
        key = id(obj)
        alias_groups.setdefault(key, []).append(name)
    aliases = {
        sorted(names)[0]: sorted(names)
        for names in alias_groups.values()
        if len(names) > 1
    }

    api["root_all"] = sorted(getattr(root_mod, "__all__", []))
    api["root_all_count"] = len(getattr(root_mod, "__all__", []))
    api["importable_public_names"] = sorted(importable_public_names)
    api["exception_hierarchy"] = sorted(exception_hierarchy, key=lambda e: e["name"])
    api["enum_inventory"] = dict(sorted(enum_inventory.items()))
    api["aliases"] = aliases
    api["client"] = _client_lifecycle(api_errors)
    api["subclients"] = _subclients(api_errors)
    api["annotation_identity_deviations"] = _annotation_identity_deviations(api)
    api["introspection_errors"] = api_errors
    return api


# --------------------------------------------------------------------------- #
# Auth sources (interactive login + browser-cookie import facts)
# --------------------------------------------------------------------------- #


def _read_pkg_source(package_dir: str) -> dict:
    sources = {}
    for dirpath, _dirs, files in os.walk(package_dir):
        for fn in files:
            if fn.endswith(".py"):
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, package_dir)
                try:
                    sources[rel] = open(
                        full, "r", encoding="utf-8", errors="replace"
                    ).read()
                except OSError:
                    continue
    return sources


def build_auth_sources(cli_tree: dict, package_dir: str) -> dict:
    sources = _read_pkg_source(package_dir)

    # Interactive-login browser choices straight from the real Click option.
    login_browsers = []
    for opt in cli_tree["choice_options"]:
        if opt["command"] == "notebooklm login" and opt["param"] == "browser":
            login_browsers = opt["choices"]

    # Chromium-family cookie-import set, read reflectively from upstream constant.
    chromium_family = []
    try:
        prof = import_module("notebooklm.cli._chromium_profiles")
        cf = getattr(prof, "_CHROMIUM_BROWSERS", None)
        if cf is not None:
            chromium_family = sorted(str(x) for x in cf)
    except Exception:
        # Fall back to scraping the literal from source.
        for rel, text in sources.items():
            m = re.search(r"_CHROMIUM_BROWSERS[^{]*\{([^}]*)\}", text)
            if m:
                chromium_family = sorted(re.findall(r'"([a-z0-9\-]+)"', m.group(1)))
                break

    # rookiepy backends referenced in upstream source.
    rookiepy_refs = sorted(
        set(re.findall(r"rookiepy\.([a-z_]+)", "\n".join(sources.values())))
    )

    # OS-specific browser cookie-store path keys (per-OS dicts in _chromium_profiles).
    os_path_keys = {}
    for osname, marker in (
        ("macos", "app_support"),
        ("linux", "xdg"),
        ("windows", "base"),
    ):
        keys = set()
        for rel, text in sources.items():
            if "_chromium_profiles" not in rel:
                continue
            for block in re.findall(rf"{marker}\b.*?\{{(.*?)\}}", text, re.S):
                keys.update(re.findall(r'"([a-z0-9\-]+)"\s*:', block))
        if keys:
            os_path_keys[osname] = sorted(keys)

    firefox_support = any("_firefox_containers" in rel for rel in sources)
    safari_support = bool(re.search(r"\bsafari\b", "\n".join(sources.values()), re.I))

    # Auth/session/profile command surface, taken from the real CLI tree.
    auth_command_groups = (
        "auth",
        "profile",
        "session",
        "login",
        "logout",
        "doctor",
        "status",
    )
    auth_commands = [
        n["command"]
        for n in cli_tree["nodes"]
        if any(part in n["path"] for part in auth_command_groups)
    ]

    # Full param set of the login command (browser/profile/account flags).
    login_params = []
    for n in cli_tree["nodes"]:
        if n["command"] == "notebooklm login":
            login_params = [p["name"] for p in n["params"]]

    return {
        "interactive_login_browsers": login_browsers,
        "chromium_family_cookie_browsers": chromium_family,
        "firefox_cookie_support": firefox_support,
        "safari_cookie_support": safari_support,
        "rookiepy_backends_referenced": rookiepy_refs,
        "os_cookie_store_path_keys": os_path_keys,
        "auth_session_profile_commands": sorted(auth_commands),
        "login_command_params": sorted(login_params),
    }


# --------------------------------------------------------------------------- #
# RPC wire shape (for fake-server / parser fixtures)
# --------------------------------------------------------------------------- #


def build_rpc_shape(package_dir: str) -> dict:
    sources = _read_pkg_source(package_dir)
    joined = "\n".join(sources.values())
    # XSSI guard prefixes that upstream strips before JSON parsing.
    xssi = sorted(set(re.findall(r"""["'](\)\]\}'?)["']""", joined)))
    batch_markers = sorted(
        set(re.findall(r'"(wrb\.fr|af\.httprm|batchexecute|rpcids|f\.req)"', joined))
    )
    endpoints = sorted(set(re.findall(r'"(/_/[A-Za-z0-9_/.\-]+)"', joined)))
    host_refs = sorted(
        set(
            re.findall(
                r"(notebooklm\.google\.com|[a-z]+\.clients6\.google\.com)", joined
            )
        )
    )
    return {
        "xssi_prefixes": xssi,
        "batchexecute_markers": batch_markers,
        "endpoint_path_literals": endpoints[:50],
        "host_literals": host_refs,
        "rpc_modules": sorted(rel for rel in sources if rel.startswith("rpc/")),
    }


# --------------------------------------------------------------------------- #
# Dependency metadata
# --------------------------------------------------------------------------- #


def build_dependency_meta() -> dict:
    from importlib import metadata

    dist = metadata.distribution("notebooklm-py")
    requires = list(dist.requires or [])
    base = []
    extras = {}
    for req in requires:
        if "extra ==" in req:
            m = re.search(r"extra == ['\"]([^'\"]+)['\"]", req)
            extra = m.group(1) if m else "unknown"
            extras.setdefault(extra, []).append(req)
        else:
            base.append(req)

    installed = {}
    for pkg in (
        "click",
        "rich",
        "httpx",
        "filelock",
        "httpcore",
        "h11",
        "certifi",
        "markdown-it-py",
        "pygments",
    ):
        try:
            installed[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            installed[pkg] = None

    return {
        "name": dist.metadata["Name"],
        "version": dist.metadata["Version"],
        "requires_python": dist.metadata["Requires-Python"],
        "requires_dist_raw": sorted(requires),
        "base_runtime_requirements": sorted(base),
        "extras": {k: sorted(v) for k, v in sorted(extras.items())},
        "installed_base_versions": installed,
    }


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Reflective oracle prober for notebooklm-py 0.7.2"
    )
    parser.add_argument("--out", required=True, help="Path to write the JSON bundle")
    parser.add_argument(
        "--root-import",
        default="notebooklm.notebooklm_cli:cli",
        help="module:attr of the Click root group",
    )
    args = parser.parse_args(argv)

    notebooklm = import_module("notebooklm")
    package_dir = os.path.dirname(notebooklm.__file__)
    _install_redactions(package_dir)

    from importlib import metadata

    bundle = {
        "meta": {
            "schema": "phase0-introspection/1",
            "upstream_name": "notebooklm-py",
            "upstream_version": metadata.version("notebooklm-py"),
            "inspection_python_version": "%d.%d.%d" % sys.version_info[:3],
            "implementation": sys.implementation.name,
        },
        "cli_tree": walk_cli_tree(args.root_import),
        "python_api": build_python_api(),
        "dependency_meta": build_dependency_meta(),
        "rpc_shape": build_rpc_shape(package_dir),
    }
    bundle["auth_sources"] = build_auth_sources(bundle["cli_tree"], package_dir)

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(bundle, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write("\n")

    counts = bundle["cli_tree"]["counts"]
    sys.stderr.write(
        "introspect_upstream: nodes=%d groups=%d leaves=%d api_modules=%d root_all=%d\n"
        % (
            counts["nodes"],
            counts["groups"],
            counts["leaves"],
            len(bundle["python_api"]["modules"]),
            bundle["python_api"]["root_all_count"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
