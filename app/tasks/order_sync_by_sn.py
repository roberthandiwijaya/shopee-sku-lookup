import argparse
import asyncio

from app.config import settings
from app.services.order_sync_service import sync_orders
from app.services.shopee_auth import get_valid_token


async def main() -> None:
    parser = argparse.ArgumentParser(description="Synchronize specific Shopee orders")
    parser.add_argument("order_sn", nargs="+", help="One or more Shopee order serial numbers")
    args = parser.parse_args()

    access_token = await get_valid_token(settings.shopee_shop_id)
    if not access_token:
        parser.error("No valid Shopee access token")

    synced = await sync_orders(
        shop_id=settings.shopee_shop_id,
        access_token=access_token,
        order_sns=list(dict.fromkeys(args.order_sn)),
    )
    print(f"Order synchronization completed: {synced} packages synchronized")


if __name__ == "__main__":
    asyncio.run(main())
