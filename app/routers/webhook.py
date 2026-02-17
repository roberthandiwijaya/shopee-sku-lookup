import hashlib
import hmac
import json
import logging
from datetime import datetime

from fastapi import APIRouter, Request, Response

from app.config import settings
from app.database import async_session
from app.models.product import AuthAlert, TZ_GMT7

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

# Shopee push event codes
CODE_AUTHORIZATION_EXPIRY = 12


def _verify_signature(url: str, body: bytes, authorization: str) -> bool:
    """Verify Shopee webhook HMAC-SHA256 signature."""
    base_string = url + "|" + body.decode("utf-8")
    computed = hmac.new(
        settings.shopee_partner_key.encode("utf-8"),
        base_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(computed, authorization)


@router.post("/shopee")
async def shopee_webhook(request: Request):
    """Receive Shopee push notifications."""
    body = await request.body()

    # Verify signature if partner key is configured
    auth_header = request.headers.get("Authorization", "")
    if settings.shopee_partner_key and auth_header:
        url = str(request.url)
        if not _verify_signature(url, body, auth_header):
            return Response(status_code=403)

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        logger.warning("Invalid JSON in webhook body")
        return Response(status_code=200)

    code = data.get("code")
    shop_id = data.get("shop_id")

    logger.info("Received Shopee webhook: code=%s shop_id=%s", code, shop_id)

    if code == CODE_AUTHORIZATION_EXPIRY:
        expire_time = data.get("expire_time")
        expires_at = None
        if expire_time:
            expires_at = datetime.fromtimestamp(expire_time, tz=TZ_GMT7)
            formatted = expires_at.strftime("%-d %b %Y %H:%M WIB")
            message = (
                f"Shopee authorization for shop {shop_id} expires on "
                f"{formatted}. Please re-authorize."
            )
        else:
            message = (
                f"Shopee authorization for shop {shop_id} is expiring. "
                "Please re-authorize."
            )
        async with async_session() as session:
            alert = AuthAlert(
                shop_id=shop_id or 0,
                alert_type="authorization_expiry",
                message=message,
                payload=data,
                expires_at=expires_at,
            )
            session.add(alert)
            await session.commit()
        logger.info("Created auth alert for shop %s", shop_id)

    # Shopee requires 2xx with empty body
    return Response(status_code=200)
