from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings
from app.models.product import ShopeeToken, TZ_GMT7
from tests.conftest import TestSession


@pytest.mark.asyncio
async def test_refresh_retries_on_failure_then_succeeds():
    """Token refresh retries on API error and succeeds on second attempt."""
    now = datetime.now(TZ_GMT7)
    async with TestSession() as session:
        token = ShopeeToken(
            shop_id=settings.shopee_shop_id,
            access_token="old_access",
            refresh_token="valid_refresh",
            token_expires_at=now - timedelta(minutes=1),
            created_at=now,
            updated_at=now,
        )
        session.add(token)
        await session.commit()

    call_count = 0

    async def mock_public_request(path, method="GET", **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {"error": "error", "message": "transient failure"}
        return {
            "access_token": "new_access_token",
            "refresh_token": "new_refresh_token",
            "expire_in": 14400,
        }

    with patch("app.services.shopee_auth.async_session", TestSession):
        with patch("app.services.shopee_auth.shopee_client.public_request", side_effect=mock_public_request):
            with patch("app.services.shopee_auth.asyncio.sleep", new_callable=AsyncMock):
                from app.services.shopee_auth import refresh_token_if_needed
                result = await refresh_token_if_needed(settings.shopee_shop_id)

    assert result == "new_access_token"
    assert call_count == 2


@pytest.mark.asyncio
async def test_refresh_all_attempts_fail():
    """Token refresh returns None after all retry attempts fail."""
    now = datetime.now(TZ_GMT7)
    async with TestSession() as session:
        token = ShopeeToken(
            shop_id=settings.shopee_shop_id,
            access_token="old_access",
            refresh_token="valid_refresh",
            token_expires_at=now - timedelta(minutes=1),
            created_at=now,
            updated_at=now,
        )
        session.add(token)
        await session.commit()

    async def mock_public_request(path, method="GET", **kwargs):
        return {"error": "error", "message": "persistent failure"}

    with patch("app.services.shopee_auth.async_session", TestSession):
        with patch("app.services.shopee_auth.shopee_client.public_request", side_effect=mock_public_request):
            with patch("app.services.shopee_auth.asyncio.sleep", new_callable=AsyncMock):
                from app.services.shopee_auth import refresh_token_if_needed
                result = await refresh_token_if_needed(settings.shopee_shop_id)

    assert result is None
