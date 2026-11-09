import io
from unittest.mock import AsyncMock

import pytest
from PIL import Image
from pypdf import PdfReader
from sqlalchemy import update

from app.models.product import Product
from app.routers import public
from app.services import catalog_pdf_service as pdf
from tests.conftest import TestSession


@pytest.fixture(autouse=True)
def no_remote_images(monkeypatch):
    monkeypatch.setattr(pdf, "fetch_catalog_images", AsyncMock(return_value={}))


def read_pdf(data):
    reader = PdfReader(io.BytesIO(data))
    return reader, "\n".join(page.extract_text() for page in reader.pages)


@pytest.mark.asyncio
async def test_export_is_public_and_selected_only(client, seed_products):
    response = await client.post("/linked-products/export.pdf", json={"family_codes": [" zz-9001 "]})
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"
    reader, text = read_pdf(response.content)
    assert len(reader.pages) == 1
    assert "ZZ-9001" in text and "Solo Product" in text and "8 pcs" in text
    assert "MP-8002" not in text
    assert "WIB" in text


@pytest.mark.asyncio
async def test_export_filters_rows_not_just_families(client, seed_products):
    response = await client.post("/linked-products/export.pdf", json={
        "family_codes": ["VR-JE9002"], "min_stock": 2,
    })
    assert response.status_code == 200
    _, text = read_pdf(response.content)
    assert "120x200" in text
    assert "100x200" not in text
    assert "160x240" not in text
    assert "180x200" not in text


@pytest.mark.asyncio
async def test_export_all_stock_with_quantities_hidden(client, seed_products):
    response = await client.post("/linked-products/export.pdf", json={
        "family_codes": ["VR-JE9002"], "stock_mode": "all", "show_stock": False,
    })
    assert response.status_code == 200
    _, text = read_pdf(response.content)
    assert "180x200" in text and "Out of stock" in text and "Available" in text
    assert " pcs" not in text


@pytest.mark.asyncio
async def test_export_omits_empty_family_with_notice(client, seed_products):
    async with TestSession() as session:
        await session.execute(update(Product).where(Product.item_id == 1003).values(stock=0))
        await session.commit()
    response = await client.post("/linked-products/export.pdf", json={"family_codes": ["ZZ-9001", "MP-8002"]})
    assert response.status_code == 200
    assert response.headers["x-catalog-omitted-families"] == "1"
    _, text = read_pdf(response.content)
    assert "1 selected families omitted" in text


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"family_codes": []}, {"family_codes": ["missing"]}, {"family_codes": [" "]},
    {"family_codes": ["X" * 101]}, {"family_codes": [str(i) for i in range(31)]},
    {"family_codes": ["ZZ-9001"], "min_stock": 0},
    {"family_codes": ["ZZ-9001"], "stock_mode": "bad"},
    {"family_codes": ["ZZ-9001"], "min_stock": 99999},
])
async def test_export_rejects_invalid_or_empty_requests(client, seed_products, payload):
    response = await client.post("/linked-products/export.pdf", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_export_gate_rejects_concurrent_jobs(client, seed_products):
    async with public._export_gate:
        response = await client.post("/linked-products/export.pdf", json={"family_codes": ["ZZ-9001"]})
    assert response.status_code == 429


@pytest.mark.asyncio
async def test_selection_deduplicates_codes_and_ignores_catalog_pagination(seed_products):
    async with TestSession() as session:
        cards, omitted = await pdf.get_export_cards(session, ["zz-9001", "ZZ-9001", "BC-1001"])
    assert [card["family_code"] for card in cards] == ["BC-1001", "ZZ-9001"]
    assert omitted == 0


def test_many_variants_continue_and_next_family_starts_new_page():
    cards = [{"family_code": "LONG-001", "jenis_bahan": "Cotton", "primary_image_url": None,
              "products": [{"name": "Long product", "rows": [
                  {"name": f"Variant {i:03d}", "price": 123456, "stock": 4} for i in range(85)
              ]}]},
             {"family_code": "NEXT-002", "jenis_bahan": "Cotton", "primary_image_url": None,
              "products": [{"name": "Next product", "rows": [{"name": "Only", "price": None, "stock": None}]}]}]
    reader, text = read_pdf(pdf.render_catalog_pdf(cards))
    assert len(reader.pages) > 2
    assert "Variant 000" in text and "Variant 084" in text
    assert "NEXT-002" in reader.pages[-1].extract_text()
    assert "LONG-001" not in reader.pages[-1].extract_text()
    for page in reader.pages[:-1]:
        page_text = page.extract_text()
        if "Variant" in page_text:
            assert "SIZE / VARIANT" in page_text and "Long product" in page_text
            assert "LONG-001" in page_text


def test_embeds_image_and_escapes_markup():
    image = io.BytesIO()
    Image.new("RGB", (40, 40), "green").save(image, "PNG")
    cards = [{"family_code": "A&B", "jenis_bahan": "<Cotton>", "primary_image_url": "photo",
              "products": [{"name": "Name <test>", "rows": [{"name": "A&B", "price": 1000, "stock": 2}]}]}]
    reader, text = read_pdf(pdf.render_catalog_pdf(cards, images={"photo": image.getvalue()}))
    assert "Name <test>" in text
    assert len(reader.pages[0].images) == 1
    _, text = read_pdf(pdf.render_catalog_pdf(cards, images={"photo": b"not an image"}))
    assert "Product image unavailable" in text


@pytest.mark.parametrize("url", ["http://cf.shopee.co.id/a", "https://127.0.0.1/a", "https://cf.shopee.co.id.evil.test/a",
                                  "https://user@cf.shopee.co.id/a", "https://cf.shopee.co.id:8000/a", None, "file:///etc/passwd"])
def test_image_url_rejects_untrusted_hosts(url):
    assert not pdf.is_allowed_image_url(url)


@pytest.mark.asyncio
async def test_page_has_export_controls(client, seed_products):
    response = await client.get("/linked-products")
    for value in ["catalog-export-form", "catalog-family-select", "export-stock-mode", "export-show-stock", "/linked-products/exports"]:
        assert value in response.text
