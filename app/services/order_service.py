from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.order import OrderPackage, OrderSyncState
from app.services import shopee_client

ORDER_DETAIL_FIELDS = ["item_list", "package_list"]


def normalize_tracking_number(tracking_number: str) -> str:
    return tracking_number.strip().upper()


async def get_order_list(
    shop_id: int,
    access_token: str,
    time_from: datetime,
    time_to: datetime,
    cursor: str = "",
    page_size: int = 100,
    time_range_field: str = "update_time",
) -> dict:
    """Fetch orders updated in a bounded time window."""
    return await shopee_client.shop_request(
        "/api/v2/order/get_order_list",
        access_token=access_token,
        shop_id=shop_id,
        params={
            "time_range_field": time_range_field,
            "time_from": int(time_from.timestamp()),
            "time_to": int(time_to.timestamp()),
            "page_size": page_size,
            "cursor": cursor,
        },
    )


async def get_order_detail(
    shop_id: int,
    access_token: str,
    order_sn_list: list[str],
    response_optional_fields: list[str] | None = None,
) -> dict:
    """Fetch order details from Shopee without modifying the response."""
    params = {"order_sn_list": ",".join(order_sn_list)}
    fields = response_optional_fields
    if fields:
        params["response_optional_fields"] = ",".join(fields)

    return await shopee_client.shop_request(
        "/api/v2/order/get_order_detail",
        access_token=access_token,
        shop_id=shop_id,
        params=params,
    )


async def get_package_detail(
    shop_id: int,
    access_token: str,
    package_number_list: list[str],
) -> dict:
    """Fetch package details from Shopee without modifying the response."""
    return await shopee_client.shop_request(
        "/api/v2/order/get_package_detail",
        access_token=access_token,
        shop_id=shop_id,
        params={"package_number_list": ",".join(package_number_list)},
    )


async def lookup_package_by_tracking_number(
    session: AsyncSession,
    shop_id: int,
    tracking_number: str,
) -> tuple[OrderPackage | None, bool]:
    """Return a complete package and whether the initial backfill has finished."""
    normalized = normalize_tracking_number(tracking_number)
    stmt = (
        select(OrderPackage)
        .options(selectinload(OrderPackage.order), selectinload(OrderPackage.items))
        .where(
            OrderPackage.shop_id == shop_id,
            OrderPackage.tracking_number_normalized == normalized,
        )
        .order_by(OrderPackage.updated_at.desc())
    )
    result = await session.execute(stmt)
    package = result.scalars().first()
    sync_state = await session.get(OrderSyncState, shop_id)
    backfill_complete = bool(sync_state and sync_state.backfill_completed_at)
    return package, backfill_complete
