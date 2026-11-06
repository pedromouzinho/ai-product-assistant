"""Tests for write-through persistence (A1)."""

import asyncio
import pytest
import agent


# ---------------------------------------------------------------------------
# ConversationStore dirty tracking
# ---------------------------------------------------------------------------

class TestDirtyTracking:
    def test_setitem_marks_dirty(self):
        store = agent.ConversationStore(max_conversations=10, ttl_seconds=3600)
        store["c1"] = [{"role": "system", "content": "hi"}]
        assert "c1" in store.get_dirty_keys()

    def test_mark_dirty_explicit(self):
        store = agent.ConversationStore(max_conversations=10, ttl_seconds=3600)
        store["c1"] = [{"role": "system", "content": "hi"}]
        store.mark_clean("c1")
        assert "c1" not in store.get_dirty_keys()

        store["c1"].append({"role": "user", "content": "hello"})
        store.mark_dirty("c1")
        assert "c1" in store.get_dirty_keys()

    def test_mark_clean_removes_dirty(self):
        store = agent.ConversationStore(max_conversations=10, ttl_seconds=3600)
        store["c1"] = [{"role": "system", "content": "hi"}]
        assert "c1" in store.get_dirty_keys()
        store.mark_clean("c1")
        assert "c1" not in store.get_dirty_keys()

    def test_mark_dirty_nonexistent_key_ignored(self):
        store = agent.ConversationStore(max_conversations=10, ttl_seconds=3600)
        store.mark_dirty("nonexistent")
        assert len(store.get_dirty_keys()) == 0

    def test_get_dirty_keys_excludes_deleted(self):
        store = agent.ConversationStore(max_conversations=10, ttl_seconds=3600)
        store["c1"] = [{"role": "system", "content": "hi"}]
        store["c2"] = [{"role": "system", "content": "hi2"}]
        assert len(store.get_dirty_keys()) == 2
        del store["c1"]
        dirty = store.get_dirty_keys()
        assert "c1" not in dirty
        assert "c2" in dirty

    def test_evict_clears_dirty(self):
        store = agent.ConversationStore(max_conversations=10, ttl_seconds=3600)
        store["c1"] = [{"role": "system", "content": "hi"}]
        assert "c1" in store.get_dirty_keys()
        store._evict("c1", reason="test")
        assert "c1" not in store.get_dirty_keys()

    def test_multiple_dirty_keys(self):
        store = agent.ConversationStore(max_conversations=10, ttl_seconds=3600)
        for i in range(5):
            store[f"c{i}"] = [{"role": "system", "content": f"msg{i}"}]
        assert len(store.get_dirty_keys()) == 5
        store.mark_clean("c0")
        store.mark_clean("c2")
        dirty = store.get_dirty_keys()
        assert len(dirty) == 3
        assert dirty == {"c1", "c3", "c4"}


class TestAsyncDirtyTracking:
    @pytest.mark.asyncio
    async def test_async_set_marks_dirty(self):
        store = agent.ConversationStore(max_conversations=10, ttl_seconds=3600)
        await store.async_set("c1", [{"role": "system", "content": "hi"}])
        assert "c1" in store.get_dirty_keys()


# ---------------------------------------------------------------------------
# Pre-evict persist callback
# ---------------------------------------------------------------------------

class TestPreEvictPersist:
    def test_evict_calls_persist_callback(self):
        """When a dirty key is evicted, the persist callback is called with snapshot."""
        captured = {}

        def mock_persist(key, snapshot):
            captured["key"] = key
            captured["snapshot"] = list(snapshot)

        store = agent.ConversationStore(max_conversations=10, ttl_seconds=3600)
        store._on_evict_persist = mock_persist
        store["c1"] = [{"role": "system", "content": "hi"}, {"role": "user", "content": "hello"}]
        store._evict("c1", reason="test")

        assert captured["key"] == "c1"
        assert len(captured["snapshot"]) == 2
        assert captured["snapshot"][1]["content"] == "hello"

    def test_evict_skips_clean_conversations(self):
        """Clean (non-dirty) conversations should NOT trigger persist on eviction."""
        captured = {}

        def mock_persist(key, snapshot):
            captured["key"] = key

        store = agent.ConversationStore(max_conversations=10, ttl_seconds=3600)
        store._on_evict_persist = mock_persist
        store["c1"] = [{"role": "system", "content": "hi"}]
        store.mark_clean("c1")
        store._evict("c1", reason="test")

        assert "key" not in captured

    def test_lru_eviction_triggers_persist(self):
        """When LRU evicts, the persist callback fires for dirty conversations."""
        evicted = []

        def mock_persist(key, snapshot):
            evicted.append(key)

        store = agent.ConversationStore(max_conversations=2, ttl_seconds=3600)
        store._on_evict_persist = mock_persist
        store["c1"] = [{"role": "system", "content": "first"}]
        store["c2"] = [{"role": "system", "content": "second"}]
        # Adding c3 should evict c1 (LRU)
        store["c3"] = [{"role": "system", "content": "third"}]

        assert "c1" in evicted
        assert "c1" not in store


# ---------------------------------------------------------------------------
# flush_dirty_conversations
# ---------------------------------------------------------------------------

class TestFlushDirtyConversations:
    @pytest.mark.asyncio
    async def test_flush_returns_zero_when_no_dirty(self):
        # Clear all dirty flags
        for key in list(agent.conversations.get_dirty_keys()):
            agent.conversations.mark_clean(key)
        count = await agent.flush_dirty_conversations()
        assert count == 0
