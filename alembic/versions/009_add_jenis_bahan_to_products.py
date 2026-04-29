"""add jenis_bahan to products

Revision ID: 009
Revises: 008
Create Date: 2026-04-29 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("products", sa.Column("jenis_bahan", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("products", "jenis_bahan")
