from datetime import datetime, timedelta

import pytest

from app.config import settings
from app.models.product import ShopeeToken, TZ_GMT7
from tests.conftest import TEST_API_KEY, TestSession

API_HEADERS = {"X-API-Key": TEST_API_KEY}


@pytest.mark.asyncio
async def test_get_products_by_parent_sku(client, seed_products):
    resp = await client.get("/api/products", params={"sku": "PARENT-SKU-1"}, headers=API_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["products"][0]["item_sku"] == "PARENT-SKU-1"
    assert len(data["products"][0]["models"]) == 2


@pytest.mark.asyncio
async def test_get_products_by_model_sku(client, seed_products):
    resp = await client.get("/api/products", params={"sku": "CHILD-SKU-1A"}, headers=API_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["products"][0]["item_id"] == 1001


@pytest.mark.asyncio
async def test_get_products_multiple_skus(client, seed_products):
    resp = await client.get("/api/products", params={"sku": "PARENT-SKU-1,PARENT-SKU-2"}, headers=API_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 2


@pytest.mark.asyncio
async def test_get_products_not_found(client, seed_products):
    resp = await client.get("/api/products", params={"sku": "NONEXISTENT"}, headers=API_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 0
    assert "NONEXISTENT" in data["skus_not_found"]


@pytest.mark.asyncio
async def test_products_missing_api_key(client, seed_products):
    resp = await client.get("/api/products", params={"sku": "PARENT-SKU-1"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_products_invalid_api_key(client, seed_products):
    resp = await client.get("/api/products", params={"sku": "PARENT-SKU-1"}, headers={"X-API-Key": "wrong-key"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_sync_missing_api_key(client):
    resp = await client.post("/api/sync")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_sync_status(client, seed_products):
    resp = await client.get("/api/sync/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_products"] == 2
    assert data["total_models"] == 2
    assert data["token_status"] in ("healthy", "expiring_soon", "expired", "missing")


@pytest.mark.asyncio
async def test_sync_status_token_missing(client):
    """token_status is 'missing' when no token exists."""
    resp = await client.get("/api/sync/status")
    assert resp.status_code == 200
    assert resp.json()["token_status"] == "missing"


@pytest.mark.asyncio
async def test_sync_status_token_healthy(client):
    """token_status is 'healthy' when token expires in more than 24h."""
    now = datetime.now(TZ_GMT7)
    async with TestSession() as session:
        token = ShopeeToken(
            shop_id=settings.shopee_shop_id,
            access_token="tok",
            refresh_token="ref",
            token_expires_at=now + timedelta(hours=48),
            created_at=now,
            updated_at=now,
        )
        session.add(token)
        await session.commit()
    resp = await client.get("/api/sync/status")
    assert resp.status_code == 200
    assert resp.json()["token_status"] == "healthy"


@pytest.mark.asyncio
async def test_sync_status_token_expiring_soon(client):
    """token_status is 'expiring_soon' when token expires within 24h."""
    now = datetime.now(TZ_GMT7)
    async with TestSession() as session:
        token = ShopeeToken(
            shop_id=settings.shopee_shop_id,
            access_token="tok",
            refresh_token="ref",
            token_expires_at=now + timedelta(hours=2),
            created_at=now,
            updated_at=now,
        )
        session.add(token)
        await session.commit()
    resp = await client.get("/api/sync/status")
    assert resp.status_code == 200
    assert resp.json()["token_status"] == "expiring_soon"


@pytest.mark.asyncio
async def test_sync_status_token_expired(client):
    """token_status is 'expired' when token is past expiry."""
    now = datetime.now(TZ_GMT7)
    async with TestSession() as session:
        token = ShopeeToken(
            shop_id=settings.shopee_shop_id,
            access_token="tok",
            refresh_token="ref",
            token_expires_at=now - timedelta(hours=1),
            created_at=now,
            updated_at=now,
        )
        session.add(token)
        await session.commit()
    resp = await client.get("/api/sync/status")
    assert resp.status_code == 200
    assert resp.json()["token_status"] == "expired"


@pytest.mark.asyncio
async def test_root(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    assert "Shopee SKU Dashboard" in resp.text
