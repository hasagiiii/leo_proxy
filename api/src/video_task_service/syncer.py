from __future__ import annotations

import asyncio
import logging
import signal
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, case, delete, exists, func, or_, select, update

from video_task_service.config import Settings, get_settings
from video_task_service.cookie_importer import cookie_import_loop
from video_task_service.crypto import decrypt_secret, encrypt_secret
from video_task_service.db import dispose_engine, session_factory
from video_task_service.logging_config import configure_logging
from video_task_service.login_dispatch import (
    renewal_balance_available_clause,
    renewal_balance_is_available,
    retry_delay_seconds,
)
from video_task_service.models import (
    Account,
    AccountCreditLedger,
    AccountLoginJob,
    AccountRenewalSession,
    ProtocolRenewalEvent,
    ProtocolRenewalRuntime,
    Space,
    Task,
    TaskEvent,
    TaskQueue,
)
from video_task_service.pricing import PRICING_RULE_VERSION
from video_task_service.protocol_renewal import (
    ProtocolRenewalError,
    ProtocolRenewalResult,
    decode_renewal_session,
    encode_renewal_session,
    renew_protocol_session,
    reset_renewal_state,
)
from video_task_service.upstream import (
    PollResult,
    Upstream,
    UpstreamError,
    create_upstream,
    is_account_suspension_error,
)

logger = logging.getLogger(__name__)

PAUSED_SYNC_ACCOUNT_STATUSES = ("TOKEN_EXPIRED", "PENDING_VALIDATION")
TOKEN_EXPIRY_GUARD_STATUSES = ("ACTIVE", "TOKEN_EXPIRING")
PROTOCOL_RENEWAL_PENDING_STATUSES = ("IDLE", "PENDING", "RETRY", "RUNNING")
TASK_RUNNING_TIMEOUT_CODE = "TASK_RUNNING_TIMEOUT"
PROVIDER_RESULT_RETRY_LIMITS = {
    "PROVIDER_MODERATION_ERROR": 1,
    "PROVIDER_INVALID_REQUEST": 1,
}
PROVIDER_RESULT_RETRY_DELAY_SECONDS = 2


@dataclass(slots=True)
class SyncItem:
    task_id: int
    task_uuid: str
    account_id: int
    account_uuid: str
    encrypted_token: bytes
    generation_id: str
    submitted_at: datetime
    estimated_credit_cost: int


@dataclass(slots=True)
class ProtocolRenewalItem:
    account_id: int
    account_uuid: str
    login_name: str
    encrypted_token: bytes
    account_version: int
    renewal_session: dict[str, Any]
    attempt_number: int = 1
    started_at: datetime = field(
        default_factory=lambda: datetime.now(UTC).replace(tzinfo=None)
    )
    previous_token_expires_at: datetime | None = None


def protocol_renewal_event(
    *,
    account_id: int | None,
    account_uuid: str,
    attempt_number: int,
    outcome: str,
    applied: bool,
    retryable: bool,
    next_state: str,
    error_code: str | None,
    started_at: datetime,
    finished_at: datetime,
    previous_token_expires_at: datetime | None,
    renewed_token_expires_at: datetime | None = None,
) -> ProtocolRenewalEvent:
    latency_ms = max(int((finished_at - started_at).total_seconds() * 1000), 0)
    return ProtocolRenewalEvent(
        event_uuid=str(uuid4()),
        account_id=account_id,
        account_uuid_snapshot=account_uuid,
        attempt_number=attempt_number,
        outcome=outcome,
        applied=applied,
        retryable=retryable,
        next_state=next_state,
        error_code=error_code,
        started_at=started_at,
        finished_at=finished_at,
        latency_ms=latency_ms,
        previous_token_expires_at=previous_token_expires_at,
        renewed_token_expires_at=renewed_token_expires_at,
    )


async def touch_protocol_renewal_runtime(
    session: Any,
    syncer_id: str,
    settings: Settings,
    now: datetime,
    *,
    heartbeat: bool = False,
    scanned: bool = False,
    claimed: bool = False,
    completed: bool = False,
    loop_error_code: str | None = None,
) -> None:
    runtime = await session.get(ProtocolRenewalRuntime, syncer_id, with_for_update=True)
    if runtime is None:
        runtime = ProtocolRenewalRuntime(
            instance_id=syncer_id,
            enabled=settings.protocol_renewal_enabled,
            app_version=settings.service_version,
            last_heartbeat_at=now,
        )
        session.add(runtime)
    runtime.enabled = settings.protocol_renewal_enabled
    runtime.app_version = settings.service_version
    if heartbeat:
        runtime.last_heartbeat_at = now
    if scanned:
        runtime.last_scan_at = now
    if claimed:
        runtime.last_claimed_at = now
    if completed:
        runtime.last_completed_at = now
    runtime.last_loop_error_code = loop_error_code


async def heartbeat_protocol_renewal_runtime(
    syncer_id: str,
    settings: Settings,
    *,
    loop_error_code: str | None = None,
) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    async with session_factory() as session, session.begin():
        await touch_protocol_renewal_runtime(
            session,
            syncer_id,
            settings,
            now,
            heartbeat=True,
            loop_error_code=loop_error_code,
        )


async def cleanup_protocol_renewal_events(settings: Settings) -> int:
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(
        days=settings.protocol_renewal_event_retention_days
    )
    async with session_factory() as session, session.begin():
        result = await session.execute(
            delete(ProtocolRenewalEvent).where(ProtocolRenewalEvent.finished_at < cutoff)
        )
        return int(result.rowcount or 0)


async def claim_sync_batch(syncer_id: str, settings: Settings) -> list[int]:
    now = datetime.now(UTC).replace(tzinfo=None)
    lease_until = now + timedelta(seconds=settings.sync_lease_seconds)
    async with session_factory() as session, session.begin():
        tasks = list(
            await session.scalars(
                select(Task)
                .join(Account, Account.id == Task.account_id)
                .where(
                    Task.status.in_(["UPSTREAM_QUEUED", "RUNNING"]),
                    Task.next_sync_at.is_not(None),
                    Task.next_sync_at <= now,
                    or_(Task.sync_lease_until.is_(None), Task.sync_lease_until < now),
                    Account.video_token_ciphertext.is_not(None),
                    Account.token_expires_at.is_not(None),
                    Account.token_expires_at > now,
                    Account.status.notin_(PAUSED_SYNC_ACCOUNT_STATUSES),
                    or_(
                        Account.last_error_code.is_(None),
                        Account.last_error_code != "UPSTREAM_UNAUTHORIZED",
                    ),
                )
                .order_by(Task.next_sync_at.asc(), Task.id.asc())
                .limit(settings.sync_batch_size)
                .with_for_update(skip_locked=True)
            )
        )
        for task in tasks:
            task.sync_lease_owner = syncer_id
            task.sync_lease_until = lease_until
        return [task.id for task in tasks]


