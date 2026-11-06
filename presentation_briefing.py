"""Model-shaped narrative briefing for the next-generation PPTX lane."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from typing import Awaitable, Callable, Optional, Sequence

from llm_provider import llm_simple
from presentation_pipeline import PreparedPresentationRun
from presentation_planning import (
    build_critic_rubric,
    build_legacy_render_payload,
    build_planner_packet,
)

LLMCall = Callable[..., Awaitable[str]]
_BRIEF_SHAPING_TIMEOUT_SECONDS = 45
_BRIEF_SHAPING_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "presentation_brief_shaping",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "central_message": {"type": "string"},
                "narrative_arc": {"type": "array", "items": {"type": "string"}},
                "decisions": {"type": "array", "items": {"type": "string"}},
                "open_questions": {"type": "array", "items": {"type": "string"}},
                "visual_emphasis": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "central_message",
                "narrative_arc",
                "decisions",
                "open_questions",
                "visual_emphasis",
            ],
            "additionalProperties": False,
        },
    },
}


def build_brief_shaping_prompt(prepared: PreparedPresentationRun) -> str:
    brief = prepared.brief
    packet = prepared.planner_packet
    language_instruction = _language_instruction_for_prepared(prepared)
    lines = [
        "You are a senior product presentation strategist inside the company.",
        "Refine the narrative brief before slide planning.",
        "Do not create slides. Do not invent facts.",
        "Use only the evidence and source authority below.",
        language_instruction,
        "",
        "OUTPUT CONTRACT:",
        "- Return JSON only.",
        "- Keys: central_message, narrative_arc, decisions, open_questions, visual_emphasis.",
        "- narrative_arc must contain 3 to 5 acts.",
        "- decisions and open_questions should be concise arrays.",
        "- visual_emphasis should be a short array naming what deserves strongest visual treatment.",
        "",
        "CURRENT BRIEF:",
        f"Capability: {brief.capability.value}",
        f"Audience: {brief.audience.value}",
        f"Central message: {brief.central_message}",
        f"Narrative arc: {' -> '.join(brief.narrative_arc)}",
        f"Source authority: {brief.source_selection_note or 'n/a'}",
        "EVIDENCE SNAPSHOT:",
    ]
    lines.extend(f"- {line}" for line in packet.evidence_snapshot[:20])
    return "\n".join(lines)


def _language_instruction_for_prepared(prepared: PreparedPresentationRun) -> str:
    text = " ".join(
        part
        for part in (
            prepared.request.title,
            prepared.request.prompt,
            prepared.request.content[:4000] if prepared.request.content else "",
            prepared.request.context[:2000] if prepared.request.context else "",
            " ".join(prepared.planner_packet.evidence_snapshot[:8]),
        )
        if part
    )
    return _output_language_instruction(text)


def _output_language_instruction(text: str) -> str:
    normalized = f" {str(text or '').lower()} "
    pt_markers = (
        " para ",
        " com ",
        " não ",
        " comissão ",
        " próximo",
        " análise ",
        " impacto ",
        " negócios",
        " empresas",
        " estratégia",
        " decisão",
        " português",
        " portugal",
        "ção",
        "ções",
    )
    en_markers = (
        " executive ",
        " strategy ",
        " roadmap ",
        " decision ",
        " current ",
        " offering ",
        " trade-off",
        " implications ",
    )
    pt_score = sum(1 for marker in pt_markers if marker in normalized)
    en_score = sum(1 for marker in en_markers if marker in normalized)
    if pt_score >= en_score:
        return (
            "OUTPUT LANGUAGE: Portuguese (Portugal). "
            "Write all titles, bullets, agenda items, notes, KPI labels, "
            "table headers, captions, and process labels in Portuguese. "
            "Keep official product names as-is only when needed."
        )
    return (
        "OUTPUT LANGUAGE: English. "
        "Use English consistently across titles, bullets, notes, labels, and captions."
    )


async def shape_prepared_run(
    prepared: PreparedPresentationRun,
    *,
    llm_call: LLMCall = llm_simple,
    tier: str = "standard",
) -> PreparedPresentationRun:
    prompt = build_brief_shaping_prompt(prepared)
    try:
        raw = await asyncio.wait_for(
            llm_call(prompt, tier=tier, max_tokens=1800, response_format=_BRIEF_SHAPING_SCHEMA),
            timeout=_BRIEF_SHAPING_TIMEOUT_SECONDS,
        )
        shaped = _extract_json(raw)
        if not isinstance(shaped, dict):
            return prepared

        brief = prepared.brief
        central_message = str(shaped.get("central_message") or brief.central_message).strip() or brief.central_message
        narrative_arc = tuple(
            str(item).strip()
            for item in (shaped.get("narrative_arc") or brief.narrative_arc)
            if str(item).strip()
        )[:5] or tuple(brief.narrative_arc)
        decisions = tuple(
            str(item).strip()
            for item in (shaped.get("decisions") or brief.decisions)
            if str(item).strip()
        )[:5]
        open_questions = tuple(
            str(item).strip()
            for item in (shaped.get("open_questions") or brief.open_questions)
            if str(item).strip()
        )[:5]
        visual_emphasis = tuple(
            str(item).strip()
            for item in (shaped.get("visual_emphasis") or brief.visual_emphasis)
            if str(item).strip()
        )[:5]

        refined_brief = replace(
            brief,
            central_message=central_message,
            narrative_arc=narrative_arc,
            decisions=decisions or brief.decisions,
            open_questions=open_questions or brief.open_questions,
            visual_emphasis=visual_emphasis or brief.visual_emphasis,
        )
        refined_packet = build_planner_packet(refined_brief)
        refined_rubric = build_critic_rubric(refined_brief)
        refined_legacy = build_legacy_render_payload(prepared.request.title, refined_brief, refined_packet)
        return PreparedPresentationRun(
            request=prepared.request,
            selected_sources=prepared.selected_sources,
            brief=refined_brief,
            planner_packet=refined_packet,
            critic_rubric=refined_rubric,
            legacy_render_payload=refined_legacy,
        )
    except Exception:
        return prepared


def _extract_json(raw: str) -> Optional[dict]:
    text = str(raw or "").strip()
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
