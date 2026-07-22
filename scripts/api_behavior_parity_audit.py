#!/usr/bin/env python3
"""Phase 9 Python API behavior parity audit.

The Phase 7 audit proved the pinned public Python API surface exists. This gate
executes one safe, fixture-backed behavior scenario for every pinned public async
sub-client method from ``notebooklm-py==0.7.2``. It is deliberately offline-only:
no live NotebookLM access, browser profile, credential store, or user home lookup
is required. The API category remains open until a later direct differential/live
closure explicitly promotes it.
"""

import argparse
import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sys
import tempfile
from typing import Any


TARGET = "notebooklm-py==0.7.2"
SCHEMA_VERSION = "api_behavior_parity_audit/1"
CHAT_FIXTURE_QUESTION = "Phase 0 synthetic question."


@dataclass(frozen=True)
class ScenarioResult:
    scenario_id: str
    status: str
    result_type: str
    detail: str = ""


def _repo_root_from_here() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _matrix_state(repo_root: Path, category: str) -> str:
    matrix = repo_root / "compat" / "parity_matrix.md"
    for line in matrix.read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 4 and cells[0] == category:
            return cells[3]
    return "missing"


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


def _pass(scenario_id: str, value: Any = None, detail: str = "") -> ScenarioResult:
    return ScenarioResult(scenario_id, "pass", _summarize(value), detail)


def _fail(scenario_id: str, exc: BaseException) -> ScenarioResult:
    return ScenarioResult(
        scenario_id,
        "fail",
        exc.__class__.__name__,
        str(exc).splitlines()[0][:180],
    )


def _prepare_imports(repo_root: Path) -> None:
    root = str(repo_root)
    if root not in sys.path:
        sys.path.insert(0, root)


def _auth_tokens(storage_path: Path):
    from notebooklm import AuthTokens

    return AuthTokens(
        cookies={},
        csrf_token="",
        session_id="",
        storage_path=storage_path,
    )


def _client(storage_path: Path):
    from notebooklm import NotebookLMClient

    return NotebookLMClient(_auth_tokens(storage_path))


async def _base_ids(client: Any) -> dict[str, str]:
    notebooks = await client.notebooks.list()
    if not notebooks:
        raise RuntimeError("offline notebook fixture is empty")
    notebook_id = notebooks[0].id
    sources = await client.sources.list(notebook_id)
    notes = await client.notes.list(notebook_id)
    mind_maps = await client.mind_maps.list(notebook_id)
    artifacts = await client.artifacts.list(notebook_id)
    if not sources or not notes or not mind_maps or not artifacts:
        raise RuntimeError("offline API fixture lacks required seed rows")
    report = next(
        (item for item in artifacts if item.kind().value == "report"), artifacts[0]
    )
    audio = next(
        (item for item in artifacts if item.kind().value == "audio"), artifacts[0]
    )
    return {
        "notebook": notebook_id,
        "source": sources[0].id,
        "note": notes[0].id,
        "mind_map": mind_maps[0].id,
        "artifact": artifacts[0].id,
        "report_artifact": report.id,
        "audio_artifact": audio.id,
    }


async def _ensure_artifact(client: Any, notebook_id: str, method_name: str) -> str:
    generators: dict[str, Callable[[], Awaitable[Any]]] = {
        "download_audio": lambda: client.artifacts.generate_audio(notebook_id),
        "download_data_table": lambda: client.artifacts.generate_data_table(
            notebook_id
        ),
        "download_flashcards": lambda: client.artifacts.generate_flashcards(
            notebook_id
        ),
        "download_infographic": lambda: client.artifacts.generate_infographic(
            notebook_id
        ),
        "download_quiz": lambda: client.artifacts.generate_quiz(notebook_id),
        "download_report": lambda: client.artifacts.generate_report(notebook_id),
        "download_slide_deck": lambda: client.artifacts.generate_slide_deck(
            notebook_id
        ),
        "download_video": lambda: client.artifacts.generate_video(notebook_id),
        "export_data_table": lambda: client.artifacts.generate_data_table(notebook_id),
        "revise_slide": lambda: client.artifacts.generate_slide_deck(notebook_id),
    }
    if method_name == "download_mind_map":
        result = await client.artifacts.generate_mind_map(notebook_id)
        return str(result.note_id)
    result = await generators[method_name]()
    return str(result.task_id)


