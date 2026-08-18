"""Add indexes for registration client monitoring windows.

Revision ID: 0019_registration_client_idx
Revises: 0018_registration_used_flag
Create Date: 2026-08-15
"""

import sqlalchemy as sa
from alembic import op

revision = "0019_registration_client_idx"
down_revision = "0018_registration_used_flag"
branch_labels = None
depends_on = None


INDEXES = (
    ("idx_registration_records_client_started", ("client_id", "started_at")),
    ("idx_registration_records_updated_client", ("updated_at", "client_id")),
    ("idx_registration_records_finished_client", ("validation_finished_at", "client_id")),
)


def upgrade() -> None:
    bind = op.get_bind()
    existing = {index["name"] for index in sa.inspect(bind).get_indexes("registration_records")}
    for name, columns in INDEXES:
        if name not in existing:
            op.create_index(name, "registration_records", list(columns), unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    existing = {index["name"] for index in sa.inspect(bind).get_indexes("registration_records")}
    for name, _columns in reversed(INDEXES):
        if name in existing:
            op.drop_index(name, table_name="registration_records")
