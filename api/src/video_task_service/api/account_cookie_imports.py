from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from video_task_service.auth import require_admin_key
from video_task_service.cookie_import_archive import (
    CookieArchiveError,
    ParsedCookieImportArchive,
    parse_cookie_import_archive,
)
from video_task_service.crypto import encrypt_secret
from video_task_service.db import session_dependency
from video_task_service.models import (
    Account,
    AccountCookieImportBatch,
    AccountCookieImportItem,
    AccountRenewalSession,
    Space,
    Task,
)
from video_task_service.protocol_renewal import renewal_session_dict
from video_task_service.schemas import (
    CookieImportBatchList,
    CookieImportBatchView,
    CookieImportItemView,
    normalize_datetime,
)

router = APIRouter(
    prefix="/account-cookie-imports",
    tags=["account-cookie-imports"],
    dependencies=[Depends(require_admin_key)],
)


@dataclass(frozen=True, slots=True)
class PersistedCookieImport:
    batch: AccountCookieImportBatch
    space: Space
    items: tuple[AccountCookieImportItem, ...]
    replayed: bool


async def persist_cookie_import_batch(
    session: AsyncSession,
    *,
    parsed: ParsedCookieImportArchive,
    archive_filename: str,
    space_name: str,
    idempotency_key: str,
    now: datetime | None = None,
) -> PersistedCookieImport:
    current_time = normalize_datetime(now or datetime.now(UTC))
    assert current_time is not None
    normalized_space_name = space_name.strip()
    existing = await session.scalar(
        select(AccountCookieImportBatch)
        .where(AccountCookieImportBatch.idempotency_key == idempotency_key)
        .with_for_update()
    )
    if existing is not None:
        if existing.archive_sha256 != parsed.archive_sha256:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                headers={"Cache-Control": "no-store"},
                detail={
                    "code": "COOKIE_IMPORT_IDEMPOTENCY_CONFLICT",
                    "message": "Idempotency key belongs to another Cookie archive",
                },
            )
        space = await session.get(Space, existing.space_id)
        assert space is not None
        if space.name != normalized_space_name:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                headers={"Cache-Control": "no-store"},
                detail={
                    "code": "COOKIE_IMPORT_IDEMPOTENCY_CONFLICT",
                    "message": "Idempotency key belongs to another target space",
                },
            )
        items = tuple(
            await session.scalars(
                select(AccountCookieImportItem)
                .where(AccountCookieImportItem.batch_id == existing.id)
                .order_by(AccountCookieImportItem.id.asc())
            )
        )
        return PersistedCookieImport(existing, space, items, replayed=True)

    space = await session.scalar(
        select(Space).where(Space.name == normalized_space_name).with_for_update()
    )
    if space is None:
        space = Space(
            space_uuid=str(uuid4()),
            name=normalized_space_name,
            max_concurrency=10,
        )
        session.add(space)
        await session.flush()

    has_queued_item = any(item.renewal_session is not None for item in parsed.items)
    batch = AccountCookieImportBatch(
        batch_uuid=str(uuid4()),
        idempotency_key=idempotency_key,
        archive_filename=archive_filename,
        archive_sha256=parsed.archive_sha256,
        space_id=space.id,
        status="QUEUED" if has_queued_item else "FAILED",
        item_count=len(parsed.items),
        finished_at=None if has_queued_item else current_time,
        created_at=current_time,
        updated_at=current_time,
    )
    session.add(batch)
    await session.flush()

    rows: list[AccountCookieImportItem] = []
    for item in parsed.items:
        item_uuid = str(uuid4())
        is_valid = item.renewal_session is not None
        encoded_session = (
            json.dumps(
                renewal_session_dict(item.renewal_session),
                separators=(",", ":"),
                sort_keys=True,
            )
            if item.renewal_session is not None
            else None
        )
        row = AccountCookieImportItem(
            item_uuid=item_uuid,
            batch_id=batch.id,
            entry_name=item.entry_name,
            entry_sha256=item.entry_sha256,
            expected_login_name=item.expected_login_name,
            session_ciphertext=(
                encrypt_secret(encoded_session, f"{item_uuid}:cookie_import_session")
                if encoded_session is not None
                else None
            ),
            credential_key_version=1,
            status="QUEUED" if is_valid else "FAILED",
            stage="RECEIVED",
            attempt_count=0,
            retryable=is_valid,
            last_error_code=item.error_code,
            last_error_message=item.error_message,
            finished_at=None if is_valid else current_time,
            version=0,
            created_at=current_time,
            updated_at=current_time,
        )
        rows.append(row)
    session.add_all(rows)
    await session.flush()
    return PersistedCookieImport(batch, space, tuple(rows), replayed=False)


