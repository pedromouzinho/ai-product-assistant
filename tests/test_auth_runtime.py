import time
from datetime import datetime, timezone

import pytest

import auth
import auth_runtime


@pytest.fixture(autouse=True)
def _reset_auth_runtime_state():
    with auth._blacklist_lock:
        auth._token_blacklist.clear()
    with auth._user_invalidated_lock:
        auth._user_invalidated_before.clear()
    with auth._login_attempts_lock:
        auth._login_attempts.clear()
    auth_runtime.clear_runtime_caches()
    yield
    with auth._blacklist_lock:
        auth._token_blacklist.clear()
    with auth._user_invalidated_lock:
        auth._user_invalidated_before.clear()
    with auth._login_attempts_lock:
        auth._login_attempts.clear()
    auth_runtime.clear_runtime_caches()


@pytest.fixture
def fake_auth_state_backend(monkeypatch):
    rows = {}

    async def _load_auth_state_row(partition_key: str, row_key: str) -> dict:
        return dict(rows.get((partition_key, row_key), {}))

    async def _upsert_auth_state_row(entity: dict) -> None:
        key = (entity["PartitionKey"], entity["RowKey"])
        current = dict(rows.get(key, {}))
        current.update(entity)
        rows[key] = current

    monkeypatch.setattr(auth_runtime, "_load_auth_state_row", _load_auth_state_row)
    monkeypatch.setattr(auth_runtime, "_upsert_auth_state_row", _upsert_auth_state_row)
    return rows


@pytest.mark.asyncio
async def test_revoked_token_persists_across_local_cache_clear(fake_auth_state_backend):
    token = auth.jwt_encode({"sub": "persistent_user"})
    payload = auth.jwt_decode(token)
    exp = datetime.fromisoformat(payload["exp"])

    await auth_runtime.revoke_token_persistent(payload["jti"], exp, username="persistent_user")

    with auth._blacklist_lock:
        auth._token_blacklist.clear()
    auth_runtime.clear_runtime_caches()

    resolved, error = await auth_runtime.validate_request_token(token)
    assert resolved is None
    assert "revoked" in error.lower()


@pytest.mark.asyncio
async def test_user_invalidation_persists_across_local_cache_clear(fake_auth_state_backend):
    token = auth.jwt_encode({"sub": "victim"})

    time.sleep(0.01)
    await auth_runtime.persist_user_invalidation("victim")

    with auth._user_invalidated_lock:
        auth._user_invalidated_before.clear()
    auth_runtime.clear_runtime_caches()

    resolved, error = await auth_runtime.validate_request_token(token)
    assert resolved is None
    assert "invalidated" in error.lower()


@pytest.mark.asyncio
async def test_lockout_persists_across_local_cache_clear(fake_auth_state_backend):
    username = "locked_user"
    for _ in range(auth._MAX_LOGIN_ATTEMPTS):
        await auth_runtime.record_login_failure_persistent(username)

    with auth._login_attempts_lock:
        auth._login_attempts.clear()
    auth_runtime.clear_runtime_caches()

    assert await auth_runtime.is_account_locked_persistent(username)


@pytest.mark.asyncio
async def test_clear_persistent_lockout(fake_auth_state_backend):
    username = "lockout_reset_user"
    for _ in range(auth._MAX_LOGIN_ATTEMPTS):
        await auth_runtime.record_login_failure_persistent(username)

    await auth_runtime.clear_login_failures_persistent(username)
    auth_runtime.clear_runtime_caches()

    assert not await auth_runtime.is_account_locked_persistent(username)
