import pytest

import token_quota


@pytest.fixture
def fake_quota_storage(monkeypatch):
    rows: dict[tuple[str, str], dict] = {}

    def _parse_filter(filter_expr: str) -> tuple[str, str | None]:
        partition = ""
        row = None
        for chunk in (filter_expr or "").split(" and "):
            chunk = chunk.strip()
            if chunk.startswith("PartitionKey eq '"):
                partition = chunk[len("PartitionKey eq '"):-1]
            elif chunk.startswith("RowKey eq '"):
                row = chunk[len("RowKey eq '"):-1]
        return partition, row

    async def fake_table_query(_table_name, filter_expr="", top=100):
        partition, row = _parse_filter(filter_expr)
        result = []
        for (pk, rk), entity in rows.items():
            if partition and pk != partition:
                continue
            if row is not None and rk != row:
                continue
            result.append(dict(entity))
        return result[:top]

    async def fake_table_merge(_table_name, entity):
        key = (str(entity.get("PartitionKey", "")), str(entity.get("RowKey", "")))
        current = dict(rows.get(key, {}))
        current.update(dict(entity))
        rows[key] = current
        return True

    async def fake_table_insert(_table_name, entity):
        key = (str(entity.get("PartitionKey", "")), str(entity.get("RowKey", "")))
        if key in rows:
            return False
        rows[key] = dict(entity)
        return True

    async def fake_table_delete(_table_name, partition_key, row_key):
        rows.pop((str(partition_key), str(row_key)), None)
        return True

    monkeypatch.setattr(token_quota, "table_query", fake_table_query)
    monkeypatch.setattr(token_quota, "table_merge", fake_table_merge)
    monkeypatch.setattr(token_quota, "table_insert", fake_table_insert)
    monkeypatch.setattr(token_quota, "table_delete", fake_table_delete)
    return rows


class TestTokenQuota:
    @pytest.mark.asyncio
    async def test_unlimited_always_allows(self, fake_quota_storage):
        _ = fake_quota_storage
        q = token_quota.TokenQuota("fast", hourly_limit=0, daily_limit=0)
        ok, reason = await q.check()
        assert ok is True
        assert reason == ""

    @pytest.mark.asyncio
    async def test_hourly_limit_enforced(self, fake_quota_storage):
        _ = fake_quota_storage
        q = token_quota.TokenQuota("fast", hourly_limit=100, daily_limit=0)
        await q.record(80)
        ok, _ = await q.check()
        assert ok is True
        await q.record(30)
        ok, reason = await q.check()
        assert ok is False
        assert "Hourly" in reason

    @pytest.mark.asyncio
    async def test_daily_limit_enforced(self, fake_quota_storage):
        _ = fake_quota_storage
        q = token_quota.TokenQuota("fast", hourly_limit=0, daily_limit=200)
        await q.record(200)
        ok, reason = await q.check()
        assert ok is False
        assert "Daily" in reason

    @pytest.mark.asyncio
    async def test_snapshot(self, fake_quota_storage):
        _ = fake_quota_storage
        q = token_quota.TokenQuota("fast", hourly_limit=1000, daily_limit=10000)
        await q.record(500)
        snap = await q.snapshot()
        assert snap["hourly_used"] == 500
        assert snap["daily_used"] == 500
        assert snap["hourly_limit"] == 1000
        assert snap["daily_limit"] == 10000

    @pytest.mark.asyncio
    async def test_reset(self, fake_quota_storage):
        _ = fake_quota_storage
        q = token_quota.TokenQuota("fast", hourly_limit=100, daily_limit=1000)
        await q.record(100)
        ok, _ = await q.check()
        assert ok is False
        await q.reset()
        ok, _ = await q.check()
        assert ok is True


class TestTokenQuotaManager:
    @pytest.mark.asyncio
    async def test_multi_tier(self, fake_quota_storage):
        _ = fake_quota_storage
        mgr = token_quota.TokenQuotaManager(
            {
                "fast": {"hourly": 1000, "daily": 5000},
                "pro": {"hourly": 100, "daily": 500},
            }
        )
        await mgr.record("fast", 500)
        await mgr.record("pro", 100)
        ok_fast, _ = await mgr.check("fast")
        ok_pro, reason = await mgr.check("pro")
        assert ok_fast is True
        assert ok_pro is False
        assert "Hourly" in reason

    @pytest.mark.asyncio
    async def test_unknown_tier_allowed(self, fake_quota_storage):
        _ = fake_quota_storage
        mgr = token_quota.TokenQuotaManager({"fast": {"hourly": 100, "daily": 0}})
        ok, _ = await mgr.check("unknown_tier")
        assert ok is True

    @pytest.mark.asyncio
    async def test_snapshot_all_tiers(self, fake_quota_storage):
        _ = fake_quota_storage
        mgr = token_quota.TokenQuotaManager(
            {
                "fast": {"hourly": 1000, "daily": 5000},
                "standard": {"hourly": 500, "daily": 2000},
            }
        )
        snap = await mgr.snapshot()
        assert "fast" in snap
        assert "standard" in snap

    @pytest.mark.asyncio
    async def test_reset_single_tier(self, fake_quota_storage):
        _ = fake_quota_storage
        mgr = token_quota.TokenQuotaManager(
            {
                "fast": {"hourly": 100, "daily": 500},
                "pro": {"hourly": 100, "daily": 500},
            }
        )
        await mgr.record("fast", 100)
        await mgr.record("pro", 100)
        await mgr.reset("fast")
        ok_fast, _ = await mgr.check("fast")
        ok_pro, _ = await mgr.check("pro")
        assert ok_fast is True
        assert ok_pro is False
