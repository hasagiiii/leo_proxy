from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import case, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from video_task_service.auth import require_login_worker_key
from video_task_service.config import Settings, get_settings
from video_task_service.crypto import decrypt_secret, encrypt_secret, token_fingerprint
from video_task_service.db import session_dependency
from video_task_service.login_dispatch import (
    ACTIVE_LOGIN_JOB_STATUSES,
    LOGIN_EXCLUDED_ACCOUNT_STATUSES,
    RENEWAL_ACCOUNT_STATUSES,
    STALLED_LOGIN_ERROR_CODES,
    WORKER_INFRASTRUCTURE_LOGIN_ERROR_CODES,
    activation_dispatch_budget,
    active_credit_deficit,
    login_failure_disposition,
    renewal_balance_available_clause,
    retry_delay_seconds,
)
from video_task_service.models import (
    Account,
    AccountLoginJob,
    AccountRenewalSession,
    LoginPoolPolicy,
)
from video_task_service.protocol_renewal import (
    RenewalSessionStorageError,
    delete_stored_renewal_session,
    store_renewal_session,
)
from video_task_service.schemas import (
    LoginJobClaimRequest,
    LoginJobClaimResponse,
    LoginJobFailureReport,
    LoginJobHeartbeatResponse,
    LoginJobItem,
    LoginJobLeaseRequest,
    LoginJobTokenReport,
    LoginJobView,
    LoginPoolSnapshot,
    LoginWorkerStatus,
)

router = APIRouter(
    prefix="/account-login-jobs",
    tags=["account-login-jobs"],
    dependencies=[Depends(require_login_worker_key)],
)
logger = logging.getLogger(__name__)


def no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


@router.get("/worker-status", response_model=LoginWorkerStatus)
async def get_login_worker_status(response: Response) -> LoginWorkerStatus:
    """Authenticate a worker without leasing account credentials."""
    no_store(response)
    settings = get_settings()
    return LoginWorkerStatus(
        credit_target=settings.login_active_credit_target,
        idle_target=settings.login_idle_target,
        renewal_window_seconds=settings.login_renewal_window_seconds,
        lease_seconds=settings.login_job_lease_seconds,
        max_batch_size=settings.login_job_max_batch_size,
    )


