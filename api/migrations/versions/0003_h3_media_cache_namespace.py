"""Scope deferred media IDs to the active upstream namespace.

Revision ID: 0003_h3_cache_ns
Revises: 0002_h3_media
Create Date: 2026-08-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_h3_cache_ns"
down_revision = "0002_h3_media"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {
        column["name"] for column in inspector.get_columns("task_media_assets")
    }
    if "provider_namespace" not in columns:
        op.add_column(
            "task_media_assets",
            sa.Column(
                "provider_namespace",
                sa.String(length=64),
                nullable=False,
                server_default="legacy",
            ),
        )
    indexes = {
        index["name"] for index in sa.inspect(bind).get_indexes("task_media_assets")
    }
    if "idx_task_media_namespace_cache" not in indexes:
        op.create_index(
            "idx_task_media_namespace_cache",
            "task_media_assets",
            [
                "provider_namespace",
                "account_id",
                "media_kind",
                "source_url_hash",
                "status",
            ],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {
        index["name"] for index in inspector.get_indexes("task_media_assets")
    }
    if "idx_task_media_namespace_cache" in indexes:
        op.drop_index(
            "idx_task_media_namespace_cache", table_name="task_media_assets"
        )
    columns = {
        column["name"]
        for column in sa.inspect(bind).get_columns("task_media_assets")
    }
    if "provider_namespace" in columns:
        op.drop_column("task_media_assets", "provider_namespace")
