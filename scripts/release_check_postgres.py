"""Destructive checks ONLY for the private, disposable release-check PostgreSQL."""
import asyncio
from datetime import datetime, timedelta, timezone
import os
import subprocess

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.product import Base
import app.models.discount  # noqa: F401
import app.models.order  # noqa: F401
import app.models.webhook  # noqa: F401
from app.models.webhook import OrderRefreshJob
from app.models.catalog_export import CatalogExportJob
from app.services.catalog_export_service import claim_job
from app.services.order_refresh_service import claim_refresh_jobs

EXPECTED_URL = "postgresql+asyncpg://release:release-test-only@release-postgres:5432/release_checks"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


async def wait_ready(engine):
    for attempt in range(60):
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return
        except Exception:
            if attempt == 59:
                raise
            await asyncio.sleep(1)


def verify_schema(conn):
    inspector = inspect(conn)
    for table in Base.metadata.sorted_tables:
        require(inspector.has_table(table.name), f"Migration missing table {table.name}")
        actual = {column["name"] for column in inspector.get_columns(table.name)}
        require(set(table.columns.keys()) <= actual, f"Migration missing columns in {table.name}")


async def check_queues(engine):
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    async with sessions() as session:
        first = OrderRefreshJob(shop_id=1, order_sn="release-locked", priority=100, available_at=now)
        second = OrderRefreshJob(shop_id=1, order_sn="release-free", priority=10, available_at=now)
        session.add_all([first, second])
        await session.commit()
        first_id, second_id = first.id, second.id
    # A separate connection holds the highest priority row. The real claim service
    # must return the other row promptly, rather than blocking or double claiming.
    async with sessions() as locker, sessions() as worker:
        await locker.execute(select(OrderRefreshJob).where(OrderRefreshJob.id == first_id).with_for_update())
        claimed = await asyncio.wait_for(claim_refresh_jobs(worker, "release-worker-a", limit=1), 5)
        require([job.id for job in claimed] == [second_id], "Order SKIP LOCKED failed")
        await locker.rollback()
    async with sessions() as worker:
        claimed = await asyncio.wait_for(claim_refresh_jobs(worker, "release-worker-b", limit=1), 5)
        require([job.id for job in claimed] == [first_id], "Unlocked order not claimable")
        require(not await claim_refresh_jobs(worker, "release-worker-c", limit=2), "Live order leases claimed twice")
    async with sessions() as session:
        session.add_all([CatalogExportJob(
            id=str(n) * 32, token_hash="0" * 64, owner_hash="1" * 64,
            options={"family_codes": ["RELEASE"]}, status="pending", attempts=0,
            created_at=now + timedelta(seconds=n), expires_at=now + timedelta(hours=1),
        ) for n in (1, 2)])
        await session.commit()
    async with sessions() as locker, sessions() as worker:
        await locker.execute(select(CatalogExportJob).where(CatalogExportJob.id == "1" * 32).with_for_update())
        claimed = await asyncio.wait_for(claim_job(worker, "release-catalog-a"), 5)
        require(claimed is not None and claimed.id == "2" * 32, "Catalog SKIP LOCKED failed")
        await locker.rollback()
    async with sessions() as worker:
        require(await claim_job(worker, "release-catalog-b") is None, "Catalog singleton live lease was bypassed")


async def check_catalog_features(engine):
    """Exercise native PostgreSQL normalization, pagination, bulk CAS and saved selections."""
    from app.models.product import Product, ProductModel, DashboardUser
    from app.schemas.catalog import CollectionInput, CatalogSelection
    from app.services import product_service, bulk_product_service, catalog_collection_service
    from app.services.catalog_preflight_service import preflight

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        user = DashboardUser(username='release-check', password_hash='unused', salt='unused', must_change_password=False)
        first = Product(shop_id=1, item_id=900001, item_name='First', item_sku='FIRST',
                        family_code='CHECK-1', jenis_bahan='  Katun\t Premium  ', stock=10)
        second = Product(shop_id=1, item_id=900002, item_name='Second', item_sku='SECOND',
                         family_code='CHECK-1', jenis_bahan='Other', stock=5)
        session.add_all([user, first, second])
        await session.flush()
        session.add_all([
            ProductModel(product_id=first.id, model_id=900011, model_name='100\t ×  200', stock=3),
            ProductModel(product_id=second.id, model_id=900012, model_name='160 x 240', stock=2),
        ])
        await session.commit()
        result = await product_service.list_linked_product_families(
            session, jenis_bahan='katun premium', variants=['100x200', '160x240'], per_page=1)
        require(result['total'] == 1 and len(result['family_cards'][0]['products']) == 2,
                'PostgreSQL normalized family filters failed')
        result = await product_service.list_products_paginated(session, search_sku='FIRST', partner_filter='has_partners')
        require(result['total'] == 1 and result['products'][0].partner_count == 1, 'PostgreSQL partner counts failed')
        preview = await preflight(session, CatalogSelection(family_codes=['CHECK-1']))
        require(preview['matching_rows'] == 2 and preview['can_export'], 'PostgreSQL preflight failed')
        saved = await catalog_collection_service.save_collection(session, user.id,
                    CollectionInput(name='Release collection', family_codes=['CHECK-1']))
        require(saved['options']['family_codes'] == ['CHECK-1'], 'PostgreSQL collection save failed')
        changes = bulk_product_service.parse_changes(ids='900001,900002', jenis_bahan='Cotton')
        _, token = await bulk_product_service.preview_changes(session, changes, user.id)
        require(await bulk_product_service.apply_changes(session, token, user.id) == 2, 'PostgreSQL bulk apply failed')
    async with sessions() as session:
        materials = (await session.scalars(select(Product.jenis_bahan).where(Product.family_code == 'CHECK-1'))).all()
        require(materials == ['Cotton', 'Cotton'], 'PostgreSQL bulk changes did not persist')


async def main():
    require(os.environ.get("DATABASE_URL") == EXPECTED_URL, "Refusing any non-release database URL")
    engine = create_async_engine(EXPECTED_URL, connect_args={"command_timeout": 10})
    try:
        await wait_ready(engine)
        async with engine.connect() as conn:
            tables = await conn.run_sync(lambda c: inspect(c).get_table_names())
            require(not tables, "Release database must start empty")
        heads = ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
        require(len(heads) == 1, "Expected exactly one migration head")
        # No shell pipelines, no swallowed migration exit codes.
        for command in (["upgrade", "head"], ["upgrade", "head"], ["downgrade", "-1"], ["upgrade", "head"]):
            subprocess.run(["alembic", *command], check=True)
        async with engine.connect() as conn:
            current = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalars().all()
            require(current == heads, "Database did not reach migration head")
            await conn.run_sync(verify_schema)
        await check_queues(engine)
        await check_catalog_features(engine)
        print('PASS: native PostgreSQL catalog filters, partner counts, preflight, collections and bulk editing', flush=True)
        print("PASS: fresh migrations, idempotence, one-step rollback/reapply, model columns, both real SKIP LOCKED claims and live leases", flush=True)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
