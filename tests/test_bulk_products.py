import re

import pytest
from sqlalchemy import select

from app.models.product import Product
from app.services import bulk_product_service as bulk
from tests.conftest import TestSession


@pytest.mark.parametrize("kwargs", [
    {"ids": "1,1", "family_code": "X"},
    {"ids": "0", "family_code": "X"},
    {"ids": "1", "family_code": "X" * 101},
    {"ids": "1", "jenis_bahan": "X" * 256},
    {"ids": "1"},
    {"ids": "1", "csv_text": "item_id,family_code\n1,X"},
    {"csv_text": "item_id,wrong\n1,X"},
    {"csv_text": "item_id,family_code\n1"},
    {"csv_text": "item_id,family_code\n1,X,extra"},
    {"ids": ",".join(map(str, range(1, 202))), "family_code": "X"},
    {"csv_text": "x" * 100001},
])
def test_bulk_rejects_invalid_input(kwargs):
    with pytest.raises(bulk.BulkEditError):
        bulk.parse_changes(**kwargs)


def test_bulk_normalizes_and_preserves_omitted_columns():
    assert bulk.parse_changes(csv_text="item_id,family_code\n1001,  mp-8002  \n1002,\n") == [
        {"item_id": 1001, "family_code": "MP-8002"},
        {"item_id": 1002, "family_code": None},
    ]
    assert bulk.parse_changes(ids="1001 1002", jenis_bahan=" Katun   Jepang ")[0] == {
        "item_id": 1001, "jenis_bahan": "Katun Jepang",
    }


async def test_bulk_preview_no_write_and_apply_atomic(seed_products):
    async with TestSession() as session:
        changes = bulk.parse_changes(ids="1001,1002", family_code=" new family ")
        rows, token = await bulk.preview_changes(session, changes, 1)
        assert rows[0]["before"]["family_code"] == "MP-8002"
        assert await session.scalar(select(Product.family_code).where(Product.item_id == 1001)) == "MP-8002"
        assert await bulk.apply_changes(session, token, 1) == 2
        assert await session.scalar(select(Product.family_code).where(Product.item_id == 1002)) == "NEW FAMILY"
        assert await session.scalar(select(Product.jenis_bahan).where(Product.item_id == 1002)) == "Microtex"


async def test_bulk_unknown_id_rejects_entire_preview(seed_products):
    async with TestSession() as session:
        with pytest.raises(bulk.BulkEditError, match="Unknown"):
            await bulk.preview_changes(session, bulk.parse_changes(ids="1001,9999", family_code="X"), 1)
        assert await session.scalar(select(Product.family_code).where(Product.item_id == 1001)) == "MP-8002"


async def test_bulk_stale_preview_rolls_back_previous_rows(seed_products):
    async with TestSession() as session:
        _, token = await bulk.preview_changes(session, bulk.parse_changes(ids="1001,1002", family_code="X"), 1)
        product = await session.scalar(select(Product).where(Product.item_id == 1002))
        product.jenis_bahan = "Changed after preview"
        await session.commit()
        with pytest.raises(bulk.BulkEditError, match="changed since preview"):
            await bulk.apply_changes(session, token, 1)
        assert await session.scalar(select(Product.family_code).where(Product.item_id == 1001)) == "MP-8002"
        assert await session.scalar(select(Product.jenis_bahan).where(Product.item_id == 1002)) == "Changed after preview"


async def test_bulk_rejects_tampering_wrong_user_and_expiry(seed_products, monkeypatch):
    async with TestSession() as session:
        _, token = await bulk.preview_changes(session, bulk.parse_changes(ids="1001", family_code="X"), 1)
        for value, user in [(token + "tampered", 1), (token, 2)]:
            with pytest.raises(bulk.BulkEditError):
                await bulk.apply_changes(session, value, user)
        from itsdangerous.timed import TimestampSigner
        monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: 1)
        _, old = await bulk.preview_changes(session, bulk.parse_changes(ids="1001", family_code="X"), 1)
        monkeypatch.undo()
        with pytest.raises(bulk.BulkEditError, match="expired"):
            await bulk.apply_changes(session, old, 1)


async def test_bulk_requires_login(client):
    response = await client.get("/products/bulk", follow_redirects=False)
    assert response.status_code in (302, 303)


async def test_bulk_editor_csrf_preview_apply(logged_in_client, seed_products):
    client = logged_in_client
    page = await client.get("/products/bulk")
    assert page.status_code == 200
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    data = {"csrf_token": csrf, "item_ids": "1001,1002", "edit_family_code": "yes", "family_code": "new"}
    assert (await client.post("/products/bulk/preview", data={"item_ids": "1001"})).status_code == 403
    assert (await client.post("/products/bulk/preview", data=data, headers={"Origin": "https://evil.example"})).status_code == 403
    preview = await client.post("/products/bulk/preview", data=data)
    assert preview.status_code == 200
    token = re.search(r'name="preview_token" value="([^"]+)"', preview.text).group(1)
    response = await client.post("/products/bulk/apply", data={"csrf_token": csrf, "preview_token": token, "confirm": "yes"})
    assert response.status_code == 200
    assert "Saved local metadata for 2 products" in response.text


async def test_bulk_deleted_product_rolls_back_batch(seed_products):
    from sqlalchemy import delete
    async with TestSession() as session:
        _, token = await bulk.preview_changes(session, bulk.parse_changes(ids="1001,1002", family_code="X"), 1)
        await session.execute(delete(Product).where(Product.item_id == 1002))
        await session.commit()
        with pytest.raises(bulk.BulkEditError, match="changed since preview"):
            await bulk.apply_changes(session, token, 1)
        assert await session.scalar(select(Product.family_code).where(Product.item_id == 1001)) == "MP-8002"


async def test_bulk_preview_escapes_user_text(logged_in_client, seed_products):
    page = await logged_in_client.get("/products/bulk")
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    response = await logged_in_client.post("/products/bulk/preview", data={
        "csrf_token": csrf, "item_ids": "1001", "edit_jenis_bahan": "yes",
        "jenis_bahan": "<script>alert(1)</script>",
    })
    assert response.status_code == 200
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;" in response.text


async def test_bulk_rejects_oversized_request(logged_in_client):
    response = await logged_in_client.post("/products/bulk/preview", content=b"x" * 600001,
                                           headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert response.status_code == 413
