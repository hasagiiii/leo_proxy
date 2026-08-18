from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from video_task_service.crypto import decrypt_secret, encrypt_secret
from video_task_service.models import (
    Mailbox,
    MailboxProject,
    ParentAccount,
    ProjectMailboxClaim,
    RegistrationRecord,
)
from video_task_service.project_mailbox_claims import (
    create_project_mailbox_claim,
    ensure_mailbox_project,
    normalize_project_name,
)
from video_task_service.protocol_renewal import encode_renewal_session
from video_task_service.schemas import (
    RegistrationJobResultRequest,
    RenewalCookie,
    RenewalSessionPayload,
)

IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
PARENT_EXHAUSTION_CREDIT_THRESHOLD = 8_000
PARENT_EXHAUSTION_STREAK = 3


class RegistrationParentPoolExhausted(RuntimeError):
    pass


class RegistrationMailboxPoolExhausted(RuntimeError):
    pass


class RegistrationNotFound(RuntimeError):
    pass


class RegistrationClaimConflict(RuntimeError):
    pass


class RegistrationLeaseExpired(RuntimeError):
    pass


class RegistrationReportTokenInvalid(RuntimeError):
    pass


class RegistrationResultConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class RegistrationClaimOutcome:
    record: RegistrationRecord
    parent_invite_url: str
    report_token: str
    project_name: str
    replayed: bool


@dataclass(frozen=True)
class RegistrationResultOutcome:
    record: RegistrationRecord
    replayed: bool


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def validate_registration_idempotency_key(value: str) -> str:
    if IDEMPOTENCY_KEY_PATTERN.fullmatch(value) is None:
        raise ValueError("idempotency key format is invalid")
    return value


def parent_candidate_statement() -> Select[tuple[ParentAccount]]:
    return (
        select(ParentAccount)
        .where(ParentAccount.status == "ACTIVE")
        .order_by(ParentAccount.id.asc())
        .limit(1)
        .with_for_update()
    )


def mailbox_candidate_statement(project_id: int | None = None) -> Select[tuple[Mailbox]]:
    used = (
        select(RegistrationRecord.id)
        .where(RegistrationRecord.email_snapshot == Mailbox.email)
        .exists()
    )
    conditions = [Mailbox.status == "ACTIVE", ~used]
    if project_id is not None:
        project_used = (
            select(ProjectMailboxClaim.id)
            .where(
                ProjectMailboxClaim.project_id == project_id,
                ProjectMailboxClaim.email_snapshot == Mailbox.email,
            )
            .exists()
        )
        conditions.append(~project_used)
    return (
        select(Mailbox)
        .where(*conditions)
        .order_by(Mailbox.id.asc())
        .limit(1)
        .with_for_update()
    )


def _decrypt_report_token(record: RegistrationRecord) -> str:
    return decrypt_secret(
        bytes(record.report_token_encrypted),
        f"{record.registration_uuid}:registration_report_token",
    )


def _verify_report_access(
    record: RegistrationRecord,
    *,
    client_id: str,
    report_token: str,
) -> None:
    if record.client_id != client_id:
        raise RegistrationReportTokenInvalid
    expected = _decrypt_report_token(record)
    if not hmac.compare_digest(expected, report_token):
        raise RegistrationReportTokenInvalid


