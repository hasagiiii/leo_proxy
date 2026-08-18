from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import Mapped, mapped_column

from video_task_service.db import Base


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=3), nullable=False, default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=3),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
    )


class Space(TimestampMixin, Base):
    __tablename__ = "spaces"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    space_uuid: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    routing_key: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    max_concurrency: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    active_tasks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (Index("idx_spaces_sched", "status", "active_tasks", "max_concurrency"),)


class Account(TimestampMixin, Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_uuid: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    space_id: Mapped[int] = mapped_column(ForeignKey("spaces.id"), nullable=False)
    login_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    credential_source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="PASSWORD", server_default="PASSWORD"
    )
    label: Mapped[str | None] = mapped_column(String(32))
    password_ciphertext: Mapped[bytes] = mapped_column(LargeBinary(length=65535), nullable=False)
    video_token_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary(length=65535))
    credential_key_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    token_expires_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    token_refreshed_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    balance_credits: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    reserved_credits: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    balance_synced_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    max_concurrency: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default="3"
    )
    active_tasks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_tasks: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    failed_tasks: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING_VALIDATION")
    disabled_reason: Mapped[str | None] = mapped_column(String(255))
    last_assigned_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_error_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    __table_args__ = (
        Index(
            "idx_accounts_scheduler",
            "status",
            "space_id",
            "token_expires_at",
            "active_tasks",
            "max_concurrency",
            "balance_credits",
            "reserved_credits",
        ),
        Index("idx_accounts_token_expiry", "status", "token_expires_at"),
    )


class AccountCookieImportBatch(TimestampMixin, Base):
    __tablename__ = "account_cookie_import_batches"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    batch_uuid: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    archive_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    archive_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    space_id: Mapped[int] = mapped_column(ForeignKey("spaces.id"), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="QUEUED", server_default="QUEUED"
    )
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    started_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    finished_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))

    __table_args__ = (Index("idx_cookie_import_batches_status", "status", "created_at"),)


class AccountCookieImportItem(TimestampMixin, Base):
    __tablename__ = "account_cookie_import_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    item_uuid: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("account_cookie_import_batches.id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"))
    entry_name: Mapped[str] = mapped_column(String(255), nullable=False)
    entry_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_login_name: Mapped[str | None] = mapped_column(String(255))
    discovered_login_name: Mapped[str | None] = mapped_column(String(255))
    session_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary(length=65535))
    credential_key_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="QUEUED", server_default="QUEUED"
    )
    stage: Mapped[str] = mapped_column(
        String(32), nullable=False, default="RECEIVED", server_default="RECEIVED"
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_until: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    retry_after: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    retryable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_error_message: Mapped[str | None] = mapped_column(String(300))
    activated_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    finished_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")

    __table_args__ = (
        UniqueConstraint(
            "batch_id",
            "entry_sha256",
            name="uq_cookie_import_items_batch_entry_sha",
        ),
        Index("idx_cookie_import_items_batch", "batch_id", "status"),
        Index(
            "idx_cookie_import_items_due_lease",
            "status",
            "retry_after",
            "lease_until",
        ),
    )


class Mailbox(TimestampMixin, Base):
    __tablename__ = "mailboxes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mailbox_uuid: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    password_ciphertext: Mapped[bytes] = mapped_column(LargeBinary(length=65535), nullable=False)
    client_id_ciphertext: Mapped[bytes] = mapped_column(LargeBinary(length=65535), nullable=False)
    refresh_token_ciphertext: Mapped[bytes] = mapped_column(
        LargeBinary(length=65535), nullable=False
    )
    credential_key_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING_VALIDATION")
    disabled_reason: Mapped[str | None] = mapped_column(String(255))
    validation_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    validation_lease_owner: Mapped[str | None] = mapped_column(String(128))
    validation_lease_until: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    next_validation_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    last_validated_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_error_message: Mapped[str | None] = mapped_column(String(1000))
    last_message_received_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    __table_args__ = (
        Index(
            "idx_mailboxes_validation_due",
            "status",
            "next_validation_at",
            "validation_lease_until",
        ),
        Index("idx_mailboxes_last_message", "last_message_received_at"),
    )


class MailboxProject(TimestampMixin, Base):
    __tablename__ = "mailbox_projects"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_key: Mapped[str] = mapped_column(
        String(128, collation="utf8mb4_bin"), nullable=False, unique=True
    )
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)


