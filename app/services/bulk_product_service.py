"""Bounded, local-only catalog metadata edits with signed optimistic concurrency."""
import csv
import io
import re

from itsdangerous import BadData, URLSafeTimedSerializer
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.product import Product
from app.services.product_service import normalize_family_code, normalize_jenis_bahan

MAX_ROWS = 200
MAX_INPUT = 100_000
FIELDS = {"family_code": (100, normalize_family_code), "jenis_bahan": (255, normalize_jenis_bahan)}


class BulkEditError(ValueError):
    pass


def _signer():
    return URLSafeTimedSerializer(settings.session_secret_key, salt="bulk-product-preview-v1")


def parse_changes(ids: str = "", csv_text: str = "", **values) -> list[dict]:
    if len(ids) + len(csv_text) > MAX_INPUT:
        raise BulkEditError("Input is too large (maximum 100,000 characters).")
    if bool(ids.strip()) == bool(csv_text.strip()):
        raise BulkEditError("Provide either item IDs or CSV, not both.")
    if csv_text.strip():
        reader = csv.DictReader(io.StringIO(csv_text), strict=True)
        headers = reader.fieldnames or []
        if (len(set(headers)) != len(headers) or "item_id" not in headers
                or not set(headers) <= {"item_id", *FIELDS} or len(headers) < 2):
            raise BulkEditError("CSV headers must be item_id and family_code and/or jenis_bahan.")
        try:
            rows = []
            for row in reader:
                if len(rows) >= MAX_ROWS:
                    raise BulkEditError(f"Maximum {MAX_ROWS} rows per preview.")
                rows.append(row)
        except csv.Error as exc:
            raise BulkEditError("Invalid CSV.") from exc
    else:
        rows = [{"item_id": item_id, **values} for item_id in re.split(r"[\s,]+", ids.strip())]
    if not 1 <= len(rows) <= MAX_ROWS:
        raise BulkEditError(f"Provide 1–{MAX_ROWS} rows.")
    changes, seen = [], set()
    for row in rows:
        raw_id = row.get("item_id")
        if not isinstance(raw_id, str) or not re.fullmatch(r"[0-9]{1,19}", raw_id.strip()):
            raise BulkEditError("Every item_id must be a positive integer.")
        item_id = int(raw_id)
        if not 0 < item_id <= 9223372036854775807 or item_id in seen:
            raise BulkEditError("Item IDs must be unique positive 64-bit integers.")
        seen.add(item_id)
        if None in row or any(value is None for value in row.values()):
            raise BulkEditError("CSV rows must have exactly the header's columns.")
        change = {"item_id": item_id}
        for field, (limit, normalize) in FIELDS.items():
            if field in row:
                value = normalize(row[field])
                if value and (len(value) > limit or any(ord(c) < 32 for c in value)):
                    raise BulkEditError(f"{field} must be at most {limit} characters without control characters.")
                change[field] = value
        if len(change) == 1:
            raise BulkEditError("Select at least one field to update.")
        changes.append(change)
    return changes


async def preview_changes(session: AsyncSession, changes: list[dict], user_id: int) -> tuple[list[dict], str]:
    products = (await session.scalars(select(Product).where(
        Product.item_id.in_([row["item_id"] for row in changes])
    ))).all()
    by_id = {product.item_id: product for product in products}
    missing = sorted({row["item_id"] for row in changes} - by_id.keys())
    if missing:
        raise BulkEditError("Unknown item IDs: " + ", ".join(map(str, missing)))
    rows = []
    for change in changes:
        product = by_id[change["item_id"]]
        rows.append({"item_id": product.item_id, "name": product.item_name,
                     "before": {field: getattr(product, field) for field in FIELDS},
                     "after": {field: change.get(field, getattr(product, field)) for field in FIELDS}})
    token = _signer().dumps({"user_id": user_id, "rows": rows})
    return rows, token


async def apply_changes(session: AsyncSession, token: str, user_id: int) -> int:
    try:
        payload = _signer().loads(token, max_age=900)
    except BadData as exc:
        raise BulkEditError("Preview is invalid or expired. Generate a new preview.") from exc
    if payload.get("user_id") != user_id:
        raise BulkEditError("This preview belongs to another session user.")
    rows = payload["rows"]
    try:
        # Compare-and-swap each row in one transaction, including on SQLite.
        # A concurrent change or deletion rolls the entire batch back.
        for row in sorted(rows, key=lambda row: row["item_id"]):
            result = await session.execute(update(Product).where(
                Product.item_id == row["item_id"],
                *(getattr(Product, field).is_not_distinct_from(value)
                  for field, value in row["before"].items()),
            ).values(**row["after"]).execution_options(synchronize_session=False))
            if result.rowcount != 1:
                raise BulkEditError("Catalog changed since preview. Nothing saved; generate a new preview.")
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return len(rows)
