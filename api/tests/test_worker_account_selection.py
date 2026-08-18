from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

import video_task_service.worker as worker_module
from video_task_service.config import Settings
from video_task_service.models import (
    Account,
    Space,
    Task,
    TaskAttempt,
    TaskEvent,
    TaskQueue,
)
from video_task_service.upstream import UpstreamError


class Transaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *args: object) -> None:
        return None


class SelectionSession:
    def __init__(self, queue: TaskQueue, task: Task) -> None:
        self.queue = queue
        self.task = task
        self.statements: list[object] = []
        self.added: list[object] = []

    async def __aenter__(self) -> SelectionSession:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def begin(self) -> Transaction:
        return Transaction()

    async def scalar(self, statement: object) -> object | None:
        self.statements.append(statement)
        if len(self.statements) == 1:
            return self.queue
        return None

    async def get(self, model: type, object_id: int, **_: object) -> object | None:
        if model is Task and object_id == self.task.id:
            return self.task
        return None

    def add(self, value: object) -> None:
        self.added.append(value)


class RecoverySession:
    def __init__(
        self,
        *,
        expired_queues: list[TaskQueue],
        tasks: dict[int, Task],
        existing_unknown: list[Task] | None = None,
        attempts: dict[int, TaskAttempt] | None = None,
    ) -> None:
        self.scalars_values: list[list[object]] = [
            expired_queues,
            list(existing_unknown or []),
        ]
        self.tasks = tasks
        self.attempts = attempts or {}
        self.added: list[object] = []

    async def __aenter__(self) -> RecoverySession:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def begin(self) -> Transaction:
        return Transaction()

    async def scalars(self, statement: object) -> list[object]:
        return self.scalars_values.pop(0)

    async def get(self, model: type, object_id: int, **_: object) -> object | None:
        if model is Task:
            return self.tasks.get(object_id)
        return None

    async def scalar(self, statement: object) -> object | None:
        for attempt in self.attempts.values():
            return attempt
        return None

    def add(self, value: object) -> None:
        self.added.append(value)


class FailureSession:
    def __init__(
        self,
        *,
        task: Task,
        account: Account,
        space: Space,
        queue: TaskQueue,
    ) -> None:
        self.task = task
        self.account = account
        self.space = space
        self.queue = queue
        self.added: list[object] = []

    async def __aenter__(self) -> FailureSession:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def begin(self) -> Transaction:
        return Transaction()

    async def get(self, model: type, object_id: int, **_: object) -> object | None:
        if model is Task and object_id == self.task.id:
            return self.task
        if model is Account and object_id == self.account.id:
            return self.account
        if model is Space and object_id == self.space.id:
            return self.space
        if model is TaskQueue and object_id == self.queue.task_id:
            return self.queue
        return None

    async def scalar(self, statement: object) -> None:
        return None

    def add(self, value: object) -> None:
        self.added.append(value)


class DiagnosisSession:
    def __init__(self, counts: list[int]) -> None:
        self.counts = iter(counts)

    async def scalar(self, _statement: object) -> int:
        return next(self.counts)


@pytest.mark.parametrize(
    ("model", "task_input", "expected"),
    [
        ("seedance-2.0", {"prompt": "test", "duration": 4, "resolution": "4K"}, 7616),
        (
            "bytedance/seedance-2.5",
            {"prompt": "test", "duration": 30, "resolution": "720P"},
            8760,
        ),
        (
            "bytedance/seedance-2.5",
            {
                "prompt": "test",
                "duration": 18,
                "resolution": "720P",
                "reference_video_urls": ["https://cdn.example.com/dance.mp4"],
            },
            8496,
        ),
    ],
)
def test_account_selection_uses_spendable_balance_and_current_quote(
    monkeypatch,
    model: str,
    task_input: dict[str, object],
    expected: int,
) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    queue = TaskQueue(
        task_id=1,
        queue_status="READY",
        priority=0,
        available_at=now - timedelta(seconds=1),
        lease_owner=None,
        lease_until=None,
        delivery_attempts=0,
    )
    task = Task(
        id=1,
        task_uuid="task-uuid",
        status="QUEUED",
        model=model,
        input_json=task_input,
        estimated_credit_cost=0,
        version=0,
    )
    session = SelectionSession(queue, task)
    monkeypatch.setattr(worker_module, "session_factory", lambda: session)

    result = asyncio.run(
        worker_module.claim_and_assign(
            "worker-1",
            Settings(token_guard_seconds=120, retry_base_seconds=1),
        )
    )

    assert result is None
    assert task.estimated_credit_cost == expected
    assert task.status == "WAITING_ACCOUNT"
    account_query = str(session.statements[1])
    assert "accounts.balance_credits - accounts.reserved_credits" in account_query
    assert ">=" in account_query
    unavailable = next(
        item for item in session.added if item.__class__.__name__ == "TaskEvent"
    )
    assert unavailable.metadata_json == {
        "reason_code": "NO_ACTIVE_ACCOUNT",
        "pricing_rule_version": "leonardo-ui-20260812.v15",
        "estimated_credit_cost": expected,
        "retry_delay_seconds": 1,
        "counts": {
            "active_accounts": 0,
            "token_ready": 0,
            "account_slot_ready": 0,
            "credit_ready": 0,
            "active_space_ready": 0,
            "schedulable": 0,
        },
    }


