"""Camada B — testes de tools de pesquisa semântica."""

from __future__ import annotations

import pytest

import figma_story_map
import story_domain_profiles
import story_flow_map
import story_policy_packs


class _MockResponse:
    def __init__(self, status_code: int, data: dict, text: str = ""):
        self.status_code = status_code
        self._data = data
        self.text = text or str(data)

    def json(self):
        return self._data


@pytest.fixture(autouse=True)
def _stub_search_key(monkeypatch):
    import tools_knowledge

    monkeypatch.setattr(tools_knowledge, "SEARCH_KEY", "test-search-key")


@pytest.mark.asyncio
class TestKnowledgeTools:
    async def test_search_workitems_returns_items_with_scores(self, monkeypatch, mock_search_results):
        import tools_knowledge

        async def _fake_embedding(_text):
            return [0.1, 0.2, 0.3]

        async def _fake_search(**kwargs):
            _ = kwargs
            return {
                "value": [
                    {
                        "id": "WI-1",
                        "content": "[US] autenticação SSO para app mobile",
                        "url": "https://dev.azure.com/mock/1",
                        "tag": "User Story",
                        "status": "Active",
                        "@search.score": 0.92,
                    }
                ]
            }

        async def _identity_rerank(query, items):
            _ = query
            return items, {"applied": False}

        monkeypatch.setattr(tools_knowledge, "get_embedding", _fake_embedding)
        monkeypatch.setattr(tools_knowledge, "search_request_with_retry", _fake_search)
        monkeypatch.setattr(tools_knowledge, "_rerank_items_post_retrieval", _identity_rerank)

        result = await tools_knowledge.tool_search_workitems("autenticação SSO", top=5)
        assert result["total_results"] == 1
        assert "score" in result["items"][0]

    async def test_search_workitems_maps_story_devops_schema(self, monkeypatch):
        import tools_knowledge

        async def _fake_embedding(_text):
            return [0.1, 0.2, 0.3]

        async def _fake_search(**kwargs):
            _ = kwargs
            return {
                "value": [
                    {
                        "id": "100277",
                        "title": "ACME | Melhorias | Definição de Password | Regras",
                        "content": "Alinhamento das regras para definição de password.",
                        "url": "https://dev.azure.com/mock/100277",
                        "work_item_type": "User Story",
                        "state": "Closed",
                        "area_path": r"AcmeProduct\\Digital\\Channels\\Payments",
                        "tags": ["FIXED"],
                        "@search.score": 0.92,
                    }
                ]
            }

        async def _identity_rerank(query, items):
            _ = query
            return items, {"applied": False}

        monkeypatch.setattr(tools_knowledge, "get_embedding", _fake_embedding)
        monkeypatch.setattr(tools_knowledge, "search_request_with_retry", _fake_search)
        monkeypatch.setattr(tools_knowledge, "_rerank_items_post_retrieval", _identity_rerank)

        result = await tools_knowledge.tool_search_workitems("payments portal", top=5)
        item = result["items"][0]
        assert item["title"] == "ACME | Melhorias | Definição de Password | Regras"
        assert item["type"] == "User Story"
        assert item["status"] == "Closed"
        assert item["area"] == r"AcmeProduct\\Digital\\Channels\\Payments"
        assert item["tags"] == ["FIXED"]

    async def test_search_website_handles_empty_results(self, monkeypatch):
        import tools_knowledge

        async def _fake_embedding(_text):
            return [0.1, 0.2, 0.3]

        async def _fake_search(**kwargs):
            _ = kwargs
            return {"value": []}

        async def _identity_rerank(query, items):
            _ = query
            return items, {"applied": False}

        monkeypatch.setattr(tools_knowledge, "get_embedding", _fake_embedding)
        monkeypatch.setattr(tools_knowledge, "search_request_with_retry", _fake_search)
        monkeypatch.setattr(tools_knowledge, "_rerank_items_post_retrieval", _identity_rerank)
        monkeypatch.setattr(tools_knowledge, "_serialize_local_story_context", lambda query, top: {"items": [], "dominant_domain": "", "sources": []})

        result = await tools_knowledge.tool_search_website("zz_no_docs", top=5)
        assert result["total_results"] == 0
        assert result["items"] == []

    async def test_search_workitems_falls_back_to_story_index_when_legacy_index_is_missing(self, monkeypatch):
        import story_devops_index
        import tools_knowledge

        async def _fake_embedding(_text):
            return [0.1, 0.2, 0.3]

        async def _missing_legacy_search(**kwargs):
            _ = kwargs
            return {"error": "Search 404: index not found"}

        async def _story_search(**kwargs):
            _ = kwargs
            return {
                "items": [
                    {
                        "id": 100513,
                        "title": "REVAMPFEE MVP2 | Jornada exemplo",
                        "content": "Story semelhante do índice dedicado.",
                        "url": "https://dev.azure.com/mock/_workitems/edit/100513",
                        "type": "Feature",
                        "state": "Active",
                        "area": r"AcmeProduct\Digital\Channels\ACME",
                        "score": 0.91,
                        "origin": "azure_ai_search_story_devops",
                    }
                ],
                "total_results": 1,
                "source": "azure_ai_search_story_devops",
            }

        monkeypatch.setattr(tools_knowledge, "get_embedding", _fake_embedding)
        monkeypatch.setattr(tools_knowledge, "search_request_with_retry", _missing_legacy_search)
        monkeypatch.setattr(tools_knowledge, "_LEGACY_INDEX_AVAILABILITY", {"devops": None, "omni": None})
        monkeypatch.setattr(story_devops_index, "search_story_devops_index", _story_search)

        result = await tools_knowledge.tool_search_workitems("payments portal", top=5)
        assert result["total_results"] == 1
        assert result["items"][0]["id"] == 100513
        assert result["_fallback"]["source"] == "azure_ai_search_story_devops"

    async def test_search_website_falls_back_to_story_index_when_legacy_index_is_missing(self, monkeypatch):
        import story_knowledge_index
        import tools_knowledge

        async def _fake_embedding(_text):
            return [0.1, 0.2, 0.3]

        async def _missing_legacy_search(**kwargs):
            _ = kwargs
            return {"error": "Search 404: index not found"}

        async def _story_search(**kwargs):
            _ = kwargs
            return {
                "items": [
                    {
                        "id": "doc-1",
                        "title": "Fluxo payments portal",
                        "content": "Resumo funcional do fluxo.",
                        "url": "https://example.com/revamp-fee",
                        "tag": "Mapa funcional",
                        "score": 0.84,
                        "origin": "azure_ai_search_story_knowledge",
                    }
                ],
                "total_results": 1,
                "source": "azure_ai_search_story_knowledge",
            }

        monkeypatch.setattr(tools_knowledge, "get_embedding", _fake_embedding)
        monkeypatch.setattr(tools_knowledge, "search_request_with_retry", _missing_legacy_search)
        monkeypatch.setattr(tools_knowledge, "_LEGACY_INDEX_AVAILABILITY", {"devops": None, "omni": None})
        monkeypatch.setattr(story_knowledge_index, "search_story_knowledge_index", _story_search)
        monkeypatch.setattr(tools_knowledge, "_serialize_local_story_context", lambda query, top: {"items": [], "dominant_domain": "", "sources": []})

        result = await tools_knowledge.tool_search_website("payments portal", top=5)
        assert result["total_results"] == 1
        assert result["items"][0]["id"] == "doc-1"
        assert result["_fallback"]["source"] == "azure_ai_search_story_knowledge"

    async def test_search_website_hybrid_augments_legacy_with_local_story_context(self, monkeypatch):
        import tools_knowledge

        async def _fake_embedding(_text):
            return [0.1, 0.2, 0.3]

        async def _fake_search(**kwargs):
            _ = kwargs
            return {
                "value": [
                    {
                        "id": "legacy-1",
                        "content": "Informação genérica sobre o portal.",
                        "url": "https://example.com/portal",
                        "tag": "Portal ACME",
                        "@search.score": 0.52,
                    }
                ]
            }

        async def _identity_rerank(query, items):
            _ = query
            return items, {"applied": False}

        monkeypatch.setattr(tools_knowledge, "get_embedding", _fake_embedding)
        monkeypatch.setattr(tools_knowledge, "search_request_with_retry", _fake_search)
        monkeypatch.setattr(tools_knowledge, "_rerank_items_post_retrieval", _identity_rerank)
        monkeypatch.setattr(
            tools_knowledge,
            "_serialize_local_story_context",
            lambda query, top: {
                "dominant_domain": "Documentos",
                "sources": ["flow_map", "domain_profile"],
                "items": [
                    {
                        "id": "story-1",
                        "title": "fileUpload · Documentos",
                        "content": "Domínio Documentos. Exemplo curado. Fluxo de upload documental.",
                        "url": "",
                        "tag": "Story flow map",
                        "score": 0.68,
                        "origin": "local_story_context",
                        "domain": "Documentos",
                        "journey": "Documentos",
                        "flow": "fileUpload",
                    }
                ],
            },
        )

        result = await tools_knowledge.tool_search_website("upload de documentos", top=5)
        assert result["total_results"] >= 1
        assert result["items"][0]["id"] == "story-1"
        assert result["_hybrid"]["dominant_domain"] == "Documentos"
        assert result["_hybrid"]["local_story_items"] == 1

    async def test_search_website_uses_local_story_context_if_legacy_and_story_fallback_fail(self, monkeypatch):
        import story_knowledge_index
        import tools_knowledge

        async def _fake_embedding(_text):
            return [0.1, 0.2, 0.3]

        async def _missing_legacy_search(**kwargs):
            _ = kwargs
            return {"error": "Search 404: index not found"}

        async def _empty_story_search(**kwargs):
            _ = kwargs
            return {"items": [], "total_results": 0, "source": "azure_ai_search_story_knowledge"}

        monkeypatch.setattr(tools_knowledge, "get_embedding", _fake_embedding)
        monkeypatch.setattr(tools_knowledge, "search_request_with_retry", _missing_legacy_search)
        monkeypatch.setattr(tools_knowledge, "_LEGACY_INDEX_AVAILABILITY", {"devops": None, "omni": None})
        monkeypatch.setattr(story_knowledge_index, "search_story_knowledge_index", _empty_story_search)
        monkeypatch.setattr(
            tools_knowledge,
            "_serialize_local_story_context",
            lambda query, top: {
                "dominant_domain": "Autenticação",
                "sources": ["flow_map"],
                "items": [
                    {
                        "id": "story-login",
                        "title": "login · Autenticação",
                        "content": "Domínio Autenticação. Mapa de jornada.",
                        "url": "",
                        "tag": "Story flow map",
                        "score": 0.72,
                        "origin": "local_story_context",
                        "domain": "Autenticação",
                        "journey": "Autenticação",
                        "flow": "login",
                    }
                ],
            },
        )

        result = await tools_knowledge.tool_search_website("login e credenciais", top=5)
        assert result["total_results"] == 1
        assert result["items"][0]["id"] == "story-login"
        assert result["_fallback"]["source"] == "local_story_context"

    async def test_search_website_survives_embedding_failure_with_local_story_context(self, monkeypatch):
        import tools_knowledge

        async def _no_embedding(_text):
            return None

        async def _fake_search(**kwargs):
            assert "vectorQueries" not in kwargs["json_body"]
            return {"value": []}

        async def _identity_rerank(query, items):
            _ = query
            return items, {"applied": False}

        monkeypatch.setattr(tools_knowledge, "get_embedding", _no_embedding)
        monkeypatch.setattr(tools_knowledge, "search_request_with_retry", _fake_search)
        monkeypatch.setattr(tools_knowledge, "_rerank_items_post_retrieval", _identity_rerank)
        monkeypatch.setattr(
            tools_knowledge,
            "_serialize_local_story_context",
            lambda query, top: {
                "dominant_domain": "Documentos",
                "sources": ["flow_map"],
                "items": [
                    {
                        "id": "story-upload",
                        "title": "fileUpload · Documentos",
                        "content": "Fluxo de upload documental.",
                        "url": "",
                        "tag": "Story flow map",
                        "score": 0.7,
                        "origin": "local_story_context",
                        "domain": "Documentos",
                        "journey": "Documentos",
                        "flow": "fileUpload",
                    }
                ],
            },
        )

        result = await tools_knowledge.tool_search_website("upload de documentos", top=5)
        assert result["total_results"] == 1
        assert result["items"][0]["id"] == "story-upload"
        assert result["_fallback"]["reason"] == "embedding_unavailable"

    async def test_search_website_uses_local_story_context_on_legacy_search_error(self, monkeypatch):
        import story_knowledge_index
        import tools_knowledge

        async def _fake_embedding(_text):
            return [0.1, 0.2, 0.3]

        async def _failing_search(**kwargs):
            _ = kwargs
            return {"error": "Search 403: forbidden"}

        async def _empty_story_search(**kwargs):
            _ = kwargs
            return {"items": [], "total_results": 0, "source": "local_story_knowledge_seed"}

        monkeypatch.setattr(tools_knowledge, "get_embedding", _fake_embedding)
        monkeypatch.setattr(tools_knowledge, "search_request_with_retry", _failing_search)
        monkeypatch.setattr(story_knowledge_index, "search_story_knowledge_index", _empty_story_search)
        monkeypatch.setattr(
            tools_knowledge,
            "_serialize_local_story_context",
            lambda query, top: {
                "dominant_domain": "Recebíveis",
                "sources": ["flow_map"],
                "items": [
                    {
                        "id": "story-spin",
                        "title": "spin · Recebíveis",
                        "content": "Fluxo de recebíveis SPIN.",
                        "url": "",
                        "tag": "Story flow map",
                        "score": 0.71,
                        "origin": "local_story_context",
                        "domain": "Recebíveis",
                        "journey": "Recebíveis",
                        "flow": "spin",
                    }
                ],
            },
        )

        result = await tools_knowledge.tool_search_website("recebiveis spin", top=5)
        assert result["total_results"] == 1
        assert result["items"][0]["id"] == "story-spin"
        assert result["_fallback"]["reason"] == "legacy_search_error"

    async def test_search_website_builds_business_first_cta_guidance(self, monkeypatch):
        import tools_knowledge

        async def _no_embedding(_text):
            return None

        async def _fake_search(**kwargs):
            _ = kwargs
            return {"value": []}

        async def _identity_rerank(query, items):
            _ = query
            return items, {"applied": False}

        monkeypatch.setattr(tools_knowledge, "get_embedding", _no_embedding)
        monkeypatch.setattr(tools_knowledge, "search_request_with_retry", _fake_search)
        monkeypatch.setattr(tools_knowledge, "_rerank_items_post_retrieval", _identity_rerank)
        monkeypatch.setattr(
            tools_knowledge,
            "_serialize_local_story_context",
            lambda query, top: {
                "dominant_domain": "Transferências",
                "sources": ["flow_map", "domain_profile"],
                "ux_terms": ["CTA", "Primary CTA", "Card", "Stepper"],
                "notes": ["Fluxo específico de transferências do canal empresas."],
                "items": [
                    {
                        "id": "story-spin-transfer",
                        "title": "spinTransfers · Transferências",
                        "content": "Fluxo de transferências SPIN com resumo e confirmação.",
                        "url": "",
                        "tag": "Story flow map",
                        "score": 0.74,
                        "origin": "local_story_context",
                        "domain": "Transferências",
                        "journey": "Transferências",
                        "flow": "spinTransfers",
                        "site_placement": "Fluxos de transferências do canal empresas.",
                        "ui_components": ["CTA", "Primary CTA", "Card", "Stepper"],
                        "ux_terms": ["spinTransfers", "CTA", "Primary CTA"],
                    }
                ],
            },
        )

        result = await tools_knowledge.tool_search_website("Que CTA devo usar para esta ação de transferência SPIN?", top=5)
        assert result["_product_brief"]["response_mode"] == "business_first"
        assert "cta_guidance" in result["_product_brief"]["intents"]
        assert result["_product_brief"]["cta_guidance"]
        assert "spinTransfers" not in result["items"][0]["business_title"]
        assert "SPIN" in result["items"][0]["business_title"]

    async def test_search_website_builds_dashboard_placement_guidance(self, monkeypatch):
        import tools_knowledge

        async def _no_embedding(_text):
            return None

        async def _fake_search(**kwargs):
            _ = kwargs
            return {"value": []}

        async def _identity_rerank(query, items):
            _ = query
            return items, {"applied": False}

        monkeypatch.setattr(tools_knowledge, "get_embedding", _no_embedding)
        monkeypatch.setattr(tools_knowledge, "search_request_with_retry", _fake_search)
        monkeypatch.setattr(tools_knowledge, "_rerank_items_post_retrieval", _identity_rerank)
        monkeypatch.setattr(
            tools_knowledge,
            "_serialize_local_story_context",
            lambda query, top: {
                "dominant_domain": "Dashboard",
                "sources": ["design_map", "flow_map"],
                "ux_terms": ["CTA", "Card", "Header", "Tab"],
                "notes": ["Fluxos de Dashboard mais recentes do canal empresas."],
                "items": [
                    {
                        "id": "story-dashboard",
                        "title": "dashboard · Dashboard",
                        "content": "Resumo operacional com agenda e atalhos.",
                        "url": "",
                        "tag": "Figma handoff",
                        "score": 0.77,
                        "origin": "local_story_context",
                        "domain": "Dashboard",
                        "journey": "dashboard",
                        "flow": "dashboard",
                        "site_placement": "Fluxos de Dashboard mais recentes do canal empresas.",
                        "ui_components": ["CTA", "Card", "Header", "Tab"],
                        "ux_terms": ["dashboard", "CTA", "Card"],
                    }
                ],
            },
        )

        result = await tools_knowledge.tool_search_website("Nesta página do dashboard onde encaixa esta ação?", top=5)
        assert "placement_guidance" in result["_product_brief"]["intents"]
        assert result["_product_brief"]["placement_guidance"]
        assert any("dashboard" in item.lower() or "próxima melhor ação" in item.lower() for item in result["_product_brief"]["placement_guidance"])
        assert result["items"][0]["business_summary"]

    async def test_local_story_context_prefers_flow_domain_over_design_domain(self, monkeypatch):
        import tools_knowledge

        monkeypatch.setattr(
            figma_story_map,
            "search_story_design_map",
            lambda **kwargs: {
                "dominant_domain": "Cartões",
                "matches": [{"title": "Cards Board", "domain": "Cartões", "journeys": ["Via Verde"], "routing_note": "Design antigo"}],
            },
        )
        monkeypatch.setattr(
            figma_story_map,
            "serialize_design_match",
            lambda entry: {
                "key": "figma:cards",
                "title": entry["title"],
                "snippet": "Design antigo",
                "url": "",
                "score": 0.7,
                "domain": entry["domain"],
            },
        )
        monkeypatch.setattr(
            story_flow_map,
            "search_story_flow_map",
            lambda **kwargs: {
                "dominant_domain": "Operações",
                "matches": [
                    {
                        "id": "flow-op",
                        "domain": "Operações",
                        "journey": "Operações",
                        "flow": "digitalSignatureAuthorizedOperation",
                        "score": 0.88,
                    }
                ],
            },
        )
        monkeypatch.setattr(
            story_flow_map,
            "serialize_story_flow_match",
            lambda entry: {
                "key": "story-flow:flow-op",
                "title": "digitalSignatureAuthorizedOperation · Operações",
                "snippet": "Flow de operações.",
                "url": "",
                "score": 0.88,
                "domain": entry["domain"],
                "page_name": entry["journey"],
                "frame_name": entry["flow"],
            },
        )
        monkeypatch.setattr(
            story_domain_profiles,
            "select_story_domain_profile",
            lambda **kwargs: {
                "domain": kwargs.get("dominant_domain") or "Operações",
                "top_journeys": ["Operações"],
                "top_flows": ["digitalSignatureAuthorizedOperation"],
                "routing_notes": ["Repo atlas aponta para Operation."],
                "production_confidence": 0.9,
                "coverage_score": 0.8,
                "score": 0.91,
            },
        )
        monkeypatch.setattr(
            story_policy_packs,
            "select_story_policy_pack",
            lambda **kwargs: {"domain": kwargs.get("dominant_domain") or "Operações"},
        )

        result = tools_knowledge._serialize_local_story_context("assinatura digital", top=3)

        assert result["dominant_domain"] == "Operações"
        assert result["items"][0]["domain"] == "Operações"

    async def test_search_uploaded_document_topk_ordering(self, monkeypatch):
        import tools_upload

        async def _fake_load_indexed_chunks(conv_id, user_sub=""):
            _ = (conv_id, user_sub)
            return [
                (
                    "spec.pdf",
                    {"index": 0, "start": 0, "end": 100, "embedding": [1.0, 0.0], "text": "auth sso"},
                ),
                (
                    "spec.pdf",
                    {"index": 1, "start": 101, "end": 200, "embedding": [0.8, 0.2], "text": "deploy"},
                ),
                (
                    "spec.pdf",
                    {"index": 2, "start": 201, "end": 300, "embedding": [0.0, 1.0], "text": "outro"},
                ),
            ]

        async def _fake_embedding(_text):
            return [1.0, 0.0]

        monkeypatch.setattr(tools_upload, "_load_indexed_chunks", _fake_load_indexed_chunks)
        monkeypatch.setattr(tools_upload, "get_embedding", _fake_embedding)

        result = await tools_upload.tool_search_uploaded_document("auth", conv_id="conv-1")
        assert result["total_results"] >= 1
        scores = [item["score"] for item in result["items"]]
        assert scores == sorted(scores, reverse=True)

    async def test_reranking_reorders_items(self, monkeypatch):
        import tools_knowledge

        class _FakeClient:
            async def post(self, url, headers=None, json=None):
                _ = (url, headers, json)
                return _MockResponse(
                    200,
                    {
                        "results": [
                            {"index": 1, "relevance_score": 0.95},
                            {"index": 0, "relevance_score": 0.51},
                        ]
                    },
                )

        monkeypatch.setattr(tools_knowledge, "RERANK_ENABLED", True)
        monkeypatch.setattr(tools_knowledge, "RERANK_ENDPOINT", "https://mock-rerank")
        monkeypatch.setattr(tools_knowledge, "RERANK_AUTH_MODE", "")
        monkeypatch.setattr(tools_knowledge, "RERANK_API_KEY", "")
        monkeypatch.setattr(tools_knowledge, "_get_http_client", lambda: _FakeClient())

        items = [
            {"id": "A", "content": "item A"},
            {"id": "B", "content": "item B"},
        ]
        ranked, meta = await tools_knowledge._rerank_items_post_retrieval("query", items)
        assert meta.get("applied") is True
        assert ranked[0]["id"] == "B"

    async def test_embedding_timeout_or_failure_returns_error(self, monkeypatch):
        import tools_knowledge

        async def _no_embedding(_text):
            return None

        monkeypatch.setattr(tools_knowledge, "get_embedding", _no_embedding)
        result = await tools_knowledge.tool_search_workitems("qualquer", top=5)
        assert "error" in result
