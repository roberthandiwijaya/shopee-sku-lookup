from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings
from app.services import order_service
from tests.conftest import TEST_API_KEY

API_HEADERS = {"X-API-Key": TEST_API_KEY}
PACKAGE_ENDPOINT = "/api/v2/order/get_package_detail"
ORDER_ENDPOINT = "/api/v2/order/get_order_detail"


@pytest.mark.asyncio
async def test_get_package_detail_comma_separated(client):
    shopee_response = {
        "error": "",
        "message": "",
        "response": {"package_list": [{"package_number": "PKG-1"}]},
        "request_id": "request-1",
    }

    with (
        patch(
            "app.routers.orders.get_valid_token",
            new_callable=AsyncMock,
            return_value="access-token",
        ) as mock_get_token,
        patch(
            "app.routers.orders.order_service.get_package_detail",
            new_callable=AsyncMock,
            return_value=shopee_response,
        ) as mock_get_package_detail,
    ):
        response = await client.get(
            PACKAGE_ENDPOINT,
            params={"package_number_list": " PKG-1, PKG-2 "},
            headers=API_HEADERS,
        )

    assert response.status_code == 200
    assert response.json() == shopee_response
    mock_get_token.assert_awaited_once_with(settings.shopee_shop_id)
    mock_get_package_detail.assert_awaited_once_with(
        shop_id=settings.shopee_shop_id,
        access_token="access-token",
        package_number_list=["PKG-1", "PKG-2"],
    )


@pytest.mark.asyncio
async def test_get_package_detail_repeated_and_comma_separated(client):
    with (
        patch(
            "app.routers.orders.get_valid_token",
            new_callable=AsyncMock,
            return_value="access-token",
        ),
        patch(
            "app.routers.orders.order_service.get_package_detail",
            new_callable=AsyncMock,
            return_value={"response": {}},
        ) as mock_get_package_detail,
    ):
        response = await client.get(
            PACKAGE_ENDPOINT,
            params=[
                ("package_number_list", "PKG-1"),
                ("package_number_list", "PKG-2, PKG-3"),
            ],
            headers=API_HEADERS,
        )

    assert response.status_code == 200
    assert mock_get_package_detail.await_args.kwargs["package_number_list"] == [
        "PKG-1",
        "PKG-2",
        "PKG-3",
    ]


@pytest.mark.asyncio
async def test_get_package_detail_preserves_shopee_error_payload(client):
    shopee_error = {
        "error": "error_param",
        "message": "invalid package number",
        "request_id": "request-2",
    }

    with (
        patch(
            "app.routers.orders.get_valid_token",
            new_callable=AsyncMock,
            return_value="access-token",
        ),
        patch(
            "app.routers.orders.order_service.get_package_detail",
            new_callable=AsyncMock,
            return_value=shopee_error,
        ),
    ):
        response = await client.get(
            PACKAGE_ENDPOINT,
            params={"package_number_list": "PKG-1"},
            headers=API_HEADERS,
        )

    assert response.status_code == 200
    assert response.json() == shopee_error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("headers", "expected_detail"),
    [
        ({}, "Not authenticated"),
        ({"X-API-Key": "wrong-key"}, "Invalid API key"),
    ],
)
async def test_get_package_detail_rejects_invalid_api_key(
    client, headers, expected_detail
):
    with (
        patch(
            "app.routers.orders.get_valid_token", new_callable=AsyncMock
        ) as mock_get_token,
        patch(
            "app.routers.orders.order_service.get_package_detail",
            new_callable=AsyncMock,
        ) as mock_get_package_detail,
    ):
        response = await client.get(
            PACKAGE_ENDPOINT,
            params={"package_number_list": "PKG-1"},
            headers=headers,
        )

    assert response.status_code == 403
    assert response.json()["detail"] == expected_detail
    mock_get_token.assert_not_awaited()
    mock_get_package_detail.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_package_detail_requires_package_number_list(client):
    with (
        patch(
            "app.routers.orders.get_valid_token", new_callable=AsyncMock
        ) as mock_get_token,
        patch(
            "app.routers.orders.order_service.get_package_detail",
            new_callable=AsyncMock,
        ) as mock_get_package_detail,
    ):
        response = await client.get(PACKAGE_ENDPOINT, headers=API_HEADERS)

    assert response.status_code == 422
    mock_get_token.assert_not_awaited()
    mock_get_package_detail.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_package_detail_rejects_empty_package_list(client):
    with (
        patch(
            "app.routers.orders.get_valid_token", new_callable=AsyncMock
        ) as mock_get_token,
        patch(
            "app.routers.orders.order_service.get_package_detail",
            new_callable=AsyncMock,
        ) as mock_get_package_detail,
    ):
        response = await client.get(
            PACKAGE_ENDPOINT,
            params={"package_number_list": " , , "},
            headers=API_HEADERS,
        )

    assert response.status_code == 422
    assert response.json()["detail"] == (
        "package_number_list must contain at least one package number"
    )
    mock_get_token.assert_not_awaited()
    mock_get_package_detail.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_package_detail_returns_503_without_token(client):
    with (
        patch(
            "app.routers.orders.get_valid_token",
            new_callable=AsyncMock,
            return_value=None,
        ) as mock_get_token,
        patch(
            "app.routers.orders.order_service.get_package_detail",
            new_callable=AsyncMock,
        ) as mock_get_package_detail,
    ):
        response = await client.get(
            PACKAGE_ENDPOINT,
            params={"package_number_list": "PKG-1"},
            headers=API_HEADERS,
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "No valid Shopee token"}
    mock_get_token.assert_awaited_once_with(settings.shopee_shop_id)
    mock_get_package_detail.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_package_detail_does_not_forward_auth_query_parameters(client):
    with (
        patch(
            "app.routers.orders.get_valid_token",
            new_callable=AsyncMock,
            return_value="configured-token",
        ),
        patch(
            "app.routers.orders.order_service.get_package_detail",
            new_callable=AsyncMock,
            return_value={"response": {}},
        ) as mock_get_package_detail,
    ):
        response = await client.get(
            PACKAGE_ENDPOINT,
            params={
                "package_number_list": "PKG-1",
                "shop_id": "999",
                "partner_id": "999",
                "access_token": "caller-token",
                "timestamp": "1",
                "sign": "caller-signature",
            },
            headers=API_HEADERS,
        )

    assert response.status_code == 200
    mock_get_package_detail.assert_awaited_once_with(
        shop_id=settings.shopee_shop_id,
        access_token="configured-token",
        package_number_list=["PKG-1"],
    )


