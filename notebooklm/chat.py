"""Offline fixture-backed chat API foothold.

Phase 3A7 exposes a tiny local ``ChatAPI`` surface that decodes only committed
synthetic ``chat_ask`` fixtures through the fake RPC seam. It keeps real service
calls, authentication stores, browser state, and NotebookLM mutation outside this
phase.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from typing import Any, NoReturn
from urllib.parse import quote, urlencode

from . import auth as _auth_module
from .config import get_default_language
from .errors import ValidationError
from .fake_rpc import OfflineFixtureRpcClient, chat_ask_request
from .rpc.encoder import nest_source_ids
from .rpc.types import ChatGoal, ChatMode, ChatResponseLength, RPCMethod, get_query_url
from .exceptions import ChatResponseParseError
from .types import AskResult, ChatReference, ConversationTurn, Note
from .utils import _future_errors_enabled

_DEFAULT_BL = "boq_labs-tailwind-frontend_20260301.03_p0"
_TEXT_RENDER_FLAGS: tuple[int | None, ...] = (0, 0, 0, None, None, None, None, 0, 0)


def _fail(reason: str) -> NoReturn:
    raise ValidationError(f"invalid chat ask payload: {reason}")


def _default_bl() -> str:
    return os.getenv("NOTEBOOKLM_BL", "").strip() or _DEFAULT_BL


def parse_chat_ask_payload(payload: Any) -> tuple[str, list[ChatReference]]:
    """Parse the decoded synthetic chat answer payload."""

    if not isinstance(payload, list) or len(payload) < 1:
        _fail("expected answer row")
    answer = payload[0]
    if not isinstance(answer, str) or answer == "":
        _fail("answer must be non-empty text")
    references_raw = payload[1] if len(payload) > 1 else []
    if not isinstance(references_raw, list):
        _fail("references must be a list")
    references: list[ChatReference] = []
    for item in references_raw:
        if isinstance(item, dict):
            source_id = item.get("source_id")
            if not isinstance(source_id, str) or source_id == "":
                _fail("reference source id must be non-empty text")
            references.append(ChatReference(source_id=source_id))
        elif isinstance(item, list) and item and isinstance(item[0], str) and item[0]:
            references.append(ChatReference(source_id=item[0]))
        else:
            _fail("reference row is malformed")
    return answer, references


def _build_passage_group(text: str, end_char: int) -> list[Any]:
    return [
        [
            0,
            end_char,
            [[[0, end_char, [text, list(_TEXT_RENDER_FLAGS)]]], [None, 1]],
        ]
    ]


def _build_source_passage_descriptor(ref: ChatReference) -> list[Any]:
    cited_text = ref.cited_text or ""
    if cited_text:
        source_start = ref.start_char if ref.start_char is not None else 0
        source_end = ref.end_char if ref.end_char is not None else len(cited_text)
    else:
        source_start = 0
        source_end = 0
    fourth_uuid = ref.passage_id if ref.passage_id is not None else ref.chunk_id
    return [
        None,
        None,
        None,
        [[None, source_start, source_end]],
        [_build_passage_group(cited_text, len(cited_text))],
        [[[fourth_uuid], ref.source_id]],
        [ref.chunk_id],
    ]


def _strip_citation_markers(answer_text: str) -> tuple[str, list[tuple[int, int]]]:
    import re

    positions: list[tuple[int, int]] = []
    clean_parts: list[str] = []
    last_end = 0
    clean_offset = 0
    for match in re.finditer(r" ?\[(\d+)\]", answer_text):
        chunk = answer_text[last_end : match.start()]
        clean_parts.append(chunk)
        clean_offset += len(chunk)
        positions.append((int(match.group(1)), clean_offset))
        last_end = match.end()
    clean_parts.append(answer_text[last_end:])
    return "".join(clean_parts), positions


def _resolve_reference(
    references: list[ChatReference], citation_number: int
) -> ChatReference | None:
    for ref in references:
        if ref.citation_number == citation_number and ref.chunk_id:
            return ref
    idx = citation_number - 1
    if 0 <= idx < len(references) and references[idx].chunk_id:
        return references[idx]
    return None


def _build_save_chat_as_note_params(
    notebook_id: str, answer_text: str, references: list[ChatReference], title: str
) -> list[Any]:
    if not references:
        raise ValueError(
            "save_chat_answer_as_note requires non-empty references; "
            "use notes.create() for plain-text notes."
        )
    clean_answer, marker_positions = _strip_citation_markers(answer_text)
    seen_chunks: list[str] = []
    chunk_to_ref: dict[str, ChatReference] = {}
    for ref in references:
        if ref.chunk_id and ref.chunk_id not in chunk_to_ref:
            seen_chunks.append(ref.chunk_id)
            chunk_to_ref[ref.chunk_id] = ref
    if not seen_chunks:
        raise ValueError(
            "save_chat_answer_as_note requires references with chunk_id set; "
            "got references without any usable chunk_id."
        )
    descriptors = {
        chunk_id: _build_source_passage_descriptor(chunk_to_ref[chunk_id])
        for chunk_id in seen_chunks
    }
    chunk_refs: list[Any] = []
    for citation_number, position in marker_positions:
        ref = _resolve_reference(references, citation_number)
        if ref is not None and ref.chunk_id is not None:
            chunk_refs.append([[ref.chunk_id], [None, 0, position]])
    return [
        notebook_id,
        answer_text,
        [2],
        [descriptors[chunk_id] for chunk_id in seen_chunks],
        title,
        [
            [_build_passage_group(clean_answer, len(clean_answer)), chunk_refs],
            None,
            None,
            [[[chunk_id], descriptors[chunk_id]] for chunk_id in seen_chunks],
            1,
        ],
        [2],
    ]


class ChatAPI:
    """Offline synthetic chat sub-client over the fake RPC seam."""

    def __init__(
        self,
        *,
        rpc: OfflineFixtureRpcClient,
        chat_timeout: float | None = 180.0,
        live_rpc: Any = None,
        source_ids_provider: Any = None,
    ) -> None:
        self._rpc = rpc
        self._chat_timeout = chat_timeout
        self._live_rpc = live_rpc
        self._source_ids_provider = source_ids_provider
        self._turns_by_conversation: dict[str, list[ConversationTurn]] = {}
        self._conversation_by_notebook: dict[str, str] = {}
        self._config_by_notebook: dict[str, dict[str, Any]] = {}
        self._saved_note_count = 0

    async def ask(
        self,
        notebook_id: str,
        question: str,
        source_ids: list[str] | None = None,
        conversation_id: str | None = None,
    ) -> AskResult:
        if source_ids is not None:
            if not isinstance(source_ids, list) or any(
                not isinstance(source_id, str) or source_id == ""
                for source_id in source_ids
            ):
                raise ValidationError("source ids must be non-empty text")
        if self._live_rpc is not None:
            return await self._live_ask(
                notebook_id,
                question,
                source_ids=source_ids,
                conversation_id=conversation_id,
            )
        request = chat_ask_request(notebook_id, question)
        payloads = self._rpc.call(request)
        if len(payloads) != 1:
            raise ValidationError("fake rpc response is not supported")
        answer, references = parse_chat_ask_payload(payloads[0])
        resolved_conversation_id = conversation_id or f"offline-chat-{notebook_id}"
        turns = self._turns_by_conversation.setdefault(resolved_conversation_id, [])
        turn_number = len(turns) + 1
        result = AskResult(
            answer=answer,
            conversation_id=resolved_conversation_id,
            turn_number=turn_number,
            is_follow_up=turn_number > 1,
            references=references,
            raw_response="",
        )
        turns.append(
            ConversationTurn(query=question, answer=answer, turn_number=turn_number)
        )
        self._conversation_by_notebook[notebook_id] = resolved_conversation_id
        return result

    async def _live_ask(
        self,
        notebook_id: str,
        question: str,
        *,
        source_ids: list[str] | None,
        conversation_id: str | None,
    ) -> AskResult:
        if source_ids is None:
            provider = self._source_ids_provider
            source_ids = []
            if provider is not None:
                value = provider(notebook_id)
                source_ids = await value if asyncio.iscoroutine(value) else value

        history = (
            self._build_conversation_history(conversation_id)
            if conversation_id
            else None
        )
        url, body = self._build_live_chat_request(
            notebook_id=notebook_id,
            question=question,
            source_ids=source_ids,
            conversation_history=history,
            conversation_id=conversation_id,
        )
        headers = {
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Cookie": self._live_rpc._cookie_header_for_url(url),
        }
        response = await asyncio.to_thread(
            self._live_rpc._post,
            url,
            body=body,
            headers=headers,
            timeout=self._chat_timeout,
            max_redirects=5,
        )
        answer, references, _stream_id = self._parse_streaming_chat_response(
            response.text()
        )
        resolved_conversation_id = conversation_id or await self.get_conversation_id(notebook_id)
        if not resolved_conversation_id:
            raise ChatResponseParseError("Server did not register a conversation for this ask")
        turns = self._turns_by_conversation.setdefault(resolved_conversation_id, [])
        turn_number = len(turns) + 1
        turns.append(
            ConversationTurn(query=question, answer=answer, turn_number=turn_number)
        )
        self._conversation_by_notebook[notebook_id] = resolved_conversation_id
        return AskResult(
            answer=answer,
            conversation_id=resolved_conversation_id,
            turn_number=turn_number,
            is_follow_up=conversation_id is not None,
            references=references,
            raw_response=response.text()[:1000],
        )

    def _build_live_chat_request(
        self,
        *,
        notebook_id: str,
        question: str,
        source_ids: list[str],
        conversation_history: list[Any] | None,
        conversation_id: str | None,
    ) -> tuple[str, str]:
        auth = self._live_rpc._auth
        params = [
            nest_source_ids(source_ids, 2),
            question,
            conversation_history,
            [2, None, [1], [1]],
            conversation_id,
            None,
            None,
            notebook_id,
            1,
        ]
        f_req_json = json.dumps(
            [None, json.dumps(params, separators=(",", ":"))],
            separators=(",", ":"),
        )
        body_parts = [f"f.req={quote(f_req_json, safe='')}"]
        if auth.csrf_token:
            body_parts.append(f"at={quote(auth.csrf_token, safe='')}")
        query = {
            "bl": _default_bl(),
            "hl": get_default_language(),
            "_reqid": str(len(self._turns_by_conversation) + 1),
            "rt": "c",
        }
        if auth.session_id:
            query["f.sid"] = auth.session_id
        if auth.account_email or auth.authuser:
            query["authuser"] = _auth_module.format_authuser_value(
                auth.authuser,
                auth.account_email,
            )
        return f"{get_query_url()}?{urlencode(query)}", "&".join(body_parts) + "&"

    @staticmethod
    def _parse_streaming_chat_response(
        text: str,
    ) -> tuple[str, list[ChatReference], str | None]:
        if text.startswith(")]}'"):
            text = text[4:]
        answer = ""
        stream_id = None
        parseable = False
        for raw_line in text.strip().splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                int(line)
                continue
            except ValueError:
                pass
            try:
                envelope = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(envelope, list):
                continue
            for item in envelope:
                if not isinstance(item, list) or len(item) < 3 or item[0] != "wrb.fr":
                    continue
                if not isinstance(item[2], str):
                    continue
                inner = json.loads(item[2])
                if (
                    not isinstance(inner, list)
                    or not inner
                    or not isinstance(inner[0], list)
                ):
                    continue
                first = inner[0]
                parseable = True
                if first and isinstance(first[0], str):
                    answer = first[0]
                conv_block = (
                    first[2] if len(first) > 2 and isinstance(first[2], list) else None
                )
                if conv_block and isinstance(conv_block[0], str):
                    stream_id = conv_block[0]
        if not parseable:
            raise ChatResponseParseError("No parseable chunks in streaming chat response")
        return answer, [], stream_id

    def _build_conversation_history(
        self, conversation_id: str | None
    ) -> list[Any] | None:
        if conversation_id is None:
            return None
        turns = self._turns_by_conversation.get(conversation_id, [])
        if not turns:
            return None
        history: list[Any] = []
        for turn in turns:
            history.append([turn.answer, None, 2])
            history.append([turn.query, None, 1])
        return history

    async def get_history(
        self,
        notebook_id: str,
        limit: int = 100,
        conversation_id: str | None = None,
    ) -> list[tuple[str, str]]:
        if self._live_rpc is not None:
            selected = conversation_id or await self.get_conversation_id(notebook_id)
            if not selected:
                return []
            turns_data = await self.get_conversation_turns(
                notebook_id, selected, limit=limit
            )
            if (
                turns_data
                and isinstance(turns_data, list)
                and turns_data[0]
                and isinstance(turns_data[0], list)
            ):
                turns_data = [list(reversed(turns_data[0]))]
            return self._parse_turns_to_qa_pairs(turns_data)
        selected = conversation_id or self._conversation_by_notebook.get(notebook_id)
        if selected is None:
            return []
        turns = self._turns_by_conversation.get(selected, [])[-limit:]
        return [(turn.query, turn.answer) for turn in turns]

    @staticmethod
    def _parse_turns_to_qa_pairs(turns_data: Any) -> list[tuple[str, str]]:
        if not turns_data or not isinstance(turns_data, list):
            return []
        first = turns_data[0]
        if not isinstance(first, list):
            return []
        pairs: list[tuple[str, str]] = []
        i = 0
        while i < len(first):
            turn = first[i]
            if not isinstance(turn, list) or len(turn) < 3:
                i += 1
                continue
            if turn[2] == 1 and len(turn) > 3:
                question = str(turn[3] or "")
                answer = ""
                if i + 1 < len(first):
                    next_turn = first[i + 1]
                    if (
                        isinstance(next_turn, list)
                        and len(next_turn) > 4
                        and next_turn[2] == 2
                    ):
                        try:
                            answer = str(next_turn[4][0][0] or "")
                        except (IndexError, TypeError):
                            answer = ""
                        i += 1
                pairs.append((question, answer))
            i += 1
        return pairs

    def get_cached_turns(self, conversation_id: str) -> list[ConversationTurn]:
        return list(self._turns_by_conversation.get(conversation_id, ()))

    def cache_size(self) -> int:
        return sum(len(turns) for turns in self._turns_by_conversation.values())

    def clear_cache(self, conversation_id: str | None = None) -> bool:
        if conversation_id is None:
            had_entries = bool(self._turns_by_conversation)
            self._turns_by_conversation.clear()
            self._conversation_by_notebook.clear()
            return had_entries
        removed = self._turns_by_conversation.pop(conversation_id, None) is not None
        if removed:
            self._conversation_by_notebook = {
                notebook_id: cached_id
                for notebook_id, cached_id in self._conversation_by_notebook.items()
                if cached_id != conversation_id
            }
        return removed

    async def configure(
        self,
        notebook_id: str,
        goal: ChatGoal | None = None,
        response_length: ChatResponseLength | None = None,
        custom_prompt: str | None = None,
    ) -> None:
        if self._live_rpc is not None:
            if goal is None:
                goal = ChatGoal.DEFAULT
            if response_length is None:
                response_length = ChatResponseLength.DEFAULT
            if goal == ChatGoal.CUSTOM and not custom_prompt:
                raise ValidationError("custom_prompt is required when goal is CUSTOM")
            goal_array = (
                [goal.value, custom_prompt] if goal == ChatGoal.CUSTOM else [goal.value]
            )
            await self._live_rpc.rpc_call(
                RPCMethod.RENAME_NOTEBOOK,
                [
                    notebook_id,
                    [
                        [
                            None,
                            None,
                            None,
                            None,
                            None,
                            None,
                            None,
                            [goal_array, [response_length.value]],
                        ]
                    ],
                ],
                source_path=f"/notebook/{notebook_id}",
                allow_null=True,
            )
            return None
        if custom_prompt is not None and not isinstance(custom_prompt, str):
            raise ValidationError("custom prompt must be text")
        if goal is not None:
            goal = ChatGoal(goal)
        if response_length is not None:
            response_length = ChatResponseLength(response_length)
        self._config_by_notebook.setdefault(notebook_id, {}).update(
            {
                "goal": goal,
                "response_length": response_length,
                "custom_prompt": custom_prompt,
            }
        )

    async def delete_conversation(self, notebook_id: str, conversation_id: str) -> bool:
        if self._live_rpc is not None:
            await self._live_rpc.rpc_call(
                RPCMethod.DELETE_CONVERSATION,
                [[], conversation_id, None, 1],
                source_path=f"/notebook/{notebook_id}",
            )
            self.clear_cache(conversation_id)
        else:
            self.clear_cache(conversation_id)
        return None if _future_errors_enabled() else True

    async def get_conversation_id(self, notebook_id: str) -> str | None:
        if self._live_rpc is not None:
            raw = await self._live_rpc.rpc_call(
                RPCMethod.GET_LAST_CONVERSATION_ID,
                [[], None, notebook_id, 1],
                source_path=f"/notebook/{notebook_id}",
            )
            if raw and isinstance(raw, list):
                for group in raw:
                    if isinstance(group, list):
                        for conv in group:
                            if (
                                isinstance(conv, list)
                                and conv
                                and isinstance(conv[0], str)
                            ):
                                return conv[0]
            return None
        return self._conversation_by_notebook.get(notebook_id)

    async def get_conversation_turns(
        self, notebook_id: str, conversation_id: str, limit: int = 2
    ) -> Any:
        if self._live_rpc is not None:
            return await self._live_rpc.rpc_call(
                RPCMethod.GET_CONVERSATION_TURNS,
                [[], None, None, conversation_id, limit],
                source_path=f"/notebook/{notebook_id}",
            )
        if limit < 0:
            raise ValidationError("conversation turn limit must be non-negative")
        turns = self.get_cached_turns(conversation_id)
        return turns[-limit:] if limit else []

    async def save_answer_as_note(
        self, notebook_id: str, ask_result: AskResult, *, title: str | None = None
    ) -> Note:
        if not isinstance(ask_result, AskResult):
            raise ValidationError("ask result is required")
        if self._live_rpc is not None:
            resolved_title = (
                title
                if title is not None
                else f"Chat: {ask_result.answer[:50].strip().replace(chr(10), ' ')}"
            )
            result = await self._live_rpc.rpc_call(
                RPCMethod.CREATE_NOTE,
                _build_save_chat_as_note_params(
                    notebook_id,
                    ask_result.answer,
                    ask_result.references,
                    resolved_title,
                ),
                source_path=f"/notebook/{notebook_id}",
                operation_variant="saved_from_chat",
            )
            note_data = None
            if isinstance(result, list) and result:
                note_data = result[0] if isinstance(result[0], list) else result
            note_id = note_data[0] if isinstance(note_data, list) and note_data else None
            if not isinstance(note_id, str) or not note_id:
                raise RuntimeError("CREATE_NOTE returned no note ID for saved-from-chat request")
            server_title = (
                note_data[4]
                if isinstance(note_data, list)
                and len(note_data) > 4
                and isinstance(note_data[4], str)
                else resolved_title
            )
            return Note(
                id=note_id,
                notebook_id=notebook_id,
                title=server_title,
                content=ask_result.answer,
            )
        self._saved_note_count += 1
        note_title = title or "Saved Answer"
        content = f"**Q:** {self._question_for_result(ask_result)}\n\n**A:** {ask_result.answer}"
        return Note(
            id=f"offline-chat-note-{self._saved_note_count:04d}",
            notebook_id=notebook_id,
            title=note_title,
            content=content,
            created_at=datetime.fromtimestamp(self._saved_note_count, timezone.utc),
        )

    async def set_mode(self, notebook_id: str, mode: ChatMode) -> None:
        self._config_by_notebook.setdefault(notebook_id, {})["mode"] = ChatMode(mode)

    def reset_after_open(self) -> None:
        self.clear_cache()

    def set_bound_loop(self, loop: asyncio.AbstractEventLoop | None) -> None:
        return None

    def _question_for_result(self, ask_result: AskResult) -> str:
        turns = self.get_cached_turns(ask_result.conversation_id)
        index = ask_result.turn_number - 1
        if 0 <= index < len(turns):
            return turns[index].query
        return ""


__all__ = [
    "AskResult",
    "ChatAPI",
    "ChatReference",
    "ConversationTurn",
    "parse_chat_ask_payload",
]
