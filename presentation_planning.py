"""Planning and review contracts for the next-generation PPTX lane.

This module stays renderer-agnostic on purpose. It prepares:

- the packet a frontier model should plan from
- the rubric a critic/reviewer should use
- an adapter payload for temporarily reusing the legacy renderer
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Dict, List, Sequence, Tuple

from presentation_orchestrator import (
    CAPABILITY_PROFILES,
    DeckCapability,
    EvidenceItem,
    EvidenceKind,
    GroundedBrief,
)


@dataclass(frozen=True)
class PlannerPacket:
    capability: DeckCapability
    audience: str
    central_message: str
    narrative_arc: Sequence[str]
    preferred_visuals: Sequence[str]
    required_visuals: Sequence[str]
    forbidden_patterns: Sequence[str]
    style_goals: Sequence[str]
    max_slides: int
    source_selection_note: str
    evidence_snapshot: Sequence[str]
    planning_brief_text: str


@dataclass(frozen=True)
class CriticRubric:
    capability: DeckCapability
    audience: str
    grounding_checks: Sequence[str]
    narrative_checks: Sequence[str]
    visual_checks: Sequence[str]
    style_checks: Sequence[str]
    failure_conditions: Sequence[str]
    repair_priorities: Sequence[str]
    supported_numeric_tokens: Sequence[str]


@dataclass(frozen=True)
class LegacyRenderPayload:
    title: str
    content: str
    context: str
    expected_visual_mix: Sequence[str] = field(default_factory=tuple)


def build_planner_packet(brief: GroundedBrief) -> PlannerPacket:
    profile = CAPABILITY_PROFILES.get(
        brief.capability,
        CAPABILITY_PROFILES.get(DeckCapability.BENCHMARK_EXECUTIVE),
    )
    preferred_visuals = tuple(profile.preferred_visuals) if profile else ("comparison", "chart")
    required_visuals = tuple(_required_visuals_for_brief(brief))
    forbidden_patterns = (
        "text dump",
        "table dump",
        "raw source leakage",
        "repeated titles",
        "invented metrics",
        "generic section-divider spam",
    )
    style_goals = tuple(_style_goals_for_brief(brief))
    max_slides = profile.max_slides_executive if profile else 12
    evidence_snapshot = tuple(_evidence_snapshot_lines(brief.evidence))
    planning_brief_text = _render_planning_brief_text(
        brief=brief,
        preferred_visuals=preferred_visuals,
        required_visuals=required_visuals,
        forbidden_patterns=forbidden_patterns,
        style_goals=style_goals,
        max_slides=max_slides,
        evidence_snapshot=evidence_snapshot,
    )
    return PlannerPacket(
        capability=brief.capability,
        audience=brief.audience.value,
        central_message=brief.central_message,
        narrative_arc=tuple(brief.narrative_arc),
        preferred_visuals=preferred_visuals,
        required_visuals=required_visuals,
        forbidden_patterns=forbidden_patterns,
        style_goals=style_goals,
        max_slides=max_slides,
        source_selection_note=brief.source_selection_note,
        evidence_snapshot=evidence_snapshot,
        planning_brief_text=planning_brief_text,
    )


def build_critic_rubric(brief: GroundedBrief) -> CriticRubric:
    grounding_checks = (
        "Every numeric claim must be traceable to selected sources.",
        "No old generated deck should outrank the primary source document.",
        "No raw source fragments or upload markers should appear on slides.",
    )
    narrative_checks = (
        "The deck must have a clear executive arc, not a source-order dump.",
        "Titles should communicate conclusions, not just topics.",
        "The close should ask for a decision or action when appropriate.",
    )
    visual_checks = (
        "No wall-of-text slides.",
        "No repeated layout dominating the whole deck.",
        "At least one meaningful visual per evidence-rich slide group.",
    )
    style_checks = (
        "Every slide should feel intentionally designed, not default PowerPoint.",
        "Use one dominant visual direction with a restrained accent, not equal-weight colors everywhere.",
        "Avoid decorative accent lines under titles; use spacing, contrast, or layout instead.",
        "Vary layout rhythm so the deck does not repeat the same composition slide after slide.",
    )
    failure_conditions = list(grounding_checks) + list(visual_checks) + list(style_checks)
    repair_priorities = _repair_priorities_for_capability(brief.capability)
    supported_numeric_tokens = _supported_numeric_tokens(brief)
    return CriticRubric(
        capability=brief.capability,
        audience=brief.audience.value,
        grounding_checks=grounding_checks,
        narrative_checks=narrative_checks,
        visual_checks=visual_checks,
        style_checks=style_checks,
        failure_conditions=tuple(failure_conditions),
        repair_priorities=tuple(repair_priorities),
        supported_numeric_tokens=tuple(supported_numeric_tokens),
    )


def build_legacy_render_payload(title: str, brief: GroundedBrief, packet: PlannerPacket) -> LegacyRenderPayload:
    """Compact bridge for experimenting with the legacy renderer.

    This deliberately passes a compact, grounded brief instead of raw document
    dumps so we can compare old rendering with better orchestration.
    """
    evidence_block = "\n".join(f"- {line}" for line in packet.evidence_snapshot[:18])
    content = (
        f"MENSAGEM CENTRAL:\n{brief.central_message}\n\n"
        f"EVIDÊNCIA ESTRUTURADA:\n{evidence_block}"
    ).strip()
    context = (
        f"Capability: {brief.capability.value}\n"
        f"Audiência: {brief.audience.value}\n"
        f"Arco narrativo: {' -> '.join(brief.narrative_arc)}\n"
        f"Source authority: {brief.source_selection_note or 'n/a'}\n"
        f"Visuais preferidos: {', '.join(packet.preferred_visuals)}\n"
        f"Visuais obrigatórios: {', '.join(packet.required_visuals)}\n"
        f"Padrões proibidos: {', '.join(packet.forbidden_patterns)}"
    ).strip()
    return LegacyRenderPayload(
        title=title,
        content=content,
        context=context,
        expected_visual_mix=packet.required_visuals,
    )


def _required_visuals_for_brief(brief: GroundedBrief) -> Sequence[str]:
    kinds = {item.kind for item in brief.evidence}
    required: List[str] = []
    if EvidenceKind.METRIC in kinds:
        required.append("chart")
    if EvidenceKind.COMPARISON in kinds:
        required.append("comparison")
    if EvidenceKind.TABLE in kinds and "table" not in required:
        required.append("table")
    if brief.capability == DeckCapability.BENCHMARK_EXECUTIVE and "table" not in required:
        required.append("table")
    if brief.capability == DeckCapability.BACKLOG_STEERING and "process" not in required:
        required.append("process")
    return tuple(required or ("comparison",))


def _evidence_snapshot_lines(evidence: Sequence[EvidenceItem]) -> List[str]:
    grouped: Dict[EvidenceKind, List[str]] = {}
    for item in evidence:
        grouped.setdefault(item.kind, []).append(item.summary)

    ordered_kinds = (
        EvidenceKind.METRIC,
        EvidenceKind.COMPARISON,
        EvidenceKind.DECISION,
        EvidenceKind.RISK,
        EvidenceKind.FACT,
        EvidenceKind.TABLE,
        EvidenceKind.IMAGE,
    )
    lines: List[str] = []
    for kind in ordered_kinds:
        for summary in grouped.get(kind, [])[:6]:
            lines.append(f"[{kind.value}] {summary}")
    return lines


def _render_planning_brief_text(
    *,
    brief: GroundedBrief,
    preferred_visuals: Sequence[str],
    required_visuals: Sequence[str],
    forbidden_patterns: Sequence[str],
    style_goals: Sequence[str],
    max_slides: int,
    evidence_snapshot: Sequence[str],
) -> str:
    sections = [
        f"CAPABILITY: {brief.capability.value}",
        f"AUDIENCE: {brief.audience.value}",
        f"CENTRAL MESSAGE: {brief.central_message}",
        f"NARRATIVE ARC: {' -> '.join(brief.narrative_arc)}",
        f"MAX SLIDES: {max_slides}",
        f"SOURCE AUTHORITY: {brief.source_selection_note or 'No source note provided.'}",
        f"PREFERRED VISUALS: {', '.join(preferred_visuals)}",
        f"REQUIRED VISUALS: {', '.join(required_visuals)}",
        f"FORBIDDEN PATTERNS: {', '.join(forbidden_patterns)}",
        f"STYLE GOALS: {', '.join(style_goals)}",
        f"VISUAL EMPHASIS: {', '.join(brief.visual_emphasis) if brief.visual_emphasis else 'Use strongest evidence as visual focus.'}",
        "EVIDENCE SNAPSHOT:",
    ]
    sections.extend(f"- {line}" for line in evidence_snapshot[:20])
    supported_numeric_tokens = _supported_numeric_tokens(brief)
    if supported_numeric_tokens:
        sections.append("SUPPORTED NUMERIC TOKENS:")
        sections.append(f"- {', '.join(supported_numeric_tokens[:20])}")
    if brief.decisions:
        sections.append("DECISIONS TO SUPPORT:")
        sections.extend(f"- {item}" for item in brief.decisions[:5])
    if brief.open_questions:
        sections.append("OPEN QUESTIONS:")
        sections.extend(f"- {item}" for item in brief.open_questions[:5])
    return "\n".join(sections)


def _style_goals_for_brief(brief: GroundedBrief) -> Sequence[str]:
    common = [
        "Every slide should include a meaningful visual or structural focal point.",
        "Prefer strong visual hierarchy over dense bullet stacks.",
        "Use layout variety and avoid repeating the same slide composition more than twice in a row.",
    ]

    if brief.audience.value == "executive":
        common.extend(
            [
                "Use a premium executive rhythm: strong open, synthesis-heavy middle, decisive close.",
                "Prefer a dark/light sandwich structure or another clear motif instead of generic white slides throughout.",
            ]
        )

    if brief.capability == DeckCapability.BENCHMARK_EXECUTIVE:
        common.extend(
            [
                "Show competitive gaps through comparison, charts, and synthesis rather than source-order summaries.",
                "Make trade-offs and implications visually scannable for steering decisions.",
            ]
        )

    return tuple(common)


def _repair_priorities_for_capability(capability: DeckCapability) -> Sequence[str]:
    if capability == DeckCapability.BENCHMARK_EXECUTIVE:
        return (
            "Replace domain-by-domain dump with macro synthesis.",
            "Add at least one chart and one comparison rooted in source evidence.",
            "Collapse repetitive source sections into executive conclusions.",
        )
    if capability == DeckCapability.BACKLOG_STEERING:
        return (
            "Make status, risk, and next action immediately scannable.",
            "Prefer KPI + chart + roadmap over Jira-like text blocks.",
        )
    return (
        "Increase narrative clarity.",
        "Reduce text density.",
        "Improve visual variety without losing editability.",
    )


def _supported_numeric_tokens(brief: GroundedBrief) -> Sequence[str]:
    text = " ".join(
        [brief.central_message]
        + [item.summary for item in brief.evidence[:30]]
        + list(brief.decisions[:10])
        + list(brief.open_questions[:10])
    )
    tokens: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"\d+(?:[.,]\d+)?", text):
        token = match.group(0).replace(",", ".").strip()
        if token and token not in seen:
            seen.add(token)
            tokens.append(token)
    return tuple(tokens)
