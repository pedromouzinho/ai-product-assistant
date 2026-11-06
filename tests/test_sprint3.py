"""Sprint 3 tests — digest, model badge, field encryption, chat TTL, user daily limit, feedback stats."""

import importlib
import json
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock

import pytest


# =========================================================================
# S3-01/02/03: Digest endpoint
# =========================================================================

class TestDigest:
    def test_digest_config_exists(self):
        """Digest-related env vars should be loadable."""
        import config
        # CHAT_HISTORY_TTL_DAYS is from S3-09, ensures config loads
        assert hasattr(config, "CHAT_HISTORY_TTL_DAYS")

    @pytest.mark.asyncio
    async def test_digest_endpoint_exists(self):
        """GET /api/digest should be a registered route."""
        from app import app
        routes = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/api/digest" in routes


# =========================================================================
# S3-06: Proactive CSV suggestion (system prompt hint)
# =========================================================================

class TestProactiveCSVHint:
    def test_system_prompt_contains_proactive_hint(self):
        """System prompt should contain proactive CSV analysis hint."""
        from tools import get_agent_system_prompt
        prompt = get_agent_system_prompt()
        assert "PROATIVO" in prompt or "proativo" in prompt.lower()


# =========================================================================
# S3-05: Model fallback detection
# =========================================================================

class TestFallbackDetection:
    def test_response_used_fallback_true(self):
        """Should detect fallback when chain has failed entry."""
        from agent import _response_used_fallback
        from models import LLMResponse
        resp = LLMResponse(content="test", fallback_chain=[{"provider": "azure", "status": "failed"}, {"provider": "anthropic", "status": "success"}])
        assert _response_used_fallback(resp) is True

    def test_response_used_fallback_false(self):
        """Should not detect fallback when no failed entries."""
        from agent import _response_used_fallback
        from models import LLMResponse
        resp = LLMResponse(content="test", fallback_chain=[])
        assert _response_used_fallback(resp) is False

    def test_response_used_fallback_none(self):
        """Should handle no chain gracefully."""
        from agent import _response_used_fallback
        from models import LLMResponse
        resp = LLMResponse(content="test")
        assert _response_used_fallback(resp) is False

    def test_agent_chat_response_has_is_fallback(self):
        """AgentChatResponse should have is_fallback field."""
        from models import AgentChatResponse
        resp = AgentChatResponse(answer="test", conversation_id="c1", is_fallback=True)
        assert resp.is_fallback is True

    def test_stream_event_class_exists(self):
        """StreamEvent should be importable from llm_provider."""
        from llm_provider import StreamEvent
        evt = StreamEvent(type="done", data={"fallback_chain": [{"status": "failed"}]})
        assert evt.type == "done"
        assert "fallback_chain" in evt.data

    def test_is_fallback_sticky_across_iterations(self):
        """Once is_fallback is True, subsequent non-fallback responses should not reset it."""
        from agent import _response_used_fallback
        from models import LLMResponse
        # Simulate iteration 1: no fallback
        r1 = LLMResponse(content="ok", fallback_chain=[])
        is_fallback = False
        if _response_used_fallback(r1):
            is_fallback = True
        assert is_fallback is False
        # Simulate iteration 2: fallback happened
        r2 = LLMResponse(content="ok", model="fallback-model", fallback_chain=[{"provider": "primary", "status": "failed"}])
        if _response_used_fallback(r2):
            is_fallback = True
        assert is_fallback is True
        # Simulate iteration 3: no fallback — is_fallback should stay True
        r3 = LLMResponse(content="final", fallback_chain=[])
        if _response_used_fallback(r3):
            is_fallback = True
        assert is_fallback is True  # sticky


# =========================================================================
# S3-07: Feedback stats enrichment
# =========================================================================

class TestFeedbackStatsEnriched:
    @pytest.mark.asyncio
    async def test_feedback_stats_returns_daily_trend(self, monkeypatch):
        """Feedback stats should include daily_trend, tags, and recent fields."""
        from routes_admin import feedback_stats

        ts = datetime.now(timezone.utc).isoformat()
        fake_rows = [
            {"PartitionKey": "abc", "RowKey": "1", "Rating": 8, "Note": "Great answer", "Timestamp_str": ts, "Question": "test q", "Answer": "test a"},
            {"PartitionKey": "abc", "RowKey": "2", "Rating": 3, "Note": "Impreciso, Lento — bad response", "Timestamp_str": ts, "Question": "q2", "Answer": "a2"},
        ]

        monkeypatch.setattr("routes_admin.table_query", AsyncMock(return_value=fake_rows))
        monkeypatch.setattr("routes_admin.feedback_memory", [])
        monkeypatch.setattr("routes_admin._is_admin_user", lambda u: True)
        monkeypatch.setattr("routes_admin.get_current_user", lambda c: {"sub": "admin", "role": "admin"})

        # Access the actual function — it may be wrapped by rate limiter
        fn = feedback_stats
        # Unwrap slowapi/limiter decorators
        while hasattr(fn, "__wrapped__"):
            fn = fn.__wrapped__

        mock_request = MagicMock()
        mock_request.client = MagicMock()
        mock_request.client.host = "127.0.0.1"
        mock_request.headers = {}
        mock_creds = MagicMock()

        result = await fn(mock_request, mock_creds)

        assert "daily_trend" in result
        assert "tags" in result
        assert "recent" in result
        assert result["total"] == 2
        assert "Impreciso" in result["tags"]


