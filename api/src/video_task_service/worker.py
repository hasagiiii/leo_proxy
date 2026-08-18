from __future__ import annotations

import asyncio
import hashlib
import logging
import signal
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from video_task_service.config import Settings, get_settings
from video_task_service.crypto import decrypt_secret
from video_task_service.db import dispose_engine, session_factory
from video_task_service.gemini_omni_flash import (
    build_leonardo_gemini_omni_flash_request,
    is_gemini_omni_flash_model,
)
from video_task_service.gpt_image_2 import (
    build_leonardo_gpt_image_2_request,
    is_gpt_image_2_model,
)
from video_task_service.h3 import (
    MediaSpec,
    ResolvedMedia,
    build_leonardo_h3_request,
    media_specs,
)
from video_task_service.kling_o3 import (
    build_leonardo_kling_o3_request,
    is_kling_o3_model,
)
from video_task_service.logging_config import configure_logging
from video_task_service.models import (
    Account,
    AccountCreditLedger,
    Space,
    Task,
    TaskAttempt,
    TaskEvent,
    TaskMediaAsset,
    TaskQueue,
)
from video_task_service.nano_images import (
    build_leonardo_nano_image_request,
    is_nano_image_model,
)
from video_task_service.pricing import PRICING_RULE_VERSION, quote_credit_cost
from video_task_service.seed_audio import (
    SEED_AUDIO_SCHEMA_VERSION,
    build_leonardo_seed_audio_request,
    is_seed_audio_model,
)
from video_task_service.seedance import (
    build_leonardo_seedance_request,
    is_seedance_25_model,
    is_seedance_model,
)
from video_task_service.upstream import (
    AccountValidation,
    SubmitResult,
    Upstream,
    UpstreamError,
    create_upstream,
    is_account_suspension_error,
    validate_reference_audio_collection,
    validate_reference_video_collection,
)
from video_task_service.veo_3_1 import (
    build_leonardo_veo_3_1_request,
    is_veo_3_1_model,
)

logger = logging.getLogger(__name__)

UPSTREAM_OUTAGE_ERROR_CODES = frozenset({"UPSTREAM_PROVIDER_UNAVAILABLE"})


@dataclass(slots=True)
class Assignment:
    task_id: int
    task_uuid: str
    account_id: int
    account_uuid: str
    space_id: int
    attempt_no: int
    model: str
    task_input: dict[str, Any]
    encrypted_token: bytes
    estimated_credit_cost: int
    mode: str | None = None
    input_schema_version: str = "legacy"


@dataclass(frozen=True, slots=True)
class AccountUnavailableDiagnosis:
    code: str
    message: str
    counts: dict[str, int]


def account_unavailable_retry_delay(
    delivery_attempts: int,
    base_seconds: int,
    maximum_seconds: int,
) -> int:
    exponent = min(max(delivery_attempts - 1, 0), 8)
    return min(base_seconds * (2**exponent), maximum_seconds)


async def count_accounts(session: AsyncSession, *conditions: Any) -> int:
    value = await session.scalar(select(func.count(Account.id)).where(*conditions))
    return int(value or 0)


async def diagnose_account_unavailable(
    session: AsyncSession,
    guard_time: datetime,
    estimated: int,
) -> AccountUnavailableDiagnosis:
    active_conditions = (Account.status == "ACTIVE",)
    token_conditions = (
        *active_conditions,
        Account.video_token_ciphertext.is_not(None),
        Account.token_expires_at.is_not(None),
        Account.token_expires_at > guard_time,
    )
    slot_conditions = (*token_conditions, Account.active_tasks < Account.max_concurrency)
    credit_conditions = (
        *slot_conditions,
        Account.balance_credits - Account.reserved_credits >= estimated,
    )
    active_accounts = await count_accounts(session, *active_conditions)
    token_ready = await count_accounts(session, *token_conditions)
    account_slot_ready = await count_accounts(session, *slot_conditions)
    credit_ready = await count_accounts(session, *credit_conditions)
    active_space_ready = int(
        await session.scalar(
            select(func.count(Account.id))
            .join(Space, Space.id == Account.space_id)
            .where(*credit_conditions, Space.status == "ACTIVE")
        )
        or 0
    )
    schedulable = int(
        await session.scalar(
            select(func.count(Account.id))
            .join(Space, Space.id == Account.space_id)
            .where(
                *credit_conditions,
                Space.status == "ACTIVE",
                Space.active_tasks < Space.max_concurrency,
            )
        )
        or 0
    )
    counts = {
        "active_accounts": active_accounts,
        "token_ready": token_ready,
        "account_slot_ready": account_slot_ready,
        "credit_ready": credit_ready,
        "active_space_ready": active_space_ready,
        "schedulable": schedulable,
    }
    if active_accounts == 0:
        return AccountUnavailableDiagnosis(
            "NO_ACTIVE_ACCOUNT", "no ACTIVE account is available", counts
        )
    if token_ready == 0:
        return AccountUnavailableDiagnosis(
            "TOKEN_UNAVAILABLE", "ACTIVE accounts do not have a usable token", counts
        )
    if account_slot_ready == 0:
        return AccountUnavailableDiagnosis(
            "ACCOUNT_SATURATED", "all usable accounts are at their concurrency limit", counts
        )
    if credit_ready == 0:
        return AccountUnavailableDiagnosis(
            "INSUFFICIENT_CREDITS",
            f"no account has {estimated} spendable credits",
            counts,
        )
    if active_space_ready == 0:
        return AccountUnavailableDiagnosis(
            "SPACE_UNAVAILABLE", "eligible accounts belong to inactive spaces", counts
        )
    if schedulable == 0:
        return AccountUnavailableDiagnosis(
            "SPACE_SATURATED",
            "all eligible accounts belong to spaces at their concurrency limit",
            counts,
        )
    return AccountUnavailableDiagnosis(
        "ACCOUNT_LOCK_CONTENTION",
        "eligible account rows are currently locked by another assignment",
        counts,
    )


