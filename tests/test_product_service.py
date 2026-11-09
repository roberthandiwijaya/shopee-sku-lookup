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
async def test_list_linked_product_families_includes_single_and_multi_product_families(seed_products):
    async with TestSession() as session:
        result = await product_service.list_linked_product_families(session)

    family_cards = result["family_cards"]
    assert result["total"] == 4
    assert [card["family_code"] for card in family_cards] == ["BC-1001", "MP-8002", "VR-JE9002", "ZZ-9001"]
    assert {product.item_id for product in next(card for card in family_cards if card["family_code"] == "MP-8002")["products"]} == {1001, 1002}
    assert {product.item_id for product in next(card for card in family_cards if card["family_code"] == "VR-JE9002")["products"]} == {1007, 1008}


@pytest.mark.asyncio
async def test_list_linked_product_families_prefers_sprei_image_source(seed_products):
    async with TestSession() as session:
        result = await product_service.list_linked_product_families(session)

    mp_card = next(card for card in result["family_cards"] if card["family_code"] == "MP-8002")
    assert mp_card["image_source"].item_id == 1001
    assert mp_card["primary_image_url"] == "https://example.com/sprei-aurora-1.jpg"
    sprei_product = next(product for product in mp_card["products"] if product.item_id == 1001)
    assert sprei_product.display_stock == 12


@pytest.mark.asyncio
async def test_list_linked_product_families_falls_back_to_bedcover(seed_products):
    async with TestSession() as session:
        result = await product_service.list_linked_product_families(session)

    bc_card = next(card for card in result["family_cards"] if card["family_code"] == "BC-1001")
    assert bc_card["image_source"].item_id == 1005
    assert bc_card["primary_image_url"] == "https://example.com/bedcover-blossom-1.jpg"


@pytest.mark.asyncio
async def test_list_linked_product_families_filters_by_jenis_bahan(seed_products):
    async with TestSession() as session:
        result = await product_service.list_linked_product_families(session, jenis_bahan="microtex")

    assert [card["family_code"] for card in result["family_cards"]] == ["MP-8002", "VR-JE9002"]


@pytest.mark.asyncio
async def test_list_linked_product_families_filters_by_family_code(seed_products):
    async with TestSession() as session:
        result = await product_service.list_linked_product_families(session, family_code="vr-je9002")

    assert [card["family_code"] for card in result["family_cards"]] == ["VR-JE9002"]


@pytest.mark.asyncio
async def test_list_linked_product_families_paginates_final_family_cards(seed_products):
    async with TestSession() as session:
        page_one = await product_service.list_linked_product_families(session, page=1, per_page=2)
        page_two = await product_service.list_linked_product_families(session, page=2, per_page=2)

    assert page_one["total"] == 4
    assert page_one["total_pages"] == 2
    assert [card["family_code"] for card in page_one["family_cards"]] == ["BC-1001", "MP-8002"]
    assert [card["family_code"] for card in page_two["family_cards"]] == ["VR-JE9002", "ZZ-9001"]


@pytest.mark.asyncio
async def test_list_linked_product_families_pagination_preserves_family_level_filters(seed_products):
    async with TestSession() as session:
        result = await product_service.list_linked_product_families(
            session,
            jenis_bahan="microtex",
            page=1,
            per_page=1,
        )

    assert result["total"] == 2
    assert result["total_pages"] == 2
    assert [card["family_code"] for card in result["family_cards"]] == ["MP-8002"]


@pytest.mark.asyncio
async def test_list_linked_product_families_clamps_page_to_last_page(seed_products):
    async with TestSession() as session:
        result = await product_service.list_linked_product_families(session, page=99, per_page=2)

    assert result["page"] == 2
    assert [card["family_code"] for card in result["family_cards"]] == ["VR-JE9002", "ZZ-9001"]


@pytest.mark.asyncio
async def test_list_linked_product_families_clamps_page_to_first_page(seed_products):
    async with TestSession() as session:
        result = await product_service.list_linked_product_families(session, page=0, per_page=2)

    assert result["page"] == 1
    assert [card["family_code"] for card in result["family_cards"]] == ["BC-1001", "MP-8002"]


