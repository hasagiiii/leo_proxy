from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from video_task_service.account_session_import import (
    CookieAccountUpsertInput,
    upsert_cookie_session_account,
)
from video_task_service.config import Settings
from video_task_service.crypto import decrypt_secret, encrypt_secret
from video_task_service.models import Account, AccountRenewalSession, Space
from video_task_service.protocol_renewal import decode_renewal_session
from video_task_service.schemas import RenewalSessionPayload


class FakeSession:
    def __init__(
        self,
        scalar_values: list[object | None],
        get_values: dict[tuple[type[object], int], object | None] | None = None,
    ) -> None:
        self.scalar_values = scalar_values
        self.get_values = get_values or {}
        self.added: list[object] = []
        self._next_space_id = 20
        self._next_account_id = 30

    async def scalar(self, statement: object) -> object | None:
        assert statement is not None
        return self.scalar_values.pop(0)

    async def get(self, model: type[object], identity: int, **_: object) -> object | None:
        return self.get_values.get((model, identity))

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        for value in self.added:
            if isinstance(value, Space) and value.id is None:
                value.id = self._next_space_id
                self._next_space_id += 1
            if isinstance(value, Account) and value.id is None:
                value.id = self._next_account_id
                self._next_account_id += 1


def _renewal_session() -> RenewalSessionPayload:
    return RenewalSessionPayload(
        cookies=[
            {
                "name": "__Secure-better-auth.session_token",
                "value": "session-token-secret",
                "domain": "app.leonardo.ai",
            },
            {
                "name": "__Secure-better-auth.session_data.0",
                "value": "session-data-secret",
                "domain": "app.leonardo.ai",
            },
        ],
        client_version="server-cookie-zip-v1",
    )


def _input(now: datetime, *, balance: int = 8_500) -> CookieAccountUpsertInput:
    return CookieAccountUpsertInput(
        space_name="cookie-batch-20300101",
        login_name="  Worker@Example.TEST ",
        token="video-token-secret",
        token_expires_at=now + timedelta(hours=1),
        balance_credits=balance,
        renewal_session=_renewal_session(),
        max_concurrency=3,
    )


@pytest.mark.asyncio
async def test_creates_active_cookie_session_account_with_encrypted_credentials() -> None:
    now = datetime(2030, 1, 1, tzinfo=UTC)
    space = Space(
        id=5,
        space_uuid="50000000-0000-0000-0000-000000000001",
        name="cookie-batch-20300101",
        max_concurrency=10,
    )
    session = FakeSession([None, space])

    result = await upsert_cookie_session_account(
        session,  # type: ignore[arg-type]
        _input(now),
        Settings(),
        now=now,
    )

    account = next(value for value in session.added if isinstance(value, Account))
    renewal = next(
        value for value in session.added if isinstance(value, AccountRenewalSession)
    )
    assert result.action == "CREATED"
    assert result.account_uuid == account.account_uuid
    assert result.account_status == "ACTIVE"
    assert account.login_name == "worker@example.test"
    assert account.credential_source == "COOKIE_SESSION"
    assert account.space_id == 5
    assert account.balance_credits == 8_500
    assert account.status == "ACTIVE"
    assert decrypt_secret(
        bytes(account.video_token_ciphertext), f"{account.account_uuid}:video_token"
    ) == "video-token-secret"
    marker = decrypt_secret(
        bytes(account.password_ciphertext), f"{account.account_uuid}:password"
    )
    assert len(marker) >= 32
    assert marker != "video-token-secret"
    assert renewal.account_id == account.id
    stored = decode_renewal_session(renewal, account.account_uuid)
    assert len(stored["cookies"]) == 2


@pytest.mark.asyncio
async def test_new_cookie_account_uses_existing_low_balance_status_rule() -> None:
    now = datetime(2030, 1, 1, tzinfo=UTC)
    space = Space(
        id=5,
        space_uuid="50000000-0000-0000-0000-000000000001",
        name="cookie-batch-20300101",
        max_concurrency=10,
    )
    session = FakeSession([None, space])

    result = await upsert_cookie_session_account(
        session,  # type: ignore[arg-type]
        _input(now, balance=99),
        Settings(low_balance_threshold=100),
        now=now,
    )

    account = next(value for value in session.added if isinstance(value, Account))
    assert result.account_status == "LOW_BALANCE_DISABLED"
    assert account.disabled_reason == "balance_below_threshold"


@pytest.mark.asyncio
async def test_updates_existing_account_without_overwriting_management_fields() -> None:
    now = datetime(2030, 1, 1, tzinfo=UTC)
    account_uuid = "30000000-0000-0000-0000-000000000001"
    password_ciphertext = encrypt_secret("real-password", f"{account_uuid}:password")
    account = Account(
        id=7,
        account_uuid=account_uuid,
        space_id=9,
        login_name="worker@example.test",
        credential_source="PASSWORD",
        label="macbook",
        password_ciphertext=password_ciphertext,
        balance_credits=1,
        reserved_credits=33,
        max_concurrency=3,
        active_tasks=1,
        completed_tasks=4,
        failed_tasks=2,
        status="MANUAL_DISABLED",
        disabled_reason="manual",
        version=5,
    )
    space = Space(
        id=9,
        space_uuid="50000000-0000-0000-0000-000000000009",
        name="original-space",
        max_concurrency=10,
    )
    session = FakeSession([account], {(Space, 9): space})

    result = await upsert_cookie_session_account(
        session,  # type: ignore[arg-type]
        _input(now),
        Settings(),
        now=now,
    )

    assert result.action == "UPDATED"
    assert account.space_id == 9
    assert account.password_ciphertext == password_ciphertext
    assert account.credential_source == "PASSWORD"
    assert account.label == "macbook"
    assert account.reserved_credits == 33
    assert account.active_tasks == 1
    assert account.completed_tasks == 4
    assert account.failed_tasks == 2
    assert account.status == "MANUAL_DISABLED"
    assert account.disabled_reason == "manual"
    assert account.balance_credits == 8_500
    assert account.version == 6
    assert decrypt_secret(
        bytes(account.video_token_ciphertext), f"{account.account_uuid}:video_token"
    ) == "video-token-secret"
