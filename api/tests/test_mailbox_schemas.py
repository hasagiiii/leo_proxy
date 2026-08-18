from datetime import datetime

import pytest
from pydantic import ValidationError

from video_task_service.schemas import (
    MailboxCodeQuery,
    MailboxCodeResult,
    MailboxImportRequest,
    MailboxPatch,
)


def test_mailbox_import_content_is_secret() -> None:
    request = MailboxImportRequest(
        content="user@example.com----password----client----refresh"
    )

    assert request.content.get_secret_value().startswith("user@example.com")
    assert "password" not in repr(request)


def test_mailbox_code_query_normalizes_email_and_bounds_timeout() -> None:
    query = MailboxCodeQuery(email=" USER@Example.COM ", timeout_seconds=120)

    assert query.email == "user@example.com"
    assert query.timeout_seconds == 120

    with pytest.raises(ValidationError):
        MailboxCodeQuery(email="user@example.com", timeout_seconds=0)
    with pytest.raises(ValidationError):
        MailboxCodeQuery(email="user@example.com", timeout_seconds=121)


def test_mailbox_code_query_rejects_malformed_email() -> None:
    with pytest.raises(ValidationError):
        MailboxCodeQuery(email="not-an-email")


def test_mailbox_patch_requires_supported_manual_status() -> None:
    assert MailboxPatch(manual_status="MANUAL_DISABLED", expected_version=3).expected_version == 3
    assert MailboxPatch(
        manual_status="PENDING_VALIDATION", expected_version=4
    ).manual_status == "PENDING_VALIDATION"

    with pytest.raises(ValidationError):
        MailboxPatch(manual_status="ACTIVE", expected_version=0)  # type: ignore[arg-type]


def test_mailbox_code_result_contract() -> None:
    result = MailboxCodeResult(
        email="user@example.com",
        code="483921",
        received_at=datetime(2026, 8, 13, 1, 2, 3),
        subject="Your verification code",
        sender="no-reply@example.com",
        message_id="message-id",
        matched_by="KEYWORD_NEARBY",
    )

    assert result.code == "483921"
    assert result.matched_by == "KEYWORD_NEARBY"
