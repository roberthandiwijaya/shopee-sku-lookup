import pytest


@pytest.mark.asyncio
async def test_linked_products_page_is_public(client, seed_products):
    response = await client.get("/linked-products")

    assert response.status_code == 200
    assert "Linked Product Families" in response.text
    assert "SPREI Aurora" in response.text
    assert "BEDCOVER Aurora" in response.text
    assert "Red / S" in response.text
    assert "10 pcs" in response.text
    assert "No variants" in response.text
    assert "PLAIN-SKU-4" not in response.text


@pytest.mark.asyncio
async def test_linked_products_page_uses_public_nav_without_login(client, seed_products):
    response = await client.get("/linked-products")

    assert response.status_code == 200
    assert "Linked Products" in response.text
    assert "Logout" not in response.text
    assert "Change Password" not in response.text
    assert 'href="/discounts"' not in response.text
    assert 'href="/"' not in response.text


@pytest.mark.asyncio
async def test_linked_products_page_filters_by_jenis_bahan_and_family_code(client, seed_products):
    response = await client.get("/linked-products?jenis_bahan=Microtex&family_code=vr-je9002")

    assert response.status_code == 200
    assert "VR-JE9002" in response.text
    assert "Sprei Microtex Deluxe" in response.text
    assert "BEDCOVER Aurora" not in response.text


@pytest.mark.asyncio
async def test_linked_products_page_filters_by_family_level_variants(client, seed_products):
    response = await client.get("/linked-products?variants=100x200,160x240&min_stock=1")

    assert response.status_code == 200
    assert "VR-JE9002" in response.text
    assert "100x200" in response.text
    assert "160x240" in response.text
    assert "SPREI Aurora" not in response.text


@pytest.mark.asyncio
async def test_linked_products_page_supports_partial_and_mixed_width_variant_queries(client, seed_products):
    partial_response = await client.get("/linked-products?variants=100,160x240&min_stock=1")
    spaced_response = await client.get("/linked-products?variants=160%20x%20240&min_stock=1")

    assert partial_response.status_code == 200
    assert "VR-JE9002" in partial_response.text
    assert "Sprei Microtex Deluxe" in partial_response.text
    assert spaced_response.status_code == 200
    assert "VR-JE9002" in spaced_response.text


@pytest.mark.asyncio
async def test_linked_products_page_excludes_families_when_variant_is_missing(client, seed_products):
    response = await client.get("/linked-products?variants=100x200,200x200")

    assert response.status_code == 200
    assert "No linked product families found." in response.text


@pytest.mark.asyncio
async def test_linked_products_page_shows_validation_error_for_invalid_family_code(client, seed_products):
    response = await client.get("/linked-products?jenis_bahan=100&family_code=150x220")

    assert response.status_code == 200
    assert "Family code must look like MP-8002 or VR-JE9002" in response.text
    assert "Internal Server Error" not in response.text
    assert "value=\"100\"" in response.text
    assert "value=\"150x220\"" in response.text
