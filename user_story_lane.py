"""Dedicated user story lane: context pack, structured draft, validation and publish."""

from __future__ import annotations

import asyncio
import difflib
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from config import (
    CHAT_TOOLRESULT_BLOB_CONTAINER,
    DEVOPS_AREAS,
    STORY_CONTEXT_CURATED_TOP,
    STORY_CONTEXT_DOC_TOP,
    STORY_CONTEXT_FIGMA_FILES_TOP,
    STORY_CONTEXT_FIGMA_FLOW_TOP,
    STORY_CONTEXT_SIMILAR_TOP,
    STORY_LANE_ENABLED,
    STORY_MAX_CLARIFICATIONS,
)
from figma_story_map import search_story_design_map, serialize_design_match
from llm_provider import llm_with_fallback
from story_curated_corpus import (
    build_curated_story_entry,
    get_curated_story_corpus_summary,
    search_curated_story_examples,
    serialize_curated_story_match,
)
from story_devops_index import search_story_devops_index
from story_domain_profiles import select_story_domain_profile
from story_examples_index import (
    delete_story_example_index_document,
    search_story_examples_index,
    upsert_story_example_index_document,
)
from story_feature_packs import select_story_feature_pack, serialize_feature_pack
from story_knowledge_index import search_story_knowledge_index
from story_flow_map import search_story_flow_map, serialize_story_flow_match
from story_policy_packs import select_story_policy_pack
from storage import blob_download_json, blob_upload_json, table_insert, table_merge, table_query
from structured_schemas import USER_STORY_LANE_DRAFT_SCHEMA
from tools_devops import (
    US_TEMPLATE_VERSION,
    _canonicalize_area_path,
    _validate_us_output,
    create_workitem_in_devops,
    tool_query_hierarchy,
    tool_query_workitems,
)
from tools_figma import tool_search_figma
from tools_knowledge import tool_search_website, tool_search_workitems
from tools_learning import _load_writer_profile
from tools_upload import tool_search_uploaded_document
from utils import odata_escape, safe_blob_component

logger = logging.getLogger(__name__)

_DRAFTS_TABLE = "UserStoryDrafts"
_FEEDBACK_TABLE = "UserStoryFeedback"
_CURATED_TABLE = "UserStoryCurated"
_CURATED_STATUS_CANDIDATE = "candidate"
_CURATED_STATUS_ACTIVE = "active"
_CURATED_STATUS_REJECTED = "rejected"
_CURATED_STATUS_INACTIVE = "inactive"
_EVAL_MIN_DOMAIN_SAMPLES = 3
_EVAL_LOW_CORPUS_THRESHOLD = 6
_EVAL_HIGH_EDIT_BURDEN = 0.22
_EVAL_LOW_PUBLISH_RATE = 0.4
_EVAL_LOW_QUALITY = 0.78
_EVAL_LOW_PLACEMENT_CONFIDENCE = 0.62
_EVAL_CURATION_MIN_QUALITY = 0.74
_EVAL_CURATION_MIN_SIMILARITY = 0.65
_LEXICON_HINTS = [
    "CTA",
    "Primary CTA",
    "Secondary CTA",
    "Card",
    "Bloco",
    "Dropdown",
    "Input",
    "Link",
    "Stepper",
    "Modal",
    "Toast",
    "Hero",
    "Tab",
    "Sidebar",
]
_ACTOR_HINTS = (
    "cliente",
    "gestor",
    "utilizador",
    "operador",
    "administrador",
    "analista",
    "user",
    "customer",
    "agent",
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _draft_partition_key(user_sub: str) -> str:
    return f"user:{str(user_sub or 'anon').strip() or 'anon'}"


def _partition_user_sub(partition_key: str) -> str:
    value = str(partition_key or "").strip()
    if value.startswith("user:"):
        return value.split(":", 1)[1].strip()
    return value


def _safe_json_load(text: str) -> dict:
    if not isinstance(text, str):
        return {}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _ensure_lane_enabled() -> None:
    if not STORY_LANE_ENABLED:
        raise RuntimeError("User story lane desativada em configuração.")


def _normalize_story_text(value: str) -> str:
    text = str(value or "").strip()
    return re.sub(r"\s+", " ", text)


def _clip(value: str, max_len: int = 400) -> str:
    text = _normalize_story_text(value)
    return text[:max_len]


def _tokenize(value: str) -> set[str]:
    text = re.sub(r"[^a-z0-9áàâãéèêíïóôõöúç ]+", " ", str(value or "").lower())
    return {token for token in text.split() if len(token) >= 3}


def _overlap_score(query: str, candidate: str) -> float:
    query_tokens = _tokenize(query)
    candidate_tokens = _tokenize(candidate)
    if not query_tokens or not candidate_tokens:
        return 0.0
    shared = query_tokens & candidate_tokens
    return len(shared) / max(1, len(query_tokens))


def _coerce_list(value: Any, max_items: int = 8) -> list[str]:
    if isinstance(value, list):
        items = value
    elif isinstance(value, str):
        items = [segment.strip() for segment in re.split(r"[\n;]+", value) if segment.strip()]
    else:
        items = []
    return [_clip(item, 500) for item in items[:max_items] if str(item or "").strip()]


def _format_generation_source_line(source: dict) -> str:
    if not isinstance(source, dict):
        return ""
    key = _clip(str(source.get("key", "") or ""), 80)
    title = _clip(str(source.get("title", "") or "Fonte"), 140)
    snippet = _clip(str(source.get("snippet", "") or ""), 220)
    source_type = _clip(str(source.get("type", "") or source.get("origin", "") or ""), 40)
    parts = [part for part in [key, source_type] if part]
    prefix = f"- {title}"
    if parts:
        prefix += f" [{', '.join(parts)}]"
    if snippet:
        prefix += f": {snippet}"
    return prefix


def _format_generation_example_line(example: dict) -> str:
    if not isinstance(example, dict):
        return ""
    title = _clip(str(example.get("title", "") or "Exemplo curado"), 160)
    title_pattern = _clip(str(example.get("title_pattern", "") or ""), 120)
    provenance = _clip(str(example.get("provenance_excerpt", "") or ""), 180)
    behavior = _clip(str(example.get("behavior_excerpt", "") or ""), 180)
    domain = _clip(str(example.get("domain", "") or ""), 60)
    author = _clip(str(example.get("author", "") or ""), 60)
    descriptor = " · ".join(part for part in [domain, author] if part)
    notes = " | ".join(part for part in [title_pattern, provenance, behavior] if part)
    line = f"- {title}"
    if descriptor:
        line += f" [{descriptor}]"
    if notes:
        line += f": {notes}"
    return line


def _format_design_flow_line(item: dict) -> str:
    if not isinstance(item, dict):
        return ""
    title = _clip(str(item.get("title", "") or item.get("name", "") or "Frame"), 160)
    ui_components = ", ".join(_coerce_list(item.get("ui_components", []), max_items=5))
    snippet = _clip(str(item.get("snippet", "") or ""), 180)
    domain = _clip(str(item.get("domain", "") or ""), 60)
    parts = [part for part in [domain, ui_components] if part]
    line = f"- {title}"
    if parts:
        line += f" [{'; '.join(parts)}]"
    if snippet:
        line += f": {snippet}"
    return line


def _format_feature_story_line(item: dict) -> str:
    if not isinstance(item, dict):
        return ""
    title = _clip(str(item.get("title", "") or ""), 160)
    snippet = _clip(str(item.get("snippet", "") or item.get("description", "") or ""), 180)
    line = f"- {title or 'Story relacionada'}"
    if snippet:
        line += f": {snippet}"
    return line


def _build_generation_brief(context_pack: dict, *, lean: bool = False) -> str:
    intake = context_pack.get("intake", {}) if isinstance(context_pack, dict) else {}
    placement = context_pack.get("placement", {}) if isinstance(context_pack, dict) else {}
    policy_pack = context_pack.get("policy_pack", {}) if isinstance(context_pack, dict) else {}
    story_policy_pack = context_pack.get("story_policy_pack", {}) if isinstance(context_pack, dict) else {}
    domain_profile = context_pack.get("domain_profile", {}) if isinstance(context_pack, dict) else {}
    design_flow = context_pack.get("design_flow", {}) if isinstance(context_pack, dict) else {}
    feature_pack = context_pack.get("feature_pack", {}) if isinstance(context_pack, dict) else {}

    selected_epic = placement.get("selected_epic", {}) if isinstance(placement.get("selected_epic"), dict) else {}
    selected_feature = placement.get("selected_feature", {}) if isinstance(placement.get("selected_feature"), dict) else {}
    placement_reasoning = _coerce_list(placement.get("reasoning", []), max_items=3)
    missing_fields = _coerce_list(context_pack.get("missing_fields", []), max_items=3)
    clarification_questions = _coerce_list(context_pack.get("clarification_questions", []), max_items=2)
    style_hint = _clip(str(policy_pack.get("writer_profile_summary", "") or ""), 700)

    sources = [
        line
        for line in (
            _format_generation_source_line(source)
            for source in list(context_pack.get("sources", []) or [])[: (5 if lean else 8)]
        )
        if line
    ]
    curated_examples = [
        line
        for line in (
            _format_generation_example_line(example)
            for example in list(context_pack.get("curated_examples", []) or [])[: (2 if lean else 3)]
        )
        if line
    ]
    design_lines = [
        line
        for line in (
            _format_design_flow_line(item)
            for item in list((design_flow.get("matches", []) if isinstance(design_flow, dict) else []) or [])[: (2 if lean else 3)]
        )
        if line
    ]
    sibling_lines = [
        line
        for line in (
            _format_feature_story_line(item)
            for item in list(context_pack.get("feature_siblings", []) or [])[:2]
        )
        if line
    ]

    sections = [
        "Pedido do Product Owner",
        f"- Objetivo: {_clip(str(intake.get('objective', '') or ''), 260) or 'n/a'}",
        f"- Área/equipa: {_clip(str(intake.get('team_scope', '') or ''), 160) or 'n/a'}",
        f"- Feature/épico fornecido: {_clip(str(intake.get('epic_or_feature', '') or ''), 180) or 'n/a'}",
        f"- Contexto extra: {_clip(str(intake.get('context', '') or ''), 320) or 'n/a'}",
        "",
        "Encaixe provável da jornada",
        f"- Epic: {_clip(str(selected_epic.get('title', '') or ''), 180) or 'n/a'}",
        f"- Feature: {_clip(str(selected_feature.get('title', '') or ''), 180) or 'n/a'}",
        f"- Area path resolvida: {_clip(str(placement.get('resolved_area_path', '') or intake.get('canonical_area_path', '') or ''), 180) or 'n/a'}",
    ]
    if placement_reasoning:
        sections.extend(placement_reasoning)
    if domain_profile.get("domain") or story_policy_pack.get("domain"):
        sections.extend(
            [
                "",
                "House style e domínio",
                f"- Domínio dominante: {_clip(str(domain_profile.get('domain', '') or story_policy_pack.get('domain', '') or ''), 120) or 'n/a'}",
                f"- Título canónico: {_clip(str(story_policy_pack.get('canonical_title_pattern', '') or ''), 180) or 'n/a'}",
                f"- Léxico preferido: {', '.join(_coerce_list(policy_pack.get('preferred_lexicon', []), max_items=8)) or 'n/a'}",
            ]
        )
    if curated_examples:
        sections.extend(["", "Exemplos curados mais próximos", *curated_examples])
    if design_lines:
        sections.extend(["", "Frames e passos observáveis", *design_lines])
    if sibling_lines:
        sections.extend(["", "Stories irmãs da mesma feature", *sibling_lines])
    if sources:
        sections.extend(["", "Evidência observável e fontes", *sources])
    feature_notes = _coerce_list(feature_pack.get("notes", []), max_items=3)
    if feature_notes:
        sections.extend(["", "Notas do pack da feature", *feature_notes])
    if missing_fields or clarification_questions:
        sections.extend(["", "Lacunas a gerir sem bloquear"])
        if missing_fields:
            sections.extend([f"- Missing field: {item}" for item in missing_fields])
        if clarification_questions:
            sections.extend([f"- Clarificação opcional: {item}" for item in clarification_questions])
    if style_hint and not lean:
        sections.extend(
            [
                "",
                "Writer profile opcional",
                f"- Usa só se não colidir com a evidência: {style_hint}",
            ]
        )
    return "\n".join(part for part in sections if part is not None)


def _normalize_scalar(value: Any) -> str:
    return _normalize_story_text(str(value or ""))


def _normalize_dict(value: Any) -> dict:
    return dict(value) if isinstance(value, dict) else {}


def _normalize_list_items(items: Any) -> list[str]:
    normalized = []
    for item in items if isinstance(items, list) else []:
        text = _normalize_scalar(item)
        if text:
            normalized.append(text)
    return normalized


def _normalize_acceptance_items(items: Any) -> list[str]:
    normalized = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict):
            item_id = _normalize_scalar(item.get("id", ""))
            text = _normalize_scalar(item.get("text", ""))
            value = " | ".join(part for part in [item_id, text] if part)
        else:
            value = _normalize_scalar(item)
        if value:
            normalized.append(value)
    return normalized


def _normalize_test_items(items: Any) -> list[str]:
    normalized = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            text = _normalize_scalar(item)
            if text:
                normalized.append(text)
            continue
        value = " | ".join(
            part
            for part in [
                _normalize_scalar(item.get("id", "")),
                _normalize_scalar(item.get("title", "")),
                _normalize_scalar(item.get("given", "")),
                _normalize_scalar(item.get("when", "")),
                _normalize_scalar(item.get("then", "")),
            ]
            if part
        )
        if value:
            normalized.append(value)
    return normalized


def _extract_workitem_id(raw_value: str) -> Optional[int]:
    text = str(raw_value or "").strip()
    if not text:
        return None
    match = re.search(r"(?:edit/|#|^)(\d{3,8})(?:\D|$)", text)
    if match:
        try:
            return int(match.group(1))
        except Exception:
            return None
    if text.isdigit():
        try:
            return int(text)
        except Exception:
            return None
    return None


def _detect_missing_fields(objective: str, context: str, epic_or_feature: str) -> list[str]:
    objective_text = str(objective or "").strip()
    merged = " ".join(part for part in (objective, context, epic_or_feature) if part)
    merged_lower = merged.lower()
    missing = []
    if not any(token in merged_lower for token in _ACTOR_HINTS):
        missing.append("actor principal")
    if len(objective_text.split()) < 5:
        missing.append("trigger/objetivo")
    if not epic_or_feature and "flux" not in merged_lower and "ecra" not in merged_lower and "journey" not in merged_lower:
        missing.append("boundary funcional")
    if not re.search(r"\b(valida|limite|regra|erro|bloque|obriga|must|deve)\b", merged_lower):
        missing.append("regra de negócio crítica")
    if not re.search(r"\baceita[cç][aã]o|crit[eé]rio|ca-|teste|cenario|cenário\b", merged_lower):
        missing.append("critério de aceitação mínimo")
    return missing


def _build_clarification_questions(missing_fields: list[str]) -> list[str]:
    question_map = {
        "actor principal": "Quem é o ator principal desta user story e em que contexto usa este fluxo?",
        "trigger/objetivo": "Qual é o trigger exato e o resultado que o utilizador quer atingir neste passo?",
        "boundary funcional": "Em que fluxo, página ou área funcional do site/app esta história se encaixa?",
        "regra de negócio crítica": "Existe alguma regra de validação, limite, condição legal ou restrição de negócio que seja obrigatória?",
        "critério de aceitação mínimo": "Qual é o mínimo comportamento observável que tens de aceitar como pronto?",
    }
    questions = []
    for field_name in missing_fields:
        question = question_map.get(field_name)
        if question and question not in questions:
            questions.append(question)
        if len(questions) >= max(1, STORY_MAX_CLARIFICATIONS):
            break
    return questions


def _source_key(source: dict) -> str:
    return str(source.get("key") or source.get("id") or uuid.uuid4().hex[:8])


def _serialize_source(source: dict) -> dict:
    return {
        "key": _source_key(source),
        "type": str(source.get("type", "") or ""),
        "title": _clip(source.get("title", ""), 180),
        "snippet": _clip(source.get("snippet", ""), 500),
        "url": _clip(source.get("url", ""), 400),
        "score": round(float(source.get("score", 0.0) or 0.0), 4),
        "origin": str(source.get("origin", "") or ""),
    }


def _safe_wiql_literal_local(value: str, max_len: int = 160) -> str:
    return str(value or "").strip().replace("'", "''")[:max_len]


