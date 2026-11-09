import asyncio
import logging
import math
import re
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select, text

from app.config import settings
from app.database import async_session
from app.models.product import Product, ProductModel, TZ_GMT7
from app.services import shopee_client
from app.services.shopee_auth import get_valid_token

logger = logging.getLogger(__name__)
ITEM_LIST_PAGE_SIZE = 100
ITEM_DETAIL_BATCH_SIZE = 50
ITEM_STATUSES = ("NORMAL", "UNLIST", "BANNED")
_local_sync_lock = asyncio.Lock()
# Deliberately anchored: other warnings can indicate incomplete catalog data.
_SHIPPING_WARNING = re.compile(r"fail to get channel estimated_shipping_fee for channel \[[0-9]+\]")


def _shipping_warning(warning):
    if not isinstance(warning, str):
        return False
    lines = [line.strip().rstrip(";") for line in warning.splitlines() if line.strip()]
    return bool(lines) and all(_SHIPPING_WARNING.fullmatch(line) for line in lines)


class SyncValidationError(RuntimeError):
    """An upstream response cannot safely be applied to the local catalog."""


@asynccontextmanager
async def _sync_lock(shop_id):
    """Keep the PostgreSQL session lock on one dedicated connection all run."""
    async with async_session() as session:
        engine = session.bind
        if engine.dialect.name != "postgresql":
            async with _local_sync_lock:
                yield
            return
        async with engine.connect() as connection:
            key = (0x53484F50 << 32) | (shop_id & 0xFFFFFFFF)
            acquired = await connection.scalar(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": key}
            )
            if not acquired:
                raise RuntimeError("Product sync already running")
            try:
                yield
            finally:
                try:
                    await connection.execute(
                        text("SELECT pg_advisory_unlock(:key)"), {"key": key}
                    )
                except BaseException:
                    # Never return a still-locked connection to the pool.
                    await connection.invalidate()
                    raise


async def run_sync() -> None:
    """Refresh listed and locally known products without inferring deletions."""
    shop_id = settings.shopee_shop_id
    async with _sync_lock(shop_id):
        access_token = await get_valid_token(shop_id)
        if not access_token:
            raise RuntimeError("No valid Shopee access token. Please authorize first via /api/auth/login")
        item_ids = set(await _fetch_all_item_ids(access_token, shop_id))
        async with async_session() as session:
            item_ids.update(await session.scalars(
                select(Product.item_id).where(Product.shop_id == shop_id)
            ))
        ordered_ids = sorted(item_ids)
        failures = 0
        for start in range(0, len(ordered_ids), ITEM_DETAIL_BATCH_SIZE):
            batch = ordered_ids[start:start + ITEM_DETAIL_BATCH_SIZE]
            try:
                await _sync_item_batch(access_token, shop_id, batch)
            except SyncValidationError:
                # Isolate omissions without starving other known or listed items.
                if len(batch) == 1:
                    logger.warning("Product sync validation failed for item %d; local record retained", batch[0])
                    failures += 1
                    continue
                for item_id in batch:
                    try:
                        await _sync_item_batch(access_token, shop_id, [item_id])
                    except SyncValidationError:
                        logger.warning("Product sync validation failed for item %d; local record retained", item_id)
                        failures += 1
        if failures:
            raise SyncValidationError(f"Product sync incomplete: {failures} item(s) failed validation")
        logger.info("Product sync completed for shop %d (%d items)", shop_id, len(item_ids))


def _response(data, *, allow_shipping_warning=False):
    if not isinstance(data, dict) or data.get("error"):
        raise SyncValidationError("Shopee API returned an error or invalid envelope")
    response = data.get("response")
    if not isinstance(response, dict):
        raise SyncValidationError("Shopee API response is missing")
    for envelope in (data, response):
        if envelope.get("error"):
            raise SyncValidationError("Shopee API returned an error")
        for field in ("warning", "warnings", "warning_list"):
            warning = envelope.get(field)
            if warning:
                if not (allow_shipping_warning and _shipping_warning(warning)):
                    raise SyncValidationError("Shopee API returned an unrecognized warning")
    return response


def _records(response, field, id_field, expected=None, *, nonempty=False):
    records = response.get(field)
    if not isinstance(records, list) or (nonempty and not records):
        raise SyncValidationError(f"Invalid or missing {field}")
    ids = []
    for record in records:
        if not isinstance(record, dict):
            raise SyncValidationError(f"Invalid {field} entry")
        value = record.get(id_field)
        if type(value) is not int or value <= 0:
            raise SyncValidationError(f"Invalid {id_field}")
        ids.append(value)
    if len(ids) != len(set(ids)) or (expected is not None and set(ids) != set(expected)):
        raise SyncValidationError(f"Duplicate, missing or unexpected {id_field}")
    return records


async def _request(path, **kwargs):
    try:
        return await shopee_client.shop_request(path, **kwargs)
    except Exception:
        # Transport exceptions may embed a signed URL and access token.
        raise SyncValidationError("Shopee request failed") from None


async def _fetch_all_item_ids(access_token: str, shop_id: int) -> list[int]:
    all_ids = set()
    for status in ITEM_STATUSES:
        offset, seen = 0, set()
        while True:
            data = await _request(
                "/api/v2/product/get_item_list", access_token=access_token, shop_id=shop_id,
                params={"offset": offset, "page_size": ITEM_LIST_PAGE_SIZE, "item_status": status},
            )
            response = _response(data)
            items = _records(response, "item", "item_id")
            ids = {item["item_id"] for item in items}
            if seen & ids or type(response.get("has_next_page")) is not bool:
                raise SyncValidationError("Invalid item pagination")
            seen.update(ids)
            if not response["has_next_page"]:
                break
            if not items:
                raise SyncValidationError("Empty item page claims a next page")
            offset += ITEM_LIST_PAGE_SIZE
        all_ids.update(seen)
    return sorted(all_ids)


