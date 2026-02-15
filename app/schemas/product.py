from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class ProductModelOut(BaseModel):
    model_id: int
    model_sku: str | None = None
    model_name: str | None = None
    original_price: Decimal | None = None
    current_price: Decimal | None = None
    stock: int | None = None

    model_config = {"from_attributes": True}


class ProductOut(BaseModel):
    item_id: int
    item_sku: str | None = None
    item_name: str
    item_status: str | None = None
    description: str | None = None
    currency: str | None = None
    original_price: Decimal | None = None
    current_price: Decimal | None = None
    stock: int | None = None
    category_id: int | None = None
    has_model: bool | None = None
    models: list[ProductModelOut] = []
    synced_at: datetime | None = None

    model_config = {"from_attributes": True}


class ProductSearchResponse(BaseModel):
    products: list[ProductOut]
    count: int
    skus_not_found: list[str]


class SyncStatusResponse(BaseModel):
    last_synced_at: datetime | None = None
    total_products: int = 0
    total_models: int = 0
    token_status: str = "unknown"


class SyncTriggerResponse(BaseModel):
    message: str
    status: str
