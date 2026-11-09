"""Add export admission accounting and bounded preflight counters.

Revision ID: 015
Revises: 014
"""
from alembic import op
import sqlalchemy as sa

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("catalog_export_jobs", sa.Column("network_hash", sa.String(64), nullable=True))
    op.add_column("catalog_export_jobs", sa.Column("requested_family_count", sa.Integer(), nullable=True))
    op.add_column("catalog_export_jobs", sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()))
    # Use the immutable request snapshot, never worker-mutated `total`.
    if op.get_bind().dialect.name == "postgresql":
        op.execute("""UPDATE catalog_export_jobs SET requested_family_count =
            CASE WHEN jsonb_typeof(options::jsonb -> 'family_codes') = 'array'
            THEN jsonb_array_length(options::jsonb -> 'family_codes') ELSE 0 END""")
    else:
        op.execute("""UPDATE catalog_export_jobs SET requested_family_count =
            COALESCE(json_array_length(options, '$.family_codes'), 0)""")
    with op.batch_alter_table("catalog_export_jobs") as batch:
        batch.alter_column("requested_family_count", existing_type=sa.Integer(), nullable=False)
    op.create_index("ix_catalog_export_jobs_owner_created", "catalog_export_jobs", ["owner_hash", "created_at"])
    op.create_index("ix_catalog_export_jobs_network_created", "catalog_export_jobs", ["network_hash", "created_at"])
    op.create_table("catalog_rate_buckets",
        sa.Column("scope", sa.String(16), primary_key=True),
        sa.Column("identity_hash", sa.String(64), primary_key=True),
        sa.Column("minute", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False))
    op.create_index("ix_catalog_rate_buckets_minute", "catalog_rate_buckets", ["minute"])


def downgrade():
    op.drop_index("ix_catalog_rate_buckets_minute", table_name="catalog_rate_buckets")
    op.drop_table("catalog_rate_buckets")
    op.drop_index("ix_catalog_export_jobs_network_created", table_name="catalog_export_jobs")
    op.drop_index("ix_catalog_export_jobs_owner_created", table_name="catalog_export_jobs")
    with op.batch_alter_table("catalog_export_jobs") as batch:
        batch.drop_column("cancel_requested")
        batch.drop_column("requested_family_count")
        batch.drop_column("network_hash")
