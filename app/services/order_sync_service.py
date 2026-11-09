import logging
from datetime import datetime, timedelta
from typing import Any
import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session
from app.models.order import Order, OrderPackage, OrderPackageItem, OrderSyncState
from app.models.product import TZ_GMT7
from app.services import order_service
from app.services.shopee_auth import get_valid_token

logger = logging.getLogger(__name__)

MAX_WINDOW_DAYS = 15
ORDER_BATCH_SIZE = 50
PACKAGE_BATCH_SIZE = 50


class OrderSyncError(RuntimeError):
    pass


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _require_response(data: dict, operation: str) -> dict:
    if data.get("error"):
        raise OrderSyncError(f"Shopee {operation} failed: {data.get('message') or data['error']}")
    response = data.get("response")
    if not isinstance(response, dict):
        raise OrderSyncError(f"Shopee {operation} returned an invalid response")
    return response


def _item_key(item: dict) -> tuple[int, int]:
    return int(item.get("item_id") or 0), int(item.get("model_id") or 0)


def _image_url(item: dict) -> str | None:
    image_info = item.get("image_info") or {}
    return image_info.get("image_url") if isinstance(image_info, dict) else None


async def _get_or_create_state(
    session: AsyncSession,
    shop_id: int,
    backfill_from: datetime,
) -> OrderSyncState:
    state = await session.get(OrderSyncState, shop_id)
    now = datetime.now(TZ_GMT7)
    if state is None:
        state = OrderSyncState(
            shop_id=shop_id,
            backfill_from=backfill_from,
            last_started_at=now,
            updated_at=now,
        )
        session.add(state)
    else:
        state.last_started_at = now
        state.last_error = None
        state.updated_at = now
        if state.backfill_from is None:
            state.backfill_from = backfill_from
    await session.commit()
    return state


async def _list_order_sns(
    shop_id: int,
    access_token: str,
    time_from: datetime,
    time_to: datetime,
    time_range_field: str = "update_time",
) -> list[str]:
    order_sns: list[str] = []
    cursor = ""
    while True:
        data = await order_service.get_order_list(
            shop_id=shop_id,
            access_token=access_token,
            time_from=time_from,
            time_to=time_to,
            cursor=cursor,
            time_range_field=time_range_field,
        )
        response = _require_response(data, "get_order_list")
        for order in response.get("order_list") or []:
            order_sn = order.get("order_sn")
            if order_sn:
                order_sns.append(str(order_sn))
        if not response.get("more"):
            break
        next_cursor = response.get("next_cursor")
        if not next_cursor or next_cursor == cursor:
            raise OrderSyncError("Shopee get_order_list returned an invalid cursor")
        cursor = str(next_cursor)
    return list(dict.fromkeys(order_sns))


async def _fetch_order_details(
    shop_id: int,
    access_token: str,
    order_sns: list[str],
) -> list[dict]:
    details: list[dict] = []
    for batch in _chunks(order_sns, ORDER_BATCH_SIZE):
        data = await order_service.get_order_detail(
            shop_id=shop_id,
            access_token=access_token,
            order_sn_list=batch,
            response_optional_fields=order_service.ORDER_DETAIL_FIELDS,
        )
        response = _require_response(data, "get_order_detail")
        batch_details = response.get("order_list") or []
        returned = {str(detail.get("order_sn")) for detail in batch_details}
        missing = [order_sn for order_sn in batch if order_sn not in returned]
        if missing:
            raise OrderSyncError(
                f"Shopee get_order_detail omitted {len(missing)} requested orders"
            )
        details.extend(batch_details)
    return details


async def _fetch_package_details(
    shop_id: int,
    access_token: str,
    package_numbers: list[str],
) -> dict[str, dict]:
    packages: dict[str, dict] = {}
    for batch in _chunks(package_numbers, PACKAGE_BATCH_SIZE):
        data = await order_service.get_package_detail(
            shop_id=shop_id,
            access_token=access_token,
            package_number_list=batch,
        )
        response = _require_response(data, "get_package_detail")
        for package in response.get("package_list") or []:
            package_number = package.get("package_number")
            if package_number:
                packages[str(package_number)] = package
        missing = [package_number for package_number in batch if package_number not in packages]
        if missing:
            logger.warning(
                "Shopee get_package_detail omitted %d requested packages; "
                "they will remain incomplete and be retried",
                len(missing),
            )
    return packages


