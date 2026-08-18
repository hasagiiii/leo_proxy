"""Add the task model-history lookup index.

Revision ID: 0014_task_model_index
Revises: 0013_parent_account_pool
Create Date: 2026-08-13
"""

import sqlalchemy as sa
from alembic import op

revision = "0014_task_model_index"
down_revision = "0013_parent_account_pool"
branch_labels = None
depends_on = None

INDEX_NAME = "idx_tasks_model_created"


def upgrade() -> None:
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("tasks")}
    if INDEX_NAME in indexes:
        return
    op.create_index(INDEX_NAME, "tasks", ["model", "created_at"], unique=False)


def downgrade() -> None:
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("tasks")}
    if INDEX_NAME not in indexes:
        return
    op.drop_index(INDEX_NAME, table_name="tasks")
