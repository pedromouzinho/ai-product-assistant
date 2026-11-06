"""Model runtime for the next-generation PPTX lane.

This layer turns the orchestrator objects into concrete LLM prompts and runs a
small repair loop when the deterministic critic rejects the first plan.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import re
from typing import Awaitable, Callable, Optional, Sequence

from llm_provider import llm_simple
from presentation_briefing import _output_language_instruction, shape_prepared_run
from presentation_pipeline import PlanEvaluation, PreparedPresentationRun, evaluate_presentation_plan
from presentation_review import PlannedSlide

LLMCall = Callable[..., Awaitable[str]]
_PLANNER_TIMEOUT_SECONDS = 120
_REPAIR_TIMEOUT_SECONDS = 90
_ALLOWED_SLIDE_TYPES = (
    "title",
    "section",
    "content",
    "agenda",
    "kpi",
    "chart",
    "comparison",
    "table",
    "process",
    "stat_chart",
    "image",
    "logo",
    "screenshot",
    "closing",
)

_PRESENTATION_SLIDES_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "presentation_slide_specs",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "slides": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {"anyOf": [{"type": "string", "enum": list(_ALLOWED_SLIDE_TYPES)}, {"type": "null"}]},
                            "title": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                            "notes": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                            "bullets": {
                                "anyOf": [
                                    {"type": "array", "items": {"type": "string"}},
                                    {"type": "null"},
                                ]
                            },
                            "items": {
                                "anyOf": [
                                    {"type": "array", "items": {"type": "string"}},
                                    {"type": "null"},
                                ]
                            },
                            "kpis": {
                                "anyOf": [
                                    {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "value": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                                                "label": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                                                "description": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                                            },
                                            "required": ["value", "label", "description"],
                                            "additionalProperties": False,
                                        },
                                    },
                                    {"type": "null"},
                                ]
                            },
                            "chart_type": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                            "categories": {
                                "anyOf": [
                                    {"type": "array", "items": {"type": "string"}},
                                    {"type": "null"},
                                ]
                            },
                            "series": {
                                "anyOf": [
                                    {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "name": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                                                "values": {
                                                    "anyOf": [
                                                        {
                                                            "type": "array",
                                                            "items": {
                                                                "anyOf": [
                                                                    {"type": "number"},
                                                                    {"type": "integer"},
                                                                    {"type": "string"},
                                                                    {"type": "null"},
                                                                ]
                                                            },
                                                        },
                                                        {"type": "null"},
                                                    ]
                                                },
                                            },
                                            "required": ["name", "values"],
                                            "additionalProperties": False,
                                        },
                                    },
                                    {"type": "null"},
                                ]
                            },
                            "left_title": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                            "left_items": {
                                "anyOf": [
                                    {"type": "array", "items": {"type": "string"}},
                                    {"type": "null"},
                                ]
                            },
                            "right_title": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                            "right_items": {
                                "anyOf": [
                                    {"type": "array", "items": {"type": "string"}},
                                    {"type": "null"},
                                ]
                            },
                            "headers": {
                                "anyOf": [
                                    {"type": "array", "items": {"type": "string"}},
                                    {"type": "null"},
                                ]
                            },
                            "rows": {
                                "anyOf": [
                                    {
                                        "type": "array",
                                        "items": {
                                            "type": "array",
                                            "items": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                                        },
                                    },
                                    {"type": "null"},
                                ]
                            },
                            "steps": {
                                "anyOf": [
                                    {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "label": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                                                "description": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                                            },
                                            "required": ["label", "description"],
                                            "additionalProperties": False,
                                        },
                                    },
                                    {"type": "null"},
                                ]
                            },
                            "stat_value": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                            "stat_label": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                            "image_url": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                            "caption": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                        },
                        "required": [
                            "type",
                            "title",
                            "notes",
                            "bullets",
                            "items",
                            "kpis",
                            "chart_type",
                            "categories",
                            "series",
                            "left_title",
                            "left_items",
                            "right_title",
                            "right_items",
                            "headers",
                            "rows",
                            "steps",
                            "stat_value",
                            "stat_label",
                            "image_url",
                            "caption",
                        ],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["slides"],
            "additionalProperties": False,
        },
    },
}


@dataclass(frozen=True)
class PlanningLoopResult:
    prepared: PreparedPresentationRun
    slide_specs: Sequence[dict]
    slides: Sequence[PlannedSlide]
    evaluation: PlanEvaluation
    planner_prompt: str
    planner_raw: str
    repair_prompt: str = ""
    repair_raw: str = ""
    attempts: int = 1


def build_model_planner_prompt(prepared: PreparedPresentationRun) -> str:
    packet = prepared.planner_packet
    language_instruction = _language_instruction_for_prepared(prepared)
    lines = [
        "You are the presentation strategist for the product team inside the company.",
        "Your job is to design a board-ready PowerPoint plan that feels authored by a strong human consultant, not a summarizer.",
        "Use model judgment for narrative, structure, and visual mix.",
        "Do not invent facts or metrics. Only use evidence from the brief.",
        "Do not dump source order. Synthesize for the audience.",
        language_instruction,
        "",
        "OUTPUT CONTRACT:",
        "- Return JSON only.",
        "- Return an object with a single key: slides.",
        f"- Allowed slide types: {', '.join(_ALLOWED_SLIDE_TYPES)}.",
        "- Return body slides only. Do not include title or closing slides; the renderer adds those.",
        "- Keep slide titles unique and conclusion-led.",
        "- Return renderable slide specs for the existing PPTX renderer.",
        "- Each slide must include type, title, and notes.",
        "- Use these supported fields when relevant:",
        "  * content: bullets",
        "  * agenda: items",
        "  * kpi: kpis [{value,label,description?}]",
        "  * chart: chart_type, categories, series [{name,values}]",
        "  * comparison: left_title, left_items, right_title, right_items",
        "  * table: headers, rows",
        "  * process: steps [{label,description}]",
        "  * stat_chart: stat_value, stat_label, chart_type, categories, series",
        "  * image/logo/screenshot: image_url, caption",
        "- If you choose chart or stat_chart, you must fill categories and series with actual values.",
        "- If you choose comparison, you must fill left_title, right_title, left_items, and right_items.",
        "- If you choose table, you must fill headers and rows.",
        "- If you choose process, you must fill labeled steps.",
        "- If you choose kpi, you must fill value/label pairs.",
        "- bullets and list items must be short and selective, not source dumps.",
        "- Do not pack multiple bullets into one string with pipes (|) or semicolons; return one bullet/item per array entry.",
        "- Do not invent percentages, forecasts, adoption targets, or KPI numbers that are not explicitly supported by the brief.",
        "- If explicit numeric evidence is sparse, prefer comparison/table/process over synthetic KPI or chart values.",
        "",
        "STYLE + QUALITY RULES:",
        "- Premium, executive, editable, and visually intentional.",
        "- Every evidence-rich slide group should contain a meaningful visual.",
        "- Avoid repeated layouts and generic section-divider spam.",
        "- Avoid decorative accent lines under titles.",
        "",
        "PLANNING BRIEF:",
        packet.planning_brief_text,
    ]
    return "\n".join(lines).strip()


def build_model_repair_prompt(
    prepared: PreparedPresentationRun,
    *,
    current_slides: Sequence[PlannedSlide],
    evaluation: PlanEvaluation,
) -> str:
    current_plan_json = json.dumps(
        {
            "slides": [
                {
                    "type": slide.slide_type,
                    "title": slide.title,
                    "bullets": list(slide.bullets),
                    "has_visual": slide.has_visual,
                }
                for slide in current_slides
            ]
        },
        ensure_ascii=False,
        indent=2,
    )
    language_instruction = _language_instruction_for_prepared(prepared)
    lines = [
        "You are revising a rejected PPTX slide plan for the product team.",
        "The previous plan failed the critic. Repair it without inventing facts.",
        "Preserve the strongest ideas, but change structure and visual mix decisively where needed.",
        language_instruction,
        "",
        "OUTPUT CONTRACT:",
        "- Return JSON only.",
        "- Return an object with a single key: slides.",
        f"- Allowed slide types: {', '.join(_ALLOWED_SLIDE_TYPES)}.",
        "- Do not pack multiple bullets into one string with pipes (|) or semicolons; return one bullet/item per array entry.",
        "- Remove any invented numeric values that are not explicitly supported by the brief.",
        "",
        "ORIGINAL PLANNING BRIEF:",
        prepared.planner_packet.planning_brief_text,
        "",
        "CURRENT REJECTED PLAN:",
        current_plan_json,
        "",
        "CRITIC REPAIR PACKET:",
        evaluation.repair_packet,
    ]
    return "\n".join(lines).strip()


async def run_planning_loop(
    prepared: PreparedPresentationRun,
    *,
    llm_call: LLMCall = llm_simple,
    brief_tier: str = "standard",
    planner_tier: str = "pro",
    repair_tier: str = "standard",
    max_repairs: int = 1,
    shape_brief: bool = True,
) -> PlanningLoopResult:
    runtime_prepared = prepared
    if shape_brief:
        runtime_prepared = await shape_prepared_run(prepared, llm_call=llm_call, tier=brief_tier)

    planner_prompt = build_model_planner_prompt(runtime_prepared)
    try:
        planner_raw = await asyncio.wait_for(
            llm_call(
                planner_prompt,
                tier=planner_tier,
                max_tokens=4000,
                response_format=_PRESENTATION_SLIDES_SCHEMA,
            ),
            timeout=_PLANNER_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        raise RuntimeError("VNext PPTX planner timed out") from exc
    if _looks_like_provider_error(planner_raw):
        raise RuntimeError(f"VNext PPTX planner provider error: {str(planner_raw).strip()[:240]}")
    slide_specs = _parse_slide_specs(planner_raw)
    slides = _summarize_slide_specs(slide_specs)
    evaluation = evaluate_presentation_plan(runtime_prepared, slides)

    repair_prompt = ""
    repair_raw = ""
    attempts = 1

    while not evaluation.approved and attempts <= max_repairs:
        repair_prompt = build_model_repair_prompt(
            runtime_prepared,
            current_slides=slides,
            evaluation=evaluation,
        )
        try:
            repair_raw = await asyncio.wait_for(
                llm_call(
                    repair_prompt,
                    tier=repair_tier,
                    max_tokens=4000,
                    response_format=_PRESENTATION_SLIDES_SCHEMA,
                ),
                timeout=_REPAIR_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as exc:
            raise RuntimeError("VNext PPTX repair pass timed out") from exc
        if _looks_like_provider_error(repair_raw):
            raise RuntimeError(f"VNext PPTX repair provider error: {str(repair_raw).strip()[:240]}")
        repaired_slide_specs = _parse_slide_specs(repair_raw)
        if _is_parse_failure_specs(repaired_slide_specs):
            attempts += 1
            break
        repaired_slides = _summarize_slide_specs(repaired_slide_specs)
        attempts += 1
        repaired_evaluation = evaluate_presentation_plan(runtime_prepared, repaired_slides)
        slide_specs = repaired_slide_specs
        slides = repaired_slides
        evaluation = repaired_evaluation
        if evaluation.approved:
            break

    return PlanningLoopResult(
        prepared=runtime_prepared,
        slide_specs=slide_specs,
        slides=slides,
        evaluation=evaluation,
        planner_prompt=planner_prompt,
        planner_raw=planner_raw,
        repair_prompt=repair_prompt,
        repair_raw=repair_raw,
        attempts=attempts,
    )


def _parse_slide_specs(raw: str) -> Sequence[dict]:
    parsed = _extract_json(raw)
    slides_obj: Optional[Sequence[dict]] = None

    if isinstance(parsed, dict) and isinstance(parsed.get("slides"), list):
        slides_obj = parsed["slides"]
    elif isinstance(parsed, list):
        slides_obj = parsed

    if not slides_obj:
        return (
            {
                "type": "content",
                "title": "Planner output could not be parsed",
                "bullets": [raw[:240]],
                "notes": "Planner output could not be parsed cleanly.",
            },
        )

    normalized = []
    for item in slides_obj:
        if not isinstance(item, dict):
            continue
        slide_type = str(item.get("type") or "content").strip().lower()
        if slide_type not in _ALLOWED_SLIDE_TYPES:
            slide_type = "content"
        spec = dict(item)
        spec["type"] = slide_type
        spec["title"] = str(spec.get("title") or "").strip()
        spec["notes"] = str(spec.get("notes") or "").strip()

        if slide_type == "content":
            spec["bullets"] = _normalize_string_list(spec.get("bullets"), limit=6)
        elif slide_type == "agenda":
            spec["items"] = _normalize_string_list(spec.get("items"), limit=8)
        elif slide_type == "comparison":
            comparison_items = _normalize_string_list(spec.get("items"), limit=8)
            left_title = str(spec.get("left_title") or "").strip()
            spec["left_items"] = _normalize_string_list(spec.get("left_items"), limit=6)
            right_title = str(spec.get("right_title") or "").strip()
            spec["right_items"] = _normalize_string_list(spec.get("right_items"), limit=6)
            if comparison_items and (
                not left_title
                or not right_title
                or not spec["left_items"]
                or not spec["right_items"]
            ):
                inferred = _infer_comparison_from_items(comparison_items)
                if inferred:
                    left_title = left_title or inferred["left_title"]
                    right_title = right_title or inferred["right_title"]
                    spec["left_items"] = spec["left_items"] or inferred["left_items"]
                    spec["right_items"] = spec["right_items"] or inferred["right_items"]
            spec["left_title"] = left_title
            spec["right_title"] = right_title
        elif slide_type == "table":
            spec["headers"] = _normalize_string_list(spec.get("headers"), limit=8)
            rows = spec.get("rows") if isinstance(spec.get("rows"), list) else []
            normalized_rows = []
            for row in rows[:8]:
                if isinstance(row, list):
                    normalized_rows.append([str(cell).strip() for cell in row[:8]])
            spec["rows"] = normalized_rows
        elif slide_type == "process":
            steps = spec.get("steps") if isinstance(spec.get("steps"), list) else []
            normalized_steps = []
            for step in steps[:5]:
                if isinstance(step, dict):
                    normalized_steps.append(
                        {
                            "label": str(step.get("label") or "").strip(),
                            "description": str(step.get("description") or "").strip(),
                        }
                    )
            spec["steps"] = normalized_steps
        elif slide_type == "kpi":
            kpis = spec.get("kpis") if isinstance(spec.get("kpis"), list) else []
            normalized_kpis = []
            for kpi in kpis[:4]:
                if isinstance(kpi, dict):
                    normalized_kpis.append(
                        {
                            "value": str(kpi.get("value") or "").strip(),
                            "label": str(kpi.get("label") or "").strip(),
                            "description": str(kpi.get("description") or "").strip(),
                        }
                    )
            spec["kpis"] = normalized_kpis
        elif slide_type == "stat_chart":
            kpis = spec.get("kpis") if isinstance(spec.get("kpis"), list) else []
            normalized_kpis = []
            for kpi in kpis[:4]:
                if isinstance(kpi, dict):
                    normalized_kpis.append(
                        {
                            "value": str(kpi.get("value") or "").strip(),
                            "label": str(kpi.get("label") or "").strip(),
                            "description": str(kpi.get("description") or "").strip(),
                        }
                    )
            spec["kpis"] = normalized_kpis
            spec["chart_type"] = str(spec.get("chart_type") or "column").strip() or "column"
            spec["categories"] = _normalize_string_list(spec.get("categories"), limit=12)
            series = spec.get("series") if isinstance(spec.get("series"), list) else []
            normalized_series = []
            for item in series[:4]:
                if isinstance(item, dict):
                    normalized_series.append(
                        {
                            "name": str(item.get("name") or "").strip(),
                            "values": item.get("values") if isinstance(item.get("values"), list) else [],
                        }
                    )
            spec["series"] = normalized_series
            spec["stat_value"] = str(spec.get("stat_value") or "").strip()
            spec["stat_label"] = str(spec.get("stat_label") or "").strip()
            if normalized_kpis:
                spec["stat_value"] = spec["stat_value"] or normalized_kpis[0]["value"]
                spec["stat_label"] = spec["stat_label"] or normalized_kpis[0]["label"]

        normalized.append(spec)

    return tuple(normalized) or (
        {
            "type": "content",
            "title": "Planner returned no usable slides",
            "bullets": [],
            "notes": "Planner returned no usable slides.",
        },
    )


def _is_parse_failure_specs(specs: Sequence[dict]) -> bool:
    if len(specs) != 1:
        return False
    first = specs[0] if specs else {}
    if not isinstance(first, dict):
        return False
    return str(first.get("title") or "").strip().lower() == "planner output could not be parsed"


def _summarize_slide_specs(specs: Sequence[dict]) -> Sequence[PlannedSlide]:
    summary = []
    for spec in specs:
        slide_type = str(spec.get("type") or "content").strip().lower()
        title = str(spec.get("title") or "").strip()
        bullets: Sequence[str] = ()
        if slide_type == "content":
            bullets = tuple(_normalize_string_list(spec.get("bullets"), limit=8))
        elif slide_type == "agenda":
            bullets = tuple(_normalize_string_list(spec.get("items"), limit=8))
        elif slide_type == "comparison":
            bullets = tuple(
                _normalize_string_list(spec.get("left_items"), limit=4)
                + _normalize_string_list(spec.get("right_items"), limit=4)
            )
        elif slide_type == "table":
            headers = _normalize_string_list(spec.get("headers"), limit=8)
            bullets = tuple(headers)
        elif slide_type == "process":
            steps = spec.get("steps") if isinstance(spec.get("steps"), list) else []
            bullets = tuple(
                str(step.get("label") or "").strip()
                for step in steps
                if isinstance(step, dict) and str(step.get("label") or "").strip()
            )
        has_visual, render_issue = _evaluate_visual_renderability(spec)
        summary.append(
            PlannedSlide(
                slide_type=slide_type,
                title=title,
                bullets=bullets,
                numeric_tokens=_extract_numeric_tokens(spec),
                has_visual=has_visual,
                render_issue=render_issue,
            )
        )
    return tuple(summary)


def _evaluate_visual_renderability(spec: dict) -> tuple[bool, str]:
    slide_type = str(spec.get("type") or "content").strip().lower()

    if slide_type == "chart":
        categories = _normalize_string_list(spec.get("categories"), limit=12)
        series = spec.get("series") if isinstance(spec.get("series"), list) else []
        if categories and _series_has_values(series):
            return True, ""
        return False, "chart requires categories and series values"

    if slide_type == "stat_chart":
        categories = _normalize_string_list(spec.get("categories"), limit=12)
        series = spec.get("series") if isinstance(spec.get("series"), list) else []
        if (
            str(spec.get("stat_value") or "").strip()
            and str(spec.get("stat_label") or "").strip()
            and categories
            and _series_has_values(series)
        ):
            return True, ""
        return False, "stat_chart requires stat value, stat label, categories, and series values"

    if slide_type == "comparison":
        left_items = _normalize_string_list(spec.get("left_items"), limit=8)
        right_items = _normalize_string_list(spec.get("right_items"), limit=8)
        if (
            str(spec.get("left_title") or "").strip()
            and str(spec.get("right_title") or "").strip()
            and left_items
            and right_items
        ):
            return True, ""
        return False, "comparison requires both titles and item lists"

    if slide_type == "table":
        headers = _normalize_string_list(spec.get("headers"), limit=8)
        rows = spec.get("rows") if isinstance(spec.get("rows"), list) else []
        if headers and rows:
            return True, ""
        return False, "table requires headers and rows"

    if slide_type == "process":
        steps = spec.get("steps") if isinstance(spec.get("steps"), list) else []
        if any(
            isinstance(step, dict) and str(step.get("label") or "").strip()
            for step in steps
        ):
            return True, ""
        return False, "process requires labeled steps"

    if slide_type == "kpi":
        kpis = spec.get("kpis") if isinstance(spec.get("kpis"), list) else []
        if any(
            isinstance(kpi, dict)
            and str(kpi.get("value") or "").strip()
            and str(kpi.get("label") or "").strip()
            for kpi in kpis
        ):
            return True, ""
        return False, "kpi requires value/label pairs"

    if slide_type in {"image", "logo", "screenshot"}:
        if str(spec.get("image_url") or "").strip() or spec.get("image_bytes"):
            return True, ""
        return False, "image slide requires image_url or prefetched image bytes"

    return slide_type in {"content", "agenda", "title", "section", "closing"}, ""


def _series_has_values(series) -> bool:
    if not isinstance(series, list):
        return False
    for item in series:
        if not isinstance(item, dict):
            continue
        values = item.get("values")
        if isinstance(values, list) and any(str(value).strip() for value in values):
            return True
    return False


def _normalize_string_list(value, *, limit: int) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        text = str(item).strip()
        if not text:
            continue
        if "\n" not in text and text.count(" | ") >= 2:
            parts = [part.strip(" -•") for part in text.split(" | ") if part.strip()]
            normalized.extend(parts)
            continue
        normalized.append(text)
    return normalized[:limit]


def _extract_numeric_tokens(spec: dict) -> Sequence[str]:
    values: list[str] = []

    def _append_from(value):
        if isinstance(value, (int, float)):
            values.append(str(value))
        elif isinstance(value, str):
            for match in re.finditer(r"\d+(?:[.,]\d+)?", value):
                values.append(match.group(0).replace(",", "."))
        elif isinstance(value, list):
            for item in value:
                _append_from(item)
        elif isinstance(value, dict):
            for item in value.values():
                _append_from(item)

    for key in ("kpis", "stat_value", "series", "headers", "rows", "bullets", "title", "notes"):
        _append_from(spec.get(key))
    deduped: list[str] = []
    seen: set[str] = set()
    for token in values:
        normalized = str(token).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return tuple(deduped)


def _infer_comparison_from_items(items: Sequence[str]) -> dict[str, object] | None:
    parsed = []
    for entry in items[:2]:
        if ":" not in entry:
            continue
        title, raw_items = entry.split(":", 1)
        title = title.strip()
        row_items = [part.strip(" -•") for part in re.split(r"[;,]", raw_items) if part.strip()]
        if title and row_items:
            parsed.append((title, row_items[:6]))
    if len(parsed) < 2:
        return None
    return {
        "left_title": parsed[0][0],
        "left_items": parsed[0][1],
        "right_title": parsed[1][0],
        "right_items": parsed[1][1],
    }


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


def _extract_json(raw: str):
    text = str(raw or "").strip()
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = text.find(open_ch)
        end = text.rfind(close_ch)
        if start == -1 or end <= start:
            continue
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            continue
    return None


def _looks_like_provider_error(raw: str) -> bool:
    text = str(raw or "").strip().lower()
    if not text:
        return False
    return (
        text.startswith("erro real detetado ao contactar o modelo")
        or text.startswith("erro real detetado ao contactar os modelos")
        or "access denied due to invalid subscription key" in text
        or "unable to resolve azure bearer token" in text
    )
