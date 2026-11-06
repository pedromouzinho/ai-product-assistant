"""Persistent story knowledge assets promoted from uploads or pasted text."""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from config import CHAT_TOOLRESULT_BLOB_CONTAINER
from storage import (
    blob_download_bytes,
    blob_download_json,
    blob_upload_json,
    parse_blob_ref,
    table_insert,
    table_merge,
    table_query,
)
from story_knowledge_index import (
    build_story_knowledge_asset_index_document,
    delete_story_knowledge_index_document,
    upsert_story_knowledge_index_document,
)
from utils import odata_escape

logger = logging.getLogger(__name__)

_ASSETS_TABLE = "UserStoryKnowledgeAssets"
_ACTIVE = "active"
_INACTIVE = "inactive"
_DELETED = "deleted"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clip(value: Any, max_len: int = 500) -> str:
    return str(value or "").strip()[:max_len]


def _asset_id_from_upload(conversation_id: str, file_id: str) -> str:
    safe_conv = _clip(conversation_id, 48).replace("/", "_")
    safe_file = _clip(file_id, 48).replace("/", "_")
    return f"upload-{safe_conv}-{safe_file}"


def _slug(value: Any, max_len: int = 64) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower())
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text[:max_len] or "item"


def _asset_id_from_manual_key(asset_key: str, *, title: str = "", domain: str = "", journey: str = "", flow: str = "") -> str:
    seed = str(asset_key or "").strip()
    if not seed:
        seed = "|".join(part for part in [domain, journey, flow, title] if str(part or "").strip())
    seed = seed or uuid.uuid4().hex
    return f"bundle-{_slug(seed, 80)}"


def _asset_preview(row: dict) -> dict:
    return {
        "asset_id": str(row.get("RowKey", "") or ""),
        "title": str(row.get("Title", "") or ""),
        "status": str(row.get("Status", "") or ""),
        "domain": str(row.get("Domain", "") or ""),
        "journey": str(row.get("Journey", "") or ""),
        "flow": str(row.get("Flow", "") or ""),
        "source_type": str(row.get("SourceType", "") or ""),
        "filename": str(row.get("Filename", "") or ""),
        "conversation_id": str(row.get("ConversationId", "") or ""),
        "file_id": str(row.get("UploadRowKey", "") or ""),
        "search_document_id": str(row.get("SearchDocumentId", "") or ""),
        "updated_at": str(row.get("UpdatedAt", "") or ""),
    }


async def _load_asset_row(asset_id: str) -> dict:
    rows = await table_query(
        _ASSETS_TABLE,
        f"PartitionKey eq 'global' and RowKey eq '{odata_escape(str(asset_id or '').strip())}'",
        top=1,
    )
    return rows[0] if rows else {}


async def _load_asset_entry(row: dict) -> dict:
    blob_ref = str((row or {}).get("EntryBlobRef", "") or "").strip()
    if not blob_ref or "/" not in blob_ref:
        return {}
    try:
        container, blob_name = blob_ref.split("/", 1)
        payload = await blob_download_json(container, blob_name)
    except Exception as exc:
        logger.warning("[StoryKnowledgeAssets] asset load failed: %s", exc)
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload.get("entry", {}) if isinstance(payload.get("entry"), dict) else {}


async def _load_upload_index_row(conversation_id: str, file_id: str) -> dict:
    rows = await table_query(
        "UploadIndex",
        f"PartitionKey eq '{odata_escape(str(conversation_id or '').strip())}' and RowKey eq '{odata_escape(str(file_id or '').strip())}'",
        top=1,
    )
    return rows[0] if rows else {}


