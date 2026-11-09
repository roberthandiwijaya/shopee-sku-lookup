from pydantic import BaseModel


class TrackingItemOut(BaseModel):
    item_name: str
    model_name: str | None = None
    model_quantity_purchased: int
    model_sku: str | None = None
    image_url: str | None = None


class TrackingPackageOut(BaseModel):
    order_sn: str
    package_number: str
    shipping_carrier: str | None = None
    items: list[TrackingItemOut]
