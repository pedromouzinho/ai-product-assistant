# =============================================================================
# llm_provider.py — Abstração Multi-Modelo do AI Product Assistant v7.2
# =============================================================================
# Suporta Azure OpenAI (GPT-4.1, GPT-4.1-mini) e Anthropic (Claude Opus,
# Sonnet, Haiku). Normaliza tool calling, streaming e respostas.
# =============================================================================

import json
import re
import asyncio
import inspect
import uuid
import logging
from typing import AsyncGenerator, Optional, List, Dict, Any
from collections import deque
from datetime import datetime, timezone

import httpx

from azure_auth import build_azure_openai_auth_headers, close_azure_auth
from config import (
    AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_KEY, AZURE_OPENAI_BASE_URL,
    AZURE_OPENAI_API_PREFIX, AZURE_OPENAI_AUTH_MODE, AZURE_OPENAI_AUTH_HEADER,
    AZURE_OPENAI_AUTH_VALUE, CHAT_DEPLOYMENT,
    EMBEDDING_DEPLOYMENT, API_VERSION_CHAT, API_VERSION_OPENAI,
    ANTHROPIC_API_KEY, ANTHROPIC_API_BASE, ANTHROPIC_BASE_URL,
    ANTHROPIC_MESSAGES_PATH, ANTHROPIC_AUTH_MODE, ANTHROPIC_AUTH_HEADER,
    ANTHROPIC_AUTH_VALUE,
    ANTHROPIC_MODEL_OPUS, ANTHROPIC_MODEL_SONNET,
    ANTHROPIC_MODEL_HAIKU,
    LLM_DEFAULT_TIER, LLM_TIER_FAST, LLM_TIER_STANDARD, LLM_TIER_PRO, LLM_TIER_VISION,
    LLM_FALLBACK, AGENT_MAX_TOKENS, AGENT_TEMPERATURE,
    DEBUG_LOG_SIZE,
    _normalize_url_path,
)
from http_helpers import _sanitize_error_response
from models import LLMResponse, LLMToolCall, StreamEvent
from pii_shield import PIIMaskingContext, mask_messages, PII_ENABLED
from prompt_shield import check_messages, PROMPT_SHIELD_ENABLED
from config import PROVIDER_GOVERNANCE_MODE, PROVIDER_EXTERNAL_MODEL_FAMILIES
from request_context import format_request_context, get_conversation_id, get_request_id

# Debug log ring buffer (shared across providers)
_llm_debug_log: deque = deque(maxlen=DEBUG_LOG_SIZE)
logger = logging.getLogger(__name__)

def get_debug_log() -> list:
    return list(_llm_debug_log)

def _log(msg: str):
    request_id = get_request_id()
    conversation_id = get_conversation_id()
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "msg": msg,
        "request_id": request_id,
        "conversation_id": conversation_id,
    }
    _llm_debug_log.append(entry)
    ctx = format_request_context()
    prefix = f"[{ctx}] " if ctx else ""
    logger.info("[LLM] %s%s", prefix, msg)


def _classify_provider_exception(exc: Exception) -> Dict[str, Any]:
    if isinstance(exc, (asyncio.TimeoutError, httpx.TimeoutException)):
        return {
            "kind": "timeout",
            "detail": "timeout a aguardar resposta do provider",
        }
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = int(getattr(exc.response, "status_code", 0) or 0)
        body = _sanitize_error_response(getattr(exc.response, "text", ""), 240)
        detail = f"HTTP {status_code}"
        if body:
            detail += f": {body}"
        return {
            "kind": "http_status",
            "status_code": status_code,
            "detail": detail,
        }
    if isinstance(exc, httpx.RequestError):
        return {
            "kind": "request_error",
            "detail": f"{exc.__class__.__name__}: {str(exc).strip() or 'erro de rede sem detalhe'}",
        }
    detail = str(exc).strip() or exc.__class__.__name__
    return {
        "kind": exc.__class__.__name__,
        "detail": f"{exc.__class__.__name__}: {detail}",
    }


def _format_failure_chain_for_user(fallback_chain: List[Dict[str, Any]]) -> str:
    failures = []
    for item in fallback_chain:
        if item.get("status") != "failed":
            continue
        provider = str(item.get("provider") or "provider").strip() or "provider"
        detail = str(item.get("detail") or item.get("error") or item.get("reason") or "erro sem detalhe").strip()
        failures.append(f"{provider}: {detail}")
    if not failures:
        return "A chamada ao modelo terminou sem conteúdo."
    return "Erro real detetado ao contactar os modelos: " + " | ".join(failures[:3])


def format_llm_error_for_user(error: Exception, fallback_chain: Optional[List[Dict[str, Any]]] = None) -> str:
    if fallback_chain:
        return _format_failure_chain_for_user(fallback_chain)
    info = _classify_provider_exception(error)
    return f"Erro real detetado ao contactar o modelo: {info.get('detail') or 'erro sem detalhe'}"


def _zero_usage() -> Dict[str, int]:
    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def _make_error_response(
    *,
    content: str,
    model: str,
    provider: str,
    error_kind: str,
    error_detail: Optional[str] = None,
    fallback_chain: Optional[List[Dict[str, Any]]] = None,
) -> LLMResponse:
    return LLMResponse(
        content=content,
        tool_calls=None,
        usage=_zero_usage(),
        model=model,
        provider=provider,
        is_error=True,
        error_kind=error_kind,
        error_detail=error_detail or content,
        fallback_chain=fallback_chain,
    )