async def _extract_upload_text(row: dict) -> str:
    extracted_blob_ref = str(row.get("ExtractedBlobRef", "") or "").strip()
    if extracted_blob_ref:
        container, blob_name = parse_blob_ref(extracted_blob_ref)
        if container and blob_name:
            try:
                payload = await blob_download_bytes(container, blob_name)
                if isinstance(payload, (bytes, bytearray)) and payload:
                    return payload.decode("utf-8", errors="replace")[:50000]
            except Exception as exc:
                logger.warning("[StoryKnowledgeAssets] extracted blob read failed: %s", exc)

    chunks_blob_ref = str(row.get("ChunksBlobRef", "") or "").strip()
    if chunks_blob_ref:
        container, blob_name = parse_blob_ref(chunks_blob_ref)
        if container and blob_name:
            try:
                payload = await blob_download_json(container, blob_name)
                if isinstance(payload, dict):
                    chunks = payload.get("chunks", []) if isinstance(payload.get("chunks"), list) else []
                    lines = []
                    for chunk in chunks[:20]:
                        if isinstance(chunk, dict):
                            text = _clip(chunk.get("text", ""), 2000)
                            if text:
                                lines.append(text)
                    if lines:
                        return "\n".join(lines)[:50000]
            except Exception as exc:
                logger.warning("[StoryKnowledgeAssets] chunks blob read failed: %s", exc)

    return _clip(row.get("PreviewText", ""), 50000)


async def _record_search_state(asset_id: str, *, state: str, error: str = "", document_id: str = "") -> None:
    row = await _load_asset_row(asset_id)
    if not row:
        return
    await table_merge(
        _ASSETS_TABLE,
        {
            "PartitionKey": row["PartitionKey"],
            "RowKey": row["RowKey"],
            "SearchSyncState": _clip(state, 80),
            "SearchSyncError": _clip(error, 500),
            "SearchDocumentId": _clip(document_id or asset_id, 128),
            "SearchSyncAt": _utc_now_iso(),
            "UpdatedAt": _utc_now_iso(),
        },
    )


async def _sync_asset_row_to_search(row: dict) -> dict:
    asset_id = str((row or {}).get("RowKey", "") or "").strip()
    if not asset_id:
        return {"ok": False, "skipped": "missing_asset_id"}
    status = str((row or {}).get("Status", "") or "").strip().lower()
    if status != _ACTIVE:
        result = await delete_story_knowledge_index_document(asset_id)
        await _record_search_state(
            asset_id,
            state="deleted" if result.get("ok") else str(result.get("skipped", "") or "error"),
            error=str(result.get("error", "") or ""),
            document_id=str(result.get("document_id", "") or asset_id),
        )
        return result

    entry = await _load_asset_entry(row)
    if not entry:
        result = {"ok": False, "error": "missing_entry_blob", "document_id": asset_id}
        await _record_search_state(asset_id, state="error", error="missing_entry_blob", document_id=asset_id)
        return result

    doc = build_story_knowledge_asset_index_document(asset_id=asset_id, entry=entry, row=row)
    result = await upsert_story_knowledge_index_document(doc)
    await _record_search_state(
        asset_id,
        state="synced" if result.get("ok") else str(result.get("skipped", "") or "error"),
        error=str(result.get("error", "") or ""),
        document_id=str(result.get("document_id", "") or asset_id),
    )
    return result


