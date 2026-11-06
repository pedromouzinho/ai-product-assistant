import json
import asyncio

import presentation_briefing
import presentation_runtime
import pytest
from presentation_briefing import build_brief_shaping_prompt, shape_prepared_run
from presentation_orchestrator import (
    AudienceClass,
    DeckCapability,
    EvidenceKind,
    PresentationRequest,
    build_grounded_brief,
    build_grounded_brief_from_sources,
    classify_presentation_request,
    infer_evidence_items_from_text,
    review_slide_plan,
)
from presentation_pipeline import evaluate_presentation_plan, prepare_presentation_run
from presentation_planning import build_critic_rubric, build_legacy_render_payload, build_planner_packet
from presentation_sources import build_presentation_sources, load_conversation_presentation_sources
from presentation_runtime import build_model_planner_prompt, run_planning_loop, _parse_slide_specs
from presentation_review import PlannedSlide, build_repair_packet, review_planned_slides
from presentation_reasoning import PresentationSource, SourceKind, select_authoritative_sources


def test_classify_benchmark_request():
    request = PresentationRequest(
        title="Benchmark Neobank Business vs Acme Business",
        prompt="Faz-me um PowerPoint para a comissão executiva com base neste benchmark.",
    )
    intent = classify_presentation_request(request)
    assert intent.capability == DeckCapability.BENCHMARK_EXECUTIVE
    assert intent.audience == AudienceClass.EXECUTIVE


def test_classify_backlog_request():
    request = PresentationRequest(
        title="Payments",
        prompt="Gera uma apresentação sobre o estado atual do backlog com métricas, bloqueios e próximos passos.",
    )
    intent = classify_presentation_request(request)
    assert intent.capability == DeckCapability.BACKLOG_STEERING


def test_build_grounded_brief_defaults():
    request = PresentationRequest(
        title="Deck Estratégico",
        prompt="Resumo estratégico.",
    )
    intent = classify_presentation_request(
        PresentationRequest(title="Roadmap estratégico", prompt="Definir visão e roadmap")
    )
    brief = build_grounded_brief(request, intent)
    assert brief.capability == intent.capability
    assert brief.central_message
    assert brief.narrative_arc


def test_review_rejects_text_only_benchmark():
    review = review_slide_plan(
        capability=DeckCapability.BENCHMARK_EXECUTIVE,
        slide_types=["title", "content", "content", "closing"],
        titles=["T", "A", "B", "C"],
        bullet_counts=[0, 5, 4, 0],
    )
    assert not review.approved
    codes = {issue.code for issue in review.issues}
    assert "text_only" in codes
    assert "missing_chart" in codes
    assert "missing_comparison" in codes


def test_infer_evidence_items_from_text():
    content = (
        "Readiness média: 36%\n"
        "Neobank vs Acme em pagamentos\n"
        "Prioridade recomendada: reforçar integração\n"
    )
    items = infer_evidence_items_from_text(content, source_name="benchmark.pdf")
    kinds = {item.kind for item in items}
    assert EvidenceKind.METRIC in kinds
    assert EvidenceKind.COMPARISON in kinds
    assert EvidenceKind.DECISION in kinds


def test_select_authoritative_sources_prefers_primary_pdf():
    sources = [
        PresentationSource(
            name="Benchmark Neobank Business vs Acme Business-8.pptx",
            content="Slide generated output with repeated text",
            kind=SourceKind.PPTX,
            is_generated_artifact=True,
        ),
        PresentationSource(
            name="Benchmark - Neobank Business.pdf",
            content="Readiness média: 36%\nNeobank vs Acme em pagamentos",
            kind=SourceKind.PDF,
        ),
    ]
    selected = select_authoritative_sources(sources, capability="benchmark_executive")
    assert selected[0].name.endswith(".pdf")


def test_build_grounded_brief_from_sources_uses_selected_sources():
    request = PresentationRequest(
        title="Benchmark Neobank Business vs Acme Business",
        prompt="Faz-me um PowerPoint para a comissão executiva com base neste benchmark.",
        source_names=("Benchmark - Neobank Business.pdf", "Benchmark Neobank Business vs Acme Business-8.pptx"),
    )
    sources = [
        PresentationSource(
            name="Benchmark Neobank Business vs Acme Business-8.pptx",
            content="Deck antigo gerado automaticamente com texto repetido",
            kind=SourceKind.PPTX,
            is_generated_artifact=True,
        ),
        PresentationSource(
            name="Benchmark - Neobank Business.pdf",
            content=(
                "Readiness média: 36%\n"
                "Pagamentos: 56%\n"
                "APIs: 31%\n"
                "Neobank vs Acme em pagamentos\n"
                "Prioridade recomendada: reforçar integração\n"
            ),
            kind=SourceKind.PDF,
        ),
    ]
    brief = build_grounded_brief_from_sources(request, sources)
    assert brief.capability == DeckCapability.BENCHMARK_EXECUTIVE
    assert brief.source_names[0].endswith(".pdf")
    assert "Primary sources selected" in brief.source_selection_note
    assert any(item.kind == EvidenceKind.METRIC for item in brief.evidence)


