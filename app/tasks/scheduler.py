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


def start_scheduler():
    scheduler.add_job(
        _sync_job,
        "interval",
        minutes=settings.sync_interval_minutes,
        id="shopee_sync",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Scheduler started — syncing every %d minutes", settings.sync_interval_minutes)


def stop_scheduler():
    scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped")
