import pytest
from fastapi import HTTPException

import routes_chat as chat_module
from models import UpdateChatTitleRequest


@pytest.mark.asyncio
async def test_update_chat_title_updates_existing_conversation(monkeypatch):
    merged = {}

    async def _fake_table_query(table, filter_expr, top=1):
        assert table == "ChatHistory"
        assert "conv-1" in filter_expr
        return [{"PartitionKey": "user-1", "RowKey": "conv-1", "Title": "Antigo"}]

    async def _fake_table_merge(table, entity):
        merged["table"] = table
        merged["entity"] = entity
        return True

    monkeypatch.setattr(chat_module, "get_current_user", lambda _credentials=None: {"sub": "user-1", "role": "user"})
    monkeypatch.setattr(chat_module, "table_query", _fake_table_query)
    monkeypatch.setattr(chat_module, "table_merge", _fake_table_merge)

    result = await chat_module.update_chat_title(
        None,
        "ignored-user",
        "conv-1",
        UpdateChatTitleRequest(title="Novo título"),
        credentials=None,
    )

    assert result["status"] == "ok"
    assert result["title"] == "Novo título"
    assert merged["table"] == "ChatHistory"
    assert merged["entity"]["PartitionKey"] == "user-1"
    assert merged["entity"]["RowKey"] == "conv-1"
    assert merged["entity"]["Title"] == "Novo título"
    assert merged["entity"]["UpdatedAt"]


@pytest.mark.asyncio
async def test_update_chat_title_returns_404_when_conversation_missing(monkeypatch):
    async def _fake_table_query(table, filter_expr, top=1):
        return []

    monkeypatch.setattr(chat_module, "get_current_user", lambda _credentials=None: {"sub": "user-1", "role": "user"})
    monkeypatch.setattr(chat_module, "table_query", _fake_table_query)

    with pytest.raises(HTTPException) as exc:
        await chat_module.update_chat_title(
            None,
            "user-1",
            "missing-conv",
            UpdateChatTitleRequest(title="Novo título"),
            credentials=None,
        )

    assert exc.value.status_code == 404