def cookie_import_batch_view(persisted: PersistedCookieImport) -> CookieImportBatchView:
    items = [
        CookieImportItemView(
            item_uuid=row.item_uuid,
            entry_name=row.entry_name,
            entry_sha256=row.entry_sha256,
            expected_login_name=row.expected_login_name,
            discovered_login_name=row.discovered_login_name,
            status=row.status,
            stage=row.stage,
            attempt_count=row.attempt_count,
            retryable=row.retryable,
            last_error_code=row.last_error_code,
            last_error_message=row.last_error_message,
            account_uuid=None,
            account_status=None,
            balance_credits=None,
            token_expires_at=None,
            renewal_status=None,
            activated_at=row.activated_at,
            finished_at=row.finished_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        for row in persisted.items
    ]
    return CookieImportBatchView(
        batch_uuid=persisted.batch.batch_uuid,
        status=persisted.batch.status,
        archive_filename=persisted.batch.archive_filename,
        archive_sha256=persisted.batch.archive_sha256,
        space_name=persisted.space.name,
        item_count=persisted.batch.item_count,
        queued=sum(row.status in {"QUEUED", "RETRY_WAIT"} for row in persisted.items),
        running=sum(row.status == "RUNNING" for row in persisted.items),
        created=sum(row.status == "CREATED" for row in persisted.items),
        updated=sum(row.status == "UPDATED" for row in persisted.items),
        failed=sum(row.status == "FAILED" for row in persisted.items),
        total_balance_credits=0,
        tasks_after_import=0,
        completed_tasks_after_import=0,
        failed_tasks_after_import=0,
        consumed_credits_after_import=0,
        created_at=persisted.batch.created_at,
        started_at=persisted.batch.started_at,
        finished_at=persisted.batch.finished_at,
        items=items,
    )


async def build_cookie_import_batch_view(
    session: AsyncSession,
    persisted: PersistedCookieImport,
) -> CookieImportBatchView:
    base = cookie_import_batch_view(persisted)
    hydrated_items: list[CookieImportItemView] = []
    total_balance_credits = 0
    task_conditions = []
    for row, item_view in zip(persisted.items, base.items, strict=True):
        account = (
            await session.get(Account, row.account_id) if row.account_id is not None else None
        )
        renewal = (
            await session.get(AccountRenewalSession, row.account_id)
            if row.account_id is not None
            else None
        )
        if account is not None:
            total_balance_credits += account.balance_credits
            if row.activated_at is not None:
                task_conditions.append(
                    (Task.account_id == account.id) & (Task.created_at >= row.activated_at)
                )
        hydrated_items.append(
            item_view.model_copy(
                update={
                    "account_uuid": UUID(account.account_uuid) if account is not None else None,
                    "account_status": account.status if account is not None else None,
                    "balance_credits": account.balance_credits if account is not None else None,
                    "token_expires_at": account.token_expires_at if account is not None else None,
                    "renewal_status": renewal.status if renewal is not None else None,
                }
            )
        )

    tasks_after_import = 0
    completed_tasks = 0
    failed_tasks = 0
    consumed_credits = 0
    if task_conditions:
        metrics = (
            await session.execute(
                select(
                    func.count(Task.id),
                    func.sum(case((Task.status == "COMPLETED", 1), else_=0)),
                    func.sum(
                        case((Task.status.in_(("FAILED", "SUBMIT_UNKNOWN")), 1), else_=0)
                    ),
                    func.sum(func.coalesce(Task.actual_credit_cost, 0)),
                ).where(or_(*task_conditions))
            )
        ).one()
        tasks_after_import = int(metrics[0] or 0)
        completed_tasks = int(metrics[1] or 0)
        failed_tasks = int(metrics[2] or 0)
        consumed_credits = int(metrics[3] or 0)
    return base.model_copy(
        update={
            "items": hydrated_items,
            "total_balance_credits": total_balance_credits,
            "tasks_after_import": tasks_after_import,
            "completed_tasks_after_import": completed_tasks,
            "failed_tasks_after_import": failed_tasks,
            "consumed_credits_after_import": consumed_credits,
        }
    )


async def load_persisted_cookie_import(
    session: AsyncSession,
    batch: AccountCookieImportBatch,
) -> PersistedCookieImport:
    space = await session.get(Space, batch.space_id)
    assert space is not None
    items = tuple(
        await session.scalars(
            select(AccountCookieImportItem)
            .where(AccountCookieImportItem.batch_id == batch.id)
            .order_by(AccountCookieImportItem.id.asc())
        )
    )
    return PersistedCookieImport(batch=batch, space=space, items=items, replayed=True)


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _archive_http_error(error: CookieArchiveError) -> HTTPException:
    entity_too_large = {
        "ARCHIVE_TOO_LARGE",
        "ARCHIVE_TOO_MANY_ENTRIES",
        "ARCHIVE_ENTRY_TOO_LARGE",
        "ARCHIVE_EXPANDED_TOO_LARGE",
        "ARCHIVE_COMPRESSION_RATIO",
    }
    return HTTPException(
        status_code=(
            status.HTTP_413_CONTENT_TOO_LARGE
            if error.code in entity_too_large
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        ),
        headers={"Cache-Control": "no-store"},
        detail={"code": error.code, "message": str(error)},
    )


@router.post("", response_model=CookieImportBatchView, status_code=status.HTTP_202_ACCEPTED)
async def create_cookie_import(
    response: Response,
    archive: Annotated[UploadFile, File()],
    space_name: Annotated[str, Form(min_length=1, max_length=128)],
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=128),
    ],
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> CookieImportBatchView:
    _no_store(response)
    content_type = str(archive.content_type or "").lower()
    if content_type not in {
        "application/zip",
        "application/x-zip-compressed",
        "application/octet-stream",
    }:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            headers={"Cache-Control": "no-store"},
            detail={
                "code": "ARCHIVE_CONTENT_TYPE_INVALID",
                "message": "Upload content type must identify ZIP content",
            },
        )
    raw_filename = str(archive.filename or "cookies.zip").replace("\\", "/")
    archive_filename = PurePosixPath(raw_filename).name[:255]
    try:
        archive.file.seek(0)
        parsed = parse_cookie_import_archive(
            archive.file,
            archive_filename,
            datetime.now(UTC),
        )
        async with session.begin():
            persisted = await persist_cookie_import_batch(
                session,
                parsed=parsed,
                archive_filename=archive_filename,
                space_name=space_name,
                idempotency_key=idempotency_key.strip(),
            )
    except CookieArchiveError as error:
        raise _archive_http_error(error) from error
    finally:
        await archive.close()
    return cookie_import_batch_view(persisted)


