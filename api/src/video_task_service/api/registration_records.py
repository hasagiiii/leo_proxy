from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import Annotated
from urllib.parse import urlencode
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from video_task_service.account_session_import import (
    CookieAccountUpsertInput,
    upsert_cookie_session_account,
)
from video_task_service.auth import require_admin_key
from video_task_service.config import Settings, get_settings
from video_task_service.crypto import decrypt_secret
from video_task_service.db import session_dependency
from video_task_service.models import (
    Account,
    RegistrationPoolSettings,
    RegistrationRecord,
    Space,
)
from video_task_service.schemas import (
    RegistrationCookieExportRequest,
    RegistrationPoolSettingsPatch,
    RegistrationPoolSettingsView,
    RegistrationPromotionResult,
    RegistrationRecordList,
    RegistrationRecordView,
    RenewalCookie,
    RenewalSessionPayload,
    SuccessfulRegistrationRecordList,
)

router = APIRouter(
    tags=["registration-records"],
    dependencies=[Depends(require_admin_key)],
)
public_router = APIRouter(tags=["registration-cookie-exports"])

ZIP_ENTRY_DATE_TIME = (1980, 1, 1, 0, 0, 0)
LEODEV_LINKS_FILENAME = "leodev_links.txt"
EMAILS_FILENAME = "emails.txt"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _utf8_lines(values: list[str]) -> bytes:
    return ("\n".join(values) + "\n").encode("utf-8")


def _write_archive_entry(archive: ZipFile, filename: str, content: bytes) -> None:
    entry = ZipInfo(filename=filename, date_time=ZIP_ENTRY_DATE_TIME)
    entry.compress_type = ZIP_DEFLATED
    entry.external_attr = 0o600 << 16
    archive.writestr(entry, content)


def browser_cookie_export(cookie: RenewalCookie) -> dict[str, object]:
    exported: dict[str, object] = {"domain": cookie.domain}
    if cookie.expiration_date is not None:
        exported["expirationDate"] = cookie.expiration_date
    exported.update(
        {
            "hostOnly": not cookie.domain.startswith("."),
            "httpOnly": cookie.http_only,
            "name": cookie.name,
            "path": cookie.path,
            "sameSite": cookie.same_site,
            "secure": cookie.secure,
            "session": cookie.expiration_date is None,
            "storeId": "0",
            "value": cookie.value.get_secret_value(),
        }
    )
    return exported


