from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

MICROSOFT_TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
GRAPH_MESSAGES_URL = "https://graph.microsoft.com/v1.0/me/messages"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"


class MailboxGraphError(RuntimeError):
    code = "MAILBOX_PROVIDER_UNAVAILABLE"

    def __init__(self, message: str) -> None:
        super().__init__(message)


class MailboxCredentialsInvalid(MailboxGraphError):
    code = "MAILBOX_TOKEN_INVALID"


class MailboxProviderRateLimited(MailboxGraphError):
    code = "MAILBOX_PROVIDER_RATE_LIMITED"

    def __init__(self, retry_after_seconds: int = 60) -> None:
        super().__init__("Microsoft Graph rate limit reached")
        self.retry_after_seconds = retry_after_seconds


class MailboxProviderUnavailable(MailboxGraphError):
    code = "MAILBOX_PROVIDER_UNAVAILABLE"


@dataclass(frozen=True)
class GraphMessage:
    message_id: str
    subject: str
    sender: str
    received_at: datetime
    body_preview: str
    body_content: str


def _retry_after(response: httpx.Response) -> int:
    value = response.headers.get("Retry-After", "60")
    try:
        return max(int(value), 1)
    except ValueError:
        return 60


def _parse_received_at(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class MicrosoftGraphMailboxClient:
    def __init__(self, http: httpx.AsyncClient, *, timeout_seconds: float = 15) -> None:
        self.http = http
        self.timeout_seconds = timeout_seconds

    async def get_access_token(self, refresh_token: str, client_id: str) -> str:
        try:
            response = await self.http.post(
                MICROSOFT_TOKEN_URL,
                data={
                    "client_id": client_id,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "scope": GRAPH_SCOPE,
                },
                timeout=self.timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise MailboxProviderUnavailable("Microsoft token endpoint unavailable") from exc

        if response.status_code == 429:
            raise MailboxProviderRateLimited(_retry_after(response))
        payload = _json_object(response)
        error = str(payload.get("error", ""))
        if error in {"invalid_grant", "invalid_client", "unauthorized_client"}:
            raise MailboxCredentialsInvalid("Mailbox OAuth credentials are invalid")
        if response.status_code >= 500:
            raise MailboxProviderUnavailable("Microsoft token endpoint unavailable")
        if response.status_code >= 400:
            raise MailboxCredentialsInvalid("Mailbox OAuth authorization failed")
        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise MailboxProviderUnavailable("Microsoft token response omitted access token")
        return access_token

    async def latest_message(self, access_token: str) -> GraphMessage | None:
        try:
            response = await self.http.get(
                GRAPH_MESSAGES_URL,
                headers={"Authorization": f"Bearer {access_token}"},
                params={
                    "$top": "1",
                    "$orderby": "receivedDateTime desc",
                    "$select": "id,subject,from,receivedDateTime,bodyPreview,body",
                },
                timeout=self.timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise MailboxProviderUnavailable("Microsoft Graph unavailable") from exc

        if response.status_code == 429:
            raise MailboxProviderRateLimited(_retry_after(response))
        if response.status_code in {401, 403}:
            raise MailboxCredentialsInvalid("Mailbox Graph authorization is invalid")
        if response.status_code >= 400:
            raise MailboxProviderUnavailable("Microsoft Graph request failed")
        values = _json_object(response).get("value", [])
        if not isinstance(values, list) or not values:
            return None
        item = values[0]
        if not isinstance(item, dict):
            raise MailboxProviderUnavailable("Microsoft Graph returned an invalid message")
        body = item.get("body") if isinstance(item.get("body"), dict) else {}
        sender_block = item.get("from") if isinstance(item.get("from"), dict) else {}
        email_address = (
            sender_block.get("emailAddress")
            if isinstance(sender_block.get("emailAddress"), dict)
            else {}
        )
        try:
            received_at = _parse_received_at(str(item["receivedDateTime"]))
        except (KeyError, ValueError) as exc:
            raise MailboxProviderUnavailable(
                "Microsoft Graph message omitted received time"
            ) from exc
        return GraphMessage(
            message_id=str(item.get("id", "")),
            subject=str(item.get("subject", "")),
            sender=str(email_address.get("address", "")),
            received_at=received_at,
            body_preview=str(item.get("bodyPreview", "")),
            body_content=str(body.get("content", "")),
        )


def _json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        value = response.json()
    except ValueError as exc:
        raise MailboxProviderUnavailable("Microsoft endpoint returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise MailboxProviderUnavailable("Microsoft endpoint returned invalid JSON")
    return value
