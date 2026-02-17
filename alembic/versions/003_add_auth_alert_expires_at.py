"""Add expires_at column to auth_alerts

Revision ID: 003
Revises: 002
Create Date: 2026-02-17
"""

from alembic import op
import sqlalchemy as sa

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "auth_alerts",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("auth_alerts", "expires_at")
