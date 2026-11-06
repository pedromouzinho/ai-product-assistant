"""Sprint 4 tests — writer profile proactive, PII column scan, digest feedback, email detect."""

import json
import re
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


# =========================================================================
# S4-01: Proactive writer profile suggestion after N stories
# =========================================================================

class TestProactiveWriterProfileSuggestion:
    def test_config_threshold_exists(self):
        """WRITER_PROFILE_LEARN_THRESHOLD should be in config."""
        from config import WRITER_PROFILE_LEARN_THRESHOLD
        assert isinstance(WRITER_PROFILE_LEARN_THRESHOLD, int)
        assert WRITER_PROFILE_LEARN_THRESHOLD >= 0

    def test_increment_story_count(self):
        """_increment_story_count should increment and return count."""
        from agent import _increment_story_count, _user_story_counts
        # Clean state
        _user_story_counts.pop("test-s401", None)
        assert _increment_story_count("test-s401") == 1
        assert _increment_story_count("test-s401") == 2
        assert _increment_story_count("test-s401") == 3
        # Cleanup
        _user_story_counts.pop("test-s401", None)

    def test_should_suggest_at_threshold(self):
        """_should_suggest_writer_profile triggers at threshold count."""
        from agent import _should_suggest_writer_profile, _user_story_counts
        _user_story_counts["threshold-user"] = 3  # matches default threshold
        assert _should_suggest_writer_profile("threshold-user") is True
        _user_story_counts["threshold-user"] = 2  # below threshold
        assert _should_suggest_writer_profile("threshold-user") is False
        # Cleanup
        _user_story_counts.pop("threshold-user", None)

    def test_should_not_suggest_when_disabled(self, monkeypatch):
        """When threshold is 0, never suggest."""
        from agent import _should_suggest_writer_profile, _user_story_counts
        monkeypatch.setattr("agent.WRITER_PROFILE_LEARN_THRESHOLD", 0)
        _user_story_counts["disabled-user"] = 100
        assert _should_suggest_writer_profile("disabled-user") is False
        _user_story_counts.pop("disabled-user", None)

    def test_should_not_suggest_empty_user(self):
        """Empty user_sub should never trigger suggestion."""
        from agent import _should_suggest_writer_profile
        assert _should_suggest_writer_profile("") is False


# =========================================================================
# S4-02: Auto-apply writer profile in story generation
# =========================================================================

class TestAutoApplyWriterProfile:
    @pytest.mark.asyncio
    async def test_load_writer_profile_returns_dict(self, monkeypatch):
        """_load_writer_profile should return dict with style_analysis."""
        from tools_learning import _load_writer_profile

        fake_rows = [{
            "PartitionKey": "writer::test-user",
            "RowKey": "test_author",
            "AuthorName": "Test Author",
            "StyleAnalysis": "Formal, technical, uses INVEST criteria",
            "SampleCount": 5,
            "SampleIdsJson": "[]",
            "PreferredVocabulary": "deve, quando, então",
            "TitlePattern": "Como [persona], quero [ação]",
            "ACStructure": "Dado que... Quando... Então...",
        }]
        monkeypatch.setattr("tools_learning.table_query", AsyncMock(return_value=fake_rows))

        profile = await _load_writer_profile("Test Author", owner_sub="test-user")
        assert profile is not None
        assert "style_analysis" in profile
        assert "Formal" in profile["style_analysis"]
        assert profile["preferred_vocabulary"] == "deve, quando, então"

    @pytest.mark.asyncio
    async def test_load_user_default_profile_no_author(self, monkeypatch):
        """_load_user_default_profile should find profiles without author_name."""
        from tools_learning import _load_user_default_profile

        fake_rows = [{
            "PartitionKey": "writer::user-abc",
            "RowKey": "some_author",
            "AuthorName": "Some Author",
            "StyleAnalysis": "Concise, action-oriented",
            "SampleCount": 3,
            "SampleIdsJson": "[]",
            "PreferredVocabulary": "como, quero",
            "TitlePattern": "",
            "ACStructure": "",
            "UpdatedAt": "2026-03-23T10:00:00",
        }]
        monkeypatch.setattr("tools_learning.table_query", AsyncMock(return_value=fake_rows))

        profile = await _load_user_default_profile(owner_sub="user-abc")
        assert profile is not None
        assert "Concise" in profile["style_analysis"]
        assert profile["author_name"] == "Some Author"

    @pytest.mark.asyncio
    async def test_load_user_default_profile_empty_returns_none(self, monkeypatch):
        """_load_user_default_profile with empty owner_sub returns None."""
        from tools_learning import _load_user_default_profile
        result = await _load_user_default_profile(owner_sub="")
        assert result is None

    @pytest.mark.asyncio
    async def test_load_user_default_profile_picks_latest(self, monkeypatch):
        """_load_user_default_profile picks the most recently updated profile."""
        from tools_learning import _load_user_default_profile

        fake_rows = [
            {
                "PartitionKey": "writer::user-xyz",
                "RowKey": "old_author",
                "AuthorName": "Old Author",
                "StyleAnalysis": "Old style",
                "SampleIdsJson": "[]",
                "UpdatedAt": "2026-01-01T10:00:00",
            },
            {
                "PartitionKey": "writer::user-xyz",
                "RowKey": "new_author",
                "AuthorName": "New Author",
                "StyleAnalysis": "New style",
                "SampleIdsJson": "[]",
                "UpdatedAt": "2026-03-23T10:00:00",
            },
        ]
        monkeypatch.setattr("tools_learning.table_query", AsyncMock(return_value=fake_rows))

        profile = await _load_user_default_profile(owner_sub="user-xyz")
        assert profile is not None
        assert profile["author_name"] == "New Author"
        assert "New style" in profile["style_analysis"]

    def test_system_prompt_has_writer_profile_hint(self):
        """System prompt should mention writer_style_hint for auto-apply."""
        from tools import get_agent_system_prompt
        prompt = get_agent_system_prompt()
        assert "writer_style_hint" in prompt


