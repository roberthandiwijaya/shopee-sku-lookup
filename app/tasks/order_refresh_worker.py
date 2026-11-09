import argparse
import asyncio
import logging
import signal
import socket
import uuid

from app.config import settings
from app.database import async_session
from app.services import order_refresh_service
from app.services.order_sync_service import sync_orders
from app.services.shopee_auth import get_valid_token

logger = logging.getLogger(__name__)


class OrderRefreshWorker:
    def __init__(self) -> None:
        self.worker_id = f"{socket.gethostname()}-{uuid.uuid4()}"
        self.stopping = False

    def stop(self) -> None:
        self.stopping = True

    async def run_once(self) -> int:
        async with async_session() as session:
            jobs = await order_refresh_service.claim_refresh_jobs(
                session, self.worker_id
            )
        if not jobs:
            return 0

        token = await get_valid_token(settings.shopee_shop_id)
        if not token:
            for job in jobs:
                async with async_session() as session:
                    await order_refresh_service.retry_refresh_job(
                        session, job.id, self.worker_id, "No valid Shopee token"
                    )
            return len(jobs)

        for job in jobs:
            try:
                synced = await sync_orders(
                    shop_id=job.shop_id,
                    access_token=token,
                    order_sns=[job.order_sn],
                )
                if synced == 0:
                    raise RuntimeError("Order refresh produced no complete packages")
                async with async_session() as session:
                    await order_refresh_service.complete_refresh_job(
                        session, job.id, self.worker_id
                    )
                logger.info(
                    "Order refresh completed worker=%s job=%s order=%s packages=%s",
                    self.worker_id,
                    job.id,
                    job.order_sn,
                    synced,
                )
            except Exception as exc:
                async with async_session() as session:
                    await order_refresh_service.retry_refresh_job(
                        session, job.id, self.worker_id, str(exc)
                    )
                logger.exception(
                    "Order refresh failed worker=%s job=%s order=%s",
                    self.worker_id,
                    job.id,
                    job.order_sn,
                )
        return len(jobs)

    async def run(self, once: bool = False, drain: bool = False) -> None:
        while not self.stopping:
            processed = await self.run_once()
            if once or (drain and processed == 0):
                return
            await asyncio.sleep(1 if processed else 5)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Process durable order refresh jobs")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--drain", action="store_true")
    args = parser.parse_args()

    worker = OrderRefreshWorker()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, worker.stop)
    await worker.run(once=args.once, drain=args.drain)


if __name__ == "__main__":
    asyncio.run(main())
