from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from video_task_service.auth import require_api_key
from video_task_service.config import Settings, get_settings
from video_task_service.db import session_dependency
from video_task_service.registration_records import (
    RegistrationClaimConflict,
    RegistrationLeaseExpired,
    RegistrationMailboxPoolExhausted,
    RegistrationNotFound,
    RegistrationParentPoolExhausted,
    RegistrationReportTokenInvalid,
    RegistrationResultConflict,
    claim_registration_job,
    get_registration_job_status,
    heartbeat_registration_job,
    record_registration_result,
    validate_registration_idempotency_key,
)
from video_task_service.schemas import (
    RegistrationJobClaimRequest,
    RegistrationJobClaimResult,
    RegistrationJobHeartbeatRequest,
    RegistrationJobHeartbeatResult,
    RegistrationJobResultRequest,
    RegistrationJobResultResponse,
    RegistrationJobStatusRequest,
    RegistrationJobStatusResult,
)

router = APIRouter(
    prefix="/registration-jobs",
    tags=["registration-jobs"],
    dependencies=[Depends(require_api_key)],
)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _idempotency_key(value: str | None) -> str:
    try:
        return validate_registration_idempotency_key(value or "")
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "IDEMPOTENCY_KEY_INVALID",
                "message": "Idempotency-Key must be 8-128 safe ASCII characters",
            },
            headers={"Cache-Control": "no-store"},
        ) from exc


@router.get("/preflight")
async def registration_preflight(response: Response) -> dict[str, str]:
    """Authenticate invitation clients without consuming a parent or mailbox."""

    _no_store(response)
    return {"status": "ready", "project_name": "Canvas"}


@router.post(
    "/claim",
    response_model=RegistrationJobClaimResult,
    status_code=status.HTTP_201_CREATED,
)
async def claim_registration(
    body: RegistrationJobClaimRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
    settings: Annotated[Settings, Depends(get_settings)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> RegistrationJobClaimResult:
    _no_store(response)
    try:
        outcome = await claim_registration_job(
            session,
            client_id=body.client_id,
            project_name=body.project_name,
            idempotency_key=_idempotency_key(idempotency_key),
            lease_seconds=settings.registration_job_lease_seconds,
            now=_now(),
        )
    except RegistrationParentPoolExhausted as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "REGISTRATION_PARENT_POOL_EXHAUSTED",
                "message": "no active parent account is available",
            },
            headers={"Cache-Control": "no-store"},
        ) from exc
    except RegistrationMailboxPoolExhausted as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "REGISTRATION_MAILBOX_POOL_EXHAUSTED",
                "message": "no unused active mailbox is available",
            },
            headers={"Cache-Control": "no-store"},
        ) from exc
    except RegistrationClaimConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "REGISTRATION_CLAIM_CONFLICT",
                "message": "idempotency key was already used for another project",
            },
            headers={"Cache-Control": "no-store"},
        ) from exc
    response.status_code = 200 if outcome.replayed else 201
    record = outcome.record
    return RegistrationJobClaimResult(
        registration_uuid=UUID(record.registration_uuid),
        parent_account_uuid=UUID(record.parent_account_uuid_snapshot),
        parent_email=record.parent_email_snapshot,
        invite_url=outcome.parent_invite_url,
        mailbox_uuid=UUID(record.mailbox_uuid_snapshot),
        email=record.email_snapshot,
        project_name=outcome.project_name,
        report_token=outcome.report_token,
        lease_expires_at=record.lease_expires_at,
        replayed=outcome.replayed,
    )


@router.post(
    "/{registration_uuid}/status",
    response_model=RegistrationJobStatusResult,
)
async def registration_status(
    registration_uuid: UUID,
    body: RegistrationJobStatusRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> RegistrationJobStatusResult:
    _no_store(response)
    try:
        record = await get_registration_job_status(
            session,
            registration_uuid=str(registration_uuid),
            client_id=body.client_id,
            report_token=body.report_token.get_secret_value(),
        )
    except RegistrationNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "REGISTRATION_NOT_FOUND", "message": "registration was not found"},
        ) from exc
    except RegistrationReportTokenInvalid as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "REGISTRATION_REPORT_TOKEN_INVALID",
                "message": "report access was rejected",
            },
        ) from exc
    return RegistrationJobStatusResult(
        registration_uuid=UUID(record.registration_uuid),
        status=record.status,
        awarded_points=record.awarded_points,
        validation_attempts=record.validation_attempts,
        validation_error_code=record.validation_error_code,
        validation_error_message=record.validation_error_message,
        validation_finished_at=record.validation_finished_at,
        updated_at=record.updated_at,
    )


@router.post(
    "/{registration_uuid}/heartbeat",
    response_model=RegistrationJobHeartbeatResult,
)
async def heartbeat_registration(
    registration_uuid: UUID,
    body: RegistrationJobHeartbeatRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RegistrationJobHeartbeatResult:
    _no_store(response)
    try:
        record = await heartbeat_registration_job(
            session,
            registration_uuid=str(registration_uuid),
            client_id=body.client_id,
            report_token=body.report_token.get_secret_value(),
            lease_seconds=settings.registration_job_lease_seconds,
            timeout_seconds=settings.registration_job_timeout_seconds,
            now=_now(),
        )
    except RegistrationNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "REGISTRATION_NOT_FOUND", "message": "registration was not found"},
        ) from exc
    except RegistrationReportTokenInvalid as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "REGISTRATION_REPORT_TOKEN_INVALID",
                "message": "report access was rejected",
            },
        ) from exc
    except RegistrationLeaseExpired as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "REGISTRATION_LEASE_EXPIRED",
                "message": "registration lease has expired",
            },
        ) from exc
    except RegistrationClaimConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "REGISTRATION_NOT_RUNNING",
                "message": "registration is no longer running",
            },
        ) from exc
    assert record.last_heartbeat_at is not None
    return RegistrationJobHeartbeatResult(
        registration_uuid=UUID(record.registration_uuid),
        status=record.status,
        lease_expires_at=record.lease_expires_at,
        last_heartbeat_at=record.last_heartbeat_at,
        version=record.version,
    )


@router.post(
    "/{registration_uuid}/result",
    response_model=RegistrationJobResultResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def report_registration_result(
    registration_uuid: UUID,
    body: RegistrationJobResultRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> RegistrationJobResultResponse:
    _no_store(response)
    try:
        outcome = await record_registration_result(
            session,
            registration_uuid=str(registration_uuid),
            body=body,
            idempotency_key=_idempotency_key(idempotency_key),
            now=_now(),
        )
    except RegistrationNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "REGISTRATION_NOT_FOUND", "message": "registration was not found"},
        ) from exc
    except RegistrationReportTokenInvalid as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "REGISTRATION_REPORT_TOKEN_INVALID",
                "message": "report access was rejected",
            },
        ) from exc
    except RegistrationResultConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "REGISTRATION_RESULT_CONFLICT",
                "message": "result conflicts with stored registration state",
            },
        ) from exc
    response.status_code = 200 if outcome.replayed else 202
    record = outcome.record
    return RegistrationJobResultResponse(
        registration_uuid=UUID(record.registration_uuid),
        status=record.status,
        registered_email=record.registered_email,
        cookie_count=record.cookie_count,
        reported_at=record.reported_at,
        replayed=outcome.replayed,
    )
