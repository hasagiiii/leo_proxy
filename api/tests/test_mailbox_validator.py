import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from video_task_service.api.mailboxes import mailbox_from_record
from video_task_service.mailbox_codes import parse_mailbox_import
from video_task_service.mailbox_graph import (
    MailboxCredentialsInvalid,
    MailboxProviderRateLimited,
)
from video_task_service.mailbox_validator import (
    retry_delay_seconds,
    validate_claimed_mailbox,
    validate_mailboxes_concurrently,
)


def mailbox(
    email: str = "user@example.com",
    client_id: str = "client-id",
):  # type: ignore[no-untyped-def]
    record = parse_mailbox_import(
        f"{email}----password----{client_id}----refresh-token"
    ).records[0]
    return mailbox_from_record(record)


class Client:
    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure
        self.calls = 0

    async def get_access_token(self, refresh_token: str, client_id: str) -> str:
        self.calls += 1
        assert refresh_token == "refresh-token"
        assert client_id == "client-id"
        if self.failure:
            raise self.failure
        return "access-token"

    async def latest_message(self, access_token: str) -> None:
        assert access_token == "access-token"
        return None


class ConcurrentClient:
    def __init__(self, crashing_client_id: str | None = None) -> None:
        self.crashing_client_id = crashing_client_id
        self.in_flight = 0
        self.max_in_flight = 0

    async def _request(self) -> None:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0.01)
        finally:
            self.in_flight -= 1

    async def get_access_token(self, refresh_token: str, client_id: str) -> str:
        await self._request()
        if client_id == self.crashing_client_id:
            raise RuntimeError("synthetic validator crash")
        return "access-token"

    async def latest_message(self, access_token: str) -> None:
        await self._request()


def test_retry_delay_is_bounded() -> None:
    assert [retry_delay_seconds(attempt) for attempt in (1, 2, 3, 20)] == [
        60,
        300,
        900,
        900,
    ]


@pytest.mark.asyncio
async def test_successful_validation_activates_mailbox() -> None:
    item = mailbox()
    item.validation_lease_owner = "validator"
    client = Client()
    now = datetime(2026, 8, 13, 1, 0, tzinfo=UTC)

    await validate_claimed_mailbox(item, client, now=now)

    assert item.status == "ACTIVE"
    assert item.last_validated_at == now.replace(tzinfo=None)
    assert item.validation_attempts == 0
    assert item.last_error_code is None
    assert item.validation_lease_owner is None


@pytest.mark.asyncio
async def test_invalid_grant_marks_mailbox_invalid() -> None:
    item = mailbox()
    client = Client(MailboxCredentialsInvalid("invalid credentials"))
    now = datetime(2026, 8, 13, 1, 0, tzinfo=UTC)

    await validate_claimed_mailbox(item, client, now=now)

    assert item.status == "INVALID"
    assert item.last_error_code == "MAILBOX_TOKEN_INVALID"
    assert item.next_validation_at is None


@pytest.mark.asyncio
async def test_transient_failure_requeues_with_provider_delay() -> None:
    item = mailbox()
    client = Client(MailboxProviderRateLimited(120))
    now = datetime(2026, 8, 13, 1, 0, tzinfo=UTC)

    await validate_claimed_mailbox(item, client, now=now)

    assert item.status == "PENDING_VALIDATION"
    assert item.validation_attempts == 1
    assert item.last_error_code == "MAILBOX_PROVIDER_RATE_LIMITED"
    assert item.next_validation_at == (now + timedelta(seconds=120)).replace(tzinfo=None)


@pytest.mark.asyncio
async def test_manual_disabled_mailbox_is_not_validated() -> None:
    item = mailbox()
    item.status = "MANUAL_DISABLED"
    client = Client()

    await validate_claimed_mailbox(
        item,
        client,
        now=datetime(2026, 8, 13, 1, 0, tzinfo=UTC),
    )

    assert item.status == "MANUAL_DISABLED"
    assert client.calls == 0


@pytest.mark.asyncio
async def test_batch_validation_runs_with_bounded_concurrency() -> None:
    items = [mailbox(f"user-{index}@example.com") for index in range(6)]
    for item in items:
        item.validation_lease_owner = "validator"
    client = ConcurrentClient()

    await validate_mailboxes_concurrently(items, client, max_concurrency=2)

    assert client.max_in_flight == 2
    assert all(item.status == "ACTIVE" for item in items)
    assert all(item.validation_lease_owner is None for item in items)


@pytest.mark.asyncio
async def test_unexpected_failure_does_not_block_the_rest_of_the_batch() -> None:
    broken = mailbox("broken@example.com", "crashing-client")
    healthy = mailbox("healthy@example.com", "healthy-client")
    broken.validation_lease_owner = "validator"
    healthy.validation_lease_owner = "validator"
    client = ConcurrentClient(crashing_client_id="crashing-client")

    await validate_mailboxes_concurrently(
        [broken, healthy],
        client,
        max_concurrency=2,
    )

    assert broken.status == "PENDING_VALIDATION"
    assert broken.last_error_code == "MAILBOX_VALIDATION_INTERNAL_ERROR"
    assert broken.validation_attempts == 1
    assert broken.next_validation_at is not None
    assert broken.validation_lease_owner is None
    assert healthy.status == "ACTIVE"
