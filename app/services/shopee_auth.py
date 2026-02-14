import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session
from app.models.product import ShopeeToken
from app.services import shopee_client

logger = logging.getLogger(__name__)


async def exchange_code_for_token(code: str, shop_id: int) -> dict:
    """Exchange OAuth code for access + refresh tokens."""
    body = {
        "code": code,
        "shop_id": shop_id,
        "partner_id": settings.shopee_partner_id,
    }
    data = await shopee_client.public_request(
        "/api/v2/auth/token/get", method="POST", json=body
    )

    if data.get("error"):
        raise ValueError(f"Token exchange failed: {data}")

    async with async_session() as session:
        await _upsert_token(session, shop_id, data)
        await session.commit()

    return data


async def refresh_token_if_needed(shop_id: int) -> str | None:
    """Refresh the access token if it's about to expire. Returns the current valid access token."""
    async with async_session() as session:
        token = await _get_token(session, shop_id)
        if not token:
            return None

        if token.token_expires_at and token.token_expires_at > datetime.now(timezone.utc) + timedelta(minutes=10):
            return token.access_token

        logger.info("Refreshing Shopee token for shop %d", shop_id)
        body = {
            "refresh_token": token.refresh_token,
            "shop_id": shop_id,
            "partner_id": settings.shopee_partner_id,
        }
        data = await shopee_client.public_request(
            "/api/v2/auth/access_token/get", method="POST", json=body
        )

        if data.get("error"):
            logger.error("Token refresh failed: %s", data)
            return None

        await _upsert_token(session, shop_id, data)
        await session.commit()
        return data.get("access_token")


async def get_valid_token(shop_id: int) -> str | None:
    """Get a valid access token, refreshing if necessary."""
    return await refresh_token_if_needed(shop_id)


async def _get_token(session: AsyncSession, shop_id: int) -> ShopeeToken | None:
    result = await session.execute(
        select(ShopeeToken).where(ShopeeToken.shop_id == shop_id)
    )
    return result.scalar_one_or_none()


async def _upsert_token(session: AsyncSession, shop_id: int, data: dict) -> None:
    token = await _get_token(session, shop_id)
    now = datetime.now(timezone.utc)
    expire_in = data.get("expire_in", 14400)

    if token is None:
        token = ShopeeToken(
            shop_id=shop_id,
            access_token=data.get("access_token"),
            refresh_token=data.get("refresh_token"),
            token_expires_at=now + timedelta(seconds=expire_in),
            created_at=now,
            updated_at=now,
        )
        session.add(token)
    else:
        token.access_token = data.get("access_token")
        token.refresh_token = data.get("refresh_token")
        token.token_expires_at = now + timedelta(seconds=expire_in)
        token.updated_at = now
