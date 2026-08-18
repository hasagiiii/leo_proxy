from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from video_task_service.api.accounts import account_status_for_balance
from video_task_service.config import Settings
from video_task_service.crypto import encrypt_secret
from video_task_service.models import Account, Space
from video_task_service.protocol_renewal import store_renewal_session
from video_task_service.schemas import RenewalSessionPayload, normalize_datetime


@dataclass(frozen=True, slots=True)
class CookieAccountUpsertInput:
    space_name: str
    login_name: str
    token: str
    token_expires_at: datetime
    balance_credits: int
    renewal_session: RenewalSessionPayload
    max_concurrency: int


@dataclass(frozen=True, slots=True)
class CookieAccountUpsertResult:
    action: Literal["CREATED", "UPDATED"]
    account_uuid: str
    account_status: str


async def upsert_cookie_session_account(
    session: AsyncSession,
    payload: CookieAccountUpsertInput,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> CookieAccountUpsertResult:
    current_time = normalize_datetime(now or datetime.now(UTC))
    token_expires_at = normalize_datetime(payload.token_expires_at)
    assert current_time is not None
    assert token_expires_at is not None
    if token_expires_at <= current_time:
        raise ValueError("Cookie account token expiry must be in the future")

    login_name = payload.login_name.strip().lower()
    if not login_name:
        raise ValueError("Cookie account login name is required")
    space_name = payload.space_name.strip()
    if not space_name:
        raise ValueError("Cookie account space name is required")

    account = await session.scalar(
        select(Account).where(Account.login_name == login_name).with_for_update()
    )
    action: Literal["CREATED", "UPDATED"]
    if account is None:
        space = await session.scalar(
            select(Space).where(Space.name == space_name).with_for_update()
        )
        if space is None:
            space = Space(
                space_uuid=str(uuid4()),
                name=space_name,
                max_concurrency=max(10, settings.account_max_concurrency),
            )
            session.add(space)
            await session.flush()
        account_uuid = str(uuid4())
        marker = secrets.token_urlsafe(32)
        status, disabled_reason = account_status_for_balance(
            "PENDING_VALIDATION",
            token_expires_at,
            payload.balance_credits,
            now=current_time,
            low_balance_threshold=settings.low_balance_threshold,
            token_guard_seconds=settings.token_guard_seconds,
        )
        account = Account(
            account_uuid=account_uuid,
            space_id=space.id,
            login_name=login_name,
            credential_source="COOKIE_SESSION",
            password_ciphertext=encrypt_secret(marker, f"{account_uuid}:password"),
            video_token_ciphertext=encrypt_secret(
                payload.token,
                f"{account_uuid}:video_token",
            ),
            token_expires_at=token_expires_at,
            token_refreshed_at=current_time,
            balance_credits=payload.balance_credits,
            balance_synced_at=current_time,
            max_concurrency=payload.max_concurrency,
            status=status,
            disabled_reason=disabled_reason,
        )
        session.add(account)
        await session.flush()
        action = "CREATED"
    else:
        status, disabled_reason = account_status_for_balance(
            account.status,
            token_expires_at,
            payload.balance_credits,
            now=current_time,
            low_balance_threshold=settings.low_balance_threshold,
            token_guard_seconds=settings.token_guard_seconds,
        )
        account.video_token_ciphertext = encrypt_secret(
            payload.token,
            f"{account.account_uuid}:video_token",
        )
        account.token_expires_at = token_expires_at
        account.token_refreshed_at = current_time
        account.balance_credits = payload.balance_credits
        account.balance_synced_at = current_time
        account.status = status
        account.disabled_reason = disabled_reason
        account.last_error_code = None
        account.last_error_at = None
        account.version += 1
        action = "UPDATED"

    await store_renewal_session(session, account, payload.renewal_session)
    return CookieAccountUpsertResult(
        action=action,
        account_uuid=account.account_uuid,
        account_status=account.status,
    )
