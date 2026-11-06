"""User data export/delete helpers for privacy operations."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Iterable

from storage import (
    blob_delete,
    blob_download_json,
    blob_upload_json,
    parse_blob_ref,
    table_delete,
    table_merge,
    table_query,
)
from story_knowledge_assets import review_story_knowledge_asset
from user_story_lane import sync_user_story_examples_search_index
from utils import odata_escape, safe_blob_component

logger = logging.getLogger(__name__)

_DRAFTS_TABLE = "UserStoryDrafts"
_FEEDBACK_TABLE = "UserStoryFeedback"
_CURATED_TABLE = "UserStoryCurated"
_KNOWLEDGE_TABLE = "UserStoryKnowledgeAssets"
_CHAT_TOOLRESULT_BLOB_CONTAINER = "chat-toolresults"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clip(value: Any, max_len: int = 500) -> str:
    return str(value or "").strip()[:max_len]


def _draft_partition_key(user_sub: str) -> str:
    return f"user:{str(user_sub or 'anon').strip() or 'anon'}"


def _json_safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return str(value)


def _row_preview(row: dict, *, keys: Iterable[str] | None = None) -> dict:
    if not isinstance(row, dict):
        return {}
    selected = {}
    if keys:
        for key in keys:
            if key in row:
                selected[key] = _json_safe(row.get(key))
        return selected
    return {str(k): _json_safe(v) for k, v in row.items()}


async def _delete_blob_ref(blob_ref: str, seen: set[str]) -> bool:
    ref = str(blob_ref or "").strip()
    if not ref or ref in seen:
        return False
    container, blob_name = parse_blob_ref(ref)
    if not container or not blob_name:
        return False
    try:
        await blob_delete(container, blob_name)
        seen.add(ref)
        return True
    except Exception as exc:
        logger.warning("[PrivacyService] blob delete failed for %s: %s", ref, exc)
        return False


async def _query_all(table: str, filter_expr: str = "", *, top: int = 1000) -> list[dict]:
    try:
        return await table_query(table, filter_expr, top=max(1, min(int(top or 1000), 2000)))
    except Exception as exc:
        logger.warning("[PrivacyService] query failed for %s: %s", table, exc)
        return []


async def build_user_privacy_export(user_sub: str) -> dict:
    safe_user = str(user_sub or "").strip()
    if not safe_user:
        raise RuntimeError("user_sub é obrigatório para export.")

    user_rows = await _query_all("Users", f"PartitionKey eq 'user' and RowKey eq '{odata_escape(safe_user)}'", top=1)
    chat_rows = await _query_all("ChatHistory", f"PartitionKey eq '{odata_escape(safe_user)}'", top=500)
    feedback_rows = await _query_all("feedback", f"UserSub eq '{odata_escape(safe_user)}'", top=1000)
    example_rows = await _query_all("examples", f"UserSub eq '{odata_escape(safe_user)}'", top=1000)
    upload_index_rows = await _query_all("UploadIndex", f"UserSub eq '{odata_escape(safe_user)}'", top=1000)
    upload_job_rows = await _query_all(
        "UploadJobs",
        f"PartitionKey eq 'upload' and UserSub eq '{odata_escape(safe_user)}'",
        top=1000,
    )
    story_draft_rows = await _query_all(_DRAFTS_TABLE, f"PartitionKey eq '{odata_escape(_draft_partition_key(safe_user))}'", top=1000)
    story_feedback_rows = await _query_all(_FEEDBACK_TABLE, f"PartitionKey eq '{odata_escape(_draft_partition_key(safe_user))}'", top=1000)
    curated_rows = [
        row
        for row in await _query_all(_CURATED_TABLE, "PartitionKey eq 'global'", top=500)
        if str(row.get("SourceUserSub", "") or "").strip() == safe_user
    ]
    knowledge_rows = [
        row
        for row in await _query_all(_KNOWLEDGE_TABLE, "PartitionKey eq 'global'", top=500)
        if str(row.get("ImportedBy", "") or "").strip() == safe_user
        or str(row.get("SourceUserSub", "") or "").strip() == safe_user
    ]

    payload = {
        "generated_at": _utc_now_iso(),
        "user_sub": safe_user,
        "summary": {
            "users": len(user_rows),
            "chat_history": len(chat_rows),
            "feedback_rows": len(feedback_rows),
            "example_rows": len(example_rows),
            "upload_index_rows": len(upload_index_rows),
            "upload_job_rows": len(upload_job_rows),
            "user_story_drafts": len(story_draft_rows),
            "user_story_feedback": len(story_feedback_rows),
            "user_story_curated_refs": len(curated_rows),
            "knowledge_assets": len(knowledge_rows),
        },
        "data": {
            "user_rows": [_row_preview(row) for row in user_rows],
            "chat_history": [_row_preview(row) for row in chat_rows],
            "feedback": [_row_preview(row) for row in feedback_rows],
            "examples": [_row_preview(row) for row in example_rows],
            "upload_index": [_row_preview(row) for row in upload_index_rows],
            "upload_jobs": [_row_preview(row) for row in upload_job_rows],
            "user_story_drafts": [_row_preview(row) for row in story_draft_rows],
            "user_story_feedback": [_row_preview(row) for row in story_feedback_rows],
            "user_story_curated_refs": [_row_preview(row) for row in curated_rows],
            "knowledge_assets": [_row_preview(row) for row in knowledge_rows],
        },
    }
    return payload


async def delete_user_personal_data(
    user_sub: str,
    *,
    delete_account: bool = False,
) -> dict:
    safe_user = str(user_sub or "").strip()
    if not safe_user:
        raise RuntimeError("user_sub é obrigatório para delete.")

    summary = {
        "user_sub": safe_user,
        "deleted_rows": 0,
        "anonymized_rows": 0,
        "deleted_blobs": 0,
        "search_updates": 0,
        "delete_account": bool(delete_account),
        "conversation_ids_deleted": [],
        "draft_ids_deleted": [],
    }
    deleted_blob_refs: set[str] = set()

    chat_rows = await _query_all("ChatHistory", f"PartitionKey eq '{odata_escape(safe_user)}'", top=500)
    conversation_ids = [str(row.get("RowKey", "") or "").strip() for row in chat_rows if str(row.get("RowKey", "") or "").strip()]
    for row in chat_rows:
        try:
            await table_delete("ChatHistory", str(row.get("PartitionKey", "")), str(row.get("RowKey", "")))
            summary["deleted_rows"] += 1
        except Exception as exc:
            logger.warning("[PrivacyService] chat row delete failed: %s", exc)

    upload_index_rows = await _query_all("UploadIndex", f"UserSub eq '{odata_escape(safe_user)}'", top=1000)
    upload_job_ids: set[str] = set()
    for row in upload_index_rows:
        for field in ("RawBlobRef", "ExtractedBlobRef", "ChunksBlobRef", "TabularArtifactBlobRef"):
            if await _delete_blob_ref(str(row.get(field, "") or ""), deleted_blob_refs):
                summary["deleted_blobs"] += 1
        try:
            await table_delete("UploadIndex", str(row.get("PartitionKey", "")), str(row.get("RowKey", "")))
            summary["deleted_rows"] += 1
        except Exception as exc:
            logger.warning("[PrivacyService] upload index delete failed: %s", exc)
        row_key = str(row.get("RowKey", "") or "").strip()
        if row_key:
            upload_job_ids.add(row_key)

    upload_job_rows = await _query_all(
        "UploadJobs",
        f"PartitionKey eq 'upload' and UserSub eq '{odata_escape(safe_user)}'",
        top=1000,
    )
    for row in upload_job_rows:
        row_key = str(row.get("RowKey", "") or "").strip()
        for field in ("RawBlobRef", "ExtractedBlobRef", "ChunksBlobRef", "TabularArtifactBlobRef"):
            if await _delete_blob_ref(str(row.get(field, "") or ""), deleted_blob_refs):
                summary["deleted_blobs"] += 1
        try:
            await table_delete("UploadJobs", str(row.get("PartitionKey", "")), row_key)
            summary["deleted_rows"] += 1
        except Exception as exc:
            logger.warning("[PrivacyService] upload job delete failed: %s", exc)
        if row_key:
            upload_job_ids.add(row_key)

    story_draft_rows = await _query_all(_DRAFTS_TABLE, f"PartitionKey eq '{odata_escape(_draft_partition_key(safe_user))}'", top=1000)
    draft_ids = [str(row.get("RowKey", "") or "").strip() for row in story_draft_rows if str(row.get("RowKey", "") or "").strip()]
    for row in story_draft_rows:
        for field in ("DraftBlobRef", "FinalDraftBlobRef"):
            if await _delete_blob_ref(str(row.get(field, "") or ""), deleted_blob_refs):
                summary["deleted_blobs"] += 1
        try:
            await table_delete(_DRAFTS_TABLE, str(row.get("PartitionKey", "")), str(row.get("RowKey", "")))
            summary["deleted_rows"] += 1
        except Exception as exc:
            logger.warning("[PrivacyService] story draft delete failed: %s", exc)

    story_feedback_rows = await _query_all(_FEEDBACK_TABLE, f"PartitionKey eq '{odata_escape(_draft_partition_key(safe_user))}'", top=1000)
    for row in story_feedback_rows:
        if await _delete_blob_ref(str(row.get("EventBlobRef", "") or ""), deleted_blob_refs):
            summary["deleted_blobs"] += 1
        try:
            await table_delete(_FEEDBACK_TABLE, str(row.get("PartitionKey", "")), str(row.get("RowKey", "")))
            summary["deleted_rows"] += 1
        except Exception as exc:
            logger.warning("[PrivacyService] story feedback delete failed: %s", exc)

    feedback_rows = await _query_all("feedback", f"UserSub eq '{odata_escape(safe_user)}'", top=1000)
    for row in feedback_rows:
        try:
            await table_delete("feedback", str(row.get("PartitionKey", "")), str(row.get("RowKey", "")))
            summary["deleted_rows"] += 1
        except Exception as exc:
            logger.warning("[PrivacyService] feedback delete failed: %s", exc)

    example_rows = await _query_all("examples", f"UserSub eq '{odata_escape(safe_user)}'", top=1000)
    for row in example_rows:
        try:
            await table_delete("examples", str(row.get("PartitionKey", "")), str(row.get("RowKey", "")))
            summary["deleted_rows"] += 1
        except Exception as exc:
            logger.warning("[PrivacyService] examples delete failed: %s", exc)

    curated_rows = [
        row
        for row in await _query_all(_CURATED_TABLE, "PartitionKey eq 'global'", top=500)
        if str(row.get("SourceUserSub", "") or "").strip() == safe_user
    ]
    for row in curated_rows:
        draft_id = str(row.get("RowKey", "") or "").strip()
        entry_blob_ref = str(row.get("EntryBlobRef", "") or "").strip()
        if entry_blob_ref and "/" in entry_blob_ref:
            try:
                container, blob_name = entry_blob_ref.split("/", 1)
                payload = await blob_download_json(container, blob_name)
                entry = payload.get("entry", {}) if isinstance(payload, dict) and isinstance(payload.get("entry"), dict) else {}
                if entry:
                    entry["source_user_sub"] = ""
                    entry["promoted_by"] = _clip(entry.get("promoted_by", ""), 120)
                    payload["entry"] = entry
                    payload["privacy_updated_at"] = _utc_now_iso()
                    await blob_upload_json(container, blob_name, payload)
            except Exception as exc:
                logger.warning("[PrivacyService] curated blob anonymization failed for %s: %s", draft_id, exc)
        merged = {
            "PartitionKey": "global",
            "RowKey": draft_id,
            "SourceUserSub": "",
            "SubmittedBy": "",
            "UpdatedAt": _utc_now_iso(),
        }
        try:
            await table_merge(_CURATED_TABLE, merged)
            summary["anonymized_rows"] += 1
        except Exception as exc:
            logger.warning("[PrivacyService] curated row anonymization failed for %s: %s", draft_id, exc)
        try:
            result = await sync_user_story_examples_search_index(draft_id=draft_id, top=1)
            summary["search_updates"] += int(result.get("synced", 0) or 0) + int(result.get("deleted", 0) or 0)
        except Exception as exc:
            logger.warning("[PrivacyService] curated search sync failed for %s: %s", draft_id, exc)

    knowledge_rows = [
        row
        for row in await _query_all(_KNOWLEDGE_TABLE, "PartitionKey eq 'global'", top=500)
        if str(row.get("ImportedBy", "") or "").strip() == safe_user
        or str(row.get("SourceUserSub", "") or "").strip() == safe_user
    ]
    for row in knowledge_rows:
        asset_id = str(row.get("RowKey", "") or "").strip()
        try:
            result = await review_story_knowledge_asset(
                asset_id=asset_id,
                action="delete",
                reviewed_by=safe_user,
                note="Deleted by privacy request",
            )
            summary["search_updates"] += int(bool(result.get("search_sync", {}).get("ok")))
        except Exception as exc:
            logger.warning("[PrivacyService] knowledge asset delete failed for %s: %s", asset_id, exc)
        entry_blob_ref = str(row.get("EntryBlobRef", "") or "").strip()
        if await _delete_blob_ref(entry_blob_ref, deleted_blob_refs):
            summary["deleted_blobs"] += 1
        try:
            await table_merge(
                _KNOWLEDGE_TABLE,
                {
                    "PartitionKey": "global",
                    "RowKey": asset_id,
                    "ImportedBy": "",
                    "ConversationId": "",
                    "UploadRowKey": "",
                    "Filename": "",
                    "EntryBlobRef": "",
                    "UpdatedAt": _utc_now_iso(),
                },
            )
            summary["anonymized_rows"] += 1
        except Exception as exc:
            logger.warning("[PrivacyService] knowledge asset anonymization failed for %s: %s", asset_id, exc)

    if delete_account:
        try:
            await table_delete("Users", "user", safe_user)
            summary["deleted_rows"] += 1
        except Exception as exc:
            logger.warning("[PrivacyService] user account delete failed for %s: %s", safe_user, exc)

    export_blob = await blob_upload_json(
        _CHAT_TOOLRESULT_BLOB_CONTAINER,
        f"privacy/{safe_blob_component(safe_user, 'user')}/delete-summary-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.json",
        {
            "executed_at": _utc_now_iso(),
            "user_sub": safe_user,
            "conversation_ids_deleted": conversation_ids[:500],
            "upload_job_ids_deleted": sorted(upload_job_ids)[:1000],
            "draft_ids_deleted": draft_ids[:1000],
            "summary": summary,
        },
    )
    summary["conversation_ids_deleted"] = conversation_ids[:500]
    summary["draft_ids_deleted"] = draft_ids[:1000]
    summary["summary_blob_ref"] = str(export_blob.get("blob_ref", "") or "")
    return summary
