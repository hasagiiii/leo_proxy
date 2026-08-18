"""Add the registration-record usage marker.

Revision ID: 0018_registration_used_flag
Revises: 0017_registration_records
Create Date: 2026-08-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0018_registration_used_flag"
down_revision = "0017_registration_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("registration_records")}
    if "is_used" not in columns:
        op.add_column(
            "registration_records",
            sa.Column(
                "is_used",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            ),
        )

    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("registration_records")}
    if "idx_registration_records_usage" not in indexes:
        op.create_index(
            "idx_registration_records_usage",
            "registration_records",
            ["is_used", "status", "awarded_points", "created_at"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {index["name"] for index in inspector.get_indexes("registration_records")}
    if "idx_registration_records_usage" in indexes:
        op.drop_index("idx_registration_records_usage", table_name="registration_records")

    columns = {column["name"] for column in sa.inspect(bind).get_columns("registration_records")}
    if "is_used" in columns:
        op.drop_column("registration_records", "is_used")
