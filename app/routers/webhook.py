import json
import logging
from datetime import datetime

from fastapi import APIRouter, Request, Response

from app.config import settings
from app.database import async_session
from app.models.product import AuthAlert, TZ_GMT7
from app.services import webhook_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

CODE_CALLBACK_VERIFICATION = 0
CODE_AUTHORIZATION_EXPIRY = 12
MAX_WEBHOOK_BODY_BYTES = 256 * 1024
VERIFICATION_MESSAGE = "This is a Verification message.Please respond in the certain format."


def _is_callback_verification(body: bytes) -> bool:
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False
    return (
        isinstance(payload, dict)
        and payload.get("code") == CODE_CALLBACK_VERIFICATION
        and set(payload) == {"code", "data"}
        and isinstance(payload.get("data"), dict)
        and payload["data"].get("verify_info") == VERIFICATION_MESSAGE
        and set(payload["data"]) == {"verify_info"}
    )


@router.post("/shopee")
async def shopee_webhook(request: Request):
    """Receive verified Shopee push notifications."""
    body = await request.body()
    if len(body) > MAX_WEBHOOK_BODY_BYTES:
        return Response(status_code=413)

    auth_header = request.headers.get("Authorization", "")
    if settings.order_webhook_enabled:
        if (
            settings.order_webhook_verification_bootstrap_enabled
            and _is_callback_verification(body)
        ):
            logger.warning(
                "Acknowledged unsigned Shopee callback verification bootstrap "
                "body_length=%d",
                len(body),
            )
            return Response(status_code=200)
        if not settings.shopee_push_partner_key or not settings.shopee_webhook_callback_url:
            logger.error("Shopee webhook verification is not configured")
            return Response(status_code=503)
        if not auth_header or not webhook_service.verify_signature(body, auth_header):
            logger.warning(
                "Rejected Shopee webhook signature header_present=%s header_length=%d body_length=%d",
                bool(auth_header),
                len(auth_header),
                len(body),
            )
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