@pytest.mark.asyncio
async def test_list_linked_product_families_returns_empty_page_with_metadata(seed_products):
    async with TestSession() as session:
        result = await product_service.list_linked_product_families(session, family_code="missing")

    assert result == {
        "family_cards": [],
        "total": 0,
        "page": 1,
        "per_page": 25,
        "total_pages": 1,
    }


@pytest.mark.asyncio
async def test_list_linked_product_families_filters_by_family_variant_match(seed_products):
    async with TestSession() as session:
        result = await product_service.list_linked_product_families(
            session,
            variants=["100x200", "160x240"],
        )

    assert [card["family_code"] for card in result["family_cards"]] == ["VR-JE9002"]


@pytest.mark.asyncio
async def test_list_linked_product_families_supports_partial_and_mixed_width_variant_terms(seed_products):
    async with TestSession() as session:
        partial_cards = await product_service.list_linked_product_families(
            session,
            variants=["100", "160x240"],
        )
        spaced_cards = await product_service.list_linked_product_families(
            session,
            variants=["160 x 240"],
        )
        multiplied_cards = await product_service.list_linked_product_families(
            session,
            variants=["160×240"],
        )

    assert [card["family_code"] for card in partial_cards["family_cards"]] == ["VR-JE9002"]
    assert [card["family_code"] for card in spaced_cards["family_cards"]] == ["VR-JE9002"]
    assert [card["family_code"] for card in multiplied_cards["family_cards"]] == ["VR-JE9002"]


@pytest.mark.asyncio
async def test_list_linked_product_families_excludes_missing_or_understocked_variants(seed_products):
    async with TestSession() as session:
        missing_variant_cards = await product_service.list_linked_product_families(
            session,
            variants=["100x200", "200x200"],
        )
        understocked_cards = await product_service.list_linked_product_families(
            session,
            variants=["100x200", "180x200"],
        )
        threshold_cards = await product_service.list_linked_product_families(
            session,
            variants=["100x200", "160x240"],
            min_stock=2,
        )

    assert missing_variant_cards["family_cards"] == []
    assert understocked_cards["family_cards"] == []
    assert threshold_cards["family_cards"] == []


@pytest.mark.asyncio
async def test_list_products_filters_has_partners(seed_products):
    async with TestSession() as session:
        result = await product_service.list_products_paginated(session, partner_filter="has_partners")

    item_ids = {product.item_id for product in result["products"]}
    assert item_ids == {1001, 1002, 1005, 1006, 1007, 1008}
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
    assert item_ids == {1001, 1002, 1003, 1005, 1006, 1007, 1008}


@pytest.mark.asyncio
async def test_list_products_filters_no_family_code(seed_products):
    async with TestSession() as session:
        result = await product_service.list_products_paginated(session, partner_filter="no_family_code")

    item_ids = {product.item_id for product in result["products"]}
    assert item_ids == {1004}


@pytest.mark.asyncio
async def test_list_products_combines_no_family_code_filter_with_search(seed_products):
    async with TestSession() as session:
        result = await product_service.list_products_paginated(
            session,
            search_sku="katun jepang",
            partner_filter="no_family_code",
        )

    assert result["products"] == []


@pytest.mark.asyncio
async def test_list_products_combines_has_family_code_filter_with_search(seed_products):
    async with TestSession() as session:
        result = await product_service.list_products_paginated(
            session,
            search_sku="katun jepang",
            partner_filter="has_family_code",
        )

    item_ids = {product.item_id for product in result["products"]}
    assert item_ids == {1001}


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
        product = await product_service.update_product_family_code(session, 1004, " vr-je9002 ")

    assert product is not None
    assert product.family_code == "VR-JE9002"


@pytest.mark.asyncio
async def test_update_product_family_code_allows_clearing(seed_products):
    async with TestSession() as session:
        product = await product_service.update_product_family_code(session, 1001, "")

    assert product is not None
    assert product.family_code is None


@pytest.mark.asyncio
async def test_update_product_family_code_accepts_full_string_value(seed_products):
    async with TestSession() as session:
        product = await product_service.update_product_family_code(session, 1001, "  full string model 150x220  ")

    assert product is not None
    assert product.family_code == "FULL STRING MODEL 150X220"


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
