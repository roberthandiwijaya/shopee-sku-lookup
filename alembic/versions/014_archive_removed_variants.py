"""Archive removed product variants without deleting their data.

Revision ID: 014
Revises: 013
"""
from alembic import op
import sqlalchemy as sa

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("product_models", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade():
    op.drop_column("product_models", "is_active")
