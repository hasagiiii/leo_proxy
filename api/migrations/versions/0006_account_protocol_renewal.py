"""Add encrypted browser sessions and cloud protocol-renewal state.

Revision ID: 0006_account_protocol_renewal
Revises: 0005_account_login_jobs
Create Date: 2026-08-09
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0006_account_protocol_renewal"
down_revision = "0005_account_login_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("account_renewal_sessions"):
        return
    op.create_table(
        "account_renewal_sessions",
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("session_ciphertext", sa.LargeBinary(length=65535), nullable=False),
        sa.Column(
            "credential_key_version", sa.Integer(), server_default="1", nullable=False
        ),
        sa.Column("status", sa.String(length=32), server_default="IDLE", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("claimed_account_version", sa.BigInteger(), nullable=True),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_until", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column("fallback_after", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column("retry_after", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column("last_attempt_at", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column("last_success_at", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("previous_token_expires_at", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column("renewed_token_expires_at", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=3),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=3),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("account_id"),
    )
    op.create_index(
        "idx_account_renewal_sessions_claim",
        "account_renewal_sessions",
        ["status", "retry_after", "lease_until"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("account_renewal_sessions"):
        op.drop_table("account_renewal_sessions")