async def _upsert_order(
    session: AsyncSession,
    shop_id: int,
    order_detail: dict,
    package_details: dict[str, dict],
) -> int:
    order_sn = str(order_detail.get("order_sn") or "")
    if not order_sn:
        raise OrderSyncError("Order detail is missing order_sn")

    now = datetime.now(TZ_GMT7)
    result = await session.execute(
        select(Order).where(Order.shop_id == shop_id, Order.order_sn == order_sn)
    )
    order = result.scalar_one_or_none()
    update_timestamp = order_detail.get("update_time")
    update_time = (
        datetime.fromtimestamp(update_timestamp, tz=TZ_GMT7)
        if update_timestamp
        else None
    )
    if order is None:
        order = Order(
            shop_id=shop_id,
            order_sn=order_sn,
            shopee_update_time=update_time,
            synced_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(order)
        await session.flush()
    else:
        order.shopee_update_time = update_time
        order.synced_at = now
        order.updated_at = now

    rich_items = {_item_key(item): item for item in order_detail.get("item_list") or []}
    synced_packages = 0
    for summary in order_detail.get("package_list") or []:
        package_number = str(summary.get("package_number") or "")
        if not package_number:
            continue
        package_data = package_details.get(package_number)
        if package_data is None:
            package_result = await session.execute(
                select(OrderPackage).where(
                    OrderPackage.shop_id == shop_id,
                    OrderPackage.package_number == package_number,
                )
            )
            package = package_result.scalar_one_or_none()
            if package is None:
                package = OrderPackage(
                    order_id=order.id,
                    shop_id=shop_id,
                    package_number=package_number,
                    sync_complete=False,
                    created_at=now,
                )
                session.add(package)
            package.order_id = order.id
            package.shipping_carrier = summary.get("shipping_carrier")
            package.synced_at = now
            package.updated_at = now
            continue
        if str(package_data.get("order_sn") or "") != order_sn:
            raise OrderSyncError(f"Package {package_number} belongs to another order")

        item_snapshots: list[dict[str, Any]] = []
        for package_item in package_data.get("item_list") or []:
            key = _item_key(package_item)
            rich_item = rich_items.get(key)
            if rich_item is None:
                raise OrderSyncError(
                    f"Package {package_number} item {key[0]}/{key[1]} is not in order detail"
                )
            quantity = int(
                package_item.get("model_quantity")
                or package_item.get("model_quantity_purchased")
                or 0
            )
            if quantity <= 0:
                raise OrderSyncError(f"Package {package_number} has an invalid item quantity")
            item_snapshots.append(
                {
                    "item_id": key[0],
                    "model_id": key[1],
                    "item_name": rich_item.get("item_name") or f"Item {key[0]}",
                    "model_name": rich_item.get("model_name"),
                    "package_quantity": quantity,
                    "order_quantity": rich_item.get("model_quantity_purchased"),
                    "model_sku": rich_item.get("model_sku") or rich_item.get("item_sku"),
                    "image_url": _image_url(rich_item),
                }
            )

        if not item_snapshots:
            raise OrderSyncError(f"Package {package_number} has no mappable items")

        package_result = await session.execute(
            select(OrderPackage).where(
                OrderPackage.shop_id == shop_id,
                OrderPackage.package_number == package_number,
            )
        )
        package = package_result.scalar_one_or_none()
        tracking_number = package_data.get("tracking_number")
        carrier = package_data.get("shipping_carrier") or summary.get("shipping_carrier")
        if package is None:
            package = OrderPackage(
                order_id=order.id,
                shop_id=shop_id,
                package_number=package_number,
                created_at=now,
            )
            session.add(package)
            await session.flush()
        package.order_id = order.id
        if tracking_number and not (
            package.tracking_source == "webhook"
            and package.tracking_event_at
            and package.tracking_event_at > now
        ):
            package.tracking_number = str(tracking_number)
            package.tracking_number_normalized = order_service.normalize_tracking_number(
                str(tracking_number)
            )
            package.tracking_source = "api"
        package.shipping_carrier = carrier
        package.sync_complete = bool(tracking_number)
        package.synced_at = now
        package.updated_at = now

        await session.execute(
            delete(OrderPackageItem).where(OrderPackageItem.package_id == package.id)
        )
        for snapshot in item_snapshots:
            session.add(
                OrderPackageItem(
                    package_id=package.id,
                    created_at=now,
                    updated_at=now,
                    **snapshot,
                )
            )
        synced_packages += 1

    await session.commit()
    return synced_packages


async def sync_orders(
    shop_id: int,
    access_token: str,
    order_sns: list[str],
) -> int:
    if not order_sns:
        return 0
    order_details = await _fetch_order_details(shop_id, access_token, order_sns)
    package_numbers = list(
        dict.fromkeys(
            str(package.get("package_number"))
            for order in order_details
            for package in order.get("package_list") or []
            if package.get("package_number")
        )
    )
    package_details = await _fetch_package_details(
        shop_id, access_token, package_numbers
    )
    synced_packages = 0
    async with async_session() as session:
        for order_detail in order_details:
            synced_packages += await _upsert_order(
                session, shop_id, order_detail, package_details
            )
    return synced_packages


async def sync_window(
    shop_id: int,
    access_token: str,
    time_from: datetime,
    time_to: datetime,
) -> int:
    if time_to <= time_from or time_to - time_from > timedelta(days=MAX_WINDOW_DAYS):
        raise ValueError("Order sync windows must be between 0 and 15 days")
    order_sns = await _list_order_sns(shop_id, access_token, time_from, time_to)
    return await sync_orders(shop_id, access_token, order_sns)


async def run_order_sync(
    days: int = 90,
    full_backfill: bool = False,
    now: datetime | None = None,
    reconciliation: bool = False,
) -> int:
    from app.services.order_refresh_service import (
        PRIORITY_BACKFILL,
        PRIORITY_INCOMPLETE,
        PRIORITY_POLL,
        PRIORITY_RECONCILE,
        SOURCE_BACKFILL,
        SOURCE_INCOMPLETE,
        SOURCE_POLL,
        SOURCE_RECONCILE,
        enqueue_refresh,
    )

    shop_id = settings.shopee_shop_id
    access_token = await get_valid_token(shop_id)
    if not access_token:
        raise OrderSyncError("No valid Shopee access token")

    now = now or datetime.now(TZ_GMT7)
    floor = now - timedelta(days=days)
    cycle_id = str(uuid.uuid4())
    async with async_session() as session:
        state = await _get_or_create_state(session, shop_id, floor)
        try:
            total = 0
            if full_backfill or reconciliation:
                cursor = now
                field = "create_time"
                source = SOURCE_RECONCILE if reconciliation else SOURCE_BACKFILL
                priority = PRIORITY_RECONCILE if reconciliation else PRIORITY_BACKFILL
                if reconciliation:
                    state.reconcile_cycle_id = cycle_id
                    state.reconcile_from = floor
                    state.reconcile_to = now
                    state.last_reconcile_started_at = now
                else:
                    state.backfill_cycle_id = cycle_id
                    state.backfill_from = floor
                while cursor > floor:
                    window_start = max(floor, cursor - timedelta(days=MAX_WINDOW_DAYS))
                    order_sns = await _list_order_sns(
                        shop_id, access_token, window_start, cursor, field
                    )
                    for order_sn in order_sns:
                        await enqueue_refresh(
                            session,
                            shop_id,
                            order_sn,
                            source,
                            priority,
                            backfill_cycle_id=None if reconciliation else cycle_id,
                            reconcile_cycle_id=cycle_id if reconciliation else None,
                        )
                    total += len(order_sns)
                    cursor = window_start
                    if reconciliation:
                        state.reconcile_cursor_time = cursor
                    else:
                        state.cursor_time = cursor
                    await session.commit()
                if reconciliation:
                    state.last_reconcile_success_at = now
                    state.last_reconcile_error = None
                else:
                    state.backfill_enumeration_completed_at = now
                    state.backfill_completed_at = now
            else:
                overlap = timedelta(minutes=settings.order_sync_overlap_minutes)
                cursor = state.poll_cursor_time or state.cursor_time or now - overlap
                if cursor.tzinfo is None:
                    cursor = cursor.replace(tzinfo=TZ_GMT7)
                start = cursor - overlap
                order_sns = await _list_order_sns(
                    shop_id, access_token, start, now, "update_time"
                )
                for order_sn in order_sns:
                    await enqueue_refresh(
                        session, shop_id, order_sn, SOURCE_POLL, PRIORITY_POLL
                    )
                total += len(order_sns)
                state.poll_cursor_time = now
                state.last_poll_started_at = now
                state.last_poll_success_at = now
                state.last_poll_error = None

                package_cursor = state.incomplete_cursor_package_id or 0
                stmt = (
                    select(OrderPackage.id, Order.order_sn)
                    .join(Order, Order.id == OrderPackage.order_id)
                    .where(
                        OrderPackage.shop_id == shop_id,
                        OrderPackage.sync_complete.is_(False),
                        OrderPackage.id > package_cursor,
                    )
                    .order_by(OrderPackage.id)
                    .limit(settings.order_incomplete_scan_batch_size)
                )
                rows = (await session.execute(stmt)).all()
                if not rows:
                    state.incomplete_cursor_package_id = 0
                else:
                    for package_id, order_sn in rows:
                        await enqueue_refresh(
                            session,
                            shop_id,
                            order_sn,
                            SOURCE_INCOMPLETE,
                            PRIORITY_INCOMPLETE,
                            revive_dead=False,
                        )
                        state.incomplete_cursor_package_id = package_id
                    total += len(rows)

            state.last_started_at = now
            state.last_success_at = now
            state.last_error = None
            state.updated_at = now
            await session.commit()
            return total
        except Exception as exc:
            await session.rollback()
            state = await session.get(OrderSyncState, shop_id)
            if state:
                state.last_error = str(exc)[:2000]
                if reconciliation:
                    state.last_reconcile_error = str(exc)[:2000]
                else:
                    state.last_poll_error = str(exc)[:2000]
                state.updated_at = datetime.now(TZ_GMT7)
                await session.commit()
            raise
