from datetime import datetime, timedelta

from sqlalchemy import case, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.webhook import OrderRefreshJob
from app.models.product import TZ_GMT7

SOURCE_WEBHOOK = 1
SOURCE_POLL = 2
SOURCE_INCOMPLETE = 4
SOURCE_BACKFILL = 8
SOURCE_RECONCILE = 16

PRIORITY_WEBHOOK = 100
PRIORITY_POLL = 80
PRIORITY_INCOMPLETE = 40
PRIORITY_BACKFILL = 20
PRIORITY_RECONCILE = 10


async def enqueue_refresh(
    session: AsyncSession,
    shop_id: int,
    order_sn: str,
    source_flag: int,
    priority: int,
    package_number: str | None = None,
    tracking_number: str | None = None,
    source_event_at: datetime | None = None,
    backfill_cycle_id: str | None = None,
    reconcile_cycle_id: str | None = None,
    revive_dead: bool = True,
) -> OrderRefreshJob:
    now = datetime.now(TZ_GMT7)
    if session.bind and session.bind.dialect.name == "postgresql":
        stmt = pg_insert(OrderRefreshJob).values(
            shop_id=shop_id,
            order_sn=order_sn,
            package_number_hint=package_number,
            tracking_number_hint=tracking_number,
            source_event_at=source_event_at,
            source_flags=source_flag,
            priority=priority,
            request_version=1,
            status="pending",
            attempts=0,
            available_at=now,
            backfill_cycle_id=backfill_cycle_id,
            reconcile_cycle_id=reconcile_cycle_id,
            created_at=now,
            updated_at=now,
        )
        excluded = stmt.excluded
        status = case(
            (OrderRefreshJob.status == "leased", OrderRefreshJob.status),
            (
                or_(OrderRefreshJob.status != "dead", revive_dead),
                "pending",
            ),
            else_=OrderRefreshJob.status,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["shop_id", "order_sn"],
            set_={
                "package_number_hint": func.coalesce(excluded.package_number_hint, OrderRefreshJob.package_number_hint),
                "tracking_number_hint": func.coalesce(excluded.tracking_number_hint, OrderRefreshJob.tracking_number_hint),
                "source_event_at": func.greatest(excluded.source_event_at, OrderRefreshJob.source_event_at),
                "source_flags": OrderRefreshJob.source_flags.op("|")(source_flag),
                "priority": func.greatest(OrderRefreshJob.priority, priority),
                "request_version": OrderRefreshJob.request_version + 1,
                "status": status,
                "available_at": case(
                    (OrderRefreshJob.status == "leased", OrderRefreshJob.available_at),
                    else_=now,
                ),
                "attempts": case(
                    (OrderRefreshJob.status == "leased", OrderRefreshJob.attempts),
                    else_=0,
                ),
                "backfill_cycle_id": func.coalesce(excluded.backfill_cycle_id, OrderRefreshJob.backfill_cycle_id),
                "reconcile_cycle_id": func.coalesce(excluded.reconcile_cycle_id, OrderRefreshJob.reconcile_cycle_id),
                "updated_at": now,
            },
        ).returning(OrderRefreshJob)
        return (await session.execute(stmt)).scalar_one()

    result = await session.execute(
        select(OrderRefreshJob).where(
            OrderRefreshJob.shop_id == shop_id,
            OrderRefreshJob.order_sn == order_sn,
        )
    )
    job = result.scalar_one_or_none()
    if job is None:
        job = OrderRefreshJob(
            shop_id=shop_id,
            order_sn=order_sn,
            source_flags=source_flag,
            priority=priority,
            status="pending",
            attempts=0,
            available_at=now,
            request_version=1,
            created_at=now,
            updated_at=now,
        )
        session.add(job)
        await session.flush()
    else:
        job.source_flags |= source_flag
        job.priority = max(job.priority, priority)
        job.request_version += 1
        if job.status != "leased" and (job.status != "dead" or revive_dead):
            job.status = "pending"
            job.available_at = now
            job.attempts = 0
    job.package_number_hint = package_number or job.package_number_hint
    job.tracking_number_hint = tracking_number or job.tracking_number_hint
    job.source_event_at = max(
        [value for value in (job.source_event_at, source_event_at) if value is not None],
        default=None,
    )
    job.backfill_cycle_id = backfill_cycle_id or job.backfill_cycle_id
    job.reconcile_cycle_id = reconcile_cycle_id or job.reconcile_cycle_id
    job.updated_at = now
    return job


async def claim_refresh_jobs(
    session: AsyncSession,
    worker_id: str,
    limit: int | None = None,
) -> list[OrderRefreshJob]:
    now = datetime.now(TZ_GMT7)
    stmt = (
        select(OrderRefreshJob)
        .where(
            or_(
                (
                    OrderRefreshJob.status.in_(["pending", "retry"])
                    & (OrderRefreshJob.available_at <= now)
                ),
                (
                    (OrderRefreshJob.status == "leased")
                    & (OrderRefreshJob.lease_expires_at < now)
                ),
            )
        )
        .order_by(OrderRefreshJob.priority.desc(), OrderRefreshJob.available_at, OrderRefreshJob.id)
        .limit(limit or settings.order_refresh_worker_batch_size)
        .with_for_update(skip_locked=True)
    )
    jobs = list((await session.execute(stmt)).scalars())
    lease_expires = now + timedelta(seconds=settings.order_refresh_lease_seconds)
    for job in jobs:
        job.status = "leased"
        job.lease_owner = worker_id
        job.lease_expires_at = lease_expires
        job.claimed_version = job.request_version
        job.attempts += 1
        job.last_started_at = now
        job.updated_at = now
    await session.commit()
    return jobs


async def complete_refresh_job(
    session: AsyncSession, job_id: int, worker_id: str
) -> bool:
    job = await session.get(OrderRefreshJob, job_id, with_for_update=True)
    if job is None or job.lease_owner != worker_id or job.status != "leased":
        return False
    now = datetime.now(TZ_GMT7)
    if job.request_version > (job.claimed_version or 0):
        job.status = "pending"
        job.available_at = now
    else:
        job.status = "completed"
        job.completed_at = now
        job.last_success_at = now
    job.lease_owner = None
    job.lease_expires_at = None
    job.last_error = None
    job.updated_at = now
    await session.commit()
    return True


async def retry_refresh_job(
    session: AsyncSession, job_id: int, worker_id: str, error: str
) -> bool:
    job = await session.get(OrderRefreshJob, job_id, with_for_update=True)
    if job is None or job.lease_owner != worker_id or job.status != "leased":
        return False
    now = datetime.now(TZ_GMT7)
    if job.attempts >= settings.order_refresh_max_attempts:
        job.status = "dead"
    else:
        delay = min(
            settings.order_refresh_retry_base_seconds * (2 ** max(0, job.attempts - 1)),
            1800,
        )
        job.status = "retry"
        job.available_at = now + timedelta(seconds=delay)
    job.lease_owner = None
    job.lease_expires_at = None
    job.last_error = error[:2000]
    job.updated_at = now
    await session.commit()
    return True
