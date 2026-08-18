from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, case, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from video_task_service.auth import require_admin_key
from video_task_service.config import Settings, get_settings
from video_task_service.db import session_dependency
from video_task_service.login_dispatch import renewal_balance_available_clause
from video_task_service.models import (
    Account,
    AccountRenewalSession,
    ProtocolRenewalEvent,
    ProtocolRenewalRuntime,
    Space,
    Task,
)
from video_task_service.schemas import (
    AccountMetrics,
    CountByStatus,
    DailyTaskMetric,
    DashboardStats,
    ModelMetric,
    ProtocolRenewalAttemptMetrics,
    ProtocolRenewalCoverageMetrics,
    ProtocolRenewalErrorMetric,
    ProtocolRenewalHealth,
    ProtocolRenewalHealthReason,
    ProtocolRenewalQueueMetrics,
    ProtocolRenewalStats,
    ProtocolRenewalTrendMetric,
    TaskMetrics,
    TaskTrendMetric,
)

router = APIRouter(
    prefix="/stats",
    tags=["statistics"],
    dependencies=[Depends(require_admin_key)],
)

ACTIVE_TASK_STATUSES = {
    "ASSIGNED",
    "RESOLVING_MEDIA",
    "SUBMITTING",
    "SUBMITTED",
    "UPSTREAM_QUEUED",
    "PROCESSING",
    "RUNNING",
}
QUEUED_TASK_STATUSES = {"QUEUED", "WAITING_ACCOUNT", "RETRY_WAIT"}
FAILED_TASK_STATUSES = {"FAILED", "SUBMIT_UNKNOWN"}
ATTENTION_ACCOUNT_STATUSES = {
    "LOW_BALANCE",
    "LOW_BALANCE_DISABLED",
    "TOKEN_EXPIRED",
    "TOKEN_EXPIRING",
    "TOKEN_INVALID",
    "MANUAL_DISABLED",
    "PENDING_VALIDATION",
}
DashboardPeriod = Literal["total", "today", "hour"]
ProtocolRenewalPeriod = Literal["hour", "six_hours", "day", "week"]


def as_int(value: Any) -> int:
    return int(value or 0)


def effective_capacity(
    rows: list[tuple[int, int, int, int]],
) -> tuple[int, int]:
    """Bound eligible account slots by the Space limits enforced by Worker."""

    maximum = 0
    available = 0
    for space_max, space_active, account_max, account_available in rows:
        maximum += min(max(space_max, 0), max(account_max, 0))
        available += min(
            max(space_max - space_active, 0),
            max(account_available, 0),
        )
    return maximum, available


def local_midnight_utc(now: datetime, timezone_offset_minutes: int) -> datetime:
    offset = timedelta(minutes=timezone_offset_minutes)
    local_now = now + offset
    local_midnight = datetime.combine(local_now.date(), datetime.min.time())
    return local_midnight - offset


def period_start_for(
    period: DashboardPeriod,
    now: datetime,
    timezone_offset_minutes: int,
) -> datetime | None:
    if period == "today":
        return local_midnight_utc(now, timezone_offset_minutes)
    if period == "hour":
        return now - timedelta(hours=1)
    return None


def trend_settings(
    period: DashboardPeriod,
    now: datetime,
    timezone_offset_minutes: int,
) -> tuple[Literal["day", "hour", "five_minutes"], int, datetime]:
    if period == "today":
        return "hour", 60 * 60, local_midnight_utc(now, timezone_offset_minutes)
    if period == "hour":
        return "five_minutes", 5 * 60, now - timedelta(hours=1)
    return (
        "day",
        24 * 60 * 60,
        local_midnight_utc(now, timezone_offset_minutes) - timedelta(days=6),
    )


def bucket_number(value: datetime, timezone_offset_minutes: int, bucket_seconds: int) -> int:
    utc_seconds = int(value.replace(tzinfo=UTC).timestamp())
    return (utc_seconds + timezone_offset_minutes * 60) // bucket_seconds


