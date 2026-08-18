from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import mysql

import video_task_service.syncer as syncer_module
from video_task_service.config import Settings
from video_task_service.cookie_importer import (
    ClaimedCookieImportItem,
    CookieImportProcessingError,
    cookie_import_claim_statement,
    cookie_import_retry_delay,
    process_cookie_import_item,
)
from video_task_service.crypto import encrypt_secret
from video_task_service.protocol_renewal import ProtocolRenewalError, renewal_session_dict
from video_task_service.schemas import RenewalSessionPayload
from video_task_service.upstream import AccountValidation


def _session() -> RenewalSessionPayload:
    return RenewalSessionPayload(
        cookies=[
            {
                "name": "__Secure-better-auth.session_token",
                "value": "staging-token-secret",
                "domain": "app.leonardo.ai",
            },
            {
                "name": "__Secure-better-auth.session_data.0",
                "value": "staging-data-secret",
                "domain": "app.leonardo.ai",
            },
        ],
        client_version="server-cookie-zip-v1",
    )


def _claim(now: datetime, *, attempt_count: int = 1) -> ClaimedCookieImportItem:
    import json

    item_uuid = "20000000-0000-0000-0000-000000000001"
    material = json.dumps(renewal_session_dict(_session()), separators=(",", ":"))
    return ClaimedCookieImportItem(
        item_id=1,
        item_uuid=item_uuid,
        batch_id=2,
        space_name="cookie-batch-20300101",
        expected_login_name="worker@example.test",
        session_ciphertext=encrypt_secret(
            material,
            f"{item_uuid}:cookie_import_session",
        ),
        owner="cookie-importer-test",
        claimed_version=1,
        attempt_count=attempt_count,
        lease_until=now + timedelta(minutes=2),
    )


def test_claim_statement_selects_due_and_expired_leases_with_skip_locked() -> None:
    now = datetime(2030, 1, 1)
    sql = str(
        cookie_import_claim_statement(now, 2).compile(
            dialect=mysql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "account_cookie_import_items.status = 'QUEUED'" in sql
    assert "account_cookie_import_items.status = 'RETRY_WAIT'" in sql
    assert "account_cookie_import_items.retry_after <= '2030-01-01 00:00:00'" in sql
    assert "account_cookie_import_items.status = 'RUNNING'" in sql
    assert "account_cookie_import_items.lease_until <= '2030-01-01 00:00:00'" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "LIMIT 2" in sql


@pytest.mark.parametrize(
    ("attempt_count", "expected"),
    [(1, 60), (2, 300), (3, 900), (8, 900)],
)
def test_cookie_import_retry_delay_is_bounded(attempt_count: int, expected: int) -> None:
    assert cookie_import_retry_delay(attempt_count) == expected


@pytest.mark.asyncio
async def test_processes_cookie_session_with_matching_protocol_and_graphql_identity() -> None:
    now = datetime(2030, 1, 1, tzinfo=UTC)
    claim = _claim(now)

    async def protocol_client(**kwargs: object) -> object:
        assert kwargs["stored_token"] == ""
        assert kwargs["expected_email"] == "worker@example.test"
        return SimpleNamespace(
            token="renewed-video-token",
            token_expires_at=now.replace(tzinfo=None) + timedelta(hours=1),
            renewal_session=renewal_session_dict(_session()),
            session_email="worker@example.test",
        )

    class Upstream:
        async def validate_account(self, *, token: str) -> AccountValidation:
            assert token == "renewed-video-token"
            return AccountValidation(
                valid=True,
                balance_credits=8_500,
                login_name="worker@example.test",
            )

    result = await process_cookie_import_item(
        claim,
        protocol_client,
        Upstream(),
        Settings(),
    )

    assert result.login_name == "worker@example.test"
    assert result.token == "renewed-video-token"
    assert result.balance_credits == 8_500
    assert result.renewal_session.client_version == "server-cookie-zip-v1"


@pytest.mark.asyncio
async def test_graphql_identity_mismatch_is_terminal_and_secret_free() -> None:
    now = datetime(2030, 1, 1, tzinfo=UTC)

    async def protocol_client(**_: object) -> object:
        return SimpleNamespace(
            token="renewed-video-token",
            token_expires_at=now.replace(tzinfo=None) + timedelta(hours=1),
            renewal_session=renewal_session_dict(_session()),
            session_email="worker@example.test",
        )

    class Upstream:
        async def validate_account(self, *, token: str) -> AccountValidation:
            assert token == "renewed-video-token"
            return AccountValidation(
                valid=True,
                balance_credits=8_500,
                login_name="another@example.test",
            )

    with pytest.raises(CookieImportProcessingError) as caught:
        await process_cookie_import_item(
            _claim(now),
            protocol_client,
            Upstream(),
            Settings(),
        )

    assert caught.value.code == "COOKIE_IMPORT_IDENTITY_MISMATCH"
    assert caught.value.retryable is False
    assert "renewed-video-token" not in str(caught.value)


def test_protocol_retryability_is_preserved_without_secret_message() -> None:
    error = ProtocolRenewalError(
        "PROTOCOL_RATE_LIMITED",
        "rate limited while processing secret-cookie-value",
        retryable=True,
        retry_after_seconds=420,
    )
    converted = CookieImportProcessingError.from_protocol(error)

    assert converted.code == "PROTOCOL_RATE_LIMITED"
    assert converted.retryable is True
    assert converted.retry_after_seconds == 420
    assert "secret-cookie-value" not in str(converted)


@pytest.mark.asyncio
async def test_syncer_task_group_starts_both_loops(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[str] = []

    async def task_loop(_: object) -> None:
        started.append("task")

    async def cookie_loop(_: object) -> None:
        started.append("cookie")

    monkeypatch.setattr(syncer_module, "syncer_loop", task_loop)
    monkeypatch.setattr(syncer_module, "cookie_import_loop", cookie_loop)
    stop = __import__("asyncio").Event()

    async with __import__("asyncio").TaskGroup() as group:
        group.create_task(syncer_module.syncer_loop(stop))
        group.create_task(syncer_module.cookie_import_loop(stop))

    assert sorted(started) == ["cookie", "task"]
