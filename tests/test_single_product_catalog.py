import pytest
from sqlalchemy import update

from app.models.product import Product
from app.services import product_service
from tests.conftest import TestSession


@pytest.mark.asyncio
async def test_single_product_family_matches_code_and_material(seed_products):
    async with TestSession() as session:
        await session.execute(
            update(Product)
            .where(Product.item_id == 1003)
            .values(family_code="KJ-P0718", jenis_bahan="KATUN JEPANG PREMIUM")
        )
        await session.commit()
        result = await product_service.list_linked_product_families(
            session,
            family_code=" kj-p0718 ",
            jenis_bahan="katun jepang premium",
        )

    assert result["total"] == 1
    card = result["family_cards"][0]
    assert card["family_code"] == "KJ-P0718"
    assert card["jenis_bahan"] == "KATUN JEPANG PREMIUM"
    assert card["product_count"] == 1
    assert card["primary_image_url"] == "https://example.com/solo-zz-9001.jpg"
    assert card["min_price"] == card["max_price"] == 175000
    assert card["products"][0].item_id == 1003
    assert card["products"][0].display_stock == 8


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("variants", "min_stock", "expected_total"),
    [(None, 1, 1), (["100x200"], 1, 1), (["100x200"], 2, 0), (["200x200"], 1, 0)],
)
async def test_single_product_with_models_preserves_variant_filters(
    seed_products, variants, min_stock, expected_total
):
    async with TestSession() as session:
        await product_service.update_product_family_code(session, 1007, "KJ-P0718")
        result = await product_service.list_linked_product_families(
            session, family_code="KJ-P0718", variants=variants, min_stock=min_stock
        )

    assert result["total"] == expected_total
    if expected_total:
        card = result["family_cards"][0]
        assert card["product_count"] == 1
        assert card["products"][0].item_id == 1007
        assert card["products"][0].display_stock == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("has_models", [False, True])
async def test_public_catalog_search_shows_single_product_family(client, seed_products, has_models):
    item_id = 1007 if has_models else 1003
    async with TestSession() as session:
        await session.execute(
            update(Product)
            .where(Product.item_id == item_id)
            .values(family_code="KJ-P0718", jenis_bahan="KATUN JEPANG PREMIUM")
        )
        await session.commit()

    response = await client.get(
        "/linked-products",
        params={"family_code": "kj-p0718", "jenis_bahan": "katun jepang premium"},
    )

    assert response.status_code == 200
    assert ">KJ-P0718</h2>" in response.text
    assert "1 linked families" in response.text
    assert "No linked product families found." not in response.text
    assert "Sprei Microtex Deluxe" in response.text if has_models else "Solo Product ZZ-9001" in response.text
