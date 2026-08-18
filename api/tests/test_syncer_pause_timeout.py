from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

import video_task_service.syncer as syncer_module
from video_task_service.config import Settings
from video_task_service.crypto import decrypt_secret, encrypt_secret
from video_task_service.models import (
    Account,
    AccountLoginJob,
    AccountRenewalSession,
    ProtocolRenewalEvent,
    Space,
    Task,
    TaskQueue,
)
from video_task_service.protocol_renewal import (
    ProtocolRenewalResult,
    decode_renewal_session,
)
from video_task_service.upstream import AccountValidation, PollResult, UpstreamError


class Transaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *args: object) -> None:
        return None


class FakeSession:
    def __init__(
        self,
        *,
        task: Task,
        account: Account | None = None,
        renewal: AccountRenewalSession | None = None,
        space: Space | None = None,
        queue: TaskQueue | None = None,
        provider_result_retry_count: int = 0,
        scalar_tasks: list[Task] | None = None,
    ) -> None:
        self.task = task
        self.account = account
        self.renewal = renewal
        self.space = space
        self.queue = queue
        self.provider_result_retry_count = provider_result_retry_count
        self.scalar_tasks = scalar_tasks or []
        self.statements: list[object] = []
        self.added: list[object] = []

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def begin(self) -> Transaction:
        return Transaction()

    async def scalars(self, statement: object) -> list[Task]:
        self.statements.append(statement)
        return self.scalar_tasks

    async def get(self, model: type, object_id: int, **_: object) -> object | None:
        if model is Task and object_id == self.task.id:
            return self.task
        if model is Account and self.account is not None and object_id == self.account.id:
            return self.account
        if (
            model is AccountRenewalSession
            and self.renewal is not None
            and object_id == self.renewal.account_id
        ):
            return self.renewal
        if model is Space and self.space is not None and object_id == self.space.id:
            return self.space
        if model is TaskQueue and self.queue is not None and object_id == self.queue.task_id:
            return self.queue
        return None

    async def scalar(self, statement: object) -> int:
        self.statements.append(statement)
        return self.provider_result_retry_count

    def add(self, value: object) -> None:
        self.added.append(value)


class ValidationSession(FakeSession):
    def __init__(
        self,
        *,
        task: Task,
        account: Account,
        login_job: AccountLoginJob,
    ) -> None:
        super().__init__(task=task, account=account)
        self.login_job = login_job

    async def scalar(self, statement: object) -> AccountLoginJob:
        self.statements.append(statement)
        return self.login_job


class ValidatingUpstream:
    async def validate_account(self, *, token: str) -> AccountValidation:
        assert token == "reported-token"
        return AccountValidation(valid=True, balance_credits=500)


def settings() -> Settings:
    return Settings(
        sync_interval_seconds=0.5,
        sync_batch_size=20,
        task_running_timeout_seconds=7200,
        low_balance_threshold=100,
    )


def running_task(now: datetime) -> Task:
    return Task(
        id=1,
        task_uuid="task-uuid",
        status="RUNNING",
        account_id=2,
        space_id=3,
        upstream_submitted_at=now - timedelta(hours=3),
        upstream_started_at=now - timedelta(hours=2, seconds=1),
        next_sync_at=now,
        sync_lease_owner="syncer-1",
        sync_lease_until=now + timedelta(seconds=30),
        sync_attempts=5,
        reserved_credit_cost=50,
        error_code=None,
        error_message=None,
        version=1,
    )


def account(now: datetime) -> Account:
    return Account(
        id=2,
        account_uuid="account-uuid",
        space_id=3,
        active_tasks=1,
        max_concurrency=1,
        balance_credits=1000,
        reserved_credits=50,
        failed_tasks=0,
        status="ACTIVE",
        token_expires_at=now + timedelta(hours=1),
        version=1,
    )


def space() -> Space:
    return Space(id=3, space_uuid="space-uuid", name="space", active_tasks=1)


def test_claim_sync_batch_excludes_expired_and_pending_tokens(monkeypatch) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    task = running_task(now)
    session = FakeSession(task=task)
    monkeypatch.setattr(syncer_module, "session_factory", lambda: session)

    claimed = asyncio.run(syncer_module.claim_sync_batch("syncer-1", settings()))

    assert claimed == []
    statement = str(session.statements[0])
    assert "JOIN accounts" in statement
    assert "accounts.token_expires_at >" in statement
    assert "accounts.status NOT IN" in statement
    assert "accounts.last_error_code" in statement


