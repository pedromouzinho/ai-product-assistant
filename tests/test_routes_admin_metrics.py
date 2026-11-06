from routes_admin import (
    _build_client_error_payload,
    _build_llm_debug_payload,
    _build_recent_metrics_payload,
    _build_request_trace_payload,
    _filter_client_error_entries,
    _filter_llm_debug_entries,
    _filter_recent_metrics_items,
)


def test_build_recent_metrics_payload_summarizes_status_and_error_kind():
    payload = _build_recent_metrics_payload(
        [
            {"status": "ok", "error_kind": ""},
            {"status": "blocked", "error_kind": "pre_model_blocked"},
            {"status": "error", "error_kind": "all_providers_failed"},
            {"status": "error", "error_kind": "all_providers_failed"},
            {"status": "tool_error", "error_kind": "validation_failed"},
        ]
    )

    assert payload["summary"]["total"] == 5
    assert payload["summary"]["status_counts"] == {
        "blocked": 1,
        "error": 2,
        "ok": 1,
        "tool_error": 1,
    }
    assert payload["summary"]["error_kind_counts"] == {
        "all_providers_failed": 2,
        "pre_model_blocked": 1,
        "validation_failed": 1,
    }


def test_build_recent_metrics_payload_ignores_non_dict_and_blank_values():
    payload = _build_recent_metrics_payload(
        [
            None,
            "bad",
            {"status": "", "error_kind": ""},
            {"status": " OK ", "error_kind": " Timeout "},
        ]
    )

    assert payload["summary"]["total"] == 4
    assert payload["summary"]["status_counts"] == {"ok": 1}
    assert payload["summary"]["error_kind_counts"] == {"timeout": 1}


def test_filter_recent_metrics_items_filters_by_correlation_and_error_fields():
    items = [
        {"request_id": "r1", "conversation_id": "c1", "status": "ok", "error_kind": ""},
        {"request_id": "r2", "conversation_id": "c1", "status": "blocked", "error_kind": "pre_model_blocked"},
        {"request_id": "r3", "conversation_id": "c2", "status": "error", "error_kind": "all_providers_failed"},
    ]

    filtered = _filter_recent_metrics_items(
        items,
        conversation_id="c1",
        status="blocked",
        error_kind="pre_model_blocked",
    )

    assert filtered == [
        {"request_id": "r2", "conversation_id": "c1", "status": "blocked", "error_kind": "pre_model_blocked"}
    ]


def test_filter_recent_metrics_items_applies_tail_limit():
    items = [
        {"request_id": "r1", "status": "ok"},
        {"request_id": "r2", "status": "ok"},
        {"request_id": "r3", "status": "ok"},
    ]

    filtered = _filter_recent_metrics_items(items, limit=2)

    assert [item["request_id"] for item in filtered] == ["r2", "r3"]


def test_filter_llm_debug_entries_filters_by_request_context_and_query():
    entries = [
        {"msg": "primary failed", "request_id": "r1", "conversation_id": "c1"},
        {"msg": "fallback ok", "request_id": "r2", "conversation_id": "c1"},
        {"msg": "primary failed again", "request_id": "r3", "conversation_id": "c2"},
    ]

    filtered = _filter_llm_debug_entries(
        entries,
        conversation_id="c1",
        query="fallback",
    )

    assert filtered == [
        {"msg": "fallback ok", "request_id": "r2", "conversation_id": "c1"}
    ]


def test_build_llm_debug_payload_counts_unique_request_and_conversation_ids():
    payload = _build_llm_debug_payload(
        [
            {"msg": "a", "request_id": "r1", "conversation_id": "c1"},
            {"msg": "b", "request_id": "r2", "conversation_id": "c1"},
            {"msg": "c", "request_id": "", "conversation_id": ""},
        ]
    )

    assert payload["summary"] == {
        "total": 3,
        "request_id_count": 2,
        "conversation_id_count": 1,
    }


def test_filter_client_error_entries_filters_by_context_and_query():
    entries = [
        {"request_id": "r1", "conversation_id": "c1", "error_type": "uncaught_error", "message": "chart crash", "component": "ChartBlock"},
        {"request_id": "r2", "conversation_id": "c1", "error_type": "react_error_boundary", "message": "message bubble", "component": "MessageBubble"},
        {"request_id": "r3", "conversation_id": "c2", "error_type": "unhandled_rejection", "message": "upload timeout", "component": "UploadPanel"},
    ]

    filtered = _filter_client_error_entries(
        entries,
        conversation_id="c1",
        error_type="react_error_boundary",
        query="bubble",
    )

    assert filtered == [
        {"request_id": "r2", "conversation_id": "c1", "error_type": "react_error_boundary", "message": "message bubble", "component": "MessageBubble"}
    ]


