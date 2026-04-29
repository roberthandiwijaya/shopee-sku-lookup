"""Add family_code to products and product_models

Revision ID: 007
Revises: 006
Create Date: 2026-04-28
"""

from alembic import op
import sqlalchemy as sa

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("products", sa.Column("family_code", sa.String(length=100), nullable=True))
    op.add_column("product_models", sa.Column("family_code", sa.String(length=100), nullable=True))
    op.create_index("ix_products_family_code", "products", ["family_code"])
    op.create_index("ix_product_models_family_code", "product_models", ["family_code"])


def downgrade() -> None:
    op.drop_index("ix_product_models_family_code", table_name="product_models")
    op.drop_index("ix_products_family_code", table_name="products")
    op.drop_column("product_models", "family_code")
    op.drop_column("products", "family_code")
