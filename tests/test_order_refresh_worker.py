from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.product import TZ_GMT7
from app.models.webhook import OrderRefreshJob
from app.services import order_refresh_service
from tests.conftest import TestSession


@pytest.mark.asyncio
async def test_enqueue_coalesces_and_claims_once():
    async with TestSession() as session:
        first = await order_refresh_service.enqueue_refresh(
            session, 1, "ORDER-1", order_refresh_service.SOURCE_POLL, 80
        )
        await session.commit()
        second = await order_refresh_service.enqueue_refresh(
            session, 1, "ORDER-1", order_refresh_service.SOURCE_WEBHOOK, 100
        )
        await session.commit()
        assert first.id == second.id
        assert second.request_version == 2

    async with TestSession() as session:
        jobs = await order_refresh_service.claim_refresh_jobs(session, "worker-1")
        assert len(jobs) == 1
        assert jobs[0].status == "leased"


@pytest.mark.asyncio
async def test_expired_lease_is_reclaimed():
    now = datetime.now(TZ_GMT7)
    async with TestSession() as session:
        session.add(
            OrderRefreshJob(
                shop_id=1,
                order_sn="ORDER-1",
                source_flags=1,
                priority=100,
                request_version=1,
                claimed_version=1,
                status="leased",
                attempts=1,
                available_at=now,
                lease_owner="dead-worker",
                lease_expires_at=now - timedelta(seconds=1),
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()

    async with TestSession() as session:
        jobs = await order_refresh_service.claim_refresh_jobs(session, "worker-2")
        assert len(jobs) == 1
        assert jobs[0].lease_owner == "worker-2"


@pytest.mark.asyncio
async def test_newer_request_requeues_on_completion():
    async with TestSession() as session:
        job = await order_refresh_service.enqueue_refresh(
            session, 1, "ORDER-1", 1, 100
        )
        await session.commit()
    async with TestSession() as session:
        claimed = (await order_refresh_service.claim_refresh_jobs(session, "worker"))[0]
    async with TestSession() as session:
        job = await session.get(OrderRefreshJob, claimed.id)
        job.request_version += 1
        await session.commit()
    async with TestSession() as session:
        assert await order_refresh_service.complete_refresh_job(
            session, claimed.id, "worker"
        )
        job = await session.get(OrderRefreshJob, claimed.id)
        assert job.status == "pending"
