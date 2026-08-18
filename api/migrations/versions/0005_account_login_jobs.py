"""Add leased account-login jobs and the serialized idle-pool policy.

Revision ID: 0005_account_login_jobs
Revises: 0004_account_concurrency
Create Date: 2026-08-08
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0005_account_login_jobs"
down_revision = "0004_account_concurrency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("login_pool_policy"):
        op.create_table(
            "login_pool_policy",
            sa.Column("id", sa.SmallInteger(), nullable=False),
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
            sa.PrimaryKeyConstraint("id"),
        )
    op.execute(
        sa.text(
            "INSERT IGNORE INTO login_pool_policy (id, created_at, updated_at) "
            "VALUES (1, NOW(3), NOW(3))"
        )
    )

    if inspector.has_table("account_login_jobs"):
        return
    op.create_table(
        "account_login_jobs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("job_uuid", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("active_account_id", sa.BigInteger(), nullable=True),
        sa.Column("job_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="LEASED"),
        sa.Column("lease_owner", sa.String(length=128), nullable=False),
        sa.Column("lease_token_hash", sa.String(length=64), nullable=False),
        sa.Column("lease_until", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column("claimed_account_version", sa.BigInteger(), nullable=False),
        sa.Column("claimed_token_refreshed_at", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column("attempt_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("reported_token_fingerprint", sa.String(length=16), nullable=True),
        sa.Column("token_received_at", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column("validation_finished_at", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column("retry_after", mysql.DATETIME(fsp=3), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=1000), nullable=True),
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
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["active_account_id"], ["accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_uuid"),
        sa.UniqueConstraint(
            "active_account_id",
            name="uk_account_login_jobs_active_account",
        ),
    )
    op.create_index(
        "idx_account_login_jobs_claim",
        "account_login_jobs",
        ["status", "lease_until", "created_at"],
    )
    op.create_index(
        "idx_account_login_jobs_account",
        "account_login_jobs",
        ["account_id", "id"],
    )
    op.create_index(
        "idx_account_login_jobs_retry",
        "account_login_jobs",
        ["account_id", "status", "retry_after"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("account_login_jobs"):
        op.drop_table("account_login_jobs")
    if inspector.has_table("login_pool_policy"):
        op.drop_table("login_pool_policy")
