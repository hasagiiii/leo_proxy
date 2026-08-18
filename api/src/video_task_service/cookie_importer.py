from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from video_task_service.account_session_import import (
    CookieAccountUpsertInput,
    upsert_cookie_session_account,
)
from video_task_service.config import Settings, get_settings
from video_task_service.crypto import decrypt_secret
from video_task_service.db import session_factory
from video_task_service.models import (
    Account,
    AccountCookieImportBatch,
    AccountCookieImportItem,
    Space,
)
from video_task_service.protocol_renewal import (
    ProtocolRenewalError,
    ProtocolRenewalResult,
    renew_protocol_session,
)
from video_task_service.schemas import RenewalSessionPayload
from video_task_service.upstream import AccountValidation, Upstream, create_upstream

logger = logging.getLogger(__name__)


class ProtocolClient(Protocol):
    def __call__(self, **kwargs: Any) -> Awaitable[ProtocolRenewalResult]: ...


@dataclass(frozen=True, slots=True)
class ClaimedCookieImportItem:
    item_id: int
    item_uuid: str
    batch_id: int
    space_name: str
    expected_login_name: str | None
    session_ciphertext: bytes
    owner: str
    claimed_version: int
    attempt_count: int
    lease_until: datetime


@dataclass(frozen=True, slots=True)
class CookieImportAttemptResult:
    login_name: str
    token: str
    token_expires_at: datetime
    balance_credits: int
    renewal_session: RenewalSessionPayload


class CookieImportProcessingError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message[:300])
        self.code = code[:64]
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds

    @classmethod
    def from_protocol(cls, error: ProtocolRenewalError) -> CookieImportProcessingError:
        messages = {
            "PROTOCOL_SESSION_REVOKED": "Stored browser session was revoked",
            "PROTOCOL_WRONG_ACCOUNT": "Stored browser session belongs to another account",
            "PROTOCOL_IDENTITY_UNAVAILABLE": "Session endpoint returned no account identity",
            "PROTOCOL_RATE_LIMITED": "Session endpoint rate limited the request",
            "PROTOCOL_TIMEOUT": "Session endpoint timed out",
            "PROTOCOL_NETWORK_ERROR": "Session endpoint network request failed",
        }
        return cls(
            error.code,
            messages.get(error.code, "Session validation failed"),
            retryable=error.retryable,
            retry_after_seconds=error.retry_after_seconds,
        )


def cookie_import_retry_delay(attempt_count: int) -> int:
    delays = (60, 300, 900)
    return delays[min(max(attempt_count, 1), len(delays)) - 1]


def cookie_import_claim_statement(now: datetime, batch_size: int):  # type: ignore[no-untyped-def]
    return (
        select(AccountCookieImportItem)
        .where(
            or_(
                AccountCookieImportItem.status == "QUEUED",
                (
                    (AccountCookieImportItem.status == "RETRY_WAIT")
                    & (AccountCookieImportItem.retry_after.is_not(None))
                    & (AccountCookieImportItem.retry_after <= now)
                ),
                (
                    (AccountCookieImportItem.status == "RUNNING")
                    & (AccountCookieImportItem.lease_until.is_not(None))
                    & (AccountCookieImportItem.lease_until <= now)
                ),
            )
        )
        .order_by(AccountCookieImportItem.id.asc())
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )


async def claim_cookie_import_items(
    session: AsyncSession,
    owner: str,
    now: datetime,
    batch_size: int,
    lease_seconds: int,
) -> list[ClaimedCookieImportItem]:
    rows = list(await session.scalars(cookie_import_claim_statement(now, batch_size)))
    claims: list[ClaimedCookieImportItem] = []
    lease_until = now + timedelta(seconds=lease_seconds)
    for row in rows:
        if row.session_ciphertext is None:
            row.status = "FAILED"
            row.retryable = False
            row.last_error_code = "COOKIE_IMPORT_SESSION_MISSING"
            row.last_error_message = "Encrypted staging session is missing"
            row.finished_at = now
            continue
        batch = await session.get(
            AccountCookieImportBatch,
            row.batch_id,
            with_for_update=True,
        )
        assert batch is not None
        space = await session.get(Space, batch.space_id)
        assert space is not None
        row.status = "RUNNING"
        row.stage = "SESSION_VALIDATION"
        row.attempt_count += 1
        row.lease_owner = owner
        row.lease_until = lease_until
        row.retry_after = None
        row.version += 1
        batch.status = "RUNNING"
        batch.started_at = batch.started_at or now
        claims.append(
            ClaimedCookieImportItem(
                item_id=row.id,
                item_uuid=row.item_uuid,
                batch_id=row.batch_id,
                space_name=space.name,
                expected_login_name=row.expected_login_name,
                session_ciphertext=bytes(row.session_ciphertext),
                owner=owner,
                claimed_version=row.version,
                attempt_count=row.attempt_count,
                lease_until=lease_until,
            )
        )
    return claims


