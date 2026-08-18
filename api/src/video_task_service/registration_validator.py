from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from video_task_service.config import Settings, get_settings
from video_task_service.crypto import decrypt_secret, encrypt_secret
from video_task_service.db import session_factory
from video_task_service.models import ParentAccount, RegistrationRecord
from video_task_service.protocol_renewal import (
    ProtocolRenewalError,
    ProtocolRenewalResult,
    encode_renewal_session,
    renew_protocol_session,
)
from video_task_service.registration_records import settle_success
from video_task_service.schemas import RenewalSessionPayload
from video_task_service.upstream import AccountValidation, Upstream, create_upstream

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ClaimedRegistration:
    registration_id: int
    registration_uuid: str
    parent_account_id: int
    email: str
    registered_email: str
    session_ciphertext: bytes
    owner: str
    claimed_version: int
    attempt_count: int
    lease_until: datetime


@dataclass(frozen=True, slots=True)
class RegistrationValidationResult:
    login_name: str
    token: str
    token_expires_at: datetime
    balance_credits: int
    renewal_session: RenewalSessionPayload


class RegistrationValidationError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message[:300])
        self.code = code[:64]
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


def registration_validation_claim_statement(now: datetime, batch_size: int):  # type: ignore[no-untyped-def]
    return (
        select(RegistrationRecord)
        .where(
            or_(
                RegistrationRecord.status == "COOKIE_REPORTED",
                (
                    (RegistrationRecord.status == "VALIDATION_RETRY_WAIT")
                    & (RegistrationRecord.retry_after.is_not(None))
                    & (RegistrationRecord.retry_after <= now)
                ),
                (
                    (RegistrationRecord.status == "VALIDATING")
                    & (RegistrationRecord.validation_lease_until.is_not(None))
                    & (RegistrationRecord.validation_lease_until <= now)
                ),
            )
        )
        .order_by(RegistrationRecord.id.asc())
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )


async def claim_registrations(
    session: AsyncSession,
    *,
    owner: str,
    now: datetime,
    batch_size: int,
    lease_seconds: int,
) -> list[ClaimedRegistration]:
    rows = list(await session.scalars(registration_validation_claim_statement(now, batch_size)))
    lease_until = now + timedelta(seconds=lease_seconds)
    claims: list[ClaimedRegistration] = []
    for row in rows:
        if row.session_ciphertext is None or row.registered_email is None:
            row.status = "VALIDATION_FAILED"
            row.validation_error_code = "REGISTRATION_SESSION_MISSING"
            row.validation_error_message = "Encrypted registration session is missing"
            row.validation_finished_at = now
            row.version += 1
            continue
        row.status = "VALIDATING"
        row.validation_attempts += 1
        row.validation_lease_owner = owner
        row.validation_lease_until = lease_until
        row.retry_after = None
        row.version += 1
        claims.append(
            ClaimedRegistration(
                registration_id=row.id,
                registration_uuid=row.registration_uuid,
                parent_account_id=row.parent_account_id,
                email=row.email_snapshot,
                registered_email=row.registered_email,
                session_ciphertext=bytes(row.session_ciphertext),
                owner=owner,
                claimed_version=row.version,
                attempt_count=row.validation_attempts,
                lease_until=lease_until,
            )
        )
    return claims


