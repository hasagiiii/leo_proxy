from __future__ import annotations

import asyncio
from collections.abc import Iterable
from uuid import UUID

import pytest
from fastapi import HTTPException, Response
from sqlalchemy.dialects import mysql

from video_task_service.api.parent_accounts import (
    delete_parent_account,
    import_parent_accounts,
    invitation_result_statement,
    list_parent_accounts,
    parent_account_from_record,
    parent_account_stats,
    parent_account_view,
    record_parent_account_invitation_result,
)
from video_task_service.crypto import decrypt_secret
from video_task_service.parent_accounts import parse_parent_account_import
from video_task_service.schemas import (
    ParentAccountImportRequest,
    ParentAccountInvitationResultRequest,
)

PARENT_UUID = UUID("67420f85-e589-4356-9c3a-12345678d086")


class Transaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *args: object) -> None:
        return None


class ImportSession:
    def __init__(self, existing: Iterable[str] = ()) -> None:
        self.existing = list(existing)
        self.added: list[object] = []

    def begin(self) -> Transaction:
        return Transaction()

    async def scalars(self, statement: object) -> list[str]:
        return self.existing

    def add_all(self, values: list[object]) -> None:
        self.added.extend(values)

    async def flush(self) -> None:
        return None


class StatsResult:
    def one(self) -> tuple[int, ...]:
        return 3, 7, 2, 2, 1, 4, 1, 9, 3


class StatsSession:
    async def execute(self, statement: object) -> StatsResult:
        return StatsResult()


class EntitySession:
    def __init__(self, account: object | None) -> None:
        self.account = account
        self.deleted: list[object] = []

    def begin(self) -> Transaction:
        return Transaction()

    async def scalar(self, statement: object) -> object | None:
        if "count(" in str(statement).lower():
            return 0
        return self.account

    async def delete(self, account: object) -> None:
        self.deleted.append(account)


class UpdateResult:
    rowcount = 1


class InvitationResultSession:
    def __init__(self, account: object) -> None:
        self.account = account
        self.executed: list[object] = []

    def begin(self) -> Transaction:
        return Transaction()

    async def execute(self, statement: object) -> UpdateResult:
        self.executed.append(statement)
        self.account.invite_failure_count += 1  # type: ignore[attr-defined]
        return UpdateResult()

    async def scalar(self, statement: object) -> object:
        return self.account


def test_parent_account_record_encrypts_password_and_view_decrypts_it() -> None:
    record = parse_parent_account_import(
        "user@example.com Visible-Secret https://example.test/join"
    ).records[0]

    account = parent_account_from_record(record)
    view = parent_account_view(account)

    assert account.password_encrypted != b"Visible-Secret"
    assert (
        decrypt_secret(
            account.password_encrypted,
            f"{account.parent_account_uuid}:parent_account_password",
        )
        == "Visible-Secret"
    )
    assert view.password == "Visible-Secret"
    assert view.invite_success_count == 0
    assert view.invite_failure_count == 0
    assert not hasattr(view, "password_encrypted")


def test_parent_account_bulk_import_skips_existing_and_never_echoes_passwords() -> None:
    session = ImportSession(existing=["existing@example.com"])
    response = Response()
    request = ParentAccountImportRequest(
        content=(
            "existing@example.com Old-Secret https://example.test/existing\n"
            "new@example.com Visible-Secret https://example.test/new"
        )
    )

    result = asyncio.run(
        import_parent_accounts(request, response, session=session)  # type: ignore[arg-type]
    )

    assert result.requested == 2
    assert result.imported == 1
    assert result.duplicates == 1
    assert result.invalid == 0
    assert result.issues[0].code == "DUPLICATE_EXISTING"
    assert len(session.added) == 1
    assert response.headers["cache-control"] == "no-store"
    assert "Secret" not in result.model_dump_json()


def test_parent_account_stats_aggregates_all_counters() -> None:
    response = Response()

    result = asyncio.run(
        parent_account_stats(response, session=StatsSession())  # type: ignore[arg-type]
    )

    assert result.total_parent_accounts == 3
    assert result.total_invite_successes == 7
    assert result.total_invite_failures == 2
    assert result.active_parent_accounts == 2
    assert result.exhausted_parent_accounts == 1
    assert result.traceable_registrations == 9
    assert result.promotable_registrations == 3
    assert response.headers["cache-control"] == "no-store"


def test_parent_account_list_returns_decrypted_password() -> None:
    account = parent_account_from_record(
        parse_parent_account_import(
            "user@example.com Visible-Secret https://example.test/join"
        ).records[0]
    )

    class ListSession:
        async def scalar(self, statement: object) -> int:
            return 1

        async def scalars(self, statement: object) -> list[object]:
            return [account]

        async def execute(self, statement: object) -> list[object]:
            return []

    response = Response()
    result = asyncio.run(
        list_parent_accounts(  # type: ignore[arg-type]
            response,
            session=ListSession(),
            search="USER",
            limit=50,
            offset=0,
        )
    )

    assert result.total == 1
    assert result.items[0].email == "user@example.com"
    assert result.items[0].password == "Visible-Secret"
    assert response.headers["cache-control"] == "no-store"


def test_delete_parent_account_removes_selected_record() -> None:
    account = parent_account_from_record(
        parse_parent_account_import("user@example.com Secret https://example.test/join").records[0]
    )
    account.parent_account_uuid = str(PARENT_UUID)
    session = EntitySession(account)

    asyncio.run(
        delete_parent_account(  # type: ignore[arg-type]
            PARENT_UUID,
            Response(),
            session=session,
        )
    )

    assert session.deleted == [account]


def test_delete_parent_account_returns_stable_not_found_error() -> None:
    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            delete_parent_account(  # type: ignore[arg-type]
                PARENT_UUID,
                Response(),
                session=EntitySession(None),
            )
        )

    assert caught.value.status_code == 404
    assert caught.value.detail["code"] == "PARENT_ACCOUNT_NOT_FOUND"


def test_invitation_result_statement_increments_only_selected_counter() -> None:
    success_sql = str(
        invitation_result_statement(PARENT_UUID, success=True).compile(dialect=mysql.dialect())
    )
    failure_sql = str(
        invitation_result_statement(PARENT_UUID, success=False).compile(dialect=mysql.dialect())
    )

    assert "invite_success_count=(parent_accounts.invite_success_count + %s)" in success_sql
    assert "invite_failure_count=" not in success_sql
    assert "invite_failure_count=(parent_accounts.invite_failure_count + %s)" in failure_sql
    assert "invite_success_count=" not in failure_sql


def test_invitation_result_endpoint_is_retired() -> None:
    account = parent_account_from_record(
        parse_parent_account_import("user@example.com Secret https://example.test/join").records[0]
    )
    session = InvitationResultSession(account)
    account_uuid = UUID(account.parent_account_uuid)

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            record_parent_account_invitation_result(  # type: ignore[arg-type]
                account_uuid,
                ParentAccountInvitationResultRequest(success=False),
                Response(),
                session=session,
            )
        )

    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == "PARENT_ACCOUNT_INVITATION_RESULT_RETIRED"
    assert session.executed == []
