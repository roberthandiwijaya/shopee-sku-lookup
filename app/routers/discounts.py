from fastapi import APIRouter, Depends, HTTPException, Query, Security
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_session
from app.schemas.discount import (
    DiscountActionResponse,
    DiscountAddItemsRequest,
    DiscountCreateRequest,
    DiscountDeleteItemsRequest,
    DiscountListOut,
    DiscountOut,
    DiscountUpdateItemsRequest,
    DiscountUpdateRequest,
    SyncDiscountsResponse,
)
from app.services import discount_service
from app.services.shopee_auth import get_valid_token

router = APIRouter(prefix="/api/discounts", tags=["discounts"])

api_key_header = APIKeyHeader(name="X-API-Key")


async def verify_api_key(api_key: str = Security(api_key_header)):
    if api_key != settings.api_key:
        raise HTTPException(status_code=403, detail="Invalid API key")


# ── Sync (declared before /{discount_id} to avoid route collision) ────────────

@router.post("/sync", response_model=SyncDiscountsResponse, dependencies=[Depends(verify_api_key)])
async def sync_discounts():
    """Sync all discounts from Shopee to the local database."""
    try:
        synced_count = await discount_service.sync_discounts()
        return SyncDiscountsResponse(
            status="success",
            message=f"Sync completed successfully",
            synced_count=synced_count,
        )
    except Exception as e:
        return SyncDiscountsResponse(
            status="error",
            message=f"Sync failed: {e}",
            synced_count=0,
        )


# ── Read endpoints (local DB) ─────────────────────────────────────────────────

