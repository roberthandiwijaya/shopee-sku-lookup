import math
import re
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models.product import AuthAlert, Product, ProductModel, ShopeeToken, ShopInfo, TZ_GMT7
from app.schemas.product import ProductOut, ProductSearchResponse

FAMILY_CODE_PATTERN = re.compile(r"\b([A-Z]{1,5}-\d{2,6})\b", re.IGNORECASE)


async def lookup_by_skus(
    session: AsyncSession, skus: list[str]
) -> ProductSearchResponse:
    stmt = (
        select(Product)
        .outerjoin(Product.models)
        .options(selectinload(Product.models))
        .where(
            or_(
                Product.item_sku.in_(skus),
                ProductModel.model_sku.in_(skus),
            )
        )
        .distinct()
    )
    result = await session.execute(stmt)
    products = result.scalars().all()

    found_skus: set[str] = set()
    for p in products:
        if p.item_sku:
            found_skus.add(p.item_sku)
        for m in p.models:
            if m.model_sku:
                found_skus.add(m.model_sku)

    skus_not_found = [s for s in skus if s not in found_skus]

    product_list = [ProductOut.model_validate(p) for p in products]
    return ProductSearchResponse(
        products=product_list,
        count=len(product_list),
        skus_not_found=skus_not_found,
    )


async def get_sync_stats(session: AsyncSession) -> dict:
    product_count = await session.scalar(select(func.count(Product.id)))
    model_count = await session.scalar(select(func.count(ProductModel.id)))
    last_sync = await session.scalar(
        select(func.max(Product.synced_at))
    )
    token_status = await _get_token_status(session)
    return {
        "last_synced_at": last_sync,
        "total_products": product_count or 0,
        "total_models": model_count or 0,
        "token_status": token_status,
    }


def _get_product_image_urls(product: Product) -> list[str]:
    images = product.images if isinstance(product.images, list) else []
    return [image.strip() for image in images if isinstance(image, str) and image.strip()]



def _get_primary_image_url(product: Product) -> str | None:
    images = _get_product_image_urls(product)
    return images[0] if images else None


