from __future__ import annotations

import asyncio

import video_task_service.worker as worker_module
from video_task_service.config import Settings
from video_task_service.upstream import AccountValidation, SubmitResult, UpstreamError
from video_task_service.worker import Assignment, handle_assignment


class RecordingUpstream:
    def __init__(self, validation: AccountValidation) -> None:
        self.validation = validation
        self.calls: list[str] = []

    async def validate_account(self, *, token: str) -> AccountValidation:
        assert token == "plain-token"
        self.calls.append("validate")
        return self.validation

    async def submit(self, *, token: str, model: str, task_input: dict) -> SubmitResult:
        self.calls.append("submit")
        return SubmitResult(generation_id="generation-1")


def assignment() -> Assignment:
    return Assignment(
        task_id=1,
        task_uuid="task-uuid",
        account_id=2,
        account_uuid="account-uuid",
        space_id=3,
        attempt_no=1,
        model="model",
        task_input={"prompt": "test"},
        encrypted_token=b"ciphertext",
        estimated_credit_cost=50,
    )


def settings() -> Settings:
    return Settings(
        upstream_mode="leonardo",
        token_guard_seconds=120,
        low_balance_threshold=100,
    )


def test_handle_assignment_refreshes_balance_before_submit(monkeypatch) -> None:
    upstream = RecordingUpstream(AccountValidation(valid=True, balance_credits=500))
    persisted: list[int] = []
    succeeded: list[str] = []

    async def persist(assignment, validation, worker_id, settings):
        upstream.calls.append("persist")
        persisted.append(validation.balance_credits)
        return None

    async def success(assignment, result, worker_id):
        succeeded.append(result.generation_id)

    monkeypatch.setattr(worker_module, "decrypt_secret", lambda *_: "plain-token")
    monkeypatch.setattr(worker_module, "apply_pre_submit_balance_refresh", persist)
    monkeypatch.setattr(worker_module, "finish_submit_success", success)

    asyncio.run(handle_assignment(assignment(), upstream, "worker-1", settings()))

    assert upstream.calls == ["validate", "persist", "submit"]
    assert persisted == [500]
    assert succeeded == ["generation-1"]


def test_handle_assignment_stops_before_submit_when_balance_check_fails(monkeypatch) -> None:
    upstream = RecordingUpstream(AccountValidation(valid=True, balance_credits=25))
    failures: list[str] = []

    async def reject(assignment, validation, worker_id, settings):
        upstream.calls.append("persist")
        return UpstreamError("PRE_SUBMIT_LOW_BALANCE", "balance too low")

    async def failure(assignment, error, worker_id, settings):
        failures.append(error.code)

    monkeypatch.setattr(worker_module, "decrypt_secret", lambda *_: "plain-token")
    monkeypatch.setattr(worker_module, "apply_pre_submit_balance_refresh", reject)
    monkeypatch.setattr(worker_module, "finish_submit_failure", failure)

    asyncio.run(handle_assignment(assignment(), upstream, "worker-1", settings()))

    assert upstream.calls == ["validate", "persist"]
    assert failures == ["PRE_SUBMIT_LOW_BALANCE"]
