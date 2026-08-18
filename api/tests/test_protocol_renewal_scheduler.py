from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.dialects import mysql

from video_task_service.api.stats import _protocol_renewal_due_account_filter
from video_task_service.login_dispatch import (
    renewal_balance_available_clause,
    renewal_balance_is_available,
)
from video_task_service.syncer import (
    _confirmed_protocol_session_filter,
    _expired_account_filter,
    _protocol_renewal_account_filter,
    _protocol_renewal_priority,
    _renewable_protocol_session_filter,
    protocol_renewal_session_is_confirmed,
)


def compile_mysql(expression: object) -> str:
    return str(
        expression.compile(  # type: ignore[attr-defined]
            dialect=mysql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def test_recent_server_keepalive_allows_second_protocol_renewal_cycle() -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    cutoff = now - timedelta(minutes=80)

    assert protocol_renewal_session_is_confirmed(
        now - timedelta(minutes=81),
        now - timedelta(minutes=5),
        cutoff,
    )

    sql = compile_mysql(_confirmed_protocol_session_filter(cutoff))
    assert "account_renewal_sessions.client_reported_at >" in sql
    assert "account_renewal_sessions.session_refreshed_at >" in sql
    assert " OR " in sql


def test_session_without_recent_client_or_server_confirmation_stays_excluded() -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    cutoff = now - timedelta(minutes=80)

    assert not protocol_renewal_session_is_confirmed(
        now - timedelta(minutes=81),
        now - timedelta(minutes=81),
        cutoff,
    )
    assert not protocol_renewal_session_is_confirmed(None, None, cutoff)


def test_expiring_status_advances_to_expired_after_token_deadline() -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    sql = compile_mysql(_expired_account_filter(now))

    assert "accounts.status IN ('ACTIVE', 'TOKEN_EXPIRING')" in sql
    assert "accounts.token_expires_at IS NOT NULL" in sql
    assert "accounts.token_expires_at <=" in sql


def test_renewal_balance_gate_accepts_positive_and_unknown_but_rejects_zero() -> None:
    now = datetime.now(UTC).replace(tzinfo=None)

    assert renewal_balance_is_available(now, 99)
    assert renewal_balance_is_available(now, 1)
    assert not renewal_balance_is_available(now, 0)
    assert renewal_balance_is_available(None, 0)

    sql = compile_mysql(renewal_balance_available_clause())
    assert "accounts.balance_synced_at IS NULL" in sql
    assert "accounts.balance_credits > 0" in sql
    assert " OR " in sql


def test_protocol_candidate_filter_allows_low_status_until_balance_is_zero() -> None:
    sql = compile_mysql(_protocol_renewal_account_filter())

    assert "accounts.status NOT IN ('PENDING_VALIDATION', 'MANUAL_DISABLED')" in sql
    assert "LOW_BALANCE_DISABLED" not in sql
    assert "accounts.balance_synced_at IS NULL OR accounts.balance_credits > 0" in sql


def test_protocol_priority_keeps_low_balance_eligible_but_behind_other_accounts() -> None:
    sql = compile_mysql(_protocol_renewal_priority())

    assert "accounts.status = 'LOW_BALANCE_DISABLED'" in sql
    assert "THEN 1 ELSE 0" in sql


def test_fresh_pending_protocol_session_defers_expired_status() -> None:
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=80)
    sql = compile_mysql(_renewable_protocol_session_filter(cutoff))

    assert "EXISTS (SELECT account_renewal_sessions.account_id" in sql
    assert "account_renewal_sessions.account_id = accounts.id" in sql
    assert "account_renewal_sessions.status IN ('IDLE', 'PENDING', 'RETRY', 'RUNNING')" in sql
    assert "account_renewal_sessions.client_reported_at >" in sql
    assert "account_renewal_sessions.session_refreshed_at >" in sql


def test_queue_lag_filter_matches_positive_balance_renewal_eligibility() -> None:
    sql = compile_mysql(_protocol_renewal_due_account_filter())

    assert "accounts.status NOT IN ('PENDING_VALIDATION', 'MANUAL_DISABLED')" in sql
    assert "LOW_BALANCE_DISABLED" not in sql
    assert "accounts.balance_synced_at IS NULL OR accounts.balance_credits > 0" in sql
