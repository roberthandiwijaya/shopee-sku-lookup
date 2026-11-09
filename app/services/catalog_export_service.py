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


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def now():
    return datetime.now(TZ_GMT7)


async def _serialize(session):
    # Transaction-scoped mutex also serializes queue-cap checks across app processes.
    if session.bind.dialect.name == "postgresql":
        await session.execute(text("SELECT pg_advisory_xact_lock(73420712)"))


async def create_job(session, options, owner):
    codes = list(dict.fromkeys(normalize_family_code(code) for code in options["family_codes"]))
    if not codes or len(codes) > 250 or any(not code or len(code) > 100 for code in codes):
        raise ValueError("Select 1–250 valid family codes (maximum 100 characters each).")
    options = {**options, "family_codes": codes}
    await _serialize(session)
    active = (CatalogExportJob.status.in_(["pending", "running"])) & (CatalogExportJob.expires_at > now())
    queued = await session.scalar(select(func.count()).select_from(CatalogExportJob).where(active))
    owner_active = await session.scalar(select(func.count()).select_from(CatalogExportJob).where(active, CatalogExportJob.owner_hash == owner))
    recent = await session.scalar(select(func.count()).select_from(CatalogExportJob).where(
        CatalogExportJob.owner_hash == owner, CatalogExportJob.created_at > now() - timedelta(hours=1)))
    if queued >= settings.catalog_export_queue_limit or owner_active >= 2 or recent >= 10:
        raise QueueFull("Export queue is busy or your export limit was reached. Please try again later.")
    token = secrets.token_urlsafe(32)
    job = CatalogExportJob(id=secrets.token_hex(16), token_hash=digest(token), owner_hash=owner,
        options=options, status="pending", progress=0, total=len(codes), attempts=0,
        created_at=now(), expires_at=now() + timedelta(hours=settings.catalog_export_retention_hours))
    session.add(job)
    await session.commit()
    return job, token


async def authorized_job(session, job_id, token):
    job = await session.get(CatalogExportJob, job_id)
    if not job or not token or not secrets.compare_digest(job.token_hash, digest(token)):
        return None
    return job


async def claim_job(session, worker_id):
    await _serialize(session)
    await session.execute(update(CatalogExportJob).where(
        CatalogExportJob.status == "running", CatalogExportJob.lease_until < now(),
        CatalogExportJob.attempts >= 3,
    ).values(status="failed", error="Export worker stopped repeatedly. Please generate a new export."))
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
    result = await session.execute(update(CatalogExportJob).where(
        CatalogExportJob.id == job_id, CatalogExportJob.worker_id == worker_id,
        CatalogExportJob.status == "running", CatalogExportJob.lease_until > now(),
        CatalogExportJob.expires_at > now(),
    ).values(**values).execution_options(synchronize_session=False))
    await session.commit()
    return bool(result.rowcount)


async def cancel_job(session, job):
    await session.execute(update(CatalogExportJob).where(
        CatalogExportJob.id == job.id, CatalogExportJob.status.in_(["pending", "running"])
    ).values(status="cancelled"))
    await session.commit()


def safe_directory(relative):
    root = Path(settings.catalog_export_dir).resolve()
    candidate = (root / relative).resolve()
    if candidate == root or root not in candidate.parents:
        raise ValueError("Invalid export path")
    return candidate


async def cleanup_jobs(session):
    expired = (await session.scalars(select(CatalogExportJob).where(
        CatalogExportJob.expires_at < now(), CatalogExportJob.status != "expired"))).all()
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
