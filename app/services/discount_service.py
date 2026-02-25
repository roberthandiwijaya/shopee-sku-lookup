import logging
import math
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import async_session
from app.models.discount import Discount, DiscountItem
from app.models.product import TZ_GMT7
from app.services import shopee_client
from app.services.shopee_auth import get_valid_token

logger = logging.getLogger(__name__)


# ── Local DB reads ────────────────────────────────────────────────────────────

async def list_discounts(
    session: AsyncSession,
    page: int = 1,
    per_page: int = 25,
    status_filter: str | None = None,
) -> dict:
    base = select(Discount).options(selectinload(Discount.items))

    if status_filter:
        base = base.where(Discount.discount_status == status_filter)

    count_stmt = select(func.count()).select_from(base.subquery())
    total = await session.scalar(count_stmt) or 0
    total_pages = max(1, math.ceil(total / per_page))

    offset = (page - 1) * per_page
    stmt = base.order_by(Discount.start_time.desc()).offset(offset).limit(per_page)
    result = await session.execute(stmt)
    discounts = result.scalars().unique().all()

    return {
        "discounts": discounts,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
    }


async def get_discount_detail(session: AsyncSession, discount_id: int) -> Discount | None:
    stmt = (
        select(Discount)
        .options(selectinload(Discount.items))
        .where(Discount.discount_id == discount_id)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_discount_sync_stats(session: AsyncSession) -> dict:
    total = await session.scalar(select(func.count(Discount.id))) or 0
    ongoing = await session.scalar(
        select(func.count(Discount.id)).where(Discount.discount_status == "ongoing")
    ) or 0
    upcoming = await session.scalar(
        select(func.count(Discount.id)).where(Discount.discount_status == "upcoming")
    ) or 0
    last_synced_at = await session.scalar(select(func.max(Discount.synced_at)))
    return {
        "total": total,
        "ongoing": ongoing,
        "upcoming": upcoming,
        "last_synced_at": last_synced_at,
    }


# ── Sync (Shopee → DB) ────────────────────────────────────────────────────────

async def sync_discounts() -> int:
    shop_id = settings.shopee_shop_id
    access_token = await get_valid_token(shop_id)
    if not access_token:
        raise RuntimeError("No valid Shopee access token. Please authorize first via /api/auth/login")

    logger.info("Starting discount sync for shop %d", shop_id)
    total_synced = 0

    for status in ("upcoming", "ongoing", "expired"):
        count = await _sync_discounts_by_status(access_token, shop_id, status)
        total_synced += count

    logger.info("Discount sync completed: %d discounts synced", total_synced)
    return total_synced


async def _sync_discounts_by_status(access_token: str, shop_id: int, status: str) -> int:
    page_no = 1
    page_size = 100
    synced = 0

    while True:
        data = await shopee_client.shop_request(
            "/api/v2/discount/get_discount_list",
            access_token=access_token,
            shop_id=shop_id,
            params={
                "page_no": page_no,
                "page_size": page_size,
                "discount_status": status,
            },
        )

        response = data.get("response", {})
        discount_list = response.get("discount_list", [])

        for summary in discount_list:
            await _sync_single_discount(access_token, shop_id, summary, status)
            synced += 1

        if len(discount_list) < page_size:
            break
        page_no += 1

    return synced


async def _sync_single_discount(
    access_token: str, shop_id: int, summary: dict, status: str
) -> None:
    now = datetime.now(TZ_GMT7)
    shopee_discount_id = summary["discount_id"]

    start_ts = summary.get("start_time")
    end_ts = summary.get("end_time")
    start_time = datetime.fromtimestamp(start_ts, tz=TZ_GMT7) if start_ts else None
    end_time = datetime.fromtimestamp(end_ts, tz=TZ_GMT7) if end_ts else None

    async with async_session() as session:
        # Upsert the Discount header
        stmt = pg_insert(Discount).values(
            shop_id=shop_id,
            discount_id=shopee_discount_id,
            discount_name=summary.get("discount_name"),
            discount_status=status,
            start_time=start_time,
            end_time=end_time,
            raw_data=summary,
            synced_at=now,
            created_at=now,
            updated_at=now,
        ).on_conflict_do_update(
            index_elements=["discount_id"],
            set_={
                "discount_name": summary.get("discount_name"),
                "discount_status": status,
                "start_time": start_time,
                "end_time": end_time,
                "raw_data": summary,
                "synced_at": now,
                "updated_at": now,
            },
        ).returning(Discount.id)
        result = await session.execute(stmt)
        local_discount_id = result.scalar_one()
        await session.commit()

    # Fetch and upsert items (paginated)
    item_offset = 0
    item_limit = 100

    while True:
        detail = await shopee_client.shop_request(
            "/api/v2/discount/get_discount",
            access_token=access_token,
            shop_id=shop_id,
            params={
                "discount_id": shopee_discount_id,
                "item_offset": item_offset,
                "item_limit": item_limit,
            },
        )

        response = detail.get("response", {})
        item_list = response.get("item_list", [])

        async with async_session() as session:
            for item in item_list:
                model_id = item.get("model_id", 0) or 0
                item_id = item.get("item_id")

                stmt = pg_insert(DiscountItem).values(
                    discount_id=local_discount_id,
                    shopee_discount_id=shopee_discount_id,
                    shopee_item_id=item_id,
                    shopee_model_id=model_id,
                    item_promotion_price=item.get("item_promotion_price"),
                    purchase_limit=item.get("purchase_limit"),
                    raw_data=item,
                    created_at=now,
                    updated_at=now,
                ).on_conflict_do_update(
                    index_elements=["discount_id", "shopee_item_id", "shopee_model_id"],
                    set_={
                        "item_promotion_price": item.get("item_promotion_price"),
                        "purchase_limit": item.get("purchase_limit"),
                        "raw_data": item,
                        "updated_at": now,
                    },
                )
                await session.execute(stmt)
            await session.commit()

        if len(item_list) < item_limit:
            break
        item_offset += item_limit


# ── Write functions (Shopee API calls) ────────────────────────────────────────

async def create_discount(
    shop_id: int,
    access_token: str,
    name: str,
    start_time: int,
    end_time: int,
    item_list: list[dict],
) -> dict:
    body = {
        "discount_name": name,
        "start_time": start_time,
        "end_time": end_time,
        "item_list": item_list,
    }
    return await shopee_client.shop_request(
        "/api/v2/discount/add_discount",
        access_token=access_token,
        shop_id=shop_id,
        method="POST",
        json=body,
    )


async def update_discount(
    shop_id: int,
    access_token: str,
    discount_id: int,
    name: str | None = None,
    start_time: int | None = None,
    end_time: int | None = None,
) -> dict:
    body: dict = {"discount_id": discount_id}
    if name is not None:
        body["discount_name"] = name
    if start_time is not None:
        body["start_time"] = start_time
    if end_time is not None:
        body["end_time"] = end_time
    return await shopee_client.shop_request(
        "/api/v2/discount/update_discount",
        access_token=access_token,
        shop_id=shop_id,
        method="POST",
        json=body,
    )


async def delete_discount(shop_id: int, access_token: str, discount_id: int) -> dict:
    return await shopee_client.shop_request(
        "/api/v2/discount/delete_discount",
        access_token=access_token,
        shop_id=shop_id,
        method="POST",
        json={"discount_id": discount_id},
    )


async def end_discount(shop_id: int, access_token: str, discount_id: int) -> dict:
    return await shopee_client.shop_request(
        "/api/v2/discount/end_discount",
        access_token=access_token,
        shop_id=shop_id,
        method="POST",
        json={"discount_id": discount_id},
    )


async def add_discount_items(
    shop_id: int, access_token: str, discount_id: int, item_list: list[dict]
) -> dict:
    return await shopee_client.shop_request(
        "/api/v2/discount/add_discount_item",
        access_token=access_token,
        shop_id=shop_id,
        method="POST",
        json={"discount_id": discount_id, "item_list": item_list},
    )


async def update_discount_items(
    shop_id: int, access_token: str, discount_id: int, item_list: list[dict]
) -> dict:
    return await shopee_client.shop_request(
        "/api/v2/discount/update_discount_item",
        access_token=access_token,
        shop_id=shop_id,
        method="POST",
        json={"discount_id": discount_id, "item_list": item_list},
    )


async def delete_discount_items(
    shop_id: int, access_token: str, discount_id: int, item_list: list[dict]
) -> dict:
    return await shopee_client.shop_request(
        "/api/v2/discount/delete_discount_item",
        access_token=access_token,
        shop_id=shop_id,
        method="POST",
        json={"discount_id": discount_id, "item_list": item_list},
    )
