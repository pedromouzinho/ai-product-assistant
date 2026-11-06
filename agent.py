# =============================================================================
# agent.py — Agent loop com streaming SSE v7.0
# =============================================================================
# Suporta 2 modos de execução:
# 1. agent_chat() — request/response clássico (retorna AgentChatResponse)
# 2. agent_chat_stream() — SSE streaming (yield StreamEvents)
# =============================================================================

import json
import uuid
import asyncio
import inspect
import logging
import re
import time
import unicodedata
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, AsyncGenerator, Callable, Iterator
from collections.abc import MutableMapping

from config import (
    AGENT_MAX_ITERATIONS, AGENT_MAX_TOKENS, AGENT_TEMPERATURE,
    AGENT_HISTORY_LIMIT, LLM_DEFAULT_TIER, UPLOAD_MAX_IMAGES_PER_MESSAGE,
    UPLOAD_INDEX_TOP, DEVOPS_AREAS, CHAT_TOOLRESULT_BLOB_CONTAINER, PII_ENABLED,
    TOKEN_QUOTA_ENFORCEMENT_ENABLED, USER_DAILY_TOKEN_LIMIT,
    WRITER_PROFILE_LEARN_THRESHOLD,
)
from models import (
    AgentChatRequest, AgentChatResponse, LLMToolCall,
)
from llm_provider import (
    get_provider, llm_with_fallback, llm_stream_with_fallback,
    make_assistant_message_from_response, format_llm_error_for_user,
)
from token_counter import (
    count_messages_tokens,
    count_single_message_tokens,
    count_tools_tokens,
    resolve_context_window,
    RESPONSE_RESERVE_TOKENS,
)
from tool_metrics import tool_metrics
from agent_metrics import agent_metrics
import token_quota as _tq_module
from learning import get_learned_rules, get_few_shot_examples
from tool_registry import execute_tool, get_all_tool_definitions, validate_tool_arguments
from tool_registry import get_tool_runtime_profile
from tools import (
    truncate_tool_result,
    get_agent_system_prompt, get_userstory_system_prompt,
)
from storage import (
    table_insert,
    table_merge,
    table_query,
    blob_download_bytes,
    blob_upload_json,
    parse_blob_ref,
)
from utils import odata_escape
from pii_shield import PIIMaskingContext, mask_pii
from tabular_loader import is_tabular_filename
from data_dictionary import get_dictionary, format_dictionary_for_prompt
from tools_devops import issue_create_workitem_confirmation_token
from request_context import (
    format_request_context,
    get_request_id,
    reset_request_context,
    set_request_context,
)

# =============================================================================
# IN-MEMORY STORES (migra para persistent storage em fase futura)
# =============================================================================
MAX_CONVERSATIONS = 500
CONVERSATION_TTL_SECONDS = 4 * 3600
_PENDING_POLYMORPHIC_SELECTION_KEY = "pending_polymorphic_selection"


class ConversationStore(MutableMapping[str, List[dict]]):
    """Store em memória com TTL + eviction LRU + write-through dirty tracking."""

    def __init__(
        self,
        max_conversations: int,
        ttl_seconds: int,
        on_evict: Optional[Callable[[str], None]] = None,
    ):
        self._data: Dict[str, List[dict]] = {}
        self._last_accessed: Dict[str, datetime] = {}
        self.max_conversations = max_conversations
        self.ttl_seconds = ttl_seconds
        self._on_evict = on_evict
        self._lock = asyncio.Lock()
        # Write-through: dirty tracking
        self._dirty: set[str] = set()
        self._on_evict_persist: Optional[Callable] = None  # set externally after init

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)

    def _touch(self, key: str) -> None:
        self._last_accessed[key] = self._utcnow()

    def mark_dirty(self, key: str) -> None:
        """Marca conversa como modificada (precisa de persist)."""
        if key in self._data:
            self._dirty.add(key)

    def mark_clean(self, key: str) -> None:
        """Marca conversa como persistida (já não precisa de persist)."""
        self._dirty.discard(key)

    def get_dirty_keys(self) -> set[str]:
        """Devolve chaves que têm alterações por persistir."""
        return self._dirty & set(self._data.keys())

    def _evict(self, key: str, reason: str) -> None:
        # Write-through: persist snapshot before evicting dirty data
        if key in self._dirty and key in self._data and self._on_evict_persist:
            snapshot = list(self._data[key])  # shallow copy
            try:
                self._on_evict_persist(key, snapshot)
            except Exception:
                logging.getLogger(__name__).error(
                    "[ConversationStore] Pre-evict persist failed for %s",
                    key,
                    exc_info=True,
                )
        self._dirty.discard(key)
        self._data.pop(key, None)
        self._last_accessed.pop(key, None)
        if self._on_evict:
            self._on_evict(key)
        logging.getLogger(__name__).info("[ConversationStore] Evicted %s (%s)", key, reason)

    def _is_expired(self, key: str, now: datetime) -> bool:
        last = self._last_accessed.get(key)
        if not last:
            return False
        return (now - last).total_seconds() > self.ttl_seconds

    def cleanup_expired(self) -> List[str]:
        now = self._utcnow()
        expired = [k for k in list(self._data.keys()) if self._is_expired(k, now)]
        for key in expired:
            self._evict(key, reason="ttl")
        return expired

    def _evict_lru(self, exclude_key: Optional[str] = None) -> Optional[str]:
        candidates = [
            (ts, key)
            for key, ts in self._last_accessed.items()
            if key in self._data and key != exclude_key
        ]
        if not candidates:
            return None
        _, oldest_key = min(candidates, key=lambda item: item[0])
        self._evict(oldest_key, reason="lru")
        return oldest_key

    def ensure_capacity_for_new(self, new_key: str) -> None:
        self.cleanup_expired()
        while len(self._data) >= self.max_conversations:
            if self._evict_lru(exclude_key=new_key) is None:
                break

    def touch(self, key: str) -> None:
        if key in self._data:
            self._touch(key)

    async def async_get(self, key: str, default=None):
        """Thread-safe get com touch."""
        async with self._lock:
            if key in self._data:
                self._touch(key)
                return self._data[key]
            return default

    async def async_set(self, key: str, value: List[dict]) -> None:
        """Thread-safe set com capacity check."""
        async with self._lock:
            if key not in self._data:
                self.cleanup_expired()
                while len(self._data) >= self.max_conversations:
                    if self._evict_lru(exclude_key=key) is None:
                        break
            self._data[key] = value
            self._touch(key)
            self._dirty.add(key)

    async def async_delete(self, key: str) -> None:
        """Thread-safe delete."""
        async with self._lock:
            if key in self._data:
                self._evict(key, reason="manual")

    async def async_contains(self, key: str) -> bool:
        """Thread-safe contains check."""
        async with self._lock:
            return key in self._data

    async def async_cleanup_expired(self) -> List[str]:
        """Thread-safe cleanup."""
        async with self._lock:
            return self.cleanup_expired()

    def __getitem__(self, key: str) -> List[dict]:
        value = self._data[key]
        self._touch(key)
        return value

    def __setitem__(self, key: str, value: List[dict]) -> None:
        if key not in self._data:
            self.ensure_capacity_for_new(new_key=key)
        self._data[key] = value
        self._touch(key)
        self._dirty.add(key)

    def __delitem__(self, key: str) -> None:
        if key not in self._data:
            raise KeyError(key)
        self._evict(key, reason="manual")

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key: object) -> bool:
        return key in self._data

    def get(self, key: str, default=None):
        if key in self._data:
            self._touch(key)
            return self._data[key]
        return default

    def items(self):
        return self._data.items()

    def keys(self):
        return self._data.keys()

    def values(self):
        return self._data.values()


conversation_meta: Dict[str, Dict] = {}
_conversation_meta_lock = asyncio.Lock()
uploaded_files_store: Dict[str, Dict] = {}
_uploaded_files_lock = asyncio.Lock()
_conversation_locks: Dict[str, asyncio.Lock] = {}
_conversation_locks_guard = asyncio.Lock()


def _cleanup_conversation_related_state(conv_id: str) -> None:
    conversation_meta.pop(conv_id, None)
    uploaded_files_store.pop(conv_id, None)
    lock = _conversation_locks.get(conv_id)
    if lock is None:
        return
    if not lock.locked():
        _conversation_locks.pop(conv_id, None)
        return

    async def _deferred_lock_cleanup(cid: str, held_lock: asyncio.Lock) -> None:
        try:
            async with asyncio.timeout(30):
                async with held_lock:
                    pass
        except Exception:
            pass
        _conversation_locks.pop(cid, None)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_deferred_lock_cleanup(conv_id, lock))
    except RuntimeError:
        pass


conversations = ConversationStore(
    max_conversations=MAX_CONVERSATIONS,
    ttl_seconds=CONVERSATION_TTL_SECONDS,
    on_evict=_cleanup_conversation_related_state,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Write-through persistence — background loop + pre-evict + shutdown hook
# ---------------------------------------------------------------------------
_WRITETHROUGH_INTERVAL_SECONDS = 30
_writethrough_task: Optional[asyncio.Task] = None


def _pre_evict_persist(conv_id: str, snapshot: List[dict]) -> None:
    """Fire-and-forget persist of conversation snapshot before eviction."""
    meta = conversation_meta.get(conv_id, {})
    pk = meta.get("partition_key", "")
    if not pk:
        return
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_persist_conversation_snapshot(conv_id, pk, snapshot))
    except RuntimeError:
        pass


conversations._on_evict_persist = _pre_evict_persist


async def _persist_conversation_snapshot(
    conv_id: str, partition_key: str, snapshot: List[dict]
) -> None:
    """Persist a snapshot of messages (used for pre-evict write-through)."""
    try:
        compact = _prepare_messages_for_storage(snapshot)
        messages_json = json.dumps(compact, ensure_ascii=False, default=str)

        meta = conversation_meta.get(conv_id, {})
        entity = {
            "PartitionKey": partition_key,
            "RowKey": conv_id,
            "Messages": messages_json,
            "Mode": meta.get("mode", "general"),
            "CreatedAt": meta.get("created_at", datetime.now(timezone.utc).isoformat()),
            "UpdatedAt": datetime.now(timezone.utc).isoformat(),
            "MessageCount": len(compact),
        }

        inserted = await table_insert("ChatHistory", entity)
        if not inserted:
            await table_merge("ChatHistory", entity)
        logger.info("[WriteThrough] Pre-evict persist OK for %s", conv_id)
    except Exception as e:
        logger.error("[WriteThrough] Pre-evict persist failed for %s: %s", conv_id, e, exc_info=True)


async def _writethrough_loop() -> None:
    """Background loop: persists all dirty conversations every N seconds."""
    while True:
        try:
            await asyncio.sleep(_WRITETHROUGH_INTERVAL_SECONDS)
            dirty_keys = conversations.get_dirty_keys()
            if not dirty_keys:
                continue
            logger.info("[WriteThrough] %d dirty conversation(s) to persist", len(dirty_keys))
            for conv_id in list(dirty_keys):
                meta = conversation_meta.get(conv_id, {})
                pk = meta.get("partition_key", "")
                if not pk:
                    continue
                try:
                    await asyncio.wait_for(
                        _persist_conversation(conv_id, pk), timeout=10.0
                    )
                except Exception as e:
                    logger.warning(
                        "[WriteThrough] Background persist failed for %s: %s(%r)",
                        conv_id,
                        type(e).__name__,
                        e,
                    )
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning("[WriteThrough] Loop iteration failed: %s", e)


async def flush_dirty_conversations() -> int:
    """Persist all dirty conversations (used on shutdown). Returns count persisted."""
    dirty_keys = conversations.get_dirty_keys()
    if not dirty_keys:
        return 0
    logger.info("[WriteThrough] Shutdown flush: %d dirty conversation(s)", len(dirty_keys))
    persisted = 0
    for conv_id in list(dirty_keys):
        meta = conversation_meta.get(conv_id, {})
        pk = meta.get("partition_key", "")
        if not pk:
            continue
        try:
            await asyncio.wait_for(
                _persist_conversation(conv_id, pk), timeout=8.0
            )
            persisted += 1
        except Exception as e:
            logger.warning(
                "[WriteThrough] Shutdown persist failed for %s: %s(%r)",
                conv_id,
                type(e).__name__,
                e,
            )
    logger.info("[WriteThrough] Shutdown flush complete: %d/%d persisted", persisted, len(dirty_keys))
    return persisted


def start_writethrough_loop() -> None:
    """Start the background write-through loop. Call from app startup."""
    global _writethrough_task
    if _writethrough_task and not _writethrough_task.done():
        return
    _writethrough_task = asyncio.create_task(_writethrough_loop())
    logger.info("[WriteThrough] Background persist loop started (interval=%ds)", _WRITETHROUGH_INTERVAL_SECONDS)


def stop_writethrough_loop() -> None:
    """Cancel the background write-through loop. Call from app shutdown."""
    global _writethrough_task
    if _writethrough_task and not _writethrough_task.done():
        _writethrough_task.cancel()
    _writethrough_task = None


def _fallback_answer_from_tool_details(tool_details: List[dict]) -> str:
    for detail in reversed(tool_details or []):
        if not isinstance(detail, dict):
            continue
        summary = detail.get("result_summary", {}) if isinstance(detail.get("result_summary"), dict) else {}
        if summary.get("has_error"):
            continue
        total = summary.get("total_count")
        if total in (None, "", "N/A"):
            total = summary.get("items_returned", 0)
        tool_name = str(detail.get("tool", "consulta") or "consulta")
        try:
            total_int = int(total)
        except Exception:
            total_int = 0
        if total_int > 0:
            return (
                f"Encontrei {total_int} resultados via {tool_name}, "
                "mas não consegui gerar o resumo final. "
                "Pede \"lista completa\" para mostrar os itens diretamente."
            )
    return (
        "Consegui executar a pesquisa, mas não consegui gerar o texto final. "
        "Tenta novamente (preferencialmente em Fast) para resposta mais imediata."
    )


def _log_fallback_chain_if_needed(response) -> None:
    chain = getattr(response, "fallback_chain", None)
    if not isinstance(chain, list) or not chain:
        return
    failed = [p for p in chain if isinstance(p, dict) and p.get("status") == "failed"]
    if failed:
        ctx = format_request_context()
        prefix = f"[{ctx}] " if ctx else ""
        logger.warning("[Agent] %sLLM fallback used: %s", prefix, json.dumps(chain, ensure_ascii=False))


def _response_used_fallback(response) -> bool:
    """Check if LLM response used a fallback provider (primary failed)."""
    chain = getattr(response, "fallback_chain", None)
    if not isinstance(chain, list):
        return False
    return any(isinstance(p, dict) and p.get("status") == "failed" for p in chain)


def _llm_payload_text(payload) -> str:
    if isinstance(payload, dict):
        content = payload.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        error_detail = payload.get("error_detail")
        if isinstance(error_detail, str) and error_detail.strip():
            return error_detail.strip()
        return ""

    content = getattr(payload, "content", None)
    if isinstance(content, str) and content.strip():
        return content.strip()
    error_detail = getattr(payload, "error_detail", None)
    if isinstance(error_detail, str) and error_detail.strip():
        return error_detail.strip()
    return ""


def _normalize_tool_error_kind(status: str, tool_result: Any) -> str:
    if isinstance(tool_result, dict):
        explicit = str(tool_result.get("error_kind", "") or "").strip().lower()
        if explicit:
            return explicit
    normalized_status = str(status or "").strip().lower()
    if normalized_status == "blocked":
        return "blocked"
    if normalized_status == "exception":
        return "execution_exception"
    if normalized_status == "tool_error":
        if isinstance(tool_result, dict) and tool_result.get("validation_errors"):
            return "invalid_tool_arguments"
        return "tool_error"
    return ""