def test_build_planner_packet_contains_grounded_constraints():
    request = PresentationRequest(
        title="Benchmark Neobank Business vs Acme Business",
        prompt="Faz-me um PowerPoint para a comissão executiva com base neste benchmark.",
    )
    sources = [
        PresentationSource(
            name="Benchmark - Neobank Business.pdf",
            content=(
                "Readiness média: 36%\n"
                "Pagamentos: 56%\n"
                "Onboarding: 31%\n"
                "Neobank vs Acme em pagamentos\n"
                "Prioridade recomendada: reforçar integração\n"
            ),
            kind=SourceKind.PDF,
        ),
    ]
    brief = build_grounded_brief_from_sources(request, sources)
    packet = build_planner_packet(brief)
    assert "SOURCE AUTHORITY:" in packet.planning_brief_text
    assert "FORBIDDEN PATTERNS:" in packet.planning_brief_text
    assert "invented metrics" in packet.planning_brief_text
    assert "chart" in packet.required_visuals


def test_build_model_planner_prompt_defaults_to_portuguese_for_portuguese_request():
    request = PresentationRequest(
        title="Benchmark Neobank Business vs Acme Business",
        prompt="Faz-me um PowerPoint para a comissão executiva com base neste benchmark.",
        content="Análise comparativa e implicações estratégicas para a nova app empresas.",
    )
    sources = [
        PresentationSource(
            name="Benchmark - Neobank Business.pdf",
            content="Readiness média: 36%\nNeobank vs Acme em pagamentos\n",
            kind=SourceKind.PDF,
        ),
    ]
    prepared = prepare_presentation_run(request, sources)
    prompt = build_model_planner_prompt(prepared)
    assert "OUTPUT LANGUAGE: Portuguese (Portugal)." in prompt
    assert "Do not invent percentages" in prompt


def test_parse_slide_specs_splits_pipe_separated_bullets():
    raw = json.dumps(
        {
            "slides": [
                {
                    "type": "content",
                    "title": "Diagnóstico",
                    "notes": "Notas",
                    "bullets": [
                        "Funcionalidade base disponível | Suporte internacional limitado | Sem instant payments"
                    ],
                    "items": None,
                    "kpis": None,
                    "chart_type": None,
                    "categories": None,
                    "series": None,
                    "left_title": None,
                    "left_items": None,
                    "right_title": None,
                    "right_items": None,
                    "headers": None,
                    "rows": None,
                    "steps": None,
                    "stat_value": None,
                    "stat_label": None,
                    "image_url": None,
                    "caption": None,
                }
            ]
        },
        ensure_ascii=False,
    )
    specs = _parse_slide_specs(raw)
    assert list(specs[0]["bullets"]) == [
        "Funcionalidade base disponível",
        "Suporte internacional limitado",
        "Sem instant payments",
    ]


def test_parse_slide_specs_salvages_stat_chart_from_kpis():
    raw = json.dumps(
        {
            "slides": [
                {
                    "type": "stat_chart",
                    "title": "Capacidade multimoeda",
                    "notes": "Notas",
                    "bullets": None,
                    "items": None,
                    "kpis": [
                        {
                            "value": "35+",
                            "label": "Moedas suportadas",
                            "description": "Número de moedas disponíveis",
                        }
                    ],
                    "chart_type": "bar",
                    "categories": ["Moedas", "Pagamentos"],
                    "series": [{"name": "Valores", "values": [35, 100]}],
                    "left_title": None,
                    "left_items": None,
                    "right_title": None,
                    "right_items": None,
                    "headers": None,
                    "rows": None,
                    "steps": None,
                    "stat_value": None,
                    "stat_label": None,
                    "image_url": None,
                    "caption": None,
                }
            ]
        },
        ensure_ascii=False,
    )
    specs = _parse_slide_specs(raw)
    assert specs[0]["stat_value"] == "35+"
    assert specs[0]["stat_label"] == "Moedas suportadas"


def test_parse_slide_specs_salvages_comparison_from_items():
    raw = json.dumps(
        {
            "slides": [
                {
                    "type": "comparison",
                    "title": "Comparação funcional",
                    "notes": "Notas",
                    "bullets": None,
                    "items": [
                        "Neobank Business: Conta multimoeda, Instant payments, Exportação",
                        "Acme: Conta básica, Sem instant payments, Funcionalidades limitadas",
                    ],
                    "kpis": None,
                    "chart_type": None,
                    "categories": None,
                    "series": None,
                    "left_title": None,
                    "left_items": None,
                    "right_title": None,
                    "right_items": None,
                    "headers": None,
                    "rows": None,
                    "steps": None,
                    "stat_value": None,
                    "stat_label": None,
                    "image_url": None,
                    "caption": None,
                }
            ]
        },
        ensure_ascii=False,
    )
    specs = _parse_slide_specs(raw)
    assert specs[0]["left_title"] == "Neobank Business"
    assert specs[0]["right_title"] == "Acme"
    assert specs[0]["left_items"][:2] == ["Conta multimoeda", "Instant payments"]


