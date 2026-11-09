import argparse
import asyncio

from app.config import settings
from app.services.order_sync_service import run_order_sync


async def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill Shopee order tracking data")
    parser.add_argument(
        "--days",
        type=int,
        default=settings.order_sync_backfill_days,
        help="Number of days to backfill",
    )
    args = parser.parse_args()
    if args.days < 1:
        parser.error("--days must be at least 1")

    synced = await run_order_sync(days=args.days, full_backfill=True)
    print(f"Order tracking backfill completed: {synced} packages synchronized")


if __name__ == "__main__":
    asyncio.run(main())
