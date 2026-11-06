"""Dedicated Azure AI Search sync/search helpers for story knowledge grounding."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import API_VERSION_SEARCH, OMNI_INDEX, SEARCH_KEY, SEARCH_SERVICE, STORY_KNOWLEDGE_INDEX
from azure_auth import build_search_auth_headers
from figma_story_map import search_story_design_map
from http_helpers import search_request_with_retry
from story_domain_profiles import select_story_domain_profile
from story_flow_map import search_story_flow_map
from story_policy_packs import select_story_policy_pack
from storage import table_insert, table_merge
from tools_knowledge import get_embedding

logger = logging.getLogger(__name__)

_SYNC_TABLE = "IndexSyncState"
_SYNC_PARTITION = "story_knowledge_index"
_SYNC_ROW = "latest"
_DATA_DIR = Path(__file__).resolve().parent / "data"
_INDEX_EMBED_CONCURRENCY = 2
_INDEX_BATCH_SIZE = 10
_INDEX_BATCH_DELAY_SECONDS = 2.0
_INDEX_MAX_RETRIES = 5
_INDEX_UPLOAD_ATTEMPTS = 4
_LOCAL_SEED_CACHE: list[dict] | None = None


def _clip(value: Any, max_len: int) -> str:
    return str(value or "").strip()[:max_len]


def _safe_doc_id(value: Any, *, max_len: int = 128) -> str:
    raw = str(value or "").strip()
    normalized = re.sub(r"[^A-Za-z0-9_=\\-]+", "_", raw).strip("_")
    return normalized[:max_len] or "story_knowledge_doc"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _target_index_ready() -> bool:
    return bool(str(SEARCH_SERVICE or "").strip() and str(STORY_KNOWLEDGE_INDEX or "").strip())


def _source_index_ready() -> bool:
    return bool(str(SEARCH_SERVICE or "").strip() and str(OMNI_INDEX or "").strip())


async def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    headers.update(await build_search_auth_headers(api_key=SEARCH_KEY, service_name=SEARCH_SERVICE))
    return headers


def _source_url(suffix: str) -> str:
    return f"https://{SEARCH_SERVICE}.search.windows.net/indexes/{OMNI_INDEX}/{suffix}?api-version={API_VERSION_SEARCH}"


def _target_url(suffix: str) -> str:
    return f"https://{SEARCH_SERVICE}.search.windows.net/indexes/{STORY_KNOWLEDGE_INDEX}/{suffix}?api-version={API_VERSION_SEARCH}"


def _first_content_line(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    compact = re.sub(r"\s+", " ", text)
    return compact[:240]


def _coerce_str_list(values: Any, *, max_items: int = 24, max_len: int = 120) -> list[str]:
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


def _normalize_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", folded).strip()


def _tokenize(value: Any) -> set[str]:
    normalized = re.sub(r"[^a-z0-9 ]+", " ", _normalize_text(value))
    return {token for token in normalized.split() if len(token) >= 3}


def _source_title(item: dict) -> str:
    tag = _clip(item.get("tag", ""), 220)
    if tag:
        return tag
    return _first_content_line(item.get("content", ""))


def _load_json_entries(filename: str) -> list[dict]:
    path = _DATA_DIR / filename
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    entries = payload.get("entries", []) if isinstance(payload, dict) else []
    return [entry for entry in entries if isinstance(entry, dict)]


def _build_local_seed_documents() -> list[dict]:
    docs: list[dict] = []

    for entry in _load_json_entries("figma_story_map.json"):
        docs.append(
            {
                "id": _safe_doc_id(f"figma:{_clip(entry.get('file_key', '') or entry.get('id', ''), 120)}"),
                "title": _clip(entry.get("title", "") or entry.get("domain", ""), 500),
                "content": "\n".join(
                    part
                    for part in [
                        _clip(entry.get("title", ""), 500),
                        _clip(entry.get("domain", ""), 160),
                        " ".join(str(item) for item in entry.get("journeys", [])[:8] if item),
                        _clip(entry.get("site_placement", ""), 500),
                        _clip(entry.get("routing_note", ""), 800),
                        " ".join(str(item) for item in entry.get("ux_terms", [])[:12] if item),
                    ]
                    if part
                )[:16000],
                "url": _clip(entry.get("url", ""), 2000),
                "tag": "Figma handoff",
                "domain": _clip(entry.get("domain", ""), 160),
                "journey": _clip((entry.get("journeys", []) or [""])[0], 160),
                "flow": _clip((entry.get("journeys", []) or [""])[-1], 160),
                "detail": _clip(entry.get("status", ""), 220),
                "site_section": _clip(entry.get("site_placement", ""), 220),
                "ux_terms": _coerce_str_list(entry.get("ux_terms", []), max_items=24, max_len=80),
                "visibility": "global",
                "source_kind": "story_design_registry",
                "source_id": _clip(entry.get("file_key", "") or entry.get("id", ""), 128),
                "source_index": "local_story_assets",
                "updated_at": _utc_now_iso(),
            }
        )

    for entry in _load_json_entries("story_flow_map.json"):
        docs.append(
            {
                "id": _safe_doc_id(f"flow:{_clip(entry.get('id', ''), 120)}"),
                "title": _clip(entry.get("title", "") or entry.get("flow", "") or entry.get("journey", ""), 500),
                "content": "\n".join(
                    part
                    for part in [
                        _clip(entry.get("domain", ""), 160),
                        _clip(entry.get("journey", ""), 160),
                        _clip(entry.get("flow", ""), 160),
                        _clip(entry.get("detail", ""), 220),
                        _clip(entry.get("site_placement", ""), 500),
                        _clip(entry.get("routing_note", ""), 800),
                        " ".join(str(item) for item in entry.get("ui_components", [])[:12] if item),
                    ]
                    if part
                )[:16000],
                "url": _clip(entry.get("url", ""), 2000),
                "tag": "Story flow map",
                "domain": _clip(entry.get("domain", ""), 160),
                "journey": _clip(entry.get("journey", ""), 160),
                "flow": _clip(entry.get("flow", ""), 160),
                "detail": _clip(entry.get("detail", ""), 220),
                "site_section": _clip(entry.get("site_placement", ""), 220),
                "ux_terms": _coerce_str_list(entry.get("ux_terms", []) or entry.get("ui_components", []), max_items=24, max_len=80),
                "visibility": "global",
                "source_kind": f"story_flow_map:{_clip(entry.get('source_kind', ''), 80)}",
                "source_id": _clip(entry.get("id", ""), 128),
                "source_index": "local_story_assets",
                "updated_at": _utc_now_iso(),
            }
        )

    for entry in _load_json_entries("story_policy_packs.json"):
        docs.append(
            {
                "id": _safe_doc_id(f"policy:{_clip(entry.get('domain', '') or entry.get('id', ''), 120)}"),
                "title": _clip(f"Policy Pack | {entry.get('domain', '')}", 500),
                "content": "\n".join(
                    part
                    for part in [
                        _clip(entry.get("domain", ""), 160),
                        _clip(entry.get("canonical_title_pattern", ""), 500),
                        " ".join(str(item) for item in entry.get("mandatory_sections", [])[:12] if item),
                        " ".join(str(item) for item in entry.get("preferred_lexicon", [])[:12] if item),
                        " ".join(str(item) for item in entry.get("top_journeys", [])[:8] if item),
                        " ".join(str(item) for item in entry.get("top_flows", [])[:8] if item),
                        " ".join(str(item) for item in entry.get("notes", [])[:6] if item),
                    ]
                    if part
                )[:16000],
                "url": "",
                "tag": "Story policy pack",
                "domain": _clip(entry.get("domain", ""), 160),
                "journey": _clip((entry.get("top_journeys", []) or [""])[0], 160),
                "flow": _clip((entry.get("top_flows", []) or [""])[0], 160),
                "detail": _clip(entry.get("acceptance_style", ""), 220),
                "site_section": _clip(entry.get("design_file_title", ""), 220),
                "ux_terms": _coerce_str_list(entry.get("preferred_lexicon", []), max_items=24, max_len=80),
                "visibility": "global",
                "source_kind": "story_policy_pack",
                "source_id": _clip(entry.get("id", "") or entry.get("domain", ""), 128),
                "source_index": "local_story_assets",
                "updated_at": _utc_now_iso(),
            }
        )

    for entry in _load_json_entries("story_domain_profiles.json"):
        docs.append(
            {
                "id": _safe_doc_id(f"profile:{_clip(entry.get('domain', ''), 120)}"),
                "title": _clip(f"Domain Profile | {entry.get('domain', '')}", 500),
                "content": "\n".join(
                    part
                    for part in [
                        _clip(entry.get("domain", ""), 160),
                        " ".join(str(item) for item in entry.get("aliases", [])[:8] if item),
                        " ".join(str(item) for item in entry.get("top_journeys", [])[:8] if item),
                        " ".join(str(item) for item in entry.get("top_flows", [])[:8] if item),
                        " ".join(str(item) for item in entry.get("preferred_lexicon", [])[:12] if item),
                        " ".join(str(item) for item in entry.get("routing_notes", [])[:6] if item),
                        _clip(entry.get("design_file_title", ""), 500),
                    ]
                    if part
                )[:16000],
                "url": _clip(entry.get("design_file_url", ""), 2000),
                "tag": "Story domain profile",
                "domain": _clip(entry.get("domain", ""), 160),
                "journey": _clip((entry.get("top_journeys", []) or [""])[0], 160),
                "flow": _clip((entry.get("top_flows", []) or [""])[0], 160),
                "detail": _clip(entry.get("design_file_title", ""), 220),
                "site_section": _clip(entry.get("design_file_title", ""), 220),
                "ux_terms": _coerce_str_list(entry.get("preferred_lexicon", []), max_items=24, max_len=80),
                "visibility": "global",
                "source_kind": "story_domain_profile",
                "source_id": _clip(entry.get("domain", ""), 128),
                "source_index": "local_story_assets",
                "updated_at": _utc_now_iso(),
            }
        )

    deduped: dict[str, dict] = {}
    for doc in docs:
        doc_id = str(doc.get("id", "") or "").strip()
        if doc_id and doc_id not in deduped:
            deduped[doc_id] = doc
    return list(deduped.values())


def _get_local_seed_documents() -> list[dict]:
    global _LOCAL_SEED_CACHE
    if _LOCAL_SEED_CACHE is None:
        _LOCAL_SEED_CACHE = _build_local_seed_documents()
    return list(_LOCAL_SEED_CACHE)


def _infer_story_context(item: dict) -> dict:
    title = _source_title(item)
    content = _clip(item.get("content", ""), 5000)
    url = _clip(item.get("url", ""), 1500)
    merged = " | ".join(part for part in [title, content[:2200], url] if part)

    design = search_story_design_map(objective=title, context=content[:2200], epic_or_feature=url, top=2)
    dominant = str(design.get("dominant_domain", "") or "").strip()
    profile = select_story_domain_profile(objective=title, context=content[:2200], epic_or_feature=url, dominant_domain=dominant)
    pack = select_story_policy_pack(objective=title, context=content[:2200], epic_or_feature=url, dominant_domain=dominant or str(profile.get("domain", "") or ""))
    flow_map = search_story_flow_map(
        objective=title,
        context=content[:2200],
        epic_or_feature=url,
        dominant_domain=dominant or str(profile.get("domain", "") or str(pack.get("domain", "") or "")),
        top=1,
    )

    primary_flow = (flow_map.get("matches", []) or [{}])[0]
    primary_design = (design.get("matches", []) or [{}])[0]
    resolved_domain = (
        dominant
        or str(profile.get("domain", "") or "")
        or str(pack.get("domain", "") or "")
        or str(primary_flow.get("domain", "") or "")
    )
    journey = (
        str(primary_flow.get("journey", "") or "")
        or str((profile.get("top_journeys", []) or [""])[0] or "")
        or str((pack.get("top_journeys", []) or [""])[0] or "")
    )
    flow = (
        str(primary_flow.get("flow", "") or "")
        or str((profile.get("top_flows", []) or [""])[0] or "")
        or str((pack.get("top_flows", []) or [""])[0] or "")
    )
    detail = str(primary_flow.get("detail", "") or str(primary_design.get("title", "") or ""))
    site_section = (
        str(primary_flow.get("site_placement", "") or "")
        or str(primary_design.get("site_placement", "") or "")
        or str(primary_design.get("domain", "") or "")
    )
    ux_terms = []
    for collection in (
        primary_design.get("ux_terms", []),
        profile.get("preferred_lexicon", []),
        pack.get("preferred_lexicon", []),
        primary_flow.get("ui_components", []),
    ):
        for term in collection or []:
            clipped = _clip(term, 80)
            if clipped and clipped not in ux_terms:
                ux_terms.append(clipped)

    content_hint_parts = [
        title,
        content,
        url,
        resolved_domain,
        journey,
        flow,
        detail,
        site_section,
        " ".join(ux_terms[:12]),
        " ".join(str(item) for item in design.get("notes", [])[:4]),
        " ".join(str(item) for item in flow_map.get("notes", [])[:4]),
    ]
    return {
        "title": title,
        "content": "\n".join(part for part in content_hint_parts if part).strip()[:16000],
        "tag": _clip(item.get("tag", ""), 220),
        "domain": _clip(resolved_domain, 160),
        "journey": _clip(journey, 160),
        "flow": _clip(flow, 160),
        "detail": _clip(detail, 220),
        "site_section": _clip(site_section, 220),
        "ux_terms": ux_terms[:24],
    }


def build_story_knowledge_index_document(item: dict) -> dict:
    inferred = _infer_story_context(item)
    source_id = _clip(item.get("id", ""), 128)
    return {
        "id": _safe_doc_id(source_id),
        "title": inferred["title"],
        "content": inferred["content"],
        "url": _clip(item.get("url", ""), 2000),
        "tag": inferred["tag"],
        "domain": inferred["domain"],
        "journey": inferred["journey"],
        "flow": inferred["flow"],
        "detail": inferred["detail"],
        "site_section": inferred["site_section"],
        "ux_terms": _coerce_str_list(inferred.get("ux_terms", []), max_items=24, max_len=80),
        "visibility": "global",
        "source_kind": "omni_story_knowledge_sync",
        "source_id": source_id,
        "source_index": _clip(OMNI_INDEX, 160),
        "updated_at": _utc_now_iso(),
    }


def build_story_knowledge_asset_index_document(*, asset_id: str, entry: dict, row: dict | None = None) -> dict:
    row = dict(row or {})
    source_item = {
        "id": asset_id,
        "tag": str(entry.get("tag", "") or row.get("Tag", "") or row.get("Filename", "") or ""),
        "content": str(entry.get("content", "") or ""),
        "url": str(entry.get("url", "") or row.get("SourceUrl", "") or ""),
    }
    doc = build_story_knowledge_index_document(source_item)
    doc["id"] = _safe_doc_id(asset_id)
    doc["title"] = _clip(entry.get("title", "") or doc.get("title", ""), 500)
    doc["content"] = _clip(entry.get("content", "") or doc.get("content", ""), 16000)
    doc["tag"] = _clip(entry.get("tag", "") or doc.get("tag", ""), 220)
    doc["url"] = _clip(entry.get("url", "") or doc.get("url", ""), 2000)
    doc["domain"] = _clip(entry.get("domain", "") or doc.get("domain", ""), 160)
    doc["journey"] = _clip(entry.get("journey", "") or doc.get("journey", ""), 160)
    doc["flow"] = _clip(entry.get("flow", "") or doc.get("flow", ""), 160)
    doc["detail"] = _clip(entry.get("detail", "") or doc.get("detail", ""), 220)
    doc["site_section"] = _clip(entry.get("site_section", "") or entry.get("team_scope", "") or doc.get("site_section", ""), 220)
    doc["ux_terms"] = _coerce_str_list(entry.get("ux_terms", []) or doc.get("ux_terms", []), max_items=24, max_len=80)
    doc["visibility"] = _clip(entry.get("visibility", "") or row.get("Visibility", "") or "global", 32)
    doc["source_kind"] = "promoted_story_knowledge_asset"
    doc["source_id"] = _clip(asset_id, 128)
    doc["source_index"] = "UserStoryKnowledgeAssets"
    doc["updated_at"] = _clip(row.get("UpdatedAt", "") or row.get("ImportedAt", "") or _utc_now_iso(), 80)
    return doc


async def _fetch_source_batch(*, top: int, skip: int) -> list[dict]:
    if not _source_index_ready():
        return []
    body = {
        "search": "*",
        "top": max(1, min(int(top or 100), 1000)),
        "skip": max(0, int(skip or 0)),
        "select": "id,content,url,tag",
        "count": False,
    }
    data = await search_request_with_retry(
        url=_source_url("docs/search"),
        headers=await _headers(),
        json_body=body,
        max_retries=3,
        timeout=45,
    )
    if "error" in data:
        raise RuntimeError(f"Azure AI Search source fetch failed: {data['error']}")
    return list(data.get("value", []) or [])


async def _index_documents(docs: list[dict]) -> dict:
    if not docs:
        return {"ok": True, "indexed": 0}
    if not _target_index_ready():
        return {"ok": False, "skipped": "search_not_configured"}
    if STORY_KNOWLEDGE_INDEX == OMNI_INDEX:
        return {"ok": False, "skipped": "target_matches_source_index"}

    total_indexed = 0
    semaphore = asyncio.Semaphore(_INDEX_EMBED_CONCURRENCY)

    async def _embed_doc(doc: dict) -> dict | None:
        async with semaphore:
            embedding = await get_embedding(doc.get("content", "") or doc.get("title", ""))
        if not embedding:
            return None
        return {"@search.action": "mergeOrUpload", **doc, "content_vector": embedding}

    for start in range(0, len(docs), _INDEX_BATCH_SIZE):
        embedded_docs = await asyncio.gather(
            *[_embed_doc(doc) for doc in docs[start : start + _INDEX_BATCH_SIZE]],
            return_exceptions=True,
        )
        enriched = []
        for embedded in embedded_docs:
            if isinstance(embedded, Exception):
                logger.warning("[StoryKnowledgeIndex] embedding generation failed: %s", embedded)
                continue
            if not embedded:
                continue
            enriched.append(embedded)
        if not enriched:
            continue
        data: dict | None = None
        for upload_attempt in range(1, _INDEX_UPLOAD_ATTEMPTS + 1):
            data = await search_request_with_retry(
                url=_target_url("docs/index"),
                headers=await _headers(),
                json_body={"value": enriched},
                max_retries=_INDEX_MAX_RETRIES,
                timeout=60,
            )
            if "error" not in data:
                break
            error_text = str(data.get("error", "") or "")
            if "429" not in error_text or upload_attempt >= _INDEX_UPLOAD_ATTEMPTS:
                raise RuntimeError(f"Azure AI Search index failed: {error_text}")
            wait = min(20.0, _INDEX_BATCH_DELAY_SECONDS * (upload_attempt + 1))
            logger.info(
                "[StoryKnowledgeIndex] batch upload throttled (attempt %s/%s); retry in %.1fs",
                upload_attempt,
                _INDEX_UPLOAD_ATTEMPTS,
                wait,
            )
            await asyncio.sleep(wait)
        total_indexed += len(enriched)
        logger.info("[StoryKnowledgeIndex] indexed %s/%s knowledge docs", total_indexed, len(docs))
        if start + _INDEX_BATCH_SIZE < len(docs):
            await asyncio.sleep(_INDEX_BATCH_DELAY_SECONDS)
    if total_indexed <= 0:
        return {"ok": False, "skipped": "missing_embeddings"}
    return {"ok": True, "indexed": total_indexed}


async def upsert_story_knowledge_index_document(doc: dict) -> dict:
    if not _target_index_ready():
        return {"ok": False, "skipped": "search_not_configured"}
    embedding = await get_embedding(doc.get("content", "") or doc.get("title", ""))
    if not embedding:
        return {"ok": False, "skipped": "missing_embedding"}
    payload = {"value": [{"@search.action": "mergeOrUpload", **doc, "content_vector": embedding}]}
    data = await search_request_with_retry(
        url=_target_url("docs/index"),
        headers=await _headers(),
        json_body=payload,
        max_retries=3,
        timeout=30,
    )
    if "error" in data:
        logger.warning("[StoryKnowledgeIndex] upsert failed for %s: %s", doc.get("id", ""), data["error"])
        return {"ok": False, "error": data["error"], "document_id": str(doc.get("id", "") or "")}
    return {"ok": True, "document_id": str(doc.get("id", "") or "")}


async def delete_story_knowledge_index_document(document_id: str) -> dict:
    if not _target_index_ready():
        return {"ok": False, "skipped": "search_not_configured"}
    payload = {"value": [{"@search.action": "delete", "id": _safe_doc_id(document_id)}]}
    data = await search_request_with_retry(
        url=_target_url("docs/index"),
        headers=await _headers(),
        json_body=payload,
        max_retries=3,
        timeout=30,
    )
    if "error" in data:
        logger.warning("[StoryKnowledgeIndex] delete failed for %s: %s", document_id, data["error"])
        return {"ok": False, "error": data["error"], "document_id": _safe_doc_id(document_id)}
    return {"ok": True, "document_id": _safe_doc_id(document_id), "deleted": True}


async def _save_sync_state(*, scanned: int, indexed: int, mode: str) -> None:
    entity = {
        "PartitionKey": _SYNC_PARTITION,
        "RowKey": _SYNC_ROW,
        "LastSyncAt": _utc_now_iso(),
        "LastScannedCount": int(scanned or 0),
        "LastIndexedCount": int(indexed or 0),
        "Mode": _clip(mode, 40),
        "SourceIndex": _clip(OMNI_INDEX, 160),
        "TargetIndex": _clip(STORY_KNOWLEDGE_INDEX, 160),
    }
    inserted = await table_insert(_SYNC_TABLE, entity)
    if not inserted:
        await table_merge(_SYNC_TABLE, entity)


def _result_to_item(doc: dict) -> dict:
    return {
        "id": str(doc.get("id", "") or ""),
        "title": str(doc.get("title", "") or ""),
        "content": str(doc.get("content", "") or ""),
        "url": str(doc.get("url", "") or ""),
        "tag": str(doc.get("tag", "") or ""),
        "domain": str(doc.get("domain", "") or ""),
        "journey": str(doc.get("journey", "") or ""),
        "flow": str(doc.get("flow", "") or ""),
        "detail": str(doc.get("detail", "") or ""),
        "site_section": str(doc.get("site_section", "") or ""),
        "ux_terms": _coerce_str_list(doc.get("ux_terms", []), max_items=24, max_len=80),
        "score": round(float(doc.get("@search.score", 0.0) or 0.0), 4),
        "origin": "azure_ai_search_story_knowledge",
    }


def _local_seed_to_item(doc: dict, *, score: float) -> dict:
    item = _result_to_item({**doc, "@search.score": score})
    item["origin"] = "local_story_knowledge_seed"
    return item


def _search_local_seed_documents(*, query_text: str, dominant_domain: str = "", top: int = 3) -> dict:
    effective_query = str(query_text or "").strip()
    if not effective_query:
        return {"items": [], "total_results": 0, "source": "local_story_knowledge_seed"}

    query_normalized = _normalize_text(effective_query)
    query_tokens = _tokenize(effective_query)
    dominant_normalized = _normalize_text(dominant_domain)
    ranked: list[dict] = []

    for doc in _get_local_seed_documents():
        haystack = " ".join(
            str(part)
            for part in [
                doc.get("title", ""),
                doc.get("content", ""),
                doc.get("domain", ""),
                doc.get("journey", ""),
                doc.get("flow", ""),
                doc.get("detail", ""),
                doc.get("site_section", ""),
                " ".join(str(item) for item in doc.get("ux_terms", [])[:12] if item),
            ]
            if part
        )
        haystack_normalized = _normalize_text(haystack)
        haystack_tokens = _tokenize(haystack)
        overlap = (len(query_tokens & haystack_tokens) / max(1, len(query_tokens))) if query_tokens else 0.0
        phrase_bonus = 0.2 if query_normalized and query_normalized in haystack_normalized else 0.0
        domain_bonus = 0.0
        if dominant_normalized and _normalize_text(doc.get("domain", "")) == dominant_normalized:
            domain_bonus = 0.35
        elif dominant_normalized and dominant_normalized in haystack_normalized:
            domain_bonus = 0.15
        score = round(overlap + phrase_bonus + domain_bonus, 4)
        if score <= 0:
            continue
        ranked.append(_local_seed_to_item(doc, score=score))

    ranked.sort(
        key=lambda item: (
            float(item.get("score", 0.0) or 0.0),
            _normalize_text(item.get("title", "")),
        ),
        reverse=True,
    )
    limit = max(1, int(top or 3))
    return {
        "items": ranked[:limit],
        "total_results": len(ranked),
        "source": "local_story_knowledge_seed",
    }


async def search_story_knowledge_index(
    *,
    query_text: str,
    dominant_domain: str = "",
    team_scope: str = "",
    top: int = 3,
) -> dict:
    if not _target_index_ready():
        return _search_local_seed_documents(query_text=query_text, dominant_domain=dominant_domain, top=top)
    effective_query = str(query_text or "").strip()
    if not effective_query:
        return {"items": [], "total_results": 0, "source": "empty_query"}

    filters = ["visibility eq 'global'"]
    safe_domain = _clip(dominant_domain, 160)
    if safe_domain:
        escaped_domain = safe_domain.replace("'", "''")
        filters.append(f"(domain eq '{escaped_domain}' or journey eq '{escaped_domain}' or flow eq '{escaped_domain}')")

    body: dict[str, Any] = {
        "search": effective_query[:1200],
        "top": max(1, int(top or 3)),
        "count": True,
        "select": ",".join(
            [
                "id",
                "title",
                "content",
                "url",
                "tag",
                "domain",
                "journey",
                "flow",
                "detail",
                "site_section",
                "ux_terms",
            ]
        ),
        "filter": " and ".join(filters),
    }
    embedding = await get_embedding(effective_query)
    if embedding:
        body["vectorQueries"] = [
            {
                "kind": "vector",
                "vector": embedding,
                "fields": "content_vector",
                "k": max(6, int(top or 3) * 2),
            }
        ]
    data = await search_request_with_retry(
        url=_target_url("docs/search"),
        headers=await _headers(),
        json_body=body,
        max_retries=3,
        timeout=30,
    )
    if "error" in data:
        logger.warning("[StoryKnowledgeIndex] search failed: %s", data["error"])
        fallback = _search_local_seed_documents(query_text=effective_query, dominant_domain=dominant_domain, top=top)
        fallback["error"] = data["error"]
        return fallback

    dominant = safe_domain.lower()
    ranked: list[dict] = []
    for doc in data.get("value", []) or []:
        item = _result_to_item(doc)
        score = float(item.get("score", 0.0) or 0.0)
        if dominant and str(doc.get("domain", "") or "").strip().lower() == dominant:
            score += 0.2
        item["score"] = round(score, 4)
        ranked.append(item)
    ranked.sort(key=lambda item: float(item.get("score", 0.0) or 0.0), reverse=True)
    if not ranked:
        return _search_local_seed_documents(query_text=effective_query, dominant_domain=dominant_domain, top=top)
    return {
        "items": ranked[: max(1, int(top or 3))],
        "total_results": int(data.get("@odata.count", 0) or 0),
        "source": "azure_ai_search_story_knowledge",
    }


async def sync_story_knowledge_index(*, max_docs: int = 1500, batch_size: int = 150, update_state: bool = True) -> dict:
    if STORY_KNOWLEDGE_INDEX == OMNI_INDEX:
        return {"ok": False, "skipped": "target_matches_source_index", "source_index": OMNI_INDEX, "target_index": STORY_KNOWLEDGE_INDEX}

    scanned = 0
    indexed = 0
    skip = 0
    safe_batch = max(1, min(int(batch_size or 150), 500))
    safe_max_docs = max(1, min(int(max_docs or 1500), 10000))
    mode = "local_seed_only"
    local_docs = _build_local_seed_documents()
    local_seeded = len(local_docs)
    if local_docs:
        local_result = await _index_documents(local_docs)
        indexed += int(local_result.get("indexed", 0) or 0)
        scanned += len(local_docs)

    source_missing = False
    remote_scanned = 0
    remote_indexed = 0
    while remote_scanned < safe_max_docs:
        try:
            batch = await _fetch_source_batch(top=min(safe_batch, safe_max_docs - remote_scanned), skip=skip)
        except RuntimeError as exc:
            if "404" in str(exc):
                source_missing = True
                logger.info("[StoryKnowledgeIndex] source index %s not available; proceeding with local seeds only", OMNI_INDEX)
                break
            raise
        if not batch:
            break
        docs = [build_story_knowledge_index_document(item) for item in batch if str(item.get("id", "") or "").strip()]
        index_result = await _index_documents(docs)
        indexed += int(index_result.get("indexed", 0) or 0)
        remote_indexed += int(index_result.get("indexed", 0) or 0)
        scanned += len(batch)
        remote_scanned += len(batch)
        skip += len(batch)
        mode = "hybrid_scan"
        if len(batch) < safe_batch:
            break

    if update_state:
        await _save_sync_state(scanned=scanned, indexed=indexed, mode=mode)
    return {
        "source_index": OMNI_INDEX,
        "target_index": STORY_KNOWLEDGE_INDEX,
        "scanned": scanned,
        "indexed": indexed,
        "local_seeded": local_seeded,
        "remote_scanned": remote_scanned,
        "remote_indexed": remote_indexed,
        "source_missing": source_missing,
        "batch_size": safe_batch,
        "updated_state": bool(update_state),
        "mode": mode,
    }
