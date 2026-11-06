"""Tests para client error endpoint (SPEC-17)."""

import pytest
import pydantic

from models import ClientErrorReport


class TestClientErrorModel:
    def test_valid_report(self):
        report = ClientErrorReport(
            error_type="uncaught_error",
            message="Cannot read property 'map' of undefined",
            stack="TypeError: Cannot read...\n    at App (index.html:123)",
            component="ChatMessage",
            url="https://app.example.com/",
            timestamp="2026-02-28T10:00:00Z",
        )
        assert report.error_type == "uncaught_error"
        assert len(report.message) > 0

    def test_minimal_report(self):
        report = ClientErrorReport(
            error_type="error",
            message="Something went wrong",
        )
        assert report.stack is None
        assert report.component is None
        assert report.request_id is None
        assert report.conversation_id is None
        assert report.agent_mode is None

    def test_report_accepts_runtime_context_fields(self):
        report = ClientErrorReport(
            error_type="react_error_boundary",
            message="Boom",
            request_id="req_123",
            conversation_id="conv_456",
            agent_mode="general",
        )
        assert report.request_id == "req_123"
        assert report.conversation_id == "conv_456"
        assert report.agent_mode == "general"

    def test_error_type_max_length(self):
        with pytest.raises(pydantic.ValidationError):
            ClientErrorReport(
                error_type="x" * 200,
                message="test",
            )

    def test_client_error_path_exempt(self):
        import app as app_module

        exempt = getattr(app_module, "_AUTH_EXEMPT_PATHS", set())
        assert "/api/client-error" in exempt