async def load_sync_item(task_id: int, syncer_id: str) -> SyncItem | None:
    async with session_factory() as session:
        row = (
            await session.execute(
                select(Task, Account)
                .join(Account, Account.id == Task.account_id)
                .where(
                    Task.id == task_id,
                    Task.sync_lease_owner == syncer_id,
                    Task.status.in_(["UPSTREAM_QUEUED", "RUNNING"]),
                )
            )
        ).one_or_none()
        if row is None:
            return None
        task, account = row
        if (
            account.video_token_ciphertext is None
            or task.upstream_task_id is None
            or task.upstream_submitted_at is None
        ):
            return None
        return SyncItem(
            task_id=task.id,
            task_uuid=task.task_uuid,
            account_id=account.id,
            account_uuid=account.account_uuid,
            encrypted_token=bytes(account.video_token_ciphertext),
            generation_id=task.upstream_task_id,
            submitted_at=task.upstream_submitted_at,
            # Submission can replace the reservation with Leonardo's
            # apiCreditCost. Carry the current reserved value into polling so
            # completion settles the same amount when the provider omits a
            # second cost field.
            estimated_credit_cost=(
                task.reserved_credit_cost or task.estimated_credit_cost
            ),
        )


def sync_event(
    task: Task,
    syncer_id: str,
    previous: str,
    current: str,
    event_type: str,
    reason: str | None = None,
) -> TaskEvent:
    return TaskEvent(
        event_uuid=str(uuid4()),
        task_id=task.id,
        event_type=event_type,
        from_status=previous,
        to_status=current,
        actor_type="SYNCER",
        actor_id=syncer_id,
        reason=reason,
    )


async def apply_poll_result(
    task_id: int,
    result: PollResult,
    syncer_id: str,
    settings: Settings,
) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    async with session_factory() as session, session.begin():
        task = await session.get(Task, task_id, with_for_update=True)
        if (
            task is None
            or task.sync_lease_owner != syncer_id
            or task.status not in {"UPSTREAM_QUEUED", "RUNNING"}
        ):
            return
        previous = task.status
        task.sync_attempts += 1
        task.sync_lease_owner = None
        task.sync_lease_until = None
        task.version += 1
        normalized = result.status.upper()

        if normalized in {"PENDING", "QUEUED", "RUNNING", "PROCESSING"}:
            task.status = "RUNNING" if normalized in {"RUNNING", "PROCESSING"} else previous
            if task.status == "RUNNING" and task.upstream_started_at is None:
                task.upstream_started_at = now
            task.next_sync_at = now + timedelta(seconds=settings.sync_interval_seconds)
            task.error_code = None
            task.error_message = None
            if task.status != previous:
                session.add(
                    sync_event(
                        task,
                        syncer_id,
                        previous,
                        task.status,
                        "UPSTREAM_RUNNING",
                    )
                )
            return

        account = (
            await session.get(Account, task.account_id, with_for_update=True)
            if task.account_id is not None
            else None
        )
        space = (
            await session.get(Space, task.space_id, with_for_update=True)
            if task.space_id is not None
            else None
        )
        if account is not None:
            account.active_tasks = max(account.active_tasks - 1, 0)
            reserved_before = account.reserved_credits
            account.reserved_credits = max(account.reserved_credits - task.reserved_credit_cost, 0)
            account.version += 1
        else:
            reserved_before = 0
        if space is not None:
            space.active_tasks = max(space.active_tasks - 1, 0)

        if normalized in {"COMPLETE", "COMPLETED", "SUCCESS"}:
            actual = (
                result.actual_credit_cost
                if result.actual_credit_cost is not None
                else task.reserved_credit_cost
            )
            task.status = "COMPLETED"
            task.output_json = result.output or {}
            task.actual_credit_cost = actual
            task.finished_at = now
            task.next_sync_at = None
            task.error_code = None
            task.error_message = None
            if account is not None:
                balance_before = account.balance_credits
                # A desktop/protocol balance refresh can land after Leonardo has
                # already charged the upstream wallet but before the task reaches
                # COMPLETED locally. In that case the refreshed snapshot already
                # includes this task's cost; subtracting again would double-count
                # the charge in the account pool.
                provider_balance_already_reflected = bool(
                    account.balance_synced_at is not None
                    and task.upstream_submitted_at is not None
                    and account.balance_synced_at > task.upstream_submitted_at
                )
                if not provider_balance_already_reflected:
                    account.balance_credits = max(account.balance_credits - actual, 0)
                account.completed_tasks += 1
                if (
                    account.balance_credits < settings.low_balance_threshold
                    and account.status != "MANUAL_DISABLED"
                ):
                    account.status = "LOW_BALANCE_DISABLED"
                    account.disabled_reason = "balance_below_threshold"
                session.add(
                    AccountCreditLedger(
                        ledger_uuid=str(uuid4()),
                        account_id=account.id,
                        task_id=task.id,
                        entry_type="SETTLE",
                        balance_before=balance_before,
                        balance_after=account.balance_credits,
                        reserved_before=reserved_before,
                        reserved_after=account.reserved_credits,
                        credit_delta=-actual,
                        metadata_json={
                            "source": "task_completion",
                            "pricing_rule_version": PRICING_RULE_VERSION,
                            "model": task.model,
                            "estimated_credit_cost": task.estimated_credit_cost,
                            "reserved_credit_cost": task.reserved_credit_cost,
                            "actual_credit_cost": actual,
                            "provider_reported": result.actual_credit_cost is not None,
                            "balance_reconciled_from_provider": (
                                provider_balance_already_reflected
                            ),
                        },
                    )
                )
            session.add(
                sync_event(
                    task,
                    syncer_id,
                    previous,
                    "COMPLETED",
                    "TASK_COMPLETED",
                )
            )
            return

        failure_code = result.error_code or "UPSTREAM_GENERATION_FAILED"
        retry_limit = PROVIDER_RESULT_RETRY_LIMITS.get(failure_code, 0)
        if retry_limit:
            retry_count = int(
                await session.scalar(
                    select(func.count(TaskEvent.id)).where(
                        TaskEvent.task_id == task.id,
                        TaskEvent.event_type == "PROVIDER_RESULT_RETRY_SCHEDULED",
                        TaskEvent.reason == failure_code,
                    )
                )
                or 0
            )
            queue = await session.get(TaskQueue, task.id, with_for_update=True)
            if retry_count < retry_limit and queue is not None:
                previous_upstream_task_id = task.upstream_task_id
                released_reservation = task.reserved_credit_cost
                if result.output:
                    task.output_json = {**(task.output_json or {}), **result.output}
                task.status = "RETRY_WAIT"
                task.upstream_task_id = None
                task.upstream_submitted_at = None
                task.upstream_started_at = None
                task.reserved_credit_cost = 0
                task.actual_credit_cost = None
                task.finished_at = None
                task.next_sync_at = None
                task.error_code = failure_code
                task.error_message = (
                    result.error_message or "upstream generation failed"
                )[:1000]
                queue.queue_status = "RETRY_WAIT"
                queue.available_at = now + timedelta(
                    seconds=PROVIDER_RESULT_RETRY_DELAY_SECONDS
                )
                queue.lease_owner = None
                queue.lease_until = None
                queue.last_error_code = failure_code
                if account is not None and released_reservation > 0:
                    session.add(
                        AccountCreditLedger(
                            ledger_uuid=str(uuid4()),
                            account_id=account.id,
                            task_id=task.id,
                            entry_type="RELEASE",
                            balance_before=account.balance_credits,
                            balance_after=account.balance_credits,
                            reserved_before=reserved_before,
                            reserved_after=account.reserved_credits,
                            credit_delta=0,
                            metadata_json={
                                "source": "provider_result_retry",
                                "model": task.model,
                                "released_credit_cost": released_reservation,
                                "error_code": failure_code,
                                "retry_number": retry_count + 1,
                                "retry_limit": retry_limit,
                            },
                        )
                    )
                retry_event = sync_event(
                    task,
                    syncer_id,
                    previous,
                    "RETRY_WAIT",
                    "PROVIDER_RESULT_RETRY_SCHEDULED",
                    failure_code,
                )
                retry_event.metadata_json = {
                    "error_code": failure_code,
                    "retry_number": retry_count + 1,
                    "retry_limit": retry_limit,
                    "retry_delay_seconds": PROVIDER_RESULT_RETRY_DELAY_SECONDS,
                    "previous_upstream_task_id": previous_upstream_task_id,
                }
                session.add(retry_event)
                return

        task.status = "FAILED"
        if result.output:
            task.output_json = {**(task.output_json or {}), **result.output}
        task.error_code = failure_code
        task.error_message = (result.error_message or "upstream generation failed")[:1000]
        task.finished_at = now
        task.next_sync_at = None
        task.actual_credit_cost = result.actual_credit_cost or 0
        if account is not None:
            account.failed_tasks += 1
            session.add(
                AccountCreditLedger(
                    ledger_uuid=str(uuid4()),
                    account_id=account.id,
                    task_id=task.id,
                    entry_type="RELEASE",
                    balance_before=account.balance_credits,
                    balance_after=account.balance_credits,
                    reserved_before=reserved_before,
                    reserved_after=account.reserved_credits,
                    credit_delta=0,
                    metadata_json={
                        "source": "task_failure",
                        "model": task.model,
                        "estimated_credit_cost": task.estimated_credit_cost,
                        "reserved_credit_cost": task.reserved_credit_cost,
                        "actual_credit_cost": task.actual_credit_cost,
                        "error_code": task.error_code,
                    },
                )
            )
        session.add(
            sync_event(
                task,
                syncer_id,
                previous,
                "FAILED",
                "TASK_FAILED",
                task.error_code,
            )
        )


