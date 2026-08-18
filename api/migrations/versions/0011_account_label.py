"""Add an optional source label to accounts.

Revision ID: 0011_account_label
Revises: 0010_account_ledger
Create Date: 2026-08-13
"""

import sqlalchemy as sa
from alembic import op

revision = "0011_account_label"
down_revision = "0010_account_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("accounts")
    }
    if "label" not in columns:
        op.add_column("accounts", sa.Column("label", sa.String(length=32), nullable=True))


def downgrade() -> None:
    columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("accounts")
    }
    if "label" in columns:
        op.drop_column("accounts", "label")