def _normalize_match_text(value: str) -> str:
    text = re.sub(r"[^a-z0-9áàâãéèêíïóôõöúç ]+", " ", str(value or "").lower())
    return re.sub(r"\s+", " ", text).strip()


def _unique_strings(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized.lower() in seen:
            continue
        seen.add(normalized.lower())
        result.append(normalized)
    return result


def _candidate_text(item: dict) -> str:
    return " ".join(
        str(item.get(key, "") or "")
        for key in ("title", "area", "description", "acceptance_criteria", "tags", "parent_title")
        if str(item.get(key, "") or "").strip()
    )


def _parent_anchor_candidate(item: dict, *, expected_parent_type: str, inferred_type: str) -> dict:
    parent_id = str(item.get("parent_id", "") or "").strip()
    parent_title = str(item.get("parent_title", "") or "").strip()
    parent_type = str(item.get("parent_type", "") or "").strip()
    if not parent_id or not parent_title or parent_type.lower() != str(expected_parent_type or "").strip().lower():
        return {}
    inferred_score = float(item.get("_placement_score", item.get("score", 0.58)) or 0.58)
    return {
        "id": int(parent_id) if parent_id.isdigit() else parent_id,
        "type": inferred_type,
        "title": parent_title,
        "area": item.get("area", ""),
        "url": "",
        "_placement_score": round(max(0.48, min(0.94, inferred_score - 0.05)), 4),
    }


async def _enrich_candidate_from_search(item: dict) -> dict:
    candidate = dict(item or {})
    title = str(candidate.get("title", "") or "").strip()
    item_id = str(candidate.get("id", "") or "").strip()
    item_type = str(candidate.get("type", "") or candidate.get("tag", "") or "").strip()
    if not title:
        return candidate
    try:
        result = await search_story_devops_index(
            query_text=title,
            work_item_types=[item_type] if item_type else ["Epic", "Feature", "User Story"],
            top=5,
        )
    except Exception as exc:
        logger.warning("[UserStoryLane] enrich candidate from search failed: %s", exc)
        return candidate
    items = list(result.get("items", []) or []) if isinstance(result, dict) else []
    match = None
    for current in items:
        if item_id and str(current.get("id", "") or "").strip() == item_id:
            match = current
            break
    if match is None and items:
        match = items[0]
    if not isinstance(match, dict):
        return candidate
    for key in ("parent_id", "parent_title", "parent_type", "score", "content"):
        if match.get(key) not in (None, ""):
            candidate[key] = match.get(key)
    return candidate


def _score_backlog_candidate(
    item: dict,
    *,
    query_text: str,
    dominant_domain: str = "",
    feature_hint: str = "",
    curated_examples: list[dict] | None = None,
) -> float:
    score = _overlap_score(query_text, _candidate_text(item))
    title_norm = _normalize_match_text(item.get("title", ""))
    if dominant_domain:
        domain_norm = _normalize_match_text(dominant_domain)
        if domain_norm and domain_norm in title_norm:
            score += 0.45
    if feature_hint:
        hint_norm = _normalize_match_text(feature_hint)
        if hint_norm and hint_norm in title_norm:
            score += 0.35
    for example in curated_examples or []:
        for field in ("domain", "journey", "flow"):
            field_value = _normalize_match_text(example.get(field, ""))
            if field_value and field_value in title_norm:
                score += 0.12
    return round(score, 4)


def _expand_design_flow_hints(values: list[str]) -> list[str]:
    expanded: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        expanded.append(text)
        parts = re.split(r"\s*[|/>,]\s*|\s+[–—-]\s+", text)
        for part in parts:
            candidate = str(part or "").strip(" -–—")
            if not candidate:
                continue
            if len(candidate) > 80:
                continue
            expanded.append(candidate)
    return _unique_strings(expanded)


def _score_design_flow_candidate(
    item: dict,
    *,
    query_text: str,
    hints: list[str],
    design_entry: dict,
) -> float:
    search_text = " ".join(
        part
        for part in [
            str(item.get("name", "") or ""),
            str(item.get("page_name", "") or ""),
            str(item.get("file_name", "") or ""),
            " ".join(str(value) for value in item.get("ui_components", []) if value),
            " ".join(str(value) for value in item.get("transition_targets", []) if value),
            str(design_entry.get("domain", "") or ""),
            " ".join(str(value) for value in design_entry.get("journeys", []) if value),
            str(design_entry.get("site_placement", "") or ""),
        ]
        if part
    )
    score = _overlap_score(query_text, search_text)
    normalized_search = _normalize_match_text(search_text)
    normalized_name = _normalize_match_text(item.get("name", ""))
    normalized_page = _normalize_match_text(item.get("page_name", ""))
    for hint in hints:
        normalized_hint = _normalize_match_text(hint)
        if not normalized_hint:
            continue
        if normalized_hint in normalized_name:
            score += 0.65
        elif normalized_hint in normalized_page:
            score += 0.45
        elif normalized_hint in normalized_search:
            score += 0.2
    item_type = str(item.get("type", "") or "").strip().upper()
    if item_type == "FRAME":
        score += 0.18
    elif item_type == "SECTION":
        score += 0.12
    elif item_type == "PAGE":
        score += 0.08
    transition_targets = item.get("transition_targets", []) or []
    if isinstance(transition_targets, list) and transition_targets:
        score += min(0.08, 0.02 * len(transition_targets))
    ui_components = item.get("ui_components", []) or []
    if isinstance(ui_components, list) and ui_components:
        component_overlap = _tokenize(" ".join(str(value) for value in ui_components))
        if component_overlap & _tokenize(query_text):
            score += 0.1
    score += min(0.12, float(design_entry.get("currentness_score", 0.0) or 0.0) * 0.12)
    return round(score, 4)


async def _retrieve_design_flow_context(
    *,
    objective: str,
    context: str,
    epic_or_feature: str,
    placement: dict,
    design_context: dict,
    curated_context: dict,
) -> dict:
    design_matches = list((design_context or {}).get("matches", []) or [])[: max(1, STORY_CONTEXT_FIGMA_FILES_TOP)]
    curated_matches = list((curated_context or {}).get("matches", []) or [])
    top_curated = curated_matches[0] if curated_matches else {}
    selected_feature = (placement or {}).get("selected_feature", {}) or {}
    selected_epic = (placement or {}).get("selected_epic", {}) or {}

    hint_values = _expand_design_flow_hints(
        [
            objective,
            context,
            epic_or_feature,
            str(selected_feature.get("title", "") or ""),
            str(selected_epic.get("title", "") or ""),
            str(top_curated.get("flow", "") or ""),
            str(top_curated.get("journey", "") or ""),
            str(top_curated.get("domain", "") or ""),
            *(str(value) for entry in design_matches for value in entry.get("journeys", [])[:3]),
        ]
    )
    persisted = search_story_flow_map(
        objective=objective,
        context=context,
        team_scope=str((placement or {}).get("resolved_area_path", "") or ""),
        epic_or_feature=epic_or_feature,
        dominant_domain=str((design_context or {}).get("dominant_domain", "") or ""),
        top=max(1, STORY_CONTEXT_FIGMA_FLOW_TOP),
    )
    persisted_matches = [serialize_story_flow_match(item) for item in persisted.get("matches", [])]
    if not design_matches and persisted_matches:
        return {
            "matches": persisted_matches[: max(1, STORY_CONTEXT_FIGMA_FLOW_TOP)],
            "notes": list(persisted.get("notes", [])),
            "hints": hint_values[:8],
        }
    if not design_matches:
        return {"matches": persisted_matches[: max(1, STORY_CONTEXT_FIGMA_FLOW_TOP)], "notes": list(persisted.get("notes", [])), "hints": hint_values[:8]}

    query_text = " | ".join(
        part
        for part in [
            objective,
            context,
            epic_or_feature,
            str(selected_feature.get("title", "") or ""),
            str(selected_epic.get("title", "") or ""),
            str(top_curated.get("flow", "") or ""),
            str(top_curated.get("journey", "") or ""),
            str((design_context or {}).get("dominant_domain", "") or ""),
        ]
        if str(part or "").strip()
    )

    async def _search_file(design_entry: dict) -> list[dict]:
        file_key = str(design_entry.get("file_key", "") or "").strip()
        if not file_key:
            return []
        try:
            result = await tool_search_figma(query="", file_key=file_key)
        except Exception as exc:
            logger.warning("[UserStoryLane] figma flow retrieval failed for %s: %s", file_key, exc)
            return []
        if not isinstance(result, dict) or result.get("error"):
            if isinstance(result, dict) and result.get("error"):
                logger.info("[UserStoryLane] figma flow retrieval skipped for %s: %s", file_key, result.get("error"))
            return []

        scored: list[dict] = []
        for item in list(result.get("items", []) or [])[:100]:
            item_type = str(item.get("type", "") or "").strip().upper()
            if item_type not in {"FRAME", "PAGE", "SECTION"}:
                continue
            score = _score_design_flow_candidate(
                item,
                query_text=query_text,
                hints=hint_values,
                design_entry=design_entry,
            )
            if score < 0.35:
                continue
            snippet_parts = []
            page_name = str(item.get("page_name", "") or "").strip()
            if page_name:
                snippet_parts.append(f"Página {page_name}.")
            ui_components = [str(value) for value in item.get("ui_components", []) if str(value or "").strip()]
            if ui_components:
                snippet_parts.append(f"Componentes: {', '.join(ui_components[:4])}.")
            transition_targets = item.get("transition_targets", []) or []
            if isinstance(transition_targets, list) and transition_targets:
                snippet_parts.append(f"Tem {len(transition_targets)} transições ou próximos passos.")
            snippet_parts.append(f"Domínio {design_entry.get('domain', '')}.")
            snippet_parts.append(str(design_entry.get("routing_note", "") or ""))
            scored.append(
                {
                    "key": f"figma-flow:{file_key}:{item.get('id', '')}",
                    "type": "design_flow_frame",
                    "title": " · ".join(
                        part for part in [str(item.get("name", "") or "").strip(), page_name] if part
                    ),
                    "snippet": " ".join(part for part in snippet_parts if part).strip(),
                    "url": str(item.get("url", "") or ""),
                    "score": score,
                    "origin": "figma_frame_search",
                    "domain": str(design_entry.get("domain", "") or ""),
                    "file_key": file_key,
                    "file_title": str(design_entry.get("title", "") or ""),
                    "page_name": page_name,
                    "frame_name": str(item.get("name", "") or ""),
                    "node_id": str(item.get("id", "") or ""),
                    "ui_components": ui_components[:6],
                }
            )
        return scored

    results = await asyncio.gather(*[_search_file(entry) for entry in design_matches], return_exceptions=True)
    deduped: dict[str, dict] = {}
    for result in results:
        if isinstance(result, Exception):
            logger.warning("[UserStoryLane] figma flow gather failed: %s", result)
            continue
        for item in result:
            key = str(item.get("key", "") or "").strip()
            current = deduped.get(key)
            if current is None or float(item.get("score", 0.0) or 0.0) > float(current.get("score", 0.0) or 0.0):
                deduped[key] = item
    for item in persisted_matches:
        key = str(item.get("key", "") or "").strip()
        current = deduped.get(key)
        if current is None or float(item.get("score", 0.0) or 0.0) > float(current.get("score", 0.0) or 0.0):
            deduped[key] = item

    ranked = sorted(
        deduped.values(),
        key=lambda item: (
            1 if str(item.get("origin", "") or "").strip() == "figma_frame_search" else 0,
            float(item.get("score", 0.0) or 0.0),
        ),
        reverse=True,
    )
    notes = []
    if ranked:
        notes.append(f"Frame/step Figma mais provável: {ranked[0].get('title', '')}.")
    if hint_values:
        notes.append(f"Hints usados no matching visual: {', '.join(hint_values[:4])}.")
    notes.extend(list(persisted.get("notes", []))[:2])
    return {
        "matches": ranked[: max(1, STORY_CONTEXT_FIGMA_FLOW_TOP)],
        "notes": notes[:4],
        "hints": hint_values[:8],
    }


async def _query_candidates_by_title_hint(
    work_item_type: str,
    *,
    title_hint: str,
    area_path: str = "",
    top: int = 8,
) -> list[dict]:
    hint = str(title_hint or "").strip()
    if not hint:
        return []

    clauses = [f"[System.WorkItemType] = '{_safe_wiql_literal_local(work_item_type, 80)}'"]
    if area_path and str(work_item_type or "").strip().lower() != "epic":
        clauses.append(f"[System.AreaPath] UNDER '{_safe_wiql_literal_local(area_path, 300)}'")
    clauses.append(f"[System.Title] CONTAINS '{_safe_wiql_literal_local(hint, 120)}'")

    result = await tool_query_workitems(
        " AND ".join(clauses),
        fields=[
            "System.Id",
            "System.Title",
            "System.WorkItemType",
            "System.AreaPath",
            "System.State",
            "System.Description",
            "Microsoft.VSTS.Common.AcceptanceCriteria",
            "System.Tags",
        ],
        top=top,
    )
    if not isinstance(result, dict):
        return []
    return result.get("items", []) if isinstance(result.get("items", []), list) else []


async def _collect_ranked_candidates(
    work_item_type: str,
    *,
    hints: list[str],
    area_path: str,
    objective: str,
    context: str,
    dominant_domain: str,
    feature_hint: str,
    curated_examples: list[dict],
    top: int = 5,
) -> list[dict]:
    collected = {}
    query_text = " | ".join(part for part in [objective, context, dominant_domain, feature_hint] if part)
    for hint in _unique_strings(hints):
        for item in await _query_candidates_by_title_hint(work_item_type, title_hint=hint, area_path=area_path, top=max(4, top)):
            key = str(item.get("id", "") or "").strip()
            if not key:
                continue
            current = collected.get(key)
            score = _score_backlog_candidate(
                item,
                query_text=query_text,
                dominant_domain=dominant_domain,
                feature_hint=feature_hint,
                curated_examples=curated_examples,
            )
            candidate = {**item, "_placement_score": score}
            if current is None or float(candidate["_placement_score"]) > float(current.get("_placement_score", 0.0)):
                collected[key] = candidate
    ranked = sorted(collected.values(), key=lambda item: float(item.get("_placement_score", 0.0) or 0.0), reverse=True)
    return ranked[: max(1, top)]


async def _resolve_story_placement(
    *,
    objective: str,
    context: str,
    team_scope: str,
    epic_or_feature: str,
    canonical_area_path: str,
    feature_context: dict,
    design_context: dict,
    curated_context: dict,
) -> dict:
    dominant_domain = str(design_context.get("dominant_domain", "") or "").strip()
    curated_examples = curated_context.get("matches", []) if isinstance(curated_context, dict) else []
    explicit_type = str(feature_context.get("type", "") or "").strip().lower()
    selected_epic = feature_context if explicit_type == "epic" else None
    selected_feature = feature_context if explicit_type == "feature" else None
    if explicit_type == "user story":
        inferred_feature = _parent_anchor_candidate(feature_context, expected_parent_type="Feature", inferred_type="Feature")
        if inferred_feature:
            selected_feature = inferred_feature
            reasoning = ["A story indicada aponta diretamente para uma Feature pai já conhecida."]
        else:
            reasoning = []
    else:
        reasoning = []
    if dominant_domain:
        reasoning.append(f"Domínio dominante inferido por design: {dominant_domain}.")
    if curated_examples:
        top_curated = curated_examples[0]
        if top_curated.get("title"):
            reasoning.append(f"Exemplo curado mais próximo: {top_curated.get('title')}.")

    epic_hints = [
        dominant_domain,
        epic_or_feature if explicit_type == "epic" else "",
        curated_examples[0].get("domain", "") if curated_examples else "",
    ]
    feature_hints = [
        epic_or_feature if explicit_type != "epic" else "",
        curated_examples[0].get("flow", "") if curated_examples else "",
        curated_examples[0].get("journey", "") if curated_examples else "",
        dominant_domain,
    ]

    epic_candidates = []
    if not selected_epic:
        epic_candidates = await _collect_ranked_candidates(
            "Epic",
            hints=epic_hints,
            area_path=canonical_area_path,
            objective=objective,
            context=context,
            dominant_domain=dominant_domain,
            feature_hint=epic_or_feature,
            curated_examples=curated_examples,
            top=4,
        )
        if epic_candidates:
            selected_epic = epic_candidates[0]
            reasoning.append(f"Epic sugerida: {selected_epic.get('title', '')}.")

    if selected_feature and not selected_epic:
        selected_feature = await _enrich_candidate_from_search(selected_feature)
        inferred_epic = _parent_anchor_candidate(selected_feature, expected_parent_type="Epic", inferred_type="Epic")
        if inferred_epic:
            selected_epic = inferred_epic
            reasoning.append(f"Epic inferida a partir da Feature: {selected_epic.get('title', '')}.")

    hierarchy_feature_candidates = []
    if selected_epic and str(selected_epic.get("id", "") or "").strip().isdigit():
        hierarchy_result = await tool_query_hierarchy(
            parent_id=int(selected_epic.get("id")),
            parent_type="Epic",
            child_type="Feature",
            area_path=canonical_area_path,
            title_contains=epic_or_feature or "",
        )
        if isinstance(hierarchy_result, dict):
            hierarchy_feature_candidates = list(hierarchy_result.get("items", []) or [])

    feature_candidates = []
    if selected_feature:
        feature_candidates = [selected_feature]
    elif hierarchy_feature_candidates:
        query_text = " | ".join(part for part in [objective, context, dominant_domain, epic_or_feature] if part)
        ranked = []
        for item in hierarchy_feature_candidates:
            ranked.append(
                {
                    **item,
                    "_placement_score": _score_backlog_candidate(
                        item,
                        query_text=query_text,
                        dominant_domain=dominant_domain,
                        feature_hint=epic_or_feature,
                        curated_examples=curated_examples,
                    ),
                }
            )
        feature_candidates = sorted(ranked, key=lambda item: float(item.get("_placement_score", 0.0) or 0.0), reverse=True)[:4]
        if feature_candidates:
            selected_feature = feature_candidates[0]
            reasoning.append(f"Feature sugerida dentro da Epic: {selected_feature.get('title', '')}.")
    else:
        feature_candidates = await _collect_ranked_candidates(
            "Feature",
            hints=feature_hints,
            area_path=canonical_area_path,
            objective=objective,
            context=context,
            dominant_domain=dominant_domain,
            feature_hint=epic_or_feature,
            curated_examples=curated_examples,
            top=4,
        )
        if feature_candidates:
            selected_feature = feature_candidates[0]
            reasoning.append(f"Feature sugerida por ranking direto: {selected_feature.get('title', '')}.")

    if selected_epic and feature_candidates:
        selected_epic_id = str(selected_epic.get("id", "") or "").strip()
        reranked = []
        for item in feature_candidates:
            candidate = dict(item)
            if selected_epic_id and str(candidate.get("parent_id", "") or "").strip() == selected_epic_id:
                candidate["_placement_score"] = round(float(candidate.get("_placement_score", 0.0) or 0.0) + 0.24, 4)
            reranked.append(candidate)
        feature_candidates = sorted(reranked, key=lambda item: float(item.get("_placement_score", 0.0) or 0.0), reverse=True)[:4]
        if feature_candidates:
            selected_feature = feature_candidates[0]

    if selected_feature and not selected_epic:
        selected_feature = await _enrich_candidate_from_search(selected_feature)
        inferred_epic = _parent_anchor_candidate(selected_feature, expected_parent_type="Epic", inferred_type="Epic")
        if inferred_epic:
            selected_epic = inferred_epic
            reasoning.append(f"Epic inferida a partir do parentesco da Feature: {selected_epic.get('title', '')}.")

    confidence_parts = []
    if selected_epic:
        confidence_parts.append(float(selected_epic.get("_placement_score", 0.55) or 0.55))
    if selected_feature:
        confidence_parts.append(float(selected_feature.get("_placement_score", 0.55) or 0.55))
    placement_confidence = round(min(0.97, sum(confidence_parts) / max(1, len(confidence_parts))), 4) if confidence_parts else 0.0

    if selected_epic and selected_feature:
        reasoning.append("O draft deve encaixar explicitamente na relação Epic > Feature selecionada.")
    elif selected_feature:
        reasoning.append("Existe confiança maior na Feature do que na Epic; usar a Feature como anchor principal.")
    elif selected_epic:
        reasoning.append("Existe confiança suficiente na Epic, mas a Feature ainda não está resolvida.")

    return {
        "confidence": placement_confidence,
        "selected_epic": selected_epic or {},
        "selected_feature": selected_feature or {},
        "candidate_epics": epic_candidates[:3],
        "candidate_features": feature_candidates[:3],
        "reasoning": reasoning[:6],
        "resolved_area_path": str(
            (selected_feature or {}).get("area")
            or (selected_epic or {}).get("area")
            or canonical_area_path
            or team_scope
        ),
    }


async def _fetch_feature_context(epic_or_feature: str = "") -> dict:
    candidate = str(epic_or_feature or "").strip()
    if not candidate:
        return {}
    work_item_id = _extract_workitem_id(candidate)
    if work_item_id:
        try:
            result = await tool_query_workitems(
                f"[System.Id] = {int(work_item_id)}",
                fields=[
                    "System.Id",
                    "System.Title",
                    "System.WorkItemType",
                    "System.AreaPath",
                    "System.State",
                    "System.Description",
                    "Microsoft.VSTS.Common.AcceptanceCriteria",
                    "System.Tags",
                ],
                top=1,
            )
        except Exception as exc:
            logger.warning("[UserStoryLane] fetch feature by id failed: %s", exc)
            result = {}
        items = result.get("items", []) if isinstance(result, dict) else []
        if items:
            item = items[0]
            base = {
                "id": item.get("id"),
                "type": item.get("type", ""),
                "title": item.get("title", ""),
                "area": item.get("area", ""),
                "description": item.get("description", ""),
                "acceptance_criteria": item.get("acceptance_criteria", ""),
                "tags": item.get("tags", ""),
                "url": item.get("url", ""),
            }
            return await _enrich_candidate_from_search(base)
    search = await search_story_devops_index(
        query_text=candidate,
        work_item_types=["Epic", "Feature", "User Story"],
        top=1,
    )
    if not isinstance(search, dict) or not list(search.get("items", []) or []):
        search = await tool_search_workitems(candidate, top=1)
    items = search.get("items", []) if isinstance(search, dict) else []
    if not items:
        return {}
    item = items[0]
    return {
        "id": item.get("id"),
        "type": item.get("type", item.get("tag", "Work Item")),
        "title": item.get("title", ""),
        "area": item.get("area", ""),
        "description": item.get("content", ""),
        "acceptance_criteria": "",
        "tags": "",
        "url": item.get("url", ""),
        "parent_id": item.get("parent_id"),
        "parent_title": item.get("parent_title", ""),
        "parent_type": item.get("parent_type", ""),
        "score": item.get("score", 0.0),
    }


async def _retrieve_feature_sibling_stories(
    *,
    objective: str,
    context: str,
    placement: dict,
    top: int = 4,
) -> dict:
    selected_feature = (placement or {}).get("selected_feature", {}) or {}
    feature_id = str(selected_feature.get("id", "") or "").strip()
    if not feature_id.isdigit():
        return {"items": [], "notes": []}

    try:
        result = await tool_query_hierarchy(
            parent_id=int(feature_id),
            parent_type="Feature",
            child_type="User Story",
            area_path=str((placement or {}).get("resolved_area_path", "") or ""),
            title_contains="",
        )
    except Exception as exc:
        logger.warning("[UserStoryLane] feature sibling retrieval failed: %s", exc)
        return {"items": [], "notes": []}

    items = list(result.get("items", []) or []) if isinstance(result, dict) else []
    if not items:
        return {"items": [], "notes": []}

    query_text = " | ".join(
        part for part in [objective, context, str(selected_feature.get("title", "") or "")] if str(part or "").strip()
    )
    ranked = []
    for item in items:
        score = _score_backlog_candidate(
            item,
            query_text=query_text,
            dominant_domain=_extract_domain_from_title(str(selected_feature.get("title", "") or "")),
            feature_hint=str(selected_feature.get("title", "") or ""),
            curated_examples=[],
        )
        snippet = _clip(
            " ".join(
                part for part in [
                    str(item.get("description", "") or ""),
                    str(item.get("acceptance_criteria", "") or ""),
                ] if str(part or "").strip()
            ),
            420,
        )
        ranked.append(
            {
                "key": f"feature-sibling:{item.get('id', '')}",
                "type": "feature_sibling_story",
                "id": item.get("id"),
                "title": str(item.get("title", "") or ""),
                "snippet": snippet,
                "url": str(item.get("url", "") or ""),
                "score": round(score + 0.12, 4),
                "origin": "devops_feature_hierarchy",
            }
        )
    ranked.sort(key=lambda item: float(item.get("score", 0.0) or 0.0), reverse=True)
    return {
        "items": ranked[: max(1, int(top or 4))],
        "notes": [
            f"Histórias irmãs recuperadas dentro da Feature: {selected_feature.get('title', '')}.",
            "Usar estas histórias como prova de encaixe e para evitar redundância ou colisão de scope.",
        ],
    }


async def _retrieve_curated_workitem_reference_context(
    *,
    objective: str,
    context: str,
    curated_matches: list[dict],
    dominant_domain: str,
    top: int = 4,
) -> dict:
    refs: list[int] = []
    for entry in curated_matches[:4]:
        for ref in list(entry.get("workitem_refs", []) or [])[:6]:
            try:
                numeric = int(ref)
            except Exception:
                continue
            if numeric not in refs:
                refs.append(numeric)
    if not refs:
        return {"items": [], "notes": []}

    where = " OR ".join(f"[System.Id] = {int(ref)}" for ref in refs[:12])
    try:
        result = await tool_query_workitems(
            where,
            fields=[
                "System.Id",
                "System.Title",
                "System.WorkItemType",
                "System.AreaPath",
                "System.State",
                "System.Description",
                "Microsoft.VSTS.Common.AcceptanceCriteria",
                "System.Tags",
            ],
            top=max(4, min(12, len(refs))),
        )
    except Exception as exc:
        logger.warning("[UserStoryLane] curated workitem ref retrieval failed: %s", exc)
        return {"items": [], "notes": []}

    items = list(result.get("items", []) or []) if isinstance(result, dict) else []
    if not items:
        return {"items": [], "notes": []}

    query_text = " | ".join(part for part in [objective, context, dominant_domain] if str(part or "").strip())
    ranked = []
    for item in items:
        score = _score_backlog_candidate(
            item,
            query_text=query_text,
            dominant_domain=dominant_domain,
            feature_hint="",
            curated_examples=curated_matches,
        )
        ranked.append(
            {
                "key": f"curated-ref:{item.get('id', '')}",
                "type": "curated_workitem_ref",
                "id": item.get("id"),
                "title": str(item.get("title", "") or ""),
                "snippet": _clip(
                    " ".join(
                        part for part in [
                            f"Ref citado em user stories curadas.",
                            str(item.get("type", "") or ""),
                            str(item.get("area", "") or ""),
                            str(item.get("description", "") or ""),
                            str(item.get("acceptance_criteria", "") or ""),
                        ]
                        if str(part or "").strip()
                    ),
                    420,
                ),
                "url": str(item.get("url", "") or ""),
                "score": round(score + 0.18, 4),
                "origin": "curated_story_workitem_ref",
            }
        )
    ranked.sort(key=lambda item: float(item.get("score", 0.0) or 0.0), reverse=True)
    return {
        "items": ranked[: max(1, int(top or 4))],
        "notes": [
            "Refs DevOps recuperados a partir da proveniência das melhores user stories do corpus.",
            "Usar estas referências como evidência de encaixe e continuidade do fluxo.",
        ],
    }


async def _retrieve_previous_examples(user_sub: str, team_scope: str, objective: str) -> list[dict]:
    if not user_sub:
        return []
    try:
        rows = await table_query(
            _DRAFTS_TABLE,
            f"PartitionKey eq '{odata_escape(_draft_partition_key(user_sub))}'",
            top=30,
        )
    except Exception as exc:
        logger.warning("[UserStoryLane] previous examples query failed: %s", exc)
        return []

    scored_rows = []
    canonical_team = _canonicalize_area_path(team_scope) if team_scope else ""
    for row in rows:
        outcome = str(row.get("FeedbackOutcome", "") or "").strip().lower()
        status = str(row.get("Status", "") or "").strip().lower()
        if outcome not in ("accepted", "edited") and status not in ("published", "accepted"):
            continue
        team_bonus = 0.15 if canonical_team and str(row.get("CanonicalAreaPath", "") or "") == canonical_team else 0.0
        base_score = _overlap_score(
            objective,
            " ".join(
                str(row.get(key, "") or "")
                for key in ("Objective", "Title", "BusinessGoal", "TeamScope")
            ),
        )
        scored_rows.append((base_score + team_bonus, row))

    scored_rows.sort(key=lambda item: item[0], reverse=True)
    examples = []
    for score, row in scored_rows[:2]:
        draft_blob_ref = str(row.get("DraftBlobRef", "") or "")
        if not draft_blob_ref:
            continue
        try:
            container, blob_name = draft_blob_ref.split("/", 1)
        except ValueError:
            continue
        payload = await blob_download_json(container, blob_name)
        if not isinstance(payload, dict):
            continue
        draft = await _load_learning_variant(row, payload)
        if not draft:
            continue
        examples.append(
            {
                "title": draft.get("title", row.get("Title", "")),
                "business_goal": draft.get("business_goal", row.get("BusinessGoal", "")),
                "acceptance_criteria": _coerce_list(draft.get("acceptance_criteria", []), max_items=4),
                "score": round(score, 4),
            }
        )
    return examples


async def _retrieve_promoted_curated_examples(
    *,
    objective: str,
    context: str,
    team_scope: str,
    epic_or_feature: str,
    dominant_design_domain: str,
    top: int,
) -> dict:
    query_text = " | ".join(
        part for part in [objective, context, team_scope, epic_or_feature, dominant_design_domain] if str(part or "").strip()
    ).strip()
    if not query_text:
        return {"matches": [], "promoted_count": 0}
    search_result = await search_story_examples_index(
        query_text=query_text,
        dominant_domain=dominant_design_domain,
        top=max(1, int(top or STORY_CONTEXT_CURATED_TOP)),
    )
    search_matches = list(search_result.get("matches", []) or [])
    if search_matches:
        return {
            "matches": search_matches,
            "promoted_count": int(search_result.get("promoted_count", 0) or 0),
            "source": str(search_result.get("source", "") or "azure_ai_search_story_examples"),
        }
    try:
        rows = await table_query(_CURATED_TABLE, "PartitionKey eq 'global'", top=80)
    except Exception as exc:
        logger.warning("[UserStoryLane] promoted curated query failed: %s", exc)
        return {"matches": [], "promoted_count": 0}

    dominant_domain = _normalize_story_text(dominant_design_domain).lower()
    scored: list[tuple[float, dict]] = []
    active_rows = [row for row in rows if str(row.get("Status", "") or "").strip().lower() == _CURATED_STATUS_ACTIVE]
    for row in active_rows:
        if str(row.get("Status", "") or "").strip().lower() != _CURATED_STATUS_ACTIVE:
            continue
        search_text = " ".join(
            part for part in [
                row.get("Title", ""),
                row.get("SearchText", ""),
                row.get("Domain", ""),
                row.get("Journey", ""),
                row.get("Flow", ""),
            ]
            if str(part or "").strip()
        )
        score = _overlap_score(query_text, search_text)
        row_domain = _normalize_story_text(row.get("Domain", "")).lower()
        if dominant_domain and row_domain and row_domain == dominant_domain:
            score += 0.45
        if score < 0.12:
            continue
        scored.append((score, row))

    scored.sort(key=lambda item: (float(item[0]), float(item[1].get("QualityScore", 0.0) or 0.0)), reverse=True)
    matches = []
    for score, row in scored[: max(1, int(top or STORY_CONTEXT_CURATED_TOP))]:
        blob_ref = str(row.get("EntryBlobRef", "") or "").strip()
        entry = {}
        if blob_ref and "/" in blob_ref:
            try:
                container, blob_name = blob_ref.split("/", 1)
                payload = await blob_download_json(container, blob_name)
            except Exception as exc:
                logger.warning("[UserStoryLane] promoted curated blob load failed: %s", exc)
                payload = {}
            if isinstance(payload, dict):
                entry = payload.get("entry", {}) if isinstance(payload.get("entry"), dict) else {}
        if not entry:
            continue
        entry["score"] = round(score, 4)
        entry["origin"] = "promoted_curated_story"
        entry["url"] = str(entry.get("url", "") or row.get("PublishedWorkItemUrl", "") or "")
        matches.append(entry)
    return {"matches": matches, "promoted_count": len(active_rows), "source": "table_fallback"}


async def _build_context_pack(
    payload: dict,
    *,
    user_sub: str,
    conversation_id: str,
) -> dict:
    objective = str(payload.get("objective", "") or "").strip()
    context = str(payload.get("context", "") or "").strip()
    team_scope = str(payload.get("team_scope", "") or "").strip()
    epic_or_feature = str(payload.get("epic_or_feature", "") or "").strip()
    reference_author = str(payload.get("reference_author", "") or "").strip()

    canonical_area_path = _canonicalize_area_path(team_scope) if team_scope else ""
    retrieval_query = " | ".join(part for part in [objective, team_scope, epic_or_feature] if part).strip() or objective

    design_context = search_story_design_map(
        objective=objective,
        context=context,
        team_scope=team_scope,
        epic_or_feature=epic_or_feature,
        top=4,
    )
    design_sources = [serialize_design_match(entry) for entry in design_context.get("matches", [])]
    curated_context = search_curated_story_examples(
        objective=objective,
        context=context,
        team_scope=team_scope,
        epic_or_feature=epic_or_feature,
        dominant_design_domain=str(design_context.get("dominant_domain", "") or ""),
        top=max(1, STORY_CONTEXT_CURATED_TOP),
    )
    promoted_curated_context = await _retrieve_promoted_curated_examples(
        objective=objective,
        context=context,
        team_scope=team_scope,
        epic_or_feature=epic_or_feature,
        dominant_design_domain=str(design_context.get("dominant_domain", "") or ""),
        top=max(1, STORY_CONTEXT_CURATED_TOP),
    )
    combined_curated_matches = list(promoted_curated_context.get("matches", []) or []) + list(curated_context.get("matches", []) or [])
    curated_sources = [serialize_curated_story_match(entry) for entry in combined_curated_matches]

    similar_items = []
    workitems_result = await search_story_devops_index(
        query_text=retrieval_query,
        team_scope=canonical_area_path or team_scope,
        dominant_domain=str(design_context.get("dominant_domain", "") or ""),
        work_item_types=["Epic", "Feature", "User Story"],
        top=max(1, STORY_CONTEXT_SIMILAR_TOP),
    )
    if not isinstance(workitems_result, dict) or not list(workitems_result.get("items", []) or []):
        workitems_result = await tool_search_workitems(retrieval_query, top=max(1, STORY_CONTEXT_SIMILAR_TOP))
    for item in workitems_result.get("items", [])[: max(1, STORY_CONTEXT_SIMILAR_TOP)]:
        similar_items.append(
            {
                "key": f"devops:{item.get('id', '')}",
                "type": "devops_workitem",
                "title": item.get("title", ""),
                "snippet": item.get("content", ""),
                "url": item.get("url", ""),
                "score": item.get("score", 0.0),
                "origin": item.get("origin", "azure_search_devops"),
            }
        )

    website_sources = []
    website_result = await search_story_knowledge_index(
        query_text=retrieval_query,
        dominant_domain=str(design_context.get("dominant_domain", "") or ""),
        team_scope=canonical_area_path or team_scope,
        top=3,
    )
    if not isinstance(website_result, dict) or not list(website_result.get("items", []) or []):
        website_result = await tool_search_website(retrieval_query, top=3)
    for item in website_result.get("items", [])[:3]:
        website_sources.append(
            {
                "key": f"knowledge:{item.get('id', '')}",
                "type": "knowledge_doc",
                "title": item.get("tag", "") or item.get("title", "") or "Documento",
                "snippet": item.get("content", ""),
                "url": item.get("url", ""),
                "score": item.get("score", 0.0),
                "origin": item.get("origin", "azure_search_knowledge"),
            }
        )

    uploaded_sources = []
    if conversation_id:
        uploaded_result = await tool_search_uploaded_document(
            query=retrieval_query,
            conv_id=conversation_id,
            user_sub=user_sub,
        )
        for item in uploaded_result.get("items", [])[: max(1, STORY_CONTEXT_DOC_TOP)]:
            uploaded_sources.append(
                {
                    "key": f"upload:{item.get('filename', '')}:{item.get('chunk_index', '')}",
                    "type": "uploaded_document",
                    "title": item.get("filename", "Documento carregado"),
                    "snippet": item.get("text", ""),
                    "url": "",
                    "score": item.get("score", 0.0),
                    "origin": "uploaded_chunks",
                }
            )

    feature_context = await _fetch_feature_context(epic_or_feature)
    feature_sources = []
    if feature_context:
        feature_sources.append(
            {
                "key": f"feature:{feature_context.get('id', '') or safe_blob_component(epic_or_feature, 'feature')}",
                "type": "feature_context",
                "title": feature_context.get("title", epic_or_feature),
                "snippet": feature_context.get("description", "") or feature_context.get("acceptance_criteria", ""),
                "url": feature_context.get("url", ""),
                "score": 1.0,
                "origin": "devops_feature_context",
            }
        )

    writer_profile = None
    if reference_author:
        writer_profile = await _load_writer_profile(reference_author, owner_sub=user_sub)

    previous_examples = await _retrieve_previous_examples(user_sub, team_scope, objective)
    placement = await _resolve_story_placement(
        objective=objective,
        context=context,
        team_scope=team_scope,
        epic_or_feature=epic_or_feature,
        canonical_area_path=canonical_area_path,
        feature_context=feature_context,
        design_context=design_context,
        curated_context=curated_context,
    )
    domain_profile = select_story_domain_profile(
        objective=objective,
        context=context,
        team_scope=team_scope,
        epic_or_feature=epic_or_feature,
        dominant_domain=str(design_context.get("dominant_domain", "") or ""),
    )
    story_policy_pack = select_story_policy_pack(
        objective=objective,
        context=context,
        team_scope=team_scope,
        epic_or_feature=epic_or_feature,
        dominant_domain=str(domain_profile.get("domain", "") or design_context.get("dominant_domain", "") or ""),
    )
    design_flow_context = await _retrieve_design_flow_context(
        objective=objective,
        context=context,
        epic_or_feature=epic_or_feature,
        placement=placement,
        design_context=design_context,
        curated_context=curated_context,
    )
    feature_sibling_context = await _retrieve_feature_sibling_stories(
        objective=objective,
        context=context,
        placement=placement,
        top=4,
    )
    feature_pack = select_story_feature_pack(
        objective=objective,
        context=context,
        team_scope=team_scope,
        epic_or_feature=epic_or_feature,
        placement=placement,
    )
    curated_workitem_ref_context = await _retrieve_curated_workitem_reference_context(
        objective=objective,
        context=context,
        curated_matches=combined_curated_matches,
        dominant_domain=str(design_context.get("dominant_domain", "") or ""),
        top=4,
    )
    design_flow_sources = list(design_flow_context.get("matches", []) or [])
    feature_sibling_sources = list(feature_sibling_context.get("items", []) or [])
    feature_pack_sources = [
        {
            "key": f"feature-pack:{feature_pack.get('feature_id', '')}:{item.get('id', '')}",
            "type": "feature_pack_story",
            "title": item.get("title", ""),
            "snippet": item.get("snippet", ""),
            "url": item.get("url", ""),
            "score": max(0.72, float(feature_pack.get("score", 0.0) or 0.0)),
            "origin": "devops_feature_pack",
        }
        for item in list(feature_pack.get("stories", []) or [])[:4]
    ]
    curated_ref_sources = list(curated_workitem_ref_context.get("items", []) or [])
    missing_fields = _detect_missing_fields(objective, context, epic_or_feature)
    clarification_questions = _build_clarification_questions(missing_fields)

    policy_pack = {
        "template_version": US_TEMPLATE_VERSION,
        "language": "pt-PT",
        "team_scope": team_scope,
        "canonical_area_path": canonical_area_path,
        "preferred_lexicon": list(
            dict.fromkeys(
                _LEXICON_HINTS
                + list(story_policy_pack.get("preferred_lexicon", []))
                + list(domain_profile.get("preferred_lexicon", []))
                + list(curated_context.get("preferred_lexicon", []))
                + list(design_context.get("ux_terms", []))
                + list(feature_pack.get("top_ux_terms", []))
            )
        ),
        "reference_author": reference_author,
        "writer_profile_summary": _clip((writer_profile or {}).get("style_analysis", ""), 900) if writer_profile else "",
        "design_domains": [str(item.get("domain", "") or "") for item in design_context.get("matches", []) if item.get("domain")],
        "design_ux_terms": list(design_context.get("ux_terms", [])),
        "design_notes": list(design_context.get("notes", [])),
        "curated_title_patterns": list(curated_context.get("title_patterns", [])),
        "curated_notes": list(curated_context.get("notes", [])),
        "promoted_curated_count": int(promoted_curated_context.get("promoted_count", 0) or 0),
        "domain_top_journeys": list(domain_profile.get("top_journeys", [])),
        "domain_top_flows": list(domain_profile.get("top_flows", [])),
        "domain_routing_notes": list(domain_profile.get("routing_notes", [])),
        "story_policy_pack": story_policy_pack,
        "policy_notes": list(story_policy_pack.get("notes", [])),
        "policy_mandatory_sections": list(story_policy_pack.get("mandatory_sections", [])),
        "policy_terminology_overrides": list(story_policy_pack.get("terminology_overrides", [])),
        "resolved_epic": str((placement.get("selected_epic", {}) or {}).get("title", "") or ""),
        "resolved_feature": str((placement.get("selected_feature", {}) or {}).get("title", "") or ""),
        "design_flow_hints": list(design_flow_context.get("hints", [])),
        "design_flow_notes": list(design_flow_context.get("notes", [])),
        "feature_pack_notes": list(feature_pack.get("notes", [])),
        "feature_pack_flows": list(feature_pack.get("top_flows", [])),
        "feature_sibling_notes": list(feature_sibling_context.get("notes", [])),
        "curated_workitem_ref_notes": list(curated_workitem_ref_context.get("notes", [])),
        "domain_profile": domain_profile,
    }

    sources = (
        feature_sources
        + feature_pack_sources
        + curated_ref_sources
        + feature_sibling_sources
        + design_sources
        + design_flow_sources
        + curated_sources
        + similar_items
        + website_sources
        + uploaded_sources
    )
    return {
        "conversation_id": conversation_id,
        "intake": {
            "objective": objective,
            "team_scope": team_scope,
            "canonical_area_path": canonical_area_path,
            "epic_or_feature": epic_or_feature,
            "context": context,
            "reference_author": reference_author,
            "reference_topic": str(payload.get("reference_topic", "") or "").strip(),
        },
        "policy_pack": policy_pack,
        "feature_context": feature_context,
        "placement": {
            "confidence": round(float(placement.get("confidence", 0.0) or 0.0), 4),
            "selected_epic": {
                "id": (placement.get("selected_epic", {}) or {}).get("id"),
                "title": str((placement.get("selected_epic", {}) or {}).get("title", "") or ""),
                "area": str((placement.get("selected_epic", {}) or {}).get("area", "") or ""),
                "url": str((placement.get("selected_epic", {}) or {}).get("url", "") or ""),
                "score": round(float((placement.get("selected_epic", {}) or {}).get("_placement_score", 0.0) or 0.0), 4),
            },
            "selected_feature": {
                "id": (placement.get("selected_feature", {}) or {}).get("id"),
                "title": str((placement.get("selected_feature", {}) or {}).get("title", "") or ""),
                "area": str((placement.get("selected_feature", {}) or {}).get("area", "") or ""),
                "url": str((placement.get("selected_feature", {}) or {}).get("url", "") or ""),
                "score": round(float((placement.get("selected_feature", {}) or {}).get("_placement_score", 0.0) or 0.0), 4),
            },
            "candidate_epics": [
                {
                    "id": item.get("id"),
                    "title": str(item.get("title", "") or ""),
                    "area": str(item.get("area", "") or ""),
                    "url": str(item.get("url", "") or ""),
                    "score": round(float(item.get("_placement_score", 0.0) or 0.0), 4),
                }
                for item in placement.get("candidate_epics", [])[:3]
            ],
            "candidate_features": [
                {
                    "id": item.get("id"),
                    "title": str(item.get("title", "") or ""),
                    "area": str(item.get("area", "") or ""),
                    "url": str(item.get("url", "") or ""),
                    "score": round(float(item.get("_placement_score", 0.0) or 0.0), 4),
                }
                for item in placement.get("candidate_features", [])[:3]
            ],
            "reasoning": list(placement.get("reasoning", [])),
            "resolved_area_path": str(placement.get("resolved_area_path", "") or canonical_area_path),
        },
        "sources": [_serialize_source(source) for source in sources[:10]],
        "design_sources": [_serialize_source(source) for source in design_sources[:4]],
        "design_flow": {
            "notes": list(design_flow_context.get("notes", [])),
            "hints": list(design_flow_context.get("hints", [])),
            "matches": [
                {
                    "key": str(item.get("key", "") or ""),
                    "title": str(item.get("title", "") or ""),
                    "snippet": str(item.get("snippet", "") or ""),
                    "url": str(item.get("url", "") or ""),
                    "score": round(float(item.get("score", 0.0) or 0.0), 4),
                    "domain": str(item.get("domain", "") or ""),
                    "file_title": str(item.get("file_title", "") or ""),
                    "page_name": str(item.get("page_name", "") or ""),
                    "frame_name": str(item.get("frame_name", "") or ""),
                    "node_id": str(item.get("node_id", "") or ""),
                    "ui_components": list(item.get("ui_components", []) or [])[:6],
                }
                for item in design_flow_sources[: max(1, STORY_CONTEXT_FIGMA_FLOW_TOP)]
            ],
        },
        "feature_siblings": [
            {
                "key": str(item.get("key", "") or ""),
                "id": item.get("id"),
                "title": str(item.get("title", "") or ""),
                "snippet": str(item.get("snippet", "") or ""),
                "url": str(item.get("url", "") or ""),
                "score": round(float(item.get("score", 0.0) or 0.0), 4),
                "origin": str(item.get("origin", "") or "devops_feature_hierarchy"),
            }
            for item in feature_sibling_sources[:4]
        ],
        "feature_pack": serialize_feature_pack(feature_pack),
        "curated_workitem_refs": [
            {
                "key": str(item.get("key", "") or ""),
                "id": item.get("id"),
                "title": str(item.get("title", "") or ""),
                "snippet": str(item.get("snippet", "") or ""),
                "url": str(item.get("url", "") or ""),
                "score": round(float(item.get("score", 0.0) or 0.0), 4),
                "origin": str(item.get("origin", "") or "curated_story_workitem_ref"),
            }
            for item in curated_ref_sources[:4]
        ],
        "curated_examples": [
            {
                "title": str(item.get("title", "") or ""),
                "author": str(item.get("created_by", "") or ""),
                "domain": str(item.get("domain", "") or ""),
                "title_pattern": str(item.get("title_pattern", "") or ""),
                "ux_terms": list(item.get("ux_terms", []) or [])[:8],
                "provenance_excerpt": _clip((item.get("sections", {}) or {}).get("proveniência", ""), 280),
                "behavior_excerpt": _clip((item.get("sections", {}) or {}).get("comportamento", ""), 280),
                "score": round(float(item.get("score", 0.0) or 0.0), 4),
                "url": str(item.get("url", "") or ""),
                "origin": str(item.get("origin", "") or "curated_story_csv"),
            }
            for item in combined_curated_matches[: max(1, STORY_CONTEXT_CURATED_TOP)]
        ],
        "design_map": {
            "dominant_domain": design_context.get("dominant_domain", ""),
            "notes": list(design_context.get("notes", [])),
            "matches": [
                {
                    "domain": str(item.get("domain", "") or ""),
                    "title": str(item.get("title", "") or ""),
                    "status": str(item.get("status", "") or ""),
                    "score": round(float(item.get("score", 0.0) or 0.0), 4),
                    "url": str(item.get("url", "") or ""),
                    "site_placement": str(item.get("site_placement", "") or ""),
                }
                for item in design_context.get("matches", [])[:4]
            ],
        },
        "domain_profile": {
            "domain": str(domain_profile.get("domain", "") or ""),
            "design_file_title": str(domain_profile.get("design_file_title", "") or ""),
            "top_title_patterns": list(domain_profile.get("top_title_patterns", []))[:3],
            "top_journeys": list(domain_profile.get("top_journeys", []))[:5],
            "top_flows": list(domain_profile.get("top_flows", []))[:6],
            "preferred_lexicon": list(domain_profile.get("preferred_lexicon", []))[:10],
            "section_emphasis": list(domain_profile.get("section_emphasis", []))[:6],
            "routing_notes": list(domain_profile.get("routing_notes", []))[:4],
            "curated_example_count": int(domain_profile.get("curated_example_count", 0) or 0),
            "coverage_score": round(float(domain_profile.get("coverage_score", 0.0) or 0.0), 4),
        },
        "story_policy_pack": {
            "id": str(story_policy_pack.get("id", "") or ""),
            "domain": str(story_policy_pack.get("domain", "") or ""),
            "detail_level": str(story_policy_pack.get("detail_level", "") or ""),
            "template_version": str(story_policy_pack.get("template_version", "") or ""),
            "canonical_title_pattern": str(story_policy_pack.get("canonical_title_pattern", "") or ""),
            "mandatory_sections": list(story_policy_pack.get("mandatory_sections", []))[:8],
            "preferred_lexicon": list(story_policy_pack.get("preferred_lexicon", []))[:10],
            "terminology_overrides": list(story_policy_pack.get("terminology_overrides", []))[:6],
            "top_journeys": list(story_policy_pack.get("top_journeys", []))[:5],
            "top_flows": list(story_policy_pack.get("top_flows", []))[:6],
            "notes": list(story_policy_pack.get("notes", []))[:4],
            "coverage_score": round(float(story_policy_pack.get("coverage_score", 0.0) or 0.0), 4),
        },
        "curated_corpus": {
            "notes": list(curated_context.get("notes", [])),
            "title_patterns": list(curated_context.get("title_patterns", [])),
            "preferred_lexicon": list(curated_context.get("preferred_lexicon", [])),
            "stats": curated_context.get("corpus_stats", {}),
            "promoted_count": int(promoted_curated_context.get("promoted_count", 0) or 0),
        },
        "similar_items": [_serialize_source(source) for source in similar_items[: max(1, STORY_CONTEXT_SIMILAR_TOP)]],
        "knowledge_sources": [_serialize_source(source) for source in website_sources[:3]],
        "uploaded_sources": [_serialize_source(source) for source in uploaded_sources[: max(1, STORY_CONTEXT_DOC_TOP)]],
        "previous_examples": previous_examples,
        "missing_fields": missing_fields,
        "clarification_questions": clarification_questions,
    }


def _fallback_title(team_scope: str, epic_or_feature: str, objective: str) -> str:
    area_segment = safe_blob_component(team_scope.split("\\")[-1] if "\\" in team_scope else team_scope, "Transversal", 32).replace("_", " ")
    flow_segment = safe_blob_component(epic_or_feature or "Fluxo principal", "Fluxo", 36).replace("_", " ")
    detail_segment = safe_blob_component(objective, "Detalhe", 46).replace("_", " ")
    return f"ACME | {area_segment or 'Transversal'} | {flow_segment or 'Fluxo principal'} | Jornada | {detail_segment or 'Alteração'}"


def _render_story_html(draft: dict, context_pack: dict) -> tuple[str, str, str]:
    intake = context_pack.get("intake", {}) if isinstance(context_pack, dict) else {}
    title = _normalize_story_text(draft.get("title", "")) or _fallback_title(
        intake.get("team_scope", ""),
        intake.get("epic_or_feature", ""),
        intake.get("objective", ""),
    )
    if not title.startswith("ACME |"):
        title = _fallback_title(
            intake.get("team_scope", ""),
            intake.get("epic_or_feature", ""),
            title or intake.get("objective", ""),
        )

    narrative = draft.get("narrative", {}) if isinstance(draft.get("narrative"), dict) else {}
    as_a = _clip(narrative.get("as_a", "") or "cliente do banco", 180)
    i_want = _clip(narrative.get("i_want", "") or intake.get("objective", ""), 240)
    so_that = _clip(narrative.get("so_that", "") or draft.get("business_goal", "") or "atingir o resultado esperado", 240)
    business_goal = _clip(draft.get("business_goal", ""), 400)

    description_html = (
        f"<div>Eu como <b>{as_a}</b>, quero <b>{i_want}</b>, para que <b>{so_that}</b>.</div>"
        f"<div><b>Objetivo de Negócio</b>: {business_goal or 'A confirmar com Product Owner.'}</div>"
    )

    provenance_items = _coerce_list(draft.get("provenance", []), max_items=5)
    if not provenance_items:
        provenance_items = [
            f"Fluxo enquadrado em {intake.get('epic_or_feature') or intake.get('team_scope') or 'trajeto principal do site/app'}."
        ]

    condition_items = _coerce_list(draft.get("conditions", []), max_items=5) or ["NA"]
    rules_items = _coerce_list(draft.get("rules_constraints", []), max_items=8)
    dependencies = _coerce_list(draft.get("dependencies", []), max_items=5)
    observations = _coerce_list(draft.get("observations", []), max_items=5)

    ac_lines = []
    for item in draft.get("acceptance_criteria", []) if isinstance(draft.get("acceptance_criteria"), list) else []:
        if not isinstance(item, dict):
            continue
        item_id = _clip(item.get("id", ""), 20)
        text = _clip(item.get("text", ""), 600)
        if item_id or text:
            ac_lines.append(f"<li><b>{item_id or 'CA'}</b> — {text}</li>")
    if not ac_lines:
        ac_lines.append("<li><b>CA-01</b> — Critérios de aceitação a confirmar.</li>")

    test_lines = []
    for item in draft.get("test_scenarios", []) if isinstance(draft.get("test_scenarios"), list) else []:
        if not isinstance(item, dict):
            continue
        scenario_id = _clip(item.get("id", ""), 20) or "CT"
        title_text = _clip(item.get("title", ""), 140)
        covers = ", ".join(_coerce_list(item.get("covers", []), max_items=4))
        given = _clip(item.get("given", ""), 240)
        when = _clip(item.get("when", ""), 240)
        then = _clip(item.get("then", ""), 240)
        test_lines.append(
            "<li>"
            f"<b>{scenario_id}</b> — {title_text}<br>"
            f"Dado {given}<br>Quando {when}<br>Então {then}"
            f"{'<br>Cobre: ' + covers if covers else ''}"
            "</li>"
        )
    if not test_lines:
        test_lines.append("<li><b>CT-01</b> — Cenário principal a detalhar.</li>")

    source_items = []
    for source in context_pack.get("sources", [])[:3]:
        if not isinstance(source, dict):
            continue
        label = _clip(source.get("title", ""), 120) or source.get("key", "")
        source_items.append(label)

    ac_html = (
        f"<div><b>Objetivo</b></div><ul><li>{business_goal or 'A confirmar com Product Owner.'}</li></ul>"
        f"<div><b>Proveniência</b></div><ul>{''.join(f'<li>{item}</li>' for item in provenance_items)}</ul>"
        f"<div><b>Condições</b></div><ul>{''.join(f'<li>{item}</li>' for item in condition_items)}</ul>"
        f"<div><b>Composição</b></div><ul>{''.join(f'<li>{item}</li>' for item in (rules_items or ['Elementos de UI e conteúdo textual a detalhar segundo os sources.']))}</ul>"
        f"<div><b>Comportamento</b></div><ul>{''.join(ac_lines)}</ul>"
        f"<div><b>Cenários de Teste</b></div><ul>{''.join(test_lines)}</ul>"
        f"<div><b>Dependências</b></div><ul>{''.join(f'<li>{item}</li>' for item in (dependencies or ['Sem dependências explícitas além das evidências recolhidas.']))}</ul>"
        f"<div><b>Observações</b></div><ul>{''.join(f'<li>{item}</li>' for item in (observations or ['Sem observações adicionais.']))}</ul>"
        f"<div><b>Mockup</b></div><ul><li>{'; '.join(source_items) if source_items else 'Mockup a confirmar com UX.'}</li></ul>"
    )
    return title, description_html, ac_html


def validate_user_story_draft(draft: dict, context_pack: Optional[dict] = None) -> dict:
    context = context_pack if isinstance(context_pack, dict) else {"intake": {}, "sources": []}
    title, description_html, acceptance_html = _render_story_html(draft, context)
    combined = (
        f"Título: {title}\n\n"
        f"Descrição:\n{description_html}\n\n"
        f"Critérios de Aceitação:\n{acceptance_html}"
    )
    quality = _validate_us_output(combined)
    draft_confidence = 0.0
    try:
        draft_confidence = float(draft.get("confidence", 0.0) or 0.0)
    except Exception:
        draft_confidence = 0.0
    publish_ready = bool(quality.get("score", 0.0) >= 0.72 and draft_confidence >= 0.45)
    return {
        "title": title,
        "description_html": description_html,
        "acceptance_criteria_html": acceptance_html,
        "quality_score": round(float(quality.get("score", 0.0) or 0.0), 4),
        "quality_issues": list(quality.get("issues", [])),
        "publish_ready": publish_ready,
        "template_version": US_TEMPLATE_VERSION,
    }


def _build_generation_prompt(context_pack: dict) -> list[dict]:
    available_source_keys = [
        _clip(str(source.get("key", "") or ""), 80)
        for source in list(context_pack.get("sources", []) or [])
        if str(source.get("key", "") or "").strip()
    ]
    generation_brief = _build_generation_brief(context_pack, lean=False)
    system = (
        "És um Product Owner sénior do Acme. "
        "Gera exatamente uma user story pronta para Azure DevOps com grounding forte nas evidências. "
        "Usa PT-PT, linguagem observável e o estilo das user stories atuais do ACME: proveniência clara, condições reais, "
        "composição do ecrã/fluxo, comportamento verificável e cenários de teste úteis. "
        "Não inventes APIs, integrações, dados de backend ou regras de negócio sem base nas fontes. "
        "Prefere jornadas simples, com step/estado explícito quando a evidência indicar Step 1/2/3, OK/NOK/Pendente, resumo, autorização ou conclusão. "
        "No schema, usa 'rules_constraints' para descrever composição visível, labels, CTAs, blocos, validações e regras observáveis do fluxo. "
        "Usa 'acceptance_criteria' para comportamento verificável e resultado esperado. "
        "Nunca exponhas policy packs, scores, corpus interno, reasoning técnico nem learning internals no texto final. "
        "O título deve começar por 'ACME |' e soar a história humana escrita por um PO, não a dump técnico."
    )
    user = (
        "Contexto resumido para gerar a user story:\n"
        f"{generation_brief}\n\n"
        "Regras obrigatórias:\n"
        "- Produzir um draft único, específico e publicável.\n"
        "- Aproxima a estrutura às stories existentes: Proveniência -> Condições -> Composição/Regras -> Comportamento -> Testes.\n"
        "- Quando faltarem dados, não bloquear; regista no máximo 2 clarification_questions e baixa confidence.\n"
        "- Provenance deve explicar a entrada na jornada, o step anterior ou o ponto do fluxo onde esta story encaixa.\n"
        "- Conditions deve listar apenas pré-condições reais, gating rules ou estados necessários para entrar neste passo.\n"
        "- Rules_constraints deve listar composição visível, conteúdos, CTAs, labels, estados, stepper, blocos e regras observáveis do fluxo.\n"
        "- Acceptance criteria devem ser mensuráveis, testáveis e refletir o comportamento esperado da interface/fluxo.\n"
        "- Test scenarios devem usar Dado/Quando/Então coerentes com os critérios e com o estado da jornada.\n"
        f"- source_keys devem apontar apenas para keys existentes nas fontes fornecidas: {available_source_keys[:10] if available_source_keys else ['n/a']}.\n"
        "- Reutiliza o léxico e o estilo dos melhores exemplos quando estiverem suportados pela evidência.\n"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


async def _persist_draft(
    *,
    user_sub: str,
    conversation_id: str,
    request_payload: dict,
    context_pack: dict,
    draft: dict,
    validation: dict,
    stages: list[dict],
) -> str:
    draft_id = uuid.uuid4().hex
    owner_blob = safe_blob_component(user_sub or "anon", "anon")
    payload_blob = await blob_upload_json(
        CHAT_TOOLRESULT_BLOB_CONTAINER,
        f"user-stories/{owner_blob}/{draft_id}/draft.json",
        {
            "draft": draft,
            "context_pack": context_pack,
            "validation": validation,
            "stages": stages,
            "saved_at": _utc_now_iso(),
        },
    )
    entity = {
        "PartitionKey": _draft_partition_key(user_sub),
        "RowKey": draft_id,
        "ConversationId": conversation_id,
        "Objective": _clip(request_payload.get("objective", ""), 500),
        "TeamScope": _clip(request_payload.get("team_scope", ""), 200),
        "CanonicalAreaPath": _clip(context_pack.get("intake", {}).get("canonical_area_path", ""), 250),
        "EpicOrFeature": _clip(request_payload.get("epic_or_feature", ""), 250),
        "ReferenceAuthor": _clip(request_payload.get("reference_author", ""), 120),
        "ResolvedDomain": _clip(
            str(
                (context_pack.get("domain_profile", {}) or {}).get("domain")
                or (context_pack.get("story_policy_pack", {}) or {}).get("domain")
                or ""
            ),
            120,
        ),
        "PolicyDomain": _clip(str((context_pack.get("story_policy_pack", {}) or {}).get("domain", "") or ""), 120),
        "ResolvedEpic": _clip(str((context_pack.get("placement", {}).get("selected_epic", {}) or {}).get("title", "") or ""), 250),
        "ResolvedFeature": _clip(str((context_pack.get("placement", {}).get("selected_feature", {}) or {}).get("title", "") or ""), 250),
        "PlacementConfidence": float((context_pack.get("placement", {}) or {}).get("confidence", 0.0) or 0.0),
        "PrimaryFlowTitle": _clip(
            str((((context_pack.get("design_flow", {}) or {}).get("matches", []) or [{}])[0] or {}).get("title", "") or ""),
            250,
        ),
        "Title": _clip(validation.get("title", ""), 250),
        "BusinessGoal": _clip(draft.get("business_goal", ""), 600),
        "DraftBlobRef": payload_blob.get("blob_ref", ""),
        "TemplateVersion": US_TEMPLATE_VERSION,
        "QualityScore": float(validation.get("quality_score", 0.0) or 0.0),
        "PublishReady": bool(validation.get("publish_ready", False)),
        "Status": "draft",
        "FeedbackOutcome": "",
        "CreatedAt": _utc_now_iso(),
        "UpdatedAt": _utc_now_iso(),
    }
    inserted = await table_insert(_DRAFTS_TABLE, entity)
    if not inserted:
        await table_merge(_DRAFTS_TABLE, entity)
    return draft_id


async def _load_draft(user_sub: str, draft_id: str) -> Tuple[dict, dict]:
    rows = await table_query(
        _DRAFTS_TABLE,
        f"PartitionKey eq '{odata_escape(_draft_partition_key(user_sub))}' and RowKey eq '{odata_escape(draft_id)}'",
        top=1,
    )
    if not rows:
        return {}, {}
    row = rows[0]
    blob_ref = str(row.get("DraftBlobRef", "") or "")
    if not blob_ref or "/" not in blob_ref:
        return row, {}
    container, blob_name = blob_ref.split("/", 1)
    payload = await blob_download_json(container, blob_name) or {}
    return row, payload if isinstance(payload, dict) else {}


def _build_stage(name: str, status: str, duration_ms: int) -> dict:
    return {"name": name, "status": status, "duration_ms": max(0, int(duration_ms))}


async def build_context_preview(request_payload: dict, *, user_sub: str) -> dict:
    _ensure_lane_enabled()
    conversation_id = str(request_payload.get("conversation_id", "") or "").strip() or uuid.uuid4().hex
    t0 = datetime.now(timezone.utc)
    context_pack = await _build_context_pack(request_payload, user_sub=user_sub, conversation_id=conversation_id)
    duration_ms = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
    return {
        "conversation_id": conversation_id,
        "stages": [
            _build_stage("intake", "completed", 0),
            _build_stage("retrieval", "completed", duration_ms),
            _build_stage("context_pack", "completed", 0),
        ],
        "context_pack": context_pack,
        "sources": context_pack.get("sources", []),
        "design_sources": context_pack.get("design_sources", []),
        "domain_profile": context_pack.get("domain_profile", {}),
        "story_policy_pack": context_pack.get("story_policy_pack", {}),
        "design_flow": (context_pack.get("design_flow", {}) or {}).get("matches", []),
        "feature_siblings": context_pack.get("feature_siblings", []),
        "feature_pack": context_pack.get("feature_pack", {}),
        "curated_workitem_refs": context_pack.get("curated_workitem_refs", []),
        "curated_examples": context_pack.get("curated_examples", []),
        "placement": context_pack.get("placement", {}),
        "similar_items": context_pack.get("similar_items", []),
        "missing_fields": context_pack.get("missing_fields", []),
        "clarification_questions": context_pack.get("clarification_questions", []),
    }


async def generate_user_story(request_payload: dict, *, user_sub: str) -> dict:
    _ensure_lane_enabled()
    preview = await build_context_preview(request_payload, user_sub=user_sub)
    conversation_id = preview.get("conversation_id", "")
    context_pack = preview.get("context_pack", {})
    stages = list(preview.get("stages", []))

    draft_start = datetime.now(timezone.utc)
    draft = {}
    for attempt_idx, lean in enumerate((False, True), start=1):
        prompt_messages = _build_generation_prompt(context_pack if not lean else {
            **context_pack,
            "sources": list(context_pack.get("sources", []) or [])[:5],
            "curated_examples": list(context_pack.get("curated_examples", []) or [])[:2],
            "feature_siblings": list(context_pack.get("feature_siblings", []) or [])[:2],
        })
        llm_result = await llm_with_fallback(
            messages=prompt_messages,
            tier="standard",
            max_tokens=3000 if lean else 3500,
            response_format=USER_STORY_LANE_DRAFT_SCHEMA,
        )
        raw_content = getattr(llm_result, "content", "") if llm_result is not None else ""
        draft = _safe_json_load(raw_content)
        if draft:
            break
        logger.warning("[UserStoryLane] empty structured draft on attempt %s (lean=%s)", attempt_idx, lean)
    draft_duration_ms = int((datetime.now(timezone.utc) - draft_start).total_seconds() * 1000)
    if not draft:
        raise RuntimeError("Falha a gerar draft estruturado de user story.")

    validate_start = datetime.now(timezone.utc)
    validation = validate_user_story_draft(draft, context_pack)
    validate_duration_ms = int((datetime.now(timezone.utc) - validate_start).total_seconds() * 1000)
    stages.extend(
        [
            _build_stage("draft", "completed", draft_duration_ms),
            _build_stage("validator", "completed", validate_duration_ms),
        ]
    )

    draft_id = await _persist_draft(
        user_sub=user_sub,
        conversation_id=conversation_id,
        request_payload=request_payload,
        context_pack=context_pack,
        draft=draft,
        validation=validation,
        stages=stages,
    )

    confidence = validation.get("quality_score", 0.0)
    draft["confidence"] = round(max(float(draft.get("confidence", 0.0) or 0.0), float(confidence or 0.0)), 4)
    return {
        "conversation_id": conversation_id,
        "draft_id": draft_id,
        "stages": stages,
        "draft": {
            **draft,
            "title": validation.get("title", draft.get("title", "")),
            "description_html": validation.get("description_html", ""),
            "acceptance_criteria_html": validation.get("acceptance_criteria_html", ""),
        },
        "sources": context_pack.get("sources", []),
        "design_sources": context_pack.get("design_sources", []),
        "domain_profile": context_pack.get("domain_profile", {}),
        "story_policy_pack": context_pack.get("story_policy_pack", {}),
        "design_flow": (context_pack.get("design_flow", {}) or {}).get("matches", []),
        "feature_siblings": context_pack.get("feature_siblings", []),
        "feature_pack": context_pack.get("feature_pack", {}),
        "curated_workitem_refs": context_pack.get("curated_workitem_refs", []),
        "curated_examples": context_pack.get("curated_examples", []),
        "placement": context_pack.get("placement", {}),
        "similar_items": context_pack.get("similar_items", []),
        "missing_fields": context_pack.get("missing_fields", []),
        "clarification_questions": context_pack.get("clarification_questions", []),
        "confidence": draft.get("confidence", 0.0),
        "publish_ready": validation.get("publish_ready", False),
        "validation": validation,
    }


async def validate_user_story_request(request_payload: dict, *, user_sub: str) -> dict:
    draft = request_payload.get("draft")
    context_pack = {}
    if not isinstance(draft, dict):
        draft_id = str(request_payload.get("draft_id", "") or "").strip()
        if not draft_id:
            raise RuntimeError("draft_id ou draft é obrigatório para validar.")
        _, payload = await _load_draft(user_sub, draft_id)
        draft = payload.get("draft", {}) if isinstance(payload, dict) else {}
        context_pack = payload.get("context_pack", {}) if isinstance(payload, dict) else {}
    validation = validate_user_story_draft(draft, context_pack)
    return {
        "draft_id": str(request_payload.get("draft_id", "") or "").strip(),
        "draft": {
            **(draft or {}),
            "title": validation.get("title", ""),
            "description_html": validation.get("description_html", ""),
            "acceptance_criteria_html": validation.get("acceptance_criteria_html", ""),
        },
        "validation": validation,
        "publish_ready": validation.get("publish_ready", False),
    }


async def publish_user_story(request_payload: dict, *, user_sub: str) -> dict:
    draft_id = str(request_payload.get("draft_id", "") or "").strip()
    if not draft_id:
        raise RuntimeError("draft_id é obrigatório para publicar.")
    row, payload = await _load_draft(user_sub, draft_id)
    if not row or not payload:
        raise RuntimeError("Draft não encontrado.")
    draft = payload.get("draft", {}) if isinstance(payload.get("draft"), dict) else {}
    final_draft = request_payload.get("final_draft")
    effective_draft = final_draft if isinstance(final_draft, dict) and final_draft else draft
    context_pack = payload.get("context_pack", {}) if isinstance(payload.get("context_pack"), dict) else {}
    validation = validate_user_story_draft(effective_draft, context_pack)

    area_path = str(request_payload.get("area_path", "") or "").strip()
    if not area_path:
        area_path = str(row.get("CanonicalAreaPath", "") or "").strip()
    if not area_path:
        area_path = str(((context_pack.get("placement", {}) or {}).get("resolved_area_path", "")) or "").strip()
    if area_path and area_path not in DEVOPS_AREAS:
        area_path = _canonicalize_area_path(area_path)
    tags = str(request_payload.get("tags", "") or "").strip()
    if "AI-DRAFT" not in tags.upper():
        tags = ";".join(part for part in [tags, "AI-Draft"] if part)

    result = await create_workitem_in_devops(
        work_item_type="User Story",
        title=validation.get("title", ""),
        description=validation.get("description_html", ""),
        acceptance_criteria=validation.get("acceptance_criteria_html", ""),
        area_path=area_path,
        assigned_to=str(request_payload.get("assigned_to", "") or "").strip(),
        tags=tags,
    )
    if "error" in result:
        return result

    learning_update = {}
    if isinstance(final_draft, dict) and final_draft:
        learning_update = await _persist_final_draft_variant(
            user_sub=user_sub,
            draft_id=draft_id,
            row=row,
            context_pack=context_pack,
            original_draft=draft,
            final_draft=effective_draft,
            source="publish",
        )

    entity = {
        "PartitionKey": row["PartitionKey"],
        "RowKey": row["RowKey"],
        "Status": "published",
        "PublishedAt": _utc_now_iso(),
        "PublishedWorkItemId": int(result.get("id") or 0),
        "PublishedWorkItemUrl": result.get("url", ""),
        "UpdatedAt": _utc_now_iso(),
    }
    await table_merge(_DRAFTS_TABLE, entity)
    return {
        "draft_id": draft_id,
        "published": True,
        "work_item": result,
        "title": validation.get("title", ""),
        "diff_summary": learning_update.get("diff_summary", {}),
    }


def _diff_summary(draft: dict, final_draft: dict) -> dict:
    original = json.dumps(draft or {}, ensure_ascii=False, sort_keys=True)
    revised = json.dumps(final_draft or {}, ensure_ascii=False, sort_keys=True)
    matcher = difflib.SequenceMatcher(a=original, b=revised)
    changed_fields = []
    for field_name in ("title", "business_goal", "provenance", "conditions", "rules_constraints", "dependencies", "observations", "clarification_questions"):
        left = draft.get(field_name) if isinstance(draft, dict) else None
        right = final_draft.get(field_name) if isinstance(final_draft, dict) else None
        if json.dumps(left, ensure_ascii=False, sort_keys=True) != json.dumps(right, ensure_ascii=False, sort_keys=True):
            changed_fields.append(field_name)

    draft_narrative = _normalize_dict(draft.get("narrative", {}) if isinstance(draft, dict) else {})
    final_narrative = _normalize_dict(final_draft.get("narrative", {}) if isinstance(final_draft, dict) else {})
    if json.dumps(draft_narrative, ensure_ascii=False, sort_keys=True) != json.dumps(final_narrative, ensure_ascii=False, sort_keys=True):
        changed_fields.append("narrative")

    original_ac = _normalize_acceptance_items(draft.get("acceptance_criteria", []) if isinstance(draft, dict) else [])
    revised_ac = _normalize_acceptance_items(final_draft.get("acceptance_criteria", []) if isinstance(final_draft, dict) else [])
    original_ac_set = set(original_ac)
    revised_ac_set = set(revised_ac)
    original_tests = _normalize_test_items(draft.get("test_scenarios", []) if isinstance(draft, dict) else [])
    revised_tests = _normalize_test_items(final_draft.get("test_scenarios", []) if isinstance(final_draft, dict) else [])
    original_tests_set = set(original_tests)
    revised_tests_set = set(revised_tests)

    if original_ac != revised_ac:
        changed_fields.append("acceptance_criteria")
    if original_tests != revised_tests:
        changed_fields.append("test_scenarios")

    return {
        "similarity": round(float(matcher.ratio()), 4),
        "length_delta": len(revised) - len(original),
        "changed_fields": changed_fields,
        "title_changed": _normalize_scalar((draft or {}).get("title", "")) != _normalize_scalar((final_draft or {}).get("title", "")),
        "business_goal_changed": _normalize_scalar((draft or {}).get("business_goal", "")) != _normalize_scalar((final_draft or {}).get("business_goal", "")),
        "acceptance_criteria_added": max(0, len(revised_ac_set - original_ac_set)),
        "acceptance_criteria_removed": max(0, len(original_ac_set - revised_ac_set)),
        "test_scenarios_added": max(0, len(revised_tests_set - original_tests_set)),
        "test_scenarios_removed": max(0, len(original_tests_set - revised_tests_set)),
    }


async def _persist_final_draft_variant(
    *,
    user_sub: str,
    draft_id: str,
    row: dict,
    context_pack: dict,
    original_draft: dict,
    final_draft: dict,
    source: str,
) -> dict:
    if not isinstance(final_draft, dict) or not final_draft:
        return {"diff_summary": {}, "validation": validate_user_story_draft(original_draft, context_pack), "blob_ref": ""}

    validation = validate_user_story_draft(final_draft, context_pack)
    diff_summary = _diff_summary(original_draft, final_draft)
    owner_blob = safe_blob_component(user_sub or "anon", "anon")
    blob = await blob_upload_json(
        CHAT_TOOLRESULT_BLOB_CONTAINER,
        f"user-stories/{owner_blob}/{draft_id}/final-{safe_blob_component(source, 'final')}.json",
        {
            "draft": original_draft,
            "final_draft": final_draft,
            "validation": validation,
            "diff_summary": diff_summary,
            "saved_at": _utc_now_iso(),
            "source": source,
        },
    )
    await table_merge(
        _DRAFTS_TABLE,
        {
            "PartitionKey": row["PartitionKey"],
            "RowKey": row["RowKey"],
            "FinalDraftBlobRef": blob.get("blob_ref", ""),
            "FinalTitle": _clip(validation.get("title", ""), 250),
            "FinalQualityScore": float(validation.get("quality_score", 0.0) or 0.0),
            "LearningSimilarity": float(diff_summary.get("similarity", 0.0) or 0.0),
            "LearningChangedFields": json.dumps(list(diff_summary.get("changed_fields", []))[:12], ensure_ascii=False),
            "UpdatedAt": _utc_now_iso(),
        },
    )
    return {
        "diff_summary": diff_summary,
        "validation": validation,
        "blob_ref": blob.get("blob_ref", ""),
    }


async def _load_learning_variant(row: dict, payload: dict) -> dict:
    final_blob_ref = str((row or {}).get("FinalDraftBlobRef", "") or "").strip()
    if final_blob_ref and "/" in final_blob_ref:
        try:
            container, blob_name = final_blob_ref.split("/", 1)
            final_payload = await blob_download_json(container, blob_name)
        except Exception as exc:
            logger.warning("[UserStoryLane] learning final blob load failed: %s", exc)
            final_payload = {}
        if isinstance(final_payload, dict):
            final_draft = final_payload.get("final_draft", {})
            if isinstance(final_draft, dict) and final_draft:
                return final_draft
    draft = payload.get("draft", {}) if isinstance(payload.get("draft"), dict) else {}
    return draft if isinstance(draft, dict) else {}


async def _load_curated_row(draft_id: str) -> dict:
    rows = await table_query(
        _CURATED_TABLE,
        f"PartitionKey eq 'global' and RowKey eq '{odata_escape(draft_id)}'",
        top=1,
    )
    return rows[0] if rows else {}


async def _load_curated_history(row: dict) -> list[dict]:
    blob_ref = str((row or {}).get("HistoryBlobRef", "") or "").strip()
    if blob_ref and "/" in blob_ref:
        try:
            container, blob_name = blob_ref.split("/", 1)
            payload = await blob_download_json(container, blob_name)
        except Exception as exc:
            logger.warning("[UserStoryLane] curated history load failed: %s", exc)
            payload = {}
        if isinstance(payload, dict) and isinstance(payload.get("history"), list):
            return [item for item in payload.get("history", []) if isinstance(item, dict)]
    return []


async def _load_curated_entry_payload(row: dict) -> tuple[dict, dict]:
    blob_ref = str((row or {}).get("EntryBlobRef", "") or "").strip()
    if not blob_ref or "/" not in blob_ref:
        return {}, {}
    try:
        container, blob_name = blob_ref.split("/", 1)
        payload = await blob_download_json(container, blob_name)
    except Exception as exc:
        logger.warning("[UserStoryLane] curated entry load failed: %s", exc)
        payload = {}
    if not isinstance(payload, dict):
        return {}, {}
    entry = payload.get("entry", {}) if isinstance(payload.get("entry"), dict) else {}
    return entry, payload


async def _write_curated_history(*, draft_id: str, history: list[dict]) -> str:
    blob = await blob_upload_json(
        CHAT_TOOLRESULT_BLOB_CONTAINER,
        f"user-stories/curated/global/{draft_id}/history.json",
        {
            "history": history,
            "saved_at": _utc_now_iso(),
        },
    )
    return str(blob.get("blob_ref", "") or "")


async def _append_curated_history(row: dict, event: dict) -> str:
    history = await _load_curated_history(row)
    history.append(event)
    return await _write_curated_history(draft_id=str(row.get("RowKey", "") or ""), history=history[-20:])


def _curated_entry_preview(row: dict) -> dict:
    return {
        "draft_id": str(row.get("RowKey", "") or ""),
        "title": str(row.get("Title", "") or ""),
        "domain": str(row.get("Domain", "") or ""),
        "status": str(row.get("Status", "") or ""),
        "url": str(row.get("PublishedWorkItemUrl", "") or ""),
    }


async def _record_curated_search_sync_state(
    draft_id: str,
    *,
    state: str,
    error: str = "",
    document_id: str = "",
) -> None:
    current = await _load_curated_row(draft_id)
    if not current:
        return
    await table_merge(
        _CURATED_TABLE,
        {
            "PartitionKey": current["PartitionKey"],
            "RowKey": current["RowKey"],
            "SearchSyncState": _clip(state, 80),
            "SearchSyncError": _clip(error, 500),
            "SearchDocumentId": _clip(document_id or draft_id, 128),
            "SearchSyncAt": _utc_now_iso(),
            "UpdatedAt": _utc_now_iso(),
        },
    )


async def _sync_curated_row_to_search(row: dict) -> dict:
    draft_id = str((row or {}).get("RowKey", "") or "").strip()
    if not draft_id:
        return {"ok": False, "skipped": "missing_draft_id"}
    status = str((row or {}).get("Status", "") or "").strip().lower()
    if status != _CURATED_STATUS_ACTIVE:
        result = await delete_story_example_index_document(draft_id)
        state = "deleted" if result.get("ok") else str(result.get("skipped", "") or "error")
        await _record_curated_search_sync_state(
            draft_id,
            state=state,
            error=str(result.get("error", "") or ""),
            document_id=str(result.get("document_id", "") or draft_id),
        )
        return result

    entry, _ = await _load_curated_entry_payload(row)
    if not entry:
        result = {"ok": False, "error": "missing_entry_blob", "document_id": draft_id}
        await _record_curated_search_sync_state(draft_id, state="error", error="missing_entry_blob", document_id=draft_id)
        return result

    result = await upsert_story_example_index_document(draft_id=draft_id, entry=entry, row=row)
    sync_state = "synced" if result.get("ok") else str(result.get("skipped", "") or "error")
    await _record_curated_search_sync_state(
        draft_id,
        state=sync_state,
        error=str(result.get("error", "") or ""),
        document_id=str(result.get("document_id", "") or draft_id),
    )
    return result


async def sync_user_story_examples_search_index(*, draft_id: str = "", top: int = 200) -> dict:
    if str(draft_id or "").strip():
        row = await _load_curated_row(str(draft_id or "").strip())
        rows = [row] if row else []
    else:
        rows = await table_query(_CURATED_TABLE, "PartitionKey eq 'global'", top=max(1, int(top or 200)))
    summary = {
        "requested_draft_id": str(draft_id or "").strip(),
        "scanned": 0,
        "synced": 0,
        "deleted": 0,
        "skipped": 0,
        "errors": 0,
        "results": [],
    }
    for row in rows:
        if not isinstance(row, dict) or not row:
            continue
        summary["scanned"] += 1
        result = await _sync_curated_row_to_search(row)
        status = str((row or {}).get("Status", "") or "").strip().lower()
        item = {
            "draft_id": str(row.get("RowKey", "") or ""),
            "status": status,
            "result": result,
        }
        summary["results"].append(item)
        if result.get("ok") and status == _CURATED_STATUS_ACTIVE:
            summary["synced"] += 1
        elif result.get("ok"):
            summary["deleted"] += 1
        elif result.get("skipped"):
            summary["skipped"] += 1
        else:
            summary["errors"] += 1
    return summary


async def promote_user_story_to_curated_corpus(
    *,
    draft_id: str,
    source_user_sub: str,
    promoted_by: str,
    note: str = "",
) -> dict:
    row, payload = await _load_draft(source_user_sub, draft_id)
    if not row or not payload:
        raise RuntimeError("Draft não encontrado para promoção.")
    existing = await _load_curated_row(draft_id)
    existing_status = str((existing or {}).get("Status", "") or "").strip().lower()
    if existing_status in (_CURATED_STATUS_CANDIDATE, _CURATED_STATUS_ACTIVE):
        raise RuntimeError("Este draft já se encontra submetido à curadoria.")

    context_pack = payload.get("context_pack", {}) if isinstance(payload.get("context_pack"), dict) else {}
    final_draft = await _load_learning_variant(row, payload)
    if not isinstance(final_draft, dict) or not final_draft:
        raise RuntimeError("Este draft ainda não tem variante final aprendida para promover.")

    validation = validate_user_story_draft(final_draft, context_pack)
    title, description_html, acceptance_html = _render_story_html(final_draft, context_pack)
    source_id = str(row.get("PublishedWorkItemId", "") or "").strip() or f"PROMOTED-{draft_id}"
    pseudo_row = {
        "ID": source_id,
        "Work Item Type": "User Story",
        "Title": validation.get("title", title),
        "Description": description_html,
        "Acceptance Criteria": acceptance_html,
        "Assigned To": "",
        "Created By": source_user_sub,
        "State": str(row.get("Status", "") or "published").strip(),
        "Tags": "AI-Curated;Promoted",
        "Parent": str((context_pack.get("placement", {}) or {}).get("selected_feature", {}).get("id", "") or ""),
        "Area Path": str(row.get("CanonicalAreaPath", "") or ""),
    }
    entry = build_curated_story_entry(pseudo_row)
    entry["origin"] = "promoted_curated_story"
    entry["url"] = str(row.get("PublishedWorkItemUrl", "") or entry.get("url", "") or "")
    entry["source_draft_id"] = draft_id
    entry["source_user_sub"] = source_user_sub
    entry["promoted_by"] = promoted_by
    entry["promoted_at"] = _utc_now_iso()
    entry["quality_score"] = round(
        max(float(entry.get("quality_score", 0.0) or 0.0), float(row.get("FinalQualityScore", 0.0) or row.get("QualityScore", 0.0) or 0.0)),
        4,
    )

    blob = await blob_upload_json(
        CHAT_TOOLRESULT_BLOB_CONTAINER,
        f"user-stories/curated/global/{draft_id}.json",
        {
            "entry": entry,
            "source_user_sub": source_user_sub,
            "submitted_by": promoted_by,
            "note": _clip(note, 2000),
            "saved_at": _utc_now_iso(),
        },
    )
    history_blob_ref = await _write_curated_history(
        draft_id=draft_id,
        history=[
            {
                "action": "submitted",
                "by": promoted_by,
                "note": _clip(note, 2000),
                "at": _utc_now_iso(),
            }
        ],
    )
    entity = {
        "PartitionKey": "global",
        "RowKey": draft_id,
        "Status": _CURATED_STATUS_CANDIDATE,
        "SourceUserSub": source_user_sub,
        "Title": _clip(entry.get("title", ""), 250),
        "Domain": _clip(entry.get("domain", ""), 120),
        "Journey": _clip(entry.get("journey", ""), 160),
        "Flow": _clip(entry.get("flow", ""), 160),
        "SearchText": _clip(entry.get("search_text", ""), 2000),
        "QualityScore": float(entry.get("quality_score", 0.0) or 0.0),
        "PublishedWorkItemUrl": str(entry.get("url", "") or ""),
        "EntryBlobRef": blob.get("blob_ref", ""),
        "SubmittedBy": _clip(promoted_by, 120),
        "SubmittedAt": _utc_now_iso(),
        "HistoryBlobRef": history_blob_ref,
        "ReviewNote": _clip(note, 500),
        "SearchSyncState": "pending_review",
        "SearchDocumentId": _clip(draft_id, 128),
    }
    inserted = await table_insert(_CURATED_TABLE, entity)
    if not inserted:
        await table_merge(_CURATED_TABLE, entity)

    await table_merge(
        _DRAFTS_TABLE,
        {
            "PartitionKey": row["PartitionKey"],
            "RowKey": row["RowKey"],
            "PromotedToCurated": False,
            "CuratedState": _CURATED_STATUS_CANDIDATE,
            "CuratedPromotionAt": _utc_now_iso(),
            "CuratedPromotionBy": _clip(promoted_by, 120),
            "UpdatedAt": _utc_now_iso(),
        },
    )
    return {
        "draft_id": draft_id,
        "submitted": True,
        "status": _CURATED_STATUS_CANDIDATE,
        "entry": _curated_entry_preview(entity),
    }


async def review_user_story_curated_candidate(
    *,
    draft_id: str,
    action: str,
    reviewed_by: str,
    note: str = "",
) -> dict:
    row = await _load_curated_row(draft_id)
    if not row:
        raise RuntimeError("Entrada de curadoria não encontrada.")

    current_status = str(row.get("Status", "") or "").strip().lower()
    desired_action = str(action or "").strip().lower()
    if desired_action == "approve":
        if current_status not in (_CURATED_STATUS_CANDIDATE, _CURATED_STATUS_INACTIVE):
            raise RuntimeError("Só candidatos ou entradas inativas podem ser aprovados.")
        next_status = _CURATED_STATUS_ACTIVE
    elif desired_action == "reject":
        if current_status != _CURATED_STATUS_CANDIDATE:
            raise RuntimeError("Só candidatos pendentes podem ser rejeitados.")
        next_status = _CURATED_STATUS_REJECTED
    elif desired_action == "deactivate":
        if current_status != _CURATED_STATUS_ACTIVE:
            raise RuntimeError("Só entradas ativas podem ser desativadas.")
        next_status = _CURATED_STATUS_INACTIVE
    elif desired_action == "reactivate":
        if current_status != _CURATED_STATUS_INACTIVE:
            raise RuntimeError("Só entradas inativas podem ser reativadas.")
        next_status = _CURATED_STATUS_ACTIVE
    else:
        raise RuntimeError("Ação de curadoria inválida.")

    history_blob_ref = await _append_curated_history(
        row,
        {
            "action": desired_action,
            "from_status": current_status,
            "to_status": next_status,
            "by": reviewed_by,
            "note": _clip(note, 2000),
            "at": _utc_now_iso(),
        },
    )
    merged = {
        "PartitionKey": row["PartitionKey"],
        "RowKey": row["RowKey"],
        "Status": next_status,
        "ReviewedBy": _clip(reviewed_by, 120),
        "ReviewedAt": _utc_now_iso(),
        "ReviewNote": _clip(note, 500),
        "HistoryBlobRef": history_blob_ref,
        "UpdatedAt": _utc_now_iso(),
    }
    if next_status == _CURATED_STATUS_ACTIVE:
        merged["ActivatedAt"] = _utc_now_iso()
    if next_status == _CURATED_STATUS_INACTIVE:
        merged["DeactivatedAt"] = _utc_now_iso()
    await table_merge(_CURATED_TABLE, merged)

    draft_owner_sub = str(row.get("SourceUserSub", "") or "").strip()
    if draft_owner_sub:
        await table_merge(
            _DRAFTS_TABLE,
            {
                "PartitionKey": _draft_partition_key(draft_owner_sub),
                "RowKey": draft_id,
                "PromotedToCurated": next_status == _CURATED_STATUS_ACTIVE,
                "CuratedState": next_status,
                "UpdatedAt": _utc_now_iso(),
            },
        )

    search_sync = await _sync_curated_row_to_search({**row, **merged})

    refreshed = {**row, **merged}
    return {
        "draft_id": draft_id,
        "status": next_status,
        "entry": _curated_entry_preview(refreshed),
        "search_sync": search_sync,
    }


async def record_user_story_feedback(request_payload: dict, *, user_sub: str) -> dict:
    draft_id = str(request_payload.get("draft_id", "") or "").strip()
    if not draft_id:
        raise RuntimeError("draft_id é obrigatório para feedback.")
    row, payload = await _load_draft(user_sub, draft_id)
    if not row or not payload:
        raise RuntimeError("Draft não encontrado.")

    draft = payload.get("draft", {}) if isinstance(payload.get("draft"), dict) else {}
    final_draft = request_payload.get("final_draft")
    diff_summary = _diff_summary(draft, final_draft) if isinstance(final_draft, dict) and final_draft else {}
    event_id = uuid.uuid4().hex
    learning_update = {}
    event_blob_ref = ""
    if isinstance(final_draft, dict) and final_draft:
        learning_update = await _persist_final_draft_variant(
            user_sub=user_sub,
            draft_id=draft_id,
            row=row,
            context_pack=payload.get("context_pack", {}) if isinstance(payload.get("context_pack"), dict) else {},
            original_draft=draft,
            final_draft=final_draft,
            source=f"feedback-{event_id}",
        )
        event_blob_ref = learning_update.get("blob_ref", "")
        diff_summary = learning_update.get("diff_summary", diff_summary)

    feedback_entity = {
        "PartitionKey": _draft_partition_key(user_sub),
        "RowKey": event_id,
        "DraftId": draft_id,
        "ResolvedDomain": _clip(str(row.get("ResolvedDomain", "") or ""), 120),
        "DraftTitle": _clip(str(row.get("FinalTitle", "") or row.get("Title", "") or ""), 250),
        "Outcome": str(request_payload.get("outcome", "") or "").strip().lower(),
        "Note": _clip(request_payload.get("note", ""), 2000),
        "EventBlobRef": event_blob_ref,
        "CreatedAt": _utc_now_iso(),
        "Similarity": float(diff_summary.get("similarity", 0.0) or 0.0),
    }
    inserted = await table_insert(_FEEDBACK_TABLE, feedback_entity)
    if not inserted:
        await table_merge(_FEEDBACK_TABLE, feedback_entity)

    await table_merge(
        _DRAFTS_TABLE,
        {
            "PartitionKey": row["PartitionKey"],
            "RowKey": row["RowKey"],
            "FeedbackOutcome": feedback_entity["Outcome"],
            "FeedbackNote": feedback_entity["Note"],
            "FeedbackEventId": event_id,
            "LearningSimilarity": float(diff_summary.get("similarity", 0.0) or 0.0),
            "UpdatedAt": _utc_now_iso(),
        },
    )
    return {
        "draft_id": draft_id,
        "event_id": event_id,
        "outcome": feedback_entity["Outcome"],
        "diff_summary": diff_summary,
    }


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _safe_json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item).strip() for item in parsed if str(item).strip()]


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 4)


