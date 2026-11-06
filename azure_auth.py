"""Azure authentication helpers with AAD fallback for local/dev runtimes."""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from typing import Optional

from azure.identity import DefaultAzureCredential

logger = logging.getLogger(__name__)

AZURE_OPENAI_AAD_SCOPE = "https://cognitiveservices.azure.com/.default"
AZURE_SEARCH_AAD_SCOPE = "https://search.azure.com/.default"

_credential: Optional[DefaultAzureCredential] = None
_credential_lock = asyncio.Lock()
_token_cache: dict[str, tuple[str, float]] = {}
_token_locks: dict[str, asyncio.Lock] = {}
_search_key_cache: dict[str, str] = {}


async def _get_credential() -> DefaultAzureCredential:
    global _credential
    async with _credential_lock:
        if _credential is None:
            _credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
        return _credential


def _token_lock(scope: str) -> asyncio.Lock:
    lock = _token_locks.get(scope)
    if lock is None:
        lock = asyncio.Lock()
        _token_locks[scope] = lock
    return lock


async def get_bearer_token(scope: str) -> str:
    now = time.time()
    cached = _token_cache.get(scope)
    if cached and cached[1] > now + 300:
        return cached[0]

    async with _token_lock(scope):
        cached = _token_cache.get(scope)
        if cached and cached[1] > time.time() + 300:
            return cached[0]

        token_value: str | None = None
        expires_on: float = 0.0

        try:
            credential = await _get_credential()
            token = await asyncio.to_thread(credential.get_token, scope)
            token_value = str(token.token or "").strip()
            expires_on = float(token.expires_on)
        except Exception as credential_exc:
            logger.info("[AzureAuth] DefaultAzureCredential token lookup failed for %s: %s", scope, credential_exc)
            token_value, expires_on = await _get_az_cli_token(scope)

        if not token_value:
            raise RuntimeError(f"Unable to resolve Azure bearer token for scope {scope}")

        _token_cache[scope] = (token_value, expires_on)
        return token_value


def _scope_to_resource(scope: str) -> str:
    raw = str(scope or "").strip()
    if raw.endswith("/.default"):
        return raw[: -len("/.default")]
    return raw


def _parse_cli_expiry(payload: dict) -> float:
    raw = payload.get("expires_on")
    if isinstance(raw, (int, float)):
        return float(raw)
    raw = str(payload.get("expiresOn") or "").strip()
    if raw:
        try:
            from datetime import datetime

            return datetime.fromisoformat(raw).timestamp()
        except Exception:
            pass
    return time.time() + 1800


def _read_az_cli_token(scope: str) -> tuple[str, float]:
    resource = _scope_to_resource(scope)
    proc = subprocess.run(
        ["az", "account", "get-access-token", "--resource", resource, "--output", "json"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(proc.stdout or "{}")
    token = str(payload.get("accessToken") or payload.get("access_token") or "").strip()
    return token, _parse_cli_expiry(payload)


async def _get_az_cli_token(scope: str) -> tuple[str, float]:
    try:
        return await asyncio.to_thread(_read_az_cli_token, scope)
    except Exception as exc:
        logger.warning("[AzureAuth] az CLI token lookup failed for %s: %s", scope, exc)
        return "", 0.0


def _read_search_admin_key(service_name: str) -> str:
    service = str(service_name or "").strip()
    if not service:
        return ""

    resource_group_proc = subprocess.run(
        [
            "az",
            "resource",
            "list",
            "--name",
            service,
            "--resource-type",
            "Microsoft.Search/searchServices",
            "--query",
            "[0].resourceGroup",
            "--output",
            "tsv",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    resource_group = str(resource_group_proc.stdout or "").strip()
    if not resource_group:
        raise RuntimeError(f"Search resource group not found for {service}")

    key_proc = subprocess.run(
        [
            "az",
            "search",
            "admin-key",
            "show",
            "--resource-group",
            resource_group,
            "--service-name",
            service,
            "--query",
            "primaryKey",
            "--output",
            "tsv",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return str(key_proc.stdout or "").strip()


async def _get_search_admin_key(service_name: str) -> str:
    service = str(service_name or "").strip()
    if not service:
        return ""
    cached = _search_key_cache.get(service)
    if cached:
        return cached
    try:
        secret = await asyncio.to_thread(_read_search_admin_key, service)
    except Exception as exc:
        logger.warning("[AzureAuth] Search admin key lookup failed for %s: %s", service, exc)
        return ""
    if secret:
        _search_key_cache[service] = secret
    return secret


def _static_auth_headers(
    auth_mode: str,
    auth_header: str,
    auth_value: str,
    default_header: str,
) -> dict[str, str]:
    mode = str(auth_mode or "").strip().lower()
    header_name = str(auth_header or "").strip() or default_header
    secret = str(auth_value or "").strip()
    if not secret or mode in {"none", "disabled"}:
        return {}
    if mode == "bearer":
        if not secret.lower().startswith("bearer "):
            secret = f"Bearer {secret}"
        return {header_name or "Authorization": secret}
    return {header_name: secret}


async def build_azure_openai_auth_headers(
    *,
    auth_mode: str,
    auth_header: str,
    auth_value: str,
    default_header: str = "api-key",
) -> dict[str, str]:
    static_headers = _static_auth_headers(auth_mode, auth_header, auth_value, default_header)
    if static_headers:
        return static_headers
    try:
        token = await get_bearer_token(AZURE_OPENAI_AAD_SCOPE)
    except Exception as exc:
        logger.warning("[AzureAuth] Azure OpenAI bearer token unavailable: %s", exc)
        return {}
    return {"Authorization": f"Bearer {token}"}


async def build_search_auth_headers(*, api_key: str, service_name: str = "") -> dict[str, str]:
    secret = str(api_key or "").strip()
    if secret:
        return {"api-key": secret}
    secret = await _get_search_admin_key(service_name)
    if secret:
        return {"api-key": secret}
    try:
        token = await get_bearer_token(AZURE_SEARCH_AAD_SCOPE)
    except Exception as exc:
        logger.warning("[AzureAuth] Azure Search bearer token unavailable: %s", exc)
        return {}
    return {"Authorization": f"Bearer {token}"}


async def close_azure_auth() -> None:
    global _credential
    async with _credential_lock:
        if _credential is not None:
            await asyncio.to_thread(_credential.close)
            _credential = None
    _token_cache.clear()
    _search_key_cache.clear()
