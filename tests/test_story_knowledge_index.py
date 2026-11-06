from __future__ import annotations

import pytest


def test_build_story_knowledge_index_document_infers_domain(monkeypatch):
    import story_knowledge_index

    monkeypatch.setattr(
        story_knowledge_index,
        "search_story_design_map",
        lambda **kwargs: {
            "dominant_domain": "Pagamentos",
            "matches": [{"domain": "Pagamentos", "title": "ACME | Pagamentos [Handoff] III", "site_placement": "Pagamentos > Transferências", "ux_terms": ["CTA", "Card"]}],
            "notes": ["Fluxo de pagamentos."],
        },
    )
    monkeypatch.setattr(
        story_knowledge_index,
        "select_story_domain_profile",
        lambda **kwargs: {"domain": "Pagamentos", "top_journeys": ["Transferências"], "top_flows": ["Recorrências"], "preferred_lexicon": ["Primary CTA"]},
    )
    monkeypatch.setattr(
        story_knowledge_index,
        "select_story_policy_pack",
        lambda **kwargs: {"domain": "Pagamentos", "top_journeys": ["Transferências"], "top_flows": ["Recorrências"], "preferred_lexicon": ["Stepper"]},
    )
    monkeypatch.setattr(
        story_knowledge_index,
        "search_story_flow_map",
        lambda **kwargs: {"matches": [{"domain": "Pagamentos", "journey": "Transferências", "flow": "Recorrências", "detail": "Resumo e confirmação", "site_placement": "Pagamentos > Transferências"}], "notes": ["Flow persistente."]},
    )

    doc = story_knowledge_index.build_story_knowledge_index_document(
        {
            "id": "site-1",
            "tag": "Mapa do site",
            "content": "O fluxo de transferências recorrentes termina num resumo com CTA primário Confirmar.",
            "url": "https://example.com/pagamentos/transferencias",
        }
    )

    assert doc["id"] == "site-1"
    assert doc["domain"] == "Pagamentos"
    assert doc["journey"] == "Transferências"
    assert doc["flow"] == "Recorrências"
    assert "CTA" in doc["ux_terms"] or "Primary CTA" in doc["ux_terms"]


@pytest.mark.asyncio
async def test_search_story_knowledge_index_prefers_matching_domain(monkeypatch):
    import story_knowledge_index

    async def _fake_embedding(_text: str):
        return [0.1, 0.2, 0.3]

    async def _fake_search_request_with_retry(**kwargs):
        if kwargs["url"].endswith("/docs/search?api-version=2023-11-01"):
            return {
                "@odata.count": 2,
                "value": [
                    {
                        "id": "site-1",
                        "title": "Mapa do site - Transferências recorrentes",
                        "content": "Pagamentos > Transferências > Recorrências.",
                        "url": "https://example.com/1",
                        "tag": "Mapa do site",
                        "domain": "Pagamentos",
                        "journey": "Transferências",
                        "flow": "Recorrências",
                        "detail": "Resumo",
                        "site_section": "Pagamentos > Transferências",
                        "ux_terms": ["CTA", "Card"],
                        "@search.score": 0.61,
                    },
                    {
                        "id": "site-2",
                        "title": "Mapa do site - Dashboard",
                        "content": "Dashboard > Agenda.",
                        "url": "https://example.com/2",
                        "tag": "Mapa do site",
                        "domain": "Dashboard",
                        "journey": "Home",
                        "flow": "Agenda",
                        "detail": "Resumo",
                        "site_section": "Dashboard",
                        "ux_terms": ["Card"],
                        "@search.score": 0.7,
                    },
                ],
            }
        raise AssertionError(f"Unexpected URL: {kwargs['url']}")

    monkeypatch.setattr(story_knowledge_index, "get_embedding", _fake_embedding)
    monkeypatch.setattr(story_knowledge_index, "search_request_with_retry", _fake_search_request_with_retry)
    monkeypatch.setattr(story_knowledge_index, "SEARCH_SERVICE", "search-test")
    monkeypatch.setattr(story_knowledge_index, "SEARCH_KEY", "secret")
    monkeypatch.setattr(story_knowledge_index, "STORY_KNOWLEDGE_INDEX", "story-knowledge-test")

    result = await story_knowledge_index.search_story_knowledge_index(
        query_text="confirmar transferência recorrente",
        dominant_domain="Pagamentos",
        top=2,
    )

    assert result["source"] == "azure_ai_search_story_knowledge"
    assert result["items"][0]["domain"] == "Pagamentos"
    assert result["items"][0]["origin"] == "azure_ai_search_story_knowledge"