def test_unauthorized_poll_pauses_without_changing_task_status(monkeypatch) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    task = running_task(now)
    assigned_account = account(now)
    session = FakeSession(task=task, account=assigned_account)
    monkeypatch.setattr(syncer_module, "session_factory", lambda: session)

    before = datetime.now(UTC).replace(tzinfo=None)
    asyncio.run(
        syncer_module.apply_poll_error(
            task.id,
            UpstreamError("UPSTREAM_UNAUTHORIZED", "expired", retryable=False),
            "syncer-1",
            settings(),
        )
    )

    assert task.status == "RUNNING"
    assert task.next_sync_at is not None and task.next_sync_at >= before
    assert task.sync_lease_owner is None
    assert assigned_account.status == "TOKEN_EXPIRED"


def test_unauthorized_manual_account_keeps_manual_state_but_pauses(monkeypatch) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    task = running_task(now)
    assigned_account = account(now)
    assigned_account.status = "MANUAL_DISABLED"
    session = FakeSession(task=task, account=assigned_account)
    monkeypatch.setattr(syncer_module, "session_factory", lambda: session)

    asyncio.run(
        syncer_module.apply_poll_error(
            task.id,
            UpstreamError("UPSTREAM_UNAUTHORIZED", "expired", retryable=False),
            "syncer-1",
            settings(),
        )
    )

    assert task.status == "RUNNING"
    assert assigned_account.status == "MANUAL_DISABLED"
    assert assigned_account.last_error_code == "UPSTREAM_UNAUTHORIZED"


def test_suspended_poll_account_is_manually_disabled(monkeypatch) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    task = running_task(now)
    assigned_account = account(now)
    session = FakeSession(task=task, account=assigned_account)
    monkeypatch.setattr(syncer_module, "session_factory", lambda: session)

    asyncio.run(
        syncer_module.apply_poll_error(
            task.id,
            UpstreamError(
                "UPSTREAM_GRAPHQL_ERROR",
                "You are suspended for violating our user agreement.",
            ),
            "syncer-1",
            settings(),
        )
    )

    assert task.status == "RUNNING"
    assert assigned_account.status == "MANUAL_DISABLED"
    assert assigned_account.disabled_reason == "upstream_account_suspended"
    assert assigned_account.last_error_code == "UPSTREAM_GRAPHQL_ERROR"


def test_successful_poll_after_token_refresh_clears_transient_error(monkeypatch) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    task = running_task(now)
    task.error_code = "UPSTREAM_UNAUTHORIZED"
    task.error_message = "expired"
    session = FakeSession(task=task)
    monkeypatch.setattr(syncer_module, "session_factory", lambda: session)

    asyncio.run(
        syncer_module.apply_poll_result(
            task.id,
            PollResult(status="RUNNING"),
            "syncer-1",
            settings(),
        )
    )

    assert task.status == "RUNNING"
    assert task.error_code is None
    assert task.error_message is None


@pytest.mark.parametrize(
    "error_code",
    ["PROVIDER_MODERATION_ERROR", "PROVIDER_INVALID_REQUEST"],
)
def test_provider_result_failure_is_requeued_once(monkeypatch, error_code: str) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    task = running_task(now)
    task.model = "bytedance/seedance-2.5"
    task.upstream_task_id = "generation-1"
    task.output_json = {"submit": {"apiCreditCost": None}}
    assigned_account = account(now)
    assigned_space = space()
    queue = TaskQueue(
        task_id=task.id,
        queue_status="DONE",
        available_at=now,
        delivery_attempts=1,
    )
    session = FakeSession(
        task=task,
        account=assigned_account,
        space=assigned_space,
        queue=queue,
    )
    monkeypatch.setattr(syncer_module, "session_factory", lambda: session)

    asyncio.run(
        syncer_module.apply_poll_result(
            task.id,
            PollResult(
                status="FAILED",
                output={
                    "provider": "leonardo",
                    "generation_id": "generation-1",
                    "error": {
                        "code": error_code,
                        "message": "provider rejected generation",
                    },
                },
                error_code=error_code,
                error_message="provider rejected generation",
            ),
            "syncer-1",
            settings(),
        )
    )

    assert task.status == "RETRY_WAIT"
    assert task.upstream_task_id is None
    assert task.reserved_credit_cost == 0
    assert task.actual_credit_cost is None
    assert task.finished_at is None
    assert task.next_sync_at is None
    assert queue.queue_status == "RETRY_WAIT"
    assert queue.last_error_code == error_code
    assert task.output_json == {
        "submit": {"apiCreditCost": None},
        "provider": "leonardo",
        "generation_id": "generation-1",
        "error": {
            "code": error_code,
            "message": "provider rejected generation",
        },
    }
    assert task.error_code == error_code
    assert task.error_message == "provider rejected generation"
    assert assigned_account.failed_tasks == 0
    release = next(
        item for item in session.added if item.__class__.__name__ == "AccountCreditLedger"
    )
    assert release.entry_type == "RELEASE"
    assert release.credit_delta == 0
    assert release.reserved_before == 50
    assert release.reserved_after == 0
    assert release.metadata_json["source"] == "provider_result_retry"
    retry_event = next(
        item
        for item in session.added
        if getattr(item, "event_type", None) == "PROVIDER_RESULT_RETRY_SCHEDULED"
    )
    assert retry_event.from_status == "RUNNING"
    assert retry_event.to_status == "RETRY_WAIT"
    assert retry_event.reason == error_code
    assert retry_event.metadata_json == {
        "error_code": error_code,
        "retry_number": 1,
        "retry_limit": 1,
        "retry_delay_seconds": 2,
        "previous_upstream_task_id": "generation-1",
    }


