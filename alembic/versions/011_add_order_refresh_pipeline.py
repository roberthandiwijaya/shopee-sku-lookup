"""add order refresh pipeline

Revision ID: 011
Revises: 010
Create Date: 2026-07-15 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("order_packages", sa.Column("tracking_event_at", sa.DateTime(timezone=True)))
    op.add_column("order_packages", sa.Column("tracking_source", sa.String(length=16)))
    op.add_column("order_packages", sa.Column("last_webhook_event_id", sa.BigInteger()))

    for name, column in (
        ("poll_cursor_time", sa.DateTime(timezone=True)),
        ("incomplete_cursor_package_id", sa.Integer()),
        ("producer_lease_owner", sa.String(length=128)),
        ("producer_lease_expires_at", sa.DateTime(timezone=True)),
        ("last_poll_started_at", sa.DateTime(timezone=True)),
        ("last_poll_success_at", sa.DateTime(timezone=True)),
        ("last_poll_error", sa.Text()),
        ("backfill_cycle_id", sa.String(length=36)),
        ("backfill_enumeration_completed_at", sa.DateTime(timezone=True)),
        ("reconcile_cycle_id", sa.String(length=36)),
        ("reconcile_from", sa.DateTime(timezone=True)),
        ("reconcile_to", sa.DateTime(timezone=True)),
        ("reconcile_cursor_time", sa.DateTime(timezone=True)),
        ("last_reconcile_started_at", sa.DateTime(timezone=True)),
        ("last_reconcile_success_at", sa.DateTime(timezone=True)),
        ("last_reconcile_error", sa.Text()),
    ):
        op.add_column("order_sync_state", sa.Column(name, column))

    op.execute(
        "UPDATE order_sync_state SET poll_cursor_time = COALESCE(cursor_time, last_success_at), "
        "backfill_enumeration_completed_at = backfill_completed_at"
    )

    op.create_table(
        "order_refresh_jobs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("shop_id", sa.BigInteger(), nullable=False),
        sa.Column("order_sn", sa.String(length=100), nullable=False),
        sa.Column("package_number_hint", sa.String(length=255)),
        sa.Column("tracking_number_hint", sa.String(length=255)),
        sa.Column("source_event_at", sa.DateTime(timezone=True)),
        sa.Column("source_flags", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("priority", sa.SmallInteger(), nullable=False, server_default="10"),
        sa.Column("request_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("claimed_version", sa.Integer()),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=128)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_started_at", sa.DateTime(timezone=True)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("backfill_cycle_id", sa.String(length=36)),
        sa.Column("reconcile_cycle_id", sa.String(length=36)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending','leased','retry','completed','dead')",
            name="ck_order_refresh_jobs_status",
        ),
        sa.UniqueConstraint("shop_id", "order_sn", name="uq_order_refresh_jobs_shop_order"),
    )
    op.create_index("ix_order_refresh_jobs_claim", "order_refresh_jobs", ["status", "available_at", "priority", "id"])
    op.create_index("ix_order_refresh_jobs_lease", "order_refresh_jobs", ["lease_expires_at"])
    op.create_index("ix_order_refresh_jobs_backfill_cycle", "order_refresh_jobs", ["backfill_cycle_id"])
    op.create_index("ix_order_refresh_jobs_reconcile_cycle", "order_refresh_jobs", ["reconcile_cycle_id"])

    op.create_table(
        "shopee_webhook_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("body_sha256", sa.String(length=64), nullable=False, unique=True),
        sa.Column("code", sa.Integer()),
        sa.Column("shop_id", sa.BigInteger()),
        sa.Column("event_timestamp", sa.DateTime(timezone=True)),
        sa.Column("order_sn", sa.String(length=100)),
        sa.Column("package_number", sa.String(length=255)),
        sa.Column("tracking_number", sa.String(length=255)),
        sa.Column("raw_body", sa.LargeBinary(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("processing_status", sa.String(length=32), nullable=False),
        sa.Column("mapped_package_id", sa.Integer(), sa.ForeignKey("order_packages.id", ondelete="SET NULL")),
        sa.Column("refresh_job_id", sa.BigInteger()),
        sa.Column("delivery_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("first_received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("processing_error", sa.Text()),
    )
    op.create_index("ix_webhook_events_shop_received", "shopee_webhook_events", ["shop_id", "first_received_at"])
    op.create_index("ix_webhook_events_status_received", "shopee_webhook_events", ["processing_status", "first_received_at"])
    op.create_index("ix_webhook_events_order_package", "shopee_webhook_events", ["order_sn", "package_number"])


def downgrade() -> None:
    op.drop_table("shopee_webhook_events")
    op.drop_table("order_refresh_jobs")
    for name in (
        "last_reconcile_error", "last_reconcile_success_at", "last_reconcile_started_at",
        "reconcile_cursor_time", "reconcile_to", "reconcile_from", "reconcile_cycle_id",
        "backfill_enumeration_completed_at", "backfill_cycle_id", "last_poll_error",
        "last_poll_success_at", "last_poll_started_at", "producer_lease_expires_at",
        "producer_lease_owner", "incomplete_cursor_package_id", "poll_cursor_time",
    ):
        op.drop_column("order_sync_state", name)
    op.drop_column("order_packages", "last_webhook_event_id")
    op.drop_column("order_packages", "tracking_source")
    op.drop_column("order_packages", "tracking_event_at")