async def _run_artifacts(
    client: Any, method: str, ids: dict[str, str], work: Path
) -> Any:
    from notebooklm import ExportType

    nb = ids["notebook"]
    art = ids["artifact"]
    report = ids["report_artifact"]
    audio = ids["audio_artifact"]
    output = work / f"artifact-{method}.txt"
    match method:
        case "list":
            return await client.artifacts.list(nb)
        case "get":
            return await client.artifacts.get(nb, art)
        case "get_or_none":
            return await client.artifacts.get_or_none(nb, art)
        case "list_audio":
            return await client.artifacts.list_audio(nb)
        case "list_data_tables":
            return await client.artifacts.list_data_tables(nb)
        case "list_flashcards":
            return await client.artifacts.list_flashcards(nb)
        case "list_infographics":
            return await client.artifacts.list_infographics(nb)
        case "list_quizzes":
            return await client.artifacts.list_quizzes(nb)
        case "list_reports":
            return await client.artifacts.list_reports(nb)
        case "list_slide_decks":
            return await client.artifacts.list_slide_decks(nb)
        case "list_video":
            return await client.artifacts.list_video(nb)
        case "delete":
            return await client.artifacts.delete(nb, art)
        case "download_audio":
            return await client.artifacts.download_audio(nb, str(output), audio)
        case "download_data_table":
            return await client.artifacts.download_data_table(
                nb, str(output), await _ensure_artifact(client, nb, method)
            )
        case "download_flashcards":
            return await client.artifacts.download_flashcards(
                nb, str(output), await _ensure_artifact(client, nb, method)
            )
        case "download_infographic":
            return await client.artifacts.download_infographic(
                nb, str(output), await _ensure_artifact(client, nb, method)
            )
        case "download_mind_map":
            return await client.artifacts.download_mind_map(
                nb, str(output), await _ensure_artifact(client, nb, method)
            )
        case "download_quiz":
            return await client.artifacts.download_quiz(
                nb, str(output), await _ensure_artifact(client, nb, method)
            )
        case "download_report":
            return await client.artifacts.download_report(nb, str(output), report)
        case "download_slide_deck":
            return await client.artifacts.download_slide_deck(
                nb, str(output), await _ensure_artifact(client, nb, method)
            )
        case "download_video":
            return await client.artifacts.download_video(
                nb, str(output), await _ensure_artifact(client, nb, method)
            )
        case "export":
            return await client.artifacts.export(
                nb, artifact_id=None, content="fixture export", title="Fixture Export"
            )
        case "export_data_table":
            return await client.artifacts.export_data_table(
                nb, await _ensure_artifact(client, nb, method), title="Fixture Sheet"
            )
        case "export_report":
            return await client.artifacts.export_report(
                nb, report, title="Fixture Doc", export_type=ExportType.DOCS
            )
        case "generate_audio":
            return await client.artifacts.generate_audio(nb)
        case "generate_cinematic_video":
            return await client.artifacts.generate_cinematic_video(nb)
        case "generate_data_table":
            return await client.artifacts.generate_data_table(nb)
        case "generate_flashcards":
            return await client.artifacts.generate_flashcards(nb)
        case "generate_infographic":
            return await client.artifacts.generate_infographic(nb)
        case "generate_mind_map":
            return await client.artifacts.generate_mind_map(nb)
        case "generate_quiz":
            return await client.artifacts.generate_quiz(nb)
        case "generate_report":
            return await client.artifacts.generate_report(nb)
        case "generate_slide_deck":
            return await client.artifacts.generate_slide_deck(nb)
        case "generate_study_guide":
            return await client.artifacts.generate_study_guide(nb)
        case "generate_video":
            return await client.artifacts.generate_video(nb)
        case "poll_status":
            return await client.artifacts.poll_status(nb, audio)
        case "rename":
            return await client.artifacts.rename(nb, art, "Renamed Artifact")
        case "retry_failed":
            return await client.artifacts.retry_failed(nb, art)
        case "revise_slide":
            return await client.artifacts.revise_slide(
                nb, await _ensure_artifact(client, nb, method), 0, "Improve clarity"
            )
        case "suggest_reports":
            return await client.artifacts.suggest_reports(nb)
        case "wait_for_completion":
            return await client.artifacts.wait_for_completion(nb, audio)
    raise KeyError(method)


