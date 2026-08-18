from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    RootModel,
    SecretStr,
    field_validator,
    model_validator,
)

from video_task_service.gemini_omni_flash import (
    GEMINI_OMNI_FLASH_PROMPT_MAX_CHARS,
    is_gemini_omni_flash_model,
)
from video_task_service.gpt_image_2 import (
    gpt_image_2_resolution,
    is_gpt_image_2_model,
)
from video_task_service.h3 import H3_PROMPT_MAX_CHARS, H3Mode, normalize_h3_prompt
from video_task_service.kling_o3 import is_kling_o3_model
from video_task_service.mailbox_codes import EMAIL_PATTERN
from video_task_service.nano_images import is_nano_image_model, nano_image_resolution
from video_task_service.pricing import quote_credit_cost
from video_task_service.project_mailbox_claims import normalize_project_name
from video_task_service.seed_audio import (
    SEED_AUDIO_DEFAULT_VOICE_ID,
    SEED_AUDIO_PROMPT_MAX_CHARS,
    is_seed_audio_model,
)
from video_task_service.seedance import (
    SEEDANCE_MODEL_RESOLUTIONS,
    is_seedance_25_model,
    is_seedance_model,
)
from video_task_service.veo_3_1 import (
    VEO_3_1_MODEL_MODES,
    VEO_3_1_MODEL_RESOLUTIONS,
    VEO_3_1_PROMPT_MAX_CHARS,
    is_veo_3_1_model,
)


def normalize_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


class RenewalCookie(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=256)
    value: SecretStr = Field(max_length=16384)
    domain: str = Field(min_length=1, max_length=255)
    path: str = Field(default="/", min_length=1, max_length=2048)
    expiration_date: float | None = Field(default=None, gt=0)
    secure: bool = True
    http_only: bool = False
    same_site: Literal["strict", "lax", "no_restriction", "unspecified"] = "unspecified"

    @field_validator("domain")
    @classmethod
    def leonardo_domain_only(cls, value: str) -> str:
        normalized = value.lower().rstrip(".")
        bare = normalized.lstrip(".")
        if bare != "leonardo.ai" and not bare.endswith(".leonardo.ai"):
            raise ValueError("renewal cookie domain must belong to leonardo.ai")
        return normalized


class RenewalSessionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    cookies: list[RenewalCookie] = Field(min_length=1, max_length=64)
    user_agent: str = Field(default="", max_length=1024)
    accept_language: str = Field(default="en-US,en;q=0.9", max_length=512)
    client_version: str | None = Field(default=None, max_length=32)
    capability: Literal["better-auth-v1"] = "better-auth-v1"


class SpaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    routing_key: str | None = Field(default=None, max_length=128)
    max_concurrency: int = Field(default=10, ge=1, le=1000)


class SpaceView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    space_uuid: UUID
    name: str
    routing_key: str | None
    status: str
    max_concurrency: int
    active_tasks: int
    created_at: datetime
    updated_at: datetime


class AccountCreate(BaseModel):
    space_uuid: UUID
    login_name: str = Field(min_length=3, max_length=255)
    password: SecretStr = Field(min_length=1, max_length=4096)
    label: Literal["mmoshenqi", "macbook"] | None = None
    video_token: SecretStr | None = Field(default=None, max_length=16384)
    token_expires_at: datetime | None = None
    balance_credits: int = Field(default=0, ge=0)
    max_concurrency: int = Field(default=3, ge=1, le=100)

    @model_validator(mode="after")
    def token_and_expiry_are_paired(self) -> AccountCreate:
        if (self.video_token is None) != (self.token_expires_at is None):
            raise ValueError("video_token and token_expires_at must be supplied together")
        return self

    @field_validator("token_expires_at")
    @classmethod
    def normalize_expiry(cls, value: datetime | None) -> datetime | None:
        return normalize_datetime(value)


class AccountTokenUpdate(BaseModel):
    video_token: SecretStr = Field(min_length=1, max_length=16384)
    token_expires_at: datetime
    expected_version: int = Field(ge=0)

    @field_validator("token_expires_at")
    @classmethod
    def expiry_is_future(cls, value: datetime) -> datetime:
        normalized = normalize_datetime(value)
        assert normalized is not None
        if normalized <= datetime.now(UTC).replace(tzinfo=None):
            raise ValueError("token_expires_at must be in the future")
        return normalized


class AccountPatch(BaseModel):
    space_uuid: UUID | None = None
    password: SecretStr | None = Field(default=None, min_length=1, max_length=4096)
    max_concurrency: int | None = Field(default=None, ge=1, le=100)
    manual_status: Literal["ACTIVE", "MANUAL_DISABLED"] | None = None
    expected_version: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def at_least_one_change(self) -> AccountPatch:
        if not any(
            value is not None
            for value in (
                self.space_uuid,
                self.password,
                self.max_concurrency,
                self.manual_status,
            )
        ):
            raise ValueError("at least one account field must be supplied")
        return self


class AccountSync(BaseModel):
    """Upsert payload used by the desktop account collector.

    ``password`` is optional because an existing account only needs its token
    refreshed.  The API requires it when the login name is first seen.
    """

    space_name: str = Field(min_length=1, max_length=128)
    routing_key: str | None = Field(default=None, max_length=128)
    login_name: str = Field(min_length=3, max_length=255)
    password: SecretStr | None = Field(default=None, max_length=4096)
    video_token: SecretStr = Field(min_length=1, max_length=16384)
    token_expires_at: datetime
    balance_credits: int | None = Field(default=None, ge=0)
    max_concurrency: int = Field(default=3, ge=1, le=100)
    renewal_session: RenewalSessionPayload | None = None

    @field_validator("token_expires_at")
    @classmethod
    def expiry_is_future(cls, value: datetime) -> datetime:
        normalized = normalize_datetime(value)
        assert normalized is not None
        if normalized <= datetime.now(UTC).replace(tzinfo=None):
            raise ValueError("token_expires_at must be in the future")
        return normalized


class AccountView(BaseModel):
    account_uuid: UUID
    space_uuid: UUID
    login_name: str
    login_name_masked: str
    credential_source: Literal["PASSWORD", "COOKIE_SESSION"] = "PASSWORD"
    label: Literal["mmoshenqi", "macbook"] | None
    status: str
    disabled_reason: str | None
    token_configured: bool
    token_expires_at: datetime | None
    token_refreshed_at: datetime | None
    balance_credits: int
    reserved_credits: int
    balance_synced_at: datetime | None
    max_concurrency: int
    active_tasks: int
    completed_tasks: int
    failed_tasks: int
    version: int
    created_at: datetime
    updated_at: datetime


