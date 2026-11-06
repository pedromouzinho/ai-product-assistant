from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_get_story_lane_index_status_returns_counts_and_sync_state(monkeypatch):
    import story_index_admin

    async def _fake_search_request_with_retry(**kwargs):
        url = kwargs["url"]
        if "story-examples-index" in url:
            return {"@odata.count": 5, "value": []}
        if "story-devops-index" in url:
            return {"@odata.count": 800, "value": []}
        if "story-knowledge-index" in url:
            return {"@odata.count": 168, "value": []}
        return {"error": "not_found"}

    async def _fake_table_query(table_name: str, filter_str: str = "", top: int = 50):
        _ = (table_name, top)
        if "story_devops_index" in filter_str:
            return [{"LastSyncAt": "2026-03-11T10:00:00Z", "LastSyncedCount": 800, "Mode": "cursor"}]
        if "story_knowledge_index" in filter_str:
            return [{"LastSyncAt": "2026-03-11T10:19:49Z", "LastIndexedCount": 168, "LastScannedCount": 168, "Mode": "local_seed_only"}]
        return []

    monkeypatch.setattr(story_index_admin, "search_request_with_retry", _fake_search_request_with_retry)
    monkeypatch.setattr(story_index_admin, "table_query", _fake_table_query)
    monkeypatch.setattr(story_index_admin, "SEARCH_SERVICE", "your-search-service")
    monkeypatch.setattr(story_index_admin, "SEARCH_KEY", "secret")

    result = await story_index_admin.get_story_lane_index_status()

    assert result["search_service"] == "your-search-service"
    assert len(result["indexes"]) == 3
    examples = next(item for item in result["indexes"] if item["key"] == "examples")
    devops = next(item for item in result["indexes"] if item["key"] == "devops")
    knowledge = next(item for item in result["indexes"] if item["key"] == "knowledge")
    assert examples["document_count"] == 5
    assert devops["document_count"] == 800
    assert devops["mode"] == "cursor"
    assert knowledge["document_count"] == 168
    assert knowledge["mode"] == "local_seed_only"