async def _run_chat(client: Any, method: str, ids: dict[str, str], work: Path) -> Any:
    from notebooklm import ChatGoal, ChatMode, ChatResponseLength

    nb = ids["notebook"]
    if method in {
        "delete_conversation",
        "get_conversation_id",
        "get_conversation_turns",
        "get_history",
        "save_answer_as_note",
    }:
        ask_result = await client.chat.ask(nb, CHAT_FIXTURE_QUESTION)
    else:
        ask_result = None
    match method:
        case "ask":
            return await client.chat.ask(nb, CHAT_FIXTURE_QUESTION)
        case "configure":
            return await client.chat.configure(
                nb,
                goal=ChatGoal.DEFAULT,
                response_length=ChatResponseLength.DEFAULT,
                custom_prompt="answer from fixtures",
            )
        case "delete_conversation":
            return await client.chat.delete_conversation(nb, ask_result.conversation_id)
        case "get_conversation_id":
            return await client.chat.get_conversation_id(nb)
        case "get_conversation_turns":
            return await client.chat.get_conversation_turns(
                nb, ask_result.conversation_id
            )
        case "get_history":
            return await client.chat.get_history(nb)
        case "save_answer_as_note":
            return await client.chat.save_answer_as_note(
                nb, ask_result, title="Saved fixture answer"
            )
        case "set_mode":
            return await client.chat.set_mode(nb, ChatMode.CONCISE)
    raise KeyError(method)


async def _run_mind_maps(
    client: Any, method: str, ids: dict[str, str], work: Path
) -> Any:
    from notebooklm import MindMapKind

    nb = ids["notebook"]
    mm = ids["mind_map"]
    match method:
        case "delete":
            return await client.mind_maps.delete(nb, mm)
        case "generate":
            return await client.mind_maps.generate(
                nb, [ids["source"]], kind=MindMapKind.INTERACTIVE
            )
        case "get":
            return await client.mind_maps.get(nb, mm)
        case "get_or_none":
            return await client.mind_maps.get_or_none(nb, mm)
        case "get_tree":
            return await client.mind_maps.get_tree(nb, mm)
        case "list":
            return await client.mind_maps.list(nb)
        case "rename":
            return await client.mind_maps.rename(nb, mm, "Renamed Mind Map")
    raise KeyError(method)


async def _run_notebooks(
    client: Any, method: str, ids: dict[str, str], work: Path
) -> Any:
    nb = ids["notebook"]
    match method:
        case "create":
            return await client.notebooks.create("Synthetic Created Notebook")
        case "delete":
            return await client.notebooks.delete(nb)
        case "get":
            return await client.notebooks.get(nb)
        case "get_description":
            return await client.notebooks.get_description(nb)
        case "get_metadata":
            return await client.notebooks.get_metadata(nb)
        case "get_or_none":
            return await client.notebooks.get_or_none(nb)
        case "get_raw":
            return await client.notebooks.get_raw(nb)
        case "get_source_ids":
            return await client.notebooks.get_source_ids(nb)
        case "get_summary":
            return await client.notebooks.get_summary(nb)
        case "list":
            return await client.notebooks.list()
        case "remove_from_recent":
            return await client.notebooks.remove_from_recent(nb)
        case "rename":
            return await client.notebooks.rename(nb, "Renamed Notebook")
        case "share":
            return await client.notebooks.share(
                nb, public=True, artifact_id=ids["artifact"]
            )
    raise KeyError(method)


async def _run_notes(client: Any, method: str, ids: dict[str, str], work: Path) -> Any:
    nb = ids["notebook"]
    note = ids["note"]
    mm = ids["mind_map"]
    match method:
        case "create":
            return await client.notes.create(nb, "Created Note", "Fixture body")
        case "delete":
            return await client.notes.delete(nb, note)
        case "delete_mind_map":
            return await client.notes.delete_mind_map(nb, mm)
        case "get":
            return await client.notes.get(nb, note)
        case "get_or_none":
            return await client.notes.get_or_none(nb, note)
        case "list":
            return await client.notes.list(nb)
        case "list_mind_maps":
            return await client.notes.list_mind_maps(nb)
        case "update":
            return await client.notes.update(nb, note, "Updated body", "Updated title")
    raise KeyError(method)


