from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from video_task_service.auth import require_api_key
from video_task_service.db import session_dependency
from video_task_service.project_mailbox_claims import (
    ProjectMailboxPoolExhausted,
    claim_mailbox_for_project,
    validate_idempotency_key,
)
from video_task_service.schemas import (
    ProjectMailboxClaimRequest,
    ProjectMailboxClaimResult,
)

router = APIRouter(
    prefix="/mailboxes",
    tags=["mailboxes"],
    dependencies=[Depends(require_api_key)],
)


@router.post(
    "/claim",
    response_model=ProjectMailboxClaimResult,
    status_code=status.HTTP_201_CREATED,
)
async def claim_project_mailbox(
    body: ProjectMailboxClaimRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key"),
    ] = None,
) -> ProjectMailboxClaimResult:
    response.headers["Cache-Control"] = "no-store"
    try:
        validated_key = validate_idempotency_key(idempotency_key or "")
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "IDEMPOTENCY_KEY_INVALID",
                "message": "Idempotency-Key must be 8-128 safe ASCII characters",
            },
            headers={"Cache-Control": "no-store"},
        ) from exc

    try:
        outcome = await claim_mailbox_for_project(
            session,
            body.project_name,
            validated_key,
        )
    except ProjectMailboxPoolExhausted as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "PROJECT_MAILBOX_POOL_EXHAUSTED",
                "message": "all active mailboxes have already been claimed by this project",
            },
            headers={"Cache-Control": "no-store"},
        ) from exc

    response.status_code = (
        status.HTTP_200_OK if outcome.replayed else status.HTTP_201_CREATED
    )
    return ProjectMailboxClaimResult(
        claim_uuid=outcome.claim.claim_uuid,
        project_name=outcome.project_name,
        mailbox_uuid=outcome.claim.mailbox_uuid_snapshot,
        email=outcome.claim.email_snapshot,
        claimed_at=outcome.claim.claimed_at,
        replayed=outcome.replayed,
    )