@pytest.mark.parametrize(
    "error_code",
    ["PROVIDER_MODERATION_ERROR", "PROVIDER_INVALID_REQUEST"],
)
def test_provider_result_second_failure_is_terminal(monkeypatch, error_code: str) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    task = running_task(now)
    task.upstream_task_id = "generation-2"
    assigned_account = account(now)
    assigned_space = space()
    queue = TaskQueue(
        task_id=task.id,
        queue_status="DONE",
        available_at=now,
        delivery_attempts=2,
    )
    session = FakeSession(
        task=task,
        account=assigned_account,
        space=assigned_space,
        queue=queue,
        provider_result_retry_count=1,
    )
    monkeypatch.setattr(syncer_module, "session_factory", lambda: session)

    asyncio.run(
        syncer_module.apply_poll_result(
            task.id,
            PollResult(
                status="FAILED",
                error_code=error_code,
                error_message="provider rejected generation again",
            ),
            "syncer-1",
            settings(),
        )
    )

    assert task.status == "FAILED"
    assert task.upstream_task_id == "generation-2"
    assert task.error_code == error_code
    assert task.error_message == "provider rejected generation again"
    assert assigned_account.failed_tasks == 1
    assert queue.queue_status == "DONE"
    assert not any(
        getattr(item, "event_type", None) == "PROVIDER_RESULT_RETRY_SCHEDULED"
        for item in session.added
    )


def test_completed_poll_records_pricing_and_settlement_metadata(monkeypatch) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    task = running_task(now)
    task.model = "seedance-2.0-fast"
    task.estimated_credit_cost = 449
    task.reserved_credit_cost = 967
    assigned_account = account(now)
    assigned_account.balance_credits = 5000
    assigned_account.reserved_credits = 967
    assigned_account.completed_tasks = 0
    assigned_space = space()
    session = FakeSession(
        task=task,
        account=assigned_account,
        space=assigned_space,
    )
    monkeypatch.setattr(syncer_module, "session_factory", lambda: session)

    asyncio.run(
        syncer_module.apply_poll_result(
            task.id,
            PollResult(
                status="COMPLETED",
                actual_credit_cost=967,
                output={"generation_id": "generation-1"},
            ),
            "syncer-1",
            settings(),
        )
    )

    ledger = next(
        item for item in session.added if item.__class__.__name__ == "AccountCreditLedger"
    )
    assert ledger.credit_delta == -967
    assert ledger.metadata_json == {
        "source": "task_completion",
        "pricing_rule_version": "leonardo-ui-20260812.v15",
        "model": "seedance-2.0-fast",
        "estimated_credit_cost": 449,
        "reserved_credit_cost": 967,
        "actual_credit_cost": 967,
        "provider_reported": True,
        "balance_reconciled_from_provider": False,
    }


