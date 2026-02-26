import logging
import math
from datetime import datetime

from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import async_session
from app.models.discount import Discount, DiscountItem
from app.models.product import Product, ProductModel, TZ_GMT7
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


async def fetch_live_discount_prices(
    shop_id: int, access_token: str, shopee_discount_id: int
) -> dict[tuple[int, int], dict]:
    """Return {(item_id, model_id): {"price": float|None, "limit": int|None}} from Shopee API."""
    overrides: dict[tuple[int, int], dict] = {}
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
        item_list = detail.get("response", {}).get("item_list", [])
        for item in item_list:
            item_id = item.get("item_id")
            model_id = item.get("model_id", 0) or 0
            price = item.get("item_promotion_price")
            limit = item.get("purchase_limit")
            overrides[(item_id, model_id)] = {
                "price": float(price) if price else None,
                "limit": limit or None,
            }
        if len(item_list) < item_limit:
            break
        item_offset += item_limit
    return overrides


async def get_enriched_discount_items(
    session: AsyncSession,
    local_discount_id: int,
    price_overrides: dict[tuple[int, int], dict] | None = None,
) -> list[dict]:
    stmt = (
        select(DiscountItem, Product, ProductModel)
        .outerjoin(Product, Product.item_id == DiscountItem.shopee_item_id)
        .outerjoin(
            ProductModel,
            and_(
                ProductModel.model_id == DiscountItem.shopee_model_id,
                DiscountItem.shopee_model_id != 0,
            ),
        )
        .where(DiscountItem.discount_id == local_discount_id)
        .order_by(DiscountItem.shopee_item_id, DiscountItem.shopee_model_id)
    )
    result = await session.execute(stmt)
    rows = result.all()

    # Batch-load ProductModels for all item-level rows (model_id == 0)
    item_ids_to_expand = [di.shopee_item_id for di, _, _ in rows if di.shopee_model_id == 0]
    models_by_item: dict[int, list[ProductModel]] = {}
    if item_ids_to_expand:
        res = await session.execute(
            select(ProductModel)
            .where(ProductModel.item_id.in_(item_ids_to_expand))
            .order_by(ProductModel.item_id, ProductModel.model_name)
        )
        for pm in res.scalars():
            models_by_item.setdefault(pm.item_id, []).append(pm)

    items = []
    for di, product, model in rows:
        product_name = product.item_name if product else f"Item {di.shopee_item_id}"
        product_image = product.images[0] if (product and product.images) else None
        raw_promo = di.item_promotion_price
        raw_limit = di.purchase_limit

        # Only apply the pre-branch override for model-level DB rows.
        # Item-level rows (shopee_model_id==0) need per-model lookup inside the expansion loop.
        if price_overrides is not None and di.shopee_model_id != 0:
            override_key = (di.shopee_item_id, di.shopee_model_id)
            if override_key in price_overrides:
                ov = price_overrides[override_key]
                raw_promo = ov["price"]
                raw_limit = ov["limit"]

        if di.shopee_model_id != 0:
            # Model-level row — existing logic, unchanged
            promo = float(raw_promo) if raw_promo else None
            original_price = (
                float(model.original_price) if (model and model.original_price is not None)
                else (float(product.original_price) if (product and product.original_price is not None) else None)
            )
            discount_pct = (
                round((1 - promo / original_price) * 100)
                if (promo and original_price and original_price > 0) else None
            )
            form_key = f"{di.shopee_item_id}_{di.shopee_model_id}"
            items.append({
                "form_key": form_key,
                "discount_item_id": di.id,
                "shopee_item_id": di.shopee_item_id,
                "shopee_model_id": di.shopee_model_id,
                "item_promotion_price": promo,
                "purchase_limit": raw_limit or None,
                "product_name": product_name,
                "product_image": product_image,
                "model_name": model.model_name if model else None,
                "model_sku": (model.model_sku if model else None) or (product.item_sku if product else None),
                "original_price": original_price,
                "discount_pct": discount_pct,
            })
        else:
            # Item-level row — expand into one row per ProductModel
            pm_list = models_by_item.get(di.shopee_item_id, [])
            if pm_list:
                for pm in pm_list:
                    # Per-model override: try (item_id, model_id) then (item_id, 0)
                    model_promo = raw_promo
                    model_limit = raw_limit
                    if price_overrides is not None:
                        ov = (price_overrides.get((di.shopee_item_id, pm.model_id))
                              or price_overrides.get((di.shopee_item_id, 0)))
                        if ov:
                            model_promo = ov["price"]
                            model_limit = ov["limit"]
                    promo = float(model_promo) if model_promo else None
                    original_price = float(pm.original_price) if pm.original_price else None
                    discount_pct = (
                        round((1 - promo / original_price) * 100)
                        if (promo and original_price and original_price > 0) else None
                    )
                    form_key = f"{di.shopee_item_id}_{pm.model_id}"
                    items.append({
                        "form_key": form_key,
                        "discount_item_id": di.id,
                        "shopee_item_id": di.shopee_item_id,
                        "shopee_model_id": pm.model_id,
                        "item_promotion_price": promo,
                        "purchase_limit": model_limit or None,
                        "product_name": product_name,
                        "product_image": product_image,
                        "model_name": pm.model_name,
                        "model_sku": pm.model_sku or (product.item_sku if product else None),
                        "original_price": original_price,
                        "discount_pct": discount_pct,
                    })
            else:
                # No models found — fall back to item-level row
                promo = float(raw_promo) if raw_promo else None
                original_price = (
                    float(product.original_price) if (product and product.original_price is not None) else None
                )
                discount_pct = (
                    round((1 - promo / original_price) * 100)
                    if (promo and original_price and original_price > 0) else None
                )
                form_key = f"{di.shopee_item_id}_0"
                items.append({
                    "form_key": form_key,
                    "discount_item_id": di.id,
                    "shopee_item_id": di.shopee_item_id,
                    "shopee_model_id": 0,
                    "item_promotion_price": promo,
                    "purchase_limit": raw_limit or None,
                    "product_name": product_name,
                    "product_image": product_image,
                    "model_name": None,
                    "model_sku": product.item_sku if product else None,
                    "original_price": original_price,
                    "discount_pct": discount_pct,
                })
    return items


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
