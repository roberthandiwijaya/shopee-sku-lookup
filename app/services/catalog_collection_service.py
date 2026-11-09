"""Owner-scoped saved collections; a user-row lock enforces the collection cap."""
from datetime import datetime

from sqlalchemy import func, select

from app.models.catalog_collection import CatalogCollection
from app.models.product import DashboardUser, TZ_GMT7

MAX_COLLECTIONS = 50


def serialize(collection):
    return {"id": collection.id, "name": collection.name, "options": collection.options,
            "updated_at": collection.updated_at.isoformat()}


async def list_collections(session, user_id):
    rows = await session.scalars(select(CatalogCollection).where(CatalogCollection.user_id == user_id)
                                 .order_by(CatalogCollection.name, CatalogCollection.id).limit(MAX_COLLECTIONS))
    return [serialize(row) for row in rows]


async def owned_collection(session, user_id, collection_id):
    return await session.scalar(select(CatalogCollection).where(
        CatalogCollection.id == collection_id, CatalogCollection.user_id == user_id))


async def save_collection(session, user_id, payload, collection=None):
    # Serialize mutations per user in PostgreSQL, including duplicate-name/cap checks.
    await session.scalar(select(DashboardUser).where(DashboardUser.id == user_id).with_for_update())
    duplicate = await session.scalar(select(CatalogCollection).where(
        CatalogCollection.user_id == user_id, CatalogCollection.name == payload.name))
    if duplicate and (collection is None or duplicate.id != collection.id):
        raise ValueError("A collection with that name already exists. Choose another name or update it.")
    if collection is None:
        count = await session.scalar(select(func.count()).select_from(CatalogCollection)
                                     .where(CatalogCollection.user_id == user_id))
        if count >= MAX_COLLECTIONS:
            raise ValueError(f"Keep at most {MAX_COLLECTIONS} collections. Delete one before saving another.")
        collection = CatalogCollection(user_id=user_id)
        session.add(collection)
    collection.name = payload.name
    collection.options = payload.model_dump(exclude={"name"})
    collection.updated_at = datetime.now(TZ_GMT7)
    await session.commit()
    await session.refresh(collection)
    return serialize(collection)
