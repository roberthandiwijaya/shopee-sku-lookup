import math
import re
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from app.config import settings
from app.models.product import AuthAlert, Product, ProductModel, ShopeeToken, ShopInfo, TZ_GMT7
from app.schemas.product import ProductOut, ProductSearchResponse

FAMILY_CODE_PATTERN = re.compile(r"\b([A-Z]{1,5}-[A-Z0-9]{2,10})\b", re.IGNORECASE)


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


def _get_family_image_priority(product: Product) -> tuple[int, str]:
    name = (product.item_name or "").upper()
    if "SPREI" in name:
        return (0, name)
    if "BEDCOVER" in name:
        return (1, name)
    if _get_product_image_urls(product):
        return (2, name)
    return (3, name)


def _select_family_image_source(products: list[Product]) -> Product | None:
    if not products:
        return None
    ordered = sorted(products, key=_get_family_image_priority)
    for product in ordered:
        if _get_product_image_urls(product):
            return product
    return ordered[0]


def _get_product_display_stock(product: Product) -> int | None:
    if product.stock is not None:
        return product.stock
    if product.models:
        stocks = [model.stock for model in product.models if model.stock is not None]
        if stocks:
            return sum(stocks)
    return None


def _build_family_card(family_code: str, products: list[Product]) -> dict:
    ordered_products = sorted(products, key=lambda product: ((product.item_name or "").upper(), product.item_id))
    image_source = _select_family_image_source(ordered_products)
    image_urls = _get_product_image_urls(image_source) if image_source else []
    prices = [float(product.current_price) for product in ordered_products if product.current_price is not None]
    family_jenis_bahan = next((product.jenis_bahan for product in ordered_products if product.jenis_bahan), None)

    for product in ordered_products:
        product.display_stock = _get_product_display_stock(product)

    return {
        "family_code": family_code,
        "jenis_bahan": family_jenis_bahan,
        "products": ordered_products,
        "product_count": len(ordered_products),
        "image_source": image_source,
        "image_source_label": image_source.item_name if image_source else None,
        "image_urls": image_urls,
        "primary_image_url": image_urls[0] if image_urls else None,
        "min_price": min(prices) if prices else None,
        "max_price": max(prices) if prices else None,
    }


def _normalize_variant_term(value: str) -> str:
    normalized = " ".join(value.strip().upper().split())
    normalized = normalized.replace("×", "X")
    normalized = normalized.replace(" X ", "X")
    normalized = normalized.replace(" X", "X")
    normalized = normalized.replace("X ", "X")
    return normalized


def _normalize_linked_product_variants(variants: list[str] | None) -> list[str]:
    if not variants:
        return []

    normalized_variants: list[str] = []
    seen: set[str] = set()
    for value in variants:
        for part in value.split(","):
            normalized = _normalize_variant_term(part)
            if normalized and normalized not in seen:
                normalized_variants.append(normalized)
                seen.add(normalized)
    return normalized_variants


def _matches_variant_filters(products: list[Product], variants: list[str], min_stock: int) -> bool:
    if not variants:
        return True

    matched_variants: set[str] = set()
    for requested_variant in variants:
        for product in products:
            for model in product.models:
                model_name = _normalize_variant_term(model.model_name or "")
                if requested_variant in model_name and (model.stock or 0) >= min_stock:
                    matched_variants.add(requested_variant)
                    break
            if requested_variant in matched_variants:
                break

    return len(matched_variants) == len(variants)


async def list_catalog_materials(session: AsyncSession) -> list[str]:
    """List normalized materials from all catalog families, independent of filters/pages."""
    result = await session.scalars(
        select(Product.jenis_bahan).distinct().where(
            Product.family_code.isnot(None), Product.family_code != "",
            Product.jenis_bahan.isnot(None), Product.jenis_bahan != "",
        )
    )
    materials = {
        normalized.upper()
        for value in result
        if (normalized := normalize_jenis_bahan(value))
    }
    return sorted(materials)


