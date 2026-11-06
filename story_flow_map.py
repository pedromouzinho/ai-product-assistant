"""Hybrid story flow map built from Figma design registry and curated user stories."""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

_FLOW_MAP_PATH = Path(__file__).resolve().parent / "data" / "story_flow_map.json"


def _normalize_text(value: str) -> str:
    text = str(value or "").strip().lower()
    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", folded).strip()


def _expand_identifier_text(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
    return text


def _tokenize(value: str) -> set[str]:
    normalized = re.sub(r"[^a-z0-9 ]+", " ", _normalize_text(_expand_identifier_text(value)))
    return {token for token in normalized.split() if len(token) >= 3}


@lru_cache(maxsize=1)
def _load_story_flow_map() -> list[dict]:
    try:
        payload = json.loads(_FLOW_MAP_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    entries = payload.get("entries", []) if isinstance(payload, dict) else []
    return [entry for entry in entries if isinstance(entry, dict)]


def _searchable_text(entry: dict) -> str:
    parts = [
        str(entry.get("domain", "") or ""),
        str(entry.get("journey", "") or ""),
        str(entry.get("flow", "") or ""),
        str(entry.get("detail", "") or ""),
        str(entry.get("title", "") or ""),
        str(entry.get("site_placement", "") or ""),
        str(entry.get("routing_note", "") or ""),
        " ".join(str(item) for item in entry.get("aliases", []) if item),
        " ".join(str(item) for item in entry.get("ux_terms", []) if item),
        " ".join(str(item) for item in entry.get("ui_components", []) if item),
    ]
    return " ".join(part for part in parts if part)


def _flow_score(entry: dict, query_text: str, query_tokens: set[str], dominant_domain: str) -> float:
    searchable = _searchable_text(entry)
    normalized_search = _normalize_text(searchable)
    score = 0.0

    domain = _normalize_text(entry.get("domain", ""))
    if dominant_domain and dominant_domain == domain:
        score += 0.55
    elif domain and domain in query_text:
        score += 0.4

    for field in ("journey", "flow", "detail"):
        value = _normalize_text(entry.get(field, ""))
        if value and value in query_text:
            score += 0.38 if field == "flow" else 0.22

    aliases = entry.get("aliases", []) or []
    for alias in aliases:
        alias_norm = _normalize_text(alias)
        if alias_norm and alias_norm in query_text:
            score += 0.16

    search_tokens = _tokenize(normalized_search)
    if query_tokens and search_tokens:
        score += (len(query_tokens & search_tokens) / max(1, len(query_tokens))) * 0.75

    source_kind = str(entry.get("source_kind", "") or "")
    if source_kind == "curated_story":
        score += min(0.16, float(entry.get("quality_score", 0.0) or 0.0) * 0.16)
    elif source_kind == "design_journey":
        score += 0.08

    score += min(0.12, float(entry.get("currentness_score", 0.0) or 0.0) * 0.12)
    score += min(0.1, float(entry.get("production_confidence", 0.0) or 0.0) * 0.1)
    return round(score, 4)


def _dedupe(entries: list[dict]) -> list[dict]:
    deduped: dict[str, dict] = {}
    for entry in entries:
        key = str(entry.get("dedupe_key", "") or entry.get("id", "") or "").strip()
        if not key:
            continue
        current = deduped.get(key)
        if current is None or float(entry.get("score", 0.0) or 0.0) > float(current.get("score", 0.0) or 0.0):
            deduped[key] = entry
    return sorted(deduped.values(), key=lambda item: float(item.get("score", 0.0) or 0.0), reverse=True)


def search_story_flow_map(
    *,
    objective: str = "",
    context: str = "",
    team_scope: str = "",
    epic_or_feature: str = "",
    dominant_domain: str = "",
    top: int = 4,
) -> dict:
    merged = " | ".join(part for part in [objective, context, team_scope, epic_or_feature, dominant_domain] if str(part or "").strip())
    query_text = _normalize_text(merged)
    query_tokens = _tokenize(merged)
    dominant = _normalize_text(dominant_domain)
    if not query_text:
        return {"matches": [], "notes": [], "dominant_domain": dominant_domain}

    ranked = []
    for entry in _load_story_flow_map():
        score = _flow_score(entry, query_text, query_tokens, dominant)
        if score < 0.45:
            continue
        ranked.append({**entry, "score": score})

    matches = _dedupe(ranked)[: max(1, int(top or 4))]
    notes = []
    if matches:
        notes.append(f"Flow map persistente aponta para {matches[0].get('domain', '')} > {matches[0].get('journey', '')}.")
    return {
        "matches": matches,
        "notes": notes[:4],
        "dominant_domain": str(matches[0].get("domain", "") or dominant_domain) if matches else dominant_domain,
    }


def serialize_story_flow_match(entry: dict) -> dict:
    flow = str(entry.get("flow", "") or "").strip()
    journey = str(entry.get("journey", "") or "").strip()
    title_parts = []
    if flow:
        title_parts.append(flow)
    if journey and _normalize_text(journey) != _normalize_text(flow):
        title_parts.append(journey)
    title = " · ".join(part for part in title_parts if part) or str(entry.get("title", "") or "")
    source_kind = str(entry.get("source_kind", "") or "")
    evidence = "Exemplo curado" if source_kind == "curated_story" else "Mapa de jornada"
    snippet = " ".join(
        part
        for part in [
            f"Domínio {entry.get('domain', '')}.",
            f"{evidence}.",
            str(entry.get("site_placement", "") or ""),
            str(entry.get("routing_note", "") or ""),
        ]
        if part
    ).strip()
    return {
        "key": f"story-flow:{entry.get('id', '')}",
        "type": "story_flow_map",
        "title": title,
        "snippet": snippet,
        "url": str(entry.get("url", "") or ""),
        "score": round(float(entry.get("score", 0.0) or 0.0), 4),
        "origin": "story_flow_map",
        "domain": str(entry.get("domain", "") or ""),
        "file_title": str(entry.get("file_title", "") or ""),
        "page_name": str(entry.get("journey", "") or ""),
        "frame_name": str(entry.get("flow", "") or entry.get("detail", "") or ""),
        "node_id": str(entry.get("source_work_item_id", "") or ""),
        "ui_components": list(entry.get("ui_components", []) or [])[:6],
        "source_kind": source_kind,
    }
