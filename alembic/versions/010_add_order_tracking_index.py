"""add order tracking index

Revision ID: 010
Revises: 009
Create Date: 2026-07-14 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.BigInteger(), nullable=False),
        sa.Column("order_sn", sa.String(length=100), nullable=False),
        sa.Column("shopee_update_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("shop_id", "order_sn", name="uq_orders_shop_order_sn"),
    )
    op.create_table(
        "order_sync_state",
        sa.Column("shop_id", sa.BigInteger(), nullable=False),
        sa.Column("cursor_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("backfill_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("backfill_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("shop_id"),
    )
    op.create_table(
        "order_packages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.BigInteger(), nullable=False),
        sa.Column("package_number", sa.String(length=255), nullable=False),
        sa.Column("tracking_number", sa.String(length=255), nullable=True),
        sa.Column("tracking_number_normalized", sa.String(length=255), nullable=True),
        sa.Column("shipping_carrier", sa.String(length=255), nullable=True),
        sa.Column("sync_complete", sa.Boolean(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "shop_id", "package_number", name="uq_order_packages_shop_package"
        ),
    )
    op.create_index(
        "ix_order_packages_order_id", "order_packages", ["order_id"], unique=False
    )
    op.create_index(
        "ix_order_packages_shop_tracking",
        "order_packages",
        ["shop_id", "tracking_number_normalized"],
        unique=False,
    )
    op.create_table(
        "order_package_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("package_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.BigInteger(), nullable=False),
        sa.Column("model_id", sa.BigInteger(), nullable=False),
        sa.Column("item_name", sa.String(length=1000), nullable=False),
        sa.Column("model_name", sa.String(length=500), nullable=True),
        sa.Column("package_quantity", sa.Integer(), nullable=False),
        sa.Column("order_quantity", sa.Integer(), nullable=True),
        sa.Column("model_sku", sa.String(length=255), nullable=True),
        sa.Column("image_url", sa.String(length=2000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "package_quantity > 0", name="ck_order_package_items_quantity_positive"
        ),
        sa.ForeignKeyConstraint(
            ["package_id"], ["order_packages.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "package_id",
            "item_id",
            "model_id",
            name="uq_order_package_items_item_model",
        ),
    )
    op.create_index(
        "ix_order_package_items_package_id",
        "order_package_items",
        ["package_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_order_package_items_package_id", table_name="order_package_items")
    op.drop_table("order_package_items")
    op.drop_index("ix_order_packages_shop_tracking", table_name="order_packages")
    op.drop_index("ix_order_packages_order_id", table_name="order_packages")
    op.drop_table("order_packages")
    op.drop_table("order_sync_state")
    op.drop_table("orders")