# BEGIN EMAIL AUDIT API
class AccountEmailAuditRequest(BaseModel):
    """Email keys used for a read-only account-pool/database comparison."""

    model_config = ConfigDict(extra="forbid")

    emails: list[str] = Field(min_length=1, max_length=500)

    @field_validator("emails")
    @classmethod
    def normalize_emails(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            email = value.strip().lower()
            if EMAIL_PATTERN.fullmatch(email) is None:
                raise ValueError("emails must contain valid email addresses")
            if email not in normalized:
                normalized.append(email)
        return normalized


class AccountEmailAuditItemView(BaseModel):
    email: str
    in_account_pool: bool
    account_uuid: UUID | None
    account_status: str | None
    disabled_reason: str | None
    balance_credits: int | None
    completed_tasks: int | None
    failed_tasks: int | None
    blocked: bool | None
    blocked_source: Literal["DB_MANUAL_STATUS", "NOT_RECORDED"]
    image_task_total: int = Field(ge=0)
    image_task_success: int = Field(ge=0)
    image_task_failed: int = Field(ge=0)
    image_models: list[str]


class AccountEmailAuditResponse(BaseModel):
    checked_at: datetime
    requested_count: int = Field(ge=1)
    matched_count: int = Field(ge=0)
    missing_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    image_success_account_count: int = Field(ge=0)
    image_success_task_count: int = Field(ge=0)
    items: list[AccountEmailAuditItemView]


# END EMAIL AUDIT API


class AccountSyncResult(BaseModel):
    action: Literal["CREATED", "TOKEN_UPDATED"]
    account: AccountView


class AccountBalanceRefreshResult(BaseModel):
    valid: bool
    account: AccountView
    previous_balance_credits: int
    balance_credits: int
    credit_delta: int
    refreshed_at: datetime
    error_code: str | None = None


CookieImportBatchStatus = Literal[
    "QUEUED",
    "RUNNING",
    "COMPLETED",
    "PARTIAL_FAILED",
    "FAILED",
]
CookieImportItemStatus = Literal[
    "QUEUED",
    "RUNNING",
    "RETRY_WAIT",
    "CREATED",
    "UPDATED",
    "SKIPPED_DUPLICATE",
    "FAILED",
]
CookieImportStage = Literal[
    "RECEIVED",
    "SESSION_VALIDATION",
    "BALANCE_VALIDATION",
    "ACCOUNT_ACTIVATION",
    "RENEWAL_READY",
]


class CookieImportItemView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_uuid: UUID
    entry_name: str = Field(min_length=1, max_length=255)
    entry_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    expected_login_name: str | None = Field(default=None, max_length=255)
    discovered_login_name: str | None = Field(default=None, max_length=255)
    status: CookieImportItemStatus
    stage: CookieImportStage
    attempt_count: int = Field(ge=0)
    retryable: bool
    last_error_code: str | None = Field(default=None, max_length=64)
    last_error_message: str | None = Field(default=None, max_length=300)
    account_uuid: UUID | None = None
    account_status: str | None = Field(default=None, max_length=32)
    balance_credits: int | None = Field(default=None, ge=0)
    token_expires_at: datetime | None = None
    renewal_status: str | None = Field(default=None, max_length=32)
    activated_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class CookieImportBatchView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_uuid: UUID
    status: CookieImportBatchStatus
    archive_filename: str = Field(min_length=1, max_length=255)
    archive_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    space_name: str = Field(min_length=1, max_length=128)
    item_count: int = Field(ge=0)
    queued: int = Field(ge=0)
    running: int = Field(ge=0)
    created: int = Field(ge=0)
    updated: int = Field(ge=0)
    failed: int = Field(ge=0)
    total_balance_credits: int = Field(ge=0)
    tasks_after_import: int = Field(ge=0)
    completed_tasks_after_import: int = Field(ge=0)
    failed_tasks_after_import: int = Field(ge=0)
    consumed_credits_after_import: int = Field(ge=0)
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    items: list[CookieImportItemView] = Field(default_factory=list)


class CookieImportBatchList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batches: list[CookieImportBatchView]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


MailboxStatus = Literal[
    "PENDING_VALIDATION",
    "ACTIVE",
    "INVALID",
    "MANUAL_DISABLED",
]


class MailboxImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: SecretStr = Field(min_length=1, max_length=10 * 1024 * 1024)


class MailboxImportIssueView(BaseModel):
    line_number: int = Field(ge=1)
    email: str
    code: str
    reason: str


class MailboxImportResult(BaseModel):
    requested: int = Field(ge=0)
    imported: int = Field(ge=0)
    duplicates: int = Field(ge=0)
    invalid: int = Field(ge=0)
    blank_lines: int = Field(ge=0)
    issues: list[MailboxImportIssueView]


class MailboxView(BaseModel):
    mailbox_uuid: UUID
    email: str
    status: MailboxStatus
    disabled_reason: str | None
    validation_attempts: int
    next_validation_at: datetime | None
    last_validated_at: datetime | None
    last_error_code: str | None
    last_error_message: str | None
    last_message_received_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime


class MailboxList(BaseModel):
    items: list[MailboxView]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=200)
    offset: int = Field(ge=0)


class MailboxStats(BaseModel):
    total: int = Field(ge=0)
    pending_validation: int = Field(ge=0)
    active: int = Field(ge=0)
    invalid: int = Field(ge=0)
    manual_disabled: int = Field(ge=0)


class MailboxPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manual_status: Literal["PENDING_VALIDATION", "MANUAL_DISABLED"]
    expected_version: int = Field(ge=0)


class ParentAccountImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: SecretStr = Field(min_length=1, max_length=10 * 1024 * 1024)


class ParentAccountImportIssueView(BaseModel):
    line_number: int = Field(ge=1)
    email: str
    code: str
    reason: str


class ParentAccountImportResult(BaseModel):
    requested: int = Field(ge=0)
    imported: int = Field(ge=0)
    duplicates: int = Field(ge=0)
    invalid: int = Field(ge=0)
    blank_lines: int = Field(ge=0)
    issues: list[ParentAccountImportIssueView]


class ParentAccountView(BaseModel):
    parent_account_uuid: UUID
    email: str
    password: str
    invite_url: str
    invite_success_count: int = Field(ge=0)
    invite_failure_count: int = Field(ge=0)
    status: Literal["ACTIVE", "EXHAUSTED", "MANUAL_DISABLED"] = "ACTIVE"
    consecutive_150_count: int = Field(default=0, ge=0, le=3)
    exhausted_reason: str | None = None
    exhausted_at: datetime | None = None
    legacy_invite_success_count: int = Field(default=0, ge=0)
    legacy_invite_failure_count: int = Field(default=0, ge=0)
    running_registration_count: int = Field(default=0, ge=0)
    traceable_registration_count: int = Field(default=0, ge=0)
    promotable_registration_count: int = Field(default=0, ge=0)
    version: int = Field(default=0, ge=0)
    created_at: datetime
    updated_at: datetime


class ParentAccountList(BaseModel):
    items: list[ParentAccountView]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=200)
    offset: int = Field(ge=0)


class ParentAccountStats(BaseModel):
    total_parent_accounts: int = Field(ge=0)
    total_invite_successes: int = Field(ge=0)
    total_invite_failures: int = Field(ge=0)
    active_parent_accounts: int = Field(default=0, ge=0)
    exhausted_parent_accounts: int = Field(default=0, ge=0)
    traceable_registrations: int = Field(default=0, ge=0)
    promotable_registrations: int = Field(default=0, ge=0)
    legacy_invite_successes: int = Field(default=0, ge=0)
    legacy_invite_failures: int = Field(default=0, ge=0)


class ParentAccountInvitationResultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool


class CDPCookie(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        populate_by_name=True,
    )

    name: str = Field(min_length=1, max_length=256)
    value: SecretStr = Field(max_length=16384)
    domain: str = Field(min_length=1, max_length=255)
    path: str = Field(default="/", min_length=1, max_length=2048)
    expires: float | None = None
    http_only: bool = Field(default=False, alias="httpOnly")
    secure: bool = True
    same_site: Literal["Strict", "Lax", "None", "Unset"] = Field(default="Unset", alias="sameSite")


class RegistrationJobClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    client_id: str = Field(min_length=1, max_length=128)
    project_name: str = Field(default="Canvas", min_length=1, max_length=128)

    @field_validator("project_name")
    @classmethod
    def normalize_registration_project_name(cls, value: str) -> str:
        return normalize_project_name(value).display_name


class RegistrationJobHeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    client_id: str = Field(min_length=1, max_length=128)
    report_token: SecretStr = Field(min_length=32, max_length=512)


class RegistrationJobStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    client_id: str = Field(min_length=1, max_length=128)
    report_token: SecretStr = Field(min_length=32, max_length=512)


class RegistrationJobClaimResult(BaseModel):
    registration_uuid: UUID
    parent_account_uuid: UUID
    parent_email: str
    invite_url: str
    mailbox_uuid: UUID
    email: str
    project_name: str
    report_token: str
    lease_expires_at: datetime
    replayed: bool


class RegistrationJobStatusResult(BaseModel):
    registration_uuid: UUID
    status: RegistrationStatus
    awarded_points: int | None = Field(default=None, ge=0)
    validation_attempts: int = Field(ge=0)
    validation_error_code: str | None
    validation_error_message: str | None
    validation_finished_at: datetime | None
    updated_at: datetime


class RegistrationJobHeartbeatResult(BaseModel):
    registration_uuid: UUID
    status: str
    lease_expires_at: datetime
    last_heartbeat_at: datetime
    version: int = Field(ge=0)


class RegistrationJobResultResponse(BaseModel):
    registration_uuid: UUID
    status: str
    registered_email: str | None
    cookie_count: int = Field(ge=0, le=64)
    reported_at: datetime | None
    replayed: bool


class RegistrationJobResultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    client_id: str = Field(min_length=1, max_length=128)
    report_token: SecretStr = Field(min_length=32, max_length=512)
    status: Literal["REGISTERED", "FAILED"]
    registered_email: str | None = Field(default=None, max_length=255)
    user_agent: str = Field(default="", max_length=1024)
    accept_language: str = Field(default="en-US,en;q=0.9", max_length=512)
    cookies: list[CDPCookie] | None = Field(default=None, max_length=64)
    error_code: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    error_message: str | None = Field(default=None, max_length=1000)

    @field_validator("registered_email")
    @classmethod
    def normalize_registered_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if EMAIL_PATTERN.fullmatch(normalized) is None:
            raise ValueError("email format is invalid")
        return normalized

    @model_validator(mode="after")
    def validate_result_shape(self) -> RegistrationJobResultRequest:
        if self.status == "REGISTERED":
            if self.registered_email is None or not self.cookies:
                raise ValueError("registered result requires email and cookies")
            if self.error_code is not None or self.error_message is not None:
                raise ValueError("registered result cannot include failure fields")
        else:
            if self.error_code is None:
                raise ValueError("failed result requires error_code")
            if self.registered_email is not None or self.cookies is not None:
                raise ValueError("failed result cannot include registration material")
        return self


RegistrationStatus = Literal[
    "RUNNING",
    "COOKIE_REPORTED",
    "VALIDATING",
    "VALIDATION_RETRY_WAIT",
    "VALIDATION_FAILED",
    "FAILED",
    "SUCCEEDED",
]


class RegistrationRecordView(BaseModel):
    registration_uuid: UUID
    parent_account_uuid: UUID
    parent_email: str
    email: str
    client_id: str
    status: RegistrationStatus
    registered_email: str | None
    verified_email: str | None
    awarded_points: int | None = Field(default=None, ge=0)
    is_used: bool = False
    cookie_count: int = Field(ge=0, le=64)
    validation_attempts: int = Field(ge=0)
    validation_error_code: str | None
    validation_error_message: str | None
    started_at: datetime
    reported_at: datetime | None
    validation_finished_at: datetime | None
    promoted_at: datetime | None
    account_uuid: UUID | None
    promotable: bool = False
    cookie_status: Literal["RECEIVED", "VALIDATING", "VERIFIED", "INVALID"] = "RECEIVED"
    version: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime


REGISTRATION_COOKIE_EXPORT_MAX_EMAILS = 500


class RegistrationCookieExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    email: str | None = Field(default=None, min_length=3, max_length=255)
    emails: list[str] | None = Field(
        default=None,
        min_length=1,
        max_length=REGISTRATION_COOKIE_EXPORT_MAX_EMAILS,
    )

    @field_validator("email")
    @classmethod
    def normalize_export_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return cls._normalize_export_email(value)

    @field_validator("emails")
    @classmethod
    def normalize_export_emails(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        return list(dict.fromkeys(cls._normalize_export_email(value) for value in values))

    @model_validator(mode="after")
    def select_single_or_batch(self) -> RegistrationCookieExportRequest:
        if (self.email is None) == (self.emails is None):
            raise ValueError("provide exactly one of email or emails")
        return self

    @property
    def requested_emails(self) -> list[str]:
        if self.email is not None:
            return [self.email]
        return self.emails or []

    @property
    def is_batch(self) -> bool:
        return self.emails is not None

    @staticmethod
    def _normalize_export_email(value: str) -> str:
        normalized = value.lower()
        if not EMAIL_PATTERN.fullmatch(normalized) or any(
            character in normalized for character in ('"', "'", "/", "\\")
        ):
            raise ValueError("email must be a valid export filename email")
        return normalized


class RegistrationRecordList(BaseModel):
    items: list[RegistrationRecordView]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=200)
    offset: int = Field(ge=0)


RegistrationMonitorHealth = Literal["NORMAL", "ATTENTION", "ABNORMAL", "NO_ACTIVITY"]


class RegistrationMonitorWindow(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: datetime = Field(alias="from")
    to: datetime


class RegistrationClientHealthReason(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=255)


class RegistrationClientSummary(BaseModel):
    total_clients: int = Field(ge=0)
    active_clients: int = Field(ge=0)
    normal_clients: int = Field(ge=0)
    attention_clients: int = Field(ge=0)
    abnormal_clients: int = Field(ge=0)
    no_activity_clients: int = Field(ge=0)
    jobs: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    processing: int = Field(ge=0)


class RegistrationClientListItem(BaseModel):
    client_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=64)
    health: RegistrationMonitorHealth
    health_reasons: list[RegistrationClientHealthReason] = Field(max_length=8)
    last_activity_at: datetime | None
    jobs: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    processing: int = Field(ge=0)
    retry_wait: int = Field(ge=0)
    stalled: int = Field(ge=0)
    success_rate: float | None = Field(default=None, ge=0, le=1)
    average_duration_seconds: float | None = Field(default=None, ge=0)
    latest_error_code: str | None = Field(default=None, max_length=64)
    latest_error_message: str | None = Field(default=None, max_length=1000)


class RegistrationClientListResponse(BaseModel):
    server_now: datetime
    window: RegistrationMonitorWindow
    summary: RegistrationClientSummary
    items: list[RegistrationClientListItem]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


class RegistrationClientSeriesPoint(BaseModel):
    at: datetime
    claimed: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)


class RegistrationClientDetailResponse(BaseModel):
    server_now: datetime
    window: RegistrationMonitorWindow
    client: RegistrationClientListItem
    series: list[RegistrationClientSeriesPoint]