def _normalize_tool_error_detail(tool_result: Any) -> str:
    if not isinstance(tool_result, dict):
        return ""
    detail = tool_result.get("error_detail")
    if isinstance(detail, str) and detail.strip():
        return detail.strip()
    err = tool_result.get("error")
    if isinstance(err, str) and err.strip():
        return err.strip()
    return ""


def _normalize_tool_result_envelope(tool_result: Any, status: str) -> Any:
    if not isinstance(tool_result, dict):
        return tool_result
    payload = dict(tool_result)
    error_kind = _normalize_tool_error_kind(status, payload)
    error_detail = _normalize_tool_error_detail(payload)
    is_error = str(status or "").strip().lower() in {"blocked", "tool_error", "exception"} or "error" in payload
    if is_error:
        payload["is_error"] = True
        if error_kind and not payload.get("error_kind"):
            payload["error_kind"] = error_kind
        if error_detail and not payload.get("error_detail"):
            payload["error_detail"] = error_detail
    return payload


def _build_tool_result_summary(tool_result: Any, status: str) -> Dict[str, Any]:
    normalized_status = str(status or "ok").strip().lower() or "ok"
    if isinstance(tool_result, dict):
        total_count = tool_result.get("total_count", tool_result.get("total_results", tool_result.get("total_found", "N/A")))
        items = tool_result.get("items", tool_result.get("analysis_data", []))
        items_returned = len(items) if isinstance(items, list) else 0
        has_error = bool(tool_result.get("is_error")) or "error" in tool_result or normalized_status in {"blocked", "tool_error", "exception"}
        error_kind = _normalize_tool_error_kind(normalized_status, tool_result)
        error_detail = _normalize_tool_error_detail(tool_result)
        summary = {
            "total_count": total_count,
            "items_returned": items_returned,
            "has_error": has_error,
            "result_status": normalized_status,
        }
        if error_kind:
            summary["error_kind"] = error_kind
        if error_detail:
            summary["error_detail"] = error_detail[:500]
        return summary
    return {
        "total_count": "N/A",
        "items_returned": 0,
        "has_error": normalized_status in {"blocked", "tool_error", "exception"},
        "result_status": normalized_status,
    }


def _derive_request_status_from_llm_payload(current_status: str, payload) -> str:
    status = str(current_status or "ok").strip().lower() or "ok"
    if status != "ok" or payload is None:
        return status

    if isinstance(payload, dict):
        is_error = bool(payload.get("is_error"))
        error_kind = str(payload.get("error_kind", "") or "").strip().lower()
    else:
        is_error = bool(getattr(payload, "is_error", False))
        error_kind = str(getattr(payload, "error_kind", "") or "").strip().lower()

    if not is_error:
        return status
    if "blocked" in error_kind:
        return "blocked"
    return "error"


def _derive_request_error_kind_from_llm_payload(current_error_kind: str, payload) -> str:
    existing = str(current_error_kind or "").strip().lower()
    if existing or payload is None:
        return existing

    if isinstance(payload, dict):
        is_error = bool(payload.get("is_error"))
        error_kind = str(payload.get("error_kind", "") or "").strip().lower()
    else:
        is_error = bool(getattr(payload, "is_error", False))
        error_kind = str(getattr(payload, "error_kind", "") or "").strip().lower()

    if not is_error:
        return existing
    return error_kind


def _merge_usage_totals(current_usage: Dict[str, Any], new_usage: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    merged = dict(current_usage or {})
    if not isinstance(new_usage, dict):
        return merged
    for key, value in new_usage.items():
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            merged[key] = merged.get(key, 0) + value
    return merged


async def _run_tool_detection_llm_round(
    conv_id: str,
    effective_question: str,
    *,
    request: AgentChatRequest,
    tier: str,
    tool_definitions: List[dict],
    user_sub: str,
    total_usage: Dict[str, Any],
    model_used: str,
    is_fallback: bool,
):
    ephemeral = await _build_llm_messages(
        conv_id,
        effective_question,
        request=request,
        tier=tier,
        tools_list=tool_definitions,
        user_sub=user_sub,
    )
    response = await llm_with_fallback(
        ephemeral,
        tools=tool_definitions,
        tier=tier,
        temperature=AGENT_TEMPERATURE,
        max_tokens=AGENT_MAX_TOKENS,
    )
    _log_fallback_chain_if_needed(response)
    return (
        response,
        _merge_usage_totals(total_usage, getattr(response, "usage", {})),
        getattr(response, "model", "") or model_used,
        is_fallback or _response_used_fallback(response),
    )


async def _run_tool_call_iterations(
    initial_response,
    conv_id: str,
    effective_question: str,
    *,
    request: AgentChatRequest,
    tier: str,
    tool_definitions: List[dict],
    user_sub: str,
    tools_used: List[str],
    tool_details: List[Dict[str, Any]],
    total_usage: Dict[str, Any],
    model_used: str,
    is_fallback: bool,
    has_exportable: bool,
    export_idx: Optional[int],
    on_tool_start=None,
    on_tool_result=None,
    on_before_next_round=None,
):
    response = initial_response
    iteration = 0

    while iteration < AGENT_MAX_ITERATIONS:
        current_calls = list(getattr(response, "tool_calls", None) or [])
        if not current_calls:
            return (
                response,
                total_usage,
                model_used,
                is_fallback,
                has_exportable,
                export_idx,
                False,
            )

        iteration += 1

        conversations[conv_id].append(make_assistant_message_from_response(response))
        conversations.mark_dirty(conv_id)

        if on_tool_start:
            for tc in current_calls:
                maybe_value = on_tool_start(tc)
                if inspect.isawaitable(maybe_value):
                    await maybe_value

        tu, td = await _execute_tool_calls(
            current_calls,
            conv_id,
            user_sub=user_sub,
        )
        has_exportable, export_idx = _merge_tool_batch_results(
            tools_used,
            tool_details,
            tu,
            td,
            has_exportable=has_exportable,
            export_idx=export_idx,
        )

        if on_tool_result:
            for detail in td:
                maybe_value = on_tool_result(detail)
                if inspect.isawaitable(maybe_value):
                    await maybe_value

        if on_before_next_round:
            maybe_value = on_before_next_round(iteration)
            if inspect.isawaitable(maybe_value):
                await maybe_value

        response, total_usage, model_used, is_fallback = await _run_tool_detection_llm_round(
            conv_id,
            effective_question,
            request=request,
            tier=tier,
            tool_definitions=tool_definitions,
            user_sub=user_sub,
            total_usage=total_usage,
            model_used=model_used,
            is_fallback=is_fallback,
        )

    return (
        response,
        total_usage,
        model_used,
        is_fallback,
        has_exportable,
        export_idx,
        bool(getattr(response, "tool_calls", None)),
    )


async def _stream_final_response_payloads(
    response,
    conv_id: str,
    effective_question: str,
    *,
    request: AgentChatRequest,
    tier: str,
    user_sub: str,
    prefix: str,
    state: Dict[str, Any],
) -> AsyncGenerator[Dict[str, Any], None]:
    request_status = _derive_request_status_from_llm_payload(state.get("request_status", "ok"), response)
    request_error_kind = _derive_request_error_kind_from_llm_payload(state.get("request_error_kind", ""), response)
    total_usage = dict(state.get("total_usage") or {})
    model_used = str(state.get("model_used", "") or "")
    is_fallback = bool(state.get("is_fallback", False))
    static_content = _llm_payload_text(response)
    ttft_start = time.perf_counter()
    ttft_logged = False
    stream_content = ""

    try:
        stream_ephemeral = await _build_llm_messages(
            conv_id,
            effective_question,
            request=request,
            tier=tier,
            tools_list=None,
            user_sub=user_sub,
        )
        async for event in llm_stream_with_fallback(
            stream_ephemeral,
            tools=None,
            tier=tier,
            temperature=AGENT_TEMPERATURE,
            max_tokens=AGENT_MAX_TOKENS,
        ):
            if event.type == "token" and event.text:
                if not ttft_logged:
                    ttft_ms = int((time.perf_counter() - ttft_start) * 1000)
                    logger.info("[Agent] %sTTFT=%dms", prefix, ttft_ms)
                    ttft_logged = True
                stream_content += event.text
                yield {"type": "token", "text": event.text}
            elif event.type == "done":
                done_data = event.data or {}
                if isinstance(done_data, dict):
                    request_status = _derive_request_status_from_llm_payload(request_status, done_data)
                    request_error_kind = _derive_request_error_kind_from_llm_payload(request_error_kind, done_data)
                    total_usage = _merge_usage_totals(total_usage, done_data.get("usage", {}))
                    stream_chain = done_data.get("fallback_chain")
                    if isinstance(stream_chain, list) and any(
                        isinstance(p, dict) and p.get("status") == "failed" for p in stream_chain
                    ):
                        is_fallback = True
                    if done_data.get("model"):
                        model_used = done_data["model"]
                    if not stream_content:
                        done_content = _llm_payload_text(done_data)
                        if done_content:
                            stream_content = done_content
                            yield {"type": "token", "text": done_content}
    except Exception as stream_err:
        logger.warning("[Agent] %sstreaming fallback to static: %s", prefix, stream_err)
        if not stream_content and static_content:
            stream_content = static_content
            yield {"type": "token", "text": stream_content}
        elif not stream_content:
            stream_content = _format_agent_error_for_user(stream_err)
            yield {"type": "token", "text": stream_content}

    if not stream_content and static_content:
        stream_content = static_content
        yield {"type": "token", "text": stream_content}

    if stream_content:
        await _append_assistant_text(
            conv_id,
            stream_content,
            effective_question=effective_question,
            refresh_polymorphic=True,
        )
        state["should_persist"] = True
    else:
        yield {"type": "token", "text": "A chamada ao modelo terminou sem conteúdo."}

    state["request_status"] = request_status
    state["request_error_kind"] = request_error_kind
    state["total_usage"] = total_usage
    state["model_used"] = model_used
    state["is_fallback"] = is_fallback


async def _stream_tool_loop_payloads(
    conv_id: str,
    effective_question: str,
    *,
    request: AgentChatRequest,
    tier: str,
    tool_definitions: List[dict],
    user_sub: str,
    tools_used: List[str],
    tool_details: List[Dict[str, Any]],
    state: Dict[str, Any],
) -> AsyncGenerator[Dict[str, Any], None]:
    total_usage = dict(state.get("total_usage") or {})
    model_used = str(state.get("model_used", "") or "")
    is_fallback = bool(state.get("is_fallback", False))
    has_exportable = bool(state.get("has_exportable", False))
    export_idx = state.get("export_idx")

    yield {"type": "thinking", "text": "A analisar..."}
    response, total_usage, model_used, is_fallback = await _run_tool_detection_llm_round(
        conv_id,
        effective_question,
        request=request,
        tier=tier,
        tool_definitions=tool_definitions,
        user_sub=user_sub,
        total_usage=total_usage,
        model_used=model_used,
        is_fallback=is_fallback,
    )
    if not list(getattr(response, "tool_calls", None) or []):
        state["response"] = response
        state["total_usage"] = total_usage
        state["model_used"] = model_used
        state["is_fallback"] = is_fallback
        state["has_exportable"] = has_exportable
        state["export_idx"] = export_idx
        state["exhausted_tool_loop"] = False
        return

    event_queue: asyncio.Queue = asyncio.Queue()
    sentinel = object()

    async def _on_tool_start(tc):
        await event_queue.put({"type": "tool_start", "tool": tc.name, "text": f"🔍 {tc.name}..."})

    async def _on_tool_result(detail):
        await event_queue.put(_build_stream_tool_result_event(detail))

    async def _on_before_next_round(_iteration):
        await event_queue.put({"type": "thinking", "text": "A processar resultados..."})

    async def _run_iterations_task():
        try:
            return await _run_tool_call_iterations(
                response,
                conv_id,
                effective_question,
                request=request,
                tier=tier,
                tool_definitions=tool_definitions,
                user_sub=user_sub,
                tools_used=tools_used,
                tool_details=tool_details,
                total_usage=total_usage,
                model_used=model_used,
                is_fallback=is_fallback,
                has_exportable=has_exportable,
                export_idx=export_idx,
                on_tool_start=_on_tool_start,
                on_tool_result=_on_tool_result,
                on_before_next_round=_on_before_next_round,
            )
        finally:
            await event_queue.put(sentinel)

    task = asyncio.create_task(_run_iterations_task())
    try:
        while True:
            payload = await event_queue.get()
            if payload is sentinel:
                break
            yield payload
    finally:
        (
            response,
            total_usage,
            model_used,
            is_fallback,
            has_exportable,
            export_idx,
            exhausted_tool_loop,
        ) = await task

    state["response"] = response
    state["total_usage"] = total_usage
    state["model_used"] = model_used
    state["is_fallback"] = is_fallback
    state["has_exportable"] = has_exportable
    state["export_idx"] = export_idx
    state["exhausted_tool_loop"] = exhausted_tool_loop


async def _check_pre_model_blockers(tier: Optional[str], conv_id: str, user_sub: str) -> str:
    quota_reason = await _check_token_quota(tier, conv_id)
    if quota_reason:
        return quota_reason
    return await _check_user_daily_limit(user_sub)


async def _append_assistant_text(
    conv_id: str,
    content: str,
    *,
    effective_question: str = "",
    refresh_polymorphic: bool = False,
) -> None:
    if refresh_polymorphic:
        await _refresh_polymorphic_pending_state(conv_id, effective_question, content)
    conversations[conv_id].append({"role": "assistant", "content": content})
    conversations.mark_dirty(conv_id)


async def _bootstrap_agent_turn(
    conv_id: str,
    mode: str,
    partition_key: str,
    question: str,
    *,
    user_sub: str,
) -> tuple[str, str]:
    await _ensure_conversation(conv_id, mode, partition_key, user_sub=user_sub)
    await _ensure_uploaded_files_loaded(conv_id, user_sub=user_sub)
    await _inject_file_context(conv_id, conversations[conv_id])
    effective_question, pending_reply = await _prepare_polymorphic_followup_question(conv_id, question)
    conversations[conv_id].append(
        {"role": "user", "content": question if pending_reply else effective_question}
    )
    conversations[conv_id] = _trim_history(conversations[conv_id])
    return effective_question, pending_reply


async def _finalize_agent_request(
    route_name: str,
    conv_id: str,
    partition_key: str,
    tier: Optional[str],
    total_usage: Dict[str, Any],
    *,
    user_sub: str,
    should_persist: bool,
    total_time_ms: int,
    tool_count: int,
    is_fallback: bool,
    request_status: str,
    request_error_kind: str,
    persist_task_label: str,
    model_used: str = "",
) -> None:
    if should_persist:
        try:
            await asyncio.wait_for(_persist_conversation(conv_id, partition_key), timeout=8.0)
        except Exception:
            _create_logged_task(
                _persist_conversation(conv_id, partition_key),
                persist_task_label,
            )
    await _record_token_quota(tier, total_usage)
    await _record_user_daily_spend(user_sub, int(total_usage.get("total_tokens", 0) or 0))
    agent_metrics.record(
        route_name,
        total_time_ms,
        tool_count=tool_count,
        fallback=is_fallback,
        status=request_status,
        request_id=get_request_id(),
        conversation_id=conv_id,
        model_used=model_used,
        error_kind=request_error_kind,
    )


def _quota_tier_name(tier: Optional[str]) -> str:
    txt = str(tier or "").strip().lower()
    return txt if txt in ("fast", "standard", "pro") else "fast"


async def _check_token_quota(tier: Optional[str], conv_id: str) -> str:
    if not TOKEN_QUOTA_ENFORCEMENT_ENABLED:
        return ""
    mgr = _tq_module.token_quota_manager
    if not mgr:
        return ""
    quota_tier = _quota_tier_name(tier)
    try:
        allowed, reason = await mgr.check(quota_tier)
    except Exception as e:
        logger.warning("[Agent] Token quota check failed closed tier=%s conv=%s err=%s", quota_tier, conv_id, e)
        return "Token quota check unavailable"
    if allowed:
        return ""
    logger.warning(
        "[Agent] Token quota exceeded: %s tier=%s conv=%s",
        reason,
        quota_tier,
        conv_id,
    )
    return reason or "Token quota exceeded"


async def _record_token_quota(tier: Optional[str], usage: Dict) -> None:
    mgr = _tq_module.token_quota_manager
    if not mgr or not isinstance(usage, dict):
        return
    try:
        total_tokens = int(usage.get("total_tokens", 0) or 0)
    except Exception:
        total_tokens = 0
    if total_tokens <= 0:
        return
    try:
        await mgr.record(_quota_tier_name(tier), total_tokens)
    except Exception as e:
        logger.warning("[Agent] Token quota record failed tier=%s err=%s", _quota_tier_name(tier), e)


# ── SEC-13: Per-user daily token spend tracking ──────────────────
# Single summary row per user per day in Table Storage.
# No local cache — every check reads directly from Table Storage so all
# instances always see the latest persisted total.
#
# Intra-instance concurrency: an asyncio.Lock per user serialises the
# read→add→merge sequence, so two concurrent requests for the same user
# on the same instance cannot interleave across the awaits.
#
# Cross-instance: the race window is bounded by network latency (~50ms)
# between one instance's read and another's merge.  For a daily budget
# limiter (not a billing meter), this is acceptable.
import time as _time
_USER_TOKENS_TABLE = "UserDailyTokens"
_user_spend_locks: Dict[str, asyncio.Lock] = {}


def _today_key() -> str:
    t = _time.gmtime()
    return f"{t.tm_year}-{t.tm_yday:03d}"


def _get_user_spend_lock(user_sub: str) -> asyncio.Lock:
    """Get or create a per-user asyncio.Lock (intra-instance serialisation)."""
    lock = _user_spend_locks.get(user_sub)
    if lock is None:
        lock = asyncio.Lock()
        _user_spend_locks[user_sub] = lock
    return lock


async def _read_user_spend_from_storage(user_sub: str, day: str) -> int:
    """Read current daily token spend directly from Table Storage."""
    try:
        from utils import odata_escape
        rows = await table_query(
            _USER_TOKENS_TABLE,
            f"PartitionKey eq '{odata_escape(day)}' and RowKey eq '{odata_escape(user_sub)}'",
            top=1,
        )
        return int(rows[0].get("TotalTokens", 0)) if rows else 0
    except Exception as e:
        logger.warning("[Agent] _read_user_spend_from_storage failed: %s", e)
        return 0


async def _check_user_daily_limit(user_sub: str) -> str:
    """Check if user exceeded daily token spend. Returns reason string if blocked, else ''."""
    if USER_DAILY_TOKEN_LIMIT <= 0 or not user_sub:
        return ""
    today = _today_key()
    total = await _read_user_spend_from_storage(user_sub, today)
    if total >= USER_DAILY_TOKEN_LIMIT:
        return f"Limite diário de tokens atingido ({USER_DAILY_TOKEN_LIMIT:,} tokens/dia)"
    return ""


async def _record_user_daily_spend(user_sub: str, tokens: int) -> None:
    """Record token spend: read current from storage, add delta, merge back.

    Uses a per-user asyncio.Lock so that concurrent requests for the same
    user on this instance are serialised across the await boundaries.
    """
    if USER_DAILY_TOKEN_LIMIT <= 0 or not user_sub or tokens <= 0:
        return
    today = _today_key()

    async with _get_user_spend_lock(user_sub):
        try:
            current = await _read_user_spend_from_storage(user_sub, today)
            new_total = current + tokens

            entity = {
                "PartitionKey": today,
                "RowKey": user_sub,
                "TotalTokens": new_total,
                "LastUpdated": datetime.now(timezone.utc).isoformat(),
            }
            if not await table_insert(_USER_TOKENS_TABLE, entity):
                await table_merge(_USER_TOKENS_TABLE, entity)
        except Exception as e:
            logger.warning("[Agent] _record_user_daily_spend persist failed: %s", e)


# ── S4-01: Track story generation count per user for proactive learning ──
_user_story_counts: Dict[str, int] = {}


def _increment_story_count(user_sub: str) -> int:
    """Increment and return the user's session story count."""
    if not user_sub:
        return 0
    count = _user_story_counts.get(user_sub, 0) + 1
    _user_story_counts[user_sub] = count
    return count


def _should_suggest_writer_profile(user_sub: str) -> bool:
    """Check if we should suggest writer profile learning after N stories."""
    if WRITER_PROFILE_LEARN_THRESHOLD <= 0 or not user_sub:
        return False
    count = _user_story_counts.get(user_sub, 0)
    # Suggest exactly at threshold, then every N*2 stories
    return count > 0 and (
        count == WRITER_PROFILE_LEARN_THRESHOLD
        or (count > WRITER_PROFILE_LEARN_THRESHOLD and count % (WRITER_PROFILE_LEARN_THRESHOLD * 2) == 0)
    )


def _format_agent_error_for_user(error: Exception) -> str:
    return format_llm_error_for_user(error)


def _user_partition_key(user: Optional[dict]) -> str:
    raw = (user or {}).get("sub") or (user or {}).get("username") or "anon"
    # Azure Table key hygiene: avoid problematic characters for filters/URLs.
    safe = "".join(c if c.isalnum() or c in "._-@" else "_" for c in str(raw))
    return (safe or "anon")[:100]


def _safe_blob_component(raw: str, max_len: int = 120) -> str:
    txt = str(raw or "").strip()
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in txt)
    if not safe:
        safe = "x"
    return safe[:max_len]


