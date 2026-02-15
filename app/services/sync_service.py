import logging
from datetime import datetime

from app.models.product import TZ_GMT7

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import settings
from app.database import async_session
from app.models.product import Product, ProductModel
from app.services import shopee_client
from app.services.shopee_auth import get_valid_token

logger = logging.getLogger(__name__)

ITEM_LIST_PAGE_SIZE = 100
ITEM_DETAIL_BATCH_SIZE = 50


async def run_sync() -> None:
    """Sync all products from Shopee to the local database."""
    shop_id = settings.shopee_shop_id
    access_token = await get_valid_token(shop_id)
    if not access_token:
        raise RuntimeError("No valid Shopee access token. Please authorize first via /api/auth/login")

    logger.info("Starting product sync for shop %d", shop_id)

    item_ids = await _fetch_all_item_ids(access_token, shop_id)
    logger.info("Found %d items to sync", len(item_ids))

    for i in range(0, len(item_ids), ITEM_DETAIL_BATCH_SIZE):
        batch = item_ids[i : i + ITEM_DETAIL_BATCH_SIZE]
        await _sync_item_batch(access_token, shop_id, batch)

    logger.info("Product sync completed for shop %d", shop_id)


async def _fetch_all_item_ids(access_token: str, shop_id: int) -> list[int]:
    """Paginate through get_item_list to collect all item_ids."""
    all_ids: list[int] = []
    offset = 0

    while True:
        data = await shopee_client.shop_request(
            "/api/v2/product/get_item_list",
            access_token=access_token,
            shop_id=shop_id,
            params={
                "offset": offset,
                "page_size": ITEM_LIST_PAGE_SIZE,
                "item_status": "NORMAL",
            },
        )

        response = data.get("response", {})
        items = response.get("item", [])
        all_ids.extend(item["item_id"] for item in items)

        if not response.get("has_next_page", False):
            break
        offset += ITEM_LIST_PAGE_SIZE

    return all_ids


async def _sync_item_batch(access_token: str, shop_id: int, item_ids: list[int]) -> None:
    """Fetch details for a batch of item_ids and upsert into DB."""
    id_str = ",".join(str(i) for i in item_ids)
    data = await shopee_client.shop_request(
        "/api/v2/product/get_item_base_info",
        access_token=access_token,
        shop_id=shop_id,
        params={"item_id_list": id_str},
    )

    items = data.get("response", {}).get("item_list", [])
    now = datetime.now(TZ_GMT7)

    async with async_session() as session:
        for item in items:
            item_id = item["item_id"]
            has_model = bool(item.get("has_model", False))

            price_info = item.get("price_info", [{}])
            original_price = price_info[0].get("original_price") if price_info else None
            current_price = price_info[0].get("current_price") if price_info else None

            image_info = item.get("image", {})
            images = image_info.get("image_url_list", [])

            stmt = pg_insert(Product).values(
                shop_id=shop_id,
                item_id=item_id,
                item_sku=item.get("item_sku", ""),
                item_name=item.get("item_name", ""),
                item_status=item.get("item_status"),
                description=item.get("description", ""),
                currency=item.get("currency"),
                original_price=original_price,
                current_price=current_price,
                stock=item.get("stock_info_v2", {}).get("summary_info", {}).get("total_available_stock"),
                images=images,
                category_id=item.get("category_id"),
                has_model=has_model,
                raw_data=item,
                synced_at=now,
                created_at=now,
                updated_at=now,
            ).on_conflict_do_update(
                index_elements=["item_id"],
                set_={
                    "item_sku": item.get("item_sku", ""),
                    "item_name": item.get("item_name", ""),
                    "item_status": item.get("item_status"),
                    "description": item.get("description", ""),
                    "currency": item.get("currency"),
                    "original_price": original_price,
                    "current_price": current_price,
                    "stock": item.get("stock_info_v2", {}).get("summary_info", {}).get("total_available_stock"),
                    "images": images,
                    "category_id": item.get("category_id"),
                    "has_model": has_model,
                    "raw_data": item,
                    "synced_at": now,
                    "updated_at": now,
                },
            )
            await session.execute(stmt)

            if has_model:
                await _sync_models(session, access_token, shop_id, item_id, now)

        await session.commit()


async def _sync_models(
    session, access_token: str, shop_id: int, item_id: int, now: datetime
) -> None:
    """Fetch and upsert model/variant data for a single item."""
    data = await shopee_client.shop_request(
        "/api/v2/product/get_model_list",
        access_token=access_token,
        shop_id=shop_id,
        params={"item_id": item_id},
    )

    models = data.get("response", {}).get("model", [])

    product_row = await session.execute(
        select(Product.id).where(Product.item_id == item_id)
    )
    product_id = product_row.scalar_one_or_none()
    if product_id is None:
        return

    for model in models:
        model_id = model["model_id"]

        price_info = model.get("price_info", [{}])
        original_price = price_info[0].get("original_price") if price_info else None
        current_price = price_info[0].get("current_price") if price_info else None

        tier_index = model.get("tier_index", [])
        model_name = model.get("model_name", "") or " / ".join(str(t) for t in tier_index)

        stmt = pg_insert(ProductModel).values(
            product_id=product_id,
            item_id=item_id,
            model_id=model_id,
            model_sku=model.get("model_sku", ""),
            model_name=model_name,
            original_price=original_price,
            current_price=current_price,
            stock=model.get("stock_info_v2", {}).get("summary_info", {}).get("total_available_stock"),
            raw_data=model,
            synced_at=now,
            created_at=now,
            updated_at=now,
        ).on_conflict_do_update(
            index_elements=["model_id"],
            set_={
                "model_sku": model.get("model_sku", ""),
                "model_name": model_name,
                "original_price": original_price,
                "current_price": current_price,
                "stock": model.get("stock_info_v2", {}).get("summary_info", {}).get("total_available_stock"),
                "raw_data": model,
                "synced_at": now,
                "updated_at": now,
            },
        )
        await session.execute(stmt)
