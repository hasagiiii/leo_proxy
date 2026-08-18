"""Store complete encrypted child-account ledger profiles.

Revision ID: 0010_account_ledger
Revises: 0009_client_rotation
Create Date: 2026-08-12
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0010_account_ledger"
down_revision = "0009_client_rotation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("account_ledger_profiles"):
        return
    op.create_table(
        "account_ledger_profiles",
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(length=255), nullable=False),
        sa.Column("source_kind", sa.String(length=64), nullable=False),
        sa.Column("source_raw", sa.Boolean(), nullable=False),
        sa.Column("source_exported_at", mysql.DATETIME(fsp=3), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False),
        sa.Column("source_file_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_record_id", sa.String(length=36), nullable=False),
        sa.Column("parent_account_id", sa.String(length=255), nullable=True),
        sa.Column("parent_account", sa.String(length=255), nullable=True),
        sa.Column("invite_status", sa.String(length=32), nullable=True),
        sa.Column("invited_at", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column("invite_error", sa.String(length=1000), nullable=True),
        sa.Column("invite_attempts", sa.Integer(), nullable=False),
        sa.Column("registration_status", sa.String(length=32), nullable=True),
        sa.Column("registration_attempts", sa.Integer(), nullable=False),
        sa.Column("registered_at", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column("registration_account_id", sa.String(length=255), nullable=True),
        sa.Column("registration_error", sa.String(length=1000), nullable=True),
        sa.Column("credits_total", sa.BigInteger(), nullable=True),
        sa.Column("credits_subscription", sa.BigInteger(), nullable=True),
        sa.Column("credits_purchase", sa.BigInteger(), nullable=True),
        sa.Column("credits_rollover", sa.BigInteger(), nullable=True),
        sa.Column("credits_checked_at", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column("credits_error", sa.String(length=1000), nullable=True),
        sa.Column("credits_attempts", sa.Integer(), nullable=False),
        sa.Column("source_created_at", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column("source_updated_at", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column("has_registration_password", sa.Boolean(), nullable=False),
        sa.Column("has_group_token", sa.Boolean(), nullable=False),
        sa.Column("has_authorization_token", sa.Boolean(), nullable=False),
        sa.Column("raw_record_ciphertext", sa.LargeBinary(length=65535), nullable=False),
        sa.Column("raw_record_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "credential_key_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=3),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
        ),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=3),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("account_id"),
        sa.UniqueConstraint("source_record_id"),
    )
    op.create_index(
        "idx_account_ledger_profiles_credits",
        "account_ledger_profiles",
        ["credits_total", "registration_status"],
    )


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("account_ledger_profiles"):
        op.drop_table("account_ledger_profiles")
