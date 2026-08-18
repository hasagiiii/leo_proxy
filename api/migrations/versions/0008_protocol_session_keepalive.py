"""Track protocol session keepalive freshness.

Revision ID: 0008_protocol_keepalive
Revises: 0007_protocol_observe
Create Date: 2026-08-10
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0008_protocol_keepalive"
down_revision = "0007_protocol_observe"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("account_renewal_sessions")}
    if "session_refreshed_at" not in columns:
        op.add_column(
            "account_renewal_sessions",
            sa.Column("session_refreshed_at", mysql.DATETIME(fsp=3), nullable=True),
        )
        op.execute(
            "UPDATE account_renewal_sessions "
            "SET session_refreshed_at = COALESCE(updated_at, created_at, UTC_TIMESTAMP(3))"
        )
    inspector = sa.inspect(bind)
    indexes = {index["name"] for index in inspector.get_indexes("account_renewal_sessions")}
    if "idx_account_renewal_sessions_keepalive" not in indexes:
        op.create_index(
            "idx_account_renewal_sessions_keepalive",
            "account_renewal_sessions",
            ["status", "session_refreshed_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {index["name"] for index in inspector.get_indexes("account_renewal_sessions")}
    if "idx_account_renewal_sessions_keepalive" in indexes:
        op.drop_index(
            "idx_account_renewal_sessions_keepalive",
            table_name="account_renewal_sessions",
        )
    columns = {column["name"] for column in inspector.get_columns("account_renewal_sessions")}
    if "session_refreshed_at" in columns:
        op.drop_column("account_renewal_sessions", "session_refreshed_at")
