from datetime import datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models.product import Product, ProductModel, ShopeeToken, TZ_GMT7
from app.schemas.product import ProductOut, ProductSearchResponse


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