async def _get_conversation_lock(conv_id: str) -> asyncio.Lock:
    async with _conversation_locks_guard:
        if conv_id not in _conversation_locks:
            _conversation_locks[conv_id] = asyncio.Lock()
        return _conversation_locks[conv_id]


async def _mask_pii_structured(data: Any, ctx: PIIMaskingContext) -> Any:
    """Apply PII masking without breaking the surrounding JSON shape."""
    if isinstance(data, dict):
        return {key: await _mask_pii_structured(value, ctx) for key, value in data.items()}
    if isinstance(data, list):
        return [await _mask_pii_structured(item, ctx) for item in data]
    if isinstance(data, str) and len(data) >= 3:
        return await mask_pii(data, ctx)
    return data


def _create_logged_task(coro, label: str) -> None:
    task = asyncio.create_task(coro)

    def _on_done(done_task: asyncio.Task) -> None:
        try:
            done_task.result()
        except Exception as e:
            logger.warning("[Agent] background task '%s' failed: %s", label, e, exc_info=True)

    task.add_done_callback(_on_done)

# =============================================================================
# HISTORY MANAGEMENT
# =============================================================================

def _trim_history(messages: List[dict]) -> List[dict]:
    """Gere histórico: mantém system msgs + últimas N non-system msgs."""
    if len(messages) <= 20:
        return messages
    
    sys_msgs = [m for m in messages if m.get("role") == "system"]
    other = [m for m in messages if m.get("role") != "system"]
    kept = other[-AGENT_HISTORY_LIMIT:]
    
    # Comprimir tool results antigos
    for i, msg in enumerate(kept):
        if msg.get("role") == "tool" and len(msg.get("content", "")) > 500:
            try:
                data = json.loads(msg["content"])
                summary = {"total_count": data.get("total_count", "?"), "items_returned": len(data.get("items", [])), "_compressed": True}
                kept[i] = {**msg, "content": json.dumps(summary, ensure_ascii=False)}
            except (json.JSONDecodeError, TypeError):
                kept[i] = {**msg, "content": msg["content"][:200] + "...(truncado)"}
    
    return sys_msgs + kept


MAX_IMAGES_PER_INPUT = max(1, int(UPLOAD_MAX_IMAGES_PER_MESSAGE))
MAX_FILES_CONTEXT = 10


async def _get_conversation_meta(conv_id: str) -> Dict:
    async with _conversation_meta_lock:
        meta = conversation_meta.get(conv_id)
        return dict(meta) if isinstance(meta, dict) else {}


async def _set_conversation_meta(conv_id: str, value: Dict) -> None:
    async with _conversation_meta_lock:
        conversation_meta[conv_id] = dict(value)


async def _update_conversation_meta(conv_id: str, **updates) -> None:
    async with _conversation_meta_lock:
        meta = dict(conversation_meta.get(conv_id, {}))
        meta.update(updates)
        conversation_meta[conv_id] = meta


async def _remove_conversation_meta_key(conv_id: str, key: str) -> None:
    async with _conversation_meta_lock:
        meta = dict(conversation_meta.get(conv_id, {}))
        if key in meta:
            meta.pop(key, None)
            conversation_meta[conv_id] = meta


def _normalize_uploaded_files_entry_unlocked(conv_id: str) -> dict:
    current = uploaded_files_store.get(conv_id)
    if isinstance(current, dict) and isinstance(current.get("files"), list):
        return current
    if isinstance(current, dict) and current:
        legacy = dict(current)
        legacy.pop("files", None)
        normalized = {"files": [legacy]}
        uploaded_files_store[conv_id] = normalized
        return normalized
    return {"files": []}


def _get_uploaded_files_unlocked(conv_id: str) -> List[dict]:
    return list(_normalize_uploaded_files_entry_unlocked(conv_id).get("files", []))


def _get_uploaded_files(conv_id: str) -> List[dict]:
    """Sync compatibility wrapper used by older tests/helpers."""
    return _get_uploaded_files_unlocked(conv_id)


async def _normalize_uploaded_files_entry(conv_id: str) -> dict:
    async with _uploaded_files_lock:
        return _normalize_uploaded_files_entry_unlocked(conv_id)


async def _get_uploaded_files_async(conv_id: str) -> List[dict]:
    async with _uploaded_files_lock:
        return _get_uploaded_files_unlocked(conv_id)


async def _ensure_uploaded_files_loaded(conv_id: str, user_sub: str = "") -> None:
    async with _uploaded_files_lock:
        entry = _normalize_uploaded_files_entry_unlocked(conv_id)
        current_files = list(entry.get("files") or [])
        cached_owner = str(entry.get("owner_sub", "") or "")
        safe_user = str(user_sub or "")
        if current_files and (not safe_user or cached_owner == safe_user):
            return
        if current_files:
            uploaded_files_store.pop(conv_id, None)
    try:
        safe_conv = odata_escape(conv_id)
        rows = await table_query("UploadIndex", f"PartitionKey eq '{safe_conv}'", top=max(1, min(UPLOAD_INDEX_TOP, 500)))
    except Exception as e:
        logger.warning("[Agent] upload index query failed for %s: %s", conv_id, e)
        return
    if not rows:
        return

    filtered = []
    for row in rows:
        owner_sub = str(row.get("UserSub", "") or "")
        if owner_sub and safe_user and owner_sub != safe_user:
            continue
        filtered.append(row)
    if not filtered:
        return

    files = []
    for row in sorted(filtered, key=lambda r: str(r.get("UploadedAt", ""))):
        preview_text = str(row.get("PreviewText", "") or "")
        extracted_ref = str(row.get("ExtractedBlobRef", "") or "")
        if not preview_text and extracted_ref:
            container, blob_name = parse_blob_ref(extracted_ref)
            if container and blob_name:
                try:
                    raw = await blob_download_bytes(container, blob_name)
                    if raw:
                        preview_text = raw.decode("utf-8", errors="replace")[:16000]
                except Exception as e:
                    logger.warning("[Agent] extracted blob read failed for %s: %s", extracted_ref, e)
        try:
            col_names = json.loads(row.get("ColNamesJson", "[]") or "[]")
        except Exception:
            col_names = []
        try:
            col_analysis = json.loads(row.get("ColAnalysisJson", "[]") or "[]")
        except Exception:
            col_analysis = []
        try:
            full_col_stats = json.loads(row.get("FullColStatsJson", "[]") or "[]")
        except Exception:
            full_col_stats = []
        polymorphic_schema = None
        try:
            parsed_poly = json.loads(row.get("PolymorphicSchemaJson", "") or "")
            if isinstance(parsed_poly, dict) and parsed_poly.get("is_polymorphic"):
                polymorphic_schema = parsed_poly
        except Exception:
            polymorphic_schema = None
        if polymorphic_schema is None:
            poly_summary = str(row.get("PolymorphicSummary", "") or "").strip()
            pivot_column = str(row.get("PivotColumn", "") or "").strip()
            if poly_summary:
                polymorphic_schema = {
                    "is_polymorphic": True,
                    "summary_text": poly_summary,
                    "pivot_column": pivot_column,
                    "pivot_values_count": int(row.get("PivotValuesCount", 0) or 0),
                }
        files.append(
            {
                "filename": row.get("Filename", ""),
                "data_text": preview_text,
                "row_count": int(row.get("RowCount", 0) or 0),
                "col_names": col_names if isinstance(col_names, list) else [],
                "col_analysis": col_analysis if isinstance(col_analysis, list) else [],
                "full_col_stats": full_col_stats if isinstance(full_col_stats, list) else [],
                "truncated": bool(row.get("Truncated", False)),
                "uploaded_at": row.get("UploadedAt", ""),
                "has_chunks": str(row.get("HasChunks", "")).lower() in ("true", "1"),
                "chunks_blob_ref": row.get("ChunksBlobRef", ""),
                "extracted_blob_ref": extracted_ref,
                "polymorphic_schema": polymorphic_schema,
            }
        )
    if files:
        async with _uploaded_files_lock:
            if _get_uploaded_files_unlocked(conv_id):
                return
            uploaded_files_store[conv_id] = {
                "files": files[-MAX_FILES_CONTEXT:],
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
                "from_index": True,
                "owner_sub": safe_user,
            }


def _extract_request_images(request: AgentChatRequest) -> List[dict]:
    """Extrai até MAX_IMAGES_PER_INPUT imagens do request (novo campo images + fallback legacy)."""
    extracted: List[dict] = []
    raw_images = getattr(request, "images", None) or []
    for raw in raw_images[:MAX_IMAGES_PER_INPUT]:
        if isinstance(raw, dict):
            base64_data = raw.get("base64")
            content_type = raw.get("content_type") or raw.get("contentType")
            filename = raw.get("filename")
        else:
            base64_data = getattr(raw, "base64", None)
            content_type = getattr(raw, "content_type", None) or getattr(raw, "contentType", None)
            filename = getattr(raw, "filename", None)
        b64 = str(base64_data or "").strip()
        if not b64:
            continue
        extracted.append(
            {
                "base64": b64,
                "content_type": str(content_type or "image/png"),
                "filename": str(filename or "")[:120],
            }
        )

    if extracted:
        return extracted

    legacy_b64 = str(getattr(request, "image_base64", "") or "").strip()
    if legacy_b64:
        extracted.append(
            {
                "base64": legacy_b64,
                "content_type": str(getattr(request, "image_content_type", "image/png") or "image/png"),
                "filename": "",
            }
        )
    return extracted


async def _build_user_message(
    request: AgentChatRequest,
    conv_id: Optional[str] = None,
    question_override: Optional[str] = None,
) -> dict:
    """Constrói a mensagem do user (texto puro ou multimodal com 1..MAX_IMAGES_PER_INPUT imagens)."""
    images = _extract_request_images(request)
    question_text = str(question_override or request.question)

    # Fallback: usar imagem previamente carregada via /upload nesta conversa.
    if not images and conv_id:
        uploaded_images = []
        for file_data in await _get_uploaded_files_async(conv_id):
            upload_b64 = str(file_data.get("image_base64", "") or "").strip()
            if not upload_b64:
                continue
            uploaded_images.append(
                {
                    "base64": upload_b64,
                    "content_type": str(file_data.get("image_content_type") or "image/png"),
                    "filename": str(file_data.get("filename") or "")[:120],
                }
            )
        images = uploaded_images[:MAX_IMAGES_PER_INPUT]

    if not images:
        return {"role": "user", "content": question_text}

    content_blocks: List[dict] = [{"type": "text", "text": question_text}]
    if len(images) == 2:
        content_blocks.append(
            {"type": "text", "text": "Contexto visual: Imagem 1 = ANTES; Imagem 2 = DEPOIS."}
        )
    for idx, img in enumerate(images, 1):
        if img.get("filename"):
            content_blocks.append({"type": "text", "text": f"Imagem {idx}: {img['filename']}"})
        content_blocks.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{img['content_type']};base64,{img['base64']}",
                },
            }
        )

    return {"role": "user", "content": content_blocks}