def test_build_critic_rubric_for_benchmark():
    request = PresentationRequest(
        title="Benchmark Neobank Business vs Acme Business",
        prompt="Faz-me um PowerPoint para a comissão executiva com base neste benchmark.",
    )
    sources = [
        PresentationSource(
            name="Benchmark - Neobank Business.pdf",
            content="Readiness média: 36%\nNeobank vs Acme em pagamentos\n",
            kind=SourceKind.PDF,
        ),
    ]
    brief = build_grounded_brief_from_sources(request, sources)
    rubric = build_critic_rubric(brief)
    assert any("numeric claim" in item.lower() for item in rubric.grounding_checks)
    assert any("chart" in item.lower() for item in rubric.repair_priorities)


def test_build_legacy_render_payload_uses_compact_grounded_content():
    request = PresentationRequest(
        title="Benchmark Neobank Business vs Acme Business",
        prompt="Faz-me um PowerPoint para a comissão executiva com base neste benchmark.",
    )
    sources = [
        PresentationSource(
            name="Benchmark - Neobank Business.pdf",
            content=(
                "Readiness média: 36%\n"
                "Pagamentos: 56%\n"
                "APIs: 31%\n"
                "Neobank vs Acme em pagamentos\n"
                "Prioridade recomendada: reforçar integração\n"
            ),
            kind=SourceKind.PDF,
        ),
    ]
    brief = build_grounded_brief_from_sources(request, sources)
    packet = build_planner_packet(brief)
    payload = build_legacy_render_payload(request.title, brief, packet)
    assert "MENSAGEM CENTRAL:" in payload.content
    assert "EVIDÊNCIA ESTRUTURADA:" in payload.content
    assert "CONTEÚDO INTEGRAL DOS DOCUMENTOS" not in payload.content
    assert "Source authority:" in payload.context


def test_review_planned_slides_flags_text_only_benchmark_plan():
    request = PresentationRequest(
        title="Benchmark Neobank Business vs Acme Business",
        prompt="Faz-me um PowerPoint para a comissão executiva com base neste benchmark.",
    )
    sources = [
        PresentationSource(
            name="Benchmark - Neobank Business.pdf",
            content="Readiness média: 36%\nNeobank vs Acme em pagamentos\n",
            kind=SourceKind.PDF,
        ),
    ]
    brief = build_grounded_brief_from_sources(request, sources)
    rubric = build_critic_rubric(brief)
    review = review_planned_slides(
        [
            PlannedSlide("title", "Benchmark"),
            PlannedSlide("content", "Contexto", bullets=("Ponto 1", "Ponto 2")),
            PlannedSlide("content", "Impacto", bullets=("Ponto 3", "Ponto 4")),
            PlannedSlide("closing", "Obrigado"),
        ],
        rubric,
    )
    assert not review.approved
    codes = {finding.code for finding in review.findings}
    assert "text_only" in codes
    assert "missing_chart" in codes
    assert "missing_comparison" in codes
    assert not review.approved


def test_review_planned_slides_allows_medium_only_warnings():
    request = PresentationRequest(
        title="Benchmark Neobank Business vs Acme Business",
        prompt="Faz-me um PowerPoint para a comissão executiva com base neste benchmark.",
    )
    sources = [
        PresentationSource(
            name="Benchmark - Neobank Business.pdf",
            content="Readiness média: 36%\nNeobank vs Acme em pagamentos\n",
            kind=SourceKind.PDF,
        ),
    ]
    brief = build_grounded_brief_from_sources(request, sources)
    rubric = build_critic_rubric(brief)
    review = review_planned_slides(
        [
            PlannedSlide("chart", "Readiness por domínio", has_visual=True),
            PlannedSlide(
                "comparison",
                "Gap competitivo",
                bullets=("A", "B", "C", "D", "E", "F", "G"),
                has_visual=True,
            ),
        ],
        rubric,
    )
    codes = {finding.code for finding in review.findings}
    assert "dense_text" in codes
    assert review.approved


def test_review_planned_slides_flags_unsupported_numeric_claims():
    request = PresentationRequest(
        title="Benchmark Neobank Business vs Acme Business",
        prompt="Faz-me um PowerPoint para a comissão executiva com base neste benchmark.",
    )
    sources = [
        PresentationSource(
            name="Benchmark - Neobank Business.pdf",
            content="Neobank suporta mais de 35 moedas.\nMVP 1 disponível.\nMVP 2 planeado.\n",
            kind=SourceKind.PDF,
        ),
    ]
    brief = build_grounded_brief_from_sources(request, sources)
    rubric = build_critic_rubric(brief)
    review = review_planned_slides(
        [
            PlannedSlide(
                "chart",
                "Impacto esperado",
                numeric_tokens=("85", "88", "91", "94"),
                has_visual=True,
            ),
            PlannedSlide(
                "comparison",
                "Gap competitivo",
                has_visual=True,
            ),
        ],
        rubric,
    )
    codes = {finding.code for finding in review.findings}
    assert "unsupported_numeric_claim" in codes
    assert not review.approved


