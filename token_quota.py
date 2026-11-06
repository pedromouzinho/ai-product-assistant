"""Per-tier token quota enforcement backed by Azure Table Storage shards."""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from storage import table_delete, table_insert, table_merge, table_query
from utils import odata_escape

TOKEN_QUOTA_TABLE = "TokenQuota"
_INSTANCE_ID = (
    str(
        os.getenv("WEBSITE_INSTANCE_ID")
        or os.getenv("HOSTNAME")
        or f"pid-{os.getpid()}"
    )
    .strip()
    .replace("/", "_")
    .replace("\\", "_")
    [:120]
)


class TokenQuota:
    """Async token quota tracker with distributed per-instance shards."""

    def __init__(self, tier: str, hourly_limit: int = 0, daily_limit: int = 0):
        self._lock = asyncio.Lock()
        self._tier = str(tier or "").strip().lower() or "unknown"
        self._hourly_limit = max(0, int(hourly_limit or 0))
        self._daily_limit = max(0, int(daily_limit or 0))
        self._hourly_shards: dict[str, int] = {}
        self._daily_shards: dict[str, int] = {}

    @staticmethod
    def _hour_key() -> str:
        t = time.gmtime()
        return f"{t.tm_year}-{t.tm_yday:03d}-{t.tm_hour:02d}"

    @staticmethod
    def _day_key() -> str:
        t = time.gmtime()
        return f"{t.tm_year}-{t.tm_yday:03d}"

    def _hour_partition(self, hour_key: str) -> str:
        return f"{self._tier}::hour::{hour_key}"

    def _day_partition(self, day_key: str) -> str:
        return f"{self._tier}::day::{day_key}"

    async def _read_partition_total(self, partition_key: str) -> int:
        rows = await table_query(
            TOKEN_QUOTA_TABLE,
            f"PartitionKey eq '{odata_escape(partition_key)}'",
            top=200,
        )
        total = 0
        for row in rows or []:
            try:
                total += int(row.get("Count", 0) or 0)
            except (ValueError, TypeError):
                continue
        return max(0, total)

    async def _read_instance_count(self, partition_key: str) -> int:
        rows = await table_query(
            TOKEN_QUOTA_TABLE,
            (
                f"PartitionKey eq '{odata_escape(partition_key)}' and "
                f"RowKey eq '{odata_escape(_INSTANCE_ID)}'"
            ),
            top=1,
        )
        if not rows:
            return 0
        try:
            return max(0, int(rows[0].get("Count", 0) or 0))
        except (ValueError, TypeError):
            return 0

    async def _upsert_instance_count(
        self,
        partition_key: str,
        *,
        scope: str,
        window_key: str,
        count: int,
    ) -> None:
        entity = {
            "PartitionKey": partition_key,
            "RowKey": _INSTANCE_ID,
            "Tier": self._tier,
            "Scope": scope,
            "WindowKey": window_key,
            "InstanceId": _INSTANCE_ID,
            "Count": int(max(0, count)),
            "UpdatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        try:
            await table_merge(TOKEN_QUOTA_TABLE, entity)
        except Exception:
            inserted = await table_insert(TOKEN_QUOTA_TABLE, entity)
            if not inserted:
                raise RuntimeError("TokenQuota insert returned False")

    async def check(self) -> tuple[bool, str]:
        """Check if quota allows a request. Returns (allowed, reason)."""
        if not self._hourly_limit and not self._daily_limit:
            return True, ""
        async with self._lock:
            hour_key = self._hour_key()
            day_key = self._day_key()
            hourly_used = 0
            daily_used = 0
            if self._hourly_limit:
                hourly_used = await self._read_partition_total(self._hour_partition(hour_key))
                if hourly_used >= self._hourly_limit:
                    return False, f"Hourly token limit reached ({self._hourly_limit})"
            if self._daily_limit:
                daily_used = await self._read_partition_total(self._day_partition(day_key))
                if daily_used >= self._daily_limit:
                    return False, f"Daily token limit reached ({self._daily_limit})"
            return True, ""

    async def record(self, tokens: int) -> None:
        """Record token usage in this instance's shard."""
        try:
            amount = int(tokens or 0)
        except (ValueError, TypeError):
            amount = 0
        if amount <= 0:
            return
        async with self._lock:
            hour_key = self._hour_key()
            day_key = self._day_key()
            if self._hourly_limit:
                current = self._hourly_shards.get(hour_key)
                if current is None:
                    current = await self._read_instance_count(self._hour_partition(hour_key))
                current += amount
                self._hourly_shards[hour_key] = current
                await self._upsert_instance_count(
                    self._hour_partition(hour_key),
                    scope="hour",
                    window_key=hour_key,
                    count=current,
                )
            if self._daily_limit:
                current = self._daily_shards.get(day_key)
                if current is None:
                    current = await self._read_instance_count(self._day_partition(day_key))
                current += amount
                self._daily_shards[day_key] = current
                await self._upsert_instance_count(
                    self._day_partition(day_key),
                    scope="day",
                    window_key=day_key,
                    count=current,
                )
            self._evict_locked()

    def _evict_locked(self) -> None:
        hour_candidates = []
        day_candidates = []
        for key in self._hourly_shards:
            try:
                year, yday, hour = str(key).split("-")
                hour_candidates.append((int(year), int(yday), int(hour), key))
            except ValueError:
                continue
        for key in self._daily_shards:
            try:
                year, yday = str(key).split("-")
                day_candidates.append((int(year), int(yday), key))
            except ValueError:
                continue

        hour_candidates.sort()
        day_candidates.sort()

        if len(hour_candidates) > 48:
            keep = {item[3] for item in hour_candidates[-48:]}
            self._hourly_shards = {k: v for k, v in self._hourly_shards.items() if k in keep}
        if len(day_candidates) > 8:
            keep = {item[2] for item in day_candidates[-8:]}
            self._daily_shards = {k: v for k, v in self._daily_shards.items() if k in keep}

    async def snapshot(self) -> dict[str, Any]:
        """Return current distributed usage snapshot."""
        async with self._lock:
            hour_key = self._hour_key()
            day_key = self._day_key()
            hourly_used = await self._read_partition_total(self._hour_partition(hour_key)) if self._hourly_limit else 0
            daily_used = await self._read_partition_total(self._day_partition(day_key)) if self._daily_limit else 0
            return {
                "hourly_used": hourly_used,
                "hourly_limit": self._hourly_limit,
                "daily_used": daily_used,
                "daily_limit": self._daily_limit,
                "hour_key": hour_key,
                "day_key": day_key,
                "instance_id": _INSTANCE_ID,
                "local_hourly_shard": self._hourly_shards.get(hour_key, 0),
                "local_daily_shard": self._daily_shards.get(day_key, 0),
            }

    async def reset(self) -> None:
        async with self._lock:
            hour_keys = list(self._hourly_shards.keys())
            day_keys = list(self._daily_shards.keys())
            self._hourly_shards.clear()
            self._daily_shards.clear()
        for hour_key in hour_keys:
            try:
                await table_delete(TOKEN_QUOTA_TABLE, self._hour_partition(hour_key), _INSTANCE_ID)
            except Exception:
                continue
        for day_key in day_keys:
            try:
                await table_delete(TOKEN_QUOTA_TABLE, self._day_partition(day_key), _INSTANCE_ID)
            except Exception:
                continue


class TokenQuotaManager:
    """Manages distributed quotas for multiple tiers."""

    def __init__(self, config: dict[str, dict[str, int]]):
        self._quotas: dict[str, TokenQuota] = {}
        for tier, limits in (config or {}).items():
            safe_tier = str(tier or "").strip()
            self._quotas[safe_tier] = TokenQuota(
                safe_tier,
                hourly_limit=int((limits or {}).get("hourly", 0) or 0),
                daily_limit=int((limits or {}).get("daily", 0) or 0),
            )

    async def check(self, tier: str) -> tuple[bool, str]:
        q = self._quotas.get(str(tier))
        if not q:
            return True, ""
        return await q.check()

    async def record(self, tier: str, tokens: int) -> None:
        q = self._quotas.get(str(tier))
        if q:
            await q.record(tokens)

    async def snapshot(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for tier, quota in sorted(self._quotas.items()):
            result[tier] = await quota.snapshot()
        return result

    async def reset(self, tier: str = "") -> None:
        if tier and tier in self._quotas:
            await self._quotas[tier].reset()
        elif not tier:
            for quota in self._quotas.values():
                await quota.reset()


# Singleton — initialised in app.py startup or lazily
token_quota_manager: TokenQuotaManager | None = None
