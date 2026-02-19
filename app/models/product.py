from datetime import datetime, timedelta, timezone

# GMT+7 (WIB - Western Indonesian Time)
TZ_GMT7 = timezone(timedelta(hours=7))

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Use JSONB on PostgreSQL, plain JSON elsewhere (e.g. SQLite for tests)
JsonType = JSON().with_variant(JSONB, "postgresql")


class Base(DeclarativeBase):
    pass


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shop_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    item_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    item_sku: Mapped[str | None] = mapped_column(String(255), index=True)
    item_name: Mapped[str] = mapped_column(String(1000), nullable=False)
    item_status: Mapped[str | None] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str | None] = mapped_column(String(10))
    original_price: Mapped[float | None] = mapped_column(Numeric(15, 2))
    current_price: Mapped[float | None] = mapped_column(Numeric(15, 2))
    stock: Mapped[int | None] = mapped_column(Integer)
    images: Mapped[dict | None] = mapped_column(JsonType)
    category_id: Mapped[int | None] = mapped_column(BigInteger)
    has_model: Mapped[bool | None] = mapped_column(Boolean, default=False)
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

    models: Mapped[list["ProductModel"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class ProductModel(Base):
    __tablename__ = "product_models"
    __table_args__ = (Index("ix_product_models_model_sku", "model_sku"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    item_id: Mapped[int | None] = mapped_column(BigInteger)
    model_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    model_sku: Mapped[str | None] = mapped_column(String(255))
    model_name: Mapped[str | None] = mapped_column(String(500))
    original_price: Mapped[float | None] = mapped_column(Numeric(15, 2))
    current_price: Mapped[float | None] = mapped_column(Numeric(15, 2))
    stock: Mapped[int | None] = mapped_column(Integer)
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

    product: Mapped["Product"] = relationship(back_populates="models")


class AuthAlert(Base):
    __tablename__ = "auth_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shop_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    alert_type: Mapped[str] = mapped_column(String(100), nullable=False)
    message: Mapped[str] = mapped_column(String(1000), nullable=False)
    payload: Mapped[dict | None] = mapped_column(JsonType)
    is_dismissed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(TZ_GMT7)
    )


class ShopInfo(Base):
    __tablename__ = "shop_info"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shop_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    shop_name: Mapped[str | None] = mapped_column(String(500))
    auth_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expire_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_data: Mapped[dict | None] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(TZ_GMT7)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(TZ_GMT7),
        onupdate=lambda: datetime.now(TZ_GMT7),
    )


class DashboardUser(Base):
    __tablename__ = "dashboard_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(150), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(500), nullable=False)
    salt: Mapped[str] = mapped_column(String(64), nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(TZ_GMT7)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(TZ_GMT7),
        onupdate=lambda: datetime.now(TZ_GMT7),
    )


class ShopeeToken(Base):
    __tablename__ = "shopee_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shop_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    access_token: Mapped[str | None] = mapped_column(String(500))
    refresh_token: Mapped[str | None] = mapped_column(String(500))
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(TZ_GMT7)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(TZ_GMT7),
        onupdate=lambda: datetime.now(TZ_GMT7),
    )
