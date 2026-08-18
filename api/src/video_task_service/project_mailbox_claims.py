from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Select, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from video_task_service.models import Mailbox, MailboxProject, ProjectMailboxClaim

PROJECT_NAME_MAX_LENGTH = 128
IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


@dataclass(frozen=True)
class NormalizedProjectName:
    project_key: str
    display_name: str


@dataclass(frozen=True)
class ProjectMailboxClaimOutcome:
    claim: ProjectMailboxClaim
    project_name: str
    replayed: bool


class ProjectMailboxPoolExhausted(RuntimeError):
    pass


def normalize_project_name(value: str) -> NormalizedProjectName:
    normalized = unicodedata.normalize("NFKC", value)
    if any(unicodedata.category(character) == "Cc" for character in normalized):
        raise ValueError("project name contains control characters")
    display_name = " ".join(normalized.split())
    if not display_name:
        raise ValueError("project name is empty")
    if len(display_name) > PROJECT_NAME_MAX_LENGTH:
        raise ValueError("project name is too long")
    return NormalizedProjectName(
        project_key=display_name.casefold(),
        display_name=display_name,
    )


def validate_idempotency_key(value: str) -> str:
    if IDEMPOTENCY_KEY_PATTERN.fullmatch(value) is None:
        raise ValueError("idempotency key format is invalid")
    return value


def mailbox_candidate_statement(project_id: int) -> Select[tuple[Mailbox]]:
    already_claimed = (
        select(ProjectMailboxClaim.id)
        .where(
            ProjectMailboxClaim.project_id == project_id,
            ProjectMailboxClaim.email_snapshot == Mailbox.email,
        )
        .exists()
    )
    return (
        select(Mailbox)
        .where(Mailbox.status == "ACTIVE", ~already_claimed)
        .order_by(Mailbox.id.asc())
        .limit(1)
        .with_for_update()
    )


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


async def claim_mailbox_for_project(
    session: AsyncSession,
    project_name: str,
    idempotency_key: str,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> ProjectMailboxClaimOutcome:
    normalized = normalize_project_name(project_name)
    validated_key = validate_idempotency_key(idempotency_key)
    async with session.begin():
        project = await ensure_mailbox_project(session, normalized)

        prior_claim = await session.scalar(
            select(ProjectMailboxClaim).where(
                ProjectMailboxClaim.project_id == project.id,
                ProjectMailboxClaim.idempotency_key == validated_key,
            )
        )
        if prior_claim is not None:
            return ProjectMailboxClaimOutcome(
                claim=prior_claim,
                project_name=project.display_name,
                replayed=True,
            )

        mailbox = await session.scalar(mailbox_candidate_statement(project.id))
        if mailbox is None:
            raise ProjectMailboxPoolExhausted(normalized.project_key)

        claim = create_project_mailbox_claim(
            project_id=project.id,
            mailbox=mailbox,
            idempotency_key=validated_key,
            claimed_at=_naive_utc(now()),
        )
        session.add(claim)
        await session.flush()
        return ProjectMailboxClaimOutcome(
            claim=claim,
            project_name=project.display_name,
            replayed=False,
        )


async def ensure_mailbox_project(
    session: AsyncSession,
    normalized: NormalizedProjectName,
) -> MailboxProject:
    """Create/load a project inside the caller's transaction and lock its row."""
    create_project = (
        mysql_insert(MailboxProject)
        .values(
            project_key=normalized.project_key,
            display_name=normalized.display_name,
        )
        .on_duplicate_key_update(display_name=MailboxProject.display_name)
    )
    await session.execute(create_project)
    project = await session.scalar(
        select(MailboxProject)
        .where(MailboxProject.project_key == normalized.project_key)
        .with_for_update()
    )
    if project is None:
        raise RuntimeError("mailbox project upsert did not produce a row")
    return project


def create_project_mailbox_claim(
    *,
    project_id: int,
    mailbox: Mailbox,
    idempotency_key: str,
    claimed_at: datetime,
) -> ProjectMailboxClaim:
    """Build the durable project/email tombstone used by both claim flows."""
    return ProjectMailboxClaim(
        claim_uuid=str(uuid4()),
        project_id=project_id,
        mailbox_id=mailbox.id,
        mailbox_uuid_snapshot=mailbox.mailbox_uuid,
        email_snapshot=mailbox.email,
        idempotency_key=validate_idempotency_key(idempotency_key),
        claimed_at=_naive_utc(claimed_at),
    )