def test_account_unavailable_retry_delay_is_exponential_and_capped() -> None:
    assert worker_module.account_unavailable_retry_delay(1, 2, 30) == 2
    assert worker_module.account_unavailable_retry_delay(2, 2, 30) == 4
    assert worker_module.account_unavailable_retry_delay(4, 2, 30) == 16
    assert worker_module.account_unavailable_retry_delay(5, 2, 30) == 30
    assert worker_module.account_unavailable_retry_delay(5904, 2, 30) == 30


def test_account_unavailable_diagnosis_identifies_space_saturation() -> None:
    diagnosis = asyncio.run(
        worker_module.diagnose_account_unavailable(
            DiagnosisSession([12, 12, 12, 12, 12, 0]),  # type: ignore[arg-type]
            datetime.now(UTC).replace(tzinfo=None),
            140,
        )
    )

    assert diagnosis.code == "SPACE_SATURATED"
    assert diagnosis.counts == {
        "active_accounts": 12,
        "token_ready": 12,
        "account_slot_ready": 12,
        "credit_ready": 12,
        "active_space_ready": 12,
        "schedulable": 0,
    }


def test_repeated_waiting_account_does_not_duplicate_unavailable_event(monkeypatch) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    queue = TaskQueue(
        task_id=1,
        queue_status="RETRY_WAIT",
        priority=0,
        available_at=now - timedelta(seconds=1),
        lease_owner=None,
        lease_until=None,
        delivery_attempts=4,
        last_error_code="SPACE_SATURATED",
    )
    task = Task(
        id=1,
        task_uuid="task-uuid",
        status="WAITING_ACCOUNT",
        model="gpt-image-2",
        input_json={"prompt": "test"},
        estimated_credit_cost=8,
        error_code="SPACE_SATURATED",
        version=0,
    )
    session = SelectionSession(queue, task)
    monkeypatch.setattr(worker_module, "session_factory", lambda: session)

    result = asyncio.run(
        worker_module.claim_and_assign(
            "worker-1",
            Settings(
                token_guard_seconds=120,
                retry_base_seconds=2,
                account_unavailable_retry_max_seconds=30,
            ),
        )
    )

    assert result is None
    assert task.status == "WAITING_ACCOUNT"
    assert queue.last_error_code == "SPACE_SATURATED"
    assert 29 <= (queue.available_at - now).total_seconds() <= 31
    assert not any(isinstance(item, TaskEvent) for item in session.added)


def test_terminal_submit_failure_records_released_reservation(monkeypatch) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    task = Task(
        id=1,
        task_uuid="task-uuid",
        status="RESOLVING_MEDIA",
        model="veo-3.1-generate-001",
        input_json={"prompt": "test"},
        estimated_credit_cost=800,
        reserved_credit_cost=800,
        submit_attempts=3,
        version=1,
    )
    assigned_account = Account(
        id=2,
        account_uuid="account-uuid",
        space_id=3,
        active_tasks=1,
        balance_credits=5000,
        reserved_credits=800,
        failed_tasks=0,
        status="ACTIVE",
        version=1,
    )
    assigned_space = Space(
        id=3,
        space_uuid="space-uuid",
        name="space",
        active_tasks=1,
    )
    queue = TaskQueue(
        task_id=1,
        queue_status="LEASED",
        priority=0,
        available_at=now,
        delivery_attempts=3,
    )
    session = FailureSession(
        task=task,
        account=assigned_account,
        space=assigned_space,
        queue=queue,
    )
    monkeypatch.setattr(worker_module, "session_factory", lambda: session)
    assignment = worker_module.Assignment(
        task_id=1,
        task_uuid="task-uuid",
        account_id=2,
        account_uuid="account-uuid",
        space_id=3,
        attempt_no=3,
        model="veo-3.1-generate-001",
        task_input={"prompt": "test"},
        encrypted_token=b"token",
        estimated_credit_cost=800,
        mode="reference-to-video",
        input_schema_version="veo-3.1.v1",
    )

    asyncio.run(
        worker_module.finish_submit_failure(
            assignment,
            UpstreamError("UPSTREAM_GRAPHQL_ERROR", "An error occurred.", retryable=True),
            "worker-1",
            Settings(worker_max_submit_attempts=3),
        )
    )

    assert task.status == "FAILED"
    assert task.reserved_credit_cost == 0
    assert assigned_account.reserved_credits == 0
    ledger = next(
        item for item in session.added if item.__class__.__name__ == "AccountCreditLedger"
    )
    assert ledger.entry_type == "RELEASE"
    assert ledger.balance_before == ledger.balance_after == 5000
    assert ledger.reserved_before == 800
    assert ledger.reserved_after == 0
    assert ledger.credit_delta == 0
    assert ledger.metadata_json == {
        "source": "worker_submit_failure",
        "phase": "RESOLVING_MEDIA",
        "attempt_no": 3,
        "retryable": False,
        "error_code": "UPSTREAM_GRAPHQL_ERROR",
        "model": "veo-3.1-generate-001",
        "estimated_credit_cost": 800,
        "released_credit_cost": 800,
    }


