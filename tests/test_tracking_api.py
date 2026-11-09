from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings
from app.models.order import Order, OrderPackage, OrderPackageItem, OrderSyncState
from app.models.product import TZ_GMT7
from tests.conftest import TEST_API_KEY, TestSession

API_HEADERS = {"X-API-Key": TEST_API_KEY}
ENDPOINT = "/api/orders/by-tracking-number"


async def _seed_tracking_package(
    tracking_number: str = "SPX-123",
    sync_complete: bool = True,
    backfill_complete: bool = True,
):
    now = datetime.now(TZ_GMT7)
    async with TestSession() as session:
        order = Order(
            shop_id=settings.shopee_shop_id,
            order_sn="ORDER-1",
            synced_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(order)
        await session.flush()
        package = OrderPackage(
            order_id=order.id,
            shop_id=settings.shopee_shop_id,
            package_number="PACKAGE-1",
            tracking_number=tracking_number,
            tracking_number_normalized=tracking_number.upper(),
            shipping_carrier="SPX Express",
            sync_complete=sync_complete,
            synced_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(package)
        await session.flush()
        session.add_all(
            [
                OrderPackageItem(
                    package_id=package.id,
                    item_id=1001,
                    model_id=5001,
                    item_name="Sprei Aurora",
                    model_name="Blue / Queen",
                    package_quantity=2,
                    order_quantity=2,
                    model_sku="SKU-BLUE-Q",
                    image_url="https://example.com/blue.jpg",
                    created_at=now,
                    updated_at=now,
                ),
                OrderPackageItem(
                    package_id=package.id,
                    item_id=1002,
                    model_id=0,
                    item_name="Pillow",
                    model_name=None,
                    package_quantity=1,
                    order_quantity=1,
                    model_sku="PILLOW-1",
                    image_url=None,
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )
        session.add(
            OrderSyncState(
                shop_id=settings.shopee_shop_id,
                backfill_completed_at=now if backfill_complete else None,
                updated_at=now,
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_tracking_lookup_returns_package_with_items(client):
    await _seed_tracking_package()
    with patch(
        "app.services.order_service.shopee_client.shop_request",
        new_callable=AsyncMock,
    ) as mock_shopee:
        response = await client.get(
            ENDPOINT,
            params={"tracking_number": " spx-123 "},
            headers=API_HEADERS,
        )

    assert response.status_code == 200
    assert response.json() == {
        "order_sn": "ORDER-1",
        "package_number": "PACKAGE-1",
        "shipping_carrier": "SPX Express",
        "items": [
            {
                "item_name": "Sprei Aurora",
                "model_name": "Blue / Queen",
                "model_quantity_purchased": 2,
                "model_sku": "SKU-BLUE-Q",
                "image_url": "https://example.com/blue.jpg",
            },
            {
                "item_name": "Pillow",
                "model_name": None,
                "model_quantity_purchased": 1,
                "model_sku": "PILLOW-1",
                "image_url": None,
            },
        ],
    }
    mock_shopee.assert_not_awaited()


@pytest.mark.asyncio
async def test_tracking_lookup_returns_404_after_backfill(client):
    now = datetime.now(TZ_GMT7)
    async with TestSession() as session:
        session.add(
            OrderSyncState(
                shop_id=settings.shopee_shop_id,
                backfill_completed_at=now,
                updated_at=now,
            )
        )
        await session.commit()

    response = await client.get(
        ENDPOINT,
        params={"tracking_number": "UNKNOWN"},
        headers=API_HEADERS,
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_tracking_lookup_returns_503_during_backfill(client):
    response = await client.get(
        ENDPOINT,
        params={"tracking_number": "UNKNOWN"},
        headers=API_HEADERS,
    )
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_tracking_lookup_returns_503_for_incomplete_package(client):
    await _seed_tracking_package(sync_complete=False)
    response = await client.get(
        ENDPOINT,
        params={"tracking_number": "SPX-123"},
        headers=API_HEADERS,
    )
    assert response.status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("headers", "status"),
    [({}, 403), ({"X-API-Key": "wrong"}, 403)],
)
async def test_tracking_lookup_requires_api_key(client, headers, status):
    response = await client.get(
        ENDPOINT,
        params={"tracking_number": "SPX-123"},
        headers=headers,
    )
    assert response.status_code == status


@pytest.mark.asyncio
@pytest.mark.parametrize("tracking_number", [None, "   "])
async def test_tracking_lookup_validates_input(client, tracking_number):
    params = {} if tracking_number is None else {"tracking_number": tracking_number}
    response = await client.get(ENDPOINT, params=params, headers=API_HEADERS)
    assert response.status_code == 422
