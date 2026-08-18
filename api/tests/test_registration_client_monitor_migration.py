from __future__ import annotations

from video_task_service.models import RegistrationRecord


def test_registration_client_monitor_indexes_match_window_queries() -> None:
    indexes = {
        index.name: tuple(column.name for column in index.columns)
        for index in RegistrationRecord.__table__.indexes
    }

    assert indexes["idx_registration_records_client_started"] == (
        "client_id",
        "started_at",
    )
    assert indexes["idx_registration_records_updated_client"] == (
        "updated_at",
        "client_id",
    )
    assert indexes["idx_registration_records_finished_client"] == (
        "validation_finished_at",
        "client_id",
    )
