from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import UTC, datetime

from fastapi import Response

from video_task_service.api.mailboxes import (
    import_mailboxes,
    mailbox_from_record,
    mailbox_import_window,
    mailbox_view,
)
from video_task_service.crypto import decrypt_secret
from video_task_service.mailbox_codes import parse_mailbox_import
from video_task_service.schemas import MailboxImportRequest


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


def test_mailbox_record_is_encrypted_and_view_omits_credentials() -> None:
    record = parse_mailbox_import(
        "user@example.com----password----client-id----refresh-token"
    ).records[0]

    mailbox = mailbox_from_record(record)
    view = mailbox_view(mailbox)

    assert mailbox.password_ciphertext != b"password"
    assert decrypt_secret(
        mailbox.password_ciphertext,
        f"{mailbox.mailbox_uuid}:mailbox_password",
    ) == "password"
    assert decrypt_secret(
        mailbox.client_id_ciphertext,
        f"{mailbox.mailbox_uuid}:mailbox_client_id",
    ) == "client-id"
    assert decrypt_secret(
        mailbox.refresh_token_ciphertext,
        f"{mailbox.mailbox_uuid}:mailbox_refresh_token",
    ) == "refresh-token"
    assert view.email == "user@example.com"
    assert not hasattr(view, "refresh_token")
    assert not hasattr(view, "password")


def test_bulk_import_skips_existing_email_and_never_echoes_secrets() -> None:
    session = ImportSession(existing=["existing@example.com"])
    response = Response()
    request = MailboxImportRequest(
        content=(
            "existing@example.com----old-secret----client-a----refresh-a\n"
            "new@example.com----new-secret----client-b----refresh-b"
        )
    )

    result = asyncio.run(
        import_mailboxes(request, response, session=session)  # type: ignore[arg-type]
    )

    assert result.requested == 2
    assert result.imported == 1
    assert result.duplicates == 1
    assert result.invalid == 0
    assert result.issues[0].code == "DUPLICATE_EXISTING"
    assert len(session.added) == 1
    assert response.headers["cache-control"] == "no-store"
    assert "secret" not in result.model_dump_json()


def test_mailbox_import_windows_are_mutually_exclusive_local_day_buckets() -> None:
    now = datetime(2026, 8, 13, 10, 30, tzinfo=UTC)

    assert mailbox_import_window("today", 480, now=now) == (
        datetime(2026, 8, 12, 16),
        datetime(2026, 8, 13, 16),
    )
    assert mailbox_import_window("yesterday", 480, now=now) == (
        datetime(2026, 8, 11, 16),
        datetime(2026, 8, 12, 16),
    )
    assert mailbox_import_window("recent_7d", 480, now=now) == (
        datetime(2026, 8, 5, 16),
        datetime(2026, 8, 11, 16),
    )
    assert mailbox_import_window("older", 480, now=now) == (
        None,
        datetime(2026, 8, 5, 16),
    )
