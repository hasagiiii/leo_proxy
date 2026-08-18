"""Add typed H3 modes and deferred media resolution state.

Revision ID: 0002_h3_media
Revises: 0001_initial
Create Date: 2026-08-05
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0002_h3_media"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    task_columns = {column["name"] for column in inspector.get_columns("tasks")}
    if "mode" not in task_columns:
        op.add_column("tasks", sa.Column("mode", sa.String(length=32), nullable=True))
    if "input_schema_version" not in task_columns:
        op.add_column(
            "tasks",
            sa.Column(
                "input_schema_version",
                sa.String(length=32),
                nullable=False,
                server_default="legacy",
            ),
        )
    if "media_total" not in task_columns:
        op.add_column(
            "tasks",
            sa.Column("media_total", sa.Integer(), nullable=False, server_default="0"),
        )
    if "media_resolved" not in task_columns:
        op.add_column(
            "tasks",
            sa.Column("media_resolved", sa.Integer(), nullable=False, server_default="0"),
        )
    if inspector.has_table("task_media_assets"):
        return
    op.create_table(
        "task_media_assets",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("asset_uuid", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("media_kind", sa.String(length=16), nullable=False),
        sa.Column("media_role", sa.String(length=32), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("source_url", sa.String(length=2048), nullable=False),
        sa.Column("source_url_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PENDING"),
        sa.Column("provider_asset_id", sa.String(length=255), nullable=True),
        sa.Column("provider_asset_type", sa.String(length=32), nullable=True),
        sa.Column("content_type", sa.String(length=128), nullable=True),
        sa.Column("content_length", sa.BigInteger(), nullable=True),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("metadata_json", mysql.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=1000), nullable=True),
        sa.Column("resolved_at", mysql.DATETIME(fsp=3), nullable=True),
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
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("asset_uuid"),
        sa.UniqueConstraint(
            "task_id",
            "attempt_no",
            "media_role",
            "media_kind",
            "ordinal",
            name="uk_task_media_attempt_role",
        ),
    )
    op.create_index(
        "idx_task_media_task_attempt",
        "task_media_assets",
        ["task_id", "attempt_no", "status"],
    )
    op.create_index(
        "idx_task_media_cache",
        "task_media_assets",
        ["account_id", "media_kind", "source_url_hash", "status"],
    )


def downgrade() -> None:
    op.drop_table("task_media_assets")
    op.drop_column("tasks", "media_resolved")
    op.drop_column("tasks", "media_total")
    op.drop_column("tasks", "input_schema_version")
    op.drop_column("tasks", "mode")
