"""Add auth_alerts table

Revision ID: 002
Revises: 001
Create Date: 2026-02-17
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_alerts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("shop_id", sa.BigInteger, nullable=False, index=True),
        sa.Column("alert_type", sa.String(100), nullable=False),
        sa.Column("message", sa.String(1000), nullable=False),
        sa.Column("payload", JSONB),
        sa.Column("is_dismissed", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("auth_alerts")
