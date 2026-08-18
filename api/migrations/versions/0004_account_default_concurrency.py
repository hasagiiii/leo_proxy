"""Set the account concurrency default to three.

Revision ID: 0004_account_concurrency
Revises: 0003_h3_cache_ns
Create Date: 2026-08-06
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_account_concurrency"
down_revision = "0003_h3_cache_ns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "accounts",
        "max_concurrency",
        existing_type=sa.Integer(),
        existing_nullable=False,
        server_default=sa.text("3"),
    )


def downgrade() -> None:
    op.alter_column(
        "accounts",
        "max_concurrency",
        existing_type=sa.Integer(),
        existing_nullable=False,
        server_default=None,
    )
