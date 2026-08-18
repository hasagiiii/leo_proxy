"""Add durable Cookie ZIP account-import batches.

Revision ID: 0015_account_cookie_imports
Revises: 0014_task_model_index
Create Date: 2026-08-13
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0015_account_cookie_imports"
down_revision = "0014_task_model_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    account_columns = {column["name"] for column in inspector.get_columns("accounts")}
    if "credential_source" not in account_columns:
        op.add_column(
            "accounts",
            sa.Column(
                "credential_source",
                sa.String(length=32),
                server_default=sa.text("'PASSWORD'"),
                nullable=False,
            ),
        )

    tables = set(sa.inspect(bind).get_table_names())
    if "account_cookie_import_batches" not in tables:
        op.create_table(
            "account_cookie_import_batches",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("batch_uuid", sa.String(length=36), nullable=False),
            sa.Column("idempotency_key", sa.String(length=128), nullable=False),
            sa.Column("archive_filename", sa.String(length=255), nullable=False),
            sa.Column("archive_sha256", sa.String(length=64), nullable=False),
            sa.Column("space_id", sa.BigInteger(), nullable=False),
            sa.Column(
                "status",
                sa.String(length=32),
                server_default=sa.text("'QUEUED'"),
                nullable=False,
            ),
            sa.Column("item_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("started_at", mysql.DATETIME(fsp=3), nullable=True),
            sa.Column("finished_at", mysql.DATETIME(fsp=3), nullable=True),
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
            sa.ForeignKeyConstraint(["space_id"], ["spaces.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("batch_uuid", name="uq_cookie_import_batches_uuid"),
            sa.UniqueConstraint("idempotency_key", name="uq_cookie_import_batches_idem"),
        )
        op.create_index(
            "idx_cookie_import_batches_status",
            "account_cookie_import_batches",
            ["status", "created_at"],
            unique=False,
        )

    tables = set(sa.inspect(bind).get_table_names())
    if "account_cookie_import_items" not in tables:
        op.create_table(
            "account_cookie_import_items",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("item_uuid", sa.String(length=36), nullable=False),
            sa.Column("batch_id", sa.BigInteger(), nullable=False),
            sa.Column("account_id", sa.BigInteger(), nullable=True),
            sa.Column("entry_name", sa.String(length=255), nullable=False),
            sa.Column("entry_sha256", sa.String(length=64), nullable=False),
            sa.Column("expected_login_name", sa.String(length=255), nullable=True),
            sa.Column("discovered_login_name", sa.String(length=255), nullable=True),
            sa.Column("session_ciphertext", sa.LargeBinary(length=65535), nullable=True),
            sa.Column(
                "credential_key_version",
                sa.Integer(),
                server_default=sa.text("1"),
                nullable=False,
            ),
            sa.Column(
                "status",
                sa.String(length=32),
                server_default=sa.text("'QUEUED'"),
                nullable=False,
            ),
            sa.Column(
                "stage",
                sa.String(length=32),
                server_default=sa.text("'RECEIVED'"),
                nullable=False,
            ),
            sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("lease_owner", sa.String(length=128), nullable=True),
            sa.Column("lease_until", mysql.DATETIME(fsp=3), nullable=True),
            sa.Column("retry_after", mysql.DATETIME(fsp=3), nullable=True),
            sa.Column("retryable", sa.Boolean(), server_default=sa.text("1"), nullable=False),
            sa.Column("last_error_code", sa.String(length=64), nullable=True),
            sa.Column("last_error_message", sa.String(length=300), nullable=True),
            sa.Column("activated_at", mysql.DATETIME(fsp=3), nullable=True),
            sa.Column("finished_at", mysql.DATETIME(fsp=3), nullable=True),
            sa.Column("version", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
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
            sa.ForeignKeyConstraint(
                ["account_id"],
                ["accounts.id"],
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(
                ["batch_id"],
                ["account_cookie_import_batches.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("item_uuid", name="uq_cookie_import_items_uuid"),
            sa.UniqueConstraint(
                "batch_id",
                "entry_sha256",
                name="uq_cookie_import_items_batch_entry_sha",
            ),
        )
        op.create_index(
            "idx_cookie_import_items_batch",
            "account_cookie_import_items",
            ["batch_id", "status"],
            unique=False,
        )
        op.create_index(
            "idx_cookie_import_items_due_lease",
            "account_cookie_import_items",
            ["status", "retry_after", "lease_until"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "account_cookie_import_items" in tables:
        op.drop_index(
            "idx_cookie_import_items_due_lease",
            table_name="account_cookie_import_items",
        )
        op.drop_index(
            "idx_cookie_import_items_batch",
            table_name="account_cookie_import_items",
        )
        op.drop_table("account_cookie_import_items")
    tables = set(sa.inspect(bind).get_table_names())
    if "account_cookie_import_batches" in tables:
        op.drop_index(
            "idx_cookie_import_batches_status",
            table_name="account_cookie_import_batches",
        )
        op.drop_table("account_cookie_import_batches")
    account_columns = {
        column["name"] for column in sa.inspect(bind).get_columns("accounts")
    }
    if "credential_source" in account_columns:
        op.drop_column("accounts", "credential_source")