def _make_error_done_payload(
    *,
    content: str,
    model: str,
    provider: str,
    error_kind: str,
    error_detail: Optional[str] = None,
    fallback_chain: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    return _make_error_response(
        content=content,
        model=model,
        provider=provider,
        error_kind=error_kind,
        error_detail=error_detail,
        fallback_chain=fallback_chain,
    ).model_dump()


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


def _is_gpt5_family(deployment: str) -> bool:
    """Detect GPT-5 style Azure deployments."""
    return "gpt-5" in (deployment or "").strip().lower()


def _normalize_base_url(value: str) -> str:
    return str(value or "").strip().rstrip("/")



def _join_url(base_url: str, *paths: str) -> str:
    url = _normalize_base_url(base_url)
    for path in paths:
        normalized = _normalize_url_path(path)
        if normalized:
            url = f"{url}{normalized}"
    return url


def _build_auth_headers(
    auth_mode: str,
    auth_header: str,
    auth_value: str,
    default_header: str,
) -> dict[str, str]:
    mode = str(auth_mode or "").strip().lower()
    header_name = str(auth_header or "").strip() or default_header
    secret = str(auth_value or "").strip()
    if not secret or mode in {"none", "disabled"}:
        return {}
    if mode == "bearer":
        if not secret.lower().startswith("bearer "):
            secret = f"Bearer {secret}"
        return {header_name or "Authorization": secret}
    return {header_name: secret}


# =============================================================================
# TOOL FORMAT TRANSLATION
# =============================================================================
# O nosso formato canónico é o OpenAI (porque as tools já estão definidas assim).
# Para Anthropic, traduzimos on-the-fly.

def _openai_tools_to_anthropic(tools: List[dict]) -> List[dict]:
    """Converte tool definitions de formato OpenAI → Anthropic."""
    anthropic_tools = []
    for tool in tools:
        if tool.get("type") != "function":
            continue
        fn = tool["function"]
        anthropic_tools.append({
            "name": fn["name"],
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
        })
    return anthropic_tools


_ANTHROPIC_IMAGE_DATA_URL_RE = re.compile(
    r"^data:(?P<media_type>[^;,]+);base64,(?P<data>.+)$",
    flags=re.IGNORECASE | re.DOTALL,
)
_ANTHROPIC_IMAGE_MEDIA_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
}


def _openai_content_to_anthropic(content: Any) -> Any:
    """Converte content blocks OpenAI para o formato esperado pelo Anthropic."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return content

    converted: List[dict] = []
    for block in content:
        if not isinstance(block, dict):
            text = str(block or "").strip()
            if text:
                converted.append({"type": "text", "text": text})
            continue

        block_type = str(block.get("type", "") or "").strip()
        if block_type == "text":
            converted.append({"type": "text", "text": str(block.get("text", "") or "")})
            continue

        if block_type == "image_url":
            image_url = block.get("image_url", {})
            raw_url = image_url.get("url") if isinstance(image_url, dict) else image_url
            raw_url = str(raw_url or "").strip()
            if not raw_url:
                continue

            match = _ANTHROPIC_IMAGE_DATA_URL_RE.match(raw_url)
            if not match:
                converted.append(
                    {
                        "type": "text",
                        "text": "[Imagem omitida: URL externa não suportada pelo provider Anthropic.]",
                    }
                )
                continue

            media_type = match.group("media_type").strip().lower()
            data = match.group("data").strip()
            if media_type == "image/jpg":
                media_type = "image/jpeg"

            if media_type not in _ANTHROPIC_IMAGE_MEDIA_TYPES:
                converted.append(
                    {
                        "type": "text",
                        "text": (
                            "[Imagem omitida: formato não suportado pelo provider Anthropic "
                            f"({media_type}).]"
                        ),
                    }
                )
                continue

            converted.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": data,
                    },
                }
            )
            continue

        converted.append(block)

    return converted


def _openai_messages_to_anthropic(messages: List[dict]) -> tuple[str, List[dict]]:
    """Converte messages de formato OpenAI → Anthropic.
    
    Anthropic separa system prompt dos messages.
    Anthropic não suporta role="tool" — converte para tool_result dentro de role="user".
    
    Returns: (system_prompt, anthropic_messages)
    """
    messages = _sanitize_openai_tool_messages(messages)
    system_parts = []
    anthropic_msgs = []
    
    i = 0
    while i < len(messages):
        msg = messages[i]
        role = msg.get("role", "")
        
        # System messages → extrair para system prompt separado
        if role == "system":
            system_parts.append(msg.get("content", ""))
            i += 1
            continue
        
        # User messages → passam directo
        if role == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                anthropic_msgs.append({"role": "user", "content": content})
            else:
                anthropic_msgs.append({"role": "user", "content": _openai_content_to_anthropic(content)})
            i += 1
            continue
        
        # Assistant messages (podem ter tool_calls)
        if role == "assistant":
            content_blocks = []
            text = msg.get("content")
            if isinstance(text, str):
                if text:
                    content_blocks.append({"type": "text", "text": text})
            elif isinstance(text, list):
                content_blocks.extend(_openai_content_to_anthropic(text))
            
            # Converter tool_calls do formato OpenAI → Anthropic tool_use blocks
            tool_calls = msg.get("tool_calls", [])
            for tc in tool_calls:
                fn = tc.get("function", {})
                try:
                    args = json.loads(fn.get("arguments", "{}"))
                except (json.JSONDecodeError, TypeError):
                    args = {}
                content_blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id", str(uuid.uuid4())),
                    "name": fn.get("name", ""),
                    "input": args,
                })
            
            if content_blocks:
                anthropic_msgs.append({"role": "assistant", "content": content_blocks})
            i += 1
            continue
        
        # Tool results → converter para user message com tool_result blocks
        if role == "tool":
            # Agrupar tool results consecutivos num único user message
            tool_results = []
            while i < len(messages) and messages[i].get("role") == "tool":
                t = messages[i]
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": t.get("tool_call_id", ""),
                    "content": t.get("content", ""),
                })
                i += 1
            anthropic_msgs.append({"role": "user", "content": tool_results})
            continue
        
        # Qualquer outro role — skip
        i += 1
    
    # --- Sanitize: remove orphaned tool_result user messages that have no
    # preceding assistant tool_use block (prevents API errors). ---
    known_tool_use_ids: set[str] = set()
    sanitized: list[dict] = []
    for m in anthropic_msgs:
        if m.get("role") == "assistant":
            for block in (m.get("content") or []):
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    known_tool_use_ids.add(block.get("id", ""))
            sanitized.append(m)
        elif m.get("role") == "user" and isinstance(m.get("content"), list):
            # Check if all blocks are tool_result
            blocks = m["content"]
            if blocks and all(isinstance(b, dict) and b.get("type") == "tool_result" for b in blocks):
                # Keep only tool_results whose tool_use_id is known
                valid = [b for b in blocks if b.get("tool_use_id") in known_tool_use_ids]
                if valid:
                    sanitized.append({"role": "user", "content": valid})
                else:
                    logger.warning("[LLM] Dropped orphaned tool_result user message (no matching tool_use)")
            else:
                sanitized.append(m)
        else:
            sanitized.append(m)

    system_prompt = "\n\n".join(system_parts) if system_parts else ""
    return system_prompt, sanitized


def _anthropic_response_to_normalized(response: dict, model: str) -> LLMResponse:
    """Converte resposta Anthropic → formato normalizado LLMResponse."""
    content_blocks = response.get("content", [])
    
    text_parts = []
    tool_calls = []
    
    for block in content_blocks:
        btype = block.get("type", "")
        if btype == "text":
            text_parts.append(block.get("text", ""))
        elif btype == "tool_use":
            tool_calls.append(LLMToolCall(
                id=block.get("id", str(uuid.uuid4())),
                name=block.get("name", ""),
                arguments=block.get("input", {}),
            ))
    
    usage = response.get("usage", {})
    
    return LLMResponse(
        content="\n".join(text_parts) if text_parts else None,
        tool_calls=tool_calls if tool_calls else None,
        usage={
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
            "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
        },
        model=model,
        provider="anthropic",
    )


def _openai_response_to_normalized(response: dict, provider_name: str = "azure_openai") -> LLMResponse:
    """Converte resposta Azure OpenAI → formato normalizado LLMResponse."""
    choice = response.get("choices", [{}])[0]
    message = choice.get("message", {})
    
    tool_calls = None
    if message.get("tool_calls"):
        tool_calls = []
        for tc in message["tool_calls"]:
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments", "{}"))
            except (json.JSONDecodeError, TypeError):
                args = {}
            tool_calls.append(LLMToolCall(
                id=tc.get("id", str(uuid.uuid4())),
                name=fn.get("name", ""),
                arguments=args,
            ))
    
    usage = response.get("usage", {})
    model = response.get("model", "")
    
    return LLMResponse(
        content=message.get("content"),
        tool_calls=tool_calls,
        usage={
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        },
        model=model,
        provider=provider_name,
    )


# =============================================================================
# BASE PROVIDER
# =============================================================================

class LLMProvider:
    """Interface base para todos os providers."""
    
    name: str = "base"
    
    async def chat(
        self,
        messages: List[dict],
        tools: Optional[List[dict]] = None,
        temperature: float = AGENT_TEMPERATURE,
        max_tokens: int = AGENT_MAX_TOKENS,
        response_format: Optional[dict] = None,
        **kwargs,
    ) -> LLMResponse:
        raise NotImplementedError
    
    async def chat_stream(
        self,
        messages: List[dict],
        tools: Optional[List[dict]] = None,
        temperature: float = AGENT_TEMPERATURE,
        max_tokens: int = AGENT_MAX_TOKENS,
        response_format: Optional[dict] = None,
        **kwargs,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Streaming — yield StreamEvents. Default: fallback to non-streaming."""
        response = await self.chat(
            messages,
            tools,
            temperature,
            max_tokens,
            response_format=response_format,
            **kwargs,
        )
        if response.content:
            yield StreamEvent(type="token", text=response.content)
        yield StreamEvent(type="done", data=response.model_dump())
    
    async def embed(self, text: str) -> List[float]:
        """Embeddings — default não implementado."""
        raise NotImplementedError

    async def close(self) -> None:
        """Optional provider cleanup."""
        return None