def test_suspended_submit_account_is_manually_disabled(monkeypatch) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    task = Task(
        id=11,
        task_uuid="suspended-task",
        status="SUBMITTING",
        model="fixture-model",
        input_json={"prompt": "test"},
        estimated_credit_cost=50,
        reserved_credit_cost=50,
        submit_attempts=1,
        version=1,
    )
    assigned_account = Account(
        id=12,
        account_uuid="suspended-account",
        space_id=13,
        active_tasks=1,
        balance_credits=500,
        reserved_credits=50,
        failed_tasks=0,
        status="ACTIVE",
        version=1,
    )
    assigned_space = Space(
        id=13,
        space_uuid="suspended-space",
        name="space",
        active_tasks=1,
    )
    queue = TaskQueue(
        task_id=11,
        queue_status="LEASED",
        priority=0,
        available_at=now,
        delivery_attempts=1,
    )
    session = FailureSession(
        task=task,
        account=assigned_account,
        space=assigned_space,
        queue=queue,
    )
    monkeypatch.setattr(worker_module, "session_factory", lambda: session)
    assignment = worker_module.Assignment(
        task_id=11,
        task_uuid="suspended-task",
        account_id=12,
        account_uuid="suspended-account",
        space_id=13,
        attempt_no=1,
        model="fixture-model",
        task_input={"prompt": "test"},
        encrypted_token=b"token",
        estimated_credit_cost=50,
    )

    asyncio.run(
        worker_module.finish_submit_failure(
            assignment,
            UpstreamError(
                "UPSTREAM_GRAPHQL_ERROR",
                "You are suspended for violating our user agreement.",
            ),
            "worker-1",
            Settings(worker_max_submit_attempts=3),
        )
    )

    assert task.status == "RETRY_WAIT"
    assert assigned_account.status == "MANUAL_DISABLED"
    assert assigned_account.disabled_reason == "upstream_account_suspended"
    assert assigned_account.last_error_code == "UPSTREAM_GRAPHQL_ERROR"


def test_expired_submit_lease_finishes_failed_instead_of_staying_unknown(
    monkeypatch,
) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    task = Task(
        id=51,
        task_uuid="submit-expired",
        status="SUBMITTING",
        model="seedance-2.0",
        input_json={"prompt": "fixture"},
        upstream_task_id=None,
        account_id=None,
        space_id=None,
        reserved_credit_cost=0,
        submit_attempts=1,
        error_code=None,
        error_message=None,
        version=0,
    )
    queue = TaskQueue(
        task_id=task.id,
        queue_status="LEASED",
        priority=0,
        available_at=now - timedelta(minutes=2),
        lease_owner="dead-worker",
        lease_until=now - timedelta(minutes=1),
        delivery_attempts=1,
    )
    attempt = TaskAttempt(
        id=71,
        attempt_uuid="attempt-expired",
        task_id=task.id,
        account_id=1,
        attempt_no=1,
        outcome="STARTED",
        started_at=now - timedelta(minutes=2),
    )
    session = RecoverySession(
        expired_queues=[queue], tasks={task.id: task}, attempts={task.id: attempt}
    )
    monkeypatch.setattr(worker_module, "session_factory", lambda: session)
    recovered = asyncio.run(worker_module.recover_expired_leases("worker-1"))
    assert recovered == 1
    assert task.status == "FAILED"
    assert task.finished_at is not None
    assert task.error_code == "WORKER_LEASE_EXPIRED_DURING_SUBMIT"
    assert queue.queue_status == "DONE"
    assert attempt.outcome == "FAILED"
    assert attempt.finished_at is not None
    assert attempt.error_code == "WORKER_LEASE_EXPIRED_DURING_SUBMIT"


def test_existing_submit_unknown_tasks_are_terminalized_by_recovery(monkeypatch) -> None:
    task = Task(
        id=52,
        task_uuid="legacy-submit-unknown",
        status="SUBMIT_UNKNOWN",
        model="seedance-2.0",
        input_json={"prompt": "fixture"},
        error_code="WORKER_LEASE_EXPIRED_DURING_SUBMIT",
        submit_attempts=1,
        version=0,
    )
    attempt = TaskAttempt(
        id=72,
        attempt_uuid="attempt-legacy-unknown",
        task_id=task.id,
        account_id=1,
        attempt_no=1,
        outcome="STARTED",
        started_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1),
    )
    session = RecoverySession(
        expired_queues=[],
        tasks={task.id: task},
        existing_unknown=[task],
        attempts={task.id: attempt},
    )
    monkeypatch.setattr(worker_module, "session_factory", lambda: session)
    recovered = asyncio.run(worker_module.recover_expired_leases("worker-1"))
    assert recovered == 1
    assert task.status == "FAILED"
    assert task.finished_at is not None
    assert attempt.outcome == "FAILED"
    assert attempt.finished_at is not None