async def _inject_file_context(conv_id: str, messages: List[dict]):
    """Injeta contexto de ficheiros uploaded na conversa."""
    files = await _get_uploaded_files_async(conv_id)
    meta = await _get_conversation_meta(conv_id)
    owner_sub = str(meta.get("owner_sub", "") or meta.get("partition_key", "") or "").strip()
    if files and not meta.get("file_injected"):
        messages[:] = [
            m for m in messages
            if not (
                m.get("role") == "system"
                and isinstance(m.get("content"), str)
                and (
                    m.get("content", "").startswith("FICHEIROS CARREGADOS:")
                    or m.get("content", "").startswith("FICHEIRO CARREGADO:")
                )
            )
        ]
        mode = meta.get("mode", "general")
        use_files = files[-MAX_FILES_CONTEXT:]
        ctx_parts = [
            f"FICHEIROS CARREGADOS: {len(use_files)} (máximo analisado por pedido: {MAX_FILES_CONTEXT})",
            "Analisa TODOS os ficheiros listados antes de responder. Não ignores nenhum.",
            "Quando existir secção 'ESTATÍSTICAS COMPLETAS', usa SEMPRE esses valores para min/max/mean/std; "
            "não recalcules a partir da amostra truncada.",
            "Para pedidos de gráfico sobre ficheiros carregados, usa chart_uploaded_table como primeira tentativa; "
            "se faltar coluna/agregação, pede clarificação objetiva ao utilizador.",
        ]

        total_budget = 90000
        per_file_budget = max(3000, min(18000, total_budget // max(1, len(use_files))))

        if mode == "userstory":
            ctx_parts.append(
                "MODO USERSTORY — integra requisitos de todos os anexos numa proposta coerente e sem duplicações."
            )

        polymorphic_blocks = []
        for idx, file_data in enumerate(use_files, 1):
            filename = str(file_data.get("filename", ""))
            filename_lower = filename.lower()
            is_tabular = is_tabular_filename(filename_lower)
            is_pdf = filename_lower.endswith(".pdf")
            is_pptx = filename_lower.endswith(".pptx")
            is_image = bool(file_data.get("image_base64"))
            cols = file_data.get("col_names", [])
            cols_txt = ", ".join(cols) if isinstance(cols, list) else str(cols or "")
            polymorphic_schema = file_data.get("polymorphic_schema") if isinstance(file_data.get("polymorphic_schema"), dict) else None
            dictionary_context = str(file_data.get("dictionary_context", "") or "")
            if polymorphic_schema and polymorphic_schema.get("is_polymorphic") and not dictionary_context:
                try:
                    dictionary_entries = await get_dictionary(filename, owner_sub=owner_sub)
                    if dictionary_entries:
                        dictionary_context = format_dictionary_for_prompt(dictionary_entries, table_name=filename)
                        file_data["dictionary_context"] = dictionary_context
                except Exception as e:
                    logger.warning("[Agent] data dictionary lookup failed for %s: %s", filename, e)
            if polymorphic_schema and polymorphic_schema.get("is_polymorphic"):
                polymorphic_blocks.append(
                    "\n".join(
                        [
                            f"### {filename} (pivô: {polymorphic_schema.get('pivot_column', '') or '?'})",
                            str(polymorphic_schema.get("summary_text", "") or "Dataset polimórfico detetado."),
                        ]
                    )
                )

            block = [
                f"[FICHEIRO {idx}] {filename}",
                f"Linhas: {file_data.get('row_count', 0)} | Colunas: {cols_txt}",
            ]
            if file_data.get("truncated"):
                block.append("NOTA: Conteúdo truncado para contexto.")

            full_stats = file_data.get("full_col_stats")
            col_analysis = file_data.get("col_analysis")
            if isinstance(full_stats, list) and full_stats:
                block.append("ESTATÍSTICAS COMPLETAS (calculadas sobre TODAS as linhas):")
                for fs in full_stats[:20]:
                    name = str(fs.get("name", "") or "")
                    if fs.get("type") == "numeric":
                        block.append(
                            f"- {name} (numeric): min={fs.get('min')}, max={fs.get('max')}, "
                            f"mean={fs.get('mean')}, std={fs.get('std')}, "
                            f"P25={fs.get('p25')}, P50={fs.get('p50')}, P75={fs.get('p75')}, "
                            f"non_null={fs.get('non_null')}, zeros={fs.get('zeros')}"
                        )
                    else:
                        first_last = ""
                        if fs.get("first") and fs.get("last"):
                            first_last = f", range=[{fs.get('first')} ... {fs.get('last')}]"
                        block.append(
                            f"- {name} (text): unique≈{fs.get('unique_approx', '?')}, "
                            f"sample={fs.get('sample', [])}{first_last}"
                        )
                block.append(
                    "NOTA: Os dados acima representam o ficheiro COMPLETO. "
                    "O conteúdo abaixo é uma AMOSTRA truncada — usa as estatísticas acima para valores exatos."
                )
            elif col_analysis:
                block.append("ANÁLISE DE COLUNAS (amostra):")
                for ca in col_analysis[:20]:
                    sample = ", ".join((ca.get("sample") or [])[:3])
                    block.append(f"- {ca.get('name','')} ({ca.get('type','text')}): {sample}")

            if polymorphic_schema and polymorphic_schema.get("is_polymorphic"):
                block.append("PERFIL POLIMÓRFICO DETECTADO:")
                block.append(str(polymorphic_schema.get("summary_text", "") or "Dataset polimórfico detetado."))
                if dictionary_context:
                    block.append(dictionary_context[:4000])
                else:
                    block.append(
                        "Sem dicionário de dados conhecido. Antes de interpretar campo_N/field_N, "
                        "pede contexto ao utilizador ou consulta get_data_dictionary."
                    )

            if mode == "userstory":
                if is_tabular:
                    block.append("Interpretar como lista estruturada de requisitos funcionais.")
                elif is_pdf:
                    block.append("Interpretar secções como hierarquia Épico > Feature > US > AC.")
                elif is_pptx:
                    block.append("Interpretar cada slide como bloco de requisitos.")
                elif is_image:
                    block.append("Imagem de apoio visual: extrair CTAs, inputs, labels, estados e validações.")
                else:
                    block.append("Extrair requisitos acionáveis e testáveis.")

            data_text = str(file_data.get("data_text", "") or "")
            if data_text:
                block.append("CONTEÚDO:")
                block.append(data_text[:per_file_budget])

            if (isinstance(file_data.get("chunks"), list) and file_data.get("chunks")) or file_data.get("has_chunks"):
                block.append(
                    "NOTA: Documento grande com chunks semânticos disponíveis; usa search_uploaded_document para pesquisa profunda."
                )

            ctx_parts.append("\n".join(block))

        if polymorphic_blocks:
            ctx_parts.extend(
                [
                    "## DATASETS POLIMÓRFICOS DETECTADOS",
                    "ATENÇÃO: colunas genéricas (campo_N/field_N) podem mudar de significado conforme o pivot.",
                    "Para cada análise: 1) filtra por pivot value 2) interpreta os campos conforme o perfil desse valor.",
                    "\n\n".join(polymorphic_blocks),
                ]
            )

        # A5: Cross-file JOIN hint when multiple tabular files exist
        tabular_count = sum(
            1 for f in use_files
            if is_tabular_filename(str(f.get("filename", "")))
        )
        if tabular_count >= 2:
            tabular_names = [
                str(f.get("filename", "")) for f in use_files
                if is_tabular_filename(str(f.get("filename", "")))
            ]
            ctx_parts.append(
                "## CROSS-FILE ANALYSIS DISPONÍVEL\n"
                f"Existem {tabular_count} ficheiros tabulares ({', '.join(tabular_names)}). "
                "No run_code, o sandbox fornece:\n"
                "- `DB` — DuckDB connection com todas as tabelas pré-carregadas (nomes derivados dos ficheiros)\n"
                "- `DUCKDB_TABLES` — dict {nome_tabela: ficheiro_origem}\n"
                "- Podes fazer JOINs entre tabelas: `DB.execute('SELECT ... FROM t1 JOIN t2 ON ...')`\n"
                "- Para análises cruzadas entre ficheiros, PREFERE usar DB (DuckDB) em vez de pandas.\n"
                "- Os ficheiros .parquet e .csv estão ambos disponíveis no sandbox."
            )

        ctx = "\n\n".join(ctx_parts)[:120000]
        messages.append({"role": "system", "content": ctx})
        await _update_conversation_meta(conv_id, file_injected=True)


async def _load_conversation_from_storage(conv_id: str, partition_key: str) -> bool:
    """Tenta carregar conversa do Table Storage. Retorna True se encontrou."""
    try:
        safe_pk = odata_escape(partition_key)
        safe_conv = odata_escape(conv_id)
        rows = await table_query(
            "ChatHistory",
            f"PartitionKey eq '{safe_pk}' and RowKey eq '{safe_conv}'",
            top=1,
        )
        if not rows:
            return False

        row = rows[0]
        messages_json = row.get("Messages", "[]")
        messages = json.loads(messages_json)

        if not messages:
            return False

        # Verificar que o system prompt é actual (pode ter mudado entre deploys)
        stored_mode = row.get("Mode", "general")
        current_sp = get_userstory_system_prompt() if stored_mode == "userstory" else get_agent_system_prompt()

        # Substituir system prompt armazenado pelo actual
        if messages and messages[0].get("role") == "system":
            messages[0] = {"role": "system", "content": current_sp}
        else:
            messages.insert(0, {"role": "system", "content": current_sp})

        conversations[conv_id] = messages
        await _set_conversation_meta(
            conv_id,
            {
                "mode": stored_mode,
                "created_at": row.get("CreatedAt", datetime.now(timezone.utc).isoformat()),
                "loaded_from_storage": True,
                "partition_key": partition_key,
                "owner_sub": partition_key,
            },
        )

        logger.info("[Agent] Loaded conversation %s from storage (%d messages)", conv_id, len(messages))
        return True
    except Exception as e:
        logger.warning("[Agent] _load_conversation_from_storage failed for %s: %s", conv_id, e)
        return False


async def _ensure_conversation(conv_id: str, mode: str, partition_key: str, user_sub: str = "") -> str:
    """Garante que a conversa existe — tenta lazy-load do Table Storage se não estiver em memória."""
    conversations.cleanup_expired()
    expected_owner = str(user_sub or partition_key or "").strip()
    current_meta = await _get_conversation_meta(conv_id)
    current_owner = str(current_meta.get("owner_sub", "") or current_meta.get("partition_key", "") or "").strip()
    if conv_id in conversations and expected_owner and current_owner != expected_owner:
        _cleanup_conversation_related_state(conv_id)
        if conv_id in conversations:
            del conversations[conv_id]
    if conv_id not in conversations:
        # Tentar carregar do Table Storage
        loaded = await _load_conversation_from_storage(conv_id, partition_key)
        if not loaded:
            # Conversa nova
            sp = get_userstory_system_prompt() if mode == "userstory" else get_agent_system_prompt()
            conversations[conv_id] = [{"role": "system", "content": sp}]
            await _set_conversation_meta(
                conv_id,
                {
                    "mode": mode,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "partition_key": partition_key,
                    "owner_sub": expected_owner,
                },
            )
        else:
            await _update_conversation_meta(conv_id, partition_key=partition_key, owner_sub=expected_owner or partition_key)
    else:
        conversations.touch(conv_id)
        await _update_conversation_meta(conv_id, partition_key=partition_key, owner_sub=expected_owner or partition_key)
    return conv_id


async def _build_llm_messages(
    conv_id: str,
    question: str,
    request: Optional[AgentChatRequest] = None,
    tier: Optional[str] = None,
    tools_list: Optional[List[dict]] = None,
    user_sub: str = "",
) -> List[dict]:
    """Cópia efémera do histórico + regras + few-shot. Não muta conversations[]."""
    base = conversations[conv_id].copy()
    insert_pos = 1 if base else 0

    learned = await get_learned_rules()
    if learned:
        base.insert(insert_pos, {"role": "system", "content": learned})
        insert_pos += 1

    fewshot = await get_few_shot_examples(question, user_sub=str(user_sub or "").strip())
    if fewshot:
        base.insert(insert_pos, {"role": "system", "content": fewshot})

    # Injeta conteúdo multimodal apenas na cópia efémera para evitar bloat no histórico real.
    if request:
        user_msg = await _build_user_message(request, conv_id=conv_id, question_override=question)
        if isinstance(user_msg.get("content"), list):
            replaced = False
            for idx in range(len(base) - 1, -1, -1):
                msg = base[idx]
                if msg.get("role") != "user":
                    continue
                if isinstance(msg.get("content"), str) and msg.get("content") == question:
                    base[idx] = user_msg
                    replaced = True
                    break
            if not replaced:
                base.append(user_msg)

    tools_tokens = count_tools_tokens(tools_list or [])
    messages_tokens = count_messages_tokens(base)
    provider = get_provider(tier)
    model_name = str(getattr(provider, "deployment", "") or getattr(provider, "model", "") or "")
    context_window = resolve_context_window(model_name)
    available = context_window - tools_tokens - messages_tokens - RESPONSE_RESERVE_TOKENS
    if available < 500:
        logger.warning(
            "[Agent] Context budget tight (available=%d, model=%s), trimming history",
            available,
            model_name or "unknown",
        )

    while available < 2000:
        non_system_indices = [idx for idx, msg in enumerate(base) if msg.get("role") != "system"]
        if len(non_system_indices) <= 2:
            break
        target_idx = non_system_indices[0]
        target_msg = base[target_idx]

        # Collect indices of messages that must be removed together to
        # preserve tool_calls ↔ tool_result pairing.
        indices_to_remove = [target_idx]

        if target_msg.get("role") == "assistant" and target_msg.get("tool_calls"):
            # Removing an assistant message with tool_calls → also remove the
            # consecutive tool-result messages that follow it.
            tc_ids = {tc.get("id") for tc in target_msg.get("tool_calls", [])}
            j = target_idx + 1
            while j < len(base) and base[j].get("role") == "tool":
                indices_to_remove.append(j)
                j += 1

        elif target_msg.get("role") == "tool":
            # Removing a tool-result message → also remove the preceding
            # assistant message (with tool_calls) and all sibling tool results.
            # Walk backwards to find the assistant message.
            start = target_idx
            while start > 0 and base[start - 1].get("role") == "tool":
                start -= 1
            # The message right before the tool block should be the assistant.
            if start > 0 and base[start - 1].get("role") == "assistant" and base[start - 1].get("tool_calls"):
                indices_to_remove = [start - 1]
            else:
                indices_to_remove = []
            # Include all consecutive tool messages in this block.
            j = start
            while j < len(base) and base[j].get("role") == "tool":
                indices_to_remove.append(j)
                j += 1

        # Remove in reverse order to preserve indices.
        for idx in sorted(set(indices_to_remove), reverse=True):
            removed = base.pop(idx)
            messages_tokens -= count_single_message_tokens(removed)

        available = context_window - tools_tokens - messages_tokens - RESPONSE_RESERVE_TOKENS

    return base


def _compact_message_for_storage(message: dict) -> dict:
    compact = dict(message)
    role = compact.get("role")
    content = compact.get("content", "")

    if role == "assistant" and compact.get("tool_calls"):
        reduced_calls = []
        for tc in list(compact.get("tool_calls", []) or [])[:4]:
            if not isinstance(tc, dict):
                continue
            function = tc.get("function") if isinstance(tc.get("function"), dict) else {}
            arguments = function.get("arguments", "")
            if not isinstance(arguments, str):
                arguments = json.dumps(arguments, ensure_ascii=False, default=str)
            if len(arguments) > 300:
                arguments = arguments[:300] + "...(truncado)"
            reduced_calls.append(
                {
                    "id": str(tc.get("id", "") or "")[:120],
                    "type": "function",
                    "function": {
                        "name": str(function.get("name", "") or "")[:80],
                        "arguments": arguments,
                    },
                }
            )
        compact["tool_calls"] = reduced_calls
        if isinstance(content, str):
            compact["content"] = content[:800] + ("...(truncado)" if len(content) > 800 else "")
        elif content is None:
            compact["content"] = ""
        else:
            compact["content"] = str(content)[:800]
        return compact

    if role == "tool":
        if len(content) > 500:
            try:
                data = json.loads(content)
                summary = {
                    "total_count": data.get("total_count", "?"),
                    "items_returned": len(data.get("items", [])),
                    "_persisted_summary": True,
                }
                content = json.dumps(summary, ensure_ascii=False)
            except (json.JSONDecodeError, TypeError):
                content = str(content)[:500] + "...(truncado)"
        compact["content"] = content
        return compact

    if isinstance(content, str):
        compact["content"] = content[:8000] + ("...(truncado)" if len(content) > 8000 else "")
        return compact

    if isinstance(content, list):
        reduced = []
        for part in content[:10]:
            if not isinstance(part, dict):
                reduced.append(str(part)[:200])
                continue
            ptype = part.get("type")
            if ptype == "text":
                txt = str(part.get("text", ""))
                if len(txt) > 2000:
                    txt = txt[:2000] + "...(truncado)"
                reduced.append({"type": "text", "text": txt})
            elif ptype == "image_url":
                reduced.append({"type": "image_url", "image_url": {"url": "[base64_omitted]"}})
            else:
                reduced.append({"type": str(ptype or "unknown")})
        compact["content"] = reduced
        return compact

    if isinstance(content, dict):
        compact["content"] = json.dumps(content, ensure_ascii=False, default=str)[:2000]
        return compact

    compact["content"] = str(content)[:2000]
    return compact


def _minimal_message_for_storage(message: dict) -> dict:
    role = str(message.get("role", "") or "")
    compact: Dict[str, Any] = {"role": role}

    if role == "tool":
        compact["content"] = "resultado de tool omitido"
        if message.get("tool_call_id"):
            compact["tool_call_id"] = str(message.get("tool_call_id", "") or "")[:120]
        if message.get("result_blob_ref"):
            compact["result_blob_ref"] = str(message.get("result_blob_ref", "") or "")[:240]
        return compact

    if role == "assistant" and message.get("tool_calls"):
        compact["content"] = "tool call executado"
        compact["tool_calls"] = [
            {
                "id": str(tc.get("id", "") or "")[:120],
                "function": {
                    "name": str((tc.get("function") or {}).get("name", "") or "")[:80],
                },
            }
            for tc in list(message.get("tool_calls", []) or [])[:3]
            if isinstance(tc, dict)
        ]
        return compact

    content = message.get("content", "")
    if isinstance(content, list):
        text_parts = []
        for part in content[:4]:
            if isinstance(part, dict) and part.get("type") == "text":
                text_parts.append(str(part.get("text", "")))
            else:
                text_parts.append(str(part))
        content = " ".join(text_parts)
    elif isinstance(content, dict):
        content = json.dumps(content, ensure_ascii=False, default=str)

    compact["content"] = str(content or "")[:400]
    return compact


def _prepare_messages_for_storage(messages: List[dict]) -> List[dict]:
    compact = [_compact_message_for_storage(m) for m in messages]
    messages_json = json.dumps(compact, ensure_ascii=False, default=str)
    if len(messages_json.encode("utf-8")) <= 60000:
        return compact

    compact = [compact[0]] + compact[-10:] if compact else []
    messages_json = json.dumps(compact, ensure_ascii=False, default=str)
    if len(messages_json.encode("utf-8")) <= 60000:
        return compact

    compact = [_minimal_message_for_storage(m) for m in ([compact[0]] + compact[-6:] if compact else [])]
    messages_json = json.dumps(compact, ensure_ascii=False, default=str)
    if len(messages_json.encode("utf-8")) <= 60000:
        return compact

    return [{"role": "system", "content": "Histórico truncado para persistência."}]


def _has_explicit_create_confirmation(conv_id: str) -> bool:
    def _normalize(text: str) -> str:
        lowered = (text or "").lower()
        deaccented = unicodedata.normalize("NFKD", lowered)
        return "".join(ch for ch in deaccented if not unicodedata.combining(ch))

    approval_patterns = (
        r"\bconfirmo\b",
        r"\bconfirmado\b",
        r"\bsim\b",
        r"\bavanca\b",
        r"\bpodes\s+criar\b",
        r"\bpodes\s+avancar\b",
        r"\bclaro\b",
        r"\byep\b",
        r"\bok,\s*cria\b",
    )
    explicit_negative_patterns = (
        r"\bnao\s+confirmo\b",
        r"\bnao\s+avanc(?:a|ar|o|es)\b",
        r"\bnunca\s+confirmo\b",
        r"\bnunca\s+avanc(?:a|ar|o|es)\b",
    )
    negation_tokens = r"\b(nao|nunca|jamais)\b"

    for msg in reversed(conversations.get(conv_id, [])):
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            txt = " ".join(
                str(p.get("text", ""))
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            )
        else:
            txt = str(content)

        normalized = _normalize(txt)

        if any(re.search(p, normalized) for p in explicit_negative_patterns):
            return False

        for pattern in approval_patterns:
            for match in re.finditer(pattern, normalized):
                # Bloqueia aprovações com negação imediatamente antes (ex: "não confirmo")
                prefix = normalized[max(0, match.start() - 24):match.start()]
                if re.search(negation_tokens, prefix):
                    return False
                return True
        return False
    return False


def _normalize_request_text(value: str) -> str:
    lowered = str(value or "").lower()
    deaccented = unicodedata.normalize("NFKD", lowered)
    clean = "".join(ch for ch in deaccented if not unicodedata.combining(ch))
    clean = clean.replace("|", " ").replace("—", " ").replace("-", " ").replace("_", " ")
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def _iter_recent_assistant_tool_calls(conv_id: str):
    for msg in reversed(conversations.get(conv_id, [])):
        if msg.get("role") != "assistant":
            continue
        for tc in reversed(msg.get("tool_calls") or []):
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            name = str(fn.get("name", "") or "").strip()
            raw_args = fn.get("arguments", {})
            args = {}
            if isinstance(raw_args, dict):
                args = raw_args
            elif isinstance(raw_args, str):
                try:
                    parsed = json.loads(raw_args)
                    if isinstance(parsed, dict):
                        args = parsed
                except Exception:
                    args = {}
            if name:
                yield name, args


def _latest_query_workitems_args(conv_id: str) -> Dict[str, Any]:
    for name, args in _iter_recent_assistant_tool_calls(conv_id):
        if name != "query_workitems":
            continue
        wiql_where = str(args.get("wiql_where", "") or "").strip()
        if wiql_where:
            return {
                "wiql_where": wiql_where,
                "fields": args.get("fields"),
                "top": args.get("top", 200),
            }
    return {}


def _extract_likely_person_name(question: str) -> str:
    raw = re.sub(r"\b[\w.\-+%]+@[\w.\-]+\.[A-Za-z]{2,}\b", " ", str(question or ""))
    preferred_patterns = [
        r"(?i)\b(?:criadas?|feitas?|abertas?)\s+(?:por|pelo|pela)\s+([A-ZÀ-Ý][\wÀ-ÿ'.-]+(?:\s+[A-ZÀ-Ý][\wÀ-ÿ'.-]+){1,3})\b",
        r"(?i)\b(?:do|da|de)\s+([A-ZÀ-Ý][\wÀ-ÿ'.-]+(?:\s+[A-ZÀ-Ý][\wÀ-ÿ'.-]+){1,3})\b",
    ]
    for pattern in preferred_patterns:
        match = re.search(pattern, raw)
        if match:
            return str(match.group(1) or "").strip()

    stop_terms = {
        "user stories",
        "azure devops",
        "acme",
        "payments portal",
        "nova conversa",
        "assistente ai",
    }
    for candidate in re.findall(r"\b([A-ZÀ-Ý][\wÀ-ÿ'.-]+(?:\s+[A-ZÀ-Ý][\wÀ-ÿ'.-]+){1,3})\b", raw):
        cleaned = str(candidate or "").strip()
        norm = _normalize_request_text(cleaned)
        if norm and norm not in stop_terms:
            return cleaned
    return ""


def _detect_person_filter_field(question: str, wiql_where: str) -> str:
    safe_where = str(wiql_where or "")
    norm = _normalize_request_text(question)
    if re.search(r"\[System\.CreatedBy\]", safe_where, re.IGNORECASE):
        return "created_by"
    if re.search(r"\[System\.AssignedTo\]", safe_where, re.IGNORECASE):
        return "assigned_to"
    if re.search(r"\b(assigned|assignee|atribu|responsavel|responsável)\b", norm):
        return "assigned_to"
    if re.search(r"\b(criad|autor|criador|feito por|feitas por|aberto por|abertas por)\b", norm):
        return "created_by"
    if re.search(r"\b(s[oó]|apenas|somente)\s+as?\s+d[oa]\b", norm):
        return "created_by"
    return ""


def _replace_person_filter_in_wiql(wiql_where: str, field: str, person_name: str) -> str:
    safe_where = str(wiql_where or "").strip()
    safe_name = str(person_name or "").strip().replace("'", "''")
    if not safe_where or not safe_name:
        return safe_where

    system_field = "System.CreatedBy" if field == "created_by" else "System.AssignedTo"
    clause_pattern = re.compile(
        rf"(?i)(?:^|\s+AND\s+)\[{re.escape(system_field)}\]\s+(?:=|CONTAINS)\s+'(?:[^']|'')*'"
    )
    updated = clause_pattern.sub("", safe_where).strip()
    updated = re.sub(r"(?i)^\s*AND\s+", "", updated)
    updated = re.sub(r"(?i)\s+AND\s*$", "", updated)
    updated = re.sub(r"(?i)\s+AND\s+AND\s+", " AND ", updated)
    updated = re.sub(r"\s+", " ", updated).strip()
    new_clause = f"[{system_field}] CONTAINS '{safe_name}'"
    return f"{updated} AND {new_clause}" if updated else new_clause


def _looks_like_workitem_filter_followup(question: str) -> bool:
    norm = _normalize_request_text(question)
    has_refinement_hint = bool(
        re.search(r"\b(s[oó]|apenas|somente|filtra|filtrar|mantem|mantém|troca|agora|em vez)\b", norm)
    )
    has_recipient_hint = "@" in str(question or "")
    return has_refinement_hint or has_recipient_hint


async def _run_forced_workitem_filter_followup(
    question: str,
    conv_id: str,
    user_sub: str = "",
) -> tuple[List[str], List[Dict]]:
    if not _looks_like_workitem_filter_followup(question):
        return [], []

    previous_query = _latest_query_workitems_args(conv_id)
    if not previous_query:
        return [], []

    person_name = _extract_likely_person_name(question)
    if not person_name:
        return [], []

    field = _detect_person_filter_field(question, previous_query.get("wiql_where", ""))
    if field not in {"created_by", "assigned_to"}:
        return [], []

    updated_where = _replace_person_filter_in_wiql(previous_query.get("wiql_where", ""), field, person_name)
    if not updated_where or updated_where == previous_query.get("wiql_where", ""):
        return [], []

    forced_call = LLMToolCall(
        id=f"forced_qw_refine_{uuid.uuid4().hex[:8]}",
        name="query_workitems",
        arguments={
            "wiql_where": updated_where,
            "fields": previous_query.get("fields"),
            "top": previous_query.get("top", 200),
        },
    )
    logger.info(
        "[Agent] forcing query_workitems refresh for follow-up filter (conv=%s, field=%s, person=%s)",
        conv_id,
        field,
        person_name,
    )
    conversations[conv_id].append(_make_tool_calls_assistant_message([forced_call]))
    conversations.mark_dirty(conv_id)
    return await _execute_tool_calls([forced_call], conv_id, user_sub=user_sub)


async def _latest_polymorphic_upload_context(conv_id: str) -> Dict[str, Any]:
    for file_data in reversed(await _get_uploaded_files_async(conv_id)):
        schema = file_data.get("polymorphic_schema")
        if not isinstance(schema, dict) or not schema.get("is_polymorphic"):
            continue
        pivot_profiles = schema.get("pivot_profiles") if isinstance(schema.get("pivot_profiles"), dict) else {}
        available_values = [
            str(value or "").strip()
            for value in pivot_profiles.keys()
            if str(value or "").strip()
        ]
        if not available_values:
            continue
        return {
            "table_name": str(file_data.get("filename", "") or "").strip(),
            "pivot_column": str(schema.get("pivot_column", "") or "").strip(),
            "available_values": available_values,
        }
    return {}


def _extract_polymorphic_selection_reply(question: str, pending: Dict[str, Any]) -> str:
    raw = str(question or "").strip()
    if not raw:
        return ""
    available_values = [
        str(value or "").strip()
        for value in (pending.get("available_values") or [])
        if str(value or "").strip()
    ]
    if not available_values:
        return ""

    allowed_map = {value: value for value in available_values}
    if raw in allowed_map:
        return raw

    norm = _normalize_request_text(raw)
    pivot_norm = _normalize_request_text(str(pending.get("pivot_column", "") or ""))
    for value in available_values:
        value_norm = _normalize_request_text(value)
        if norm == value_norm:
            return value
        if pivot_norm and re.fullmatch(rf"{re.escape(pivot_norm)}\s*=?\s*{re.escape(value_norm)}", norm):
            return value
        if len(norm.split()) <= 6 and re.search(rf"\b{re.escape(value_norm)}\b", norm):
            return value
    return ""


async def _prepare_polymorphic_followup_question(conv_id: str, question: str) -> tuple[str, str]:
    meta = await _get_conversation_meta(conv_id)
    pending = meta.get(_PENDING_POLYMORPHIC_SELECTION_KEY)
    if not isinstance(pending, dict) or not pending:
        return str(question or ""), ""

    selected = _extract_polymorphic_selection_reply(question, pending)
    if selected:
        await _remove_conversation_meta_key(conv_id, _PENDING_POLYMORPHIC_SELECTION_KEY)
        pivot_column = str(pending.get("pivot_column", "") or "pivot").strip() or "pivot"
        table_name = str(pending.get("table_name", "") or "ficheiro carregado").strip() or "ficheiro carregado"
        original_question = str(pending.get("original_question", "") or "Analisa o dataset polimórfico").strip()
        rewritten = (
            f"{original_question}\n\n"
            f"Confirmação do utilizador: respondeu '{str(question or '').strip()}'. "
            f"Considera apenas o ficheiro '{table_name}' e o valor {pivot_column}={selected}. "
            "Usa o dicionário de dados conhecido, se existir, e responde com os nomes mapeados."
        )
        return rewritten, ""

    norm = _normalize_request_text(question)
    if norm and len(norm.split()) <= 4:
        pivot_column = str(pending.get("pivot_column", "") or "pivot").strip() or "pivot"
        options = ", ".join(str(value) for value in (pending.get("available_values") or [])[:12])
        return "", f"Estou à espera de um valor de {pivot_column}. Escolhe um destes: {options}."

    await _remove_conversation_meta_key(conv_id, _PENDING_POLYMORPHIC_SELECTION_KEY)
    return str(question or ""), ""


async def _refresh_polymorphic_pending_state(conv_id: str, question: str, answer: str) -> None:
    context = await _latest_polymorphic_upload_context(conv_id)
    available_values = [
        str(value or "").strip()
        for value in (context.get("available_values") or [])
        if str(value or "").strip()
    ]
    if len(available_values) < 2 or not str(answer or "").strip():
        await _remove_conversation_meta_key(conv_id, _PENDING_POLYMORPHIC_SELECTION_KEY)
        return

    norm_answer = _normalize_request_text(answer)
    pivot_norm = _normalize_request_text(str(context.get("pivot_column", "") or ""))
    mentioned_values = [
        value
        for value in available_values
        if re.search(rf"\b{re.escape(_normalize_request_text(value))}\b", norm_answer)
    ]
    asks_choice = any(
        marker in norm_answer
        for marker in ("qual", "quais", "indica", "escolhe", "seleciona", "selecciona", "diz me")
    )
    if asks_choice and (pivot_norm in norm_answer or len(mentioned_values) >= 2):
        await _update_conversation_meta(
            conv_id,
            **{
                _PENDING_POLYMORPHIC_SELECTION_KEY: {
                    "table_name": context.get("table_name", ""),
                    "pivot_column": context.get("pivot_column", ""),
                    "available_values": available_values,
                    "original_question": str(question or "").strip(),
                }
            },
        )
        return

    await _remove_conversation_meta_key(conv_id, _PENDING_POLYMORPHIC_SELECTION_KEY)


def _canonicalize_area_path(area_hint: str) -> str:
    raw = str(area_hint or "").strip()
    if not raw:
        return ""
    if "\\" in raw:
        return raw
    norm_hint = _normalize_request_text(raw)
    for known in DEVOPS_AREAS:
        known_norm = _normalize_request_text(known)
        if norm_hint and (known_norm.endswith(norm_hint) or norm_hint in known_norm):
            return known
    return raw


def _extract_forced_dual_hierarchy_calls(question: str) -> List[LLMToolCall]:
    norm = _normalize_request_text(question)
    has_bug = bool(re.search(r"\bbugs?\b", norm))
    has_us = bool(re.search(r"\buser stor(?:y|ies)\b", norm))
    has_epic = bool(re.search(r"\bepic\b", norm))
    has_feature = bool(re.search(r"\bfeature\b", norm))
    if not (has_bug and has_us and has_epic and has_feature):
        return []

    epic_match = re.search(r"\bepic\b[^\d]{0,90}(\d{4,9})", norm)
    feature_match = re.search(r"\bfeature\b[^\d]{0,90}(\d{4,9})", norm)
    if not epic_match or not feature_match:
        return []

    try:
        epic_id = int(epic_match.group(1))
        feature_id = int(feature_match.group(1))
    except (TypeError, ValueError):
        return []
    if epic_id <= 0 or feature_id <= 0:
        return []

    area_hint = ""
    area_match = re.search(r"\barea\b\s+(.+?)(?:\bnao\b|[.,;]|$)", norm)
    if area_match:
        area_hint = area_match.group(1).strip()
    area_path = _canonicalize_area_path(area_hint)

    title_contains = ""
    title_match = re.search(
        r"\btitulo\s+tem\s+(.+?)(?:\bdentro\s+da\s+area\b|\bna\s+area\b|\bnao\b|[.,;]|$)",
        norm,
    )
    if title_match:
        title_contains = title_match.group(1).strip()

    calls = [
        LLMToolCall(
            id=f"forced_qh_bug_{uuid.uuid4().hex[:8]}",
            name="query_hierarchy",
            arguments={
                "parent_id": epic_id,
                "parent_type": "Epic",
                "child_type": "Bug",
                "area_path": area_path,
            },
        ),
        LLMToolCall(
            id=f"forced_qh_us_{uuid.uuid4().hex[:8]}",
            name="query_hierarchy",
            arguments={
                "parent_id": feature_id,
                "parent_type": "Feature",
                "child_type": "User Story",
                "area_path": area_path,
                "title_contains": title_contains,
            },
        ),
    ]
    return calls


def _make_tool_calls_assistant_message(tool_calls: List[LLMToolCall]) -> dict:
    msg_calls = []
    for tc in tool_calls:
        msg_calls.append(
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": json.dumps(tc.arguments, ensure_ascii=False, default=str),
                },
            }
        )
    return {"role": "assistant", "content": "", "tool_calls": msg_calls}


