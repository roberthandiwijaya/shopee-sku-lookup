from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select, update

from app.config import settings
from app.models.catalog_export import CatalogExportJob
from app.models.catalog_rate_bucket import CatalogRateBucket
from app.models.product import TZ_GMT7
from app.services import catalog_export_service as jobs
from app.services.catalog_admission_service import check_preflight
from tests.conftest import TestSession


@pytest.mark.asyncio
async def test_expired_running_entry_stops_blocking_admission_after_safe_claim_cleanup(clock):
    async with TestSession() as session:
        job = await accepted(session, terminal=False)
        await jobs.claim_job(session, 'worker')
        await session.execute(update(CatalogExportJob).where(CatalogExportJob.id == job.id).values(
            expires_at=clock[0]-timedelta(seconds=1), lease_until=clock[0]-timedelta(seconds=1)))
        await session.commit()
        assert await jobs.claim_job(session, 'new-worker') is None
        await session.refresh(job)
        assert job.status == 'expired'



@pytest.fixture
def clock(monkeypatch, tmp_path):
    current = [datetime(2026, 11, 9, 12, 0, tzinfo=TZ_GMT7)]
    monkeypatch.setattr(jobs, "now", lambda: current[0])
    monkeypatch.setattr(settings, "catalog_export_dir", str(tmp_path))
    return current


async def accepted(session, owner="owner", network="network", count=1, terminal=True):
    job, _ = await jobs.create_job(session, {"family_codes": [f"AA-{i:04}" for i in range(count)]}, owner, network)
    if terminal:
        job.status = "cancelled"
        await session.commit()
    return job


@pytest.mark.asyncio
async def test_cooldown_denials_do_not_write_or_extend(clock):
    async with TestSession() as session:
        first = await accepted(session)
        clock[0] += timedelta(seconds=10)
        with pytest.raises(jobs.RateLimited) as denied:
            await accepted(session)
        assert denied.value.reason == "owner_cooldown"
        assert denied.value.retry_after == 20
        assert await session.scalar(select(func.count()).select_from(CatalogExportJob)) == 1
        clock[0] += timedelta(seconds=20)
        await accepted(session)
        assert first.requested_family_count == 1


@pytest.mark.asyncio
async def test_active_capacity_and_legacy_network_optional(clock):
    async with TestSession() as session:
        await accepted(session, network=None, terminal=False)
        clock[0] += timedelta(seconds=31)
        with pytest.raises(jobs.RateLimited) as denied:
            await accepted(session, network=None)
        assert denied.value.reason == "owner_active"
        assert denied.value.retry_after == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["owner", "network"])
async def test_hourly_job_limit_and_exact_boundary(clock, monkeypatch, scope):
    monkeypatch.setattr(settings, f"catalog_export_{scope}_hourly_limit", 2)
    async with TestSession() as session:
        await accepted(session)
        clock[0] += timedelta(seconds=31)
        await accepted(session, owner="other" if scope == "network" else "owner")
        clock[0] += timedelta(seconds=31)
        with pytest.raises(jobs.RateLimited) as denied:
            await accepted(session, owner="third" if scope == "network" else "owner")
        assert denied.value.reason == f"{scope}_hourly"
        assert denied.value.retry_after == 3538
        clock[0] += timedelta(seconds=3538)
        await accepted(session, owner="third" if scope == "network" else "owner")


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["owner", "network"])
async def test_weighted_family_reset_uses_enough_expiring_cost(clock, monkeypatch, scope):
    monkeypatch.setattr(settings, f"catalog_export_{scope}_family_hourly_limit", 500)
    async with TestSession() as session:
        job = await accepted(session, count=100)
        job.total = 0  # Worker output/progress must not change admission cost.
        job.status = "failed"
        await session.commit()
        clock[0] += timedelta(seconds=60)
        await accepted(session, owner="other" if scope == "network" else "owner", count=250)
        clock[0] += timedelta(seconds=60)
        with pytest.raises(jobs.RateLimited) as denied:
            await accepted(session, owner="third" if scope == "network" else "owner", count=250)
        assert denied.value.reason == f"{scope}_family_hourly"
        assert denied.value.retry_after == 3480
        # A 250-family request needs 100 of 350 charged families to expire.
        clock[0] += timedelta(seconds=3480)
        await accepted(session, owner="third" if scope == "network" else "owner", count=250)