def test_build_repair_packet_contains_findings_and_actions():
    request = PresentationRequest(
        title="Benchmark Neobank Business vs Acme Business",
        prompt="Faz-me um PowerPoint para a comissão executiva com base neste benchmark.",
    )
    sources = [
        PresentationSource(
            name="Benchmark - Neobank Business.pdf",
            content="Readiness média: 36%\nNeobank vs Acme em pagamentos\n",
            kind=SourceKind.PDF,
        ),
    ]
    brief = build_grounded_brief_from_sources(request, sources)
    rubric = build_critic_rubric(brief)
    review = review_planned_slides(
        [PlannedSlide("content", "Deck inteiro", bullets=("Muito texto",) * 7)],
        rubric,
    )
    packet = build_repair_packet(review, rubric)
    assert "FINDINGS:" in packet
    assert "MANDATORY FIXES:" in packet
    assert "Add at least one chart" in packet or "Add at least one comparison" in packet


def test_review_planned_slides_flags_non_renderable_visuals():
    request = PresentationRequest(
        title="Benchmark Neobank Business vs Acme Business",
        prompt="Faz-me um PowerPoint premium para a comissão executiva com base neste benchmark.",
    )
    sources = [
        PresentationSource(
            name="Benchmark - Neobank Business.pdf",
            content="Readiness média: 36%\nNeobank vs Acme em pagamentos\n",
            kind=SourceKind.PDF,
        ),
    ]
    brief = build_grounded_brief_from_sources(request, sources)
    rubric = build_critic_rubric(brief)
    review = review_planned_slides(
        [
            PlannedSlide("chart", "Readiness por domínio", has_visual=False, render_issue="chart requires categories and series values"),
            PlannedSlide("comparison", "Gap competitivo", has_visual=True),
        ],
        rubric,
    )
    codes = {finding.code for finding in review.findings}
    assert "non_renderable_visual" in codes
    assert "missing_chart" in codes


def test_build_planner_packet_includes_style_goals():
    request = PresentationRequest(
        title="Benchmark Neobank Business vs Acme Business",
        prompt="Faz-me um PowerPoint premium para a comissão executiva com base neste benchmark.",
    )
    sources = [
        PresentationSource(
            name="Benchmark - Neobank Business.pdf",
            content="Readiness média: 36%\nNeobank vs Acme em pagamentos\n",
            kind=SourceKind.PDF,
        ),
    ]
    brief = build_grounded_brief_from_sources(request, sources)
    packet = build_planner_packet(brief)
    assert any("visual" in item.lower() for item in packet.style_goals)
    assert "STYLE GOALS:" in packet.planning_brief_text


def test_build_critic_rubric_includes_style_checks():
    request = PresentationRequest(
        title="Benchmark Neobank Business vs Acme Business",
        prompt="Faz-me um PowerPoint premium para a comissão executiva com base neste benchmark.",
    )
    sources = [
        PresentationSource(
            name="Benchmark - Neobank Business.pdf",
            content="Readiness média: 36%\nNeobank vs Acme em pagamentos\n",
            kind=SourceKind.PDF,
        ),
    ]
    brief = build_grounded_brief_from_sources(request, sources)
    rubric = build_critic_rubric(brief)
    assert any("decorative accent lines" in item.lower() for item in rubric.style_checks)


def test_prepare_presentation_run_builds_end_to_end_bundle():
    request = PresentationRequest(
        title="Benchmark Neobank Business vs Acme Business",
        prompt="Faz-me um PowerPoint premium para a comissão executiva com base neste benchmark.",
    )
    sources = [
        PresentationSource(
            name="Benchmark Neobank Business vs Acme Business-8.pptx",
            content="Deck gerado automaticamente",
            kind=SourceKind.PPTX,
            is_generated_artifact=True,
        ),
        PresentationSource(
            name="Benchmark - Neobank Business.pdf",
            content=(
                "Readiness média: 36%\n"
                "Neobank vs Acme em pagamentos\n"
                "Prioridade recomendada: reforçar integração\n"
            ),
            kind=SourceKind.PDF,
        ),
    ]
    prepared = prepare_presentation_run(request, sources)
    assert prepared.selected_sources[0].name.endswith(".pdf")
    assert prepared.brief.central_message
    assert prepared.planner_packet.required_visuals
    assert "Source authority:" in prepared.legacy_render_payload.context


