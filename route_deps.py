# =============================================================================
# route_deps.py — Shared route dependencies (auth, rate-limit, helpers)
# =============================================================================
# Extracted from app.py for the A2 modular split.
# Route modules import from here; app.py wires middleware.
# =============================================================================

import json
import re
import time
import uuid
import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional, Callable, Tuple
from urllib.parse import urlsplit

from fastapi import Request, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from config import AUTH_COOKIE_NAME, ALLOWED_ORIGINS, ROUTE_RATE_LIMIT_ENFORCEMENT_ENABLED
from auth import jwt_decode, principal_is_admin
from agent import conversation_meta
from storage import table_query, table_insert
from utils import odata_escape
from rate_limit_storage import TableStorageRateLimit
from provider_governance import evaluate_provider_governance
from field_encryption import encrypt_field, AUDIT_SENSITIVE_FIELDS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Security scheme (shared Depends across all routes)
# ---------------------------------------------------------------------------
security = HTTPBearer(auto_error=False)

# ---------------------------------------------------------------------------
# Origins
# ---------------------------------------------------------------------------
_allowed_origins = [o.strip().rstrip("/") for o in ALLOWED_ORIGINS.split(",") if o.strip()]
_allowed_origins_set = set(_allowed_origins)
_AUTH_EXEMPT_PATHS = {"/health", "/api/info", "/api/client-error", "/docs", "/openapi.json", "/redoc"}

# ---------------------------------------------------------------------------
# Client IP
# ---------------------------------------------------------------------------

def _client_ip(request: Request) -> str:
    xff = (request.headers.get("x-forwarded-for") or "").strip()
    if xff:
        return xff.split(",")[0].strip() or "unknown"
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


# ---------------------------------------------------------------------------
# Token extraction + auth helpers
# ---------------------------------------------------------------------------

def _extract_request_token(request: Request) -> str:
    cookie_token = (request.cookies.get(AUTH_COOKIE_NAME) or "").strip()
    authz = (request.headers.get("authorization") or "").strip()
    bearer_token = ""
    if authz.lower().startswith("bearer "):
        bearer_token = authz.split(" ", 1)[1].strip()
    return cookie_token or bearer_token


def _auth_payload_from_request(request: Request) -> dict:
    cached_payload = getattr(request.state, "auth_payload", None)
    if isinstance(cached_payload, dict) and cached_payload:
        return cached_payload
    if str(getattr(request.state, "auth_error", "") or "").strip():
        return {}

    token = _extract_request_token(request)
    if not token:
        return {}
    try:
        return jwt_decode(token)
    except Exception:
        return {}


def _is_admin_user(user: Optional[dict]) -> bool:
    return principal_is_admin(user)


async def _conversation_belongs_to_user(conversation_id: str, user_sub: str) -> bool:
    safe_conv = str(conversation_id or "").strip()
    safe_user = str(user_sub or "").strip()
    if not safe_conv or not safe_user:
        return False
    meta = conversation_meta.get(safe_conv, {})
    owner_sub = str((meta or {}).get("owner_sub", "") or (meta or {}).get("partition_key", "") or "").strip()
    if owner_sub:
        return owner_sub == safe_user
    try:
        rows = await table_query(
            "ChatHistory",
            f"PartitionKey eq '{odata_escape(safe_user)}' and RowKey eq '{odata_escape(safe_conv)}'",
            top=1,
        )
        return bool(rows)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Rate-limit key functions
# ---------------------------------------------------------------------------

def _login_rate_key(request: Request) -> str:
    return f"ip:{_client_ip(request)}"


def _user_or_ip_rate_key(request: Request) -> str:
    payload = _auth_payload_from_request(request)
    sub = str(payload.get("sub", "")).strip()
    if sub:
        return f"user:{sub}"
    return f"ip:{_client_ip(request)}"


# ---------------------------------------------------------------------------
# Decorator-based rate limiter
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _RateLimitRule:
    max_requests: int
    window_seconds: int
    key_func: Callable[[Request], str]
    scope: Optional[str] = None


class _DecoratorRateLimiter:
    """Compat layer para manter @limiter.limit e @limiter.shared_limit."""

    def __init__(self, key_func: Callable[[Request], str]):
        self._default_key_func = key_func

    @staticmethod
    def _parse_limit(limit_spec: str) -> Tuple[int, int]:
        text = str(limit_spec or "").strip().lower()
        match = re.fullmatch(r"(\d+)\s*/\s*(second|seconds|minute|minutes|hour|hours|day|days)", text)
        if not match:
            raise ValueError(f"Rate limit inválido: {limit_spec}")
        amount = int(match.group(1))
        unit = match.group(2)
        factor = 1
        if unit.startswith("minute"):
            factor = 60
        elif unit.startswith("hour"):
            factor = 3600
        elif unit.startswith("day"):
            factor = 86400
        return amount, factor

    def limit(self, limit_spec: str, key_func: Optional[Callable[[Request], str]] = None):
        max_requests, window_seconds = self._parse_limit(limit_spec)
        resolved_key_func = key_func or self._default_key_func

        def decorator(fn):
            if max_requests <= 0 or window_seconds <= 0:
                if hasattr(fn, "__app_rate_limit__"):
                    delattr(fn, "__app_rate_limit__")
                return fn
            setattr(
                fn,
                "__app_rate_limit__",
                _RateLimitRule(
                    max_requests=max_requests,
                    window_seconds=window_seconds,
                    key_func=resolved_key_func,
                    scope=None,
                ),
            )
            return fn

        return decorator

    def shared_limit(
        self,
        limit_spec: str,
        scope: str,
        key_func: Optional[Callable[[Request], str]] = None,
    ):
        max_requests, window_seconds = self._parse_limit(limit_spec)
        resolved_key_func = key_func or self._default_key_func
        shared_scope = str(scope or "").strip()

        def decorator(fn):
            if max_requests <= 0 or window_seconds <= 0:
                if hasattr(fn, "__app_rate_limit__"):
                    delattr(fn, "__app_rate_limit__")
                return fn
            setattr(
                fn,
                "__app_rate_limit__",
                _RateLimitRule(
                    max_requests=max_requests,
                    window_seconds=window_seconds,
                    key_func=resolved_key_func,
                    scope=shared_scope if shared_scope else None,
                ),
            )
            return fn

        return decorator

    @staticmethod
    def resolve(request: Request) -> Optional[_RateLimitRule]:
        route = request.scope.get("route")
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None:
            return None
        return getattr(endpoint, "__app_rate_limit__", None)