@router.get("", response_model=DiscountListOut)
async def list_discounts(
    page: int = Query(1, ge=1),
    status: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    """List discounts from local DB with optional status filter and pagination."""
    result = await discount_service.list_discounts(
        session, page=page, per_page=25, status_filter=status or None
    )
    discounts_out = [
        DiscountOut.model_validate(d) for d in result["discounts"]
    ]
    return DiscountListOut(
        discounts=discounts_out,
        total=result["total"],
        page=result["page"],
        per_page=result["per_page"],
        total_pages=result["total_pages"],
    )


@router.get("/{discount_id}", response_model=DiscountOut)
async def get_discount(
    discount_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Get a single discount with items from local DB."""
    discount = await discount_service.get_discount_detail(session, discount_id)
    if not discount:
        raise HTTPException(status_code=404, detail="Discount not found")
    return DiscountOut.model_validate(discount)


# ── Write endpoints (Shopee API) ──────────────────────────────────────────────

@router.post("", response_model=DiscountActionResponse, dependencies=[Depends(verify_api_key)])
async def create_discount(body: DiscountCreateRequest):
    """Create a new discount on Shopee."""
    shop_id = settings.shopee_shop_id
    access_token = await get_valid_token(shop_id)
    if not access_token:
        raise HTTPException(status_code=503, detail="No valid Shopee token")

    item_list = [
        {
            "item_id": i.item_id,
            "model_id": i.model_id,
            "item_promotion_price": float(i.item_promotion_price),
            **({"purchase_limit": i.purchase_limit} if i.purchase_limit is not None else {}),
        }
        for i in body.item_list
    ]

    data = await discount_service.create_discount(
        shop_id=shop_id,
        access_token=access_token,
        name=body.discount_name,
        start_time=body.start_time,
        end_time=body.end_time,
        item_list=item_list,
    )

    if data.get("error"):
        raise HTTPException(status_code=400, detail=data.get("message", "Shopee API error"))

    new_id = data.get("response", {}).get("discount_id")
    return DiscountActionResponse(
        discount_id=new_id,
        status="success",
        message="Discount created successfully",
    )


@router.put("/{discount_id}", response_model=DiscountActionResponse, dependencies=[Depends(verify_api_key)])
async def update_discount(discount_id: int, body: DiscountUpdateRequest):
    """Update an existing discount on Shopee."""
    shop_id = settings.shopee_shop_id
    access_token = await get_valid_token(shop_id)
    if not access_token:
        raise HTTPException(status_code=503, detail="No valid Shopee token")

    data = await discount_service.update_discount(
        shop_id=shop_id,
        access_token=access_token,
        discount_id=discount_id,
        name=body.discount_name,
        start_time=body.start_time,
        end_time=body.end_time,
    )

    if data.get("error"):
        raise HTTPException(status_code=400, detail=data.get("message", "Shopee API error"))

    return DiscountActionResponse(
        discount_id=discount_id,
        status="success",
        message="Discount updated successfully",
    )


@router.delete("/{discount_id}", response_model=DiscountActionResponse, dependencies=[Depends(verify_api_key)])
async def delete_discount(discount_id: int):
    """Delete a discount on Shopee."""
    shop_id = settings.shopee_shop_id
    access_token = await get_valid_token(shop_id)
    if not access_token:
        raise HTTPException(status_code=503, detail="No valid Shopee token")

    data = await discount_service.delete_discount(
        shop_id=shop_id,
        access_token=access_token,
        discount_id=discount_id,
    )

    if data.get("error"):
        raise HTTPException(status_code=400, detail=data.get("message", "Shopee API error"))

    return DiscountActionResponse(
        discount_id=discount_id,
        status="success",
        message="Discount deleted successfully",
    )


@router.post("/{discount_id}/end", response_model=DiscountActionResponse, dependencies=[Depends(verify_api_key)])
async def end_discount(discount_id: int):
    """End a running discount on Shopee."""
    shop_id = settings.shopee_shop_id
    access_token = await get_valid_token(shop_id)
    if not access_token:
        raise HTTPException(status_code=503, detail="No valid Shopee token")

    data = await discount_service.end_discount(
        shop_id=shop_id,
        access_token=access_token,
        discount_id=discount_id,
    )

    if data.get("error"):
        raise HTTPException(status_code=400, detail=data.get("message", "Shopee API error"))

    return DiscountActionResponse(
        discount_id=discount_id,
        status="success",
        message="Discount ended successfully",
    )


@router.post("/{discount_id}/items", response_model=DiscountActionResponse, dependencies=[Depends(verify_api_key)])
async def add_discount_items(discount_id: int, body: DiscountAddItemsRequest):
    """Add items to a discount on Shopee."""
    shop_id = settings.shopee_shop_id
    access_token = await get_valid_token(shop_id)
    if not access_token:
        raise HTTPException(status_code=503, detail="No valid Shopee token")

    item_list = [
        {
            "item_id": i.item_id,
            "model_id": i.model_id,
            "item_promotion_price": float(i.item_promotion_price),
            **({"purchase_limit": i.purchase_limit} if i.purchase_limit is not None else {}),
        }
        for i in body.item_list
    ]

    data = await discount_service.add_discount_items(
        shop_id=shop_id,
        access_token=access_token,
        discount_id=discount_id,
        item_list=item_list,
    )

    if data.get("error"):
        raise HTTPException(status_code=400, detail=data.get("message", "Shopee API error"))

    return DiscountActionResponse(
        discount_id=discount_id,
        status="success",
        message="Items added successfully",
    )


@router.put("/{discount_id}/items", response_model=DiscountActionResponse, dependencies=[Depends(verify_api_key)])
async def update_discount_items(discount_id: int, body: DiscountUpdateItemsRequest):
    """Update item prices in a discount on Shopee."""
    shop_id = settings.shopee_shop_id
    access_token = await get_valid_token(shop_id)
    if not access_token:
        raise HTTPException(status_code=503, detail="No valid Shopee token")

    item_list = []
    for i in body.item_list:
        entry: dict = {"item_id": i.item_id, "model_id": i.model_id}
        if i.item_promotion_price is not None:
            entry["item_promotion_price"] = float(i.item_promotion_price)
        if i.purchase_limit is not None:
            entry["purchase_limit"] = i.purchase_limit
        item_list.append(entry)

    data = await discount_service.update_discount_items(
        shop_id=shop_id,
        access_token=access_token,
        discount_id=discount_id,
        item_list=item_list,
    )

    if data.get("error"):
        raise HTTPException(status_code=400, detail=data.get("message", "Shopee API error"))

    return DiscountActionResponse(
        discount_id=discount_id,
        status="success",
        message="Items updated successfully",
    )


@router.delete("/{discount_id}/items", response_model=DiscountActionResponse, dependencies=[Depends(verify_api_key)])
async def delete_discount_items(discount_id: int, body: DiscountDeleteItemsRequest):
    """Remove items from a discount on Shopee."""
    shop_id = settings.shopee_shop_id
    access_token = await get_valid_token(shop_id)
    if not access_token:
        raise HTTPException(status_code=503, detail="No valid Shopee token")

    item_list = [{"item_id": i.item_id, "model_id": i.model_id} for i in body.item_list]

    data = await discount_service.delete_discount_items(
        shop_id=shop_id,
        access_token=access_token,
        discount_id=discount_id,
        item_list=item_list,
    )

    if data.get("error"):
        raise HTTPException(status_code=400, detail=data.get("message", "Shopee API error"))

    return DiscountActionResponse(
        discount_id=discount_id,
        status="success",
        message="Items deleted successfully",
    )