def test_evaluate_presentation_plan_returns_repair_packet():
    request = PresentationRequest(
        title="Benchmark Neobank Business vs Acme Business",
        prompt="Faz-me um PowerPoint premium para a comissão executiva com base neste benchmark.",
    )
    sources = [
        PresentationSource(
            name="Benchmark - Neobank Business.pdf",
            content="Readiness média: 36%\nNeobank vs Acme em pagamentos\n",
            kind=SourceKind.PDF,
        ),
    ]
    prepared = prepare_presentation_run(request, sources)
    evaluation = evaluate_presentation_plan(
        prepared,
        [
            PlannedSlide("title", "Benchmark"),
            PlannedSlide("content", "Contexto", bullets=("Ponto 1",) * 7),
            PlannedSlide("closing", "Obrigado"),
        ],
    )
    assert not evaluation.approved
    assert "MANDATORY FIXES:" in evaluation.repair_packet


def test_build_model_planner_prompt_contains_contract_and_style():
    request = PresentationRequest(
        title="Benchmark Neobank Business vs Acme Business",
        prompt="Faz-me um PowerPoint premium para a comissão executiva com base neste benchmark.",
    )
    sources = [
        PresentationSource(
            name="Benchmark - Neobank Business.pdf",
            content="Readiness média: 36%\nNeobank vs Acme em pagamentos\n",
            kind=SourceKind.PDF,
        ),
    ]
    prepared = prepare_presentation_run(request, sources)
    prompt = build_model_planner_prompt(prepared)
    assert "OUTPUT CONTRACT:" in prompt
    assert "STYLE + QUALITY RULES:" in prompt
    assert "Return JSON only." in prompt
    assert "image/logo/screenshot" in prompt


def test_build_brief_shaping_prompt_contains_evidence_snapshot():
    request = PresentationRequest(
        title="Benchmark Neobank Business vs Acme Business",
        prompt="Faz-me um PowerPoint premium para a comissão executiva com base neste benchmark.",
    )
    sources = [
        PresentationSource(
            name="Benchmark - Neobank Business.pdf",
            content=(
                "Readiness média: 36%\n"
                "Pagamentos: 56%\n"
                "Onboarding: 31%\n"
                "Neobank vs Acme em pagamentos\n"
                "Prioridade recomendada: reforçar integração\n"
            ),
            kind=SourceKind.PDF,
        ),
    ]
    prepared = prepare_presentation_run(request, sources)
    prompt = build_brief_shaping_prompt(prepared)
    assert "OUTPUT CONTRACT:" in prompt
    assert "EVIDENCE SNAPSHOT:" in prompt
    assert "Source authority:" in prompt or "Source authority" in prompt


async def test_shape_prepared_run_refines_brief_when_json_is_valid():
    request = PresentationRequest(
        title="Benchmark Neobank Business vs Acme Business",
        prompt="Faz-me um PowerPoint premium para a comissão executiva com base neste benchmark.",
    )
    sources = [
        PresentationSource(
            name="Benchmark - Neobank Business.pdf",
            content=(
                "Readiness média: 36%\n"
                "Pagamentos: 56%\n"
                "APIs: 31%\n"
                "Neobank vs Acme em pagamentos\n"
                "Prioridade recomendada: reforçar integração\n"
            ),
            kind=SourceKind.PDF,
        ),
    ]
    prepared = prepare_presentation_run(request, sources)

    async def _fake_llm(prompt, tier="standard", max_tokens=1800, response_format=None):
        return json.dumps(
            {
                "central_message": "O benchmark confirma um gap competitivo mensurável em pagamentos e integrações.",
                "narrative_arc": [
                    "Diagnóstico competitivo",
                    "Evidência por domínio",
                    "Decisão e prioridades",
                ],
                "decisions": ["Priorizar APIs e onboarding digital"],
                "open_questions": ["Qual o investimento faseado aceitável?"],
                "visual_emphasis": ["gaps por domínio", "APIs"],
            },
            ensure_ascii=False,
        )

    shaped = await shape_prepared_run(prepared, llm_call=_fake_llm)
    assert shaped.brief.central_message.startswith("O benchmark confirma")
    assert shaped.brief.narrative_arc[0] == "Diagnóstico competitivo"
    assert "gaps por domínio" in shaped.planner_packet.planning_brief_text


async def test_shape_prepared_run_keeps_original_when_json_is_invalid():
    request = PresentationRequest(
        title="Benchmark Neobank Business vs Acme Business",
        prompt="Faz-me um PowerPoint premium para a comissão executiva com base neste benchmark.",
    )
    sources = [
        PresentationSource(
            name="Benchmark - Neobank Business.pdf",
            content="Readiness média: 36%\nNeobank vs Acme em pagamentos\n",
            kind=SourceKind.PDF,
        ),
    ]
    prepared = prepare_presentation_run(request, sources)

    async def _fake_llm(prompt, tier="standard", max_tokens=1800, response_format=None):
        return "nonsense"

    shaped = await shape_prepared_run(prepared, llm_call=_fake_llm)
    assert shaped == prepared


