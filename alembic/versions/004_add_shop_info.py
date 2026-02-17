"""Add shop_info table

Revision ID: 004
Revises: 003
Create Date: 2026-02-17
"""

from alembic import op
import sqlalchemy as sa

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shop_info",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("shop_id", sa.BigInteger, nullable=False, unique=True),
        sa.Column("shop_name", sa.String(500), nullable=True),
        sa.Column("auth_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expire_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_data", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("shop_info")
