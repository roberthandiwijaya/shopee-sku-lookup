import pytest

from tests.conftest import TEST_API_KEY

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


@pytest.mark.asyncio
async def test_root(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    assert "Shopee SKU Lookup API" in resp.json()["message"]
