#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4


class ResponseError(RuntimeError):
    def __init__(self, status: int, payload: Any) -> None:
        super().__init__(f"HTTP {status}: {payload}")
        self.status = status
        self.payload = payload


def request(
    base_url: str,
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, Any]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    all_headers = {"content-type": "application/json", **(headers or {})}
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=body,
        method=method,
        headers=all_headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            content = response.read()
            return response.status, json.loads(content) if content else None
    except urllib.error.HTTPError as exc:
        content = exc.read()
        parsed = json.loads(content) if content else None
        raise ResponseError(exc.code, parsed) from exc


def wait_until(message: str, timeout: float, callback):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = callback()
            if last:
                return last
        except (urllib.error.URLError, TimeoutError, ResponseError) as exc:
            last = exc
        time.sleep(0.25)
    raise RuntimeError(f"timeout waiting for {message}; last={last}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18080")
    parser.add_argument("--api-key", default="local-api-key")
    parser.add_argument("--admin-key", default="local-admin-key")
    args = parser.parse_args()

    admin_headers = {"X-Admin-Key": args.admin_key}
    api_headers = {"X-API-Key": args.api_key}
    wait_until(
        "API readiness",
        60,
        lambda: request(args.base_url, "GET", "/health/ready")[1].get("status") == "ready",
    )

    suffix = uuid4().hex[:10]
    _, space = request(
        args.base_url,
        "POST",
        "/admin/spaces",
        headers=admin_headers,
        payload={"name": f"smoke-{suffix}", "max_concurrency": 4},
    )
    expiry = datetime.now(timezone.utc) + timedelta(hours=2)
    _, account = request(
        args.base_url,
        "POST",
        "/admin/accounts",
        headers=admin_headers,
        payload={
            "space_uuid": space["space_uuid"],
            "login_name": f"smoke-{suffix}@example.com",
            "password": "local-password",
            "video_token": f"mock-token-{suffix}",
            "token_expires_at": expiry.isoformat(),
            "balance_credits": 1000,
            "max_concurrency": 2,
        },
    )
    if "video_token" in account or "password" in account:
        raise AssertionError("credential leaked in account response")

    def active_account():
        _, current = request(
            args.base_url,
            "GET",
            f"/admin/accounts/{account['account_uuid']}",
            headers=admin_headers,
        )
        return current if current["status"] == "ACTIVE" else None

    account = wait_until("account activation", 20, active_account)

    idempotency_key = f"smoke-task-{suffix}"
    task_payload = {
        "model": "seedance-2.0-mini",
        "task_type": "VIDEO_GENERATION",
        "input": {
            "prompt": "A paper boat sailing across a calm blue pond",
            "duration": 4,
            "width": 864,
            "height": 496,
            "quantity": 1,
        },
        "estimated_credit_cost": 50,
    }
    task_headers = {**api_headers, "Idempotency-Key": idempotency_key}
    _, task = request(
        args.base_url,
        "POST",
        "/v1/tasks",
        headers=task_headers,
        payload=task_payload,
    )
    _, duplicate = request(
        args.base_url,
        "POST",
        "/v1/tasks",
        headers=task_headers,
        payload=task_payload,
    )
    if duplicate["task_uuid"] != task["task_uuid"]:
        raise AssertionError("idempotent request created another task")

    def completed_task():
        _, current = request(
            args.base_url,
            "GET",
            f"/v1/tasks/{task['task_uuid']}",
            headers=api_headers,
        )
        if current["status"] == "FAILED":
            raise RuntimeError(f"task failed: {current}")
        return current if current["status"] == "COMPLETED" else None

    task = wait_until("task completion", 30, completed_task)
    media = (task.get("output") or {}).get("media") or []
    if not media or not media[0].get("url"):
        raise AssertionError("completed task has no media result")

    _, account = request(
        args.base_url,
        "GET",
        f"/admin/accounts/{account['account_uuid']}",
        headers=admin_headers,
    )
    if account["completed_tasks"] != 1 or account["balance_credits"] != 950:
        raise AssertionError(f"account settlement mismatch: {account}")

    old_version = account["version"]
    _, updated = request(
        args.base_url,
        "PUT",
        f"/admin/accounts/{account['account_uuid']}/token",
        headers=admin_headers,
        payload={
            "video_token": f"mock-token-updated-{suffix}",
            "token_expires_at": (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat(),
            "expected_version": old_version,
        },
    )
    if updated["version"] != old_version + 1 or updated["status"] != "PENDING_VALIDATION":
        raise AssertionError(f"token update mismatch: {updated}")
    try:
        request(
            args.base_url,
            "PUT",
            f"/admin/accounts/{account['account_uuid']}/token",
            headers=admin_headers,
            payload={
                "video_token": "stale-write",
                "token_expires_at": (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat(),
                "expected_version": old_version,
            },
        )
        raise AssertionError("stale token update unexpectedly succeeded")
    except ResponseError as exc:
        if exc.status != 409:
            raise

    reactivated = wait_until("updated token validation", 20, active_account)
    summary = {
        "result": "PASS",
        "space_uuid": space["space_uuid"],
        "account_uuid": reactivated["account_uuid"],
        "account_status": reactivated["status"],
        "account_balance": reactivated["balance_credits"],
        "task_uuid": task["task_uuid"],
        "upstream_task_id": task["upstream_task_id"],
        "task_status": task["status"],
        "actual_credit_cost": task["actual_credit_cost"],
        "media_url": media[0]["url"],
        "idempotency_verified": True,
        "token_version_conflict_verified": True,
    }
    json.dump(summary, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
