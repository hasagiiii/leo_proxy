from __future__ import annotations

import httpx

from video_task_service.api.main import app
from video_task_service.config import get_settings
from video_task_service.db import session_dependency
from video_task_service.models import Account


class _ScalarRows:
    def __init__(self, rows: list[object]) -> None:
        self.rows = rows

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self.rows)


class _ExecuteRows:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows

    def all(self) -> list[tuple[object, ...]]:
        return self.rows


class _Session:
    def __init__(self) -> None:
        self.account = Account(
            id=7,
            account_uuid="70000000-0000-0000-0000-000000000007",
            login_name="one@example.com",
            status="MANUAL_DISABLED",
            disabled_reason="manual",
            balance_credits=8500,
            completed_tasks=0,
            failed_tasks=2,
        )

    async def scalars(self, statement: object) -> _ScalarRows:
        del statement
        return _ScalarRows([self.account])

    async def execute(self, statement: object) -> _ExecuteRows:
        del statement
        return _ExecuteRows(
            [
                (7, "FAILED", "gpt-image-2"),
                (7, "SUBMIT_UNKNOWN", "gpt-image-2"),
            ]
        )


async def _session_override():  # type: ignore[no-untyped-def]
    yield _Session()


def test_email_audit_route_is_registered() -> None:
    assert "/admin/accounts/blocked-check" in app.openapi()["paths"]


async def test_email_audit_reads_account_state_and_image_tasks() -> None:
    app.dependency_overrides[session_dependency] = _session_override
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/admin/accounts/blocked-check",
                headers={"X-Admin-Key": get_settings().admin_auth_key_value},
                json={"emails": [" ONE@example.com ", "missing@example.com"]},
            )
    finally:
        app.dependency_overrides.pop(session_dependency, None)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["requested_count"] == 2
    assert body["matched_count"] == 1
    assert body["missing_count"] == 1
    assert body["blocked_count"] == 1
    assert body["image_success_account_count"] == 0
    assert body["image_success_task_count"] == 0
    assert body["items"][0] == {
        "email": "one@example.com",
        "in_account_pool": True,
        "account_uuid": "70000000-0000-0000-0000-000000000007",
        "account_status": "MANUAL_DISABLED",
        "disabled_reason": "manual",
        "balance_credits": 8500,
        "completed_tasks": 0,
        "failed_tasks": 2,
        "blocked": True,
        "blocked_source": "DB_MANUAL_STATUS",
        "image_task_total": 2,
        "image_task_success": 0,
        "image_task_failed": 2,
        "image_models": ["gpt-image-2"],
    }
    assert body["items"][1]["in_account_pool"] is False
    assert body["items"][1]["blocked"] is None


async def test_email_audit_rejects_invalid_email() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/admin/accounts/blocked-check",
            headers={"X-Admin-Key": get_settings().admin_auth_key_value},
            json={"emails": ["not-an-email"]},
        )
    assert response.status_code == 422