def test_completed_poll_does_not_double_count_refreshed_provider_balance(monkeypatch) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    task = running_task(now)
    task.upstream_submitted_at = now - timedelta(minutes=2)
    task.model = "veo-3.1-generate-001"
    task.estimated_credit_cost = 1200
    task.reserved_credit_cost = 1200
    assigned_account = account(now)
    assigned_account.balance_credits = 11246
    assigned_account.balance_synced_at = now - timedelta(minutes=1)
    assigned_account.reserved_credits = 1200
    assigned_account.completed_tasks = 0
    assigned_space = space()
    session = FakeSession(
        task=task,
        account=assigned_account,
        space=assigned_space,
    )
    monkeypatch.setattr(syncer_module, "session_factory", lambda: session)

    asyncio.run(
        syncer_module.apply_poll_result(
            task.id,
            PollResult(
                status="COMPLETED",
                output={"generation_id": "generation-1"},
            ),
            "syncer-1",
            settings(),
        )
    )

    ledger = next(
        item for item in session.added if item.__class__.__name__ == "AccountCreditLedger"
    )
    assert task.actual_credit_cost == 1200
    assert assigned_account.balance_credits == 11246
    assert assigned_account.reserved_credits == 0
    assert ledger.balance_before == 11246
    assert ledger.balance_after == 11246
    assert ledger.credit_delta == -1200
    assert ledger.metadata_json["provider_reported"] is False
    assert ledger.metadata_json["balance_reconciled_from_provider"] is True


def test_running_task_over_two_hours_fails_and_releases_capacity(monkeypatch) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    task = running_task(now)
    assigned_account = account(now)
    assigned_space = space()
    session = FakeSession(
        task=task,
        account=assigned_account,
        space=assigned_space,
        scalar_tasks=[task],
    )
    monkeypatch.setattr(syncer_module, "session_factory", lambda: session)

    failed = asyncio.run(syncer_module.fail_timed_out_tasks("syncer-1", settings()))

    assert failed == 1
    assert task.status == "FAILED"
    assert task.error_code == "TASK_RUNNING_TIMEOUT"
    assert task.finished_at is not None
    assert task.next_sync_at is None
    assert assigned_account.active_tasks == 0
    assert assigned_account.reserved_credits == 0
    assert assigned_account.failed_tasks == 1
    assert assigned_space.active_tasks == 0


def test_account_validation_completes_reported_login_job(monkeypatch) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    assigned_account = account(now)
    assigned_account.status = "PENDING_VALIDATION"
    assigned_account.video_token_ciphertext = encrypt_secret(
        "reported-token",
        f"{assigned_account.account_uuid}:video_token",
    )
    assigned_account.token_expires_at = now + timedelta(hours=1)
    assigned_account.balance_synced_at = None
    assigned_account.version = 2
    login_job = AccountLoginJob(
        id=10,
        job_uuid="20000000-0000-0000-0000-000000000002",
        account_id=assigned_account.id,
        active_account_id=assigned_account.id,
        job_type="RENEW_TOKEN",
        status="VALIDATING",
        lease_owner="login-worker-1",
        lease_token_hash="a" * 64,
        lease_until=None,
        claimed_account_version=1,
        attempt_no=1,
        token_received_at=now,
        created_at=now,
        updated_at=now,
    )
    task = running_task(now)
    session = ValidationSession(
        task=task,
        account=assigned_account,
        login_job=login_job,
    )
    monkeypatch.setattr(syncer_module, "session_factory", lambda: session)

    asyncio.run(
        syncer_module.validate_account(
            assigned_account.id,
            ValidatingUpstream(),  # type: ignore[arg-type]
            settings(),
        )
    )

    assert assigned_account.status == "ACTIVE"
    assert assigned_account.balance_credits == 500
    assert assigned_account.balance_synced_at is not None
    assert login_job.status == "SUCCEEDED"
    assert login_job.active_account_id is None
    assert login_job.validation_finished_at is not None


