"""Add the independent parent-account invitation pool.

Revision ID: 0013_parent_account_pool
Revises: 0012_mailbox_pool
Create Date: 2026-08-13
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0013_parent_account_pool"
down_revision = "0012_mailbox_pool"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "parent_accounts" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "parent_accounts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("parent_account_uuid", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_encrypted", sa.LargeBinary(length=65535), nullable=False),
        sa.Column(
            "credential_key_version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("invite_url", sa.String(length=8192), nullable=False),
        sa.Column(
            "invite_success_count",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "invite_failure_count",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
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
        sa.UniqueConstraint("email", name="uq_parent_accounts_email"),
        sa.UniqueConstraint(
            "parent_account_uuid",
            name="uq_parent_accounts_parent_account_uuid",
        ),
    )
    op.create_index(
        "idx_parent_accounts_email",
        "parent_accounts",
        ["email"],
        unique=False,
    )


def downgrade() -> None:
    if "parent_accounts" not in sa.inspect(op.get_bind()).get_table_names():
        return
    op.drop_index("idx_parent_accounts_email", table_name="parent_accounts")
    op.drop_table("parent_accounts")
