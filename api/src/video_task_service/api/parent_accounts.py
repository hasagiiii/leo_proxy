from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from video_task_service.auth import require_admin_key
from video_task_service.crypto import decrypt_secret, encrypt_secret
from video_task_service.db import session_dependency
from video_task_service.models import ParentAccount, RegistrationRecord
from video_task_service.parent_accounts import (
    ParentAccountImportRecord,
    parse_parent_account_import,
)
from video_task_service.schemas import (
    ParentAccountImportIssueView,
    ParentAccountImportRequest,
    ParentAccountImportResult,
    ParentAccountInvitationResultRequest,
    ParentAccountList,
    ParentAccountStats,
    ParentAccountView,
)

router = APIRouter(
    prefix="/parent-accounts",
    tags=["parent-accounts"],
    dependencies=[Depends(require_admin_key)],
)


def no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def parent_account_from_record(record: ParentAccountImportRecord) -> ParentAccount:
    parent_account_uuid = str(uuid4())
    now = _utc_now()
    return ParentAccount(
        parent_account_uuid=parent_account_uuid,
        email=record.email,
        password_encrypted=encrypt_secret(
            record.password,
            f"{parent_account_uuid}:parent_account_password",
        ),
        credential_key_version=1,
        invite_url=record.invite_url,
        invite_success_count=0,
        invite_failure_count=0,
        status="ACTIVE",
        consecutive_150_count=0,
        successful_settlement_sequence=0,
        legacy_invite_success_count=0,
        legacy_invite_failure_count=0,
        version=0,
        created_at=now,
        updated_at=now,
    )


def parent_account_view(
    parent_account: ParentAccount,
    *,
    running_registration_count: int = 0,
    traceable_registration_count: int = 0,
    promotable_registration_count: int = 0,
) -> ParentAccountView:
    return ParentAccountView(
        parent_account_uuid=UUID(parent_account.parent_account_uuid),
        email=parent_account.email,
        password=decrypt_secret(
            parent_account.password_encrypted,
            f"{parent_account.parent_account_uuid}:parent_account_password",
        ),
        invite_url=parent_account.invite_url,
        invite_success_count=parent_account.invite_success_count,
        invite_failure_count=parent_account.invite_failure_count,
        status=parent_account.status or "ACTIVE",
        consecutive_150_count=parent_account.consecutive_150_count or 0,
        exhausted_reason=parent_account.exhausted_reason,
        exhausted_at=parent_account.exhausted_at,
        legacy_invite_success_count=parent_account.legacy_invite_success_count or 0,
        legacy_invite_failure_count=parent_account.legacy_invite_failure_count or 0,
        running_registration_count=running_registration_count,
        traceable_registration_count=traceable_registration_count,
        promotable_registration_count=promotable_registration_count,
        version=parent_account.version or 0,
        created_at=parent_account.created_at,
        updated_at=parent_account.updated_at,
    )


@router.post("/import", response_model=ParentAccountImportResult)
async def import_parent_accounts(
    body: ParentAccountImportRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> ParentAccountImportResult:
    no_store(response)
    content = body.content.get_secret_value()
    initial = parse_parent_account_import(content)
    candidate_emails = [record.email for record in initial.records]
    try:
        async with session.begin():
            existing_emails: list[str] = []
            if candidate_emails:
                existing_emails = list(
                    await session.scalars(
                        select(ParentAccount.email).where(
                            ParentAccount.email.in_(candidate_emails)
                        )
                    )
                )
            parsed = parse_parent_account_import(content, existing_emails)
            parent_accounts = [
                parent_account_from_record(record) for record in parsed.records
            ]
            session.add_all(parent_accounts)
            await session.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PARENT_ACCOUNT_ALREADY_EXISTS",
                "message": "one or more parent accounts already exist",
            },
        ) from exc

    issues = [
        ParentAccountImportIssueView(
            line_number=issue.line_number,
            email=issue.email,
            code=issue.code,
            reason=issue.reason,
        )
        for issue in parsed.issues
    ]
    duplicates = sum(issue.code.startswith("DUPLICATE_") for issue in parsed.issues)
    requested = len(parsed.records) + len(parsed.issues)
    return ParentAccountImportResult(
        requested=requested,
        imported=len(parent_accounts),
        duplicates=duplicates,
        invalid=len(parsed.issues) - duplicates,
        blank_lines=parsed.blank_lines,
        issues=issues,
    )