async def _fetch_base(access_token, shop_id, item_ids):
    data = await _request(
        "/api/v2/product/get_item_base_info", access_token=access_token, shop_id=shop_id,
        params={"item_id_list": ",".join(map(str, item_ids))},
    )
    items = _records(_response(data, allow_shipping_warning=True), "item_list", "item_id", item_ids)
    for item in items:
        if type(item.get("has_model")) is not bool:
            raise SyncValidationError("has_model must be an explicit boolean")
        if item.get("item_status") not in (*ITEM_STATUSES, "DELETED"):
            raise SyncValidationError("Invalid or missing item_status")
    return items


def _number(value, field, *, integer=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float, Decimal))
            or not math.isfinite(value) or value < 0 or (integer and type(value) is not int)):
        raise SyncValidationError(f"Invalid {field}")
    return value


def _price_stock(record, *, required=False):
    values = {}
    if "price_info" in record or required:
        prices = record.get("price_info")
        if not isinstance(prices, list) or not prices or not isinstance(prices[0], dict):
            raise SyncValidationError("Invalid price_info")
        for field in ("original_price", "current_price"):
            if field in prices[0] or required:
                values[field] = _number(prices[0].get(field), field)
    if "stock_info_v2" in record or required:
        stock = record.get("stock_info_v2")
        summary = stock.get("summary_info") if isinstance(stock, dict) else None
        if not isinstance(summary, dict):
            raise SyncValidationError("Invalid stock_info_v2")
        values["stock"] = _number(summary.get("total_available_stock"), "stock", integer=True)
    return values


async def _fetch_models(access_token, shop_id, item_id):
    data = await _request(
        "/api/v2/product/get_model_list", access_token=access_token, shop_id=shop_id,
        params={"item_id": item_id},
    )
    response = _response(data)
    if any(response.get(field) for field in ("has_next_page", "has_more", "more", "failure_list", "failed_list")):
        raise SyncValidationError("Incomplete model response")
    models = _records(response, "model", "model_id", nonempty=True)
    for model in models:
        _price_stock(model, required=True)
    return models


async def _sync_item_batch(access_token: str, shop_id: int, item_ids: list[int]) -> None:
    """Validate all upstream reads before atomically persisting one bounded batch."""
    if not item_ids or len(set(item_ids)) != len(item_ids):
        raise SyncValidationError("Invalid requested item IDs")
    items = await _fetch_base(access_token, shop_id, item_ids)
    model_data = {}
    for item in items:
        _price_stock(item)
        if item["has_model"]:
            first = await _fetch_models(access_token, shop_id, item["item_id"])
            second = await _fetch_models(access_token, shop_id, item["item_id"])
            if {m["model_id"] for m in first} != {m["model_id"] for m in second}:
                raise SyncValidationError("Model set changed during confirmation")
            model_data[item["item_id"]] = second
    false_ids = [item["item_id"] for item in items if not item["has_model"]]
    if false_ids:
        confirmation = await _fetch_base(access_token, shop_id, false_ids)
        if any(item["has_model"] for item in confirmation):
            raise SyncValidationError("has_model changed during confirmation")

    now = datetime.now(TZ_GMT7)
    async with async_session() as session:
        async with session.begin():
            for item in items:
                product = await session.scalar(
                    select(Product).where(Product.item_id == item["item_id"]).with_for_update()
                )
                if product is None:
                    product = Product(shop_id=shop_id, item_id=item["item_id"], created_at=now)
                    session.add(product)
                elif product.shop_id != shop_id:
                    raise SyncValidationError("Item belongs to a different shop")
                # ORM dirty-column updates intentionally never write local family/material metadata.
                for field in ("item_sku", "item_name", "item_status", "description", "currency", "category_id"):
                    if field in item:
                        setattr(product, field, item[field])
                if product.item_name is None:
                    product.item_name = ""
                if "image" in item:
                    image = item["image"]
                    if not isinstance(image, dict) or not isinstance(image.get("image_url_list"), list):
                        raise SyncValidationError("Invalid image data")
                    product.images = image["image_url_list"]
                for field, value in _price_stock(item).items():
                    setattr(product, field, value)
                product.has_model = item["has_model"]
                product.raw_data = item
                product.synced_at = product.updated_at = now
                await session.flush()
                existing = list(await session.scalars(
                    select(ProductModel).where(ProductModel.product_id == product.id).with_for_update()
                ))
                by_id = {model.model_id: model for model in existing}
                returned = model_data.get(item["item_id"], [])
                returned_ids = {model["model_id"] for model in returned}
                for payload in returned:
                    model = by_id.get(payload["model_id"])
                    if model is None:
                        # A globally unique ID owned by another product is not safe to reparent.
                        collision = await session.scalar(select(ProductModel.id).where(
                            ProductModel.model_id == payload["model_id"]
                        ))
                        if collision is not None:
                            raise SyncValidationError("Model belongs to a different product")
                        model = ProductModel(product_id=product.id, item_id=product.item_id,
                                             model_id=payload["model_id"], created_at=now)
                        session.add(model)
                    model.model_sku = payload.get("model_sku", "")
                    model.model_name = payload.get("model_name") or " / ".join(map(str, payload.get("tier_index", [])))
                    for field, value in _price_stock(payload, required=True).items():
                        setattr(model, field, value)
                    model.raw_data = payload
                    model.is_active = True
                    model.synced_at = model.updated_at = now
                for model in existing:
                    if model.model_id not in returned_ids:
                        model.is_active = False
                        model.updated_at = now
