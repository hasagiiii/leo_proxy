from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy.dialects import mysql

from video_task_service.config import Settings
from video_task_service.crypto import encrypt_secret
from video_task_service.protocol_renewal import ProtocolRenewalResult
from video_task_service.registration_validator import (
    ClaimedRegistration,
    RegistrationValidationError,
    registration_validation_claim_statement,
    validate_registration,
)
from video_task_service.schemas import RenewalSessionPayload
from video_task_service.upstream import AccountValidation


def _session() -> RenewalSessionPayload:
    return RenewalSessionPayload.model_validate(
        {
            "cookies": [
                {
                    "name": "session",
                    "value": "fixture-cookie-secret",
                    "domain": ".leonardo.ai",
                }
            ],
            "user_agent": "Chrome/136",
        }
    )


def _claim() -> ClaimedRegistration:
    registration_uuid = "b04fc99a-c906-438a-81c3-12345678c70a"
    material = _session().model_dump(mode="json")
    material["cookies"][0]["value"] = "fixture-cookie-secret"
    return ClaimedRegistration(
        registration_id=3,
        registration_uuid=registration_uuid,
        parent_account_id=7,
        email="child@example.com",
        registered_email="child@example.com",
        session_ciphertext=encrypt_secret(
            json.dumps(material),
            f"{registration_uuid}:registration_session",
        ),
        owner="validator-a",
        claimed_version=2,
        attempt_count=1,
        lease_until=datetime(2026, 8, 13, 9, 0),
    )


class StubUpstream:
    def __init__(self, result: AccountValidation) -> None:
        self.result = result
        self.tokens: list[str] = []

    async def validate_account(self, *, token: str) -> AccountValidation:
        self.tokens.append(token)
        return self.result


async def protocol(**kwargs: object) -> ProtocolRenewalResult:
    material = kwargs["material"]
    assert isinstance(material, dict)
    return ProtocolRenewalResult(
        token="fixture-video-token",
        token_expires_at=datetime(2026, 8, 13, 10, 0),
        renewal_session=material,
        token_changed=True,
        needs_refresh=False,
        get_session_status=200,
        cross_origin_cookie_status=0,
        session_email="child@example.com",
    )


def test_validator_claims_cookie_reported_retry_and_expired_lease_rows() -> None:
    now = datetime(2026, 8, 13, 8, 30)
    sql = str(
        registration_validation_claim_statement(now, 2).compile(
            dialect=mysql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).lower()

    assert "cookie_reported" in sql
    assert "validation_retry_wait" in sql
    assert "validating" in sql
    assert "skip locked" in sql
    assert "limit 2" in sql


def test_validator_uses_cookie_session_and_server_credit_only() -> None:
    upstream = StubUpstream(
        AccountValidation(
            valid=True,
            login_name="child@example.com",
            balance_credits=150,
        )
    )

    result = asyncio.run(
        validate_registration(_claim(), protocol, upstream, Settings())  # type: ignore[arg-type]
    )

    assert result.login_name == "child@example.com"
    assert result.balance_credits == 150
    assert result.token == "fixture-video-token"
    assert upstream.tokens == ["fixture-video-token"]


def test_validator_rejects_session_email_mismatch_terminally() -> None:
    async def wrong_protocol(**kwargs: object) -> ProtocolRenewalResult:
        result = await protocol(**kwargs)
        result.session_email = "other@example.com"
        return result

    with pytest.raises(RegistrationValidationError) as caught:
        asyncio.run(
            validate_registration(
                _claim(),
                wrong_protocol,
                StubUpstream(AccountValidation(valid=True)),  # type: ignore[arg-type]
                Settings(),
            )
        )

    assert caught.value.code == "REGISTRATION_IDENTITY_MISMATCH"
    assert caught.value.retryable is False


@pytest.mark.parametrize(
    "validation",
    [
        AccountValidation(valid=False, error_code="UPSTREAM_RATE_LIMITED"),
        AccountValidation(valid=False, error_code="UPSTREAM_NETWORK_ERROR"),
        AccountValidation(valid=False, error_code="UPSTREAM_SERVER_ERROR"),
        AccountValidation(valid=True, login_name=None, balance_credits=None),
    ],
)
def test_validator_retries_transient_and_missing_server_fields(
    validation: AccountValidation,
) -> None:
    with pytest.raises(RegistrationValidationError) as caught:
        asyncio.run(
            validate_registration(
                _claim(),
                protocol,
                StubUpstream(validation),
                Settings(),  # type: ignore[arg-type]
            )
        )

    assert caught.value.retryable is True


def test_rotated_session_is_retained_for_promotion() -> None:
    upstream = StubUpstream(
        AccountValidation(
            valid=True,
            login_name="child@example.com",
            balance_credits=900,
        )
    )
    result = asyncio.run(
        validate_registration(_claim(), protocol, upstream, Settings())  # type: ignore[arg-type]
    )

    assert result.renewal_session.cookies[0].value.get_secret_value() == "fixture-cookie-secret"
    assert result.token_expires_at - datetime(2026, 8, 13, 9, 0) == timedelta(hours=1)
