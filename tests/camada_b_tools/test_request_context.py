from __future__ import annotations

from llm_provider import _log, get_debug_log
from request_context import (
    format_request_context,
    get_conversation_id,
    get_request_id,
    reset_request_context,
    set_request_context,
)


def test_request_context_roundtrip():
    tokens = set_request_context("req-123", "conv-456")
    try:
        assert get_request_id() == "req-123"
        assert get_conversation_id() == "conv-456"
        assert format_request_context() == "req=req-123 conv=conv-456"
    finally:
        reset_request_context(tokens)

    assert get_request_id() == ""
    assert get_conversation_id() == ""
    assert format_request_context() == ""


def test_llm_debug_log_includes_request_context():
    tokens = set_request_context("req-ctx", "conv-ctx")
    try:
        _log("mensagem de teste")
    finally:
        reset_request_context(tokens)

    entry = get_debug_log()[-1]
    assert entry["msg"] == "mensagem de teste"
    assert entry["request_id"] == "req-ctx"
    assert entry["conversation_id"] == "conv-ctx"
