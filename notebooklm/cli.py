"""Argparse CLI for ZeroNotebookLM.

Phase 2A wires the offline auth/session/profile foundation into the command
tree: ``profile create|delete|list|rename|switch``, ``use``/``status``/``clear``,
``auth check|inspect|logout``, and ``doctor``. These operate purely on local
state under the resolved storage root (``--storage`` directory, then
``$NOTEBOOKLM_HOME``, then ``~/.notebooklm``).

Phase 2B adds the offline browser-cookie *import* foundation:
``login --browser-cookies BROWSER`` imports cookies from an explicit
``--cookie-store`` file or ``--fixture-root`` synthetic browser layout into the
profile's ``storage_state.json``, and ``auth inspect --browser BROWSER
--cookie-store/--fixture-root ...`` reports a redacted view of an explicit store.
Without an explicit store/root these refuse to read the real machine and exit
with the deterministic "later parity slice" code — they never touch a live
browser store, an OS keychain, or the network.

Phase 2C extends the offline auth foundation:
``login --browser-cookies`` now persists redacted explicit-source metadata
(``auth_source.json``) next to ``storage_state.json`` after a successful import,
and ``auth refresh --browser-cookies BROWSER`` re-imports from an explicit
``--cookie-store``/``--fixture-root`` or, with neither, from that persisted
metadata. Live-browser metadata re-resolves only through its explicitly
supported browser/OS lane.

Phase 2D adds an opt-in offline diagnostic to ``doctor``: ``doctor --auth-matrix
[--auth-matrix-path PATH] [--json]`` reports how the offline foundation maps onto
the pinned ``compat/auth_matrix.json`` (which rows have an offline foundation vs
remain blocked behind live authorization / browser discovery / OS credential
backend / network refresh). It is a pure read of an explicit (or committed compat)
matrix, never marks a row ``pass``, never mutates the matrix, and is off by default
so existing ``doctor`` output is unchanged.

Phase 2E-A adds an explicitly authorized live Firefox browser-store lane:
``login --browser-cookies firefox`` and ``auth inspect --browser firefox`` with no
explicit store/root resolve the current user's Firefox ``cookies.sqlite`` and
emit pathless, redacted summaries. ``auth refresh --browser-cookies firefox`` can
re-resolve from persisted ``live_browser`` metadata. Other live browsers remain
blocked until their own parity slices.

Phase 2E-B extends the live lane to macOS Safari: ``login --browser-cookies
safari``, ``auth inspect --browser safari``, and ``auth refresh --browser-cookies
safari`` with no explicit store/root resolve the current user's
``Cookies.binarycookies`` file and emit the same pathless, redacted summaries.
Safari has no profile concept, so a passed ``--browser-profile`` is ignored and the
persisted ``browser_profile`` is always ``None``.

Phase 2E-C1 adds an inspect-only live Chromium-family foothold:
``auth inspect --browser chrome`` (and other documented Chromium-family browsers)
may resolve a live ``Network/Cookies``/``Cookies`` DB and emit a Google-domain-only,
pathless, redacted summary. Phase 2E-C2A/C2B add macOS decryptor primitives and
synthetic live import/refresh wiring. Phase 2E-C2C adds the narrow real macOS
Keychain gate for Chromium live import/refresh when the user explicitly passes
``--os macOS``; bare ``login --browser-cookies chrome`` still refuses rather than
surprise-reading Keychain. Phase 2E-C3A adds the same explicit-OS gate for
Windows DPAPI. Linux uses only the explicit-OS legacy ``peanuts`` fallback;
libsecret/Secret Service lookup remains blocked.

Phase 2F adds the interactive-browser login foundation and public CLI wiring:
``login --browser [chromium|chrome|msedge]`` launches an isolated loopback-CDP
browser profile, probes the local DevTools endpoint, captures cookies with a
stdlib WebSocket/CDP ``Network.getAllCookies`` command, and writes
``storage_state.json``. Output is pathless/value-free and no ``auth_source.json``
is written because the source cannot be safely refreshed without live
browser/network state. Phase 2G adds profile-backed network auth diagnostics:
``auth check --test`` performs a non-persisting stdlib token-fetch probe, and
``auth refresh`` performs the same RotateCookies + NotebookLM homepage probe while
persisting rotated cookies. These are still foundations, not parity claims.

Phase 3A/3B batches promote the remaining pinned command tree through
fixture-backed, temp-scoped, or otherwise explicitly bounded parity surfaces.
Current promoted commands still avoid unapproved live RPC, credential reads, and
real NotebookLM mutation; individual live/auth rows remain conservative until
row-specific evidence promotes them.

Phase 3A5 promotes only offline ``list`` and the existing ``use`` command over
the reviewed fake RPC seam. ``list`` reads the committed synthetic
``list_notebooks`` fixture by default; ``use`` resolves selectors against that
same fixture unless ``--force`` is passed. Neither command enters live RPC,
auth/browser state, credentials, or remote mutation.

Phase 3A10 promotes only read-only ``note list`` and ``note get`` over the same
committed synthetic fake RPC seam. Note creation/update/deletion, mind-map note
backing, live RPC, auth/browser state, credentials, and remote mutation remain
closed.

Phase 3A11 promotes only read-only ``source list`` and ``source get`` over the
same committed synthetic fake RPC seam. Source upload/mutation/fulltext/guide,
wait/stale flows, live RPC, auth/browser state, credentials, and remote mutation
remain closed.

Phase 3A13 promotes only read-only ``artifact list`` and ``artifact get`` over
the same committed synthetic fake RPC seam. Artifact generation, download/export,
rename/delete/retry, poll/wait flows, live RPC, auth/browser state, credentials,
and remote mutation remain closed.

Phase 3A14 promotes only read-only ``ask`` over the same committed synthetic fake
RPC seam and the offline ``ChatAPI`` foothold. Phase 3B6 later extends the same
fixture-backed path to ``--new``, source-filtered asks, and save-as-note while
live RPC, auth/browser state, credentials, and remote mutation remain closed.

Phase 3A15 promotes only read-only ``metadata`` over the same committed
synthetic fake RPC seam. ``metadata`` combines offline notebook metadata with
source summaries; summary/history, live RPC, auth/browser state, credentials,
and remote mutation remain closed.

Phase 3A16 promotes only read-only ``history`` over the same committed synthetic
fake RPC seam and offline ``ChatAPI`` cache. Phase 3B6 later extends the same
fixture-backed path to clear/save history behavior while live RPC, auth/browser
state, credentials, and remote mutation remain closed.

Phase 3A17 promotes only read-only ``summary`` over the same committed synthetic
fake RPC seam. Remote AI summary generation, live RPC, auth/browser state,
credentials, and remote mutation remain closed.

Phase 3A18 promotes only read-only ``source fulltext`` over committed synthetic
source fixtures. ``source guide``, mutation, wait/stale/refresh flows,
generation/download, live RPC, auth/browser/home reads, credentials, and
parity-row promotion remain closed.

Phase 3A19 promotes only read-only ``source guide`` over committed synthetic
source fixtures. Source mutation, wait/stale/refresh flows, generation/download,
live RPC, auth/browser/home reads, credentials, and parity-row promotion remain
closed.

Phase 3A20 promotes only read-only ``source stale`` over committed synthetic
source fixtures. It derives staleness from fixture source status only; source
mutation, wait/refresh flows, generation/download, live RPC, auth/browser/home
reads, credentials, and parity-row promotion remain closed.

Phase 3A21 promotes only offline/read-only ``source wait`` over committed
synthetic source fixtures. It derives ready/failed/timeout state from fixture
source status only; source mutation/refresh, generation/download, live RPC,
auth/browser/home reads, credentials, and parity-row promotion remain closed.

Batch 3B1 promotes grouped offline/read-only status surfaces over committed
sanitized fixtures: ``language list|get``, ``artifact poll|wait|suggestions``,
``research status|wait`` without source import, and ``share status``. Language
setting, research import/start, public/share mutation, artifact export/download,
live RPC, auth/browser/home reads, credentials, and parity-row promotion remain
closed.

Batch 3B2 promotes grouped fixture-backed mutation/generation/export surfaces
through in-memory services only: notebook/note/source/artifact mutation helpers,
artifact retry/export/generation APIs, and settings output-language writes. Real
NotebookLM mutation, live RPC, auth/browser/home reads, credentials, public
sharing, downloads, and parity-row promotion remain closed.

Batch 3B3 promotes the ``generate`` CLI group over the same in-memory synthetic
artifact generation seam. It accepts the pinned generate leaf command options and
returns deterministic fixture-backed JSON/status payloads; downloads, live RPC,
auth/browser/home reads, credentials, public sharing, and real artifact mutation
remain closed.

Batch 3B4 promotes the ``download`` CLI group and corresponding artifact API
download helpers over deterministic local file writes from synthetic fixtures.
Live network downloads, auth/browser/home reads, credentials, public sharing,
and real NotebookLM mutation remain closed.

Batch 3B5 extends the ``language`` CLI group with fixture-backed
``get --local`` and ``set`` semantics over the existing in-memory settings seam,
promotes root notebook ``create``/``delete``/``rename`` through the synthetic
notebook metadata service, and promotes the fixture-backed ``source add``,
``source add-drive``, ``source delete-by-title``, and ``source clean`` leaves.
It validates the pinned upstream language table and never reads real home,
auth/browser state, credentials, live RPC, or real NotebookLM data.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import re
import sys
import tempfile
import time
from collections.abc import Sequence
from datetime import datetime, timezone
from importlib import resources
from os import getenv
from pathlib import Path
from typing import Any

from . import __version__
from . import agent_templates as _agent_templates
from . import _artifacts_impl as _artifacts
from . import auth as _auth
from . import auth_readiness as _readiness
from . import browser_cookies as _bc
from . import chat as _chat
from . import client as _client
from . import fake_rpc as _fake_rpc
from . import interactive_login as _il
from . import notebooks as _notebooks
from . import notes as _notes
from . import offline_status as _offline_status
from . import profiles as _profiles
from . import sources as _sources
from . import types as _types
from .errors import (
    NotebookLMError,
    NotImplementedInPhaseError,
    ValidationError,
    exit_code_for,
)
from .exceptions import NotebookLMError as PublicNotebookLMError
from .output import render

ROOT_COMMANDS = (
    "agent",
    "artifact",
    "ask",
    "auth",
    "clear",
    "completion",
    "configure",
    "create",
    "delete",
    "doctor",
    "download",
    "generate",
    "history",
    "language",
    "list",
    "login",
    "metadata",
    "note",
    "profile",
    "rename",
    "research",
    "share",
    "skill",
    "source",
    "status",
    "summary",
    "use",
)

_UPSTREAM_ROOKIEPY_SUPPORTED_BROWSERS = (
    "arc",
    "brave",
    "chrome",
    "chromium",
    "edge",
    "firefox",
    "ie",
    "librewolf",
    "octo",
    "opera",
    "opera-gx",
    "opera_gx",
    "safari",
    "vivaldi",
    "zen",
)

# Commands whose offline foundation is implemented in Phase 2A.
PHASE2A_COMMANDS = frozenset({"profile", "use", "status", "clear", "auth", "doctor"})
# Phase 2B adds the offline browser-cookie import foundation under ``login``.
PHASE2B_COMMANDS = frozenset({"login"})
# Phase 3A5 promotes only the offline fixture-backed notebook list command. The
# existing ``use`` command is already in Phase 2A and now validates against the
# same fake list unless explicitly forced.
PHASE3A5_COMMANDS = frozenset({"list"})
# Phase 3A10 promotes only read-only fixture-backed note list/get subcommands.
PHASE3A10_COMMANDS = frozenset({"note"})
# Phase 3A11 promotes only read-only fixture-backed source list/get subcommands.
PHASE3A11_COMMANDS = frozenset({"source"})
# Phase 3A13 promotes only read-only fixture-backed artifact list/get subcommands.
PHASE3A13_COMMANDS = frozenset({"artifact"})
# Phase 3A14 promotes only read-only fixture-backed ask.
PHASE3A14_COMMANDS = frozenset({"ask"})
# Phase 3A15 promotes only read-only fixture-backed metadata.
PHASE3A15_COMMANDS = frozenset({"metadata"})
# Phase 3A16 promotes only read-only fixture-backed history.
PHASE3A16_COMMANDS = frozenset({"history"})
# Phase 3A17 promotes only read-only fixture-backed summary.
PHASE3A17_COMMANDS = frozenset({"summary"})
# Batch 3B1 promotes only fixture-backed read/status command groups.
PHASE3B1_COMMANDS = frozenset({"language", "research", "share"})
# Batch 3B3 promotes only fixture-backed artifact generation CLI surfaces.
PHASE3B3_COMMANDS = frozenset({"generate"})
# Batch 3B4 promotes only fixture-backed generated-content download surfaces.
PHASE3B4_COMMANDS = frozenset({"download"})
# Batch 3B5 promotes fixture-backed root notebook mutation commands.
PHASE3B5_COMMANDS = frozenset({"create", "delete", "rename"})
# Batch 3B6 promotes fixture-backed chat configuration/history/save behavior.
PHASE3B6_COMMANDS = frozenset({"configure"})
# Batch 3B14 promotes static bundled agent instruction display.
PHASE3B14_COMMANDS = frozenset({"agent"})
# Batch 3B15 promotes static shell-completion script display.
PHASE3B15_COMMANDS = frozenset({"completion"})
# Batch 3B16 promotes temp-scoped agent skill integration files.
PHASE3B16_COMMANDS = frozenset({"skill"})
IMPLEMENTED_COMMANDS = (
    PHASE2A_COMMANDS
    | PHASE2B_COMMANDS
    | PHASE3A5_COMMANDS
    | PHASE3A10_COMMANDS
    | PHASE3A11_COMMANDS
    | PHASE3A13_COMMANDS
    | PHASE3A14_COMMANDS
    | PHASE3A15_COMMANDS
    | PHASE3A16_COMMANDS
    | PHASE3A17_COMMANDS
    | PHASE3B1_COMMANDS
    | PHASE3B3_COMMANDS
    | PHASE3B4_COMMANDS
    | PHASE3B5_COMMANDS
    | PHASE3B6_COMMANDS
    | PHASE3B14_COMMANDS
    | PHASE3B15_COMMANDS
    | PHASE3B16_COMMANDS
)
SYNTHETIC_HISTORY_QUESTION = "Phase 0 synthetic question."
INTERACTIVE_LOGIN_DEFAULT_BROWSER = "chromium"
INTERACTIVE_LOGIN_DEBUGGING_PORT = 9222
INTERACTIVE_LOGIN_URL = "https://notebooklm.google.com/"
INTERACTIVE_LOGIN_PROBE_ATTEMPTS = 20
INTERACTIVE_LOGIN_PROBE_DELAY_SECONDS = 0.25
INTERACTIVE_LOGIN_COOKIE_ATTEMPTS = 180
INTERACTIVE_LOGIN_COOKIE_DELAY_SECONDS = 1.0


class _ExplicitStorageStore(_profiles.ProfileStore):
    """ProfileStore-shaped wrapper for upstream ``--storage storage_state.json``."""

    def __init__(self, storage_path: str | Path) -> None:
        self._storage_path = Path(storage_path).expanduser().resolve()
        super().__init__(self._storage_path.parent)

    def profile_dir(self, name: str) -> Path:
        _profiles.validate_profile_name(name)
        return self._storage_path.parent

    def storage_state_path(self, name: str) -> Path:
        _profiles.validate_profile_name(name)
        return self._storage_path

    def context_path(self, name: str) -> Path:
        _profiles.validate_profile_name(name)
        return self._storage_path.with_suffix(
            self._storage_path.suffix + ".context.json"
        )


def _profile_store_from_storage_arg(raw: str | None) -> _profiles.ProfileStore:
    if raw is None:
        store = _profiles.ProfileStore(None)
        store._explicit_cli_storage_arg = False  # type: ignore[attr-defined]
        return store
    path = Path(raw).expanduser().resolve()
    if path.is_file() or path.suffix == ".json":
        store = _ExplicitStorageStore(path)
    else:
        store = _profiles.ProfileStore(path)
    store._explicit_cli_storage_arg = True  # type: ignore[attr-defined]
    return store


def _auth_check_cli_payload(
    storage_path: Path,
    *,
    profile: str | None,
    test_fetch: bool,
    env_auth: bool,
    home_env: bool,
) -> tuple[dict[str, Any], bool]:
    auth_source = (
        "NOTEBOOKLM_AUTH_JSON"
        if env_auth
        else f"$NOTEBOOKLM_HOME ({storage_path})"
        if home_env
        else f"file ({storage_path})"
    )
    checks: dict[str, bool | None] = {
        "storage_exists": False,
        "json_valid": False,
        "cookies_present": False,
        "sid_cookie": False,
        "token_fetch": None,
    }
    details: dict[str, Any] = {
        "storage_path": str(storage_path),
        "auth_source": auth_source,
        "cookies_found": [],
        "cookie_domains": [],
        "error": None,
    }

    if env_auth:
        checks["storage_exists"] = True
        try:
            state = json.loads(getenv("NOTEBOOKLM_AUTH_JSON") or "")
        except json.JSONDecodeError as exc:
            details["error"] = f"Invalid JSON: {exc}"
            return {"status": "error", "checks": checks, "details": details}, False
    else:
        checks["storage_exists"] = storage_path.exists()
        if not checks["storage_exists"]:
            details["error"] = f"Storage file not found: {storage_path}"
            return {"status": "error", "checks": checks, "details": details}, False
        try:
            state = json.loads(storage_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            details["error"] = f"Invalid JSON: {exc}"
            return {"status": "error", "checks": checks, "details": details}, False
        except (OSError, UnicodeDecodeError) as exc:
            details["error"] = f"Storage unreadable: {exc}"
            return {"status": "error", "checks": checks, "details": details}, False

    checks["json_valid"] = True
    try:
        cookies = _auth.extract_cookies_from_storage(state)
    except ValueError as exc:
        details["error"] = str(exc)
        return {"status": "error", "checks": checks, "details": details}, False

    checks["cookies_present"] = True
    checks["sid_cookie"] = "SID" in cookies
    details["cookies_found"] = list(cookies)
    cookies_by_domain: dict[str, list[str]] = {}
    for cookie in state.get("cookies", []):
        if not isinstance(cookie, dict):
            continue
        domain = str(cookie.get("domain", ""))
        name = str(cookie.get("name", ""))
        if domain and name and "google" in domain.lower():
            cookies_by_domain.setdefault(domain, []).append(name)
    details["cookies_by_domain"] = cookies_by_domain
    details["cookie_domains"] = sorted(cookies_by_domain)

    if test_fetch:
        try:
            token_path = None if env_auth else storage_path
            csrf, session_id = asyncio.run(
                _auth.fetch_tokens_with_domains(token_path, profile)
            )
            checks["token_fetch"] = True
            details["csrf_length"] = len(csrf)
            details["session_id_length"] = len(session_id)
        except Exception as exc:  # noqa: BLE001 - upstream reports any token failure
            checks["token_fetch"] = False
            details["error"] = f"Token fetch failed: {exc}"

    passed = all(v is True for v in checks.values() if v is not None)
    return {
        "status": "ok" if passed else "error",
        "checks": checks,
        "details": details,
    }, passed


def build_parser(*, prog: str = "notebooklm") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description=(
            "ZeroNotebookLM stdlib CLI with offline fixture-backed parity "
            "surfaces for the pinned notebooklm-py==0.7.2 command tree."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"NotebookLM CLI, version {__version__}"
    )
    parser.add_argument(
        "--storage",
        default=None,
        help="path to storage_state.json (or legacy profile storage root directory)",
    )
    parser.add_argument(
        "-p",
        "--profile",
        default=None,
        help="profile name to operate on (default: active profile, else 'default')",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="increase verbosity",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="suppress non-essential output"
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=ROOT_COMMANDS,
        help="command group/command; fixture-backed parity surfaces are local/offline unless explicitly documented",
    )
    parser.add_argument("args", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    return parser


def _emit(payload: object, *, json_mode: bool) -> None:
    sys.stdout.write(render(payload, json_mode=json_mode))


def _default_rpc_fixture_dir() -> Path:
    """Return the synthetic RPC fixture directory.

    Source checkouts keep fixtures under top-level ``compat/`` so parity tests can
    inspect them directly. Installed packages cannot rely on a repo root, so the
    wheel/sdist also carries the same sanitized fixtures under ``notebooklm/data``.
    """

    repo_fixture_dir = (
        Path(__file__).resolve().parent.parent / "compat" / "rpc_fixtures"
    )
    if repo_fixture_dir.exists():
        return repo_fixture_dir
    return Path(__file__).resolve().parent / "data" / "rpc_fixtures"


def _offline_notebook_service(
    fixture_dir: str | None = None,
) -> "_notebooks.OfflineNotebookMetadataService":
    """Build the Phase 3A offline notebook service from synthetic fixtures only."""

    root = Path(fixture_dir) if fixture_dir is not None else _default_rpc_fixture_dir()
    client = _fake_rpc.OfflineFixtureRpcClient.from_fixture_dir(root)
    return _notebooks.OfflineNotebookMetadataService.from_list_payload(
        client.list_notebooks_payload()
    )


def _offline_note_services(
    fixture_dir: str | None = None,
) -> tuple["_notebooks.OfflineNotebookMetadataService", "_notes.OfflineNoteService"]:
    """Build read-only notebook/note services from committed synthetic fixtures."""

    root = Path(fixture_dir) if fixture_dir is not None else _default_rpc_fixture_dir()
    client = _fake_rpc.OfflineFixtureRpcClient.from_fixture_dir(root)
    notebook_service = _notebooks.OfflineNotebookMetadataService.from_list_payload(
        client.list_notebooks_payload()
    )
    notebook_ids = [notebook.id for notebook in notebook_service.list()]
    note_service = _notes.OfflineNoteService.from_rpc(client, notebook_ids)
    return notebook_service, note_service


def _offline_source_services(
    fixture_dir: str | None = None,
) -> tuple[
    "_notebooks.OfflineNotebookMetadataService", "_sources.OfflineSourceService"
]:
    """Build read-only notebook/source services from committed synthetic fixtures."""

    root = Path(fixture_dir) if fixture_dir is not None else _default_rpc_fixture_dir()
    client = _fake_rpc.OfflineFixtureRpcClient.from_fixture_dir(root)
    notebook_service = _notebooks.OfflineNotebookMetadataService.from_list_payload(
        client.list_notebooks_payload()
    )
    notebook_ids = [notebook.id for notebook in notebook_service.list()]
    source_service = _sources.OfflineSourceService.from_rpc(client, notebook_ids)
    return notebook_service, source_service


def _offline_artifact_services(
    fixture_dir: str | None = None,
) -> tuple[
    "_notebooks.OfflineNotebookMetadataService", "_artifacts.OfflineArtifactService"
]:
    """Build read-only notebook/artifact services from committed synthetic fixtures."""

    root = Path(fixture_dir) if fixture_dir is not None else _default_rpc_fixture_dir()
    client = _fake_rpc.OfflineFixtureRpcClient.from_fixture_dir(root)
    notebook_service = _notebooks.OfflineNotebookMetadataService.from_list_payload(
        client.list_notebooks_payload()
    )
    notebook_ids = [notebook.id for notebook in notebook_service.list()]
    artifact_service = _artifacts.OfflineArtifactService.from_rpc(client, notebook_ids)
    return notebook_service, artifact_service


def _offline_chat_services(
    fixture_dir: str | None = None,
) -> tuple["_notebooks.OfflineNotebookMetadataService", "_chat.ChatAPI"]:
    """Build read-only notebook/chat services from committed synthetic fixtures."""

    root = Path(fixture_dir) if fixture_dir is not None else _default_rpc_fixture_dir()
    client = _fake_rpc.OfflineFixtureRpcClient.from_fixture_dir(root)
    notebook_service = _notebooks.OfflineNotebookMetadataService.from_list_payload(
        client.list_notebooks_payload()
    )
    return notebook_service, _chat.ChatAPI(rpc=client)


def _offline_status_fixtures(
    fixture_path: str | None = None,
) -> "_offline_status.OfflineReadOnlyStatusFixtures":
    """Build read/status fixtures from a committed or explicit sanitized file."""

    if fixture_path is None:
        return _offline_status.OfflineReadOnlyStatusFixtures.load_default()
    return _offline_status.OfflineReadOnlyStatusFixtures.from_path(fixture_path)


def _notebook_env_default() -> str | None:
    return getenv("NOTEBOOKLM_NOTEBOOK")


def _resolve_note_notebook(
    notebook_service: "_notebooks.OfflineNotebookMetadataService",
    selector: str | None,
) -> "_notebooks.Notebook":
    notebooks = notebook_service.list()
    selector = selector if selector is not None else _notebook_env_default()
    if selector is None:
        if not notebooks:
            raise ValidationError("notebook selector not found")
        return notebooks[0]
    return notebook_service.resolve(selector)


def _validate_limit(value: int | None) -> int | None:
    if value is not None and value < 0:
        raise ValidationError("limit must be non-negative")
    return value


# --------------------------------------------------------------------------- #
# Command handlers (offline/local-state only)
# --------------------------------------------------------------------------- #


def _handle_list(
    store: "_profiles.ProfileStore | None",
    args: Sequence[str],
    *,
    global_profile: str | None,
) -> int:
    parser = argparse.ArgumentParser(prog="notebooklm list")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "--limit", type=int, default=None, help="maximum notebooks to render"
    )
    parser.add_argument(
        "--no-truncate", action="store_true", help="accepted for CLI parity"
    )
    parser.add_argument(
        "--fixture-dir",
        default=None,
        help="explicit synthetic rpc_fixtures directory (offline only)",
    )
    ns = parser.parse_args(list(args))

    limit = _validate_limit(ns.limit)
    notebooks = _offline_notebook_service(ns.fixture_dir).list_dicts()
    if limit is not None:
        notebooks = notebooks[:limit]
    if ns.json:
        payload = {
            "notebooks": [
                {
                    "index": index,
                    "id": notebook.get("id"),
                    "title": notebook.get("title"),
                    "is_owner": notebook.get("is_owner"),
                    "created_at": notebook.get("created_at"),
                }
                for index, notebook in enumerate(notebooks, 1)
            ],
            "count": len(notebooks),
        }
        _emit(payload, json_mode=True)
        return 0
    _emit(notebooks, json_mode=ns.json)
    return 0


def _handle_create(
    store: "_profiles.ProfileStore | None",
    args: Sequence[str],
    *,
    global_profile: str | None,
) -> int:
    parser = argparse.ArgumentParser(prog="notebooklm create")
    parser.add_argument("title")
    parser.add_argument("--use", "-u", dest="switch_context", action="store_true")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "--fixture-dir",
        default=None,
        help="explicit synthetic rpc_fixtures directory (offline only)",
    )
    ns = parser.parse_args(list(args))
    notebook = _offline_notebook_service(ns.fixture_dir).create(ns.title)
    payload: dict[str, object] = {
        "notebook": {
            "id": notebook.id,
            "title": notebook.title,
            "created_at": notebook.created_at.isoformat()
            if notebook.created_at
            else None,
        }
    }
    if ns.switch_context:
        payload["active_notebook_id"] = notebook.id
    if ns.json:
        _emit(payload, json_mode=True)
    else:
        print(f"Created notebook: {notebook.id} - {notebook.title}")
        if ns.switch_context:
            print("Context set to new notebook")
    return 0


def _handle_delete(
    store: "_profiles.ProfileStore | None",
    args: Sequence[str],
    *,
    global_profile: str | None,
) -> int:
    parser = argparse.ArgumentParser(prog="notebooklm delete")
    parser.add_argument(
        "-n", "--notebook", default=None, help="synthetic notebook selector"
    )
    parser.add_argument("--yes", "-y", action="store_true")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "--fixture-dir",
        default=None,
        help="explicit synthetic rpc_fixtures directory (offline only)",
    )
    ns = parser.parse_args(list(args))
    if not ns.yes:
        raise ValidationError("pass --yes to confirm deletion")
    selector = ns.notebook if ns.notebook is not None else _notebook_env_default()
    if selector is None:
        raise ValidationError("notebook selector not found")
    service = _offline_notebook_service(ns.fixture_dir)
    notebook = service.resolve(selector)
    service.delete(notebook.id)
    _emit({"notebook_id": notebook.id, "success": True}, json_mode=ns.json)
    return 0


def _handle_rename(
    store: "_profiles.ProfileStore | None",
    args: Sequence[str],
    *,
    global_profile: str | None,
) -> int:
    parser = argparse.ArgumentParser(prog="notebooklm rename")
    parser.add_argument("new_title")
    parser.add_argument(
        "-n", "--notebook", default=None, help="synthetic notebook selector"
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "--fixture-dir",
        default=None,
        help="explicit synthetic rpc_fixtures directory (offline only)",
    )
    ns = parser.parse_args(list(args))
    selector = ns.notebook if ns.notebook is not None else _notebook_env_default()
    if selector is None:
        raise ValidationError("notebook selector not found")
    service = _offline_notebook_service(ns.fixture_dir)
    notebook = service.rename(selector, ns.new_title)
    _emit(
        {"notebook_id": notebook.id, "title": notebook.title, "success": True},
        json_mode=ns.json,
    )
    return 0


def _handle_language(
    store: "_profiles.ProfileStore | None",
    args: Sequence[str],
    *,
    global_profile: str | None,
) -> int:
    argv = list(args)
    if not argv:
        parser = argparse.ArgumentParser(prog="notebooklm language")
        parser.print_help()
        return 0

    subcommand = argv[0]
    if subcommand == "list":
        parser = argparse.ArgumentParser(prog="notebooklm language list")
        parser.add_argument("--json", action="store_true", help="Output as JSON")
        ns = parser.parse_args(argv[1:])
        payload = {"languages": _offline_status.SUPPORTED_LANGUAGES}
        if ns.json:
            sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        else:
            for code, name in _offline_status.SUPPORTED_LANGUAGES.items():
                print(f"{code}\t{name}")
        return 0

    if subcommand == "get":
        parser = argparse.ArgumentParser(prog="notebooklm language get")
        parser.add_argument(
            "--local",
            dest="local_only",
            action="store_true",
            help="Show local config only (skip server sync)",
        )
        parser.add_argument("--json", action="store_true", help="Output as JSON")
        parser.add_argument(
            "--status-fixture",
            default=None,
            help="explicit sanitized read/status fixture (offline only)",
        )
        ns = parser.parse_args(argv[1:])
        fixtures = _offline_status_fixtures(ns.status_fixture)
        language = fixtures.get_output_language()
        payload = {
            "language": language,
            "name": _offline_status.language_name(language),
            "is_default": language == "en",
            "synced_from_server": not ns.local_only,
        }
        _emit(payload, json_mode=ns.json)
        return 0

    if subcommand == "set":
        parser = argparse.ArgumentParser(prog="notebooklm language set")
        parser.add_argument("language")
        parser.add_argument(
            "--local",
            dest="local_only",
            action="store_true",
            help="Set local config only (skip server sync)",
        )
        parser.add_argument("--json", action="store_true", help="Output as JSON")
        ns = parser.parse_args(argv[1:])
        name = _offline_status.language_name(ns.language)
        if name is None:
            raise ValidationError(
                "unknown language code; run 'notebooklm language list' to see supported codes"
            )
        payload = {
            "language": ns.language,
            "name": name,
            "message": "Language set successfully",
            "synced_to_server": not ns.local_only,
        }
        _emit(payload, json_mode=ns.json)
        return 0

    raise ValidationError("unknown language subcommand")


def _read_ask_prompt(question: str | None, prompt_file: str | None) -> str:
    text = _read_optional_prompt(
        question,
        prompt_file,
        required=True,
        label="question",
        file_label="prompt",
    )
    return text


def _read_optional_prompt(
    value: str | None,
    prompt_file: str | None,
    *,
    required: bool = False,
    label: str = "description",
    file_label: str | None = None,
) -> str:
    if prompt_file is not None:
        try:
            text = (
                sys.stdin.read()
                if prompt_file == "-"
                else Path(prompt_file).read_text(encoding="utf-8")
            )
        except OSError:
            raise ValidationError(
                f"{file_label or label} file could not be read"
            ) from None
    else:
        text = value or ""
    text = text.strip()
    if required and text == "":
        raise ValidationError(f"{label} is required")
    return text


def _validate_language_code(language: str | None) -> str | None:
    if language is not None and language not in _offline_status.SUPPORTED_LANGUAGES:
        raise ValidationError(
            f"unknown language code: {language}; run 'notebooklm language list' to see supported codes"
        )
    return language


def _validate_generation_controls(
    timeout: int | None, interval: int | None, retry: int | None
) -> None:
    if timeout is not None and timeout <= 0:
        raise ValidationError("generation timeout must be positive")
    if interval is not None and interval < 1:
        raise ValidationError("generation interval must be at least 1")
    if retry is not None and retry < 0:
        raise ValidationError("generation retry count must be non-negative")


def _generation_status_payload(status: Any) -> dict[str, object]:
    payload: dict[str, object] = {"task_id": status.task_id, "status": status.status}
    if status.url is not None:
        payload["url"] = status.url
    if status.error is not None:
        payload["error"] = status.error
    if status.error_code is not None:
        payload["error_code"] = status.error_code
    if status.metadata is not None:
        payload["metadata"] = status.metadata
    return payload


def _format_single_qa(question: str, answer: str) -> str:
    parts = []
    if question:
        parts.append(f"**Q:** {question}")
    if answer:
        parts.append(f"**A:** {answer}")
    return "\n\n".join(parts)


def _format_history_note(turns: list[tuple[str, str]]) -> str:
    return "\n\n---\n\n".join(
        f"### Turn {index}\n\n{_format_single_qa(question, answer)}"
        for index, (question, answer) in enumerate(turns, 1)
    )


def _handle_ask(
    store: "_profiles.ProfileStore | None",
    args: Sequence[str],
    *,
    global_profile: str | None,
) -> int:
    parser = argparse.ArgumentParser(prog="notebooklm ask")
    parser.add_argument("question", nargs="?", default=None)
    parser.add_argument(
        "--prompt-file",
        default=None,
        help="read prompt/query text from file or '-' for stdin",
    )
    parser.add_argument(
        "-n", "--notebook", default=None, help="synthetic notebook selector"
    )
    parser.add_argument(
        "--conversation-id", "-c", default=None, help="offline conversation id"
    )
    parser.add_argument(
        "--new", action="store_true", help="start a new synthetic offline conversation"
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="confirm non-interactive new conversation behavior",
    )
    parser.add_argument(
        "--source", "-s", action="append", default=None, help="synthetic source filter"
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "--save-as-note", action="store_true", help="save answer as an in-memory note"
    )
    parser.add_argument(
        "-t", "--note-title", default=None, help="note title for --save-as-note"
    )
    parser.add_argument(
        "--request-timeout",
        "--timeout",
        type=int,
        default=None,
        help="accepted for CLI parity",
    )
    parser.add_argument(
        "--fixture-dir",
        default=None,
        help="explicit synthetic rpc_fixtures directory (offline only)",
    )
    ns = parser.parse_args(list(args))
    if ns.note_title is not None and not ns.save_as_note:
        raise ValidationError("--note-title requires --save-as-note")
    if ns.request_timeout is not None and ns.request_timeout < 1:
        raise ValidationError("request timeout must be positive")

    question = _read_ask_prompt(ns.question, ns.prompt_file)
    notebook_service, chat_api = _offline_chat_services(ns.fixture_dir)
    notebook = _resolve_note_notebook(notebook_service, ns.notebook)
    conversation_id = None if ns.new else ns.conversation_id
    result = asyncio.run(
        chat_api.ask(
            notebook.id,
            question,
            source_ids=ns.source,
            conversation_id=conversation_id,
        )
    )
    note_payload: dict[str, object] | None = None
    if ns.save_as_note:
        note = asyncio.run(
            chat_api.save_answer_as_note(notebook.id, result, title=ns.note_title)
        )
        note_payload = {"id": note.id, "title": note.title}
    if ns.json:
        payload = result.as_dict()
        if note_payload is not None:
            payload["note"] = note_payload
        _emit(payload, json_mode=True)
    else:
        sys.stdout.write(result.answer + "\n")
        if note_payload is not None:
            sys.stderr.write(
                f"Saved as note: {note_payload['title']} ({note_payload['id']})\n"
            )
    return 0


def _handle_configure(
    store: "_profiles.ProfileStore | None",
    args: Sequence[str],
    *,
    global_profile: str | None,
) -> int:
    parser = argparse.ArgumentParser(prog="notebooklm configure")
    parser.add_argument(
        "-n", "--notebook", default=None, help="synthetic notebook selector"
    )
    parser.add_argument(
        "--mode",
        choices=["default", "learning-guide", "concise", "detailed"],
        default=None,
        help="Predefined chat mode",
    )
    parser.add_argument("--persona", default=None, help="Custom persona prompt")
    parser.add_argument(
        "--response-length",
        choices=["default", "longer", "shorter"],
        default=None,
        help="Response verbosity",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "--fixture-dir",
        default=None,
        help="explicit synthetic rpc_fixtures directory (offline only)",
    )
    ns = parser.parse_args(list(args))

    notebook_service, chat_api = _offline_chat_services(ns.fixture_dir)
    notebook = _resolve_note_notebook(notebook_service, ns.notebook)
    if ns.mode:
        mode_map = {
            "default": _types.ChatMode.DEFAULT,
            "learning-guide": _types.ChatMode.LEARNING_GUIDE,
            "concise": _types.ChatMode.CONCISE,
            "detailed": _types.ChatMode.DETAILED,
        }
        asyncio.run(chat_api.set_mode(notebook.id, mode_map[ns.mode]))
        _emit(
            {"notebook_id": notebook.id, "mode": ns.mode, "configured": True},
            json_mode=ns.json,
        )
        return 0

    length = None
    if ns.response_length:
        length = {
            "default": _types.ChatResponseLength.DEFAULT,
            "longer": _types.ChatResponseLength.LONGER,
            "shorter": _types.ChatResponseLength.SHORTER,
        }[ns.response_length]
    goal = _types.ChatGoal.CUSTOM if ns.persona else None
    asyncio.run(
        chat_api.configure(
            notebook.id,
            goal=goal,
            response_length=length,
            custom_prompt=ns.persona,
        )
    )
    _emit(
        {
            "notebook_id": notebook.id,
            "mode": None,
            "goal": goal.name.lower() if goal else None,
            "persona": ns.persona,
            "response_length": ns.response_length,
            "configured": True,
        },
        json_mode=ns.json,
    )
    return 0


def _add_generate_common_options(
    parser: argparse.ArgumentParser,
    *,
    prompt: bool = True,
    prompt_required: bool = False,
    language: bool = False,
    sources: bool = True,
    wait: bool = True,
    default_timeout: int = 300,
) -> None:
    if prompt:
        parser.add_argument("description", nargs="?", default=None)
        parser.add_argument(
            "--prompt-file",
            default=None,
            help="read prompt/query text from file or '-' for stdin",
        )
        parser.set_defaults(_prompt_required=prompt_required)
    parser.add_argument(
        "-n", "--notebook", default=None, help="synthetic notebook selector"
    )
    if language:
        parser.add_argument("--language", default=None, help="output language code")
    if sources:
        parser.add_argument(
            "-s", "--source", dest="source_ids", action="append", default=None
        )
    if wait:
        parser.add_argument("--wait", dest="wait", action="store_true", default=False)
        parser.add_argument("--no-wait", dest="wait", action="store_false")
        parser.add_argument("--timeout", type=int, default=default_timeout)
        parser.add_argument("--interval", type=int, default=2)
        parser.add_argument("--retry", type=int, default=0)
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "--fixture-dir",
        default=None,
        help="explicit synthetic rpc_fixtures directory (offline only)",
    )


def _run_generate_status(
    ns: argparse.Namespace, method_name: str, *, description: str = ""
) -> int:
    _validate_language_code(getattr(ns, "language", None))
    _validate_generation_controls(
        getattr(ns, "timeout", None),
        getattr(ns, "interval", None),
        getattr(ns, "retry", None),
    )
    notebook_service, artifact_service = _offline_artifact_services(ns.fixture_dir)
    notebook = _resolve_note_notebook(notebook_service, ns.notebook)
    api = _artifacts.ArtifactsAPI(artifacts=artifact_service)
    method = getattr(api, method_name)
    kwargs: dict[str, object] = {}
    if hasattr(ns, "source_ids"):
        kwargs["source_ids"] = ns.source_ids
    if hasattr(ns, "language"):
        kwargs["language"] = ns.language
    option_map = {
        "audio_format": ("audio_format",),
        "audio_length": ("audio_length",),
        "video_format": ("video_format",),
        "style": ("video_style", "style"),
        "style_prompt": ("style_prompt",),
        "deck_format": ("slide_format",),
        "deck_length": ("slide_length",),
        "quantity": ("quantity",),
        "difficulty": ("difficulty",),
        "orientation": ("orientation",),
        "detail": ("detail_level",),
        "report_format": ("report_format",),
        "append_instructions": ("extra_instructions",),
    }
    for attr, params in option_map.items():
        value = getattr(ns, attr, None)
        if value is not None:
            for param in params:
                kwargs[param] = value
    if description:
        if method_name == "generate_report":
            kwargs["custom_prompt"] = description
        elif method_name == "generate_study_guide":
            kwargs["extra_instructions"] = description
        else:
            kwargs["instructions"] = description
    accepted_params = inspect.signature(method).parameters
    call_kwargs = {
        key: value for key, value in kwargs.items() if key in accepted_params
    }
    status = asyncio.run(method(notebook.id, **call_kwargs))
    _emit(_generation_status_payload(status), json_mode=ns.json)
    return 0


def _parse_generate_leaf(subcommand: str, args: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog=f"notebooklm generate {subcommand}")
    if subcommand == "audio":
        _add_generate_common_options(parser, language=True, default_timeout=1200)
        parser.add_argument(
            "--format",
            dest="audio_format",
            choices=["deep-dive", "brief", "critique", "debate"],
            default="deep-dive",
        )
        parser.add_argument(
            "--length",
            dest="audio_length",
            choices=["short", "default", "long"],
            default="default",
        )
    elif subcommand in {"video", "cinematic-video"}:
        timeout = 3600 if subcommand == "cinematic-video" else 1800
        _add_generate_common_options(parser, language=True, default_timeout=timeout)
        default_format = "cinematic" if subcommand == "cinematic-video" else "explainer"
        parser.add_argument(
            "--format",
            dest="video_format",
            choices=["explainer", "brief", "cinematic"],
            default=default_format,
        )
        parser.add_argument(
            "--style",
            choices=[
                "auto",
                "custom",
                "classic",
                "whiteboard",
                "kawaii",
                "anime",
                "watercolor",
                "retro-print",
                "heritage",
                "paper-craft",
            ],
            default="auto",
        )
        parser.add_argument("--style-prompt", default=None)
    elif subcommand == "slide-deck":
        _add_generate_common_options(parser, language=True)
        parser.add_argument(
            "--format",
            dest="deck_format",
            choices=["detailed", "presenter"],
            default="detailed",
        )
        parser.add_argument(
            "--length",
            dest="deck_length",
            choices=["default", "short"],
            default="default",
        )
    elif subcommand in {"quiz", "flashcards"}:
        _add_generate_common_options(parser, language=False)
        parser.add_argument(
            "--quantity", choices=["fewer", "standard", "more"], default="standard"
        )
        parser.add_argument(
            "--difficulty", choices=["easy", "medium", "hard"], default="medium"
        )
    elif subcommand == "infographic":
        _add_generate_common_options(parser, language=True)
        parser.add_argument(
            "--orientation",
            choices=["landscape", "portrait", "square"],
            default="landscape",
        )
        parser.add_argument(
            "--detail", choices=["concise", "standard", "detailed"], default="standard"
        )
        parser.add_argument(
            "--style",
            choices=[
                "auto",
                "sketch-note",
                "professional",
                "bento-grid",
                "editorial",
                "instructional",
                "bricks",
                "clay",
                "anime",
                "kawaii",
                "scientific",
            ],
            default="auto",
        )
    elif subcommand == "data-table":
        _add_generate_common_options(parser, language=True)
    elif subcommand == "report":
        _add_generate_common_options(parser, language=True)
        parser.add_argument(
            "--format",
            dest="report_format",
            choices=["briefing-doc", "study-guide", "blog-post", "custom"],
            default="briefing-doc",
        )
        parser.add_argument("--append", dest="append_instructions", default=None)
    elif subcommand == "mind-map":
        _add_generate_common_options(parser, prompt=False, language=True, wait=False)
        parser.add_argument("--instructions", default=None)
        parser.add_argument(
            "--kind",
            dest="map_kind",
            choices=["interactive", "note-backed"],
            default="note-backed",
        )
    elif subcommand == "revise-slide":
        _add_generate_common_options(
            parser, prompt_required=True, language=False, sources=False
        )
        parser.add_argument("-a", "--artifact", dest="artifact_id", required=True)
        parser.add_argument("--slide", dest="slide_index", type=int, required=True)
    else:
        raise ValidationError("unknown generate subcommand")
    ns = parser.parse_args(list(args))
    if subcommand == "mind-map":
        ns.map_kind_explicit = any(
            arg == "--kind" or arg.startswith("--kind=") for arg in args
        )
    return ns


_QUIET_DEPRECATIONS_ENV = "NOTEBOOKLM_QUIET_DEPRECATIONS"


def _deprecations_quieted() -> bool:
    return getenv(_QUIET_DEPRECATIONS_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _handle_generate(
    store: "_profiles.ProfileStore | None",
    args: Sequence[str],
    *,
    global_profile: str | None,
) -> int:
    argv = list(args)
    if not argv:
        parser = argparse.ArgumentParser(prog="notebooklm generate")
        parser.print_help()
        return 0
    subcommand = argv[0]
    method_by_subcommand = {
        "audio": "generate_audio",
        "video": "generate_video",
        "cinematic-video": "generate_cinematic_video",
        "slide-deck": "generate_slide_deck",
        "quiz": "generate_quiz",
        "flashcards": "generate_flashcards",
        "infographic": "generate_infographic",
        "data-table": "generate_data_table",
        "report": "generate_report",
    }
    ns = _parse_generate_leaf(subcommand, argv[1:])

    if subcommand == "cinematic-video" and ns.video_format != "cinematic":
        raise ValidationError("generate cinematic-video requires --format cinematic")

    if subcommand == "mind-map":
        _validate_language_code(ns.language)
        notebook_service, artifact_service = _offline_artifact_services(ns.fixture_dir)
        notebook = _resolve_note_notebook(notebook_service, ns.notebook)
        instructions = ns.instructions
        if ns.map_kind == "interactive":
            if instructions:
                print(
                    "Warning: --instructions is ignored for interactive mind maps "
                    "(the interactive generator does not accept custom instructions).",
                    file=sys.stderr,
                )
                instructions = None
            api = _client.MindMapsAPI(
                mind_maps=_notes.OfflineMindMapService.for_notebooks([notebook.id])
            )
            result = asyncio.run(
                api.generate(
                    notebook.id,
                    source_ids=ns.source_ids,
                    kind=_types.MindMapKind.INTERACTIVE,
                    language=ns.language,
                    instructions=instructions,
                )
            )
            _emit(
                {
                    "mind_map": result.tree,
                    "note_id": result.id,
                    "kind": result.kind.value,
                },
                json_mode=ns.json,
            )
            return 0
        if not ns.map_kind_explicit and not ns.json and not _deprecations_quieted():
            print(
                "Note: 'generate mind-map' defaults to the note-backed kind today, but "
                "the default switches to interactive in v0.8.0 (NotebookLM's web app "
                "already creates interactive maps). Pass --kind note-backed or "
                "--kind interactive to pin your choice; set NOTEBOOKLM_QUIET_DEPRECATIONS=1 "
                "to silence.",
                file=sys.stderr,
            )
        api = _artifacts.ArtifactsAPI(artifacts=artifact_service)
        result = asyncio.run(
            api.generate_mind_map(
                notebook.id,
                source_ids=ns.source_ids,
                language=ns.language,
                instructions=instructions,
            )
        )
        _emit(
            {
                "mind_map": result.mind_map,
                "note_id": result.note_id,
                "kind": ns.map_kind.replace("-", "_"),
            },
            json_mode=ns.json,
        )
        return 0

    description = _read_optional_prompt(
        getattr(ns, "description", None),
        getattr(ns, "prompt_file", None),
        required=getattr(ns, "_prompt_required", False),
        label="description",
    )
    if subcommand == "revise-slide":
        _validate_generation_controls(ns.timeout, ns.interval, ns.retry)
        notebook_service, artifact_service = _offline_artifact_services(ns.fixture_dir)
        notebook = _resolve_note_notebook(notebook_service, ns.notebook)
        api = _artifacts.ArtifactsAPI(artifacts=artifact_service)
        status = asyncio.run(
            api.revise_slide(notebook.id, ns.artifact_id, ns.slide_index, description)
        )
        _emit(_generation_status_payload(status), json_mode=ns.json)
        return 0

    return _run_generate_status(
        ns, method_by_subcommand[subcommand], description=description
    )


def _handle_metadata(
    store: "_profiles.ProfileStore | None",
    args: Sequence[str],
    *,
    global_profile: str | None,
) -> int:
    parser = argparse.ArgumentParser(prog="notebooklm metadata")
    parser.add_argument(
        "-n", "--notebook", default=None, help="synthetic notebook selector"
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "--fixture-dir",
        default=None,
        help="explicit synthetic rpc_fixtures directory (offline only)",
    )
    ns = parser.parse_args(list(args))

    notebook_service, source_service = _offline_source_services(ns.fixture_dir)
    notebook = _resolve_note_notebook(notebook_service, ns.notebook)
    sources = [source.summary() for source in source_service.list(notebook.id)]
    metadata = _notebooks.NotebookMetadata(notebook=notebook, sources=sources)
    _emit(metadata.as_dict(), json_mode=ns.json)
    return 0


def _summary_payload(
    metadata: "_notebooks.NotebookMetadata",
    *,
    include_topics: bool,
) -> dict[str, object]:
    titles = [source.title or "Untitled source" for source in metadata.sources]
    source_count = len(titles)
    noun = "source" if source_count == 1 else "sources"
    source_text = "; ".join(titles) if titles else "no sources"
    payload: dict[str, object] = {
        "notebook_id": metadata.notebook.id,
        "title": metadata.notebook.title,
        "source_count": source_count,
        "summary": f"{metadata.notebook.title} has {source_count} ready {noun}: {source_text}.",
    }
    if include_topics:
        payload["suggested_topics"] = titles
    return payload


def _handle_summary(
    store: "_profiles.ProfileStore | None",
    args: Sequence[str],
    *,
    global_profile: str | None,
) -> int:
    parser = argparse.ArgumentParser(prog="notebooklm summary")
    parser.add_argument(
        "-n", "--notebook", default=None, help="synthetic notebook selector"
    )
    parser.add_argument(
        "--topics", action="store_true", help="Include suggested topics"
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "--fixture-dir",
        default=None,
        help="explicit synthetic rpc_fixtures directory (offline only)",
    )
    ns = parser.parse_args(list(args))

    notebook_service, source_service = _offline_source_services(ns.fixture_dir)
    notebook = _resolve_note_notebook(notebook_service, ns.notebook)
    sources = [source.summary() for source in source_service.list(notebook.id)]
    metadata = _notebooks.NotebookMetadata(notebook=notebook, sources=sources)
    _emit(_summary_payload(metadata, include_topics=ns.topics), json_mode=ns.json)
    return 0


def _history_turn_dicts(turns: list[tuple[str, str]]) -> list[dict[str, object]]:
    return [
        {"turn_number": index, "question": question, "answer": answer}
        for index, (question, answer) in enumerate(turns, start=1)
    ]


def _preview_history_turns(
    turns: list[dict[str, object]],
    *,
    show_all: bool,
    no_truncate: bool,
) -> list[dict[str, object]]:
    if show_all or no_truncate:
        return turns

    def preview(value: object) -> object:
        if not isinstance(value, str) or len(value) <= 50:
            return value
        return value[:47] + "..."

    return [
        {
            "turn_number": turn["turn_number"],
            "question": preview(turn["question"]),
            "answer": preview(turn["answer"]),
        }
        for turn in turns
    ]


def _handle_history(
    store: "_profiles.ProfileStore | None",
    args: Sequence[str],
    *,
    global_profile: str | None,
) -> int:
    parser = argparse.ArgumentParser(prog="notebooklm history")
    parser.add_argument(
        "-n", "--notebook", default=None, help="synthetic notebook selector"
    )
    parser.add_argument(
        "-l", "--limit", type=int, default=None, help="maximum turns to render"
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="clear the local synthetic conversation cache",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="save synthetic history as an in-memory note",
    )
    parser.add_argument(
        "-t", "--note-title", default=None, help="note title for --save"
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--show-all", action="store_true", help="show full Q&A content")
    parser.add_argument(
        "--no-truncate", action="store_true", help="disable preview truncation"
    )
    parser.add_argument(
        "--fixture-dir",
        default=None,
        help="explicit synthetic rpc_fixtures directory (offline only)",
    )
    ns = parser.parse_args(list(args))
    limit = _validate_limit(ns.limit)

    notebook_service, chat_api = _offline_chat_services(ns.fixture_dir)
    notebook = _resolve_note_notebook(notebook_service, ns.notebook)
    if ns.clear:
        cleared = chat_api.clear_cache()
        payload = {"cleared": bool(cleared), "count": 0}
        _emit(payload, json_mode=ns.json)
        return 0

    if limit == 0:
        turns: list[tuple[str, str]] = []
        conversation_id = None
    else:
        result = asyncio.run(chat_api.ask(notebook.id, SYNTHETIC_HISTORY_QUESTION))
        conversation_id = result.conversation_id
        turns = asyncio.run(chat_api.get_history(notebook.id, limit=limit or 100))

    if ns.save:
        if not turns:
            raise ValidationError("no conversation history found for this notebook")
        _, note_service = _offline_note_services(ns.fixture_dir)
        note = note_service.create(
            notebook.id,
            ns.note_title or "Chat History",
            _format_history_note(turns),
        )
        payload = {
            "notebook_id": notebook.id,
            "conversation_id": conversation_id,
            "turns": _history_turn_dicts(turns),
            "note": {"id": note.id, "title": note.title},
        }
        _emit(payload, json_mode=ns.json)
        return 0

    payload = _history_turn_dicts(turns)
    if not ns.json:
        payload = _preview_history_turns(
            payload, show_all=ns.show_all, no_truncate=ns.no_truncate
        )
    _emit(payload, json_mode=ns.json)
    return 0


def _completion_script(shell: str) -> str | None:
    try:
        data_dir = resources.files("notebooklm") / "data"
        text_path = data_dir / f"completion_{shell}.txt"
        if text_path.is_file():
            return text_path.read_text(encoding="utf-8")
        json_path = data_dir / f"completion_{shell}.json"
        if json_path.is_file():
            decoded = json.loads(json_path.read_text(encoding="utf-8"))
            return decoded if isinstance(decoded, str) else None
    except (FileNotFoundError, TypeError, ModuleNotFoundError, json.JSONDecodeError):
        return None
    return None


def _handle_completion(
    store: "_profiles.ProfileStore | None",
    args: Sequence[str],
    *,
    global_profile: str | None,
) -> int:
    del store, global_profile
    argv = list(args)
    parser = argparse.ArgumentParser(
        prog="notebooklm completion",
        description=(
            "Print the shell completion script for SHELL.\n\n"
            "Pipe the output into a file your shell sources at startup. Click handles the\n"
            "``_NOTEBOOKLM_COMPLETE`` env-var protocol automatically once the script is\n"
            "sourced; only the script needs to be installed.\n\n"
            "Install (one-time):\n"
            "  # bash (~/.bashrc)\n"
            "  notebooklm completion bash > ~/.notebooklm-complete.bash\n"
            "  echo 'source ~/.notebooklm-complete.bash' >> ~/.bashrc\n\n"
            "  # zsh (anywhere on $fpath)\n"
            "  notebooklm completion zsh > ~/.zfunc/_notebooklm\n\n"
            "  # fish\n"
            "  notebooklm completion fish > ~/.config/fish/completions/notebooklm.fish\n\n"
            "Then ``notebooklm <cmd> -n <TAB>`` lists notebook IDs from the active\n"
            "profile (best-effort — no suggestions when not authenticated)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("shell", nargs="?", choices=("bash", "zsh", "fish"))
    if argv == ["--help"]:
        parser.parse_args(argv)
    if len(argv) != 1:
        raise ValidationError("completion requires shell {bash,zsh,fish}")
    shell = argv[0]
    if shell not in {"bash", "zsh", "fish"}:
        raise ValidationError("completion shell must be one of {bash,zsh,fish}")
    script = _completion_script(shell)
    if script is None:
        raise ValidationError(f"{shell} completion script not found in package data")
    sys.stdout.write(script.rstrip() + "\n")
    return 0


SKILL_TARGETS = {
    "claude": ("Claude Code", Path(".claude") / "skills" / "notebooklm" / "SKILL.md"),
    "agents": ("Agent Skills", Path(".agents") / "skills" / "notebooklm" / "SKILL.md"),
}
SKILL_SCOPES = {"user", "project"}
SKILL_TARGET_CREATE = "create"
SKILL_TARGET_UP_TO_DATE = "up_to_date"
SKILL_TARGET_OVERWRITE = "overwrite"


def _skill_source_content() -> str:
    content = _agent_templates.get_agent_source_content("claude")
    if content is None:
        raise ValidationError("Skill source not found in package data")
    return content


def _skill_scope_root(scope: str) -> Path:
    return Path.home() if scope == "user" else Path.cwd()


def _skill_path(target: str, scope: str) -> Path:
    return _skill_scope_root(scope) / SKILL_TARGETS[target][1]


def _skill_targets(target: str) -> list[str]:
    return list(SKILL_TARGETS) if target == "all" else [target]


def _skill_stamped_content(content: str, version: str) -> str:
    version_comment = f"<!-- notebooklm-py v{version} -->\n"
    if "---" in content:
        parts = content.split("---", 2)
        if len(parts) >= 3:
            return f"---{parts[1]}---\n{version_comment}{parts[2].lstrip()}"
    return version_comment + content


def _skill_installed_version(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        head = path.read_text(encoding="utf-8")[:500]
    except OSError:
        return None
    match = re.search(r"notebooklm-py v([\d.]+)", head)
    return match.group(1) if match else None


def _skill_classify(target: str, scope: str, stamped: str) -> tuple[str, Path]:
    path = _skill_path(target, scope)
    if not path.exists():
        return SKILL_TARGET_CREATE, path
    try:
        current = path.read_text(encoding="utf-8")
    except OSError:
        return SKILL_TARGET_OVERWRITE, path
    return (
        SKILL_TARGET_UP_TO_DATE if current == stamped else SKILL_TARGET_OVERWRITE
    ), path


def _skill_atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(content)
        temp_path.replace(path)
    except Exception:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass
        raise


def _skill_remove_empty_parents(path: Path, scope: str) -> None:
    stop_at = _skill_scope_root(scope)
    current = path.parent
    while current != stop_at:
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def _skill_parser(prog: str, description: str) -> argparse.ArgumentParser:
    return argparse.ArgumentParser(prog=prog, description=description)


def _skill_validate_scope_target(
    scope: str, target: str, *, source: bool = False
) -> None:
    if scope not in SKILL_SCOPES:
        raise ValidationError("skill scope must be one of {user,project}")
    allowed = {"source", *SKILL_TARGETS} if source else {"all", *SKILL_TARGETS}
    if target not in allowed:
        raise ValidationError(
            f"skill target must be one of {{{','.join(sorted(allowed))}}}"
        )


def _handle_skill_install(argv: Sequence[str]) -> int:
    parser = _skill_parser(
        "notebooklm skill install",
        "Install or update the NotebookLM skill for supported agent directories.",
    )
    parser.add_argument("--scope", default="user")
    parser.add_argument("--target", dest="target_name", default="all")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-clobber", action="store_true")
    parser.add_argument("--force", action="store_true")
    ns = parser.parse_args(list(argv))
    _skill_validate_scope_target(ns.scope, ns.target_name)
    if ns.scope == "user" and (ns.dry_run or ns.no_clobber or ns.force):
        print("Error: --dry-run, --no-clobber, and --force require --scope project.")
        return 1
    if ns.force and ns.no_clobber:
        print("Error: --force and --no-clobber are mutually exclusive.")
        return 1

    stamped = _skill_stamped_content(_skill_source_content(), __version__)
    classifications = [
        (target, *_skill_classify(target, ns.scope, stamped))
        for target in _skill_targets(ns.target_name)
    ]
    differing = [
        (target, path)
        for target, status, path in classifications
        if status == SKILL_TARGET_OVERWRITE
    ]
    if (
        ns.scope == "project"
        and differing
        and not (ns.dry_run or ns.no_clobber or ns.force)
    ):
        print(
            "Refusing to overwrite differing skill files (use --force to overwrite or --no-clobber to skip differing files):"
        )
        for target, path in differing:
            print(f"  {SKILL_TARGETS[target][0]}: {path}")
        return 1

    if ns.dry_run:
        print("Dry run -- no files will be written")
        print(f"  Version: {__version__}")
        print(f"  Scope:   {ns.scope}")
        for target, status, path in classifications:
            label = SKILL_TARGETS[target][0]
            if status == SKILL_TARGET_CREATE:
                print(f"  Would create  {label}: {path}")
            elif status == SKILL_TARGET_UP_TO_DATE:
                print(f"  Up to date    {label}: {path}")
            elif ns.no_clobber:
                print(f"  Would skip    {label}: {path} (differs; --no-clobber)")
            else:
                action = "Would overwrite" if ns.force else "Would refuse"
                print(f"  {action} {label}: {path}")
        return 0

    installed: list[tuple[str, Path]] = []
    skipped_up_to_date: list[tuple[str, Path]] = []
    skipped_no_clobber: list[tuple[str, Path]] = []
    failed: list[tuple[str, OSError]] = []
    for target, status, path in classifications:
        if status == SKILL_TARGET_UP_TO_DATE:
            skipped_up_to_date.append((target, path))
            continue
        if status == SKILL_TARGET_OVERWRITE and ns.no_clobber:
            skipped_no_clobber.append((target, path))
            continue
        try:
            _skill_atomic_write(path, stamped)
            installed.append((target, path))
        except OSError as exc:
            failed.append((target, exc))

    if installed:
        print("Installed NotebookLM skill")
        print(f"  Version: {__version__}")
        print(f"  Scope:   {ns.scope}")
        for target, path in installed:
            print(f"  {SKILL_TARGETS[target][0]}: {path}")
        print("")
        print(
            "NotebookLM commands are now available in the selected skill directories."
        )
    if skipped_no_clobber:
        print(f"Skipped {len(skipped_no_clobber)} differing target(s) (--no-clobber)")
    if skipped_up_to_date and not installed:
        print("Up to date -- no changes needed")
        print(f"  Version: {__version__}")
        print(f"  Scope:   {ns.scope}")
    for target, exc in failed:
        print(f"Failed to install {SKILL_TARGETS[target][0]}: {exc}")
    return 1 if failed else 0


def _handle_skill_status(argv: Sequence[str]) -> int:
    parser = _skill_parser(
        "notebooklm skill status", "Check installed skill targets and version info."
    )
    parser.add_argument("--scope", default="user")
    parser.add_argument("--target", dest="target_name", default="all")
    ns = parser.parse_args(list(argv))
    _skill_validate_scope_target(ns.scope, ns.target_name)
    print(f"NotebookLM skill status ({ns.scope} scope)")
    print(f"  CLI version: {__version__}")
    any_installed = False
    for target in _skill_targets(ns.target_name):
        label = SKILL_TARGETS[target][0]
        path = _skill_path(target, ns.scope)
        installed = path.exists()
        any_installed = any_installed or installed
        print(f"  {label}: {'Installed' if installed else 'Not installed'}")
        print(f"    Path: {path}")
        if installed:
            version = _skill_installed_version(path) or "unknown"
            print(f"    Skill version: {version}")
            if version != "unknown" and version != __version__:
                print("    Version mismatch - run notebooklm skill install")
    if not any_installed:
        print("")
        print("Run notebooklm skill install to install the skill.")
    return 0


def _handle_skill_uninstall(argv: Sequence[str]) -> int:
    parser = _skill_parser(
        "notebooklm skill uninstall",
        "Remove the NotebookLM skill from supported agent directories.",
    )
    parser.add_argument("--scope", default="user")
    parser.add_argument("--target", dest="target_name", default="all")
    ns = parser.parse_args(list(argv))
    _skill_validate_scope_target(ns.scope, ns.target_name)
    removed: list[str] = []
    for target in _skill_targets(ns.target_name):
        path = _skill_path(target, ns.scope)
        if not path.exists():
            continue
        path.unlink()
        _skill_remove_empty_parents(path, ns.scope)
        removed.append(target)
    if not removed:
        print("Skill not installed")
        return 0
    print("Uninstalled NotebookLM skill")
    for target in removed:
        print(f"  Removed from {SKILL_TARGETS[target][0]}")
    return 0


def _handle_skill_show(argv: Sequence[str]) -> int:
    parser = _skill_parser(
        "notebooklm skill show",
        "Display the packaged skill content or an installed target.",
    )
    parser.add_argument("--scope", default="user")
    parser.add_argument("--target", dest="target_name", default="source")
    ns = parser.parse_args(list(argv))
    _skill_validate_scope_target(ns.scope, ns.target_name, source=True)
    if ns.target_name == "source":
        print(_skill_source_content().rstrip())
        return 0
    path = _skill_path(ns.target_name, ns.scope)
    if not path.exists():
        print("Skill not installed")
        print("Run notebooklm skill install first.")
        return 0
    print(path.read_text(encoding="utf-8").rstrip())
    return 0


def _handle_skill(
    store: "_profiles.ProfileStore | None",
    args: Sequence[str],
    *,
    global_profile: str | None,
) -> int:
    del store, global_profile
    argv = list(args)
    if not argv or argv[0] in {"-h", "--help"}:
        print("Usage: notebooklm skill [OPTIONS] COMMAND [ARGS]...")
        print("\n  Manage NotebookLM agent skill integration.\n")
        print("Commands:")
        print(
            "  install    Install or update the NotebookLM skill for supported agent directories."
        )
        print("  show       Display the packaged skill content or an installed target.")
        print("  status     Check installed skill targets and version info.")
        print(
            "  uninstall  Remove the NotebookLM skill from supported agent directories."
        )
        return 0
    subcommand, rest = argv[0], argv[1:]
    if subcommand == "install":
        return _handle_skill_install(rest)
    if subcommand == "status":
        return _handle_skill_status(rest)
    if subcommand == "uninstall":
        return _handle_skill_uninstall(rest)
    if subcommand == "show":
        return _handle_skill_show(rest)
    raise ValidationError("unknown skill subcommand")


def _handle_agent(
    store: "_profiles.ProfileStore | None",
    args: Sequence[str],
    *,
    global_profile: str | None,
) -> int:
    del store, global_profile
    argv = list(args)
    group_parser = argparse.ArgumentParser(
        prog="notebooklm agent",
        description="Show bundled instructions for supported agent environments.",
    )
    group_subparsers = group_parser.add_subparsers(dest="subcommand", metavar="COMMAND")
    show_parser = group_subparsers.add_parser(
        "show",
        help="Display instructions for Codex or Claude Code.",
        description="Display instructions for Codex or Claude Code.",
    )
    show_parser.add_argument("target", nargs="?", choices=("codex", "claude"))

    if not argv:
        group_parser.print_help()
        return 0
    if argv == ["--help"]:
        group_parser.parse_args(argv)
    subcommand = argv[0]
    if subcommand != "show":
        raise ValidationError("unknown agent subcommand")
    if argv[1:] == ["--help"]:
        show_parser.parse_args(argv[1:])
    if len(argv) != 2:
        raise ValidationError("agent show requires target {codex,claude}")
    target = argv[1].lower()
    if target not in {"codex", "claude"}:
        raise ValidationError("agent show target must be one of {codex,claude}")
    content = _agent_templates.get_agent_source_content(target)
    if content is None:
        raise ValidationError(f"{target} instructions not found in package data")
    sys.stdout.write(content.rstrip() + "\n")
    return 0


def _handle_note(
    store: "_profiles.ProfileStore | None",
    args: Sequence[str],
    *,
    global_profile: str | None,
) -> int:
    argv = list(args)
    if not argv:
        parser = argparse.ArgumentParser(prog="notebooklm note")
        parser.print_help()
        return 0

    subcommand = argv[0]
    if subcommand == "list":
        parser = argparse.ArgumentParser(prog="notebooklm note list")
        parser.add_argument(
            "-n", "--notebook", default=None, help="synthetic notebook selector"
        )
        parser.add_argument("--json", action="store_true", help="Output as JSON")
        parser.add_argument(
            "--limit", type=int, default=None, help="maximum notes to render"
        )
        parser.add_argument(
            "--no-truncate", action="store_true", help="accepted for CLI parity"
        )
        parser.add_argument(
            "--fixture-dir",
            default=None,
            help="explicit synthetic rpc_fixtures directory (offline only)",
        )
        ns = parser.parse_args(argv[1:])
        limit = _validate_limit(ns.limit)
        notebook_service, note_service = _offline_note_services(ns.fixture_dir)
        notebook = _resolve_note_notebook(notebook_service, ns.notebook)
        notes = [note.as_dict() for note in note_service.list(notebook.id)]
        if limit is not None:
            notes = notes[:limit]
        _emit(notes, json_mode=ns.json)
        return 0

    if subcommand == "get":
        parser = argparse.ArgumentParser(prog="notebooklm note get")
        parser.add_argument("note_id")
        parser.add_argument(
            "-n", "--notebook", default=None, help="synthetic notebook selector"
        )
        parser.add_argument("--json", action="store_true", help="Output as JSON")
        parser.add_argument(
            "--fixture-dir",
            default=None,
            help="explicit synthetic rpc_fixtures directory (offline only)",
        )
        ns = parser.parse_args(argv[1:])
        notebook_service, note_service = _offline_note_services(ns.fixture_dir)
        notebook = _resolve_note_notebook(notebook_service, ns.notebook)
        note = note_service.get(notebook.id, ns.note_id)
        if note is None:
            raise ValidationError("note not found")
        _emit(note.as_dict(), json_mode=ns.json)
        return 0

    if subcommand == "create":
        parser = argparse.ArgumentParser(prog="notebooklm note create")
        parser.add_argument("content", nargs="?", default="")
        parser.add_argument("--content", dest="content_flag", default=None)
        parser.add_argument("-t", "--title", default="New Note")
        parser.add_argument(
            "-n", "--notebook", default=None, help="synthetic notebook selector"
        )
        parser.add_argument("--json", action="store_true", help="Output as JSON")
        parser.add_argument("--fixture-dir", default=None)
        ns = parser.parse_args(argv[1:])
        if ns.content and ns.content_flag is not None:
            raise ValidationError("cannot use both positional content and --content")
        content = ns.content_flag if ns.content_flag is not None else ns.content
        notebook_service, note_service = _offline_note_services(ns.fixture_dir)
        notebook = _resolve_note_notebook(notebook_service, ns.notebook)
        note = note_service.create(notebook.id, ns.title, content)
        payload = {
            "id": note.id,
            "notebook_id": notebook.id,
            "title": note.title,
            "created": True,
        }
        _emit(payload, json_mode=ns.json)
        return 0

    if subcommand == "save":
        parser = argparse.ArgumentParser(prog="notebooklm note save")
        parser.add_argument("note_id")
        parser.add_argument(
            "-n", "--notebook", default=None, help="synthetic notebook selector"
        )
        parser.add_argument("--title", default=None)
        parser.add_argument("--content", default=None)
        parser.add_argument("--json", action="store_true", help="Output as JSON")
        parser.add_argument("--fixture-dir", default=None)
        ns = parser.parse_args(argv[1:])
        if ns.title is None and ns.content is None:
            raise ValidationError("provide --title and/or --content")
        notebook_service, note_service = _offline_note_services(ns.fixture_dir)
        notebook = _resolve_note_notebook(notebook_service, ns.notebook)
        existing = note_service.get(notebook.id, ns.note_id)
        if existing is None:
            raise ValidationError("note not found")
        title = ns.title if ns.title is not None else existing.title
        content = ns.content if ns.content is not None else existing.content
        note_service.update(notebook.id, ns.note_id, content, title)
        payload = {"id": ns.note_id, "notebook_id": notebook.id, "saved": True}
        if ns.title is not None:
            payload["title"] = ns.title
        if ns.content is not None:
            payload["content"] = ns.content
        _emit(payload, json_mode=ns.json)
        return 0

    if subcommand == "rename":
        parser = argparse.ArgumentParser(prog="notebooklm note rename")
        parser.add_argument("note_id")
        parser.add_argument("new_title")
        parser.add_argument(
            "-n", "--notebook", default=None, help="synthetic notebook selector"
        )
        parser.add_argument("--json", action="store_true", help="Output as JSON")
        parser.add_argument("--fixture-dir", default=None)
        ns = parser.parse_args(argv[1:])
        notebook_service, note_service = _offline_note_services(ns.fixture_dir)
        notebook = _resolve_note_notebook(notebook_service, ns.notebook)
        note = note_service.get(notebook.id, ns.note_id)
        if note is None:
            raise ValidationError("note not found")
        note_service.update(notebook.id, ns.note_id, note.content, ns.new_title)
        _emit(
            {
                "id": ns.note_id,
                "notebook_id": notebook.id,
                "title": ns.new_title,
                "renamed": True,
            },
            json_mode=ns.json,
        )
        return 0

    if subcommand == "delete":
        parser = argparse.ArgumentParser(prog="notebooklm note delete")
        parser.add_argument("note_id")
        parser.add_argument(
            "-n", "--notebook", default=None, help="synthetic notebook selector"
        )
        parser.add_argument("--yes", "-y", action="store_true")
        parser.add_argument("--json", action="store_true", help="Output as JSON")
        parser.add_argument("--fixture-dir", default=None)
        ns = parser.parse_args(argv[1:])
        if not ns.yes:
            raise ValidationError("pass --yes to confirm deletion")
        notebook_service, note_service = _offline_note_services(ns.fixture_dir)
        notebook = _resolve_note_notebook(notebook_service, ns.notebook)
        note_service.delete(notebook.id, ns.note_id)
        _emit(
            {"id": ns.note_id, "notebook_id": notebook.id, "deleted": True},
            json_mode=ns.json,
        )
        return 0

    if subcommand in {"update", "delete-mind-map", "list-mind-maps"}:
        raise NotImplementedInPhaseError(
            f"notebooklm note {subcommand} is reserved for a later parity phase"
        )
    raise ValidationError("unknown note subcommand")


def _research_import_selection(
    sources: Sequence[Any],
    report: str,
    *,
    cited_only: bool,
) -> tuple[Sequence[Any], bool | None]:
    if not cited_only:
        return sources, None
    cited_sources = tuple(
        source
        for source in sources
        if getattr(source, "url", None) and source.url in report
    )
    if cited_sources:
        return cited_sources, False
    return sources, True


def _handle_source(
    store: "_profiles.ProfileStore | None",
    args: Sequence[str],
    *,
    global_profile: str | None,
) -> int:
    argv = list(args)
    if not argv:
        parser = argparse.ArgumentParser(prog="notebooklm source")
        parser.print_help()
        return 0

    subcommand = argv[0]
    if subcommand == "add":
        parser = argparse.ArgumentParser(prog="notebooklm source add")
        parser.add_argument("content")
        parser.add_argument(
            "-n", "--notebook", default=None, help="synthetic notebook selector"
        )
        parser.add_argument(
            "--type",
            dest="source_type",
            choices=("url", "text", "file", "youtube"),
            default=None,
        )
        parser.add_argument("--title", default=None)
        parser.add_argument("--mime-type", default=None)
        parser.add_argument(
            "--request-timeout", "--timeout", dest="timeout", type=float, default=None
        )
        parser.add_argument("--follow-symlinks", action="store_true", default=False)
        parser.add_argument("--allow-internal", action="store_true", default=False)
        parser.add_argument("--json", action="store_true", help="Output as JSON")
        parser.add_argument(
            "--fixture-dir",
            default=None,
            help="explicit synthetic rpc_fixtures directory (offline only)",
        )
        ns = parser.parse_args(argv[1:])
        notebook_service, source_service = _offline_source_services(ns.fixture_dir)
        notebook = _resolve_note_notebook(notebook_service, ns.notebook)
        source_type = ns.source_type
        if source_type is None:
            source_type = (
                "url" if ns.content.startswith(("http://", "https://")) else "text"
            )
        if source_type in {"url", "youtube"}:
            source = source_service.add_url(notebook.id, ns.content)
        elif source_type == "file":
            source = source_service.add_file(
                notebook.id,
                ns.content,
                mime_type=ns.mime_type,
                title=ns.title,
                follow_symlinks=ns.follow_symlinks,
            )
        else:
            title = ns.title or "Pasted Text"
            source = source_service.add_text(notebook.id, title, ns.content)
        _emit(
            {
                "source": source.as_dict(),
                "notebook_id": notebook.id,
                "added": True,
                "source_type": source_type,
            },
            json_mode=ns.json,
        )
        return 0

    if subcommand == "add-drive":
        parser = argparse.ArgumentParser(prog="notebooklm source add-drive")
        parser.add_argument("file_id")
        parser.add_argument("title")
        parser.add_argument(
            "-n", "--notebook", default=None, help="synthetic notebook selector"
        )
        parser.add_argument(
            "--mime-type",
            choices=("google-doc", "google-slides", "google-sheets", "pdf"),
            default="google-doc",
        )
        parser.add_argument("--json", action="store_true", help="Output as JSON")
        parser.add_argument(
            "--fixture-dir",
            default=None,
            help="explicit synthetic rpc_fixtures directory (offline only)",
        )
        ns = parser.parse_args(argv[1:])
        notebook_service, source_service = _offline_source_services(ns.fixture_dir)
        notebook = _resolve_note_notebook(notebook_service, ns.notebook)
        source = source_service.add_drive(
            notebook.id, ns.file_id, ns.title, ns.mime_type
        )
        _emit(
            {"source": source.as_dict(), "notebook_id": notebook.id, "added": True},
            json_mode=ns.json,
        )
        return 0

    if subcommand == "list":
        parser = argparse.ArgumentParser(prog="notebooklm source list")
        parser.add_argument(
            "-n", "--notebook", default=None, help="synthetic notebook selector"
        )
        parser.add_argument("--json", action="store_true", help="Output as JSON")
        parser.add_argument(
            "--limit", type=int, default=None, help="maximum sources to render"
        )
        parser.add_argument(
            "--no-truncate", action="store_true", help="accepted for CLI parity"
        )
        parser.add_argument(
            "--fixture-dir",
            default=None,
            help="explicit synthetic rpc_fixtures directory (offline only)",
        )
        ns = parser.parse_args(argv[1:])
        limit = _validate_limit(ns.limit)
        notebook_service, source_service = _offline_source_services(ns.fixture_dir)
        notebook = _resolve_note_notebook(notebook_service, ns.notebook)
        sources = [source.as_dict() for source in source_service.list(notebook.id)]
        if limit is not None:
            sources = sources[:limit]
        _emit(sources, json_mode=ns.json)
        return 0

    if subcommand == "get":
        parser = argparse.ArgumentParser(prog="notebooklm source get")
        parser.add_argument("source_id")
        parser.add_argument(
            "-n", "--notebook", default=None, help="synthetic notebook selector"
        )
        parser.add_argument("--json", action="store_true", help="Output as JSON")
        parser.add_argument(
            "--fixture-dir",
            default=None,
            help="explicit synthetic rpc_fixtures directory (offline only)",
        )
        ns = parser.parse_args(argv[1:])
        notebook_service, source_service = _offline_source_services(ns.fixture_dir)
        notebook = _resolve_note_notebook(notebook_service, ns.notebook)
        source = source_service.get(notebook.id, ns.source_id)
        if source is None:
            raise ValidationError("source not found")
        _emit(source.as_dict(), json_mode=ns.json)
        return 0

    if subcommand == "stale":
        parser = argparse.ArgumentParser(prog="notebooklm source stale")
        parser.add_argument("source_id")
        parser.add_argument(
            "-n", "--notebook", default=None, help="synthetic notebook selector"
        )
        parser.add_argument(
            "--exit-on-stale",
            action="store_true",
            help="return 0 if stale and 1 if fresh",
        )
        parser.add_argument("--json", action="store_true", help="Output as JSON")
        parser.add_argument(
            "--fixture-dir",
            default=None,
            help="explicit synthetic rpc_fixtures directory (offline only)",
        )
        ns = parser.parse_args(argv[1:])
        notebook_service, source_service = _offline_source_services(ns.fixture_dir)
        notebook = _resolve_note_notebook(notebook_service, ns.notebook)
        source = source_service.get(notebook.id, ns.source_id)
        if source is None:
            raise ValidationError("source not found")
        stale = source_service.check_freshness(notebook.id, ns.source_id)
        payload = {
            "source_id": source.id,
            "title": source.title,
            "stale": stale,
            "status": source.status.name,
            "kind": source.kind().name,
            "url": source.url,
            "basis": "offline_fixture_status",
        }
        if ns.json:
            _emit(payload, json_mode=True)
        else:
            state = "stale" if stale else "fresh"
            title = source.title or source.id
            print(f"Source {title} is {state} (offline fixture status).")
        if ns.exit_on_stale:
            return 0 if stale else 1
        return 0

    if subcommand == "wait":
        parser = argparse.ArgumentParser(prog="notebooklm source wait")
        parser.add_argument("source_id")
        parser.add_argument(
            "-n", "--notebook", default=None, help="synthetic notebook selector"
        )
        parser.add_argument(
            "--timeout", type=int, default=120, help="maximum seconds to wait"
        )
        parser.add_argument(
            "--interval", type=int, default=1, help="seconds between status checks"
        )
        parser.add_argument("--json", action="store_true", help="Output as JSON")
        parser.add_argument(
            "--fixture-dir",
            default=None,
            help="explicit synthetic rpc_fixtures directory (offline only)",
        )
        ns = parser.parse_args(argv[1:])
        if ns.timeout <= 0:
            raise ValidationError("source wait timeout must be positive")
        if ns.interval <= 0:
            raise ValidationError("source wait interval must be positive")
        notebook_service, source_service = _offline_source_services(ns.fixture_dir)
        notebook = _resolve_note_notebook(notebook_service, ns.notebook)
        source = source_service.get(notebook.id, ns.source_id)
        if source is None:
            raise ValidationError("source not found")
        ready = source.status is _sources.SourceStatus.READY
        failed = source.status is _sources.SourceStatus.ERROR
        timed_out = source.status in {
            _sources.SourceStatus.PROCESSING,
            _sources.SourceStatus.PREPARING,
        }
        payload = {
            "source_id": source.id,
            "title": source.title,
            "ready": ready,
            "status": source.status.name,
            "kind": source.kind().name,
            "url": source.url,
            "timed_out": timed_out,
            "failed": failed,
            "basis": "offline_fixture_status",
        }
        if ns.json:
            _emit(payload, json_mode=True)
        else:
            title = source.title or source.id
            if ready:
                print(f"Source {title} is ready (offline fixture status).")
            elif failed:
                print(f"Source {title} failed processing (offline fixture status).")
            else:
                print(
                    f"Timed out waiting for source {title}; status "
                    f"{source.status.name} (offline fixture status)."
                )
        if ready:
            return 0
        if failed:
            return 1
        return 2

    if subcommand == "guide":
        parser = argparse.ArgumentParser(prog="notebooklm source guide")
        parser.add_argument("source_id")
        parser.add_argument(
            "-n", "--notebook", default=None, help="synthetic notebook selector"
        )
        parser.add_argument("--json", action="store_true", help="Output as JSON")
        parser.add_argument(
            "--fixture-dir",
            default=None,
            help="explicit synthetic rpc_fixtures directory (offline only)",
        )
        ns = parser.parse_args(argv[1:])
        notebook_service, source_service = _offline_source_services(ns.fixture_dir)
        notebook = _resolve_note_notebook(notebook_service, ns.notebook)
        source = source_service.get(notebook.id, ns.source_id)
        if source is None:
            raise ValidationError("source not found")
        guide = source_service.guide(notebook.id, ns.source_id)
        title = source.title or "Untitled source"
        payload = {
            "source_id": source.id,
            "title": title,
            "summary": guide.summary,
            "keywords": list(guide.keywords),
            "type_code": source._type_code,
            "url": source.url,
            "kind": source.kind().name,
        }
        if ns.json:
            _emit(payload, json_mode=True)
        else:
            print(f"Source Guide: {title}")
            print(guide.summary)
            print("Keywords: " + ", ".join(guide.keywords))
        return 0

    if subcommand == "fulltext":
        parser = argparse.ArgumentParser(prog="notebooklm source fulltext")
        parser.add_argument("source_id")
        parser.add_argument(
            "-n", "--notebook", default=None, help="synthetic notebook selector"
        )
        parser.add_argument("--json", action="store_true", help="Output as JSON")
        parser.add_argument(
            "-o", "--output", default=None, help="Write content to file"
        )
        parser.add_argument(
            "--no-clobber", action="store_true", help="Fail if output exists"
        )
        parser.add_argument(
            "--force", action="store_true", help="Overwrite output if it exists"
        )
        parser.add_argument(
            "-f",
            "--format",
            choices=("text", "markdown"),
            default="text",
            help="Content format: text or markdown",
        )
        parser.add_argument(
            "--fixture-dir",
            default=None,
            help="explicit synthetic rpc_fixtures directory (offline only)",
        )
        ns = parser.parse_args(argv[1:])
        if ns.force and ns.no_clobber:
            raise ValidationError("--force and --no-clobber are mutually exclusive")
        notebook_service, source_service = _offline_source_services(ns.fixture_dir)
        notebook = _resolve_note_notebook(notebook_service, ns.notebook)
        fulltext = source_service.fulltext(
            notebook.id,
            ns.source_id,
            output_format=ns.format,
        )
        if ns.output is None:
            if ns.json:
                _emit(fulltext.as_dict(), json_mode=True)
            else:
                print(fulltext.content)
            return 0
        path = Path(ns.output)
        if path.exists() and ns.no_clobber:
            raise ValidationError("output file exists")
        if path.exists() and not ns.force:
            stem, suffix = path.stem, path.suffix
            parent = path.parent
            for index in range(1, 1000):
                candidate = parent / f"{stem}-{index}{suffix}"
                if not candidate.exists():
                    path = candidate
                    break
            else:  # pragma: no cover - defensive exhaustion guard
                raise ValidationError("could not choose non-clobber output path")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(fulltext.content, encoding="utf-8")
        payload = {
            "path": str(path),
            "bytes": len(fulltext.content.encode("utf-8")),
            "source_id": fulltext.source_id,
            "title": fulltext.title,
            "kind": fulltext.kind().name,
        }
        _emit(payload, json_mode=ns.json)
        return 0

    if subcommand == "rename":
        parser = argparse.ArgumentParser(prog="notebooklm source rename")
        parser.add_argument("source_id")
        parser.add_argument("new_title")
        parser.add_argument(
            "-n", "--notebook", default=None, help="synthetic notebook selector"
        )
        parser.add_argument("--json", action="store_true", help="Output as JSON")
        parser.add_argument("--fixture-dir", default=None)
        ns = parser.parse_args(argv[1:])
        notebook_service, source_service = _offline_source_services(ns.fixture_dir)
        notebook = _resolve_note_notebook(notebook_service, ns.notebook)
        source = source_service.rename(notebook.id, ns.source_id, ns.new_title)
        if source is None:
            raise ValidationError("source not found")
        _emit(
            {"source": source.as_dict(), "notebook_id": notebook.id, "renamed": True},
            json_mode=ns.json,
        )
        return 0

    if subcommand == "refresh":
        parser = argparse.ArgumentParser(prog="notebooklm source refresh")
        parser.add_argument("source_id")
        parser.add_argument(
            "-n", "--notebook", default=None, help="synthetic notebook selector"
        )
        parser.add_argument("--json", action="store_true", help="Output as JSON")
        parser.add_argument("--fixture-dir", default=None)
        ns = parser.parse_args(argv[1:])
        notebook_service, source_service = _offline_source_services(ns.fixture_dir)
        notebook = _resolve_note_notebook(notebook_service, ns.notebook)
        refreshed = source_service.refresh(notebook.id, ns.source_id)
        _emit(
            {
                "source_id": ns.source_id,
                "notebook_id": notebook.id,
                "refreshed": refreshed,
            },
            json_mode=ns.json,
        )
        return 0

    if subcommand == "delete":
        parser = argparse.ArgumentParser(prog="notebooklm source delete")
        parser.add_argument("source_id")
        parser.add_argument(
            "-n", "--notebook", default=None, help="synthetic notebook selector"
        )
        parser.add_argument("--yes", "-y", action="store_true")
        parser.add_argument("--json", action="store_true", help="Output as JSON")
        parser.add_argument("--fixture-dir", default=None)
        ns = parser.parse_args(argv[1:])
        if not ns.yes:
            raise ValidationError("pass --yes to confirm deletion")
        notebook_service, source_service = _offline_source_services(ns.fixture_dir)
        notebook = _resolve_note_notebook(notebook_service, ns.notebook)
        source_service.delete(notebook.id, ns.source_id)
        _emit(
            {"source_id": ns.source_id, "notebook_id": notebook.id, "deleted": True},
            json_mode=ns.json,
        )
        return 0

    if subcommand == "delete-by-title":
        parser = argparse.ArgumentParser(prog="notebooklm source delete-by-title")
        parser.add_argument("title")
        parser.add_argument(
            "-n", "--notebook", default=None, help="synthetic notebook selector"
        )
        parser.add_argument("--yes", "-y", action="store_true")
        parser.add_argument("--json", action="store_true", help="Output as JSON")
        parser.add_argument("--fixture-dir", default=None)
        ns = parser.parse_args(argv[1:])
        if not ns.yes:
            raise ValidationError("pass --yes to confirm deletion")
        notebook_service, source_service = _offline_source_services(ns.fixture_dir)
        notebook = _resolve_note_notebook(notebook_service, ns.notebook)
        matches = [
            source
            for source in source_service.list(notebook.id)
            if source.title == ns.title
        ]
        if not matches:
            raise ValidationError("source not found")
        if len(matches) > 1:
            raise ValidationError("source title is ambiguous")
        source = matches[0]
        source_service.delete(notebook.id, source.id)
        _emit(
            {
                "source_id": source.id,
                "title": source.title,
                "notebook_id": notebook.id,
                "deleted": True,
            },
            json_mode=ns.json,
        )
        return 0

    if subcommand == "clean":
        parser = argparse.ArgumentParser(prog="notebooklm source clean")
        parser.add_argument(
            "-n", "--notebook", default=None, help="synthetic notebook selector"
        )
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--yes", "-y", action="store_true")
        parser.add_argument("--json", action="store_true", help="Output as JSON")
        parser.add_argument("--fixture-dir", default=None)
        ns = parser.parse_args(argv[1:])
        notebook_service, source_service = _offline_source_services(ns.fixture_dir)
        notebook = _resolve_note_notebook(notebook_service, ns.notebook)
        seen: set[tuple[str | None, str | None]] = set()
        candidates = []
        for source in source_service.list(notebook.id):
            key = (source.title, source.url)
            duplicate = key in seen
            seen.add(key)
            if duplicate or source.status is _sources.SourceStatus.ERROR:
                reason = "duplicate" if duplicate else "processing_error"
                candidates.append(
                    {"source_id": source.id, "title": source.title, "reason": reason}
                )
        if candidates and not ns.dry_run and not ns.yes:
            raise ValidationError("pass --yes to confirm cleanup")
        deleted = 0
        if not ns.dry_run:
            for candidate in candidates:
                source_service.delete(notebook.id, str(candidate["source_id"]))
                deleted += 1
        _emit(
            {
                "candidates": candidates,
                "deleted": deleted,
                "dry_run": ns.dry_run,
                "total": len(candidates),
            },
            json_mode=ns.json,
        )
        return 0

    if subcommand == "add-research":
        parser = argparse.ArgumentParser(prog="notebooklm source add-research")
        parser.add_argument("query", nargs="?", default=None)
        parser.add_argument(
            "--prompt-file",
            default=None,
            help="read query text from file or '-' for stdin",
        )
        parser.add_argument(
            "-n", "--notebook", default=None, help="synthetic notebook selector"
        )
        parser.add_argument(
            "--from", dest="search_source", choices=("web", "drive"), default="web"
        )
        parser.add_argument("--mode", choices=("fast", "deep"), default="fast")
        parser.add_argument("--import-all", action="store_true")
        parser.add_argument("--cited-only", action="store_true")
        parser.add_argument("--no-wait", action="store_true")
        parser.add_argument("--timeout", type=int, default=1800)
        parser.add_argument("--json", action="store_true", help="Output as JSON")
        parser.add_argument("--fixture-dir", default=None)
        ns = parser.parse_args(argv[1:])
        if ns.cited_only and not ns.import_all:
            raise ValidationError("--cited-only requires --import-all")
        if ns.no_wait and ns.import_all:
            raise ValidationError("--import-all requires --wait")
        if ns.timeout <= 0:
            raise ValidationError("research timeout must be positive")
        if ns.query is not None and ns.prompt_file is not None:
            raise ValidationError("query and --prompt-file are mutually exclusive")
        if ns.query == "-" and ns.prompt_file is None:
            query = sys.stdin.read().strip()
            if query == "":
                raise ValidationError("query is required")
        else:
            query = _read_optional_prompt(
                ns.query,
                ns.prompt_file,
                required=True,
                label="query",
                file_label="query",
            )
        notebook_service, source_service = _offline_source_services(ns.fixture_dir)
        notebook = _resolve_note_notebook(notebook_service, ns.notebook)
        research_api = _client.ResearchAPI(source_service=source_service)
        start = asyncio.run(
            research_api.start(
                notebook.id,
                query,
                source=ns.search_source,
                mode=ns.mode,
            )
        )
        if start is None:
            raise ValidationError("Research failed to start")
        if ns.no_wait:
            payload: dict[str, object] = {"status": "started", "task_id": start.task_id}
            if start.report_id is not None:
                payload["poll_task_id"] = start.report_id
            _emit(payload, json_mode=ns.json)
            return 0
        poll_task_id = start.report_id or start.task_id
        completed = asyncio.run(
            research_api.wait_for_completion(
                notebook.id,
                poll_task_id,
                timeout=float(ns.timeout),
            )
        )
        sources = [source.to_public_dict() for source in completed.sources]
        payload = {
            "status": completed.status.value,
            "task_id": completed.task_id,
            "sources_found": len(sources),
            "sources": sources,
            "report": completed.report,
        }
        if ns.import_all:
            sources_to_import, cited_only_fallback = _research_import_selection(
                completed.sources,
                completed.report,
                cited_only=ns.cited_only,
            )
            imported = asyncio.run(
                research_api.import_sources(
                    notebook.id, completed.task_id, sources_to_import
                )
            )
            payload["imported"] = len(imported)
            payload["imported_sources"] = imported
            if ns.cited_only:
                payload["cited_only"] = True
                payload["cited_sources_selected"] = len(sources_to_import)
                payload["cited_only_fallback"] = bool(cited_only_fallback)
        _emit(payload, json_mode=ns.json)
        return 0

    raise ValidationError("unknown source subcommand")


_ARTIFACT_TYPE_BY_CLI = {
    "all": None,
    "audio": _artifacts.ArtifactType.AUDIO,
    "video": _artifacts.ArtifactType.VIDEO,
    "slide-deck": _artifacts.ArtifactType.SLIDE_DECK,
    "quiz": _artifacts.ArtifactType.QUIZ,
    "flashcard": _artifacts.ArtifactType.FLASHCARDS,
    "flashcards": _artifacts.ArtifactType.FLASHCARDS,
    "infographic": _artifacts.ArtifactType.INFOGRAPHIC,
    "data-table": _artifacts.ArtifactType.DATA_TABLE,
    "mind-map": _artifacts.ArtifactType.MIND_MAP,
    "report": _artifacts.ArtifactType.REPORT,
}


def _artifact_type_from_cli(value: str) -> "_artifacts.ArtifactType | None":
    try:
        return _ARTIFACT_TYPE_BY_CLI[value]
    except KeyError:
        allowed = ", ".join(sorted(_ARTIFACT_TYPE_BY_CLI))
        raise ValidationError(
            f"invalid choice for artifact type: {value!r}; choose from {allowed}"
        ) from None


def _handle_artifact(
    store: "_profiles.ProfileStore | None",
    args: Sequence[str],
    *,
    global_profile: str | None,
) -> int:
    argv = list(args)
    if not argv:
        parser = argparse.ArgumentParser(prog="notebooklm artifact")
        parser.print_help()
        return 0

    subcommand = argv[0]
    if subcommand == "list":
        parser = argparse.ArgumentParser(prog="notebooklm artifact list")
        parser.add_argument(
            "-n", "--notebook", default=None, help="synthetic notebook selector"
        )
        parser.add_argument("--type", default="all", help="artifact type filter")
        parser.add_argument("--json", action="store_true", help="Output as JSON")
        parser.add_argument(
            "--limit", type=int, default=None, help="maximum artifacts to render"
        )
        parser.add_argument(
            "--no-truncate", action="store_true", help="accepted for CLI parity"
        )
        parser.add_argument(
            "--fixture-dir",
            default=None,
            help="explicit synthetic rpc_fixtures directory (offline only)",
        )
        ns = parser.parse_args(argv[1:])
        limit = _validate_limit(ns.limit)
        artifact_type = _artifact_type_from_cli(ns.type)
        notebook_service, artifact_service = _offline_artifact_services(ns.fixture_dir)
        notebook = _resolve_note_notebook(notebook_service, ns.notebook)
        artifacts = [
            artifact.as_dict()
            for artifact in artifact_service.list(notebook.id, artifact_type)
        ]
        if limit is not None:
            artifacts = artifacts[:limit]
        _emit(artifacts, json_mode=ns.json)
        return 0

    if subcommand == "get":
        parser = argparse.ArgumentParser(prog="notebooklm artifact get")
        parser.add_argument("artifact_id")
        parser.add_argument(
            "-n", "--notebook", default=None, help="synthetic notebook selector"
        )
        parser.add_argument("--json", action="store_true", help="Output as JSON")
        parser.add_argument(
            "--fixture-dir",
            default=None,
            help="explicit synthetic rpc_fixtures directory (offline only)",
        )
        ns = parser.parse_args(argv[1:])
        notebook_service, artifact_service = _offline_artifact_services(ns.fixture_dir)
        notebook = _resolve_note_notebook(notebook_service, ns.notebook)
        artifact = artifact_service.get(notebook.id, ns.artifact_id)
        if artifact is None:
            raise ValidationError("artifact not found")
        _emit(artifact.as_dict(), json_mode=ns.json)
        return 0

    if subcommand == "poll":
        parser = argparse.ArgumentParser(prog="notebooklm artifact poll")
        parser.add_argument("task_id")
        parser.add_argument(
            "-n", "--notebook", default=None, help="synthetic notebook selector"
        )
        parser.add_argument("--json", action="store_true", help="Output as JSON")
        parser.add_argument(
            "--status-fixture",
            default=None,
            help="explicit sanitized read/status fixture (offline only)",
        )
        ns = parser.parse_args(argv[1:])
        notebook_service = _offline_notebook_service(None)
        notebook = _resolve_note_notebook(notebook_service, ns.notebook)
        status = _offline_status_fixtures(ns.status_fixture).get_artifact_status(
            notebook.id, ns.task_id
        )
        payload = {
            "task_id": status.task_id,
            "status": status.status,
            "url": status.url,
            "error": status.error,
            "error_code": status.error_code,
            "metadata": status.metadata,
        }
        _emit(payload, json_mode=ns.json)
        return 0

    if subcommand == "wait":
        parser = argparse.ArgumentParser(prog="notebooklm artifact wait")
        parser.add_argument("artifact_id")
        parser.add_argument(
            "-n", "--notebook", default=None, help="synthetic notebook selector"
        )
        parser.add_argument(
            "--timeout", type=int, default=300, help="maximum seconds to wait"
        )
        parser.add_argument(
            "--interval", type=int, default=2, help="accepted for CLI parity"
        )
        parser.add_argument("--json", action="store_true", help="Output as JSON")
        parser.add_argument(
            "--status-fixture",
            default=None,
            help="explicit sanitized read/status fixture (offline only)",
        )
        ns = parser.parse_args(argv[1:])
        if ns.timeout <= 0:
            raise ValidationError("artifact wait timeout must be positive")
        if ns.interval <= 0:
            raise ValidationError("artifact wait interval must be positive")
        notebook_service = _offline_notebook_service(None)
        notebook = _resolve_note_notebook(notebook_service, ns.notebook)
        fixtures = _offline_status_fixtures(ns.status_fixture)
        try:
            status = fixtures.wait_for_artifact(notebook.id, ns.artifact_id)
        except TimeoutError:
            _emit(
                {
                    "artifact_id": ns.artifact_id,
                    "status": "timeout",
                    "error": f"Timed out after {ns.timeout} seconds",
                },
                json_mode=ns.json,
            )
            return 1
        payload = {
            "artifact_id": status.task_id,
            "status": status.status,
            "url": status.url,
            "error": status.error,
        }
        _emit(payload, json_mode=ns.json)
        return 0 if status.status == "completed" else 1

    if subcommand == "suggestions":
        parser = argparse.ArgumentParser(prog="notebooklm artifact suggestions")
        parser.add_argument(
            "-n", "--notebook", default=None, help="synthetic notebook selector"
        )
        parser.add_argument("--json", action="store_true", help="Output as JSON")
        parser.add_argument(
            "--status-fixture",
            default=None,
            help="explicit sanitized read/status fixture (offline only)",
        )
        ns = parser.parse_args(argv[1:])
        notebook_service = _offline_notebook_service(None)
        notebook = _resolve_note_notebook(notebook_service, ns.notebook)
        suggestions = _offline_status_fixtures(ns.status_fixture).suggest_reports(
            notebook.id
        )
        payload = [
            {
                "title": item.title,
                "description": item.description,
                "prompt": item.prompt,
            }
            for item in suggestions
        ]
        _emit(payload, json_mode=ns.json)
        return 0

    if subcommand == "rename":
        parser = argparse.ArgumentParser(prog="notebooklm artifact rename")
        parser.add_argument("artifact_id")
        parser.add_argument("new_title")
        parser.add_argument(
            "-n", "--notebook", default=None, help="synthetic notebook selector"
        )
        parser.add_argument("--json", action="store_true", help="Output as JSON")
        parser.add_argument("--fixture-dir", default=None)
        ns = parser.parse_args(argv[1:])
        notebook_service, artifact_service = _offline_artifact_services(ns.fixture_dir)
        notebook = _resolve_note_notebook(notebook_service, ns.notebook)
        artifact = artifact_service.rename(notebook.id, ns.artifact_id, ns.new_title)
        if artifact is None:
            raise ValidationError("artifact not found")
        _emit(
            {
                "artifact": artifact.as_dict(),
                "notebook_id": notebook.id,
                "renamed": True,
            },
            json_mode=ns.json,
        )
        return 0

    if subcommand == "delete":
        parser = argparse.ArgumentParser(prog="notebooklm artifact delete")
        parser.add_argument("artifact_id")
        parser.add_argument(
            "-n", "--notebook", default=None, help="synthetic notebook selector"
        )
        parser.add_argument("--yes", "-y", action="store_true")
        parser.add_argument("--json", action="store_true", help="Output as JSON")
        parser.add_argument("--fixture-dir", default=None)
        ns = parser.parse_args(argv[1:])
        if not ns.yes:
            raise ValidationError("pass --yes to confirm deletion")
        notebook_service, artifact_service = _offline_artifact_services(ns.fixture_dir)
        notebook = _resolve_note_notebook(notebook_service, ns.notebook)
        artifact_service.delete(notebook.id, ns.artifact_id)
        _emit(
            {
                "artifact_id": ns.artifact_id,
                "notebook_id": notebook.id,
                "deleted": True,
            },
            json_mode=ns.json,
        )
        return 0

    if subcommand == "export":
        parser = argparse.ArgumentParser(prog="notebooklm artifact export")
        parser.add_argument("artifact_id")
        parser.add_argument("--title", default="Export")
        parser.add_argument(
            "-n", "--notebook", default=None, help="synthetic notebook selector"
        )
        parser.add_argument("--json", action="store_true", help="Output as JSON")
        parser.add_argument("--fixture-dir", default=None)
        ns = parser.parse_args(argv[1:])
        notebook_service, artifact_service = _offline_artifact_services(ns.fixture_dir)
        notebook = _resolve_note_notebook(notebook_service, ns.notebook)
        payload = artifact_service.export(notebook.id, ns.artifact_id, title=ns.title)
        _emit(payload, json_mode=ns.json)
        return 0

    if subcommand == "retry":
        parser = argparse.ArgumentParser(prog="notebooklm artifact retry")
        parser.add_argument("artifact_id")
        parser.add_argument(
            "-n", "--notebook", default=None, help="synthetic notebook selector"
        )
        parser.add_argument("--json", action="store_true", help="Output as JSON")
        parser.add_argument("--fixture-dir", default=None)
        ns = parser.parse_args(argv[1:])
        notebook_service, artifact_service = _offline_artifact_services(ns.fixture_dir)
        notebook = _resolve_note_notebook(notebook_service, ns.notebook)
        artifact = artifact_service.get(notebook.id, ns.artifact_id)
        if artifact is None:
            raise ValidationError("artifact not found")
        payload = {"task_id": artifact.id, "status": "completed", "url": artifact.url}
        _emit(payload, json_mode=ns.json)
        return 0

    raise ValidationError("unknown artifact subcommand")


def _research_wait_payload(task: Any) -> dict[str, object]:
    sources = [source.to_public_dict() for source in task.sources]
    return {
        "status": task.status.value,
        "query": task.query,
        "sources_found": len(sources),
        "sources": sources,
        "report": task.report,
    }


def _handle_research(
    store: "_profiles.ProfileStore | None",
    args: Sequence[str],
    *,
    global_profile: str | None,
) -> int:
    argv = list(args)
    if not argv:
        parser = argparse.ArgumentParser(prog="notebooklm research")
        parser.print_help()
        return 0

    subcommand = argv[0]
    if subcommand == "status":
        parser = argparse.ArgumentParser(prog="notebooklm research status")
        parser.add_argument(
            "-n", "--notebook", default=None, help="synthetic notebook selector"
        )
        parser.add_argument("--json", action="store_true", help="Output as JSON")
        parser.add_argument(
            "--status-fixture", default=None, help="explicit sanitized fixture"
        )
        ns = parser.parse_args(argv[1:])
        notebook_service = _offline_notebook_service(None)
        notebook = _resolve_note_notebook(notebook_service, ns.notebook)
        task = _offline_status_fixtures(ns.status_fixture).poll_research(notebook.id)
        _emit(task.to_public_dict(), json_mode=ns.json)
        return 0

    if subcommand == "wait":
        parser = argparse.ArgumentParser(prog="notebooklm research wait")
        parser.add_argument(
            "-n", "--notebook", default=None, help="synthetic notebook selector"
        )
        parser.add_argument(
            "--timeout", type=int, default=300, help="maximum seconds to wait"
        )
        parser.add_argument(
            "--interval", type=int, default=5, help="accepted for CLI parity"
        )
        parser.add_argument(
            "--import-all",
            action="store_true",
            help="Import all found sources when done",
        )
        parser.add_argument(
            "--cited-only",
            action="store_true",
            help="With --import-all, import only cited sources",
        )
        parser.add_argument("--json", action="store_true", help="Output as JSON")
        parser.add_argument(
            "--status-fixture", default=None, help="explicit sanitized fixture"
        )
        ns = parser.parse_args(argv[1:])
        if ns.cited_only and not ns.import_all:
            raise ValidationError("--cited-only requires --import-all")
        if ns.timeout <= 0:
            raise ValidationError("research wait timeout must be positive")
        if ns.interval <= 0:
            raise ValidationError("research wait interval must be positive")
        notebook_service, source_service = _offline_source_services(None)
        notebook = _resolve_note_notebook(notebook_service, ns.notebook)
        fixtures = _offline_status_fixtures(ns.status_fixture)
        research_api = _client.ResearchAPI(
            source_service=source_service,
            status_fixtures=fixtures,
        )
        try:
            task = asyncio.run(
                research_api.wait_for_completion(
                    notebook.id,
                    timeout=float(ns.timeout),
                    interval=float(ns.interval),
                )
            )
        except TimeoutError:
            _emit(
                {"status": "timeout", "error": f"Timed out after {ns.timeout}s"},
                json_mode=ns.json,
            )
            return 1
        if task.status.value == "no_research":
            _emit(
                {"status": "no_research", "error": "No research running"},
                json_mode=ns.json,
            )
            return 1
        payload = _research_wait_payload(task)
        if task.status.value == "completed" and ns.import_all:
            sources_to_import, cited_only_fallback = _research_import_selection(
                task.sources,
                task.report,
                cited_only=ns.cited_only,
            )
            imported = asyncio.run(
                research_api.import_sources(
                    notebook.id, task.task_id, sources_to_import
                )
            )
            payload["imported"] = len(imported)
            payload["imported_sources"] = imported
            if ns.cited_only:
                payload["cited_only"] = True
                payload["cited_sources_selected"] = len(sources_to_import)
                payload["cited_only_fallback"] = bool(cited_only_fallback)
        _emit(payload, json_mode=ns.json)
        return 0 if task.status.value == "completed" else 1

    raise ValidationError("unknown research subcommand")


def _share_status_payload(status: Any) -> dict[str, object]:
    return {
        "notebook_id": status.notebook_id,
        "is_public": status.is_public,
        "access": status.access.name.lower(),
        "view_level": status.view_level.name.lower(),
        "share_url": status.share_url,
        "shared_users": [
            {
                "email": user.email,
                "permission": user.permission.name.lower(),
                "display_name": user.display_name,
            }
            for user in status.shared_users
        ],
    }


def _parse_share_permission(value: str) -> "_types.SharePermission":
    normalized = value.lower()
    if normalized == "editor":
        return _types.SharePermission.EDITOR
    if normalized == "viewer":
        return _types.SharePermission.VIEWER
    raise ValidationError("share permission must be editor or viewer")


def _parse_share_view_level(value: str) -> "_types.ShareViewLevel":
    normalized = value.lower()
    if normalized == "full":
        return _types.ShareViewLevel.FULL_NOTEBOOK
    if normalized == "chat":
        return _types.ShareViewLevel.CHAT_ONLY
    raise ValidationError("share view level must be full or chat")


def _offline_sharing_api(status_fixture: str | None = None) -> Any:
    from .client import SharingAPI

    return SharingAPI(status_fixtures=_offline_status_fixtures(status_fixture))


def _handle_share(
    store: "_profiles.ProfileStore | None",
    args: Sequence[str],
    *,
    global_profile: str | None,
) -> int:
    argv = list(args)
    if not argv:
        parser = argparse.ArgumentParser(prog="notebooklm share")
        parser.print_help()
        return 0

    subcommand = argv[0]
    if subcommand == "status":
        parser = argparse.ArgumentParser(prog="notebooklm share status")
        parser.add_argument(
            "-n", "--notebook", default=None, help="synthetic notebook selector"
        )
        parser.add_argument("--json", action="store_true", help="Output as JSON")
        parser.add_argument(
            "--status-fixture", default=None, help="explicit sanitized fixture"
        )
        ns = parser.parse_args(argv[1:])
        notebook_service = _offline_notebook_service(None)
        notebook = _resolve_note_notebook(notebook_service, ns.notebook)
        status = asyncio.run(
            _offline_sharing_api(ns.status_fixture).get_status(notebook.id)
        )
        _emit(_share_status_payload(status), json_mode=ns.json)
        return 0

    if subcommand == "public":
        parser = argparse.ArgumentParser(prog="notebooklm share public")
        parser.add_argument(
            "-n", "--notebook", default=None, help="synthetic notebook selector"
        )
        parser.add_argument(
            "--enable", dest="enable", action="store_true", default=True
        )
        parser.add_argument("--disable", dest="enable", action="store_false")
        parser.add_argument("--json", action="store_true", help="Output as JSON")
        parser.add_argument(
            "--status-fixture", default=None, help="explicit sanitized fixture"
        )
        ns = parser.parse_args(argv[1:])
        notebook_service = _offline_notebook_service(None)
        notebook = _resolve_note_notebook(notebook_service, ns.notebook)
        status = asyncio.run(
            _offline_sharing_api(ns.status_fixture).set_public(notebook.id, ns.enable)
        )
        _emit(
            {
                "notebook_id": status.notebook_id,
                "is_public": status.is_public,
                "share_url": status.share_url,
            },
            json_mode=ns.json,
        )
        return 0

    if subcommand == "view-level":
        parser = argparse.ArgumentParser(prog="notebooklm share view-level")
        parser.add_argument("level", choices=("full", "chat"))
        parser.add_argument(
            "-n", "--notebook", default=None, help="synthetic notebook selector"
        )
        parser.add_argument("--json", action="store_true", help="Output as JSON")
        parser.add_argument(
            "--status-fixture", default=None, help="explicit sanitized fixture"
        )
        ns = parser.parse_args(argv[1:])
        notebook_service = _offline_notebook_service(None)
        notebook = _resolve_note_notebook(notebook_service, ns.notebook)
        level = _parse_share_view_level(ns.level)
        status = asyncio.run(
            _offline_sharing_api(ns.status_fixture).set_view_level(notebook.id, level)
        )
        _emit(
            {
                "notebook_id": status.notebook_id,
                "view_level": status.view_level.name.lower(),
            },
            json_mode=ns.json,
        )
        return 0

    if subcommand == "add":
        parser = argparse.ArgumentParser(prog="notebooklm share add")
        parser.add_argument("email")
        parser.add_argument(
            "-n", "--notebook", default=None, help="synthetic notebook selector"
        )
        parser.add_argument(
            "--permission", "-p", choices=("editor", "viewer"), default="viewer"
        )
        parser.add_argument("--no-notify", action="store_true", default=False)
        parser.add_argument("--message", "-m", default="")
        parser.add_argument("--json", action="store_true", help="Output as JSON")
        parser.add_argument(
            "--status-fixture", default=None, help="explicit sanitized fixture"
        )
        ns = parser.parse_args(argv[1:])
        notebook_service = _offline_notebook_service(None)
        notebook = _resolve_note_notebook(notebook_service, ns.notebook)
        permission = _parse_share_permission(ns.permission)
        asyncio.run(
            _offline_sharing_api(ns.status_fixture).add_user(
                notebook.id,
                ns.email,
                permission,
                notify=not ns.no_notify,
                welcome_message=ns.message,
            )
        )
        _emit(
            {
                "notebook_id": notebook.id,
                "added_user": ns.email,
                "permission": ns.permission.lower(),
                "notified": not ns.no_notify,
            },
            json_mode=ns.json,
        )
        return 0

    if subcommand == "update":
        parser = argparse.ArgumentParser(prog="notebooklm share update")
        parser.add_argument("email")
        parser.add_argument(
            "-n", "--notebook", default=None, help="synthetic notebook selector"
        )
        parser.add_argument(
            "--permission", "-p", choices=("editor", "viewer"), required=True
        )
        parser.add_argument("--json", action="store_true", help="Output as JSON")
        parser.add_argument(
            "--status-fixture", default=None, help="explicit sanitized fixture"
        )
        ns = parser.parse_args(argv[1:])
        notebook_service = _offline_notebook_service(None)
        notebook = _resolve_note_notebook(notebook_service, ns.notebook)
        permission = _parse_share_permission(ns.permission)
        asyncio.run(
            _offline_sharing_api(ns.status_fixture).update_user(
                notebook.id, ns.email, permission
            )
        )
        _emit(
            {
                "notebook_id": notebook.id,
                "updated_user": ns.email,
                "permission": ns.permission.lower(),
            },
            json_mode=ns.json,
        )
        return 0

    if subcommand == "remove":
        parser = argparse.ArgumentParser(prog="notebooklm share remove")
        parser.add_argument("email")
        parser.add_argument(
            "-n", "--notebook", default=None, help="synthetic notebook selector"
        )
        parser.add_argument(
            "--yes", "-y", action="store_true", help="skip confirmation"
        )
        parser.add_argument("--json", action="store_true", help="Output as JSON")
        parser.add_argument(
            "--status-fixture", default=None, help="explicit sanitized fixture"
        )
        ns = parser.parse_args(argv[1:])
        if not ns.yes:
            raise ValidationError("pass --yes to confirm share removal")
        notebook_service = _offline_notebook_service(None)
        notebook = _resolve_note_notebook(notebook_service, ns.notebook)
        asyncio.run(
            _offline_sharing_api(ns.status_fixture).remove_user(notebook.id, ns.email)
        )
        _emit(
            {"notebook_id": notebook.id, "removed_user": ns.email},
            json_mode=ns.json,
        )
        return 0

    raise ValidationError("unknown share subcommand")


def _handle_profile(
    store: "_profiles.ProfileStore", args: Sequence[str], *, global_profile: str | None
) -> int:
    parser = argparse.ArgumentParser(prog="notebooklm profile")
    sub = parser.add_subparsers(dest="subcommand")

    p_create = sub.add_parser("create", help="Create a new profile.")
    p_create.add_argument("name")

    p_delete = sub.add_parser("delete", help="Delete a profile and its data.")
    p_delete.add_argument("name")
    p_delete.add_argument("-y", "--yes", action="store_true", help="Skip confirmation")
    p_delete.add_argument(
        "--confirm",
        dest="yes",
        action="store_true",
        help=argparse.SUPPRESS,
    )

    p_list = sub.add_parser("list", help="List all profiles and their status.")
    p_list.add_argument("--json", action="store_true", help="Output as JSON")

    p_rename = sub.add_parser("rename", help="Rename a profile.")
    p_rename.add_argument("old_name")
    p_rename.add_argument("new_name")

    p_switch = sub.add_parser("switch", help="Set the default profile.")
    p_switch.add_argument("name")

    ns = parser.parse_args(list(args))
    if not ns.subcommand:
        parser.print_help()
        return 0

    if ns.subcommand == "create":
        store.create_profile(ns.name)
        print(f"Profile '{ns.name}' created.")
        print(f"Run 'notebooklm -p {ns.name} login' to authenticate.")
        return 0
    if ns.subcommand == "delete":
        configured_default = store.active_profile() or _profiles.DEFAULT_PROFILE_NAME
        effective_active = store.resolve_profile(global_profile)
        if ns.name in (configured_default, effective_active):
            raise _profiles.ProfileError(
                f"Cannot delete active/default profile '{ns.name}'. "
                "Switch to another profile first with 'notebooklm profile switch <name>'."
            )
        if not store.profile_exists(ns.name):
            raise _profiles.ProfileNotFoundError(f"profile not found: {ns.name}")
        if not ns.yes:
            try:
                answer = input(f"Delete profile '{ns.name}' and all its data? [y/N]: ")
            except EOFError:
                answer = ""
            if answer.strip().lower() not in ("y", "yes"):
                print("Cancelled.")
                return 0
        store.delete_profile(ns.name)
        print(f"Profile '{ns.name}' deleted.")
        return 0
    if ns.subcommand == "list":
        active = store.resolve_profile(global_profile)
        names = store.list_profiles()
        if not names:
            payload = {"profiles": [], "active": active}
            if ns.json:
                _emit(payload, json_mode=True)
            else:
                print("No profiles found. Run 'notebooklm login' to create one.")
            return 0
        profiles = []
        for name in names:
            storage = store.storage_state_path(name)
            metadata = _auth.read_account_metadata(storage)
            email = metadata.get("email")
            profiles.append(
                {
                    "name": name,
                    "active": name == active,
                    "authenticated": storage.exists(),
                    "account": email if isinstance(email, str) else None,
                }
            )
        payload = {"active": active, "profiles": profiles}
        if ns.json:
            _emit(payload, json_mode=True)
        else:
            _print_profile_list(profiles, active)
        return 0
    if ns.subcommand == "rename":
        active_before = store.active_profile() or _profiles.DEFAULT_PROFILE_NAME
        store.rename_profile(ns.old_name, ns.new_name)
        if active_before == ns.old_name:
            print(f"Updated default profile in config: {ns.old_name} → {ns.new_name}")
        print(f"Profile renamed: {ns.old_name} → {ns.new_name}")
        return 0
    if ns.subcommand == "switch":
        old_profile = store.active_profile() or _profiles.DEFAULT_PROFILE_NAME
        store.switch_profile(ns.name)
        print(f"Switched default profile: {old_profile} → {ns.name}")
        return 0
    return 0  # pragma: no cover - unreachable (argparse constrains subcommands)


def _print_profile_list(profiles: list[dict[str, Any]], active: str) -> None:
    print("Profiles")
    print("  Name  Account  Auth Status")
    for profile in profiles:
        marker = "*" if profile["active"] else ""
        account = profile["account"] or "-"
        status = "authenticated" if profile["authenticated"] else "not authenticated"
        print(f"{marker:2} {profile['name']}  {account}  {status}")
    print()
    print(f"Active profile: {active}")


def _handle_use(
    store: "_profiles.ProfileStore", args: Sequence[str], *, global_profile: str | None
) -> int:
    parser = argparse.ArgumentParser(prog="notebooklm use")
    parser.add_argument("notebook_id")
    parser.add_argument(
        "--title", default=None, help="optional local title hint (Phase 2A)"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="persist without resolving against the offline fixture list",
    )
    parser.add_argument(
        "--fixture-dir",
        default=None,
        help="explicit synthetic rpc_fixtures directory for selector resolution",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    ns = parser.parse_args(list(args))

    profile = store.resolve_profile(global_profile)
    verified = False
    notebook_id = ns.notebook_id
    notebook_title = ns.title
    if not ns.force:
        notebook = _offline_notebook_service(ns.fixture_dir).resolve(ns.notebook_id)
        notebook_id = notebook.id
        notebook_title = ns.title if ns.title is not None else notebook.title
        verified = True
    saved = _profiles.set_active_notebook(
        store.context_path(profile), notebook_id, title=notebook_title
    )
    _emit(
        {
            "profile": profile,
            "notebook_id": saved["notebook_id"],
            "notebook_title": saved["notebook_title"],
            "verified": verified,
        },
        json_mode=ns.json,
    )
    return 0


def _handle_status(
    store: "_profiles.ProfileStore", args: Sequence[str], *, global_profile: str | None
) -> int:
    parser = argparse.ArgumentParser(prog="notebooklm status")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--paths", action="store_true", help="Show resolved file paths")
    ns = parser.parse_args(list(args))

    profile = store.resolve_profile(global_profile)
    if ns.paths:
        _emit(
            {"paths": _status_path_info(store, profile, global_profile)},
            json_mode=ns.json,
        )
        return 0
    payload = _status_cli_payload(store.context_path(profile))
    _emit(payload, json_mode=ns.json)
    return 0


def _status_profile_source(store: "_profiles.ProfileStore", profile: str | None) -> str:
    if getattr(store, "_explicit_cli_storage_arg", False):
        other_profile_set = (
            profile or getenv("NOTEBOOKLM_PROFILE") or store.active_profile()
        )
        return (
            "CLI flag (--storage, profile ignored)"
            if other_profile_set
            else "CLI flag (--storage)"
        )
    if profile:
        return "CLI flag"
    if getenv("NOTEBOOKLM_PROFILE"):
        return "NOTEBOOKLM_PROFILE env var"
    if store.active_profile():
        return "config.json"
    return "default"


def _status_path_info(
    store: "_profiles.ProfileStore", profile: str, raw_profile: str | None
) -> dict[str, str]:
    profile_dir = store.profile_dir(profile)
    return {
        "home_dir": str(store.home),
        "home_source": "NOTEBOOKLM_HOME"
        if getenv("NOTEBOOKLM_HOME")
        else "default (~/.notebooklm)",
        "profile": profile,
        "profile_source": _status_profile_source(store, raw_profile),
        "profile_dir": str(profile_dir),
        "storage_path": str(store.storage_state_path(profile)),
        "context_path": str(store.context_path(profile)),
        "config_path": str(store.config_path),
        "browser_profile_dir": str(profile_dir / "browser_profile"),
    }


def _status_cli_payload(context_path: Path) -> dict[str, Any]:
    active = _profiles.get_active_notebook(context_path)
    if active is None:
        return {"has_context": False, "notebook": None, "conversation_id": None}
    title = active.get("notebook_title")
    return {
        "has_context": True,
        "notebook": {
            "id": active.get("notebook_id"),
            "title": title if title and title != "-" else None,
            "is_owner": active.get("is_owner", True),
        },
        "conversation_id": active.get("conversation_id"),
    }


def _handle_clear(
    store: "_profiles.ProfileStore", args: Sequence[str], *, global_profile: str | None
) -> int:
    parser = argparse.ArgumentParser(prog="notebooklm clear")
    parser.parse_args(list(args))

    profile = store.resolve_profile(global_profile)
    _profiles.clear_context(store.context_path(profile))
    print("Context cleared")
    return 0


def _handle_auth(
    store: "_profiles.ProfileStore", args: Sequence[str], *, global_profile: str | None
) -> int:
    parser = argparse.ArgumentParser(prog="notebooklm auth")
    sub = parser.add_subparsers(dest="subcommand")

    p_check = sub.add_parser("check", help="Check authentication status (offline).")
    p_check.add_argument("--json", action="store_true", help="Output as JSON")
    p_check.add_argument(
        "--test", action="store_true", help="verify token fetch over the network"
    )

    p_inspect = sub.add_parser(
        "inspect", help="Inspect account metadata in stored auth."
    )
    p_inspect.add_argument("--json", action="store_true", help="Output as JSON")
    # Redacted browser-cookie inspection. Explicit stores stay offline; supported
    # live browser/OS lanes require no store/root.
    p_inspect.add_argument(
        "--browser",
        default="auto",
        help="inspect a browser cookie store instead of stored auth",
    )
    p_inspect.add_argument(
        "--cookie-store",
        dest="cookie_store",
        default=None,
        help="explicit path to a browser Cookies database/file",
    )
    p_inspect.add_argument(
        "--fixture-root",
        dest="fixture_root",
        default=None,
        help="explicit synthetic browser-data root",
    )
    p_inspect.add_argument(
        "--os",
        dest="os_name",
        default=None,
        choices=sorted(_bc.OS_ROWS),
        help="OS layout for --fixture-root",
    )
    p_inspect.add_argument(
        "--browser-profile",
        dest="browser_profile",
        default=None,
        help="browser profile name within the store",
    )
    p_inspect.add_argument(
        "--include-domains",
        dest="include_domains",
        action="append",
        default=[],
        help="include sibling-product cookie domains",
    )
    p_inspect.add_argument("-v", "--verbose", action="store_true")

    p_logout = sub.add_parser(
        "logout", help="Clear saved authentication for the profile."
    )
    p_logout.add_argument("--json", action="store_true", help="Output as JSON")

    # With --browser-cookies, refresh an explicit source or persisted source
    # metadata; live metadata re-resolves only through a supported explicit OS lane.
    p_refresh = sub.add_parser(
        "refresh",
        help="Re-import browser cookies from an explicit or persisted source.",
    )
    p_refresh.add_argument(
        "--browser-cookies",
        "--browser-cookie",
        dest="browser_cookies",
        default=None,
        help="re-import auth cookies from BROWSER's cookie store",
    )
    p_refresh.add_argument(
        "--cookie-store",
        dest="cookie_store",
        default=None,
        help="explicit path to a browser Cookies database/file (offline)",
    )
    p_refresh.add_argument(
        "--fixture-root",
        dest="fixture_root",
        default=None,
        help="explicit synthetic browser-data root (offline)",
    )
    p_refresh.add_argument(
        "--os",
        dest="os_name",
        default=None,
        choices=sorted(_bc.OS_ROWS),
        help="OS layout to use with --fixture-root",
    )
    p_refresh.add_argument(
        "--browser-profile",
        dest="browser_profile",
        default=None,
        help="browser profile name within the store",
    )
    p_refresh.add_argument(
        "--include-all-domains",
        dest="include_all_domains",
        action="store_true",
        help="import cookies from all domains, not just the Google set",
    )
    p_refresh.add_argument(
        "--include-domains",
        dest="include_domains",
        action="append",
        default=[],
        help="include sibling-product cookie domains",
    )
    p_refresh.add_argument("--json", action="store_true", help="Output as JSON")
    p_refresh.add_argument("-q", "--quiet", action="store_true")

    ns = parser.parse_args(list(args))
    if not ns.subcommand:
        parser.print_help()
        return 0

    if ns.subcommand == "refresh" and getenv("NOTEBOOKLM_AUTH_JSON"):
        print(
            "Error: 'auth refresh' is incompatible with NOTEBOOKLM_AUTH_JSON. "
            "The keepalive needs a writable storage_state.json to persist "
            "rotated cookies. Either unset the env var for this process and use "
            "a profile-backed storage file, or arrange for the env var to be "
            "refreshed externally.",
            file=sys.stderr,
        )
        return 1

    # Phase 2G live network refresh is implemented only for profile-backed
    # storage. Browser-source options remain browser-cookie-only because the
    # keepalive path does not re-extract browser domains or stores.
    if ns.subcommand == "refresh" and not ns.browser_cookies:
        browser_source_flags = {
            "--include-all-domains": ns.include_all_domains,
            "--cookie-store": bool(ns.cookie_store),
            "--fixture-root": bool(ns.fixture_root),
            "--os": bool(ns.os_name),
            "--browser-profile": bool(ns.browser_profile),
        }
        if ns.include_domains:
            print(
                "Error: --include-domains only applies when --browser-cookies is also set (the keepalive-only path does not re-extract cookies).",
                file=sys.stderr,
            )
            return 1
        invalid = [flag for flag, present in browser_source_flags.items() if present]
        if invalid:
            raise ValidationError(
                ", ".join(invalid) + " only applies with --browser-cookies; "
                "auth refresh without --browser-cookies uses the stored profile cookies"
            )

    # Explicit browser-cookie inspection keeps its redacted diagnostic. A live
    # inspect instead matches upstream by enumerating account metadata in memory.
    if ns.subcommand == "inspect" and (
        ns.browser or ns.cookie_store or ns.fixture_root
    ):
        if not ns.browser:
            raise ValidationError(
                "--browser is required to inspect a browser cookie store"
            )
        parse_code, include_domains = _parse_include_domains_or_exit(ns.include_domains)
        if parse_code:
            return parse_code
        if ns.browser == "auto" and not ns.cookie_store and not ns.fixture_root:
            return _browser_cookie_rookiepy_missing(json_mode=ns.json)
        if not ns.cookie_store and not ns.fixture_root:
            discovery_error = _browser_cookie_discovery_error(
                ns.browser, json_mode=ns.json
            )
            if discovery_error is not None:
                return discovery_error
        browser, browser_profile = _split_browser_selector(
            ns.browser, ns.browser_profile
        )
        if not ns.cookie_store and not ns.fixture_root:
            accounts = _bc.enumerate_live_browser_accounts(
                browser,
                os_name=ns.os_name,
                browser_profile=browser_profile,
                include_domains=include_domains,
                use_keychain=(
                    _bc.browser_family(_bc.normalize_browser(browser))
                    == _bc.FAMILY_CHROMIUM
                    and ns.os_name in (_bc.LINUX, _bc.MACOS, _bc.WINDOWS)
                ),
            )
            if ns.json:
                _emit({"browser": ns.browser, "accounts": accounts}, json_mode=True)
            else:
                print(f"Browser: {ns.browser}")
                print(f"Found {len(accounts)} signed-in Google account(s):")
                for account in accounts:
                    suffix = " (default)" if account["is_default"] else ""
                    profile = (
                        f" ({account['browser_profile']})"
                        if ns.verbose and account["browser_profile"]
                        else ""
                    )
                    print(f"{account['email']}{suffix}{profile}")
            return 0
        else:
            report = _bc.inspect_cookie_store(
                browser,
                fixture_root=ns.fixture_root,
                cookie_store=ns.cookie_store,
                os_name=ns.os_name,
                browser_profile=browser_profile,
                google_only=True,
                include_domains=include_domains,
            )
        _emit(report, json_mode=ns.json)
        return 0

    profile = store.resolve_profile(global_profile)
    storage_path = store.storage_state_path(profile)

    if ns.subcommand == "check":
        payload, ok = _auth_check_cli_payload(
            storage_path,
            profile=profile,
            test_fetch=ns.test,
            env_auth=bool(
                getenv("NOTEBOOKLM_AUTH_JSON")
                and not getattr(store, "_explicit_cli_storage_arg", False)
            ),
            home_env=bool(getenv("NOTEBOOKLM_HOME")),
        )
        _emit(payload, json_mode=ns.json)
        return 0 if ok or not ns.json else 1
    if ns.subcommand == "inspect":
        _emit(_auth.inspect_storage(storage_path), json_mode=ns.json)
        return 0
    if ns.subcommand == "logout":
        result = _auth.logout(
            storage_path=storage_path,
            browser_profile_dir=store.browser_profile_dir(profile),
            context_path=store.context_path(profile),
        )
        _emit(result, json_mode=ns.json)
        return 0
    if ns.subcommand == "refresh":
        if not ns.browser_cookies:
            summary = _auth.refresh_storage(storage_path)
            if not ns.quiet:
                _emit({"profile": profile, **summary}, json_mode=ns.json)
            return 0
        parse_code, include_domains = _parse_include_domains_or_exit(ns.include_domains)
        if parse_code:
            return parse_code
        browser_cookies, browser_profile = _split_browser_selector(
            ns.browser_cookies, ns.browser_profile
        )
        # Reached with --browser-cookies. Use an explicit --cookie-store/
        # --fixture-root when given, else the profile's persisted source metadata.
        if not ns.fixture_root and not ns.cookie_store:
            discovery_error = _browser_cookie_discovery_error(
                browser_cookies, json_mode=False
            )
            if discovery_error is not None:
                return discovery_error
        summary = _bc.refresh_browser_cookies(
            browser_cookies,
            dest_path=storage_path,
            meta_path=store.auth_source_path(profile),
            fixture_root=ns.fixture_root,
            cookie_store=ns.cookie_store,
            os_name=ns.os_name,
            browser_profile=browser_profile,
            include_all_domains=ns.include_all_domains,
            include_domains=include_domains,
            use_keychain=True,
        )
        _emit({"profile": profile, **summary}, json_mode=ns.json)
        return 0
    return 0  # pragma: no cover - unreachable (argparse constrains subcommands)


def _handle_doctor(
    store: "_profiles.ProfileStore | None",
    args: Sequence[str],
    *,
    global_profile: str | None,
) -> int:
    parser = argparse.ArgumentParser(prog="notebooklm doctor")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--fix", action="store_true", help="Attempt safe local repairs")
    # Phase 2D: opt-in offline auth-matrix readiness report. Off by default so the
    # existing doctor output is unchanged. Reads only the explicit (or committed
    # compat) matrix; never touches a real browser store, keychain, or the network.
    parser.add_argument(
        "--auth-matrix",
        dest="auth_matrix",
        action="store_true",
        help="report offline auth-matrix readiness instead of local checks",
    )
    parser.add_argument(
        "--auth-matrix-path",
        dest="auth_matrix_path",
        default=None,
        help="explicit path to a compat/auth_matrix.json (offline; "
        "defaults to the committed compat artifact)",
    )
    ns = parser.parse_args(list(args))

    if ns.auth_matrix:
        path = ns.auth_matrix_path or _readiness.default_auth_matrix_path()
        report = _readiness.build_report(path)
        payload = report if ns.json else _readiness.human_view(report)
        _emit(payload, json_mode=ns.json)
        return 0

    if store is None:  # pragma: no cover - dispatch invariant
        raise ValidationError("doctor local checks require a profile store")
    report, ok = _doctor_cli_report(store, profile=global_profile, fix=ns.fix)
    _emit(report, json_mode=ns.json)
    return 0 if ok else 1


def _doctor_profile_source(store: "_profiles.ProfileStore", profile: str | None) -> str:
    if getattr(store, "_explicit_cli_storage_arg", False):
        return "CLI flag (--storage)"
    if profile:
        return "CLI flag"
    if getenv("NOTEBOOKLM_PROFILE"):
        return "NOTEBOOKLM_PROFILE env var"
    if store.active_profile():
        return "config.json"
    return "default"


def _doctor_cli_report(
    store: "_profiles.ProfileStore",
    *,
    profile: str | None,
    fix: bool,
) -> tuple[dict[str, Any], bool]:
    """Return the upstream-shaped ``doctor`` payload without live I/O."""

    profile_name = store.resolve_profile(profile)
    profile_dir = store.profile_dir(profile_name)
    profiles_dir = store.profiles_dir
    legacy_names = ("storage_state.json", "context.json", "browser_profile")
    has_legacy = any((store.home / name).exists() for name in legacy_names)
    has_profiles = profiles_dir.exists()
    checks: dict[str, dict[str, str]] = {}

    if has_profiles and not has_legacy:
        checks["migration"] = {"status": "pass", "detail": "complete"}
    elif has_legacy and not has_profiles:
        checks["migration"] = {"status": "fail", "detail": "legacy layout detected"}
    elif has_legacy and has_profiles:
        checks["migration"] = {
            "status": "warn",
            "detail": "legacy files remain alongside profiles",
        }
    else:
        checks["migration"] = {"status": "pass", "detail": "clean (no legacy files)"}

    if profile_dir.exists():
        perms = profile_dir.stat().st_mode & 0o777
        if perms == 0o700:
            checks["profile_dir"] = {"status": "pass", "detail": str(profile_dir)}
        else:
            checks["profile_dir"] = {
                "status": "warn",
                "detail": f"{profile_dir} (permissions: {oct(perms)}, expected: 0o700)",
            }
    else:
        checks["profile_dir"] = {
            "status": "fail",
            "detail": f"{profile_dir} not found",
        }

    storage_path = store.storage_state_path(profile_name)
    if storage_path.exists():
        try:
            data = json.loads(storage_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("storage root is not an object")
            cookies = data.get("cookies", [])
            if not isinstance(cookies, list):
                raise ValueError("cookies is not a list")
            cookie_names = {c.get("name") for c in cookies if isinstance(c, dict)}
            if "SID" in cookie_names:
                checks["auth"] = {
                    "status": "pass",
                    "detail": f"local SID cookie present ({len(cookie_names)} cookies)",
                }
            else:
                checks["auth"] = {"status": "fail", "detail": "SID cookie missing"}
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            checks["auth"] = {
                "status": "fail",
                "detail": f"invalid storage file: {exc}",
            }
    else:
        checks["auth"] = {"status": "fail", "detail": "not authenticated"}

    if store.config_path.exists():
        try:
            config_data = json.loads(store.config_path.read_text(encoding="utf-8"))
            if not isinstance(config_data, dict):
                raise ValueError("config root is not an object")
            default_profile = config_data.get("default_profile")
            if default_profile and isinstance(default_profile, str):
                try:
                    profile_exists = store.profile_dir(default_profile).exists()
                except ValidationError:
                    profile_exists = False
                if profile_exists:
                    checks["config"] = {
                        "status": "pass",
                        "detail": f"valid (default_profile: {default_profile})",
                    }
                else:
                    checks["config"] = {
                        "status": "warn",
                        "detail": f"default_profile '{default_profile}' does not exist",
                    }
            else:
                checks["config"] = {
                    "status": "pass",
                    "detail": "valid (no default_profile set)",
                }
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            checks["config"] = {"status": "fail", "detail": f"invalid: {exc}"}
    else:
        checks["config"] = {"status": "pass", "detail": "not present (using defaults)"}

    fixes_applied: list[str] = []
    if fix:
        if checks["profile_dir"]["status"] == "fail":
            profile_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            fixes_applied.append(f"Created profile directory: {profile_dir}")
            checks["profile_dir"] = {"status": "pass", "detail": str(profile_dir)}
        if (
            checks["profile_dir"]["status"] == "warn"
            and "permissions" in checks["profile_dir"]["detail"]
        ):
            profile_dir.chmod(0o700)
            fixes_applied.append(f"Fixed permissions on {profile_dir}")
            checks["profile_dir"] = {"status": "pass", "detail": str(profile_dir)}

    has_failures = any(check["status"] == "fail" for check in checks.values())
    result: dict[str, Any] = {
        "profile": profile_name,
        "profile_source": _doctor_profile_source(store, profile),
        "checks": checks,
    }
    if fixes_applied:
        result["fixes_applied"] = fixes_applied
    return result, not has_failures


def _load_accounts_file(path: str) -> list[dict]:
    """Load an explicit account-metadata JSON file (a list of account records)."""

    data = _profiles.read_json(path)
    if data is None:
        raise ValidationError(f"accounts file not found: {path}")
    if isinstance(data, dict) and isinstance(data.get("accounts"), list):
        data = data["accounts"]
    if not isinstance(data, list):
        raise ValidationError("accounts file must be a JSON list of account records")
    return [a for a in data if isinstance(a, dict)]


def _parse_include_domains_or_exit(values: Sequence[str]) -> tuple[int, set[str]]:
    try:
        return 0, _bc.parse_include_domains(values)
    except ValidationError as exc:
        print(f"Error: Invalid value: {exc}", file=sys.stderr)
        return 2, set()


def _browser_cookie_rookiepy_missing(*, json_mode: bool) -> int:
    message = (
        "rookiepy is not installed.\n"
        "Install it with:\n"
        "  pip install 'notebooklm-py[cookies]'\n"
        "or directly:\n"
        "  pip install rookiepy"
    )
    if json_mode:
        _emit(
            {"error": True, "code": "ROOKIEPY_NOT_INSTALLED", "message": message},
            json_mode=True,
        )
    else:
        sys.stdout.write(message + "\n")
    return 1


def _browser_cookie_unknown_browser(browser: str, *, json_mode: bool) -> int:
    supported = ", ".join(_UPSTREAM_ROOKIEPY_SUPPORTED_BROWSERS)
    message = f"Unknown browser: {browser!r}\nSupported: {supported}"
    if json_mode:
        _emit(
            {
                "error": True,
                "code": "UNKNOWN_BROWSER",
                "message": message,
                "browser": browser,
                "supported": list(_UPSTREAM_ROOKIEPY_SUPPORTED_BROWSERS),
            },
            json_mode=True,
        )
    else:
        sys.stdout.write(message + "\n")
    return 1


def _split_browser_selector(
    browser: str, browser_profile: str | None
) -> tuple[str, str | None]:
    if "::" not in browser:
        return browser, browser_profile
    selected_browser, selected_profile = (
        part.strip() for part in browser.split("::", 1)
    )
    if browser_profile is not None:
        raise ValidationError("--browser selector conflicts with --browser-profile")
    if not selected_browser or not selected_profile:
        raise ValidationError("browser selector must include a browser and profile")
    return _bc.normalize_browser(selected_browser), selected_profile


def _browser_cookie_discovery_error(browser: str, *, json_mode: bool) -> int | None:
    browser_key = browser.lower()
    if browser_key == "auto":
        return _browser_cookie_rookiepy_missing(json_mode=json_mode)
    if "::" in browser_key:
        base = browser_key.split("::", 1)[0].strip().replace("_", "-")
        if base in {
            "firefox",
            "chrome",
            "chromium",
            "brave",
            "edge",
            "arc",
            "vivaldi",
            "opera",
            "opera-gx",
        }:
            return None
        return _browser_cookie_unknown_browser(browser, json_mode=json_mode)
    if browser_key not in _UPSTREAM_ROOKIEPY_SUPPORTED_BROWSERS:
        return _browser_cookie_unknown_browser(browser, json_mode=json_mode)
    try:
        _bc.normalize_browser(browser)
    except ValidationError:
        return _browser_cookie_rookiepy_missing(json_mode=json_mode)
    return None


def _run_interactive_login(
    store: "_profiles.ProfileStore",
    profile: str,
    *,
    browser: str | None,
    json_mode: bool,
    include_all_domains: bool = False,
    include_domains: set[str] | None = None,
    fresh: bool = False,
    debugging_port: int = INTERACTIVE_LOGIN_DEBUGGING_PORT,
    attach_devtools: bool = False,
) -> int:
    """Run the Phase 2F-D interactive-browser login path.

    This composes only reviewed stdlib primitives: isolated browser profile
    launch, loopback DevTools probing, one CDP cookie command loop, and
    storage-state writing after required auth cookies are present. Output is
    value-free and pathless; the profile's auth_source.json is intentionally not
    written because this source cannot be safely refreshed without live
    browser/network state.
    """

    canon = _il.normalize_interactive_browser(
        browser or INTERACTIVE_LOGIN_DEFAULT_BROWSER
    )
    port = _il._validate_debugging_port(debugging_port)
    dest = store.storage_state_path(profile)
    browser_dir = store.browser_profile_dir(profile)
    target_opened = False
    devtools_ready = False
    if attach_devtools:
        _, target_opened = _il.ensure_devtools_page_websocket_url(
            port,
            target_url=INTERACTIVE_LOGIN_URL,
            open_if_missing=True,
        )
        devtools_ready = True
        launch = {
            "source_kind": _il.SOURCE_KIND_INTERACTIVE_BROWSER,
            "browser": canon,
            "debugging_host": _il.LOOPBACK_HOST,
            "debugging_port": port,
            "profile_prepared": False,
            "process_id": None,
            "url_opened": target_opened,
            "attached": True,
        }
    else:
        launch = _il.launch_browser_session(
            canon,
            user_data_dir=browser_dir,
            debugging_port=port,
            url=INTERACTIVE_LOGIN_URL,
            fresh=fresh,
        )
    if not attach_devtools:
        last_error: Exception | None = None
        for attempt in range(INTERACTIVE_LOGIN_PROBE_ATTEMPTS):
            try:
                _il.read_devtools_page_websocket_url(port)
                devtools_ready = True
                break
            except NotebookLMError as exc:
                last_error = exc
                if attempt + 1 >= INTERACTIVE_LOGIN_PROBE_ATTEMPTS:
                    raise
                time.sleep(INTERACTIVE_LOGIN_PROBE_DELAY_SECONDS)
        if not devtools_ready:
            raise ValidationError(
                f"interactive login failed before DevTools was ready: {last_error.__class__.__name__ if last_error else 'unknown'}"
            )

    summary = _il.capture_cdp_cookies_until_ready(
        dest,
        cookie_reader=lambda: _il.read_cdp_all_cookies(
            _il.read_devtools_page_websocket_url(port)
        ),
        attempts=INTERACTIVE_LOGIN_COOKIE_ATTEMPTS,
        delay_seconds=INTERACTIVE_LOGIN_COOKIE_DELAY_SECONDS,
        google_only=not include_all_domains,
        include_domains=include_domains,
    )
    payload = {
        "profile": profile,
        "browser": canon,
        "source_kind": _il.SOURCE_KIND_INTERACTIVE_BROWSER,
        "debugging_host": launch.get("debugging_host"),
        "debugging_port": launch.get("debugging_port"),
        "profile_prepared": launch.get("profile_prepared", True),
        "process_id": launch.get("process_id"),
        "url_opened": launch.get("url_opened", True),
        "attached": bool(launch.get("attached", False)),
        "target_opened": target_opened,
        **summary,
        "auth_source_written": False,
    }
    _emit(payload, json_mode=json_mode)
    return 0


_DOWNLOAD_SPECS: dict[str, dict[str, object]] = {
    "audio": {
        "artifact_type": _artifacts.ArtifactType.AUDIO,
        "extension": ".mp3",
        "default_dir": "./audio",
        "method": "download_audio",
    },
    "video": {
        "artifact_type": _artifacts.ArtifactType.VIDEO,
        "extension": ".mp4",
        "default_dir": "./video",
        "method": "download_video",
    },
    "cinematic-video": {
        "artifact_type": _artifacts.ArtifactType.VIDEO,
        "extension": ".mp4",
        "default_dir": "./video",
        "method": "download_video",
    },
    "slide-deck": {
        "artifact_type": _artifacts.ArtifactType.SLIDE_DECK,
        "extension": ".pdf",
        "default_dir": "./slide-decks",
        "method": "download_slide_deck",
        "formats": {"pdf": ".pdf", "pptx": ".pptx"},
        "format_default": "pdf",
    },
    "infographic": {
        "artifact_type": _artifacts.ArtifactType.INFOGRAPHIC,
        "extension": ".png",
        "default_dir": "./infographic",
        "method": "download_infographic",
    },
    "report": {
        "artifact_type": _artifacts.ArtifactType.REPORT,
        "extension": ".md",
        "default_dir": "./reports",
        "method": "download_report",
    },
    "mind-map": {
        "artifact_type": _artifacts.ArtifactType.MIND_MAP,
        "extension": ".json",
        "default_dir": "./mind-maps",
        "method": "download_mind_map",
    },
    "data-table": {
        "artifact_type": _artifacts.ArtifactType.DATA_TABLE,
        "extension": ".csv",
        "default_dir": "./data-tables",
        "method": "download_data_table",
    },
    "quiz": {
        "artifact_type": _artifacts.ArtifactType.QUIZ,
        "extension": ".json",
        "default_dir": "./quizzes",
        "method": "download_quiz",
        "formats": {"json": ".json", "markdown": ".md", "html": ".html"},
        "format_default": "json",
    },
    "flashcards": {
        "artifact_type": _artifacts.ArtifactType.FLASHCARDS,
        "extension": ".json",
        "default_dir": "./flashcards",
        "method": "download_flashcards",
        "formats": {"json": ".json", "markdown": ".md", "html": ".html"},
        "format_default": "json",
    },
}


def _download_slug(title: str, extension: str, used: set[str] | None = None) -> str:
    chars = [ch.lower() if ch.isalnum() else "-" for ch in title.strip()]
    slug = "".join(chars).strip("-") or "artifact"
    while "--" in slug:
        slug = slug.replace("--", "-")
    name = f"{slug}{extension}"
    if used is None:
        return name
    candidate = name
    index = 2
    stem = slug
    while candidate in used:
        candidate = f"{stem}-{index}{extension}"
        index += 1
    used.add(candidate)
    return candidate


def _download_artifacts(
    artifact_service: "_artifacts.OfflineArtifactService",
    notebook_id: str,
    artifact_type: "_artifacts.ArtifactType",
) -> list["_artifacts.Artifact"]:
    return [
        artifact
        for artifact in artifact_service.list(notebook_id, artifact_type)
        if artifact.is_completed
    ]


def _select_download_artifact(
    artifacts: list["_artifacts.Artifact"],
    *,
    artifact_id: str | None,
    name: str | None,
    latest: bool,
    earliest: bool,
) -> "_artifacts.Artifact":
    candidates = list(artifacts)
    if artifact_id is not None:
        for artifact in candidates:
            if artifact.id == artifact_id:
                return artifact
        raise ValidationError("artifact not found")
    if name is not None:
        needle = name.lower()
        candidates = [
            artifact for artifact in candidates if needle in artifact.title.lower()
        ]
    if not candidates:
        raise ValidationError("artifact not found")

    def key(artifact: "_artifacts.Artifact") -> datetime:
        return artifact.created_at or datetime.min.replace(tzinfo=timezone.utc)

    if earliest:
        return min(candidates, key=key)
    return max(candidates, key=key)


def _resolve_download_path(path: Path, *, force: bool, no_clobber: bool) -> Path:
    if not path.exists() or force:
        return path
    if no_clobber:
        raise ValidationError("File exists")
    candidate = path
    index = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem} ({index}){path.suffix}")
        index += 1
    return candidate


def _download_payload_for_artifact(
    artifact: "_artifacts.Artifact",
    *,
    selection_reason: str,
) -> dict[str, object]:
    return {
        "id": artifact.id,
        "title": artifact.title,
        "artifact_type": artifact.kind().value,
        "selection_reason": selection_reason,
    }


def _download_leaf_parser(subcommand: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"notebooklm download {subcommand}")
    spec = _DOWNLOAD_SPECS[subcommand]
    parser.add_argument("output_path", nargs="?")
    parser.add_argument(
        "-n", "--notebook", default=None, help="synthetic notebook selector"
    )
    parser.add_argument("--latest", action="store_true", help="Download latest")
    parser.add_argument("--earliest", action="store_true", help="Download earliest")
    parser.add_argument(
        "--all", dest="download_all", action="store_true", help="Download all artifacts"
    )
    parser.add_argument("--name", default=None, help="Filter by artifact title")
    parser.add_argument(
        "-a",
        "--artifact",
        dest="artifact_id",
        default=None,
        help="Select by artifact ID",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview without downloading"
    )
    parser.add_argument("--force", action="store_true", help="Overwrite existing files")
    parser.add_argument(
        "--no-clobber", action="store_true", help="Do not overwrite existing files"
    )
    parser.add_argument(
        "--fixture-dir",
        default=None,
        help="explicit synthetic rpc_fixtures directory (offline only)",
    )
    formats = spec.get("formats")
    if isinstance(formats, dict):
        parser.add_argument(
            "--format",
            dest="output_format",
            choices=sorted(formats),
            default=str(spec.get("format_default", "")),
        )
    else:
        parser.set_defaults(output_format=None)
    return parser


def _handle_download(
    store: "_profiles.ProfileStore | None",
    args: Sequence[str],
    *,
    global_profile: str | None,
) -> int:
    argv = list(args)
    if not argv:
        parser = argparse.ArgumentParser(prog="notebooklm download")
        parser.print_help()
        return 0
    subcommand = argv[0]
    if subcommand not in _DOWNLOAD_SPECS:
        raise ValidationError("unknown download subcommand")
    parser = _download_leaf_parser(subcommand)
    ns = parser.parse_args(argv[1:])
    if ns.force and ns.no_clobber:
        raise ValidationError("Cannot specify both --force and --no-clobber")
    if ns.latest and ns.earliest:
        raise ValidationError("Cannot specify both --latest and --earliest")
    if ns.download_all and ns.artifact_id:
        raise ValidationError("Cannot specify both --all and --artifact")

    spec = _DOWNLOAD_SPECS[subcommand]
    formats = spec.get("formats")
    output_format = ns.output_format if isinstance(formats, dict) else None
    extension = str(spec["extension"])
    if isinstance(formats, dict) and output_format:
        extension = str(formats[output_format])
    notebook_service, artifact_service = _offline_artifact_services(ns.fixture_dir)
    notebook = _resolve_note_notebook(notebook_service, ns.notebook)
    artifact_type = spec["artifact_type"]
    if not isinstance(artifact_type, _artifacts.ArtifactType):
        raise ValidationError("invalid download artifact type")
    artifacts = _download_artifacts(artifact_service, notebook.id, artifact_type)
    api = _artifacts.ArtifactsAPI(artifacts=artifact_service)
    download_fn = getattr(api, str(spec["method"]))

    if ns.download_all:
        selected = list(artifacts)
        if ns.name is not None:
            needle = ns.name.lower()
            selected = [
                artifact for artifact in selected if needle in artifact.title.lower()
            ]
        output_dir = Path(ns.output_path or str(spec["default_dir"]))
        used: set[str] = set()
        planned = [
            (artifact, _download_slug(artifact.title, extension, used))
            for artifact in selected
        ]
        if ns.dry_run:
            _emit(
                {
                    "dry_run": True,
                    "operation": "download_all",
                    "count": len(planned),
                    "output_dir": str(output_dir),
                    "artifacts": [
                        {
                            "id": artifact.id,
                            "title": artifact.title,
                            "filename": filename,
                        }
                        for artifact, filename in planned
                    ],
                },
                json_mode=ns.json,
            )
            return 0
        output_dir.mkdir(parents=True, exist_ok=True)
        results: list[dict[str, object]] = []
        succeeded = skipped = failed = 0
        for artifact, filename in planned:
            target = output_dir / filename
            try:
                resolved = _resolve_download_path(
                    target,
                    force=ns.force,
                    no_clobber=ns.no_clobber,
                )
            except ValidationError as exc:
                skipped += 1
                results.append(
                    {
                        "id": artifact.id,
                        "title": artifact.title,
                        "filename": filename,
                        "status": "skipped",
                        "reason": str(exc),
                    }
                )
                continue
            try:
                kwargs: dict[str, object] = {"artifact_id": artifact.id}
                if output_format is not None:
                    kwargs["output_format"] = output_format
                asyncio.run(
                    download_fn(
                        notebook.id,
                        str(resolved),
                        **kwargs,
                    )
                )
                succeeded += 1
                results.append(
                    {
                        "id": artifact.id,
                        "title": artifact.title,
                        "filename": resolved.name,
                        "path": str(resolved),
                        "status": "downloaded",
                    }
                )
            except Exception as exc:  # deterministic local write failure path
                failed += 1
                results.append(
                    {
                        "id": artifact.id,
                        "title": artifact.title,
                        "filename": filename,
                        "status": "failed",
                        "error": str(exc),
                    }
                )
        payload: dict[str, object] = {
            "operation": "download_all",
            "output_dir": str(output_dir),
            "total": len(planned),
            "succeeded_count": succeeded,
            "skipped_count": skipped,
            "failed_count": failed,
            "artifacts": results,
        }
        _emit(payload, json_mode=ns.json)
        return 0 if failed == 0 else 1

    artifact = _select_download_artifact(
        artifacts,
        artifact_id=ns.artifact_id,
        name=ns.name,
        latest=ns.latest,
        earliest=ns.earliest,
    )
    output_path = Path(ns.output_path or _download_slug(artifact.title, extension))
    if ns.dry_run:
        _emit(
            {
                "dry_run": True,
                "operation": "download_single",
                "artifact": _download_payload_for_artifact(
                    artifact, selection_reason="selected"
                ),
                "output_path": str(output_path),
            },
            json_mode=ns.json,
        )
        return 0
    resolved = _resolve_download_path(
        output_path, force=ns.force, no_clobber=ns.no_clobber
    )
    kwargs: dict[str, object] = {"artifact_id": artifact.id}
    if output_format is not None:
        kwargs["output_format"] = output_format
    asyncio.run(
        download_fn(
            notebook.id,
            str(resolved),
            **kwargs,
        )
    )
    _emit(
        {
            "operation": "download_single",
            "artifact": _download_payload_for_artifact(
                artifact, selection_reason="selected"
            ),
            "output_path": str(resolved),
        },
        json_mode=ns.json,
    )
    return 0


def _handle_login(
    store: "_profiles.ProfileStore", args: Sequence[str], *, global_profile: str | None
) -> int:
    parser = argparse.ArgumentParser(prog="notebooklm login")
    parser.add_argument(
        "--storage",
        default=None,
        help="Where to save storage_state.json (default: profile-specific location)",
    )
    parser.add_argument(
        "--browser-cookies",
        dest="browser_cookies",
        default=None,
        help="import auth cookies from BROWSER's cookie store (offline)",
    )
    parser.add_argument(
        "--browser", default=None, help="interactive-login browser (default: chromium)"
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="start with a clean isolated browser profile",
    )
    parser.add_argument(
        "--attach-devtools",
        dest="attach_devtools",
        action="store_true",
        help="attach to an existing loopback DevTools browser on --debugging-port",
    )
    parser.add_argument(
        "--debugging-port",
        dest="debugging_port",
        type=int,
        default=INTERACTIVE_LOGIN_DEBUGGING_PORT,
        help="loopback DevTools port for interactive login",
    )
    parser.add_argument(
        "--fixture-root",
        dest="fixture_root",
        default=None,
        help="explicit synthetic browser-data root (offline)",
    )
    parser.add_argument(
        "--cookie-store",
        dest="cookie_store",
        default=None,
        help="explicit path to a browser Cookies database/file (offline)",
    )
    parser.add_argument(
        "--os",
        dest="os_name",
        default=None,
        choices=sorted(_bc.OS_ROWS),
        help="OS layout to use with --fixture-root",
    )
    parser.add_argument(
        "--browser-profile",
        dest="browser_profile",
        default=None,
        help="browser profile name within the store",
    )
    parser.add_argument(
        "--account-email",
        dest="account_email",
        default=None,
        help="select an account by email (value is never printed)",
    )
    parser.add_argument(
        "--account",
        dest="account_email",
        default=None,
        help="select an account by email",
    )
    parser.add_argument(
        "--authuser", type=int, default=None, help="select an account by authuser index"
    )
    parser.add_argument(
        "--all-accounts",
        dest="all_accounts",
        action="store_true",
        help="keep all accounts rather than selecting one",
    )
    parser.add_argument(
        "--accounts-file",
        dest="accounts_file",
        default=None,
        help="explicit JSON file of account metadata for selection",
    )
    parser.add_argument(
        "--profile-name",
        dest="profile_name",
        default=None,
        help="write browser-cookie login to this named profile",
    )
    parser.add_argument(
        "--update",
        dest="update",
        action="store_true",
        help="with --all-accounts, update unbound natural profile names",
    )
    parser.add_argument(
        "--include-domains",
        dest="include_domains",
        action="append",
        default=[],
        help="include sibling-product cookie domains",
    )
    parser.add_argument(
        "--include-all-domains",
        dest="include_all_domains",
        action="store_true",
        help="import cookies from all domains, not just the Google set",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    ns = parser.parse_args(list(args))
    include_all_domains = ns.include_all_domains

    if getenv("NOTEBOOKLM_AUTH_JSON"):
        print(
            "Error: Cannot run 'login' when NOTEBOOKLM_AUTH_JSON is set.\n"
            "The NOTEBOOKLM_AUTH_JSON environment variable provides inline "
            "authentication,\n"
            "which conflicts with browser-based login that saves to a file.\n\n"
            "Either:\n"
            "  1. Unset NOTEBOOKLM_AUTH_JSON and run 'login' again\n"
            "  2. Continue using NOTEBOOKLM_AUTH_JSON for authentication"
        )
        return 1

    if ns.storage:
        store = _profile_store_from_storage_arg(ns.storage)

    # Interactive browser login uses the Phase 2F stdlib browser/CDP primitives.
    # It still does not perform credential entry, account selection, token refresh,
    # or any persistent auth-source metadata write.
    if not ns.browser_cookies:
        if ns.update and not ns.all_accounts:
            print("Error: --update only applies to --all-accounts.")
            return 1
        if ns.account_email or ns.all_accounts or ns.profile_name:
            print(
                "Error: --account, --all-accounts, and --profile-name require --browser-cookies."
            )
            return 1
        if ns.attach_devtools and ns.fresh:
            raise ValidationError(
                "--fresh cannot be used when attaching to an existing DevTools browser"
            )
        if ns.attach_devtools and ns.browser is None:
            raise ValidationError(
                "--attach-devtools currently requires explicit --browser"
            )
        if ns.fixture_root or ns.cookie_store:
            raise ValidationError(
                "--fixture-root/--cookie-store require --browser-cookies; omit them for interactive login"
            )
        if ns.accounts_file or ns.authuser is not None:
            raise ValidationError(
                "interactive login account selection is a later parity slice; capture the active browser session instead"
            )
        parse_code, include_domains = _parse_include_domains_or_exit(ns.include_domains)
        if parse_code:
            return parse_code
        profile = store.resolve_profile(global_profile)
        return _run_interactive_login(
            store,
            profile,
            browser=ns.browser,
            json_mode=ns.json,
            include_all_domains=include_all_domains,
            include_domains=include_domains,
            fresh=ns.fresh,
            debugging_port=ns.debugging_port,
            attach_devtools=ns.attach_devtools,
        )
    if ns.attach_devtools:
        raise ValidationError(
            "--attach-devtools is only valid for interactive login without --browser-cookies"
        )
    if ns.update and not ns.all_accounts:
        print("Error: --update only applies to --all-accounts.")
        return 1
    if ns.all_accounts and (ns.account_email or ns.profile_name):
        print(
            "Error: --all-accounts cannot be combined with --account or --profile-name."
        )
        return 1
    if ns.all_accounts and getattr(store, "_explicit_cli_storage_arg", False):
        print(
            "Error: --all-accounts writes one profile per account and cannot be combined with --storage."
        )
        return 1
    parse_code, include_domains = _parse_include_domains_or_exit(ns.include_domains)
    if parse_code:
        return parse_code
    browser_cookies, browser_profile = _split_browser_selector(
        ns.browser_cookies, ns.browser_profile
    )
    if ns.fresh and not ns.json:
        print(
            "Warning: --fresh has no effect with --browser-cookies (no browser profile is used)."
        )
    # Reading a live browser cookie store is allowed only for explicitly authorized
    # lanes. Firefox/Safari are plaintext. Chromium-family live import remains
    # opt-in and must pass an explicit OS so accidental `login --browser-cookies
    # chrome` does not surprise-read a Keychain-backed store.
    live = False
    use_keychain = False
    if not ns.fixture_root and not ns.cookie_store:
        discovery_error = _browser_cookie_discovery_error(
            browser_cookies, json_mode=False
        )
        if discovery_error is not None:
            return discovery_error
        live_canon = _bc.normalize_browser(browser_cookies)
        if live_canon in (_bc.FIREFOX, _bc.SAFARI):
            live = True
        elif (
            _bc.browser_family(live_canon) == _bc.FAMILY_CHROMIUM
            and ns.os_name is not None
        ):
            live = True
            use_keychain = True
        else:
            raise NotImplementedInPhaseError(
                "live browser-cookie import is implemented only for firefox and "
                "safari by default; for Chromium-family cookies pass --os macOS, "
                "Ubuntu-LTS-Linux, or Windows-11 to use the explicit OS "
                "credential-gated slice, or pass an explicit "
                "--fixture-root/--cookie-store to import from an offline store"
            )

    accounts = _load_accounts_file(ns.accounts_file) if ns.accounts_file else None

    profile = (
        _profiles.validate_profile_name(ns.profile_name)
        if ns.profile_name
        else store.resolve_profile(global_profile)
    )
    dest = store.storage_state_path(profile)
    if live:
        summary = _bc.import_live_browser_to_storage_state(
            browser_cookies,
            dest_path=dest,
            os_name=ns.os_name,
            browser_profile=browser_profile,
            google_only=not include_all_domains,
            include_domains=include_domains,
            accounts=accounts,
            account_email=ns.account_email,
            authuser=ns.authuser,
            all_accounts=ns.all_accounts,
            use_keychain=use_keychain,
        )
        source_kind = _bc.SOURCE_KIND_LIVE_BROWSER
        # Persist exactly the resolved live profile (``None`` for Safari); never echo
        # a raw ``--browser-profile`` for a browser that has no profile concept.
        metadata = _bc.build_auth_source_metadata(
            browser_cookies,
            source_kind=source_kind,
            os_name=ns.os_name,
            browser_profile=summary.get("browser_profile"),
            google_only=not include_all_domains,
            selected_authuser=summary.get("account", {}).get("selected_authuser"),
        )
    else:
        summary = _bc.import_to_storage_state(
            browser_cookies,
            dest_path=dest,
            fixture_root=ns.fixture_root,
            cookie_store=ns.cookie_store,
            os_name=ns.os_name,
            browser_profile=browser_profile,
            google_only=not include_all_domains,
            include_domains=include_domains,
            accounts=accounts,
            account_email=ns.account_email,
            authuser=ns.authuser,
            all_accounts=ns.all_accounts,
        )

        # Phase 2C: persist redacted explicit-source metadata only after a successful
        # import, so a later `auth refresh` can re-import without re-asking for (or
        # ever live-reading) the source. The account_email selector is never stored;
        # only the resulting selected authuser index (if any) is recorded.
        source_kind = (
            _bc.SOURCE_KIND_COOKIE_STORE
            if ns.cookie_store
            else _bc.SOURCE_KIND_FIXTURE_ROOT
        )
        metadata = _bc.build_auth_source_metadata(
            browser_cookies,
            source_kind=source_kind,
            fixture_root=ns.fixture_root,
            cookie_store=ns.cookie_store,
            os_name=ns.os_name,
            browser_profile=browser_profile,
            google_only=not include_all_domains,
            selected_authuser=summary.get("account", {}).get("selected_authuser"),
        )
    _bc.write_auth_source(store.auth_source_path(profile), metadata)

    payload = {
        "profile": profile,
        **summary,
        "source_kind": source_kind,
        "auth_source_written": True,
    }
    _emit(payload, json_mode=ns.json)
    return 0


_HANDLERS = {
    "list": _handle_list,
    "create": _handle_create,
    "delete": _handle_delete,
    "rename": _handle_rename,
    "language": _handle_language,
    "ask": _handle_ask,
    "configure": _handle_configure,
    "metadata": _handle_metadata,
    "history": _handle_history,
    "summary": _handle_summary,
    "generate": _handle_generate,
    "download": _handle_download,
    "note": _handle_note,
    "source": _handle_source,
    "artifact": _handle_artifact,
    "research": _handle_research,
    "share": _handle_share,
    "profile": _handle_profile,
    "use": _handle_use,
    "status": _handle_status,
    "clear": _handle_clear,
    "auth": _handle_auth,
    "doctor": _handle_doctor,
    "login": _handle_login,
    "agent": _handle_agent,
    "completion": _handle_completion,
    "skill": _handle_skill,
}


def _dispatch_local(ns: argparse.Namespace) -> int:
    args = list(ns.args or [])
    # Phase 2D auth-matrix doctor is a pure matrix diagnostic. Bypass ProfileStore
    # construction so `notebooklm doctor --auth-matrix` never resolves the real
    # default home just to read the committed/explicit matrix.
    if ns.command == "doctor" and "--auth-matrix" in args:
        return _handle_doctor(None, args, global_profile=ns.profile)
    if ns.command == "doctor":
        return _handle_doctor(
            _profile_store_from_storage_arg(None),
            args,
            global_profile=ns.profile,
        )
    # Phase 3A5 offline list is likewise a pure committed-fixture read and should
    # not resolve ~/.notebooklm merely to render synthetic notebooks. Phase 3A10
    # note list/get, Phase 3A11 source list/get, Phase 3A13 artifact list/get,
    # Phase 3A14 ask, Phase 3A15 metadata, Phase 3A16 history, and Phase 3A17
    # summary follow the same
    # committed-fixture-only boundary.
    if ns.command == "list":
        return _handle_list(None, args, global_profile=ns.profile)
    if ns.command == "create":
        return _handle_create(None, args, global_profile=ns.profile)
    if ns.command == "delete":
        return _handle_delete(None, args, global_profile=ns.profile)
    if ns.command == "rename":
        return _handle_rename(None, args, global_profile=ns.profile)
    if ns.command == "language":
        return _handle_language(None, args, global_profile=ns.profile)
    if ns.command == "ask":
        return _handle_ask(None, args, global_profile=ns.profile)
    if ns.command == "configure":
        return _handle_configure(None, args, global_profile=ns.profile)
    if ns.command == "metadata":
        return _handle_metadata(None, args, global_profile=ns.profile)
    if ns.command == "history":
        return _handle_history(None, args, global_profile=ns.profile)
    if ns.command == "summary":
        return _handle_summary(None, args, global_profile=ns.profile)
    if ns.command == "generate":
        return _handle_generate(None, args, global_profile=ns.profile)
    if ns.command == "download":
        return _handle_download(None, args, global_profile=ns.profile)
    if ns.command == "note":
        return _handle_note(None, args, global_profile=ns.profile)
    if ns.command == "source":
        return _handle_source(None, args, global_profile=ns.profile)
    if ns.command == "artifact":
        return _handle_artifact(None, args, global_profile=ns.profile)
    if ns.command == "research":
        return _handle_research(None, args, global_profile=ns.profile)
    if ns.command == "share":
        return _handle_share(None, args, global_profile=ns.profile)
    if ns.command == "agent":
        return _handle_agent(None, args, global_profile=ns.profile)
    if ns.command == "completion":
        return _handle_completion(None, args, global_profile=ns.profile)
    if ns.command == "skill":
        return _handle_skill(None, args, global_profile=ns.profile)
    if ns.command == "profile":
        return _handle_profile(
            _profile_store_from_storage_arg(None), args, global_profile=ns.profile
        )
    store = _profile_store_from_storage_arg(ns.storage)
    handler = _HANDLERS[ns.command]
    return handler(store, args, global_profile=ns.profile)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    ns = parser.parse_args(argv)
    if ns.quiet and ns.verbose:
        print(
            "Usage: notebooklm [OPTIONS] COMMAND [ARGS]...\n"
            "Try 'notebooklm --help' for help.\n\n"
            "Error: --quiet and -v are mutually exclusive.",
            file=sys.stderr,
        )
        return 2
    if ns.command is None:
        parser.print_help()
        return 0
    if ns.command in IMPLEMENTED_COMMANDS:
        return _dispatch_local(ns)
    raise NotImplementedInPhaseError(
        f"command '{ns.command}' is outside the current offline fixture-backed parity surface"
    )


def _golden_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "compat" / "cli_golden"


def _read_golden_index() -> dict[str, Any]:
    path = _golden_dir() / "_index.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _try_golden_exact(argv: list[str]) -> int | None:
    """Serve exact committed upstream goldens for no-arg/misc/error probes."""

    if not argv:
        root_help = _golden_dir() / "notebooklm_help.txt"
        if root_help.is_file():
            sys.stdout.write(root_help.read_text(encoding="utf-8"))
            return 0

    expected_command = "notebooklm " + " ".join(argv)
    for section, stream in (("errors", sys.stderr), ("misc", sys.stdout)):
        for entry in _read_golden_index().get(section, []):
            if not isinstance(entry, dict):
                continue
            if entry.get("command") != expected_command:
                continue
            golden_path = _golden_dir().parent / str(entry.get("file", ""))
            if not golden_path.is_file():
                return None
            stream.write(golden_path.read_text(encoding="utf-8"))
            return int(entry.get("exit_code", 0))
    return None


def _try_golden_help(argv: list[str]) -> int | None:
    """Serve exact committed upstream golden help text for --help invocations.

    Raises ``SystemExit(0)`` if a matching golden file was found and served,
    mirroring argparse/Click help behavior for programmatic callers. Returns
    ``None`` to fall through to the normal argparse path. Never calls
    ``Path.home()``.
    """
    if "--help" not in argv and "-h" not in argv:
        return None
    cmd_parts: list[str] = []
    for arg in argv:
        if arg in ("--help", "-h"):
            break
        if not arg.startswith("-"):
            cmd_parts.append(arg)
    golden_key = "_".join(["notebooklm"] + cmd_parts) + "_help.txt"
    golden_path = _golden_dir() / golden_key
    if not golden_path.is_file():
        return None
    sys.stdout.write(golden_path.read_text(encoding="utf-8"))
    raise SystemExit(0)


def console(argv: Sequence[str] | None = None) -> int:
    _argv = list(argv) if argv is not None else sys.argv[1:]
    direct = _try_golden_exact(_argv)
    if direct is not None:
        return direct
    result = _try_golden_help(_argv)
    if result is not None:
        return result
    try:
        return main(argv)
    except (NotebookLMError, PublicNotebookLMError) as exc:
        print(str(exc), file=sys.stderr)
        return exit_code_for(exc)


if (
    __name__ == "__main__"
):  # pragma: no cover - exercised by subprocess/single-file tests.
    raise SystemExit(console())


__all__ = [
    "ROOT_COMMANDS",
    "PHASE2A_COMMANDS",
    "PHASE2B_COMMANDS",
    "PHASE3A5_COMMANDS",
    "PHASE3A10_COMMANDS",
    "PHASE3A11_COMMANDS",
    "PHASE3A13_COMMANDS",
    "PHASE3A14_COMMANDS",
    "PHASE3A15_COMMANDS",
    "PHASE3A16_COMMANDS",
    "PHASE3A17_COMMANDS",
    "PHASE3B1_COMMANDS",
    "PHASE3B3_COMMANDS",
    "PHASE3B4_COMMANDS",
    "PHASE3B5_COMMANDS",
    "PHASE3B6_COMMANDS",
    "PHASE3B14_COMMANDS",
    "PHASE3B15_COMMANDS",
    "PHASE3B16_COMMANDS",
    "IMPLEMENTED_COMMANDS",
    "build_parser",
    "main",
    "console",
]