def lease_token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def job_view(job: AccountLoginJob, account: Account) -> LoginJobView:
    return LoginJobView(
        job_uuid=UUID(job.job_uuid),
        account_uuid=UUID(account.account_uuid),
        job_type=job.job_type,
        status=job.status,
        attempt_no=job.attempt_no,
        lease_owner=job.lease_owner,
        lease_expires_at=job.lease_until,
        token_received_at=job.token_received_at,
        validation_finished_at=job.validation_finished_at,
        retry_after=job.retry_after,
        error_code=job.error_code,
        error_message=job.error_message,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def check_lease_identity(job: AccountLoginJob, body: LoginJobLeaseRequest) -> None:
    received = lease_token_hash(body.lease_token.get_secret_value())
    if job.lease_owner != body.worker_id or not secrets.compare_digest(
        received,
        job.lease_token_hash,
    ):
        raise HTTPException(
            status_code=401,
            detail={"code": "INVALID_LOGIN_JOB_LEASE", "message": "lease credentials differ"},
        )


def check_active_lease(
    job: AccountLoginJob,
    body: LoginJobLeaseRequest,
    now: datetime,
) -> None:
    check_lease_identity(job, body)
    if job.status != "LEASED":
        raise HTTPException(
            status_code=409,
            detail={"code": "LOGIN_JOB_NOT_LEASED", "status": job.status},
        )
    if job.lease_until is None or job.lease_until <= now:
        raise HTTPException(
            status_code=409,
            detail={"code": "LOGIN_JOB_LEASE_EXPIRED", "message": "claim the account again"},
        )


async def apply_login_failure(
    session: AsyncSession,
    job: AccountLoginJob,
    account: Account,
    *,
    error_code: str,
    error_message: str | None,
    retryable: bool,
    now: datetime,
    settings: Settings,
) -> None:
    normalized = error_code.strip().upper()
    previous_account_failures = int(
        await session.scalar(
            account_failure_count_statement(account.id, job.job_type, normalized)
        )
        or 0
    )
    disposition = login_failure_disposition(
        normalized,
        retryable=retryable,
        previous_account_failures=previous_account_failures,
        max_account_failures=settings.login_job_max_account_failures,
        stalled_max_account_failures=settings.login_job_stalled_max_account_failures,
    )
    job.status = "FAILED"
    job.active_account_id = None
    job.lease_until = None
    job.validation_finished_at = now
    if disposition == "QUARANTINE":
        job.retry_after = None
        account.status = "MANUAL_DISABLED"
        account.disabled_reason = (
            f"login_retry_exhausted:{normalized}"
            if retryable
            else f"login_nonretryable:{normalized}"
        )
    else:
        delay = (
            settings.login_job_worker_backoff_seconds
            if disposition == "WORKER_BACKOFF"
            else retry_delay_seconds(
                previous_account_failures + 1,
                retryable=True,
                retry_base_seconds=settings.login_job_retry_base_seconds,
                nonretryable_retry_seconds=settings.login_job_nonretryable_retry_seconds,
            )
        )
        job.retry_after = now + timedelta(seconds=delay)
    job.error_code = normalized
    job.error_message = error_message
    account.last_error_code = normalized
    account.last_error_at = now
    account.version += 1


async def expire_stale_jobs(
    session: AsyncSession,
    *,
    now: datetime,
    settings: Settings,
) -> None:
    validation_cutoff = now - timedelta(seconds=settings.login_validation_timeout_seconds)
    jobs = list(
        await session.scalars(
            select(AccountLoginJob)
            .where(
                or_(
                    (
                        (AccountLoginJob.status == "LEASED")
                        & (AccountLoginJob.lease_until.is_not(None))
                        & (AccountLoginJob.lease_until <= now)
                    ),
                    (
                        (AccountLoginJob.status == "VALIDATING")
                        & (AccountLoginJob.token_received_at.is_not(None))
                        & (AccountLoginJob.token_received_at <= validation_cutoff)
                    ),
                )
            )
            .with_for_update(skip_locked=True)
        )
    )
    for job in jobs:
        was_leased = job.status == "LEASED"
        error_code = (
            "LOGIN_JOB_LEASE_EXPIRED" if was_leased else "LOGIN_JOB_VALIDATION_TIMEOUT"
        )
        account = await session.get(Account, job.account_id, with_for_update=True)
        if account is None:
            job.status = "FAILED"
            job.active_account_id = None
            job.lease_until = None
            job.validation_finished_at = now
            job.retry_after = None
            job.error_code = "LOGIN_JOB_ACCOUNT_MISSING"
            job.error_message = "account was removed"
            continue
        await apply_login_failure(
            session,
            job,
            account,
            error_code=error_code,
            error_message="login job timed out",
            retryable=True,
            now=now,
            settings=settings,
        )


def known_low_balance_clause(settings: Settings):  # type: ignore[no-untyped-def]
    return (
        Account.balance_synced_at.is_not(None)
        & (Account.balance_credits < settings.low_balance_threshold)
    )


def active_credit_total_statement():  # type: ignore[no-untyped-def]
    return select(func.coalesce(func.sum(Account.balance_credits), 0)).where(
        Account.status == "ACTIVE"
    )


def active_job_exists_clause():  # type: ignore[no-untyped-def]
    return exists(
        select(AccountLoginJob.id).where(AccountLoginJob.active_account_id == Account.id)
    )


def retry_block_exists_clause(now: datetime):  # type: ignore[no-untyped-def]
    return exists(
        select(AccountLoginJob.id).where(
            AccountLoginJob.account_id == Account.id,
            AccountLoginJob.status == "FAILED",
            AccountLoginJob.retry_after.is_not(None),
            AccountLoginJob.retry_after > now,
        )
    )


def account_failure_count_statement(  # type: ignore[no-untyped-def]
    account_id: int,
    job_type: str,
    error_code: str,
):
    """Count account-attributable failures relevant to the current error."""

    normalized = error_code.strip().upper()
    conditions = [
        AccountLoginJob.account_id == account_id,
        AccountLoginJob.job_type == job_type,
        AccountLoginJob.status == "FAILED",
    ]
    if normalized in STALLED_LOGIN_ERROR_CODES:
        conditions.append(AccountLoginJob.error_code.in_(STALLED_LOGIN_ERROR_CODES))
    else:
        conditions.append(
            or_(
                AccountLoginJob.error_code.is_(None),
                AccountLoginJob.error_code.notin_(WORKER_INFRASTRUCTURE_LOGIN_ERROR_CODES),
            )
        )
    return select(func.count(AccountLoginJob.id)).where(*conditions)


def activation_failure_count_subquery():  # type: ignore[no-untyped-def]
    return (
        select(func.count(AccountLoginJob.id))
        .where(
            AccountLoginJob.account_id == Account.id,
            AccountLoginJob.job_type == "ACTIVATE_NEW",
            AccountLoginJob.status == "FAILED",
            or_(
                AccountLoginJob.error_code.is_(None),
                AccountLoginJob.error_code.notin_(WORKER_INFRASTRUCTURE_LOGIN_ERROR_CODES),
            ),
        )
        .correlate(Account)
        .scalar_subquery()
    )


def activation_last_attempt_subquery():  # type: ignore[no-untyped-def]
    return (
        select(func.max(AccountLoginJob.created_at))
        .where(
            AccountLoginJob.account_id == Account.id,
            AccountLoginJob.job_type == "ACTIVATE_NEW",
        )
        .correlate(Account)
        .scalar_subquery()
    )


async def next_attempt_no(session: AsyncSession, account_id: int) -> int:
    last_attempt = await session.scalar(
        select(func.max(AccountLoginJob.attempt_no)).where(
            AccountLoginJob.account_id == account_id
        )
    )
    return int(last_attempt or 0) + 1


async def lease_account(
    session: AsyncSession,
    account: Account,
    *,
    job_type: str,
    worker_id: str,
    now: datetime,
    settings: Settings,
) -> tuple[AccountLoginJob, LoginJobItem] | None:
    try:
        password = decrypt_secret(
            bytes(account.password_ciphertext),
            f"{account.account_uuid}:password",
        )
    except Exception:
        account.status = "MANUAL_DISABLED"
        account.disabled_reason = "password_decrypt_failed"
        account.last_error_code = "ACCOUNT_PASSWORD_DECRYPT_FAILED"
        account.last_error_at = now
        account.version += 1
        return None

    lease_token = secrets.token_urlsafe(32)
    lease_until = now + timedelta(seconds=settings.login_job_lease_seconds)
    job = AccountLoginJob(
        job_uuid=str(uuid4()),
        account_id=account.id,
        active_account_id=account.id,
        job_type=job_type,
        status="LEASED",
        lease_owner=worker_id,
        lease_token_hash=lease_token_hash(lease_token),
        lease_until=lease_until,
        claimed_account_version=account.version,
        claimed_token_refreshed_at=account.token_refreshed_at,
        attempt_no=await next_attempt_no(session, account.id),
    )
    session.add(job)
    return job, LoginJobItem(
        job_uuid=UUID(job.job_uuid),
        job_type=job_type,  # type: ignore[arg-type]
        account_uuid=UUID(account.account_uuid),
        login_name=account.login_name,
        password=password,
        previous_token_expires_at=account.token_expires_at,
        lease_token=lease_token,
        lease_expires_at=lease_until,
    )


@router.post("/claim", response_model=LoginJobClaimResponse)
async def claim_login_jobs(
    body: LoginJobClaimRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> LoginJobClaimResponse:
    no_store(response)
    settings = get_settings()
    now = datetime.now(UTC).replace(tzinfo=None)
    renewal_cutoff = now + timedelta(seconds=settings.login_renewal_window_seconds)
    client_session_cutoff = now - timedelta(
        seconds=settings.protocol_renewal_client_session_max_age_seconds
    )
    claim_limit = min(body.limit, settings.login_job_max_batch_size)
    claimed: list[LoginJobItem] = []
    renewal_claimed = 0
    activation_claimed = 0

    async with session.begin():
        policy = await session.scalar(
            select(LoginPoolPolicy).where(LoginPoolPolicy.id == 1).with_for_update()
        )
        if policy is None:
            policy = LoginPoolPolicy(id=1)
            session.add(policy)
            await session.flush()

        await expire_stale_jobs(session, now=now, settings=settings)
        await session.flush()
        active_job = active_job_exists_clause()
        retry_block = retry_block_exists_clause(now)
        known_low = known_low_balance_clause(settings)

        active_credit_total = int(await session.scalar(active_credit_total_statement()) or 0)
        credit_deficit = active_credit_deficit(
            settings.login_active_credit_target,
            active_credit_total,
        )

        # These count metrics remain response-only compatibility data for
        # existing desktop builds. They no longer decide activation dispatch.
        available_idle = int(
            await session.scalar(
                select(func.count(Account.id)).where(
                    Account.status == "ACTIVE",
                    Account.video_token_ciphertext.is_not(None),
                    Account.token_expires_at.is_not(None),
                    Account.token_expires_at > renewal_cutoff,
                    Account.active_tasks == 0,
                    Account.balance_synced_at.is_not(None),
                    Account.balance_credits >= settings.low_balance_threshold,
                    ~active_job,
                )
            )
            or 0
        )
        in_flight_idle = int(
            await session.scalar(
                select(func.count(AccountLoginJob.id))
                .join(Account, Account.id == AccountLoginJob.active_account_id)
                .where(
                    AccountLoginJob.status.in_(ACTIVE_LOGIN_JOB_STATUSES),
                    Account.active_tasks == 0,
                    ~known_low,
                    Account.status != "MANUAL_DISABLED",
                )
            )
            or 0
        )
        activation_in_flight = int(
            await session.scalar(
                select(func.count(AccountLoginJob.id)).where(
                    AccountLoginJob.status.in_(ACTIVE_LOGIN_JOB_STATUSES),
                    AccountLoginJob.job_type == "ACTIVATE_NEW",
                )
            )
            or 0
        )
        budget_before = activation_dispatch_budget(
            settings.login_active_credit_target,
            active_credit_total,
            activation_in_flight,
            settings.login_activation_max_in_flight,
        )
        # Desktop workers normally request a single job.  If renewals always run
        # first, a continuous renewal stream can consume that slot forever and
        # starve a pool that is already below its ACTIVE-credit watermark.  Keep
        # one activation lane occupied while below watermark; once it is in
        # flight, the remaining workers continue handling renewals.  When no
        # renewal is due, the unused capacity can still fill all activation
        # slots up to login_activation_max_in_flight.
        activation_reservation = int(
            credit_deficit > 0
            and budget_before > 0
            and activation_in_flight == 0
            and claim_limit > 0
        )
        renewal_claim_limit = max(0, claim_limit - activation_reservation)

        renewal_reasons = [
            Account.status.in_(RENEWAL_ACCOUNT_STATUSES),
            Account.last_error_code == "UPSTREAM_UNAUTHORIZED",
            Account.token_expires_at.is_(None),
            Account.token_expires_at <= renewal_cutoff,
        ]
        if settings.protocol_renewal_enabled:
            renewal_reasons.append(
                exists(
                    select(AccountRenewalSession.account_id).where(
                        AccountRenewalSession.account_id == Account.id,
                        or_(
                            AccountRenewalSession.client_reported_at.is_(None),
                            AccountRenewalSession.client_reported_at
                            <= client_session_cutoff,
                        ),
                    )
                )
            )
        renewal_due = or_(*renewal_reasons)
        renewals: list[Account] = []
        if renewal_claim_limit > 0:
            renewals = list(
                await session.scalars(
                    select(Account)
                    .where(
                        Account.credential_source == "PASSWORD",
                        Account.video_token_ciphertext.is_not(None),
                        Account.status != "PENDING_VALIDATION",
                        Account.status.notin_(LOGIN_EXCLUDED_ACCOUNT_STATUSES),
                        renewal_balance_available_clause(),
                        renewal_due,
                        ~active_job,
                        ~retry_block,
                    )
                    .order_by(
                        case(
                            (Account.last_error_code == "UPSTREAM_UNAUTHORIZED", 0),
                            (Account.token_expires_at.is_(None), 1),
                            (Account.token_expires_at <= now, 2),
                            else_=3,
                        ),
                        Account.token_expires_at.asc(),
                        Account.id.asc(),
                    )
                    .limit(renewal_claim_limit)
                    .with_for_update(skip_locked=True)
                )
            )
        newly_claimed_idle_renewals = 0
        for account in renewals:
            client_refresh_due = False
            if settings.protocol_renewal_enabled:
                renewal_session = await session.get(
                    AccountRenewalSession,
                    account.id,
                    with_for_update=True,
                )
                if renewal_session is not None:
                    client_refresh_due = (
                        renewal_session.client_reported_at is None
                        or renewal_session.client_reported_at <= client_session_cutoff
                    )
                    if (
                        client_refresh_due
                        and renewal_session.status == "RUNNING"
                        and renewal_session.lease_until is not None
                        and renewal_session.lease_until > now
                    ):
                        if account.active_tasks == 0:
                            newly_claimed_idle_renewals += 1
                        continue
                    if (
                        renewal_session.fallback_after is not None
                        and renewal_session.fallback_after <= now
                    ):
                        renewal_session.status = "FALLBACK"
                        renewal_session.lease_owner = None
                        renewal_session.lease_until = None
                        renewal_session.retry_after = None
                        renewal_session.last_error_code = (
                            renewal_session.last_error_code or "PROTOCOL_GRACE_EXPIRED"
                        )
                    if (
                        not client_refresh_due
                        and renewal_session.status != "FALLBACK"
                    ):
                        if renewal_session.status == "IDLE":
                            renewal_session.status = "PENDING"
                            renewal_session.attempt_count = 0
                            renewal_session.fallback_after = now + timedelta(
                                seconds=settings.protocol_renewal_grace_seconds
                            )
                            renewal_session.retry_after = None
                            renewal_session.last_error_code = None
                        if account.active_tasks == 0:
                            newly_claimed_idle_renewals += 1
                        continue
            leased = await lease_account(
                session,
                account,
                job_type=("REFRESH_SESSION" if client_refresh_due else "RENEW_TOKEN"),
                worker_id=body.worker_id,
                now=now,
                settings=settings,
            )
            if leased is None:
                continue
            _, item = leased
            claimed.append(item)
            renewal_claimed += 1
            if account.active_tasks == 0:
                newly_claimed_idle_renewals += 1

        remaining = claim_limit - len(claimed)
        activation_limit = min(remaining, budget_before)
        if activation_limit > 0:
            activations = list(
                await session.scalars(
                    select(Account)
                    .where(
                        Account.credential_source == "PASSWORD",
                        Account.video_token_ciphertext.is_(None),
                        Account.status.in_(
                            ("PENDING_VALIDATION", "TOKEN_EXPIRED", "TOKEN_INVALID")
                        ),
                        ~known_low,
                        Account.active_tasks == 0,
                        ~active_job,
                        ~retry_block,
                    )
                    .order_by(
                        activation_failure_count_subquery().asc(),
                        func.coalesce(
                            activation_last_attempt_subquery(), Account.created_at
                        ).asc(),
                        Account.id.asc(),
                    )
                    .limit(activation_limit)
                    .with_for_update(skip_locked=True)
                )
            )
            for account in activations:
                leased = await lease_account(
                    session,
                    account,
                    job_type="ACTIVATE_NEW",
                    worker_id=body.worker_id,
                    now=now,
                    settings=settings,
                )
                if leased is None:
                    continue
                _, item = leased
                claimed.append(item)
                activation_claimed += 1

        policy.updated_at = now

    post_claim_in_flight = (
        in_flight_idle + newly_claimed_idle_renewals + activation_claimed
    )
    effective_idle = available_idle + post_claim_in_flight
    post_claim_activation_in_flight = activation_in_flight + activation_claimed
    logger.info(
        "account_credit_watermark mode=ACTIVE_CREDIT_SUM target=%s active_total=%s "
        "deficit=%s below=%s activation_in_flight=%s activation_reserved=%s "
        "activation_claimed=%s renewal_claimed=%s",
        settings.login_active_credit_target,
        active_credit_total,
        credit_deficit,
        credit_deficit > 0,
        post_claim_activation_in_flight,
        activation_reservation,
        activation_claimed,
        renewal_claimed,
    )
    return LoginJobClaimResponse(
        jobs=claimed,
        pool=LoginPoolSnapshot(
            credit_target=settings.login_active_credit_target,
            active_credit_total=active_credit_total,
            credit_deficit=credit_deficit,
            below_watermark=credit_deficit > 0,
            activation_in_flight=post_claim_activation_in_flight,
            idle_target=settings.login_idle_target,
            available_idle=available_idle,
            in_flight_idle=post_claim_in_flight,
            effective_idle=effective_idle,
            activation_budget_before_claim=budget_before,
            renewal_claimed=renewal_claimed,
            activation_claimed=activation_claimed,
            new_account_dispatch_suppressed=activation_claimed == 0,
        ),
    )


async def locked_job_and_account(
    session: AsyncSession,
    job_uuid: UUID,
) -> tuple[AccountLoginJob, Account]:
    job = await session.scalar(
        select(AccountLoginJob)
        .where(AccountLoginJob.job_uuid == str(job_uuid))
        .with_for_update()
    )
    if job is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "LOGIN_JOB_NOT_FOUND", "message": "login job was not found"},
        )
    account = await session.get(Account, job.account_id, with_for_update=True)
    if account is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "LOGIN_JOB_ACCOUNT_MISSING", "message": "account was removed"},
        )
    return job, account