# =============================================================================
# AZURE OPENAI PROVIDER
# =============================================================================

class AzureOpenAIProvider(LLMProvider):
    """Azure OpenAI — GPT-4.1, GPT-4.1-mini, etc."""
    
    name = "azure_openai"
    
    def __init__(self, deployment: str = None):
        self.deployment = deployment or CHAT_DEPLOYMENT
        self.endpoint = AZURE_OPENAI_ENDPOINT
        self.base_url = _normalize_base_url(AZURE_OPENAI_BASE_URL or AZURE_OPENAI_ENDPOINT)
        self.api_prefix = _normalize_url_path(AZURE_OPENAI_API_PREFIX, "/openai")
        self.auth_mode = AZURE_OPENAI_AUTH_MODE
        self.auth_header = AZURE_OPENAI_AUTH_HEADER
        self.api_key = AZURE_OPENAI_AUTH_VALUE or AZURE_OPENAI_KEY
        self._http_client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()

    def _auth_headers(self) -> dict[str, str]:
        return _build_auth_headers(
            self.auth_mode,
            self.auth_header,
            self.api_key,
            default_header="api-key",
        )

    async def _request_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        headers.update(
            await build_azure_openai_auth_headers(
                auth_mode=self.auth_mode,
                auth_header=self.auth_header,
                auth_value=self.api_key,
            )
        )
        return headers

    def _chat_url(self) -> str:
        return (
            f"{_join_url(self.base_url, self.api_prefix, 'deployments', self.deployment, 'chat', 'completions')}"
            f"?api-version={API_VERSION_CHAT}"
        )

    def _embedding_url(self) -> str:
        return (
            f"{_join_url(self.base_url, self.api_prefix, 'deployments', EMBEDDING_DEPLOYMENT, 'embeddings')}"
            f"?api-version={API_VERSION_OPENAI}"
        )

    async def _get_client(self) -> httpx.AsyncClient:
        async with self._client_lock:
            if self._http_client is None or self._http_client.is_closed:
                self._http_client = httpx.AsyncClient(
                    timeout=180,
                    limits=httpx.Limits(max_connections=50, max_keepalive_connections=10),
                )
            return self._http_client

    async def close(self) -> None:
        async with self._client_lock:
            if self._http_client and not self._http_client.is_closed:
                await self._http_client.aclose()
            self._http_client = None
        await close_azure_auth()

    async def chat(
        self,
        messages,
        tools=None,
        temperature=AGENT_TEMPERATURE,
        max_tokens=AGENT_MAX_TOKENS,
        response_format: Optional[dict] = None,
        **kwargs,
    ) -> LLMResponse:
        url = self._chat_url()
        messages = _sanitize_openai_tool_messages(messages)
        body = {"messages": messages}
        if _is_gpt5_family(self.deployment):
            # GPT-5 family rejects max_tokens and non-default temperature values.
            body["max_completion_tokens"] = max_tokens
        else:
            body["temperature"] = temperature
            body["max_tokens"] = max_tokens
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        elif response_format:
            # Structured outputs: compatível quando não há tool calling na mesma chamada.
            body["response_format"] = response_format
        
        max_retries = 5
        client = await _maybe_await(self._get_client())
        for attempt in range(max_retries):
            try:
                resp = await client.post(
                    url, json=body,
                    headers=await self._request_headers(),
                )
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", 5 * (attempt + 1)))
                    wait = min(retry_after, 30)
                    _log(f"Azure OpenAI 429, attempt {attempt+1}/{max_retries}, wait {wait}s")
                    await asyncio.sleep(wait)
                    continue
                if resp.status_code >= 500:
                    wait = 3 * (attempt + 1)
                    _log(f"Azure OpenAI {resp.status_code}, attempt {attempt+1}/{max_retries}, wait {wait}s")
                    await asyncio.sleep(wait)
                    continue
                resp.raise_for_status()
                return _openai_response_to_normalized(resp.json(), self.name)
            except httpx.TimeoutException:
                _log(f"Azure OpenAI timeout, attempt {attempt+1}/{max_retries}")
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(3 * (attempt + 1))
            except httpx.HTTPStatusError as e:
                _log(
                    f"Azure OpenAI HTTP {e.response.status_code}: "
                    f"{_sanitize_error_response(e.response.text, 200)}"
                )
                raise

        raise RuntimeError("Azure OpenAI: max retries exceeded")
    
    async def chat_stream(
        self,
        messages,
        tools=None,
        temperature=AGENT_TEMPERATURE,
        max_tokens=AGENT_MAX_TOKENS,
        response_format: Optional[dict] = None,
        **kwargs,
    ) -> AsyncGenerator[StreamEvent, None]:
        url = self._chat_url()
        messages = _sanitize_openai_tool_messages(messages)
        body = {"messages": messages, "stream": True}
        if _is_gpt5_family(self.deployment):
            body["max_completion_tokens"] = max_tokens
        else:
            body["temperature"] = temperature
            body["max_tokens"] = max_tokens
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        
        # Streaming com tools é complexo no OpenAI — se há tools, fallback para non-stream
        # (tool calls vêm em chunks parciais que precisam de ser reassemblados)
        if tools:
            response = await self.chat(
                messages,
                tools,
                temperature,
                max_tokens,
                response_format=response_format,
            )
            if response.tool_calls:
                yield StreamEvent(type="done", data=response.model_dump())
                return
            if response.content:
                yield StreamEvent(type="token", text=response.content)
            yield StreamEvent(type="done", data=response.model_dump())
            return
        
        if response_format:
            body["response_format"] = response_format

        # Streaming puro (sem tools) — stream token a token
        client = await _maybe_await(self._get_client())
        async with client.stream(
            "POST", url, json=body,
            headers=await self._request_headers(),
        ) as resp:
            full_content = ""
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    text = delta.get("content", "")
                    if text:
                        full_content += text
                        yield StreamEvent(type="token", text=text)
                except json.JSONDecodeError:
                    continue

            yield StreamEvent(type="done", data={
                "content": full_content,
                "model": self.deployment,
                "provider": self.name,
            })
    
    async def embed(self, text: str) -> List[float]:
        url = self._embedding_url()
        client = await _maybe_await(self._get_client())
        resp = await client.post(
            url, json={"input": text},
            headers=await self._request_headers(),
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]


