from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.product import Base


class CatalogRateBucket(Base):
    """Short-lived, fixed-minute preflight counters; keys contain only HMACs."""

    __tablename__ = "catalog_rate_buckets"

    scope: Mapped[str] = mapped_column(String(16), primary_key=True)
    identity_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    minute: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, index=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
