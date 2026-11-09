from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.product import Base, TZ_GMT7


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("shop_id", "order_sn", name="uq_orders_shop_order_sn"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shop_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    order_sn: Mapped[str] = mapped_column(String(100), nullable=False)
    shopee_update_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(TZ_GMT7)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(TZ_GMT7),
        onupdate=lambda: datetime.now(TZ_GMT7),
    )

    packages: Mapped[list["OrderPackage"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class OrderPackage(Base):
    __tablename__ = "order_packages"
    __table_args__ = (
        UniqueConstraint(
            "shop_id", "package_number", name="uq_order_packages_shop_package"
        ),
        Index(
            "ix_order_packages_shop_tracking",
            "shop_id",
            "tracking_number_normalized",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    shop_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    package_number: Mapped[str] = mapped_column(String(255), nullable=False)
    tracking_number: Mapped[str | None] = mapped_column(String(255))
    tracking_number_normalized: Mapped[str | None] = mapped_column(String(255))
    shipping_carrier: Mapped[str | None] = mapped_column(String(255))
    sync_complete: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    tracking_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tracking_source: Mapped[str | None] = mapped_column(String(16))
    last_webhook_event_id: Mapped[int | None] = mapped_column(BigInteger)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(TZ_GMT7)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(TZ_GMT7),
        onupdate=lambda: datetime.now(TZ_GMT7),
    )

    order: Mapped[Order] = relationship(back_populates="packages")
    items: Mapped[list["OrderPackageItem"]] = relationship(
        back_populates="package", cascade="all, delete-orphan"
    )


class OrderPackageItem(Base):
    __tablename__ = "order_package_items"
    __table_args__ = (
        UniqueConstraint(
            "package_id", "item_id", "model_id", name="uq_order_package_items_item_model"
        ),
        CheckConstraint("package_quantity > 0", name="ck_order_package_items_quantity_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    package_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("order_packages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    item_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    model_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    item_name: Mapped[str] = mapped_column(String(1000), nullable=False)
    model_name: Mapped[str | None] = mapped_column(String(500))
    package_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    order_quantity: Mapped[int | None] = mapped_column(Integer)
    model_sku: Mapped[str | None] = mapped_column(String(255))
    image_url: Mapped[str | None] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(TZ_GMT7)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(TZ_GMT7),
        onupdate=lambda: datetime.now(TZ_GMT7),
    )

    package: Mapped[OrderPackage] = relationship(back_populates="items")


class OrderSyncState(Base):
    __tablename__ = "order_sync_state"

    shop_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    cursor_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    backfill_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    backfill_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    poll_cursor_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    incomplete_cursor_package_id: Mapped[int | None] = mapped_column(Integer)
    producer_lease_owner: Mapped[str | None] = mapped_column(String(128))
    producer_lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_poll_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_poll_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_poll_error: Mapped[str | None] = mapped_column(Text)
    backfill_cycle_id: Mapped[str | None] = mapped_column(String(36))
    backfill_enumeration_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reconcile_cycle_id: Mapped[str | None] = mapped_column(String(36))
    reconcile_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reconcile_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reconcile_cursor_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_reconcile_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_reconcile_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_reconcile_error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(TZ_GMT7),
        onupdate=lambda: datetime.now(TZ_GMT7),
    )