async def claim_registration_job(
    session: AsyncSession,
    *,
    client_id: str,
    project_name: str,
    idempotency_key: str,
    lease_seconds: int,
    now: datetime,
) -> RegistrationClaimOutcome:
    key = validate_registration_idempotency_key(idempotency_key)
    normalized_project = normalize_project_name(project_name)
    timestamp = _naive_utc(now)
    async with session.begin():
        prior = await session.scalar(
            select(RegistrationRecord)
            .where(
                RegistrationRecord.client_id == client_id,
                RegistrationRecord.claim_idempotency_key == key,
            )
            .with_for_update()
        )
        if prior is not None:
            parent = await session.get(ParentAccount, prior.parent_account_id)
            if parent is None:
                raise RegistrationNotFound
            project = await session.scalar(
                select(MailboxProject)
                .join(
                    ProjectMailboxClaim,
                    ProjectMailboxClaim.project_id == MailboxProject.id,
                )
                .where(ProjectMailboxClaim.id == prior.project_mailbox_claim_id)
            )
            if project is None or project.project_key != normalized_project.project_key:
                raise RegistrationClaimConflict
            return RegistrationClaimOutcome(
                record=prior,
                parent_invite_url=parent.invite_url,
                report_token=_decrypt_report_token(prior),
                project_name=project.display_name,
                replayed=True,
            )

        parent = await session.scalar(parent_candidate_statement())
        if parent is None:
            raise RegistrationParentPoolExhausted
        project = await ensure_mailbox_project(session, normalized_project)
        mailbox = await session.scalar(mailbox_candidate_statement(project.id))
        if mailbox is None:
            raise RegistrationMailboxPoolExhausted

        registration_uuid = secrets.token_hex(16)
        registration_uuid = (
            f"{registration_uuid[:8]}-{registration_uuid[8:12]}-"
            f"{registration_uuid[12:16]}-{registration_uuid[16:20]}-"
            f"{registration_uuid[20:]}"
        )
        report_token = secrets.token_urlsafe(32)
        project_claim_key = "registration:" + hashlib.sha256(
            f"{client_id}:{key}".encode()
        ).hexdigest()
        project_claim = create_project_mailbox_claim(
            project_id=project.id,
            mailbox=mailbox,
            idempotency_key=project_claim_key,
            claimed_at=timestamp,
        )
        session.add(project_claim)
        await session.flush()
        record = RegistrationRecord(
            registration_uuid=registration_uuid,
            client_id=client_id,
            claim_idempotency_key=key,
            parent_account_id=parent.id,
            parent_account_uuid_snapshot=parent.parent_account_uuid,
            parent_email_snapshot=parent.email,
            mailbox_id=mailbox.id,
            project_mailbox_claim_id=project_claim.id,
            mailbox_uuid_snapshot=mailbox.mailbox_uuid,
            email_snapshot=mailbox.email,
            report_token_encrypted=encrypt_secret(
                report_token,
                f"{registration_uuid}:registration_report_token",
            ),
            lease_expires_at=timestamp + timedelta(seconds=lease_seconds),
            last_heartbeat_at=timestamp,
            status="RUNNING",
            cookie_count=0,
            validation_attempts=0,
            started_at=timestamp,
            version=0,
            created_at=timestamp,
            updated_at=timestamp,
        )
        session.add(record)
        await session.flush()
        return RegistrationClaimOutcome(
            record=record,
            parent_invite_url=parent.invite_url,
            report_token=report_token,
            project_name=project.display_name,
            replayed=False,
        )


async def get_registration_job_status(
    session: AsyncSession,
    *,
    registration_uuid: str,
    client_id: str,
    report_token: str,
) -> RegistrationRecord:
    record = await session.scalar(
        select(RegistrationRecord).where(
            RegistrationRecord.registration_uuid == registration_uuid
        )
    )
    if record is None:
        raise RegistrationNotFound
    _verify_report_access(record, client_id=client_id, report_token=report_token)
    return record


async def heartbeat_registration_job(
    session: AsyncSession,
    *,
    registration_uuid: str,
    client_id: str,
    report_token: str,
    lease_seconds: int,
    timeout_seconds: int,
    now: datetime,
) -> RegistrationRecord:
    timestamp = _naive_utc(now)
    async with session.begin():
        record = await session.scalar(
            select(RegistrationRecord)
            .where(RegistrationRecord.registration_uuid == registration_uuid)
            .with_for_update()
        )
        if record is None:
            raise RegistrationNotFound
        _verify_report_access(record, client_id=client_id, report_token=report_token)
        if record.status != "RUNNING":
            raise RegistrationClaimConflict
        absolute_deadline = record.started_at + timedelta(seconds=timeout_seconds)
        if record.lease_expires_at < timestamp or absolute_deadline <= timestamp:
            raise RegistrationLeaseExpired
        record.last_heartbeat_at = timestamp
        record.lease_expires_at = min(
            timestamp + timedelta(seconds=lease_seconds),
            absolute_deadline,
        )
        record.version += 1
        return record


_SAME_SITE = {
    "Strict": "strict",
    "Lax": "lax",
    "None": "no_restriction",
    "Unset": "unspecified",
}


def cdp_session_payload(body: RegistrationJobResultRequest) -> RenewalSessionPayload:
    if body.status != "REGISTERED" or not body.cookies:
        raise ValueError("registered Cookie payload is required")
    cookies = [
        RenewalCookie(
            name=cookie.name,
            value=cookie.value,
            domain=cookie.domain,
            path=cookie.path,
            expiration_date=(
                cookie.expires if cookie.expires is not None and cookie.expires > 0 else None
            ),
            secure=cookie.secure,
            http_only=cookie.http_only,
            same_site=_SAME_SITE[cookie.same_site],
        )
        for cookie in body.cookies
    ]
    return RenewalSessionPayload(
        cookies=cookies,
        user_agent=body.user_agent,
        accept_language=body.accept_language,
    )