class ProjectMailboxClaim(Base):
    __tablename__ = "project_mailbox_claims"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    claim_uuid: Mapped[str] = mapped_column(String(36), nullable=False)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("mailbox_projects.id", ondelete="CASCADE"), nullable=False
    )
    mailbox_id: Mapped[int | None] = mapped_column(ForeignKey("mailboxes.id", ondelete="SET NULL"))
    mailbox_uuid_snapshot: Mapped[str] = mapped_column(String(36), nullable=False)
    email_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(
        String(128, collation="utf8mb4_bin"), nullable=False
    )
    claimed_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=3), nullable=False, default=utc_now, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("claim_uuid", name="uq_project_mailbox_claims_uuid"),
        UniqueConstraint(
            "project_id",
            "email_snapshot",
            name="uq_project_mailbox_claims_project_email",
        ),
        UniqueConstraint(
            "project_id",
            "idempotency_key",
            name="uq_project_mailbox_claims_project_idem",
        ),
        Index("idx_project_mailbox_claims_project_claimed", "project_id", "claimed_at"),
    )


class ParentAccount(TimestampMixin, Base):
    __tablename__ = "parent_accounts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    parent_account_uuid: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    password_encrypted: Mapped[bytes] = mapped_column(LargeBinary(length=65535), nullable=False)
    credential_key_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    invite_url: Mapped[str] = mapped_column(String(8192), nullable=False)
    invite_success_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    invite_failure_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="ACTIVE", server_default="ACTIVE"
    )
    consecutive_150_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    successful_settlement_sequence: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    exhausted_reason: Mapped[str | None] = mapped_column(String(64))
    exhausted_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    legacy_invite_success_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    legacy_invite_failure_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")

    __table_args__ = (Index("idx_parent_accounts_email", "email"),)


