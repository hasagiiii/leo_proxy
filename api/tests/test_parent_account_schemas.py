from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from video_task_service.schemas import (
    ParentAccountInvitationResultRequest,
    ParentAccountView,
)


def test_parent_account_invitation_result_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ParentAccountInvitationResultRequest.model_validate(
            {"success": True, "count": 4}
        )


def test_parent_account_view_accepts_display_password_and_nonnegative_counts() -> None:
    view = ParentAccountView(
        parent_account_uuid=uuid4(),
        email="user@example.com",
        password="Visible-Secret",
        invite_url="https://example.test/join",
        invite_success_count=0,
        invite_failure_count=0,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    assert view.password == "Visible-Secret"


def test_parent_account_view_rejects_negative_counters() -> None:
    with pytest.raises(ValidationError):
        ParentAccountView(
            parent_account_uuid=uuid4(),
            email="user@example.com",
            password="Visible-Secret",
            invite_url="https://example.test/join",
            invite_success_count=-1,
            invite_failure_count=0,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