# =============================================================================
# ANTHROPIC PROVIDER
# =============================================================================

class AnthropicProvider(LLMProvider):
    """Anthropic Claude — Opus 4.6, Sonnet 4.5, Haiku 4.5."""
    
    name = "anthropic"
    API_VERSION = "2023-06-01"
    
    def __init__(self, model: str = None):
        self.model = model or ANTHROPIC_MODEL_SONNET
        self.api_key = ANTHROPIC_AUTH_VALUE or ANTHROPIC_API_KEY
        self.api_url = ANTHROPIC_API_BASE  # Mantido para compatibilidade/debug.
        self.base_url = _normalize_base_url(ANTHROPIC_BASE_URL or ANTHROPIC_API_BASE)
        self.messages_path = _normalize_url_path(ANTHROPIC_MESSAGES_PATH, "/v1/messages")
        self.auth_mode = ANTHROPIC_AUTH_MODE
        self.auth_header = ANTHROPIC_AUTH_HEADER
        self._http_client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()

    async def _get_client(self) -> httpx.AsyncClient:
        async with self._client_lock:
            if self._http_client is None or self._http_client.is_closed:
                self._http_client = httpx.AsyncClient(
                    timeout=180,
                    limits=httpx.Limits(max_connections=50, max_keepalive_connections=10),
                )
            return self._http_client

    async def close(self) -> None:
        async with self._client_lock:
            if self._http_client and not self._http_client.is_closed:
                await self._http_client.aclose()
            self._http_client = None
    
    def _headers(self) -> dict:
        headers = {
            # Azure AI Foundry Claude accepts Anthropic-compatible headers with the resource key.
            "anthropic-version": self.API_VERSION,
            "content-type": "application/json",
        }
        headers.update(
            _build_auth_headers(
                self.auth_mode,
                self.auth_header,
                self.api_key,
                default_header="x-api-key",
            )
        )
        return headers

    def _messages_url(self) -> str:
        return _join_url(self.base_url, self.messages_path)
    
    async def chat(
        self,
        messages,
        tools=None,
        temperature=AGENT_TEMPERATURE,
        max_tokens=AGENT_MAX_TOKENS,
        response_format: Optional[dict] = None,
        **kwargs,
    ) -> LLMResponse:
        # Traduzir mensagens e tools para formato Anthropic
        system_prompt, anthropic_msgs = _openai_messages_to_anthropic(messages)
        
        body: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": anthropic_msgs,
        }
        if system_prompt:
            body["system"] = system_prompt
        if tools:
            body["tools"] = _openai_tools_to_anthropic(tools)
            body["tool_choice"] = {"type": "auto"}
        
        max_retries = 5
        client = await _maybe_await(self._get_client())
        for attempt in range(max_retries):
            try:
                resp = await client.post(
                    self._messages_url(), json=body, headers=self._headers(),
                )
                if resp.status_code == 429:
                    _log(
                        "Anthropic 429, fast-failing to provider fallback instead of waiting locally"
                    )
                    resp.raise_for_status()
                if resp.status_code >= 500:
                    wait = 3 * (attempt + 1)
                    _log(f"Anthropic {resp.status_code}, attempt {attempt+1}/{max_retries}, wait {wait}s")
                    await asyncio.sleep(wait)
                    continue
                resp.raise_for_status()
                return _anthropic_response_to_normalized(resp.json(), self.model)
            except httpx.TimeoutException:
                _log(f"Anthropic timeout, attempt {attempt+1}/{max_retries}")
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(3 * (attempt + 1))
            except httpx.HTTPStatusError as e:
                _log(
                    f"Anthropic HTTP {e.response.status_code}: "
                    f"{_sanitize_error_response(e.response.text, 300)}"
                )
                raise

        raise RuntimeError("Anthropic: max retries exceeded")
    
    async def chat_stream(
        self,
        messages,
        tools=None,
        temperature=AGENT_TEMPERATURE,
        max_tokens=AGENT_MAX_TOKENS,
        response_format: Optional[dict] = None,
        **kwargs,
    ) -> AsyncGenerator[StreamEvent, None]:
        system_prompt, anthropic_msgs = _openai_messages_to_anthropic(messages)
        
        body: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": anthropic_msgs,
            "stream": True,
        }
        if system_prompt:
            body["system"] = system_prompt
        if tools:
            body["tools"] = _openai_tools_to_anthropic(tools)
            body["tool_choice"] = {"type": "auto"}
        
        # Com tools e streaming no Anthropic, tool_use events vêm inline
        # Precisamos de reconstruir os tool calls a partir dos deltas
        
        client = await _maybe_await(self._get_client())
        async with client.stream(
            "POST", self._messages_url(), json=body, headers=self._headers(),
        ) as resp:
            if resp.status_code != 200:
                body_bytes = await resp.aread()
                body_preview = body_bytes[:500].decode("utf-8", errors="replace")
                logger.warning(
                    "Anthropic streaming failed (status=%d), falling back to non-streaming. Body: %s",
                    resp.status_code,
                    _sanitize_error_response(body_preview, 300),
                )
                # Fallback to non-streaming
                body.pop("stream")
                response = await self.chat(messages, tools, temperature, max_tokens)
                if response.content:
                    yield StreamEvent(type="token", text=response.content)
                yield StreamEvent(type="done", data=response.model_dump())
                return

            full_content = ""
            current_tool_calls: List[dict] = []
            current_tool: Optional[dict] = None
            current_tool_json = ""
            usage_data = {}

            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:].strip()
                if not data_str or data_str == "[DONE]":
                    continue

                try:
                    event = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                event_type = event.get("type", "")

                if event_type == "content_block_start":
                    block = event.get("content_block", {})
                    if block.get("type") == "tool_use":
                        current_tool = {
                            "id": block.get("id", ""),
                            "name": block.get("name", ""),
                        }
                        current_tool_json = ""

                elif event_type == "content_block_delta":
                    delta = event.get("delta", {})
                    delta_type = delta.get("type", "")

                    if delta_type == "text_delta":
                        text = delta.get("text", "")
                        if text:
                            full_content += text
                            yield StreamEvent(type="token", text=text)

                    elif delta_type == "input_json_delta":
                        current_tool_json += delta.get("partial_json", "")

                elif event_type == "content_block_stop":
                    if current_tool:
                        try:
                            args = json.loads(current_tool_json) if current_tool_json else {}
                        except json.JSONDecodeError:
                            args = {}
                        current_tool_calls.append(LLMToolCall(
                            id=current_tool["id"],
                            name=current_tool["name"],
                            arguments=args,
                        ))
                        current_tool = None
                        current_tool_json = ""

                elif event_type == "message_delta":
                    usage_data = event.get("usage", {})

                elif event_type == "message_start":
                    msg_usage = event.get("message", {}).get("usage", {})
                    if msg_usage:
                        usage_data = msg_usage

            # Fim do stream
            yield StreamEvent(type="done", data=LLMResponse(
                content=full_content if full_content else None,
                tool_calls=current_tool_calls if current_tool_calls else None,
                usage={
                    "prompt_tokens": usage_data.get("input_tokens", 0),
                    "completion_tokens": usage_data.get("output_tokens", 0),
                    "total_tokens": usage_data.get("input_tokens", 0) + usage_data.get("output_tokens", 0),
                },
                model=self.model,
                provider="anthropic",
            ).model_dump())


