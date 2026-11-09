"""Private, durable catalog selections owned by dashboard users."""
from datetime import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.product import Base, TZ_GMT7


class CatalogCollection(Base):
    __tablename__ = "catalog_collections"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_catalog_collection_user_name"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    user_id: Mapped[int] = mapped_column(ForeignKey("dashboard_users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    options: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(TZ_GMT7))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(TZ_GMT7))