class ClientRegistrationTaskView(BaseModel):
    registration_uuid: UUID
    parent_account_uuid: UUID
    parent_email: str
    email: str
    client_id: str
    status: RegistrationStatus
    registered_email: str | None
    awarded_points: int | None = Field(default=None, ge=0)
    started_at: datetime
    lease_expires_at: datetime
    last_heartbeat_at: datetime | None
    reported_at: datetime | None
    validation_finished_at: datetime | None
    validation_lease_until: datetime | None
    retry_after: datetime | None
    duration_seconds: float | None = Field(default=None, ge=0)
    stalled: bool = False
    client_error_code: str | None = Field(default=None, max_length=64)
    client_error_message: str | None = Field(default=None, max_length=1000)
    validation_error_code: str | None = Field(default=None, max_length=64)
    validation_error_message: str | None = Field(default=None, max_length=1000)
    is_used: bool = False
    created_at: datetime
    updated_at: datetime


class ClientRegistrationTaskList(BaseModel):
    items: list[ClientRegistrationTaskView]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=200)
    offset: int = Field(ge=0)


class SuccessfulRegistrationRecordList(RegistrationRecordList):
    limit: int = Field(ge=1, le=500)
    unused_8500_count: int = Field(ge=0)


class RegistrationPoolSettingsView(BaseModel):
    target_space_uuid: UUID | None
    target_space_name: str | None
    target_space_status: str | None
    default_max_concurrency: int = Field(ge=1, le=100)
    promotion_available: bool
    version: int = Field(ge=0)
    updated_at: datetime


class RegistrationPoolSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_space_uuid: UUID | None
    default_max_concurrency: int = Field(ge=1, le=100)
    expected_version: int = Field(ge=0)


class RegistrationPromotionResult(BaseModel):
    registration_uuid: UUID
    account_uuid: UUID
    account_status: str
    target_space_uuid: UUID
    replayed: bool


class MailboxCodeQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=255)
    timeout_seconds: int = Field(default=60, ge=1, le=120)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if EMAIL_PATTERN.fullmatch(normalized) is None:
            raise ValueError("email format is invalid")
        return normalized


class AtomicMailCodeQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credential: SecretStr = Field(
        description="Single Atomic Mail credential line in 邮箱|密码 format",
    )
    timeout_seconds: int = Field(default=60, ge=1, le=60)


class RegistrationMailboxCodeQuery(BaseModel):
    """Look up the mailbox bound to a registration lease.

    The client never supplies an email address; the API resolves it from the
    registration record after validating the client/report token pair.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    registration_uuid: UUID
    client_id: str = Field(min_length=1, max_length=128)
    report_token: SecretStr = Field(min_length=32, max_length=512)
    timeout_seconds: int = Field(default=60, ge=1, le=120)


class MailboxCodeResult(BaseModel):
    email: str
    code: str = Field(min_length=4, max_length=8)
    received_at: datetime
    subject: str
    sender: str
    message_id: str
    matched_by: Literal["KEYWORD_NEARBY", "HTML_EMPHASIS", "NUMERIC_FALLBACK"]


class ProjectMailboxClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_name: str

    @field_validator("project_name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return normalize_project_name(value).display_name


class ProjectMailboxClaimResult(BaseModel):
    claim_uuid: UUID
    project_name: str
    mailbox_uuid: UUID
    email: str
    claimed_at: datetime
    replayed: bool


class AccountBulkSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_uuids: list[UUID] = Field(min_length=1, max_length=1000)

    @field_validator("account_uuids")
    @classmethod
    def deduplicate_account_uuids(cls, values: list[UUID]) -> list[UUID]:
        return list(dict.fromkeys(values))


class AccountLedgerRecord(BaseModel):
    """Complete child-account ledger row accepted by the account importer."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: UUID
    email: str = Field(min_length=3, max_length=255)
    password: SecretStr = Field(min_length=1, max_length=4096)
    registration_password: SecretStr = Field(
        alias="registrationPassword", min_length=1, max_length=4096
    )
    group_token: SecretStr = Field(alias="groupToken", min_length=1, max_length=16384)
    authorization_token: SecretStr = Field(
        alias="authorizationToken", min_length=1, max_length=16384
    )
    parent_account_id: str | None = Field(alias="parentAccountId", default=None, max_length=255)
    parent_account: str | None = Field(alias="parentAccount", default=None, max_length=255)
    invite_status: str | None = Field(alias="inviteStatus", default=None, max_length=32)
    invited_at: datetime | None = Field(alias="invitedAt", default=None)
    invite_error: str | None = Field(alias="inviteError", default=None, max_length=1000)
    invite_attempts: int = Field(alias="inviteAttempts", default=0, ge=0)
    registration_status: str | None = Field(alias="registrationStatus", default=None, max_length=32)
    registration_attempts: int = Field(alias="registrationAttempts", default=0, ge=0)
    registered_at: datetime | None = Field(alias="registeredAt", default=None)
    registration_account_id: str | None = Field(
        alias="registrationAccountId", default=None, max_length=255
    )
    registration_error: str | None = Field(alias="registrationError", default=None, max_length=1000)
    credits_total: int | None = Field(alias="creditsTotal", default=None, ge=0)
    credits_subscription: int | None = Field(alias="creditsSubscription", default=None, ge=0)
    credits_purchase: int | None = Field(alias="creditsPurchase", default=None, ge=0)
    credits_rollover: int | None = Field(alias="creditsRollover", default=None, ge=0)
    credits_checked_at: datetime | None = Field(alias="creditsCheckedAt", default=None)
    credits_error: str | None = Field(alias="creditsError", default=None, max_length=1000)
    credits_attempts: int = Field(alias="creditsAttempts", default=0, ge=0)
    source_created_at: datetime | None = Field(alias="createdAt", default=None)
    source_updated_at: datetime | None = Field(alias="updatedAt", default=None)

    @field_validator(
        "invited_at",
        "registered_at",
        "credits_checked_at",
        "source_created_at",
        "source_updated_at",
    )
    @classmethod
    def normalize_ledger_datetime(cls, value: datetime | None) -> datetime | None:
        return normalize_datetime(value)

    def source_document(self) -> dict[str, Any]:
        document = self.model_dump(mode="json", by_alias=True)
        document["password"] = self.password.get_secret_value()
        document["registrationPassword"] = self.registration_password.get_secret_value()
        document["groupToken"] = self.group_token.get_secret_value()
        document["authorizationToken"] = self.authorization_token.get_secret_value()
        return document


class AccountLedgerImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    space_uuid: UUID
    source: str = Field(min_length=1, max_length=255)
    source_kind: str = Field(min_length=1, max_length=64)
    source_raw: bool
    source_exported_at: datetime
    source_count: int = Field(ge=1)
    source_file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    records: list[AccountLedgerRecord] = Field(min_length=1, max_length=500)

    @field_validator("source_exported_at")
    @classmethod
    def normalize_source_exported_at(cls, value: datetime) -> datetime:
        normalized = normalize_datetime(value)
        assert normalized is not None
        return normalized

    @model_validator(mode="after")
    def records_are_unique(self) -> AccountLedgerImportRequest:
        emails: set[str] = set()
        source_ids: set[UUID] = set()
        for record in self.records:
            email = record.email.strip().lower()
            if email in emails:
                raise ValueError(f"duplicate ledger email: {email}")
            if record.id in source_ids:
                raise ValueError(f"duplicate ledger source id: {record.id}")
            emails.add(email)
            source_ids.add(record.id)
        return self