@router.post("/{job_uuid}/heartbeat", response_model=LoginJobHeartbeatResponse)
async def heartbeat_login_job(
    job_uuid: UUID,
    body: LoginJobLeaseRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> LoginJobHeartbeatResponse:
    no_store(response)
    now = datetime.now(UTC).replace(tzinfo=None)
    settings = get_settings()
    async with session.begin():
        job, _ = await locked_job_and_account(session, job_uuid)
        check_active_lease(job, body, now)
        job.lease_until = now + timedelta(seconds=settings.login_job_lease_seconds)
    assert job.lease_until is not None
    return LoginJobHeartbeatResponse(
        job_uuid=job_uuid,
        status=job.status,
        lease_expires_at=job.lease_until,
    )


@router.post(
    "/{job_uuid}/token",
    response_model=LoginJobView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def report_login_token(
    job_uuid: UUID,
    body: LoginJobTokenReport,
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> LoginJobView:
    no_store(response)
    now = datetime.now(UTC).replace(tzinfo=None)
    settings = get_settings()
    fingerprint = token_fingerprint(body.video_token.get_secret_value())
    try:
        async with session.begin():
            job, account = await locked_job_and_account(session, job_uuid)
            check_lease_identity(job, body)
            if job.status in {"VALIDATING", "SUCCEEDED"}:
                if job.reported_token_fingerprint != fingerprint:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "LOGIN_JOB_TOKEN_ALREADY_REPORTED",
                            "status": job.status,
                        },
                    )
                return job_view(job, account)
            check_active_lease(job, body, now)
            if account.status == "MANUAL_DISABLED":
                raise HTTPException(
                    status_code=409,
                    detail={"code": "LOGIN_JOB_ACCOUNT_DISABLED", "message": "account is disabled"},
                )
            if account.token_refreshed_at != job.claimed_token_refreshed_at:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "LOGIN_JOB_TOKEN_SUPERSEDED",
                        "message": "a newer token was stored after this job was claimed",
                    },
                )
            if body.token_expires_at <= now + timedelta(
                seconds=settings.login_renewal_window_seconds
            ):
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "LOGIN_JOB_TOKEN_EXPIRY_TOO_SOON",
                        "message": "reported token must remain valid beyond the renewal window",
                    },
                )

            account.video_token_ciphertext = encrypt_secret(
                body.video_token.get_secret_value(),
                f"{account.account_uuid}:video_token",
            )
            account.token_expires_at = body.token_expires_at
            account.token_refreshed_at = now
            if body.balance_credits is not None:
                account.balance_credits = body.balance_credits
                account.balance_synced_at = now
            account.status = "PENDING_VALIDATION"
            account.disabled_reason = None
            account.last_error_code = None
            account.last_error_at = None
            account.version += 1
            if body.renewal_session is not None:
                await store_renewal_session(session, account, body.renewal_session)
            else:
                await delete_stored_renewal_session(session, account.id)

            job.status = "VALIDATING"
            job.reported_token_fingerprint = fingerprint
            job.token_received_at = now
            job.lease_until = None
            job.error_code = None
            job.error_message = None
    except RenewalSessionStorageError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "RENEWAL_SESSION_TOO_LARGE",
                "message": "renewal session exceeds the encrypted storage limit",
            },
        ) from exc
    return job_view(job, account)