class RegistrationRecord(TimestampMixin, Base):
    __tablename__ = "registration_records"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    registration_uuid: Mapped[str] = mapped_column(String(36), nullable=False)
    client_id: Mapped[str] = mapped_column(String(128), nullable=False)
    claim_idempotency_key: Mapped[str] = mapped_column(
        String(128, collation="utf8mb4_bin"), nullable=False
    )
    result_idempotency_key: Mapped[str | None] = mapped_column(String(128, collation="utf8mb4_bin"))
    result_fingerprint: Mapped[str | None] = mapped_column(String(64))
    parent_account_id: Mapped[int] = mapped_column(
        ForeignKey("parent_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    parent_account_uuid_snapshot: Mapped[str] = mapped_column(String(36), nullable=False)
    parent_email_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    mailbox_id: Mapped[int | None] = mapped_column(ForeignKey("mailboxes.id", ondelete="SET NULL"))
    project_mailbox_claim_id: Mapped[int | None] = mapped_column(
        ForeignKey("project_mailbox_claims.id", ondelete="SET NULL")
    )
    mailbox_uuid_snapshot: Mapped[str] = mapped_column(String(36), nullable=False)
    email_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    report_token_encrypted: Mapped[bytes] = mapped_column(LargeBinary(length=65535), nullable=False)
    lease_expires_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), nullable=False)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="RUNNING", server_default="RUNNING"
    )
    registered_email: Mapped[str | None] = mapped_column(String(255))
    verified_email: Mapped[str | None] = mapped_column(String(255))
    awarded_points: Mapped[int | None] = mapped_column(BigInteger)
    is_used: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    points_checked_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    parent_settlement_sequence: Mapped[int | None] = mapped_column(BigInteger)
    session_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary(length=65535))
    video_token_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary(length=65535))
    token_expires_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    cookie_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    credential_key_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    validation_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    validation_lease_owner: Mapped[str | None] = mapped_column(String(128))
    validation_lease_until: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    retry_after: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    client_error_code: Mapped[str | None] = mapped_column(String(64))
    client_error_message: Mapped[str | None] = mapped_column(String(1000))
    validation_error_code: Mapped[str | None] = mapped_column(String(64))
    validation_error_message: Mapped[str | None] = mapped_column(String(1000))
    started_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), nullable=False)
    reported_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    validation_finished_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"))
    account_uuid_snapshot: Mapped[str | None] = mapped_column(String(36))
    promoted_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")

    __table_args__ = (
        UniqueConstraint("registration_uuid", name="uq_registration_records_uuid"),
        UniqueConstraint("email_snapshot", name="uq_registration_records_email_snapshot"),
        UniqueConstraint(
            "client_id",
            "claim_idempotency_key",
            name="uq_registration_records_client_claim_idem",
        ),
        UniqueConstraint(
            "client_id",
            "result_idempotency_key",
            name="uq_registration_records_client_result_idem",
        ),
        Index(
            "idx_registration_records_parent_created",
            "parent_account_id",
            "created_at",
        ),
        Index(
            "idx_registration_records_validation_due",
            "status",
            "retry_after",
            "validation_lease_until",
        ),
        Index("idx_registration_records_account", "account_id"),
        Index("idx_registration_records_project_claim", "project_mailbox_claim_id"),
        Index(
            "idx_registration_records_usage",
            "is_used",
            "status",
            "awarded_points",
            "created_at",
        ),
        Index(
            "idx_registration_records_client_started",
            "client_id",
            "started_at",
        ),
        Index(
            "idx_registration_records_updated_client",
            "updated_at",
            "client_id",
        ),
        Index(
            "idx_registration_records_finished_client",
            "validation_finished_at",
            "client_id",
        ),
    )


class RegistrationPoolSettings(TimestampMixin, Base):
    __tablename__ = "registration_pool_settings"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1, server_default="1")
    target_space_id: Mapped[int | None] = mapped_column(
        ForeignKey("spaces.id", ondelete="SET NULL")
    )
    default_max_concurrency: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default="3"
    )
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")


class AccountLedgerProfile(TimestampMixin, Base):
    __tablename__ = "account_ledger_profiles"

    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True
    )
    source: Mapped[str] = mapped_column(String(255), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    source_raw: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source_exported_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), nullable=False)
    source_count: Mapped[int] = mapped_column(Integer, nullable=False)
    source_file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_record_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    parent_account_id: Mapped[str | None] = mapped_column(String(255))
    parent_account: Mapped[str | None] = mapped_column(String(255))
    invite_status: Mapped[str | None] = mapped_column(String(32))
    invited_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    invite_error: Mapped[str | None] = mapped_column(String(1000))
    invite_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    registration_status: Mapped[str | None] = mapped_column(String(32))
    registration_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    registered_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    registration_account_id: Mapped[str | None] = mapped_column(String(255))
    registration_error: Mapped[str | None] = mapped_column(String(1000))
    credits_total: Mapped[int | None] = mapped_column(BigInteger)
    credits_subscription: Mapped[int | None] = mapped_column(BigInteger)
    credits_purchase: Mapped[int | None] = mapped_column(BigInteger)
    credits_rollover: Mapped[int | None] = mapped_column(BigInteger)
    credits_checked_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    credits_error: Mapped[str | None] = mapped_column(String(1000))
    credits_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_created_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    source_updated_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    has_registration_password: Mapped[bool] = mapped_column(Boolean, nullable=False)
    has_group_token: Mapped[bool] = mapped_column(Boolean, nullable=False)
    has_authorization_token: Mapped[bool] = mapped_column(Boolean, nullable=False)
    raw_record_ciphertext: Mapped[bytes] = mapped_column(LargeBinary(length=65535), nullable=False)
    raw_record_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    credential_key_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )

    __table_args__ = (
        Index("idx_account_ledger_profiles_credits", "credits_total", "registration_status"),
    )


