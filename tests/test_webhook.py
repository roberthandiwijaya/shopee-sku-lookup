import hashlib
import hmac
import json
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.config import settings
from app.models.order import Order, OrderPackage, OrderSyncState
from app.models.product import TZ_GMT7
from app.models.webhook import OrderRefreshJob, ShopeeWebhookEvent
from tests.conftest import TestSession

ENDPOINT = "/api/webhooks/shopee"


@pytest.fixture(autouse=True)
def isolated_webhook_config(monkeypatch):
    """Webhook unit tests must never depend on deployment secrets or .env."""
    monkeypatch.setattr(settings, "shopee_push_partner_key", "test-webhook-signing-key")
    monkeypatch.setattr(settings, "order_webhook_enabled", True)
    monkeypatch.setattr(settings, "shopee_webhook_callback_url", "https://test.invalid/api/webhooks/shopee")
    monkeypatch.setattr(settings, "shopee_shop_id", 1)


def _body(timestamp: int | None = None) -> bytes:
    payload = {
        "data": {
            "ordersn": "ORDER-1",
            "package_number": "PACKAGE-1",
            "tracking_no": "SPXID-123",
        },
        "shop_id": settings.shopee_shop_id,
        "code": 4,
        "timestamp": timestamp or int(datetime.now(TZ_GMT7).timestamp()),
    }
    return json.dumps(payload, separators=(",", ":")).encode()


def _signature(body: bytes) -> str:
    return hmac.new(
        settings.shopee_push_partner_key.encode(),
        settings.shopee_webhook_callback_url.encode() + b"|" + body,
        hashlib.sha256,
    ).hexdigest()


async def _seed_package() -> None:
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
        session.add(
            OrderPackage(
                order_id=order.id,
                shop_id=settings.shopee_shop_id,
                package_number="PACKAGE-1",
                sync_complete=False,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            OrderSyncState(shop_id=settings.shopee_shop_id, updated_at=now)
        )
        await session.commit()


@pytest.mark.asyncio
async def test_code4_maps_package_and_enqueues_without_shopee_call(client):
    await _seed_package()
    body = _body()
    with (
        patch("app.routers.webhook.async_session", TestSession),
        patch(
            "app.services.webhook_service.order_service.shopee_client.shop_request",
            new_callable=AsyncMock,
        ) as mock_shopee,
    ):
        response = await client.post(
            ENDPOINT,
            content=body,
            headers={"Authorization": _signature(body)},
        )

    assert response.status_code == 200
    assert response.content == b""
    mock_shopee.assert_not_awaited()
    async with TestSession() as session:
        package = await session.scalar(select(OrderPackage))
        event = await session.scalar(select(ShopeeWebhookEvent))
        job = await session.scalar(select(OrderRefreshJob))
        assert package.tracking_number == "SPXID-123"
        assert package.tracking_source == "webhook"
        assert package.sync_complete is False
        assert event.processing_status == "mapped"
        assert job.order_sn == "ORDER-1"


@pytest.mark.asyncio
async def test_code4_duplicate_is_idempotent(client):
    await _seed_package()
    body = _body()
    with patch("app.routers.webhook.async_session", TestSession):
        for _ in range(2):
            response = await client.post(
                ENDPOINT,
                content=body,
                headers={"Authorization": _signature(body)},
            )
            assert response.status_code == 200

    async with TestSession() as session:
        events = list((await session.execute(select(ShopeeWebhookEvent))).scalars())
        jobs = list((await session.execute(select(OrderRefreshJob))).scalars())
        assert len(events) == 1
        assert events[0].delivery_count == 2
        assert len(jobs) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("authorization", [None, "wrong"])
async def test_webhook_rejects_missing_or_invalid_signature(client, authorization):
    body = _body()
    headers = {} if authorization is None else {"Authorization": authorization}
    with patch("app.routers.webhook.async_session", TestSession):
        response = await client.post(ENDPOINT, content=body, headers=headers)
    assert response.status_code == 403
    async with TestSession() as session:
        assert await session.scalar(select(ShopeeWebhookEvent)) is None
