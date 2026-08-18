from __future__ import annotations

import asyncio

import httpx
from fastapi import APIRouter, HTTPException, Response

from video_task_service.atomicmail_codes import (
    AtomicMailClient,
    AtomicMailCodeTimeout,
    AtomicMailCredentialFormatInvalid,
    AtomicMailCredentialsInvalid,
    AtomicMailProviderRateLimited,
    AtomicMailProviderUnavailable,
    fetch_atomicmail_code,
    parse_atomicmail_credential,
)
from video_task_service.config import get_settings
from video_task_service.schemas import AtomicMailCodeQuery, MailboxCodeResult

router = APIRouter(
    prefix="/atomicmail-codes",
    tags=["atomicmail-codes"],
)


@router.post("/query", response_model=MailboxCodeResult)
async def query_atomicmail_code(
    body: AtomicMailCodeQuery,
    response: Response,
) -> MailboxCodeResult:
    response.headers["Cache-Control"] = "no-store"
    try:
        credentials = parse_atomicmail_credential(body.credential.get_secret_value())
    except AtomicMailCredentialFormatInvalid as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc

    settings = get_settings()
    try:
        async with asyncio.timeout(body.timeout_seconds):
            async with httpx.AsyncClient() as http:
                result = await fetch_atomicmail_code(
                    credentials,
                    AtomicMailClient(
                        http,
                        timeout_seconds=settings.mailbox_provider_timeout_seconds,
                    ),
                    timeout_seconds=body.timeout_seconds,
                )
    except (TimeoutError, AtomicMailCodeTimeout) as exc:
        raise HTTPException(
            status_code=408,
            detail={
                "code": AtomicMailCodeTimeout.code,
                "message": "no verification code arrived before the configured deadline",
            },
        ) from exc
    except AtomicMailCredentialsInvalid as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": "Atomic Mail credentials are invalid"},
        ) from exc
    except AtomicMailProviderRateLimited as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": exc.code, "message": "Atomic Mail rate limited the request"},
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except AtomicMailProviderUnavailable as exc:
        raise HTTPException(
            status_code=502,
            detail={"code": exc.code, "message": "Atomic Mail is unavailable"},
        ) from exc

    return MailboxCodeResult(
        email=result.email,
        code=result.code,
        received_at=result.received_at,
        subject=result.subject,
        sender=result.sender,
        message_id=result.message_id,
        matched_by=result.matched_by,
    )
