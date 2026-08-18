from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from video_task_service.auth import require_admin_key
from video_task_service.config import get_settings
from video_task_service.db import session_dependency
from video_task_service.models import Account, AccountRenewalSession, ProtocolRenewalEvent
from video_task_service.schemas import (
    ProtocolRenewalAccountList,
    ProtocolRenewalAccountView,
    ProtocolRenewalEventList,
    ProtocolRenewalEventView,
)

router = APIRouter(
    prefix="/protocol-renewals",
    tags=["protocol-renewals"],
    dependencies=[Depends(require_admin_key)],
)


def renewal_account_view(
    account: Account,
    renewal: AccountRenewalSession | None,
) -> ProtocolRenewalAccountView:
    now = datetime.now(UTC).replace(tzinfo=None)
    client_cutoff = now - timedelta(
        seconds=get_settings().protocol_renewal_client_session_max_age_seconds
    )
    return ProtocolRenewalAccountView(
        account_uuid=UUID(account.account_uuid),
        login_name=account.login_name,
        account_status=account.status,
        token_expires_at=account.token_expires_at,
        has_session=renewal is not None,
        status=renewal.status if renewal is not None else "UNCONFIGURED",
        attempt_count=renewal.attempt_count if renewal is not None else 0,
        lease_until=renewal.lease_until if renewal is not None else None,
        retry_after=renewal.retry_after if renewal is not None else None,
        fallback_after=renewal.fallback_after if renewal is not None else None,
        last_attempt_at=renewal.last_attempt_at if renewal is not None else None,
        last_success_at=renewal.last_success_at if renewal is not None else None,
        last_error_code=renewal.last_error_code if renewal is not None else None,
        previous_token_expires_at=(
            renewal.previous_token_expires_at if renewal is not None else None
        ),
        renewed_token_expires_at=(
            renewal.renewed_token_expires_at if renewal is not None else None
        ),
        client_reported_at=(renewal.client_reported_at if renewal is not None else None),
        client_version=renewal.client_version if renewal is not None else None,
        renewal_capability=(
            renewal.renewal_capability if renewal is not None else None
        ),
        client_session_fresh=(
            renewal is not None
            and renewal.client_reported_at is not None
            and renewal.client_reported_at > client_cutoff
        ),
    )


@router.get("/accounts", response_model=ProtocolRenewalAccountList)
async def list_protocol_renewal_accounts(
    session: Annotated[AsyncSession, Depends(session_dependency)],
    renewal_status: Annotated[str | None, Query(alias="status", max_length=32)] = None,
    error_code: Annotated[str | None, Query(max_length=64)] = None,
    has_session: Annotated[bool | None, Query()] = None,
    due_within_seconds: Annotated[int | None, Query(ge=0, le=604800)] = None,
    search: Annotated[str | None, Query(max_length=255)] = None,
    limit: Annotated[int, Query(ge=1, le=2000)] = 1000,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ProtocolRenewalAccountList:
    conditions = []
    normalized_status = renewal_status.upper() if renewal_status else None
    if normalized_status == "UNCONFIGURED":
        conditions.append(AccountRenewalSession.account_id.is_(None))
    elif normalized_status:
        conditions.append(AccountRenewalSession.status == normalized_status)
    if error_code:
        conditions.append(AccountRenewalSession.last_error_code == error_code.upper())
    if has_session is True:
        conditions.append(AccountRenewalSession.account_id.is_not(None))
    elif has_session is False:
        conditions.append(AccountRenewalSession.account_id.is_(None))
    if due_within_seconds is not None:
        cutoff = datetime.now(UTC).replace(tzinfo=None) + timedelta(
            seconds=due_within_seconds
        )
        conditions.extend(
            [Account.token_expires_at.is_not(None), Account.token_expires_at <= cutoff]
        )
    if search:
        pattern = f"%{search.strip()}%"
        conditions.append(
            or_(Account.login_name.like(pattern), Account.account_uuid.like(pattern))
        )

    base = (
        select(Account, AccountRenewalSession)
        .outerjoin(
            AccountRenewalSession,
            AccountRenewalSession.account_id == Account.id,
        )
        .where(*conditions)
    )
    total = int(
        await session.scalar(
            select(func.count(Account.id))
            .outerjoin(
                AccountRenewalSession,
                AccountRenewalSession.account_id == Account.id,
            )
            .where(*conditions)
        )
        or 0
    )
    rows = (
        await session.execute(
            base.order_by(
                case((Account.token_expires_at.is_(None), 1), else_=0),
                Account.token_expires_at.asc(),
                Account.id.asc(),
            )
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return ProtocolRenewalAccountList(
        items=[renewal_account_view(account, renewal) for account, renewal in rows],
        total=total,
    )


def renewal_event_view(event: ProtocolRenewalEvent) -> ProtocolRenewalEventView:
    return ProtocolRenewalEventView(
        event_uuid=UUID(event.event_uuid),
        account_uuid=UUID(event.account_uuid_snapshot),
        attempt_number=event.attempt_number,
        outcome=event.outcome,
        applied=event.applied,
        retryable=event.retryable,
        next_state=event.next_state,
        error_code=event.error_code,
        started_at=event.started_at,
        finished_at=event.finished_at,
        latency_ms=event.latency_ms,
        previous_token_expires_at=event.previous_token_expires_at,
        renewed_token_expires_at=event.renewed_token_expires_at,
    )


@router.get(
    "/accounts/{account_uuid}/events",
    response_model=ProtocolRenewalEventList,
)
async def list_protocol_renewal_events(
    account_uuid: UUID,
    session: Annotated[AsyncSession, Depends(session_dependency)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ProtocolRenewalEventList:
    normalized_uuid = str(account_uuid)
    account_exists = await session.scalar(
        select(func.count(Account.id)).where(Account.account_uuid == normalized_uuid)
    )
    if not account_exists:
        raise HTTPException(
            status_code=404,
            detail={"code": "ACCOUNT_NOT_FOUND", "message": "account does not exist"},
        )
    total = int(
        await session.scalar(
            select(func.count(ProtocolRenewalEvent.id)).where(
                ProtocolRenewalEvent.account_uuid_snapshot == normalized_uuid
            )
        )
        or 0
    )
    events = list(
        await session.scalars(
            select(ProtocolRenewalEvent)
            .where(ProtocolRenewalEvent.account_uuid_snapshot == normalized_uuid)
            .order_by(
                ProtocolRenewalEvent.finished_at.desc(),
                ProtocolRenewalEvent.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
    )
    return ProtocolRenewalEventList(
        items=[renewal_event_view(event) for event in events],
        total=total,
    )