async def create_story_knowledge_asset_from_upload(
    *,
    conversation_id: str,
    file_id: str,
    imported_by: str,
    title: str = "",
    domain: str = "",
    journey: str = "",
    flow: str = "",
    team_scope: str = "",
    note: str = "",
) -> dict:
    row = await _load_upload_index_row(conversation_id, file_id)
    if not row:
        raise RuntimeError("Upload não encontrado para promover.")

    content = await _extract_upload_text(row)
    if not str(content or "").strip():
        raise RuntimeError("O upload não tem conteúdo textual suficiente para promover.")

    asset_id = _asset_id_from_upload(conversation_id, file_id)
    entry = {
        "title": _clip(title or row.get("Filename", "") or "Knowledge asset", 500),
        "content": str(content or "").strip()[:50000],
        "tag": _clip(row.get("Filename", "") or "Upload promovido", 220),
        "domain": _clip(domain, 160),
        "journey": _clip(journey, 160),
        "flow": _clip(flow, 160),
        "detail": _clip(note or row.get("Filename", ""), 220),
        "team_scope": _clip(team_scope, 220),
        "site_section": _clip(team_scope, 220),
        "ux_terms": [],
        "visibility": "global",
        "source_type": "upload_index",
        "source_user_sub": _clip(row.get("UserSub", ""), 120),
        "source_filename": _clip(row.get("Filename", ""), 240),
        "source_conversation_id": _clip(conversation_id, 120),
        "source_upload_id": _clip(file_id, 120),
        "imported_by": _clip(imported_by, 120),
        "imported_at": _utc_now_iso(),
        "note": _clip(note, 2000),
    }

    blob = await blob_upload_json(
        CHAT_TOOLRESULT_BLOB_CONTAINER,
        f"user-stories/knowledge-assets/global/{asset_id}.json",
        {"entry": entry, "saved_at": _utc_now_iso()},
    )
    entity = {
        "PartitionKey": "global",
        "RowKey": asset_id,
        "Status": _ACTIVE,
        "Title": _clip(entry.get("title", ""), 250),
        "Domain": _clip(entry.get("domain", ""), 120),
        "Journey": _clip(entry.get("journey", ""), 160),
        "Flow": _clip(entry.get("flow", ""), 160),
        "TeamScope": _clip(entry.get("team_scope", ""), 220),
        "SourceType": "upload_index",
        "ConversationId": _clip(conversation_id, 120),
        "UploadRowKey": _clip(file_id, 120),
        "Filename": _clip(row.get("Filename", ""), 240),
        "ImportedBy": _clip(imported_by, 120),
        "ImportedAt": _utc_now_iso(),
        "ReviewNote": _clip(note, 500),
        "EntryBlobRef": str(blob.get("blob_ref", "") or ""),
        "SearchSyncState": "pending",
        "SearchDocumentId": _clip(asset_id, 128),
        "UpdatedAt": _utc_now_iso(),
    }
    inserted = await table_insert(_ASSETS_TABLE, entity)
    if not inserted:
        await table_merge(_ASSETS_TABLE, entity)

    result = await _sync_asset_row_to_search(entity)
    return {
        "asset_id": asset_id,
        "created": True,
        "entry": _asset_preview({**entity, "SearchDocumentId": result.get("document_id", asset_id)}),
        "search_sync": result,
    }


async def create_story_knowledge_asset_from_text(
    *,
    title: str,
    content: str,
    imported_by: str,
    asset_key: str = "",
    domain: str = "",
    journey: str = "",
    flow: str = "",
    team_scope: str = "",
    note: str = "",
) -> dict:
    asset_id = _asset_id_from_manual_key(
        asset_key,
        title=title,
        domain=domain,
        journey=journey,
        flow=flow,
    ) if str(asset_key or "").strip() else f"text-{uuid.uuid4().hex[:16]}"
    entry = {
        "title": _clip(title or "Knowledge asset", 500),
        "content": str(content or "").strip()[:50000],
        "tag": "Texto promovido",
        "domain": _clip(domain, 160),
        "journey": _clip(journey, 160),
        "flow": _clip(flow, 160),
        "detail": _clip(note or title, 220),
        "team_scope": _clip(team_scope, 220),
        "site_section": _clip(team_scope, 220),
        "ux_terms": [],
        "visibility": "global",
        "source_type": "manual_bundle" if str(asset_key or "").strip() else "manual_text",
        "imported_by": _clip(imported_by, 120),
        "imported_at": _utc_now_iso(),
        "note": _clip(note, 2000),
        "asset_key": _clip(asset_key, 160),
    }
    blob = await blob_upload_json(
        CHAT_TOOLRESULT_BLOB_CONTAINER,
        f"user-stories/knowledge-assets/global/{asset_id}.json",
        {"entry": entry, "saved_at": _utc_now_iso()},
    )
    entity = {
        "PartitionKey": "global",
        "RowKey": asset_id,
        "Status": _ACTIVE,
        "Title": _clip(entry.get("title", ""), 250),
        "Domain": _clip(entry.get("domain", ""), 120),
        "Journey": _clip(entry.get("journey", ""), 160),
        "Flow": _clip(entry.get("flow", ""), 160),
        "TeamScope": _clip(entry.get("team_scope", ""), 220),
        "SourceType": entry.get("source_type", "manual_text"),
        "ConversationId": "",
        "UploadRowKey": "",
        "Filename": "",
        "AssetKey": _clip(asset_key, 160),
        "ImportedBy": _clip(imported_by, 120),
        "ImportedAt": _utc_now_iso(),
        "ReviewNote": _clip(note, 500),
        "EntryBlobRef": str(blob.get("blob_ref", "") or ""),
        "SearchSyncState": "pending",
        "SearchDocumentId": _clip(asset_id, 128),
        "UpdatedAt": _utc_now_iso(),
    }
    inserted = await table_insert(_ASSETS_TABLE, entity)
    if not inserted:
        await table_merge(_ASSETS_TABLE, entity)
    result = await _sync_asset_row_to_search(entity)
    return {
        "asset_id": asset_id,
        "created": True,
        "entry": _asset_preview({**entity, "SearchDocumentId": result.get("document_id", asset_id)}),
        "search_sync": result,
    }


