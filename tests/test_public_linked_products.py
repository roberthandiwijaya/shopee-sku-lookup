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