async def process_cookie_import_item(
    claim: ClaimedCookieImportItem,
    protocol_client: Callable[..., Awaitable[Any]],
    upstream: Upstream,
    settings: Settings,
) -> CookieImportAttemptResult:
    try:
        encoded = decrypt_secret(
            claim.session_ciphertext,
            f"{claim.item_uuid}:cookie_import_session",
        )
        material = json.loads(encoded)
        RenewalSessionPayload.model_validate(material)
    except (ValueError, json.JSONDecodeError, ValidationError) as error:
        raise CookieImportProcessingError(
            "COOKIE_IMPORT_SESSION_INVALID",
            "Encrypted staging session has an invalid shape",
            retryable=False,
        ) from error

    try:
        protocol_result = await protocol_client(
            material=material,
            stored_token="",
            expected_email=claim.expected_login_name,
            settings=settings,
        )
    except ProtocolRenewalError as error:
        raise CookieImportProcessingError.from_protocol(error) from error

    session_email = str(protocol_result.session_email or "").strip().lower()
    if not session_email:
        raise CookieImportProcessingError(
            "PROTOCOL_IDENTITY_UNAVAILABLE",
            "Session endpoint returned no account identity",
            retryable=False,
        )
    if claim.expected_login_name and claim.expected_login_name.strip().lower() != session_email:
        raise CookieImportProcessingError(
            "COOKIE_IMPORT_IDENTITY_MISMATCH",
            "Archive filename and session identity do not match",
            retryable=False,
        )

    validation: AccountValidation = await upstream.validate_account(token=protocol_result.token)
    if not validation.valid:
        code = validation.error_code or "UPSTREAM_ACCOUNT_VALIDATION_FAILED"
        retryable = code in {
            "UPSTREAM_SERVER_ERROR",
            "UPSTREAM_NETWORK_ERROR",
            "UPSTREAM_RATE_LIMITED",
        }
        raise CookieImportProcessingError(
            code,
            "Balance validation failed",
            retryable=retryable,
        )
    graphql_email = str(validation.login_name or "").strip().lower()
    if not graphql_email or validation.balance_credits is None:
        raise CookieImportProcessingError(
            "UPSTREAM_IDENTITY_OR_BALANCE_MISSING",
            "Balance response omitted identity or credits",
            retryable=False,
        )
    if graphql_email != session_email:
        raise CookieImportProcessingError(
            "COOKIE_IMPORT_IDENTITY_MISMATCH",
            "Session and balance identities do not match",
            retryable=False,
        )
    try:
        renewed_session = RenewalSessionPayload.model_validate(protocol_result.renewal_session)
    except ValidationError as error:
        raise CookieImportProcessingError(
            "PROTOCOL_SESSION_ROTATION_INVALID",
            "Rotated session has an invalid shape",
            retryable=False,
        ) from error
    return CookieImportAttemptResult(
        login_name=session_email,
        token=protocol_result.token,
        token_expires_at=protocol_result.token_expires_at,
        balance_credits=validation.balance_credits,
        renewal_session=renewed_session,
    )


def _claim_matches(row: AccountCookieImportItem, claim: ClaimedCookieImportItem) -> bool:
    return bool(
        row.status == "RUNNING"
        and row.lease_owner == claim.owner
        and row.version == claim.claimed_version
    )


async def _refresh_batch_status(
    session: AsyncSession,
    batch_id: int,
    now: datetime,
) -> None:
    batch = await session.get(AccountCookieImportBatch, batch_id, with_for_update=True)
    assert batch is not None
    statuses = list(
        await session.scalars(
            select(AccountCookieImportItem.status).where(
                AccountCookieImportItem.batch_id == batch_id
            )
        )
    )
    active = {"QUEUED", "RUNNING", "RETRY_WAIT"}
    successful = {"CREATED", "UPDATED", "SKIPPED_DUPLICATE"}
    if any(value in active for value in statuses):
        batch.status = "RUNNING"
        return
    failed = sum(value == "FAILED" for value in statuses)
    succeeded = sum(value in successful for value in statuses)
    batch.status = (
        "PARTIAL_FAILED" if failed and succeeded else "FAILED" if failed else "COMPLETED"
    )
    batch.finished_at = now


