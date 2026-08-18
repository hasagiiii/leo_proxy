from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from video_task_service.models import RegistrationPoolSettings, RegistrationRecord


def test_registration_migration_is_the_single_head() -> None:
    root = Path(__file__).resolve().parents[1]
    config = Config(root / "alembic.ini")
    config.set_main_option("path_separator", "os")
    config.set_main_option("script_location", str(root / "migrations"))
    scripts = ScriptDirectory.from_config(config)

    assert scripts.get_heads() == ["0019_registration_client_idx"]
    revision = scripts.get_revision("0019_registration_client_idx")
    assert revision is not None
    assert len(revision.revision) <= 32
    assert revision.down_revision == "0018_registration_used_flag"


def test_registration_record_has_permanent_email_and_idempotency_constraints() -> None:
    names = {constraint.name for constraint in RegistrationRecord.__table__.constraints}

    assert "uq_registration_records_uuid" in names
    assert "uq_registration_records_email_snapshot" in names
    assert "uq_registration_records_client_claim_idem" in names
    assert "uq_registration_records_client_result_idem" in names
    project_claim = RegistrationRecord.__table__.c.project_mailbox_claim_id
    foreign_key = next(iter(project_claim.foreign_keys))
    assert project_claim.nullable
    assert foreign_key.target_fullname == "project_mailbox_claims.id"
    assert foreign_key.ondelete == "SET NULL"


def test_registration_settings_is_a_singleton_destination() -> None:
    columns = RegistrationPoolSettings.__table__.c

    assert columns.target_space_id.foreign_keys
    assert columns.default_max_concurrency.default.arg == 3
    assert columns.version.default.arg == 0


def test_registration_record_tracks_usage_and_indexes_export_selection() -> None:
    column = RegistrationRecord.__table__.c.is_used
    indexes = {
        index.name: tuple(item.name for item in index.columns)
        for index in RegistrationRecord.__table__.indexes
    }

    assert not column.nullable
    assert column.default.arg is False
    assert column.server_default.arg == "0"
    assert indexes["idx_registration_records_usage"] == (
        "is_used",
        "status",
        "awarded_points",
        "created_at",
    )