@pytest.mark.asyncio
async def test_weighted_retry_waits_for_multiple_expiries(clock):
    async with TestSession() as session:
        await accepted(session, count=100)
        clock[0] += timedelta(seconds=60)
        await accepted(session, count=200)
        clock[0] += timedelta(seconds=60)
        await accepted(session, count=200)
        clock[0] += timedelta(seconds=60)
        with pytest.raises(jobs.RateLimited) as denied:
            await accepted(session, count=250)
        # Expiring the oldest 100 alone leaves 400 + 250 > 500.
        assert denied.value.reason == "owner_family_hourly"
        assert denied.value.retry_after == 3480


@pytest.mark.asyncio
async def test_queue_limit_is_global(clock, monkeypatch):
    monkeypatch.setattr(settings, "catalog_export_queue_limit", 1)
    async with TestSession() as session:
        await accepted(session, terminal=False)
        with pytest.raises(jobs.RateLimited) as denied:
            await accepted(session, owner="other", network="other-network")
        assert denied.value.reason == "queue_full"


@pytest.mark.asyncio
async def test_retry_after_is_maximum_of_violations(clock, monkeypatch):
    monkeypatch.setattr(settings, "catalog_export_owner_hourly_limit", 1)
    async with TestSession() as session:
        await accepted(session, terminal=False)
        clock[0] += timedelta(seconds=1)
        with pytest.raises(jobs.RateLimited) as denied:
            await accepted(session)
        assert denied.value.reason == "owner_hourly"
        assert denied.value.retry_after == 3599


@pytest.mark.asyncio
async def test_preflight_fixed_minute_denial_no_writes_and_boundary(clock, monkeypatch):
    monkeypatch.setattr(settings, "catalog_export_preflight_owner_per_minute", 1)
    async with TestSession() as session:
        clock[0] += timedelta(seconds=59, microseconds=100000)
        await check_preflight(session, "owner", "network")
        with pytest.raises(jobs.RateLimited) as denied:
            await check_preflight(session, "owner", "network")
        assert denied.value.reason == "preflight_owner"
        assert denied.value.retry_after == 1
        assert (await session.scalars(select(CatalogRateBucket.count))).all() == [1, 1]
        assert await session.scalar(select(func.count()).select_from(CatalogExportJob)) == 0
        clock[0] += timedelta(seconds=1)
        await check_preflight(session, "owner", "network")


@pytest.mark.asyncio
async def test_shared_preflight_network_limit(clock, monkeypatch):
    monkeypatch.setattr(settings, "catalog_export_preflight_network_per_minute", 1)
    async with TestSession() as session:
        await check_preflight(session, "one", "network")
        with pytest.raises(jobs.RateLimited) as denied:
            await check_preflight(session, "two", "network")
        assert denied.value.reason == "preflight_network"
        assert await session.scalar(select(func.count()).select_from(CatalogRateBucket)) == 2


@pytest.mark.asyncio
async def test_cleanup_erases_network_independently_of_expiry_and_prunes(clock):
    async with TestSession() as session:
        job = await accepted(session)
        await session.execute(update(CatalogExportJob).where(CatalogExportJob.id == job.id).values(
            created_at=clock[0] - timedelta(hours=24), expires_at=clock[0] + timedelta(days=2)))
        session.add(CatalogRateBucket(scope="owner", identity_hash="old", minute=clock[0] - timedelta(minutes=3), count=1))
        session.add(CatalogRateBucket(scope="owner", identity_hash="new", minute=clock[0], count=1))
        await session.commit()
        await jobs.cleanup_jobs(session)
        await session.refresh(job)
        assert job.network_hash is None
        assert job.status == "cancelled"
        assert (await session.scalars(select(CatalogRateBucket.identity_hash))).all() == ["new"]
