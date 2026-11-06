from __future__ import annotations

from story_curated_corpus import build_curated_story_entry, search_curated_story_examples


def test_build_curated_story_entry_extracts_sections_and_terms():
    entry = build_curated_story_entry(
        {
            "ID": "123",
            "Work Item Type": "User Story",
            "Title": "Revamp | Pagamentos | Transferências | Recorrências | Confirmar operação",
            "Created By": "Mary Johnson",
            "Description": "<div>Como utilizador quero <b>confirmar</b> a operação através de um CTA primário.</div>",
            "Acceptance Criteria": (
                "<div><b>Proveniência</b></div><ul><li>User Story 456789</li></ul>"
                "<div><b>Comportamento</b></div><ul><li>Mostrar card resumo e stepper.</li></ul>"
            ),
        }
    )

    assert entry["domain"] == "Pagamentos"
    assert entry["journey"] == "Transferências"
    assert 456789 in entry["workitem_refs"]
    assert "CTA" in entry["ux_terms"]
    assert "proveniência" in entry["sections"]
    assert "comportamento" in entry["sections"]


def test_search_curated_story_examples_prefers_matching_domain(monkeypatch):
    import story_curated_corpus

    monkeypatch.setattr(
        story_curated_corpus,
        "_load_curated_corpus_payload",
        lambda: {
            "metadata": {"count": 2},
            "entries": [
                {
                    "id": "1",
                    "title": "Revamp | Pagamentos | Transferências | Recorrências | Confirmar operação",
                    "domain": "Pagamentos",
                    "journey": "Transferências",
                    "flow": "Recorrências",
                    "created_by": "Mary Johnson",
                    "title_pattern": "[Prefix] | [Domínio] | [Jornada/Subárea] | [Fluxo/Step] | [Detalhe]",
                    "ux_terms": ["CTA", "Card"],
                    "sections": {"proveniência": "Pagamentos", "comportamento": "Card resumo"},
                    "search_tokens": ["pagamentos", "transferencias", "recorrencias", "confirmar", "cta", "card"],
                    "quality_score": 0.9,
                    "url": "https://example.com/1",
                },
                {
                    "id": "2",
                    "title": "Revamp | Cartões | Oferta | Detalhe | Ver cartão",
                    "domain": "Cartões",
                    "journey": "Oferta",
                    "flow": "Detalhe",
                    "created_by": "Beatriz Melo",
                    "title_pattern": "[Prefix] | [Domínio] | [Jornada/Subárea] | [Fluxo/Step] | [Detalhe]",
                    "ux_terms": ["CTA"],
                    "sections": {"proveniência": "Cartões"},
                    "search_tokens": ["cartoes", "oferta", "detalhe", "cartao", "cta"],
                    "quality_score": 0.82,
                    "url": "https://example.com/2",
                },
            ],
        },
    )

    result = search_curated_story_examples(
        objective="Quero confirmar uma transferência recorrente",
        context="O utilizador vê um card resumo e um CTA de confirmação",
        dominant_design_domain="Pagamentos",
        top=1,
    )

    assert result["matches"]
    assert result["matches"][0]["domain"] == "Pagamentos"
    assert "CTA" in result["preferred_lexicon"]
