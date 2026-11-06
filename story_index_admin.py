"""Admin helpers for story lane search indexes and sync state."""

from __future__ import annotations

from typing import Any

from config import API_VERSION_SEARCH, SEARCH_KEY, SEARCH_SERVICE, STORY_DEVOPS_INDEX, STORY_EXAMPLES_INDEX, STORY_KNOWLEDGE_INDEX
from http_helpers import search_request_with_retry
from storage import StorageOperationError, table_query
from utils import odata_escape

_SYNC_TABLE = "IndexSyncState"


def _headers() -> dict[str, str]:
    return {"api-key": SEARCH_KEY, "Content-Type": "application/json"}


def _index_url(index_name: str) -> str:
    return f"https://{SEARCH_SERVICE}.search.windows.net/indexes/{index_name}/docs/search?api-version={API_VERSION_SEARCH}"


async def _search_index_count(index_name: str) -> dict[str, Any]:
    if not SEARCH_SERVICE or not SEARCH_KEY or not index_name:
        return {"available": False, "document_count": 0, "error": "search_not_configured"}
    data = await search_request_with_retry(
        url=_index_url(index_name),
        headers=_headers(),
        json_body={"search": "*", "top": 0, "count": True},
        max_retries=2,
        timeout=20,
    )
    if "error" in data:
        return {"available": False, "document_count": 0, "error": data["error"]}
    return {"available": True, "document_count": int(data.get("@odata.count", 0) or 0)}


async def _load_sync_row(partition_key: str, row_key: str) -> dict:
    try:
        rows = await table_query(
            _SYNC_TABLE,
            f"PartitionKey eq '{odata_escape(partition_key)}' and RowKey eq '{odata_escape(row_key)}'",
            top=1,
        )
        return rows[0] if rows else {}
    except StorageOperationError:
        return {}


async def get_story_lane_index_status() -> dict:
    definitions = [
        {
            "key": "examples",
            "label": "Curated Examples",
            "index_name": STORY_EXAMPLES_INDEX,
            "sync_partition": "",
            "sync_row": "",
            "notes": "Memória promovida e exemplos aprovados da lane.",
        },
        {
            "key": "devops",
            "label": "Backlog DevOps",
            "index_name": STORY_DEVOPS_INDEX,
            "sync_partition": "story_devops_index",
            "sync_row": "cursor",
            "notes": "Epic, Feature e User Story indexados para placement e similaridade.",
        },
        {
            "key": "knowledge",
            "label": "Product Knowledge",
            "index_name": STORY_KNOWLEDGE_INDEX,
            "sync_partition": "story_knowledge_index",
            "sync_row": "latest",
            "notes": "Fluxos, policy packs, domain profiles e knowledge de produto para grounding.",
        },
    ]

    items: list[dict[str, Any]] = []
    for definition in definitions:
        count_info = await _search_index_count(definition["index_name"])
        sync_row = {}
        if definition["sync_partition"] and definition["sync_row"]:
            sync_row = await _load_sync_row(definition["sync_partition"], definition["sync_row"])
        item = {
            "key": definition["key"],
            "label": definition["label"],
            "index_name": definition["index_name"],
            "available": bool(count_info.get("available")),
            "document_count": int(count_info.get("document_count", 0) or 0),
            "error": str(count_info.get("error", "") or ""),
            "last_sync_at": str(sync_row.get("LastSyncAt", "") or ""),
            "last_indexed_count": int(sync_row.get("LastIndexedCount", 0) or 0),
            "last_scanned_count": int(sync_row.get("LastScannedCount", 0) or sync_row.get("LastSyncedCount", 0) or 0),
            "mode": str(sync_row.get("Mode", "") or ""),
            "notes": definition["notes"],
        }
        items.append(item)

    return {
        "search_service": SEARCH_SERVICE,
        "indexes": items,
    }