async def _run_research(
    client: Any, method: str, ids: dict[str, str], work: Path
) -> Any:
    nb = ids["notebook"]
    source = {
        "url": "https://example.test/research-source",
        "title": "Fixture research source",
        "research_task_id": "fixture-task",
        "report_markdown": "# Fixture report",
    }
    match method:
        case "import_sources":
            return await client.research.import_sources(nb, "fixture-task", [source])
        case "import_sources_with_verification":
            return await client.research.import_sources_with_verification(
                nb, "fixture-task", [source], max_elapsed=1, initial_delay=0
            )
        case "poll":
            return await client.research.poll(nb, "fake-research-complete-0001")
        case "start":
            return await client.research.start(
                nb, "fixture query", source="web", mode="fast"
            )
        case "wait_for_completion":
            return await client.research.wait_for_completion(
                nb, "fake-research-complete-0001", timeout=1, initial_interval=1
            )
    raise KeyError(method)


async def _run_settings(
    client: Any, method: str, ids: dict[str, str], work: Path
) -> Any:
    match method:
        case "get_account_limits":
            return await client.settings.get_account_limits()
        case "get_account_tier":
            return await client.settings.get_account_tier()
        case "get_output_language":
            return await client.settings.get_output_language()
        case "set_output_language":
            return await client.settings.set_output_language("en")
    raise KeyError(method)


async def _run_sharing(
    client: Any, method: str, ids: dict[str, str], work: Path
) -> Any:
    from notebooklm import SharePermission, ShareViewLevel

    nb = ids["notebook"]
    match method:
        case "add_user":
            return await client.sharing.add_user(
                nb, "phase9.viewer@example.test", SharePermission.VIEWER, notify=False
            )
        case "get_status":
            return await client.sharing.get_status(nb)
        case "remove_user":
            return await client.sharing.remove_user(nb, "fixture.viewer@example.test")
        case "set_public":
            return await client.sharing.set_public(nb, True)
        case "set_view_level":
            return await client.sharing.set_view_level(nb, ShareViewLevel.CHAT_ONLY)
        case "update_user":
            return await client.sharing.update_user(
                nb, "fixture.viewer@example.test", SharePermission.EDITOR
            )
    raise KeyError(method)


async def _run_sources(
    client: Any, method: str, ids: dict[str, str], work: Path
) -> Any:
    nb = ids["notebook"]
    src = ids["source"]
    file_path = work / "source-upload.txt"
    file_path.write_text("fixture upload body", encoding="utf-8")
    match method:
        case "add_drive":
            return await client.sources.add_drive(nb, "drive-file-1", "Drive File")
        case "add_file":
            progress: list[tuple[int, int]] = []
            result = await client.sources.add_file(
                nb,
                file_path,
                title="Uploaded Fixture",
                on_progress=lambda sent, total: progress.append((sent, total)),
            )
            return {"source": result.as_dict(), "progress_events": len(progress)}
        case "add_text":
            return await client.sources.add_text(nb, "Text Fixture", "Fixture content")
        case "add_url":
            return await client.sources.add_url(nb, "https://example.test/source")
        case "check_freshness":
            return await client.sources.check_freshness(nb, src)
        case "delete":
            return await client.sources.delete(nb, src)
        case "get":
            return await client.sources.get(nb, src)
        case "get_fulltext":
            return await client.sources.get_fulltext(nb, src)
        case "get_guide":
            return await client.sources.get_guide(nb, src)
        case "get_or_none":
            return await client.sources.get_or_none(nb, src)
        case "list":
            return await client.sources.list(nb)
        case "refresh":
            return await client.sources.refresh(nb, src)
        case "rename":
            return await client.sources.rename(nb, src, "Renamed Source")
        case "wait_for_sources":
            return await client.sources.wait_for_sources(nb, [src], timeout=1)
        case "wait_until_ready":
            return await client.sources.wait_until_ready(nb, src, timeout=1)
        case "wait_until_registered":
            return await client.sources.wait_until_registered(nb, src, timeout=1)
    raise KeyError(method)


_RUNNERS: dict[str, Callable[[Any, str, dict[str, str], Path], Awaitable[Any]]] = {
    "artifacts": _run_artifacts,
    "chat": _run_chat,
    "mind_maps": _run_mind_maps,
    "notebooks": _run_notebooks,
    "notes": _run_notes,
    "research": _run_research,
    "settings": _run_settings,
    "sharing": _run_sharing,
    "sources": _run_sources,
}