def _rate(part: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(float(part) / float(total), 4)


def _extract_domain_from_title(title: str) -> str:
    parts = [segment.strip() for segment in str(title or "").split("|") if segment.strip()]
    if len(parts) >= 2 and parts[0].upper() == "ACME":
        return parts[1]
    return ""


def _extract_eval_domain(row: dict, payload: dict) -> str:
    direct = str(row.get("ResolvedDomain", "") or row.get("PolicyDomain", "") or "").strip()
    if direct:
        return direct
    context_pack = payload.get("context_pack", {}) if isinstance(payload, dict) else {}
    candidates = [
        (context_pack.get("domain_profile", {}) or {}).get("domain"),
        (context_pack.get("story_policy_pack", {}) or {}).get("domain"),
        ((context_pack.get("design_map", {}) or {}).get("matches", []) or [{}])[0].get("domain"),
        _extract_domain_from_title(str(row.get("Title", "") or "")),
        _extract_domain_from_title(str((context_pack.get("placement", {}) or {}).get("selected_feature", {}).get("title", "") or "")),
        _extract_domain_from_title(str((context_pack.get("placement", {}) or {}).get("selected_epic", {}).get("title", "") or "")),
    ]
    for candidate in candidates:
        value = str(candidate or "").strip()
        if value:
            return value
    return "Unknown"


def _row_has_final_variant(row: dict) -> bool:
    return bool(str(row.get("FinalDraftBlobRef", "") or "").strip())


async def _load_eval_payload(row: dict) -> dict:
    blob_ref = str(row.get("DraftBlobRef", "") or "").strip()
    if not blob_ref or "/" not in blob_ref:
        return {}
    try:
        container, blob_name = blob_ref.split("/", 1)
    except ValueError:
        return {}
    try:
        payload = await blob_download_json(container, blob_name)
    except Exception as exc:
        logger.warning("[UserStoryLane] eval payload load failed: %s", exc)
        return {}
    return payload if isinstance(payload, dict) else {}


def _summarize_eval_row(row: dict, payload: dict) -> dict:
    domain = _extract_eval_domain(row, payload)
    quality_score = _safe_float(row.get("QualityScore", 0.0))
    final_quality = _safe_float(row.get("FinalQualityScore", 0.0))
    placement_confidence = _safe_float(row.get("PlacementConfidence", 0.0))
    has_final_variant = _row_has_final_variant(row)
    similarity = _safe_float(row.get("LearningSimilarity", 0.0))
    changed_fields = _safe_json_list(row.get("LearningChangedFields", ""))
    return {
        "draft_id": str(row.get("RowKey", "") or ""),
        "owner_sub": _partition_user_sub(str(row.get("PartitionKey", "") or "")),
        "domain": domain,
        "title": str(row.get("FinalTitle", "") or row.get("Title", "") or "").strip(),
        "status": str(row.get("Status", "") or "").strip().lower(),
        "feedback_outcome": str(row.get("FeedbackOutcome", "") or "").strip().lower(),
        "publish_ready": bool(row.get("PublishReady", False)),
        "quality_score": quality_score,
        "final_quality_score": final_quality,
        "placement_confidence": placement_confidence,
        "resolved_epic": str(row.get("ResolvedEpic", "") or "").strip(),
        "resolved_feature": str(row.get("ResolvedFeature", "") or "").strip(),
        "canonical_area_path": str(row.get("CanonicalAreaPath", "") or "").strip(),
        "has_final_variant": has_final_variant,
        "promoted_to_curated": bool(row.get("PromotedToCurated", False)),
        "curated_state": str(row.get("CuratedState", "") or "").strip().lower(),
        "similarity": similarity if has_final_variant else None,
        "edit_burden": round(max(0.0, 1.0 - similarity), 4) if has_final_variant else None,
        "changed_fields": changed_fields,
        "created_at": str(row.get("CreatedAt", "") or ""),
        "updated_at": str(row.get("UpdatedAt", "") or ""),
        "published_at": str(row.get("PublishedAt", "") or ""),
    }


def _aggregate_eval_bucket(rows: list[dict]) -> dict:
    count = len(rows)
    status_counts = {
        "draft": sum(1 for row in rows if row.get("status") == "draft"),
        "published": sum(1 for row in rows if row.get("status") == "published"),
        "accepted": sum(1 for row in rows if row.get("status") == "accepted"),
    }
    feedback_counts = {
        "accepted": sum(1 for row in rows if row.get("feedback_outcome") == "accepted"),
        "edited": sum(1 for row in rows if row.get("feedback_outcome") == "edited"),
        "rejected": sum(1 for row in rows if row.get("feedback_outcome") == "rejected"),
    }
    quality_scores = [float(row.get("quality_score", 0.0) or 0.0) for row in rows]
    final_quality_scores = [float(row.get("final_quality_score", 0.0) or 0.0) for row in rows if float(row.get("final_quality_score", 0.0) or 0.0) > 0.0]
    placement_scores = [float(row.get("placement_confidence", 0.0) or 0.0) for row in rows if float(row.get("placement_confidence", 0.0) or 0.0) > 0.0]
    similarities = [float(row.get("similarity", 0.0) or 0.0) for row in rows if row.get("similarity") is not None]
    edit_burdens = [float(row.get("edit_burden", 0.0) or 0.0) for row in rows if row.get("edit_burden") is not None]
    changed_fields: dict[str, int] = {}
    for row in rows:
        for field_name in row.get("changed_fields", []):
            changed_fields[field_name] = changed_fields.get(field_name, 0) + 1
    top_changed_fields = [
        {"field": field_name, "count": count_value}
        for field_name, count_value in sorted(changed_fields.items(), key=lambda item: (-item[1], item[0]))[:6]
    ]
    return {
        "draft_count": count,
        "published_count": status_counts["published"],
        "publish_rate": _rate(status_counts["published"], count),
        "publish_ready_count": sum(1 for row in rows if row.get("publish_ready")),
        "publish_ready_rate": _rate(sum(1 for row in rows if row.get("publish_ready")), count),
        "feedback_count": sum(1 for row in rows if row.get("feedback_outcome")),
        "feedback_rate": _rate(sum(1 for row in rows if row.get("feedback_outcome")), count),
        "feedback_breakdown": feedback_counts,
        "status_breakdown": status_counts,
        "avg_quality_score": _mean(quality_scores),
        "avg_final_quality_score": _mean(final_quality_scores),
        "avg_placement_confidence": _mean(placement_scores),
        "final_variant_count": sum(1 for row in rows if row.get("has_final_variant")),
        "avg_similarity": _mean(similarities),
        "avg_edit_burden": _mean(edit_burdens),
        "top_changed_fields": top_changed_fields,
    }


def _build_eval_recommendations(domain_summary: list[dict]) -> list[dict]:
    recommendations = []
    for item in domain_summary:
        domain = str(item.get("domain", "") or "Unknown")
        draft_count = int(item.get("draft_count", 0) or 0)
        alerts = set(item.get("alerts", []) or [])
        top_fields = [entry.get("field", "") for entry in item.get("top_changed_fields", []) if entry.get("field")]
        if "corpus_coverage_low" in alerts:
            recommendations.append(
                {
                    "priority": "high" if draft_count >= 3 else "medium",
                    "action": "curate_examples",
                    "domain": domain,
                    "title": f"Reforçar corpus curado de {domain}",
                    "rationale": f"O domínio tem {draft_count} drafts recentes e apenas {int(item.get('curated_example_count', 0) or 0)} exemplos curados.",
                    "signals": ["corpus_coverage_low"],
                    "suggested_next_step": "Adicionar 5-10 user stories fortes deste domínio ao corpus curado.",
                }
            )
        if "edit_burden_high" in alerts:
            recommendations.append(
                {
                    "priority": "high",
                    "action": "review_policy_pack",
                    "domain": domain,
                    "title": f"Rever policy pack de {domain}",
                    "rationale": "O esforço de edição humana continua alto depois da geração do draft.",
                    "signals": ["edit_burden_high", *top_fields[:3]],
                    "suggested_next_step": "Reforçar template, nível de detalhe e linguagem preferida com base nos campos mais editados.",
                }
            )
        if "placement_confidence_low" in alerts:
            recommendations.append(
                {
                    "priority": "medium",
                    "action": "reinforce_flow_map",
                    "domain": domain,
                    "title": f"Melhorar placement e flow map de {domain}",
                    "rationale": "A confiança média de placement está baixa para este domínio.",
                    "signals": ["placement_confidence_low"],
                    "suggested_next_step": "Adicionar mais evidência de Epic/Feature e frames/flows canónicos para o domínio.",
                }
            )
        if "publish_rate_low" in alerts:
            recommendations.append(
                {
                    "priority": "medium",
                    "action": "inspect_publish_friction",
                    "domain": domain,
                    "title": f"Inspecionar fricção de publicação em {domain}",
                    "rationale": "A taxa de publicação está baixa face ao volume de drafts gerados.",
                    "signals": ["publish_rate_low", *top_fields[:2]],
                    "suggested_next_step": "Comparar drafts gerados com finais aceites para perceber onde a lane falha antes de publicar.",
                }
            )
    priority_rank = {"high": 0, "medium": 1, "low": 2}
    recommendations.sort(
        key=lambda item: (
            priority_rank.get(str(item.get("priority", "low")), 9),
            str(item.get("domain", "") or ""),
            str(item.get("action", "") or ""),
        )
    )
    return recommendations[:12]


def _build_curation_candidates(eval_rows: list[dict]) -> list[dict]:
    candidates = []
    for row in eval_rows:
        status = str(row.get("status", "") or "").strip().lower()
        feedback_outcome = str(row.get("feedback_outcome", "") or "").strip().lower()
        final_quality = float(row.get("final_quality_score", 0.0) or 0.0)
        quality = float(row.get("quality_score", 0.0) or 0.0)
        similarity = row.get("similarity")
        edit_burden = row.get("edit_burden")
        has_final_variant = bool(row.get("has_final_variant"))
        if bool(row.get("promoted_to_curated", False)) or str(row.get("curated_state", "") or "").strip():
            continue
        if feedback_outcome == "rejected":
            continue
        if status != "published" and feedback_outcome not in ("accepted", "edited"):
            continue
        if not has_final_variant and status != "published":
            continue
        if similarity is not None and float(similarity or 0.0) < _EVAL_CURATION_MIN_SIMILARITY and max(final_quality, quality) < 0.86:
            continue
        reasons = []
        if status == "published":
            reasons.append("published")
        if feedback_outcome:
            reasons.append(f"feedback:{feedback_outcome}")
        if final_quality >= 0.88 or quality >= 0.88:
            reasons.append("quality_strong")
        elif final_quality >= _EVAL_CURATION_MIN_QUALITY or quality >= _EVAL_CURATION_MIN_QUALITY:
            reasons.append("quality_viable")
        if edit_burden is not None and float(edit_burden or 0.0) <= 0.12:
            reasons.append("low_edit_burden")
        if has_final_variant:
            reasons.append("has_final_variant")
        promotion_score = max(final_quality, quality)
        if similarity is not None:
            promotion_score += float(similarity or 0.0) * 0.15
        if status == "published":
            promotion_score += 0.08
        if feedback_outcome == "accepted":
            promotion_score += 0.08
        elif feedback_outcome == "edited":
            promotion_score += 0.04
        candidates.append(
            {
                "draft_id": row.get("draft_id", ""),
                "owner_sub": row.get("owner_sub", ""),
                "domain": row.get("domain", "Unknown"),
                "title": row.get("title", ""),
                "quality_score": round(max(final_quality, quality), 4),
                "edit_burden": round(float(edit_burden or 0.0), 4) if edit_burden is not None else None,
                "feedback_outcome": feedback_outcome,
                "reasons": reasons,
                "promotion_score": round(promotion_score, 4),
            }
        )
    candidates.sort(
        key=lambda item: (
            float(item.get("promotion_score", 0.0) or 0.0),
            1.0 - float(item.get("edit_burden", 1.0) or 1.0),
            str(item.get("domain", "") or ""),
        ),
        reverse=True,
    )
    return candidates[:8]


async def _build_curated_registry_summary(*, top: int = 120) -> dict:
    try:
        rows = await table_query(_CURATED_TABLE, "PartitionKey eq 'global'", top=max(1, min(int(top or 120), 300)))
    except Exception as exc:
        logger.warning("[UserStoryLane] curated registry query failed: %s", exc)
        return {"counts": {}, "review_queue": [], "recent_active": []}

    counts = {
        _CURATED_STATUS_CANDIDATE: 0,
        _CURATED_STATUS_ACTIVE: 0,
        _CURATED_STATUS_REJECTED: 0,
        _CURATED_STATUS_INACTIVE: 0,
    }
    review_queue = []
    recent_active = []
    for row in rows:
        status = str(row.get("Status", "") or "").strip().lower()
        if status in counts:
            counts[status] += 1
        item = {
            "draft_id": str(row.get("RowKey", "") or ""),
            "source_user_sub": str(row.get("SourceUserSub", "") or ""),
            "title": str(row.get("Title", "") or ""),
            "domain": str(row.get("Domain", "") or ""),
            "status": status,
            "quality_score": round(float(row.get("QualityScore", 0.0) or 0.0), 4),
            "submitted_at": str(row.get("SubmittedAt", "") or ""),
            "reviewed_at": str(row.get("ReviewedAt", "") or ""),
            "reviewed_by": str(row.get("ReviewedBy", "") or ""),
            "url": str(row.get("PublishedWorkItemUrl", "") or ""),
        }
        if status == _CURATED_STATUS_CANDIDATE:
            review_queue.append(item)
        elif status == _CURATED_STATUS_ACTIVE:
            recent_active.append(item)
    review_queue.sort(key=lambda item: str(item.get("submitted_at", "") or ""), reverse=True)
    recent_active.sort(key=lambda item: str(item.get("reviewed_at", "") or item.get("submitted_at", "") or ""), reverse=True)
    return {
        "counts": counts,
        "review_queue": review_queue[:8],
        "recent_active": recent_active[:8],
    }


async def build_user_story_eval_summary(*, user_sub: str = "", top: int = 250) -> dict:
    safe_top = max(1, min(int(top or 250), 1000))
    filter_str = ""
    if user_sub:
        filter_str = f"PartitionKey eq '{odata_escape(_draft_partition_key(user_sub))}'"

    draft_rows = await table_query(_DRAFTS_TABLE, filter_str, top=safe_top)
    feedback_rows = await table_query(_FEEDBACK_TABLE, filter_str, top=safe_top)

    payloads = await asyncio.gather(*(_load_eval_payload(row) for row in draft_rows)) if draft_rows else []
    eval_rows = [_summarize_eval_row(row, payload) for row, payload in zip(draft_rows, payloads)]

    domains: dict[str, list[dict]] = {}
    for row in eval_rows:
        domains.setdefault(str(row.get("domain", "Unknown") or "Unknown"), []).append(row)

    domain_summary = []
    for domain_name, domain_rows in sorted(domains.items(), key=lambda item: (-len(item[1]), item[0])):
        bucket = _aggregate_eval_bucket(domain_rows)
        domain_summary.append(
            {
                "domain": domain_name,
                **bucket,
            }
        )

    curated_summary = get_curated_story_corpus_summary()
    curated_domain_counts = curated_summary.get("domain_counts", {}) if isinstance(curated_summary, dict) else {}
    corpus_gaps = []
    enriched_domains = []
    for item in domain_summary:
        normalized_domain = _normalize_story_text(item.get("domain", "")).lower()
        curated_bucket = curated_domain_counts.get(normalized_domain, {}) if isinstance(curated_domain_counts, dict) else {}
        curated_count = int(curated_bucket.get("count", 0) or 0)
        alerts = []
        if item["draft_count"] >= _EVAL_MIN_DOMAIN_SAMPLES and item["avg_edit_burden"] >= _EVAL_HIGH_EDIT_BURDEN:
            alerts.append("edit_burden_high")
        if item["draft_count"] >= _EVAL_MIN_DOMAIN_SAMPLES and item["publish_rate"] <= _EVAL_LOW_PUBLISH_RATE:
            alerts.append("publish_rate_low")
        if item["draft_count"] >= _EVAL_MIN_DOMAIN_SAMPLES and item["avg_quality_score"] <= _EVAL_LOW_QUALITY:
            alerts.append("quality_low")
        if item["draft_count"] >= _EVAL_MIN_DOMAIN_SAMPLES and item["avg_placement_confidence"] <= _EVAL_LOW_PLACEMENT_CONFIDENCE:
            alerts.append("placement_confidence_low")
        if item["draft_count"] >= 2 and curated_count <= _EVAL_LOW_CORPUS_THRESHOLD:
            alerts.append("corpus_coverage_low")
            corpus_gaps.append(
                {
                    "domain": item["domain"],
                    "draft_count": item["draft_count"],
                    "curated_example_count": curated_count,
                    "recommended_action": "Adicionar mais user stories boas deste domínio ao corpus curado.",
                }
            )
        enriched_domains.append(
            {
                **item,
                "curated_example_count": curated_count,
                "alerts": alerts,
            }
        )
    domain_summary = enriched_domains

    hotspots = [
        {
            "domain": item["domain"],
            "draft_count": item["draft_count"],
            "publish_rate": item["publish_rate"],
            "avg_edit_burden": item["avg_edit_burden"],
            "avg_quality_score": item["avg_quality_score"],
            "curated_example_count": item.get("curated_example_count", 0),
            "alerts": item.get("alerts", []),
            "top_changed_fields": item["top_changed_fields"][:3],
        }
        for item in sorted(
            domain_summary,
            key=lambda entry: (
                float(entry.get("avg_edit_burden", 0.0) or 0.0),
                1.0 - float(entry.get("publish_rate", 0.0) or 0.0),
                float(entry.get("draft_count", 0) or 0),
            ),
            reverse=True,
        )[:5]
    ]

    most_edited = [
        {
            "draft_id": row["draft_id"],
            "domain": row["domain"],
            "title": row["title"],
            "feedback_outcome": row["feedback_outcome"],
            "similarity": row["similarity"],
            "edit_burden": row["edit_burden"],
            "changed_fields": row["changed_fields"][:5],
        }
        for row in sorted(
            [item for item in eval_rows if item.get("edit_burden") is not None],
            key=lambda item: (
                float(item.get("edit_burden", 0.0) or 0.0),
                float(item.get("quality_score", 0.0) or 0.0) * -1.0,
                str(item.get("updated_at", "") or ""),
            ),
            reverse=True,
        )[:5]
    ]

    totals = _aggregate_eval_bucket(eval_rows)
    alerts = []
    totals_draft_count = int(totals["draft_count"])
    if totals_draft_count >= 5 and totals["avg_edit_burden"] >= _EVAL_HIGH_EDIT_BURDEN:
        alerts.append(
            {
                "level": "warning",
                "code": "edit_burden_high",
                "message": "O esforço médio de edição humana ainda está alto na lane de user stories.",
            }
        )
    if totals_draft_count >= 5 and totals["publish_rate"] <= _EVAL_LOW_PUBLISH_RATE:
        alerts.append(
            {
                "level": "warning",
                "code": "publish_rate_low",
                "message": "A taxa de publicação ainda está baixa face ao volume de drafts gerados.",
            }
        )
    if corpus_gaps:
        alerts.append(
            {
                "level": "info",
                "code": "corpus_gaps",
                "message": "Existem domínios com poucos exemplos curados face ao volume de drafts recentes.",
            }
        )
    if any(item.get("alerts") for item in domain_summary):
        alerts.append(
            {
                "level": "info",
                "code": "domain_hotspots",
                "message": "Há domínios que merecem reforço de corpus, placement ou policy pack.",
            }
        )

    feedback_breakdown = {
        "accepted": sum(1 for row in feedback_rows if str(row.get("Outcome", "") or "").strip().lower() == "accepted"),
        "edited": sum(1 for row in feedback_rows if str(row.get("Outcome", "") or "").strip().lower() == "edited"),
        "rejected": sum(1 for row in feedback_rows if str(row.get("Outcome", "") or "").strip().lower() == "rejected"),
    }
    recommendations = _build_eval_recommendations(domain_summary)
    curation_candidates = _build_curation_candidates(eval_rows)
    curated_registry = await _build_curated_registry_summary()

    return {
        "scope": {
            "user_sub": str(user_sub or "").strip(),
            "top": safe_top,
            "draft_rows_sampled": len(draft_rows),
            "feedback_rows_sampled": len(feedback_rows),
            "truncated": len(draft_rows) >= safe_top or len(feedback_rows) >= safe_top,
        },
        "totals": {
            **totals,
            "feedback_event_count": len(feedback_rows),
            "feedback_event_breakdown": feedback_breakdown,
            "domain_count": len(domain_summary),
            "recommendation_count": len(recommendations),
            "curation_candidate_count": len(curation_candidates),
            "curation_review_queue_count": int((curated_registry.get("counts", {}) or {}).get(_CURATED_STATUS_CANDIDATE, 0) or 0),
        },
        "corpus": curated_summary,
        "alerts": alerts,
        "corpus_gaps": corpus_gaps,
        "recommendations": recommendations,
        "curation_candidates": curation_candidates,
        "curated_registry": curated_registry,
        "domains": domain_summary,
        "hotspots": hotspots,
        "most_edited": most_edited,
    }