# Singletons
_rate_limiter_backend = TableStorageRateLimit()
limiter = _DecoratorRateLimiter(key_func=_user_or_ip_rate_key)


def resolve_rate_limit_rule(request: Request) -> Optional[_RateLimitRule]:
    if not ROUTE_RATE_LIMIT_ENFORCEMENT_ENABLED:
        return None
    return limiter.resolve(request)


# ---------------------------------------------------------------------------
# HTTPS / Origin helpers (used by middleware in app.py)
# ---------------------------------------------------------------------------

def _request_is_https(request: Request) -> bool:
    proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    return request.url.scheme == "https" or proto == "https"


def _normalize_origin_value(origin: str) -> str:
    raw = str(origin or "").strip().rstrip("/")
    if not raw:
        return ""
    parsed = urlsplit(raw)
    if not parsed.scheme or not parsed.netloc:
        return raw
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return raw
    port = parsed.port
    default_port = 443 if scheme == "https" else 80 if scheme == "http" else None
    suffix = "" if port in (None, default_port) else f":{port}"
    return f"{scheme}://{host}{suffix}"


def _request_origin(request: Request) -> str:
    host = (request.headers.get("host") or request.url.netloc or "").strip()
    if not host:
        return ""
    scheme = "https" if _request_is_https(request) else request.url.scheme
    return _normalize_origin_value(f"{scheme}://{host}")


def _origin_allowed_for_request(request: Request, origin: str) -> bool:
    normalized_origin = _normalize_origin_value(origin)
    if not normalized_origin:
        return True
    if normalized_origin in _allowed_origins_set:
        return True
    request_origin = _request_origin(request)
    return bool(request_origin and normalized_origin == request_origin)


# ---------------------------------------------------------------------------
# Audit log (shared across route modules)
# ---------------------------------------------------------------------------
feedback_memory: deque = deque(maxlen=100)


def _audit_clip(value: Any, limit: int) -> str:
    return str(value or "")[: max(1, int(limit or 1))]


async def log_audit(
    user_id, action, question="", tools_used=None, tokens=None,
    duration_ms=0, metadata: Optional[dict] = None,
):
    try:
        ts = datetime.now(timezone.utc)
        safe_meta = metadata if isinstance(metadata, dict) else {}
        governance = evaluate_provider_governance(
            provider_used=safe_meta.get("provider_used", ""),
            model_used=safe_meta.get("model_used", ""),
            action=action,
            mode=safe_meta.get("mode", ""),
            tools_used=tools_used,
        )
        safe_meta = {
            **safe_meta,
            "provider_policy_mode": governance["policy_mode"],
            "provider_family": governance["provider_family"],
            "external_provider": governance["external_provider"],
            "data_sensitivity": governance["data_sensitivity"],
            "provider_policy_note": governance["policy_note"],
        }
        audit_entity = {
                "PartitionKey": ts.strftime("%Y-%m"),
                "RowKey": f"{ts.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}",
                "UserId": user_id or "anon",
                "Action": _audit_clip(action, 120),
                "Question": _audit_clip(question, 500),
                "ToolsUsed": ",".join(tools_used) if tools_used else "",
                "TotalTokens": tokens.get("total_tokens", 0) if tokens else 0,
                "DurationMs": int(duration_ms or 0),
                "Timestamp": ts.isoformat(),
                "Mode": _audit_clip(safe_meta.get("mode", ""), 32),
                "ModelUsed": _audit_clip(safe_meta.get("model_used", ""), 160),
                "ProviderUsed": _audit_clip(safe_meta.get("provider_used", ""), 80),
                "ProviderFamily": _audit_clip(safe_meta.get("provider_family", ""), 64),
                "ExternalProvider": _audit_clip(safe_meta.get("external_provider", ""), 8),
                "DataSensitivity": _audit_clip(safe_meta.get("data_sensitivity", ""), 32),
                "PolicyMode": _audit_clip(safe_meta.get("provider_policy_mode", ""), 32),
                "ConversationId": _audit_clip(safe_meta.get("conversation_id", ""), 128),
                "Confidence": _audit_clip(safe_meta.get("confidence", ""), 32),
                "MetadataJson": _audit_clip(json.dumps(safe_meta, ensure_ascii=False, default=str), 4000),
        }
        # SEC-08: Encrypt sensitive fields at rest
        for field in AUDIT_SENSITIVE_FIELDS:
            if field in audit_entity and audit_entity[field]:
                audit_entity[field] = encrypt_field(str(audit_entity[field]))
        await table_insert("AuditLog", audit_entity)
    except Exception as e:
        logger.error("[App] log_audit failed: %s", e)