# =============================================================================
# NORMALIZED TOOL RESULT → OPENAI FORMAT (para o conversation history)
# =============================================================================

def _sanitize_openai_tool_messages(messages: list[dict]) -> list[dict]:
    """Remove orphaned tool messages that lack a preceding assistant with matching tool_calls.

    OpenAI / Azure OpenAI require every role='tool' message to have a preceding
    assistant message whose tool_calls contain the referenced tool_call_id.
    """
    sanitized: list[dict] = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        role = msg.get("role", "")

        if role == "assistant" and msg.get("tool_calls"):
            expected_ids = [
                str(tc.get("id", "") or "").strip()
                for tc in msg.get("tool_calls", [])
                if str(tc.get("id", "") or "").strip()
            ]
            j = i + 1
            matching_tools: list[dict] = []
            seen_ids: set[str] = set()
            while j < len(messages) and messages[j].get("role") == "tool":
                tool_msg = messages[j]
                tc_id = str(tool_msg.get("tool_call_id", "") or "").strip()
                if tc_id and tc_id in expected_ids and tc_id not in seen_ids:
                    matching_tools.append(tool_msg)
                    seen_ids.add(tc_id)
                else:
                    logger.warning("[LLM] Dropped orphaned/extra tool message (tool_call_id=%s)", tc_id)
                j += 1

            missing_ids = [tc_id for tc_id in expected_ids if tc_id not in seen_ids]
            if expected_ids and missing_ids:
                logger.warning(
                    "[LLM] Dropped assistant tool_call message with missing tool results (missing=%s)",
                    ",".join(missing_ids),
                )
            else:
                sanitized.append(msg)
                sanitized.extend(matching_tools)
            i = j
            continue

        if role == "tool":
            tc_id = str(msg.get("tool_call_id", "") or "").strip()
            logger.warning("[LLM] Dropped orphaned tool message (tool_call_id=%s)", tc_id)
        else:
            sanitized.append(msg)
        i += 1
    return sanitized


