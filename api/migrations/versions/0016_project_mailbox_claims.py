"""Add idempotent per-project mailbox claims.

Revision ID: 0016_project_mailbox_claims
Revises: 0015_account_cookie_imports
Create Date: 2026-08-13
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0016_project_mailbox_claims"
down_revision = "0015_account_cookie_imports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "mailbox_projects" not in tables:
        op.create_table(
            "mailbox_projects",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column(
                "project_key",
                sa.String(length=128, collation="utf8mb4_bin"),
                nullable=False,
            ),
            sa.Column("display_name", sa.String(length=128), nullable=False),
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
            sa.UniqueConstraint("project_key", name="uq_mailbox_projects_project_key"),
        )

    tables = set(sa.inspect(bind).get_table_names())
    if "project_mailbox_claims" not in tables:
        op.create_table(
            "project_mailbox_claims",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("claim_uuid", sa.String(length=36), nullable=False),
            sa.Column("project_id", sa.BigInteger(), nullable=False),
            sa.Column("mailbox_id", sa.BigInteger(), nullable=True),
            sa.Column("mailbox_uuid_snapshot", sa.String(length=36), nullable=False),
            sa.Column("email_snapshot", sa.String(length=255), nullable=False),
            sa.Column(
                "idempotency_key",
                sa.String(length=128, collation="utf8mb4_bin"),
                nullable=False,
            ),
            sa.Column("claimed_at", mysql.DATETIME(fsp=3), nullable=False),
            sa.Column(
                "created_at",
                mysql.DATETIME(fsp=3),
                server_default=sa.text("CURRENT_TIMESTAMP(3)"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["mailbox_id"],
                ["mailboxes.id"],
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(
                ["project_id"],
                ["mailbox_projects.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("claim_uuid", name="uq_project_mailbox_claims_uuid"),
            sa.UniqueConstraint(
                "project_id",
                "email_snapshot",
                name="uq_project_mailbox_claims_project_email",
            ),
            sa.UniqueConstraint(
                "project_id",
                "idempotency_key",
                name="uq_project_mailbox_claims_project_idem",
            ),
        )
        op.create_index(
            "idx_project_mailbox_claims_project_claimed",
            "project_mailbox_claims",
            ["project_id", "claimed_at"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "project_mailbox_claims" in tables:
        op.drop_index(
            "idx_project_mailbox_claims_project_claimed",
            table_name="project_mailbox_claims",
        )
        op.drop_table("project_mailbox_claims")
    tables = set(sa.inspect(bind).get_table_names())
    if "mailbox_projects" in tables:
        op.drop_table("mailbox_projects")