# =========================================================================
# S3-08: Field encryption
# =========================================================================

class TestFieldEncryption:
    def test_encrypt_decrypt_roundtrip(self):
        """Encrypted field should decrypt back to original."""
        # Force a deterministic key by setting env var
        import os
        from cryptography.fernet import Fernet
        key = Fernet.generate_key()
        os.environ["AUDIT_ENCRYPTION_KEY"] = key.decode("utf-8")

        # Reimport to pick up new key
        import field_encryption
        field_encryption._FERNET = None  # Reset cached instance

        original = "Sensitive question about bank transfers"
        encrypted = field_encryption.encrypt_field(original)

        assert encrypted.startswith("ENC::")
        assert encrypted != original

        decrypted = field_encryption.decrypt_field(encrypted)
        assert decrypted == original

        # Cleanup
        del os.environ["AUDIT_ENCRYPTION_KEY"]
        field_encryption._FERNET = None

    def test_encrypt_empty_returns_empty(self):
        """Empty string should not be encrypted."""
        from field_encryption import encrypt_field
        assert encrypt_field("") == ""

    def test_decrypt_non_encrypted_passthrough(self):
        """Non-encrypted values (no ENC:: prefix) pass through."""
        from field_encryption import decrypt_field
        assert decrypt_field("plain text") == "plain text"

    def test_sensitive_fields_list(self):
        """AUDIT_SENSITIVE_FIELDS should include Question and MetadataJson."""
        from field_encryption import AUDIT_SENSITIVE_FIELDS
        assert "Question" in AUDIT_SENSITIVE_FIELDS
        assert "MetadataJson" in AUDIT_SENSITIVE_FIELDS

    def test_route_deps_imports_encryption(self):
        """route_deps should import encrypt_field."""
        import route_deps
        assert hasattr(route_deps, "encrypt_field")


# =========================================================================
# S3-09: ChatHistory TTL
# =========================================================================

class TestChatHistoryTTL:
    def test_ttl_config_default(self):
        """Default CHAT_HISTORY_TTL_DAYS should be 90."""
        from config import CHAT_HISTORY_TTL_DAYS
        assert CHAT_HISTORY_TTL_DAYS == 90

    @pytest.mark.asyncio
    async def test_cleanup_deletes_old_entries(self, monkeypatch):
        """cleanup_expired_chats should delete entries matched by server-side filter."""
        from chat_ttl import cleanup_expired_chats

        old_date = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()

        # Server-side filter means only expired rows are returned
        expired_rows = [
            {"PartitionKey": "user1", "RowKey": "conv-old", "UpdatedAt": old_date},
        ]

        deleted_items = []
        query_filters = []

        async def fake_query(table, filter_str=None, top=200):
            query_filters.append(filter_str)
            if len(query_filters) == 1:
                return expired_rows
            return []  # second call: nothing left

        async def fake_delete(table, pk, rk):
            deleted_items.append((pk, rk))

        monkeypatch.setattr("chat_ttl.table_query", fake_query)
        monkeypatch.setattr("chat_ttl.table_delete", fake_delete)
        monkeypatch.setattr("chat_ttl.CHAT_HISTORY_TTL_DAYS", 90)

        count = await cleanup_expired_chats()

        assert count == 1
        assert ("user1", "conv-old") in deleted_items
        # Verify server-side OData filter was used
        assert query_filters[0] and "UpdatedAt lt" in query_filters[0]

    @pytest.mark.asyncio
    async def test_ttl_disabled_when_zero(self, monkeypatch):
        """TTL=0 should skip cleanup."""
        from chat_ttl import cleanup_expired_chats

        monkeypatch.setattr("chat_ttl.CHAT_HISTORY_TTL_DAYS", 0)
        count = await cleanup_expired_chats()
        assert count == 0


# =========================================================================
# S3-10: Per-user daily token limit
# =========================================================================