def test_protocol_success_atomically_replaces_token_and_resets_dispatch_gate(
    monkeypatch,
) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    assigned_account = account(now)
    assigned_account.video_token_ciphertext = encrypt_secret(
        "old-token",
        f"{assigned_account.account_uuid}:video_token",
    )
    assigned_account.token_expires_at = now + timedelta(minutes=5)
    assigned_account.version = 7
    renewal = AccountRenewalSession(
        account_id=assigned_account.id,
        session_ciphertext=encrypt_secret(
            '{"cookies":[]}',
            f"{assigned_account.account_uuid}:renewal_session",
        ),
        status="RUNNING",
        attempt_count=1,
        claimed_account_version=7,
        lease_owner="syncer-1",
        lease_until=now + timedelta(seconds=20),
        fallback_after=now + timedelta(seconds=45),
    )
    session = FakeSession(
        task=running_task(now),
        account=assigned_account,
        renewal=renewal,
    )
    monkeypatch.setattr(syncer_module, "session_factory", lambda: session)
    expires_at = now + timedelta(hours=1)
    result = ProtocolRenewalResult(
        token="new-token",
        token_expires_at=expires_at,
        renewal_session={
            "cookies": [
                {
                    "name": "session",
                    "value": "rotated-cookie",
                    "domain": "app.leonardo.ai",
                    "path": "/",
                }
            ],
            "user_agent": "fixture-agent",
            "accept_language": "en-US",
        },
        token_changed=True,
        needs_refresh=False,
        get_session_status=200,
        cross_origin_cookie_status=204,
    )
    item = syncer_module.ProtocolRenewalItem(
        account_id=assigned_account.id,
        account_uuid=assigned_account.account_uuid,
        login_name="worker@example.test",
        encrypted_token=bytes(assigned_account.video_token_ciphertext),
        account_version=7,
        renewal_session={"cookies": []},
    )

    applied = asyncio.run(
        syncer_module.apply_protocol_renewal_success(
            item,
            result,
            700,
            "syncer-1",
            settings(),
        )
    )

    assert applied is True
    assert decrypt_secret(
        bytes(assigned_account.video_token_ciphertext),
        f"{assigned_account.account_uuid}:video_token",
    ) == "new-token"
    assert assigned_account.token_expires_at == expires_at
    assert assigned_account.balance_credits == 700
    assert assigned_account.status == "ACTIVE"
    assert assigned_account.version == 8
    assert renewal.status == "IDLE"
    assert renewal.attempt_count == 0
    assert renewal.lease_owner is None
    assert renewal.fallback_after is None
    assert renewal.last_success_at is not None
    renewal_events = [item for item in session.added if isinstance(item, ProtocolRenewalEvent)]
    assert len(renewal_events) == 1
    assert renewal_events[0].outcome == "SUCCEEDED"
    assert renewal_events[0].applied is True
    assert decode_renewal_session(renewal, assigned_account.account_uuid)["cookies"][0][
        "value"
    ] == "rotated-cookie"


def test_protocol_success_can_repeat_below_task_threshold_until_one_credit(
    monkeypatch,
) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    assigned_account = account(now)
    assigned_account.video_token_ciphertext = encrypt_secret(
        "initial-token",
        f"{assigned_account.account_uuid}:video_token",
    )
    assigned_account.token_expires_at = now + timedelta(minutes=5)
    assigned_account.balance_synced_at = now
    assigned_account.version = 20
    renewal = AccountRenewalSession(
        account_id=assigned_account.id,
        session_ciphertext=encrypt_secret(
            '{"cookies":[]}',
            f"{assigned_account.account_uuid}:renewal_session",
        ),
        status="RUNNING",
        attempt_count=1,
        claimed_account_version=20,
        lease_owner="syncer-repeat",
        lease_until=now + timedelta(seconds=20),
        fallback_after=now + timedelta(seconds=45),
    )
    session = FakeSession(
        task=running_task(now),
        account=assigned_account,
        renewal=renewal,
    )
    monkeypatch.setattr(syncer_module, "session_factory", lambda: session)

    def result(token: str, expires_at: datetime, cookie: str) -> ProtocolRenewalResult:
        return ProtocolRenewalResult(
            token=token,
            token_expires_at=expires_at,
            renewal_session={
                "cookies": [
                    {
                        "name": "session",
                        "value": cookie,
                        "domain": "app.leonardo.ai",
                        "path": "/",
                    }
                ]
            },
            token_changed=True,
            needs_refresh=False,
            get_session_status=200,
            cross_origin_cookie_status=204,
        )

    first_item = syncer_module.ProtocolRenewalItem(
        account_id=assigned_account.id,
        account_uuid=assigned_account.account_uuid,
        login_name="worker@example.test",
        encrypted_token=bytes(assigned_account.video_token_ciphertext),
        account_version=20,
        renewal_session={"cookies": []},
    )
    assert asyncio.run(
        syncer_module.apply_protocol_renewal_success(
            first_item,
            result("token-cycle-1", now + timedelta(hours=1), "cookie-cycle-1"),
            99,
            "syncer-repeat",
            settings(),
        )
    )
    assert assigned_account.status == "LOW_BALANCE_DISABLED"
    assert assigned_account.balance_credits == 99
    assert assigned_account.version == 21
    assert renewal.status == "IDLE"
    assert renewal.attempt_count == 0

    renewal.status = "RUNNING"
    renewal.attempt_count = 1
    renewal.claimed_account_version = 21
    renewal.lease_owner = "syncer-repeat"
    renewal.lease_until = now + timedelta(seconds=20)
    renewal.fallback_after = now + timedelta(seconds=45)
    second_item = syncer_module.ProtocolRenewalItem(
        account_id=assigned_account.id,
        account_uuid=assigned_account.account_uuid,
        login_name="worker@example.test",
        encrypted_token=bytes(assigned_account.video_token_ciphertext),
        account_version=21,
        renewal_session={"cookies": []},
    )
    assert asyncio.run(
        syncer_module.apply_protocol_renewal_success(
            second_item,
            result("token-cycle-2", now + timedelta(hours=2), "cookie-cycle-2"),
            1,
            "syncer-repeat",
            settings(),
        )
    )

    assert decrypt_secret(
        bytes(assigned_account.video_token_ciphertext),
        f"{assigned_account.account_uuid}:video_token",
    ) == "token-cycle-2"
    assert assigned_account.balance_credits == 1
    assert assigned_account.status == "LOW_BALANCE_DISABLED"
    assert assigned_account.version == 22
    assert renewal.status == "IDLE"
    assert renewal.attempt_count == 0
    assert renewal.lease_owner is None
    assert renewal.fallback_after is None
    assert len(
        [event for event in session.added if isinstance(event, ProtocolRenewalEvent)]
    ) == 2


