from __future__ import annotations

from datetime import UTC, datetime

from video_task_service.schemas import (
    ClientRegistrationTaskView,
    RegistrationClientListItem,
    RegistrationMonitorWindow,
)


def test_monitor_window_serializes_from_alias() -> None:
    now = datetime.now(UTC)
    window = RegistrationMonitorWindow(from_=now, to=now)

    assert window.model_dump(by_alias=True)["from"] == now


def test_client_list_item_and_task_view_exclude_saved_secrets() -> None:
    now = datetime.now(UTC)
    item = RegistrationClientListItem(
        client_id="invitation-desktop-12345678",
        display_name="客户端 12345678",
        health="NORMAL",
        health_reasons=[],
        last_activity_at=now,
        jobs=1,
        succeeded=1,
        failed=0,
        processing=0,
        retry_wait=0,
        stalled=0,
        success_rate=1,
        average_duration_seconds=30,
    )
    task = ClientRegistrationTaskView(
        registration_uuid="4ad3d08f-262c-4b32-93d8-0246adc5fa04",
        parent_account_uuid="f5ebdbf0-2ff2-4c17-aec9-8782d0bc045a",
        parent_email="parent@example.test",
        email="child@example.test",
        client_id=item.client_id,
        status="SUCCEEDED",
        registered_email="child@example.test",
        awarded_points=150,
        started_at=now,
        lease_expires_at=now,
        last_heartbeat_at=now,
        reported_at=now,
        validation_finished_at=now,
        validation_lease_until=None,
        retry_after=None,
        duration_seconds=30,
        created_at=now,
        updated_at=now,
    )

    payload = task.model_dump_json() + item.model_dump_json()
    assert "report_token" not in payload
    assert "session_ciphertext" not in payload
    assert "video_token_ciphertext" not in payload
    assert "cookies" not in payload
