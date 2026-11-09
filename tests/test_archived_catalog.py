"""Archived variants stay recoverable but never leak into current catalog reads."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.product import Product, ProductModel, TZ_GMT7
from app.schemas.catalog import CatalogSelection
from app.services import product_service, catalog_pdf_service
from app.services.catalog_preflight_service import preflight
from tests.conftest import TestSession

pytestmark = pytest.mark.asyncio


async def test_archived_variants_hidden_from_lookup_filters_and_freshness(seed_products):
    async with TestSession() as session:
        variant = await session.scalar(select(ProductModel).where(ProductModel.model_id == 5001))
        variant.is_active = False
        variant.synced_at = datetime.now(TZ_GMT7) - timedelta(days=30)
        await session.commit()
    async with TestSession() as session:
        result = await product_service.lookup_by_skus(session, ['CHILD-SKU-1A'])
        assert result.count == 0
        assert result.skus_not_found == ['CHILD-SKU-1A']
        product = await product_service.get_product_by_item_id(session, 1001)
        assert [m.model_id for m in product.models] == [5002]
        filtered = await product_service.list_linked_product_families(session, family_code='MP-8002', variants=['Red'])
        assert filtered['total'] == 0
        checked = await preflight(session, CatalogSelection(family_codes=['MP-8002'], stock_mode='all'))
        assert checked['matching_rows'] == 2 and checked['warnings'] == []
        cards, _ = await catalog_pdf_service.get_export_cards(session, ['MP-8002'], stock_mode='all')
        assert sum(len(p['rows']) for c in cards for p in c['products']) == 2
        assert await session.scalar(select(ProductModel.id).where(ProductModel.model_id == 5001)) is not None


async def test_unlisted_products_excluded_publicly_but_retained_for_ops(seed_products):
    async with TestSession() as session:
        product = await session.scalar(select(Product).where(Product.item_id == 1003))
        product.item_status = 'UNLIST'
        await session.commit()
    async with TestSession() as session:
        public = await product_service.list_linked_product_families(session, family_code='ZZ-9001')
        assert public['total'] == 0
        checked = await preflight(session, CatalogSelection(family_codes=['ZZ-9001']))
        assert not checked['can_export'] and checked['missing'] == ['ZZ-9001']
        with pytest.raises(ValueError):
            await catalog_pdf_service.get_export_cards(session, ['ZZ-9001'])
        assert (await product_service.lookup_by_skus(session, ['SOLO-SKU-3'])).count == 1


async def test_no_fake_standard_variant_when_all_models_archived(seed_products):
    async with TestSession() as session:
        variants = (await session.scalars(select(ProductModel).where(ProductModel.item_id == 1001))).all()
        for variant in variants:
            variant.is_active = False
        await session.commit()
    async with TestSession() as session:
        checked = await preflight(session, CatalogSelection(family_codes=['MP-8002'], stock_mode='all'))
        cards, _ = await catalog_pdf_service.get_export_cards(session, ['MP-8002'], stock_mode='all')
        assert checked['matching_rows'] == 1
        assert len(cards[0]['products']) == 1 and cards[0]['products'][0]['name'] == 'BEDCOVER Aurora'
