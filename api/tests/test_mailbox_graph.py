from datetime import UTC, datetime

import httpx
import pytest

from video_task_service.mailbox_graph import (
    MailboxCredentialsInvalid,
    MailboxProviderRateLimited,
    MailboxProviderUnavailable,
    MicrosoftGraphMailboxClient,
)


@pytest.mark.asyncio
async def test_get_access_token_uses_consumer_refresh_flow() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/consumers/oauth2/v2.0/token")
        assert b"grant_type=refresh_token" in request.content
        assert b"client_id=client-id" in request.content
        return httpx.Response(200, json={"access_token": "access-token"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = MicrosoftGraphMailboxClient(http)
        token = await client.get_access_token("refresh-token", "client-id")

    assert token == "access-token"


@pytest.mark.asyncio
async def test_invalid_grant_is_a_deterministic_credential_error() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": "invalid_grant", "error_description": "expired refresh token"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(MailboxCredentialsInvalid) as raised:
            await MicrosoftGraphMailboxClient(http).get_access_token("refresh", "client")

    assert raised.value.code == "MAILBOX_TOKEN_INVALID"
    assert "refresh" not in str(raised.value)


@pytest.mark.asyncio
async def test_rate_limit_and_server_errors_are_typed() -> None:
    responses = [
        httpx.Response(429, headers={"Retry-After": "17"}),
        httpx.Response(503, text="temporarily down"),
    ]

    async def handler(_: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = MicrosoftGraphMailboxClient(http)
        with pytest.raises(MailboxProviderRateLimited) as limited:
            await client.latest_message("token")
        with pytest.raises(MailboxProviderUnavailable):
            await client.latest_message("token")

    assert limited.value.retry_after_seconds == 17


@pytest.mark.asyncio
async def test_transport_timeout_is_provider_unavailable() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow provider", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(MailboxProviderUnavailable):
            await MicrosoftGraphMailboxClient(http).latest_message("token")


@pytest.mark.asyncio
async def test_latest_message_maps_minimal_graph_fields() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer access-token"
        assert request.url.params["$top"] == "1"
        assert request.url.params["$orderby"] == "receivedDateTime desc"
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "message-id",
                        "subject": "Your verification code",
                        "from": {"emailAddress": {"address": "sender@example.com"}},
                        "receivedDateTime": "2026-08-13T01:02:03Z",
                        "bodyPreview": "Code 483921",
                        "body": {"contentType": "html", "content": "<b>483921</b>"},
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        message = await MicrosoftGraphMailboxClient(http).latest_message("access-token")

    assert message is not None
    assert message.message_id == "message-id"
    assert message.sender == "sender@example.com"
    assert message.received_at == datetime(2026, 8, 13, 1, 2, 3, tzinfo=UTC)
    assert message.body_content == "<b>483921</b>"


@pytest.mark.asyncio
async def test_empty_graph_mailbox_returns_none() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"value": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert await MicrosoftGraphMailboxClient(http).latest_message("token") is None
