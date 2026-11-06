"""Curated user story corpus built from exported CSV examples."""

from __future__ import annotations

import html
import json
import re
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from config import DEVOPS_ORG, DEVOPS_PROJECT, STORY_CONTEXT_CURATED_TOP

_CORPUS_PATH = Path(__file__).resolve().parent / "data" / "curated_story_examples.json"
_UX_TERMS = [
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
    "Header",
    "Chip",
    "Tooltip",
    "Accordion",
]
_SECTION_HEADINGS = [
    "proveniência",
    "condições",
    "composição",
    "comportamento",
    "mockup",
    "objetivo",
    "cenários de teste",
    "critérios de aceitação",
    "observações",
    "assunções",
    "assuncoes",
    "documentos pdf",
]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_text(value: str) -> str:
    text = str(value or "").strip().lower()
    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", folded).strip()


def _tokenize(value: str) -> list[str]:
    normalized = re.sub(r"[^a-z0-9 ]+", " ", _normalize_text(value))
    return [token for token in normalized.split() if len(token) >= 3]


def _unique(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        normalized = str(item or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def clean_story_html(value: str, *, max_len: int = 4000) -> str:
    raw = str(value or "")
    if not raw.strip():
        return ""
    text = html.unescape(raw)
    text = text.replace("\u200b", " ").replace("\ufeff", " ")
    text = re.sub(r"(?is)<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", r"\2 (\1)", text)
    text = re.sub(r"(?is)<img[^>]*alt=[\"']?([^\"'>]+)[\"']?[^>]*>", r" [Imagem: \1] ", text)
    text = re.sub(r"(?i)<br\\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(div|p|section|article|h[1-6]|table|tr)>", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "\n- ", text)
    text = re.sub(r"(?i)</(ul|ol)>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = text.strip()
    if max_len and len(text) > max_len:
        return text[: max_len - 1].rstrip() + "…"
    return text


def _extract_title_parts(title: str) -> dict:
    segments = [segment.strip() for segment in str(title or "").split("|") if segment.strip()]
    parts = {
        "segments": segments,
        "segment_count": len(segments),
        "prefix": segments[0] if segments else "",
        "domain": segments[1] if len(segments) >= 2 else "",
        "journey": segments[2] if len(segments) >= 3 else "",
        "flow": segments[3] if len(segments) >= 4 else "",
        "detail": segments[4] if len(segments) >= 5 else "",
    }
    parts["title_pattern"] = " | ".join(
        [
            "[Prefix]",
            "[Domínio]" if parts["domain"] else "",
            "[Jornada/Subárea]" if parts["journey"] else "",
            "[Fluxo/Step]" if parts["flow"] else "",
            "[Detalhe]" if parts["detail"] else "",
        ]
    ).replace(" |  |", " | ").strip(" |")
    return parts


def _line_is_heading(line: str) -> str:
    normalized = _normalize_text(line).strip("-: ")
    for heading in _SECTION_HEADINGS:
        if normalized == _normalize_text(heading):
            return heading
    return ""


def _extract_sections(text: str) -> dict:
    sections: dict[str, list[str]] = {}
    current = ""
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        heading = _line_is_heading(line)
        if heading:
            current = heading
            sections.setdefault(current, [])
            continue
        if current:
            sections.setdefault(current, []).append(line)
    return {key: "\n".join(values).strip() for key, values in sections.items() if values}


def _extract_workitem_refs(*values: str) -> list[int]:
    refs = set()
    for value in values:
        for match in re.findall(r"_workitems/edit/(\d{3,8})", str(value or "")):
            try:
                refs.add(int(match))
            except Exception:
                continue
        for match in re.findall(r"\bUser Story\s+(\d{3,8})\b", str(value or ""), flags=re.I):
            try:
                refs.add(int(match))
            except Exception:
                continue
    return sorted(refs)


def _detect_ux_terms(*values: str) -> list[str]:
    text = " ".join(str(value or "") for value in values)
    norm = _normalize_text(text)
    found = []
    for term in _UX_TERMS:
        if _normalize_text(term) in norm:
            found.append(term)
    return _unique(found)


def _quality_score(sections: dict, description_text: str, acceptance_text: str, refs: list[int]) -> float:
    score = 0.25
    score += min(0.25, len(sections) * 0.05)
    if description_text:
        score += 0.12
    if acceptance_text:
        score += 0.18
    if refs:
        score += 0.08
    if sections.get("proveniência"):
        score += 0.08
    if sections.get("comportamento"):
        score += 0.08
    if sections.get("mockup"):
        score += 0.06
    return round(min(score, 0.98), 4)


def _example_url(work_item_id: str) -> str:
    work_id = str(work_item_id or "").strip()
    if not work_id:
        return ""
    return f"https://dev.azure.com/{DEVOPS_ORG}/{DEVOPS_PROJECT}/_workitems/edit/{work_id}"


def build_curated_story_entry(row: dict) -> dict:
    title = str(row.get("Title", "") or "").strip()
    title_parts = _extract_title_parts(title)
    description_text = clean_story_html(row.get("Description", ""), max_len=5000)
    acceptance_text = clean_story_html(row.get("Acceptance Criteria", ""), max_len=9000)
    sections = _extract_sections(acceptance_text)
    refs = _extract_workitem_refs(row.get("Description", ""), row.get("Acceptance Criteria", ""))
    ux_terms = _detect_ux_terms(title, description_text, acceptance_text)

    search_text_parts = [
        title,
        description_text,
        acceptance_text,
        " ".join(ux_terms),
        " ".join(str(ref) for ref in refs),
        str(row.get("Tags", "") or ""),
        str(row.get("Created By", "") or ""),
    ]
    search_text = " ".join(part for part in search_text_parts if part).strip()
    search_tokens = _unique(_tokenize(search_text))

    return {
        "id": str(row.get("ID", "") or "").strip(),
        "work_item_type": str(row.get("Work Item Type", "") or "").strip(),
        "title": title,
        "title_segments": title_parts["segments"],
        "title_segment_count": title_parts["segment_count"],
        "title_pattern": title_parts["title_pattern"],
        "domain": title_parts["domain"],
        "journey": title_parts["journey"],
        "flow": title_parts["flow"],
        "detail": title_parts["detail"],
        "assigned_to": str(row.get("Assigned To", "") or "").strip(),
        "created_by": str(row.get("Created By", "") or "").strip(),
        "state": str(row.get("State", "") or "").strip(),
        "tags": [segment.strip() for segment in str(row.get("Tags", "") or "").split(";") if segment.strip()],
        "parent_id": str(row.get("Parent", "") or "").strip(),
        "area_path": str(row.get("Area Path", "") or "").strip(),
        "description_text": description_text,
        "acceptance_text": acceptance_text,
        "sections": sections,
        "ux_terms": ux_terms,
        "workitem_refs": refs,
        "quality_score": _quality_score(sections, description_text, acceptance_text, refs),
        "search_text": search_text[:12000],
        "search_tokens": search_tokens[:600],
        "url": _example_url(row.get("ID", "")),
    }


def build_curated_story_payload(rows: list[dict], *, source_path: str = "") -> dict:
    entries = [build_curated_story_entry(row) for row in rows if str(row.get("Title", "") or "").strip()]
    domains = Counter(_normalize_text(entry.get("domain", "")) for entry in entries if entry.get("domain"))
    authors = Counter(str(entry.get("created_by", "") or "").strip() for entry in entries if entry.get("created_by"))
    patterns = Counter(str(entry.get("title_pattern", "") or "").strip() for entry in entries if entry.get("title_pattern"))
    lexicon = Counter(term for entry in entries for term in entry.get("ux_terms", []) or [])

    return {
        "version": "2026-03-10",
        "generated_at": _utc_now_iso(),
        "source_path": source_path,
        "metadata": {
            "count": len(entries),
            "top_domains": domains.most_common(10),
            "top_authors": authors.most_common(10),
            "top_title_patterns": patterns.most_common(6),
            "top_ux_terms": lexicon.most_common(20),
        },
        "entries": entries,
    }


@lru_cache(maxsize=1)
def _load_curated_corpus_payload() -> dict:
    if not _CORPUS_PATH.exists():
        return {"metadata": {}, "entries": []}
    try:
        return json.loads(_CORPUS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"metadata": {}, "entries": []}


def _match_score(entry: dict, query_text: str, query_tokens: set[str], dominant_domain: str) -> float:
    if not query_tokens:
        return 0.0
    entry_tokens = set(entry.get("search_tokens", []) or [])
    overlap = len(query_tokens & entry_tokens) / max(1, len(query_tokens))
    score = overlap

    domain = _normalize_text(entry.get("domain", ""))
    if domain and domain in query_text:
        score += 0.45
    if dominant_domain and domain and domain == dominant_domain:
        score += 0.35

    journey = _normalize_text(entry.get("journey", ""))
    flow = _normalize_text(entry.get("flow", ""))
    if journey and journey in query_text:
        score += 0.2
    if flow and flow in query_text:
        score += 0.25

    journey_tokens = set(_tokenize(entry.get("journey", "")))
    flow_tokens = set(_tokenize(entry.get("flow", "")))
    detail_tokens = set(_tokenize(entry.get("detail", "")))
    if journey_tokens:
        score += (len(query_tokens & journey_tokens) / max(1, len(journey_tokens))) * 0.35
    if flow_tokens:
        score += (len(query_tokens & flow_tokens) / max(1, len(flow_tokens))) * 0.3
    if detail_tokens:
        score += (len(query_tokens & detail_tokens) / max(1, len(detail_tokens))) * 0.15

    score += min(0.15, float(entry.get("quality_score", 0.0) or 0.0) * 0.2)
    if entry.get("workitem_refs"):
        score += 0.05
    return round(score, 4)


def search_curated_story_examples(
    *,
    objective: str = "",
    context: str = "",
    team_scope: str = "",
    epic_or_feature: str = "",
    dominant_design_domain: str = "",
    top: int = STORY_CONTEXT_CURATED_TOP,
) -> dict:
    payload = _load_curated_corpus_payload()
    entries = payload.get("entries", []) if isinstance(payload, dict) else []
    if not entries:
        return {
            "matches": [],
            "title_patterns": [],
            "preferred_lexicon": [],
            "notes": [],
            "corpus_stats": {},
        }

    merged = " | ".join(
        part for part in [objective, context, team_scope, epic_or_feature, dominant_design_domain] if str(part or "").strip()
    )
    query_text = _normalize_text(merged)
    query_tokens = set(_tokenize(merged))
    dominant_domain = _normalize_text(dominant_design_domain)

    scored = []
    for entry in entries:
        score = _match_score(entry, query_text, query_tokens, dominant_domain)
        if score < 0.16:
            continue
        scored.append({**entry, "score": score})

    scored.sort(
        key=lambda item: (float(item.get("score", 0.0) or 0.0), float(item.get("quality_score", 0.0) or 0.0)),
        reverse=True,
    )
    matches = scored[: max(1, int(top or STORY_CONTEXT_CURATED_TOP or 4))]

    title_patterns = Counter(entry.get("title_pattern", "") for entry in matches if entry.get("title_pattern")).most_common(3)
    preferred_lexicon = Counter(term for entry in matches for term in entry.get("ux_terms", []) or []).most_common(10)
    notes = []
    if matches:
        dominant = matches[0].get("domain", "") or matches[0].get("journey", "")
        if dominant:
            notes.append(f"O corpus curado aponta {dominant} como domínio/jornada provável para este pedido.")
    if title_patterns:
        notes.append(f"O padrão de título mais forte é {title_patterns[0][0]}.")
    if preferred_lexicon:
        notes.append(
            "Termos UX/UI recorrentes nas stories semelhantes: " + ", ".join(term for term, _ in preferred_lexicon[:6]) + "."
        )

    return {
        "matches": matches,
        "title_patterns": [pattern for pattern, _ in title_patterns],
        "preferred_lexicon": [term for term, _ in preferred_lexicon],
        "notes": notes[:5],
        "corpus_stats": payload.get("metadata", {}),
    }


def serialize_curated_story_match(entry: dict) -> dict:
    provenance = entry.get("sections", {}).get("proveniência", "")
    behavior = entry.get("sections", {}).get("comportamento", "")
    snippet_parts = [
        f"Domínio {entry.get('domain', '') or 'n/a'}.",
        f"Criada por {entry.get('created_by', '') or 'n/a'}.",
        provenance[:220] if provenance else "",
        behavior[:220] if behavior else "",
    ]
    return {
        "key": f"curated:{entry.get('id', '')}",
        "type": "curated_story_example",
        "title": str(entry.get("title", "") or ""),
        "snippet": " ".join(part for part in snippet_parts if part).strip(),
        "url": str(entry.get("url", "") or ""),
        "score": round(float(entry.get("score", 0.0) or 0.0), 4),
        "origin": str(entry.get("origin", "") or "curated_story_csv"),
        "author": str(entry.get("created_by", "") or ""),
        "title_pattern": str(entry.get("title_pattern", "") or ""),
        "ux_terms": list(entry.get("ux_terms", []) or []),
    }


def get_curated_story_corpus_summary() -> dict:
    payload = _load_curated_corpus_payload()
    entries = payload.get("entries", []) if isinstance(payload, dict) else []
    metadata = payload.get("metadata", {}) if isinstance(payload, dict) else {}
    domain_counts = Counter()
    domain_labels: dict[str, str] = {}
    for entry in entries:
        label = str(entry.get("domain", "") or "").strip()
        normalized = _normalize_text(label)
        if not normalized:
            continue
        domain_counts[normalized] += 1
        domain_labels.setdefault(normalized, label)
    return {
        "count": int(metadata.get("count", len(entries)) or len(entries)),
        "top_domains": list(metadata.get("top_domains", []) or []),
        "top_authors": list(metadata.get("top_authors", []) or []),
        "top_title_patterns": list(metadata.get("top_title_patterns", []) or []),
        "top_ux_terms": list(metadata.get("top_ux_terms", []) or []),
        "domain_counts": {
            normalized: {
                "label": domain_labels.get(normalized, normalized.title()),
                "count": int(count),
            }
            for normalized, count in domain_counts.items()
        },
    }