class AccountRenewalSession(TimestampMixin, Base):
    __tablename__ = "account_renewal_sessions"

    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True
    )
    session_ciphertext: Mapped[bytes] = mapped_column(LargeBinary(length=65535), nullable=False)
    credential_key_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="IDLE", server_default="IDLE"
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    claimed_account_version: Mapped[int | None] = mapped_column(BigInteger)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_until: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    fallback_after: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    retry_after: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    last_success_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    session_refreshed_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    client_reported_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    client_version: Mapped[str | None] = mapped_column(String(32))
    renewal_capability: Mapped[str | None] = mapped_column(String(64))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    previous_token_expires_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    renewed_token_expires_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))

    __table_args__ = (
        Index(
            "idx_account_renewal_sessions_claim",
            "status",
            "retry_after",
            "lease_until",
        ),
        Index(
            "idx_account_renewal_sessions_keepalive",
            "status",
            "session_refreshed_at",
        ),
        Index(
            "idx_account_renewal_sessions_client_age",
            "status",
            "client_reported_at",
        ),
    )


class ProtocolRenewalEvent(TimestampMixin, Base):
    __tablename__ = "protocol_renewal_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_uuid: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"))
    account_uuid_snapshot: Mapped[str] = mapped_column(String(36), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    applied: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    retryable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    next_state: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_token_expires_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    renewed_token_expires_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))

    __table_args__ = (
        Index("idx_protocol_renewal_events_window", "finished_at", "outcome", "applied"),
        Index("idx_protocol_renewal_events_account", "account_id", "finished_at"),
        Index("idx_protocol_renewal_events_error", "error_code", "finished_at"),
    )


class ProtocolRenewalRuntime(TimestampMixin, Base):
    __tablename__ = "protocol_renewal_runtime"

    instance_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    app_version: Mapped[str] = mapped_column(String(64), nullable=False)
    last_heartbeat_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), nullable=False)
    last_scan_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    last_claimed_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    last_completed_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    last_loop_error_code: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (Index("idx_protocol_renewal_runtime_heartbeat", "last_heartbeat_at"),)


class LoginPoolPolicy(TimestampMixin, Base):
    __tablename__ = "login_pool_policy"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1)


class AccountLoginJob(TimestampMixin, Base):
    __tablename__ = "account_login_jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_uuid: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    active_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"))
    job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="LEASED")
    lease_owner: Mapped[str] = mapped_column(String(128), nullable=False)
    lease_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    lease_until: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    claimed_account_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    claimed_token_refreshed_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    reported_token_fingerprint: Mapped[str | None] = mapped_column(String(16))
    token_received_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    validation_finished_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    retry_after: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(1000))

    __table_args__ = (
        UniqueConstraint(
            "active_account_id",
            name="uk_account_login_jobs_active_account",
        ),
        Index("idx_account_login_jobs_claim", "status", "lease_until", "created_at"),
        Index("idx_account_login_jobs_account", "account_id", "id"),
        Index("idx_account_login_jobs_retry", "account_id", "status", "retry_after"),
    )


