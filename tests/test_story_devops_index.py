from __future__ import annotations

import pytest

import story_devops_index


def test_build_story_devops_index_document_extracts_parent_and_title_segments():
    doc = story_devops_index.build_story_devops_index_document(
        {
            "id": 100513,
            "fields": {
                "System.Title": "ACME | Pagamentos | Transferências | Recorrências | Configurar recorrência",
                "System.WorkItemType": "Feature",
                "System.State": "Active",
                "System.AreaPath": r"AcmeProduct\Digital\Channels\ACME",
                "System.IterationPath": "ACME\\Sprint 23",
                "System.Tags": "Pagamentos;Recorrências",
                "System.Description": "<div>Configurar card resumo e CTA primário.</div>",
                "Microsoft.VSTS.Common.AcceptanceCriteria": "<div>CA-01 CTA ativo.</div>",
                "System.Parent": 100886,
                "System.AssignedTo": {"displayName": "Mary Johnson"},
                "System.CreatedBy": {"displayName": "Jane Doe"},
                "System.CreatedDate": "2026-03-01T10:00:00Z",
                "System.ChangedDate": "2026-03-10T12:00:00Z",
            },
        },
        parent_lookup={"100886": {"id": "100886", "title": "ACME — Pagamentos", "type": "Epic"}},
    )

    assert doc["id"] == "100513"
    assert doc["work_item_type"] == "Feature"
    assert doc["parent_id"] == "100886"
    assert doc["parent_title"] == "ACME — Pagamentos"
    assert doc["domain"] == "Pagamentos"
    assert doc["journey"] == "Transferências"
    assert "CTA primário" in doc["content"]


@pytest.mark.asyncio
async def test_search_story_devops_index_prefers_matching_domain(monkeypatch):
    monkeypatch.setattr(story_devops_index, "SEARCH_SERVICE", "mock-search")
    monkeypatch.setattr(story_devops_index, "SEARCH_KEY", "mock-key")
    monkeypatch.setattr(story_devops_index, "STORY_DEVOPS_INDEX", "story-devops-v2")

    async def _fake_embedding(text: str):
        _ = text
        return [0.1, 0.2, 0.3]

    async def _fake_search_request_with_retry(**kwargs):
        _ = kwargs
        return {
            "@odata.count": 2,
            "value": [
                {
                    "id": "dash-1",
                    "title": "ACME | Dashboard | Agenda | Resumo | Atualizar agenda",
                    "content": "Cards e agenda",
                    "url": "https://example.com/dash-1",
                    "work_item_type": "Feature",
                    "state": "Active",
                    "area_path": "ACME",
                    "parent_id": "100",
                    "parent_title": "ACME | Dashboard",
                    "parent_type": "Epic",
                    "domain": "Dashboard",
                    "journey": "Agenda",
                    "flow": "Resumo",
                    "detail": "Atualizar agenda",
                    "@search.score": 0.84,
                },
                {
                    "id": "pay-1",
                    "title": "ACME | Pagamentos | Transferências | Recorrências | Confirmar recorrência",
                    "content": "CTA primário, card resumo e validações",
                    "url": "https://example.com/pay-1",
                    "work_item_type": "Feature",
                    "state": "Active",
                    "area_path": "ACME",
                    "parent_id": "200",
                    "parent_title": "ACME | Pagamentos",
                    "parent_type": "Epic",
                    "domain": "Pagamentos",
                    "journey": "Transferências",
                    "flow": "Recorrências",
                    "detail": "Confirmar recorrência",
                    "@search.score": 0.72,
                },
            ],
        }

    monkeypatch.setattr(story_devops_index, "get_embedding", _fake_embedding)
    monkeypatch.setattr(story_devops_index, "search_request_with_retry", _fake_search_request_with_retry)

    result = await story_devops_index.search_story_devops_index(
        query_text="preciso de uma feature para pagamentos recorrentes",
        dominant_domain="Pagamentos",
        top=2,
    )

    assert result["total_results"] == 2
    assert result["items"][0]["type"] == "Feature"
    assert result["items"][0]["origin"] == "azure_ai_search_story_devops"
    assert "Pagamentos" in result["items"][0]["title"]


@pytest.mark.asyncio
async def test_sync_story_devops_index_uses_cursor_and_updates_state(monkeypatch):
    saved_state = {}

    async def _fake_load_sync_state():
        return {"LastChangedAt": "2026-03-01T00:00:00+00:00"}

    async def _fake_query_changed_workitem_ids(*, since_iso: str, top: int = 1200, area_paths=None):
        assert since_iso == "2026-03-01T00:00:00+00:00"
        _ = (top, area_paths)
        return [100513]

    async def _fake_fetch_workitems(ids):
        assert ids == [100513]
        return [
            {
                "id": 100513,
                "fields": {
                    "System.Title": "ACME | Pagamentos | Transferências | Recorrências | Configurar recorrência",
                    "System.WorkItemType": "Feature",
                    "System.State": "Active",
                    "System.AreaPath": r"AcmeProduct\Digital\Channels\ACME",
                    "System.IterationPath": "ACME\\Sprint 23",
                    "System.Tags": "Pagamentos;Recorrências",
                    "System.Description": "<div>Configurar CTA primário.</div>",
                    "Microsoft.VSTS.Common.AcceptanceCriteria": "<div>CA-01</div>",
                    "System.Parent": 100886,
                    "System.CreatedDate": "2026-03-01T10:00:00Z",
                    "System.ChangedDate": "2026-03-10T12:00:00Z",
                },
            }
        ]

    async def _fake_fetch_parent_lookup(parent_ids):
        assert parent_ids == [100886]
        return {"100886": {"id": "100886", "title": "ACME — Pagamentos", "type": "Epic"}}

    async def _fake_index_documents(docs):
        assert len(docs) == 1
        assert docs[0]["parent_title"] == "ACME — Pagamentos"
        return {"ok": True, "indexed": 1}

    async def _fake_save_sync_state(*, last_changed_at: str, synced_count: int, mode: str):
        saved_state.update(
            {
                "last_changed_at": last_changed_at,
                "synced_count": synced_count,
                "mode": mode,
            }
        )

    monkeypatch.setattr(story_devops_index, "_load_sync_state", _fake_load_sync_state)
    monkeypatch.setattr(story_devops_index, "_query_changed_workitem_ids", _fake_query_changed_workitem_ids)
    monkeypatch.setattr(story_devops_index, "_fetch_workitems", _fake_fetch_workitems)
    monkeypatch.setattr(story_devops_index, "_fetch_parent_lookup", _fake_fetch_parent_lookup)
    monkeypatch.setattr(story_devops_index, "_index_documents", _fake_index_documents)
    monkeypatch.setattr(story_devops_index, "_save_sync_state", _fake_save_sync_state)

    summary = await story_devops_index.sync_story_devops_index(top=50)

    assert summary["matched_ids"] == 1
    assert summary["indexed"] == 1
    assert summary["mode"] == "cursor"
    assert saved_state["synced_count"] == 1
    assert saved_state["last_changed_at"] == "2026-03-10T12:00:00Z"
