from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Protocol

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from video_task_service.auth import require_api_key
from video_task_service.config import get_settings
from video_task_service.crypto import decrypt_secret
from video_task_service.db import session_dependency
from video_task_service.mailbox_codes import extract_verification_code
from video_task_service.mailbox_graph import (
    GraphMessage,
    MailboxCredentialsInvalid,
    MailboxProviderRateLimited,
    MailboxProviderUnavailable,
    MicrosoftGraphMailboxClient,
)
from video_task_service.models import Mailbox, RegistrationRecord
from video_task_service.registration_records import (
    RegistrationReportTokenInvalid,
    _verify_report_access,
)
from video_task_service.schemas import (
    MailboxCodeQuery,
    MailboxCodeResult,
    RegistrationMailboxCodeQuery,
)

router = APIRouter(
    prefix="/mailbox-codes",
    tags=["mailbox-codes"],
    dependencies=[Depends(require_api_key)],
)

MAILBOX_CODE_LOOKBACK_SECONDS = 10 * 60
MAILBOX_CODE_POLL_SECONDS = 3.0


class MailboxCodeClient(Protocol):
    async def get_access_token(self, refresh_token: str, client_id: str) -> str: ...

    async def latest_message(self, access_token: str) -> GraphMessage | None: ...


class MailboxCodeTimeout(RuntimeError):
    pass


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def poll_mailbox_code(
    mailbox: Mailbox,
    client: MailboxCodeClient,
    *,
    timeout_seconds: int,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> MailboxCodeResult:
    client_id = decrypt_secret(
        mailbox.client_id_ciphertext,
        f"{mailbox.mailbox_uuid}:mailbox_client_id",
    )
    refresh_token = decrypt_secret(
        mailbox.refresh_token_ciphertext,
        f"{mailbox.mailbox_uuid}:mailbox_refresh_token",
    )
    access_token = await client.get_access_token(refresh_token, client_id)
    deadline = _aware_utc(now()) + timedelta(seconds=timeout_seconds)

    while True:
        current = _aware_utc(now())
        if current >= deadline:
            raise MailboxCodeTimeout("verification code polling timed out")
        message = await client.latest_message(access_token)
        if message is not None:
            cutoff = current - timedelta(seconds=MAILBOX_CODE_LOOKBACK_SECONDS)
            received_at = _aware_utc(message.received_at)
            if received_at >= cutoff:
                matched = extract_verification_code(
                    message.subject,
                    message.body_preview,
                    message.body_content,
                )
                if matched is not None:
                    mailbox.last_message_received_at = received_at.replace(tzinfo=None)
                    return MailboxCodeResult(
                        email=mailbox.email,
                        code=matched.code,
                        received_at=received_at,
                        subject=message.subject,
                        sender=message.sender,
                        message_id=message.message_id,
                        matched_by=matched.matched_by,
                    )
        remaining = (deadline - _aware_utc(now())).total_seconds()
        if remaining <= 0:
            raise MailboxCodeTimeout("verification code polling timed out")
        await sleep(min(MAILBOX_CODE_POLL_SECONDS, remaining))


@router.post("/query", response_model=MailboxCodeResult)
async def query_mailbox_code(
    body: MailboxCodeQuery,
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> MailboxCodeResult:
    response.headers["Cache-Control"] = "no-store"
    mailbox = await session.scalar(select(Mailbox).where(Mailbox.email == body.email))
    if mailbox is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "MAILBOX_NOT_FOUND", "message": "mailbox was not found"},
        )
    if mailbox.status != "ACTIVE":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "MAILBOX_NOT_ACTIVE",
                "message": f"mailbox status is {mailbox.status}",
            },
        )

    settings = get_settings()
    try:
        async with httpx.AsyncClient() as http:
            result = await poll_mailbox_code(
                mailbox,
                MicrosoftGraphMailboxClient(
                    http,
                    timeout_seconds=settings.mailbox_provider_timeout_seconds,
                ),
                timeout_seconds=body.timeout_seconds,
            )
    except MailboxCodeTimeout as exc:
        raise HTTPException(
            status_code=408,
            detail={
                "code": "MAILBOX_CODE_TIMEOUT",
                "message": "no verification code arrived before the deadline",
            },
        ) from exc
    except MailboxCredentialsInvalid as exc:
        mailbox.status = "INVALID"
        mailbox.disabled_reason = "oauth_credentials_invalid"
        mailbox.last_error_code = exc.code
        mailbox.last_error_message = str(exc)[:1000]
        mailbox.last_validated_at = datetime.now(UTC).replace(tzinfo=None)
        mailbox.version += 1
        await session.commit()
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": "mailbox credentials are invalid"},
        ) from exc
    except MailboxProviderRateLimited as exc:
        response.headers["Retry-After"] = str(exc.retry_after_seconds)
        raise HTTPException(
            status_code=503,
            detail={"code": exc.code, "message": "mail provider rate limited the request"},
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except MailboxProviderUnavailable as exc:
        raise HTTPException(
            status_code=502,
            detail={"code": exc.code, "message": "mail provider is unavailable"},
        ) from exc

    mailbox.version += 1
    await session.commit()
    return result


@router.post("/query-for-registration", response_model=MailboxCodeResult)
async def query_registration_mailbox_code(
    body: RegistrationMailboxCodeQuery,
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> MailboxCodeResult:
    """Resolve and poll the mailbox already leased by a registration job."""

    response.headers["Cache-Control"] = "no-store"
    record = await session.scalar(
        select(RegistrationRecord).where(
            RegistrationRecord.registration_uuid == str(body.registration_uuid),
            RegistrationRecord.client_id == body.client_id,
            RegistrationRecord.status == "RUNNING",
        )
    )
    if record is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "REGISTRATION_NOT_FOUND", "message": "registration was not found"},
        )
    try:
        _verify_report_access(
            record,
            client_id=body.client_id,
            report_token=body.report_token.get_secret_value(),
        )
    except RegistrationReportTokenInvalid as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "REGISTRATION_REPORT_TOKEN_INVALID",
                "message": "report access was rejected",
            },
        ) from exc

    mailbox = await session.get(Mailbox, record.mailbox_id) if record.mailbox_id else None
    if mailbox is None:
        mailbox = await session.scalar(
            select(Mailbox).where(Mailbox.mailbox_uuid == record.mailbox_uuid_snapshot)
        )
    if mailbox is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "MAILBOX_NOT_FOUND", "message": "mailbox was not found"},
        )
    if mailbox.status != "ACTIVE":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "MAILBOX_NOT_ACTIVE",
                "message": f"mailbox status is {mailbox.status}",
            },
        )

    settings = get_settings()
    try:
        async with httpx.AsyncClient() as http:
            result = await poll_mailbox_code(
                mailbox,
                MicrosoftGraphMailboxClient(
                    http,
                    timeout_seconds=settings.mailbox_provider_timeout_seconds,
                ),
                timeout_seconds=body.timeout_seconds,
            )
    except MailboxCodeTimeout as exc:
        raise HTTPException(
            status_code=408,
            detail={
                "code": "MAILBOX_CODE_TIMEOUT",
                "message": "no verification code arrived before the deadline",
            },
        ) from exc
    except MailboxCredentialsInvalid as exc:
        mailbox.status = "INVALID"
        mailbox.disabled_reason = "oauth_credentials_invalid"
        mailbox.last_error_code = exc.code
        mailbox.last_error_message = str(exc)[:1000]
        mailbox.last_validated_at = datetime.now(UTC).replace(tzinfo=None)
        mailbox.version += 1
        await session.commit()
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": "mailbox credentials are invalid"},
        ) from exc
    except MailboxProviderRateLimited as exc:
        response.headers["Retry-After"] = str(exc.retry_after_seconds)
        raise HTTPException(
            status_code=503,
            detail={"code": exc.code, "message": "mail provider rate limited the request"},
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except MailboxProviderUnavailable as exc:
        raise HTTPException(
            status_code=502,
            detail={"code": exc.code, "message": "mail provider is unavailable"},
        ) from exc

    mailbox.version += 1
    await session.commit()
    return result
