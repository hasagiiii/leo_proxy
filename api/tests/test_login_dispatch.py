from __future__ import annotations

from datetime import UTC, datetime, timedelta

from video_task_service.login_dispatch import (
    activation_dispatch_budget,
    active_credit_deficit,
    is_available_idle_account,
    is_known_low_balance,
    login_failure_disposition,
    login_job_type_for,
    retry_delay_seconds,
)
from video_task_service.models import Account


def account(now: datetime, **overrides: object) -> Account:
    values: dict[str, object] = {
        "id": 1,
        "account_uuid": "10000000-0000-0000-0000-000000000001",
        "space_id": 1,
        "login_name": "worker@example.test",
        "credential_source": "PASSWORD",
        "password_ciphertext": b"encrypted-password",
        "video_token_ciphertext": b"encrypted-token",
        "token_expires_at": now + timedelta(hours=1),
        "token_refreshed_at": now,
        "balance_credits": 500,
        "reserved_credits": 0,
        "balance_synced_at": now,
        "max_concurrency": 3,
        "active_tasks": 0,
        "completed_tasks": 0,
        "failed_tasks": 0,
        "status": "ACTIVE",
        "version": 0,
    }
    values.update(overrides)
    return Account(**values)


def test_unknown_imported_balance_is_not_treated_as_low() -> None:
    assert not is_known_low_balance(None, 0, 100)
    assert is_known_low_balance(datetime(2026, 8, 8), 99, 100)


def test_new_import_needs_activation_but_fresh_reported_token_does_not() -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    imported = account(
        now,
        status="PENDING_VALIDATION",
        video_token_ciphertext=None,
        token_expires_at=None,
        token_refreshed_at=None,
        balance_credits=0,
        balance_synced_at=None,
    )
    assert login_job_type_for(
        imported,
        now=now,
        renewal_window_seconds=600,
        low_balance_threshold=100,
    ) == "ACTIVATE_NEW"

    imported.video_token_ciphertext = b"reported"
    imported.token_expires_at = now + timedelta(hours=1)
    assert (
        login_job_type_for(
            imported,
            now=now,
            renewal_window_seconds=600,
            low_balance_threshold=100,
        )
        is None
    )


def test_renewal_becomes_due_at_ten_minute_boundary() -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    due = account(now, token_expires_at=now + timedelta(minutes=10))
    assert login_job_type_for(
        due,
        now=now,
        renewal_window_seconds=600,
        low_balance_threshold=100,
    ) == "RENEW_TOKEN"

    due.token_expires_at = now + timedelta(minutes=10, milliseconds=1)
    assert (
        login_job_type_for(
            due,
            now=now,
            renewal_window_seconds=600,
            low_balance_threshold=100,
        )
        is None
    )


def test_positive_low_balance_remains_eligible_for_token_renewal() -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    low = account(
        now,
        balance_credits=99,
        token_expires_at=now - timedelta(seconds=1),
        status="LOW_BALANCE_DISABLED",
    )
    assert login_job_type_for(
        low,
        now=now,
        renewal_window_seconds=600,
        low_balance_threshold=100,
    ) == "RENEW_TOKEN"


def test_confirmed_zero_balance_stops_token_renewal() -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    exhausted = account(
        now,
        balance_credits=0,
        token_expires_at=now - timedelta(seconds=1),
        status="LOW_BALANCE_DISABLED",
    )
    assert (
        login_job_type_for(
            exhausted,
            now=now,
            renewal_window_seconds=600,
            low_balance_threshold=100,
        )
        is None
    )


def test_cookie_session_account_never_enters_browser_login_fallback() -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    due = account(
        now,
        credential_source="COOKIE_SESSION",
        token_expires_at=now - timedelta(seconds=1),
        status="TOKEN_EXPIRED",
    )
    activation = account(
        now,
        credential_source="COOKIE_SESSION",
        video_token_ciphertext=None,
        token_expires_at=None,
        status="PENDING_VALIDATION",
    )

    assert login_job_type_for(
        due,
        now=now,
        renewal_window_seconds=600,
        low_balance_threshold=100,
    ) is None
    assert login_job_type_for(
        activation,
        now=now,
        renewal_window_seconds=600,
        low_balance_threshold=100,
    ) is None


def test_low_balance_without_token_still_does_not_enter_activation_pool() -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    low = account(
        now,
        balance_credits=99,
        video_token_ciphertext=None,
        token_expires_at=None,
        status="TOKEN_EXPIRED",
    )
    assert (
        login_job_type_for(
            low,
            now=now,
            renewal_window_seconds=600,
            low_balance_threshold=100,
        )
        is None
    )


def test_active_credit_watermark_dispatches_up_to_configured_parallel_limit() -> None:
    assert active_credit_deficit(1_000_000, 999_999) == 1
    assert active_credit_deficit(1_000_000, 1_000_000) == 0
    assert active_credit_deficit(1_000_000, 1_000_001) == 0
    assert activation_dispatch_budget(1_000_000, 999_999, 0, 3) == 3
    assert activation_dispatch_budget(1_000_000, 999_999, 1, 3) == 2
    assert activation_dispatch_budget(1_000_000, 999_999, 3, 3) == 0
    assert activation_dispatch_budget(1_000_000, 1_000_000, 0, 3) == 0


def test_login_failure_disposition_caps_account_failures_without_charging_worker_faults() -> None:
    assert login_failure_disposition(
        "ENOSPC", retryable=True, previous_account_failures=99,
        max_account_failures=5, stalled_max_account_failures=3,
    ) == "WORKER_BACKOFF"
    assert login_failure_disposition(
        "TIMEOUT", retryable=True, previous_account_failures=1,
        max_account_failures=5, stalled_max_account_failures=3,
    ) == "RETRY_ACCOUNT"
    assert login_failure_disposition(
        "TIMEOUT", retryable=True, previous_account_failures=2,
        max_account_failures=5, stalled_max_account_failures=3,
    ) == "QUARANTINE"
    assert login_failure_disposition(
        "LOGIN_REJECTED", retryable=False, previous_account_failures=0,
        max_account_failures=5, stalled_max_account_failures=3,
    ) == "QUARANTINE"


def test_idle_account_requires_more_than_ten_minutes_and_zero_active_tasks() -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    idle = account(now, token_expires_at=now + timedelta(minutes=11))
    assert is_available_idle_account(
        idle,
        now=now,
        renewal_window_seconds=600,
        low_balance_threshold=100,
    )
    idle.active_tasks = 1
    assert not is_available_idle_account(
        idle,
        now=now,
        renewal_window_seconds=600,
        low_balance_threshold=100,
    )


def test_retry_delay_is_exponential_and_capped() -> None:
    assert retry_delay_seconds(
        1,
        retryable=True,
        retry_base_seconds=60,
        nonretryable_retry_seconds=86400,
    ) == 60
    assert retry_delay_seconds(
        20,
        retryable=True,
        retry_base_seconds=60,
        nonretryable_retry_seconds=86400,
    ) == 1800
    assert retry_delay_seconds(
        1,
        retryable=False,
        retry_base_seconds=60,
        nonretryable_retry_seconds=86400,
    ) == 86400
