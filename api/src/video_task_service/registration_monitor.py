from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import floor
from typing import Any
from uuid import UUID

from sqlalchemy import and_, case, func, literal_column, or_, select

from video_task_service.models import RegistrationRecord
from video_task_service.schemas import (
    ClientRegistrationTaskView,
    RegistrationClientHealthReason,
    RegistrationClientListItem,
    RegistrationClientSeriesPoint,
    RegistrationClientSummary,
)

MAX_WINDOW = timedelta(days=31)
DEFAULT_WINDOW = timedelta(minutes=10)
PROCESSING_STATUSES = frozenset(
    {"RUNNING", "COOKIE_REPORTED", "VALIDATING", "VALIDATION_RETRY_WAIT"}
)
RETRY_STATUSES = frozenset({"VALIDATION_RETRY_WAIT"})
FAILED_STATUSES = frozenset({"FAILED", "VALIDATION_FAILED"})
TERMINAL_STATUSES = frozenset({"SUCCEEDED", "FAILED", "VALIDATION_FAILED"})


@dataclass(frozen=True)
class MonitorWindow:
    from_at: datetime
    to_at: datetime

    @property
    def seconds(self) -> int:
        return max(1, int((self.to_at - self.from_at).total_seconds()))


MONITOR_COLUMNS = (
    RegistrationRecord.registration_uuid,
    RegistrationRecord.parent_account_uuid_snapshot,
    RegistrationRecord.parent_email_snapshot,
    RegistrationRecord.email_snapshot,
    RegistrationRecord.client_id,
    RegistrationRecord.status,
    RegistrationRecord.registered_email,
    RegistrationRecord.awarded_points,
    RegistrationRecord.is_used,
    RegistrationRecord.started_at,
    RegistrationRecord.lease_expires_at,
    RegistrationRecord.last_heartbeat_at,
    RegistrationRecord.reported_at,
    RegistrationRecord.validation_finished_at,
    RegistrationRecord.validation_lease_until,
    RegistrationRecord.retry_after,
    RegistrationRecord.client_error_code,
    RegistrationRecord.client_error_message,
    RegistrationRecord.validation_error_code,
    RegistrationRecord.validation_error_message,
    RegistrationRecord.created_at,
    RegistrationRecord.updated_at,
)


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def normalize_monitor_window(
    from_value: datetime | None,
    to_value: datetime | None,
    *,
    now: datetime | None = None,
) -> MonitorWindow:
    current = _utc_naive(now or datetime.now(UTC))
    end = _utc_naive(to_value) if to_value is not None else current
    start = _utc_naive(from_value) if from_value is not None else end - DEFAULT_WINDOW
    if start >= end:
        raise ValueError("monitor window requires from to be before to")
    if end - start > MAX_WINDOW:
        raise ValueError("monitor window cannot exceed 31 days")
    return MonitorWindow(from_at=start, to_at=end)


def choose_bucket_seconds(window: MonitorWindow) -> int:
    seconds = window.seconds
    if seconds <= 3600:
        return 60
    if seconds <= 6 * 3600:
        return 5 * 60
    if seconds <= 24 * 3600:
        return 30 * 60
    return 3600


def _activity_expression() -> Any:
    started = RegistrationRecord.started_at
    return func.greatest(
        started,
        func.coalesce(RegistrationRecord.last_heartbeat_at, started),
        func.coalesce(RegistrationRecord.reported_at, started),
        func.coalesce(RegistrationRecord.validation_finished_at, started),
        func.coalesce(RegistrationRecord.updated_at, started),
    )


def client_catalog_statement(client_id: str | None = None) -> Any:
    statement = select(
        RegistrationRecord.client_id,
        func.max(_activity_expression()).label("last_activity_at"),
    ).group_by(RegistrationRecord.client_id)
    if client_id is not None:
        statement = statement.where(RegistrationRecord.client_id == client_id)
    return statement