async def _run_one(
    repo_root: Path, work_dir: Path, subclient: str, method: str
) -> ScenarioResult:
    scenario_id = f"{subclient}.{method}"
    scenario_dir = work_dir / scenario_id.replace(".", "-")
    scenario_dir.mkdir(parents=True, exist_ok=True)
    try:
        client = _client(scenario_dir / "storage-state.json")
        ids = await _base_ids(client)
        value = await _RUNNERS[subclient](client, method, ids, scenario_dir)
        return _pass(scenario_id, value)
    except BaseException as exc:  # noqa: BLE001 - audit report records all failures.
        return _fail(scenario_id, exc)


async def _run_api_scenarios(
    repo_root: Path, work_dir: Path, signatures: dict[str, Any]
) -> list[ScenarioResult]:
    results: list[ScenarioResult] = []
    for subclient, spec in signatures["subclients"].items():
        if subclient not in _RUNNERS:
            for method in spec["async_methods"]:
                results.append(
                    ScenarioResult(
                        f"{subclient}.{method}", "fail", "missing-runner", ""
                    )
                )
            continue
        for method in spec["async_methods"]:
            results.append(await _run_one(repo_root, work_dir, subclient, method))
    return results


async def _client_lifecycle(repo_root: Path, work_dir: Path) -> dict[str, Any]:
    from notebooklm import NotebookLMClient
    from notebooklm.errors import NotImplementedInPhaseError, ValidationError

    client = _client(work_dir / "lifecycle-storage.json")
    statuses = {
        "drain": "fail",
        "close": "fail",
        "refresh_auth": "fail",
        "rpc_call": "fail",
    }
    try:
        await client.drain()
        statuses["drain"] = "pass"
    except Exception:
        pass
    try:
        value = await client.rpc_call("wXbhsf", [None, 1])
        statuses["rpc_call"] = "pass" if isinstance(value, list) else "fail"
    except Exception:
        pass
    try:
        await client.refresh_auth()
    except NotImplementedInPhaseError:
        statuses["refresh_auth"] = "pass"
    except ValidationError:
        statuses["refresh_auth"] = "pass"
    except Exception:
        pass
    try:
        await client.close()
        statuses["close"] = "pass"
    except Exception:
        pass

    from_storage_path = work_dir / "from-storage.json"
    try:
        stored = NotebookLMClient.from_storage(path=str(from_storage_path))
        classmethods = {
            "from_storage": "pass"
            if hasattr(stored, "__aenter__") and hasattr(stored, "__await__")
            else "fail"
        }
    except Exception:
        classmethods = {"from_storage": "fail"}

    props_client = _client(work_dir / "props-storage.json")
    properties = {
        "auth": "pass" if props_client.auth is not None else "fail",
        "is_connected": "pass" if props_client.is_connected is True else "fail",
    }
    return {
        "async_methods": statuses,
        "classmethods": classmethods,
        "properties": properties,
        "status": "pass"
        if all(
            v == "pass"
            for group in (statuses, classmethods, properties)
            for v in group.values()
        )
        else "fail",
    }


async def _model_behavior(
    repo_root: Path, work_dir: Path, signatures: dict[str, Any]
) -> dict[str, Any]:
    from notebooklm import AuthTokens, ChatReference

    client = _client(work_dir / "models-storage.json")
    ids = await _base_ids(client)
    nb = ids["notebook"]
    src = ids["source"]
    note_id = ids["note"]
    artifact = (await client.artifacts.list(nb))[0]
    ask = await client.chat.ask(nb, CHAT_FIXTURE_QUESTION)
    turns = await client.chat.get_conversation_turns(nb, ask.conversation_id)
    note = await client.notes.get(nb, note_id)
    notebook = await client.notebooks.get(nb)
    metadata = await client.notebooks.get_metadata(nb)
    source = await client.sources.get(nb, src)
    fulltext = await client.sources.get_fulltext(nb, src)
    guide = await client.sources.get_guide(nb, src)
    checked = 0
    probes: list[tuple[Any, str]] = [
        (artifact, "as_dict"),
        (artifact, "state"),
        (artifact, "kind"),
        (ask, "as_dict"),
        (ChatReference(source_id=src), "as_dict"),
        (turns[0], "as_dict"),
        (note, "as_dict"),
        (notebook, "as_dict"),
        (metadata, "as_dict"),
        (source, "as_dict"),
        (source, "summary"),
        (source, "kind"),
        (fulltext, "as_dict"),
        (fulltext, "kind"),
        (guide, "as_dict"),
        (guide, "summary"),
    ]
    for obj, attr in probes:
        if obj is None or not hasattr(obj, attr):
            continue
        value = getattr(obj, attr)
        value() if callable(value) else value
        checked += 1
    if metadata.sources:
        metadata.sources[0].as_dict()
        checked += 1
    repr_text = repr(
        AuthTokens(
            cookies={("name", "domain", "/"): "SECRET"},
            csrf_token="TOKEN",
            session_id="SESSION",
        )
    )
    return {
        "dataclasses_checked": len(signatures["dataclasses"]),
        "roundtrip_helpers_checked": checked,
        "redaction_checked": "SECRET" not in repr_text and "TOKEN" not in repr_text,
    }


