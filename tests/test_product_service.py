import pytest

from app.services import product_service
from tests.conftest import TestSession


@pytest.mark.asyncio
async def test_list_products_matches_family_code(seed_products):
    async with TestSession() as session:
        result = await product_service.list_products_paginated(session, search_sku="MP-8002")

    item_ids = {product.item_id for product in result["products"]}
    assert item_ids == {1001, 1002}
    assert result["total"] == 2


@pytest.mark.asyncio
async def test_list_products_filters_has_partners(seed_products):
    async with TestSession() as session:
        result = await product_service.list_products_paginated(session, partner_filter="has_partners")

    item_ids = {product.item_id for product in result["products"]}
    assert item_ids == {1001, 1002}
    assert all(product.has_partners for product in result["products"])


@pytest.mark.asyncio
async def test_list_products_filters_no_partners(seed_products):
    async with TestSession() as session:
        result = await product_service.list_products_paginated(session, partner_filter="no_partners")

    item_ids = {product.item_id for product in result["products"]}
    assert item_ids == {1003, 1004}


@pytest.mark.asyncio
async def test_list_products_filters_has_family_code(seed_products):
    async with TestSession() as session:
        result = await product_service.list_products_paginated(session, partner_filter="has_family_code")

    item_ids = {product.item_id for product in result["products"]}
    assert item_ids == {1001, 1002, 1003}


@pytest.mark.asyncio
async def test_list_products_filters_no_family_code(seed_products):
    async with TestSession() as session:
        result = await product_service.list_products_paginated(session, partner_filter="no_family_code")

    item_ids = {product.item_id for product in result["products"]}
    assert item_ids == {1004}


@pytest.mark.asyncio
async def test_get_related_products_returns_family_siblings_and_models(seed_products):
    async with TestSession() as session:
        detail = await product_service.get_related_products(session, 1001)

    assert detail["product"].item_id == 1001
    assert {model.model_id for model in detail["models"]} == {5001, 5002}
    assert {product.item_id for product in detail["related_products"]} == {1002}


@pytest.mark.asyncio
async def test_get_related_products_returns_empty_for_unknown_item(seed_products):
    async with TestSession() as session:
        detail = await product_service.get_related_products(session, 9999)

    assert detail == {"product": None, "models": [], "related_products": []}


@pytest.mark.asyncio
async def test_update_product_family_code_normalizes_value(seed_products):
    async with TestSession() as session:
        product = await product_service.update_product_family_code(session, 1004, " mp-7001 ")

    assert product is not None
    assert product.family_code == "MP-7001"


@pytest.mark.asyncio
async def test_update_product_family_code_allows_clearing(seed_products):
    async with TestSession() as session:
        product = await product_service.update_product_family_code(session, 1001, "")

    assert product is not None
    assert product.family_code is None


@pytest.mark.asyncio
async def test_update_product_family_code_rejects_invalid_value(seed_products):
    async with TestSession() as session:
        with pytest.raises(ValueError):
            await product_service.update_product_family_code(session, 1001, "bad code")