def make_tool_result_message(tool_call: LLMToolCall, result_str: str) -> dict:
    """Cria mensagem de tool result em formato OpenAI (canónico para storage)."""
    return {
        "role": "tool",
        "tool_call_id": tool_call.id,
        "content": result_str,
    }


def make_assistant_message_from_response(response: LLMResponse) -> dict:
    """Converte LLMResponse → mensagem assistant em formato OpenAI (para storage)."""
    msg: Dict[str, Any] = {"role": "assistant"}
    if response.content:
        msg["content"] = response.content
    if response.tool_calls:
        msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                },
            }
            for tc in response.tool_calls
        ]
        if not response.content:
            msg["content"] = None
    return msg


# =============================================================================
# PROVIDER FACTORY
# =============================================================================

_PROVIDER_CACHE: Dict[str, LLMProvider] = {}

def _parse_provider_spec(spec: str) -> tuple[str, str]:
    """Parse 'provider:model' → (provider_name, model_name)."""
    if ":" in spec:
        parts = spec.split(":", 1)
        return parts[0], parts[1]
    return spec, ""


def get_provider(tier: str = None) -> LLMProvider:
    """Retorna o provider para o tier pedido.

    Tiers: "fast", "standard", "pro", "vision"
    Se tier=None, usa LLM_DEFAULT_TIER.
    """
    tier = (tier or LLM_DEFAULT_TIER or "standard").strip().lower()

    tier_map = {
        "fast": LLM_TIER_FAST,
        "standard": LLM_TIER_STANDARD,
        "pro": LLM_TIER_PRO,
        "vision": LLM_TIER_VISION,
    }

    spec = tier_map.get(tier, LLM_TIER_STANDARD)
    provider_name, model = _parse_provider_spec(spec)

    return _get_cached_provider(provider_name, model)


def get_fallback_provider() -> LLMProvider:
    """Retorna o provider de fallback."""
    provider_name, model = _parse_provider_spec(LLM_FALLBACK)
    return _get_cached_provider(provider_name, model)


def get_embedding_provider() -> AzureOpenAIProvider:
    """Embeddings — sempre Azure OpenAI (temos os índices lá)."""
    provider = _get_cached_provider("azure_openai", "")
    return provider if isinstance(provider, AzureOpenAIProvider) else AzureOpenAIProvider()


def _create_provider(provider_name: str, model: str) -> LLMProvider:
    """Factory interna."""
    if provider_name == "azure_openai":
        return AzureOpenAIProvider(deployment=model if model else None)
    
    if provider_name == "anthropic":
        # Resolver aliases amigáveis
        model_map = {
            "opus": ANTHROPIC_MODEL_OPUS,
            "sonnet": ANTHROPIC_MODEL_SONNET,
            "haiku": ANTHROPIC_MODEL_HAIKU,
        }
        resolved = model_map.get(model, model) if model else ANTHROPIC_MODEL_SONNET
        return AnthropicProvider(model=resolved)
    
    _log(f"Provider desconhecido: {provider_name}, fallback para Azure OpenAI")
    return AzureOpenAIProvider()


def _get_cached_provider(provider_name: str, model: str) -> LLMProvider:
    cache_key = f"{provider_name}:{model or ''}"
    provider = _PROVIDER_CACHE.get(cache_key)
    if provider is None:
        provider = _create_provider(provider_name, model)
        _PROVIDER_CACHE[cache_key] = provider
    return provider


def get_provider_for_spec(spec: str) -> LLMProvider:
    """Retorna o provider correspondente a um spec explicito provider:model."""
    provider_name, model = _parse_provider_spec(spec or "")
    return _get_cached_provider(provider_name, model)


async def close_all_providers() -> None:
    for provider in _PROVIDER_CACHE.values():
        try:
            await provider.close()
        except Exception as e:
            logger.warning("Failed to close provider %s: %s", getattr(provider, "name", "unknown"), e)


# =============================================================================
# UTILITY: Chat simples sem tools (para análise interna, classificação, etc.)
# =============================================================================

async def llm_simple(
    prompt: str,
    tier: str = "fast",
    max_tokens: int = 2000,
    response_format: Optional[dict] = None,
) -> str:
    """Chamada simples ao LLM sem tools. Usa tier 'fast' por default."""
    response = await llm_with_fallback(
        messages=[{"role": "user", "content": prompt}],
        tier=tier,
        max_tokens=max_tokens,
        response_format=response_format,
    )
    return response.content or ""


def _provider_supports_response_format(provider: Optional[LLMProvider]) -> bool:
    return provider is not None and not isinstance(provider, AnthropicProvider)


def _same_provider_config(left: Optional[LLMProvider], right: Optional[LLMProvider]) -> bool:
    if left is None or right is None:
        return False
    return (
        left.name == right.name
        and getattr(left, "deployment", "") == getattr(right, "deployment", "")
        and getattr(left, "model", "") == getattr(right, "model", "")
    )