def test_build_client_error_payload_summarizes_error_types_and_ids():
    payload = _build_client_error_payload(
        [
            {"request_id": "r1", "conversation_id": "c1", "error_type": "uncaught_error"},
            {"request_id": "r2", "conversation_id": "c1", "error_type": "react_error_boundary"},
            {"request_id": "", "conversation_id": "", "error_type": "react_error_boundary"},
        ]
    )

    assert payload["summary"] == {
        "total": 3,
        "request_id_count": 2,
        "conversation_id_count": 1,
        "error_type_counts": {
            "react_error_boundary": 2,
            "uncaught_error": 1,
        },
    }


def test_build_request_trace_payload_aggregates_correlated_sections():
    payload = _build_request_trace_payload(
        request_id="r1",
        agent_items=[
            {"ts": "2026-03-26T10:00:02+00:00", "request_id": "r1", "conversation_id": "c1", "status": "ok", "error_kind": ""},
            {"ts": "2026-03-26T10:00:05+00:00", "request_id": "r2", "conversation_id": "c1", "status": "error", "error_kind": "timeout"},
        ],
        tool_items=[
            {"ts": "2026-03-26T10:00:01+00:00", "request_id": "r1", "conversation_id": "c1", "status": "blocked", "error_kind": "blocked", "tool": "query_workitems"},
            {"ts": "2026-03-26T10:00:04+00:00", "request_id": "r2", "conversation_id": "c1", "status": "ok", "error_kind": ""},
        ],
        llm_entries=[
            {"ts": "2026-03-26T10:00:00+00:00", "msg": "primary failed", "request_id": "r1", "conversation_id": "c1"},
            {"ts": "2026-03-26T10:00:03+00:00", "msg": "fallback ok", "request_id": "r2", "conversation_id": "c1"},
        ],
        client_error_items=[
            {"ts": "2026-03-26T10:00:01.500000+00:00", "request_id": "r1", "conversation_id": "c1", "error_type": "react_error_boundary", "component": "MessageBubble", "agent_mode": "general"},
            {"ts": "2026-03-26T10:00:05.500000+00:00", "request_id": "r2", "conversation_id": "c1", "error_type": "uncaught_error", "component": "ChartBlock", "agent_mode": "general"},
        ],
    )

    assert payload["filters"]["request_id"] == "r1"
    assert payload["summary"] == {
        "agent_events": 1,
        "client_error_events": 1,
        "final_error_kind": "",
        "final_fallback": False,
        "final_model_used": "",
        "final_status": "ok",
        "llm_events": 1,
        "latest_client_error_type": "react_error_boundary",
        "latest_tool": "query_workitems",
        "latest_tool_error_kind": "blocked",
        "latest_tool_status": "blocked",
        "source_counts": {"agent": 1, "client_error": 1, "llm": 1, "tool": 1},
        "timeline_events": 4,
        "tool_events": 1,
    }
    assert payload["agent"]["summary"]["status_counts"] == {"ok": 1}
    assert payload["tools"]["summary"]["error_kind_counts"] == {"blocked": 1}
    assert payload["llm"]["summary"]["request_id_count"] == 1
    assert payload["client_errors"]["summary"]["error_type_counts"] == {"react_error_boundary": 1}
    assert payload["latest_by_source"] == {
        "agent": {
            "ts": "2026-03-26T10:00:02+00:00",
            "status": "ok",
            "error_kind": "",
            "model_used": "",
            "fallback": False,
        },
        "tool": {
            "ts": "2026-03-26T10:00:01+00:00",
            "tool": "query_workitems",
            "status": "blocked",
            "error_kind": "blocked",
        },
        "llm": {
            "ts": "2026-03-26T10:00:00+00:00",
            "msg": "primary failed",
        },
        "client_error": {
            "ts": "2026-03-26T10:00:01.500000+00:00",
            "error_type": "react_error_boundary",
            "component": "MessageBubble",
            "agent_mode": "general",
        },
    }
    assert [entry["source"] for entry in payload["timeline"]] == ["llm", "tool", "client_error", "agent"]


def test_build_request_trace_payload_normalizes_invalid_limit():
    payload = _build_request_trace_payload(
        request_id="r1",
        limit="bad",
        agent_items=[],
        tool_items=[],
        llm_entries=[],
        client_error_items=[],
    )

    assert payload["filters"]["limit"] == 100