class AccountLedgerProfileView(BaseModel):
    source: str
    source_kind: str
    source_raw: bool
    source_exported_at: datetime
    source_count: int
    source_file_sha256: str
    source_record_id: UUID
    parent_account_id: str | None
    parent_account: str | None
    invite_status: str | None
    invited_at: datetime | None
    invite_error: str | None
    invite_attempts: int
    registration_status: str | None
    registration_attempts: int
    registered_at: datetime | None
    registration_account_id: str | None
    registration_error: str | None
    credits_total: int | None
    credits_subscription: int | None
    credits_purchase: int | None
    credits_rollover: int | None
    credits_checked_at: datetime | None
    credits_error: str | None
    credits_attempts: int
    source_created_at: datetime | None
    source_updated_at: datetime | None
    has_registration_password: bool
    has_group_token: bool
    has_authorization_token: bool
    raw_record_sha256: str


class AccountLedgerImportItem(BaseModel):
    action: Literal["CREATED", "UPDATED"]
    account_uuid: UUID
    login_name: str
    source_record_id: UUID
    credits_total: int | None


class AccountLedgerImportResult(BaseModel):
    requested: int
    created: int
    updated: int
    items: list[AccountLedgerImportItem]


class AccountBulkDeleteRequest(AccountBulkSelection):
    export_receipt: str = Field(min_length=32, max_length=4096)


class AccountBulkDeleteItem(BaseModel):
    account_uuid: UUID
    login_name: str | None = None
    outcome: Literal["DELETABLE", "PROTECTED", "MISSING", "DELETED", "SKIPPED"]
    code: str | None = None
    message: str | None = None


class AccountBulkDeletePreview(BaseModel):
    requested: int
    deletable: int
    protected: int
    missing: int
    items: list[AccountBulkDeleteItem]


class AccountBulkDeleteResult(BaseModel):
    requested: int
    deleted: int
    skipped: int
    items: list[AccountBulkDeleteItem]


class LoginJobClaimRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    worker_id: str = Field(min_length=1, max_length=128)
    limit: int = Field(default=1, ge=1, le=100)


class LoginJobItem(BaseModel):
    job_uuid: UUID
    job_type: Literal["ACTIVATE_NEW", "RENEW_TOKEN", "REFRESH_SESSION"]
    account_uuid: UUID
    login_name: str
    password: str
    previous_token_expires_at: datetime | None
    lease_token: str
    lease_expires_at: datetime


class LoginPoolSnapshot(BaseModel):
    watermark_mode: Literal["ACTIVE_CREDIT_SUM"] = "ACTIVE_CREDIT_SUM"
    credit_target: int
    active_credit_total: int
    credit_deficit: int
    below_watermark: bool
    activation_in_flight: int
    # Compatibility-only count metrics for existing desktop installations.
    idle_target: int
    available_idle: int
    in_flight_idle: int
    effective_idle: int
    activation_budget_before_claim: int
    renewal_claimed: int
    activation_claimed: int
    new_account_dispatch_suppressed: bool


class LoginJobClaimResponse(BaseModel):
    jobs: list[LoginJobItem]
    pool: LoginPoolSnapshot


class LoginWorkerStatus(BaseModel):
    status: Literal["ready"] = "ready"
    watermark_mode: Literal["ACTIVE_CREDIT_SUM"] = "ACTIVE_CREDIT_SUM"
    credit_target: int
    idle_target: int
    renewal_window_seconds: int
    lease_seconds: int
    max_batch_size: int


class LoginJobLeaseRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    worker_id: str = Field(min_length=1, max_length=128)
    lease_token: SecretStr = Field(min_length=32, max_length=512)


class LoginJobHeartbeatResponse(BaseModel):
    job_uuid: UUID
    status: str
    lease_expires_at: datetime


class LoginJobTokenReport(LoginJobLeaseRequest):
    video_token: SecretStr = Field(min_length=1, max_length=16384)
    token_expires_at: datetime
    balance_credits: int | None = Field(default=None, ge=0)
    renewal_session: RenewalSessionPayload | None = None

    @field_validator("token_expires_at")
    @classmethod
    def expiry_is_future(cls, value: datetime) -> datetime:
        normalized = normalize_datetime(value)
        assert normalized is not None
        if normalized <= datetime.now(UTC).replace(tzinfo=None):
            raise ValueError("token_expires_at must be in the future")
        return normalized


class LoginJobFailureReport(LoginJobLeaseRequest):
    error_code: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    error_message: str | None = Field(default=None, max_length=1000)
    retryable: bool = True


class LoginJobView(BaseModel):
    job_uuid: UUID
    account_uuid: UUID
    job_type: str
    status: str
    attempt_no: int
    lease_owner: str
    lease_expires_at: datetime | None
    token_received_at: datetime | None
    validation_finished_at: datetime | None
    retry_after: datetime | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class H3InputBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=H3_PROMPT_MAX_CHARS)
    duration: int = Field(default=5, ge=5, le=15)
    resolution: Literal["768P", "2K"] = "2K"

    @field_validator("prompt", mode="before")
    @classmethod
    def truncate_prompt_for_provider(cls, value: Any) -> Any:
        if isinstance(value, str):
            return normalize_h3_prompt(value)
        return value


class H3TextToVideoInput(H3InputBase):
    aspect_ratio: Literal["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"] = "16:9"


class H3ImageToVideoInput(H3InputBase):
    image_url: HttpUrl
    end_image_url: HttpUrl | None = None


class H3ReferenceToVideoInput(H3InputBase):
    aspect_ratio: Literal["adaptive", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16"] = "adaptive"
    reference_image_urls: list[HttpUrl] = Field(default_factory=list, max_length=9)
    reference_video_urls: list[HttpUrl] = Field(default_factory=list, max_length=3)
    reference_audio_urls: list[HttpUrl] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def validate_reference_counts(self) -> H3ReferenceToVideoInput:
        total = (
            len(self.reference_image_urls)
            + len(self.reference_video_urls)
            + len(self.reference_audio_urls)
        )
        if total > 12:
            raise ValueError("reference images, videos, and audio must total at most 12 files")
        if self.reference_audio_urls and not (
            self.reference_image_urls or self.reference_video_urls
        ):
            raise ValueError("reference audio requires at least one reference image or video")
        return self


SeedanceAspectRatio = Literal["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"]
SeedanceResolution = Literal["480P", "720P", "1080P", "4K"]


class SeedanceInputBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=7000)
    duration: int = Field(default=4, ge=4, le=15)
    resolution: SeedanceResolution = "480P"
    aspect_ratio: SeedanceAspectRatio = "16:9"


class SeedanceTextToVideoInput(SeedanceInputBase):
    pass


class SeedanceImageToVideoInput(SeedanceInputBase):
    image_url: HttpUrl
    end_image_url: HttpUrl | None = None


class SeedanceReferenceToVideoInput(SeedanceInputBase):
    reference_image_urls: list[HttpUrl] = Field(default_factory=list, max_length=4)
    reference_video_urls: list[HttpUrl] = Field(default_factory=list, max_length=3)
    reference_audio_urls: list[HttpUrl] = Field(default_factory=list, max_length=1)

    @model_validator(mode="after")
    def validate_omni_references(self) -> SeedanceReferenceToVideoInput:
        if not (self.reference_image_urls or self.reference_video_urls):
            raise ValueError("Seedance omni mode requires an image or video reference")
        return self


Seedance25Resolution = Literal["480P", "720P"]
SeedanceReferenceStrength = Literal["LOW", "MID", "HIGH"]


class Seedance25ReferenceImage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: HttpUrl
    strength: SeedanceReferenceStrength = "MID"


class Seedance25InputBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=5000)
    duration: int = Field(default=8, ge=4, le=30)
    resolution: Seedance25Resolution = "720P"
    aspect_ratio: SeedanceAspectRatio = "16:9"
    audio: bool = True


class Seedance25TextToVideoInput(Seedance25InputBase):
    pass


