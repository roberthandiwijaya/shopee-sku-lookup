from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


# ── Request schemas ──────────────────────────────────────────────────────────

class DiscountItemIn(BaseModel):
    item_id: int
    model_id: int = 0
    item_promotion_price: Decimal
    purchase_limit: int | None = None


class DiscountCreateRequest(BaseModel):
    discount_name: str
    start_time: int  # Unix timestamp
    end_time: int    # Unix timestamp
    item_list: list[DiscountItemIn]


class DiscountUpdateRequest(BaseModel):
    discount_name: str | None = None
    start_time: int | None = None
    end_time: int | None = None


class DiscountItemUpdateIn(BaseModel):
    item_id: int
    model_id: int = 0
    item_promotion_price: Decimal | None = None
    purchase_limit: int | None = None


class DiscountAddItemsRequest(BaseModel):
    item_list: list[DiscountItemIn]


class DiscountUpdateItemsRequest(BaseModel):
    item_list: list[DiscountItemUpdateIn]


class DiscountDeleteItemIn(BaseModel):
    item_id: int
    model_id: int = 0


class DiscountDeleteItemsRequest(BaseModel):
    item_list: list[DiscountDeleteItemIn]


# ── Response schemas ─────────────────────────────────────────────────────────

class DiscountItemOut(BaseModel):
    shopee_item_id: int
    shopee_model_id: int
    item_promotion_price: Decimal | None = None
    purchase_limit: int | None = None

    model_config = {"from_attributes": True}


class DiscountOut(BaseModel):
    discount_id: int
    discount_name: str | None = None
    discount_status: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    synced_at: datetime | None = None
    items: list[DiscountItemOut] = []

    model_config = {"from_attributes": True}


class DiscountListOut(BaseModel):
    discounts: list[DiscountOut]
    total: int
    page: int
    per_page: int
    total_pages: int


class DiscountActionResponse(BaseModel):
    discount_id: int | None = None
    status: str
    message: str


class SyncDiscountsResponse(BaseModel):
    status: str
    message: str
    synced_count: int
