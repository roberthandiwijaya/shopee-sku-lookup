"""Keep family_code only on products

Revision ID: 008
Revises: 007
Create Date: 2026-04-29
"""

from alembic import op
import sqlalchemy as sa

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE products
        SET family_code = sub.family_code
        FROM (
            SELECT pm.item_id, MIN(pm.family_code) AS family_code
            FROM product_models pm
            WHERE pm.family_code IS NOT NULL AND pm.family_code <> ''
            GROUP BY pm.item_id
        ) AS sub
        WHERE products.item_id = sub.item_id
          AND (products.family_code IS NULL OR products.family_code = '')
        """
    )
    op.drop_index("ix_product_models_family_code", table_name="product_models")
    op.drop_column("product_models", "family_code")


def downgrade() -> None:
    op.add_column("product_models", sa.Column("family_code", sa.String(length=100), nullable=True))
    op.create_index("ix_product_models_family_code", "product_models", ["family_code"])
    op.execute(
        """
        UPDATE product_models
        SET family_code = products.family_code
        FROM products
        WHERE product_models.item_id = products.item_id
        """
    )
