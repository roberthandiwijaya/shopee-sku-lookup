"""Initial schema

Revision ID: 001
Revises:
Create Date: 2026-02-13
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("shop_id", sa.BigInteger, nullable=False),
        sa.Column("item_id", sa.BigInteger, nullable=False, unique=True),
        sa.Column("item_sku", sa.String(255), index=True),
        sa.Column("item_name", sa.String(1000), nullable=False),
        sa.Column("item_status", sa.String(50)),
        sa.Column("description", sa.Text),
        sa.Column("currency", sa.String(10)),
        sa.Column("original_price", sa.Numeric(15, 2)),
        sa.Column("current_price", sa.Numeric(15, 2)),
        sa.Column("stock", sa.Integer),
        sa.Column("images", JSONB),
        sa.Column("category_id", sa.BigInteger),
        sa.Column("has_model", sa.Boolean, default=False),
        sa.Column("raw_data", JSONB),
        sa.Column("synced_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "product_models",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "product_id",
            sa.Integer,
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("item_id", sa.BigInteger),
        sa.Column("model_id", sa.BigInteger, nullable=False, unique=True),
        sa.Column("model_sku", sa.String(255)),
        sa.Column("model_name", sa.String(500)),
        sa.Column("original_price", sa.Numeric(15, 2)),
        sa.Column("current_price", sa.Numeric(15, 2)),
        sa.Column("stock", sa.Integer),
        sa.Column("raw_data", JSONB),
        sa.Column("synced_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_product_models_model_sku", "product_models", ["model_sku"])

    op.create_table(
        "shopee_tokens",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("shop_id", sa.BigInteger, nullable=False, unique=True),
        sa.Column("access_token", sa.String(500)),
        sa.Column("refresh_token", sa.String(500)),
        sa.Column("token_expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("shopee_tokens")
    op.drop_table("product_models")
    op.drop_table("products")
