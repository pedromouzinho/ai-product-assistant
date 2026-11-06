"""Request-scoped correlation context for logs and provider tracing."""

from __future__ import annotations

from contextvars import ContextVar, Token


_request_id_ctx: ContextVar[str] = ContextVar("app_request_id", default="")
_conversation_id_ctx: ContextVar[str] = ContextVar("app_conversation_id", default="")


def set_request_context(request_id: str = "", conversation_id: str = "") -> tuple[Token, Token]:
    return (
        _request_id_ctx.set((request_id or "").strip()),
        _conversation_id_ctx.set((conversation_id or "").strip()),
    )


def reset_request_context(tokens: tuple[Token, Token]) -> None:
    request_token, conversation_token = tokens
    _request_id_ctx.reset(request_token)
    _conversation_id_ctx.reset(conversation_token)


def get_request_id() -> str:
    return _request_id_ctx.get()


def get_conversation_id() -> str:
    return _conversation_id_ctx.get()


def format_request_context() -> str:
    parts = []
    request_id = get_request_id()
    conversation_id = get_conversation_id()
    if request_id:
        parts.append(f"req={request_id}")
    if conversation_id:
        parts.append(f"conv={conversation_id}")
    return " ".join(parts)