class Seedance25ImageToVideoInput(Seedance25InputBase):
    image_url: HttpUrl
    end_image_url: HttpUrl | None = None


class Seedance25ReferenceToVideoInput(Seedance25InputBase):
    reference_images: list[Seedance25ReferenceImage] = Field(
        default_factory=list,
        max_length=30,
    )
    reference_image_urls: list[HttpUrl] = Field(default_factory=list, max_length=30)
    reference_video_urls: list[HttpUrl] = Field(default_factory=list, max_length=10)
    reference_audio_urls: list[HttpUrl] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def validate_omni_references(self) -> Seedance25ReferenceToVideoInput:
        if self.reference_images and self.reference_image_urls:
            raise ValueError("use reference_images or reference_image_urls, not both")
        if not (self.reference_images or self.reference_image_urls or self.reference_video_urls):
            raise ValueError("Seedance 2.5 omni mode requires an image or video reference")
        return self


KlingO3AspectRatio = Literal["16:9", "1:1", "9:16"]
KlingO3Resolution = Literal["720P", "1080P", "4K"]


class KlingO3InputBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=1500)
    duration: int = Field(default=3, ge=3, le=15)
    resolution: KlingO3Resolution = "1080P"
    aspect_ratio: KlingO3AspectRatio = "16:9"
    audio: bool = True


class KlingO3TextToVideoInput(KlingO3InputBase):
    pass


class KlingO3ImageToVideoInput(KlingO3InputBase):
    image_url: HttpUrl
    end_image_url: HttpUrl | None = None


class KlingO3ReferenceToVideoInput(KlingO3InputBase):
    reference_image_urls: list[HttpUrl] = Field(default_factory=list, max_length=7)
    reference_video_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_references(self) -> KlingO3ReferenceToVideoInput:
        if not (self.reference_image_urls or self.reference_video_id):
            raise ValueError("Kling O3 reference mode requires image references or a video id")
        if self.reference_video_id and len(self.reference_image_urls) > 4:
            raise ValueError("Kling O3 accepts at most 4 images with a video reference")
        if self.reference_video_id and self.duration > 10:
            raise ValueError("Kling O3 video-reference duration is limited to 10 seconds")
        if self.reference_image_urls and self.resolution == "4K":
            raise ValueError("Kling O3 image references are incompatible with 4K")
        return self


GeminiOmniFlashAspectRatio = Literal["16:9", "9:16"]


class GeminiOmniFlashInputBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=GEMINI_OMNI_FLASH_PROMPT_MAX_CHARS)
    duration: int = Field(default=5, ge=3, le=10)
    resolution: Literal["720P"] = "720P"
    aspect_ratio: GeminiOmniFlashAspectRatio = "16:9"


class GeminiOmniFlashTextToVideoInput(GeminiOmniFlashInputBase):
    pass


class GeminiOmniFlashReferenceToVideoInput(GeminiOmniFlashInputBase):
    reference_image_urls: list[HttpUrl] = Field(min_length=1, max_length=5)


Veo31AspectRatio = Literal["16:9", "9:16"]
Veo31Resolution = Literal["720P", "1080P", "4K"]
Veo31ReferenceStrength = Literal["LOW", "MID", "HIGH"]


class Veo31InputBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=VEO_3_1_PROMPT_MAX_CHARS)
    duration: Literal[4, 6, 8] = 8
    resolution: Veo31Resolution = "720P"
    aspect_ratio: Veo31AspectRatio = "16:9"
    audio: bool = True
    negative_prompt: str | None = Field(default=None, max_length=1000)
    seed: int | None = Field(default=None, ge=0, le=4294967295)


class Veo31TextToVideoInput(Veo31InputBase):
    pass


class Veo31ImageToVideoInput(Veo31InputBase):
    image_url: HttpUrl
    end_image_url: HttpUrl | None = None


class Veo31ReferenceToVideoInput(Veo31InputBase):
    reference_image_urls: list[HttpUrl] = Field(min_length=1, max_length=3)
    reference_strength: Veo31ReferenceStrength = "MID"


GPTImage2AspectRatio = Literal[
    "21:9", "16:9", "3:2", "4:3", "5:4", "1:1", "4:5", "3:4", "2:3", "9:16"
]
GPTImage2Quality = Literal["LOW", "MEDIUM", "HIGH"]
GPTImage2Size = Literal["SMALL", "MEDIUM", "LARGE"]


class GPTImage2InputBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=7000)
    quality: GPTImage2Quality = "MEDIUM"
    aspect_ratio: GPTImage2AspectRatio = "1:1"
    size: GPTImage2Size = "SMALL"
    resolution: str | None = Field(default=None, pattern=r"^\d{3,4}[x×]\d{3,4}$")

    @model_validator(mode="after")
    def validate_and_resolve_dimensions(self) -> GPTImage2InputBase:
        expected = gpt_image_2_resolution(self.aspect_ratio, self.size)
        if self.resolution is not None and self.resolution.replace("×", "x") != expected:
            raise ValueError("resolution must match the selected GPT Image 2 aspect_ratio and size")
        self.resolution = expected
        return self


class GPTImage2TextToImageInput(GPTImage2InputBase):
    pass


class GPTImage2ImageToImageInput(GPTImage2InputBase):
    reference_image_urls: list[HttpUrl] = Field(min_length=1, max_length=6)


NanoImageAspectRatio = Literal[
    "21:9", "16:9", "3:2", "4:3", "5:4", "1:1", "4:5", "3:4", "2:3", "9:16"
]
NanoImageSize = Literal["SMALL", "MEDIUM", "LARGE"]


class NanoImageInputBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=7000)
    aspect_ratio: NanoImageAspectRatio = "1:1"
    size: NanoImageSize = "SMALL"
    resolution: str | None = Field(default=None, pattern=r"^\d{3,4}[x×]\d{3,4}$")

    @model_validator(mode="after")
    def validate_and_resolve_dimensions(self) -> NanoImageInputBase:
        expected = nano_image_resolution(self.aspect_ratio, self.size)
        if self.resolution is not None and self.resolution.replace("×", "x") != expected:
            raise ValueError("resolution must match the selected Nano image aspect_ratio and size")
        self.resolution = expected
        return self


class NanoImageTextToImageInput(NanoImageInputBase):
    pass


class NanoImageImageToImageInput(NanoImageInputBase):
    reference_image_urls: list[HttpUrl] = Field(min_length=1, max_length=6)


class SeedAudioTextToSpeechInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=SEED_AUDIO_PROMPT_MAX_CHARS)
    voice_id: str = Field(
        default=SEED_AUDIO_DEFAULT_VOICE_ID,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    speed: float = Field(default=1.0, ge=0.5, le=2.0, multiple_of=0.05)
    volume: float = Field(default=1.0, ge=0.5, le=2.0, multiple_of=0.05)
    pitch: int = Field(default=0, ge=-12, le=12)
    quantity: Literal[1, 2, 3, 4] = 1

    @field_validator("prompt", mode="before")
    @classmethod
    def strip_prompt(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


H3_INPUT_MODELS: dict[str, type[H3InputBase]] = {
    "text-to-video": H3TextToVideoInput,
    "image-to-video": H3ImageToVideoInput,
    "reference-to-video": H3ReferenceToVideoInput,
}

SEEDANCE_INPUT_MODELS: dict[str, type[SeedanceInputBase]] = {
    "text-to-video": SeedanceTextToVideoInput,
    "image-to-video": SeedanceImageToVideoInput,
    "reference-to-video": SeedanceReferenceToVideoInput,
}

SEEDANCE_25_INPUT_MODELS: dict[str, type[Seedance25InputBase]] = {
    "text-to-video": Seedance25TextToVideoInput,
    "image-to-video": Seedance25ImageToVideoInput,
    "reference-to-video": Seedance25ReferenceToVideoInput,
}

KLING_O3_INPUT_MODELS: dict[str, type[KlingO3InputBase]] = {
    "text-to-video": KlingO3TextToVideoInput,
    "image-to-video": KlingO3ImageToVideoInput,
    "reference-to-video": KlingO3ReferenceToVideoInput,
}

GEMINI_OMNI_FLASH_INPUT_MODELS: dict[str, type[GeminiOmniFlashInputBase]] = {
    "text-to-video": GeminiOmniFlashTextToVideoInput,
    "reference-to-video": GeminiOmniFlashReferenceToVideoInput,
}

VEO_3_1_INPUT_MODELS: dict[str, type[Veo31InputBase]] = {
    "text-to-video": Veo31TextToVideoInput,
    "image-to-video": Veo31ImageToVideoInput,
    "reference-to-video": Veo31ReferenceToVideoInput,
}

GPT_IMAGE_2_INPUT_MODELS: dict[str, type[GPTImage2InputBase]] = {
    "text-to-image": GPTImage2TextToImageInput,
    "image-to-image": GPTImage2ImageToImageInput,
}

NANO_IMAGE_INPUT_MODELS: dict[str, type[NanoImageInputBase]] = {
    "text-to-image": NanoImageTextToImageInput,
    "image-to-image": NanoImageImageToImageInput,
}

SEED_AUDIO_INPUT_MODELS: dict[str, type[SeedAudioTextToSpeechInput]] = {
    "text-to-speech": SeedAudioTextToSpeechInput,
}


class LegacyTaskInput(RootModel[dict[str, Any]]):
    pass


TaskInput = (
    LegacyTaskInput
    | H3TextToVideoInput
    | H3ImageToVideoInput
    | H3ReferenceToVideoInput
    | SeedanceTextToVideoInput
    | SeedanceImageToVideoInput
    | SeedanceReferenceToVideoInput
    | Seedance25TextToVideoInput
    | Seedance25ImageToVideoInput
    | Seedance25ReferenceToVideoInput
    | KlingO3TextToVideoInput
    | KlingO3ImageToVideoInput
    | KlingO3ReferenceToVideoInput
    | GeminiOmniFlashTextToVideoInput
    | GeminiOmniFlashReferenceToVideoInput
    | Veo31TextToVideoInput
    | Veo31ImageToVideoInput
    | Veo31ReferenceToVideoInput
    | GPTImage2TextToImageInput
    | GPTImage2ImageToImageInput
    | NanoImageTextToImageInput
    | NanoImageImageToImageInput
    | SeedAudioTextToSpeechInput
)


FORBIDDEN_INPUT_KEYS = {
    "authorization",
    "cookie",
    "password",
    "token",
    "video_token",
    "access_token",
    "refresh_token",
}


def contains_forbidden_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in FORBIDDEN_INPUT_KEYS or contains_forbidden_key(child):
                return True
    if isinstance(value, list):
        return any(contains_forbidden_key(child) for child in value)
    return False


class TaskCreate(BaseModel):
    provider: str = Field(default="leonardo", min_length=1, max_length=32)
    task_type: str = Field(default="VIDEO_GENERATION", min_length=1, max_length=32)
    model: str = Field(min_length=1, max_length=64)
    mode: (
        H3Mode
        | Literal[
            "omni",
            "omini",
            "text-to-image",
            "image-to-image",
            "text-to-speech",
        ]
        | None
    ) = None
    input: TaskInput
    priority: int = Field(default=0, ge=-100, le=100)
    estimated_credit_cost: int = Field(default=0, ge=0)

    @field_validator("input", mode="before")
    @classmethod
    def preserve_raw_input(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return LegacyTaskInput(root=value)
        return value

    @field_validator("input")
    @classmethod
    def reject_credentials(cls, value: TaskInput) -> TaskInput:
        document = value.model_dump(mode="json")
        if contains_forbidden_key(document):
            raise ValueError("task input contains a forbidden credential field")
        return value

    def input_document(self) -> dict[str, Any]:
        if isinstance(self.input, BaseModel):
            return self.input.model_dump(mode="json", exclude_none=True)
        return cast(dict[str, Any], self.input)

    @model_validator(mode="after")
    def validate_typed_generation_input(self) -> TaskCreate:
        if self.mode is None:
            self.input = cast(TaskInput, self.input_document())
            return self
        if self.provider.lower() != "leonardo":
            raise ValueError("typed generation modes currently use provider=leonardo")

        model = self.model.lower()
        mode = self.mode
        document = self.input_document()

        if is_seed_audio_model(model):
            if self.task_type.upper() != "AUDIO_GENERATION":
                raise ValueError("Seed Audio 1.0 requires task_type=AUDIO_GENERATION")
            if mode not in SEED_AUDIO_INPUT_MODELS:
                raise ValueError("Seed Audio 1.0 mode must be text-to-speech")
            parsed_audio = SEED_AUDIO_INPUT_MODELS[mode].model_validate(document)
            self.input = cast(
                TaskInput,
                parsed_audio.model_dump(mode="json", exclude_none=True),
            )
            quote = quote_credit_cost(self.model, self.input_document())
            if quote is not None:
                self.estimated_credit_cost = quote
            return self

        if is_gpt_image_2_model(model):
            if self.task_type.upper() != "IMAGE_GENERATION":
                raise ValueError("GPT Image 2 requires task_type=IMAGE_GENERATION")
            if mode not in GPT_IMAGE_2_INPUT_MODELS:
                raise ValueError("GPT Image 2 mode must be text-to-image or image-to-image")
            parsed_image = GPT_IMAGE_2_INPUT_MODELS[mode].model_validate(document)
            self.input = cast(
                TaskInput,
                parsed_image.model_dump(mode="json", exclude_none=True),
            )
            quote = quote_credit_cost(self.model, self.input_document())
            if quote is not None:
                self.estimated_credit_cost = quote
            return self

        if is_nano_image_model(model):
            if self.task_type.upper() != "IMAGE_GENERATION":
                raise ValueError("Nano image models require task_type=IMAGE_GENERATION")
            if mode not in NANO_IMAGE_INPUT_MODELS:
                raise ValueError("Nano image mode must be text-to-image or image-to-image")
            parsed_nano = NANO_IMAGE_INPUT_MODELS[mode].model_validate(document)
            self.input = cast(
                TaskInput,
                parsed_nano.model_dump(mode="json", exclude_none=True),
            )
            quote = quote_credit_cost(self.model, self.input_document())
            if quote is not None:
                self.estimated_credit_cost = quote
            return self

        if self.task_type.upper() != "VIDEO_GENERATION":
            raise ValueError("typed video modes require task_type=VIDEO_GENERATION")
        if mode in {"omni", "omini"}:
            if not (
                is_seedance_model(model)
                or is_kling_o3_model(model)
                or is_gemini_omni_flash_model(model)
            ):
                raise ValueError(
                    "omni mode is available for Seedance, Kling O3, and Gemini Omni Flash models"
                )
            mode = "reference-to-video"
            self.mode = mode

        if mode not in H3_INPUT_MODELS:
            raise ValueError("video models require a supported video generation mode")

        if model == "hailuo-03":
            parsed = H3_INPUT_MODELS[mode].model_validate(document)
        elif is_seedance_25_model(model):
            parsed = SEEDANCE_25_INPUT_MODELS[mode].model_validate(document)
        elif is_seedance_model(model):
            parsed = SEEDANCE_INPUT_MODELS[mode].model_validate(document)
            resolution = str(parsed.resolution)
            if resolution not in SEEDANCE_MODEL_RESOLUTIONS[model]:
                raise ValueError(f"{model} does not expose the {resolution} resolution tier")
        elif is_kling_o3_model(model):
            parsed = KLING_O3_INPUT_MODELS[mode].model_validate(document)
        elif is_gemini_omni_flash_model(model):
            if mode not in GEMINI_OMNI_FLASH_INPUT_MODELS:
                raise ValueError(
                    "Gemini Omni Flash mode must be text-to-video or reference-to-video"
                )
            parsed = GEMINI_OMNI_FLASH_INPUT_MODELS[mode].model_validate(document)
        elif is_veo_3_1_model(model):
            if mode not in VEO_3_1_MODEL_MODES[model]:
                raise ValueError(f"{model} does not expose the {mode} mode")
            parsed = VEO_3_1_INPUT_MODELS[mode].model_validate(document)
            resolution = str(parsed.resolution)
            if resolution not in VEO_3_1_MODEL_RESOLUTIONS[model]:
                raise ValueError(f"{model} does not expose the {resolution} resolution tier")
        else:
            raise ValueError("typed video modes require a supported Leonardo video model")
        self.input = cast(
            TaskInput,
            parsed.model_dump(mode="json", exclude_none=True),
        )
        quote = quote_credit_cost(self.model, self.input_document())
        if quote is not None:
            self.estimated_credit_cost = quote
        return self


class TaskProgress(BaseModel):
    phase: str
    resolved_assets: int
    total_assets: int


class TaskView(BaseModel):
    task_uuid: UUID
    idempotency_key: str | None
    provider: str
    upstream_task_id: str | None
    account_uuid: UUID | None
    space_uuid: UUID | None
    task_type: str
    model: str
    mode: str | None
    input_schema_version: str
    input: dict[str, Any]
    output: dict[str, Any] | None
    status: str
    priority: int
    estimated_credit_cost: int
    reserved_credit_cost: int
    actual_credit_cost: int | None
    submit_attempts: int
    sync_attempts: int
    error_code: str | None
    error_message: str | None
    progress: TaskProgress
    created_at: datetime
    queued_at: datetime
    assigned_at: datetime | None
    upstream_submitted_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime


class TaskList(BaseModel):
    items: list[TaskView]
    total: int
    models: list[str]


class ModelCatalogItem(BaseModel):
    id: str
    type: str
    rank: int
    title: str
    description: str | None
    url: str
    image_url: str | None
    video_url: str | None
    model: str | None


class ModelCatalogResponse(BaseModel):
    provider: str
    source: str
    items: list[ModelCatalogItem]
    total: int


class CountByStatus(BaseModel):
    status: str
    count: int


class DailyTaskMetric(BaseModel):
    date: str
    total: int
    completed: int
    failed: int
    credits: int


class ModelMetric(BaseModel):
    model: str
    total: int
    completed: int
    credits: int


class AccountMetrics(BaseModel):
    total: int
    active: int
    attention: int
    low_balance: int
    expiring_24h: int
    balance_credits: int
    available_credits: int
    reserved_credits: int
    active_tasks: int
    max_concurrency: int
    effective_max_concurrency: int
    effective_available_concurrency: int
    active_balance_credits: int
    active_credit_target: int


class TaskMetrics(BaseModel):
    total: int
    queued: int
    running: int
    completed: int
    failed: int
    canceled: int
    success_rate: float
    consumed_credits: int
    average_duration_seconds: float | None


class TaskTrendMetric(BaseModel):
    bucket_start: datetime
    label: str
    total: int
    completed: int
    failed: int
    credits: int


class DashboardStats(BaseModel):
    generated_at: datetime
    period: Literal["total", "today", "hour"]
    period_started_at: datetime | None
    timezone_offset_minutes: int
    trend_granularity: Literal["day", "hour", "five_minutes"]
    accounts: AccountMetrics
    tasks: TaskMetrics
    account_statuses: list[CountByStatus]
    task_statuses: list[CountByStatus]
    daily_tasks: list[DailyTaskMetric]
    task_trend: list[TaskTrendMetric]
    models: list[ModelMetric]


class ProtocolRenewalHealthReason(BaseModel):
    code: str
    value: float | None = None
    threshold: float | None = None


class ProtocolRenewalHealth(BaseModel):
    state: Literal["HEALTHY", "HEALTHY_IDLE", "DEGRADED", "DOWN", "DISABLED"]
    label: str
    reasons: list[ProtocolRenewalHealthReason]
    enabled: bool
    last_heartbeat_at: datetime | None
    last_scan_at: datetime | None
    last_completed_at: datetime | None


class ProtocolRenewalAttemptMetrics(BaseModel):
    total: int
    applied_success: int
    failed: int
    stale: int
    strict_success_rate: float | None
    average_latency_ms: float | None
    average_extension_seconds: float | None
    last_success_at: datetime | None


class ProtocolRenewalQueueMetrics(BaseModel):
    pending: int
    running: int
    retry: int
    fallback: int
    expired_leases: int
    oldest_due_age_seconds: int | None


class ProtocolRenewalCoverageMetrics(BaseModel):
    session_accounts: int
    eligible_accounts: int
    ratio: float


class ProtocolRenewalTrendMetric(BaseModel):
    bucket_start: datetime
    label: str
    total: int
    applied_success: int
    failed: int
    strict_success_rate: float | None


class ProtocolRenewalErrorMetric(BaseModel):
    error_code: str
    count: int


class ProtocolRenewalStats(BaseModel):
    generated_at: datetime
    period: Literal["hour", "six_hours", "day", "week"]
    period_started_at: datetime
    timezone_offset_minutes: int
    target_success_rate: float
    health: ProtocolRenewalHealth
    attempts: ProtocolRenewalAttemptMetrics
    queue: ProtocolRenewalQueueMetrics
    coverage: ProtocolRenewalCoverageMetrics
    trend: list[ProtocolRenewalTrendMetric]
    errors: list[ProtocolRenewalErrorMetric]


class ProtocolRenewalAccountView(BaseModel):
    account_uuid: UUID
    login_name: str
    account_status: str
    token_expires_at: datetime | None
    has_session: bool
    status: str
    attempt_count: int
    lease_until: datetime | None
    retry_after: datetime | None
    fallback_after: datetime | None
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    last_error_code: str | None
    previous_token_expires_at: datetime | None
    renewed_token_expires_at: datetime | None
    client_reported_at: datetime | None
    client_version: str | None
    renewal_capability: str | None
    client_session_fresh: bool


class ProtocolRenewalAccountList(BaseModel):
    items: list[ProtocolRenewalAccountView]
    total: int


class ProtocolRenewalEventView(BaseModel):
    event_uuid: UUID
    account_uuid: UUID
    attempt_number: int
    outcome: str
    applied: bool
    retryable: bool
    next_state: str
    error_code: str | None
    started_at: datetime
    finished_at: datetime
    latency_ms: int
    previous_token_expires_at: datetime | None
    renewed_token_expires_at: datetime | None


class ProtocolRenewalEventList(BaseModel):
    items: list[ProtocolRenewalEventView]
    total: int


class ErrorBody(BaseModel):
    code: str
    message: str
