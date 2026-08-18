from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.dialects import mysql

from video_task_service.models import ParentAccount, RegistrationRecord
from video_task_service.registration_records import (
    cdp_session_payload,
    mailbox_candidate_statement,
    parent_candidate_statement,
    registration_result_fingerprint,
    settle_success,
    validate_registration_idempotency_key,
)
from video_task_service.schemas import RegistrationJobResultRequest


def _mysql_sql(statement: object) -> str:
    return str(
        statement.compile(  # type: ignore[attr-defined]
            dialect=mysql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).lower()


def test_parent_candidate_waits_for_oldest_active_parent() -> None:
    sql = _mysql_sql(parent_candidate_statement())

    assert "parent_accounts.status = 'active'" in sql
    assert "order by parent_accounts.id asc" in sql
    assert "for update" in sql
    assert "skip locked" not in sql


def test_mailbox_candidate_excludes_every_email_tombstone() -> None:
    sql = _mysql_sql(mailbox_candidate_statement(9))

    assert "mailboxes.status = 'active'" in sql
    assert "registration_records.email_snapshot = mailboxes.email" in sql
    assert "project_mailbox_claims.project_id = 9" in sql
    assert "project_mailbox_claims.email_snapshot = mailboxes.email" in sql
    assert "order by mailboxes.id asc" in sql
    assert "for update" in sql


def test_registration_idempotency_key_uses_safe_ascii() -> None:
    value = "019ffa1b-9284-76c2-be3b-b8c5f3ae093e"

    assert validate_registration_idempotency_key(value) == value
    for invalid in ("short", "contains spaces", "contains/unsafe"):
        try:
            validate_registration_idempotency_key(invalid)
        except ValueError:
            pass
        else:  # pragma: no cover - assertion branch
            raise AssertionError(f"accepted invalid idempotency key: {invalid}")


def _registered_body(*, reverse: bool = False) -> RegistrationJobResultRequest:
    cookies = [
        {
            "name": "session",
            "value": "session-value",
            "domain": ".leonardo.ai",
            "path": "/",
            "expires": -1,
            "httpOnly": True,
            "secure": True,
            "sameSite": "Lax",
        },
        {
            "name": "csrf",
            "value": "csrf-value",
            "domain": "app.leonardo.ai",
            "path": "/",
            "expires": 1_790_000_000,
            "sameSite": "None",
        },
    ]
    if reverse:
        cookies.reverse()
    return RegistrationJobResultRequest.model_validate(
        {
            "client_id": "client-a",
            "report_token": "r" * 32,
            "status": "REGISTERED",
            "registered_email": "child@example.com",
            "user_agent": "Chrome/136",
            "cookies": cookies,
        }
    )


def test_registered_result_fingerprint_is_stable_across_cookie_order() -> None:
    assert registration_result_fingerprint(_registered_body()) == registration_result_fingerprint(
        _registered_body(reverse=True)
    )


def test_cdp_cookie_adapter_maps_session_expiry_and_same_site() -> None:
    payload = cdp_session_payload(_registered_body())
    by_name = {cookie.name: cookie for cookie in payload.cookies}

    assert by_name["session"].expiration_date is None
    assert by_name["session"].http_only is True
    assert by_name["session"].same_site == "lax"
    assert by_name["csrf"].expiration_date == 1_790_000_000
    assert by_name["csrf"].same_site == "no_restriction"


def _parent() -> ParentAccount:
    return ParentAccount(
        parent_account_uuid="9c36fc3f-d7fb-43cf-a0d3-1b3ec4ad4d50",
        email="parent@example.com",
        password_encrypted=b"password",
        invite_url="https://example.test/invite",
        invite_success_count=0,
        invite_failure_count=0,
        status="ACTIVE",
        consecutive_150_count=0,
        successful_settlement_sequence=0,
        legacy_invite_success_count=0,
        legacy_invite_failure_count=0,
        version=0,
    )


def _record(sequence: int) -> RegistrationRecord:
    return RegistrationRecord(
        registration_uuid=f"00000000-0000-0000-0000-{sequence:012d}",
        client_id=f"client-{sequence}",
        claim_idempotency_key=f"claim-key-{sequence:08d}",
        parent_account_id=1,
        parent_account_uuid_snapshot="9c36fc3f-d7fb-43cf-a0d3-1b3ec4ad4d50",
        parent_email_snapshot="parent@example.com",
        mailbox_uuid_snapshot=f"10000000-0000-0000-0000-{sequence:012d}",
        email_snapshot=f"child-{sequence}@example.com",
        report_token_encrypted=b"token",
        lease_expires_at=datetime(2026, 8, 13, 9, 0),
        status="VALIDATING",
        started_at=datetime(2026, 8, 13, 8, 0),
    )


def test_three_server_settled_below_8000_results_exhaust_parent() -> None:
    parent = _parent()
    now = datetime(2026, 8, 13, 8, 30, tzinfo=UTC)

    records = [_record(index) for index in range(1, 4)]
    for record, credits in zip(records, (150, 7_999, 3_500), strict=True):
        settle_success(parent, record, credits, now)

    assert parent.invite_success_count == 3
    assert parent.successful_settlement_sequence == 3
    assert [record.parent_settlement_sequence for record in records] == [1, 2, 3]
    assert parent.consecutive_150_count == 3
    assert parent.status == "EXHAUSTED"
    assert parent.exhausted_reason == "THREE_CONSECUTIVE_BELOW_8000"
    assert parent.exhausted_at == now.replace(tzinfo=None)


def test_8000_credit_success_resets_only_an_active_parent_streak() -> None:
    parent = _parent()
    parent.consecutive_150_count = 2

    settle_success(parent, _record(1), 8_000, datetime(2026, 8, 13, 8, 30))

    assert parent.consecutive_150_count == 0
    assert parent.status == "ACTIVE"


def test_inflight_success_after_exhaustion_keeps_streak_three() -> None:
    parent = _parent()
    parent.status = "EXHAUSTED"
    parent.consecutive_150_count = 3
    parent.successful_settlement_sequence = 3

    record = _record(4)
    settle_success(parent, record, 900, datetime(2026, 8, 13, 8, 30))

    assert parent.invite_success_count == 1
    assert parent.successful_settlement_sequence == 4
    assert record.parent_settlement_sequence == 4
    assert parent.consecutive_150_count == 3
