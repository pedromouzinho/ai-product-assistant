"""Dedicated Azure AI Search sync/search helpers for user story backlog grounding."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from config import (
    API_VERSION_SEARCH,
    DEVOPS_AREAS,
    DEVOPS_ORG,
    DEVOPS_PAT,
    DEVOPS_PROJECT,
    SEARCH_KEY,
    SEARCH_SERVICE,
    STORY_DEVOPS_INDEX,
)
from http_helpers import devops_request_with_retry, search_request_with_retry
from storage import StorageOperationError, table_insert, table_merge, table_query
from tools_knowledge import get_embedding
from utils import odata_escape

logger = logging.getLogger(__name__)

_SYNC_TABLE = "IndexSyncState"
_SYNC_PARTITION = "story_devops_index"
_SYNC_ROW = "cursor"
_WORK_ITEM_FIELDS = [
    "System.Id",
    "System.Title",
    "System.WorkItemType",
    "System.State",
    "System.AreaPath",
    "System.IterationPath",
    "System.Tags",
    "System.Description",
    "Microsoft.VSTS.Common.AcceptanceCriteria",
    "System.Parent",
    "System.AssignedTo",
    "System.CreatedBy",
    "System.CreatedDate",
    "System.ChangedDate",
]
_SUPPORTED_TYPES = ("Epic", "Feature", "User Story")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clip(value: Any, max_len: int) -> str:
    return str(value or "").strip()[:max_len]


def _index_ready() -> bool:
    return bool(str(SEARCH_SERVICE or "").strip() and str(SEARCH_KEY or "").strip() and str(STORY_DEVOPS_INDEX or "").strip())


def _search_headers() -> dict[str, str]:
    return {"api-key": SEARCH_KEY, "Content-Type": "application/json"}


def _search_url(suffix: str) -> str:
    return (
        f"https://{SEARCH_SERVICE}.search.windows.net/indexes/"
        f"{STORY_DEVOPS_INDEX}/{suffix}?api-version={API_VERSION_SEARCH}"
    )


def _devops_headers() -> dict[str, str]:
    import base64

    token = base64.b64encode(f":{DEVOPS_PAT}".encode()).decode()
    return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}


def _devops_url(path: str) -> str:
    return f"https://dev.azure.com/{DEVOPS_ORG}/{DEVOPS_PROJECT}/_apis/{path}"


def _normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _html_to_text(value: str, max_len: int = 12000) -> str:
    text = str(value or "")
    if not text:
        return ""
    text = text.replace("&nbsp;", " ")
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\\1>", " ", text)
    text = re.sub(r"(?i)<br\\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(div|p|section|article|li|ul|ol|h[1-6]|table|tr)>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len]


def _coerce_tags(raw_tags: str) -> list[str]:
    if not isinstance(raw_tags, str):
        return []
    return [tag.strip() for tag in raw_tags.split(";") if tag.strip()][:40]


def _title_segments(title: str) -> dict[str, str]:
    parts = [part.strip() for part in str(title or "").split("|") if part.strip()]
    return {
        "domain": parts[1] if len(parts) >= 2 else "",
        "journey": parts[2] if len(parts) >= 3 else "",
        "flow": parts[3] if len(parts) >= 4 else "",
        "detail": parts[4] if len(parts) >= 5 else "",
    }


def _safe_display_name(value: Any) -> str:
    if isinstance(value, dict):
        return _clip(value.get("displayName", "") or value.get("uniqueName", ""), 200)
    return _clip(value, 200)


def _coerce_numeric_id(value: Any) -> Any:
    text = str(value or "").strip()
    if text.isdigit():
        try:
            return int(text)
        except Exception:
            return text
    return text


def _item_to_result(doc: dict) -> dict:
    return {
        "id": _coerce_numeric_id(doc.get("id", "")),
        "title": str(doc.get("title", "") or ""),
        "content": str(doc.get("content", "") or ""),
        "url": str(doc.get("url", "") or ""),
        "type": str(doc.get("work_item_type", "") or ""),
        "state": str(doc.get("state", "") or ""),
        "area": str(doc.get("area_path", "") or ""),
        "parent_id": _coerce_numeric_id(doc.get("parent_id", "")),
        "parent_title": str(doc.get("parent_title", "") or ""),
        "parent_type": str(doc.get("parent_type", "") or ""),
        "score": round(float(doc.get("@search.score", 0.0) or doc.get("score", 0.0) or 0.0), 4),
        "origin": "azure_ai_search_story_devops",
    }


async def _load_sync_state() -> dict:
    try:
        rows = await table_query(
            _SYNC_TABLE,
            f"PartitionKey eq '{odata_escape(_SYNC_PARTITION)}' and RowKey eq '{odata_escape(_SYNC_ROW)}'",
            top=1,
        )
        return rows[0] if rows else {}
    except StorageOperationError as exc:
        if "TableNotFound" in str(exc):
            return {}
        raise


async def _save_sync_state(*, last_changed_at: str, synced_count: int, mode: str) -> None:
    entity = {
        "PartitionKey": _SYNC_PARTITION,
        "RowKey": _SYNC_ROW,
        "LastChangedAt": _clip(last_changed_at, 80),
        "LastSyncAt": _utc_now_iso(),
        "LastSyncedCount": int(synced_count or 0),
        "Mode": _clip(mode, 40),
    }
    inserted = await table_insert(_SYNC_TABLE, entity)
    if not inserted:
        await table_merge(_SYNC_TABLE, entity)


async def _query_changed_workitem_ids(*, since_iso: str, top: int = 1200, area_paths: list[str] | None = None) -> list[int]:
    safe_since = _clip(since_iso, 80)
    wiql_since = safe_since.split("T", 1)[0].split(" ", 1)[0] if safe_since else safe_since
    safe_project = str(DEVOPS_PROJECT or "").replace("'", "''")
    conds = [
        f"[System.TeamProject] = '{safe_project}'",
        "(" + " OR ".join(f"[System.WorkItemType] = '{item}'" for item in _SUPPORTED_TYPES) + ")",
        f"[System.ChangedDate] >= '{wiql_since}'",
    ]
    valid_areas = [area for area in (area_paths or DEVOPS_AREAS) if str(area or "").strip()]
    if valid_areas:
        area_parts = []
        for area in valid_areas[:20]:
            safe_area = str(area or "").replace("'", "''")
            area_parts.append(f"[System.AreaPath] UNDER '{safe_area}'")
        area_clause = " OR ".join(area_parts)
        conds.append(f"({area_clause})")

    wiql = (
        "SELECT [System.Id] FROM WorkItems "
        f"WHERE {' AND '.join(conds)} "
        "ORDER BY [System.ChangedDate] ASC"
    )
    async with httpx.AsyncClient(timeout=90) as client:
        response = await devops_request_with_retry(
            "POST",
            _devops_url("wit/wiql?api-version=7.1"),
            _devops_headers(),
            {"query": wiql},
            timeout=90,
            max_retries=4,
            client=client,
        )
    if "error" in response:
        raise RuntimeError(f"DevOps WIQL failed: {response['error']}")
    ids = [int(item.get("id")) for item in response.get("workItems", []) if item.get("id")]
    return ids[: max(1, int(top or 1200))]


async def _fetch_workitems(ids: list[int]) -> list[dict]:
    if not ids:
        return []
    headers = _devops_headers()
    items: list[dict] = []
    async with httpx.AsyncClient(timeout=90) as client:
        for start in range(0, len(ids), 100):
            batch = ids[start : start + 100]
            response = await devops_request_with_retry(
                "POST",
                _devops_url("wit/workitemsbatch?api-version=7.1"),
                headers,
                {"ids": batch, "fields": _WORK_ITEM_FIELDS},
                timeout=90,
                max_retries=4,
                client=client,
            )
            if "error" in response:
                raise RuntimeError(f"DevOps workitemsbatch failed: {response['error']}")
            items.extend(response.get("value", []) or [])
    return items


async def _fetch_parent_lookup(parent_ids: list[int], *, max_depth: int = 2) -> dict[str, dict]:
    if not parent_ids:
        return {}
    lookup: dict[str, dict] = {}
    pending = sorted(set(int(parent_id) for parent_id in parent_ids if str(parent_id).isdigit()))
    depth = 0
    while pending and depth < max(1, int(max_depth or 1)):
        try:
            items = await _fetch_workitems(pending)
        except RuntimeError as exc:
            logger.warning("[StoryDevOpsIndex] parent batch fetch failed, falling back to best-effort GET: %s", exc)
            items = []
            headers = _devops_headers()
            async with httpx.AsyncClient(timeout=60) as client:
                for parent_id in pending:
                    response = await devops_request_with_retry(
                        "GET",
                        _devops_url(
                            "wit/workitems/"
                            f"{int(parent_id)}?fields={','.join(_WORK_ITEM_FIELDS)}&api-version=7.1"
                        ),
                        headers,
                        timeout=60,
                        max_retries=3,
                        client=client,
                    )
                    if "error" in response:
                        logger.info("[StoryDevOpsIndex] skipping parent %s: %s", parent_id, response["error"])
                        continue
                    if response.get("id"):
                        items.append(response)
        next_pending: list[int] = []
        for item in items:
            fields = item.get("fields", {}) if isinstance(item.get("fields"), dict) else {}
            item_id = str(item.get("id", "") or "")
            raw_parent = str(fields.get("System.Parent", "") or "").strip()
            lookup[item_id] = {
                "id": item_id,
                "title": _clip(fields.get("System.Title", ""), 500),
                "type": _clip(fields.get("System.WorkItemType", ""), 80),
                "parent_id": _clip(raw_parent, 80),
            }
            if raw_parent.isdigit() and raw_parent not in lookup:
                next_pending.append(int(raw_parent))
        pending = sorted(set(next_pending))
        depth += 1
    return lookup


def build_story_devops_index_document(item: dict, *, parent_lookup: dict[str, dict] | None = None) -> dict:
    fields = item.get("fields", {}) if isinstance(item.get("fields"), dict) else {}
    title = _clip(fields.get("System.Title", ""), 500)
    segments = _title_segments(title)
    description = _html_to_text(fields.get("System.Description", ""), max_len=8000)
    acceptance = _html_to_text(fields.get("Microsoft.VSTS.Common.AcceptanceCriteria", ""), max_len=12000)
    raw_parent = fields.get("System.Parent", "")
    parent_id = str(raw_parent or "").strip()
    parent_info = (parent_lookup or {}).get(parent_id, {}) if parent_id else {}
    parent_parent_id = str(parent_info.get("parent_id", "") or "").strip()
    parent_parent_info = (parent_lookup or {}).get(parent_parent_id, {}) if parent_parent_id else {}
    tags = _coerce_tags(str(fields.get("System.Tags", "") or ""))
    work_item_type = _clip(fields.get("System.WorkItemType", ""), 80)
    feature_title = ""
    epic_title = ""
    if work_item_type == "Feature":
        epic_title = _clip(parent_info.get("title", ""), 500) if _clip(parent_info.get("type", ""), 80) == "Epic" else ""
    elif work_item_type == "User Story":
        if _clip(parent_info.get("type", ""), 80) == "Feature":
            feature_title = _clip(parent_info.get("title", ""), 500)
            if _clip(parent_parent_info.get("type", ""), 80) == "Epic":
                epic_title = _clip(parent_parent_info.get("title", ""), 500)
        elif _clip(parent_info.get("type", ""), 80) == "Epic":
            epic_title = _clip(parent_info.get("title", ""), 500)
    elif work_item_type == "Epic":
        epic_title = title

    hierarchy_parts = [part for part in [epic_title, feature_title, title] if part]
    content_parts = [
        title,
        description,
        acceptance,
        " ".join(tags),
        _clip(fields.get("System.AreaPath", ""), 500),
        _clip(fields.get("System.IterationPath", ""), 500),
        work_item_type,
        _clip(fields.get("System.State", ""), 80),
        _clip(parent_info.get("title", ""), 500),
        " > ".join(hierarchy_parts),
        f"Epic: {epic_title}" if epic_title else "",
        f"Feature: {feature_title}" if feature_title else "",
    ]
    content = "\n".join(part for part in content_parts if part).strip()[:16000]
    return {
        "id": str(item.get("id", "") or ""),
        "title": title,
        "content": content,
        "description_text": description,
        "acceptance_text": acceptance,
        "work_item_type": _clip(fields.get("System.WorkItemType", ""), 80),
        "state": _clip(fields.get("System.State", ""), 80),
        "area_path": _clip(fields.get("System.AreaPath", ""), 500),
        "iteration_path": _clip(fields.get("System.IterationPath", ""), 500),
        "tags": tags,
        "parent_id": _clip(parent_id, 80),
        "parent_title": _clip(parent_info.get("title", ""), 500),
        "parent_type": _clip(parent_info.get("type", ""), 80),
        "assigned_to": _safe_display_name(fields.get("System.AssignedTo", "")),
        "created_by": _safe_display_name(fields.get("System.CreatedBy", "")),
        "created_date": _clip(fields.get("System.CreatedDate", ""), 80),
        "changed_date": _clip(fields.get("System.ChangedDate", ""), 80),
        "url": f"https://dev.azure.com/{DEVOPS_ORG}/{DEVOPS_PROJECT}/_workitems/edit/{item.get('id')}",
        "domain": _clip(segments.get("domain", ""), 160),
        "journey": _clip(segments.get("journey", ""), 160),
        "flow": _clip(segments.get("flow", ""), 160),
        "detail": _clip(segments.get("detail", ""), 160),
        "visibility": "global",
        "source_kind": "devops_backlog_sync",
    }


async def _index_documents(docs: list[dict]) -> dict:
    if not docs:
        return {"ok": True, "indexed": 0}
    if not _index_ready():
        return {"ok": False, "skipped": "search_not_configured"}
    total_indexed = 0
    semaphore = asyncio.Semaphore(6)

    async def _embed_doc(doc: dict) -> dict | None:
        async with semaphore:
            embedding = await get_embedding(doc.get("content", "") or doc.get("title", ""))
        if not embedding:
            return None
        return {"@search.action": "mergeOrUpload", **doc, "content_vector": embedding}

    for start in range(0, len(docs), 100):
        embedded_docs = await asyncio.gather(*[_embed_doc(doc) for doc in docs[start : start + 100]], return_exceptions=True)
        enriched = []
        for embedded in embedded_docs:
            if isinstance(embedded, Exception):
                logger.warning("[StoryDevOpsIndex] embedding generation failed: %s", embedded)
                continue
            if not embedded:
                continue
            enriched.append(embedded)
        if not enriched:
            continue
        payload = {"value": enriched}
        data = await search_request_with_retry(
            url=_search_url("docs/index"),
            headers=_search_headers(),
            json_body=payload,
            max_retries=3,
            timeout=60,
        )
        if "error" in data:
            raise RuntimeError(f"Azure AI Search index failed: {data['error']}")
        total_indexed += len(payload["value"])
        logger.info("[StoryDevOpsIndex] indexed %s/%s backlog docs", total_indexed, len(docs))
    if total_indexed <= 0:
        return {"ok": False, "skipped": "missing_embeddings"}
    return {"ok": True, "indexed": total_indexed}


async def search_story_devops_index(
    *,
    query_text: str,
    team_scope: str = "",
    dominant_domain: str = "",
    work_item_types: list[str] | None = None,
    top: int = 8,
) -> dict:
    if not _index_ready():
        return {"items": [], "total_results": 0, "source": "disabled"}
    effective_query = str(query_text or "").strip()
    if not effective_query:
        return {"items": [], "total_results": 0, "source": "empty_query"}

    filters = ["visibility eq 'global'"]
    safe_team = _clip(team_scope, 500)
    if safe_team:
        escaped = safe_team.replace("'", "''")
        filters.append(f"search.ismatch('{escaped}', 'area_path')")
    allowed_types = [str(item or "").strip() for item in (work_item_types or []) if str(item or "").strip()]
    if allowed_types:
        type_parts = []
        for item in allowed_types[:6]:
            safe_item = str(item or "").replace("'", "''")
            type_parts.append(f"work_item_type eq '{safe_item}'")
        type_clause = " or ".join(type_parts)
        filters.append(f"({type_clause})")

    body: dict[str, Any] = {
        "search": effective_query[:1200],
        "top": max(1, int(top or 8)),
        "count": True,
        "select": ",".join(
            [
                "id",
                "title",
                "content",
                "url",
                "work_item_type",
                "state",
                "area_path",
                "parent_id",
                "parent_title",
                "parent_type",
                "domain",
                "journey",
                "flow",
                "detail",
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
                "k": max(8, int(top or 8) * 2),
            }
        ]
    data = await search_request_with_retry(
        url=_search_url("docs/search"),
        headers=_search_headers(),
        json_body=body,
        max_retries=3,
        timeout=30,
    )
    if "error" in data:
        logger.warning("[StoryDevOpsIndex] search failed: %s", data["error"])
        return {"items": [], "total_results": 0, "source": "error", "error": data["error"]}

    dominant = str(dominant_domain or "").strip().lower()
    ranked: list[dict] = []
    for doc in data.get("value", []) or []:
        item = _item_to_result(doc)
        score = float(doc.get("@search.score", 0.0) or 0.0)
        if dominant and str(doc.get("domain", "") or "").strip().lower() == dominant:
            score += 0.18
        item["score"] = round(score, 4)
        ranked.append(item)
    ranked.sort(key=lambda item: float(item.get("score", 0.0) or 0.0), reverse=True)
    return {
        "items": ranked[: max(1, int(top or 8))],
        "total_results": int(data.get("@odata.count", 0) or 0),
        "source": "azure_ai_search_story_devops",
    }


async def sync_story_devops_index(
    *,
    since_iso: str = "",
    since_days: int = 30,
    top: int = 1200,
    area_paths: list[str] | None = None,
    update_cursor: bool = True,
) -> dict:
    effective_since = str(since_iso or "").strip()
    mode = "manual"
    if not effective_since:
        state = await _load_sync_state()
        effective_since = _clip(state.get("LastChangedAt", ""), 80)
        mode = "cursor" if effective_since else "lookback"
    if not effective_since:
        effective_since = (datetime.now(timezone.utc) - timedelta(days=max(1, int(since_days or 30)))).replace(microsecond=0).isoformat()

    ids = await _query_changed_workitem_ids(
        since_iso=effective_since,
        top=max(1, int(top or 1200)),
        area_paths=area_paths,
    )
    if not ids:
        return {
            "since_iso": effective_since,
            "mode": mode,
            "matched_ids": 0,
            "indexed": 0,
            "latest_changed_at": effective_since,
        }

    items = await _fetch_workitems(ids)
    parent_ids = []
    for item in items:
        fields = item.get("fields", {}) if isinstance(item.get("fields"), dict) else {}
        raw_parent = fields.get("System.Parent")
        if raw_parent:
            try:
                parent_ids.append(int(raw_parent))
            except Exception:
                continue
    parent_lookup = await _fetch_parent_lookup(parent_ids)
    docs = [build_story_devops_index_document(item, parent_lookup=parent_lookup) for item in items]
    index_result = await _index_documents(docs)

    latest_changed_at = effective_since
    for item in items:
        fields = item.get("fields", {}) if isinstance(item.get("fields"), dict) else {}
        changed_at = _clip(fields.get("System.ChangedDate", ""), 80)
        if changed_at and changed_at > latest_changed_at:
            latest_changed_at = changed_at

    if update_cursor and index_result.get("ok"):
        await _save_sync_state(last_changed_at=latest_changed_at, synced_count=int(index_result.get("indexed", 0) or 0), mode=mode)

    return {
        "since_iso": effective_since,
        "mode": mode,
        "matched_ids": len(ids),
        "fetched_items": len(items),
        "indexed": int(index_result.get("indexed", 0) or 0),
        "latest_changed_at": latest_changed_at,
        "areas": list(area_paths or DEVOPS_AREAS),
    }
