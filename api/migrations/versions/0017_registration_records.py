"""Add durable mother-account registration records.

Revision ID: 0017_registration_records
Revises: 0016_project_mailbox_claims
Create Date: 2026-08-13
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0017_registration_records"
down_revision = "0016_project_mailbox_claims"
branch_labels = None
depends_on = None


def _timestamp(name: str, *, nullable: bool = True) -> sa.Column:
    return sa.Column(name, mysql.DATETIME(fsp=3), nullable=nullable)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    parent_columns = {column["name"] for column in inspector.get_columns("parent_accounts")}
    legacy_success_added = "legacy_invite_success_count" not in parent_columns
    legacy_failure_added = "legacy_invite_failure_count" not in parent_columns
    additions = (
        ("status", sa.Column("status", sa.String(32), nullable=False, server_default="ACTIVE")),
        (
            "consecutive_150_count",
            sa.Column(
                "consecutive_150_count", sa.SmallInteger(), nullable=False, server_default="0"
            ),
        ),
        (
            "successful_settlement_sequence",
            sa.Column(
                "successful_settlement_sequence",
                sa.BigInteger(),
                nullable=False,
                server_default="0",
            ),
        ),
        ("exhausted_reason", sa.Column("exhausted_reason", sa.String(64))),
        ("exhausted_at", _timestamp("exhausted_at")),
        (
            "legacy_invite_success_count",
            sa.Column(
                "legacy_invite_success_count", sa.BigInteger(), nullable=False, server_default="0"
            ),
        ),
        (
            "legacy_invite_failure_count",
            sa.Column(
                "legacy_invite_failure_count", sa.BigInteger(), nullable=False, server_default="0"
            ),
        ),
        ("version", sa.Column("version", sa.BigInteger(), nullable=False, server_default="0")),
    )
    for name, column in additions:
        if name not in parent_columns:
            op.add_column("parent_accounts", column)
    legacy_backfill = []
    if legacy_success_added:
        legacy_backfill.append("legacy_invite_success_count=invite_success_count")
    if legacy_failure_added:
        legacy_backfill.append("legacy_invite_failure_count=invite_failure_count")
    if legacy_backfill:
        op.execute(f"UPDATE parent_accounts SET {', '.join(legacy_backfill)}")

    tables = set(sa.inspect(bind).get_table_names())
    if "registration_records" not in tables:
        op.create_table(
            "registration_records",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("registration_uuid", sa.String(36), nullable=False),
            sa.Column("client_id", sa.String(128), nullable=False),
            sa.Column(
                "claim_idempotency_key", sa.String(128, collation="utf8mb4_bin"), nullable=False
            ),
            sa.Column("result_idempotency_key", sa.String(128, collation="utf8mb4_bin")),
            sa.Column("result_fingerprint", sa.String(64)),
            sa.Column("parent_account_id", sa.BigInteger(), nullable=False),
            sa.Column("parent_account_uuid_snapshot", sa.String(36), nullable=False),
            sa.Column("parent_email_snapshot", sa.String(255), nullable=False),
            sa.Column("mailbox_id", sa.BigInteger()),
            sa.Column("project_mailbox_claim_id", sa.BigInteger()),
            sa.Column("mailbox_uuid_snapshot", sa.String(36), nullable=False),
            sa.Column("email_snapshot", sa.String(255), nullable=False),
            sa.Column("report_token_encrypted", sa.LargeBinary(65535), nullable=False),
            _timestamp("lease_expires_at", nullable=False),
            _timestamp("last_heartbeat_at"),
            sa.Column("status", sa.String(32), nullable=False, server_default="RUNNING"),
            sa.Column("registered_email", sa.String(255)),
            sa.Column("verified_email", sa.String(255)),
            sa.Column("awarded_points", sa.BigInteger()),
            _timestamp("points_checked_at"),
            sa.Column("parent_settlement_sequence", sa.BigInteger()),
            sa.Column("session_ciphertext", sa.LargeBinary(65535)),
            sa.Column("video_token_ciphertext", sa.LargeBinary(65535)),
            _timestamp("token_expires_at"),
            sa.Column("cookie_count", sa.SmallInteger(), nullable=False, server_default="0"),
            sa.Column("credential_key_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("validation_attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("validation_lease_owner", sa.String(128)),
            _timestamp("validation_lease_until"),
            _timestamp("retry_after"),
            sa.Column("client_error_code", sa.String(64)),
            sa.Column("client_error_message", sa.String(1000)),
            sa.Column("validation_error_code", sa.String(64)),
            sa.Column("validation_error_message", sa.String(1000)),
            _timestamp("started_at", nullable=False),
            _timestamp("reported_at"),
            _timestamp("validation_finished_at"),
            sa.Column("account_id", sa.BigInteger()),
            sa.Column("account_uuid_snapshot", sa.String(36)),
            _timestamp("promoted_at"),
            sa.Column("version", sa.BigInteger(), nullable=False, server_default="0"),
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
            sa.ForeignKeyConstraint(
                ["parent_account_id"], ["parent_accounts.id"], ondelete="RESTRICT"
            ),
            sa.ForeignKeyConstraint(["mailbox_id"], ["mailboxes.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(
                ["project_mailbox_claim_id"],
                ["project_mailbox_claims.id"],
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("registration_uuid", name="uq_registration_records_uuid"),
            sa.UniqueConstraint("email_snapshot", name="uq_registration_records_email_snapshot"),
            sa.UniqueConstraint(
                "client_id",
                "claim_idempotency_key",
                name="uq_registration_records_client_claim_idem",
            ),
            sa.UniqueConstraint(
                "client_id",
                "result_idempotency_key",
                name="uq_registration_records_client_result_idem",
            ),
        )
        op.create_index(
            "idx_registration_records_parent_created",
            "registration_records",
            ["parent_account_id", "created_at"],
        )
        op.create_index(
            "idx_registration_records_validation_due",
            "registration_records",
            ["status", "retry_after", "validation_lease_until"],
        )
        op.create_index("idx_registration_records_account", "registration_records", ["account_id"])
        op.create_index(
            "idx_registration_records_project_claim",
            "registration_records",
            ["project_mailbox_claim_id"],
        )

    tables = set(sa.inspect(bind).get_table_names())
    if "registration_pool_settings" not in tables:
        op.create_table(
            "registration_pool_settings",
            sa.Column("id", sa.SmallInteger(), nullable=False, server_default="1"),
            sa.Column("target_space_id", sa.BigInteger()),
            sa.Column("default_max_concurrency", sa.Integer(), nullable=False, server_default="3"),
            sa.Column("version", sa.BigInteger(), nullable=False, server_default="0"),
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
            sa.ForeignKeyConstraint(["target_space_id"], ["spaces.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "registration_pool_settings" in tables:
        op.drop_table("registration_pool_settings")
    if "registration_records" in tables:
        op.drop_index(
            "idx_registration_records_project_claim", table_name="registration_records"
        )
        op.drop_index("idx_registration_records_account", table_name="registration_records")
        op.drop_index("idx_registration_records_validation_due", table_name="registration_records")
        op.drop_index("idx_registration_records_parent_created", table_name="registration_records")
        op.drop_table("registration_records")
    columns = {column["name"] for column in sa.inspect(bind).get_columns("parent_accounts")}
    for name in (
        "version",
        "legacy_invite_failure_count",
        "legacy_invite_success_count",
        "exhausted_at",
        "exhausted_reason",
        "successful_settlement_sequence",
        "consecutive_150_count",
        "status",
    ):
        if name in columns:
            op.drop_column("parent_accounts", name)
