"""Add dashboard_users table and seed admin user

Revision ID: 005
Revises: 004
Create Date: 2026-02-19
"""

import hashlib
import secrets

from alembic import op
import sqlalchemy as sa

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), 100_000
    ).hex()


def upgrade() -> None:
    table = op.create_table(
        "dashboard_users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("username", sa.String(150), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(500), nullable=False),
        sa.Column("salt", sa.String(64), nullable=False),
        sa.Column("must_change_password", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    salt = secrets.token_hex(16)
    password_hash = _hash_password("admin", salt)

    op.bulk_insert(table, [
        {
            "username": "admin",
            "password_hash": password_hash,
            "salt": salt,
            "must_change_password": True,
        }
    ])


def downgrade() -> None:
    op.drop_table("dashboard_users")