async def llm_with_fallback(
    messages: List[dict],
    tools: Optional[List[dict]] = None,
    tier: str = None,
    temperature: float = AGENT_TEMPERATURE,
    max_tokens: int = AGENT_MAX_TOKENS,
    response_format: Optional[dict] = None,
) -> LLMResponse:
    """Chat com fallback automático e cadeia explícita de tentativas."""
    fallback_chain: List[Dict[str, Any]] = []
    primary = get_provider(tier)
    fallback = get_fallback_provider()
    pii_context: Optional[PIIMaskingContext] = None
    actual_messages = messages

    # PII Shield: mascarar dados do utilizador antes de enviar ao LLM.
    if PII_ENABLED:
        pii_context = PIIMaskingContext()
        actual_messages = await mask_messages(messages, pii_context)

    # Prompt Shield: deteta prompt injection/jailbreak antes da chamada ao provider.
    if PROMPT_SHIELD_ENABLED:
        shield_result = await check_messages(actual_messages)
        if shield_result.is_blocked:
            blocked_text = (
                "Pedido bloqueado por seguranca: "
                + (shield_result.details or "Tentativa de manipulacao detectada.")
            )
            return LLMResponse(
                content=blocked_text,
                model="prompt_shield",
                provider="prompt_shield",
                is_error=True,
                error_kind="prompt_shield_blocked",
                error_detail=blocked_text,
                fallback_chain=[
                    {
                        "provider": "prompt_shield",
                        "status": "blocked",
                        "attack_type": shield_result.attack_type,
                    }
                ],
            )

    def _unmask_response_if_needed(response: LLMResponse) -> LLMResponse:
        if not pii_context or not pii_context.mappings:
            return response
        if response.content:
            response.content = pii_context.unmask(response.content)
        if response.tool_calls:
            for tc in response.tool_calls:
                if isinstance(tc.arguments, dict):
                    tc.arguments = pii_context.unmask_any(tc.arguments)
        return response

    provider_attempts: List[LLMProvider] = []
    if response_format and not _provider_supports_response_format(primary):
        fallback_chain.append(
            {
                "provider": primary.name,
                "status": "skipped",
                "reason": "response_format_unsupported",
            }
        )
        if _provider_supports_response_format(fallback):
            provider_attempts.append(fallback)
        azure_structured = _get_cached_provider("azure_openai", "")
        if all(
            p.name != azure_structured.name or getattr(p, "deployment", "") != getattr(azure_structured, "deployment", "")
            for p in provider_attempts
        ):
            provider_attempts.append(azure_structured)
    else:
        provider_attempts.append(primary)

    for candidate in (fallback,):
        if all(
            candidate.name != existing.name
            or getattr(candidate, "deployment", "") != getattr(existing, "deployment", "")
            or getattr(candidate, "model", "") != getattr(existing, "model", "")
            for existing in provider_attempts
        ):
            provider_attempts.append(candidate)

    # Governance gate: em modo restrictive, filtrar providers externos.
    if PROVIDER_GOVERNANCE_MODE == "restrictive":
        filtered = []
        for p in provider_attempts:
            family = str(getattr(p, "name", "") or "").strip().lower()
            if family in PROVIDER_EXTERNAL_MODEL_FAMILIES:
                fallback_chain.append({
                    "provider": p.name,
                    "status": "blocked",
                    "reason": "provider_governance_restrictive",
                })
                _log(f"Governance gate: provider '{p.name}' bloqueado (mode=restrictive)")
            else:
                filtered.append(p)
        provider_attempts = filtered
        if not provider_attempts:
            blocked_text = "Pedido bloqueado: nenhum provider interno disponível neste modo de governação."
            return _make_error_response(
                content=blocked_text,
                model="governance_gate",
                provider="governance_gate",
                error_kind="provider_governance_blocked",
                error_detail=blocked_text,
                fallback_chain=fallback_chain,
            )

    for provider in provider_attempts:
        try:
            result = await provider.chat(
                actual_messages,
                tools,
                temperature,
                max_tokens,
                response_format=response_format,
            )
            fallback_chain.append({"provider": provider.name, "status": "ok"})
            result = _unmask_response_if_needed(result)
            result.fallback_chain = fallback_chain
            return result
        except Exception as e:
            info = _classify_provider_exception(e)
            fallback_chain.append(
                {
                    "provider": provider.name,
                    "status": "failed",
                    "error": str(info.get("detail") or "")[:240],
                    "detail": str(info.get("detail") or "")[:240],
                    "error_kind": info.get("kind"),
                    "status_code": info.get("status_code"),
                }
            )
            _log(f"Provider ({provider.name}) failed: {e}")

    _log(f"ALL providers failed. Chain: {fallback_chain}")
    failure_text = _format_failure_chain_for_user(fallback_chain)
    return _make_error_response(
        content=failure_text,
        model="",
        provider="",
        error_kind="all_providers_failed",
        error_detail=failure_text,
        fallback_chain=fallback_chain,
    )