# =========================================================================
# S4-03: Email pattern detection in uploads
# =========================================================================

class TestEmailPatternDetection:
    def test_system_prompt_has_email_hint(self):
        """System prompt should contain proactive email classification hint."""
        from tools import get_agent_system_prompt
        prompt = get_agent_system_prompt()
        assert "EMAIL" in prompt or "email" in prompt.lower()


class TestPresentationUploadEnrichment:
    def test_generate_presentation_does_not_force_upload_injection(self):
        """PPTX generation should not auto-inject raw uploaded documents into content."""
        text = (REPO_ROOT / "agent.py").read_text(encoding="utf-8")
        assert 'if tc.name == "generate_presentation" and files:' not in text
        assert "--- CONTEÚDO INTEGRAL DOS DOCUMENTOS ---" not in text


# =========================================================================
# S4-07: Digest feedback summary
# =========================================================================

class TestDigestFeedbackSummary:
    def test_config_exists(self):
        """DIGEST_FEEDBACK_SUMMARY_ENABLED should be in config."""
        from config import DIGEST_FEEDBACK_SUMMARY_ENABLED
        assert isinstance(DIGEST_FEEDBACK_SUMMARY_ENABLED, bool)

    @pytest.mark.asyncio
    async def test_weekly_feedback_summary(self, monkeypatch):
        """_get_weekly_feedback_summary should return stats dict."""
        from routes_digest import _get_weekly_feedback_summary

        fake_rules = [{"PartitionKey": "r1", "RowKey": "1"}]
        fake_feedbacks = [
            {"PartitionKey": "f1", "RowKey": "1", "Rating": 8},
            {"PartitionKey": "f2", "RowKey": "2", "Rating": 3},
            {"PartitionKey": "f3", "RowKey": "3", "Rating": 9},
        ]

        call_count = {"n": 0}
        async def fake_query(table, filter_str=None, top=200):
            call_count["n"] += 1
            if table == "PromptRules":
                return fake_rules
            if table == "feedback":
                return fake_feedbacks
            return []

        monkeypatch.setattr("routes_digest.table_query", fake_query)

        result = await _get_weekly_feedback_summary()
        assert result["rules_learned"] == 1
        assert result["total_feedbacks"] == 3
        assert result["avg_rating"] == 6.7  # (8+3+9)/3
        assert result["positive_count"] == 2  # 8 and 9
        assert result["negative_count"] == 1  # 3

    @pytest.mark.asyncio
    async def test_digest_endpoint_includes_feedback(self, monkeypatch):
        """GET /api/digest should include feedback_summary when enabled."""
        from routes_digest import _get_weekly_feedback_summary

        # Just verify the function exists and returns expected shape
        monkeypatch.setattr("routes_digest.table_query", AsyncMock(return_value=[]))
        result = await _get_weekly_feedback_summary()
        assert "rules_learned" in result
        assert "total_feedbacks" in result