async def _run_forced_dual_hierarchy(
    question: str,
    conv_id: str,
    user_sub: str = "",
) -> tuple[List[str], List[Dict]]:
    forced_calls = _extract_forced_dual_hierarchy_calls(question)
    if not forced_calls:
        return [], []

    logger.info(
        "[Agent] forcing dual query_hierarchy calls for mixed hierarchy request (conv=%s)",
        conv_id,
    )
    conversations[conv_id].append(_make_tool_calls_assistant_message(forced_calls))
    conversations.mark_dirty(conv_id)
    return await _execute_tool_calls(forced_calls, conv_id, user_sub=user_sub)


def _has_tabular_uploads(conv_id: str) -> bool:
    for file_data in _get_uploaded_files(conv_id):
        if is_tabular_filename(str(file_data.get("filename", "") or "").lower()):
            return True
    return False


async def _has_tabular_uploads_async(conv_id: str) -> bool:
    for file_data in await _get_uploaded_files_async(conv_id):
        if is_tabular_filename(str(file_data.get("filename", "") or "").lower()):
            return True
    return False


def _should_prefer_tabular_tool_over_run_code(question: str) -> bool:
    norm = _normalize_request_text(question)
    if not norm:
        return False

    advanced_patterns = [
        r"\bpython\b",
        r"\bcodigo\b|\bcódigo\b|\bscript\b",
        r"\bcorrelac[aã]o\b|\bcorrelation\b|\bregress[aã]o\b|\bregression\b",
        r"\bscatter\b|\bheatmap\b|\boutlier\b|\banomalia estatistica\b",
        r"\bjoin\b|\bmerge\b|\bpivot\b|\bgroupby\b",
        r"\bmulti[- ]?sheet\b|\bmulti[- ]?tab\b|\bworkbook\b|\bxlsx\b",
        r"\bexcel com\b|\bgera ficheiro\b|\bexporta\b|\bdownload\b",
        r"\bdashboard\b|\bprogram[aá]tico\b|\bcustom\b|\bpersonalizado\b",
        r"\bsem amostra\b|\blista completa\b|\btodos os valores\b|\bexaustiv",
    ]
    if any(re.search(pattern, norm) for pattern in advanced_patterns):
        return False

    exploratory_patterns = [
        r"\banalisa\b|\banalisar\b|\bresume\b|\bresumo\b|\binsight",
        r"\btend[eê]ncia\b|\bpadr[aã]o\b|\bpadrao\b|\bdistribui[cç][aã]o\b",
        r"\btop\b|\branking\b|\bcategor",
        r"\balerta\b|\bsubscri",
        r"\bmedia\b|\bm[eé]dia\b|\bminimo\b|\bm[ií]nimo\b|\bmaximo\b|\bm[aá]ximo\b",
        r"\bfrequ[eê]ncia\b|\bcontagem\b|\bpor coluna\b|\bpor categoria\b",
    ]
    return any(re.search(pattern, norm) for pattern in exploratory_patterns)