def test_protocol_keepalive_persists_rotated_session_without_counting_token_success(
    monkeypatch,
) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    assigned_account = account(now)
    assigned_account.video_token_ciphertext = encrypt_secret(
        "live-token",
        f"{assigned_account.account_uuid}:video_token",
    )
    assigned_account.token_expires_at = now + timedelta(minutes=40)
    assigned_account.version = 11
    renewal = AccountRenewalSession(
        account_id=assigned_account.id,
        session_ciphertext=encrypt_secret(
            '{"cookies":[]}',
            f"{assigned_account.account_uuid}:renewal_session",
        ),
        status="RUNNING",
        attempt_count=1,
        claimed_account_version=11,
        lease_owner="syncer-1",
        lease_until=now + timedelta(seconds=20),
        fallback_after=None,
    )
    session = FakeSession(
        task=running_task(now),
        account=assigned_account,
        renewal=renewal,
    )
    monkeypatch.setattr(syncer_module, "session_factory", lambda: session)
    result = ProtocolRenewalResult(
        token="live-token",
        token_expires_at=assigned_account.token_expires_at,
        renewal_session={
            "cookies": [
                {
                    "name": "session",
                    "value": "kept-alive-cookie",
                    "domain": "app.leonardo.ai",
                    "path": "/",
                }
            ],
            "user_agent": "fixture-agent",
            "accept_language": "en-US",
        },
        token_changed=False,
        needs_refresh=False,
        get_session_status=200,
        cross_origin_cookie_status=0,
    )
    item = syncer_module.ProtocolRenewalItem(
        account_id=assigned_account.id,
        account_uuid=assigned_account.account_uuid,
        login_name="worker@example.test",
        encrypted_token=bytes(assigned_account.video_token_ciphertext),
        account_version=11,
        renewal_session={"cookies": []},
    )

    applied = asyncio.run(
        syncer_module.apply_protocol_session_keepalive(
            item,
            result,
            "syncer-1",
            settings(),
            retry_renewal=False,
        )
    )

    assert applied is True
    assert assigned_account.version == 11
    assert renewal.status == "IDLE"
    assert renewal.session_refreshed_at is not None
    assert renewal.last_success_at is None
    events = [value for value in session.added if isinstance(value, ProtocolRenewalEvent)]
    assert len(events) == 1
    assert events[0].outcome == "SESSION_ALIVE"
    assert events[0].applied is False
    assert decode_renewal_session(renewal, assigned_account.account_uuid)["cookies"][0][
        "value"
    ] == "kept-alive-cookie"


def test_pending_validation_scan_skips_imports_without_tokens(monkeypatch) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    session = FakeSession(task=running_task(now), scalar_tasks=[])
    monkeypatch.setattr(syncer_module, "session_factory", lambda: session)

    assert asyncio.run(syncer_module.pending_account_ids()) == []
    statement = str(session.statements[0])
    assert "accounts.status" in statement
    assert "accounts.video_token_ciphertext IS NOT NULL" in statement