async def apply_cookie_import_success(
    session: AsyncSession,
    claim: ClaimedCookieImportItem,
    result: CookieImportAttemptResult,
    settings: Settings,
    *,
    now: datetime,
) -> bool:
    row = await session.get(AccountCookieImportItem, claim.item_id, with_for_update=True)
    if row is None or not _claim_matches(row, claim):
        return False
    upserted = await upsert_cookie_session_account(
        session,
        CookieAccountUpsertInput(
            space_name=claim.space_name,
            login_name=result.login_name,
            token=result.token,
            token_expires_at=result.token_expires_at,
            balance_credits=result.balance_credits,
            renewal_session=result.renewal_session,
            max_concurrency=settings.account_max_concurrency,
        ),
        settings,
        now=now,
    )
    account = await session.scalar(
        select(Account).where(Account.account_uuid == upserted.account_uuid)
    )
    assert account is not None
    row.account_id = account.id
    row.discovered_login_name = result.login_name
    row.session_ciphertext = None
    row.status = upserted.action
    row.stage = "RENEWAL_READY"
    row.retryable = False
    row.lease_owner = None
    row.lease_until = None
    row.retry_after = None
    row.last_error_code = None
    row.last_error_message = None
    row.activated_at = now
    row.finished_at = now
    row.version += 1
    await session.flush()
    await _refresh_batch_status(session, claim.batch_id, now)
    return True


async def apply_cookie_import_failure(
    session: AsyncSession,
    claim: ClaimedCookieImportItem,
    error: CookieImportProcessingError,
    settings: Settings,
    *,
    now: datetime,
) -> bool:
    row = await session.get(AccountCookieImportItem, claim.item_id, with_for_update=True)
    if row is None or not _claim_matches(row, claim):
        return False
    should_retry = error.retryable and row.attempt_count < settings.cookie_import_max_attempts
    row.lease_owner = None
    row.lease_until = None
    row.last_error_code = error.code
    row.last_error_message = str(error)[:300]
    row.retryable = should_retry
    if should_retry:
        delay = error.retry_after_seconds or cookie_import_retry_delay(row.attempt_count)
        if error.code == "PROTOCOL_RATE_LIMITED":
            delay = max(delay, 300)
        row.status = "RETRY_WAIT"
        row.retry_after = now + timedelta(seconds=delay)
    else:
        row.status = "FAILED"
        row.retry_after = None
        row.session_ciphertext = None
        row.finished_at = now
    row.version += 1
    await session.flush()
    await _refresh_batch_status(session, claim.batch_id, now)
    return True


async def _process_and_apply(
    claim: ClaimedCookieImportItem,
    upstream: Upstream,
    settings: Settings,
) -> None:
    try:
        result = await process_cookie_import_item(
            claim,
            renew_protocol_session,
            upstream,
            settings,
        )
    except CookieImportProcessingError as error:
        async with session_factory() as session, session.begin():
            await apply_cookie_import_failure(
                session,
                claim,
                error,
                settings,
                now=datetime.now(UTC).replace(tzinfo=None),
            )
        return
    except Exception:
        logger.exception(
            "cookie import item processing failed",
            extra={"item_uuid": claim.item_uuid},
        )
        generic = CookieImportProcessingError(
            "COOKIE_IMPORT_INTERNAL_ERROR",
            "Cookie import processing encountered an internal error",
            retryable=True,
        )
        async with session_factory() as session, session.begin():
            await apply_cookie_import_failure(
                session,
                claim,
                generic,
                settings,
                now=datetime.now(UTC).replace(tzinfo=None),
            )
        return
    async with session_factory() as session, session.begin():
        await apply_cookie_import_success(
            session,
            claim,
            result,
            settings,
            now=datetime.now(UTC).replace(tzinfo=None),
        )


async def cookie_import_loop(stop: asyncio.Event) -> None:
    settings = get_settings()
    owner = f"cookie-importer-{uuid4()}"
    upstream = create_upstream(settings)
    try:
        while not stop.is_set():
            now = datetime.now(UTC).replace(tzinfo=None)
            async with session_factory() as session, session.begin():
                claims = await claim_cookie_import_items(
                    session,
                    owner,
                    now,
                    settings.cookie_import_batch_size,
                    settings.cookie_import_lease_seconds,
                )
            if claims:
                await asyncio.gather(
                    *(_process_and_apply(claim, upstream, settings) for claim in claims)
                )
                continue
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.cookie_import_poll_seconds)
            except TimeoutError:
                pass
    finally:
        await upstream.close()