async def apply_poll_error(
    task_id: int,
    error: UpstreamError,
    syncer_id: str,
    settings: Settings,
) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    account_suspended = is_account_suspension_error(error.code, str(error))
    async with session_factory() as session, session.begin():
        task = await session.get(Task, task_id, with_for_update=True)
        if task is None or task.sync_lease_owner != syncer_id:
            return
        task.sync_attempts += 1
        task.sync_lease_owner = None
        task.sync_lease_until = None
        if error.code == "UPSTREAM_UNAUTHORIZED":
            # Keep the task eligible for immediate resumption. claim_sync_batch
            # pauses it while the account token is expired or being validated.
            task.next_sync_at = now
        else:
            task.next_sync_at = now + timedelta(
                seconds=min(30, settings.sync_interval_seconds * max(task.sync_attempts, 1))
            )
        task.error_code = error.code
        task.error_message = str(error)[:1000]
        task.version += 1
        if (
            error.code == "UPSTREAM_UNAUTHORIZED" or account_suspended
        ) and task.account_id is not None:
            account = await session.get(Account, task.account_id, with_for_update=True)
            if account is not None:
                account.last_error_code = error.code
                account.last_error_at = now
                if account_suspended:
                    account.status = "MANUAL_DISABLED"
                    account.disabled_reason = "upstream_account_suspended"
                elif account.status != "MANUAL_DISABLED":
                    account.status = "TOKEN_EXPIRED"
                    account.disabled_reason = "upstream_unauthorized"
                account.version += 1


async def fail_timed_out_tasks(syncer_id: str, settings: Settings) -> int:
    """Fail RUNNING tasks after the configured wall-clock runtime limit."""

    now = datetime.now(UTC).replace(tzinfo=None)
    cutoff = now - timedelta(seconds=settings.task_running_timeout_seconds)
    failed = 0
    async with session_factory() as session, session.begin():
        tasks = list(
            await session.scalars(
                select(Task)
                .where(
                    Task.status == "RUNNING",
                    func.coalesce(Task.upstream_started_at, Task.upstream_submitted_at)
                    <= cutoff,
                )
                .order_by(Task.upstream_started_at.asc(), Task.id.asc())
                .limit(settings.sync_batch_size)
                .with_for_update(skip_locked=True)
            )
        )
        for task in tasks:
            account = (
                await session.get(Account, task.account_id, with_for_update=True)
                if task.account_id is not None
                else None
            )
            space = (
                await session.get(Space, task.space_id, with_for_update=True)
                if task.space_id is not None
                else None
            )
            if account is not None:
                account.active_tasks = max(account.active_tasks - 1, 0)
                account.reserved_credits = max(
                    account.reserved_credits - task.reserved_credit_cost,
                    0,
                )
                account.failed_tasks += 1
                account.version += 1
            if space is not None:
                space.active_tasks = max(space.active_tasks - 1, 0)

            previous = task.status
            task.status = "FAILED"
            task.error_code = TASK_RUNNING_TIMEOUT_CODE
            task.error_message = (
                "task remained RUNNING longer than "
                f"{settings.task_running_timeout_seconds} seconds"
            )
            task.actual_credit_cost = 0
            task.finished_at = now
            task.next_sync_at = None
            task.sync_lease_owner = None
            task.sync_lease_until = None
            task.version += 1
            session.add(
                sync_event(
                    task,
                    syncer_id,
                    previous,
                    "FAILED",
                    "TASK_FAILED",
                    TASK_RUNNING_TIMEOUT_CODE,
                )
            )
            failed += 1
    return failed