async def _has_visual_uploads_async(conv_id: str) -> bool:
    for file_data in await _get_uploaded_files_async(conv_id):
        filename = str(file_data.get("filename", "") or "").lower()
        if file_data.get("image_base64") or filename.endswith(".svg"):
            return True
    return False


def _looks_like_visual_userstory_request(question: str) -> bool:
    norm = _normalize_request_text(question)
    has_story_intent = bool(
        re.search(r"\b(user stor(?:y|ies)|gera(?:r)?\s+us|gera(?:r)?\s+uma\s+us|gera(?:r)?\s+uma\s+user story|descrev(?:e|er)\s+este\s+ecra)\b", norm)
    )
    has_visual_hint = bool(
        re.search(r"\b(ecra|screen|mockup|wireframe|screenshot|imagem|png|jpg|jpeg|svg|figma|frame|ui)\b", norm)
    )
    return has_story_intent and has_visual_hint


async def _run_forced_visual_userstory(
    question: str,
    conv_id: str,
    user_sub: str = "",
) -> tuple[List[str], List[Dict]]:
    if not _looks_like_visual_userstory_request(question):
        return [], []
    if not await _has_visual_uploads_async(conv_id):
        return [], []

    forced_call = LLMToolCall(
        id=f"forced_visual_us_{uuid.uuid4().hex[:8]}",
        name="screenshot_to_us",
        arguments={
            "context": question,
        },
    )
    logger.info(
        "[Agent] forcing screenshot_to_us for visual user story request (conv=%s)",
        conv_id,
    )
    conversations[conv_id].append(_make_tool_calls_assistant_message([forced_call]))
    conversations.mark_dirty(conv_id)
    return await _execute_tool_calls([forced_call], conv_id, user_sub=user_sub)


def _merge_tool_batch_results(
    tools_used: List[str],
    tool_details: List[Dict[str, Any]],
    batch_tools_used: List[str],
    batch_tool_details: List[Dict[str, Any]],
    *,
    has_exportable: bool = False,
    export_idx: Optional[int] = None,
) -> tuple[bool, Optional[int]]:
    if batch_tools_used:
        tools_used.extend(batch_tools_used)
    if batch_tool_details:
        start_idx = len(tool_details)
        tool_details.extend(batch_tool_details)
        for local_idx, detail in enumerate(batch_tool_details):
            if detail.get("result_summary", {}).get("items_returned", 0) > 0:
                has_exportable = True
                export_idx = start_idx + local_idx
    return has_exportable, export_idx


def _resolve_exportable_state(
    tool_details: List[Dict[str, Any]],
    *,
    has_exportable: bool = False,
    export_idx: Optional[int] = None,
) -> tuple[bool, Optional[int]]:
    resolved_has_exportable = bool(has_exportable)
    resolved_export_idx = export_idx
    for idx, detail in enumerate(tool_details):
        if detail.get("result_summary", {}).get("items_returned", 0) > 0:
            resolved_has_exportable = True
            if resolved_export_idx is None:
                resolved_export_idx = idx
    return resolved_has_exportable, resolved_export_idx


def _build_agent_response_metadata(
    conv_id: str,
    request_id: str,
    request_status: str,
    request_error_kind: str,
    tools_used: List[str],
    tool_details: List[Dict[str, Any]],
    total_usage: Dict[str, Any],
    total_time_ms: int,
    model_used: str,
    is_fallback: bool,
    *,
    has_exportable: bool = False,
    export_idx: Optional[int] = None,
    suggest_learn_style: Optional[bool] = None,
) -> Dict[str, Any]:
    resolved_has_exportable, resolved_export_idx = _resolve_exportable_state(
        tool_details,
        has_exportable=has_exportable,
        export_idx=export_idx,
    )
    payload: Dict[str, Any] = {
        "conversation_id": conv_id,
        "request_id": request_id,
        "request_status": str(request_status or "ok").strip().lower() or "ok",
        "request_error_kind": str(request_error_kind or "").strip().lower(),
        "tools_used": list(set(tools_used)),
        "tool_details": tool_details,
        "has_exportable_data": resolved_has_exportable,
        "export_index": resolved_export_idx,
        "tokens_used": total_usage,
        "total_time_ms": total_time_ms,
        "model_used": model_used,
        "is_fallback": is_fallback,
    }
    if suggest_learn_style is not None:
        payload["suggest_learn_style"] = suggest_learn_style
    return payload


def _build_stream_tool_result_text(detail: Dict[str, Any]) -> str:
    """Build human-friendly tool_result text for SSE updates."""
    summary = detail.get("result_summary", {})
    has_error = summary.get("has_error", False)
    if has_error:
        return f"⚠️ {detail['tool']}: erro"
    count = summary.get("total_count", summary.get("items_returned", ""))
    if isinstance(count, (int, float)) and count > 0:
        return f"✅ {detail['tool']}: {int(count)} resultados"
    if isinstance(count, str) and count not in ("N/A", "0", ""):
        try:
            n = int(count)
            if n > 0:
                return f"✅ {detail['tool']}: {n} resultados"
        except (ValueError, TypeError):
            pass
    return f"✅ {detail['tool']}"


def _build_stream_tool_result_event(detail: Dict[str, Any]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "type": "tool_result",
        "tool": detail["tool"],
        "text": _build_stream_tool_result_text(detail),
        "duration_ms": detail.get("duration_ms"),
    }
    summary = detail.get("result_summary", {})
    if isinstance(summary, dict):
        result_status = str(summary.get("result_status", "") or "").strip().lower()
        if result_status:
            payload["result_status"] = result_status
        error_kind = str(summary.get("error_kind", "") or "").strip().lower()
        if error_kind:
            payload["error_kind"] = error_kind
        error_detail = summary.get("error_detail")
        if isinstance(error_detail, str) and error_detail.strip():
            payload["error_detail"] = error_detail.strip()
        if "has_error" in summary:
            payload["has_error"] = bool(summary.get("has_error"))
    return payload


async def _apply_forced_pretool_batches(
    forced_batches: List[Dict[str, Any]],
    tools_used: List[str],
    tool_details: List[Dict[str, Any]],
    *,
    has_exportable: bool,
    export_idx: Optional[int],
    on_detail=None,
    emit_batch_kinds: Optional[set[str]] = None,
) -> tuple[bool, Optional[int]]:
    for batch in forced_batches:
        has_exportable, export_idx = _merge_tool_batch_results(
            tools_used,
            tool_details,
            batch["tools_used"],
            batch["tool_details"],
            has_exportable=has_exportable,
            export_idx=export_idx,
        )
        if not on_detail:
            continue
        batch_kind = str(batch.get("kind", "") or "").strip()
        if emit_batch_kinds and batch_kind not in emit_batch_kinds:
            continue
        for detail in batch.get("tool_details", []):
            maybe_value = on_detail(batch_kind, detail)
            if inspect.isawaitable(maybe_value):
                await maybe_value
    return has_exportable, export_idx


async def _run_forced_pretool_batches(
    question: str,
    conv_id: str,
    *,
    user_sub: str = "",
) -> List[Dict[str, Any]]:
    batches = []

    visual_tools_used, visual_tool_details = await _run_forced_visual_userstory(
        question,
        conv_id,
        user_sub=user_sub,
    )
    batches.append(
        {
            "kind": "visual_userstory",
            "tools_used": visual_tools_used,
            "tool_details": visual_tool_details,
        }
    )

    dual_tools_used, dual_tool_details = await _run_forced_dual_hierarchy(
        question,
        conv_id,
        user_sub=user_sub,
    )
    batches.append(
        {
            "kind": "dual_hierarchy",
            "tools_used": dual_tools_used,
            "tool_details": dual_tool_details,
        }
    )

    followup_tools_used, followup_tool_details = await _run_forced_workitem_filter_followup(
        question,
        conv_id,
        user_sub=user_sub,
    )
    batches.append(
        {
            "kind": "workitem_filter_followup",
            "tools_used": followup_tools_used,
            "tool_details": followup_tool_details,
        }
    )

    return batches


async def _collect_stream_forced_pretool_payloads(
    question: str,
    conv_id: str,
    *,
    user_sub: str,
    tools_used: List[str],
    tool_details: List[Dict[str, Any]],
    has_exportable: bool,
    export_idx: Optional[int],
) -> tuple[List[Dict[str, Any]], bool, Optional[int]]:
    forced_batches = await _run_forced_pretool_batches(
        question,
        conv_id,
        user_sub=user_sub,
    )

    emitted_payloads: List[Dict[str, Any]] = []

    async def _collect_forced_detail(_batch_kind: str, detail: Dict[str, Any]):
        emitted_payloads.append({"type": "tool_start", "tool": detail["tool"], "text": f"🔍 {detail['tool']}..."})
        emitted_payloads.append(_build_stream_tool_result_event(detail))

    has_exportable, export_idx = await _apply_forced_pretool_batches(
        forced_batches,
        tools_used,
        tool_details,
        has_exportable=has_exportable,
        export_idx=export_idx,
        on_detail=_collect_forced_detail,
        emit_batch_kinds={"dual_hierarchy", "workitem_filter_followup"},
    )
    return emitted_payloads, has_exportable, export_idx