async def list_linked_product_families(
    session: AsyncSession,
    jenis_bahan: str | None = None,
    family_code: str | None = None,
    variants: list[str] | None = None,
    min_stock: int = 1,
    page: int = 1,
    per_page: int = 25,
) -> dict:
    # Filter and page family keys first; hydrate only the selected families.
    normalized_material = normalize_jenis_bahan(jenis_bahan)
    normalized_family = normalize_family_code(family_code)
    requested_variants = _normalize_linked_product_variants(variants)
    per_page = max(1, per_page)
    families = select(Product.family_code).where(
        Product.family_code.isnot(None), Product.family_code != ""
    ).distinct()
    if normalized_family:
        families = families.where(Product.family_code == normalized_family)
    if normalized_material:
        material = await _normalized_sql(session, Product.jenis_bahan)
        families = families.where(Product.family_code.in_(
            select(Product.family_code).where(material == normalized_material.upper())
        ))
    if requested_variants:
        model_name = await _normalized_sql(session, ProductModel.model_name, variant=True)
        for term in requested_variants:
            # Each term may match a different sibling/model, but all must match.
            families = families.where(Product.family_code.in_(
                select(Product.family_code).join(Product.models).where(
                    model_name.contains(term, autoescape=True),
                    ProductModel.stock >= max(min_stock, 1),
                )
            ))
    total = await session.scalar(select(func.count()).select_from(families.subquery())) or 0
    total_pages = max(1, math.ceil(total / per_page))
    current_page = min(max(page, 1), total_pages)
    keys = (await session.scalars(
        families.order_by(Product.family_code).offset((current_page - 1) * per_page).limit(per_page)
    )).all()
    grouped: dict[str, list[Product]] = defaultdict(list)
    if keys:
        products = await session.scalars(
            select(Product).options(selectinload(Product.models))
            .where(Product.family_code.in_(keys))
        )
        for product in products:
            grouped[product.family_code].append(product)
    return {
        "family_cards": [_build_family_card(key, grouped[key]) for key in keys],
        "total": total, "page": current_page, "per_page": per_page,
        "total_pages": total_pages,
    }


async def _normalized_sql(session: AsyncSession, column, variant: bool = False):
    """Normalize legacy whitespace in SQL, without hydrating the catalog."""
    if session.get_bind().dialect.name == "sqlite":
        # SQLite has no regexp_replace; install an equivalent scalar on this connection.
        connection = await session.connection()
        await connection.run_sync(lambda conn: conn.connection.dbapi_connection.create_function(
            "catalog_normalize_variant" if variant else "catalog_normalize_text", 1,
            (lambda value: _normalize_variant_term(value or "")) if variant
            else (lambda value: " ".join((value or "").split()).upper()),
            deterministic=True,
        ))
        return (func.catalog_normalize_variant(column) if variant else func.catalog_normalize_text(column))
    value = func.trim(func.regexp_replace(func.upper(func.coalesce(column, "")), r"\s+", " ", "g"))
    if variant:
        value = func.replace(value, "×", "X")
        value = func.replace(func.replace(func.replace(value, " X ", "X"), " X", "X"), "X ", "X")
    return value


def normalize_family_code(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.strip().upper().split())
    return normalized or None


def normalize_jenis_bahan(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.strip().split())
    return normalized or None


async def list_products_paginated(
    session: AsyncSession,
    page: int = 1,
    per_page: int = 25,
    search_sku: str | None = None,
    partner_filter: str | None = None,
) -> dict:
    """Page in SQL; partner counts always describe the entire local catalog."""
    sibling = aliased(Product)
    partner_count = (
        select(func.count(sibling.id)).where(
            sibling.family_code == Product.family_code,
            sibling.id != Product.id, Product.family_code != "",
        ).correlate(Product).scalar_subquery()
    )
    conditions = []
    if search_sku:
        pattern = f"%{search_sku}%"
        conditions.append(or_(
            Product.item_name.ilike(pattern), Product.item_sku.ilike(pattern),
            Product.family_code.ilike(pattern), Product.jenis_bahan.ilike(pattern),
            Product.models.any(ProductModel.model_sku.ilike(pattern)),
            Product.family_code == search_sku.strip().upper(),
        ))
    if partner_filter == "has_partners":
        conditions.append(partner_count > 0)
    elif partner_filter == "no_partners":
        conditions.append(partner_count == 0)
    elif partner_filter == "has_family_code":
        conditions.extend([Product.family_code.isnot(None), Product.family_code != ""])
    elif partner_filter == "no_family_code":
        conditions.append(or_(Product.family_code.is_(None), Product.family_code == ""))
    total = await session.scalar(select(func.count(Product.id)).where(*conditions)) or 0
    per_page, page = max(1, per_page), max(1, page)
    rows = await session.execute(
        select(Product, partner_count.label("partner_count"))
        .options(selectinload(Product.models)).where(*conditions)
        .order_by(Product.item_name, Product.item_id)
        .offset((page - 1) * per_page).limit(per_page)
    )
    products = []
    for product, count in rows:
        product.image_urls = _get_product_image_urls(product)
        product.primary_image_url = product.image_urls[0] if product.image_urls else None
        product.partner_count = count
        product.has_partners = count > 0
        products.append(product)
    return {
        "products": products, "total": total, "page": page, "per_page": per_page,
        "total_pages": max(1, math.ceil(total / per_page)), "partner_filter": partner_filter,
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


async def update_product_jenis_bahan(
    session: AsyncSession, item_id: int, jenis_bahan: str | None
) -> Product | None:
    product = await get_product_by_item_id(session, item_id)
    if product is None:
        return None
    product.jenis_bahan = normalize_jenis_bahan(jenis_bahan)
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
