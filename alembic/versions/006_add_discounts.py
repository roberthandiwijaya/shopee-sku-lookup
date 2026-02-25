"""Add discounts and discount_items tables

Revision ID: 006
Revises: 005
Create Date: 2026-02-25
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "discounts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("shop_id", sa.BigInteger, nullable=False),
        sa.Column("discount_id", sa.BigInteger, nullable=False, unique=True),
        sa.Column("discount_name", sa.String(500), nullable=True),
        sa.Column("discount_status", sa.String(50), nullable=True),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_data", JSONB, nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_discounts_discount_status", "discounts", ["discount_status"])

    op.create_table(
        "discount_items",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "discount_id",
            sa.Integer,
            sa.ForeignKey("discounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("shopee_discount_id", sa.BigInteger, nullable=False),
        sa.Column("shopee_item_id", sa.BigInteger, nullable=False),
        sa.Column("shopee_model_id", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("item_promotion_price", sa.Numeric(15, 2), nullable=True),
        sa.Column("purchase_limit", sa.Integer, nullable=True),
        sa.Column("raw_data", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_discount_items_shopee_discount_id", "discount_items", ["shopee_discount_id"])
    op.create_index(
        "uq_discount_item_model",
        "discount_items",
        ["discount_id", "shopee_item_id", "shopee_model_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("discount_items")
    op.drop_table("discounts")