def bucket_start_utc(
    bucket: int,
    timezone_offset_minutes: int,
    bucket_seconds: int,
) -> datetime:
    utc_seconds = bucket * bucket_seconds - timezone_offset_minutes * 60
    return datetime.fromtimestamp(utc_seconds, UTC).replace(tzinfo=None)


def trend_label(bucket_start: datetime, timezone_offset_minutes: int, granularity: str) -> str:
    local_start = bucket_start + timedelta(minutes=timezone_offset_minutes)
    return local_start.strftime("%m/%d" if granularity == "day" else "%H:%M")


def task_bucket_expression(timezone_offset_minutes: int, bucket_seconds: int) -> Any:
    seconds_since_utc_epoch = func.timestampdiff(
        text("SECOND"), datetime(1970, 1, 1), Task.created_at
    )
    return func.floor(
        (seconds_since_utc_epoch + timezone_offset_minutes * 60) / bucket_seconds
    )


def protocol_renewal_period_settings(
    period: ProtocolRenewalPeriod,
    now: datetime,
) -> tuple[datetime, int, Literal["five_minutes", "hour"]]:
    if period == "hour":
        return now - timedelta(hours=1), 5 * 60, "five_minutes"
    if period == "six_hours":
        return now - timedelta(hours=6), 30 * 60, "five_minutes"
    if period == "day":
        return now - timedelta(days=1), 60 * 60, "hour"
    return now - timedelta(days=7), 6 * 60 * 60, "hour"


def protocol_renewal_bucket_expression(
    timezone_offset_minutes: int,
    bucket_seconds: int,
) -> Any:
    seconds_since_utc_epoch = func.timestampdiff(
        text("SECOND"), datetime(1970, 1, 1), ProtocolRenewalEvent.finished_at
    )
    return func.floor(
        (seconds_since_utc_epoch + timezone_offset_minutes * 60) / bucket_seconds
    )


def _protocol_renewal_due_account_filter() -> Any:
    return and_(
        Account.status.notin_(("PENDING_VALIDATION", "MANUAL_DISABLED")),
        renewal_balance_available_clause(),
    )


def protocol_renewal_health(
    *,
    settings: Settings,
    now: datetime,
    last_heartbeat_at: datetime | None,
    last_scan_at: datetime | None,
    last_completed_at: datetime | None,
    attempts_total: int,
    strict_success_rate: float | None,
    queue_total: int,
    expired_leases: int,
    oldest_due_age_seconds: int | None,
) -> ProtocolRenewalHealth:
    reasons: list[ProtocolRenewalHealthReason] = []
    if not settings.protocol_renewal_enabled:
        return ProtocolRenewalHealth(
            state="DISABLED",
            label="协议续签已关闭",
            reasons=reasons,
            enabled=False,
            last_heartbeat_at=last_heartbeat_at,
            last_scan_at=last_scan_at,
            last_completed_at=last_completed_at,
        )

    heartbeat_age = (
        (now - last_heartbeat_at).total_seconds() if last_heartbeat_at is not None else None
    )
    if heartbeat_age is None or heartbeat_age > settings.protocol_renewal_heartbeat_stale_seconds:
        reasons.append(
            ProtocolRenewalHealthReason(
                code="HEARTBEAT_STALE",
                value=heartbeat_age,
                threshold=float(settings.protocol_renewal_heartbeat_stale_seconds),
            )
        )
        return ProtocolRenewalHealth(
            state="DOWN",
            label="续签执行器失联",
            reasons=reasons,
            enabled=True,
            last_heartbeat_at=last_heartbeat_at,
            last_scan_at=last_scan_at,
            last_completed_at=last_completed_at,
        )

    if expired_leases:
        reasons.append(
            ProtocolRenewalHealthReason(code="EXPIRED_LEASES", value=float(expired_leases))
        )
    if (
        oldest_due_age_seconds is not None
        and oldest_due_age_seconds > settings.protocol_renewal_queue_lag_warn_seconds
    ):
        reasons.append(
            ProtocolRenewalHealthReason(
                code="QUEUE_LAG_HIGH",
                value=float(oldest_due_age_seconds),
                threshold=float(settings.protocol_renewal_queue_lag_warn_seconds),
            )
        )
    if (
        attempts_total >= settings.protocol_renewal_health_min_sample
        and strict_success_rate is not None
        and strict_success_rate < settings.protocol_renewal_success_rate_target * 100
    ):
        reasons.append(
            ProtocolRenewalHealthReason(
                code="SUCCESS_RATE_BELOW_TARGET",
                value=strict_success_rate,
                threshold=settings.protocol_renewal_success_rate_target * 100,
            )
        )
    if reasons:
        return ProtocolRenewalHealth(
            state="DEGRADED",
            label="协议续签需要关注",
            reasons=reasons,
            enabled=True,
            last_heartbeat_at=last_heartbeat_at,
            last_scan_at=last_scan_at,
            last_completed_at=last_completed_at,
        )
    if attempts_total == 0 and queue_total == 0:
        state: Literal["HEALTHY", "HEALTHY_IDLE"] = "HEALTHY_IDLE"
        label = "运行正常 · 当前空闲"
    else:
        state = "HEALTHY"
        label = "运行正常"
    return ProtocolRenewalHealth(
        state=state,
        label=label,
        reasons=[],
        enabled=True,
        last_heartbeat_at=last_heartbeat_at,
        last_scan_at=last_scan_at,
        last_completed_at=last_completed_at,
    )


