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
async def test_list_linked_product_families_groups_multi_product_families(seed_products):
    async with TestSession() as session:
        family_cards = await product_service.list_linked_product_families(session)

    assert [card["family_code"] for card in family_cards] == ["BC-1001", "MP-8002"]
    assert {product.item_id for product in family_cards[1]["products"]} == {1001, 1002}


@pytest.mark.asyncio
async def test_list_linked_product_families_prefers_sprei_image_source(seed_products):
    async with TestSession() as session:
        family_cards = await product_service.list_linked_product_families(session)

    mp_card = next(card for card in family_cards if card["family_code"] == "MP-8002")
    assert mp_card["image_source"].item_id == 1001
    assert mp_card["primary_image_url"] == "https://example.com/sprei-aurora-1.jpg"
    sprei_product = next(product for product in mp_card["products"] if product.item_id == 1001)
    assert sprei_product.display_stock == 12


@pytest.mark.asyncio
async def test_list_linked_product_families_falls_back_to_bedcover(seed_products):
    async with TestSession() as session:
        family_cards = await product_service.list_linked_product_families(session)

    bc_card = next(card for card in family_cards if card["family_code"] == "BC-1001")
    assert bc_card["image_source"].item_id == 1005
    assert bc_card["primary_image_url"] == "https://example.com/bedcover-blossom-1.jpg"


@pytest.mark.asyncio
async def test_list_products_filters_has_partners(seed_products):
    async with TestSession() as session:
        result = await product_service.list_products_paginated(session, partner_filter="has_partners")

    item_ids = {product.item_id for product in result["products"]}
    assert item_ids == {1001, 1002, 1005, 1006}
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
    assert item_ids == {1001, 1002, 1003, 1005, 1006}


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


@pytest.mark.asyncio
async def test_update_product_jenis_bahan_normalizes_value(seed_products):
    async with TestSession() as session:
        product = await product_service.update_product_jenis_bahan(session, 1001, "  Katun   Premium  ")

    assert product is not None
    assert product.jenis_bahan == "Katun Premium"


@pytest.mark.asyncio
async def test_update_product_jenis_bahan_allows_clearing(seed_products):
    async with TestSession() as session:
        product = await product_service.update_product_jenis_bahan(session, 1001, "")

    assert product is not None
    assert product.jenis_bahan is None


@pytest.mark.asyncio
async def test_get_related_products_includes_jenis_bahan(seed_products):
    async with TestSession() as session:
        detail = await product_service.get_related_products(session, 1001)

    assert detail["product"].jenis_bahan == "Katun Jepang"
    assert {product.jenis_bahan for product in detail["related_products"]} == {"Microtex"}


@pytest.mark.asyncio
async def test_list_products_exposes_jenis_bahan(seed_products):
    async with TestSession() as session:
        result = await product_service.list_products_paginated(session, search_sku="MP-8002")

    jenis_bahan_values = {product.jenis_bahan for product in result["products"]}
    assert jenis_bahan_values == {"Katun Jepang", "Microtex"}