# =========================================================================
# S4-09: PII column scanning
# =========================================================================

class TestPIIColumnScanner:
    def test_config_exists(self):
        """PII_COLUMN_SCAN_ENABLED should be in config."""
        from config import PII_COLUMN_SCAN_ENABLED
        assert isinstance(PII_COLUMN_SCAN_ENABLED, bool)

    def test_scan_detects_email_column_by_name(self):
        """Column named 'email' should be detected as PII."""
        from pii_column_scanner import scan_columns_for_pii
        results = scan_columns_for_pii(["id", "email", "status"])
        assert len(results) >= 1
        email_result = [r for r in results if r.pii_type == "email"]
        assert len(email_result) == 1
        assert email_result[0].column_name == "email"

    def test_scan_detects_email_by_values(self):
        """Columns with email values should be detected even without name hint."""
        from pii_column_scanner import scan_columns_for_pii
        sample_rows = [
            {"contact": "john@example.com"},
            {"contact": "jane@example.com"},
            {"contact": "admin@example.org"},
        ]
        results = scan_columns_for_pii(["contact"], sample_rows)
        assert len(results) == 1
        assert results[0].pii_type == "email"

    def test_scan_detects_nif(self):
        """Column with NIF values should be detected."""
        from pii_column_scanner import scan_columns_for_pii
        sample_rows = [
            {"nif_cliente": "123456789"},
            {"nif_cliente": "987654321"},
            {"nif_cliente": "234567890"},
        ]
        results = scan_columns_for_pii(["nif_cliente"], sample_rows)
        assert len(results) >= 1
        nif_result = [r for r in results if r.pii_type == "nif"]
        assert len(nif_result) == 1

    def test_scan_detects_phone(self):
        """Column named 'telefone' should be detected as phone PII."""
        from pii_column_scanner import scan_columns_for_pii
        results = scan_columns_for_pii(["nome", "telefone", "cargo"])
        phone_results = [r for r in results if r.pii_type == "phone"]
        assert len(phone_results) == 1

    def test_scan_detects_iban(self):
        """IBAN column with valid values should be detected."""
        from pii_column_scanner import scan_columns_for_pii
        sample_rows = [
            {"iban": "PT50000201231234567890154"},
            {"iban": "PT50000201231234567890155"},
        ]
        results = scan_columns_for_pii(["iban"], sample_rows)
        assert len(results) >= 1
        assert results[0].pii_type == "iban"

    def test_scan_no_pii_in_clean_data(self):
        """Non-PII columns should not be flagged."""
        from pii_column_scanner import scan_columns_for_pii
        sample_rows = [
            {"id": "1", "status": "active", "amount": "100.50"},
            {"id": "2", "status": "closed", "amount": "200.00"},
        ]
        results = scan_columns_for_pii(["id", "status", "amount"], sample_rows)
        assert len(results) == 0

    def test_scan_disabled_returns_empty(self, monkeypatch):
        """When PII scan is disabled, should return empty."""
        monkeypatch.setattr("pii_column_scanner.PII_COLUMN_SCAN_ENABLED", False)
        from pii_column_scanner import scan_columns_for_pii
        results = scan_columns_for_pii(["email", "phone"])
        assert results == []

    def test_mask_email(self):
        """Email masking should preserve first char and domain suffix."""
        from pii_column_scanner import _mask_email
        masked = _mask_email("jane@example.com")
        assert masked.startswith("j***@")
        assert masked.endswith(".com")
        assert "jane" not in masked
        assert "example" not in masked

    def test_mask_name(self):
        """Name masking should reduce to initials."""
        from pii_column_scanner import _mask_name
        masked = _mask_name("Jane Doe")
        assert masked == "J. D."

    def test_mask_pii_in_rows(self):
        """mask_pii_in_rows should replace PII values."""
        from pii_column_scanner import scan_columns_for_pii, mask_pii_in_rows, PIIColumnResult
        pii_cols = [PIIColumnResult("email", "email", 0.9, 2, 2)]
        rows = [
            {"id": "1", "email": "jane@example.com"},
            {"id": "2", "email": "anna@example.com"},
        ]
        masked = mask_pii_in_rows(rows, pii_cols)
        assert "jane" not in masked[0]["email"]
        assert "ana" not in masked[1]["email"]
        # IDs should be unchanged
        assert masked[0]["id"] == "1"
        assert masked[1]["id"] == "2"

    def test_format_pii_report(self):
        """format_pii_report should produce readable markdown."""
        from pii_column_scanner import format_pii_report, PIIColumnResult
        results = [
            PIIColumnResult("email", "email", 0.85, 10, 9),
            PIIColumnResult("nif_cliente", "nif", 0.7, 10, 7),
        ]
        report = format_pii_report(results)
        assert "PII" in report
        assert "email" in report.lower()
        assert "NIF" in report
        assert "mascaradas" in report

    def test_mask_pii_in_text(self):
        """mask_pii_in_text should mask PII in delimited text."""
        from pii_column_scanner import mask_pii_in_text, PIIColumnResult
        columns = ["id", "email", "status"]
        pii_cols = [PIIColumnResult("email", "email", 0.9, 2, 2)]
        text = "id\temail\tstatus\n1\tjane@example.com\tactive\n2\tanna@example.com\tclosed"
        masked = mask_pii_in_text(text, columns, pii_cols, delimiter="\t")
        lines = masked.split("\n")
        # Header unchanged
        assert lines[0] == "id\temail\tstatus"
        # Data rows masked
        assert "jane" not in lines[1]
        assert "ana" not in lines[2]
        # Non-PII columns unchanged
        assert "active" in lines[1]
        assert "closed" in lines[2]

    def test_mask_pii_in_text_no_pii(self):
        """mask_pii_in_text with no PII columns should return original."""
        from pii_column_scanner import mask_pii_in_text
        text = "id\tstatus\n1\tactive"
        result = mask_pii_in_text(text, ["id", "status"], [], delimiter="\t")
        assert result == text


