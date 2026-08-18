from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects import mysql

from video_task_service.api.stats import (
    bucket_number,
    bucket_start_utc,
    effective_capacity,
    period_start_for,
    task_bucket_expression,
    trend_label,
    trend_settings,
)

NOW = datetime(2026, 8, 8, 13, 19, 12)
SHANGHAI_OFFSET_MINUTES = 8 * 60


def test_dashboard_period_starts_use_the_requested_local_day() -> None:
    assert period_start_for("total", NOW, SHANGHAI_OFFSET_MINUTES) is None
    assert period_start_for("today", NOW, SHANGHAI_OFFSET_MINUTES) == datetime(
        2026, 8, 7, 16
    )
    assert period_start_for("hour", NOW, SHANGHAI_OFFSET_MINUTES) == NOW - timedelta(hours=1)


def test_dashboard_trend_granularity_changes_with_period() -> None:
    total_granularity, total_seconds, total_start = trend_settings(
        "total", NOW, SHANGHAI_OFFSET_MINUTES
    )
    today_granularity, today_seconds, today_start = trend_settings(
        "today", NOW, SHANGHAI_OFFSET_MINUTES
    )
    hour_granularity, hour_seconds, hour_start = trend_settings(
        "hour", NOW, SHANGHAI_OFFSET_MINUTES
    )

    assert (total_granularity, total_seconds, total_start) == (
        "day",
        86_400,
        datetime(2026, 8, 1, 16),
    )
    assert (today_granularity, today_seconds, today_start) == (
        "hour",
        3_600,
        datetime(2026, 8, 7, 16),
    )
    assert (hour_granularity, hour_seconds, hour_start) == (
        "five_minutes",
        300,
        NOW - timedelta(hours=1),
    )


def test_dashboard_bucket_round_trip_and_labels_follow_client_timezone() -> None:
    bucket = bucket_number(NOW, SHANGHAI_OFFSET_MINUTES, 300)
    bucket_start = bucket_start_utc(bucket, SHANGHAI_OFFSET_MINUTES, 300)

    assert bucket_start == datetime(2026, 8, 8, 13, 15)
    assert trend_label(bucket_start, SHANGHAI_OFFSET_MINUTES, "five_minutes") == "21:15"
    assert trend_label(bucket_start, SHANGHAI_OFFSET_MINUTES, "day") == "08/08"


def test_dashboard_sql_bucket_is_independent_of_mysql_session_timezone() -> None:
    statement = select(task_bucket_expression(SHANGHAI_OFFSET_MINUTES, 300))
    compiled = str(
        statement.compile(dialect=mysql.dialect(), compile_kwargs={"literal_binds": True})
    ).lower()

    assert "timestampdiff(second, '1970-01-01 00:00:00', tasks.created_at)" in compiled
    assert "unix_timestamp" not in compiled


def test_effective_capacity_is_bounded_by_each_space() -> None:
    maximum, available = effective_capacity(
        [
            (10, 10, 402, 392),
            (30, 4, 12, 8),
            (8, 2, 3, 2),
        ]
    )

    assert maximum == 25
    assert available == 10
