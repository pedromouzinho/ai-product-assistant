from __future__ import annotations

import pytest

import story_examples_index


def test_build_story_example_index_document_includes_curated_fields():
    doc = story_examples_index.build_story_example_index_document(
        draft_id="draft-1",
        entry={
            "title": "ACME | Pagamentos | Transferências | Recorrências | Confirmar recorrência",
            "domain": "Pagamentos",
            "journey": "Transferências",
            "flow": "Recorrências",
            "detail": "Confirmar recorrência",
            "title_pattern": "[Prefix] | [Domínio] | [Jornada/Subárea] | [Fluxo/Step] | [Detalhe]",
            "description_text": "Resumo em card e CTA primário.",
            "acceptance_text": "CA-01: CTA ativo com dados válidos.",
            "sections": {"comportamento": "Mostrar resumo final antes da submissão."},
            "ux_terms": ["CTA", "Card"],
            "tags": ["AI-Curated", "Promoted"],
            "workitem_refs": [100135],
            "quality_score": 0.91,
            "search_text": "pagamentos transferências recorrências CTA card",
            "url": "https://dev.azure.com/mock/_workitems/edit/100135",
            "source_user_sub": "jane",
            "promoted_by": "admin",
        },
        row={"Status": "active", "AreaPath": r"AcmeProduct\Digital\Channels\ACME"},
    )

    assert doc["id"] == "draft-1"
    assert doc["status"] == "active"
    assert doc["visibility"] == "global"
    assert doc["domain"] == "Pagamentos"
    assert "CTA" in doc["ux_terms"]
    assert "100135" in doc["workitem_refs"]
    assert "card" in doc["search_text"].lower()


@pytest.mark.asyncio
async def test_search_story_examples_index_prefers_dominant_domain(monkeypatch):
    monkeypatch.setattr(story_examples_index, "SEARCH_SERVICE", "mock-search")
    monkeypatch.setattr(story_examples_index, "SEARCH_KEY", "mock-key")
    monkeypatch.setattr(story_examples_index, "STORY_EXAMPLES_INDEX", "story-examples-v2")

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
                    "domain": "Dashboard",
                    "journey": "Agenda",
                    "flow": "Resumo",
                    "detail": "Atualizar agenda",
                    "title_pattern": "[Prefix] | [Domínio] | [Jornada/Subárea] | [Fluxo/Step] | [Detalhe]",
                    "description_text": "Cards e agenda.",
                    "acceptance_text": "CA-01",
                    "sections_json": "{\"comportamento\": \"Mostrar agenda\"}",
                    "ux_terms": ["Card"],
                    "tags": ["AI-Curated"],
                    "workitem_refs": ["123"],
                    "quality_score": 0.9,
                    "url": "https://example.com/dash-1",
                    "source_draft_id": "dash-1",
                    "source_user_sub": "maria",
                    "@search.score": 0.82,
                },
                {
                    "id": "pay-1",
                    "title": "ACME | Pagamentos | Transferências | Recorrências | Confirmar recorrência",
                    "domain": "Pagamentos",
                    "journey": "Transferências",
                    "flow": "Recorrências",
                    "detail": "Confirmar recorrência",
                    "title_pattern": "[Prefix] | [Domínio] | [Jornada/Subárea] | [Fluxo/Step] | [Detalhe]",
                    "description_text": "CTA primário e card de resumo.",
                    "acceptance_text": "CA-01",
                    "sections_json": "{\"comportamento\": \"Mostrar resumo final\"}",
                    "ux_terms": ["CTA", "Card"],
                    "tags": ["AI-Curated"],
                    "workitem_refs": ["100135"],
                    "quality_score": 0.88,
                    "url": "https://example.com/pay-1",
                    "source_draft_id": "pay-1",
                    "source_user_sub": "jane",
                    "@search.score": 0.72,
                },
            ],
        }

    monkeypatch.setattr(story_examples_index, "get_embedding", _fake_embedding)
    monkeypatch.setattr(story_examples_index, "search_request_with_retry", _fake_search_request_with_retry)

    result = await story_examples_index.search_story_examples_index(
        query_text="preciso de uma user story para pagamentos recorrentes",
        dominant_domain="Pagamentos",
        top=2,
    )

    assert result["promoted_count"] == 2
    assert result["matches"][0]["domain"] == "Pagamentos"
    assert result["matches"][0]["origin"] == "promoted_curated_story"
