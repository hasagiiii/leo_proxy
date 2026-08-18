from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from video_task_service.auth import require_admin_key
from video_task_service.crypto import encrypt_secret
from video_task_service.db import session_dependency
from video_task_service.mailbox_codes import MailboxImportRecord, parse_mailbox_import
from video_task_service.models import Mailbox
from video_task_service.schemas import (
    MailboxImportIssueView,
    MailboxImportRequest,
    MailboxImportResult,
    MailboxList,
    MailboxPatch,
    MailboxStats,
    MailboxView,
)

router = APIRouter(
    prefix="/mailboxes",
    tags=["mailboxes"],
    dependencies=[Depends(require_admin_key)],
)

MailboxImportPeriod = Literal["today", "yesterday", "recent_7d", "older"]


def no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def mailbox_import_window(
    import_period: MailboxImportPeriod,
    timezone_offset_minutes: int,
    *,
    now: datetime | None = None,
) -> tuple[datetime | None, datetime | None]:
    """Return a half-open UTC window for mutually exclusive local-day buckets."""
    current_utc = now or _utc_now()
    if current_utc.tzinfo is not None:
        current_utc = current_utc.astimezone(UTC).replace(tzinfo=None)
    offset = timedelta(minutes=timezone_offset_minutes)
    local_today = (current_utc + offset).date()
    today_start = datetime.combine(local_today, time.min) - offset
    if import_period == "today":
        return today_start, today_start + timedelta(days=1)
    if import_period == "yesterday":
        return today_start - timedelta(days=1), today_start
    if import_period == "recent_7d":
        return today_start - timedelta(days=7), today_start - timedelta(days=1)
    return None, today_start - timedelta(days=7)


def mailbox_from_record(record: MailboxImportRecord) -> Mailbox:
    mailbox_uuid = str(uuid4())
    now = _utc_now()
    return Mailbox(
        mailbox_uuid=mailbox_uuid,
        email=record.email,
        password_ciphertext=encrypt_secret(
            record.password,
            f"{mailbox_uuid}:mailbox_password",
        ),
        client_id_ciphertext=encrypt_secret(
            record.client_id,
            f"{mailbox_uuid}:mailbox_client_id",
        ),
        refresh_token_ciphertext=encrypt_secret(
            record.refresh_token,
            f"{mailbox_uuid}:mailbox_refresh_token",
        ),
        credential_key_version=1,
        status="PENDING_VALIDATION",
        validation_attempts=0,
        next_validation_at=now,
        version=0,
        created_at=now,
        updated_at=now,
    )


def mailbox_view(mailbox: Mailbox) -> MailboxView:
    return MailboxView(
        mailbox_uuid=UUID(mailbox.mailbox_uuid),
        email=mailbox.email,
        status=mailbox.status,  # type: ignore[arg-type]
        disabled_reason=mailbox.disabled_reason,
        validation_attempts=mailbox.validation_attempts,
        next_validation_at=mailbox.next_validation_at,
        last_validated_at=mailbox.last_validated_at,
        last_error_code=mailbox.last_error_code,
        last_error_message=mailbox.last_error_message,
        last_message_received_at=mailbox.last_message_received_at,
        version=mailbox.version,
        created_at=mailbox.created_at,
        updated_at=mailbox.updated_at,
    )


@router.post("/import", response_model=MailboxImportResult)
async def import_mailboxes(
    body: MailboxImportRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> MailboxImportResult:
    no_store(response)
    content = body.content.get_secret_value()
    initial = parse_mailbox_import(content)
    candidate_emails = [record.email for record in initial.records]
    try:
        async with session.begin():
            existing_emails: list[str] = []
            if candidate_emails:
                existing_emails = list(
                    await session.scalars(
                        select(Mailbox.email).where(Mailbox.email.in_(candidate_emails))
                    )
                )
            parsed = parse_mailbox_import(content, existing_emails)
            mailboxes = [mailbox_from_record(record) for record in parsed.records]
            session.add_all(mailboxes)
            await session.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "MAILBOX_ALREADY_EXISTS",
                "message": "one or more mailboxes already exist",
            },
        ) from exc

    issues = [
        MailboxImportIssueView(
            line_number=issue.line_number,
            email=issue.email,
            code=issue.code,
            reason=issue.reason,
        )
        for issue in parsed.issues
    ]
    duplicates = sum(issue.code.startswith("DUPLICATE_") for issue in parsed.issues)
    requested = len(parsed.records) + len(parsed.issues)
    return MailboxImportResult(
        requested=requested,
        imported=len(mailboxes),
        duplicates=duplicates,
        invalid=len(parsed.issues) - duplicates,
        blank_lines=parsed.blank_lines,
        issues=issues,
    )