def client_metric_statement(window: MonitorWindow, server_now: datetime) -> Any:
    """Aggregate the monitor list in MySQL instead of returning every task row."""
    activity_at = _activity_expression()
    activity_in_window = and_(activity_at >= window.from_at, activity_at < window.to_at)
    started_in_window = and_(
        RegistrationRecord.started_at >= window.from_at,
        RegistrationRecord.started_at < window.to_at,
    )
    running_stalled = and_(
        RegistrationRecord.status == "RUNNING",
        RegistrationRecord.lease_expires_at < window.to_at,
        RegistrationRecord.lease_expires_at < server_now,
    )
    validating_stalled = and_(
        RegistrationRecord.status == "VALIDATING",
        RegistrationRecord.validation_lease_until.is_not(None),
        RegistrationRecord.validation_lease_until < window.to_at,
        RegistrationRecord.validation_lease_until < server_now,
    )
    finished_at = func.coalesce(
        RegistrationRecord.validation_finished_at,
        RegistrationRecord.updated_at,
    )
    duration_seconds = (
        func.timestampdiff(
            literal_column("MICROSECOND"),
            RegistrationRecord.started_at,
            finished_at,
        )
        / 1_000_000.0
    )

    def count_when(condition: Any) -> Any:
        return func.sum(case((condition, 1), else_=0))

    return (
        select(
            RegistrationRecord.client_id,
            func.max(activity_at).label("last_activity_at"),
            func.max(case((activity_in_window, activity_at), else_=None)).label(
                "window_last_activity_at"
            ),
            count_when(started_in_window).label("jobs"),
            count_when(and_(started_in_window, RegistrationRecord.status == "SUCCEEDED")).label(
                "succeeded"
            ),
            count_when(
                and_(started_in_window, RegistrationRecord.status.in_(FAILED_STATUSES))
            ).label("failed"),
            count_when(
                and_(started_in_window, RegistrationRecord.status.in_(PROCESSING_STATUSES))
            ).label("processing"),
            count_when(
                and_(started_in_window, RegistrationRecord.status.in_(RETRY_STATUSES))
            ).label("retry_wait"),
            count_when(or_(running_stalled, validating_stalled)).label("stalled"),
            func.avg(
                case(
                    (
                        and_(
                            started_in_window,
                            RegistrationRecord.status.in_(TERMINAL_STATUSES),
                        ),
                        duration_seconds,
                    ),
                    else_=None,
                )
            ).label("average_duration_seconds"),
        )
        .group_by(RegistrationRecord.client_id)
        .order_by(None)
    )


def recent_terminal_statement(window: MonitorWindow) -> Any:
    finished_at = func.coalesce(
        RegistrationRecord.validation_finished_at,
        RegistrationRecord.updated_at,
    ).label("finished_at")
    ranked = (
        select(
            RegistrationRecord.client_id.label("client_id"),
            RegistrationRecord.status.label("status"),
            finished_at,
            func.row_number()
            .over(
                partition_by=RegistrationRecord.client_id,
                order_by=finished_at.desc(),
            )
            .label("row_number"),
        )
        .where(
            RegistrationRecord.started_at >= window.from_at,
            RegistrationRecord.started_at < window.to_at,
            RegistrationRecord.status.in_(TERMINAL_STATUSES),
        )
        .subquery()
    )
    return select(ranked.c.client_id, ranked.c.status, ranked.c.finished_at).where(
        ranked.c.row_number <= 3
    )


def latest_error_statement(window: MonitorWindow) -> Any:
    activity_at = _activity_expression().label("activity_at")
    ranked = (
        select(
            RegistrationRecord.client_id.label("client_id"),
            activity_at,
            RegistrationRecord.client_error_code.label("client_error_code"),
            RegistrationRecord.client_error_message.label("client_error_message"),
            RegistrationRecord.validation_error_code.label("validation_error_code"),
            RegistrationRecord.validation_error_message.label("validation_error_message"),
            func.row_number()
            .over(
                partition_by=RegistrationRecord.client_id,
                order_by=activity_at.desc(),
            )
            .label("row_number"),
        )
        .where(
            activity_window_condition(window),
            or_(
                RegistrationRecord.client_error_code.is_not(None),
                RegistrationRecord.client_error_message.is_not(None),
                RegistrationRecord.validation_error_code.is_not(None),
                RegistrationRecord.validation_error_message.is_not(None),
            ),
        )
        .subquery()
    )
    return select(
        ranked.c.client_id,
        ranked.c.activity_at,
        ranked.c.client_error_code,
        ranked.c.client_error_message,
        ranked.c.validation_error_code,
        ranked.c.validation_error_message,
    ).where(ranked.c.row_number == 1)