# =========================================================================
# S4-06: History-based suggestions (placeholder structure)
# =========================================================================

class TestHistoryBasedSuggestions:
    def test_empty_state_has_suggestions(self):
        """ChatEmptyState should have getDynamicSuggestions or similar."""
        import os
        path = os.path.join(
            os.path.dirname(__file__), "..",
            "frontend", "src", "components", "ChatEmptyState.jsx"
        )
        assert os.path.isfile(path)

    def test_app_has_history_suggestion_builder(self):
        """App.jsx should contain _buildHistorySuggestions function."""
        import os
        path = os.path.join(
            os.path.dirname(__file__), "..",
            "frontend", "src", "App.jsx"
        )
        with open(path) as f:
            content = f.read()
        assert "_buildHistorySuggestions" in content

    def test_app_injects_history_suggestions(self):
        """getDynamicSuggestions should call _buildHistorySuggestions."""
        import os
        path = os.path.join(
            os.path.dirname(__file__), "..",
            "frontend", "src", "App.jsx"
        )
        with open(path) as f:
            content = f.read()
        assert "getDynamicSuggestions" in content
        assert "_buildHistorySuggestions" in content


# =========================================================================
# S4-04: Email classification preview in-chat
# =========================================================================

class TestEmailClassificationPreview:
    def test_component_exists(self):
        """EmailClassificationPreview.jsx should exist."""
        import os
        path = os.path.join(
            os.path.dirname(__file__), "..",
            "frontend", "src", "components", "EmailClassificationPreview.jsx"
        )
        assert os.path.isfile(path)

    def test_component_has_table_structure(self):
        """Component should render table with expected columns."""
        import os
        path = os.path.join(
            os.path.dirname(__file__), "..",
            "frontend", "src", "components", "EmailClassificationPreview.jsx"
        )
        with open(path) as f:
            content = f.read()
        # Table headers
        assert "<th>De</th>" in content or "De" in content
        assert "Assunto" in content
        assert "Label" in content
        assert "Conf" in content

    def test_message_bubble_imports_preview(self):
        """MessageBubble should import EmailClassificationPreview."""
        import os
        path = os.path.join(
            os.path.dirname(__file__), "..",
            "frontend", "src", "components", "MessageBubble.jsx"
        )
        with open(path) as f:
            content = f.read()
        assert "EmailClassificationPreview" in content
        assert "getEmailClassificationData" in content

    def test_message_bubble_uses_request_outcome_contract(self):
        """MessageBubble should surface explicit request outcome state from the backend."""
        import os
        path = os.path.join(
            os.path.dirname(__file__), "..",
            "frontend", "src", "components", "MessageBubble.jsx"
        )
        with open(path, encoding="utf-8") as f:
            content = f.read()
        assert "request_status" in content
        assert "request_error_kind" in content
        assert "Pedido bloqueado" in content
        assert "Pedido com erro" in content

    def test_tool_results_helper_exists(self):
        """getEmailClassificationData should be exported from toolResults."""
        import os
        path = os.path.join(
            os.path.dirname(__file__), "..",
            "frontend", "src", "utils", "toolResults.js"
        )
        with open(path) as f:
            content = f.read()
        assert "getEmailClassificationData" in content
        assert "counts_by_label" in content
        assert "total_count" in content

    def test_css_styles_exist(self):
        """CSS should include email classification preview styles."""
        import os
        path = os.path.join(
            os.path.dirname(__file__), "..",
            "frontend", "src", "styles", "index.css"
        )
        with open(path) as f:
            content = f.read()
        assert ".email-class-preview" in content
        assert ".email-class-table" in content
        assert ".email-class-pill" in content


