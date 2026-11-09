from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.product import Base, TZ_GMT7

JsonType = JSON().with_variant(JSONB, "postgresql")


class ShopeeWebhookEvent(Base):
    __tablename__ = "shopee_webhook_events"
    __table_args__ = (
        Index("ix_webhook_events_shop_received", "shop_id", "first_received_at"),
        Index("ix_webhook_events_status_received", "processing_status", "first_received_at"),
        Index("ix_webhook_events_order_package", "order_sn", "package_number"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    body_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    code: Mapped[int | None] = mapped_column(Integer)
    shop_id: Mapped[int | None] = mapped_column(BigInteger)
    event_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    order_sn: Mapped[str | None] = mapped_column(String(100))
    package_number: Mapped[str | None] = mapped_column(String(255))
    tracking_number: Mapped[str | None] = mapped_column(String(255))
    raw_body: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    payload: Mapped[dict | None] = mapped_column(JsonType)
    processing_status: Mapped[str] = mapped_column(String(32), nullable=False)
    mapped_package_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("order_packages.id", ondelete="SET NULL")
    )
    refresh_job_id: Mapped[int | None] = mapped_column(BigInteger)
    delivery_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(TZ_GMT7)
    )
    last_received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(TZ_GMT7)
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_error: Mapped[str | None] = mapped_column(Text)


class OrderRefreshJob(Base):
    __tablename__ = "order_refresh_jobs"
    __table_args__ = (
        UniqueConstraint("shop_id", "order_sn", name="uq_order_refresh_jobs_shop_order"),
        CheckConstraint(
            "status IN ('pending','leased','retry','completed','dead')",
            name="ck_order_refresh_jobs_status",
        ),
        Index("ix_order_refresh_jobs_claim", "status", "available_at", "priority", "id"),
        Index("ix_order_refresh_jobs_lease", "lease_expires_at"),
        Index("ix_order_refresh_jobs_backfill_cycle", "backfill_cycle_id"),
        Index("ix_order_refresh_jobs_reconcile_cycle", "reconcile_cycle_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shop_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    order_sn: Mapped[str] = mapped_column(String(100), nullable=False)
    package_number_hint: Mapped[str | None] = mapped_column(String(255))
    tracking_number_hint: Mapped[str | None] = mapped_column(String(255))
    source_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_flags: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=10)
    request_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    claimed_version: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(TZ_GMT7)
    )
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    backfill_cycle_id: Mapped[str | None] = mapped_column(String(36))
    reconcile_cycle_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(TZ_GMT7)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(TZ_GMT7),
        onupdate=lambda: datetime.now(TZ_GMT7),
    )
