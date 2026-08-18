from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from video_task_service.registration_monitor import (
    MonitorWindow,
    aggregate_client_metrics,
    aggregate_client_rows,
    choose_bucket_seconds,
    classify_client_health,
    client_list_item,
    normalize_monitor_window,
    series_points,
)

NOW = datetime(2026, 8, 15, 1, 20, tzinfo=UTC).replace(tzinfo=None)
WINDOW = MonitorWindow(NOW - timedelta(minutes=10), NOW)


def row(
    *,
    status: str,
    minutes_ago: int,
    lease_minutes_ago: int = -1,
) -> dict[str, object]:
    started = NOW - timedelta(minutes=minutes_ago)
    finished = started + timedelta(seconds=30) if status in {"SUCCEEDED", "FAILED"} else None
    return {
        "registration_uuid": f"00000000-0000-0000-0000-{minutes_ago:012d}",
        "parent_account_uuid_snapshot": "f5ebdbf0-2ff2-4c17-aec9-8782d0bc045a",
        "parent_email_snapshot": "parent@example.test",
        "email_snapshot": "child@example.test",
        "client_id": "invitation-desktop-client-one",
        "status": status,
        "registered_email": "child@example.test",
        "awarded_points": 150,
        "is_used": False,
        "started_at": started,
        "lease_expires_at": NOW - timedelta(minutes=lease_minutes_ago),
        "last_heartbeat_at": started,
        "reported_at": finished,
        "validation_finished_at": finished,
        "validation_lease_until": None,
        "retry_after": None,
        "client_error_code": "CLIENT_FAILURE" if status == "FAILED" else None,
        "client_error_message": "failed" if status == "FAILED" else None,
        "validation_error_code": None,
        "validation_error_message": None,
        "created_at": started,
        "updated_at": finished or started,
    }


def test_default_window_is_ten_minutes_and_max_is_31_days() -> None:
    window = normalize_monitor_window(None, None, now=NOW)
    assert window.from_at == NOW - timedelta(minutes=10)
    assert window.to_at == NOW
    with pytest.raises(ValueError, match="31 days"):
        normalize_monitor_window(NOW - timedelta(days=32), NOW, now=NOW)


def test_health_marks_recent_failure_and_stale_lease() -> None:
    catalog = [{"client_id": "invitation-desktop-client-one", "last_activity_at": NOW}]
    failed_metrics = aggregate_client_rows(
        [row(status="FAILED", minutes_ago=2)], catalog, WINDOW, NOW
    )
    failed_item = client_list_item(failed_metrics["invitation-desktop-client-one"])
    assert failed_item.health == "ATTENTION"
    assert failed_item.latest_error_code == "CLIENT_FAILURE"

    stalled_metrics = aggregate_client_rows(
        [row(status="RUNNING", minutes_ago=2, lease_minutes_ago=1)],
        catalog,
        WINDOW,
        NOW,
    )
    stalled_item = client_list_item(stalled_metrics["invitation-desktop-client-one"])
    assert stalled_item.health == "ABNORMAL"
    assert stalled_item.stalled == 1


def test_three_consecutive_failures_are_abnormal() -> None:
    health, reasons = classify_client_health(
        {
            "active": True,
            "stalled": 0,
            "failed": 3,
            "succeeded": 1,
            "retry_wait": 0,
            "terminal_statuses": [
                (NOW, "FAILED"),
                (NOW - timedelta(seconds=1), "VALIDATION_FAILED"),
                (NOW - timedelta(seconds=2), "FAILED"),
                (NOW - timedelta(seconds=3), "SUCCEEDED"),
            ],
        }
    )
    assert health == "ABNORMAL"
    assert any(reason.code == "THREE_CONSECUTIVE_FAILURES" for reason in reasons)


def test_compact_sql_metrics_preserve_health_and_latest_error() -> None:
    metrics = aggregate_client_metrics(
        [
            {
                "client_id": "invitation-desktop-client-one",
                "last_activity_at": NOW,
                "window_last_activity_at": NOW,
                "jobs": 3,
                "succeeded": 0,
                "failed": 3,
                "processing": 0,
                "retry_wait": 0,
                "stalled": 0,
                "average_duration_seconds": 42.5,
            }
        ],
        [
            {
                "client_id": "invitation-desktop-client-one",
                "status": "FAILED",
                "finished_at": NOW - timedelta(seconds=offset),
            }
            for offset in range(3)
        ],
        [
            {
                "client_id": "invitation-desktop-client-one",
                "activity_at": NOW,
                "client_error_code": "CLIENT_FAILURE",
                "client_error_message": "failed",
                "validation_error_code": None,
                "validation_error_message": None,
            }
        ],
    )

    item = client_list_item(metrics["invitation-desktop-client-one"])
    assert item.health == "ABNORMAL"
    assert item.average_duration_seconds == 42.5
    assert item.latest_error_code == "CLIENT_FAILURE"


def test_series_uses_one_minute_buckets_for_ten_minute_window() -> None:
    points = series_points(
        [row(status="SUCCEEDED", minutes_ago=2), row(status="FAILED", minutes_ago=1)],
        WINDOW,
        choose_bucket_seconds(WINDOW),
    )
    assert len(points) == 10
    assert sum(point.claimed for point in points) == 2
    assert sum(point.succeeded for point in points) == 1
    assert sum(point.failed for point in points) == 1