def activity_window_condition(window: MonitorWindow) -> Any:
    return or_(
        and_(
            RegistrationRecord.started_at >= window.from_at,
            RegistrationRecord.started_at < window.to_at,
        ),
        and_(
            RegistrationRecord.updated_at >= window.from_at,
            RegistrationRecord.updated_at < window.to_at,
        ),
        and_(
            RegistrationRecord.last_heartbeat_at >= window.from_at,
            RegistrationRecord.last_heartbeat_at < window.to_at,
        ),
        and_(
            RegistrationRecord.reported_at >= window.from_at,
            RegistrationRecord.reported_at < window.to_at,
        ),
        and_(
            RegistrationRecord.validation_finished_at >= window.from_at,
            RegistrationRecord.validation_finished_at < window.to_at,
        ),
        and_(
            RegistrationRecord.status == "RUNNING",
            RegistrationRecord.lease_expires_at < window.to_at,
        ),
        and_(
            RegistrationRecord.status == "VALIDATING",
            RegistrationRecord.validation_lease_until < window.to_at,
        ),
    )


def client_activity_statement(
    window: MonitorWindow,
    *,
    client_id: str | None = None,
    status: str | None = None,
    search: str | None = None,
) -> Any:
    conditions: list[Any] = [activity_window_condition(window)]
    if client_id is not None:
        conditions.append(RegistrationRecord.client_id == client_id)
    normalized_status = (status or "").strip().upper()
    if normalized_status == "PROCESSING":
        conditions.append(RegistrationRecord.status.in_(PROCESSING_STATUSES))
    elif normalized_status == "VALIDATING":
        conditions.append(
            RegistrationRecord.status.in_(
                {"COOKIE_REPORTED", "VALIDATING", "VALIDATION_RETRY_WAIT"}
            )
        )
    elif normalized_status == "FAILED":
        conditions.append(RegistrationRecord.status.in_(FAILED_STATUSES))
    elif normalized_status and normalized_status != "STALLED":
        conditions.append(RegistrationRecord.status == normalized_status)
    if search and search.strip():
        needle = f"%{search.strip().lower()}%"
        conditions.append(
            or_(
                RegistrationRecord.registration_uuid.like(needle),
                RegistrationRecord.email_snapshot.like(needle),
                RegistrationRecord.parent_email_snapshot.like(needle),
            )
        )
    return (
        select(*MONITOR_COLUMNS)
        .where(*conditions)
        .order_by(RegistrationRecord.updated_at.desc(), RegistrationRecord.id.desc())
    )


