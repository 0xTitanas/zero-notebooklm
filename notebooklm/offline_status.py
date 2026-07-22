"""Committed offline read/status fixtures for Batch 3B1.

This module is deliberately stdlib-only and reads only sanitized files committed
under ``compat/offline_status_fixtures``.  It is the local seam for read-only
status surfaces that notebooklm-py exposes over live RPCs: output-language reads,
account limits/tier reads, generation polling, research polling, report
suggestions, and sharing-status reads.  Mutation, export/download, public share
changes, live RPC, auth stores, browser state, and credentials stay out of
scope.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .rpc.types import ResearchStatus, ShareAccess, SharePermission, ShareViewLevel
from .types import (
    AccountLimits,
    AccountTier,
    GenerationStatus,
    ReportSuggestion,
    ResearchSource,
    ResearchTask,
    ShareStatus,
    SharedUser,
)

SUPPORTED_LANGUAGES = {
    "en": "English",
    "zh_Hans": "中文（简体）",
    "zh_Hant": "中文（繁體）",
    "es": "Español",
    "es_419": "Español (Latinoamérica)",
    "es_MX": "Español (México)",
    "hi": "हिन्दी",
    "ar_001": "العربية",
    "ar_eg": "العربية (مصر)",
    "pt_BR": "Português (Brasil)",
    "pt_PT": "Português (Portugal)",
    "bn": "বাংলা",
    "ru": "Русский",
    "ja": "日本語",
    "pa": "ਪੰਜਾਬੀ",
    "de": "Deutsch",
    "jv": "Basa Jawa",
    "ko": "한국어",
    "fr": "Français",
    "fr_CA": "Français (Canada)",
    "te": "తెలుగు",
    "vi": "Tiếng Việt",
    "mr": "मराठी",
    "ta": "தமிழ்",
    "tr": "Türkçe",
    "ur": "اردو",
    "it": "Italiano",
    "th": "ไทย",
    "gu": "ગુજરાતી",
    "fa": "فارسی",
    "pl": "Polski",
    "uk": "Українська",
    "ml": "മലയാളം",
    "kn": "ಕನ್ನಡ",
    "or": "ଓଡ଼ିଆ",
    "my": "မြန်မာဘာသာ",
    "sw": "Kiswahili",
    "nl_NL": "Nederlands",
    "ro": "Română",
    "hu": "Magyar",
    "el": "Ελληνικά",
    "cs": "Čeština",
    "sv": "Svenska",
    "be": "Беларуская",
    "bg": "Български",
    "hr": "Hrvatski",
    "sk": "Slovenčina",
    "da": "Dansk",
    "fi": "Suomi",
    "nb_NO": "Norsk Bokmål",
    "nn_NO": "Norsk Nynorsk",
    "he": "עברית",
    "iw": "עברית",
    "id": "Bahasa Indonesia",
    "ms": "Bahasa Melayu",
    "fil": "Filipino",
    "ceb": "Cebuano",
    "sr": "Српски",
    "sl": "Slovenščina",
    "sq": "Shqip",
    "mk": "Македонски",
    "lt": "Lietuvių",
    "lv": "Latviešu",
    "et": "Eesti",
    "hy": "Հայերեն",
    "ka": "ქართული",
    "az": "Azərbaycanca",
    "af": "Afrikaans",
    "am": "አማርኛ",
    "eu": "Euskara",
    "ca": "Català",
    "gl": "Galego",
    "is": "Íslenska",
    "la": "Latina",
    "ne": "नेपाली",
    "ps": "پښتو",
    "sd": "سنڌي",
    "si": "සිංහල",
    "ht": "Kreyòl Ayisyen",
    "kok": "कोंकणी",
    "mai": "मैथिली",
}


def default_status_fixture_path() -> Path:
    """Return the committed sanitized Batch 3B1 read/status fixture."""

    return (
        Path(__file__).resolve().parent.parent
        / "compat"
        / "offline_status_fixtures"
        / "phase3b1_readonly_status.json"
    )


def language_name(code: str) -> str | None:
    """Return the upstream display name for a supported output-language code."""

    value = SUPPORTED_LANGUAGES.get(code)
    return value if isinstance(value, str) else None


def _enum_by_name(enum_type: Any, name: str) -> Any:
    try:
        return enum_type[name.upper()]
    except KeyError:
        raise ValueError(f"unsupported enum name: {name}") from None


class OfflineReadOnlyStatusFixtures:
    """Typed view of the committed fixture-backed read/status parity data."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    @classmethod
    def load_default(cls) -> "OfflineReadOnlyStatusFixtures":
        return cls.from_path(default_status_fixture_path())

    @classmethod
    def from_path(cls, path: str | Path) -> "OfflineReadOnlyStatusFixtures":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("offline read/status fixture must be a JSON object")
        return cls(data)

    def get_output_language(self) -> str:
        settings = self._settings()
        language = settings.get("output_language", "en")
        if not isinstance(language, str) or language_name(language) is None:
            raise ValueError("offline output_language fixture is not supported")
        return language

    def get_account_limits(self) -> AccountLimits:
        raw = self._settings().get("account_limits", {})
        if not isinstance(raw, dict):
            raw = {}
        raw_limits = raw.get("raw_limits", ())
        if not isinstance(raw_limits, list):
            raw_limits = []
        return AccountLimits(
            notebook_limit=raw.get("notebook_limit"),
            source_limit=raw.get("source_limit"),
            raw_limits=tuple(raw_limits),
        )

    def get_account_tier(self) -> AccountTier:
        raw = self._settings().get("account_tier", {})
        if not isinstance(raw, dict):
            raw = {}
        return AccountTier(tier=raw.get("tier"), plan_name=raw.get("plan_name"))

    def get_artifact_status(self, notebook_id: str, task_id: str) -> GenerationStatus:
        by_notebook = self._data.get("artifact_statuses", {})
        rows = by_notebook.get(notebook_id, {}) if isinstance(by_notebook, dict) else {}
        raw = rows.get(task_id) if isinstance(rows, dict) else None
        if not isinstance(raw, dict):
            return GenerationStatus(task_id=task_id, status="not_found")
        return GenerationStatus(
            task_id=str(raw.get("task_id") or task_id),
            status=str(raw.get("status") or "not_found"),
            url=raw.get("url") if isinstance(raw.get("url"), str) else None,
            error=raw.get("error") if isinstance(raw.get("error"), str) else None,
            error_code=raw.get("error_code")
            if isinstance(raw.get("error_code"), str)
            else None,
            metadata=raw.get("metadata")
            if isinstance(raw.get("metadata"), dict)
            else None,
        )

    def wait_for_artifact(self, notebook_id: str, task_id: str) -> GenerationStatus:
        status = self.get_artifact_status(notebook_id, task_id)
        if status.status in {"completed", "failed", "removed"}:
            return status
        raise TimeoutError("artifact generation is still pending in offline fixture")

    def suggest_reports(self, notebook_id: str) -> list[ReportSuggestion]:
        by_notebook = self._data.get("report_suggestions", {})
        rows = by_notebook.get(notebook_id, []) if isinstance(by_notebook, dict) else []
        suggestions: list[ReportSuggestion] = []
        if not isinstance(rows, list):
            return suggestions
        for row in rows:
            if not isinstance(row, list) or len(row) < 5:
                continue
            suggestions.append(
                ReportSuggestion(
                    title=row[0] if isinstance(row[0], str) else "",
                    description=row[1] if isinstance(row[1], str) else "",
                    prompt=row[4] if isinstance(row[4], str) else "",
                    audience_level=row[5]
                    if len(row) > 5 and isinstance(row[5], int)
                    else 2,
                )
            )
        return suggestions

    def poll_research(
        self, notebook_id: str, task_id: str | None = None
    ) -> ResearchTask:
        tasks = self._research_tasks(notebook_id)
        if task_id:
            selected = [task for task in tasks if task.task_id == task_id]
            if not selected:
                return ResearchTask.not_found(task_id)
            task = selected[0]
            return ResearchTask(
                task_id=task.task_id,
                status=task.status,
                query=task.query,
                sources=task.sources,
                summary=task.summary,
                report=task.report,
                tasks=tuple(selected),
            )
        if not tasks:
            return ResearchTask.empty()
        task = tasks[0]
        return ResearchTask(
            task_id=task.task_id,
            status=task.status,
            query=task.query,
            sources=task.sources,
            summary=task.summary,
            report=task.report,
            tasks=tuple(tasks),
        )

    def wait_for_research(
        self, notebook_id: str, task_id: str | None = None
    ) -> ResearchTask:
        task = self.poll_research(notebook_id, task_id)
        if task.status in (ResearchStatus.COMPLETED, ResearchStatus.FAILED):
            return task
        if task.status == ResearchStatus.NO_RESEARCH:
            return task
        raise TimeoutError("research is still in progress in offline fixture")

    def get_share_status(self, notebook_id: str) -> ShareStatus:
        by_notebook = self._data.get("share_statuses", {})
        raw = by_notebook.get(notebook_id, {}) if isinstance(by_notebook, dict) else {}
        if not isinstance(raw, dict):
            raw = {}
        users: list[SharedUser] = []
        for user in (
            raw.get("shared_users", [])
            if isinstance(raw.get("shared_users"), list)
            else []
        ):
            if not isinstance(user, dict):
                continue
            permission = _enum_by_name(
                SharePermission, str(user.get("permission") or "VIEWER")
            )
            users.append(
                SharedUser(
                    email=str(user.get("email") or ""),
                    permission=permission,
                    display_name=user.get("display_name")
                    if isinstance(user.get("display_name"), str)
                    else None,
                    avatar_url=user.get("avatar_url")
                    if isinstance(user.get("avatar_url"), str)
                    else None,
                )
            )
        return ShareStatus(
            notebook_id=str(raw.get("notebook_id") or notebook_id),
            is_public=bool(raw.get("is_public", False)),
            access=_enum_by_name(ShareAccess, str(raw.get("access") or "RESTRICTED")),
            view_level=_enum_by_name(
                ShareViewLevel, str(raw.get("view_level") or "FULL_NOTEBOOK")
            ),
            shared_users=users,
            share_url=raw.get("share_url")
            if isinstance(raw.get("share_url"), str)
            else None,
        )

    def _settings(self) -> dict[str, Any]:
        settings = self._data.get("settings", {})
        return settings if isinstance(settings, dict) else {}

    def _research_tasks(self, notebook_id: str) -> list[ResearchTask]:
        by_notebook = self._data.get("research_tasks", {})
        rows = by_notebook.get(notebook_id, []) if isinstance(by_notebook, dict) else []
        if not isinstance(rows, list):
            return []
        tasks: list[ResearchTask] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            sources = tuple(
                self._research_source(src)
                for src in row.get("sources", [])
                if isinstance(src, dict)
            )
            status = ResearchStatus(
                str(row.get("status") or ResearchStatus.NO_RESEARCH.value)
            )
            tasks.append(
                ResearchTask(
                    task_id=str(row.get("task_id") or ""),
                    status=status,
                    query=str(row.get("query") or ""),
                    sources=sources,
                    summary=str(row.get("summary") or ""),
                    report=str(row.get("report") or ""),
                )
            )
        return tasks

    def _research_source(self, raw: dict[str, Any]) -> ResearchSource:
        return ResearchSource(
            url=str(raw.get("url") or ""),
            title=str(raw.get("title") or ""),
            result_type=raw.get("result_type", 1),
            research_task_id=(
                raw.get("research_task_id")
                if isinstance(raw.get("research_task_id"), str)
                else None
            ),
            report_markdown=str(raw.get("report_markdown") or ""),
        )


__all__ = [
    "OfflineReadOnlyStatusFixtures",
    "SUPPORTED_LANGUAGES",
    "default_status_fixture_path",
    "language_name",
]