@router.get("/dashboard", response_model=DashboardStats)
async def dashboard_stats(
    session: Annotated[AsyncSession, Depends(session_dependency)],
    period: Annotated[DashboardPeriod, Query()] = "total",
    timezone_offset_minutes: Annotated[int, Query(ge=-720, le=840)] = 0,
) -> DashboardStats:
    now = datetime.now(UTC).replace(tzinfo=None)
    settings = get_settings()
    token_guard_time = now + timedelta(seconds=settings.token_guard_seconds)
    expires_before = now + timedelta(hours=24)
    start_date = now.date() - timedelta(days=6)
    start_datetime = datetime.combine(start_date, datetime.min.time())
    period_started_at = period_start_for(period, now, timezone_offset_minutes)
    task_conditions = [Task.created_at >= period_started_at] if period_started_at else []

    account_row = (
        await session.execute(
            select(
                func.count(Account.id),
                func.sum(case((Account.status == "ACTIVE", 1), else_=0)),
                func.sum(case((Account.status.in_(ATTENTION_ACCOUNT_STATUSES), 1), else_=0)),
                func.sum(case((Account.balance_credits < 100, 1), else_=0)),
                func.sum(
                    case(
                        (
                            Account.token_expires_at.is_not(None)
                            & (Account.token_expires_at <= expires_before),
                            1,
                        ),
                        else_=0,
                    )
                ),
                func.sum(Account.balance_credits),
                func.sum(Account.balance_credits - Account.reserved_credits),
                func.sum(Account.reserved_credits),
                func.sum(Account.active_tasks),
                func.sum(Account.max_concurrency),
                func.sum(
                    case(
                        (Account.status == "ACTIVE", Account.balance_credits),
                        else_=0,
                    )
                ),
            )
        )
    ).one()

    account_capacity_rows = (
        await session.execute(
            select(
                Space.max_concurrency,
                Space.active_tasks,
                func.sum(Account.max_concurrency),
                func.sum(func.greatest(Account.max_concurrency - Account.active_tasks, 0)),
            )
            .join(Account, Account.space_id == Space.id)
            .where(
                Space.status == "ACTIVE",
                Account.status == "ACTIVE",
                Account.video_token_ciphertext.is_not(None),
                Account.token_expires_at.is_not(None),
                Account.token_expires_at > token_guard_time,
                Account.balance_credits > Account.reserved_credits,
            )
            .group_by(Space.id, Space.max_concurrency, Space.active_tasks)
        )
    ).all()
    effective_max_concurrency, effective_available_concurrency = effective_capacity(
        [
            (
                as_int(space_max),
                as_int(space_active),
                as_int(account_max),
                as_int(account_available),
            )
            for space_max, space_active, account_max, account_available in account_capacity_rows
        ]
    )

    task_status_rows = (
        await session.execute(
            select(Task.status, func.count(Task.id))
            .where(*task_conditions)
            .group_by(Task.status)
            .order_by(Task.status)
        )
    ).all()
    task_counts = {str(status): as_int(count) for status, count in task_status_rows}
    total_tasks = sum(task_counts.values())
    completed_tasks = task_counts.get("COMPLETED", 0)
    failed_tasks = sum(task_counts.get(status, 0) for status in FAILED_TASK_STATUSES)
    terminal_tasks = completed_tasks + failed_tasks
    consumed_credits = as_int(
        await session.scalar(
            select(func.sum(func.coalesce(Task.actual_credit_cost, 0))).where(*task_conditions)
        )
    )

    duration_rows = (
        await session.execute(
            select(Task.created_at, Task.finished_at).where(
                Task.finished_at.is_not(None), *task_conditions
            )
        )
    ).all()
    durations = [
        (finished_at - created_at).total_seconds()
        for created_at, finished_at in duration_rows
        if finished_at is not None
    ]
    average_duration = round(sum(durations) / len(durations), 2) if durations else None

    daily_rows = (
        await session.execute(
            select(
                func.date(Task.created_at).label("day"),
                func.count(Task.id),
                func.sum(case((Task.status == "COMPLETED", 1), else_=0)),
                func.sum(case((Task.status.in_(FAILED_TASK_STATUSES), 1), else_=0)),
                func.sum(func.coalesce(Task.actual_credit_cost, 0)),
            )
            .where(Task.created_at >= start_datetime)
            .group_by(func.date(Task.created_at))
            .order_by(func.date(Task.created_at))
        )
    ).all()
    daily_by_date = {
        str(day): DailyTaskMetric(
            date=str(day),
            total=as_int(total),
            completed=as_int(completed),
            failed=as_int(failed),
            credits=as_int(credits),
        )
        for day, total, completed, failed, credits in daily_rows
    }
    daily_tasks = []
    for offset in range(7):
        day = start_date + timedelta(days=offset)
        day_key = day.isoformat()
        daily_tasks.append(
            daily_by_date.get(
                day_key,
                DailyTaskMetric(date=day_key, total=0, completed=0, failed=0, credits=0),
            )
        )

    trend_granularity, bucket_seconds, trend_start = trend_settings(
        period, now, timezone_offset_minutes
    )
    bucket_expression = task_bucket_expression(timezone_offset_minutes, bucket_seconds)
    trend_rows = (
        await session.execute(
            select(
                bucket_expression.label("bucket_number"),
                func.count(Task.id),
                func.sum(case((Task.status == "COMPLETED", 1), else_=0)),
                func.sum(case((Task.status.in_(FAILED_TASK_STATUSES), 1), else_=0)),
                func.sum(func.coalesce(Task.actual_credit_cost, 0)),
            )
            .where(Task.created_at >= trend_start, *task_conditions)
            .group_by(bucket_expression)
            .order_by(bucket_expression)
        )
    ).all()
    trend_by_bucket = {
        as_int(bucket): (as_int(total), as_int(completed), as_int(failed), as_int(credits))
        for bucket, total, completed, failed, credits in trend_rows
    }
    first_bucket = bucket_number(trend_start, timezone_offset_minutes, bucket_seconds)
    last_bucket = bucket_number(now, timezone_offset_minutes, bucket_seconds)
    task_trend = []
    for bucket in range(first_bucket, last_bucket + 1):
        bucket_start = bucket_start_utc(bucket, timezone_offset_minutes, bucket_seconds)
        total, completed, failed, credits = trend_by_bucket.get(bucket, (0, 0, 0, 0))
        task_trend.append(
            TaskTrendMetric(
                bucket_start=bucket_start,
                label=trend_label(bucket_start, timezone_offset_minutes, trend_granularity),
                total=total,
                completed=completed,
                failed=failed,
                credits=credits,
            )
        )

    model_rows = (
        await session.execute(
            select(
                Task.model,
                func.count(Task.id).label("total"),
                func.sum(case((Task.status == "COMPLETED", 1), else_=0)),
                func.sum(func.coalesce(Task.actual_credit_cost, 0)),
            )
            .where(*task_conditions)
            .group_by(Task.model)
            .order_by(func.count(Task.id).desc(), Task.model.asc())
            .limit(8)
        )
    ).all()
    account_status_rows = (
        await session.execute(
            select(Account.status, func.count(Account.id))
            .group_by(Account.status)
            .order_by(Account.status)
        )
    ).all()

    return DashboardStats(
        generated_at=now,
        period=period,
        period_started_at=period_started_at,
        timezone_offset_minutes=timezone_offset_minutes,
        trend_granularity=trend_granularity,
        accounts=AccountMetrics(
            total=as_int(account_row[0]),
            active=as_int(account_row[1]),
            attention=as_int(account_row[2]),
            low_balance=as_int(account_row[3]),
            expiring_24h=as_int(account_row[4]),
            balance_credits=as_int(account_row[5]),
            available_credits=as_int(account_row[6]),
            reserved_credits=as_int(account_row[7]),
            active_tasks=as_int(account_row[8]),
            max_concurrency=as_int(account_row[9]),
            effective_max_concurrency=effective_max_concurrency,
            effective_available_concurrency=effective_available_concurrency,
            active_balance_credits=as_int(account_row[10]),
            active_credit_target=settings.login_active_credit_target,
        ),
        tasks=TaskMetrics(
            total=total_tasks,
            queued=sum(task_counts.get(status, 0) for status in QUEUED_TASK_STATUSES),
            running=sum(task_counts.get(status, 0) for status in ACTIVE_TASK_STATUSES),
            completed=completed_tasks,
            failed=failed_tasks,
            canceled=task_counts.get("CANCELED", 0),
            success_rate=round((completed_tasks / terminal_tasks * 100), 2)
            if terminal_tasks
            else 0.0,
            consumed_credits=consumed_credits,
            average_duration_seconds=average_duration,
        ),
        account_statuses=[
            CountByStatus(status=str(status), count=as_int(count))
            for status, count in account_status_rows
        ],
        task_statuses=[
            CountByStatus(status=str(status), count=as_int(count))
            for status, count in task_status_rows
        ],
        daily_tasks=daily_tasks,
        task_trend=task_trend,
        models=[
            ModelMetric(
                model=str(model),
                total=as_int(total),
                completed=as_int(completed),
                credits=as_int(credits),
            )
            for model, total, completed, credits in model_rows
        ],
    )