@router.get("", response_model=CookieImportBatchList)
async def list_cookie_imports(
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CookieImportBatchList:
    _no_store(response)
    total = int(await session.scalar(select(func.count(AccountCookieImportBatch.id))) or 0)
    batches = list(
        await session.scalars(
            select(AccountCookieImportBatch)
            .order_by(AccountCookieImportBatch.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    views: list[CookieImportBatchView] = []
    for batch in batches:
        persisted = await load_persisted_cookie_import(session, batch)
        view = await build_cookie_import_batch_view(session, persisted)
        views.append(view.model_copy(update={"items": []}))
    return CookieImportBatchList(batches=views, total=total, limit=limit, offset=offset)


@router.get("/{batch_uuid}", response_model=CookieImportBatchView)
async def get_cookie_import(
    batch_uuid: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> CookieImportBatchView:
    _no_store(response)
    batch = await session.scalar(
        select(AccountCookieImportBatch).where(
            AccountCookieImportBatch.batch_uuid == str(batch_uuid)
        )
    )
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            headers={"Cache-Control": "no-store"},
            detail={
                "code": "COOKIE_IMPORT_BATCH_NOT_FOUND",
                "message": "Cookie import batch was not found",
            },
        )
    persisted = await load_persisted_cookie_import(session, batch)
    return await build_cookie_import_batch_view(session, persisted)
