import logging
from datetime import datetime

from fastapi import APIRouter, Request, Response

from app.config import settings
from app.database import async_session
from app.models.product import AuthAlert, TZ_GMT7
from app.services import webhook_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

CODE_AUTHORIZATION_EXPIRY = 12
MAX_WEBHOOK_BODY_BYTES = 256 * 1024


@router.post("/shopee")
async def shopee_webhook(request: Request):
    """Receive verified Shopee push notifications."""
    body = await request.body()
    if len(body) > MAX_WEBHOOK_BODY_BYTES:
        return Response(status_code=413)

    auth_header = request.headers.get("Authorization", "")
    if settings.order_webhook_enabled:
        if not settings.shopee_push_partner_key or not settings.shopee_webhook_callback_url:
            logger.error("Shopee webhook verification is not configured")
            return Response(status_code=503)
        if not auth_header or not webhook_service.verify_signature(body, auth_header):
            logger.warning("Rejected Shopee webhook with invalid signature")
            return Response(status_code=403)

    async with async_session() as session:
        event = await webhook_service.process_webhook(session, body)

        if event.code == CODE_AUTHORIZATION_EXPIRY and event.payload:
            data = event.payload.get("data") or {}
            shop_id = event.payload.get("shop_id") or 0
            expire_time = event.payload.get("expire_time") or data.get("expire_time")
            expires_at = (
                datetime.fromtimestamp(expire_time, tz=TZ_GMT7)
                if expire_time
                else None
            )
            session.add(
                AuthAlert(
                    shop_id=shop_id,
                    alert_type="authorization_expiry",
                    message=(
                        f"Shopee authorization for shop {shop_id} is expiring. "
                        "Please re-authorize."
                    ),
                    payload=event.payload,
                    expires_at=expires_at,
                )
            )

        await session.commit()

    logger.info(
        "Accepted Shopee webhook event_id=%s code=%s status=%s shop_id=%s",
        event.id,
        event.code,
        event.processing_status,
        event.shop_id,
    )
    return Response(status_code=200)
