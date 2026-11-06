"""Curated Figma design registry for the user story lane."""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

_MAP_PATH = Path(__file__).resolve().parent / "data" / "figma_story_map.json"


@lru_cache(maxsize=1)
def _load_story_design_map() -> list[dict]:
    try:
        payload = json.loads(_MAP_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    entries = payload.get("entries", []) if isinstance(payload, dict) else []
    return [entry for entry in entries if isinstance(entry, dict)]


def _normalize_text(value: str) -> str:
    text = str(value or "").strip().lower()
    folded = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in folded if not unicodedata.combining(ch))


def _tokenize(value: str) -> set[str]:
    normalized = re.sub(r"[^a-z0-9 ]+", " ", _normalize_text(value))
    return {token for token in normalized.split() if len(token) >= 3}


def _build_search_text(entry: dict) -> str:
    parts: list[str] = [
        str(entry.get("domain", "") or ""),
        str(entry.get("title", "") or ""),
        str(entry.get("site_placement", "") or ""),
        str(entry.get("routing_note", "") or ""),
        " ".join(str(item) for item in entry.get("aliases", []) if item),
        " ".join(str(item) for item in entry.get("journeys", []) if item),
        " ".join(str(item) for item in entry.get("ux_terms", []) if item),
    ]
    return " ".join(part for part in parts if part)


def _coverage_bonus(label: str) -> float:
    normalized = _normalize_text(label)
    if normalized == "high":
        return 0.08
    if normalized == "medium":
        return 0.04
    return 0.0


def _entry_score(entry: dict, query_text: str, query_tokens: set[str]) -> float:
    searchable = _build_search_text(entry)
    searchable_norm = _normalize_text(searchable)
    score = 0.0

    for alias in entry.get("aliases", []) or []:
        alias_norm = _normalize_text(alias)
        if alias_norm and alias_norm in query_text:
            score += 1.0

    domain_norm = _normalize_text(entry.get("domain", ""))
    if domain_norm and domain_norm in query_text:
        score += 0.85

    search_tokens = _tokenize(searchable_norm)
    if query_tokens and search_tokens:
        overlap = len(query_tokens & search_tokens) / max(1, len(query_tokens))
        score += overlap * 0.8

    score += float(entry.get("currentness_score", 0.0) or 0.0) * 0.18
    score += min(0.2, float(entry.get("preferred_rank", 0) or 0) / 150.0)
    score += _coverage_bonus(entry.get("curated_example_coverage", ""))

    status = _normalize_text(entry.get("status", ""))
    if status == "meta_repository":
        score -= 0.8
    elif status == "fallback_handoff":
        score -= 0.15

    return round(score, 4)


def _dedupe_by_family(scored_entries: list[dict]) -> list[dict]:
    families: dict[str, dict] = {}
    for entry in scored_entries:
        family = str(entry.get("family", "") or entry.get("id", "") or "").strip() or entry.get("id", "")
        current = families.get(family)
        if current is None or float(entry.get("score", 0.0) or 0.0) > float(current.get("score", 0.0) or 0.0):
            families[family] = entry
    return sorted(families.values(), key=lambda item: float(item.get("score", 0.0) or 0.0), reverse=True)


def search_story_design_map(
    *,
    objective: str = "",
    context: str = "",
    team_scope: str = "",
    epic_or_feature: str = "",
    top: int = 4,
) -> dict:
    merged = " | ".join(part for part in [objective, context, team_scope, epic_or_feature] if str(part or "").strip())
    query_text = _normalize_text(merged)
    query_tokens = _tokenize(merged)
    if not query_text:
        return {"matches": [], "dominant_domain": "", "ux_terms": [], "notes": []}

    scored = []
    for entry in _load_story_design_map():
        score = _entry_score(entry, query_text, query_tokens)
        if score < 0.6:
            continue
        scored.append({**entry, "score": score})

    matches = _dedupe_by_family(scored)[: max(1, int(top or 4))]
    ux_terms: list[str] = []
    notes: list[str] = []
    for entry in matches:
        for term in entry.get("ux_terms", []) or []:
            if term not in ux_terms:
                ux_terms.append(term)
        note = str(entry.get("routing_note", "") or "").strip()
        if note and note not in notes:
            notes.append(note)

    return {
        "matches": matches,
        "dominant_domain": str(matches[0].get("domain", "") or "") if matches else "",
        "ux_terms": ux_terms[:12],
        "notes": notes[:6],
    }


def serialize_design_match(entry: dict) -> dict:
    return {
        "key": f"figma:{entry.get('file_key', '')}",
        "type": "design_handoff",
        "title": str(entry.get("title", "") or ""),
        "snippet": (
            f"Domínio {entry.get('domain', '')}. "
            f"Journeys: {', '.join(entry.get('journeys', [])[:3]) or 'n/a'}. "
            f"{entry.get('routing_note', '')}".strip()
        ),
        "url": str(entry.get("url", "") or ""),
        "score": round(float(entry.get("score", 0.0) or 0.0), 4),
        "origin": "figma_story_map",
        "domain": str(entry.get("domain", "") or ""),
        "status": str(entry.get("status", "") or ""),
        "currentness_score": round(float(entry.get("currentness_score", 0.0) or 0.0), 4),
        "production_confidence": round(float(entry.get("production_confidence", 0.0) or 0.0), 4),
        "file_key": str(entry.get("file_key", "") or ""),
    }
