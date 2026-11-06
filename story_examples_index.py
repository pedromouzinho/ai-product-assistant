"""Azure AI Search sync/search helpers for approved user story examples."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from config import API_VERSION_SEARCH, SEARCH_KEY, SEARCH_SERVICE, STORY_EXAMPLES_INDEX
from http_helpers import search_request_with_retry
from tools_knowledge import get_embedding

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _index_ready() -> bool:
    return bool(str(SEARCH_SERVICE or "").strip() and str(SEARCH_KEY or "").strip() and str(STORY_EXAMPLES_INDEX or "").strip())


def _index_url(suffix: str) -> str:
    return (
        f"https://{SEARCH_SERVICE}.search.windows.net/indexes/"
        f"{STORY_EXAMPLES_INDEX}/{suffix}?api-version={API_VERSION_SEARCH}"
    )


def _headers() -> dict[str, str]:
    return {"api-key": SEARCH_KEY, "Content-Type": "application/json"}


def _clip(value: Any, max_len: int) -> str:
    text = str(value or "").strip()
    return text[:max_len]


def _coerce_str_list(values: Any, *, max_items: int = 40, max_len: int = 200) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for raw in values[:max_items]:
        item = _clip(raw, max_len)
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _build_content(entry: dict) -> str:
    sections = entry.get("sections", {}) if isinstance(entry.get("sections"), dict) else {}
    section_lines = [
        f"{str(name or '').strip()}: {str(value or '').strip()}"
        for name, value in sections.items()
        if str(name or "").strip() and str(value or "").strip()
    ]
    parts = [
        _clip(entry.get("title", ""), 500),
        _clip(entry.get("description_text", ""), 4000),
        _clip(entry.get("acceptance_text", ""), 6000),
        "\n".join(section_lines)[:6000],
        " ".join(_coerce_str_list(entry.get("ux_terms", []), max_items=20, max_len=80)),
        " ".join(_coerce_str_list(entry.get("workitem_refs", []), max_items=20, max_len=24)),
        _clip(entry.get("search_text", ""), 12000),
    ]
    return "\n".join(part for part in parts if part).strip()[:16000]


def build_story_example_index_document(*, draft_id: str, entry: dict, row: dict | None = None) -> dict:
    row = dict(row or {})
    content = _build_content(entry)
    return {
        "id": _clip(draft_id, 128),
        "title": _clip(entry.get("title", ""), 500),
        "content": content,
        "search_text": _clip(entry.get("search_text", "") or content, 16000),
        "description_text": _clip(entry.get("description_text", ""), 6000),
        "acceptance_text": _clip(entry.get("acceptance_text", ""), 12000),
        "sections_json": json.dumps(entry.get("sections", {}), ensure_ascii=False)[:12000],
        "ux_terms": _coerce_str_list(entry.get("ux_terms", []), max_items=40, max_len=80),
        "tags": _coerce_str_list(entry.get("tags", []), max_items=40, max_len=80),
        "workitem_refs": _coerce_str_list(entry.get("workitem_refs", []), max_items=40, max_len=32),
        "title_pattern": _clip(entry.get("title_pattern", ""), 500),
        "domain": _clip(entry.get("domain", ""), 160),
        "journey": _clip(entry.get("journey", ""), 160),
        "flow": _clip(entry.get("flow", ""), 160),
        "detail": _clip(entry.get("detail", ""), 160),
        "url": _clip(entry.get("url", "") or row.get("PublishedWorkItemUrl", ""), 2000),
        "area_path": _clip(entry.get("area_path", "") or row.get("AreaPath", "") or row.get("Area Path", ""), 500),
        "status": _clip(row.get("Status", "") or "active", 32).lower() or "active",
        "visibility": "global",
        "source_kind": "promoted_curated_story",
        "source_draft_id": _clip(draft_id, 128),
        "source_user_sub": _clip(entry.get("source_user_sub", "") or row.get("SourceUserSub", ""), 200),
        "promoted_by": _clip(entry.get("promoted_by", "") or row.get("SubmittedBy", ""), 200),
        "quality_score": float(entry.get("quality_score", 0.0) or row.get("QualityScore", 0.0) or 0.0),
        "updated_at": _clip(row.get("UpdatedAt", "") or row.get("ReviewedAt", "") or entry.get("promoted_at", "") or _utc_now_iso(), 80),
    }


async def upsert_story_example_index_document(*, draft_id: str, entry: dict, row: dict | None = None) -> dict:
    if not _index_ready():
        return {"ok": False, "skipped": "search_not_configured"}
    doc = build_story_example_index_document(draft_id=draft_id, entry=entry, row=row)
    embedding = await get_embedding(doc.get("search_text", "") or doc.get("content", ""))
    if not embedding:
        return {"ok": False, "skipped": "missing_embedding"}
    doc["content_vector"] = embedding
    payload = {"value": [{"@search.action": "mergeOrUpload", **doc}]}
    data = await search_request_with_retry(
        url=_index_url("docs/index"),
        headers=_headers(),
        json_body=payload,
        max_retries=3,
        timeout=30,
    )
    if "error" in data:
        logger.warning("[StoryExamplesIndex] upsert failed for %s: %s", draft_id, data["error"])
        return {"ok": False, "error": data["error"], "document_id": draft_id}
    return {"ok": True, "document_id": draft_id}


async def delete_story_example_index_document(draft_id: str) -> dict:
    if not _index_ready():
        return {"ok": False, "skipped": "search_not_configured"}
    payload = {
        "value": [
            {
                "@search.action": "delete",
                "id": _clip(draft_id, 128),
            }
        ]
    }
    data = await search_request_with_retry(
        url=_index_url("docs/index"),
        headers=_headers(),
        json_body=payload,
        max_retries=3,
        timeout=30,
    )
    if "error" in data:
        logger.warning("[StoryExamplesIndex] delete failed for %s: %s", draft_id, data["error"])
        return {"ok": False, "error": data["error"], "document_id": draft_id}
    return {"ok": True, "document_id": draft_id, "deleted": True}


def _parse_sections(raw_value: Any) -> dict:
    if isinstance(raw_value, dict):
        return raw_value
    if not isinstance(raw_value, str) or not raw_value.strip():
        return {}
    try:
        parsed = json.loads(raw_value)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _result_to_entry(doc: dict) -> dict:
    return {
        "id": str(doc.get("id", "") or ""),
        "title": str(doc.get("title", "") or ""),
        "domain": str(doc.get("domain", "") or ""),
        "journey": str(doc.get("journey", "") or ""),
        "flow": str(doc.get("flow", "") or ""),
        "detail": str(doc.get("detail", "") or ""),
        "title_pattern": str(doc.get("title_pattern", "") or ""),
        "area_path": str(doc.get("area_path", "") or ""),
        "description_text": str(doc.get("description_text", "") or ""),
        "acceptance_text": str(doc.get("acceptance_text", "") or ""),
        "sections": _parse_sections(doc.get("sections_json", "")),
        "ux_terms": _coerce_str_list(doc.get("ux_terms", []), max_items=40, max_len=80),
        "tags": _coerce_str_list(doc.get("tags", []), max_items=40, max_len=80),
        "workitem_refs": _coerce_str_list(doc.get("workitem_refs", []), max_items=40, max_len=32),
        "quality_score": float(doc.get("quality_score", 0.0) or 0.0),
        "url": str(doc.get("url", "") or ""),
        "origin": "promoted_curated_story",
        "source_draft_id": str(doc.get("source_draft_id", "") or ""),
        "source_user_sub": str(doc.get("source_user_sub", "") or ""),
    }


async def search_story_examples_index(*, query_text: str, dominant_domain: str = "", top: int = 4) -> dict:
    if not _index_ready():
        return {"matches": [], "promoted_count": 0, "source": "disabled"}
    effective_query = str(query_text or "").strip()
    if not effective_query:
        return {"matches": [], "promoted_count": 0, "source": "empty_query"}
    body: dict[str, Any] = {
        "search": effective_query[:1200],
        "select": ",".join(
            [
                "id",
                "title",
                "domain",
                "journey",
                "flow",
                "detail",
                "title_pattern",
                "area_path",
                "description_text",
                "acceptance_text",
                "sections_json",
                "ux_terms",
                "tags",
                "workitem_refs",
                "quality_score",
                "url",
                "source_draft_id",
                "source_user_sub",
            ]
        ),
        "top": max(1, int(top or 4)),
        "count": True,
        "filter": "status eq 'active' and visibility eq 'global'",
    }
    embedding = await get_embedding(effective_query)
    if embedding:
        body["vectorQueries"] = [
            {
                "kind": "vector",
                "vector": embedding,
                "fields": "content_vector",
                "k": max(4, int(top or 4) * 2),
            }
        ]
    data = await search_request_with_retry(
        url=_index_url("docs/search"),
        headers=_headers(),
        json_body=body,
        max_retries=3,
        timeout=30,
    )
    if "error" in data:
        logger.warning("[StoryExamplesIndex] search failed: %s", data["error"])
        return {"matches": [], "promoted_count": 0, "source": "error", "error": data["error"]}

    dominant = str(dominant_domain or "").strip().lower()
    scored: list[tuple[float, dict]] = []
    for doc in data.get("value", []) or []:
        entry = _result_to_entry(doc)
        score = float(doc.get("@search.score", 0.0) or 0.0)
        row_domain = str(entry.get("domain", "") or "").strip().lower()
        if dominant and row_domain and row_domain == dominant:
            score += 0.25
        entry["score"] = round(score, 4)
        scored.append((score, entry))
    scored.sort(key=lambda item: (float(item[0]), float(item[1].get("quality_score", 0.0) or 0.0)), reverse=True)
    return {
        "matches": [entry for _, entry in scored[: max(1, int(top or 4))]],
        "promoted_count": int(data.get("@odata.count", 0) or 0),
        "source": "azure_ai_search_story_examples",
    }