async def sync_one(
    task_id: int,
    syncer_id: str,
    upstream: Upstream,
    settings: Settings,
) -> None:
    item = await load_sync_item(task_id, syncer_id)
    if item is None:
        await apply_poll_error(
            task_id,
            UpstreamError("SYNC_INPUT_MISSING", "task or credential is missing"),
            syncer_id,
            settings,
        )
        return
    try:
        token = decrypt_secret(
            item.encrypted_token,
            f"{item.account_uuid}:video_token",
        )
        result = await upstream.poll(
            token=token,
            generation_id=item.generation_id,
            submitted_at=item.submitted_at,
            estimated_credit_cost=item.estimated_credit_cost,
        )
    except UpstreamError as exc:
        await apply_poll_error(task_id, exc, syncer_id, settings)
        logger.warning(
            "upstream poll failed",
            extra={
                "task_uuid": item.task_uuid,
                "syncer_id": syncer_id,
                "error_code": exc.code,
            },
        )
        return
    except Exception:
        error = UpstreamError("SYNCER_INTERNAL_ERROR", "syncer poll failed")
        await apply_poll_error(task_id, error, syncer_id, settings)
        logger.exception(
            "syncer internal error",
            extra={"task_uuid": item.task_uuid, "syncer_id": syncer_id},
        )
        return
    await apply_poll_result(task_id, result, syncer_id, settings)


def _active_login_job_for_account() -> Any:
    return exists(
        select(AccountLoginJob.id).where(AccountLoginJob.active_account_id == Account.id)
    )


def protocol_renewal_session_is_confirmed(
    client_reported_at: datetime | None,
    session_refreshed_at: datetime | None,
    cutoff: datetime,
) -> bool:
    return any(
        confirmed_at is not None and confirmed_at > cutoff
        for confirmed_at in (client_reported_at, session_refreshed_at)
    )


def _confirmed_protocol_session_filter(cutoff: datetime) -> Any:
    return or_(
        AccountRenewalSession.client_reported_at > cutoff,
        AccountRenewalSession.session_refreshed_at > cutoff,
    )


def _expired_account_filter(now: datetime) -> Any:
    return and_(
        Account.status.in_(TOKEN_EXPIRY_GUARD_STATUSES),
        Account.token_expires_at.is_not(None),
        Account.token_expires_at <= now,
    )


def _protocol_renewal_account_filter() -> Any:
    return and_(
        Account.status.notin_(("PENDING_VALIDATION", "MANUAL_DISABLED")),
        renewal_balance_available_clause(),
    )


def _protocol_renewal_priority() -> Any:
    """Protect task-capable accounts while keeping low-balance renewal eligible."""

    return case((Account.status == "LOW_BALANCE_DISABLED", 1), else_=0)


def _renewable_protocol_session_filter(cutoff: datetime) -> Any:
    """Correlated guard for accounts whose stored session can still rotate a token."""

    return exists(
        select(AccountRenewalSession.account_id).where(
            AccountRenewalSession.account_id == Account.id,
            AccountRenewalSession.status.in_(PROTOCOL_RENEWAL_PENDING_STATUSES),
            or_(
                AccountRenewalSession.client_reported_at > cutoff,
                AccountRenewalSession.session_refreshed_at > cutoff,
            ),
        )
    )


async def claim_protocol_renewal_batch(syncer_id: str, settings: Settings) -> list[int]:
    if not settings.protocol_renewal_enabled:
        return []
    now = datetime.now(UTC).replace(tzinfo=None)
    renewal_cutoff = now + timedelta(seconds=settings.login_renewal_window_seconds)
    keepalive_cutoff = now - timedelta(
        seconds=settings.protocol_renewal_keepalive_interval_seconds
    )
    confirmed_session_cutoff = now - timedelta(
        seconds=settings.protocol_renewal_client_session_max_age_seconds
    )
    lease_until = now + timedelta(
        seconds=max(int(settings.protocol_renewal_timeout_seconds) + 5, 20)
    )
    claimed: list[int] = []
    async with session_factory() as session, session.begin():
        stale = (
            await session.execute(
                select(AccountRenewalSession, Account.account_uuid)
                .join(Account, Account.id == AccountRenewalSession.account_id)
                .where(
                    AccountRenewalSession.status == "RUNNING",
                    AccountRenewalSession.lease_until.is_not(None),
                    AccountRenewalSession.lease_until <= now,
                )
                .with_for_update(skip_locked=True)
            )
        ).all()
        for row, account_uuid in stale:
            is_keepalive = row.fallback_after is None
            attempt_number = row.attempt_count
            started_at = row.last_attempt_at or now
            previous_token_expires_at = row.previous_token_expires_at
            row.lease_owner = None
            row.lease_until = None
            row.last_error_code = "PROTOCOL_LEASE_EXPIRED"
            if row.attempt_count >= settings.protocol_renewal_max_attempts or (
                row.fallback_after is not None and row.fallback_after <= now
            ):
                row.status = "FALLBACK"
                row.retry_after = None
            else:
                row.status = "RETRY"
                row.retry_after = now + timedelta(
                    seconds=settings.protocol_renewal_retry_base_seconds
                    if is_keepalive
                    else 0
                )
            session.add(
                protocol_renewal_event(
                    account_id=row.account_id,
                    account_uuid=account_uuid,
                    attempt_number=attempt_number,
                    outcome=(
                        "SESSION_KEEPALIVE_LEASE_EXPIRED"
                        if is_keepalive
                        else "LEASE_EXPIRED"
                    ),
                    applied=False,
                    retryable=row.status == "RETRY",
                    next_state=row.status,
                    error_code="PROTOCOL_LEASE_EXPIRED",
                    started_at=started_at,
                    finished_at=now,
                    previous_token_expires_at=previous_token_expires_at,
                )
            )

        retry_ready = or_(
            AccountRenewalSession.retry_after.is_(None),
            AccountRenewalSession.retry_after <= now,
        )
        renewal_ready = and_(
            Account.token_expires_at <= renewal_cutoff,
            AccountRenewalSession.status.in_(("IDLE", "PENDING", "RETRY")),
            retry_ready,
        )
        keepalive_ready = and_(
            settings.protocol_renewal_keepalive_enabled,
            Account.token_expires_at > renewal_cutoff,
            or_(
                AccountRenewalSession.session_refreshed_at.is_(None),
                AccountRenewalSession.session_refreshed_at <= keepalive_cutoff,
            ),
            AccountRenewalSession.status.in_(("IDLE", "RETRY")),
            retry_ready,
        )
        candidates = list(
            await session.scalars(
                select(Account)
                .join(AccountRenewalSession, AccountRenewalSession.account_id == Account.id)
                .where(
                    Account.video_token_ciphertext.is_not(None),
                    Account.token_expires_at.is_not(None),
                    _confirmed_protocol_session_filter(confirmed_session_cutoff),
                    _protocol_renewal_account_filter(),
                    or_(renewal_ready, keepalive_ready),
                    ~_active_login_job_for_account(),
                )
                .order_by(
                    _protocol_renewal_priority(),
                    case((Account.token_expires_at <= renewal_cutoff, 0), else_=1),
                    Account.token_expires_at.asc(),
                    Account.id.asc(),
                )
                .limit(settings.protocol_renewal_batch_size)
                .with_for_update(skip_locked=True)
            )
        )
        for account in candidates:
            row = await session.get(
                AccountRenewalSession, account.id, with_for_update=True
            )
            if (
                row is None
                or account.video_token_ciphertext is None
                or account.token_expires_at is None
                or not renewal_balance_is_available(
                    account.balance_synced_at,
                    account.balance_credits,
                )
            ):
                continue
            if not protocol_renewal_session_is_confirmed(
                row.client_reported_at,
                row.session_refreshed_at,
                confirmed_session_cutoff,
            ):
                continue
            is_keepalive = account.token_expires_at > renewal_cutoff
            if (
                row.claimed_account_version is not None
                and row.claimed_account_version != account.version
            ):
                reset_renewal_state(row)
            if not is_keepalive and row.fallback_after is None:
                row.fallback_after = now + timedelta(
                    seconds=settings.protocol_renewal_grace_seconds
                )
            if not is_keepalive and row.fallback_after <= now:
                row.status = "FALLBACK"
                row.retry_after = None
                row.last_error_code = row.last_error_code or "PROTOCOL_GRACE_EXPIRED"
                continue
            row.status = "RUNNING"
            row.attempt_count += 1
            row.claimed_account_version = account.version
            row.lease_owner = syncer_id
            row.lease_until = lease_until
            row.retry_after = None
            row.last_attempt_at = now
            row.previous_token_expires_at = account.token_expires_at
            claimed.append(account.id)
        await touch_protocol_renewal_runtime(
            session,
            syncer_id,
            settings,
            now,
            scanned=True,
            claimed=bool(claimed),
        )
    return claimed


