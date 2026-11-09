"""Shared admission accounting; callers serialize checks and writes in one transaction."""
import math
from datetime import timedelta

from sqlalchemy import delete, select, update

from app.config import settings
from app.models.catalog_export import CatalogExportJob
from app.models.catalog_rate_bucket import CatalogRateBucket


def _aware(value, current):
    # SQLite drops timezone information; PostgreSQL preserves it.
    return value.replace(tzinfo=current.tzinfo) if value.tzinfo is None else value


def _seconds(expiry, current):
    return max(1, math.ceil((_aware(expiry, current) - current).total_seconds()))


def _release(rows, cost, limit, current):
    """Earliest expiry that frees enough weighted cost for this request."""
    excess = sum(weight for _, weight in rows) + cost - limit
    if excess <= 0:
        return 0
    for created, weight in sorted(rows, key=lambda row: row[0]):
        excess -= weight
        if excess <= 0:
            return _seconds(created + timedelta(hours=1), current)
    return 3600  # A single request exceeds a configured work budget.


async def check_job_admission(session, owner, network, cost, current):
    from app.services.catalog_export_service import RateLimited

    violations = []
    active = (CatalogExportJob.status == "running") | (
        (CatalogExportJob.status == "pending") & (CatalogExportJob.expires_at > current))
    active_jobs = (await session.scalars(select(CatalogExportJob.owner_hash).where(active))).all()
    if len(active_jobs) >= settings.catalog_export_queue_limit:
        violations.append((5, "queue_full", "Export queue is busy. Please try again shortly."))
    if active_jobs.count(owner) >= settings.catalog_export_owner_active_limit:
        violations.append((5, "owner_active", "Your previous export is still active. Wait for it or cancel it."))
    recent = (await session.execute(select(
        CatalogExportJob.owner_hash, CatalogExportJob.network_hash,
        CatalogExportJob.created_at, CatalogExportJob.requested_family_count,
    ).where(
        CatalogExportJob.created_at > current - timedelta(hours=1),
        (CatalogExportJob.owner_hash == owner) | (CatalogExportJob.network_hash == network)
        if network is not None else CatalogExportJob.owner_hash == owner,
    ))).all()
    owner_rows = [(created, count) for who, _, created, count in recent if who == owner]
    if owner_rows:
        latest = max(created for created, _ in owner_rows)
        cooldown = _aware(latest, current) + timedelta(seconds=settings.catalog_export_cooldown_seconds)
        if cooldown > current:
            violations.append((_seconds(cooldown, current), "owner_cooldown", "Please wait before requesting another export."))
    scopes = [("owner", owner_rows, settings.catalog_export_owner_hourly_limit,
               settings.catalog_export_owner_family_hourly_limit)]
    if network is not None:
        scopes.append(("network", [(created, count) for _, net, created, count in recent if net == network],
                       settings.catalog_export_network_hourly_limit, settings.catalog_export_network_family_hourly_limit))
    for scope, rows, job_limit, family_limit in scopes:
        for suffix, costs, incoming, limit in (
            ("hourly", [(created, 1) for created, _ in rows], 1, job_limit),
            ("family_hourly", rows, cost, family_limit),
        ):
            retry = _release(costs, incoming, limit, current)
            if retry:
                violations.append((retry, f"{scope}_{suffix}", "Hourly export allowance reached. Please try again later."))
    if violations:
        retry, reason, detail = max(violations, key=lambda violation: violation[0])
        raise RateLimited(reason, retry, detail)


async def prune_private_state(session, current):
    """Erase network linkage independently of result retention; keep minute state bounded."""
    await session.execute(update(CatalogExportJob).where(
        CatalogExportJob.network_hash.is_not(None),
        CatalogExportJob.created_at <= current - timedelta(hours=24),
    ).values(network_hash=None).execution_options(synchronize_session=False))
    await session.execute(delete(CatalogRateBucket).where(
        CatalogRateBucket.minute < current - timedelta(minutes=2)).execution_options(synchronize_session=False))


async def check_preflight(session, owner, network):
    """Consume one preflight request atomically, without touching generation quota.

    Network is mandatory for public callers; neither raw addresses nor secret keys
    belong here. Accepted requests commit; denied requests perform no writes.
    """
    from app.services.catalog_export_service import RateLimited, _serialize, now

    if not owner or not network:
        raise ValueError("Preflight requires owner and network identities")
    await _serialize(session)
    current = now()
    minute = current.replace(second=0, microsecond=0)
    entries = []
    for scope, identity, limit in (
        ("owner", owner, settings.catalog_export_preflight_owner_per_minute),
        ("network", network, settings.catalog_export_preflight_network_per_minute),
    ):
        bucket = await session.get(CatalogRateBucket, (scope, identity, minute))
        if (bucket.count if bucket else 0) >= limit:
            raise RateLimited(f"preflight_{scope}", _seconds(minute + timedelta(minutes=1), current),
                              "Too many catalog checks. Please wait before checking again.")
        entries.append((scope, identity, bucket))
    await prune_private_state(session, current)
    for scope, identity, bucket in entries:
        if bucket is None:
            session.add(CatalogRateBucket(scope=scope, identity_hash=identity, minute=minute, count=1))
        else:
            bucket.count += 1
    await session.commit()
