from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.product import Product, ProductModel
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
    return {
        "last_synced_at": last_sync,
        "total_products": product_count or 0,
        "total_models": model_count or 0,
    }
