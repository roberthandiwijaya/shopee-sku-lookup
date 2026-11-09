import hashlib
import hmac
import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models.order import OrderPackage
from app.models.product import TZ_GMT7
from app.models.webhook import ShopeeWebhookEvent
from app.services import order_service
from app.services.order_refresh_service import (
    PRIORITY_WEBHOOK,
    SOURCE_WEBHOOK,
    enqueue_refresh,
)

CODE_ORDER_TRACKING_NUMBER = 4


def verify_signature(raw_body: bytes, authorization: str) -> bool:
    message = settings.shopee_webhook_callback_url.encode() + b"|" + raw_body
    expected = hmac.new(
        settings.shopee_push_partner_key.encode(), message, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, authorization)


async def process_webhook(
    session: AsyncSession, raw_body: bytes
) -> ShopeeWebhookEvent:
    digest = hashlib.sha256(raw_body).hexdigest()
    now = datetime.now(TZ_GMT7)
    existing = await session.scalar(
        select(ShopeeWebhookEvent).where(
            ShopeeWebhookEvent.body_sha256 == digest
        )
    )
    if existing is not None:
        existing.delivery_count += 1
        existing.last_received_at = now
        return existing

    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        event = ShopeeWebhookEvent(
            body_sha256=digest,
            raw_body=raw_body,
            processing_status="invalid",
            processing_error="Invalid JSON payload",
            first_received_at=now,
            last_received_at=now,
            processed_at=now,
        )
        session.add(event)
        return event

    code = payload.get("code")
    shop_id = payload.get("shop_id")
    timestamp = payload.get("timestamp")
    event_at = (
        datetime.fromtimestamp(timestamp, tz=TZ_GMT7) if timestamp else None
    )
    event = ShopeeWebhookEvent(
        body_sha256=digest,
        code=code,
        shop_id=shop_id,
        event_timestamp=event_at,
        raw_body=raw_body,
        payload=payload,
        processing_status="received",
        first_received_at=now,
        last_received_at=now,
    )
    session.add(event)
    await session.flush()

    if code != CODE_ORDER_TRACKING_NUMBER:
        event.processing_status = "ignored"
        event.processed_at = now
        return event
    if shop_id != settings.shopee_shop_id:
        event.processing_status = "ignored"
        event.processing_error = "Webhook shop does not match configured shop"
        event.processed_at = now
        return event

    data = payload.get("data") or {}
    order_sn = str(data.get("ordersn") or "").strip()
    package_number = str(data.get("package_number") or "").strip()
    tracking_number = str(data.get("tracking_no") or "").strip()
    event.order_sn = order_sn or None
    event.package_number = package_number or None
    event.tracking_number = tracking_number or None
    if (
        not order_sn
        or not package_number
        or not tracking_number
        or len(order_sn) > 100
        or len(package_number) > 255
        or len(tracking_number) > 255
    ):
        event.processing_status = "invalid"
        event.processing_error = "Code 4 payload is missing required fields"
        event.processed_at = now
        return event

    package = await session.scalar(
        select(OrderPackage)
        .options(selectinload(OrderPackage.order), selectinload(OrderPackage.items))
        .where(
            OrderPackage.shop_id == shop_id,
            OrderPackage.package_number == package_number,
        )
        .with_for_update()
    )
    status = "unmapped"
    if package is not None and package.order.order_sn != order_sn:
        status = "order_mismatch"
    elif package is not None:
        if package.tracking_event_at and event_at and event_at < package.tracking_event_at:
            status = "stale"
        else:
            package.tracking_number = tracking_number
            package.tracking_number_normalized = order_service.normalize_tracking_number(
                tracking_number
            )
            package.tracking_event_at = event_at or now
            package.tracking_source = "webhook"
            package.last_webhook_event_id = event.id
            package.updated_at = now
            if package.items:
                package.sync_complete = True
            event.mapped_package_id = package.id
            status = "mapped"

    job = await enqueue_refresh(
        session=session,
        shop_id=shop_id,
        order_sn=order_sn,
        source_flag=SOURCE_WEBHOOK,
        priority=PRIORITY_WEBHOOK,
        package_number=package_number,
        tracking_number=tracking_number,
        source_event_at=event_at,
    )
    event.refresh_job_id = job.id
    event.processing_status = status
    event.processed_at = now
    return event