def refresh_task_credit_quote(task: Task) -> int:
    """Requote supported models immediately before account allocation.

    Task creation already stores a quote for typed requests. Requoting here
    also covers queued tasks created before a pricing-rule deployment and
    guarantees that account selection uses the current rule version.
    """

    quote = quote_credit_cost(task.model, task.input_json)
    if quote is not None:
        task.estimated_credit_cost = quote
    return max(task.estimated_credit_cost, 0)


def submit_retry_delay_seconds(
    error: UpstreamError,
    attempt_no: int,
    settings: Settings,
) -> int | None:
    """Return the retry delay for a pre-generation submission failure."""

    if not error.retryable:
        return None
    if error.code in UPSTREAM_OUTAGE_ERROR_CODES:
        max_attempts = settings.upstream_outage_max_submit_attempts
        base_seconds = settings.upstream_outage_retry_base_seconds
    else:
        max_attempts = settings.worker_max_submit_attempts
        base_seconds = settings.retry_base_seconds
    if attempt_no >= max_attempts:
        return None
    delay = base_seconds * (2 ** max(attempt_no - 1, 0))
    if error.retry_after_seconds is not None:
        delay = max(delay, int(error.retry_after_seconds))
    return min(delay, 300)


def add_event(
    *,
    task: Task,
    event_type: str,
    previous: str | None,
    current: str | None,
    actor_id: str,
    reason: str | None = None,
) -> TaskEvent:
    return TaskEvent(
        event_uuid=str(uuid4()),
        task_id=task.id,
        event_type=event_type,
        from_status=previous,
        to_status=current,
        actor_type="WORKER",
        actor_id=actor_id,
        reason=reason,
    )


