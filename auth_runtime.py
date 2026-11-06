"""
Persistent auth runtime state backed by Azure Table Storage.
Keeps revocations and lockouts alive across App Service recycle/deploy.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import auth
from storage import table_insert, table_merge, table_query
from utils import odata_escape

logger = logging.getLogger(__name__)

AUTH_STATE_TABLE = "AuthState"
USER_STATE_PARTITION = "user_state"
REVOKED_TOKEN_PARTITION = "revoked_token"
USER_STATE_CACHE_TTL_SECONDS = 30.0
REVOKED_TOKEN_NEGATIVE_CACHE_TTL_SECONDS = 30.0

_cache_lock = threading.Lock()
_user_state_cache: dict[str, tuple[float, dict]] = {}
_revoked_token_cache: dict[str, datetime] = {}
_revoked_token_negative_cache: dict[str, float] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(raw_value) -> Optional[datetime]:
    raw = str(raw_value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def clear_runtime_caches() -> None:
    with _cache_lock:
        _user_state_cache.clear()
        _revoked_token_cache.clear()
        _revoked_token_negative_cache.clear()


async def _load_auth_state_row(partition_key: str, row_key: str) -> dict:
    safe_pk = odata_escape(partition_key)
    safe_rk = odata_escape(row_key)
    rows = await table_query(
        AUTH_STATE_TABLE,
        f"PartitionKey eq '{safe_pk}' and RowKey eq '{safe_rk}'",
        top=1,
    )
    if rows:
        return dict(rows[0] or {})
    return {}


async def _upsert_auth_state_row(entity: dict) -> None:
    try:
        await table_merge(AUTH_STATE_TABLE, entity)
    except Exception:
        inserted = await table_insert(AUTH_STATE_TABLE, entity)
        if not inserted:
            raise RuntimeError("AuthState insert returned False")


def _cache_user_state(username: str, state: dict) -> None:
    if not username:
        return
    cached = dict(state or {})
    with _cache_lock:
        _user_state_cache[username] = (time.time(), cached)


def _get_cached_user_state(username: str) -> Optional[dict]:
    if not username:
        return None
    now = time.time()
    with _cache_lock:
        entry = _user_state_cache.get(username)
        if not entry:
            return None
        cached_at, state = entry
        if (now - cached_at) > USER_STATE_CACHE_TTL_SECONDS:
            _user_state_cache.pop(username, None)
            return None
        return dict(state or {})


def _invalidate_user_state_cache(username: str) -> None:
    if not username:
        return
    with _cache_lock:
        _user_state_cache.pop(username, None)


def _cache_revoked_token(jti: str, exp: datetime) -> None:
    if not jti:
        return
    expiry = exp if exp.tzinfo is not None else exp.replace(tzinfo=timezone.utc)
    with _cache_lock:
        _revoked_token_cache[jti] = expiry
        _revoked_token_negative_cache.pop(jti, None)


def _cache_non_revoked_token(jti: str) -> None:
    if not jti:
        return
    with _cache_lock:
        _revoked_token_negative_cache[jti] = time.time()


async def get_user_auth_state(username: str, *, use_cache: bool = True) -> dict:
    name = str(username or "").strip()
    if not name:
        return {}
    if use_cache:
        cached = _get_cached_user_state(name)
        if cached is not None:
            return cached
    try:
        row = await _load_auth_state_row(USER_STATE_PARTITION, name)
    except Exception as e:
        logger.warning("[AuthRuntime] Failed to load user auth state for %s: %s", name, e)
        return {}
    _cache_user_state(name, row)
    return row


async def is_account_locked_persistent(username: str) -> bool:
    state = await get_user_auth_state(username)
    locked_until = _parse_dt(state.get("LockoutUntil"))
    if locked_until is None:
        return False
    return _utcnow() < locked_until


async def record_login_failure_persistent(username: str) -> None:
    name = str(username or "").strip()
    if not name:
        return
    now = _utcnow()
    try:
        state = await get_user_auth_state(name, use_cache=False)
        last_failed = _parse_dt(state.get("LastFailedAt"))
        failures = int(state.get("FailedLoginCount", 0) or 0)
        window_seconds = auth._LOCKOUT_DURATION_MINUTES * 60
        if last_failed is None or (now - last_failed).total_seconds() > window_seconds:
            failures = 0
        failures += 1
        locked_until = None
        current_lockout = _parse_dt(state.get("LockoutUntil"))
        if current_lockout and current_lockout > now:
            locked_until = current_lockout
        if failures >= auth._MAX_LOGIN_ATTEMPTS:
            candidate = now + timedelta(minutes=auth._LOCKOUT_DURATION_MINUTES)
            locked_until = max(locked_until, candidate) if locked_until else candidate
        entity = {
            "PartitionKey": USER_STATE_PARTITION,
            "RowKey": name,
            "FailedLoginCount": failures,
            "LastFailedAt": now.isoformat(),
            "LockoutUntil": locked_until.isoformat() if locked_until else "",
            "UpdatedAt": now.isoformat(),
        }
        await _upsert_auth_state_row(entity)
        _cache_user_state(name, entity)
    except Exception as e:
        logger.warning("[AuthRuntime] Failed to persist login failure for %s: %s", name, e)


async def clear_login_failures_persistent(username: str) -> None:
    name = str(username or "").strip()
    if not name:
        return
    now = _utcnow()
    entity = {
        "PartitionKey": USER_STATE_PARTITION,
        "RowKey": name,
        "FailedLoginCount": 0,
        "LastFailedAt": "",
        "LockoutUntil": "",
        "UpdatedAt": now.isoformat(),
    }
    try:
        await _upsert_auth_state_row(entity)
    except Exception as e:
        logger.warning("[AuthRuntime] Failed to clear login failures for %s: %s", name, e)
    _cache_user_state(name, entity)


async def persist_user_invalidation(username: str, *, cutoff: Optional[datetime] = None) -> datetime:
    name = str(username or "").strip()
    effective_cutoff = cutoff or _utcnow()
    if effective_cutoff.tzinfo is None:
        effective_cutoff = effective_cutoff.replace(tzinfo=timezone.utc)
    if not name:
        return effective_cutoff
    now = _utcnow()
    state = await get_user_auth_state(name, use_cache=False)
    entity = {
        "PartitionKey": USER_STATE_PARTITION,
        "RowKey": name,
        "TokensInvalidBefore": effective_cutoff.isoformat(),
        "FailedLoginCount": state.get("FailedLoginCount", 0),
        "LastFailedAt": state.get("LastFailedAt", ""),
        "LockoutUntil": state.get("LockoutUntil", ""),
        "UpdatedAt": now.isoformat(),
    }
    try:
        await _upsert_auth_state_row(entity)
    except Exception as e:
        logger.warning("[AuthRuntime] Failed to persist invalidation cutoff for %s: %s", name, e)
    auth.cache_user_invalidation_cutoff(name, effective_cutoff)
    _cache_user_state(name, entity)
    return effective_cutoff


async def is_user_token_invalidated_persistent(username: str, issued_at: datetime) -> bool:
    name = str(username or "").strip()
    if not name:
        return False
    iat = issued_at if issued_at.tzinfo is not None else issued_at.replace(tzinfo=timezone.utc)
    state = await get_user_auth_state(name)
    cutoff = _parse_dt(state.get("TokensInvalidBefore"))
    if cutoff is None:
        return False
    auth.cache_user_invalidation_cutoff(name, cutoff)
    return iat <= cutoff


async def revoke_token_persistent(jti: str, exp: datetime, *, username: str = "") -> None:
    token_id = str(jti or "").strip()
    if not token_id:
        return
    expiry = exp if exp.tzinfo is not None else exp.replace(tzinfo=timezone.utc)
    entity = {
        "PartitionKey": REVOKED_TOKEN_PARTITION,
        "RowKey": token_id,
        "ExpiresAt": expiry.isoformat(),
        "Username": str(username or "").strip(),
        "CreatedAt": _utcnow().isoformat(),
    }
    try:
        await _upsert_auth_state_row(entity)
    except Exception as e:
        logger.warning("[AuthRuntime] Failed to persist revoked token %s: %s", token_id, e)
    auth.blacklist_token(token_id, expiry)
    _cache_revoked_token(token_id, expiry)


async def is_token_revoked_persistent(jti: str) -> bool:
    token_id = str(jti or "").strip()
    if not token_id:
        return False
    now = _utcnow()
    with _cache_lock:
        cached_expiry = _revoked_token_cache.get(token_id)
        if cached_expiry:
            if now < cached_expiry:
                auth.blacklist_token(token_id, cached_expiry)
                return True
            _revoked_token_cache.pop(token_id, None)
        negative_ts = _revoked_token_negative_cache.get(token_id)
        if negative_ts and (time.time() - negative_ts) < REVOKED_TOKEN_NEGATIVE_CACHE_TTL_SECONDS:
            return False
    try:
        row = await _load_auth_state_row(REVOKED_TOKEN_PARTITION, token_id)
    except Exception as e:
        logger.warning("[AuthRuntime] Failed to load revoked token %s: %s", token_id, e)
        return False
    expiry = _parse_dt(row.get("ExpiresAt"))
    if expiry and now < expiry:
        auth.blacklist_token(token_id, expiry)
        _cache_revoked_token(token_id, expiry)
        return True
    _cache_non_revoked_token(token_id)
    return False


async def validate_request_token(token: str) -> tuple[Optional[dict], str]:
    raw_token = str(token or "").strip()
    if not raw_token:
        return None, ""
    try:
        payload = auth.jwt_decode(raw_token)
    except ValueError as e:
        return None, f"Token inválido ou expirado: {e}"

    jti = str(payload.get("jti", "") or "")
    if jti and await is_token_revoked_persistent(jti):
        return None, "Token inválido ou expirado: Token revoked"

    sub = str(payload.get("sub", "") or "")
    iat_raw = payload.get("iat")
    if sub and isinstance(iat_raw, str):
        issued_at = _parse_dt(iat_raw)
        if issued_at and await is_user_token_invalidated_persistent(sub, issued_at):
            return None, "Token inválido ou expirado: Token invalidated by admin"
    return payload, ""