def _get(row: Mapping[str, Any], key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except (KeyError, TypeError):
        return default


def row_activity_at(row: Mapping[str, Any]) -> datetime | None:
    values = [
        _get(row, "started_at"),
        _get(row, "last_heartbeat_at"),
        _get(row, "reported_at"),
        _get(row, "validation_finished_at"),
        _get(row, "updated_at"),
    ]
    values = [value for value in values if isinstance(value, datetime)]
    return max(values) if values else None


def row_is_in_window(value: datetime | None, window: MonitorWindow) -> bool:
    return value is not None and window.from_at <= value < window.to_at


def row_is_stalled(row: Mapping[str, Any], server_now: datetime) -> bool:
    status = str(_get(row, "status", ""))
    if status == "RUNNING":
        lease = _get(row, "lease_expires_at")
        return isinstance(lease, datetime) and lease < server_now
    if status == "VALIDATING":
        lease = _get(row, "validation_lease_until")
        return isinstance(lease, datetime) and lease < server_now
    return False


def _empty_metrics(client_id: str, last_activity_at: datetime | None) -> dict[str, Any]:
    return {
        "client_id": client_id,
        "last_activity_at": last_activity_at,
        "window_last_activity_at": None,
        "active": False,
        "jobs": 0,
        "succeeded": 0,
        "failed": 0,
        "processing": 0,
        "retry_wait": 0,
        "stalled": 0,
        "durations": [],
        "terminal_statuses": [],
        "latest_error": None,
    }


def aggregate_client_rows(
    rows: Iterable[Mapping[str, Any]],
    catalog: Iterable[Mapping[str, Any]],
    window: MonitorWindow,
    server_now: datetime,
) -> dict[str, dict[str, Any]]:
    metrics: dict[str, dict[str, Any]] = {}
    for row in catalog:
        client_id = str(_get(row, "client_id", ""))
        if client_id:
            metrics[client_id] = _empty_metrics(client_id, _get(row, "last_activity_at"))
    for row in rows:
        client_id = str(_get(row, "client_id", ""))
        if not client_id:
            continue
        entry = metrics.setdefault(client_id, _empty_metrics(client_id, None))
        activity_at = row_activity_at(row)
        if row_is_in_window(activity_at, window):
            entry["active"] = True
            if (
                entry["window_last_activity_at"] is None
                or activity_at > entry["window_last_activity_at"]
            ):
                entry["window_last_activity_at"] = activity_at
        started_at = _get(row, "started_at")
        if row_is_in_window(started_at, window):
            entry["jobs"] += 1
            status = str(_get(row, "status", ""))
            if status == "SUCCEEDED":
                entry["succeeded"] += 1
            elif status in FAILED_STATUSES:
                entry["failed"] += 1
            elif status in PROCESSING_STATUSES:
                entry["processing"] += 1
            if status in RETRY_STATUSES:
                entry["retry_wait"] += 1
            if status in TERMINAL_STATUSES:
                finished_at = _get(row, "validation_finished_at") or _get(row, "updated_at")
                entry["terminal_statuses"].append((finished_at, status))
                if isinstance(finished_at, datetime) and isinstance(started_at, datetime):
                    entry["durations"].append(max(0.0, (finished_at - started_at).total_seconds()))
        if row_is_stalled(row, server_now):
            entry["stalled"] += 1
        error_code = _get(row, "client_error_code") or _get(row, "validation_error_code")
        error_message = _get(row, "client_error_message") or _get(row, "validation_error_message")
        if error_code or error_message:
            previous = entry["latest_error"]
            candidate = (
                activity_at or datetime.min,
                str(error_code) if error_code else None,
                str(error_message) if error_message else None,
            )
            if previous is None or candidate[0] >= previous[0]:
                entry["latest_error"] = candidate
    return metrics


def aggregate_client_metrics(
    aggregate_rows: Iterable[Mapping[str, Any]],
    terminal_rows: Iterable[Mapping[str, Any]],
    error_rows: Iterable[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Convert compact SQL aggregates into the health-classification shape."""
    metrics: dict[str, dict[str, Any]] = {}
    for row in aggregate_rows:
        client_id = str(_get(row, "client_id", ""))
        if not client_id:
            continue
        window_last_activity_at = _get(row, "window_last_activity_at")
        metrics[client_id] = {
            "client_id": client_id,
            "last_activity_at": _get(row, "last_activity_at"),
            "window_last_activity_at": window_last_activity_at,
            "active": window_last_activity_at is not None,
            "jobs": int(_get(row, "jobs", 0) or 0),
            "succeeded": int(_get(row, "succeeded", 0) or 0),
            "failed": int(_get(row, "failed", 0) or 0),
            "processing": int(_get(row, "processing", 0) or 0),
            "retry_wait": int(_get(row, "retry_wait", 0) or 0),
            "stalled": int(_get(row, "stalled", 0) or 0),
            "average_duration_seconds": _get(row, "average_duration_seconds"),
            "terminal_statuses": [],
            "latest_error": None,
        }
    for row in terminal_rows:
        client_id = str(_get(row, "client_id", ""))
        entry = metrics.get(client_id)
        if entry is not None:
            entry["terminal_statuses"].append(
                (_get(row, "finished_at"), str(_get(row, "status", "")))
            )
    for row in error_rows:
        client_id = str(_get(row, "client_id", ""))
        entry = metrics.get(client_id)
        if entry is None:
            continue
        error_code = _get(row, "client_error_code") or _get(row, "validation_error_code")
        error_message = _get(row, "client_error_message") or _get(row, "validation_error_message")
        entry["latest_error"] = (
            _get(row, "activity_at") or datetime.min,
            str(error_code) if error_code else None,
            str(error_message) if error_message else None,
        )
    return metrics


def classify_client_health(
    metrics: Mapping[str, Any],
) -> tuple[str, list[RegistrationClientHealthReason]]:
    stalled = int(metrics.get("stalled", 0))
    failed = int(metrics.get("failed", 0))
    succeeded = int(metrics.get("succeeded", 0))
    retry_wait = int(metrics.get("retry_wait", 0))
    terminal = sorted(
        metrics.get("terminal_statuses", []), key=lambda item: item[0] or datetime.min, reverse=True
    )
    reasons: list[RegistrationClientHealthReason] = []
    if stalled:
        reasons.append(
            RegistrationClientHealthReason(
                code="STALE_LEASE",
                message=f"存在 {stalled} 个运行租约已过期的任务",
            )
        )
    if len(terminal) >= 3 and all(status in FAILED_STATUSES for _at, status in terminal[:3]):
        reasons.append(
            RegistrationClientHealthReason(
                code="THREE_CONSECUTIVE_FAILURES",
                message="最近 3 个终态任务连续失败",
            )
        )
    settled = succeeded + failed
    if settled >= 5 and failed / settled >= 0.3:
        reasons.append(
            RegistrationClientHealthReason(
                code="HIGH_FAILURE_RATE",
                message=f"失败率 {failed / settled:.0%}，达到异常阈值",
            )
        )
    if reasons:
        return "ABNORMAL", reasons
    if not metrics.get("active"):
        return "NO_ACTIVITY", []
    if failed:
        reasons.append(
            RegistrationClientHealthReason(
                code="RECENT_FAILURE", message=f"窗口内有 {failed} 个失败任务"
            )
        )
    if retry_wait:
        reasons.append(
            RegistrationClientHealthReason(
                code="VALIDATION_RETRY_WAIT", message=f"有 {retry_wait} 个任务等待重试"
            )
        )
    if reasons:
        return "ATTENTION", reasons
    return "NORMAL", []


def _display_name(client_id: str) -> str:
    return f"客户端 {client_id[-8:]}"


def client_list_item(metrics: Mapping[str, Any]) -> RegistrationClientListItem:
    health, reasons = classify_client_health(metrics)
    succeeded = int(metrics.get("succeeded", 0))
    failed = int(metrics.get("failed", 0))
    settled = succeeded + failed
    durations = [float(value) for value in metrics.get("durations", [])]
    average_duration = metrics.get("average_duration_seconds")
    if average_duration is None and durations:
        average_duration = sum(durations) / len(durations)
    latest_error = metrics.get("latest_error")
    return RegistrationClientListItem(
        client_id=str(metrics["client_id"]),
        display_name=_display_name(str(metrics["client_id"])),
        health=health,
        health_reasons=reasons,
        last_activity_at=metrics.get("window_last_activity_at") or metrics.get("last_activity_at"),
        jobs=int(metrics.get("jobs", 0)),
        succeeded=succeeded,
        failed=failed,
        processing=int(metrics.get("processing", 0)),
        retry_wait=int(metrics.get("retry_wait", 0)),
        stalled=int(metrics.get("stalled", 0)),
        success_rate=(succeeded / settled) if settled else None,
        average_duration_seconds=float(average_duration) if average_duration is not None else None,
        latest_error_code=latest_error[1] if latest_error else None,
        latest_error_message=latest_error[2] if latest_error else None,
    )


def client_summary(items: Iterable[RegistrationClientListItem]) -> RegistrationClientSummary:
    values = list(items)
    return RegistrationClientSummary(
        total_clients=len(values),
        active_clients=sum(item.health != "NO_ACTIVITY" for item in values),
        normal_clients=sum(item.health == "NORMAL" for item in values),
        attention_clients=sum(item.health == "ATTENTION" for item in values),
        abnormal_clients=sum(item.health == "ABNORMAL" for item in values),
        no_activity_clients=sum(item.health == "NO_ACTIVITY" for item in values),
        jobs=sum(item.jobs for item in values),
        succeeded=sum(item.succeeded for item in values),
        failed=sum(item.failed for item in values),
        processing=sum(item.processing for item in values),
    )


def task_view(row: Mapping[str, Any], server_now: datetime) -> ClientRegistrationTaskView:
    started_at = _get(row, "started_at")
    finished_at = _get(row, "validation_finished_at")
    duration = None
    if isinstance(started_at, datetime) and isinstance(finished_at, datetime):
        duration = max(0.0, (finished_at - started_at).total_seconds())
    return ClientRegistrationTaskView(
        registration_uuid=UUID(str(_get(row, "registration_uuid"))),
        parent_account_uuid=UUID(str(_get(row, "parent_account_uuid_snapshot"))),
        parent_email=str(_get(row, "parent_email_snapshot", "")),
        email=str(_get(row, "email_snapshot", "")),
        client_id=str(_get(row, "client_id", "")),
        status=str(_get(row, "status", "RUNNING")),
        registered_email=_get(row, "registered_email"),
        awarded_points=_get(row, "awarded_points"),
        started_at=started_at,
        lease_expires_at=_get(row, "lease_expires_at"),
        last_heartbeat_at=_get(row, "last_heartbeat_at"),
        reported_at=_get(row, "reported_at"),
        validation_finished_at=finished_at,
        validation_lease_until=_get(row, "validation_lease_until"),
        retry_after=_get(row, "retry_after"),
        duration_seconds=duration,
        stalled=row_is_stalled(row, server_now),
        client_error_code=_get(row, "client_error_code"),
        client_error_message=_get(row, "client_error_message"),
        validation_error_code=_get(row, "validation_error_code"),
        validation_error_message=_get(row, "validation_error_message"),
        is_used=bool(_get(row, "is_used", False)),
        created_at=_get(row, "created_at"),
        updated_at=_get(row, "updated_at"),
    )


def series_points(
    rows: Iterable[Mapping[str, Any]],
    window: MonitorWindow,
    bucket_seconds: int,
) -> list[RegistrationClientSeriesPoint]:
    if bucket_seconds <= 0:
        raise ValueError("bucket_seconds must be positive")
    start_epoch = window.from_at.replace(tzinfo=UTC).timestamp()
    first_epoch = floor(start_epoch / bucket_seconds) * bucket_seconds
    buckets: dict[int, dict[str, int]] = {}
    cursor = first_epoch
    while cursor < window.to_at.replace(tzinfo=UTC).timestamp():
        buckets[int(cursor)] = {"claimed": 0, "succeeded": 0, "failed": 0}
        cursor += bucket_seconds
    for row in rows:
        started_at = _get(row, "started_at")
        if not isinstance(started_at, datetime) or not row_is_in_window(started_at, window):
            continue
        epoch = floor(started_at.replace(tzinfo=UTC).timestamp() / bucket_seconds) * bucket_seconds
        bucket = buckets.get(int(epoch))
        if bucket is None:
            continue
        bucket["claimed"] += 1
        status = str(_get(row, "status", ""))
        if status == "SUCCEEDED":
            bucket["succeeded"] += 1
        elif status in FAILED_STATUSES:
            bucket["failed"] += 1
    return [
        RegistrationClientSeriesPoint(
            at=datetime.fromtimestamp(epoch, tz=UTC).replace(tzinfo=None),
            claimed=value["claimed"],
            succeeded=value["succeeded"],
            failed=value["failed"],
        )
        for epoch, value in sorted(buckets.items())
    ]
