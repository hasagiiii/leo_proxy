from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from video_task_service.account_ledger import (
    apply_ledger_profile,
    ledger_profile_view,
    select_credit_records,
)
from video_task_service.crypto import decrypt_secret
from video_task_service.schemas import AccountLedgerImportRequest, AccountLedgerRecord


def ledger_record(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": "749fe3ef-5c00-43e6-91dd-0956086432ca",
        "email": "Ledger.Account@Example.test",
        "password": "login-password",
        "registrationPassword": "registration-password",
        "groupToken": "group-token",
        "authorizationToken": "authorization-token",
        "parentAccountId": "11111111-1111-1111-1111-111111111111",
        "parentAccount": "parent@example.test",
        "inviteStatus": "invited",
        "invitedAt": "2026-08-12T04:01:02.003Z",
        "inviteError": None,
        "inviteAttempts": 1,
        "registrationStatus": "succeeded",
        "registrationAttempts": 1,
        "registeredAt": "2026-08-12T04:02:03.004Z",
        "registrationAccountId": "22222222-2222-2222-2222-222222222222",
        "registrationError": None,
        "creditsTotal": 8500,
        "creditsSubscription": 8500,
        "creditsPurchase": 0,
        "creditsRollover": 0,
        "creditsCheckedAt": "2026-08-12T04:03:04.005Z",
        "creditsError": None,
        "creditsAttempts": 1,
        "createdAt": "2026-08-12T04:00:00.001Z",
        "updatedAt": "2026-08-12T04:03:04.006Z",
    }
    payload.update(overrides)
    return payload


def import_request(records: list[dict[str, object]]) -> AccountLedgerImportRequest:
    return AccountLedgerImportRequest(
        space_uuid="7b61daf1-ae28-41bb-bb4c-1b44706a3a75",
        source="account-ledger-child-raw",
        source_kind="child",
        source_raw=True,
        source_exported_at="2026-08-12T08:00:00.000Z",
        source_count=226,
        source_file_sha256="a" * 64,
        records=records,
    )


def test_ledger_record_preserves_every_source_field_and_secret() -> None:
    record = AccountLedgerRecord.model_validate(ledger_record())

    document = record.source_document()

    assert list(document) == list(ledger_record())
    assert document["email"] == "Ledger.Account@Example.test"
    assert document["password"] == "login-password"
    assert document["registrationPassword"] == "registration-password"
    assert document["groupToken"] == "group-token"
    assert document["authorizationToken"] == "authorization-token"
    assert document["creditsTotal"] == 8500
    assert document["inviteError"] is None


def test_ledger_import_rejects_duplicate_email_and_source_id() -> None:
    base = ledger_record()

    with pytest.raises(ValidationError, match="duplicate ledger email"):
        import_request(
            [base, ledger_record(id="33333333-3333-3333-3333-333333333333")]
        )

    with pytest.raises(ValidationError, match="duplicate ledger source id"):
        import_request([base, ledger_record(email="second@example.test")])


def test_select_credit_records_matches_numeric_values_and_reports_rejections() -> None:
    envelope = {
        "source": "fixture-ledger",
        "records": [
            ledger_record(),
            ledger_record(
                id="33333333-3333-3333-3333-333333333333",
                email="string@example.test",
                creditsTotal="8500",
            ),
            ledger_record(
                id="44444444-4444-4444-4444-444444444444",
                email="empty@example.test",
                creditsTotal=None,
            ),
        ],
    }

    selected, rejected = select_credit_records(envelope, credits_total=8500)

    assert [row["email"] for row in selected] == [
        "Ledger.Account@Example.test",
        "string@example.test",
    ]
    assert rejected == 1


def test_apply_ledger_profile_encrypts_raw_record_and_projects_query_fields() -> None:
    record = AccountLedgerRecord.model_validate(ledger_record())
    profile = SimpleNamespace()
    account_uuid = "55555555-5555-5555-5555-555555555555"

    apply_ledger_profile(
        profile,
        record,
        source="account-ledger-child-raw",
        source_kind="child",
        source_raw=True,
        source_exported_at=datetime(2026, 8, 12, 8, 0),
        source_count=226,
        source_file_sha256="a" * 64,
        account_uuid=account_uuid,
    )

    raw = decrypt_secret(
        profile.raw_record_ciphertext,
        f"{account_uuid}:ledger_record",
    )
    assert json.loads(raw) == record.source_document()
    assert profile.raw_record_sha256
    assert profile.source_record_id == str(record.id)
    assert profile.source_kind == "child"
    assert profile.source_raw is True
    assert profile.source_count == 226
    assert profile.source_file_sha256 == "a" * 64
    assert profile.parent_account == "parent@example.test"
    assert profile.registration_status == "succeeded"
    assert profile.credits_total == 8500
    assert profile.credits_subscription == 8500
    assert profile.has_registration_password is True
    assert profile.has_group_token is True
    assert profile.has_authorization_token is True

    view = ledger_profile_view(profile)
    assert view.source == "account-ledger-child-raw"
    assert view.source_kind == "child"
    assert view.source_raw is True
    assert view.source_count == 226
    assert view.source_file_sha256 == "a" * 64
    assert view.source_record_id == record.id
    assert view.credits_total == 8500
    assert view.registered_at == datetime(2026, 8, 12, 4, 2, 3, 4000)
    assert view.raw_record_sha256 == profile.raw_record_sha256
