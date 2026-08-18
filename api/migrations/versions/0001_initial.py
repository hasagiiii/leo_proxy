"""Create the account pool and task service schema.

Revision ID: 0001_initial
Revises: None
Create Date: 2026-08-05
"""

from alembic import op

from video_task_service import models  # noqa: F401
from video_task_service.db import Base

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind(), checkfirst=True)
