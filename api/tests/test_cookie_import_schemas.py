from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from video_task_service.models import Account, AccountCookieImportBatch, AccountCookieImportItem
from video_task_service.schemas import (
    CookieImportBatchList,
    CookieImportBatchView,
    CookieImportItemView,
)

NOW = datetime(2030, 1, 1, tzinfo=UTC)


def _item(**overrides: object) -> CookieImportItemView:
    values: dict[str, object] = {
        "item_uuid": "20000000-0000-0000-0000-000000000001",
        "entry_name": "first@example.test.json",
        "entry_sha256": "a" * 64,
        "expected_login_name": "first@example.test",
        "discovered_login_name": "first@example.test",
        "status": "CREATED",
        "stage": "RENEWAL_READY",
        "attempt_count": 1,
        "retryable": False,
        "last_error_code": None,
        "last_error_message": None,
        "account_uuid": "30000000-0000-0000-0000-000000000001",
        "account_status": "ACTIVE",
        "balance_credits": 8_500,
        "token_expires_at": NOW,
        "renewal_status": "IDLE",
        "activated_at": NOW,
        "finished_at": NOW,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return CookieImportItemView(**values)


def _batch(**overrides: object) -> CookieImportBatchView:
    values: dict[str, object] = {
        "batch_uuid": "10000000-0000-0000-0000-000000000001",
        "status": "COMPLETED",
        "archive_filename": "cookies.zip",
        "archive_sha256": "b" * 64,
        "space_name": "cookie-batch-20300101",
        "item_count": 1,
        "queued": 0,
        "running": 0,
        "created": 1,
        "updated": 0,
        "failed": 0,
        "total_balance_credits": 8_500,
        "tasks_after_import": 2,
        "completed_tasks_after_import": 1,
        "failed_tasks_after_import": 0,
        "consumed_credits_after_import": 123,
        "created_at": NOW,
        "started_at": NOW,
        "finished_at": NOW,
        "items": [_item()],
    }
    values.update(overrides)
    return CookieImportBatchView(**values)


@pytest.mark.parametrize("status", ["QUEUED", "RUNNING", "COMPLETED", "PARTIAL_FAILED", "FAILED"])
def test_cookie_import_batch_accepts_every_status(status: str) -> None:
    assert _batch(status=status).status == status


@pytest.mark.parametrize(
    "status",
    ["QUEUED", "RUNNING", "RETRY_WAIT", "CREATED", "UPDATED", "SKIPPED_DUPLICATE", "FAILED"],
)
def test_cookie_import_item_accepts_every_status(status: str) -> None:
    assert _item(status=status).status == status


@pytest.mark.parametrize(
    "stage",
    ["RECEIVED", "SESSION_VALIDATION", "BALANCE_VALIDATION", "ACCOUNT_ACTIVATION", "RENEWAL_READY"],
)
def test_cookie_import_item_accepts_every_stage(stage: str) -> None:
    assert _item(stage=stage).stage == stage


def test_cookie_import_response_serializes_uuid_datetime_and_items() -> None:
    payload = _batch().model_dump(mode="json")

    assert payload["batch_uuid"] == "10000000-0000-0000-0000-000000000001"
    assert payload["created_at"] == "2030-01-01T00:00:00Z"
    assert payload["items"][0]["item_uuid"] == "20000000-0000-0000-0000-000000000001"
    assert payload["items"][0]["balance_credits"] == 8_500


def test_cookie_import_response_forbids_secret_or_archive_fields() -> None:
    with pytest.raises(ValidationError):
        _batch(archive_bytes="raw-archive-secret")
    with pytest.raises(ValidationError):
        _item(session_ciphertext="cookie-secret")
    with pytest.raises(ValidationError):
        _item(video_token="token-secret")
    with pytest.raises(ValidationError):
        _item(password="password-secret")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("item_count", -1),
        ("queued", -1),
        ("running", -1),
        ("failed", -1),
        ("total_balance_credits", -1),
        ("consumed_credits_after_import", -1),
    ],
)
def test_cookie_import_batch_rejects_negative_counters(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        _batch(**{field: value})


def test_cookie_import_list_has_bounded_pagination_shape() -> None:
    result = CookieImportBatchList(batches=[_batch(items=[])], total=1, limit=20, offset=0)

    assert result.total == 1
    assert result.limit == 20
    assert result.batches[0].items == []


def test_cookie_import_rejects_unknown_statuses() -> None:
    with pytest.raises(ValidationError):
        _batch(status="UNKNOWN")
    with pytest.raises(ValidationError):
        _item(status="UNKNOWN")
    with pytest.raises(ValidationError):
        _item(stage="UNKNOWN")


def test_cookie_import_orm_contract_has_credential_source_and_secret_staging() -> None:
    assert Account.__table__.c.credential_source.server_default.arg == "PASSWORD"
    assert AccountCookieImportBatch.__table__.c.idempotency_key.unique is True
    assert AccountCookieImportBatch.__table__.c.batch_uuid.unique is True
    assert AccountCookieImportItem.__table__.c.session_ciphertext.nullable is True
    assert AccountCookieImportItem.__table__.c.credential_key_version.server_default.arg == "1"
    assert AccountCookieImportItem.__table__.c.version.server_default.arg == "0"


def test_cookie_import_item_account_foreign_key_uses_set_null() -> None:
    foreign_key = next(iter(AccountCookieImportItem.__table__.c.account_id.foreign_keys))

    assert foreign_key.target_fullname == "accounts.id"
    assert foreign_key.ondelete == "SET NULL"
