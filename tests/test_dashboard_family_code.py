import pytest
from sqlalchemy import select

from app.models.product import Product
from tests.conftest import TestSession

HTMX_HEADERS = {"HX-Request": "true"}


@pytest.mark.asyncio
async def test_inline_family_code_edit_requires_login(client, seed_products):
    resp = await client.get("/partials/products/1001/family-code/edit", headers=HTMX_HEADERS, follow_redirects=False)
    assert resp.status_code == 200
    assert resp.headers["HX-Redirect"] == "/login"


@pytest.mark.asyncio
async def test_inline_family_code_edit_renders_for_logged_in_user(logged_in_client, seed_products):
    resp = await logged_in_client.get("/partials/products/1001/family-code/edit", headers=HTMX_HEADERS)
    assert resp.status_code == 200
    assert "Edit Family Code" in resp.text
    assert "MP-8002" in resp.text


@pytest.mark.asyncio
async def test_inline_family_code_edit_submit_updates_product_and_returns_detail_panel(logged_in_client, seed_products):
    resp = await logged_in_client.post(
        "/partials/products/1004/family-code/edit",
        headers=HTMX_HEADERS,
        data={"family_code": "vr-je9002"},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert "Partner Products — VR-JE9002" in resp.text

    async with TestSession() as session:
        product = await session.scalar(select(Product).where(Product.item_id == 1004))

    assert product is not None
    assert product.family_code == "VR-JE9002"


@pytest.mark.asyncio
async def test_inline_family_code_edit_submit_allows_clearing(logged_in_client, seed_products):
    resp = await logged_in_client.post(
        "/partials/products/1001/family-code/edit",
        headers=HTMX_HEADERS,
        data={"family_code": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert "Partner Products" in resp.text

    async with TestSession() as session:
        product = await session.scalar(select(Product).where(Product.item_id == 1001))

    assert product is not None
    assert product.family_code is None


@pytest.mark.asyncio
async def test_inline_family_code_edit_submit_accepts_full_string_value(logged_in_client, seed_products):
    resp = await logged_in_client.post(
        "/partials/products/1001/family-code/edit",
        headers=HTMX_HEADERS,
        data={"family_code": "full string model 150x220"},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert "Partner Products — FULL STRING MODEL 150X220" in resp.text


@pytest.mark.asyncio
async def test_inline_family_code_edit_cancel_restores_detail_panel(logged_in_client, seed_products):
    resp = await logged_in_client.get("/partials/products/1001/models", headers=HTMX_HEADERS)
    assert resp.status_code == 200
    assert "Partner Products — MP-8002" in resp.text
    assert "Edit family code" in resp.text


@pytest.mark.asyncio
async def test_inline_family_code_edit_link_stays_on_dashboard_rows(logged_in_client, seed_products):
    resp = await logged_in_client.get("/partials/products", headers=HTMX_HEADERS)
    assert resp.status_code == 200
    assert 'hx-get="/partials/products/1001/family-code/edit?page=1"' in resp.text
    assert 'href="/products/1001/family-code/edit"' not in resp.text


@pytest.mark.asyncio
async def test_product_rows_pagination_preserves_partner_filter(logged_in_client, seed_products):
    async with TestSession() as session:
        for index in range(30):
            session.add(Product(
                shop_id=1,
                item_id=2000 + index,
                item_sku=f"NO-CODE-{index}",
                item_name=f"No Code Product {index:02d}",
                item_status="NORMAL",
                currency="IDR",
                current_price=100000,
                stock=1,
                has_model=False,
            ))
        await session.commit()

    resp = await logged_in_client.get("/partials/products?partner_filter=no_family_code", headers=HTMX_HEADERS)

    assert resp.status_code == 200
    assert 'hx-get="/partials/products?page=2&partner_filter=no_family_code"' in resp.text


@pytest.mark.asyncio
async def test_inline_jenis_bahan_edit_requires_login(client, seed_products):
    resp = await client.get("/partials/products/1001/jenis-bahan/edit", headers=HTMX_HEADERS, follow_redirects=False)
    assert resp.status_code == 200
    assert resp.headers["HX-Redirect"] == "/login"


@pytest.mark.asyncio
async def test_inline_jenis_bahan_edit_renders_for_logged_in_user(logged_in_client, seed_products):
    resp = await logged_in_client.get("/partials/products/1001/jenis-bahan/edit", headers=HTMX_HEADERS)
    assert resp.status_code == 200
    assert "Edit Jenis Bahan" in resp.text
    assert "Katun Jepang" in resp.text


@pytest.mark.asyncio
async def test_inline_jenis_bahan_edit_submit_updates_product_and_returns_detail_panel(logged_in_client, seed_products):
    resp = await logged_in_client.post(
        "/partials/products/1004/jenis-bahan/edit",
        headers=HTMX_HEADERS,
        data={"jenis_bahan": "  Cotton  Bamboo  "},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert "Jenis Bahan: Cotton Bamboo" in resp.text

    async with TestSession() as session:
        product = await session.scalar(select(Product).where(Product.item_id == 1004))

    assert product is not None
    assert product.jenis_bahan == "Cotton Bamboo"


@pytest.mark.asyncio
async def test_inline_jenis_bahan_edit_submit_allows_clearing(logged_in_client, seed_products):
    resp = await logged_in_client.post(
        "/partials/products/1001/jenis-bahan/edit",
        headers=HTMX_HEADERS,
        data={"jenis_bahan": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert "Jenis Bahan: -" in resp.text

    async with TestSession() as session:
        product = await session.scalar(select(Product).where(Product.item_id == 1001))

    assert product is not None
    assert product.jenis_bahan is None


@pytest.mark.asyncio
async def test_inline_jenis_bahan_edit_cancel_restores_detail_panel(logged_in_client, seed_products):
    resp = await logged_in_client.get("/partials/products/1001/models", headers=HTMX_HEADERS)
    assert resp.status_code == 200
    assert "Jenis Bahan: Katun Jepang" in resp.text
    assert "Edit jenis bahan" in resp.text


@pytest.mark.asyncio
async def test_inline_jenis_bahan_edit_link_stays_on_dashboard_rows(logged_in_client, seed_products):
    resp = await logged_in_client.get("/partials/products", headers=HTMX_HEADERS)
    assert resp.status_code == 200
    assert 'hx-get="/partials/products/1001/jenis-bahan/edit?page=1"' in resp.text
    assert 'href="/products/1001/jenis-bahan/edit"' not in resp.text
    assert "Katun Jepang" in resp.text


@pytest.mark.asyncio
async def test_non_variant_product_rows_include_inline_edit_targets(logged_in_client, seed_products):
    resp = await logged_in_client.get("/partials/products", headers=HTMX_HEADERS)
    assert resp.status_code == 200
    assert 'id="models-1004"' in resp.text
    assert 'hx-get="/partials/products/1004/family-code/edit?page=1"' in resp.text
    assert 'hx-get="/partials/products/1004/jenis-bahan/edit?page=1"' in resp.text


@pytest.mark.asyncio
async def test_non_variant_product_models_panel_returns_editable_panel(logged_in_client, seed_products):
    resp = await logged_in_client.get("/partials/products/1004/models", headers=HTMX_HEADERS)
    assert resp.status_code == 200
    assert "Partner Products" in resp.text
    assert "No partner products found for this family code." in resp.text
    assert "Set family code" in resp.text
    assert "Set jenis bahan" in resp.text
