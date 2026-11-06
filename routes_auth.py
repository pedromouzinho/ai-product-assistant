# =============================================================================
# routes_auth.py — Auth + Speech endpoints
# =============================================================================

import re
import time
import logging
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials

from config import (
    AUTH_COOKIE_NAME, AUTH_COOKIE_SECURE, AUTH_COOKIE_MAX_AGE_SECONDS,
    JWT_REFRESH_WINDOW_MINUTES,
    AZURE_SPEECH_ENABLED, AZURE_SPEECH_KEY, AZURE_SPEECH_REGION, AZURE_SPEECH_LANGUAGE,
)
from models import (
    LoginRequest, CreateUserRequest, ChangePasswordRequest,
    SpeechPromptNormalizeRequest, SpeechPromptNormalizeResponse, SpeechPromptTokenResponse,
)
from auth import (
    get_current_user, jwt_encode, jwt_decode, hash_password, verify_password,
    record_login_failure, is_account_locked, clear_login_attempts,
    _LOCKOUT_DURATION_MINUTES,
)
from auth_runtime import (
    is_account_locked_persistent, record_login_failure_persistent,
    clear_login_failures_persistent, revoke_token_persistent, persist_user_invalidation,
)
from storage import table_query, table_insert, table_merge
from utils import odata_escape
from speech_prompt import normalize_spoken_prompt
from route_deps import (
    security, limiter, log_audit,
    _login_rate_key, _user_or_ip_rate_key,
    _extract_request_token, _request_is_https,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# =============================================================================
# AUTH
# =============================================================================

@router.post("/api/auth/login")
@limiter.limit("5/minute", key_func=_login_rate_key)
async def login(request: Request, login_request: LoginRequest):
    if is_account_locked(login_request.username) or await is_account_locked_persistent(login_request.username):
        raise HTTPException(
            429,
            f"Conta temporariamente bloqueada. Tenta novamente em {_LOCKOUT_DURATION_MINUTES} minutos.",
        )

    safe_username = odata_escape(login_request.username)
    users = await table_query("Users", f"PartitionKey eq 'user' and RowKey eq '{safe_username}'", top=1)
    if not users:
        record_login_failure(login_request.username)
        await record_login_failure_persistent(login_request.username)
        raise HTTPException(401, "Credenciais inválidas")
    user = users[0]
    if not verify_password(login_request.password, user.get("PasswordHash", "")):
        record_login_failure(login_request.username)
        await record_login_failure_persistent(login_request.username)
        raise HTTPException(401, "Credenciais inválidas")
    if user.get("IsActive") == False:
        raise HTTPException(403, "Conta desactivada")
    clear_login_attempts(login_request.username)
    await clear_login_failures_persistent(login_request.username)
    token = jwt_encode({"sub": login_request.username, "role": user.get("Role", "user"), "name": user.get("DisplayName", login_request.username)})
    response = JSONResponse(
        content={
            "status": "ok",
            "username": login_request.username,
            "role": user.get("Role", "user"),
            "display_name": user.get("DisplayName", login_request.username),
        }
    )
    secure_cookie = AUTH_COOKIE_SECURE if _request_is_https(request) else False
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=secure_cookie,
        samesite="lax",
        path="/",
        max_age=AUTH_COOKIE_MAX_AGE_SECONDS,
    )
    return response


@router.post("/api/auth/logout")
@limiter.limit("20/minute", key_func=_user_or_ip_rate_key)
async def logout(request: Request):
    token = _extract_request_token(request)
    if token:
        try:
            payload = jwt_decode(token)
            jti = str(payload.get("jti", "") or "")
            exp_raw = payload.get("exp")
            if jti and isinstance(exp_raw, str):
                exp = datetime.fromisoformat(exp_raw)
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                await revoke_token_persistent(jti, exp, username=str(payload.get("sub", "") or ""))
        except ValueError:
            pass

    response = JSONResponse(content={"status": "ok"})
    secure_cookie = AUTH_COOKIE_SECURE if _request_is_https(request) else False
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value="",
        httponly=True,
        secure=secure_cookie,
        samesite="lax",
        path="/",
        max_age=0,
    )
    return response