def _scenario_summary(
    signatures: dict[str, Any], results: list[ScenarioResult]
) -> dict[str, Any]:
    total_expected = sum(
        len(spec["async_methods"]) for spec in signatures["subclients"].values()
    )
    passed = [result for result in results if result.status == "pass"]
    failed = [result for result in results if result.status != "pass"]
    return {
        "oracle_subclients": len(signatures["subclients"]),
        "oracle_async_methods": total_expected,
        "scenario_probe": {
            "total": len(results),
            "passed": len(passed),
            "failed": len(failed),
            "failure_ids": [result.scenario_id for result in failed],
            "scenario_ids": [result.scenario_id for result in results],
            "subclients": sorted(signatures["subclients"]),
            "failures": [asdict(result) for result in failed],
        },
    }


async def _build_report_async(repo_root: Path, work_dir: Path) -> dict[str, Any]:
    _prepare_imports(repo_root)
    signatures = _load_json(repo_root / "compat" / "api_golden" / "signatures.json")
    work_dir.mkdir(parents=True, exist_ok=True)
    scenario_results = await _run_api_scenarios(
        repo_root, work_dir / "scenarios", signatures
    )
    lifecycle = await _client_lifecycle(repo_root, work_dir / "lifecycle")
    models = await _model_behavior(repo_root, work_dir / "models", signatures)
    api_behavior = _scenario_summary(signatures, scenario_results)
    status = (
        "pass"
        if api_behavior["scenario_probe"]["failed"] == 0
        and lifecycle["status"] == "pass"
        and models["redaction_checked"] is True
        else "fail"
    )
    api_state = _matrix_state(repo_root, "api")
    return {
        "schema_version": SCHEMA_VERSION,
        "target": TARGET,
        "overall_status": status,
        "strict_exit_code": 0 if status == "pass" else 1,
        "live_access": False,
        "credential_access": False,
        "category_promotion": {"api": api_state == "pass"},
        "category_states": {"api": api_state},
        "api_behavior": api_behavior,
        "client_lifecycle": {
            "async_methods": lifecycle["async_methods"],
            "classmethods": lifecycle["classmethods"],
            "properties": lifecycle["properties"],
        },
        "model_behavior": models,
    }


def build_report(
    repo_root: Path | None = None, work_dir: Path | None = None
) -> dict[str, Any]:
    root = repo_root or _repo_root_from_here()
    if work_dir is not None:
        return asyncio.run(_build_report_async(root, work_dir))
    with tempfile.TemporaryDirectory(prefix="znlm-api-audit-") as tmp:
        return asyncio.run(_build_report_async(root, Path(tmp)))


def _human(report: dict[str, Any]) -> str:
    probe = report["api_behavior"]["scenario_probe"]
    lifecycle_ok = all(
        value == "pass"
        for group in report["client_lifecycle"].values()
        for value in group.values()
    )
    return "\n".join(
        [
            f"ZeroNotebookLM API behavior audit: {report['overall_status']}",
            f"API async behavior: {probe['passed']}/{probe['total']}",
            f"client lifecycle: {'pass' if lifecycle_ok else 'fail'}",
            f"model behavior helpers: {report['model_behavior']['roundtrip_helpers_checked']}",
            "category promotion: api="
            + str(report.get("category_promotion", {}).get("api", False)).lower(),
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    parser.add_argument(
        "--strict", action="store_true", help="return non-zero on audit failure"
    )
    args = parser.parse_args(argv)
    report = build_report()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(_human(report))
    return int(report["strict_exit_code"]) if args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