async def load_protocol_renewal_item(
    account_id: int,
    syncer_id: str,
) -> ProtocolRenewalItem | None:
    async with session_factory() as session:
        row = (
            await session.execute(
                select(Account, AccountRenewalSession)
                .join(
                    AccountRenewalSession,
                    AccountRenewalSession.account_id == Account.id,
                )
                .where(
                    Account.id == account_id,
                    AccountRenewalSession.status == "RUNNING",
                    AccountRenewalSession.lease_owner == syncer_id,
                )
            )
        ).one_or_none()
        if row is None:
            return None
        account, renewal = row
        if account.video_token_ciphertext is None:
            return None
        try:
            material = decode_renewal_session(renewal, account.account_uuid)
        except Exception as exc:
            raise ProtocolRenewalError(
                "PROTOCOL_SESSION_DECRYPT_FAILED",
                "stored renewal session could not be decrypted",
                retryable=False,
            ) from exc
        return ProtocolRenewalItem(
            account_id=account.id,
            account_uuid=account.account_uuid,
            login_name=account.login_name,
            encrypted_token=bytes(account.video_token_ciphertext),
            account_version=account.version,
            attempt_number=renewal.attempt_count,
            started_at=renewal.last_attempt_at or datetime.now(UTC).replace(tzinfo=None),
            previous_token_expires_at=renewal.previous_token_expires_at,
            renewal_session=material,
        )


async def apply_protocol_renewal_success(
    item: ProtocolRenewalItem,
    result: ProtocolRenewalResult,
    balance_credits: int | None,
    syncer_id: str,
    settings: Settings,
) -> bool:
    now = datetime.now(UTC).replace(tzinfo=None)
    guard = now + timedelta(seconds=settings.token_guard_seconds)
    encoded_session = encode_renewal_session(result.renewal_session)
    async with session_factory() as session, session.begin():
        account = await session.get(Account, item.account_id, with_for_update=True)
        renewal = await session.get(
            AccountRenewalSession, item.account_id, with_for_update=True
        )
        if (
            account is None
            or renewal is None
            or renewal.status != "RUNNING"
            or renewal.lease_owner != syncer_id
            or account.version != item.account_version
        ):
            if renewal is not None and renewal.lease_owner == syncer_id:
                attempt_number = renewal.attempt_count
                reset_renewal_state(renewal)
                renewal.last_error_code = "PROTOCOL_RESULT_STALE"
                session.add(
                    protocol_renewal_event(
                        account_id=item.account_id if account is not None else None,
                        account_uuid=item.account_uuid,
                        attempt_number=attempt_number,
                        outcome="STALE",
                        applied=False,
                        retryable=False,
                        next_state=renewal.status,
                        error_code="PROTOCOL_RESULT_STALE",
                        started_at=item.started_at,
                        finished_at=now,
                        previous_token_expires_at=item.previous_token_expires_at,
                        renewed_token_expires_at=result.token_expires_at,
                    )
                )
                await touch_protocol_renewal_runtime(
                    session, syncer_id, settings, now, completed=True
                )
            return False
        account.video_token_ciphertext = encrypt_secret(
            result.token, f"{account.account_uuid}:video_token"
        )
        account.token_expires_at = result.token_expires_at
        account.token_refreshed_at = now
        if balance_credits is not None:
            account.balance_credits = balance_credits
            account.balance_synced_at = now
        account.version += 1
        if account.status != "MANUAL_DISABLED":
            if account.balance_credits < settings.low_balance_threshold:
                account.status = "LOW_BALANCE_DISABLED"
                account.disabled_reason = "balance_below_threshold"
            elif result.token_expires_at <= guard:
                account.status = "TOKEN_EXPIRING"
                account.disabled_reason = "token_inside_guard_window"
            else:
                account.status = "ACTIVE"
                account.disabled_reason = None
        account.last_error_code = None
        account.last_error_at = None

        renewal.session_ciphertext = encrypt_secret(
            encoded_session,
            f"{account.account_uuid}:renewal_session",
        )
        renewal.status = "IDLE"
        renewal.attempt_count = 0
        renewal.claimed_account_version = None
        renewal.lease_owner = None
        renewal.lease_until = None
        renewal.fallback_after = None
        renewal.retry_after = None
        renewal.last_success_at = now
        renewal.session_refreshed_at = now
        renewal.last_error_code = None
        renewal.renewed_token_expires_at = result.token_expires_at
        session.add(
            protocol_renewal_event(
                account_id=account.id,
                account_uuid=account.account_uuid,
                attempt_number=item.attempt_number,
                outcome="SUCCEEDED",
                applied=True,
                retryable=False,
                next_state="IDLE",
                error_code=None,
                started_at=item.started_at,
                finished_at=now,
                previous_token_expires_at=item.previous_token_expires_at,
                renewed_token_expires_at=result.token_expires_at,
            )
        )
        await touch_protocol_renewal_runtime(
            session, syncer_id, settings, now, completed=True
        )
    return True


