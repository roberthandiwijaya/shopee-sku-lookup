from sqlalchemy import select

from app.models.product import Product, ProductModel
from app.services import product_service as service
from tests.conftest import TestSession


async def test_partner_counts_independent_of_search_and_page(seed_products):
    async with TestSession() as session:
        result = await service.list_products_paginated(
            session, search_sku="katun jepang", partner_filter="has_partners", per_page=1,
        )
        assert result["total"] == 1
        assert result["products"][0].item_id == 1001
        assert result["products"][0].partner_count == 1
        assert not (await service.list_products_paginated(
            session, search_sku="katun jepang", partner_filter="no_partners"
        ))["products"]
        for page in (1, 2):
            result = await service.list_products_paginated(session, search_sku="MP-8002", page=page, per_page=1)
            assert result["total"] == 2
            assert result["products"][0].partner_count == 1


async def test_dashboard_hydrates_only_current_page(seed_products):
    async with TestSession() as session:
        result = await service.list_products_paginated(session, per_page=1)
        loaded = [obj for obj in session.identity_map.values() if isinstance(obj, Product)]
        assert len(loaded) == len(result["products"]) == 1
        assert result["total"] == 8


async def test_family_hydrates_only_current_family(seed_products):
    async with TestSession() as session:
        result = await service.list_linked_product_families(session, per_page=1)
        loaded = [obj for obj in session.identity_map.values() if isinstance(obj, Product)]
        assert len(loaded) == len(result["family_cards"][0]["products"]) == 2
        assert result["total"] == 4


async def test_legacy_normalization_and_cross_sibling_and_semantics(seed_products):
    async with TestSession() as session:
        product = await session.scalar(select(Product).where(Product.item_id == 1007))
        product.jenis_bahan = "  Katun\t  Premium  "
        model = await session.scalar(select(ProductModel).where(ProductModel.product_id == product.id).limit(1))
        model.model_name = "  100\t ×  200  "
        model.stock = 5
        await session.commit()
        result = await service.list_linked_product_families(
            session, jenis_bahan="katun premium", variants=["100x200", "160 x 240"], per_page=1,
        )
        assert result["total"] == 1
        assert result["family_cards"][0]["family_code"] == "VR-JE9002"
        assert len(result["family_cards"][0]["products"]) == 2
        assert not (await service.list_linked_product_families(
            session, variants=["100x200", "does_not_exist"]
        ))["family_cards"]


async def test_variant_percent_and_underscore_are_literal(seed_products):
    async with TestSession() as session:
        assert not (await service.list_linked_product_families(session, variants=["%_"]))["family_cards"]