@router.get("/protocol-renewals", response_model=ProtocolRenewalStats)
async def protocol_renewal_stats(
    session: Annotated[AsyncSession, Depends(session_dependency)],
    period: Annotated[ProtocolRenewalPeriod, Query()] = "hour",
    timezone_offset_minutes: Annotated[int, Query(ge=-720, le=840)] = 0,
) -> ProtocolRenewalStats:
    settings = get_settings()
    now = datetime.now(UTC).replace(tzinfo=None)
    period_started_at, bucket_seconds, granularity = protocol_renewal_period_settings(
        period, now
    )
    strict_success = and_(
        ProtocolRenewalEvent.outcome == "SUCCEEDED",
        ProtocolRenewalEvent.applied.is_(True),
        ProtocolRenewalEvent.renewed_token_expires_at.is_not(None),
        ProtocolRenewalEvent.previous_token_expires_at.is_not(None),
        ProtocolRenewalEvent.renewed_token_expires_at
        > ProtocolRenewalEvent.previous_token_expires_at,
    )
    renewal_attempt = ProtocolRenewalEvent.outcome.in_(
        ("SUCCEEDED", "FAILED", "STALE", "LEASE_EXPIRED")
    )
    extension_seconds = func.timestampdiff(
        text("SECOND"),
        ProtocolRenewalEvent.previous_token_expires_at,
        ProtocolRenewalEvent.renewed_token_expires_at,
    )
    attempt_row = (
        await session.execute(
            select(
                func.count(ProtocolRenewalEvent.id),
                func.sum(case((strict_success, 1), else_=0)),
                func.sum(
                    case((ProtocolRenewalEvent.outcome == "STALE", 1), else_=0)
                ),
                func.avg(ProtocolRenewalEvent.latency_ms),
                func.avg(case((strict_success, extension_seconds), else_=None)),
                func.max(case((strict_success, ProtocolRenewalEvent.finished_at), else_=None)),
            ).where(
                ProtocolRenewalEvent.finished_at >= period_started_at,
                renewal_attempt,
            )
        )
    ).one()
    attempts_total = as_int(attempt_row[0])
    applied_success = as_int(attempt_row[1])
    failed = max(attempts_total - applied_success, 0)
    strict_success_rate = (
        round(applied_success / attempts_total * 100, 2) if attempts_total else None
    )

    queue_rows = (
        await session.execute(
            select(AccountRenewalSession.status, func.count(AccountRenewalSession.account_id))
            .group_by(AccountRenewalSession.status)
            .order_by(AccountRenewalSession.status)
        )
    ).all()
    queue_counts = {str(status): as_int(count) for status, count in queue_rows}
    expired_leases = as_int(
        await session.scalar(
            select(func.count(AccountRenewalSession.account_id)).where(
                AccountRenewalSession.status == "RUNNING",
                AccountRenewalSession.lease_until.is_not(None),
                AccountRenewalSession.lease_until <= now,
            )
        )
    )
    due_cutoff = now + timedelta(seconds=settings.login_renewal_window_seconds)
    oldest_due_expiry = await session.scalar(
        select(func.min(Account.token_expires_at))
        .join(AccountRenewalSession, AccountRenewalSession.account_id == Account.id)
        .where(
            Account.video_token_ciphertext.is_not(None),
            Account.token_expires_at.is_not(None),
            Account.token_expires_at <= due_cutoff,
            _protocol_renewal_due_account_filter(),
            AccountRenewalSession.status.in_(
                ("IDLE", "PENDING", "RUNNING", "RETRY")
            ),
        )
    )
    oldest_due_age_seconds = (
        max(int((due_cutoff - oldest_due_expiry).total_seconds()), 0)
        if oldest_due_expiry is not None
        else None
    )

    eligible_condition = and_(
        Account.status == "ACTIVE",
        Account.video_token_ciphertext.is_not(None),
    )
    eligible_accounts = as_int(
        await session.scalar(select(func.count(Account.id)).where(eligible_condition))
    )
    session_accounts = as_int(
        await session.scalar(
            select(func.count(AccountRenewalSession.account_id))
            .join(Account, Account.id == AccountRenewalSession.account_id)
            .where(
                eligible_condition,
                AccountRenewalSession.client_reported_at.is_not(None),
                AccountRenewalSession.client_reported_at
                > now
                - timedelta(
                    seconds=settings.protocol_renewal_client_session_max_age_seconds
                ),
            )
        )
    )

    runtime_row = (
        await session.execute(
            select(
                func.max(ProtocolRenewalRuntime.last_heartbeat_at),
                func.max(ProtocolRenewalRuntime.last_scan_at),
                func.max(ProtocolRenewalRuntime.last_completed_at),
            ).where(ProtocolRenewalRuntime.enabled.is_(True))
        )
    ).one()
    queue_total = sum(
        queue_counts.get(status, 0) for status in ("PENDING", "RUNNING", "RETRY", "FALLBACK")
    )
    health = protocol_renewal_health(
        settings=settings,
        now=now,
        last_heartbeat_at=runtime_row[0],
        last_scan_at=runtime_row[1],
        last_completed_at=runtime_row[2],
        attempts_total=attempts_total,
        strict_success_rate=strict_success_rate,
        queue_total=queue_total,
        expired_leases=expired_leases,
        oldest_due_age_seconds=oldest_due_age_seconds,
    )

    bucket_expression = protocol_renewal_bucket_expression(
        timezone_offset_minutes, bucket_seconds
    )
    trend_rows = (
        await session.execute(
            select(
                bucket_expression.label("bucket_number"),
                func.count(ProtocolRenewalEvent.id),
                func.sum(case((strict_success, 1), else_=0)),
            )
            .where(
                ProtocolRenewalEvent.finished_at >= period_started_at,
                renewal_attempt,
            )
            .group_by(bucket_expression)
            .order_by(bucket_expression)
        )
    ).all()
    trend_by_bucket = {
        as_int(bucket): (as_int(total), as_int(success))
        for bucket, total, success in trend_rows
    }
    first_bucket = bucket_number(
        period_started_at, timezone_offset_minutes, bucket_seconds
    )
    last_bucket = bucket_number(now, timezone_offset_minutes, bucket_seconds)
    trend: list[ProtocolRenewalTrendMetric] = []
    for bucket in range(first_bucket, last_bucket + 1):
        bucket_start = bucket_start_utc(bucket, timezone_offset_minutes, bucket_seconds)
        total, success = trend_by_bucket.get(bucket, (0, 0))
        trend.append(
            ProtocolRenewalTrendMetric(
                bucket_start=bucket_start,
                label=trend_label(bucket_start, timezone_offset_minutes, granularity),
                total=total,
                applied_success=success,
                failed=max(total - success, 0),
                strict_success_rate=round(success / total * 100, 2) if total else None,
            )
        )

    error_rows = (
        await session.execute(
            select(ProtocolRenewalEvent.error_code, func.count(ProtocolRenewalEvent.id))
            .where(
                ProtocolRenewalEvent.finished_at >= period_started_at,
                renewal_attempt,
                ProtocolRenewalEvent.error_code.is_not(None),
            )
            .group_by(ProtocolRenewalEvent.error_code)
            .order_by(func.count(ProtocolRenewalEvent.id).desc())
            .limit(8)
        )
    ).all()
    return ProtocolRenewalStats(
        generated_at=now,
        period=period,
        period_started_at=period_started_at,
        timezone_offset_minutes=timezone_offset_minutes,
        target_success_rate=settings.protocol_renewal_success_rate_target * 100,
        health=health,
        attempts=ProtocolRenewalAttemptMetrics(
            total=attempts_total,
            applied_success=applied_success,
            failed=failed,
            stale=as_int(attempt_row[2]),
            strict_success_rate=strict_success_rate,
            average_latency_ms=round(float(attempt_row[3]), 2)
            if attempt_row[3] is not None
            else None,
            average_extension_seconds=round(float(attempt_row[4]), 2)
            if attempt_row[4] is not None
            else None,
            last_success_at=attempt_row[5],
        ),
        queue=ProtocolRenewalQueueMetrics(
            pending=queue_counts.get("PENDING", 0),
            running=queue_counts.get("RUNNING", 0),
            retry=queue_counts.get("RETRY", 0),
            fallback=queue_counts.get("FALLBACK", 0),
            expired_leases=expired_leases,
            oldest_due_age_seconds=oldest_due_age_seconds,
        ),
        coverage=ProtocolRenewalCoverageMetrics(
            session_accounts=session_accounts,
            eligible_accounts=eligible_accounts,
            ratio=round(session_accounts / eligible_accounts * 100, 2)
            if eligible_accounts
            else 0.0,
        ),
        trend=trend,
        errors=[
            ProtocolRenewalErrorMetric(error_code=str(code), count=as_int(count))
            for code, count in error_rows
            if code is not None
        ],
    )