async def expire_timed_out_registrations(
    session: AsyncSession,
    *,
    now: datetime,
    batch_size: int,
) -> int:
    rows = list(
        await session.scalars(
            select(RegistrationRecord)
            .where(
                RegistrationRecord.status == "RUNNING",
                RegistrationRecord.lease_expires_at <= now,
            )
            .order_by(RegistrationRecord.id.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
    )
    for row in rows:
        parent = await session.get(ParentAccount, row.parent_account_id, with_for_update=True)
        row.status = "FAILED"
        row.client_error_code = "CLIENT_TIMEOUT"
        row.client_error_message = "Registration client lease expired"
        row.validation_finished_at = now
        row.version += 1
        if parent is not None:
            parent.invite_failure_count += 1
            parent.version += 1
    return len(rows)


def _protocol_error(error: ProtocolRenewalError) -> RegistrationValidationError:
    permanent_codes = {
        "PROTOCOL_SESSION_REVOKED",
        "PROTOCOL_WRONG_ACCOUNT",
        "PROTOCOL_SESSION_INVALID",
    }
    return RegistrationValidationError(
        error.code,
        "Registration session validation failed",
        retryable=error.retryable and error.code not in permanent_codes,
        retry_after_seconds=error.retry_after_seconds,
    )


async def validate_registration(
    claim: ClaimedRegistration,
    protocol_client: Callable[..., Awaitable[ProtocolRenewalResult]],
    upstream: Upstream,
    settings: Settings,
) -> RegistrationValidationResult:
    try:
        encoded = decrypt_secret(
            claim.session_ciphertext,
            f"{claim.registration_uuid}:registration_session",
        )
        material = json.loads(encoded)
        RenewalSessionPayload.model_validate(material)
    except (ValueError, json.JSONDecodeError, ValidationError) as exc:
        raise RegistrationValidationError(
            "REGISTRATION_SESSION_INVALID",
            "Encrypted registration session has an invalid shape",
            retryable=False,
        ) from exc
    try:
        protocol_result = await protocol_client(
            material=material,
            stored_token="",
            expected_email=claim.email,
            settings=settings,
        )
    except ProtocolRenewalError as exc:
        raise _protocol_error(exc) from exc

    expected = claim.email.strip().lower()
    reported = claim.registered_email.strip().lower()
    session_email = str(protocol_result.session_email or "").strip().lower()
    if not session_email:
        raise RegistrationValidationError(
            "PROTOCOL_IDENTITY_UNAVAILABLE",
            "Session identity was temporarily unavailable",
            retryable=True,
        )
    if len({expected, reported, session_email}) != 1:
        raise RegistrationValidationError(
            "REGISTRATION_IDENTITY_MISMATCH",
            "Claimed, reported, and session identities do not match",
            retryable=False,
        )

    try:
        validation: AccountValidation = await upstream.validate_account(token=protocol_result.token)
    except Exception as exc:
        raise RegistrationValidationError(
            "UPSTREAM_NETWORK_ERROR",
            "Balance validation request failed",
            retryable=True,
        ) from exc
    if not validation.valid:
        code = validation.error_code or "UPSTREAM_ACCOUNT_VALIDATION_FAILED"
        retryable = code in {
            "UPSTREAM_SERVER_ERROR",
            "UPSTREAM_NETWORK_ERROR",
            "UPSTREAM_RATE_LIMITED",
        }
        raise RegistrationValidationError(
            code,
            "Balance validation failed",
            retryable=retryable,
        )
    graphql_email = str(validation.login_name or "").strip().lower()
    if not graphql_email or validation.balance_credits is None:
        raise RegistrationValidationError(
            "UPSTREAM_IDENTITY_OR_BALANCE_MISSING",
            "Balance response omitted identity or credits",
            retryable=True,
        )
    if graphql_email != expected:
        raise RegistrationValidationError(
            "REGISTRATION_IDENTITY_MISMATCH",
            "Session and balance identities do not match",
            retryable=False,
        )
    try:
        renewed_session = RenewalSessionPayload.model_validate(protocol_result.renewal_session)
    except ValidationError as exc:
        raise RegistrationValidationError(
            "PROTOCOL_SESSION_ROTATION_INVALID",
            "Rotated session has an invalid shape",
            retryable=False,
        ) from exc
    return RegistrationValidationResult(
        login_name=expected,
        token=protocol_result.token,
        token_expires_at=protocol_result.token_expires_at,
        balance_credits=validation.balance_credits,
        renewal_session=renewed_session,
    )


def _claim_matches(row: RegistrationRecord, claim: ClaimedRegistration) -> bool:
    return bool(
        row.status == "VALIDATING"
        and row.validation_lease_owner == claim.owner
        and row.version == claim.claimed_version
    )


async def apply_registration_validation_success(
    session: AsyncSession,
    claim: ClaimedRegistration,
    result: RegistrationValidationResult,
    *,
    now: datetime,
) -> bool:
    row = await session.get(RegistrationRecord, claim.registration_id, with_for_update=True)
    if row is None or not _claim_matches(row, claim):
        return False
    parent = await session.get(ParentAccount, row.parent_account_id, with_for_update=True)
    if parent is None:
        return False
    encoded = encode_renewal_session(result.renewal_session)
    row.verified_email = result.login_name
    row.awarded_points = result.balance_credits
    row.points_checked_at = now
    row.session_ciphertext = encrypt_secret(
        encoded,
        f"{row.registration_uuid}:registration_session",
    )
    row.video_token_ciphertext = encrypt_secret(
        result.token,
        f"{row.registration_uuid}:registration_video_token",
    )
    row.token_expires_at = result.token_expires_at
    row.status = "SUCCEEDED"
    row.validation_lease_owner = None
    row.validation_lease_until = None
    row.retry_after = None
    row.validation_error_code = None
    row.validation_error_message = None
    row.validation_finished_at = now
    row.version += 1
    settle_success(parent, row, result.balance_credits, now)
    return True


def registration_validation_retry_delay(
    attempt_count: int,
    settings: Settings,
) -> int:
    exponential = settings.registration_validation_retry_base_seconds * (
        2 ** max(attempt_count - 1, 0)
    )
    return min(exponential, settings.registration_validation_retry_max_seconds)


async def apply_registration_validation_failure(
    session: AsyncSession,
    claim: ClaimedRegistration,
    error: RegistrationValidationError,
    settings: Settings,
    *,
    now: datetime,
) -> bool:
    row = await session.get(RegistrationRecord, claim.registration_id, with_for_update=True)
    if row is None or not _claim_matches(row, claim):
        return False
    retry = (
        error.retryable and row.validation_attempts < settings.registration_validation_max_attempts
    )
    row.validation_lease_owner = None
    row.validation_lease_until = None
    row.validation_error_message = str(error)[:300]
    if retry:
        delay = max(
            error.retry_after_seconds or 0,
            registration_validation_retry_delay(row.validation_attempts, settings),
        )
        row.status = "VALIDATION_RETRY_WAIT"
        row.retry_after = now + timedelta(seconds=delay)
        row.validation_error_code = error.code
    else:
        row.status = "VALIDATION_FAILED"
        row.retry_after = None
        row.validation_finished_at = now
        row.validation_error_code = (
            "VALIDATION_RETRIES_EXHAUSTED" if error.retryable else error.code
        )
        if not error.retryable:
            row.session_ciphertext = None
            row.video_token_ciphertext = None
            row.token_expires_at = None
    row.version += 1
    return True


async def _process_and_apply(
    claim: ClaimedRegistration,
    upstream: Upstream,
    settings: Settings,
) -> None:
    try:
        result = await validate_registration(claim, renew_protocol_session, upstream, settings)
    except RegistrationValidationError as error:
        async with session_factory() as session, session.begin():
            await apply_registration_validation_failure(
                session, claim, error, settings, now=datetime.now(UTC).replace(tzinfo=None)
            )
        return
    except Exception:
        logger.exception(
            "registration validation failed",
            extra={"registration_uuid": claim.registration_uuid},
        )
        error = RegistrationValidationError(
            "REGISTRATION_VALIDATION_INTERNAL_ERROR",
            "Registration validation encountered an internal error",
            retryable=True,
        )
        async with session_factory() as session, session.begin():
            await apply_registration_validation_failure(
                session, claim, error, settings, now=datetime.now(UTC).replace(tzinfo=None)
            )
        return
    async with session_factory() as session, session.begin():
        await apply_registration_validation_success(
            session, claim, result, now=datetime.now(UTC).replace(tzinfo=None)
        )


async def registration_validation_loop(stop: asyncio.Event) -> None:
    settings = get_settings()
    owner = f"registration-validator-{uuid4()}"
    upstream = create_upstream(settings)
    try:
        while not stop.is_set():
            now = datetime.now(UTC).replace(tzinfo=None)
            async with session_factory() as session, session.begin():
                await expire_timed_out_registrations(
                    session,
                    now=now,
                    batch_size=settings.registration_validation_batch_size,
                )
                claims = await claim_registrations(
                    session,
                    owner=owner,
                    now=now,
                    batch_size=settings.registration_validation_batch_size,
                    lease_seconds=settings.registration_validation_lease_seconds,
                )
            if claims:
                await asyncio.gather(
                    *(_process_and_apply(claim, upstream, settings) for claim in claims)
                )
                continue
            try:
                await asyncio.wait_for(
                    stop.wait(), timeout=settings.registration_validation_poll_seconds
                )
            except TimeoutError:
                pass
    finally:
        await upstream.close()