@router.post("/{job_uuid}/fail", response_model=LoginJobView)
async def fail_login_job(
    job_uuid: UUID,
    body: LoginJobFailureReport,
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> LoginJobView:
    no_store(response)
    now = datetime.now(UTC).replace(tzinfo=None)
    settings = get_settings()
    async with session.begin():
        job, account = await locked_job_and_account(session, job_uuid)
        check_active_lease(job, body, now)
        await apply_login_failure(
            session,
            job,
            account,
            error_code=body.error_code,
            error_message=body.error_message,
            retryable=body.retryable,
            now=now,
            settings=settings,
        )
    return job_view(job, account)


@router.get("/{job_uuid}", response_model=LoginJobView)
async def get_login_job(
    job_uuid: UUID,
    worker_id: Annotated[str, Query(min_length=1, max_length=128)],
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> LoginJobView:
    no_store(response)
    row = (
        await session.execute(
            select(AccountLoginJob, Account)
            .join(Account, Account.id == AccountLoginJob.account_id)
            .where(AccountLoginJob.job_uuid == str(job_uuid))
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "LOGIN_JOB_NOT_FOUND", "message": "login job was not found"},
        )
    job, account = row
    if job.lease_owner != worker_id:
        raise HTTPException(
            status_code=404,
            detail={"code": "LOGIN_JOB_NOT_FOUND", "message": "login job was not found"},
        )
    return job_view(job, account)
