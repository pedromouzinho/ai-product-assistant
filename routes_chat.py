"""routes_chat.py — Chat persistence, privacy export/delete endpoints."""

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials

from auth import get_current_user, get_current_principal
from auth_runtime import persist_user_invalidation, revoke_token_persistent
from models import (
    SaveChatRequest,
    UpdateChatTitleRequest,
    PrivacyDeleteRequest,
)
from route_deps import (
    security,
    limiter,
    _user_or_ip_rate_key,
    _is_admin_user,
    _conversation_belongs_to_user,
    log_audit,
)
from storage import table_insert, table_merge, table_query, table_delete
from privacy_service import build_user_privacy_export, delete_user_personal_data
from tools import _store_generated_file
from utils import safe_blob_component, odata_escape

logger = logging.getLogger(__name__)

router = APIRouter()

# These will be injected by app.py at include time via router_state
_router_state: dict = {}


def _get_conversations():
    return _router_state["conversations"]


def _get_conversation_meta():
    return _router_state["conversation_meta"]


def _get_uploaded_files_store():
    return _router_state["uploaded_files_store"]


def _get_purge_fn():
    return _router_state["purge_upload_artifacts_for_conversation"]


# =========================================================================
# CHAT PERSISTENCE
# =========================================================================


@router.post("/api/chats/save")
@limiter.limit("30/minute", key_func=_user_or_ip_rate_key)
async def save_chat(
    request: Request,
    payload: SaveChatRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = get_current_user(credentials)
    uid = user.get("sub", payload.user_id)
    msgs = [{"role": m.get("role", ""), "content": m.get("content", "")} for m in payload.messages]
    msgs_json = json.dumps(msgs, ensure_ascii=False)
    while len(msgs_json) > 60000 and len(msgs) > 4:
        msgs.pop(1)
        msgs_json = json.dumps(msgs, ensure_ascii=False)
    entity = {
        "PartitionKey": uid,
        "RowKey": payload.conversation_id,
        "Title": (payload.title or "Nova conversa")[:100],
        "Messages": msgs_json,
        "MessageCount": len(payload.messages),
        "UpdatedAt": datetime.now(timezone.utc).isoformat(),
    }
    if not await table_insert("ChatHistory", entity):
        await table_merge("ChatHistory", entity)
    return {"status": "ok", "conversation_id": payload.conversation_id}


@router.get("/api/chats/{user_id}")
@limiter.limit("60/minute", key_func=_user_or_ip_rate_key)
async def list_chats(
    request: Request,
    user_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = get_current_user(credentials)
    uid = user.get("sub") if user.get("role") != "admin" else user_id
    safe_uid = odata_escape(uid)
    entities = await table_query("ChatHistory", f"PartitionKey eq '{safe_uid}'", top=100)
    chats = sorted(
        [
            {
                "conversation_id": e.get("RowKey", ""),
                "title": e.get("Title", ""),
                "message_count": e.get("MessageCount", 0),
                "updated_at": e.get("UpdatedAt", ""),
            }
            for e in entities
        ],
        key=lambda c: c["updated_at"],
        reverse=True,
    )
    return {"chats": chats}


@router.get("/api/chats/{user_id}/{conversation_id}")
@limiter.limit("60/minute", key_func=_user_or_ip_rate_key)
async def get_chat(
    request: Request,
    user_id: str,
    conversation_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = get_current_user(credentials)
    uid = user.get("sub") if user.get("role") != "admin" else user_id
    safe_uid = odata_escape(uid)
    safe_conv = odata_escape(conversation_id)
    es = await table_query(
        "ChatHistory",
        f"PartitionKey eq '{safe_uid}' and RowKey eq '{safe_conv}'",
        top=1,
    )
    if not es:
        raise HTTPException(404, "Não encontrada")
    return {
        "conversation_id": conversation_id,
        "title": es[0].get("Title", ""),
        "messages": json.loads(es[0].get("Messages", "[]")),
        "updated_at": es[0].get("UpdatedAt", ""),
    }


@router.post("/api/chats/{user_id}/{conversation_id}/title")
@limiter.limit("30/minute", key_func=_user_or_ip_rate_key)
async def update_chat_title(
    request: Request,
    user_id: str,
    conversation_id: str,
    payload: UpdateChatTitleRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = get_current_user(credentials)
    uid = user.get("sub") if user.get("role") != "admin" else user_id
    safe_uid = odata_escape(uid)
    safe_conv = odata_escape(conversation_id)
    existing = await table_query(
        "ChatHistory",
        f"PartitionKey eq '{safe_uid}' and RowKey eq '{safe_conv}'",
        top=1,
    )
    if not existing:
        raise HTTPException(404, "Conversa não encontrada")
    title = str(payload.title or "").strip()
    if not title:
        raise HTTPException(400, "Título inválido")
    entity = {
        "PartitionKey": uid,
        "RowKey": conversation_id,
        "Title": title[:100],
        "UpdatedAt": datetime.now(timezone.utc).isoformat(),
    }
    await table_merge("ChatHistory", entity)
    return {
        "status": "ok",
        "conversation_id": conversation_id,
        "title": entity["Title"],
        "updated_at": entity["UpdatedAt"],
    }


@router.delete("/api/chats/{user_id}/{conversation_id}")
@limiter.limit("30/minute", key_func=_user_or_ip_rate_key)
async def delete_chat(
    request: Request,
    user_id: str,
    conversation_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = get_current_user(credentials)
    is_admin = _is_admin_user(user)
    uid = user.get("sub") if not is_admin else user_id
    await table_delete("ChatHistory", uid, conversation_id)
    purge_fn = _get_purge_fn()
    await purge_fn(
        conversation_id,
        user_sub=str(uid or ""),
        include_all_users=is_admin,
    )
    _get_conversation_meta().pop(conversation_id, None)
    _get_uploaded_files_store().pop(conversation_id, None)
    conversations = _get_conversations()
    if conversation_id in conversations:
        del conversations[conversation_id]
    return {"status": "ok"}


# =========================================================================
# PRIVACY
# =========================================================================


@router.get("/api/privacy/export")
@limiter.limit("5/hour", key_func=_user_or_ip_rate_key)
async def export_my_data(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    principal = get_current_principal(credentials, request=request)
    export_payload = await build_user_privacy_export(principal.sub)
    filename = f"privacy-export-{safe_blob_component(principal.sub, 'user')}.json"
    content = json.dumps(export_payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")
    download_id = await _store_generated_file(
        content,
        "application/json",
        filename,
        "json",
        user_sub=principal.sub,
        scope="privacy_export",
    )
    await log_audit(
        principal.sub,
        "privacy_export",
        metadata={
            "mode": "privacy",
            "provider_used": "internal",
            "conversation_id": "",
            "summary": export_payload.get("summary", {}),
        },
    )
    return {
        "status": "ok",
        "download_id": download_id,
        "url": f"/api/download/{download_id}" if download_id else "",
        "filename": filename,
        "summary": export_payload.get("summary", {}),
    }


@router.post("/api/privacy/delete")
@limiter.limit("2/hour", key_func=_user_or_ip_rate_key)
async def delete_my_data(
    request: Request,
    payload: PrivacyDeleteRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    principal = get_current_principal(credentials, request=request)
    if payload.confirmation != "DELETE_MY_DATA":
        raise HTTPException(400, "Confirmação inválida.")
    result = await delete_user_personal_data(principal.sub, delete_account=bool(payload.delete_account))
    conversations = _get_conversations()
    conversation_meta = _get_conversation_meta()
    uploaded_files_store = _get_uploaded_files_store()
    for conversation_id in result.get("conversation_ids_deleted", []) or []:
        safe_conv = str(conversation_id or "").strip()
        if not safe_conv:
            continue
        conversations.pop(safe_conv, None)
        conversation_meta.pop(safe_conv, None)
        uploaded_files_store.pop(safe_conv, None)
    if payload.delete_account:
        await persist_user_invalidation(principal.sub)
    await log_audit(
        principal.sub,
        "privacy_delete",
        metadata={
            "mode": "privacy",
            "provider_used": "internal",
            "conversation_id": "",
            "delete_account": bool(payload.delete_account),
            "summary": result,
        },
    )
    return {"status": "ok", **result}
