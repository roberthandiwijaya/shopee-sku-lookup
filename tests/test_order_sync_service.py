from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.order import OrderPackage
from app.models.product import TZ_GMT7
from app.services import order_sync_service
from tests.conftest import TestSession


@pytest.mark.asyncio
async def test_sync_window_maps_split_package_items():
    now = datetime.now(TZ_GMT7)
    order_list = {
        "error": "",
        "response": {
            "more": False,
            "order_list": [{"order_sn": "ORDER-1"}],
        },
    }
    order_detail = {
        "error": "",
        "response": {
            "order_list": [
                {
                    "order_sn": "ORDER-1",
                    "update_time": int(now.timestamp()),
                    "item_list": [
                        {
                            "item_id": 1001,
                            "model_id": 5001,
                            "item_name": "Sprei Aurora",
                            "model_name": "Blue",
                            "model_quantity_purchased": 3,
                            "model_sku": "SKU-BLUE",
                            "image_info": {"image_url": "https://example.com/image.jpg"},
                        }
                    ],
                    "package_list": [
                        {"package_number": "PKG-1", "shipping_carrier": "SPX"},
                        {"package_number": "PKG-2", "shipping_carrier": "SPX"},
                    ],
                }
            ]
        },
    }
    package_detail = {
        "error": "",
        "response": {
            "package_list": [
                {
                    "order_sn": "ORDER-1",
                    "package_number": "PKG-1",
                    "tracking_number": "TRACK-1",
                    "shipping_carrier": "SPX",
                    "item_list": [
                        {"item_id": 1001, "model_id": 5001, "model_quantity": 1}
                    ],
                },
                {
                    "order_sn": "ORDER-1",
                    "package_number": "PKG-2",
                    "tracking_number": "TRACK-2",
                    "shipping_carrier": "SPX",
                    "item_list": [
                        {"item_id": 1001, "model_id": 5001, "model_quantity": 2}
                    ],
                },
            ]
        },
    }

    with (
        patch("app.services.order_sync_service.async_session", TestSession),
        patch(
            "app.services.order_sync_service.order_service.get_order_list",
            new_callable=AsyncMock,
            return_value=order_list,
        ),
        patch(
            "app.services.order_sync_service.order_service.get_order_detail",
            new_callable=AsyncMock,
            return_value=order_detail,
        ) as mock_order_detail,
        patch(
            "app.services.order_sync_service.order_service.get_package_detail",
            new_callable=AsyncMock,
            return_value=package_detail,
        ) as mock_package_detail,
    ):
        synced = await order_sync_service.sync_window(
            shop_id=1,
            access_token="token",
            time_from=now - timedelta(days=1),
            time_to=now,
        )

    assert synced == 2
    assert mock_order_detail.await_args.kwargs["response_optional_fields"] == [
        "item_list",
        "package_list",
    ]
    assert mock_package_detail.await_args.kwargs["package_number_list"] == [
        "PKG-1",
        "PKG-2",
    ]

    async with TestSession() as session:
        stmt = select(OrderPackage).options(selectinload(OrderPackage.items))
        packages = (await session.execute(stmt)).scalars().all()
        assert sorted(package.tracking_number for package in packages) == ["TRACK-1", "TRACK-2"]
        quantities = sorted(item.package_quantity for package in packages for item in package.items)
        assert quantities == [1, 2]


@pytest.mark.asyncio
async def test_sync_window_rejects_shopee_error():
    now = datetime.now(TZ_GMT7)
    with patch(
        "app.services.order_sync_service.order_service.get_order_list",
        new_callable=AsyncMock,
        return_value={"error": "error_auth", "message": "denied"},
    ):
        with pytest.raises(order_sync_service.OrderSyncError, match="denied"):
            await order_sync_service.sync_window(
                shop_id=1,
                access_token="token",
                time_from=now - timedelta(days=1),
                time_to=now,
            )


@pytest.mark.asyncio
async def test_sync_window_rejects_more_than_fifteen_days():
    now = datetime.now(TZ_GMT7)
    with pytest.raises(ValueError):
        await order_sync_service.sync_window(
            shop_id=1,
            access_token="token",
            time_from=now - timedelta(days=16),
            time_to=now,
        )