async def create_story_knowledge_assets_from_bundle(
    *,
    entries: list[dict],
    imported_by: str,
) -> dict:
    created: list[dict] = []
    for index, raw in enumerate(entries or [], start=1):
        if not isinstance(raw, dict):
            raise RuntimeError(f"Entrada {index} do bundle é inválida.")
        title = _clip(raw.get("title", ""), 500)
        content = str(raw.get("content", "") or "").strip()
        if not title or not content:
            raise RuntimeError(f"Entrada {index} do bundle exige title e content.")
        result = await create_story_knowledge_asset_from_text(
            title=title,
            content=content,
            imported_by=imported_by,
            asset_key=_clip(raw.get("asset_key", ""), 160) or f"{raw.get('domain', '')}|{raw.get('journey', '')}|{title}",
            domain=_clip(raw.get("domain", ""), 160),
            journey=_clip(raw.get("journey", ""), 160),
            flow=_clip(raw.get("flow", ""), 160),
            team_scope=_clip(raw.get("team_scope", ""), 220),
            note=_clip(raw.get("note", ""), 2000),
        )
        created.append(result.get("entry", {}))
    return {
        "created_count": len(created),
        "items": created,
    }


async def list_story_knowledge_assets(*, top: int = 100) -> dict:
    rows = await table_query(_ASSETS_TABLE, "PartitionKey eq 'global'", top=max(1, min(int(top or 100), 500)))
    rows_sorted = sorted(rows, key=lambda item: str(item.get("UpdatedAt", "") or item.get("ImportedAt", "")), reverse=True)
    return {
        "total": len(rows_sorted),
        "items": [_asset_preview(row) for row in rows_sorted],
    }


async def review_story_knowledge_asset(
    *,
    asset_id: str,
    action: str,
    reviewed_by: str,
    note: str = "",
) -> dict:
    row = await _load_asset_row(asset_id)
    if not row:
        raise RuntimeError("Knowledge asset não encontrado.")
    current_status = str(row.get("Status", "") or "").strip().lower()
    desired_action = str(action or "").strip().lower()

    if desired_action == "deactivate":
        if current_status != _ACTIVE:
            raise RuntimeError("Só assets ativos podem ser desativados.")
        next_status = _INACTIVE
    elif desired_action == "reactivate":
        if current_status != _INACTIVE:
            raise RuntimeError("Só assets inativos podem ser reativados.")
        next_status = _ACTIVE
    elif desired_action == "delete":
        next_status = _DELETED
    else:
        raise RuntimeError("Ação inválida para knowledge asset.")

    merged = {
        "PartitionKey": row["PartitionKey"],
        "RowKey": row["RowKey"],
        "Status": next_status,
        "ReviewedBy": _clip(reviewed_by, 120),
        "ReviewedAt": _utc_now_iso(),
        "ReviewNote": _clip(note, 500),
        "UpdatedAt": _utc_now_iso(),
    }
    await table_merge(_ASSETS_TABLE, merged)

    result = await _sync_asset_row_to_search({**row, **merged})
    return {
        "asset_id": asset_id,
        "status": next_status,
        "entry": _asset_preview({**row, **merged}),
        "search_sync": result,
    }