@router.get("", response_model=ParentAccountList)
async def list_parent_accounts(
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
    search: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ParentAccountList:
    no_store(response)
    conditions = []
    if search and search.strip():
        needle = f"%{search.strip().lower()}%"
        conditions.append(ParentAccount.email.like(needle))
    total = int(
        await session.scalar(select(func.count(ParentAccount.id)).where(*conditions)) or 0
    )
    statement = (
        select(ParentAccount)
        .where(*conditions)
        .order_by(ParentAccount.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    items = list(await session.scalars(statement))
    counts: dict[int, tuple[int, int, int]] = {}
    if items:
        count_rows = await session.execute(
            select(
                RegistrationRecord.parent_account_id,
                func.sum(
                    RegistrationRecord.status.in_(
                        ["RUNNING", "COOKIE_REPORTED", "VALIDATING", "VALIDATION_RETRY_WAIT"]
                    )
                ),
                func.count(RegistrationRecord.id),
                func.sum(
                    (RegistrationRecord.status == "SUCCEEDED")
                    & RegistrationRecord.account_id.is_(None)
                ),
            )
            .where(RegistrationRecord.parent_account_id.in_([item.id for item in items]))
            .group_by(RegistrationRecord.parent_account_id)
        )
        counts = {
            int(row[0]): (int(row[1] or 0), int(row[2] or 0), int(row[3] or 0))
            for row in count_rows
        }
    return ParentAccountList(
        items=[
            parent_account_view(
                parent_account,
                running_registration_count=counts.get(parent_account.id, (0, 0, 0))[0],
                traceable_registration_count=counts.get(parent_account.id, (0, 0, 0))[1],
                promotable_registration_count=counts.get(parent_account.id, (0, 0, 0))[2],
            )
            for parent_account in items
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/stats", response_model=ParentAccountStats)
async def parent_account_stats(
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> ParentAccountStats:
    no_store(response)
    row = (
        await session.execute(
            select(
                func.count(ParentAccount.id),
                func.coalesce(func.sum(ParentAccount.invite_success_count), 0),
                func.coalesce(func.sum(ParentAccount.invite_failure_count), 0),
                func.sum(ParentAccount.status == "ACTIVE"),
                func.sum(ParentAccount.status == "EXHAUSTED"),
                func.coalesce(func.sum(ParentAccount.legacy_invite_success_count), 0),
                func.coalesce(func.sum(ParentAccount.legacy_invite_failure_count), 0),
                select(func.count(RegistrationRecord.id)).scalar_subquery(),
                select(func.count(RegistrationRecord.id))
                .where(
                    RegistrationRecord.status == "SUCCEEDED",
                    RegistrationRecord.account_id.is_(None),
                )
                .scalar_subquery(),
            )
        )
    ).one()
    return ParentAccountStats(
        total_parent_accounts=int(row[0] or 0),
        total_invite_successes=int(row[1] or 0),
        total_invite_failures=int(row[2] or 0),
        active_parent_accounts=int(row[3] or 0),
        exhausted_parent_accounts=int(row[4] or 0),
        legacy_invite_successes=int(row[5] or 0),
        legacy_invite_failures=int(row[6] or 0),
        traceable_registrations=int(row[7] or 0),
        promotable_registrations=int(row[8] or 0),
    )


async def _parent_account(
    session: AsyncSession,
    parent_account_uuid: UUID,
) -> ParentAccount:
    parent_account = await session.scalar(
        select(ParentAccount).where(
            ParentAccount.parent_account_uuid == str(parent_account_uuid)
        )
    )
    if parent_account is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "PARENT_ACCOUNT_NOT_FOUND",
                "message": "parent account was not found",
            },
        )
    return parent_account


@router.delete(
    "/{parent_account_uuid}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_parent_account(
    parent_account_uuid: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> None:
    no_store(response)
    async with session.begin():
        parent_account = await _parent_account(session, parent_account_uuid)
        registration_count = int(
            await session.scalar(
                select(func.count(RegistrationRecord.id)).where(
                    RegistrationRecord.parent_account_id == parent_account.id
                )
            )
            or 0
        )
        if registration_count:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "PARENT_ACCOUNT_HAS_REGISTRATIONS",
                    "message": "parent account has traceable registration records",
                },
            )
        await session.delete(parent_account)


def invitation_result_statement(parent_account_uuid: UUID, *, success: bool):
    values = (
        {
            "invite_success_count": ParentAccount.invite_success_count + 1,
        }
        if success
        else {
            "invite_failure_count": ParentAccount.invite_failure_count + 1,
        }
    )
    return (
        update(ParentAccount)
        .where(ParentAccount.parent_account_uuid == str(parent_account_uuid))
        .values(**values)
    )


@router.post(
    "/{parent_account_uuid}/invitation-result",
    response_model=ParentAccountView,
)
async def record_parent_account_invitation_result(
    parent_account_uuid: UUID,
    body: ParentAccountInvitationResultRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> ParentAccountView:
    no_store(response)
    del parent_account_uuid, body, session
    raise HTTPException(
        status_code=409,
        detail={
            "code": "PARENT_ACCOUNT_INVITATION_RESULT_RETIRED",
            "message": "use the traceable registration result callback",
        },
    )