async def apply_protocol_session_keepalive(
    item: ProtocolRenewalItem,
    result: ProtocolRenewalResult,
    syncer_id: str,
    settings: Settings,
    *,
    retry_renewal: bool,
) -> bool:
    now = datetime.now(UTC).replace(tzinfo=None)
    encoded_session = encode_renewal_session(result.renewal_session)
    async with session_factory() as session, session.begin():
        account = await session.get(Account, item.account_id, with_for_update=True)
        renewal = await session.get(
            AccountRenewalSession, item.account_id, with_for_update=True
        )
        if (
            account is None
            or renewal is None
            or renewal.status != "RUNNING"
            or renewal.lease_owner != syncer_id
            or account.version != item.account_version
        ):
            if renewal is not None and renewal.lease_owner == syncer_id:
                reset_renewal_state(renewal)
                renewal.last_error_code = "PROTOCOL_RESULT_STALE"
                session.add(
                    protocol_renewal_event(
                        account_id=item.account_id if account is not None else None,
                        account_uuid=item.account_uuid,
                        attempt_number=item.attempt_number,
                        outcome="SESSION_KEEPALIVE_STALE",
                        applied=False,
                        retryable=False,
                        next_state=renewal.status,
                        error_code="PROTOCOL_RESULT_STALE",
                        started_at=item.started_at,
                        finished_at=now,
                        previous_token_expires_at=item.previous_token_expires_at,
                        renewed_token_expires_at=result.token_expires_at,
                    )
                )
                await touch_protocol_renewal_runtime(
                    session, syncer_id, settings, now, completed=True
                )
            return False

        renewal.session_ciphertext = encrypt_secret(
            encoded_session,
            f"{account.account_uuid}:renewal_session",
        )
        renewal.session_refreshed_at = now
        renewal.lease_owner = None
        renewal.lease_until = None
        renewal.claimed_account_version = account.version
        if retry_renewal:
            renewal.status = "RETRY"
            renewal.retry_after = now + timedelta(
                seconds=max(
                    30,
                    min(
                        settings.protocol_renewal_retry_base_seconds,
                        max(settings.protocol_renewal_grace_seconds // 2, 30),
                    ),
                )
            )
            renewal.last_error_code = "PROTOCOL_TOKEN_NOT_ROTATED"
        else:
            renewal.status = "IDLE"
            renewal.attempt_count = 0
            renewal.claimed_account_version = None
            renewal.fallback_after = None
            renewal.retry_after = None
            renewal.last_error_code = None
        session.add(
            protocol_renewal_event(
                account_id=account.id,
                account_uuid=account.account_uuid,
                attempt_number=item.attempt_number,
                outcome="SESSION_ALIVE",
                applied=False,
                retryable=retry_renewal,
                next_state=renewal.status,
                error_code=renewal.last_error_code,
                started_at=item.started_at,
                finished_at=now,
                previous_token_expires_at=item.previous_token_expires_at,
                renewed_token_expires_at=result.token_expires_at,
            )
        )
        await touch_protocol_renewal_runtime(
            session, syncer_id, settings, now, completed=True
        )
    return True


def protocol_retry_delay_seconds(
    account_id: int,
    attempt_count: int,
    error: ProtocolRenewalError,
    settings: Settings,
) -> int:
    exponential = settings.protocol_renewal_retry_base_seconds * (
        2 ** max(attempt_count - 1, 0)
    )
    delay = max(exponential, error.retry_after_seconds or 0)
    jitter_limit = settings.protocol_renewal_retry_jitter_seconds
    jitter = account_id % (jitter_limit + 1) if jitter_limit > 0 else 0
    return delay + jitter


async def apply_protocol_renewal_failure(
    account_id: int,
    syncer_id: str,
    error: ProtocolRenewalError,
    settings: Settings,
) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    async with session_factory() as session, session.begin():
        account = await session.get(Account, account_id, with_for_update=True)
        renewal = await session.get(
            AccountRenewalSession, account_id, with_for_update=True
        )
        if (
            account is None
            or renewal is None
            or renewal.status != "RUNNING"
            or renewal.lease_owner != syncer_id
        ):
            return
        if renewal.claimed_account_version != account.version:
            attempt_number = renewal.attempt_count
            started_at = renewal.last_attempt_at or now
            previous_token_expires_at = renewal.previous_token_expires_at
            reset_renewal_state(renewal)
            renewal.last_error_code = "PROTOCOL_RESULT_STALE"
            session.add(
                protocol_renewal_event(
                    account_id=account.id,
                    account_uuid=account.account_uuid,
                    attempt_number=attempt_number,
                    outcome="STALE",
                    applied=False,
                    retryable=False,
                    next_state=renewal.status,
                    error_code="PROTOCOL_RESULT_STALE",
                    started_at=started_at,
                    finished_at=now,
                    previous_token_expires_at=previous_token_expires_at,
                )
            )
            await touch_protocol_renewal_runtime(
                session, syncer_id, settings, now, completed=True
            )
            return
        attempt_number = renewal.attempt_count
        is_keepalive = renewal.fallback_after is None
        started_at = renewal.last_attempt_at or now
        previous_token_expires_at = renewal.previous_token_expires_at
        renewal.lease_owner = None
        renewal.lease_until = None
        renewal.last_error_code = error.code
        exhausted = renewal.attempt_count >= settings.protocol_renewal_max_attempts
        deadline_reached = (
            renewal.fallback_after is not None and renewal.fallback_after <= now
        )
        if not error.retryable or exhausted or deadline_reached:
            renewal.status = "FALLBACK"
            renewal.retry_after = None
        else:
            delay = protocol_retry_delay_seconds(
                account_id,
                renewal.attempt_count,
                error,
                settings,
            )
            retry_at = now + timedelta(seconds=delay)
            if renewal.fallback_after is not None and retry_at >= renewal.fallback_after:
                renewal.status = "FALLBACK"
                renewal.retry_after = None
            else:
                renewal.status = "RETRY"
                renewal.retry_after = retry_at
        session.add(
            protocol_renewal_event(
                account_id=account.id,
                account_uuid=account.account_uuid,
                attempt_number=attempt_number,
                outcome="SESSION_KEEPALIVE_FAILED" if is_keepalive else "FAILED",
                applied=False,
                retryable=error.retryable,
                next_state=renewal.status,
                error_code=error.code,
                started_at=started_at,
                finished_at=now,
                previous_token_expires_at=previous_token_expires_at,
            )
        )
        await touch_protocol_renewal_runtime(
            session, syncer_id, settings, now, completed=True
        )


async def renew_protocol_account(
    account_id: int,
    syncer_id: str,
    upstream: Upstream,
    settings: Settings,
) -> None:
    try:
        item = await load_protocol_renewal_item(account_id, syncer_id)
        if item is None:
            return
        stored_token = decrypt_secret(
            item.encrypted_token,
            f"{item.account_uuid}:video_token",
        )
        result = await renew_protocol_session(
            material=item.renewal_session,
            stored_token=stored_token,
            expected_email=item.login_name,
            settings=settings,
        )
        cutoff = datetime.now(UTC).replace(tzinfo=None) + timedelta(
            seconds=settings.login_renewal_window_seconds
        )
        retry_renewal = bool(
            item.previous_token_expires_at is not None
            and item.previous_token_expires_at <= cutoff
        )
        if not result.token_changed and result.token_expires_at > datetime.now(
            UTC
        ).replace(tzinfo=None):
            if retry_renewal and item.attempt_number >= settings.protocol_renewal_max_attempts:
                raise ProtocolRenewalError(
                    "PROTOCOL_TOKEN_NOT_ROTATED",
                    "protocol response kept the session live but did not rotate the token",
                    retryable=False,
                )
            applied = await apply_protocol_session_keepalive(
                item,
                result,
                syncer_id,
                settings,
                retry_renewal=retry_renewal,
            )
            logger.info(
                "protocol session keepalive completed",
                extra={
                    "account_uuid": item.account_uuid,
                    "syncer_id": syncer_id,
                    "applied": applied,
                    "retry_renewal": retry_renewal,
                    "token_expires_at": result.token_expires_at.isoformat(),
                    "get_session_status": result.get_session_status,
                },
            )
            return
        if result.token_expires_at <= cutoff:
            raise ProtocolRenewalError(
                "PROTOCOL_TOKEN_NOT_ROTATED",
                "protocol response did not extend the token beyond the renewal window",
            )
        validation = await upstream.validate_account(token=result.token)
        if not validation.valid:
            error_code = validation.error_code or "PROTOCOL_TOKEN_VALIDATION_FAILED"
            retryable = error_code in {
                "UPSTREAM_RATE_LIMITED",
                "UPSTREAM_TIMEOUT",
                "UPSTREAM_NETWORK_ERROR",
                "UPSTREAM_SERVER_ERROR",
            }
            raise ProtocolRenewalError(
                error_code,
                "renewed token did not pass upstream validation",
                retryable=retryable,
            )
        applied = await apply_protocol_renewal_success(
            item,
            result,
            validation.balance_credits,
            syncer_id,
            settings,
        )
        logger.info(
            "protocol token renewal completed",
            extra={
                "account_uuid": item.account_uuid,
                "syncer_id": syncer_id,
                "applied": applied,
                "token_expires_at": result.token_expires_at.isoformat(),
                "get_session_status": result.get_session_status,
                "cross_origin_cookie_status": result.cross_origin_cookie_status,
            },
        )
    except ProtocolRenewalError as exc:
        await apply_protocol_renewal_failure(account_id, syncer_id, exc, settings)
        logger.warning(
            "protocol token renewal failed",
            extra={
                "account_id": account_id,
                "syncer_id": syncer_id,
                "error_code": exc.code,
                "status": exc.status,
                "retry_after_seconds": exc.retry_after_seconds,
            },
        )
    except Exception:
        error = ProtocolRenewalError(
            "PROTOCOL_INTERNAL_ERROR", "protocol renewal failed internally"
        )
        await apply_protocol_renewal_failure(account_id, syncer_id, error, settings)
        logger.exception(
            "protocol token renewal crashed",
            extra={"account_id": account_id, "syncer_id": syncer_id},
        )


async def run_protocol_renewal_batch(
    syncer_id: str,
    upstream: Upstream,
    settings: Settings,
) -> int:
    account_ids = await claim_protocol_renewal_batch(syncer_id, settings)
    if not account_ids:
        return 0
    semaphore = asyncio.Semaphore(settings.protocol_renewal_batch_size)

    async def guarded(account_id: int) -> None:
        async with semaphore:
            await renew_protocol_account(account_id, syncer_id, upstream, settings)

    await asyncio.gather(*(guarded(account_id) for account_id in account_ids))
    return len(account_ids)


async def pending_account_ids(limit: int = 20) -> list[int]:
    async with session_factory() as session:
        return list(
            await session.scalars(
                select(Account.id)
                .where(
                    Account.status == "PENDING_VALIDATION",
                    Account.video_token_ciphertext.is_not(None),
                )
                .order_by(Account.updated_at.asc(), Account.id.asc())
                .limit(limit)
            )
        )


async def enforce_account_guards(settings: Settings) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    guard = now + timedelta(seconds=settings.token_guard_seconds)
    session_cutoff = now - timedelta(
        seconds=settings.protocol_renewal_client_session_max_age_seconds
    )
    renewable_session = _renewable_protocol_session_filter(session_cutoff)
    async with session_factory() as session, session.begin():
        await session.execute(
            update(Account)
            .where(
                Account.status == "TOKEN_EXPIRED",
                Account.token_expires_at.is_not(None),
                Account.token_expires_at <= now,
                renewable_session,
            )
            .values(
                status="TOKEN_EXPIRING",
                disabled_reason="token_renewal_pending",
            )
        )
        await session.execute(
            update(Account)
            .where(_expired_account_filter(now), renewable_session)
            .values(
                status="TOKEN_EXPIRING",
                disabled_reason="token_renewal_pending",
            )
        )
        await session.execute(
            update(Account)
            .where(_expired_account_filter(now), ~renewable_session)
            .values(status="TOKEN_EXPIRED", disabled_reason="token_expired")
        )
        await session.execute(
            update(Account)
            .where(
                Account.status == "ACTIVE",
                Account.balance_credits < settings.low_balance_threshold,
            )
            .values(
                status="LOW_BALANCE_DISABLED",
                disabled_reason="balance_below_threshold",
            )
        )
        await session.execute(
            update(Account)
            .where(
                Account.status == "ACTIVE",
                Account.token_expires_at.is_not(None),
                Account.token_expires_at > now,
                Account.token_expires_at <= guard,
            )
            .values(
                status="TOKEN_EXPIRING",
                disabled_reason="token_inside_guard_window",
            )
        )


async def validate_account(
    account_id: int,
    upstream: Upstream,
    settings: Settings,
) -> None:
    async with session_factory() as session:
        account = await session.get(Account, account_id)
        if (
            account is None
            or account.status != "PENDING_VALIDATION"
            or account.video_token_ciphertext is None
        ):
            return
        version = account.version
        account_uuid = account.account_uuid
        encrypted_token = bytes(account.video_token_ciphertext)
    try:
        token = decrypt_secret(encrypted_token, f"{account_uuid}:video_token")
        validation = await upstream.validate_account(token=token)
    except Exception:
        logger.exception("account validation failed", extra={"account_uuid": account_uuid})
        return

    now = datetime.now(UTC).replace(tzinfo=None)
    guard = now + timedelta(seconds=settings.token_guard_seconds)
    async with session_factory() as session, session.begin():
        account = await session.get(Account, account_id, with_for_update=True)
        if account is None or account.version != version or account.status != "PENDING_VALIDATION":
            return
        login_job = await session.scalar(
            select(AccountLoginJob)
            .where(
                AccountLoginJob.active_account_id == account.id,
                AccountLoginJob.status == "VALIDATING",
            )
            .with_for_update()
        )
        account.balance_synced_at = now
        account.version += 1
        if not validation.valid:
            account.status = "TOKEN_EXPIRED"
            account.disabled_reason = validation.error_code or "token_validation_failed"
            account.last_error_code = validation.error_code
            account.last_error_at = now
            if login_job is not None:
                delay = retry_delay_seconds(
                    login_job.attempt_no,
                    retryable=True,
                    retry_base_seconds=settings.login_job_retry_base_seconds,
                    nonretryable_retry_seconds=settings.login_job_nonretryable_retry_seconds,
                )
                login_job.status = "FAILED"
                login_job.active_account_id = None
                login_job.validation_finished_at = now
                login_job.retry_after = now + timedelta(seconds=delay)
                login_job.error_code = validation.error_code or "TOKEN_VALIDATION_FAILED"
                login_job.error_message = "reported token did not pass upstream validation"
            return
        if validation.balance_credits is not None:
            account.balance_credits = validation.balance_credits
        if account.token_expires_at is None or account.token_expires_at <= now:
            account.status = "TOKEN_EXPIRED"
            account.disabled_reason = "token_expired"
        elif account.balance_credits < settings.low_balance_threshold:
            account.status = "LOW_BALANCE_DISABLED"
            account.disabled_reason = "balance_below_threshold"
        elif account.token_expires_at <= guard:
            account.status = "TOKEN_EXPIRING"
            account.disabled_reason = "token_inside_guard_window"
        else:
            account.status = "ACTIVE"
            account.disabled_reason = None
            account.last_error_code = None
            account.last_error_at = None
        if login_job is not None:
            login_job.status = "SUCCEEDED"
            login_job.active_account_id = None
            login_job.validation_finished_at = now
            login_job.retry_after = None
            login_job.error_code = None
            login_job.error_message = None


async def syncer_loop(stop: asyncio.Event) -> None:
    settings = get_settings()
    syncer_id = f"syncer-{uuid4()}"
    upstream = create_upstream(settings)
    next_maintenance = 0.0
    next_protocol_heartbeat = 0.0
    next_protocol_event_cleanup = 0.0
    try:
        while not stop.is_set():
            loop_time = asyncio.get_running_loop().time()
            if loop_time >= next_protocol_heartbeat:
                await heartbeat_protocol_renewal_runtime(syncer_id, settings)
                next_protocol_heartbeat = (
                    loop_time + settings.protocol_renewal_heartbeat_seconds
                )
            if loop_time >= next_protocol_event_cleanup:
                deleted_events = await cleanup_protocol_renewal_events(settings)
                if deleted_events:
                    logger.info(
                        "expired protocol renewal events removed",
                        extra={"event_count": deleted_events},
                    )
                next_protocol_event_cleanup = loop_time + 24 * 60 * 60
            if loop_time >= next_maintenance:
                timed_out = await fail_timed_out_tasks(syncer_id, settings)
                if timed_out:
                    logger.warning(
                        "running tasks timed out",
                        extra={"syncer_id": syncer_id, "task_count": timed_out},
                    )
                try:
                    protocol_renewals = await run_protocol_renewal_batch(
                        syncer_id, upstream, settings
                    )
                except Exception:
                    await heartbeat_protocol_renewal_runtime(
                        syncer_id,
                        settings,
                        loop_error_code="PROTOCOL_RENEWAL_LOOP_ERROR",
                    )
                    raise
                if protocol_renewals:
                    logger.info(
                        "protocol renewal batch processed",
                        extra={
                            "syncer_id": syncer_id,
                            "account_count": protocol_renewals,
                        },
                    )
                await enforce_account_guards(settings)
                for account_id in await pending_account_ids():
                    await validate_account(account_id, upstream, settings)
                next_maintenance = loop_time + settings.account_maintenance_seconds

            task_ids = await claim_sync_batch(syncer_id, settings)
            if task_ids:
                semaphore = asyncio.Semaphore(settings.sync_batch_size)

                async def guarded(
                    task_id: int,
                    limiter: asyncio.Semaphore = semaphore,
                ) -> None:
                    async with limiter:
                        await sync_one(task_id, syncer_id, upstream, settings)

                await asyncio.gather(*(guarded(task_id) for task_id in task_ids))
                continue
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.sync_interval_seconds)
            except TimeoutError:
                pass
    finally:
        await upstream.close()


async def async_main() -> None:
    configure_logging()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    logger.info("syncer started")
    try:
        async with asyncio.TaskGroup() as group:
            group.create_task(syncer_loop(stop), name="task-sync-loop")
            group.create_task(cookie_import_loop(stop), name="cookie-import-loop")
    finally:
        await dispose_engine()
        logger.info("syncer stopped")


if __name__ == "__main__":
    asyncio.run(async_main())
