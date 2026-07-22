"""Offline artifact parsing and in-memory artifact API helpers.

The module started as the Phase 3A12 read-only list-artifacts decoder over
committed synthetic fake-RPC fixtures. Later parity batches now use the same
fixture seam for deterministic in-memory artifact status, export, generation,
rename/delete/retry, and CLI artifact/generate promotion. It still keeps live
RPC, authentication stores, browser state, downloads, public sharing, and real
NotebookLM data mutation outside this offline implementation.
"""

from __future__ import annotations

import asyncio
import csv
import html
import json
import os
import re
import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
from inspect import isawaitable
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import unquote, urlparse

from ._artifact_payloads import (
    build_audio_artifact_params,
    build_cinematic_video_artifact_params,
    build_data_table_artifact_params,
    build_flashcards_artifact_params,
    build_infographic_artifact_params,
    build_mind_map_params,
    build_quiz_artifact_params,
    build_report_artifact_params,
    build_retry_artifact_params,
    build_revise_slide_params,
    build_slide_deck_artifact_params,
    build_suggest_reports_params,
    build_video_artifact_params,
)
from .utils import _future_errors_enabled, _resolve_get
from .config import get_default_language
from .errors import ValidationError
from .exceptions import (
    ArtifactDownloadError,
    ArtifactFeatureUnavailableError,
    ArtifactNotFoundError,
    ArtifactNotReadyError,
    ArtifactParseError,
    ArtifactTimeoutError,
    DecodingError,
    RPCError,
)
from .fake_rpc import OfflineFixtureRpcClient
from .offline_status import OfflineReadOnlyStatusFixtures
from .rpc.types import (
    AudioFormat,
    AudioLength,
    ExportType,
    InfographicDetail,
    InfographicOrientation,
    InfographicStyle,
    QuizDifficulty,
    QuizQuantity,
    ReportFormat,
    RPCMethod,
    SlideDeckFormat,
    SlideDeckLength,
    VideoFormat,
    VideoStyle,
    artifact_status_to_str,
)
from .types import (
    Artifact,
    ArtifactStatus,
    ArtifactType,
    GenerationStatus,
    MindMapResult,
    ReportSuggestion,
)


def _fail(reason: str) -> NoReturn:
    raise ValidationError(f"invalid list_artifacts payload: {reason}")


_TYPE_BY_CODE = {
    1: ArtifactType.AUDIO,
    2: ArtifactType.REPORT,
    3: ArtifactType.VIDEO,
    4: ArtifactType.QUIZ,
    5: ArtifactType.MIND_MAP,
    7: ArtifactType.INFOGRAPHIC,
    8: ArtifactType.SLIDE_DECK,
    9: ArtifactType.DATA_TABLE,
}

_CODE_BY_TYPE = {value: key for key, value in _TYPE_BY_CODE.items()}
_CODE_BY_TYPE[ArtifactType.FLASHCARDS] = 4
_TRUSTED_DOWNLOAD_DOMAINS = (".google.com", ".googleusercontent.com", ".googleapis.com")
_LIVE_DOWNLOAD_MAX_BODY_BYTES = 512 * 1024 * 1024


def _is_trusted_download_host(hostname: str | None) -> bool:
    if hostname is None:
        return False
    hostname = unquote(hostname).lower()
    if "\\" in hostname or "/" in hostname:
        return False
    return any(
        hostname == domain.lstrip(".") or hostname.endswith(domain)
        for domain in _TRUSTED_DOWNLOAD_DOMAINS
    )


def _response_header(headers: Any, name: str) -> str:
    lowered = name.lower()
    for key, value in dict(headers or {}).items():
        if str(key).lower() == lowered:
            return str(value)
    return ""


