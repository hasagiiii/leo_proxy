"""Add the independent Microsoft mailbox pool.

Revision ID: 0012_mailbox_pool
Revises: 0011_account_label
Create Date: 2026-08-13
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0012_mailbox_pool"
down_revision = "0011_account_label"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "mailboxes" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "mailboxes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("mailbox_uuid", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_ciphertext", sa.LargeBinary(length=65535), nullable=False),
        sa.Column("client_id_ciphertext", sa.LargeBinary(length=65535), nullable=False),
        sa.Column("refresh_token_ciphertext", sa.LargeBinary(length=65535), nullable=False),
        sa.Column("credential_key_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("disabled_reason", sa.String(length=255), nullable=True),
        sa.Column("validation_attempts", sa.Integer(), nullable=False),
        sa.Column("validation_lease_owner", sa.String(length=128), nullable=True),
        sa.Column("validation_lease_until", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column("next_validation_at", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column("last_validated_at", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_message", sa.String(length=1000), nullable=True),
        sa.Column("last_message_received_at", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=3),
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=3),
            server_default=sa.text("CURRENT_TIMESTAMP(3)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_mailboxes_email"),
        sa.UniqueConstraint("mailbox_uuid", name="uq_mailboxes_mailbox_uuid"),
    )
    op.create_index(
        "idx_mailboxes_validation_due",
        "mailboxes",
        ["status", "next_validation_at", "validation_lease_until"],
        unique=False,
    )
    op.create_index(
        "idx_mailboxes_last_message",
        "mailboxes",
        ["last_message_received_at"],
        unique=False,
    )


def downgrade() -> None:
    if "mailboxes" not in sa.inspect(op.get_bind()).get_table_names():
        return
    op.drop_index("idx_mailboxes_last_message", table_name="mailboxes")
    op.drop_index("idx_mailboxes_validation_due", table_name="mailboxes")
    op.drop_table("mailboxes")
