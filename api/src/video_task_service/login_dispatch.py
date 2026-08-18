from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import or_

from video_task_service.models import Account

LoginJobType = Literal["ACTIVATE_NEW", "RENEW_TOKEN", "REFRESH_SESSION"]
LoginFailureDisposition = Literal["RETRY_ACCOUNT", "WORKER_BACKOFF", "QUARANTINE"]

ACTIVE_LOGIN_JOB_STATUSES = ("LEASED", "VALIDATING")
RENEWAL_ACCOUNT_STATUSES = ("TOKEN_EXPIRED", "TOKEN_EXPIRING", "TOKEN_INVALID")
LOGIN_EXCLUDED_ACCOUNT_STATUSES = (
    "MANUAL_DISABLED",
)
WORKER_INFRASTRUCTURE_LOGIN_ERROR_CODES = frozenset(
    {
        "EDQUOT",
        "ENOSPC",
        "LOGIN_JOB_HTTP_ERROR",
        "LOGIN_JOB_LEASE_EXPIRED",
        "LOGIN_JOB_REQUEST_FAILED",
        "LOGIN_JOB_UNREACHABLE",
    }
)
STALLED_LOGIN_ERROR_CODES = frozenset(
    {"LOGIN_JOB_VALIDATION_TIMEOUT", "LOGIN_STALLED", "TIMEOUT"}
)


def is_known_low_balance(
    balance_synced_at: datetime | None,
    balance_credits: int,
    threshold: int,
) -> bool:
    """Unknown imported balances are eligible; a measured low balance is not."""

    return balance_synced_at is not None and balance_credits < threshold


def renewal_balance_is_available(
    balance_synced_at: datetime | None,
    balance_credits: int,
) -> bool:
    """Keep renewing until a synchronized balance confirms exhaustion."""

    return balance_synced_at is None or balance_credits > 0


def renewal_balance_available_clause() -> object:
    """SQL equivalent of :func:`renewal_balance_is_available`."""

    return or_(Account.balance_synced_at.is_(None), Account.balance_credits > 0)


def login_job_type_for(
    account: Account,
    *,
    now: datetime,
    renewal_window_seconds: int,
    low_balance_threshold: int,
) -> LoginJobType | None:
    if getattr(account, "credential_source", "PASSWORD") == "COOKIE_SESSION":
        return None
    if account.status in LOGIN_EXCLUDED_ACCOUNT_STATUSES:
        return None
    if account.video_token_ciphertext is None:
        if is_known_low_balance(
            account.balance_synced_at,
            account.balance_credits,
            low_balance_threshold,
        ):
            return None
        if account.status in {"PENDING_VALIDATION", "TOKEN_EXPIRED", "TOKEN_INVALID"}:
            return "ACTIVATE_NEW"
        return None
    if account.status == "PENDING_VALIDATION" or not renewal_balance_is_available(
        account.balance_synced_at,
        account.balance_credits,
    ):
        return None
    renewal_cutoff = now + timedelta(seconds=renewal_window_seconds)
    if (
        account.status in RENEWAL_ACCOUNT_STATUSES
        or account.last_error_code == "UPSTREAM_UNAUTHORIZED"
        or account.token_expires_at is None
        or account.token_expires_at <= renewal_cutoff
    ):
        return "RENEW_TOKEN"
    return None


def is_available_idle_account(
    account: Account,
    *,
    now: datetime,
    renewal_window_seconds: int,
    low_balance_threshold: int,
) -> bool:
    renewal_cutoff = now + timedelta(seconds=renewal_window_seconds)
    return bool(
        account.status == "ACTIVE"
        and account.video_token_ciphertext is not None
        and account.token_expires_at is not None
        and account.token_expires_at > renewal_cutoff
        and account.active_tasks == 0
        and account.balance_synced_at is not None
        and account.balance_credits >= low_balance_threshold
    )


def active_credit_deficit(credit_target: int, active_credit_total: int) -> int:
    """Return the credits missing from the ACTIVE-account pool watermark."""

    return max(0, credit_target - active_credit_total)


def activation_dispatch_budget(
    credit_target: int,
    active_credit_total: int,
    activation_in_flight: int,
    max_activation_in_flight: int = 1,
) -> int:
    """Fill every free activation slot while the ACTIVE pool is below watermark."""

    if active_credit_deficit(credit_target, active_credit_total) == 0:
        return 0
    return max(0, max_activation_in_flight - activation_in_flight)


def login_failure_disposition(
    error_code: str,
    *,
    retryable: bool,
    previous_account_failures: int,
    max_account_failures: int,
    stalled_max_account_failures: int,
) -> LoginFailureDisposition:
    """Classify a failed login before retry/quarantine policy is applied."""

    normalized = error_code.strip().upper()
    if normalized in WORKER_INFRASTRUCTURE_LOGIN_ERROR_CODES:
        return "WORKER_BACKOFF"
    if not retryable:
        return "QUARANTINE"
    limit = (
        stalled_max_account_failures
        if normalized in STALLED_LOGIN_ERROR_CODES
        else max_account_failures
    )
    if previous_account_failures + 1 >= limit:
        return "QUARANTINE"
    return "RETRY_ACCOUNT"


def retry_delay_seconds(
    attempt_no: int,
    *,
    retryable: bool,
    retry_base_seconds: int,
    nonretryable_retry_seconds: int,
) -> int:
    if not retryable:
        return nonretryable_retry_seconds
    return min(retry_base_seconds * (2 ** max(attempt_no - 1, 0)), 1800)
