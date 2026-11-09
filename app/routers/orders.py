from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_session
from app.dependencies import verify_api_key
from app.schemas.order import TrackingItemOut, TrackingPackageOut
from app.services import order_service
from app.services.shopee_auth import get_valid_token

router = APIRouter(tags=["orders"])


def _split_query_values(values: list[str] | None) -> list[str]:
    return [
        part.strip()
        for value in values or []
        for part in value.split(",")
        if part.strip()
    ]


@router.get(
    "/api/v2/order/get_package_detail",
    dependencies=[Depends(verify_api_key)],
)
async def get_package_detail(
    package_number_list: list[str] = Query(
        ...,
        description="One or more package numbers (repeatable or comma-separated)",
    ),
):
    """Return Shopee package details for the supplied package numbers."""
    package_numbers = _split_query_values(package_number_list)
    if not package_numbers:
        raise HTTPException(
            status_code=422,
            detail="package_number_list must contain at least one package number",
        )

    shop_id = settings.shopee_shop_id
    access_token = await get_valid_token(shop_id)
    if not access_token:
        raise HTTPException(status_code=503, detail="No valid Shopee token")

    return await order_service.get_package_detail(
        shop_id=shop_id,
        access_token=access_token,
        package_number_list=package_numbers,
    )


@router.get(
    "/api/v2/order/get_order_detail",
    dependencies=[Depends(verify_api_key)],
)
async def get_order_detail(
    order_sn_list: list[str] = Query(
        ...,
        description="One or more order serial numbers (repeatable or comma-separated)",
    ),
    response_optional_fields: list[str] | None = Query(
        None,
        description="Optional Shopee response fields (repeatable or comma-separated)",
    ),
):
    """Return Shopee order details for the supplied order serial numbers."""
    order_sns = _split_query_values(order_sn_list)
    if not order_sns:
        raise HTTPException(
            status_code=422,
            detail="order_sn_list must contain at least one order serial number",
        )

    optional_fields = _split_query_values(response_optional_fields)
    shop_id = settings.shopee_shop_id
    access_token = await get_valid_token(shop_id)
    if not access_token:
        raise HTTPException(status_code=503, detail="No valid Shopee token")

    return await order_service.get_order_detail(
        shop_id=shop_id,
        access_token=access_token,
        order_sn_list=order_sns,
        response_optional_fields=optional_fields or None,
    )


@router.get(
    "/api/orders/by-tracking-number",
    response_model=TrackingPackageOut,
    dependencies=[Depends(verify_api_key)],
)
async def get_order_by_tracking_number(
    tracking_number: str = Query(..., min_length=1, max_length=128),
    session: AsyncSession = Depends(get_session),
):
    """Look up a locally indexed package by courier tracking number."""
    normalized = order_service.normalize_tracking_number(tracking_number)
    if not normalized:
        raise HTTPException(status_code=422, detail="tracking_number must not be blank")

    package, backfill_complete = await order_service.lookup_package_by_tracking_number(
        session=session,
        shop_id=settings.shopee_shop_id,
        tracking_number=normalized,
    )
    if package is None:
        if not backfill_complete:
            raise HTTPException(
                status_code=503,
                detail="Order tracking index backfill is still in progress",
            )
        raise HTTPException(status_code=404, detail="Tracking number not found")
    if not package.sync_complete or not package.items:
        raise HTTPException(
            status_code=503,
            detail="Tracking information is not fully synchronized",
        )

    return TrackingPackageOut(
        order_sn=package.order.order_sn,
        package_number=package.package_number,
        shipping_carrier=package.shipping_carrier,
        items=[
            TrackingItemOut(
                item_name=item.item_name,
                model_name=item.model_name,
                model_quantity_purchased=item.package_quantity,
                model_sku=item.model_sku,
                image_url=item.image_url,
            )
            for item in sorted(package.items, key=lambda value: (value.item_id, value.model_id))
        ],
    )