@router.get("", response_model=MailboxList)
async def list_mailboxes(
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
    mailbox_status: Annotated[str | None, Query(alias="status")] = None,
    search: str | None = None,
    import_period: Annotated[MailboxImportPeriod | None, Query()] = None,
    timezone_offset_minutes: Annotated[int, Query(ge=-720, le=840)] = 0,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> MailboxList:
    no_store(response)
    conditions = []
    if mailbox_status:
        conditions.append(Mailbox.status == mailbox_status.upper())
    if search and search.strip():
        needle = f"%{search.strip().lower()}%"
        conditions.append(or_(Mailbox.email.like(needle), Mailbox.mailbox_uuid.like(needle)))
    if import_period:
        created_from, created_before = mailbox_import_window(
            import_period,
            timezone_offset_minutes,
        )
        if created_from is not None:
            conditions.append(Mailbox.created_at >= created_from)
        if created_before is not None:
            conditions.append(Mailbox.created_at < created_before)
    total = int(
        await session.scalar(select(func.count(Mailbox.id)).where(*conditions)) or 0
    )
    statement = (
        select(Mailbox)
        .where(*conditions)
        .order_by(Mailbox.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    items = list(await session.scalars(statement))
    return MailboxList(
        items=[mailbox_view(mailbox) for mailbox in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/stats", response_model=MailboxStats)
async def mailbox_stats(
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> MailboxStats:
    no_store(response)
    rows = (
        await session.execute(
            select(Mailbox.status, func.count(Mailbox.id)).group_by(Mailbox.status)
        )
    ).all()
    counts = {str(item_status): int(count) for item_status, count in rows}
    return MailboxStats(
        total=sum(counts.values()),
        pending_validation=counts.get("PENDING_VALIDATION", 0),
        active=counts.get("ACTIVE", 0),
        invalid=counts.get("INVALID", 0),
        manual_disabled=counts.get("MANUAL_DISABLED", 0),
    )


async def _locked_mailbox(session: AsyncSession, mailbox_uuid: UUID) -> Mailbox:
    mailbox = await session.scalar(
        select(Mailbox)
        .where(Mailbox.mailbox_uuid == str(mailbox_uuid))
        .with_for_update()
    )
    if mailbox is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "MAILBOX_NOT_FOUND", "message": "mailbox was not found"},
        )
    return mailbox


@router.post("/{mailbox_uuid}/revalidate", response_model=MailboxView)
async def revalidate_mailbox(
    mailbox_uuid: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> MailboxView:
    no_store(response)
    async with session.begin():
        mailbox = await _locked_mailbox(session, mailbox_uuid)
        mailbox.status = "PENDING_VALIDATION"
        mailbox.disabled_reason = None
        mailbox.validation_lease_owner = None
        mailbox.validation_lease_until = None
        mailbox.next_validation_at = _utc_now()
        mailbox.version += 1
        await session.flush()
    return mailbox_view(mailbox)


@router.patch("/{mailbox_uuid}", response_model=MailboxView)
async def patch_mailbox(
    mailbox_uuid: UUID,
    body: MailboxPatch,
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> MailboxView:
    no_store(response)
    async with session.begin():
        mailbox = await _locked_mailbox(session, mailbox_uuid)
        if mailbox.version != body.expected_version:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "MAILBOX_VERSION_CONFLICT",
                    "message": "mailbox was modified by another request",
                },
            )
        mailbox.status = body.manual_status
        mailbox.disabled_reason = (
            "manual_disabled" if body.manual_status == "MANUAL_DISABLED" else None
        )
        mailbox.validation_lease_owner = None
        mailbox.validation_lease_until = None
        mailbox.next_validation_at = (
            _utc_now() if body.manual_status == "PENDING_VALIDATION" else None
        )
        mailbox.version += 1
        await session.flush()
    return mailbox_view(mailbox)


@router.delete("/{mailbox_uuid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mailbox(
    mailbox_uuid: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> None:
    no_store(response)
    async with session.begin():
        mailbox = await _locked_mailbox(session, mailbox_uuid)
        await session.delete(mailbox)
