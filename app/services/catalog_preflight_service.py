"""Read-only export validation; no jobs, images, or Shopee calls."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select

from app.models.product import Product, ProductModel
from app.schemas.catalog import CatalogSelection


async def preflight(session, options: CatalogSelection) -> dict:
    stock = case((ProductModel.id.is_not(None), ProductModel.stock), else_=Product.stock)
    included = stock >= options.min_stock if options.stock_mode == "available" else True
    query = (
        select(
            Product.family_code,
            func.count().label("rows"),
            func.sum(case((included, 1), else_=0)).label("matching"),
            func.min(Product.synced_at).label("product_sync"),
            func.min(ProductModel.synced_at).label("model_sync"),
            func.sum(case((Product.synced_at.is_(None), 1), else_=0)).label("unknown_products"),
            func.sum(case((ProductModel.id.is_not(None) & ProductModel.synced_at.is_(None), 1), else_=0)).label("unknown_models"),
        )
        .outerjoin(ProductModel, ProductModel.product_id == Product.id)
        .where(Product.family_code.in_(options.family_codes))
        .group_by(Product.family_code)
    )
    found = {row.family_code: row for row in (await session.execute(query)).all()}
    missing, excluded, families = [], [], []
    timestamps = []
    unknown = False
    for code in options.family_codes:
        row = found.get(code)
        if row is None:
            missing.append(code)
            families.append({"family_code": code, "status": "missing", "rows": 0, "matching_rows": 0})
            continue
        matching = int(row.matching or 0)
        if not matching:
            excluded.append(code)
        for stamp in (row.product_sync, row.model_sync):
            if stamp:
                # SQLite loses tzinfo; runtime timestamps are stored as WIB in this app.
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone(timedelta(hours=7)))
                timestamps.append(stamp)
        unknown = unknown or bool(row.unknown_products or row.unknown_models)
        families.append({"family_code": code, "status": "included" if matching else "excluded",
                         "rows": row.rows, "matching_rows": matching})
    matching_rows = sum(family["matching_rows"] for family in families)
    errors = []
    if missing:
        errors.append("Remove missing families before exporting.")
    if not matching_rows:
        errors.append("No rows match the stock filter.")
    if matching_rows > 25_000:
        errors.append("Export exceeds 25000 variant rows. Select fewer families.")
    oldest = min(timestamps) if timestamps else None
    stale = oldest is None or oldest < datetime.now(timezone.utc) - timedelta(hours=2)
    warnings = []
    if excluded:
        warnings.append(f"{len(excluded)} families will be omitted by the stock filter.")
    if stale or unknown:
        warnings.append("Some source data is older than two hours or has unknown freshness. Export creation time is not a Shopee refresh.")
    return {"can_export": not errors, "families": families, "missing": missing,
            "excluded": excluded, "matching_rows": matching_rows,
            "included_families": sum(f["status"] == "included" for f in families),
            "source_oldest_synced_at": oldest.isoformat() if oldest else None,
            "source_freshness_unknown": unknown, "warnings": warnings, "errors": errors}
