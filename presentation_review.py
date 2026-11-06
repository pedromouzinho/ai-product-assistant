"""Review and repair helpers for the next-generation PPTX lane."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Sequence

from presentation_orchestrator import DeckCapability
from presentation_planning import CriticRubric


@dataclass(frozen=True)
class PlannedSlide:
    slide_type: str
    title: str = ""
    bullets: Sequence[str] = ()
    numeric_tokens: Sequence[str] = ()
    has_visual: bool = False
    render_issue: str = ""


@dataclass(frozen=True)
class ReviewFinding:
    code: str
    message: str
    severity: str = "medium"


@dataclass(frozen=True)
class ReviewResult:
    approved: bool
    findings: Sequence[ReviewFinding]
    repair_instructions: Sequence[str]


def review_planned_slides(
    slides: Sequence[PlannedSlide],
    rubric: CriticRubric,
) -> ReviewResult:
    findings: List[ReviewFinding] = []
    normalized_types = [s.slide_type.strip().lower() for s in slides]
    normalized_titles = [s.title.strip().lower() for s in slides if s.title.strip()]

    if len(normalized_titles) != len(set(normalized_titles)):
        findings.append(ReviewFinding("repeated_titles", "The plan repeats slide titles.", "high"))

    if all(t in ("content", "section", "title", "closing") for t in normalized_types):
        findings.append(ReviewFinding("text_only", "The plan is effectively text-only.", "high"))

    if sum(1 for slide in slides if len(tuple(slide.bullets)) > 6) > 0:
        findings.append(ReviewFinding("dense_text", "Some planned slides are too dense.", "medium"))

    if _looks_like_repetitive_section_dump(normalized_types):
        findings.append(ReviewFinding("section_spam", "The plan overuses section-divider rhythm.", "medium"))

    if any(slide.render_issue for slide in slides):
        findings.append(
            ReviewFinding(
                "non_renderable_visual",
                "One or more visual slides are missing the fields required for rendering.",
                "high",
            )
        )

    if DeckCapability.BENCHMARK_EXECUTIVE == rubric.capability:
        numeric_visuals_present = any(
            slide.slide_type in ("chart", "stat_chart") and slide.has_visual
            for slide in slides
        )
        if rubric.supported_numeric_tokens and not numeric_visuals_present:
            findings.append(ReviewFinding("missing_chart", "Executive benchmark plan has no chart.", "high"))
        if not any(
            slide.slide_type in ("comparison", "table") and slide.has_visual
            for slide in slides
        ):
            findings.append(ReviewFinding("missing_comparison", "Executive benchmark plan has no comparison/table.", "high"))
        unsupported_numeric = _unsupported_numeric_tokens(slides, rubric.supported_numeric_tokens)
        if unsupported_numeric:
            findings.append(
                ReviewFinding(
                    "unsupported_numeric_claim",
                    (
                        "The plan introduces numeric values not grounded in the source evidence: "
                        + ", ".join(unsupported_numeric[:8])
                    ),
                    "high",
                )
            )

    blocking_findings = [finding for finding in findings if finding.severity == "high"]
    repair_instructions = tuple(_repair_instructions_for_findings(blocking_findings, rubric))
    return ReviewResult(
        approved=not blocking_findings,
        findings=tuple(findings),
        repair_instructions=repair_instructions,
    )


def build_repair_packet(
    review: ReviewResult,
    rubric: CriticRubric,
) -> str:
    if review.approved:
        return "Plan approved. No repair instructions needed."

    lines = [
        f"CAPABILITY: {rubric.capability.value}",
        f"AUDIENCE: {rubric.audience}",
        "REPAIR GOAL: revise the slide plan without inventing facts.",
        "FINDINGS:",
    ]
    lines.extend(f"- [{f.severity}] {f.code}: {f.message}" for f in review.findings)
    lines.append("REPAIR PRIORITIES:")
    lines.extend(f"- {item}" for item in rubric.repair_priorities)
    lines.append("MANDATORY FIXES:")
    lines.extend(f"- {item}" for item in review.repair_instructions)
    return "\n".join(lines)


def _looks_like_repetitive_section_dump(slide_types: Sequence[str]) -> bool:
    if len(slide_types) < 6:
        return False
    section_positions = [idx for idx, stype in enumerate(slide_types) if stype == "section"]
    if len(section_positions) < 3:
        return False
    return max(section_positions) - min(section_positions) <= len(slide_types) - 2


def _repair_instructions_for_findings(
    findings: Sequence[ReviewFinding],
    rubric: CriticRubric,
) -> List[str]:
    instructions: List[str] = []
    codes = {finding.code for finding in findings}

    if "text_only" in codes:
        instructions.append("Replace text-only structure with chart/comparison/table slides rooted in evidence.")
    if "missing_chart" in codes:
        instructions.append("Add at least one chart or stat+chart using grounded metrics.")
    if "missing_comparison" in codes:
        instructions.append("Add at least one comparison or table slide to surface trade-offs.")
    if "repeated_titles" in codes:
        instructions.append("Rewrite slide titles as unique executive messages, not repeated section names.")
    if "dense_text" in codes:
        instructions.append("Reduce bullet density and convert long text blocks into clearer visual groupings.")
    if "section_spam" in codes:
        instructions.append("Collapse decorative sections and use content slides that advance the narrative.")
    if "non_renderable_visual" in codes:
        instructions.append("Fix visual slide specs so they include the minimum renderable fields instead of placeholder types.")
    if "unsupported_numeric_claim" in codes:
        instructions.append("Remove invented numbers and keep only metrics explicitly supported by the source evidence.")

    if not instructions:
        instructions.extend(rubric.repair_priorities[:2])
    return instructions


def _unsupported_numeric_tokens(
    slides: Sequence[PlannedSlide],
    supported_tokens: Sequence[str],
) -> List[str]:
    if not supported_tokens:
        return []
    supported = {str(token).strip() for token in supported_tokens if str(token).strip()}
    unsupported: List[str] = []
    seen: set[str] = set()
    for slide in slides:
        if slide.slide_type not in {"chart", "stat_chart", "kpi"}:
            continue
        for token in slide.numeric_tokens:
            normalized = str(token).strip()
            if not normalized or normalized in supported or normalized in seen:
                continue
            seen.add(normalized)
            unsupported.append(normalized)
    return unsupported
