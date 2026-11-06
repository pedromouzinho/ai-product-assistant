"""End-to-end preparation and review flow for the next-generation PPTX lane.

This module intentionally stays renderer-agnostic. It assembles the grounded
objects needed to let a frontier model plan a deck, lets a critic evaluate the
proposed plan, and prepares a repair packet if the plan is rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from presentation_orchestrator import GroundedBrief, PresentationRequest, build_grounded_brief_from_sources
from presentation_planning import (
    CriticRubric,
    LegacyRenderPayload,
    PlannerPacket,
    build_critic_rubric,
    build_legacy_render_payload,
    build_planner_packet,
)
from presentation_reasoning import PresentationSource, select_authoritative_sources
from presentation_review import PlannedSlide, ReviewResult, build_repair_packet, review_planned_slides


@dataclass(frozen=True)
class PreparedPresentationRun:
    request: PresentationRequest
    selected_sources: Sequence[PresentationSource]
    brief: GroundedBrief
    planner_packet: PlannerPacket
    critic_rubric: CriticRubric
    legacy_render_payload: LegacyRenderPayload


@dataclass(frozen=True)
class PlanEvaluation:
    approved: bool
    review: ReviewResult
    repair_packet: str


def prepare_presentation_run(
    request: PresentationRequest,
    sources: Sequence[PresentationSource],
) -> PreparedPresentationRun:
    brief = build_grounded_brief_from_sources(request, sources)
    selected_sources = tuple(
        select_authoritative_sources(sources, capability=brief.capability.value)
    )
    planner_packet = build_planner_packet(brief)
    critic_rubric = build_critic_rubric(brief)
    legacy_render_payload = build_legacy_render_payload(request.title, brief, planner_packet)
    return PreparedPresentationRun(
        request=request,
        selected_sources=selected_sources,
        brief=brief,
        planner_packet=planner_packet,
        critic_rubric=critic_rubric,
        legacy_render_payload=legacy_render_payload,
    )


def evaluate_presentation_plan(
    prepared: PreparedPresentationRun,
    slides: Sequence[PlannedSlide],
) -> PlanEvaluation:
    review = review_planned_slides(slides, prepared.critic_rubric)
    repair_packet = build_repair_packet(review, prepared.critic_rubric)
    return PlanEvaluation(
        approved=review.approved,
        review=review,
        repair_packet=repair_packet,
    )