def normalize_family_code(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().upper()
    if not normalized:
        return None
    match = FAMILY_CODE_PATTERN.fullmatch(normalized)
    if not match:
        raise ValueError("Family code must look like MP-8002")
    return match.group(1)


async def list_products_paginated(
    session: AsyncSession,
    page: int = 1,
    per_page: int = 25,
    search_sku: str | None = None,
    partner_filter: str | None = None,
) -> dict:
    """List products with pagination, search, and partner filters."""
    base = select(Product).options(selectinload(Product.models))

    if search_sku:
        pattern = f"%{search_sku}%"
        normalized_search = search_sku.strip().upper()
        base = (
            base.outerjoin(Product.models)
            .where(
                or_(
                    Product.item_name.ilike(pattern),
                    Product.item_sku.ilike(pattern),
                    Product.family_code.ilike(pattern),
                    ProductModel.model_sku.ilike(pattern),
                    Product.family_code == normalized_search,
                )
            )
            .distinct()
        )

    result = await session.execute(base.order_by(Product.item_name))
    products = result.scalars().unique().all()

    family_counts: dict[str, int] = defaultdict(int)
    for product in products:
        if product.family_code:
            family_counts[product.family_code] += 1

    enriched_products: list[Product] = []
    for product in products:
        product.image_urls = _get_product_image_urls(product)
        product.primary_image_url = product.image_urls[0] if product.image_urls else None
        product.partner_count = family_counts.get(product.family_code, 0) - 1 if product.family_code else 0
        product.has_partners = product.partner_count > 0
        enriched_products.append(product)

    if partner_filter == "has_partners":
        enriched_products = [product for product in enriched_products if product.has_partners]
    elif partner_filter == "no_partners":
        enriched_products = [product for product in enriched_products if not product.has_partners]
    elif partner_filter == "has_family_code":
        enriched_products = [product for product in enriched_products if product.family_code]
    elif partner_filter == "no_family_code":
        enriched_products = [product for product in enriched_products if not product.family_code]

    total = len(enriched_products)
    total_pages = max(1, math.ceil(total / per_page))

    offset = (page - 1) * per_page
    paged_products = enriched_products[offset: offset + per_page]

    return {
        "products": paged_products,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "partner_filter": partner_filter,
    }


async def get_product_by_item_id(session: AsyncSession, item_id: int) -> Product | None:
    return await session.scalar(
        select(Product)
        .options(selectinload(Product.models))
        .where(Product.item_id == item_id)
    )


async def update_product_family_code(
    session: AsyncSession, item_id: int, family_code: str | None
) -> Product | None:
    product = await get_product_by_item_id(session, item_id)
    if product is None:
        return None
    product.family_code = normalize_family_code(family_code)
    await session.commit()
    await session.refresh(product)
    return product


async def get_related_products(session: AsyncSession, item_id: int) -> dict:
    product = await get_product_by_item_id(session, item_id)
    if product is None:
        return {"product": None, "models": [], "related_products": []}

    related_products: list[Product] = []
    if product.family_code:
        result = await session.execute(
            select(Product)
            .where(Product.family_code == product.family_code, Product.item_id != item_id)
            .order_by(Product.item_name)
        )
        related_products = result.scalars().all()

    return {
        "product": product,
        "models": product.models,
        "related_products": related_products,
    }


async def get_token_details(session: AsyncSession) -> dict:
    """Get detailed token status for the dashboard."""
    result = await session.execute(
        select(ShopeeToken).where(ShopeeToken.shop_id == settings.shopee_shop_id)
    )
    token = result.scalar_one_or_none()
    if token is None:
        return {
            "status": "missing",
            "shop_id": settings.shopee_shop_id,
            "expires_at": None,
            "expires_in_hours": None,
        }

    now = datetime.now(TZ_GMT7)
    expires_at = token.token_expires_at
    if expires_at is None:
        return {
            "status": "missing",
            "shop_id": settings.shopee_shop_id,
            "expires_at": None,
            "expires_in_hours": None,
        }

    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=TZ_GMT7)

    remaining = expires_at - now
    hours_left = remaining.total_seconds() / 3600

    if expires_at <= now:
        status = "expired"
    elif expires_at <= now + timedelta(hours=24):
        status = "expiring_soon"
    else:
        status = "healthy"

    return {
        "status": status,
        "shop_id": settings.shopee_shop_id,
        "expires_at": expires_at,
        "expires_in_hours": round(hours_left, 1),
    }


async def get_active_alert(session: AsyncSession) -> AuthAlert | None:
    """Return the latest non-dismissed AuthAlert for the configured shop, or None."""
    result = await session.execute(
        select(AuthAlert)
        .where(
            AuthAlert.shop_id == settings.shopee_shop_id,
            AuthAlert.is_dismissed == False,
        )
        .order_by(AuthAlert.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def dismiss_alert(session: AsyncSession, alert_id: int) -> None:
    """Mark an alert as dismissed."""
    result = await session.execute(
        select(AuthAlert).where(AuthAlert.id == alert_id)
    )
    alert = result.scalar_one_or_none()
    if alert:
        alert.is_dismissed = True
        await session.commit()


async def get_shop_info(session: AsyncSession) -> ShopInfo | None:
    """Read shop info from DB for the configured shop."""
    result = await session.execute(
        select(ShopInfo).where(ShopInfo.shop_id == settings.shopee_shop_id)
    )
    return result.scalar_one_or_none()


async def _get_token_status(session: AsyncSession) -> str:
    """Check the health of the Shopee token."""
    result = await session.execute(
        select(ShopeeToken).where(ShopeeToken.shop_id == settings.shopee_shop_id)
    )
    token = result.scalar_one_or_none()
    if token is None:
        return "missing"
    if token.token_expires_at is None:
        return "missing"
    now = datetime.now(TZ_GMT7)
    expires_at = token.token_expires_at
    # Ensure timezone-aware comparison (SQLite may strip tzinfo)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=TZ_GMT7)
    if expires_at <= now:
        return "expired"
    if expires_at <= now + timedelta(hours=24):
        return "expiring_soon"
    return "healthy"