@pytest.mark.asyncio
async def test_search_story_knowledge_index_falls_back_to_local_seed_when_azure_returns_empty(monkeypatch):
    import story_knowledge_index

    async def _fake_embedding(_text: str):
        return [0.1, 0.2, 0.3]

    async def _empty_search_request_with_retry(**kwargs):
        _ = kwargs
        return {"@odata.count": 0, "value": []}

    monkeypatch.setattr(story_knowledge_index, "get_embedding", _fake_embedding)
    monkeypatch.setattr(story_knowledge_index, "search_request_with_retry", _empty_search_request_with_retry)
    monkeypatch.setattr(story_knowledge_index, "SEARCH_SERVICE", "search-test")
    monkeypatch.setattr(story_knowledge_index, "STORY_KNOWLEDGE_INDEX", "story-knowledge-test")
    monkeypatch.setattr(
        story_knowledge_index,
        "_get_local_seed_documents",
        lambda: [
            {
                "id": "seed-auth-login",
                "title": "Autenticacao | login",
                "content": "Domínio Autenticação. Fluxo login com credenciais.",
                "url": "",
                "tag": "Story flow map",
                "domain": "Autenticação",
                "journey": "Autenticação",
                "flow": "login",
                "detail": "",
                "site_section": "Autenticação",
                "ux_terms": ["login", "credenciais"],
                "visibility": "global",
                "source_kind": "story_flow_map:repo",
                "source_id": "seed-auth-login",
                "source_index": "local_story_assets",
                "updated_at": "2026-03-20T00:00:00Z",
            }
        ],
    )

    result = await story_knowledge_index.search_story_knowledge_index(
        query_text="autenticação login",
        dominant_domain="Autenticação",
        top=3,
    )

    assert result["source"] == "local_story_knowledge_seed"
    assert result["items"][0]["domain"] == "Autenticação"
    assert result["items"][0]["origin"] == "local_story_knowledge_seed"


@pytest.mark.asyncio
async def test_sync_story_knowledge_index_scans_source_and_updates_state(monkeypatch):
    import story_knowledge_index

    rows = {}
    source_calls = []

    async def _fake_fetch_source_batch(*, top: int, skip: int):
        source_calls.append((top, skip))
        if skip >= 2:
            return []
        docs = [
            {"id": f"site-{skip + 1}", "tag": "Mapa do site", "content": "Fluxo de pagamentos.", "url": f"https://example.com/{skip + 1}"}
        ]
        if skip == 0:
            docs.append({"id": "site-2", "tag": "Regras do site", "content": "CTA primário de confirmação.", "url": "https://example.com/2"})
        return docs[:top]

    async def _fake_index_documents(docs: list[dict]):
        return {"ok": True, "indexed": len(docs)}

    async def _fake_table_insert(table_name: str, entity: dict):
        rows[(table_name, entity["PartitionKey"], entity["RowKey"])] = dict(entity)
        return True

    async def _fake_table_merge(table_name: str, entity: dict):
        rows[(table_name, entity["PartitionKey"], entity["RowKey"])] = dict(entity)

    monkeypatch.setattr(story_knowledge_index, "_fetch_source_batch", _fake_fetch_source_batch)
    monkeypatch.setattr(story_knowledge_index, "_index_documents", _fake_index_documents)
    monkeypatch.setattr(story_knowledge_index, "_build_local_seed_documents", lambda: [])
    monkeypatch.setattr(story_knowledge_index, "table_insert", _fake_table_insert)
    monkeypatch.setattr(story_knowledge_index, "table_merge", _fake_table_merge)
    monkeypatch.setattr(
        story_knowledge_index,
        "build_story_knowledge_index_document",
        lambda item: {"id": item["id"], "title": item.get("tag", ""), "content": item.get("content", ""), "url": item.get("url", ""), "tag": item.get("tag", ""), "domain": "Pagamentos", "journey": "Transferências", "flow": "Recorrências", "detail": "", "site_section": "Pagamentos", "ux_terms": [], "visibility": "global", "source_kind": "omni_story_knowledge_sync", "source_id": item["id"], "source_index": "omni", "updated_at": "2026-03-11T00:00:00Z"},
    )

    result = await story_knowledge_index.sync_story_knowledge_index(max_docs=3, batch_size=2, update_state=True)

    assert result["scanned"] == 2
    assert result["indexed"] == 2
    assert result["local_seeded"] == 0
    assert result["remote_scanned"] == 2
    assert source_calls[0] == (2, 0)
    assert ("IndexSyncState", "story_knowledge_index", "latest") in rows