class TestUserDailyTokenLimit:
    def test_config_default_zero(self):
        """USER_DAILY_TOKEN_LIMIT should default to 0 (disabled)."""
        from config import USER_DAILY_TOKEN_LIMIT
        assert USER_DAILY_TOKEN_LIMIT == 0

    @pytest.mark.asyncio
    async def test_check_passes_when_disabled(self):
        """When limit is 0, check should always pass."""
        from agent import _check_user_daily_limit
        result = await _check_user_daily_limit("user123")
        assert result == ""

    @pytest.mark.asyncio
    async def test_check_blocks_when_exceeded(self, monkeypatch):
        """When user exceeds daily limit, check should return reason (reads from storage)."""
        import agent
        monkeypatch.setattr("agent.USER_DAILY_TOKEN_LIMIT", 1000)

        # Mock storage to return a high spend
        monkeypatch.setattr(
            "agent._read_user_spend_from_storage",
            AsyncMock(return_value=1500),
        )

        result = await agent._check_user_daily_limit("test-user")
        assert "Limite" in result

        monkeypatch.setattr("agent.USER_DAILY_TOKEN_LIMIT", 0)

    @pytest.mark.asyncio
    async def test_check_allows_when_under_limit(self, monkeypatch):
        """When user is under limit, check should pass."""
        import agent
        monkeypatch.setattr("agent.USER_DAILY_TOKEN_LIMIT", 1000)
        monkeypatch.setattr(
            "agent._read_user_spend_from_storage",
            AsyncMock(return_value=500),
        )

        result = await agent._check_user_daily_limit("test-user")
        assert result == ""

        monkeypatch.setattr("agent.USER_DAILY_TOKEN_LIMIT", 0)

    @pytest.mark.asyncio
    async def test_record_reads_then_merges(self, monkeypatch):
        """_record_user_daily_spend should read current, add delta, persist."""
        import agent
        monkeypatch.setattr("agent.USER_DAILY_TOKEN_LIMIT", 10000)

        # Simulate storage has 200 tokens already
        monkeypatch.setattr(
            "agent._read_user_spend_from_storage",
            AsyncMock(return_value=200),
        )

        persisted = []
        async def fake_insert(table, entity):
            persisted.append(("insert", entity))
            return False  # row already exists
        async def fake_merge(table, entity):
            persisted.append(("merge", entity))
        monkeypatch.setattr("agent.table_insert", fake_insert)
        monkeypatch.setattr("agent.table_merge", fake_merge)

        await agent._record_user_daily_spend("rec-user", 500)

        # Should have read 200, added 500, merged 700
        assert len(persisted) == 2  # insert (failed) + merge
        last_op, last_entity = persisted[-1]
        assert last_op == "merge"
        assert last_entity["TotalTokens"] == 700
        assert last_entity["RowKey"] == "rec-user"

        monkeypatch.setattr("agent.USER_DAILY_TOKEN_LIMIT", 0)

    @pytest.mark.asyncio
    async def test_record_noop_when_disabled(self):
        """When limit is 0, record should be noop (no storage calls)."""
        import agent
        called = []
        original = agent._read_user_spend_from_storage
        async def spy(u, d):
            called.append((u, d))
            return 0
        agent._read_user_spend_from_storage = spy
        try:
            await agent._record_user_daily_spend("noop-user", 500)
            assert len(called) == 0  # should not even read
        finally:
            agent._read_user_spend_from_storage = original

    @pytest.mark.asyncio
    async def test_concurrent_records_serialised_by_lock(self, monkeypatch):
        """Two concurrent _record_user_daily_spend calls for the same user
        should be serialised by the per-user lock, so no increment is lost."""
        import asyncio
        import agent
        monkeypatch.setattr("agent.USER_DAILY_TOKEN_LIMIT", 100000)

        # Track the sequence of reads and writes
        storage_value = {"total": 0}
        call_log = []

        async def fake_read(user_sub, day):
            val = storage_value["total"]
            call_log.append(("read", val))
            await asyncio.sleep(0.01)  # simulate network latency
            return val

        async def fake_insert(table, entity):
            call_log.append(("insert", entity["TotalTokens"]))
            storage_value["total"] = entity["TotalTokens"]
            return True

        monkeypatch.setattr("agent._read_user_spend_from_storage", fake_read)
        monkeypatch.setattr("agent.table_insert", fake_insert)

        # Clear any stale lock
        agent._user_spend_locks.pop("lock-test-user", None)

        # Fire two concurrent records of 500 tokens each
        await asyncio.gather(
            agent._record_user_daily_spend("lock-test-user", 500),
            agent._record_user_daily_spend("lock-test-user", 500),
        )

        # With the lock, the second coroutine reads AFTER the first wrote,
        # so the final total must be 1000 (not 500 from a lost update).
        assert storage_value["total"] == 1000

        # Cleanup
        agent._user_spend_locks.pop("lock-test-user", None)
        monkeypatch.setattr("agent.USER_DAILY_TOKEN_LIMIT", 0)