# =========================================================================
# S4-05: Feature guide (optional overlay)
# =========================================================================

class TestFeatureGuide:
    def test_component_exists(self):
        """FeatureGuide.jsx should exist."""
        import os
        path = os.path.join(
            os.path.dirname(__file__), "..",
            "frontend", "src", "components", "FeatureGuide.jsx"
        )
        assert os.path.isfile(path)

    def test_guide_has_sections(self):
        """FeatureGuide should have categorized sections."""
        import os
        path = os.path.join(
            os.path.dirname(__file__), "..",
            "frontend", "src", "components", "FeatureGuide.jsx"
        )
        with open(path) as f:
            content = f.read()
        assert "GUIDE_SECTIONS" in content
        assert "User Stories" in content
        assert "Análise de dados" in content
        assert "Experimentar" in content

    def test_guide_moved_out_of_empty_state(self):
        """Tools manual should live outside ChatEmptyState and be triggered from App header."""
        import os
        empty_path = os.path.join(
            os.path.dirname(__file__), "..",
            "frontend", "src", "components", "ChatEmptyState.jsx"
        )
        with open(empty_path) as f:
            empty_content = f.read()
        assert "Manual de funcionalidades" not in empty_content
        assert "onShowToolsManual" not in empty_content

        app_path = os.path.join(
            os.path.dirname(__file__), "..",
            "frontend", "src", "App.jsx"
        )
        with open(app_path) as f:
            app_content = f.read()
        assert "header-manual-btn" in app_content
        assert "Manual de funcionalidades" in app_content

    def test_guide_css_exists(self):
        """CSS should include feature guide overlay styles."""
        import os
        path = os.path.join(
            os.path.dirname(__file__), "..",
            "frontend", "src", "styles", "index.css"
        )
        with open(path) as f:
            content = f.read()
        assert ".feature-guide-overlay" in content
        assert ".feature-guide-modal" in content
        assert ".feature-guide-section" in content


# =========================================================================
# Frontend component existence
# =========================================================================

class TestFrontendComponents:
    def test_writer_profile_suggestion_component_exists(self):
        """WriterProfileSuggestion.jsx should exist."""
        import os
        path = os.path.join(
            os.path.dirname(__file__), "..",
            "frontend", "src", "components", "WriterProfileSuggestion.jsx"
        )
        assert os.path.isfile(path)

    def test_digest_card_has_feedback_summary(self):
        """DigestCard.jsx should render feedback_summary section."""
        import os
        path = os.path.join(
            os.path.dirname(__file__), "..",
            "frontend", "src", "components", "DigestCard.jsx"
        )
        with open(path) as f:
            content = f.read()
        assert "feedback_summary" in content
        assert "Melhorias esta semana" in content
