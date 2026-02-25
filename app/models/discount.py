from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.product import Base, JsonType, TZ_GMT7


class Discount(Base):
    __tablename__ = "discounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shop_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    discount_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    discount_name: Mapped[str | None] = mapped_column(String(500))
    discount_status: Mapped[str | None] = mapped_column(String(50), index=True)
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_data: Mapped[dict | None] = mapped_column(JsonType)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(TZ_GMT7)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(TZ_GMT7),
        onupdate=lambda: datetime.now(TZ_GMT7),
    )

    items: Mapped[list["DiscountItem"]] = relationship(
        back_populates="discount", cascade="all, delete-orphan"
    )


class DiscountItem(Base):
    __tablename__ = "discount_items"
    __table_args__ = (
        UniqueConstraint("discount_id", "shopee_item_id", "shopee_model_id", name="uq_discount_item_model"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    discount_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("discounts.id", ondelete="CASCADE"), nullable=False
    )
    shopee_discount_id: Mapped[int] = mapped_column(BigInteger, index=True)
    shopee_item_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    shopee_model_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    item_promotion_price: Mapped[float | None] = mapped_column(Numeric(15, 2))
    purchase_limit: Mapped[int | None] = mapped_column(Integer)
    raw_data: Mapped[dict | None] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(TZ_GMT7)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(TZ_GMT7),
        onupdate=lambda: datetime.now(TZ_GMT7),
    )

    discount: Mapped["Discount"] = relationship(back_populates="items")
