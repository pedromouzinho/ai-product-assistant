"""Persistent feature-specific packs for high-signal user story flows."""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

_PACK_PATH = Path(__file__).resolve().parent / "data" / "story_feature_packs.json"


def _normalize_text(value: str) -> str:
    text = str(value or "").strip().lower()
    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", folded).strip()


def _tokenize(value: str) -> set[str]:
    normalized = re.sub(r"[^a-z0-9 ]+", " ", _normalize_text(value))
    return {token for token in normalized.split() if len(token) >= 3}


@lru_cache(maxsize=1)
def _load_feature_packs() -> list[dict]:
    try:
        payload = json.loads(_PACK_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    entries = payload.get("entries", []) if isinstance(payload, dict) else []
    return [entry for entry in entries if isinstance(entry, dict)]


def _pack_score(entry: dict, *, query_text: str, query_tokens: set[str], feature_id: str, feature_title: str) -> float:
    score = 0.0
    pack_feature_id = str(entry.get("feature_id", "") or "").strip()
    if feature_id and pack_feature_id == feature_id:
        score += 2.0

    pack_title = str(entry.get("feature_title", "") or "")
    title_norm = _normalize_text(pack_title)
    feature_title_norm = _normalize_text(feature_title)
    if feature_title_norm and title_norm and feature_title_norm in title_norm:
        score += 0.9

    searchable = " ".join(
        [
            pack_title,
            str(entry.get("domain", "") or ""),
            str(entry.get("journey", "") or ""),
            str(entry.get("flow", "") or ""),
            " ".join(str(item) for item in entry.get("aliases", []) if item),
            " ".join(str(item) for item in entry.get("top_ux_terms", []) if item),
            " ".join(str(item) for item in entry.get("top_titles", []) if item),
        ]
    )
    search_tokens = _tokenize(searchable)
    if query_tokens and search_tokens:
        score += (len(query_tokens & search_tokens) / max(1, len(query_tokens))) * 0.9

    if title_norm and title_norm in query_text:
        score += 0.5
    return round(score, 4)


def select_story_feature_pack(
    *,
    objective: str = "",
    context: str = "",
    team_scope: str = "",
    epic_or_feature: str = "",
    placement: dict | None = None,
) -> dict:
    placement = placement or {}
    selected_feature = placement.get("selected_feature", {}) if isinstance(placement, dict) else {}
    feature_id = str((selected_feature or {}).get("id", "") or "").strip()
    feature_title = str((selected_feature or {}).get("title", "") or epic_or_feature or "").strip()
    merged = " | ".join(
        part for part in [objective, context, team_scope, epic_or_feature, feature_title, feature_id] if str(part or "").strip()
    )
    query_text = _normalize_text(merged)
    query_tokens = _tokenize(merged)

    ranked = []
    for entry in _load_feature_packs():
        score = _pack_score(
            entry,
            query_text=query_text,
            query_tokens=query_tokens,
            feature_id=feature_id,
            feature_title=feature_title,
        )
        if score < 0.65:
            continue
        ranked.append({**entry, "score": score})
    ranked.sort(key=lambda item: float(item.get("score", 0.0) or 0.0), reverse=True)
    return ranked[0] if ranked else {}


def serialize_feature_pack_story(item: dict) -> dict:
    return {
        "id": str(item.get("id", "") or ""),
        "title": str(item.get("title", "") or ""),
        "snippet": str(item.get("snippet", "") or ""),
        "url": str(item.get("url", "") or ""),
        "ux_terms": list(item.get("ux_terms", []) or [])[:6],
        "score": round(float(item.get("score", 0.0) or 0.0), 4),
        "origin": str(item.get("origin", "") or "devops_feature_pack"),
    }


def serialize_feature_pack(entry: dict) -> dict:
    if not isinstance(entry, dict):
        return {}
    return {
        "feature_id": str(entry.get("feature_id", "") or ""),
        "feature_title": str(entry.get("feature_title", "") or ""),
        "area_path": str(entry.get("area_path", "") or ""),
        "domain": str(entry.get("domain", "") or ""),
        "journey": str(entry.get("journey", "") or ""),
        "flow": str(entry.get("flow", "") or ""),
        "story_count": int(entry.get("story_count", 0) or 0),
        "canonical_title_pattern": str(entry.get("canonical_title_pattern", "") or ""),
        "top_ux_terms": list(entry.get("top_ux_terms", []) or [])[:10],
        "top_flows": list(entry.get("top_flows", []) or [])[:6],
        "top_titles": list(entry.get("top_titles", []) or [])[:5],
        "figma_url": str(entry.get("figma_url", "") or ""),
        "notes": list(entry.get("notes", []) or [])[:5],
        "stories": [serialize_feature_pack_story(item) for item in list(entry.get("stories", []) or [])[:5]],
        "score": round(float(entry.get("score", 0.0) or 0.0), 4),
    }