class Task(TimestampMixin, Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_uuid: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="leonardo")
    upstream_task_id: Mapped[str | None] = mapped_column(String(128))
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"))
    space_id: Mapped[int | None] = mapped_column(ForeignKey("spaces.id"))
    task_type: Mapped[str] = mapped_column(String(32), nullable=False, default="VIDEO_GENERATION")
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str | None] = mapped_column(String(32))
    input_schema_version: Mapped[str] = mapped_column(String(32), nullable=False, default="legacy")
    input_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="QUEUED")
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    estimated_credit_cost: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    reserved_credit_cost: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    actual_credit_cost: Mapped[int | None] = mapped_column(BigInteger)
    submit_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sync_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    media_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    media_resolved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_sync_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    sync_lease_owner: Mapped[str | None] = mapped_column(String(128))
    sync_lease_until: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(1000))
    queued_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), nullable=False, default=utc_now)
    assigned_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    submit_started_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    upstream_submitted_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    upstream_started_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    finished_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    canceled_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("provider", "upstream_task_id", name="uk_tasks_upstream"),
        Index("idx_tasks_account_status", "account_id", "status", "created_at"),
        Index("idx_tasks_space_status", "space_id", "status", "created_at"),
        Index("idx_tasks_sync", "status", "next_sync_at", "sync_lease_until"),
        Index("idx_tasks_model_created", "model", "created_at"),
        Index("idx_tasks_created", "created_at"),
    )


class TaskQueue(TimestampMixin, Base):
    __tablename__ = "task_queue"

    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )
    queue_status: Mapped[str] = mapped_column(String(32), nullable=False, default="READY")
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), nullable=False, default=utc_now)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_until: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))
    delivery_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        Index("idx_queue_claim", "queue_status", "available_at", "priority", "lease_until"),
    )


class TaskEvent(Base):
    __tablename__ = "task_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_uuid: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str | None] = mapped_column(String(32))
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(128))
    reason: Mapped[str | None] = mapped_column(String(500))
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=3), nullable=False, default=utc_now, server_default=func.now()
    )

    __table_args__ = (Index("idx_task_events_task", "task_id", "id"),)


class TaskAttempt(Base):
    __tablename__ = "task_attempts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    attempt_uuid: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    phase: Mapped[str] = mapped_column(String(32), nullable=False, default="SUBMIT")
    request_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    response_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    upstream_task_id: Mapped[str | None] = mapped_column(String(128))
    outcome: Mapped[str] = mapped_column(String(32), nullable=False, default="STARTED")
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(1000))
    started_at: Mapped[datetime] = mapped_column(DATETIME(fsp=3), nullable=False, default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))

    __table_args__ = (
        UniqueConstraint("task_id", "attempt_no", name="uk_task_attempt_no"),
        Index("idx_task_attempts_account", "account_id", "started_at"),
    )


class TaskMediaAsset(TimestampMixin, Base):
    __tablename__ = "task_media_assets"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    asset_uuid: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    media_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    media_role: Mapped[str] = mapped_column(String(32), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    source_url_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_namespace: Mapped[str] = mapped_column(String(64), nullable=False, default="legacy")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    provider_asset_id: Mapped[str | None] = mapped_column(String(255))
    provider_asset_type: Mapped[str | None] = mapped_column(String(32))
    content_type: Mapped[str | None] = mapped_column(String(128))
    content_length: Mapped[int | None] = mapped_column(BigInteger)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(1000))
    resolved_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=3))

    __table_args__ = (
        UniqueConstraint(
            "task_id",
            "attempt_no",
            "media_role",
            "media_kind",
            "ordinal",
            name="uk_task_media_attempt_role",
        ),
        Index("idx_task_media_task_attempt", "task_id", "attempt_no", "status"),
        Index(
            "idx_task_media_cache",
            "account_id",
            "media_kind",
            "source_url_hash",
            "status",
        ),
        Index(
            "idx_task_media_namespace_cache",
            "provider_namespace",
            "account_id",
            "media_kind",
            "source_url_hash",
            "status",
        ),
    )


class AccountCreditLedger(Base):
    __tablename__ = "account_credit_ledger"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ledger_uuid: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"))
    entry_type: Mapped[str] = mapped_column(String(32), nullable=False)
    balance_before: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_after: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reserved_before: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reserved_after: Mapped[int] = mapped_column(BigInteger, nullable=False)
    credit_delta: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=3), nullable=False, default=utc_now, server_default=func.now()
    )

    __table_args__ = (
        Index("idx_credit_ledger_account", "account_id", "id"),
        Index("idx_credit_ledger_task", "task_id", "id"),
    )
