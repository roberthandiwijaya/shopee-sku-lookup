from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import TEST_API_KEY


@pytest.mark.asyncio
async def test_callback_exchange_error_returns_400(client):
    """OAuth callback returns 400 when exchange_code_for_token raises ValueError."""
    with patch(
        "app.routers.auth.exchange_code_for_token",
        new_callable=AsyncMock,
        side_effect=ValueError("Token exchange failed: fake error"),
    ):
        resp = await client.get(
            "/api/auth/callback", params={"code": "bad-code", "shop_id": 123}
        )
    assert resp.status_code == 400
    assert "Token exchange failed" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_callback_success(client):
    """OAuth callback returns success when exchange works."""
    with patch(
        "app.routers.auth.exchange_code_for_token",
        new_callable=AsyncMock,
        return_value={"access_token": "tok_abc123xyz", "refresh_token": "ref_xyz"},
    ):
        resp = await client.get(
            "/api/auth/callback", params={"code": "good-code", "shop_id": 123}
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["message"] == "Authorization successful"
    assert data["shop_id"] == 123