async def _persist_conversation(conv_id: str, partition_key: str) -> None:
    """Persiste conversa na tabela ChatHistory (fire-and-forget)."""
    try:
        async with await _get_conversation_lock(conv_id):
            msgs = conversations.get(conv_id)
            if not msgs:
                return

            compact = _prepare_messages_for_storage(msgs)
            messages_json = json.dumps(compact, ensure_ascii=False, default=str)

            meta = await _get_conversation_meta(conv_id)
            entity = {
                "PartitionKey": partition_key,
                "RowKey": conv_id,
                "Messages": messages_json,
                "Mode": meta.get("mode", "general"),
                "CreatedAt": meta.get("created_at", datetime.now(timezone.utc).isoformat()),
                "UpdatedAt": datetime.now(timezone.utc).isoformat(),
                "MessageCount": len(compact),
            }

        inserted = await table_insert("ChatHistory", entity)
        if not inserted:
            await table_merge("ChatHistory", entity)
        conversations.mark_clean(conv_id)
        logger.debug("[Agent] _persist_conversation OK for %s (write-through clean)", conv_id)
    except Exception as e:
        logger.error("[Agent] _persist_conversation failed for %s: %s", conv_id, e, exc_info=True)


# =============================================================================
# TOOL EXECUTION HELPER
# =============================================================================

async def _execute_tool_calls(
    tool_calls: List[LLMToolCall], conv_id: str, user_sub: str = "",
) -> tuple[List[str], List[Dict]]:
    """Executa tools em paralelo, retorna (tools_used, tool_details)."""
    tools_used = []
    tool_details = []
    request_id = get_request_id()

    def _latest_user_text() -> str:
        for msg in reversed(conversations.get(conv_id, [])):
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = []
                for p in content:
                    if isinstance(p, dict) and p.get("type") == "text":
                        parts.append(str(p.get("text", "")))
                return " ".join(parts).strip()
        return ""

    def _latest_chart_ready() -> Dict:
        for msg in reversed(conversations.get(conv_id, [])):
            if msg.get("role") != "tool":
                continue
            content = msg.get("content", "")
            if not isinstance(content, str):
                continue
            try:
                parsed = json.loads(content)
            except Exception:
                continue
            if not isinstance(parsed, dict):
                continue
            chart_ready = parsed.get("chart_ready")
            if isinstance(chart_ready, dict) and chart_ready:
                return chart_ready
        return {}

    def _looks_like_run_code_refusal(result: dict) -> bool:
        if not isinstance(result, dict):
            return False
        has_artifacts = bool(
            result.get("files")
            or result.get("images")
            or result.get("generated_artifacts")
            or result.get("items")
        )
        if has_artifacts:
            return False
        raw = " ".join(
            str(result.get(k, "") or "")
            for k in ("stdout", "stderr", "error", "output_text")
        )
        norm = _normalize_request_text(raw)
        refusal_markers = (
            "nao consigo",
            "nao foi possivel",
            "nao e possivel",
            "limitac",
            "restric",
            "nao posso",
            "nao permite",
            "falta",
        )
        return any(marker in norm for marker in refusal_markers)

    def _blocked_tool_response(
        tc: LLMToolCall,
        started: float,
        result: Dict[str, Any],
        *,
        reason: str = "",
    ) -> tuple[LLMToolCall, Dict[str, Any], int, str]:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if isinstance(result, dict):
            result = dict(result)
            result["is_error"] = True
            result["error_kind"] = "blocked"
            result["error_detail"] = _normalize_tool_error_detail(result) or str(result.get("error", "") or "").strip()
        event = {
            "event": "tool_call_execution",
            "conv_id": conv_id,
            "request_id": request_id,
            "user_sub": user_sub or "anon",
            "tool": tc.name,
            "tool_call_id": tc.id,
            "status": "blocked",
            "duration_ms": elapsed_ms,
        }
        if reason:
            event["reason"] = reason
        logger.info("[ToolCallEvent] %s", json.dumps(event, ensure_ascii=False))
        tool_metrics.record(
            tc.name,
            elapsed_ms,
            "blocked",
            request_id=request_id,
            conversation_id=conv_id,
            error_kind="blocked",
        )
        return tc, result, elapsed_ms, "blocked"

    def _invalid_tool_args_response(
        tc: LLMToolCall,
        started: float,
        validation_errors: List[str],
    ) -> tuple[LLMToolCall, Dict[str, Any], int, str]:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        result = {
            "error": "Argumentos inválidos para a tool.",
            "validation_errors": validation_errors[:20],
            "is_error": True,
            "error_kind": "invalid_tool_arguments",
            "error_detail": "Argumentos inválidos para a tool.",
        }
        event = {
            "event": "tool_call_execution",
            "conv_id": conv_id,
            "request_id": request_id,
            "user_sub": user_sub or "anon",
            "tool": tc.name,
            "tool_call_id": tc.id,
            "status": "tool_error",
            "duration_ms": elapsed_ms,
            "reason": "invalid_tool_arguments",
            "validation_errors": validation_errors[:5],
        }
        logger.info("[ToolCallEvent] %s", json.dumps(event, ensure_ascii=False))
        tool_metrics.record(
            tc.name,
            elapsed_ms,
            "tool_error",
            request_id=request_id,
            conversation_id=conv_id,
            error_kind="invalid_tool_arguments",
        )
        return tc, result, elapsed_ms, "tool_error"
    
    async def _run(tc: LLMToolCall):
        started = time.perf_counter()
        status = "ok"
        error_msg = ""
        args = tc.arguments
        user_text = _latest_user_text()
        norm_user = _normalize_request_text(user_text)
        file_analysis_intent = bool(
            re.search(
                r"\b(ficheiro|arquivo|csv|excel|xlsx|tabela|dados|coluna|linhas|registos?|analisa|analisar|resumo|"
                r"estatistic|minimo|maximo|media|desvio|grafico|chart|correlacao|scatter|lista completa|valores distintos)\b",
                norm_user,
            )
        )
        # --- Routing guardrail: CSV/Excel uploaded -> never query DevOps ---
        files = await _get_uploaded_files_async(conv_id)
        if files and tc.name in ("query_workitems", "search_workitems", "compute_kpi", "query_hierarchy"):
            _file_keywords = ("csv", "excel", "xlsx", "tabela", "ficheiro", "upload", "dados", "coluna",
                              "linha", "media", "soma", "total", "grafico", "chart", "analise", "analisa")
            _user_lower = user_text.lower()
            if any(kw in _user_lower for kw in _file_keywords):
                logger.info("[Agent] Guardrail: blocked %s — uploaded files present and user references data", tc.name)
                return _blocked_tool_response(
                    tc,
                    started,
                    {
                        "error": (
                            "Existem ficheiros carregados nesta conversa. "
                            "Para análise de ficheiro carregado, usa run_code como primeira tentativa. "
                            "Se run_code falhar, usa analyze_uploaded_table como fallback. "
                            "Para gerar gráficos, usa generate_chart com os dados da análise. "
                            "query_workitems/search_workitems sao para Azure DevOps, nao para ficheiros carregados."
                        )
                    },
                )
        # S4-02: Auto-load writer profile for story generation
        if tc.name == "generate_user_stories" and user_sub and not args.get("writer_style_hint"):
            try:
                from tools_learning import _load_writer_profile, _load_user_default_profile
                # Try reference_author first, then user's default profile
                ref_author = str(args.get("reference_author", "") or "").strip()
                profile = None
                if ref_author:
                    profile = await _load_writer_profile(ref_author, owner_sub=user_sub)
                if not profile:
                    # Load the user's most recent profile (any author)
                    profile = await _load_user_default_profile(owner_sub=user_sub)
                if profile and profile.get("style_analysis"):
                    style_hint = (
                        f"[Perfil de escrita do utilizador]\n"
                        f"Estilo: {profile['style_analysis'][:2000]}\n"
                    )
                    if profile.get("preferred_vocabulary"):
                        style_hint += f"Vocabulário preferido: {profile['preferred_vocabulary'][:500]}\n"
                    if profile.get("title_pattern"):
                        style_hint += f"Padrão de títulos: {profile['title_pattern'][:300]}\n"
                    if profile.get("ac_structure"):
                        style_hint += f"Estrutura de ACs: {profile['ac_structure'][:500]}\n"
                    args["writer_style_hint"] = style_hint
                    logger.info("[Agent] S4-02: Auto-applied writer profile for user %s", user_sub[:20])
            except Exception as e:
                logger.debug("[Agent] S4-02: Writer profile auto-load failed: %s", e)
        # Auto-inject file context for US generation
        if tc.name == "generate_user_stories" and files and not args.get("context"):
            context_blocks = []
            per_file_budget = max(2000, min(12000, 80000 // max(1, len(files))))
            for idx, f in enumerate(files[-MAX_FILES_CONTEXT:], 1):
                fname = f.get("filename", f"ficheiro_{idx}")
                content = str(f.get("data_text", "") or "")
                if not content:
                    continue
                context_blocks.append(f"[{idx}] {fname}\n{content[:per_file_budget]}")
            if context_blocks:
                args["context"] = "\n\n".join(context_blocks)[:90000]

        runtime_profile = get_tool_runtime_profile(tc.name)
        if runtime_profile.get("inject_user_sub") and user_sub and not args.get("user_sub"):
            args["user_sub"] = user_sub
        if runtime_profile.get("inject_conv_id") and not args.get("conv_id"):
            args["conv_id"] = conv_id
        if tc.name == "analyze_uploaded_table" and "full_points" not in args:
            args["full_points"] = True
        if tc.name == "analyze_uploaded_table" and "top" not in args:
            args["top"] = 5000
        if (
            files
            and tc.name == "run_code"
            and file_analysis_intent
            and _should_prefer_tabular_tool_over_run_code(user_text)
        ):
            logger.info(
                "[Agent] rerouting run_code -> analyze_uploaded_table for exploratory tabular request (conv=%s)",
                conv_id,
            )
            fallback_args: Dict[str, object] = {
                "query": user_text,
                "conv_id": conv_id,
                "user_sub": user_sub,
                "full_points": True,
                "top": 5000,
                "_rerouted_from_run_code": True,
            }
            result = await execute_tool("analyze_uploaded_table", fallback_args)
            if isinstance(result, dict):
                result["_handled_by"] = "analyze_uploaded_table"
                result["_rerouted_from"] = "run_code"
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            event = {
                "event": "tool_call_execution",
                "conv_id": conv_id,
                "request_id": request_id,
                "user_sub": user_sub or "anon",
                "tool": tc.name,
                "tool_call_id": tc.id,
                "status": status,
                "duration_ms": elapsed_ms,
                "routing": "run_code_to_analyze_uploaded_table",
            }
            logger.info("[ToolCallEvent] %s", json.dumps(event, ensure_ascii=False))
            tool_metrics.record(
                tc.name,
                elapsed_ms,
                status,
                request_id=request_id,
                conversation_id=conv_id,
            )
            return tc, result, elapsed_ms, status
        if tc.name == "generate_chart":
            has_explicit_data = bool(
                args.get("series")
                or args.get("x_values")
                or args.get("y_values")
                or args.get("labels")
                or args.get("values")
            )
            if not has_explicit_data:
                chart_ready = _latest_chart_ready()
                if chart_ready:
                    for key in ("chart_type", "title", "x_values", "y_values", "labels", "values", "series", "x_label", "y_label"):
                        if key not in args and key in chart_ready:
                            args[key] = chart_ready.get(key)
        if tc.name == "query_hierarchy":
            has_parent_id = bool(args.get("parent_id"))
            parent_type = str(args.get("parent_type", "") or "").strip().lower()
            title_contains = str(args.get("title_contains", "") or "").strip()
            explicit_title_filter = bool(re.search(r"\bt[ií]tulo\b|\btitle\b", user_text.lower()))
            if not has_parent_id and parent_type in ("epic", "feature"):
                m = None
                if parent_type == "epic":
                    m = re.search(
                        r"\bepic\b\s+(.+?)(?:\bcujo\b|\bcom\s+t[ií]tulo\b|\bt[ií]tulo\b|\bna\b|\bno\b|\bdentro\b|[?.!,]|$)",
                        user_text,
                        re.IGNORECASE,
                    )
                elif parent_type == "feature":
                    m = re.search(
                        r"\bfeature\b\s+(.+?)(?:\bcujo\b|\bcom\s+t[ií]tulo\b|\bt[ií]tulo\b|\bna\b|\bno\b|\bdentro\b|[?.!,]|$)",
                        user_text,
                        re.IGNORECASE,
                    )

                hint = ""
                if m:
                    hint = str(m.group(1) or "").strip()
                if hint and not re.search(r"\d{4,9}", hint):
                    args["parent_title_hint"] = hint[:160]
                    if title_contains and not explicit_title_filter:
                        args["title_contains"] = ""
                elif title_contains and not explicit_title_filter:
                    args["parent_title_hint"] = title_contains
                    args["title_contains"] = ""
        validation_errors = validate_tool_arguments(tc.name, args)
        if validation_errors:
            logger.warning(
                "[Agent] Invalid tool arguments generated for %s (conv=%s): %s",
                tc.name,
                conv_id,
                "; ".join(validation_errors[:5]),
            )
            return _invalid_tool_args_response(tc, started, validation_errors)
        if tc.name == "create_workitem":
            if _has_explicit_create_confirmation(conv_id):
                args["confirmed"] = True
                args["confirmation_token"] = issue_create_workitem_confirmation_token(conv_id, user_sub=user_sub)
                args["conv_id"] = conv_id
            else:
                return _blocked_tool_response(
                    tc,
                    started,
                    {
                        "error": (
                            "Confirmação explícita necessária para criar work item. "
                            "Pede ao utilizador para responder 'confirmo' e tenta novamente."
                        )
                    },
                )
        try:
            result = await execute_tool(tc.name, args)
            if tc.name == "run_code":
                failed = (
                    bool(result.get("error"))
                    or not bool(result.get("success", False))
                    or _looks_like_run_code_refusal(result)
                )
                if failed:
                    logger.info(
                        "[Agent] run_code falhou; a executar fallback analyze_uploaded_table (conv=%s)",
                        conv_id,
                    )
                    fallback_args: Dict[str, object] = {
                        "query": user_text,
                        "conv_id": conv_id,
                        "user_sub": user_sub,
                        "full_points": True,
                        "top": 5000,
                        "_fallback_from_run_code": True,
                    }
                    fallback = await execute_tool("analyze_uploaded_table", fallback_args)
                    if isinstance(fallback, dict):
                        fallback["_fallback_from"] = "run_code"
                        fallback["_run_code_error"] = str(
                            result.get("error")
                            or result.get("stderr")
                            or result.get("stdout")
                            or result.get("output_text")
                            or ""
                        )[:500]
                        result = fallback
            if isinstance(result, dict) and "error" in result:
                status = "tool_error"
        except Exception as e:
            status = "exception"
            error_msg = str(e)
            raise
        finally:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            event = {
                "event": "tool_call_execution",
                "conv_id": conv_id,
                "request_id": request_id,
                "user_sub": user_sub or "anon",
                "tool": tc.name,
                "tool_call_id": tc.id,
                "status": status,
                "duration_ms": elapsed_ms,
            }
            if error_msg:
                event["error"] = error_msg[:300]
            logger.info("[ToolCallEvent] %s", json.dumps(event, ensure_ascii=False))
            tool_metrics.record(
                tc.name,
                elapsed_ms,
                status,
                request_id=request_id,
                conversation_id=conv_id,
                error_kind=_normalize_tool_error_kind(status, locals().get("result")),
            )
        return tc, result, elapsed_ms, status
    
    results = await asyncio.gather(
        *[_run(tc) for tc in tool_calls],
        return_exceptions=True,
    )
    
    for tc, res in zip(tool_calls, results):
        if isinstance(res, Exception):
            logger.error("[Agent] Tool execution failed for %s: %s", tc.name, res)
            conversations[conv_id].append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps({"error": f"Tool execution crashed: {str(res)}", "_persisted_summary": True}),
                "result_blob_ref": "",
            })
            conversations.mark_dirty(conv_id)
            continue
        if not isinstance(res, tuple):
            logger.error("[Agent] Unexpected tool result envelope for %s: %r", tc.name, res)
            tool_result = {"error": "Tool returned invalid envelope."}
            tool_elapsed_ms = 0
        elif len(res) == 4:
            _, tool_result, tool_elapsed_ms, tool_status = res
        elif len(res) == 3:
            _, tool_result, tool_elapsed_ms = res
            tool_status = "tool_error" if isinstance(tool_result, dict) and "error" in tool_result else "ok"
        elif len(res) == 2:
            logger.warning("[Agent] Legacy 2-tuple tool envelope detected for %s; coercing safely", tc.name)
            _, tool_result = res
            tool_elapsed_ms = 0
            tool_status = "tool_error" if isinstance(tool_result, dict) and "error" in tool_result else "ok"
        else:
            logger.error("[Agent] Unsupported tool result tuple size for %s: %s", tc.name, len(res))
            tool_result = {"error": "Tool returned unsupported tuple envelope."}
            tool_elapsed_ms = 0
            tool_status = "tool_error"
        tools_used.append(tc.name)

        tool_result = _normalize_tool_result_envelope(tool_result, tool_status)

        # S4-01: Track story generation count for proactive learning suggestion
        if tc.name == "generate_user_stories" and isinstance(tool_result, dict) and "error" not in tool_result:
            _increment_story_count(user_sub)

        result_blob_ref = ""
        try:
            safe_user = _safe_blob_component(user_sub or "anon", 80)
            safe_conv = _safe_blob_component(conv_id, 80)
            safe_tool = _safe_blob_component(tc.name, 40)
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            blob_name = f"{safe_user}/{safe_conv}/{ts}_{safe_tool}_{_safe_blob_component(tc.id, 60)}.json"

            blob_payload = tool_result
            if PII_ENABLED:
                try:
                    blob_ctx = PIIMaskingContext()
                    blob_payload = await _mask_pii_structured(tool_result, blob_ctx)
                except Exception as mask_err:
                    logger.warning("[Agent] PII masking for blob failed (%s): %s", tc.name, mask_err)
                    # Never fall back to unmasked PII in blob storage.
                    blob_payload = {"error": "pii_masking_failed", "tool": tc.name}

            uploaded = await blob_upload_json(CHAT_TOOLRESULT_BLOB_CONTAINER, blob_name, blob_payload)
            result_blob_ref = str(uploaded.get("blob_ref", "") or "")
        except Exception as e:
            logger.warning("[Agent] tool result blob persist failed (%s): %s", tc.name, e)
        
        serialized_tool_result = truncate_tool_result(
            json.dumps(tool_result, ensure_ascii=False, default=str)
        )

        result_summary = _build_tool_result_summary(tool_result, tool_status)

        td = {
            "tool": tc.name, "arguments": tc.arguments,
            "result_summary": result_summary,
            "result_json": serialized_tool_result,
            "result_blob_ref": result_blob_ref,
            "duration_ms": tool_elapsed_ms,
        }
        tool_details.append(td)
        result_str = serialized_tool_result
        
        # Add tool result to conversation
        conversations[conv_id].append({
            "role": "tool",
            "tool_call_id": tc.id,
            "content": result_str,
            "result_blob_ref": result_blob_ref,
        })
        conversations.mark_dirty(conv_id)

    return tools_used, tool_details


