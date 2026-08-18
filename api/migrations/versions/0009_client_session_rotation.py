"""Track client session age and capability for proactive rotation.

Revision ID: 0009_client_rotation
Revises: 0008_protocol_keepalive
Create Date: 2026-08-10
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0009_client_rotation"
down_revision = "0008_protocol_keepalive"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {
        column["name"]
        for column in inspector.get_columns("account_renewal_sessions")
    }
    if "client_reported_at" not in columns:
        op.add_column(
            "account_renewal_sessions",
            sa.Column("client_reported_at", mysql.DATETIME(fsp=3), nullable=True),
        )
    if "client_version" not in columns:
        op.add_column(
            "account_renewal_sessions",
            sa.Column("client_version", sa.String(length=32), nullable=True),
        )
    if "renewal_capability" not in columns:
        op.add_column(
            "account_renewal_sessions",
            sa.Column("renewal_capability", sa.String(length=64), nullable=True),
        )
    op.execute(
        "UPDATE account_renewal_sessions AS rs "
        "SET rs.client_reported_at = COALESCE(("
        "SELECT MAX(j.token_received_at) FROM account_login_jobs AS j "
        "WHERE j.account_id = rs.account_id AND j.token_received_at IS NOT NULL"
        "), rs.created_at), "
        "rs.renewal_capability = COALESCE(rs.renewal_capability, 'better-auth-v1') "
        "WHERE rs.client_reported_at IS NULL"
    )
    inspector = sa.inspect(bind)
    indexes = {
        index["name"]
        for index in inspector.get_indexes("account_renewal_sessions")
    }
    if "idx_account_renewal_sessions_client_age" not in indexes:
        op.create_index(
            "idx_account_renewal_sessions_client_age",
            "account_renewal_sessions",
            ["status", "client_reported_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {
        index["name"]
        for index in inspector.get_indexes("account_renewal_sessions")
    }
    if "idx_account_renewal_sessions_client_age" in indexes:
        op.drop_index(
            "idx_account_renewal_sessions_client_age",
            table_name="account_renewal_sessions",
        )
    columns = {
        column["name"]
        for column in sa.inspect(bind).get_columns("account_renewal_sessions")
    }
    for column in ("renewal_capability", "client_version", "client_reported_at"):
        if column in columns:
            op.drop_column("account_renewal_sessions", column)
