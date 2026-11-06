"""Experimental presentation orchestrator for the next PPTX lane.

This module is intentionally not wired into production yet.

It defines the contracts for a model-first, product-team-native presentation workflow:
request classification, grounded brief creation, plan review, and capability
profiles. Rendering remains in the legacy `pptx_engine.py` until the new lane
is ready.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Dict, Iterable, List, Optional, Sequence

from presentation_reasoning import (
    PresentationSource,
    extract_grounded_evidence_text,
    select_authoritative_sources,
    summarize_source_selection,
)


class DeckCapability(str, Enum):
    BENCHMARK_EXECUTIVE = "benchmark_executive"
    BACKLOG_STEERING = "backlog_steering"
    STRATEGY = "strategy"
    PRODUCT = "product"
    RESEARCH = "research"
    OPERATIONAL_STATUS = "operational_status"
    GENERAL = "general"


class AudienceClass(str, Enum):
    EXECUTIVE = "executive"
    TECHNICAL = "technical"
    MIXED = "mixed"


class EvidenceKind(str, Enum):
    FACT = "fact"
    METRIC = "metric"
    COMPARISON = "comparison"
    DECISION = "decision"
    RISK = "risk"
    TABLE = "table"
    IMAGE = "image"


@dataclass(frozen=True)
class EvidenceItem:
    kind: EvidenceKind
    summary: str
    source_name: str = ""
    source_anchor: str = ""
    confidence: float = 1.0


@dataclass(frozen=True)
class PresentationRequest:
    title: str
    prompt: str
    content: str = ""
    context: str = ""
    source_names: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True)
class PresentationIntent:
    capability: DeckCapability
    audience: AudienceClass
    confidence: float
    rationale: str


@dataclass(frozen=True)
class GroundedBrief:
    capability: DeckCapability
    audience: AudienceClass
    central_message: str
    narrative_arc: Sequence[str]
    evidence: Sequence[EvidenceItem]
    source_names: Sequence[str] = field(default_factory=tuple)
    source_selection_note: str = ""
    decisions: Sequence[str] = field(default_factory=tuple)
    open_questions: Sequence[str] = field(default_factory=tuple)
    visual_emphasis: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True)
class ReviewIssue:
    code: str
    message: str
    severity: str = "medium"


@dataclass(frozen=True)
class PlanReview:
    approved: bool
    issues: Sequence[ReviewIssue]


@dataclass(frozen=True)
class CapabilityProfile:
    capability: DeckCapability
    objective: str
    preferred_visuals: Sequence[str]
    required_evidence_kinds: Sequence[EvidenceKind]
    max_slides_executive: int = 12


CAPABILITY_PROFILES: Dict[DeckCapability, CapabilityProfile] = {
    DeckCapability.BENCHMARK_EXECUTIVE: CapabilityProfile(
        capability=DeckCapability.BENCHMARK_EXECUTIVE,
        objective="Compare capabilities, surface gaps, support executive decision-making.",
        preferred_visuals=("comparison", "chart", "table", "stat_chart"),
        required_evidence_kinds=(
            EvidenceKind.COMPARISON,
            EvidenceKind.METRIC,
            EvidenceKind.DECISION,
        ),
    ),
    DeckCapability.BACKLOG_STEERING: CapabilityProfile(
        capability=DeckCapability.BACKLOG_STEERING,
        objective="Support steering decisions with status, risks, and next actions.",
        preferred_visuals=("kpi", "chart", "process"),
        required_evidence_kinds=(
            EvidenceKind.METRIC,
            EvidenceKind.RISK,
            EvidenceKind.DECISION,
        ),
    ),
    DeckCapability.STRATEGY: CapabilityProfile(
        capability=DeckCapability.STRATEGY,
        objective="Explain strategic direction, options, and decisions.",
        preferred_visuals=("comparison", "process", "stat_chart"),
        required_evidence_kinds=(EvidenceKind.FACT, EvidenceKind.DECISION),
    ),
}


def classify_presentation_request(request: PresentationRequest) -> PresentationIntent:
    text = " ".join(
        part for part in (request.title, request.prompt, request.context, request.content) if part
    ).lower()

    benchmark_markers = ("benchmark", "comparativ", "neobank", "vs ", "gap", "comissão executiva")
    backlog_markers = ("backlog", "jira", "estado atual", "bloqueios", "próximos passos", "mvp")
    strategy_markers = ("estratégia", "roadmap", "visão", "posicionamento", "decisão")

    if any(marker in text for marker in benchmark_markers):
        return PresentationIntent(
            capability=DeckCapability.BENCHMARK_EXECUTIVE,
            audience=AudienceClass.EXECUTIVE,
            confidence=0.92,
            rationale="Detected benchmark/comparison vocabulary with executive framing.",
        )
    if any(marker in text for marker in backlog_markers):
        return PresentationIntent(
            capability=DeckCapability.BACKLOG_STEERING,
            audience=AudienceClass.MIXED,
            confidence=0.88,
            rationale="Detected backlog/status language and steering signals.",
        )
    if any(marker in text for marker in strategy_markers):
        return PresentationIntent(
            capability=DeckCapability.STRATEGY,
            audience=AudienceClass.EXECUTIVE,
            confidence=0.78,
            rationale="Detected strategy/roadmap framing.",
        )
    return PresentationIntent(
        capability=DeckCapability.GENERAL,
        audience=AudienceClass.MIXED,
        confidence=0.55,
        rationale="No strong capability markers; route through general lane.",
    )


def build_grounded_brief(
    request: PresentationRequest,
    intent: PresentationIntent,
    evidence: Optional[Sequence[EvidenceItem]] = None,
) -> GroundedBrief:
    evidence_items = tuple(evidence or ())
    narrative_arc = _default_arc_for_capability(intent.capability)
    central_message = _default_central_message(request, intent)
    return GroundedBrief(
        capability=intent.capability,
        audience=intent.audience,
        central_message=central_message,
        narrative_arc=narrative_arc,
        evidence=evidence_items,
        source_names=tuple(request.source_names),
        source_selection_note="",
        decisions=(),
        open_questions=(),
        visual_emphasis=_default_visual_emphasis(intent.capability, evidence_items),
    )


def build_grounded_brief_from_sources(
    request: PresentationRequest,
    sources: Sequence[PresentationSource],
) -> GroundedBrief:
    intent = classify_presentation_request(request)
    selected = select_authoritative_sources(sources, capability=intent.capability.value)
    evidence_lines = extract_grounded_evidence_text(selected, capability=intent.capability.value)
    evidence = tuple(
        infer_evidence_items_from_text("\n".join(evidence_lines), source_name=src.name)
        for src in selected[:1]
    )
    flattened_evidence: List[EvidenceItem] = []
    for group in evidence:
        flattened_evidence.extend(group)

    decisions = tuple(
        item.summary for item in flattened_evidence if item.kind == EvidenceKind.DECISION
    )[:5]
    open_questions = _infer_open_questions(selected, intent.capability)
    central_message = _central_message_from_sources(request, intent, flattened_evidence)

    return GroundedBrief(
        capability=intent.capability,
        audience=intent.audience,
        central_message=central_message,
        narrative_arc=_default_arc_for_capability(intent.capability),
        evidence=tuple(flattened_evidence),
        source_names=tuple(src.name for src in selected),
        source_selection_note=summarize_source_selection(selected, capability=intent.capability.value),
        decisions=decisions,
        open_questions=open_questions,
        visual_emphasis=_default_visual_emphasis(intent.capability, flattened_evidence),
    )


def review_slide_plan(
    *,
    capability: DeckCapability,
    slide_types: Sequence[str],
    titles: Sequence[str],
    bullet_counts: Sequence[int],
) -> PlanReview:
    issues: List[ReviewIssue] = []
    normalized_types = [str(t or "").strip().lower() for t in slide_types]
    normalized_titles = [str(t or "").strip().lower() for t in titles if str(t or "").strip()]

    if len(normalized_titles) != len(set(normalized_titles)):
        issues.append(ReviewIssue("repeated_titles", "Deck repeats titles too often.", "high"))

    if all(t in ("content", "section", "title", "closing") for t in normalized_types):
        issues.append(ReviewIssue("text_only", "Deck has no meaningful visual slide types.", "high"))

    if capability == DeckCapability.BENCHMARK_EXECUTIVE:
        if not any(t in ("chart", "stat_chart") for t in normalized_types):
            issues.append(ReviewIssue("missing_chart", "Executive benchmark deck needs at least one chart.", "high"))
        if not any(t in ("comparison", "table") for t in normalized_types):
            issues.append(ReviewIssue("missing_comparison", "Executive benchmark deck needs comparison or table evidence.", "high"))

    if sum(1 for count in bullet_counts if count > 6) > 0:
        issues.append(ReviewIssue("dense_slides", "Some slides exceed the target bullet density.", "medium"))

    return PlanReview(approved=not issues, issues=tuple(issues))


def infer_evidence_items_from_text(content: str, source_name: str = "") -> List[EvidenceItem]:
    """Lightweight heuristic extractor for the new lane scaffolding.

    This is not meant to replace model reasoning. It only provides a deterministic
    bridge so the orchestrator can be tested independently of the rendering lane.
    """
    items: List[EvidenceItem] = []
    for line in content.splitlines():
        text = line.strip()
        if not text:
            continue
        if re.search(r"\b\d+(?:[.,]\d+)?%|\b\d+\b", text):
            items.append(EvidenceItem(EvidenceKind.METRIC, text[:240], source_name=source_name))
        elif any(token in text.lower() for token in ("vs", "versus", "compar", "neobank", "acme")):
            items.append(EvidenceItem(EvidenceKind.COMPARISON, text[:240], source_name=source_name))
        elif any(token in text.lower() for token in ("decisão", "recomend", "prioridad", "próximo")):
            items.append(EvidenceItem(EvidenceKind.DECISION, text[:240], source_name=source_name))
    return items


def _infer_open_questions(
    sources: Sequence[PresentationSource],
    capability: DeckCapability,
) -> Sequence[str]:
    if capability != DeckCapability.BENCHMARK_EXECUTIVE:
        return ()

    combined = "\n".join(src.content for src in sources)
    questions: List[str] = []
    if "api" in combined.lower():
        questions.append("Qual deve ser a ambição da oferta API face aos concorrentes?")
    if "fase 2" in combined.lower() or "segunda fase" in combined.lower():
        questions.append("Quais gaps devem ser tratados já no MVP e quais podem ficar para fase posterior?")
    return tuple(questions[:3])


def _default_arc_for_capability(capability: DeckCapability) -> Sequence[str]:
    if capability == DeckCapability.BENCHMARK_EXECUTIVE:
        return ("diagnóstico", "evidência", "decisão")
    if capability == DeckCapability.BACKLOG_STEERING:
        return ("estado", "riscos", "ação")
    if capability == DeckCapability.STRATEGY:
        return ("contexto", "opções", "escolha", "roadmap")
    return ("contexto", "evidência", "ação")


def _default_central_message(request: PresentationRequest, intent: PresentationIntent) -> str:
    if intent.capability == DeckCapability.BENCHMARK_EXECUTIVE:
        return "O deck deve sintetizar o gap competitivo e orientar a decisão executiva."
    if intent.capability == DeckCapability.BACKLOG_STEERING:
        return "O deck deve suportar steering com estado, riscos e próximos passos."
    if intent.capability == DeckCapability.STRATEGY:
        return "O deck deve clarificar a direção estratégica e a decisão pedida."
    return request.prompt[:240] if request.prompt else request.title[:240]


def _central_message_from_sources(
    request: PresentationRequest,
    intent: PresentationIntent,
    evidence: Sequence[EvidenceItem],
) -> str:
    if intent.capability == DeckCapability.BENCHMARK_EXECUTIVE:
        metrics = [item.summary for item in evidence if item.kind == EvidenceKind.METRIC]
        comparisons = [item.summary for item in evidence if item.kind == EvidenceKind.COMPARISON]
        if metrics or comparisons:
            return "O deck deve traduzir o benchmark em posicionamento competitivo, gaps materiais e decisão executiva."
    return _default_central_message(request, intent)


def _default_visual_emphasis(
    capability: DeckCapability,
    evidence: Sequence[EvidenceItem],
) -> Sequence[str]:
    emphasis: List[str] = []
    seen = set()

    def _add(value: str) -> None:
        normalized = str(value or "").strip()
        if not normalized:
            return
        lowered = normalized.lower()
        if lowered in seen:
            return
        seen.add(lowered)
        emphasis.append(normalized)

    for item in evidence:
        if item.kind == EvidenceKind.METRIC:
            _add("métricas-chave")
        elif item.kind == EvidenceKind.COMPARISON:
            _add("comparação competitiva")
        elif item.kind == EvidenceKind.TABLE:
            _add("evidência consolidada")
        elif item.kind == EvidenceKind.DECISION:
            _add("decisões recomendadas")

    if capability == DeckCapability.BENCHMARK_EXECUTIVE:
        _add("gaps por domínio")
        _add("APIs e ecossistema")
    elif capability == DeckCapability.BACKLOG_STEERING:
        _add("bloqueios críticos")
        _add("próximos passos")

    return tuple(emphasis[:5])