# =============================================================================
# AGENT CHAT (request/response clássico)
# =============================================================================

async def agent_chat(request: AgentChatRequest, user: dict) -> AgentChatResponse:
    start = datetime.now(timezone.utc)
    mode = request.mode or "general"
    tier = request.model_tier or LLM_DEFAULT_TIER
    conv_id = request.conversation_id or str(uuid.uuid4())
    request_id = str(uuid.uuid4())
    partition_key = _user_partition_key(user)

    tools_used = []
    tool_details = []
    total_usage = {}
    model_used = ""
    is_fallback = False
    has_exportable = False
    export_idx = None
    should_persist = False
    tool_definitions = get_all_tool_definitions()
    answer = "Não consegui processar a tua pergunta."
    request_status = "ok"
    request_error_kind = ""
    request_user_sub = str((user or {}).get("sub", "") or "")
    request_ctx_tokens = set_request_context(request_id=request_id, conversation_id=conv_id)
    try:
        ctx = format_request_context()
        prefix = f"[{ctx}] " if ctx else ""
        logger.info("[Agent] %sstart route=chat mode=%s tier=%s", prefix, mode, tier)

        async with await _get_conversation_lock(conv_id):
            effective_question, pending_reply = await _bootstrap_agent_turn(
                conv_id,
                mode,
                partition_key,
                request.question,
                user_sub=request_user_sub,
            )

            if pending_reply:
                answer = pending_reply
                await _append_assistant_text(conv_id, answer)
                should_persist = True
            else:
                quota_reason = await _check_pre_model_blockers(tier, conv_id, request_user_sub)
                if quota_reason:
                    request_status = "blocked"
                    request_error_kind = "pre_model_blocked"
                    answer = f"Erro real detetado antes de chamar o modelo: {quota_reason}."
                    await _append_assistant_text(conv_id, answer)
                    should_persist = True
                else:
                    try:
                        forced_batches = await _run_forced_pretool_batches(
                            effective_question,
                            conv_id,
                            user_sub=request_user_sub,
                        )
                        has_exportable, export_idx = await _apply_forced_pretool_batches(
                            forced_batches,
                            tools_used,
                            tool_details,
                            has_exportable=has_exportable,
                            export_idx=export_idx,
                        )

                        # Agent loop
                        response, total_usage, model_used, is_fallback = await _run_tool_detection_llm_round(
                            conv_id,
                            effective_question,
                            request=request,
                            tier=tier,
                            tool_definitions=tool_definitions,
                            user_sub=request_user_sub,
                            total_usage=total_usage,
                            model_used=model_used,
                            is_fallback=is_fallback,
                        )
                        (
                            response,
                            total_usage,
                            model_used,
                            is_fallback,
                            has_exportable,
                            export_idx,
                            exhausted_tool_loop,
                        ) = await _run_tool_call_iterations(
                            response,
                            conv_id,
                            effective_question,
                            request=request,
                            tier=tier,
                            tool_definitions=tool_definitions,
                            user_sub=request_user_sub,
                            tools_used=tools_used,
                            tool_details=tool_details,
                            total_usage=total_usage,
                            model_used=model_used,
                            is_fallback=is_fallback,
                            has_exportable=has_exportable,
                            export_idx=export_idx,
                        )

                        if exhausted_tool_loop:
                            answer = _fallback_answer_from_tool_details(tool_details)
                        else:
                            request_status = _derive_request_status_from_llm_payload(request_status, response)
                            request_error_kind = _derive_request_error_kind_from_llm_payload(request_error_kind, response)
                            answer = _llm_payload_text(response) or "Não consegui processar a tua pergunta."
                        await _append_assistant_text(
                            conv_id,
                            answer,
                            effective_question=effective_question,
                            refresh_polymorphic=True,
                        )
                        should_persist = True

                    except Exception as e:
                        request_status = "error"
                        request_error_kind = "agent_exception"
                        logger.error("[Agent] %sagent_chat exception: %s", prefix, e, exc_info=True)
                        answer = _format_agent_error_for_user(e)

        total_time = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        await _finalize_agent_request(
            "chat",
            conv_id,
            partition_key,
            tier,
            total_usage,
            user_sub=request_user_sub,
            should_persist=should_persist,
            total_time_ms=total_time,
            tool_count=len(tool_details),
            is_fallback=is_fallback,
            request_status=request_status,
            request_error_kind=request_error_kind,
            persist_task_label="persist_conversation_sync_timeout_fallback",
            model_used=model_used,
        )
        logger.info("[Agent] %send route=chat status=%s total_ms=%d", prefix, request_status, total_time)
        response_meta = _build_agent_response_metadata(
            conv_id,
            request_id,
            request_status,
            request_error_kind,
            tools_used,
            tool_details,
            total_usage,
            total_time,
            model_used,
            is_fallback,
            has_exportable=has_exportable,
            export_idx=export_idx,
        )
        return AgentChatResponse(
            answer=answer,
            mode=mode,
            **response_meta,
        )
    finally:
        reset_request_context(request_ctx_tokens)


# =============================================================================
# AGENT CHAT STREAM (SSE)
# =============================================================================

async def agent_chat_stream(request: AgentChatRequest, user: dict) -> AsyncGenerator[str, None]:
    """SSE streaming — yields 'data: {json}\n\n' strings."""
    start = datetime.now(timezone.utc)
    mode = request.mode or "general"
    tier = request.model_tier or LLM_DEFAULT_TIER
    conv_id = request.conversation_id or str(uuid.uuid4())
    request_id = str(uuid.uuid4())
    partition_key = _user_partition_key(user)

    tools_used = []
    tool_details = []
    total_usage = {}
    model_used = ""
    is_fallback = False
    should_persist = False
    has_exportable = False
    export_idx = None
    tool_definitions = get_all_tool_definitions()
    request_status = "ok"
    request_error_kind = ""
    request_user_sub = str((user or {}).get("sub", "") or "")
    
    def _sse(event: dict) -> str:
        payload = dict(event or {})
        payload.setdefault("request_id", request_id)
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
    
    request_ctx_tokens = set_request_context(request_id=request_id, conversation_id=conv_id)
    try:
        ctx = format_request_context()
        prefix = f"[{ctx}] " if ctx else ""
        logger.info("[Agent] %sstart route=chat_stream mode=%s tier=%s", prefix, mode, tier)

        async with await _get_conversation_lock(conv_id):
            effective_question, pending_reply = await _bootstrap_agent_turn(
                conv_id,
                mode,
                partition_key,
                request.question,
                user_sub=request_user_sub,
            )

            # Emit conversation_id immediately
            yield _sse({"type": "init", "conversation_id": conv_id, "mode": mode})

            if pending_reply:
                await _append_assistant_text(conv_id, pending_reply)
                should_persist = True
                yield _sse({"type": "token", "text": pending_reply})
            else:
                quota_reason = await _check_pre_model_blockers(tier, conv_id, request_user_sub)
                if quota_reason:
                    request_status = "blocked"
                    request_error_kind = "pre_model_blocked"
                    yield _sse({
                        "type": "error",
                        "text": f"Erro real detetado antes de chamar o modelo: {quota_reason}.",
                    })
                else:
                    try:
                        emitted_forced_events, has_exportable, export_idx = await _collect_stream_forced_pretool_payloads(
                            effective_question,
                            conv_id,
                            user_sub=request_user_sub,
                            tools_used=tools_used,
                            tool_details=tool_details,
                            has_exportable=has_exportable,
                            export_idx=export_idx,
                        )
                        for payload in emitted_forced_events:
                            yield payload

                        provider = get_provider(tier)
                        model_used = getattr(provider, 'model', getattr(provider, 'deployment', ''))
                        stream_loop_state = {
                            "total_usage": total_usage,
                            "model_used": model_used,
                            "is_fallback": is_fallback,
                            "has_exportable": has_exportable,
                            "export_idx": export_idx,
                        }
                        async for payload in _stream_tool_loop_payloads(
                            conv_id,
                            effective_question,
                            request=request,
                            tier=tier,
                            tool_definitions=tool_definitions,
                            user_sub=request_user_sub,
                            tools_used=tools_used,
                            tool_details=tool_details,
                            state=stream_loop_state,
                        ):
                            yield _sse(payload)

                        response = stream_loop_state["response"]
                        total_usage = stream_loop_state["total_usage"]
                        model_used = stream_loop_state["model_used"]
                        is_fallback = stream_loop_state["is_fallback"]
                        has_exportable = stream_loop_state["has_exportable"]
                        export_idx = stream_loop_state["export_idx"]
                        exhausted_tool_loop = bool(stream_loop_state.get("exhausted_tool_loop"))

                        if exhausted_tool_loop:
                            # Garantir sempre resposta textual quando o loop atinge limite de iterações.
                            fallback_text = _fallback_answer_from_tool_details(tool_details)
                            yield _sse({"type": "token", "text": fallback_text})
                            await _append_assistant_text(
                                conv_id,
                                fallback_text,
                                effective_question=effective_question,
                                refresh_polymorphic=True,
                            )
                            should_persist = True
                        else:
                            stream_state = {
                                "request_status": request_status,
                                "request_error_kind": request_error_kind,
                                "total_usage": total_usage,
                                "model_used": model_used,
                                "is_fallback": is_fallback,
                                "should_persist": should_persist,
                            }
                            async for payload in _stream_final_response_payloads(
                                response,
                                conv_id,
                                effective_question,
                                request=request,
                                tier=tier,
                                user_sub=request_user_sub,
                                prefix=prefix,
                                state=stream_state,
                            ):
                                yield _sse(payload)
                            request_status = stream_state["request_status"]
                            request_error_kind = stream_state["request_error_kind"]
                            total_usage = stream_state["total_usage"]
                            model_used = stream_state["model_used"]
                            is_fallback = stream_state["is_fallback"]
                            should_persist = stream_state["should_persist"]

                    except Exception as e:
                        request_status = "error"
                        request_error_kind = "agent_exception"
                        logger.error("[Agent] %sagent_chat_stream exception: %s", prefix, e, exc_info=True)
                        yield _sse({"type": "error", "text": _format_agent_error_for_user(e)})

        total_time = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        await _finalize_agent_request(
            "chat_stream",
            conv_id,
            partition_key,
            tier,
            total_usage,
            user_sub=request_user_sub,
            should_persist=should_persist,
            total_time_ms=total_time,
            tool_count=len(tool_details),
            is_fallback=is_fallback,
            request_status=request_status,
            request_error_kind=request_error_kind,
            persist_task_label="persist_conversation_stream_timeout_fallback",
            model_used=model_used,
        )
        # S4-01: Check if we should suggest writer profile learning
        user_sub_for_suggest = str((user or {}).get("sub", "") or "")
        suggest_learn = _should_suggest_writer_profile(user_sub_for_suggest)
        logger.info("[Agent] %send route=chat_stream status=%s total_ms=%d", prefix, request_status, total_time)
        yield _sse({"type": "done", **_build_agent_response_metadata(
            conv_id,
            request_id,
            request_status,
            request_error_kind,
            tools_used,
            tool_details,
            total_usage,
            total_time,
            model_used,
            is_fallback,
            has_exportable=has_exportable,
            export_idx=export_idx,
            suggest_learn_style=suggest_learn,
        )})
    finally:
        reset_request_context(request_ctx_tokens)


# =============================================================================
# MODE SWITCHING
# =============================================================================

async def switch_conversation_mode(conv_id: str, new_mode: str) -> bool:
    """Muda o modo de uma conversa existente. Reinjecta system prompt."""
    if conv_id not in conversations:
        return False
    if new_mode not in ("general", "userstory"):
        return False
    
    sp = get_userstory_system_prompt() if new_mode == "userstory" else get_agent_system_prompt()

    async with await _get_conversation_lock(conv_id):
        if conv_id not in conversations:
            return False
        new_msgs = [{"role": "system", "content": sp}]
        new_msgs.extend(m for m in conversations[conv_id] if m.get("role") != "system")
        conversations[conv_id] = new_msgs

    async with _conversation_meta_lock:
        meta = dict(conversation_meta.get(conv_id, {}))
        meta["mode"] = new_mode
        conversation_meta[conv_id] = meta
    
    return True
