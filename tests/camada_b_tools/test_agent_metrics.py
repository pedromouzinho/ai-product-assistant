"""Tests para métricas de requests do agente."""

from agent_metrics import AgentMetrics


class TestAgentMetrics:
    def test_record_and_snapshot(self):
        m = AgentMetrics()
        m.record("chat", 1200, tool_count=2, fallback=False, status="ok")
        m.record("chat", 800, tool_count=1, fallback=True, status="ok")
        m.record("chat_stream", 3000, tool_count=0, fallback=False, status="error", error_kind="all_providers_failed")

        snap = m.snapshot()

        assert snap["overall"]["request_count"] == 3
        assert snap["overall"]["fallback_count"] == 1
        assert snap["overall"]["error_count"] == 1
        assert snap["overall"]["total_tools"] == 3
        assert snap["overall"]["avg_tools_per_request"] == 1.0
        assert snap["by_route"]["chat"]["request_count"] == 2
        assert snap["by_route"]["chat"]["fallback_rate"] == 0.5
        assert snap["by_route"]["chat_stream"]["error_count"] == 1
        assert snap["overall"]["error_kind_counts"] == {"all_providers_failed": 1}
        assert snap["by_route"]["chat_stream"]["error_kind_counts"] == {"all_providers_failed": 1}

    def test_blocked_requests_are_counted(self):
        m = AgentMetrics()
        m.record("chat_stream", 150, tool_count=0, fallback=False, status="blocked", error_kind="pre_model_blocked")

        snap = m.snapshot()

        assert snap["overall"]["blocked_count"] == 1
        assert snap["by_route"]["chat_stream"]["blocked_count"] == 1
        assert snap["overall"]["error_kind_counts"] == {"pre_model_blocked": 1}

    def test_reset(self):
        m = AgentMetrics()
        m.record("chat", 100, tool_count=1)
        assert m.snapshot()["overall"]["request_count"] == 1
        m.reset()
        assert m.snapshot()["overall"]["request_count"] == 0
        assert m.snapshot()["by_route"] == {}

    def test_recent_events_keep_request_context(self):
        m = AgentMetrics()
        m.record(
            "chat_stream",
            250,
            tool_count=2,
            fallback=True,
            status="ok",
            request_id="req-123",
            conversation_id="conv-456",
            model_used="gpt-x",
            error_kind="",
        )

        events = m.recent_events()

        assert len(events) == 1
        assert events[0]["route"] == "chat_stream"
        assert events[0]["request_id"] == "req-123"
        assert events[0]["conversation_id"] == "conv-456"
        assert events[0]["model_used"] == "gpt-x"
        assert events[0]["fallback"] is True
        assert events[0]["error_kind"] == ""

    def test_recent_events_keep_error_kind(self):
        m = AgentMetrics()
        m.record(
            "chat",
            180,
            tool_count=0,
            fallback=False,
            status="blocked",
            error_kind="provider_governance_blocked",
        )

        events = m.recent_events()

        assert len(events) == 1
        assert events[0]["status"] == "blocked"
        assert events[0]["error_kind"] == "provider_governance_blocked"
