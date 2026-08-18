from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from video_task_service.config import Settings
from video_task_service.upstream import (
    LEONARDO_CONTENT_MODERATION_CODE,
    LEONARDO_CONTENT_MODERATION_MESSAGE,
    LEONARDO_GENERATION_NOTE_MESSAGES,
    LEONARDO_PROVIDER_FAILURE_MESSAGES,
    LEONARDO_VIDEO_FAILURE_MESSAGE,
    LeonardoUpstream,
    UpstreamError,
    is_account_suspension_error,
)


@pytest.mark.asyncio
async def test_balance_validation_returns_normalized_graphql_email_and_total() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "user_details": [
                        {
                            "subscriptionTokens": 8_000,
                            "paidTokens": 400,
                            "rolloverTokens": 100,
                            "auth0Email": "  Worker@Example.TEST  ",
                        }
                    ]
                }
            },
        )

    upstream = LeonardoUpstream(Settings())
    await upstream._client.aclose()
    upstream._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await upstream.validate_account(token="token")
    finally:
        await upstream.close()

    assert result.valid is True
    assert result.balance_credits == 8_500
    assert result.login_name == "worker@example.test"


async def poll_failed_generation(
    generation: dict[str, Any],
    generation_notes: list[dict[str, Any]] | None = None,
) -> tuple[Any, dict[str, Any]]:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.read()))
        return httpx.Response(
            200,
            json={
                "data": {
                    "generations": [generation],
                    "generationNotes": generation_notes or [],
                }
            },
        )

    upstream = LeonardoUpstream(Settings())
    await upstream._client.aclose()
    upstream._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await upstream.poll(
            token="token",
            generation_id=str(generation["id"]),
            submitted_at=datetime.now(UTC).replace(tzinfo=None),
            estimated_credit_cost=50,
        )
    finally:
        await upstream.close()
    return result, captured


@pytest.mark.asyncio
async def test_moderated_generation_exposes_exact_upstream_error_in_output() -> None:
    result, request = await poll_failed_generation(
        {"id": "generation-1", "status": "FAILED", "nsfw": True, "flagged": False}
    )

    assert "nsfw" in request["query"]
    assert "flagged" in request["query"]
    assert "generationNotes: generation_notes" in request["query"]
    assert request["variables"]["noteWhere"] == {
        "generationId": {"_eq": "generation-1"}
    }
    assert result.status == "FAILED"
    assert result.error_code == LEONARDO_CONTENT_MODERATION_CODE
    assert result.error_message == LEONARDO_CONTENT_MODERATION_MESSAGE
    assert result.output == {
        "provider": "leonardo",
        "generation_id": "generation-1",
        "error": {
            "code": LEONARDO_CONTENT_MODERATION_CODE,
            "message": LEONARDO_CONTENT_MODERATION_MESSAGE,
            "upstream_status": "FAILED",
            "nsfw": True,
            "flagged": False,
        },
    }


def test_provider_failure_message_table_contains_frontend_error_codes() -> None:
    assert set(LEONARDO_PROVIDER_FAILURE_MESSAGES) == {
        "PROVIDER_AUTHENTICATION_ERROR",
        "PROVIDER_RATE_LIMIT",
        "PROVIDER_INTERNAL_ERROR",
        "PROVIDER_INVALID_REQUEST",
        "PROVIDER_MODERATION_ERROR",
        "PROVIDER_OUTPUT_ERROR",
        "PROVIDER_TIMEOUT",
        "ALL_PROVIDERS_FAILED",
    }
    assert LEONARDO_CONTENT_MODERATION_CODE == "PROVIDER_MODERATION_ERROR"
    assert LEONARDO_PROVIDER_FAILURE_MESSAGES[LEONARDO_CONTENT_MODERATION_CODE] == (
        LEONARDO_CONTENT_MODERATION_MESSAGE
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_error_code", "expected_message"),
    list(LEONARDO_PROVIDER_FAILURE_MESSAGES.items()),
)
async def test_provider_failure_reason_uses_every_frontend_error_mapping(
    provider_error_code: str,
    expected_message: str,
) -> None:
    result, _ = await poll_failed_generation(
        {"id": "generation-note", "status": "FAILED", "nsfw": False},
        [
            {
                "noteType": "PROVIDER_FAILURE",
                "failureReason": {
                    "errorCode": provider_error_code,
                    "providerDetail": "preserved",
                },
            }
        ],
    )

    assert result.error_code == provider_error_code
    assert result.error_message == expected_message
    assert result.output["error"]["note_type"] == "PROVIDER_FAILURE"
    assert result.output["error"]["failure_reason"] == {
        "errorCode": provider_error_code,
        "providerDetail": "preserved",
    }