@router.post("/api/auth/refresh")
@limiter.limit("10/minute", key_func=_user_or_ip_rate_key)
async def refresh_token(request: Request, credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Emite um novo token se o atual ainda for válido e estiver perto de expirar."""
    token = _extract_request_token(request)
    if not token:
        raise HTTPException(401, "Token não fornecido")
    try:
        payload = jwt_decode(token)
    except ValueError as e:
        raise HTTPException(401, f"Token inválido: {e}")

    # Só permitir refresh na janela final (últimos N minutos antes de expirar)
    exp_raw = payload.get("exp")
    if isinstance(exp_raw, str):
        exp_dt = datetime.fromisoformat(exp_raw)
        if exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=timezone.utc)
        remaining = (exp_dt - datetime.now(timezone.utc)).total_seconds()
        if remaining > JWT_REFRESH_WINDOW_MINUTES * 60:
            return JSONResponse(
                content={"status": "ok", "refreshed": False, "message": "Token ainda válido, refresh não necessário."},
            )

    # Revogar o token antigo
    old_jti = str(payload.get("jti", "") or "")
    if old_jti and isinstance(exp_raw, str):
        exp_dt = datetime.fromisoformat(exp_raw)
        if exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=timezone.utc)
        await revoke_token_persistent(old_jti, exp_dt, username=str(payload.get("sub", "") or ""))

    # Emitir novo token com os mesmos claims
    new_token = jwt_encode({
        "sub": payload.get("sub"),
        "role": payload.get("role"),
        "name": payload.get("name"),
    })

    response = JSONResponse(content={"status": "ok", "refreshed": True})
    secure_cookie = AUTH_COOKIE_SECURE if _request_is_https(request) else False
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=new_token,
        httponly=True,
        secure=secure_cookie,
        samesite="lax",
        path="/",
        max_age=AUTH_COOKIE_MAX_AGE_SECONDS,
    )
    log_audit(logger, "token_refreshed", request, user=str(payload.get("sub", "")))
    return response


@router.post("/api/auth/create-user")
@limiter.limit("10/minute", key_func=_user_or_ip_rate_key)
async def create_user(request: Request, payload: CreateUserRequest, credentials: HTTPAuthorizationCredentials = Depends(security)):
    user = get_current_user(credentials)
    if user.get("role") != "admin":
        raise HTTPException(403, "Apenas admins")
    safe_username = odata_escape(payload.username)
    existing = await table_query("Users", f"PartitionKey eq 'user' and RowKey eq '{safe_username}'", top=1)
    if existing:
        raise HTTPException(409, "Username já existe")
    entity = {
        "PartitionKey": "user", "RowKey": payload.username,
        "PasswordHash": hash_password(payload.password),
        "DisplayName": payload.display_name or payload.username,
        "Role": payload.role or "user", "IsActive": True,
        "CreatedAt": datetime.now(timezone.utc).isoformat(),
        "CreatedBy": user.get("sub"),
    }
    created = await table_insert("Users", entity)
    if not created:
        raise HTTPException(500, "Falha ao criar utilizador")
    return {"status": "ok", "username": payload.username}


@router.get("/api/auth/users")
@limiter.limit("30/minute", key_func=_user_or_ip_rate_key)
async def list_users(request: Request, credentials: HTTPAuthorizationCredentials = Depends(security)):
    user = get_current_user(credentials)
    if user.get("role") != "admin":
        raise HTTPException(403, "Apenas admins")
    users = await table_query("Users", "PartitionKey eq 'user'", top=100)
    return {"users": [{"username": u.get("RowKey"), "display_name": u.get("DisplayName"), "role": u.get("Role"), "is_active": u.get("IsActive", True)} for u in users]}


@router.delete("/api/auth/users/{username}")
@limiter.limit("20/minute", key_func=_user_or_ip_rate_key)
async def deactivate_user(request: Request, username: str, credentials: HTTPAuthorizationCredentials = Depends(security)):
    user = get_current_user(credentials)
    if user.get("role") != "admin":
        raise HTTPException(403, "Apenas admins")
    if username == user.get("sub"):
        raise HTTPException(400, "Não podes desactivar-te")
    await table_merge("Users", {"PartitionKey": "user", "RowKey": username, "IsActive": False})
    await persist_user_invalidation(username)
    return {"status": "ok"}


@router.post("/api/auth/change-password")
@limiter.limit("20/minute", key_func=_user_or_ip_rate_key)
async def change_password(request: Request, payload: ChangePasswordRequest, credentials: HTTPAuthorizationCredentials = Depends(security)):
    user = get_current_user(credentials)
    username = user.get("sub")
    safe_username = odata_escape(username)
    users = await table_query("Users", f"PartitionKey eq 'user' and RowKey eq '{safe_username}'", top=1)
    if not users:
        raise HTTPException(404, "User não encontrado")
    if not verify_password(payload.current_password, users[0].get("PasswordHash", "")):
        raise HTTPException(401, "Password actual incorrecta")
    await table_merge("Users", {"PartitionKey": "user", "RowKey": username, "PasswordHash": hash_password(payload.new_password)})
    await persist_user_invalidation(username)
    return {"status": "ok"}


@router.post("/api/auth/reset-password/{username}")
@limiter.limit("15/minute", key_func=_user_or_ip_rate_key)
async def admin_reset_password(request: Request, username: str, payload: LoginRequest, credentials: HTTPAuthorizationCredentials = Depends(security)):
    user = get_current_user(credentials)
    if user.get("role") != "admin":
        raise HTTPException(403, "Apenas admins")
    await table_merge("Users", {"PartitionKey": "user", "RowKey": username, "PasswordHash": hash_password(payload.password)})
    await persist_user_invalidation(username)
    return {"status": "ok"}


@router.post("/api/auth/force-logout/{username}")
@limiter.limit("10/minute", key_func=_user_or_ip_rate_key)
async def force_logout_user(request: Request, username: str, credentials: HTTPAuthorizationCredentials = Depends(security)):
    user = get_current_user(credentials)
    if user.get("role") != "admin":
        raise HTTPException(403, "Apenas admins")
    await persist_user_invalidation(username)
    return {"status": "ok", "message": f"Tokens de {username} invalidados"}


@router.get("/api/auth/me")
@limiter.limit("60/minute", key_func=_user_or_ip_rate_key)
async def get_me(request: Request, credentials: HTTPAuthorizationCredentials = Depends(security)):
    user = get_current_user(credentials)
    return {"username": user.get("sub"), "role": user.get("role"), "name": user.get("name")}


# =============================================================================
# SPEECH
# =============================================================================

@router.post("/api/speech/prompt", response_model=SpeechPromptNormalizeResponse)
@limiter.limit("30/minute", key_func=_user_or_ip_rate_key)
async def normalize_speech_prompt_endpoint(
    request: Request,
    payload: SpeechPromptNormalizeRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user = get_current_user(credentials)
    transcript = str(payload.transcript or "").strip()
    if not transcript:
        raise HTTPException(400, "Transcrição vazia")

    started = time.perf_counter()
    result = await normalize_spoken_prompt(
        transcript=transcript,
        mode=str(payload.mode or "general"),
        language=str(payload.language or "pt-PT"),
    )
    logger.info(
        "[Speech] normalized prompt provider=%s confidence=%s auto_send=%s elapsed_ms=%d",
        result.get("provider_used", "unknown"),
        result.get("confidence", "unknown"),
        result.get("auto_send_allowed", False),
        int((time.perf_counter() - started) * 1000),
    )
    try:
        await log_audit(
            user.get("sub"),
            "speech_prompt",
            transcript,
            tools_used=["speech_prompt"],
            duration_ms=int((time.perf_counter() - started) * 1000),
            metadata={
                "mode": str(payload.mode or "general"),
                "provider_used": result.get("provider_used", "unknown"),
                "model_used": result.get("model_used", ""),
                "confidence": result.get("confidence", "unknown"),
                "auto_send_allowed": bool(result.get("auto_send_allowed", False)),
                "conversation_id": payload.conversation_id or "",
                "provider_family": result.get("provider_family", ""),
                "external_provider": bool(result.get("external_provider", False)),
                "data_sensitivity": result.get("data_sensitivity", ""),
                "provider_policy_mode": result.get("provider_policy_mode", ""),
                "provider_policy_note": result.get("provider_policy_note", ""),
            },
        )
    except Exception as exc:
        logger.warning("[App] speech prompt audit failed: %s", exc)
    return SpeechPromptNormalizeResponse(**result)


@router.post("/api/speech/token", response_model=SpeechPromptTokenResponse)
@limiter.limit("30/minute", key_func=_user_or_ip_rate_key)
async def issue_speech_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    get_current_user(credentials)
    if not (AZURE_SPEECH_ENABLED and AZURE_SPEECH_KEY and AZURE_SPEECH_REGION):
        raise HTTPException(503, "Azure Speech não está configurado.")

    token_url = f"https://{AZURE_SPEECH_REGION}.api.cognitive.microsoft.com/sts/v1.0/issuetoken"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                token_url,
                headers={"Ocp-Apim-Subscription-Key": AZURE_SPEECH_KEY, "Content-Length": "0"},
            )
            response.raise_for_status()
    except Exception as exc:
        logger.error("[Speech] token issuance failed: %s", exc)
        raise HTTPException(502, "Falha ao obter token do Azure Speech")

    token = str(response.text or "").strip()
    if not token:
        raise HTTPException(502, "Azure Speech não devolveu token válido")

    return SpeechPromptTokenResponse(
        token=token,
        region=AZURE_SPEECH_REGION,
        language=AZURE_SPEECH_LANGUAGE,
        expires_in_seconds=600,
    )


@router.post("/api/speech/synthesize")
@limiter.limit("20/minute", key_func=_user_or_ip_rate_key)
async def synthesize_speech(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Azure TTS — converte texto curto em áudio MP3 (para clarificações em modo voz)."""
    get_current_user(credentials)
    if not (AZURE_SPEECH_ENABLED and AZURE_SPEECH_KEY and AZURE_SPEECH_REGION):
        raise HTTPException(503, "Azure Speech TTS não está configurado.")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Body JSON inválido.")

    text = str(body.get("text", "") or "").strip()
    if not text or len(text) > 1000:
        raise HTTPException(400, "Texto em falta ou demasiado longo (max 1000 chars).")

    voice = str(body.get("voice", "") or "").strip() or "pt-PT-RaquelNeural"
    language = str(body.get("language", "") or "").strip() or "pt-PT"

    if not re.fullmatch(r'[a-zA-Z]{2,5}-[A-Z]{2,5}-[A-Za-z]+Neural', voice):
        voice = "pt-PT-RaquelNeural"
    if not re.fullmatch(r'[a-zA-Z]{2,5}-[A-Z]{2,5}', language):
        language = "pt-PT"

    safe_text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    ssml = (
        f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="{language}">'
        f'<voice name="{voice}">{safe_text}</voice>'
        f'</speak>'
    )
    tts_url = f"https://{AZURE_SPEECH_REGION}.tts.speech.microsoft.com/cognitiveservices/v1"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                tts_url,
                headers={
                    "Ocp-Apim-Subscription-Key": AZURE_SPEECH_KEY,
                    "Content-Type": "application/ssml+xml",
                    "X-Microsoft-OutputFormat": "audio-16khz-128kbitrate-mono-mp3",
                },
                content=ssml.encode("utf-8"),
            )
            resp.raise_for_status()
    except Exception as exc:
        logger.warning("[App] TTS synthesis failed: %s", exc)
        raise HTTPException(502, "Falha na síntese de voz.")

    return Response(content=resp.content, media_type="audio/mpeg")
