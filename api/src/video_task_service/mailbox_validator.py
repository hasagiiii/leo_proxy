from __future__ import annotations

import asyncio
import logging
import os
import socket
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import uuid4

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from video_task_service.config import get_settings
from video_task_service.crypto import decrypt_secret
from video_task_service.db import dispose_engine, session_factory
from video_task_service.logging_config import configure_logging
from video_task_service.mailbox_graph import (
    MailboxCredentialsInvalid,
    MailboxProviderRateLimited,
    MailboxProviderUnavailable,
    MicrosoftGraphMailboxClient,
)
from video_task_service.models import Mailbox

logger = logging.getLogger(__name__)


class MailboxValidationClient(Protocol):
    async def get_access_token(self, refresh_token: str, client_id: str) -> str: ...

    async def latest_message(self, access_token: str) -> object | None: ...


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def retry_delay_seconds(attempt: int) -> int:
    if attempt <= 1:
        return 60
    if attempt == 2:
        return 300
    return 900


def record_unexpected_failure(
    mailbox: Mailbox,
    error: Exception,
    *,
    now: datetime | None = None,
) -> None:
    checked_at = _naive_utc(now or datetime.now(UTC))
    mailbox.status = "PENDING_VALIDATION"
    mailbox.disabled_reason = None
    mailbox.validation_attempts += 1
    mailbox.next_validation_at = checked_at + timedelta(
        seconds=retry_delay_seconds(mailbox.validation_attempts)
    )
    mailbox.last_validated_at = checked_at
    mailbox.last_error_code = "MAILBOX_VALIDATION_INTERNAL_ERROR"
    mailbox.last_error_message = f"Unexpected {type(error).__name__}"[:1000]
    mailbox.validation_lease_owner = None
    mailbox.validation_lease_until = None
    mailbox.version += 1


async def validate_claimed_mailbox(
    mailbox: Mailbox,
    client: MailboxValidationClient,
    *,
    now: datetime | None = None,
) -> None:
    if mailbox.status == "MANUAL_DISABLED":
        mailbox.validation_lease_owner = None
        mailbox.validation_lease_until = None
        return
    checked_at = _naive_utc(now or datetime.now(UTC))
    associated_prefix = mailbox.mailbox_uuid
    client_id = decrypt_secret(
        mailbox.client_id_ciphertext,
        f"{associated_prefix}:mailbox_client_id",
    )
    refresh_token = decrypt_secret(
        mailbox.refresh_token_ciphertext,
        f"{associated_prefix}:mailbox_refresh_token",
    )
    try:
        access_token = await client.get_access_token(refresh_token, client_id)
        await client.latest_message(access_token)
    except MailboxCredentialsInvalid as exc:
        mailbox.status = "INVALID"
        mailbox.disabled_reason = "oauth_credentials_invalid"
        mailbox.validation_attempts += 1
        mailbox.next_validation_at = None
        mailbox.last_error_code = exc.code
        mailbox.last_error_message = str(exc)[:1000]
    except (MailboxProviderRateLimited, MailboxProviderUnavailable) as exc:
        mailbox.status = "PENDING_VALIDATION"
        mailbox.disabled_reason = None
        mailbox.validation_attempts += 1
        delay = retry_delay_seconds(mailbox.validation_attempts)
        if isinstance(exc, MailboxProviderRateLimited):
            delay = max(delay, exc.retry_after_seconds)
        mailbox.next_validation_at = checked_at + timedelta(seconds=delay)
        mailbox.last_error_code = exc.code
        mailbox.last_error_message = str(exc)[:1000]
    else:
        mailbox.status = "ACTIVE"
        mailbox.disabled_reason = None
        mailbox.validation_attempts = 0
        mailbox.next_validation_at = None
        mailbox.last_error_code = None
        mailbox.last_error_message = None
    mailbox.last_validated_at = checked_at
    mailbox.validation_lease_owner = None
    mailbox.validation_lease_until = None
    mailbox.version += 1


async def validate_mailboxes_concurrently(
    mailboxes: list[Mailbox],
    client: MailboxValidationClient,
    *,
    max_concurrency: int,
) -> None:
    if max_concurrency < 1:
        raise ValueError("max_concurrency must be at least 1")
    semaphore = asyncio.Semaphore(max_concurrency)

    async def validate_one(mailbox: Mailbox) -> None:
        async with semaphore:
            try:
                await validate_claimed_mailbox(mailbox, client)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception(
                    "Unexpected mailbox validation failure for %s",
                    mailbox.mailbox_uuid,
                )
                record_unexpected_failure(mailbox, exc)

    await asyncio.gather(*(validate_one(mailbox) for mailbox in mailboxes))


async def claim_mailboxes(
    session: AsyncSession,
    *,
    owner: str,
    now: datetime,
    batch_size: int,
    lease_seconds: int,
) -> list[Mailbox]:
    claimed_at = _naive_utc(now)
    async with session.begin():
        statement = (
            select(Mailbox)
            .where(
                Mailbox.status == "PENDING_VALIDATION",
                or_(
                    Mailbox.next_validation_at.is_(None),
                    Mailbox.next_validation_at <= claimed_at,
                ),
                or_(
                    Mailbox.validation_lease_until.is_(None),
                    Mailbox.validation_lease_until < claimed_at,
                ),
            )
            .order_by(Mailbox.next_validation_at.asc(), Mailbox.id.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        items = list(await session.scalars(statement))
        for mailbox in items:
            mailbox.validation_lease_owner = owner
            mailbox.validation_lease_until = claimed_at + timedelta(seconds=lease_seconds)
        await session.flush()
    return items


async def run_validator() -> None:
    settings = get_settings()
    owner = f"{socket.gethostname()}:{os.getpid()}:{uuid4()}"
    connection_limit = max(
        settings.mailbox_validation_batch_size,
        settings.mailbox_validation_concurrency,
    )
    async with httpx.AsyncClient(
        limits=httpx.Limits(
            max_connections=connection_limit,
            max_keepalive_connections=connection_limit,
        )
    ) as http:
        client = MicrosoftGraphMailboxClient(
            http,
            timeout_seconds=settings.mailbox_provider_timeout_seconds,
        )
        while True:
            async with session_factory() as session:
                mailboxes = await claim_mailboxes(
                    session,
                    owner=owner,
                    now=datetime.now(UTC),
                    batch_size=settings.mailbox_validation_batch_size,
                    lease_seconds=settings.mailbox_validation_lease_seconds,
                )
                await validate_mailboxes_concurrently(
                    mailboxes,
                    client,
                    max_concurrency=settings.mailbox_validation_concurrency,
                )
                if mailboxes:
                    async with session.begin():
                        await session.flush()
            if not mailboxes:
                await asyncio.sleep(settings.mailbox_validation_poll_seconds)


async def _main() -> None:
    try:
        await run_validator()
    finally:
        await dispose_engine()


if __name__ == "__main__":
    configure_logging()
    asyncio.run(_main())
