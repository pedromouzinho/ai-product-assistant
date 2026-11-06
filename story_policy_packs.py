"""Persistent policy packs for domain-aware user story generation."""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

_POLICY_PATH = Path(__file__).resolve().parent / "data" / "story_policy_packs.json"


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
def _load_policy_packs() -> list[dict]:
    try:
        payload = json.loads(_POLICY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    entries = payload.get("entries", []) if isinstance(payload, dict) else []
    return [entry for entry in entries if isinstance(entry, dict)]


def _pack_score(entry: dict, query_text: str, query_tokens: set[str], dominant_domain: str) -> float:
    score = 0.0
    domain = _normalize_text(entry.get("domain", ""))
    if dominant_domain and domain == dominant_domain:
        # Keep the policy pack aligned with the already-resolved dominant
        # domain when one is explicitly supplied by upstream routing.
        score += 1.2
    elif domain and domain in query_text:
        score += 0.65

    for alias in entry.get("aliases", []) or []:
        alias_norm = _normalize_text(alias)
        if alias_norm and alias_norm in query_text:
            score += 0.18

    searchable = " ".join(
        [
            str(entry.get("domain", "") or ""),
            " ".join(str(item) for item in entry.get("top_journeys", []) if item),
            " ".join(str(item) for item in entry.get("top_flows", []) if item),
            " ".join(str(item) for item in entry.get("preferred_lexicon", []) if item),
            " ".join(str(item) for item in entry.get("notes", []) if item),
        ]
    )
    search_tokens = _tokenize(searchable)
    if query_tokens and search_tokens:
        score += (len(query_tokens & search_tokens) / max(1, len(query_tokens))) * 0.75

    score += min(0.1, float(entry.get("coverage_score", 0.0) or 0.0) * 0.1)
    score += min(0.08, float(entry.get("production_confidence", 0.0) or 0.0) * 0.08)
    return round(score, 4)


def select_story_policy_pack(
    *,
    objective: str = "",
    context: str = "",
    team_scope: str = "",
    epic_or_feature: str = "",
    dominant_domain: str = "",
) -> dict:
    merged = " | ".join(part for part in [objective, context, team_scope, epic_or_feature, dominant_domain] if str(part or "").strip())
    query_text = _normalize_text(merged)
    query_tokens = _tokenize(merged)
    dominant_domain_norm = _normalize_text(dominant_domain)
    ranked = []
    for entry in _load_policy_packs():
        score = _pack_score(entry, query_text, query_tokens, dominant_domain_norm)
        if score < 0.4:
            continue
        ranked.append({**entry, "score": score})
    ranked.sort(key=lambda item: float(item.get("score", 0.0) or 0.0), reverse=True)
    return ranked[0] if ranked else {}
