"""Add durable catalog export jobs.

Revision ID: 012
Revises: 011
"""
from alembic import op
import sqlalchemy as sa

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "catalog_export_jobs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("owner_hash", sa.String(64), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(64)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("result", sa.JSON()),
        sa.Column("warnings", sa.JSON()),
        sa.Column("error", sa.Text()),
        sa.Column("snapshot_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("owner_hash", "status", "expires_at"):
        op.create_index(f"ix_catalog_export_jobs_{column}", "catalog_export_jobs", [column])


def downgrade():
    op.drop_table("catalog_export_jobs")
