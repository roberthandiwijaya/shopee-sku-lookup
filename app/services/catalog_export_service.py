"""Durable public catalog jobs with capability-token access and fenced worker leases."""
import asyncio
import hashlib
import secrets
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import func, or_, select, text, update

from app.config import settings
from app.models.catalog_export import CatalogExportJob
from app.models.product import TZ_GMT7
from app.services.product_service import normalize_family_code


class QueueFull(ValueError):
    pass


class RateLimited(QueueFull):
    def __init__(self, reason, retry_after, detail):
        self.reason = reason
        self.retry_after = max(1, int(retry_after))
        self.detail = detail
        super().__init__(detail)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def now():
    return datetime.now(TZ_GMT7)


async def _serialize(session):
    # Transaction-scoped mutex also serializes queue-cap checks across app processes.
    if session.bind.dialect.name == "postgresql":
        await session.execute(text("SELECT pg_advisory_xact_lock(73420712)"))


async def create_job(session, options, owner, network=None):
    from app.services.catalog_admission_service import check_job_admission, prune_private_state

    codes = list(dict.fromkeys(normalize_family_code(code) for code in options["family_codes"]))
    if not codes or len(codes) > 250 or any(not code or len(code) > 100 for code in codes):
        raise ValueError("Select 1–250 valid family codes (maximum 100 characters each).")
    options = {**options, "family_codes": codes}
    await _serialize(session)
    current = now()
    await check_job_admission(session, owner, network, len(codes), current)
    await prune_private_state(session, current)
    token = secrets.token_urlsafe(32)
    job = CatalogExportJob(id=secrets.token_hex(16), token_hash=digest(token), owner_hash=owner,
        network_hash=network, requested_family_count=len(codes), cancel_requested=False,
        options=options, status="pending", progress=0, total=len(codes), attempts=0,
        created_at=current, expires_at=current + timedelta(hours=settings.catalog_export_retention_hours))
    session.add(job)
    await session.commit()
    return job, token


async def authorized_job(session, job_id, token):
    job = await session.get(CatalogExportJob, job_id)
    if not job or not token or not secrets.compare_digest(job.token_hash, digest(token)):
        return None
    return job


async def claim_job(session, worker_id):
    # Caller must hold the dedicated renderer occupancy lock, including while
    # reclaiming stale leases. Admission serialization is a different lock.
    await _serialize(session)
    # An expired unclaimed queue entry no longer reserves capacity.
    await session.execute(update(CatalogExportJob).where(
        CatalogExportJob.status == "pending", CatalogExportJob.expires_at <= now(),
    ).values(status="expired").execution_options(synchronize_session=False))
    # An abandoned running job whose retention ended is terminal only while
    # the physical renderer mutex is held by this claimant.
    await session.execute(update(CatalogExportJob).where(
        CatalogExportJob.status == "running", CatalogExportJob.expires_at <= now(),
        CatalogExportJob.lease_until < now(),
    ).values(status="expired").execution_options(synchronize_session=False))
    await session.execute(update(CatalogExportJob).where(
        CatalogExportJob.status.in_(["pending", "running"]),
        CatalogExportJob.cancel_requested.is_(True),
        or_(CatalogExportJob.status == "pending", CatalogExportJob.lease_until < now()),
    ).values(status="cancelled").execution_options(synchronize_session=False))
    await session.execute(update(CatalogExportJob).where(
        CatalogExportJob.status == "running", CatalogExportJob.lease_until < now(),
        CatalogExportJob.attempts >= 3,
    ).values(status="failed", error="Export worker stopped repeatedly. Please generate a new export.").execution_options(synchronize_session=False))
    running = await session.scalar(select(func.count()).select_from(CatalogExportJob).where(
        CatalogExportJob.status == "running", CatalogExportJob.lease_until > now(),
        CatalogExportJob.expires_at > now()))
    if running:
        await session.commit()
        return None
    stmt = select(CatalogExportJob).where(
        CatalogExportJob.expires_at > now(), CatalogExportJob.attempts < 3,
        or_(CatalogExportJob.status == "pending",
            (CatalogExportJob.status == "running") & (CatalogExportJob.lease_until < now())),
    ).order_by(CatalogExportJob.created_at).limit(1).with_for_update(skip_locked=True)
    job = await session.scalar(stmt)
    if job:
        job.status = "running"
        job.worker_id = worker_id
        job.attempts += 1
        job.progress = 0
        job.lease_until = now() + timedelta(seconds=120)
        job.error = None
    await session.commit()
    return job


async def update_job(session, job_id, worker_id, **values):
    conditions = [
        CatalogExportJob.id == job_id, CatalogExportJob.worker_id == worker_id,
        CatalogExportJob.status == "running", CatalogExportJob.lease_until > now(),
        CatalogExportJob.expires_at > now(),
    ]
    # Cancellation must not stop lease renewal while a non-killable thread
    # drains, but must fence progress/publication of a newly finished result.
    if set(values) != {"lease_until"} and values.get("status") not in {"failed", "cancelled"}:
        conditions.append(CatalogExportJob.cancel_requested.is_(False))
    result = await session.execute(update(CatalogExportJob).where(
        *conditions,
    ).values(**values).execution_options(synchronize_session=False))
    await session.commit()
    return bool(result.rowcount)


async def cancel_job(session, job):
    await session.execute(update(CatalogExportJob).where(
        CatalogExportJob.id == job.id, CatalogExportJob.status == "pending"
    ).values(status="cancelled", cancel_requested=True).execution_options(synchronize_session=False))
    await session.execute(update(CatalogExportJob).where(
        CatalogExportJob.id == job.id, CatalogExportJob.status == "running"
    ).values(cancel_requested=True).execution_options(synchronize_session=False))
    await session.commit()
    await session.refresh(job)
    return job


def safe_directory(relative):
    root = Path(settings.catalog_export_dir).resolve()
    candidate = (root / relative).resolve()
    if candidate == root or root not in candidate.parents:
        raise ValueError("Invalid export path")
    return candidate


async def cleanup_jobs(session):
    from app.services.catalog_admission_service import prune_private_state

    await prune_private_state(session, now())
    await session.commit()
    expired = (await session.scalars(select(CatalogExportJob).where(
        CatalogExportJob.expires_at < now(), CatalogExportJob.status.not_in(["expired", "running"])))).all()
    for job in expired:
        await session.execute(update(CatalogExportJob).where(CatalogExportJob.id == job.id).values(status="expired", result=None))
        await session.commit()
        await asyncio.to_thread(shutil.rmtree, safe_directory(job.id), True)
    # Remove orphan attempt directories from crashed workers after a full retention period.
    root = Path(settings.catalog_export_dir)
    cutoff = now().timestamp() - settings.catalog_export_retention_hours * 3600
    if root.exists():
        for directory in root.iterdir():
            if directory.is_dir() and directory.stat().st_mtime < cutoff:
                job = await session.get(CatalogExportJob, directory.name)
                if not job or job.status in {"expired", "failed", "cancelled"}:
                    await asyncio.to_thread(shutil.rmtree, directory, True)
