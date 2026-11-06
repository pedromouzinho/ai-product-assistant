from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import learning


def test_looks_like_missing_examples_index_matches_index_404():
    detail = (
        "Search 404: The index 'examples-index' for service "
        "'your-search-service' was not found."
    )
    assert learning._looks_like_missing_examples_index(detail) is True


@pytest.mark.asyncio
async def test_search_examples_semantic_skips_when_index_temporarily_disabled(monkeypatch):
    called = {"count": 0}

    async def fake_request(**_kwargs):
        called["count"] += 1
        return {"value": []}

    monkeypatch.setattr(learning, "search_request_with_retry", fake_request)
    learning._examples_index_unavailable_until = datetime.now(timezone.utc) + timedelta(minutes=5)
    try:
        result = await learning._search_examples_semantic([0.1, 0.2], top=1)
    finally:
        learning._examples_index_unavailable_until = None

    assert result == []
    assert called["count"] == 0


@pytest.mark.asyncio
async def test_search_examples_semantic_disables_index_after_missing_index_error(monkeypatch):
    async def fake_request(**_kwargs):
        return {
            "error": (
                "Search 404: The index 'examples-index' for service "
                "'your-search-service' was not found."
            )
        }

    monkeypatch.setattr(learning, "search_request_with_retry", fake_request)
    learning._examples_index_unavailable_until = None
    try:
        result = await learning._search_examples_semantic([0.1, 0.2], top=1)
        disabled_until = learning._examples_index_unavailable_until
    finally:
        learning._examples_index_unavailable_until = None

    assert result == []
    assert disabled_until is not None
    assert disabled_until > datetime.now(timezone.utc)