@pytest.mark.asyncio
async def test_content_safety_note_uses_frontend_note_type_fallback() -> None:
    result, _ = await poll_failed_generation(
        {"id": "generation-nsfw-note", "status": "FAILED", "nsfw": False},
        [{"noteType": "CC_NSFW_TOTAL_FAILURE", "failureReason": None}],
    )

    assert result.error_code == "CC_NSFW_TOTAL_FAILURE"
    assert result.error_message == LEONARDO_GENERATION_NOTE_MESSAGES[
        "CC_NSFW_TOTAL_FAILURE"
    ]
    assert result.output["error"]["note_type"] == "CC_NSFW_TOTAL_FAILURE"


@pytest.mark.asyncio
async def test_non_moderation_failure_keeps_generic_error_but_still_returns_output() -> None:
    result, _ = await poll_failed_generation(
        {"id": "generation-2", "status": "ERROR", "nsfw": False, "flagged": False}
    )

    assert result.error_code == "UPSTREAM_GENERATION_FAILED"
    assert result.error_message == LEONARDO_VIDEO_FAILURE_MESSAGE
    assert result.output is not None
    assert result.output["error"] == {
        "code": "UPSTREAM_GENERATION_FAILED",
        "message": LEONARDO_VIDEO_FAILURE_MESSAGE,
        "upstream_status": "ERROR",
        "nsfw": False,
        "flagged": False,
    }


@pytest.mark.asyncio
async def test_remote_missing_data_graphql_error_is_provider_unavailable() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "errors": [
                    {
                        "message": (
                            'Missing "data" field with no errors in response from remote'
                        )
                    }
                ]
            },
        )

    upstream = LeonardoUpstream(Settings())
    await upstream._client.aclose()
    upstream._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(UpstreamError) as caught:
            await upstream._gql("token", "Generate", {}, "mutation Generate { ok }")
    finally:
        await upstream.close()

    assert caught.value.code == "UPSTREAM_PROVIDER_UNAVAILABLE"
    assert caught.value.retryable is True


@pytest.mark.asyncio
async def test_other_graphql_error_keeps_generic_classification() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"errors": [{"message": "model rejected one parameter"}]},
        )

    upstream = LeonardoUpstream(Settings())
    await upstream._client.aclose()
    upstream._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(UpstreamError) as caught:
            await upstream._gql("token", "Generate", {}, "mutation Generate { ok }")
    finally:
        await upstream.close()

    assert caught.value.code == "UPSTREAM_GRAPHQL_ERROR"
    assert caught.value.retryable is True


def test_graphql_suspension_message_is_account_attributable() -> None:
    assert is_account_suspension_error(
        "UPSTREAM_GRAPHQL_ERROR",
        "You are suspended for violating our user agreement.",
    )
    assert not is_account_suspension_error(
        "UPSTREAM_GRAPHQL_ERROR",
        "model rejected one parameter",
    )


@pytest.mark.asyncio
async def test_insufficient_tokens_graphql_error_has_specific_classification() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"errors": [{"message": "Insufficient tokens"}]},
        )

    upstream = LeonardoUpstream(Settings())
    await upstream._client.aclose()
    upstream._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(UpstreamError) as caught:
            await upstream._gql("token", "Generate", {}, "mutation Generate { ok }")
    finally:
        await upstream.close()

    assert caught.value.code == "UPSTREAM_INSUFFICIENT_TOKENS"
    assert caught.value.retryable is True
