import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def _sync_job():
    from app.services.sync_service import run_sync

    try:
        await run_sync()
    except Exception:
        logger.exception("Scheduled sync failed")


async def _order_sync_job():
    from app.services.order_sync_service import run_order_sync

    try:
        await run_order_sync(days=settings.order_sync_backfill_days)
    except Exception:
        logger.exception("Scheduled order sync failed")


async def _order_reconciliation_job():
    from app.services.order_sync_service import run_order_sync

    try:
        await run_order_sync(
            days=settings.order_sync_backfill_days,
            reconciliation=True,
        )
    except Exception:
        logger.exception("Scheduled order reconciliation failed")


def start_scheduler():
    scheduler.add_job(
        _sync_job,
        "interval",
        minutes=settings.sync_interval_minutes,
        id="shopee_sync",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    if settings.order_sync_enabled:
        scheduler.add_job(
            _order_sync_job,
            "interval",
            minutes=settings.order_sync_interval_minutes,
            id="shopee_order_sync",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
    if settings.order_reconciliation_enabled:
        scheduler.add_job(
            _order_reconciliation_job,
            "interval",
            minutes=settings.order_reconciliation_interval_minutes,
            id="shopee_order_reconciliation",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
    scheduler.start()
    logger.info("Scheduler started — syncing every %d minutes", settings.sync_interval_minutes)
    if settings.order_sync_enabled:
        logger.info(
            "Order scheduler started — syncing every %d minutes",
            settings.order_sync_interval_minutes,
        )


def stop_scheduler():
    scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped")