def _valid_artifact_url(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(("http://", "https://"))


def _row_variant(row: list[Any]) -> int | None:
    options = row[9] if len(row) > 9 and isinstance(row[9], list) else None
    value = (
        options[1][0]
        if options is not None
        and len(options) > 1
        and isinstance(options[1], list)
        and options[1]
        else None
    )
    return value if isinstance(value, int) else None


def _row_created_at_raw(row: list[Any]) -> int | float | None:
    value = row[15][0] if len(row) > 15 and isinstance(row[15], list) and row[15] else None
    return value if isinstance(value, (int, float)) else None


def _live_audio_url(row: list[Any]) -> str | None:
    audio = row[6] if len(row) > 6 and isinstance(row[6], list) else None
    media = audio[5] if audio is not None and len(audio) > 5 and isinstance(audio[5], list) else None
    if media is None:
        return None
    fallback = None
    for item in media:
        if not isinstance(item, list) or not item or not _valid_artifact_url(item[0]):
            continue
        fallback = fallback or item[0]
        if len(item) > 2 and item[2] == "audio/mp4":
            return item[0]
    return fallback


def _live_video_url(row: list[Any]) -> str | None:
    variants = row[8] if len(row) > 8 and isinstance(row[8], list) else None
    if variants is None:
        return None
    fallback = None
    for media in variants:
        if not isinstance(media, list):
            continue
        for item in media:
            if not isinstance(item, list) or not item or not _valid_artifact_url(item[0]):
                continue
            fallback = fallback or item[0]
            if len(item) > 2 and item[2] == "video/mp4":
                if len(item) > 1 and item[1] == 4:
                    return item[0]
                fallback = item[0]
    return fallback


def _live_infographic_url(row: list[Any]) -> str | None:
    for item in row:
        if not isinstance(item, list) or len(item) <= 2:
            continue
        content = item[2]
        first = content[0] if isinstance(content, list) and content else None
        image = first[1] if isinstance(first, list) and len(first) > 1 else None
        if isinstance(image, list) and image and _valid_artifact_url(image[0]):
            return image[0]
    return None


def _live_slide_deck_url(row: list[Any], output_format: str) -> str | None:
    metadata = row[16] if len(row) > 16 and isinstance(row[16], list) else None
    if metadata is None:
        return None
    index = 4 if output_format == "pptx" else 3
    url = metadata[index] if len(metadata) > index else None
    return url if _valid_artifact_url(url) else None


def _extract_cell_text(cell: Any) -> str:
    if isinstance(cell, str):
        return cell
    if isinstance(cell, int):
        return ""
    if isinstance(cell, list):
        return "".join(text for item in cell if (text := _extract_cell_text(item)))
    return ""


def _parse_data_table(raw_data: Any) -> tuple[list[str], list[list[str]]]:
    try:
        rows_array = raw_data[0][0][0][0][4][2]
    except (IndexError, TypeError) as exc:
        raise ArtifactParseError(f"Failed to parse data table structure: {exc}") from exc
    if not isinstance(rows_array, list) or not rows_array:
        raise ArtifactParseError("Empty data table")

    headers: list[str] = []
    rows: list[list[str]] = []
    for index, row_section in enumerate(rows_array):
        if not isinstance(row_section, list) or len(row_section) < 3:
            continue
        cell_array = row_section[2]
        if not isinstance(cell_array, list):
            continue
        values = [_extract_cell_text(cell) for cell in cell_array]
        if index == 0:
            headers = values
        else:
            rows.append(values)
    if not headers:
        raise ArtifactParseError("Failed to extract headers from data table")
    return headers, rows


def _extract_app_data(html_content: str) -> dict[str, Any]:
    match = re.search(r'data-app-data="([^"]+)"', html_content)
    if not match:
        raise ArtifactParseError("No data-app-data attribute found in HTML")
    return json.loads(html.unescape(match.group(1)))


def _format_quiz_markdown(title: str, questions: list[dict[str, Any]]) -> str:
    lines = [f"# {title}", ""]
    for index, question in enumerate(questions, 1):
        lines.append(f"## Question {index}")
        lines.append(question.get("question", ""))
        lines.append("")
        for option in question.get("answerOptions", []):
            marker = "[x]" if option.get("isCorrect") else "[ ]"
            lines.append(f"- {marker} {option.get('text', '')}")
        if question.get("hint"):
            lines.append("")
            lines.append(f"**Hint:** {question['hint']}")
        lines.append("")
    return "\n".join(lines)


def _format_flashcards_markdown(title: str, cards: list[dict[str, Any]]) -> str:
    lines = [f"# {title}", ""]
    for index, card in enumerate(cards, 1):
        lines.extend(
            [
                f"## Card {index}",
                "",
                f"**Q:** {card.get('f', '')}",
                "",
                f"**A:** {card.get('b', '')}",
                "",
                "---",
                "",
            ]
        )
    return "\n".join(lines)


def _format_interactive_content(
    app_data: dict[str, Any],
    title: str,
    output_format: str,
    html_content: str,
    *,
    is_quiz: bool,
) -> str:
    if output_format == "html":
        return html_content
    if is_quiz:
        questions = app_data.get("quiz", [])
        if output_format == "markdown":
            return _format_quiz_markdown(title, questions)
        return json.dumps({"title": title, "questions": questions}, indent=2)
    cards = app_data.get("flashcards", [])
    if output_format == "markdown":
        return _format_flashcards_markdown(title, cards)
    normalized = [{"front": card.get("f", ""), "back": card.get("b", "")} for card in cards]
    return json.dumps({"title": title, "cards": normalized}, indent=2)


def _parse_generation_result(result: Any, *, method_id: str) -> GenerationStatus:
    if result is None:
        raise ArtifactFeatureUnavailableError("artifact", method_id=method_id)
    if not isinstance(result, list) or not result or not isinstance(result[0], list):
        return GenerationStatus(
            task_id="", status="failed", error="Generation failed - no artifact_id returned"
        )
    artifact_id = result[0][0] if result[0] else None
    if artifact_id:
        status_code = result[0][4] if len(result[0]) > 4 else None
        status = artifact_status_to_str(status_code) if status_code is not None else "pending"
        return GenerationStatus(task_id=str(artifact_id), status=status)
    if _future_errors_enabled():
        if artifact_id is None:
            raise ArtifactFeatureUnavailableError("artifact", method_id=method_id)
        raise DecodingError("No artifact id", method_id=method_id)
    return GenerationStatus(
        task_id="", status="failed", error="Generation failed - no artifact_id returned"
    )


def _export_type_value(export_type: Any) -> int:
    if isinstance(export_type, bool):
        raise ValidationError("export type must be docs or sheets")
    if isinstance(export_type, int):
        return export_type
    text = str(export_type).strip().upper()
    if text.endswith(".DOCS") or text == "DOCS":
        return int(ExportType.DOCS)
    if text.endswith(".SHEETS") or text == "SHEETS":
        return int(ExportType.SHEETS)
    try:
        return int(export_type)
    except (TypeError, ValueError):
        raise ValidationError("export type must be docs or sheets") from None


def _coerce_video_format(value: Any) -> Any:
    if value is None or isinstance(value, VideoFormat):
        return value
    if isinstance(value, str):
        name = value.replace("-", "_").upper()
        try:
            return VideoFormat[name]
        except KeyError:
            pass
    return value


def _coerce_video_style(value: Any) -> Any:
    if value is None or isinstance(value, VideoStyle):
        return value
    if isinstance(value, str):
        name = value.replace("-", "_").upper()
        try:
            return VideoStyle[name]
        except KeyError:
            pass
    return value


def _kind_for_code(
    type_code: int, variant: int | None, title: str = ""
) -> ArtifactType:
    if type_code == 4 and variant == 1:
        return ArtifactType.FLASHCARDS
    return _TYPE_BY_CODE.get(type_code, ArtifactType.UNKNOWN)


def _deterministic_download_content(
    *,
    notebook_id: str,
    artifact: "Artifact",
    output_format: str | None,
) -> str:
    return (
        "# Offline NotebookLM artifact download\n"
        "\n"
        "offline deterministic NotebookLM artifact download\n"
        f"notebook_id: {notebook_id}\n"
        f"artifact_id: {artifact.id}\n"
        f"artifact_title: {artifact.title}\n"
        f"artifact_type: {artifact.kind().value}\n"
        f"output_format: {output_format or 'default'}\n"
        f"source_url: {artifact.url or 'offline-fixture'}\n"
    )


def _created_at(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        _fail("created_at must be integer seconds or null")
    try:
        return datetime.fromtimestamp(value, timezone.utc)
    except (OverflowError, OSError, ValueError):
        pass
    _fail("created_at is out of range")


def _created_at_dict(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _type_code(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail("artifact type code must be an integer")
    if value not in _TYPE_BY_CODE:
        _fail("artifact type code is not supported")
    return value


def _status(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail("status must be an integer artifact status")
    try:
        ArtifactStatus(value)
    except ValueError:
        pass
    else:
        return value
    _fail("status is not supported")


def _variant(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        _fail("variant must be integer or null")
    return value


def parse_list_artifacts_payload(payload: Any) -> list[Artifact]:
    """Parse a decoded synthetic list-artifacts payload into artifact models."""

    if not isinstance(payload, list):
        _fail("expected artifact rows")
    parsed: list[Artifact] = []
    for row in payload:
        if not isinstance(row, list) or len(row) < 7:
            _fail("artifact row is malformed")
        artifact_id, title, raw_type, raw_status, raw_created_at, url, raw_variant = (
            row[:7]
        )
        if not isinstance(artifact_id, str) or artifact_id == "":
            _fail("artifact id must be non-empty text")
        if not isinstance(title, str):
            _fail("artifact title must be text")
        if url is not None and not isinstance(url, str):
            _fail("artifact url must be text or null")
        parsed.append(
            Artifact(
                id=artifact_id,
                title=title,
                _artifact_type=_type_code(raw_type),
                status=_status(raw_status),
                created_at=_created_at(raw_created_at),
                url=url,
                _variant=_variant(raw_variant),
            )
        )
    return parsed


class OfflineArtifactService:
    """In-memory service over synthetic artifact payloads."""

    def __init__(self, artifacts_by_notebook: dict[str, list[Artifact]]) -> None:
        self._artifacts_by_notebook = {
            notebook_id: list(artifacts)
            for notebook_id, artifacts in artifacts_by_notebook.items()
        }
        self._created_count = 0

    @classmethod
    def from_rpc(
        cls,
        rpc: OfflineFixtureRpcClient,
        notebook_ids: list[str],
    ) -> "OfflineArtifactService":
        artifacts_by_notebook: dict[str, list[Artifact]] = {}
        for notebook_id in notebook_ids:
            try:
                payload = rpc.list_artifacts_payload(notebook_id)
            except ValidationError:
                artifacts_by_notebook[notebook_id] = []
                continue
            artifacts_by_notebook[notebook_id] = parse_list_artifacts_payload(payload)
        return cls(artifacts_by_notebook)

    def list(
        self,
        notebook_id: str,
        artifact_type: ArtifactType | None = None,
    ) -> list[Artifact]:
        artifacts = list(self._artifacts_by_notebook.get(notebook_id, ()))
        if artifact_type is None:
            return artifacts
        return [artifact for artifact in artifacts if artifact.kind() == artifact_type]

    def get(self, notebook_id: str, artifact_id: str) -> Artifact | None:
        for artifact in self._artifacts_by_notebook.get(notebook_id, ()):
            if artifact.id == artifact_id:
                return artifact
        return None

    def delete(self, notebook_id: str, artifact_id: str) -> None:
        artifacts = self._artifacts_by_notebook.setdefault(notebook_id, [])
        self._artifacts_by_notebook[notebook_id] = [
            artifact for artifact in artifacts if artifact.id != artifact_id
        ]

    def rename(
        self,
        notebook_id: str,
        artifact_id: str,
        new_title: str,
        *,
        return_object: bool = True,
    ) -> Artifact | None:
        artifact = self.get(notebook_id, artifact_id)
        if artifact is None:
            raise ValidationError("artifact not found")
        if not isinstance(new_title, str) or new_title == "":
            raise ValidationError("artifact title must be non-empty text")
        artifact.title = new_title
        return artifact if return_object else None

    def generate(
        self,
        notebook_id: str,
        artifact_type: ArtifactType,
        *,
        title: str | None = None,
    ) -> GenerationStatus:
        artifacts = self._artifacts_by_notebook.setdefault(notebook_id, [])
        self._created_count += 1
        safe_type = artifact_type.value.replace("_", "-")
        artifact_id = f"offline-{safe_type}-{self._created_count:04d}"
        artifact = Artifact(
            id=artifact_id,
            title=title or f"Synthetic {artifact_type.value.replace('_', ' ').title()}",
            _artifact_type=_CODE_BY_TYPE.get(
                artifact_type, _CODE_BY_TYPE[ArtifactType.QUIZ]
            ),
            status=ArtifactStatus.COMPLETED.value,
            created_at=datetime.fromtimestamp(self._created_count, timezone.utc),
            url=f"https://example.test/notebooklm-bare/generated/{artifact_id}",
            _variant=1
            if artifact_type is ArtifactType.FLASHCARDS
            else 2
            if artifact_type is ArtifactType.QUIZ
            else None,
        )
        artifacts.append(artifact)
        return GenerationStatus(
            task_id=artifact.id, status="completed", url=artifact.url
        )

    def export(
        self,
        notebook_id: str,
        artifact_id: str | None,
        *,
        content: str | None = None,
        title: str = "Export",
        export_type: Any = None,
    ) -> dict[str, Any]:
        if artifact_id is not None and self.get(notebook_id, artifact_id) is None:
            raise ValidationError("artifact not found")
        return {
            "notebook_id": notebook_id,
            "artifact_id": artifact_id,
            "title": title,
            "exported": True,
            "export_type": getattr(export_type, "name", str(export_type))
            if export_type is not None
            else None,
            "content": content,
            "url": f"https://example.test/notebooklm-bare/export/{artifact_id or 'content'}",
        }

    def select_completed(
        self,
        notebook_id: str,
        artifact_type: ArtifactType,
        artifact_id: str | None = None,
    ) -> Artifact:
        candidates = [
            artifact
            for artifact in self.list(notebook_id, artifact_type)
            if artifact.is_completed
        ]
        if artifact_id is not None:
            for artifact in candidates:
                if artifact.id == artifact_id:
                    return artifact
            raise ValidationError("artifact not found")
        if not candidates:
            raise ValidationError(
                f"no completed {artifact_type.value.replace('_', '-')} artifacts"
            )
        return max(
            candidates,
            key=lambda item: (
                item.created_at or datetime.min.replace(tzinfo=timezone.utc)
            ),
        )

    def download(
        self,
        notebook_id: str,
        output_path: str,
        artifact_type: ArtifactType,
        *,
        artifact_id: str | None = None,
        output_format: str | None = None,
    ) -> str:
        artifact = self.select_completed(notebook_id, artifact_type, artifact_id)
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            _deterministic_download_content(
                notebook_id=notebook_id,
                artifact=artifact,
                output_format=output_format,
            ),
            encoding="utf-8",
        )
        return str(path)


class ArtifactsAPI:
    """Offline synthetic artifacts sub-client over the fake RPC seam."""

    def __init__(
        self,
        *,
        artifacts: OfflineArtifactService,
        status_fixtures: OfflineReadOnlyStatusFixtures | None = None,
        live_rpc: Any = None,
        source_ids_provider: Callable[[str], Any] | None = None,
        note_creator: Callable[[str, str, str], Any] | None = None,
    ) -> None:
        self._artifacts = artifacts
        self._status_fixtures = (
            status_fixtures or OfflineReadOnlyStatusFixtures.load_default()
        )
        self._live_rpc = live_rpc
        self._source_ids_provider = source_ids_provider
        self._note_creator = note_creator

    async def list(
        self,
        notebook_id: str,
        artifact_type: ArtifactType | None = None,
    ) -> list[Artifact]:
        if self._live_rpc is not None:
            artifacts = [
                artifact
                for row in await self._live_raw_artifacts(notebook_id)
                if (artifact := Artifact.from_api_response(row)) is not None
                and self._artifact_matches(artifact, artifact_type)
            ]
            if artifact_type is None or artifact_type == ArtifactType.MIND_MAP:
                for row in await self._live_mind_map_rows(notebook_id):
                    artifact = Artifact.from_mind_map(row)
                    if artifact is not None and self._artifact_matches(
                        artifact, artifact_type
                    ):
                        artifacts.append(artifact)
            return artifacts
        return self._artifacts.list(notebook_id, artifact_type)

    async def get(self, notebook_id: str, artifact_id: str) -> Artifact | None:
        return _resolve_get(
            await self.get_or_none(notebook_id, artifact_id),
            not_found=ArtifactNotFoundError(artifact_id),
            resource="artifact",
        )

    async def get_or_none(self, notebook_id: str, artifact_id: str) -> Artifact | None:
        if self._live_rpc is not None:
            for artifact in await self.list(notebook_id):
                if artifact.id == artifact_id:
                    return artifact
            return None
        return self._artifacts.get(notebook_id, artifact_id)

    async def list_audio(self, notebook_id: str) -> list[Artifact]:
        return await self.list(notebook_id, ArtifactType.AUDIO)

    async def list_data_tables(self, notebook_id: str) -> list[Artifact]:
        return await self.list(notebook_id, ArtifactType.DATA_TABLE)

    async def list_flashcards(self, notebook_id: str) -> list[Artifact]:
        return await self.list(notebook_id, ArtifactType.FLASHCARDS)

    async def list_infographics(self, notebook_id: str) -> list[Artifact]:
        return await self.list(notebook_id, ArtifactType.INFOGRAPHIC)

    async def list_quizzes(self, notebook_id: str) -> list[Artifact]:
        return await self.list(notebook_id, ArtifactType.QUIZ)

    async def list_reports(self, notebook_id: str) -> list[Artifact]:
        return await self.list(notebook_id, ArtifactType.REPORT)

    async def list_slide_decks(self, notebook_id: str) -> list[Artifact]:
        return await self.list(notebook_id, ArtifactType.SLIDE_DECK)

    async def list_video(self, notebook_id: str) -> list[Artifact]:
        return await self.list(notebook_id, ArtifactType.VIDEO)

    async def delete(self, notebook_id: str, artifact_id: str) -> None:
        if self._live_rpc is not None:
            await self._live_rpc.rpc_call(
                RPCMethod.DELETE_ARTIFACT,
                [[2], artifact_id],
                source_path=f"/notebook/{notebook_id}",
                allow_null=True,
            )
            return None
        self._artifacts.delete(notebook_id, artifact_id)

    async def download_audio(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None = None,
        *,
        artifacts_data: list[Any] | None = None,
    ) -> str:
        if self._live_rpc is not None:
            return await self._live_download_media(
                notebook_id,
                output_path,
                ArtifactType.AUDIO,
                artifact_id=artifact_id,
                artifacts_data=artifacts_data,
            )
        return self._artifacts.download(
            notebook_id,
            output_path,
            ArtifactType.AUDIO,
            artifact_id=artifact_id,
        )

    async def download_data_table(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None = None,
        *,
        artifacts_data: list[Any] | None = None,
    ) -> str:
        if self._live_rpc is not None:
            rows = (
                artifacts_data
                if artifacts_data is not None
                else await self._live_raw_artifacts(notebook_id)
            )
            row = self._select_live_download_row(
                rows, ArtifactType.DATA_TABLE, artifact_id
            )
            raw_data = row[18] if len(row) > 18 else None
            headers, table_rows = _parse_data_table(raw_data)
            output = Path(output_path)
            output.parent.mkdir(parents=True, exist_ok=True)

            def _write_csv() -> None:
                with output.open("w", newline="", encoding="utf-8-sig") as fh:
                    writer = csv.writer(fh)
                    writer.writerow(headers)
                    writer.writerows(table_rows)

            await asyncio.to_thread(_write_csv)
            return str(output)
        return self._artifacts.download(
            notebook_id,
            output_path,
            ArtifactType.DATA_TABLE,
            artifact_id=artifact_id,
        )

    async def download_flashcards(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None = None,
        output_format: str = "json",
        *,
        artifacts: list[Any] | None = None,
    ) -> str:
        if self._live_rpc is not None:
            return await self._live_download_interactive(
                notebook_id,
                output_path,
                artifact_id,
                output_format,
                ArtifactType.FLASHCARDS,
                artifacts=artifacts,
            )
        return self._artifacts.download(
            notebook_id,
            output_path,
            ArtifactType.FLASHCARDS,
            artifact_id=artifact_id,
            output_format=output_format,
        )

    async def download_infographic(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None = None,
        *,
        artifacts_data: list[Any] | None = None,
    ) -> str:
        if self._live_rpc is not None:
            return await self._live_download_media(
                notebook_id,
                output_path,
                ArtifactType.INFOGRAPHIC,
                artifact_id=artifact_id,
                artifacts_data=artifacts_data,
            )
        return self._artifacts.download(
            notebook_id,
            output_path,
            ArtifactType.INFOGRAPHIC,
            artifact_id=artifact_id,
        )

    async def download_mind_map(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None = None,
        *,
        mind_maps: list[Any] | None = None,
        artifacts_data: list[Any] | None = None,
    ) -> str:
        if self._live_rpc is not None:
            return await self._live_download_mind_map(
                notebook_id,
                output_path,
                artifact_id,
                mind_maps=mind_maps,
                artifacts_data=artifacts_data,
            )
        return self._artifacts.download(
            notebook_id,
            output_path,
            ArtifactType.MIND_MAP,
            artifact_id=artifact_id,
        )

    async def download_quiz(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None = None,
        output_format: str = "json",
        *,
        artifacts: list[Any] | None = None,
    ) -> str:
        if self._live_rpc is not None:
            return await self._live_download_interactive(
                notebook_id,
                output_path,
                artifact_id,
                output_format,
                ArtifactType.QUIZ,
                artifacts=artifacts,
            )
        return self._artifacts.download(
            notebook_id,
            output_path,
            ArtifactType.QUIZ,
            artifact_id=artifact_id,
            output_format=output_format,
        )

    async def download_report(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None = None,
        *,
        artifacts_data: list[Any] | None = None,
    ) -> str:
        if self._live_rpc is not None:
            rows = (
                artifacts_data
                if artifacts_data is not None
                else await self._live_raw_artifacts(notebook_id)
            )
            row = self._select_live_download_row(
                rows, ArtifactType.REPORT, artifact_id
            )
            markdown = self._live_report_markdown(row)
            if not isinstance(markdown, str):
                raise ArtifactParseError("Invalid report content structure")
            output = Path(output_path)
            output.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(output.write_text, markdown, encoding="utf-8")
            return str(output)
        return self._artifacts.download(
            notebook_id,
            output_path,
            ArtifactType.REPORT,
            artifact_id=artifact_id,
        )

    async def download_slide_deck(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None = None,
        output_format: str = "pdf",
        *,
        artifacts_data: list[Any] | None = None,
    ) -> str:
        if self._live_rpc is not None:
            if output_format not in {"pdf", "pptx"}:
                raise ValidationError(
                    f"Invalid format '{output_format}'. Must be 'pdf' or 'pptx'."
                )
            return await self._live_download_media(
                notebook_id,
                output_path,
                ArtifactType.SLIDE_DECK,
                artifact_id=artifact_id,
                output_format=output_format,
                artifacts_data=artifacts_data,
            )
        return self._artifacts.download(
            notebook_id,
            output_path,
            ArtifactType.SLIDE_DECK,
            artifact_id=artifact_id,
            output_format=output_format,
        )

    async def download_video(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None = None,
        *,
        artifacts_data: list[Any] | None = None,
    ) -> str:
        if self._live_rpc is not None:
            return await self._live_download_media(
                notebook_id,
                output_path,
                ArtifactType.VIDEO,
                artifact_id=artifact_id,
                artifacts_data=artifacts_data,
            )
        return self._artifacts.download(
            notebook_id,
            output_path,
            ArtifactType.VIDEO,
            artifact_id=artifact_id,
        )

    async def export(
        self,
        notebook_id: str,
        artifact_id: str | None = None,
        content: str | None = None,
        title: str = "Export",
        export_type: ExportType = ExportType.DOCS,
    ) -> Any:
        if self._live_rpc is not None:
            return await self._live_rpc.rpc_call(
                RPCMethod.EXPORT_ARTIFACT,
                [None, artifact_id, content, title, _export_type_value(export_type)],
                source_path=f"/notebook/{notebook_id}",
                allow_null=True,
            )
        return self._artifacts.export(
            notebook_id,
            artifact_id,
            content=content,
            title=title,
            export_type=export_type,
        )

    async def export_data_table(
        self,
        notebook_id: str,
        artifact_id: str,
        title: str = "Export",
    ) -> Any:
        if self._live_rpc is not None:
            return await self.export(
                notebook_id,
                artifact_id,
                title=title,
                export_type=ExportType.SHEETS,
            )
        return self._artifacts.export(
            notebook_id,
            artifact_id,
            title=title,
            export_type="SHEETS",
        )

    async def export_report(
        self,
        notebook_id: str,
        artifact_id: str,
        title: str = "Export",
        export_type: ExportType = ExportType.DOCS,
    ) -> Any:
        if self._live_rpc is not None:
            return await self.export(
                notebook_id,
                artifact_id,
                title=title,
                export_type=export_type,
            )
        return self._artifacts.export(
            notebook_id,
            artifact_id,
            title=title,
            export_type=export_type,
        )

    async def _live_download_mind_map(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None,
        *,
        mind_maps: list[Any] | None,
        artifacts_data: list[Any] | None,
    ) -> str:
        if mind_maps is None:
            mind_maps = await self._live_mind_map_rows(notebook_id)
        json_string: str | None = None
        if artifact_id:
            mind_map = next(
                (row for row in mind_maps if row and str(row[0]) == artifact_id),
                None,
            )
            if mind_map is not None:
                json_string = self._live_note_content(mind_map)
            else:
                if artifacts_data is None:
                    artifacts_data = await self._live_raw_artifacts(notebook_id)
                interactive = any(
                    isinstance(row, list)
                    and (artifact := Artifact.from_api_response(row)).id == artifact_id
                    and artifact.is_interactive_mind_map
                    for row in artifacts_data
                )
                if interactive:
                    json_string = await self._live_interactive_mind_map_tree(
                        notebook_id, artifact_id
                    )
                    if json_string is None:
                        raise ArtifactNotReadyError("mind_map")
                elif not mind_maps:
                    raise ArtifactNotReadyError("mind_map")
                else:
                    raise ArtifactNotFoundError(artifact_id)
        else:
            if not mind_maps:
                raise ArtifactNotReadyError("mind_map")
            json_string = self._live_note_content(mind_maps[0])

        try:
            if json_string is None:
                raise ArtifactParseError("Invalid mind map content structure")
            data = json.loads(json_string)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ArtifactParseError(f"Failed to parse mind map: {exc}") from exc
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        def _write_json() -> None:
            with output.open("w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)

        await asyncio.to_thread(_write_json)
        return str(output)

    async def _live_interactive_mind_map_tree(
        self, notebook_id: str, artifact_id: str
    ) -> str | None:
        result = await self._live_rpc.rpc_call(
            RPCMethod.GET_INTERACTIVE_HTML,
            [artifact_id],
            source_path=f"/notebook/{notebook_id}",
            allow_null=True,
        )
        return (
            result[0][9][3]
            if isinstance(result, list)
            and result
            and isinstance(result[0], list)
            and len(result[0]) > 9
            and isinstance(result[0][9], list)
            and len(result[0][9]) > 3
            and isinstance(result[0][9][3], str)
            else None
        )

    async def _live_download_interactive(
        self,
        notebook_id: str,
        output_path: str,
        artifact_id: str | None,
        output_format: str,
        artifact_type: ArtifactType,
        *,
        artifacts: list[Any] | None = None,
    ) -> str:
        if output_format not in {"json", "markdown", "html"}:
            raise ValidationError(
                f"Invalid output_format: {output_format!r}. Use one of: json, markdown, html"
            )
        if artifacts is None:
            artifacts = await self.list(notebook_id, artifact_type)
        completed = [
            artifact
            for artifact in artifacts
            if isinstance(artifact, Artifact) and artifact.is_completed
        ]
        if not completed:
            raise ArtifactNotReadyError(artifact_type.value)
        completed.sort(
            key=lambda artifact: artifact.created_at.timestamp()
            if artifact.created_at
            else 0,
            reverse=True,
        )
        if artifact_id:
            artifact = next((item for item in completed if item.id == artifact_id), None)
            if artifact is None:
                raise ArtifactNotFoundError(artifact_id)
        else:
            artifact = completed[0]

        result = await self._live_rpc.rpc_call(
            RPCMethod.GET_INTERACTIVE_HTML,
            [artifact.id],
            source_path=f"/notebook/{notebook_id}",
            allow_null=True,
        )
        html_content = (
            result[0][9][0]
            if isinstance(result, list)
            and result
            and isinstance(result[0], list)
            and len(result[0]) > 9
            and isinstance(result[0][9], list)
            and result[0][9]
            and isinstance(result[0][9][0], str)
            else None
        )
        if not html_content:
            raise ArtifactDownloadError(f"Failed to fetch {artifact_type.value} content")
        try:
            app_data = _extract_app_data(html_content)
        except json.JSONDecodeError as exc:
            raise ArtifactParseError(f"Failed to parse {artifact_type.value}: {exc}") from exc

        default_title = (
            "Untitled Quiz" if artifact_type == ArtifactType.QUIZ else "Untitled Flashcards"
        )
        content = _format_interactive_content(
            app_data,
            artifact.title or default_title,
            output_format,
            html_content,
            is_quiz=artifact_type == ArtifactType.QUIZ,
        )
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(output.write_text, content, encoding="utf-8")
        return str(output)

    async def _live_download_media(
        self,
        notebook_id: str,
        output_path: str,
        artifact_type: ArtifactType,
        *,
        artifact_id: str | None,
        output_format: str = "pdf",
        artifacts_data: list[Any] | None = None,
    ) -> str:
        rows = (
            artifacts_data
            if artifacts_data is not None
            else await self._live_raw_artifacts(notebook_id)
        )
        row = self._select_live_download_row(rows, artifact_type, artifact_id)
        url = self._live_media_url(row, artifact_type, output_format)
        if not url:
            raise ArtifactParseError(
                f"Could not extract download URL for {artifact_type.value}"
            )
        return await self._live_download_url(url, output_path)

    def _select_live_download_row(
        self,
        rows: list[Any],
        artifact_type: ArtifactType,
        artifact_id: str | None,
    ) -> list[Any]:
        type_code = _CODE_BY_TYPE[artifact_type]
        matches = [
            row
            for row in rows
            if isinstance(row, list)
            and len(row) > 4
            and row[2] == type_code
            and row[4] == ArtifactStatus.COMPLETED
            and _kind_for_code(row[2], _row_variant(row)) == artifact_type
        ]
        if artifact_id is not None:
            for row in matches:
                if row and row[0] == artifact_id:
                    return row
            raise ArtifactNotReadyError(artifact_type.value)
        if not matches:
            raise ArtifactNotReadyError(artifact_type.value)
        return max(matches, key=lambda row: _row_created_at_raw(row) or 0)

    @staticmethod
    def _live_media_url(
        row: list[Any], artifact_type: ArtifactType, output_format: str
    ) -> str | None:
        if artifact_type == ArtifactType.AUDIO:
            return _live_audio_url(row)
        if artifact_type == ArtifactType.VIDEO:
            return _live_video_url(row)
        if artifact_type == ArtifactType.INFOGRAPHIC:
            return _live_infographic_url(row)
        if artifact_type == ArtifactType.SLIDE_DECK:
            return _live_slide_deck_url(row, output_format)
        return None

    @staticmethod
    def _live_report_markdown(row: list[Any]) -> str | None:
        if len(row) <= 7:
            return None
        content = row[7]
        if isinstance(content, str):
            return content
        if isinstance(content, list) and content and isinstance(content[0], str):
            return content[0]
        return None

    async def _live_download_url(self, url: str, output_path: str) -> str:
        parsed = urlparse(url)
        display_host = parsed.hostname or parsed.netloc.rsplit("@", 1)[-1]
        if parsed.scheme != "https":
            raise ArtifactDownloadError(f"Download URL must use HTTPS: {url[:80]}")
        if not _is_trusted_download_host(parsed.hostname):
            raise ArtifactDownloadError(f"Untrusted download domain: {display_host}")

        headers: dict[str, str] = {}
        cookie = self._live_rpc._cookie_header_for_url(url)
        if cookie:
            headers["Cookie"] = cookie
        response = await asyncio.to_thread(
            self._live_rpc._get,
            url,
            headers=headers,
            timeout=self._live_rpc._timeout,
            max_redirects=5,
            max_body_bytes=_LIVE_DOWNLOAD_MAX_BODY_BYTES,
        )
        if response.status in {401, 403}:
            raise ArtifactDownloadError(
                f"Authentication required for {display_host}{parsed.path}"
            )
        if response.status >= 400:
            raise ArtifactDownloadError(
                f"HTTP error {response.status} downloading {display_host}{parsed.path}"
            )
        if "text/html" in _response_header(response.headers, "content-type").lower():
            raise ArtifactDownloadError(
                "Download failed: received HTML instead of media file"
            )
        if not response.body:
            raise ArtifactDownloadError("Download produced 0 bytes")

        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            dir=output_file.parent,
            prefix=output_file.name + ".",
            suffix=".tmp",
        )
        temp_file = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(response.body)
            os.replace(temp_file, output_file)
        except BaseException:
            temp_file.unlink(missing_ok=True)
            raise
        return output_path

    async def _resolved_source_ids(
        self, notebook_id: str, source_ids: list[str] | None
    ) -> list[str]:
        if source_ids is not None:
            return source_ids
        if self._source_ids_provider is None:
            raise ValidationError("live artifact generation requires source ids")
        resolved = self._source_ids_provider(notebook_id)
        if isawaitable(resolved):
            resolved = await resolved
        return list(resolved)

    async def _call_live_generate(
        self,
        notebook_id: str,
        params: list[Any],
        *,
        null_result_artifact_type: str,
    ) -> GenerationStatus:
        try:
            result = await self._live_rpc.rpc_call(
                RPCMethod.CREATE_ARTIFACT,
                params,
                source_path=f"/notebook/{notebook_id}",
                allow_null=True,
                operation_variant=None,
            )
        except RPCError as exc:
            if exc.rpc_code == "USER_DISPLAYABLE_ERROR" and not _future_errors_enabled():
                return GenerationStatus(
                    task_id="",
                    status="failed",
                    error=str(exc),
                    error_code=str(exc.rpc_code) if exc.rpc_code is not None else None,
                )
            raise
        if result is None:
            raise ArtifactFeatureUnavailableError(
                null_result_artifact_type,
                method_id=RPCMethod.CREATE_ARTIFACT.value,
            )
        return _parse_generation_result(
            result, method_id=RPCMethod.CREATE_ARTIFACT.value
        )

    async def generate_audio(
        self,
        notebook_id: str,
        source_ids: list[str] | None = None,
        language: str | None = "en",
        instructions: str | None = None,
        audio_format: AudioFormat | None = None,
        audio_length: AudioLength | None = None,
    ) -> GenerationStatus:
        if self._live_rpc is not None:
            source_ids = await self._resolved_source_ids(notebook_id, source_ids)
            params = build_audio_artifact_params(
                notebook_id,
                source_ids,
                language=get_default_language() if language is None else language,
                instructions=instructions,
                audio_format=audio_format,
                audio_length=audio_length,
            )
            return await self._call_live_generate(
                notebook_id, params, null_result_artifact_type="audio"
            )
        return self._artifacts.generate(notebook_id, ArtifactType.AUDIO)

    async def generate_cinematic_video(
        self,
        notebook_id: str,
        source_ids: list[str] | None = None,
        language: str | None = "en",
        instructions: str | None = None,
    ) -> GenerationStatus:
        if self._live_rpc is not None:
            source_ids = await self._resolved_source_ids(notebook_id, source_ids)
            params = build_cinematic_video_artifact_params(
                notebook_id,
                source_ids,
                language=get_default_language() if language is None else language,
                instructions=instructions,
            )
            return await self._call_live_generate(
                notebook_id, params, null_result_artifact_type="cinematic video"
            )
        return self._artifacts.generate(
            notebook_id, ArtifactType.VIDEO, title="Synthetic Cinematic Video"
        )

    async def generate_data_table(
        self,
        notebook_id: str,
        source_ids: list[str] | None = None,
        language: str | None = "en",
        instructions: str | None = None,
    ) -> GenerationStatus:
        if self._live_rpc is not None:
            source_ids = await self._resolved_source_ids(notebook_id, source_ids)
            params = build_data_table_artifact_params(
                notebook_id,
                source_ids,
                language=get_default_language() if language is None else language,
                instructions=instructions,
            )
            return await self._call_live_generate(
                notebook_id, params, null_result_artifact_type="data table"
            )
        return self._artifacts.generate(notebook_id, ArtifactType.DATA_TABLE)

    async def generate_flashcards(
        self,
        notebook_id: str,
        source_ids: list[str] | None = None,
        instructions: str | None = None,
        quantity: QuizQuantity | None = None,
        difficulty: QuizDifficulty | None = None,
    ) -> GenerationStatus:
        if self._live_rpc is not None:
            source_ids = await self._resolved_source_ids(notebook_id, source_ids)
            params = build_flashcards_artifact_params(
                notebook_id,
                source_ids,
                instructions=instructions,
                quantity=quantity,
                difficulty=difficulty,
            )
            return await self._call_live_generate(
                notebook_id, params, null_result_artifact_type="flashcards"
            )
        return self._artifacts.generate(notebook_id, ArtifactType.FLASHCARDS)

    async def generate_infographic(
        self,
        notebook_id: str,
        source_ids: list[str] | None = None,
        language: str | None = "en",
        instructions: str | None = None,
        orientation: InfographicOrientation | None = None,
        detail_level: InfographicDetail | None = None,
        style: InfographicStyle | None = None,
    ) -> GenerationStatus:
        if self._live_rpc is not None:
            source_ids = await self._resolved_source_ids(notebook_id, source_ids)
            params = build_infographic_artifact_params(
                notebook_id,
                source_ids,
                language=get_default_language() if language is None else language,
                instructions=instructions,
                orientation=orientation,
                detail_level=detail_level,
                style=style,
            )
            return await self._call_live_generate(
                notebook_id, params, null_result_artifact_type="infographic"
            )
        return self._artifacts.generate(notebook_id, ArtifactType.INFOGRAPHIC)

    async def generate_mind_map(
        self,
        notebook_id: str,
        source_ids: list[str] | None = None,
        language: str | None = "en",
        instructions: str | None = None,
    ) -> MindMapResult:
        if self._live_rpc is not None:
            if self._note_creator is None:
                raise ValidationError("live mind map generation requires note creator")
            source_ids = await self._resolved_source_ids(notebook_id, source_ids)
            params = build_mind_map_params(
                source_ids,
                language=get_default_language() if language is None else language,
                instructions=instructions,
            )
            result = await self._live_rpc.rpc_call(
                RPCMethod.GENERATE_MIND_MAP,
                params,
                source_path=f"/notebook/{notebook_id}",
                allow_null=True,
                operation_variant=None,
            )
            if result and isinstance(result, list) and result:
                inner = result[0]
                if isinstance(inner, list) and inner:
                    mind_map_json = inner[0]
                    if isinstance(mind_map_json, str):
                        try:
                            mind_map = json.loads(mind_map_json)
                        except json.JSONDecodeError:
                            mind_map = mind_map_json
                            mind_map_json = str(mind_map_json)
                    else:
                        mind_map = mind_map_json
                        mind_map_json = json.dumps(mind_map_json)
                    title = "Mind Map"
                    if isinstance(mind_map, dict):
                        name = mind_map.get("name")
                        if isinstance(name, str) and name:
                            title = name
                    note = self._note_creator(notebook_id, title, mind_map_json)
                    if isawaitable(note):
                        note = await note
                    return MindMapResult(
                        mind_map=mind_map, note_id=getattr(note, "id", None) or None
                    )
            return MindMapResult(mind_map=None, note_id=None)
        status = self._artifacts.generate(notebook_id, ArtifactType.MIND_MAP)
        return MindMapResult(
            mind_map={"name": "Synthetic Mind Map", "children": []},
            note_id=status.task_id,
        )

    async def generate_quiz(
        self,
        notebook_id: str,
        source_ids: list[str] | None = None,
        instructions: str | None = None,
        quantity: QuizQuantity | None = None,
        difficulty: QuizDifficulty | None = None,
    ) -> GenerationStatus:
        if self._live_rpc is not None:
            source_ids = await self._resolved_source_ids(notebook_id, source_ids)
            params = build_quiz_artifact_params(
                notebook_id,
                source_ids,
                instructions=instructions,
                quantity=quantity,
                difficulty=difficulty,
            )
            return await self._call_live_generate(
                notebook_id, params, null_result_artifact_type="quiz"
            )
        return self._artifacts.generate(notebook_id, ArtifactType.QUIZ)

    async def generate_report(
        self,
        notebook_id: str,
        report_format: ReportFormat = ReportFormat.BRIEFING_DOC,
        source_ids: list[str] | None = None,
        language: str | None = "en",
        custom_prompt: str | None = None,
        extra_instructions: str | None = None,
    ) -> GenerationStatus:
        if self._live_rpc is not None:
            source_ids = await self._resolved_source_ids(notebook_id, source_ids)
            params = build_report_artifact_params(
                notebook_id,
                source_ids,
                report_format=report_format,
                language=get_default_language() if language is None else language,
                custom_prompt=custom_prompt,
                extra_instructions=extra_instructions,
            )
            return await self._call_live_generate(
                notebook_id, params, null_result_artifact_type="report"
            )
        return self._artifacts.generate(notebook_id, ArtifactType.REPORT)

    async def generate_slide_deck(
        self,
        notebook_id: str,
        source_ids: list[str] | None = None,
        language: str | None = "en",
        instructions: str | None = None,
        slide_format: SlideDeckFormat | None = None,
        slide_length: SlideDeckLength | None = None,
    ) -> GenerationStatus:
        if self._live_rpc is not None:
            source_ids = await self._resolved_source_ids(notebook_id, source_ids)
            params = build_slide_deck_artifact_params(
                notebook_id,
                source_ids,
                language=get_default_language() if language is None else language,
                instructions=instructions,
                slide_format=slide_format,
                slide_length=slide_length,
            )
            return await self._call_live_generate(
                notebook_id, params, null_result_artifact_type="slide deck"
            )
        return self._artifacts.generate(notebook_id, ArtifactType.SLIDE_DECK)

    async def generate_study_guide(
        self,
        notebook_id: str,
        source_ids: list[str] | None = None,
        language: str | None = "en",
        extra_instructions: str | None = None,
    ) -> GenerationStatus:
        if self._live_rpc is not None:
            return await self.generate_report(
                notebook_id,
                report_format=ReportFormat.STUDY_GUIDE,
                source_ids=source_ids,
                language=get_default_language() if language is None else language,
                extra_instructions=extra_instructions,
            )
        return self._artifacts.generate(
            notebook_id, ArtifactType.REPORT, title="Synthetic Study Guide"
        )

    async def generate_video(
        self,
        notebook_id: str,
        source_ids: list[str] | None = None,
        language: str | None = "en",
        instructions: str | None = None,
        video_format: VideoFormat | None = None,
        video_style: VideoStyle | None = None,
        style_prompt: str | None = None,
    ) -> GenerationStatus:
        video_format = _coerce_video_format(video_format)
        video_style = _coerce_video_style(video_style)
        normalized_style_prompt = style_prompt.strip() if style_prompt is not None else None
        if video_format == VideoFormat.CINEMATIC and normalized_style_prompt:
            raise ValidationError("style_prompt is not supported for cinematic videos")
        if video_style == VideoStyle.CUSTOM and not normalized_style_prompt:
            raise ValidationError("style_prompt is required when video_style is CUSTOM")
        if normalized_style_prompt and video_style != VideoStyle.CUSTOM:
            raise ValidationError("style_prompt requires video_style=VideoStyle.CUSTOM")
        if self._live_rpc is not None:
            source_ids = await self._resolved_source_ids(notebook_id, source_ids)
            params = build_video_artifact_params(
                notebook_id,
                source_ids,
                language=get_default_language() if language is None else language,
                instructions=instructions,
                video_format=video_format,
                video_style=video_style,
                style_prompt=normalized_style_prompt,
            )
            return await self._call_live_generate(
                notebook_id, params, null_result_artifact_type="video"
            )
        return self._artifacts.generate(notebook_id, ArtifactType.VIDEO)

    async def poll_status(self, notebook_id: str, task_id: str) -> GenerationStatus:
        if self._live_rpc is not None:
            for row in await self._live_raw_artifacts(notebook_id):
                if isinstance(row, list) and row and row[0] == task_id:
                    artifact = Artifact.from_api_response(row)
                    return GenerationStatus(
                        task_id=task_id,
                        status=artifact_status_to_str(artifact.status),
                        url=artifact.url,
                    )
            return GenerationStatus(task_id=task_id, status="not_found")
        return self._status_fixtures.get_artifact_status(notebook_id, task_id)

    async def rename(
        self,
        notebook_id: str,
        artifact_id: str,
        new_title: str,
        *,
        return_object: bool = True,
    ) -> Artifact | None:
        if self._live_rpc is not None:
            await self._live_rpc.rpc_call(
                RPCMethod.RENAME_ARTIFACT,
                [[artifact_id, new_title], [["title"]]],
                source_path=f"/notebook/{notebook_id}",
                allow_null=True,
            )
            if not return_object and not _future_errors_enabled():
                return None
            for row in await self._live_raw_artifacts(notebook_id):
                if isinstance(row, list) and row and row[0] == artifact_id:
                    artifact = Artifact.from_api_response(row)
                    if artifact is not None:
                        return artifact
            raise ArtifactNotFoundError(
                artifact_id, method_id=RPCMethod.RENAME_ARTIFACT.value
            )
        artifact = self._artifacts.get(notebook_id, artifact_id)
        if artifact is None:
            if return_object or _future_errors_enabled():
                raise ArtifactNotFoundError(
                    artifact_id, method_id=RPCMethod.RENAME_ARTIFACT.value
                )
            return None
        return self._artifacts.rename(
            notebook_id,
            artifact_id,
            new_title,
            return_object=return_object,
        )

    async def retry_failed(self, notebook_id: str, artifact_id: str) -> Any:
        if self._live_rpc is not None:
            result = await self._live_rpc.rpc_call(
                RPCMethod.RETRY_ARTIFACT,
                build_retry_artifact_params(artifact_id),
                source_path=f"/notebook/{notebook_id}",
                allow_null=True,
            )
            status = _parse_generation_result(
                result, method_id=RPCMethod.RETRY_ARTIFACT.value
            )
            if not status.task_id:
                raise ArtifactFeatureUnavailableError(
                    "retry", method_id=RPCMethod.RETRY_ARTIFACT.value
                )
            return status
        artifact = self._artifacts.get(notebook_id, artifact_id)
        if artifact is None:
            raise ValidationError("artifact not found")
        return GenerationStatus(
            task_id=artifact_id, status="completed", url=artifact.url
        )

    async def revise_slide(
        self,
        notebook_id: str,
        artifact_id: str,
        slide_index: int,
        prompt: str,
    ) -> GenerationStatus:
        if slide_index < 0:
            raise ValidationError("slide index must be non-negative")
        if self._live_rpc is not None:
            try:
                result = await self._live_rpc.rpc_call(
                    RPCMethod.REVISE_SLIDE,
                    build_revise_slide_params(artifact_id, slide_index, prompt),
                    source_path=f"/notebook/{notebook_id}",
                    allow_null=True,
                )
            except RPCError as exc:
                if exc.rpc_code == "USER_DISPLAYABLE_ERROR" and not _future_errors_enabled():
                    return GenerationStatus(
                        task_id="",
                        status="failed",
                        error=str(exc),
                        error_code=str(exc.rpc_code) if exc.rpc_code is not None else None,
                    )
                raise
            return _parse_generation_result(
                result, method_id=RPCMethod.REVISE_SLIDE.value
            )
        if prompt.strip() == "":
            raise ValidationError("slide revision prompt is required")
        if self._artifacts.get(notebook_id, artifact_id) is None:
            raise ValidationError("artifact not found")
        task_id = f"{artifact_id}-slide-{slide_index}-revision"
        return GenerationStatus(
            task_id=task_id,
            status="completed",
            url=f"https://example.test/notebooklm-bare/generated/{task_id}",
        )

    async def suggest_reports(self, notebook_id: str) -> list[ReportSuggestion]:
        if self._live_rpc is not None:
            result = await self._live_rpc.rpc_call(
                RPCMethod.GET_SUGGESTED_REPORTS,
                build_suggest_reports_params(notebook_id),
                source_path=f"/notebook/{notebook_id}",
                allow_null=True,
            )
            suggestions: list[ReportSuggestion] = []
            items = result
            if isinstance(result, list) and len(result) == 1 and isinstance(result[0], list):
                inner = result[0]
                if not inner or isinstance(inner[0], list):
                    items = inner
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, list) and len(item) >= 5:
                        suggestions.append(
                            ReportSuggestion(
                                title=item[0] if isinstance(item[0], str) else "",
                                description=item[1] if isinstance(item[1], str) else "",
                                prompt=item[4] if isinstance(item[4], str) else "",
                                audience_level=item[5] if len(item) > 5 else 2,
                            )
                        )
            return suggestions
        return self._status_fixtures.suggest_reports(notebook_id)

    async def wait_for_completion(
        self,
        notebook_id: str,
        task_id: str,
        initial_interval: float = 2.0,
        max_interval: float = 10.0,
        timeout: float = 300.0,
        max_not_found: int = 5,
        min_not_found_window: float = 10.0,
        on_status_change: Callable[[GenerationStatus], object] | None = None,
    ) -> GenerationStatus:
        if self._live_rpc is not None:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout
            interval = initial_interval
            last_status: str | None = None
            while True:
                status = await self.poll_status(notebook_id, task_id)
                if on_status_change is not None and status.status != last_status:
                    maybe_result = on_status_change(status)
                    if isawaitable(maybe_result):
                        await maybe_result
                    last_status = status.status
                if status.is_complete or status.is_failed or status.is_removed:
                    return status
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise ArtifactTimeoutError(
                        notebook_id,
                        task_id,
                        timeout,
                        last_status=last_status,
                    )
                await asyncio.sleep(min(interval, remaining))
                interval = min(max_interval, interval * 1.5)
        return self._status_fixtures.wait_for_artifact(notebook_id, task_id)

    async def _live_raw_artifacts(self, notebook_id: str) -> list[Any]:
        result = await self._live_rpc.rpc_call(
            RPCMethod.LIST_ARTIFACTS,
            [[2], notebook_id, 'NOT artifact.status = "ARTIFACT_STATUS_SUGGESTED"'],
            source_path=f"/notebook/{notebook_id}",
            allow_null=True,
        )
        if isinstance(result, list) and len(result) == 1 and isinstance(result[0], list):
            inner = result[0]
            if not inner or isinstance(inner[0], list):
                return inner
        return result if isinstance(result, list) else []

    async def _live_mind_map_rows(self, notebook_id: str) -> list[Any]:
        result = await self._live_rpc.rpc_call(
            RPCMethod.GET_NOTES_AND_MIND_MAPS,
            [notebook_id],
            source_path=f"/notebook/{notebook_id}",
            allow_null=True,
        )
        rows = self._live_note_row_container(result)
        normalized: list[Any] = []
        for row in rows:
            item = self._normalize_live_note_row(row)
            if item is None or self._live_note_deleted(item):
                continue
            content = self._live_note_content(item)
            if content and content.startswith("{") and (
                '"children":' in content or '"nodes":' in content
            ):
                normalized.append(item)
        return normalized

    @classmethod
    def _live_note_row_container(cls, result: Any) -> list[Any]:
        if not result or not isinstance(result, list):
            return []
        first = result[0]
        if cls._is_live_note_row_like(first):
            return result
        if isinstance(first, list):
            return first
        return []

    @classmethod
    def _normalize_live_note_row(cls, item: Any) -> list[Any] | None:
        if not cls._is_live_note_row_like(item):
            return None
        if isinstance(item[0], str):
            return item
        nested = item[1]
        return [nested[0], nested, *item[2:]]

    @staticmethod
    def _is_live_note_row_like(item: Any) -> bool:
        if not isinstance(item, list) or not item:
            return False
        if isinstance(item[0], str):
            return True
        if item[0] is not None or len(item) <= 1:
            return False
        nested = item[1]
        return isinstance(nested, list) and bool(nested) and isinstance(nested[0], str)

    @staticmethod
    def _live_note_deleted(row: list[Any]) -> bool:
        return len(row) > 2 and row[1] is None and row[2] == 2

    @staticmethod
    def _live_note_content(row: list[Any]) -> str | None:
        if len(row) <= 1:
            return None
        slot = row[1]
        if isinstance(slot, str):
            return slot
        if isinstance(slot, list) and len(slot) > 1 and isinstance(slot[1], str):
            return slot[1]
        return None

    @staticmethod
    def _artifact_matches(
        artifact: Artifact, artifact_type: ArtifactType | None
    ) -> bool:
        return artifact_type is None or artifact.kind() == artifact_type


__all__ = [
    "Artifact",
    "ArtifactStatus",
    "ArtifactType",
    "ArtifactsAPI",
    "OfflineArtifactService",
    "parse_list_artifacts_payload",
]