async def llm_stream_with_fallback(
    messages: List[dict],
    tools: Optional[List[dict]] = None,
    tier: str = None,
    temperature: float = AGENT_TEMPERATURE,
    max_tokens: int = AGENT_MAX_TOKENS,
    response_format: Optional[dict] = None,
) -> AsyncGenerator[StreamEvent, None]:
    """Streaming com fallback automático. Yield StreamEvents."""
    pii_context: Optional[PIIMaskingContext] = None
    actual_messages = messages

    if PII_ENABLED:
        pii_context = PIIMaskingContext()
        actual_messages = await mask_messages(messages, pii_context)

    if PROMPT_SHIELD_ENABLED:
        shield_result = await check_messages(actual_messages)
        if shield_result.is_blocked:
            blocked_text = (
                "Pedido bloqueado por seguranca: "
                + (shield_result.details or "Tentativa de manipulacao detectada.")
            )
            yield StreamEvent(type="token", text=blocked_text)
            yield StreamEvent(
                type="done",
                data=_make_error_done_payload(
                    content=blocked_text,
                    model="prompt_shield",
                    provider="prompt_shield",
                    error_kind="prompt_shield_blocked",
                    error_detail=blocked_text,
                    fallback_chain=[
                        {
                            "provider": "prompt_shield",
                            "status": "blocked",
                            "attack_type": shield_result.attack_type,
                        }
                    ],
                ),
            )
            return

    suppress_token_stream = bool(pii_context and pii_context.mappings)
    masked_chunks: List[str] = []

    def _finalize_done_event(done_data: Any) -> tuple[Optional[str], Any]:
        if not pii_context:
            return None, done_data
        base_data = done_data if isinstance(done_data, dict) else {}
        if not masked_chunks:
            content_in_done = base_data.get("content", "")
            if isinstance(content_in_done, str) and content_in_done:
                masked_chunks.append(content_in_done)
        masked_text = "".join(masked_chunks).strip()
        unmasked_text = pii_context.unmask(masked_text) if masked_text else ""
        safe_data = pii_context.unmask_any(base_data) if base_data else {}
        if isinstance(safe_data, dict) and unmasked_text:
            safe_data["content"] = unmasked_text
        return (unmasked_text or None), (safe_data if safe_data else done_data)

    primary = get_provider(tier)
    fallback_prov = get_fallback_provider()
    fallback_chain: List[Dict[str, Any]] = []

    if response_format and not _provider_supports_response_format(primary):
        fallback_chain.append(
            {
                "provider": getattr(primary, "name", "primary"),
                "status": "skipped",
                "reason": "response_format_unsupported",
            }
        )
        preferred = fallback_prov if _provider_supports_response_format(fallback_prov) else None
        if preferred is None:
            preferred = _get_cached_provider("azure_openai", "")
        if _same_provider_config(preferred, primary):
            fallback_prov = None
        elif _same_provider_config(preferred, fallback_prov):
            fallback_prov = None
        primary = preferred if _provider_supports_response_format(preferred) else None
    if response_format and fallback_prov is not None and not _provider_supports_response_format(fallback_prov):
        fallback_chain.append(
            {
                "provider": getattr(fallback_prov, "name", "fallback"),
                "status": "skipped",
                "reason": "response_format_unsupported",
            }
        )
        azure_structured = _get_cached_provider("azure_openai", "")
        fallback_prov = None if _same_provider_config(azure_structured, primary) else azure_structured

    # Governance gate (streaming): mesma lógica do path não-streaming.
    def _is_external(provider) -> bool:
        family = str(getattr(provider, "name", "") or "").strip().lower()
        return family in PROVIDER_EXTERNAL_MODEL_FAMILIES

    if PROVIDER_GOVERNANCE_MODE == "restrictive":
        if _is_external(primary):
            fallback_chain.append({
                "provider": primary.name,
                "status": "blocked",
                "reason": "provider_governance_restrictive",
            })
            _log(f"Governance gate (stream): provider '{primary.name}' bloqueado (mode=restrictive)")
            primary = None
        if _is_external(fallback_prov):
            fallback_chain.append({
                "provider": fallback_prov.name,
                "status": "blocked",
                "reason": "provider_governance_restrictive",
            })
            _log(f"Governance gate (stream): fallback '{fallback_prov.name}' bloqueado (mode=restrictive)")
            fallback_prov = None
        if primary is None and fallback_prov is None:
            blocked_text = "Pedido bloqueado: nenhum provider interno disponível neste modo de governação."
            yield StreamEvent(type="token", text=blocked_text)
            yield StreamEvent(
                type="done",
                data=_make_error_done_payload(
                    content=blocked_text,
                    model="governance_gate",
                    provider="governance_gate",
                    error_kind="provider_governance_blocked",
                    error_detail=blocked_text,
                    fallback_chain=fallback_chain,
                ),
            )
            return

    if primary is not None:
        try:
            async for event in primary.chat_stream(
                actual_messages,
                tools,
                temperature,
                max_tokens,
                response_format=response_format,
            ):
                if suppress_token_stream and event.type == "token":
                    if event.text:
                        masked_chunks.append(event.text)
                    continue
                if suppress_token_stream and event.type == "done":
                    final_text, final_data = _finalize_done_event(event.data)
                    if final_text:
                        yield StreamEvent(type="token", text=final_text)
                    yield StreamEvent(type="done", data=final_data)
                    return
                yield event
            return
        except Exception as e:
            info = _classify_provider_exception(e)
            fallback_chain.append(
                {
                    "provider": primary.name,
                    "status": "failed",
                    "detail": str(info.get("detail") or "")[:240],
                    "error_kind": info.get("kind"),
                    "status_code": info.get("status_code"),
                }
            )
            _log(f"Primary streaming ({primary.name}) failed: {e}, trying fallback")

    if fallback_prov is not None:
        try:
            if suppress_token_stream:
                masked_chunks.clear()
            async for event in fallback_prov.chat_stream(
                actual_messages,
                tools,
                temperature,
                max_tokens,
                response_format=response_format,
            ):
                if suppress_token_stream and event.type == "token":
                    if event.text:
                        masked_chunks.append(event.text)
                    continue
                if suppress_token_stream and event.type == "done":
                    final_text, final_data = _finalize_done_event(event.data)
                    if final_text:
                        yield StreamEvent(type="token", text=final_text)
                    # Inject fallback_chain so downstream can detect fallback
                    if fallback_chain and isinstance(final_data, dict):
                        final_data["fallback_chain"] = fallback_chain
                    yield StreamEvent(type="done", data=final_data)
                    return
                # Inject fallback_chain into done events from fallback provider
                if event.type == "done" and fallback_chain:
                    data = event.data if isinstance(event.data, dict) else {}
                    data["fallback_chain"] = fallback_chain
                    yield StreamEvent(type="done", data=data)
                    continue
                yield event
            return
        except Exception as e2:
            info = _classify_provider_exception(e2)
            fallback_chain.append(
                {
                    "provider": getattr(fallback_prov, "name", "fallback"),
                    "status": "failed",
                    "detail": str(info.get("detail") or "")[:240],
                    "error_kind": info.get("kind"),
                    "status_code": info.get("status_code"),
                }
            )
            _log(f"Fallback streaming failed: {e2}")

    failure_text = _format_failure_chain_for_user(fallback_chain)
    yield StreamEvent(
        type="token",
        text=failure_text,
    )
    yield StreamEvent(
        type="done",
        data=_make_error_done_payload(
            content="",
            model="",
            provider="",
            error_kind="all_providers_failed",
            error_detail=failure_text,
            fallback_chain=fallback_chain,
        ),
    )