async def test_shape_prepared_run_keeps_original_on_timeout(monkeypatch):
    request = PresentationRequest(
        title="Benchmark Neobank Business vs Acme Business",
        prompt="Faz-me um PowerPoint premium para a comissão executiva com base neste benchmark.",
    )
    sources = [
        PresentationSource(
            name="Benchmark - Neobank Business.pdf",
            content="Readiness média: 36%\nNeobank vs Acme em pagamentos\n",
            kind=SourceKind.PDF,
        ),
    ]
    prepared = prepare_presentation_run(request, sources)
    monkeypatch.setattr(presentation_briefing, "_BRIEF_SHAPING_TIMEOUT_SECONDS", 0.01)

    async def _slow_llm(prompt, tier="standard", max_tokens=1800, response_format=None):
        await asyncio.sleep(0.05)
        return "{}"

    shaped = await shape_prepared_run(prepared, llm_call=_slow_llm)
    assert shaped == prepared


async def test_run_planning_loop_repairs_rejected_plan():
    request = PresentationRequest(
        title="Benchmark Neobank Business vs Acme Business",
        prompt="Faz-me um PowerPoint premium para a comissão executiva com base neste benchmark.",
    )
    sources = [
        PresentationSource(
            name="Benchmark - Neobank Business.pdf",
            content=(
                "Readiness média: 36%\n"
                "Pagamentos: 56%\n"
                "Onboarding: 31%\n"
                "Neobank vs Acme em pagamentos\n"
                "Prioridade recomendada: reforçar integração\n"
            ),
            kind=SourceKind.PDF,
        ),
    ]
    prepared = prepare_presentation_run(request, sources)

    responses = iter(
        [
            json.dumps(
                {
                    "central_message": "O benchmark evidencia gaps competitivos que exigem ação faseada.",
                    "narrative_arc": [
                        "Diagnóstico competitivo",
                        "Evidência por domínio",
                        "Ação recomendada",
                    ],
                    "decisions": ["Priorizar APIs e onboarding digital"],
                    "open_questions": ["Qual o ritmo de execução viável?"],
                    "visual_emphasis": ["readiness", "APIs"],
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "slides": [
                        {"type": "title", "title": "Benchmark"},
                        {"type": "content", "title": "Contexto", "bullets": ["A", "B", "C"]},
                        {"type": "content", "title": "Impacto", "bullets": ["D", "E", "F"]},
                        {"type": "closing", "title": "Obrigado"},
                    ]
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "slides": [
                        {"type": "title", "title": "Benchmark Neobank vs Acme"},
                        {
                            "type": "chart",
                            "title": "Readiness por domínio",
                            "categories": ["Pagamentos", "APIs"],
                            "series": [{"name": "Readiness", "values": [56, 31]}],
                        },
                        {
                            "type": "comparison",
                            "title": "Gap competitivo",
                            "left_title": "Neobank",
                            "left_items": ["Instant payments", "Onboarding digital"],
                            "right_title": "Acme",
                            "right_items": ["Gap em instant payments", "Onboarding parcial"],
                        },
                        {
                            "type": "table",
                            "title": "Evidência consolidada",
                            "headers": ["Domínio", "Gap"],
                            "rows": [["Pagamentos", "Instant payments"], ["APIs", "Open API depth"]],
                        },
                        {"type": "closing", "title": "Decisão recomendada"},
                    ]
                },
                ensure_ascii=False,
            ),
        ]
    )

    async def _fake_llm(prompt, tier="pro", max_tokens=4000, response_format=None):
        return next(responses)

    result = await run_planning_loop(prepared, llm_call=_fake_llm, max_repairs=1)
    assert result.attempts == 2
    assert result.evaluation.approved
    assert any(slide.slide_type == "chart" for slide in result.slides)
    assert "CRITIC REPAIR PACKET:" in result.repair_prompt


async def test_run_planning_loop_returns_unapproved_when_repair_fails():
    request = PresentationRequest(
        title="Benchmark Neobank Business vs Acme Business",
        prompt="Faz-me um PowerPoint premium para a comissão executiva com base neste benchmark.",
    )
    sources = [
        PresentationSource(
            name="Benchmark - Neobank Business.pdf",
            content="Readiness média: 36%\nNeobank vs Acme em pagamentos\n",
            kind=SourceKind.PDF,
        ),
    ]
    prepared = prepare_presentation_run(request, sources)

    responses = iter(
        [
            json.dumps(
                {
                    "central_message": "O benchmark exige decisão rápida.",
                    "narrative_arc": [
                        "Diagnóstico",
                        "Evidência",
                        "Decisão",
                    ],
                    "decisions": ["Clarificar prioridade executiva"],
                    "open_questions": ["Que domínio atacar primeiro?"],
                    "visual_emphasis": ["gaps"],
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "slides": [
                        {"type": "title", "title": "Benchmark"},
                        {"type": "content", "title": "Contexto", "bullets": ["A"] * 7},
                        {"type": "closing", "title": "Obrigado"},
                    ]
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "slides": [
                        {"type": "title", "title": "Benchmark"},
                        {"type": "content", "title": "Contexto", "bullets": ["A"] * 7},
                        {"type": "closing", "title": "Obrigado"},
                    ]
                },
                ensure_ascii=False,
            ),
        ]
    )

    async def _fake_llm(prompt, tier="pro", max_tokens=4000, response_format=None):
        return next(responses)

    result = await run_planning_loop(prepared, llm_call=_fake_llm, max_repairs=1)
    assert not result.evaluation.approved
    assert result.repair_prompt


