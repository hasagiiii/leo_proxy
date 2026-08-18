from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from video_task_service.schemas import (
    CDPCookie,
    RegistrationCookieExportRequest,
    RegistrationJobClaimRequest,
    RegistrationJobResultRequest,
    RegistrationRecordView,
)


def cookie() -> dict[str, object]:
    return {
        "name": "__Secure-better-auth.session_token",
        "value": "fixture-cookie-secret",
        "domain": "app.leonardo.ai",
        "path": "/",
        "expires": 1_900_000_000,
        "httpOnly": True,
        "secure": True,
        "sameSite": "Lax",
    }


def test_cdp_cookie_uses_browser_field_names_and_hides_value() -> None:
    parsed = CDPCookie.model_validate(cookie())

    assert parsed.http_only is True
    assert parsed.same_site == "Lax"
    assert "fixture-cookie-secret" not in str(parsed)


def test_registration_cookie_export_accepts_500_emails_but_rejects_501() -> None:
    emails = [f"child-{index}@example.com" for index in range(500)]

    request = RegistrationCookieExportRequest(emails=emails)

    assert request.requested_emails == emails
    with pytest.raises(ValidationError):
        RegistrationCookieExportRequest(emails=[*emails, "overflow@example.com"])


def test_registered_result_requires_email_and_cookies_and_rejects_client_credit() -> None:
    valid = {
        "client_id": "desktop-01",
        "report_token": "r" * 32,
        "status": "REGISTERED",
        "registered_email": "Child@Example.com",
        "cookies": [cookie()],
    }
    request = RegistrationJobResultRequest.model_validate(valid)

    assert request.registered_email == "child@example.com"
    with pytest.raises(ValidationError):
        RegistrationJobResultRequest.model_validate({**valid, "awarded_points": 150})
    with pytest.raises(ValidationError):
        RegistrationJobResultRequest.model_validate({**valid, "cookies": None})


def test_failed_result_requires_error_and_rejects_cookie_material() -> None:
    valid = {
        "client_id": "desktop-01",
        "report_token": "r" * 32,
        "status": "FAILED",
        "error_code": "REGISTRATION_REJECTED",
    }
    request = RegistrationJobResultRequest.model_validate(valid)

    assert request.error_code == "REGISTRATION_REJECTED"
    with pytest.raises(ValidationError):
        RegistrationJobResultRequest.model_validate({**valid, "cookies": [cookie()]})
    with pytest.raises(ValidationError):
        RegistrationJobResultRequest.model_validate(
            {"client_id": "desktop-01", "report_token": "r" * 32, "status": "FAILED"}
        )


def test_claim_request_is_strict_and_normalized() -> None:
    request = RegistrationJobClaimRequest.model_validate(
        {"client_id": " desktop-01 ", "project_name": "  Canvas  "}
    )
    assert request.client_id == "desktop-01"
    assert request.project_name == "Canvas"
    assert RegistrationJobClaimRequest(client_id="desktop-01").project_name == "Canvas"
    with pytest.raises(ValidationError):
        RegistrationJobClaimRequest.model_validate(
            {"client_id": "desktop-01", "limit": 2}
        )


def test_registration_view_contains_metadata_but_no_secrets() -> None:
    view = RegistrationRecordView(
        registration_uuid=uuid4(),
        parent_account_uuid=uuid4(),
        parent_email="parent@example.com",
        email="child@example.com",
        client_id="desktop-01",
        status="SUCCEEDED",
        registered_email="child@example.com",
        verified_email="child@example.com",
        awarded_points=8_500,
        is_used=False,
        cookie_count=12,
        validation_attempts=1,
        validation_error_code=None,
        validation_error_message=None,
        started_at=datetime.now(UTC),
        reported_at=datetime.now(UTC),
        validation_finished_at=datetime.now(UTC),
        promoted_at=None,
        account_uuid=None,
        version=2,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    payload = view.model_dump()
    assert payload["awarded_points"] == 8_500
    assert "cookies" not in payload
    assert "video_token" not in payload
    assert "report_token" not in payload