@public_router.post(
    "/registration-cookies/export",
    response_class=Response,
    responses={
        200: {
            "content": {"application/json": {}, "application/zip": {}},
            "description": "Single Cookie JSON or batch ZIP attachment",
        },
        404: {"description": "Successful registration was not found"},
        409: {"description": "Saved registration Cookie is unavailable"},
    },
)
async def export_registration_cookie(
    body: RegistrationCookieExportRequest,
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> Response:
    requested_emails = body.requested_emails
    async with session.begin():
        rows = list(
            (
                await session.scalars(
                    select(RegistrationRecord)
                    .where(
                        RegistrationRecord.status == "SUCCEEDED",
                        or_(
                            func.lower(RegistrationRecord.email_snapshot).in_(requested_emails),
                            func.lower(RegistrationRecord.registered_email).in_(requested_emails),
                            func.lower(RegistrationRecord.verified_email).in_(requested_emails),
                        ),
                    )
                    .order_by(
                        RegistrationRecord.validation_finished_at.desc(),
                        RegistrationRecord.created_at.desc(),
                    )
                    .with_for_update()
                )
            ).all()
        )

        requested_set = set(requested_emails)
        rows_by_email: dict[str, RegistrationRecord] = {}
        for row in rows:
            for candidate in (
                row.email_snapshot,
                row.registered_email,
                row.verified_email,
            ):
                normalized = candidate.strip().lower() if candidate else None
                if normalized in requested_set and normalized not in rows_by_email:
                    rows_by_email[normalized] = row

        missing_emails = [email for email in requested_emails if email not in rows_by_email]
        if missing_emails:
            detail: dict[str, object] = {
                "code": (
                    "REGISTRATION_COOKIES_NOT_FOUND"
                    if body.is_batch
                    else "REGISTRATION_COOKIE_NOT_FOUND"
                ),
                "message": (
                    "one or more successful registrations were not found"
                    if body.is_batch
                    else "successful registration was not found"
                ),
            }
            if body.is_batch:
                detail["emails"] = missing_emails
            raise HTTPException(
                status_code=404,
                detail=detail,
            )

        contents: dict[str, bytes] = {}
        for email in requested_emails:
            row = rows_by_email[email]
            if row.session_ciphertext is None:
                detail = {
                    "code": "REGISTRATION_COOKIE_UNAVAILABLE",
                    "message": "saved registration Cookie is unavailable",
                }
                if body.is_batch:
                    detail["email"] = email
                raise HTTPException(status_code=409, detail=detail)
            try:
                material = json.loads(
                    decrypt_secret(
                        bytes(row.session_ciphertext),
                        f"{row.registration_uuid}:registration_session",
                    )
                )
                renewal_session = RenewalSessionPayload.model_validate(material)
            except (ValueError, json.JSONDecodeError, ValidationError) as exc:
                detail = {
                    "code": "REGISTRATION_COOKIE_INVALID",
                    "message": "saved registration Cookie is invalid",
                }
                if body.is_batch:
                    detail["email"] = email
                raise HTTPException(status_code=409, detail=detail) from exc

            payload = {
                "url": "https://app.leonardo.ai",
                "cookies": [browser_cookie_export(cookie) for cookie in renewal_session.cookies],
            }
            contents[email] = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")

        archive_content: bytes | None = None
        if body.is_batch:
            archive_buffer = BytesIO()
            with ZipFile(archive_buffer, mode="w", compression=ZIP_DEFLATED) as archive:
                for email in requested_emails:
                    _write_archive_entry(archive, f"{email}.json", contents[email])
                _write_archive_entry(
                    archive,
                    LEODEV_LINKS_FILENAME,
                    _utf8_lines(
                        [
                            f"https://leodev.app/?{urlencode({'email': email})}"
                            for email in requested_emails
                        ]
                    ),
                )
                _write_archive_entry(
                    archive,
                    EMAILS_FILENAME,
                    _utf8_lines(requested_emails),
                )
            archive_content = archive_buffer.getvalue()

        marked_row_ids: set[int] = set()
        for email in requested_emails:
            row = rows_by_email[email]
            row_identity = id(row)
            if row_identity not in marked_row_ids:
                marked_row_ids.add(row_identity)
                if not row.is_used:
                    row.is_used = True
                    row.version += 1

    common_headers = {
        "Cache-Control": "no-store",
        "Pragma": "no-cache",
        "X-Content-Type-Options": "nosniff",
    }
    if body.email is not None:
        return Response(
            content=contents[body.email],
            media_type="application/json",
            headers={
                **common_headers,
                "Content-Disposition": f'attachment; filename="{body.email}.json"',
            },
        )

    assert archive_content is not None
    points = {rows_by_email[email].awarded_points for email in requested_emails}
    point_label = str(next(iter(points))) if len(points) == 1 else "mixed"
    exported_at = _now().strftime("%Y%m%d-%H%M%S")
    filename = (
        f"leonardo-{point_label}-cookies-{len(requested_emails)}-unused-{exported_at}.zip"
    )
    return Response(
        content=archive_content,
        media_type="application/zip",
        headers={
            **common_headers,
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Exported-Count": str(len(requested_emails)),
        },
    )


def registration_record_view(row: RegistrationRecord) -> RegistrationRecordView:
    if row.status == "SUCCEEDED":
        cookie_status = "VERIFIED"
    elif row.status in {"COOKIE_REPORTED", "VALIDATING", "VALIDATION_RETRY_WAIT"}:
        cookie_status = "VALIDATING" if row.status != "COOKIE_REPORTED" else "RECEIVED"
    else:
        cookie_status = "INVALID"
    return RegistrationRecordView(
        registration_uuid=UUID(row.registration_uuid),
        parent_account_uuid=UUID(row.parent_account_uuid_snapshot),
        parent_email=row.parent_email_snapshot,
        email=row.email_snapshot,
        client_id=row.client_id,
        status=row.status,
        registered_email=row.registered_email,
        verified_email=row.verified_email,
        awarded_points=row.awarded_points,
        is_used=row.is_used,
        cookie_count=row.cookie_count,
        validation_attempts=row.validation_attempts,
        validation_error_code=row.validation_error_code,
        validation_error_message=row.validation_error_message,
        started_at=row.started_at,
        reported_at=row.reported_at,
        validation_finished_at=row.validation_finished_at,
        promoted_at=row.promoted_at,
        account_uuid=UUID(row.account_uuid_snapshot) if row.account_uuid_snapshot else None,
        promotable=row.status == "SUCCEEDED" and row.account_id is None,
        cookie_status=cookie_status,
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get(
    "/registration-records",
    response_model=SuccessfulRegistrationRecordList,
)
async def list_successful_registrations(
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
    search: str | None = None,
    is_used: bool | None = None,
    credits: int | None = Query(default=None, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> SuccessfulRegistrationRecordList:
    """List server-validated registrations for the dedicated success ledger."""

    _no_store(response)
    conditions: list[object] = [RegistrationRecord.status == "SUCCEEDED"]
    if is_used is not None:
        conditions.append(RegistrationRecord.is_used.is_(is_used))
    if credits is not None:
        conditions.append(RegistrationRecord.awarded_points == credits)
    if search and search.strip():
        needle = f"%{search.strip().lower()}%"
        conditions.append(
            or_(
                RegistrationRecord.email_snapshot.like(needle),
                RegistrationRecord.registered_email.like(needle),
                RegistrationRecord.verified_email.like(needle),
                RegistrationRecord.parent_email_snapshot.like(needle),
            )
        )
    unused_8500_count = int(
        await session.scalar(
            select(func.count(RegistrationRecord.id)).where(
                RegistrationRecord.status == "SUCCEEDED",
                RegistrationRecord.is_used.is_(False),
                RegistrationRecord.awarded_points == 8_500,
            )
        )
        or 0
    )
    total = int(
        await session.scalar(select(func.count(RegistrationRecord.id)).where(*conditions)) or 0
    )
    rows = list(
        await session.scalars(
            select(RegistrationRecord)
            .where(*conditions)
            .order_by(
                RegistrationRecord.validation_finished_at.desc(),
                RegistrationRecord.created_at.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
    )
    return SuccessfulRegistrationRecordList(
        items=[registration_record_view(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
        unused_8500_count=unused_8500_count,
    )


async def _record(
    session: AsyncSession,
    registration_uuid: UUID,
    *,
    lock: bool = False,
) -> RegistrationRecord:
    statement = select(RegistrationRecord).where(
        RegistrationRecord.registration_uuid == str(registration_uuid)
    )
    if lock:
        statement = statement.with_for_update()
    row = await session.scalar(statement)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "REGISTRATION_NOT_FOUND", "message": "registration was not found"},
        )
    return row


@router.get(
    "/parent-accounts/{parent_account_uuid}/registrations",
    response_model=RegistrationRecordList,
)
async def list_parent_registrations(
    parent_account_uuid: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
    status_filter: str | None = Query(default=None, alias="status"),
    search: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> RegistrationRecordList:
    _no_store(response)
    conditions: list[object] = [
        RegistrationRecord.parent_account_uuid_snapshot == str(parent_account_uuid)
    ]
    if status_filter:
        normalized = status_filter.strip().upper()
        if normalized == "PROMOTABLE":
            conditions.extend(
                [RegistrationRecord.status == "SUCCEEDED", RegistrationRecord.account_id.is_(None)]
            )
        elif normalized == "PROMOTED":
            conditions.append(RegistrationRecord.account_id.is_not(None))
        elif normalized == "VALIDATING":
            conditions.append(
                RegistrationRecord.status.in_(
                    ["COOKIE_REPORTED", "VALIDATING", "VALIDATION_RETRY_WAIT"]
                )
            )
        else:
            conditions.append(RegistrationRecord.status == normalized)
    if search and search.strip():
        needle = f"%{search.strip().lower()}%"
        conditions.append(
            or_(
                RegistrationRecord.email_snapshot.like(needle),
                RegistrationRecord.client_id.like(needle),
            )
        )
    total = int(
        await session.scalar(select(func.count(RegistrationRecord.id)).where(*conditions)) or 0
    )
    rows = list(
        await session.scalars(
            select(RegistrationRecord)
            .where(*conditions)
            .order_by(RegistrationRecord.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return RegistrationRecordList(
        items=[registration_record_view(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/registration-records/{registration_uuid}/revalidate",
    response_model=RegistrationRecordView,
)
async def revalidate_registration(
    registration_uuid: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> RegistrationRecordView:
    _no_store(response)
    async with session.begin():
        row = await _record(session, registration_uuid, lock=True)
        if row.status != "VALIDATION_FAILED" or row.session_ciphertext is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "REGISTRATION_REVALIDATION_UNAVAILABLE",
                    "message": "registration does not have a reusable saved session",
                },
            )
        row.status = "COOKIE_REPORTED"
        row.validation_attempts = 0
        row.validation_lease_owner = None
        row.validation_lease_until = None
        row.retry_after = _now()
        row.validation_error_code = None
        row.validation_error_message = None
        row.validation_finished_at = None
        row.version += 1
    return registration_record_view(row)


async def _settings(
    session: AsyncSession,
    *,
    lock: bool,
) -> RegistrationPoolSettings:
    row = await session.get(RegistrationPoolSettings, 1, with_for_update=lock)
    if row is None:
        timestamp = _now()
        row = RegistrationPoolSettings(
            id=1,
            target_space_id=None,
            default_max_concurrency=3,
            version=0,
            created_at=timestamp,
            updated_at=timestamp,
        )
        session.add(row)
        await session.flush()
    return row


async def _settings_view(
    session: AsyncSession,
    row: RegistrationPoolSettings,
) -> RegistrationPoolSettingsView:
    space = await session.get(Space, row.target_space_id) if row.target_space_id else None
    return RegistrationPoolSettingsView(
        target_space_uuid=UUID(space.space_uuid) if space else None,
        target_space_name=space.name if space else None,
        target_space_status=space.status if space else None,
        default_max_concurrency=row.default_max_concurrency,
        promotion_available=space is not None and space.status == "ACTIVE",
        version=row.version,
        updated_at=row.updated_at,
    )


@router.get("/registration-settings", response_model=RegistrationPoolSettingsView)
async def get_registration_settings(
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> RegistrationPoolSettingsView:
    _no_store(response)
    async with session.begin():
        row = await _settings(session, lock=False)
        view = await _settings_view(session, row)
    return view


@router.patch("/registration-settings", response_model=RegistrationPoolSettingsView)
async def patch_registration_settings(
    body: RegistrationPoolSettingsPatch,
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> RegistrationPoolSettingsView:
    _no_store(response)
    async with session.begin():
        row = await _settings(session, lock=True)
        if row.version != body.expected_version:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "REGISTRATION_SETTINGS_VERSION_CONFLICT",
                    "message": "registration settings changed",
                },
            )
        target: Space | None = None
        if body.target_space_uuid is not None:
            target = await session.scalar(
                select(Space)
                .where(Space.space_uuid == str(body.target_space_uuid))
                .with_for_update()
            )
            if target is None or target.status != "ACTIVE":
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "REGISTRATION_TARGET_SPACE_UNAVAILABLE",
                        "message": "target space must be active",
                    },
                )
        row.target_space_id = target.id if target else None
        row.default_max_concurrency = body.default_max_concurrency
        row.version += 1
        row.updated_at = _now()
        view = await _settings_view(session, row)
    return view


@router.post(
    "/registration-records/{registration_uuid}/promote",
    response_model=RegistrationPromotionResult,
)
async def promote_registration(
    registration_uuid: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RegistrationPromotionResult:
    _no_store(response)
    now = _now()
    async with session.begin():
        row = await _record(session, registration_uuid, lock=True)
        if row.account_id is not None:
            account = await session.get(Account, row.account_id)
            if account is None:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "REGISTRATION_ACCOUNT_LINK_INVALID",
                        "message": "linked account is missing",
                    },
                )
            space = await session.get(Space, account.space_id)
            assert space is not None
            return RegistrationPromotionResult(
                registration_uuid=registration_uuid,
                account_uuid=UUID(account.account_uuid),
                account_status=account.status,
                target_space_uuid=UUID(space.space_uuid),
                replayed=True,
            )
        if (
            row.status != "SUCCEEDED"
            or not row.verified_email
            or row.awarded_points is None
            or row.session_ciphertext is None
            or row.video_token_ciphertext is None
            or row.token_expires_at is None
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "REGISTRATION_NOT_PROMOTABLE",
                    "message": "registration is not server-verified",
                },
            )
        if row.token_expires_at <= now + timedelta(seconds=settings.token_guard_seconds):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "REGISTRATION_TOKEN_EXPIRED",
                    "message": "registration must be revalidated",
                },
            )
        pool = await _settings(session, lock=True)
        target = (
            await session.get(Space, pool.target_space_id, with_for_update=True)
            if pool.target_space_id
            else None
        )
        if target is None or target.status != "ACTIVE":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "REGISTRATION_TARGET_SPACE_UNAVAILABLE",
                    "message": "fixed target space is unavailable",
                },
            )
        duplicate = await session.scalar(
            select(Account).where(Account.login_name == row.verified_email).with_for_update()
        )
        if duplicate is not None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "REGISTRATION_ACCOUNT_ALREADY_EXISTS",
                    "message": "an account with this login already exists",
                },
            )
        try:
            material = json.loads(
                decrypt_secret(
                    bytes(row.session_ciphertext),
                    f"{row.registration_uuid}:registration_session",
                )
            )
            renewal_session = RenewalSessionPayload.model_validate(material)
            token = decrypt_secret(
                bytes(row.video_token_ciphertext),
                f"{row.registration_uuid}:registration_video_token",
            )
        except (ValueError, json.JSONDecodeError, ValidationError) as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "REGISTRATION_SESSION_INVALID",
                    "message": "saved registration session is invalid",
                },
            ) from exc
        created = await upsert_cookie_session_account(
            session,
            CookieAccountUpsertInput(
                space_name=target.name,
                login_name=row.verified_email,
                token=token,
                token_expires_at=row.token_expires_at,
                balance_credits=row.awarded_points,
                renewal_session=renewal_session,
                max_concurrency=pool.default_max_concurrency,
            ),
            settings,
            now=now,
        )
        if created.action != "CREATED":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "REGISTRATION_ACCOUNT_ALREADY_EXISTS",
                    "message": "promotion did not create a new account",
                },
            )
        account = await session.scalar(
            select(Account).where(Account.account_uuid == created.account_uuid)
        )
        assert account is not None
        row.account_id = account.id
        row.account_uuid_snapshot = account.account_uuid
        row.promoted_at = now
        row.video_token_ciphertext = None
        row.session_ciphertext = None
        row.version += 1
        return RegistrationPromotionResult(
            registration_uuid=registration_uuid,
            account_uuid=UUID(account.account_uuid),
            account_status=account.status,
            target_space_uuid=UUID(target.space_uuid),
            replayed=False,
        )
