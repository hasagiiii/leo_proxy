from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy import UniqueConstraint
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable

from video_task_service.db import Base
from video_task_service.models import Mailbox, MailboxProject, ProjectMailboxClaim
from video_task_service.project_mailbox_claims import (
    ProjectMailboxPoolExhausted,
    claim_mailbox_for_project,
    mailbox_candidate_statement,
    normalize_project_name,
    validate_idempotency_key,
)
from video_task_service.schemas import ProjectMailboxClaimRequest


def test_normalize_project_name_uses_nfkc_whitespace_and_casefold() -> None:
    normalized = normalize_project_name("  ＰＲＯＪＥＣＴ   Alpha  ")

    assert normalized.project_key == "project alpha"
    assert normalized.display_name == "PROJECT Alpha"


@pytest.mark.parametrize("value", ["", "   ", "a" * 129, "project\u0000name"])
def test_normalize_project_name_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_project_name(value)


def test_validate_idempotency_key_accepts_uuid_and_rejects_unsafe_values() -> None:
    key = "019ffa1b-9284-76c2-be3b-b8c5f3ae093e"

    assert validate_idempotency_key(key) == key
    with pytest.raises(ValueError):
        validate_idempotency_key("short")
    with pytest.raises(ValueError):
        validate_idempotency_key("request key with spaces")


def test_claim_request_normalizes_project_display_name() -> None:
    request = ProjectMailboxClaimRequest(project_name="  Project   A  ")

    assert request.project_name == "Project A"


def test_claim_request_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ProjectMailboxClaimRequest(project_name="Project A", unknown=True)


def _unique_column_sets() -> set[frozenset[str]]:
    return {
        frozenset(column.name for column in constraint.columns)
        for constraint in ProjectMailboxClaim.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def test_claim_models_define_durable_project_and_email_uniqueness() -> None:
    assert {"mailbox_projects", "project_mailbox_claims"} <= set(Base.metadata.tables)
    assert frozenset({"project_id", "email_snapshot"}) in _unique_column_sets()
    assert frozenset({"project_id", "idempotency_key"}) in _unique_column_sets()
    assert frozenset({"claim_uuid"}) in _unique_column_sets()
    assert MailboxProject.__table__.c.project_key.unique


def test_claim_mailbox_foreign_key_preserves_snapshot_on_delete() -> None:
    mailbox_id = ProjectMailboxClaim.__table__.c.mailbox_id
    foreign_key = next(iter(mailbox_id.foreign_keys))

    assert mailbox_id.nullable
    assert foreign_key.ondelete == "SET NULL"


def test_claim_models_compile_expected_mysql_ddl() -> None:
    project_ddl = str(
        CreateTable(MailboxProject.__table__).compile(dialect=mysql.dialect())
    ).lower()
    claim_ddl = str(
        CreateTable(ProjectMailboxClaim.__table__).compile(dialect=mysql.dialect())
    ).lower()

    assert "project_key varchar(128) collate utf8mb4_bin not null" in project_ddl
    assert "idempotency_key varchar(128) collate utf8mb4_bin not null" in claim_ddl
    assert "on delete set null" in claim_ddl
    assert "uq_project_mailbox_claims_project_email" in claim_ddl
    assert "uq_project_mailbox_claims_project_idem" in claim_ddl


class Transaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *args: object) -> None:
        return None


class ClaimSession:
    def __init__(
        self,
        project: MailboxProject,
        prior_claim: ProjectMailboxClaim | None,
        mailbox: Mailbox | None,
    ) -> None:
        self.scalar_results = [project, prior_claim]
        if prior_claim is None:
            self.scalar_results.append(mailbox)
        self.executed: list[object] = []
        self.added: list[object] = []

    def begin(self) -> Transaction:
        return Transaction()

    async def execute(self, statement: object) -> None:
        self.executed.append(statement)

    async def scalar(self, statement: object) -> object | None:
        self.executed.append(statement)
        return self.scalar_results.pop(0)

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        return None


def _project() -> MailboxProject:
    project = MailboxProject(project_key="project a", display_name="Project A")
    project.id = 7
    return project


def _mailbox() -> Mailbox:
    mailbox = Mailbox(
        mailbox_uuid="67420f85-e589-4356-9c3a-12345678d086",
        email="user@example.com",
        password_ciphertext=b"password",
        client_id_ciphertext=b"client",
        refresh_token_ciphertext=b"refresh",
        status="ACTIVE",
        validation_attempts=0,
        version=0,
    )
    mailbox.id = 11
    return mailbox


def test_first_claim_creates_durable_snapshots() -> None:
    session = ClaimSession(_project(), None, _mailbox())
    now = datetime(2026, 8, 13, 8, 30, tzinfo=UTC)

    outcome = asyncio.run(
        claim_mailbox_for_project(  # type: ignore[arg-type]
            session,
            " Project   A ",
            "019ffa1b-9284-76c2-be3b-b8c5f3ae093e",
            now=lambda: now,
        )
    )

    assert not outcome.replayed
    assert outcome.project_name == "Project A"
    assert outcome.claim.email_snapshot == "user@example.com"
    assert outcome.claim.mailbox_uuid_snapshot == "67420f85-e589-4356-9c3a-12345678d086"
    assert outcome.claim.claimed_at == now.replace(tzinfo=None)
    assert UUID(outcome.claim.claim_uuid)
    assert session.added == [outcome.claim]


def test_same_idempotency_key_replays_prior_claim() -> None:
    now = datetime(2026, 8, 13, 8, 30)
    prior = ProjectMailboxClaim(
        claim_uuid="019ffa1b-9284-76c2-be3b-b8c5f3ae093e",
        project_id=7,
        mailbox_id=11,
        mailbox_uuid_snapshot="67420f85-e589-4356-9c3a-12345678d086",
        email_snapshot="user@example.com",
        idempotency_key="019ffa1b-9284-76c2-be3b-b8c5f3ae093e",
        claimed_at=now,
    )
    session = ClaimSession(_project(), prior, None)

    outcome = asyncio.run(
        claim_mailbox_for_project(  # type: ignore[arg-type]
            session,
            "Project A",
            prior.idempotency_key,
        )
    )

    assert outcome.replayed
    assert outcome.claim is prior
    assert session.added == []


def test_exhausted_project_raises_stable_domain_error() -> None:
    session = ClaimSession(_project(), None, None)

    with pytest.raises(ProjectMailboxPoolExhausted):
        asyncio.run(
            claim_mailbox_for_project(  # type: ignore[arg-type]
                session,
                "Project A",
                "019ffa1b-9284-76c2-be3b-b8c5f3ae093e",
            )
        )


def test_candidate_query_excludes_claimed_emails_and_orders_oldest_first() -> None:
    sql = str(
        mailbox_candidate_statement(7).compile(
            dialect=mysql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).lower()

    assert "not (exists" in sql or "not exists" in sql
    assert "project_mailbox_claims.email_snapshot = mailboxes.email" in sql
    assert "project_mailbox_claims.project_id = 7" in sql
    assert "mailboxes.status = 'active'" in sql
    assert "order by mailboxes.id asc" in sql