@pytest.mark.asyncio
async def test_order_service_calls_exact_shopee_endpoint():
    shopee_response = {"response": {"package_list": []}}

    with patch(
        "app.services.order_service.shopee_client.shop_request",
        new_callable=AsyncMock,
        return_value=shopee_response,
    ) as mock_shop_request:
        result = await order_service.get_package_detail(
            shop_id=123,
            access_token="access-token",
            package_number_list=["PKG-1", "PKG-2"],
        )

    assert result == shopee_response
    mock_shop_request.assert_awaited_once_with(
        "/api/v2/order/get_package_detail",
        access_token="access-token",
        shop_id=123,
        params={"package_number_list": "PKG-1,PKG-2"},
    )


@pytest.mark.asyncio
async def test_get_order_detail_normalizes_inputs_and_optional_fields(client):
    shopee_response = {
        "error": "",
        "message": "",
        "response": {
            "order_list": [
                {"order_sn": "ORDER-1", "package_list": [{"package_number": "PKG-1"}]}
            ]
        },
        "request_id": "request-order-1",
    }

    with (
        patch(
            "app.routers.orders.get_valid_token",
            new_callable=AsyncMock,
            return_value="access-token",
        ) as mock_get_token,
        patch(
            "app.routers.orders.order_service.get_order_detail",
            new_callable=AsyncMock,
            return_value=shopee_response,
        ) as mock_get_order_detail,
    ):
        response = await client.get(
            ORDER_ENDPOINT,
            params=[
                ("order_sn_list", " ORDER-1 "),
                ("order_sn_list", "ORDER-2, ORDER-3"),
                ("response_optional_fields", "package_list"),
                ("response_optional_fields", "item_list, shipping_carrier"),
            ],
            headers=API_HEADERS,
        )

    assert response.status_code == 200
    assert response.json() == shopee_response
    mock_get_token.assert_awaited_once_with(settings.shopee_shop_id)
    mock_get_order_detail.assert_awaited_once_with(
        shop_id=settings.shopee_shop_id,
        access_token="access-token",
        order_sn_list=["ORDER-1", "ORDER-2", "ORDER-3"],
        response_optional_fields=["package_list", "item_list", "shipping_carrier"],
    )


