"""Add protocol-renewal events and runtime heartbeat state.

Revision ID: 0007_protocol_observe
Revises: 0006_account_protocol_renewal
Create Date: 2026-08-10
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0007_protocol_observe"
down_revision = "0006_account_protocol_renewal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("protocol_renewal_events"):
        op.create_table(
            "protocol_renewal_events",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("event_uuid", sa.String(length=36), nullable=False),
            sa.Column("account_id", sa.BigInteger(), nullable=True),
            sa.Column("account_uuid_snapshot", sa.String(length=36), nullable=False),
            sa.Column("attempt_number", sa.Integer(), nullable=False),
            sa.Column("outcome", sa.String(length=32), nullable=False),
            sa.Column("applied", sa.Boolean(), server_default="0", nullable=False),
            sa.Column("retryable", sa.Boolean(), server_default="0", nullable=False),
            sa.Column("next_state", sa.String(length=32), nullable=False),
            sa.Column("error_code", sa.String(length=64), nullable=True),
            sa.Column("started_at", mysql.DATETIME(fsp=3), nullable=False),
            sa.Column("finished_at", mysql.DATETIME(fsp=3), nullable=False),
            sa.Column("latency_ms", sa.Integer(), nullable=False),
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
            sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("event_uuid"),
        )
        op.create_index(
            "idx_protocol_renewal_events_window",
            "protocol_renewal_events",
            ["finished_at", "outcome", "applied"],
        )
        op.create_index(
            "idx_protocol_renewal_events_account",
            "protocol_renewal_events",
            ["account_id", "finished_at"],
        )
        op.create_index(
            "idx_protocol_renewal_events_error",
            "protocol_renewal_events",
            ["error_code", "finished_at"],
        )
    inspector = sa.inspect(bind)
    if not inspector.has_table("protocol_renewal_runtime"):
        op.create_table(
            "protocol_renewal_runtime",
            sa.Column("instance_id", sa.String(length=128), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("app_version", sa.String(length=64), nullable=False),
            sa.Column("last_heartbeat_at", mysql.DATETIME(fsp=3), nullable=False),
            sa.Column("last_scan_at", mysql.DATETIME(fsp=3), nullable=True),
            sa.Column("last_claimed_at", mysql.DATETIME(fsp=3), nullable=True),
            sa.Column("last_completed_at", mysql.DATETIME(fsp=3), nullable=True),
            sa.Column("last_loop_error_code", sa.String(length=64), nullable=True),
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
            sa.PrimaryKeyConstraint("instance_id"),
        )
        op.create_index(
            "idx_protocol_renewal_runtime_heartbeat",
            "protocol_renewal_runtime",
            ["last_heartbeat_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("protocol_renewal_runtime"):
        op.drop_table("protocol_renewal_runtime")
    inspector = sa.inspect(bind)
    if inspector.has_table("protocol_renewal_events"):
        op.drop_table("protocol_renewal_events")