def _fingerprint_cookie(cookie: object) -> dict[str, object]:
    return {
        "name": cookie.name,
        "value": cookie.value.get_secret_value(),
        "domain": cookie.domain,
        "path": cookie.path,
        "expires": cookie.expires,
        "httpOnly": cookie.http_only,
        "secure": cookie.secure,
        "sameSite": cookie.same_site,
    }


def registration_result_fingerprint(body: RegistrationJobResultRequest) -> str:
    cookies = sorted(
        (_fingerprint_cookie(cookie) for cookie in body.cookies or []),
        key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")),
    )
    canonical = {
        "client_id": body.client_id,
        "status": body.status,
        "registered_email": body.registered_email,
        "user_agent": body.user_agent,
        "accept_language": body.accept_language,
        "cookies": cookies,
        "error_code": body.error_code,
        "error_message": body.error_message,
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


async def record_registration_result(
    session: AsyncSession,
    *,
    registration_uuid: str,
    body: RegistrationJobResultRequest,
    idempotency_key: str,
    now: datetime,
) -> RegistrationResultOutcome:
    key = validate_registration_idempotency_key(idempotency_key)
    fingerprint = registration_result_fingerprint(body)
    timestamp = _naive_utc(now)
    async with session.begin():
        reused_key = await session.scalar(
            select(RegistrationRecord)
            .where(
                RegistrationRecord.client_id == body.client_id,
                RegistrationRecord.result_idempotency_key == key,
            )
            .with_for_update()
        )
        if reused_key is not None and reused_key.registration_uuid != registration_uuid:
            raise RegistrationResultConflict

        record = await session.scalar(
            select(RegistrationRecord)
            .where(RegistrationRecord.registration_uuid == registration_uuid)
            .with_for_update()
        )
        if record is None:
            raise RegistrationNotFound
        _verify_report_access(
            record,
            client_id=body.client_id,
            report_token=body.report_token.get_secret_value(),
        )
        if record.result_idempotency_key is not None:
            if record.result_idempotency_key == key and record.result_fingerprint == fingerprint:
                return RegistrationResultOutcome(record=record, replayed=True)
            raise RegistrationResultConflict
        if record.status != "RUNNING":
            raise RegistrationResultConflict

        record.result_idempotency_key = key
        record.result_fingerprint = fingerprint
        record.reported_at = timestamp
        record.version += 1
        if body.status == "FAILED":
            parent = await session.scalar(
                select(ParentAccount)
                .where(ParentAccount.id == record.parent_account_id)
                .with_for_update()
            )
            if parent is None:
                raise RegistrationNotFound
            record.status = "FAILED"
            record.client_error_code = body.error_code
            record.client_error_message = body.error_message
            record.validation_finished_at = timestamp
            parent.invite_failure_count += 1
            parent.version += 1
        else:
            if body.registered_email != record.email_snapshot.lower():
                raise RegistrationResultConflict
            payload = cdp_session_payload(body)
            encoded = encode_renewal_session(payload)
            record.status = "COOKIE_REPORTED"
            record.registered_email = body.registered_email
            record.cookie_count = len(payload.cookies)
            record.session_ciphertext = encrypt_secret(
                encoded,
                f"{record.registration_uuid}:registration_session",
            )
            record.retry_after = timestamp
        return RegistrationResultOutcome(record=record, replayed=False)


def settle_success(
    parent: ParentAccount,
    record: RegistrationRecord,
    credits: int,
    now: datetime,
) -> None:
    parent.invite_success_count += 1
    parent.successful_settlement_sequence += 1
    record.parent_settlement_sequence = parent.successful_settlement_sequence
    parent.version += 1
    if parent.status != "ACTIVE":
        return
    # Keep the persisted counter's legacy name for API and migration compatibility.
    if credits < PARENT_EXHAUSTION_CREDIT_THRESHOLD:
        parent.consecutive_150_count += 1
        if parent.consecutive_150_count >= PARENT_EXHAUSTION_STREAK:
            parent.consecutive_150_count = PARENT_EXHAUSTION_STREAK
            parent.status = "EXHAUSTED"
            parent.exhausted_reason = "THREE_CONSECUTIVE_BELOW_8000"
            parent.exhausted_at = _naive_utc(now)
    else:
        parent.consecutive_150_count = 0