@pytest.mark.asyncio
async def test_get_order_detail_omits_empty_optional_fields(client):
    with (
        patch(
            "app.routers.orders.get_valid_token",
            new_callable=AsyncMock,
            return_value="access-token",
        ),
        patch(
            "app.routers.orders.order_service.get_order_detail",
            new_callable=AsyncMock,
            return_value={"response": {"order_list": []}},
        ) as mock_get_order_detail,
    ):
        response = await client.get(
            ORDER_ENDPOINT,
            params={
                "order_sn_list": "ORDER-1",
                "response_optional_fields": " , , ",
            },
            headers=API_HEADERS,
        )

    assert response.status_code == 200
    mock_get_order_detail.assert_awaited_once_with(
        shop_id=settings.shopee_shop_id,
        access_token="access-token",
        order_sn_list=["ORDER-1"],
        response_optional_fields=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("headers", "expected_detail"),
    [
        ({}, "Not authenticated"),
        ({"X-API-Key": "wrong-key"}, "Invalid API key"),
    ],
)
async def test_get_order_detail_rejects_invalid_api_key(client, headers, expected_detail):
    with (
        patch("app.routers.orders.get_valid_token", new_callable=AsyncMock) as mock_get_token,
        patch(
            "app.routers.orders.order_service.get_order_detail",
            new_callable=AsyncMock,
        ) as mock_get_order_detail,
    ):
        response = await client.get(
            ORDER_ENDPOINT,
            params={"order_sn_list": "ORDER-1"},
            headers=headers,
        )

    assert response.status_code == 403
    assert response.json()["detail"] == expected_detail
    mock_get_token.assert_not_awaited()
    mock_get_order_detail.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("params", [{}, {"order_sn_list": " , , "}])
async def test_get_order_detail_requires_nonempty_order_list(client, params):
    with (
        patch("app.routers.orders.get_valid_token", new_callable=AsyncMock) as mock_get_token,
        patch(
            "app.routers.orders.order_service.get_order_detail",
            new_callable=AsyncMock,
        ) as mock_get_order_detail,
    ):
        response = await client.get(ORDER_ENDPOINT, params=params, headers=API_HEADERS)

    assert response.status_code == 422
    mock_get_token.assert_not_awaited()
    mock_get_order_detail.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_order_detail_returns_503_without_token(client):
    with (
        patch(
            "app.routers.orders.get_valid_token",
            new_callable=AsyncMock,
            return_value=None,
        ) as mock_get_token,
        patch(
            "app.routers.orders.order_service.get_order_detail",
            new_callable=AsyncMock,
        ) as mock_get_order_detail,
    ):
        response = await client.get(
            ORDER_ENDPOINT,
            params={"order_sn_list": "ORDER-1"},
            headers=API_HEADERS,
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "No valid Shopee token"}
    mock_get_token.assert_awaited_once_with(settings.shopee_shop_id)
    mock_get_order_detail.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_order_detail_preserves_shopee_error_and_ignores_auth_params(client):
    shopee_error = {
        "error": "error_param",
        "message": "invalid order_sn",
        "request_id": "request-order-2",
    }

    with (
        patch(
            "app.routers.orders.get_valid_token",
            new_callable=AsyncMock,
            return_value="configured-token",
        ),
        patch(
            "app.routers.orders.order_service.get_order_detail",
            new_callable=AsyncMock,
            return_value=shopee_error,
        ) as mock_get_order_detail,
    ):
        response = await client.get(
            ORDER_ENDPOINT,
            params={
                "order_sn_list": "ORDER-1",
                "shop_id": "999",
                "partner_id": "999",
                "access_token": "caller-token",
                "timestamp": "1",
                "sign": "caller-signature",
            },
            headers=API_HEADERS,
        )

    assert response.status_code == 200
    assert response.json() == shopee_error
    mock_get_order_detail.assert_awaited_once_with(
        shop_id=settings.shopee_shop_id,
        access_token="configured-token",
        order_sn_list=["ORDER-1"],
        response_optional_fields=None,
    )


@pytest.mark.asyncio
async def test_order_service_calls_exact_order_detail_endpoint():
    shopee_response = {"response": {"order_list": []}}

    with patch(
        "app.services.order_service.shopee_client.shop_request",
        new_callable=AsyncMock,
        return_value=shopee_response,
    ) as mock_shop_request:
        result = await order_service.get_order_detail(
            shop_id=123,
            access_token="access-token",
            order_sn_list=["ORDER-1", "ORDER-2"],
            response_optional_fields=["package_list", "item_list"],
        )

    assert result == shopee_response
    mock_shop_request.assert_awaited_once_with(
        "/api/v2/order/get_order_detail",
        access_token="access-token",
        shop_id=123,
        params={
            "order_sn_list": "ORDER-1,ORDER-2",
            "response_optional_fields": "package_list,item_list",
        },
    )


@pytest.mark.asyncio
async def test_order_service_omits_optional_fields_when_absent():
    with patch(
        "app.services.order_service.shopee_client.shop_request",
        new_callable=AsyncMock,
        return_value={"response": {}},
    ) as mock_shop_request:
        await order_service.get_order_detail(
            shop_id=123,
            access_token="access-token",
            order_sn_list=["ORDER-1"],
        )

    mock_shop_request.assert_awaited_once_with(
        "/api/v2/order/get_order_detail",
        access_token="access-token",
        shop_id=123,
        params={"order_sn_list": "ORDER-1"},
    )