async def test_run_planning_loop_raises_on_planner_timeout(monkeypatch):
    request = PresentationRequest(
        title="Benchmark Neobank Business vs Acme Business",
        prompt="Faz-me um PowerPoint premium para a comissão executiva com base neste benchmark.",
    )
    sources = [
        PresentationSource(
            name="Benchmark - Neobank Business.pdf",
            content="Readiness média: 36%\nNeobank vs Acme em pagamentos\n",
            kind=SourceKind.PDF,
        ),
    ]
    prepared = prepare_presentation_run(request, sources)
    monkeypatch.setattr(presentation_runtime, "_PLANNER_TIMEOUT_SECONDS", 0.01)

    async def _slow_llm(prompt, tier="pro", max_tokens=4000, response_format=None):
        await asyncio.sleep(0.05)
        return "{}"

    with pytest.raises(RuntimeError, match="planner timed out"):
        await run_planning_loop(prepared, llm_call=_slow_llm, shape_brief=False)


async def test_run_planning_loop_raises_on_provider_error_response():
    request = PresentationRequest(
        title="Benchmark Neobank Business vs Acme Business",
        prompt="Faz-me um PowerPoint premium para a comissão executiva com base neste benchmark.",
    )
    sources = [
        PresentationSource(
            name="Benchmark - Neobank Business.pdf",
            content="Readiness média: 36%\nNeobank vs Acme em pagamentos\n",
            kind=SourceKind.PDF,
        ),
    ]
    prepared = prepare_presentation_run(request, sources)

    async def _fake_llm(prompt, tier="pro", max_tokens=4000, response_format=None):
        return (
            "Erro real detetado ao contactar os modelos: azure_openai: HTTP 401: "
            "Access denied due to invalid subscription key or wrong API endpoint."
        )

    with pytest.raises(RuntimeError, match="provider error"):
        await run_planning_loop(prepared, llm_call=_fake_llm, shape_brief=False)


async def test_run_planning_loop_repairs_non_renderable_visual_plan():
    request = PresentationRequest(
        title="Benchmark Neobank Business vs Acme Business",
        prompt="Faz-me um PowerPoint premium para a comissão executiva com base neste benchmark.",
    )
    sources = [
        PresentationSource(
            name="Benchmark - Neobank Business.pdf",
            content=(
                "Readiness média: 36%\n"
                "Pagamentos: 56%\n"
                "Onboarding: 31%\n"
                "Neobank vs Acme em pagamentos\n"
                "Prioridade recomendada: reforçar integração\n"
            ),
            kind=SourceKind.PDF,
        ),
    ]
    prepared = prepare_presentation_run(request, sources)
    responses = iter(
        [
            json.dumps(
                {
                    "central_message": "O benchmark exige uma história visual grounded.",
                    "narrative_arc": ["Diagnóstico", "Evidência", "Decisão"],
                    "decisions": ["Priorizar APIs e onboarding"],
                    "open_questions": ["Qual a cadência de execução?"],
                    "visual_emphasis": ["gaps por domínio", "APIs"],
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "slides": [
                        {"type": "chart", "title": "Readiness por domínio"},
                        {"type": "comparison", "title": "Gap competitivo", "left_title": "Neobank", "left_items": ["A"], "right_title": "Acme", "right_items": ["B"]},
                    ]
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "slides": [
                        {
                            "type": "chart",
                            "title": "Readiness por domínio",
                            "categories": ["Pagamentos", "Onboarding"],
                            "series": [{"name": "Readiness", "values": [56, 31]}],
                        },
                        {
                            "type": "comparison",
                            "title": "Gap competitivo",
                            "left_title": "Neobank",
                            "left_items": ["A"],
                            "right_title": "Acme",
                            "right_items": ["B"],
                        },
                        {
                            "type": "table",
                            "title": "Evidência consolidada",
                            "headers": ["Domínio", "Gap"],
                            "rows": [["Pagamentos", "Instant payments"]],
                        },
                    ]
                },
                ensure_ascii=False,
            ),
        ]
    )

    async def _fake_llm(prompt, tier="pro", max_tokens=4000, response_format=None):
        return next(responses)

    result = await run_planning_loop(prepared, llm_call=_fake_llm, max_repairs=1)
    assert result.evaluation.approved
    assert result.attempts == 2
    assert any(slide.slide_type == "chart" and slide.has_visual for slide in result.slides)