async def claim_and_assign(worker_id: str, settings: Settings) -> Assignment | None:
    now = datetime.now(UTC).replace(tzinfo=None)
    guard_time = now + timedelta(seconds=settings.token_guard_seconds)
    lease_until = now + timedelta(seconds=settings.queue_lease_seconds)

    async with session_factory() as session, session.begin():
        queue = await session.scalar(
            select(TaskQueue)
            .where(
                TaskQueue.queue_status.in_(["READY", "RETRY_WAIT"]),
                TaskQueue.available_at <= now,
                or_(TaskQueue.lease_until.is_(None), TaskQueue.lease_until < now),
            )
            .order_by(
                TaskQueue.priority.desc(),
                TaskQueue.available_at.asc(),
                TaskQueue.task_id.asc(),
            )
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if queue is None:
            return None

        task = await session.get(Task, queue.task_id, with_for_update=True)
        if task is None or task.status not in {"QUEUED", "WAITING_ACCOUNT", "RETRY_WAIT"}:
            queue.queue_status = "DONE"
            queue.lease_owner = None
            queue.lease_until = None
            return None

        queue.queue_status = "LEASED"
        queue.lease_owner = worker_id
        queue.lease_until = lease_until
        queue.delivery_attempts += 1
        previous = task.status
        task.status = "CLAIMED"
        task.version += 1

        estimated = refresh_task_credit_quote(task)
        spendable_credits = Account.balance_credits - Account.reserved_credits
        account = await session.scalar(
            select(Account)
            .join(Space, Space.id == Account.space_id)
            .where(
                Account.status == "ACTIVE",
                Account.video_token_ciphertext.is_not(None),
                Account.token_expires_at.is_not(None),
                Account.token_expires_at > guard_time,
                Account.active_tasks < Account.max_concurrency,
                spendable_credits >= estimated,
                Space.status == "ACTIVE",
                Space.active_tasks < Space.max_concurrency,
            )
            .order_by(
                Account.active_tasks.asc(),
                Account.last_assigned_at.asc(),
                spendable_credits.desc(),
                Account.id.asc(),
            )
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if account is None:
            retry_delay = account_unavailable_retry_delay(
                queue.delivery_attempts,
                settings.retry_base_seconds,
                settings.account_unavailable_retry_max_seconds,
            )
            task.status = "WAITING_ACCOUNT"
            queue.queue_status = "RETRY_WAIT"
            queue.available_at = now + timedelta(seconds=retry_delay)
            queue.lease_owner = None
            queue.lease_until = None
            should_record = previous != "WAITING_ACCOUNT" or not queue.last_error_code
            if should_record:
                diagnosis = await diagnose_account_unavailable(
                    session,
                    guard_time,
                    estimated,
                )
                task.error_code = diagnosis.code
                task.error_message = diagnosis.message
                queue.last_error_code = diagnosis.code
                unavailable_event = add_event(
                    task=task,
                    event_type="ACCOUNT_UNAVAILABLE",
                    previous=previous,
                    current="WAITING_ACCOUNT",
                    actor_id=worker_id,
                    reason=diagnosis.message,
                )
                unavailable_event.metadata_json = {
                    "reason_code": diagnosis.code,
                    "pricing_rule_version": PRICING_RULE_VERSION,
                    "estimated_credit_cost": estimated,
                    "retry_delay_seconds": retry_delay,
                    "counts": diagnosis.counts,
                }
                session.add(unavailable_event)
            return None

        space = await session.get(Space, account.space_id, with_for_update=True)
        if space is None or space.status != "ACTIVE" or space.active_tasks >= space.max_concurrency:
            task.status = "WAITING_ACCOUNT"
            queue.queue_status = "RETRY_WAIT"
            queue.available_at = now + timedelta(seconds=settings.retry_base_seconds)
            queue.lease_owner = None
            queue.lease_until = None
            return None

        available_before = account.balance_credits - account.reserved_credits
        reserved_before = account.reserved_credits
        account.active_tasks += 1
        account.reserved_credits += estimated
        account.last_assigned_at = now
        account.version += 1
        space.active_tasks += 1
        task.account_id = account.id
        task.space_id = space.id
        typed_generation = (
            task.input_schema_version
            in {
                "h3.v1",
                "seedance.v1",
                "gemini-omni-flash.v1",
                "seedance-2.5.v1",
                "kling-o3.v1",
                "gpt-image-2.v1",
                "nano-image.v1",
                "veo-3.1.v1",
                "veo-3.1-fast.v1",
                "veo-3.1-lite.v1",
                SEED_AUDIO_SCHEMA_VERSION,
            }
            and task.mode is not None
        )
        assigned_status = "RESOLVING_MEDIA" if typed_generation else "SUBMITTING"
        task.status = assigned_status
        task.assigned_at = now
        task.submit_started_at = None if typed_generation else now
        task.reserved_credit_cost = estimated
        task.submit_attempts += 1
        task.media_resolved = 0
        task.error_code = None
        task.error_message = None
        task.version += 1
        queue.last_error_code = None
        session.add(
            TaskAttempt(
                attempt_uuid=str(uuid4()),
                task_id=task.id,
                account_id=account.id,
                attempt_no=task.submit_attempts,
                phase="RESOLVE_MEDIA" if typed_generation else "SUBMIT",
                request_json=task.input_json,
                outcome="STARTED",
                started_at=now,
            )
        )
        assigned_event = add_event(
            task=task,
            event_type="ACCOUNT_ASSIGNED",
            previous=previous,
            current=assigned_status,
            actor_id=worker_id,
        )
        assigned_event.metadata_json = {
            "pricing_rule_version": PRICING_RULE_VERSION,
            "estimated_credit_cost": estimated,
            "balance_credits": account.balance_credits,
            "reserved_before": reserved_before,
            "reserved_after": account.reserved_credits,
            "available_before": available_before,
            "available_after": account.balance_credits - account.reserved_credits,
        }
        session.add(assigned_event)
        assert account.video_token_ciphertext is not None
        return Assignment(
            task_id=task.id,
            task_uuid=task.task_uuid,
            account_id=account.id,
            account_uuid=account.account_uuid,
            space_id=space.id,
            attempt_no=task.submit_attempts,
            model=task.model,
            mode=task.mode,
            input_schema_version=task.input_schema_version,
            task_input=dict(task.input_json),
            encrypted_token=bytes(account.video_token_ciphertext),
            estimated_credit_cost=estimated,
        )


async def recover_expired_leases(worker_id: str, limit: int = 50) -> int:
    """Recover abandoned queue leases without risking an upstream double submit."""
    now = datetime.now(UTC).replace(tzinfo=None)
    recovered = 0
    async with session_factory() as session, session.begin():
        queues = list(
            await session.scalars(
                select(TaskQueue)
                .where(
                    TaskQueue.queue_status == "LEASED",
                    TaskQueue.lease_until.is_not(None),
                    TaskQueue.lease_until < now,
                )
                .order_by(TaskQueue.lease_until.asc())
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        for queue in queues:
            task = await session.get(Task, queue.task_id, with_for_update=True)
            if task is None:
                queue.queue_status = "DONE"
            elif task.status == "RESOLVING_MEDIA":
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
                    account.version += 1
                if space is not None:
                    space.active_tasks = max(space.active_tasks - 1, 0)
                previous = task.status
                task.status = "RETRY_WAIT"
                task.reserved_credit_cost = 0
                task.version += 1
                queue.queue_status = "RETRY_WAIT"
                queue.available_at = now
                session.add(
                    add_event(
                        task=task,
                        event_type="MEDIA_RESOLUTION_LEASE_RECOVERED",
                        previous=previous,
                        current="RETRY_WAIT",
                        actor_id=worker_id,
                    )
                )
            elif task.status == "SUBMITTING" and task.upstream_task_id is None:
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
                        account.reserved_credits - task.reserved_credit_cost, 0
                    )
                    account.version += 1
                if space is not None:
                    space.active_tasks = max(space.active_tasks - 1, 0)
                previous = task.status
                task.status = "FAILED"
                task.error_code = "WORKER_LEASE_EXPIRED_DURING_SUBMIT"
                task.error_message = (
                    "worker lease expired while upstream submission was in progress"
                )
                task.reserved_credit_cost = 0
                task.finished_at = now
                task.version += 1
                queue.queue_status = "DONE"
                queue.last_error_code = task.error_code
                attempt = await session.scalar(
                    select(TaskAttempt)
                    .where(
                        TaskAttempt.task_id == task.id,
                        TaskAttempt.attempt_no == task.submit_attempts,
                        TaskAttempt.outcome == "STARTED",
                    )
                    .with_for_update()
                )
                if attempt is not None:
                    attempt.outcome = "FAILED"
                    attempt.error_code = task.error_code
                    attempt.error_message = task.error_message
                    attempt.finished_at = now
                session.add(
                    add_event(
                        task=task,
                        event_type="SUBMIT_RESULT_UNKNOWN",
                        previous=previous,
                        current="FAILED",
                        actor_id=worker_id,
                        reason="queue_lease_expired",
                    )
                )
            elif task.status in {"QUEUED", "CLAIMED", "WAITING_ACCOUNT", "RETRY_WAIT"}:
                previous = task.status
                task.status = "RETRY_WAIT"
                task.version += 1
                queue.queue_status = "RETRY_WAIT"
                queue.available_at = now
                session.add(
                    add_event(
                        task=task,
                        event_type="QUEUE_LEASE_RECOVERED",
                        previous=previous,
                        current="RETRY_WAIT",
                        actor_id=worker_id,
                    )
                )
            else:
                queue.queue_status = "DONE"
            queue.lease_owner = None
            queue.lease_until = None
            recovered += 1

        submit_unknown_tasks = list(
            await session.scalars(
                select(Task)
                .where(Task.status == "SUBMIT_UNKNOWN")
                .order_by(Task.updated_at.asc(), Task.id.asc())
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        for task in submit_unknown_tasks:
            previous = task.status
            task.status = "FAILED"
            task.finished_at = now
            task.error_code = task.error_code or "WORKER_LEASE_EXPIRED_DURING_SUBMIT"
            task.error_message = task.error_message or (
                "upstream submission result remained unknown after worker lease expiry"
            )
            task.version += 1
            attempt = await session.scalar(
                select(TaskAttempt)
                .where(
                    TaskAttempt.task_id == task.id,
                    TaskAttempt.attempt_no == task.submit_attempts,
                    TaskAttempt.outcome == "STARTED",
                )
                .with_for_update()
            )
            if attempt is not None:
                attempt.outcome = "FAILED"
                attempt.error_code = task.error_code
                attempt.error_message = task.error_message
                attempt.finished_at = now
            session.add(
                add_event(
                    task=task,
                    event_type="SUBMIT_UNKNOWN_TERMINATED",
                    previous=previous,
                    current="FAILED",
                    actor_id=worker_id,
                    reason=task.error_code,
                )
            )
            recovered += 1
    return recovered


async def finish_submit_success(
    assignment: Assignment,
    result: SubmitResult,
    worker_id: str,
) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    async with session_factory() as session, session.begin():
        task = await session.get(Task, assignment.task_id, with_for_update=True)
        if task is None or task.status != "SUBMITTING":
            return
        queue = await session.get(TaskQueue, task.id, with_for_update=True)
        account = await session.get(Account, assignment.account_id, with_for_update=True)
        attempt = await session.scalar(
            select(TaskAttempt).where(
                TaskAttempt.task_id == task.id,
                TaskAttempt.attempt_no == assignment.attempt_no,
            )
        )
        previous = task.status
        reserved = (
            result.api_credit_cost
            if result.api_credit_cost is not None
            else assignment.estimated_credit_cost
        )
        if account is not None and reserved != task.reserved_credit_cost:
            delta = reserved - task.reserved_credit_cost
            account.reserved_credits = max(account.reserved_credits + delta, 0)
        task.reserved_credit_cost = reserved
        task.upstream_task_id = result.generation_id
        task.upstream_submitted_at = now
        task.status = "UPSTREAM_QUEUED"
        task.next_sync_at = now
        task.error_code = None
        task.error_message = None
        task.output_json = {"submit": result.response or {}}
        task.version += 1
        if queue is not None:
            queue.queue_status = "DONE"
            queue.lease_owner = None
            queue.lease_until = None
        if attempt is not None:
            attempt.outcome = "SUBMITTED"
            attempt.upstream_task_id = result.generation_id
            attempt.response_json = result.response
            attempt.finished_at = now
        session.add(
            add_event(
                task=task,
                event_type="UPSTREAM_SUBMITTED",
                previous=previous,
                current="UPSTREAM_QUEUED",
                actor_id=worker_id,
            )
        )


async def finish_submit_failure(
    assignment: Assignment,
    error: UpstreamError,
    worker_id: str,
    settings: Settings,
) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    async with session_factory() as session, session.begin():
        task = await session.get(Task, assignment.task_id, with_for_update=True)
        if task is None or task.status not in {"RESOLVING_MEDIA", "SUBMITTING"}:
            return
        account = await session.get(Account, assignment.account_id, with_for_update=True)
        space = await session.get(Space, assignment.space_id, with_for_update=True)
        queue = await session.get(TaskQueue, task.id, with_for_update=True)
        attempt = await session.scalar(
            select(TaskAttempt).where(
                TaskAttempt.task_id == task.id,
                TaskAttempt.attempt_no == assignment.attempt_no,
            )
        )
        previous = task.status
        retry_delay = submit_retry_delay_seconds(
            error,
            task.submit_attempts,
            settings,
        )
        retryable = retry_delay is not None
        released_reservation = task.reserved_credit_cost
        reserved_before = account.reserved_credits if account is not None else 0
        account_suspended = is_account_suspension_error(error.code, str(error))
        if account is not None:
            account.active_tasks = max(account.active_tasks - 1, 0)
            account.reserved_credits = max(
                account.reserved_credits - released_reservation,
                0,
            )
            account.last_error_code = error.code
            account.last_error_at = now
            account.version += 1
            if account_suspended:
                account.status = "MANUAL_DISABLED"
                account.disabled_reason = "upstream_account_suspended"
            elif error.code == "UPSTREAM_UNAUTHORIZED":
                account.status = "TOKEN_EXPIRED"
                account.disabled_reason = "upstream_unauthorized"
            elif error.code == "UPSTREAM_RATE_LIMITED":
                account.status = "COOLDOWN"
                account.disabled_reason = "upstream_rate_limited"
            elif error.code == "PRE_SUBMIT_LOW_BALANCE":
                account.status = "LOW_BALANCE_DISABLED"
                account.disabled_reason = "balance_below_threshold"
        if space is not None:
            space.active_tasks = max(space.active_tasks - 1, 0)

        task.error_code = error.code
        task.error_message = str(error)[:1000]
        task.reserved_credit_cost = 0
        task.version += 1
        if retryable:
            task.status = "RETRY_WAIT"
            if queue is not None:
                queue.queue_status = "RETRY_WAIT"
                queue.available_at = now + timedelta(seconds=retry_delay)
                queue.last_error_code = error.code
                queue.lease_owner = None
                queue.lease_until = None
        else:
            task.status = "FAILED"
            task.finished_at = now
            if account is not None:
                account.failed_tasks += 1
            if queue is not None:
                queue.queue_status = "DONE"
                queue.last_error_code = error.code
                queue.lease_owner = None
                queue.lease_until = None
        if attempt is not None:
            attempt.outcome = "FAILED"
            attempt.error_code = error.code
            attempt.error_message = str(error)[:1000]
            attempt.finished_at = now
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
                        "source": "worker_submit_failure",
                        "phase": previous,
                        "attempt_no": assignment.attempt_no,
                        "retryable": retryable,
                        "error_code": error.code,
                        "model": task.model,
                        "estimated_credit_cost": task.estimated_credit_cost,
                        "released_credit_cost": released_reservation,
                    },
                )
            )
        session.add(
            add_event(
                task=task,
                event_type=(
                    "MEDIA_RESOLUTION_FAILED"
                    if previous == "RESOLVING_MEDIA"
                    else "UPSTREAM_SUBMIT_FAILED"
                ),
                previous=previous,
                current=task.status,
                actor_id=worker_id,
                reason=error.code,
            )
        )


async def apply_pre_submit_balance_refresh(
    assignment: Assignment,
    validation: AccountValidation,
    worker_id: str,
    settings: Settings,
) -> UpstreamError | None:
    """Persist an authoritative balance snapshot before any upstream submission.

    The task/account reservation is already held at this point.  Comparing the
    refreshed balance with all current reservations keeps concurrent assignments
    from submitting more work than the account can fund.
    """

    now = datetime.now(UTC).replace(tzinfo=None)
    async with session_factory() as session, session.begin():
        task = await session.get(Task, assignment.task_id, with_for_update=True)
        account = await session.get(Account, assignment.account_id, with_for_update=True)
        if (
            task is None
            or account is None
            or task.status not in {"RESOLVING_MEDIA", "SUBMITTING"}
        ):
            return UpstreamError(
                "PRE_SUBMIT_STATE_CHANGED",
                "task or account changed during the pre-submit balance refresh",
            )
        if (
            account.video_token_ciphertext is None
            or bytes(account.video_token_ciphertext) != assignment.encrypted_token
        ):
            return UpstreamError(
                "PRE_SUBMIT_TOKEN_CHANGED",
                "account token changed during the pre-submit balance refresh",
            )
        if account.status != "ACTIVE":
            return UpstreamError(
                "PRE_SUBMIT_ACCOUNT_INACTIVE",
                f"account is no longer active: {account.status}",
            )

        if not validation.valid:
            code = validation.error_code or "PRE_SUBMIT_BALANCE_CHECK_FAILED"
            account.last_error_code = code
            account.last_error_at = now
            account.version += 1
            if code in {"UPSTREAM_UNAUTHORIZED", "UPSTREAM_NO_USER_DETAILS"}:
                account.status = "TOKEN_EXPIRED"
                account.disabled_reason = code.lower()
            return UpstreamError(
                code,
                "upstream account validation failed before task submission",
                retryable=code not in {"UPSTREAM_UNAUTHORIZED", "UPSTREAM_NO_USER_DETAILS"},
            )

        refreshed_balance = validation.balance_credits
        if refreshed_balance is None:
            # Mock mode has no external wallet.  It still performs the validation
            # call, then retains the fixture balance so local smoke tests stay useful.
            if settings.upstream_mode.lower() == "mock":
                refreshed_balance = account.balance_credits
            else:
                account.last_error_code = "PRE_SUBMIT_BALANCE_MISSING"
                account.last_error_at = now
                account.version += 1
                return UpstreamError(
                    "PRE_SUBMIT_BALANCE_MISSING",
                    "upstream validation returned no balance before task submission",
                )

        refreshed_balance = max(int(refreshed_balance), 0)
        previous_balance = account.balance_credits
        account.balance_credits = refreshed_balance
        account.balance_synced_at = now
        account.last_error_code = None
        account.last_error_at = None
        account.version += 1
        session.add(
            AccountCreditLedger(
                ledger_uuid=str(uuid4()),
                account_id=account.id,
                task_id=task.id,
                entry_type="REFRESH",
                balance_before=previous_balance,
                balance_after=refreshed_balance,
                reserved_before=account.reserved_credits,
                reserved_after=account.reserved_credits,
                credit_delta=refreshed_balance - previous_balance,
                metadata_json={"source": "pre_submit_balance_refresh"},
            )
        )
        event = add_event(
            task=task,
            event_type="PRE_SUBMIT_BALANCE_REFRESHED",
            previous=task.status,
            current=task.status,
            actor_id=worker_id,
        )
        event.metadata_json = {
            "balance_before": previous_balance,
            "balance_after": refreshed_balance,
            "reserved_credits": account.reserved_credits,
        }
        session.add(event)

        below_threshold = refreshed_balance < settings.low_balance_threshold
        below_reservations = refreshed_balance < account.reserved_credits
        if below_threshold:
            account.status = "LOW_BALANCE_DISABLED"
            account.disabled_reason = "balance_below_threshold"
        if below_threshold:
            return UpstreamError(
                "PRE_SUBMIT_LOW_BALANCE",
                f"refreshed balance {refreshed_balance} is below threshold "
                f"{settings.low_balance_threshold}",
            )
        if below_reservations:
            return UpstreamError(
                "PRE_SUBMIT_INSUFFICIENT_AVAILABLE_BALANCE",
                f"refreshed balance {refreshed_balance} is below current reservations "
                f"{account.reserved_credits}",
            )

    return None


def _resolved_from_row(row: TaskMediaAsset) -> ResolvedMedia:
    assert row.provider_asset_id is not None
    metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
    extension = metadata.get("extension")
    frame_rate = metadata.get("frame_rate")
    audio_channels = metadata.get("audio_channels")
    audio_sample_rate = metadata.get("audio_sample_rate")
    return ResolvedMedia(
        kind=row.media_kind,
        role=row.media_role,
        ordinal=row.ordinal,
        source_url=row.source_url,
        provider_asset_id=row.provider_asset_id,
        provider_asset_type=row.provider_asset_type or "UPLOADED",
        content_type=row.content_type,
        content_length=row.content_length,
        duration_ms=row.duration_ms,
        width=row.width,
        height=row.height,
        extension=extension if isinstance(extension, str) else None,
        frame_rate=float(frame_rate) if isinstance(frame_rate, int | float) else None,
        audio_channels=(
            int(audio_channels) if isinstance(audio_channels, int | float) else None
        ),
        audio_sample_rate=(
            int(audio_sample_rate)
            if isinstance(audio_sample_rate, int | float)
            else None
        ),
    )


async def initialize_media_assets(
    assignment: Assignment,
    specs: list[MediaSpec],
    provider_namespace: str,
) -> dict[tuple[str, str, int], ResolvedMedia]:
    ready: dict[tuple[str, str, int], ResolvedMedia] = {}
    async with session_factory() as session, session.begin():
        task = await session.get(Task, assignment.task_id, with_for_update=True)
        if task is None or task.status != "RESOLVING_MEDIA":
            raise UpstreamError("MEDIA_TASK_STATE_CHANGED", "task left media resolution state")
        task.media_total = len(specs)
        task.media_resolved = 0
        for spec in specs:
            source_hash = hashlib.sha256(spec.source_url.encode()).hexdigest()
            current = await session.scalar(
                select(TaskMediaAsset).where(
                    TaskMediaAsset.task_id == assignment.task_id,
                    TaskMediaAsset.attempt_no == assignment.attempt_no,
                    TaskMediaAsset.media_kind == spec.kind,
                    TaskMediaAsset.media_role == spec.role,
                    TaskMediaAsset.ordinal == spec.ordinal,
                )
            )
            if current is None:
                current = TaskMediaAsset(
                    asset_uuid=str(uuid4()),
                    task_id=assignment.task_id,
                    account_id=assignment.account_id,
                    attempt_no=assignment.attempt_no,
                    media_kind=spec.kind,
                    media_role=spec.role,
                    ordinal=spec.ordinal,
                    source_url=spec.source_url,
                    source_url_hash=source_hash,
                    provider_namespace=provider_namespace,
                    status="PENDING",
                )
                session.add(current)
                await session.flush()
            if current.status == "READY" and current.provider_asset_id:
                ready[(spec.role, spec.kind, spec.ordinal)] = _resolved_from_row(current)
                continue
            cached = await session.scalar(
                select(TaskMediaAsset)
                .where(
                    TaskMediaAsset.account_id == assignment.account_id,
                    TaskMediaAsset.provider_namespace == provider_namespace,
                    TaskMediaAsset.media_kind == spec.kind,
                    TaskMediaAsset.source_url_hash == source_hash,
                    TaskMediaAsset.status == "READY",
                    TaskMediaAsset.provider_asset_id.is_not(None),
                    TaskMediaAsset.id != current.id,
                )
                .order_by(TaskMediaAsset.id.desc())
                .limit(1)
            )
            if cached is not None:
                current.status = "READY"
                current.provider_asset_id = cached.provider_asset_id
                current.provider_asset_type = cached.provider_asset_type
                current.content_type = cached.content_type
                current.content_length = cached.content_length
                current.duration_ms = cached.duration_ms
                current.width = cached.width
                current.height = cached.height
                current.metadata_json = {
                    **(cached.metadata_json or {}),
                    "cache_hit_asset_uuid": cached.asset_uuid,
                }
                current.resolved_at = datetime.now(UTC).replace(tzinfo=None)
                ready[(spec.role, spec.kind, spec.ordinal)] = _resolved_from_row(current)
        task.media_resolved = len(ready)
        task.version += 1
    return ready


async def record_media_ready(
    assignment: Assignment,
    resolved: ResolvedMedia,
    worker_id: str,
) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    async with session_factory() as session, session.begin():
        task = await session.get(Task, assignment.task_id, with_for_update=True)
        if task is None or task.status != "RESOLVING_MEDIA":
            raise UpstreamError("MEDIA_TASK_STATE_CHANGED", "task left media resolution state")
        row = await session.scalar(
            select(TaskMediaAsset).where(
                TaskMediaAsset.task_id == assignment.task_id,
                TaskMediaAsset.attempt_no == assignment.attempt_no,
                TaskMediaAsset.media_kind == resolved.kind,
                TaskMediaAsset.media_role == resolved.role,
                TaskMediaAsset.ordinal == resolved.ordinal,
            )
        )
        if row is None:
            raise UpstreamError("MEDIA_ASSET_ROW_MISSING", "media progress row is missing")
        row.status = "READY"
        row.provider_asset_id = resolved.provider_asset_id
        row.provider_asset_type = resolved.provider_asset_type
        row.content_type = resolved.content_type
        row.content_length = resolved.content_length
        row.duration_ms = resolved.duration_ms
        row.width = resolved.width
        row.height = resolved.height
        row.metadata_json = {
            key: value
            for key, value in {
                "extension": resolved.extension,
                "frame_rate": resolved.frame_rate,
                "audio_channels": resolved.audio_channels,
                "audio_sample_rate": resolved.audio_sample_rate,
            }.items()
            if value is not None
        }
        row.error_code = None
        row.error_message = None
        row.resolved_at = now
        # Persist the READY transition before counting it.  MySQL otherwise sees
        # the previous PENDING value inside this transaction on some drivers.
        await session.flush()
        task.media_resolved = int(
            await session.scalar(
                select(func.count(TaskMediaAsset.id)).where(
                    TaskMediaAsset.task_id == assignment.task_id,
                    TaskMediaAsset.attempt_no == assignment.attempt_no,
                    TaskMediaAsset.status == "READY",
                )
            )
            or 0
        )
        task.version += 1
        event = add_event(
            task=task,
            event_type="MEDIA_ASSET_RESOLVED",
            previous=task.status,
            current=task.status,
            actor_id=worker_id,
        )
        event.metadata_json = {
            "kind": resolved.kind,
            "role": resolved.role,
            "ordinal": resolved.ordinal,
            "resolved_assets": task.media_resolved,
            "total_assets": task.media_total,
        }
        session.add(event)


async def record_media_failure(
    assignment: Assignment,
    spec: MediaSpec,
    error: UpstreamError,
) -> None:
    async with session_factory() as session, session.begin():
        row = await session.scalar(
            select(TaskMediaAsset).where(
                TaskMediaAsset.task_id == assignment.task_id,
                TaskMediaAsset.attempt_no == assignment.attempt_no,
                TaskMediaAsset.media_kind == spec.kind,
                TaskMediaAsset.media_role == spec.role,
                TaskMediaAsset.ordinal == spec.ordinal,
            )
        )
        if row is not None:
            row.status = "FAILED"
            row.error_code = error.code
            row.error_message = str(error)[:1000]


async def resolve_assignment_media(
    assignment: Assignment,
    upstream: Upstream,
    token: str,
    worker_id: str,
    provider_namespace: str,
    media_concurrency: int,
) -> list[ResolvedMedia]:
    assert assignment.mode is not None
    specs = media_specs(assignment.mode, assignment.task_input)
    if assignment.model.lower() == "hailuo-03" and any(
        spec.kind == "VIDEO" for spec in specs
    ):
        raise UpstreamError(
            "PROVIDER_CAPABILITY_MISMATCH",
            "Leonardo H3 video-reference guidance is not enabled",
            retryable=False,
        )
    ready = await initialize_media_assets(assignment, specs, provider_namespace)
    semaphore = asyncio.Semaphore(max(1, media_concurrency))

    async def resolve_one(spec: MediaSpec) -> ResolvedMedia | Exception:
        key = (spec.role, spec.kind, spec.ordinal)
        item = ready.get(key)
        if item is not None:
            return item
        async with semaphore:
            try:
                item = await upstream.resolve_media(
                    token=token,
                    spec=spec,
                    max_audio_duration_ms=(
                        30_000 if is_seedance_25_model(assignment.model) else 15_000
                    ),
                    enforce_video_dimensions=not is_seedance_25_model(
                        assignment.model
                    ),
                    enforce_video_duration_bounds=not is_seedance_25_model(
                        assignment.model
                    ),
                )
                await record_media_ready(assignment, item, worker_id)
            except UpstreamError as exc:
                await record_media_failure(assignment, spec, exc)
                return exc
        return item

    outcomes = await asyncio.gather(*(resolve_one(spec) for spec in specs))
    resolved: list[ResolvedMedia] = []
    for outcome in outcomes:
        if isinstance(outcome, Exception):
            raise outcome
        resolved.append(outcome)
    if is_seedance_model(assignment.model) and assignment.mode == "reference-to-video":
        validate_reference_video_collection(
            resolved,
            max_combined_duration_ms=(
                30_000 if is_seedance_25_model(assignment.model) else 15_000
            ),
            enforce_dimensions=not is_seedance_25_model(assignment.model),
            enforce_individual_duration_bounds=not is_seedance_25_model(
                assignment.model
            ),
        )
        if is_seedance_25_model(assignment.model):
            validate_reference_audio_collection(
                resolved,
                max_combined_duration_ms=30_000,
                max_individual_duration_ms=30_000,
            )
    if assignment.model.lower() == "hailuo-03" and assignment.mode == "reference-to-video":
        for kind in ("AUDIO", "VIDEO"):
            combined = sum(
                item.duration_ms or 0 for item in resolved if item.kind == kind
            )
            if combined > 15000:
                raise UpstreamError(
                    "MEDIA_COMBINED_DURATION_INVALID",
                    f"combined reference {kind.lower()} duration exceeds 15 seconds",
                    retryable=False,
                )
    return resolved


async def mark_submission_ready(
    assignment: Assignment,
    request: dict[str, Any],
    worker_id: str,
) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    async with session_factory() as session, session.begin():
        task = await session.get(Task, assignment.task_id, with_for_update=True)
        if task is None or task.status != "RESOLVING_MEDIA":
            raise UpstreamError("MEDIA_TASK_STATE_CHANGED", "task left media resolution state")
        attempt = await session.scalar(
            select(TaskAttempt).where(
                TaskAttempt.task_id == assignment.task_id,
                TaskAttempt.attempt_no == assignment.attempt_no,
            )
        )
        previous = task.status
        task.status = "SUBMITTING"
        task.submit_started_at = now
        task.version += 1
        if attempt is not None:
            attempt.phase = "SUBMIT"
            attempt.request_json = {"request": request}
        session.add(
            add_event(
                task=task,
                event_type="MEDIA_RESOLUTION_COMPLETED",
                previous=previous,
                current="SUBMITTING",
                actor_id=worker_id,
            )
        )


async def renew_assignment_lease(
    assignment: Assignment,
    worker_id: str,
    settings: Settings,
    stop: asyncio.Event,
) -> None:
    interval = max(settings.queue_lease_seconds / 3, 1)
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            continue
        except TimeoutError:
            pass
        now = datetime.now(UTC).replace(tzinfo=None)
        async with session_factory() as session, session.begin():
            queue = await session.get(TaskQueue, assignment.task_id, with_for_update=True)
            if (
                queue is None
                or queue.queue_status != "LEASED"
                or queue.lease_owner != worker_id
            ):
                return
            queue.lease_until = now + timedelta(seconds=settings.queue_lease_seconds)


async def handle_assignment(
    assignment: Assignment,
    upstream: Upstream,
    worker_id: str,
    settings: Settings,
) -> None:
    lease_stop = asyncio.Event()
    lease_task = asyncio.create_task(
        renew_assignment_lease(assignment, worker_id, settings, lease_stop)
    )
    try:
        token = decrypt_secret(
            assignment.encrypted_token,
            f"{assignment.account_uuid}:video_token",
        )
        validation = await upstream.validate_account(token=token)
        balance_error = await apply_pre_submit_balance_refresh(
            assignment,
            validation,
            worker_id,
            settings,
        )
        if balance_error is not None:
            raise balance_error
        task_input = assignment.task_input
        if (
            assignment.input_schema_version
            in {
                "h3.v1",
                "seedance.v1",
                "gemini-omni-flash.v1",
                "seedance-2.5.v1",
                "kling-o3.v1",
                "gpt-image-2.v1",
                "nano-image.v1",
                "veo-3.1.v1",
                "veo-3.1-fast.v1",
                "veo-3.1-lite.v1",
                SEED_AUDIO_SCHEMA_VERSION,
            }
            and assignment.mode is not None
        ):
            resolved = await resolve_assignment_media(
                assignment,
                upstream,
                token,
                worker_id,
                settings.upstream_mode.lower(),
                settings.media_resolution_concurrency,
            )
            try:
                if is_gpt_image_2_model(assignment.model):
                    request = build_leonardo_gpt_image_2_request(
                        model=assignment.model,
                        mode=assignment.mode,
                        task_input=assignment.task_input,
                        assets=resolved,
                    )
                elif is_nano_image_model(assignment.model):
                    request = build_leonardo_nano_image_request(
                        model=assignment.model,
                        mode=assignment.mode,
                        task_input=assignment.task_input,
                        assets=resolved,
                    )
                elif is_seedance_model(assignment.model):
                    request = build_leonardo_seedance_request(
                        model=assignment.model,
                        mode=assignment.mode,
                        task_input=assignment.task_input,
                        assets=resolved,
                    )
                elif is_gemini_omni_flash_model(assignment.model):
                    request = build_leonardo_gemini_omni_flash_request(
                        model=assignment.model,
                        mode=assignment.mode,
                        task_input=assignment.task_input,
                        assets=resolved,
                    )
                elif is_kling_o3_model(assignment.model):
                    request = build_leonardo_kling_o3_request(
                        model=assignment.model,
                        mode=assignment.mode,
                        task_input=assignment.task_input,
                        assets=resolved,
                    )
                elif is_veo_3_1_model(assignment.model):
                    request = build_leonardo_veo_3_1_request(
                        model=assignment.model,
                        mode=assignment.mode,
                        task_input=assignment.task_input,
                        assets=resolved,
                    )
                elif is_seed_audio_model(assignment.model):
                    request = build_leonardo_seed_audio_request(
                        model=assignment.model,
                        mode=assignment.mode,
                        task_input=assignment.task_input,
                    )
                else:
                    request = build_leonardo_h3_request(
                        model=assignment.model,
                        mode=assignment.mode,
                        task_input=assignment.task_input,
                        assets=resolved,
                    )
            except ValueError as exc:
                raise UpstreamError(
                    "PROVIDER_CAPABILITY_MISMATCH",
                    str(exc),
                    retryable=False,
                ) from exc
            await mark_submission_ready(assignment, request, worker_id)
            task_input = {"request": request}
        result = await upstream.submit(
            token=token,
            model=assignment.model,
            task_input=task_input,
        )
    except UpstreamError as exc:
        await finish_submit_failure(assignment, exc, worker_id, settings)
        logger.warning(
            "upstream submission failed",
            extra={
                "task_uuid": assignment.task_uuid,
                "account_uuid": assignment.account_uuid,
                "worker_id": worker_id,
                "error_code": exc.code,
            },
        )
        lease_stop.set()
        await lease_task
        return
    except Exception:
        error = UpstreamError("WORKER_INTERNAL_ERROR", "worker submission failed")
        await finish_submit_failure(assignment, error, worker_id, settings)
        logger.exception(
            "worker internal error",
            extra={"task_uuid": assignment.task_uuid, "worker_id": worker_id},
        )
        lease_stop.set()
        await lease_task
        return
    await finish_submit_success(assignment, result, worker_id)
    logger.info(
        "task submitted upstream",
        extra={"task_uuid": assignment.task_uuid, "worker_id": worker_id},
    )
    lease_stop.set()
    await lease_task


async def worker_loop(stop: asyncio.Event) -> None:
    settings = get_settings()
    worker_id = f"worker-{uuid4()}"
    upstream = create_upstream(settings)
    running: set[asyncio.Task[None]] = set()
    next_recovery = 0.0
    try:
        while not stop.is_set():
            loop_time = asyncio.get_running_loop().time()
            if loop_time >= next_recovery:
                recovered = await recover_expired_leases(worker_id)
                if recovered:
                    logger.warning(
                        "expired queue leases recovered",
                        extra={"worker_id": worker_id},
                    )
                next_recovery = loop_time + max(settings.queue_lease_seconds / 2, 5)
            running = {task for task in running if not task.done()}
            if len(running) >= settings.worker_concurrency:
                await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
                continue
            assignment = await claim_and_assign(worker_id, settings)
            if assignment is None:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_seconds)
                except TimeoutError:
                    pass
                continue
            running.add(
                asyncio.create_task(handle_assignment(assignment, upstream, worker_id, settings))
            )
        if running:
            await asyncio.gather(*running, return_exceptions=True)
    finally:
        await upstream.close()


async def async_main() -> None:
    configure_logging()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    logger.info("worker started")
    try:
        await worker_loop(stop)
    finally:
        await dispose_engine()
        logger.info("worker stopped")


if __name__ == "__main__":
    asyncio.run(async_main())
