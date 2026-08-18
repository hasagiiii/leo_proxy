from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi import HTTPException, Response

from video_task_service.api.accounts import (
    account_export_receipt,
    bulk_delete_accounts,
    classify_account_deletions,
    export_account_credentials,
    verify_account_export_receipt,
)
from video_task_service.crypto import encrypt_secret
from video_task_service.models import Account
from video_task_service.schemas import (
    AccountBulkDeleteRequest,
    AccountBulkSelection,
)


class Transaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *args: object) -> None:
        return None


class ScalarSequenceSession:
    def __init__(self, values: list[list[object]]) -> None:
        self.values = values
        self.deleted: list[Account] = []

    def begin(self) -> Transaction:
        return Transaction()

    async def scalars(self, statement: object) -> list[object]:
        return self.values.pop(0)

    async def delete(self, account: Account) -> None:
        self.deleted.append(account)


def account(
    index: int,
    *,
    active_tasks: int = 0,
    reserved_credits: int = 0,
    token: str | None = "token",
    credential_source: str = "PASSWORD",
) -> Account:
    account_uuid = f"10000000-0000-0000-0000-{index:012d}"
    return Account(
        id=index,
        account_uuid=account_uuid,
        space_id=1,
        login_name=f"account-{index}@example.test",
        credential_source=credential_source,
        password_ciphertext=encrypt_secret(
            f"password-{index}",
            f"{account_uuid}:password",
        ),
        video_token_ciphertext=(
            encrypt_secret(token, f"{account_uuid}:video_token") if token is not None else None
        ),
        active_tasks=active_tasks,
        reserved_credits=reserved_credits,
    )


def empty_history_results(*, task_account_ids: list[int] | None = None) -> list[list[object]]:
    return [task_account_ids or [], [], [], [], [], []]


def test_bulk_selection_deduplicates_uuids_in_first_seen_order() -> None:
    first = UUID("10000000-0000-0000-0000-000000000001")
    second = UUID("10000000-0000-0000-0000-000000000002")

    selection = AccountBulkSelection(account_uuids=[first, second, first])

    assert selection.account_uuids == [first, second]


def test_export_receipt_matches_exact_selection_and_expires() -> None:
    now = datetime(2026, 8, 8, 8, 0, tzinfo=UTC)
    selected = [UUID("10000000-0000-0000-0000-000000000001")]
    receipt = account_export_receipt(selected, now=now)

    verify_account_export_receipt(receipt, selected, now=now + timedelta(minutes=5))

    with pytest.raises(HTTPException) as mismatch:
        verify_account_export_receipt(
            receipt,
            [UUID("10000000-0000-0000-0000-000000000002")],
            now=now,
        )
    assert mismatch.value.detail["code"] == "ACCOUNT_EXPORT_RECEIPT_INVALID"

    with pytest.raises(HTTPException) as expired:
        verify_account_export_receipt(receipt, selected, now=now + timedelta(minutes=11))
    assert expired.value.detail["code"] == "ACCOUNT_EXPORT_RECEIPT_INVALID"


def test_credential_export_is_headerless_pipe_text_and_allows_empty_token() -> None:
    first = account(1)
    second = account(2, token=None)
    selected = AccountBulkSelection(
        account_uuids=[UUID(first.account_uuid), UUID(second.account_uuid)]
    )
    session = ScalarSequenceSession([[second, first]])

    response = asyncio.run(
        export_account_credentials(selected, session=session)  # type: ignore[arg-type]
    )

    assert response.body.decode("utf-8") == (
        "account-1@example.test|password-1|token\n"
        "account-2@example.test|password-2|"
    )
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.headers["x-exported-count"] == "2"
    assert response.headers["x-account-export-receipt"]
    assert response.headers["cache-control"] == "no-store"


def test_credential_export_hides_cookie_session_marker_password() -> None:
    cookie_account = account(3, credential_source="COOKIE_SESSION")
    selected = AccountBulkSelection(account_uuids=[UUID(cookie_account.account_uuid)])
    session = ScalarSequenceSession([[cookie_account]])

    response = asyncio.run(
        export_account_credentials(selected, session=session)  # type: ignore[arg-type]
    )

    assert response.body.decode("utf-8") == "account-3@example.test||token"
    assert "password-3" not in response.body.decode("utf-8")


def test_delete_preview_classifies_active_reserved_history_and_missing() -> None:
    active = account(1, active_tasks=1)
    reserved = account(2, reserved_credits=10)
    historical = account(3)
    deletable = account(4)
    missing_uuid = UUID("10000000-0000-0000-0000-000000000005")
    selected = [
        UUID(active.account_uuid),
        UUID(reserved.account_uuid),
        UUID(historical.account_uuid),
        UUID(deletable.account_uuid),
        missing_uuid,
    ]
    session = ScalarSequenceSession(
        [[active, reserved, historical, deletable], *empty_history_results(task_account_ids=[3])]
    )

    _, preview = asyncio.run(
        classify_account_deletions(session, selected, lock=False)  # type: ignore[arg-type]
    )

    assert preview.requested == 5
    assert preview.deletable == 1
    assert preview.protected == 3
    assert preview.missing == 1
    assert [item.code for item in preview.items] == [
        "ACCOUNT_HAS_ACTIVE_TASKS",
        "ACCOUNT_HAS_RESERVED_CREDITS",
        "ACCOUNT_HAS_HISTORY",
        None,
        "ACCOUNT_NOT_FOUND",
    ]


def test_bulk_delete_requires_matching_export_and_deletes_only_eligible_accounts() -> None:
    protected = account(1, active_tasks=1)
    deletable = account(2)
    selected = [UUID(protected.account_uuid), UUID(deletable.account_uuid)]
    receipt = account_export_receipt(selected)
    session = ScalarSequenceSession(
        [[protected, deletable], *empty_history_results()]
    )
    response = Response()

    result = asyncio.run(
        bulk_delete_accounts(
            AccountBulkDeleteRequest(
                account_uuids=selected,
                export_receipt=receipt,
            ),
            response,
            session=session,  # type: ignore[arg-type]
        )
    )

    assert result.requested == 2
    assert result.deleted == 1
    assert result.skipped == 1
    assert [item.outcome for item in result.items] == ["SKIPPED", "DELETED"]
    assert session.deleted == [deletable]
    assert response.headers["cache-control"] == "no-store"