@pytest.mark.asyncio
async def test_run_planning_loop_keeps_planner_plan_when_repair_returns_blank():
    request = PresentationRequest(
        title="Benchmark Neobank Business vs Acme Business",
        prompt="Faz-me um PowerPoint premium para a comissão executiva com base neste benchmark.",
    )
    sources = [
        PresentationSource(
            name="Benchmark - Neobank Business.pdf",
            content=(
                "Readiness média: 36%\n"
                "Neobank vs Acme em pagamentos\n"
                "Prioridade recomendada: reforçar integração\n"
            ),
            kind=SourceKind.PDF,
        ),
    ]
    prepared = prepare_presentation_run(request, sources)
    responses = iter(
        [
            json.dumps(
                {
                    "slides": [
                        {
                            "type": "chart",
                            "title": "Readiness por domínio",
                            "notes": "Compara níveis de readiness.",
                            "chart_type": "bar",
                            "categories": ["Pagamentos", "Onboarding"],
                            "series": [{"name": "Readiness", "values": [56, 31]}],
                            "bullets": None,
                            "items": None,
                            "kpis": None,
                            "left_title": None,
                            "left_items": None,
                            "right_title": None,
                            "right_items": None,
                            "headers": None,
                            "rows": None,
                            "steps": None,
                            "stat_value": None,
                            "stat_label": None,
                            "image_url": None,
                            "caption": None,
                        },
                        {
                            "type": "comparison",
                            "title": "Gap competitivo",
                            "notes": "Compara a oferta atual.",
                            "left_title": "Neobank",
                            "left_items": ["A"],
                            "right_title": "Acme",
                            "right_items": ["B"],
                            "bullets": None,
                            "items": None,
                            "kpis": None,
                            "chart_type": None,
                            "categories": None,
                            "series": None,
                            "headers": None,
                            "rows": None,
                            "steps": None,
                            "stat_value": None,
                            "stat_label": None,
                            "image_url": None,
                            "caption": None,
                        },
                        {
                            "type": "content",
                            "title": "Implicações estratégicas",
                            "notes": "Resume impactos estratégicos.",
                            "bullets": [
                                "O gap afeta competitividade internacional",
                                "É necessária uma resposta faseada",
                            ],
                            "items": None,
                            "kpis": None,
                            "chart_type": None,
                            "categories": None,
                            "series": None,
                            "left_title": None,
                            "left_items": None,
                            "right_title": None,
                            "right_items": None,
                            "headers": None,
                            "rows": None,
                            "steps": None,
                            "stat_value": None,
                            "stat_label": None,
                            "image_url": None,
                            "caption": None,
                        },
                    ]
                },
                ensure_ascii=False,
            ),
            "",
        ]
    )

    async def _fake_llm(prompt, tier="pro", max_tokens=4000, response_format=None):
        return next(responses)

    result = await run_planning_loop(prepared, llm_call=_fake_llm, shape_brief=False, max_repairs=1)
    assert [slide.slide_type for slide in result.slides] == ["chart", "comparison", "content"]
    assert result.repair_raw == ""


def test_build_presentation_sources_marks_generated_pptx_low_authority():
    sources = build_presentation_sources(
        content="Resumo do pedido.",
        uploads=[
            {
                "filename": "Benchmark - Neobank Business.pdf",
                "data_text": "Readiness média: 36%",
                "content_type": "application/pdf",
            },
            {
                "filename": "Benchmark Neobank Business vs Acme Business-8.pptx",
                "data_text": "Deck gerado automaticamente",
                "content_type": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            },
        ],
    )
    pdf = next(src for src in sources if src.name.endswith(".pdf"))
    pptx = next(src for src in sources if src.name.endswith(".pptx"))
    assert pdf.kind == SourceKind.PDF
    assert not pdf.is_generated_artifact
    assert pptx.kind == SourceKind.PPTX
    assert pptx.is_generated_artifact


async def test_load_conversation_presentation_sources_uses_upload_loader():
    async def _fake_loader(conv_id: str):
        assert conv_id == "conv-123"
        return [
            {
                "filename": "Benchmark - Neobank Business.pdf",
                "data_text": "Readiness média: 36%",
                "content_type": "application/pdf",
            }
        ]

    sources = await load_conversation_presentation_sources(
        conv_id="conv-123",
        content="Resumo do pedido",
        upload_loader=_fake_loader,
    )
    names = {src.name for src in sources}
    assert "inline_request_content.txt" in names
    assert "Benchmark - Neobank Business.pdf" in names


async def test_load_conversation_presentation_sources_prefers_extracted_blob_text():
    async def _fake_loader(conv_id: str):
        assert conv_id == "conv-blob"
        return [
            {
                "filename": "Benchmark - Neobank Business.pdf",
                "data_text": "preview curto",
                "content_type": "application/pdf",
                "extracted_blob_ref": "blob://uploads-text/full.txt",
            }
        ]

    async def _fake_blob_loader(blob_ref: str) -> str:
        assert blob_ref == "blob://uploads-text/full.txt"
        return "texto integral do benchmark com gaps, métricas e comparações"

    sources = await load_conversation_presentation_sources(
        conv_id="conv-blob",
        upload_loader=_fake_loader,
        blob_text_loader=_fake_blob_loader,
    )
    pdf = next(src for src in sources if src.name.endswith(".pdf"))
    assert "texto integral do benchmark" in pdf.content
