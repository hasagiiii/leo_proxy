from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

import httpx

from video_task_service.mailbox_codes import (
    KEYWORD_PATTERN,
    VerificationCodeMatch,
    extract_verification_code,
)

ATOMICMAIL_API_BASE_URL = "https://api.atomicmail.io"
ATOMICMAIL_CODE_LOOKBACK_SECONDS = 10 * 60
ATOMICMAIL_CODE_POLL_SECONDS = 3.0
ATOMICMAIL_DOMAIN = "atomicmail.io"


class AtomicMailError(RuntimeError):
    code = "ATOMICMAIL_PROVIDER_UNAVAILABLE"


class AtomicMailCredentialFormatInvalid(AtomicMailError):
    code = "ATOMICMAIL_CREDENTIAL_FORMAT_INVALID"


class AtomicMailCredentialsInvalid(AtomicMailError):
    code = "ATOMICMAIL_CREDENTIALS_INVALID"


class AtomicMailProviderRateLimited(AtomicMailError):
    code = "ATOMICMAIL_PROVIDER_RATE_LIMITED"

    def __init__(self, retry_after_seconds: int = 60) -> None:
        super().__init__("Atomic Mail rate limit reached")
        self.retry_after_seconds = retry_after_seconds


class AtomicMailProviderUnavailable(AtomicMailError):
    code = "ATOMICMAIL_PROVIDER_UNAVAILABLE"


class AtomicMailCodeTimeout(AtomicMailError):
    code = "ATOMICMAIL_CODE_TIMEOUT"


@dataclass(frozen=True)
class AtomicMailCredentials:
    email: str
    password: str


@dataclass(frozen=True)
class AtomicMailCode:
    email: str
    code: str
    received_at: datetime
    subject: str
    sender: str
    message_id: str
    matched_by: Literal["KEYWORD_NEARBY", "HTML_EMPHASIS", "NUMERIC_FALLBACK"]


class AtomicMailCodeClient(Protocol):
    async def login(self, credentials: AtomicMailCredentials) -> str: ...

    async def inbox_id(self, access_token: str) -> str: ...

    async def messages(self, access_token: str, mailbox_id: str) -> list[dict[str, Any]]: ...

    async def message(
        self,
        access_token: str,
        mailbox_id: str,
        message_id: str,
    ) -> dict[str, Any]: ...


def parse_atomicmail_credential(value: str) -> AtomicMailCredentials:
    line = value.replace("\ufeff", "").strip("\r\n")
    if "\n" in line or "\r" in line or "|" not in line:
        raise AtomicMailCredentialFormatInvalid("credential must be one 邮箱|密码 line")
    email_value, password = line.split("|", 1)
    email = email_value.strip().lower()
    if not email or not password:
        raise AtomicMailCredentialFormatInvalid("email and password are required")
    local_part, separator, domain = email.rpartition("@")
    if separator != "@" or not local_part or domain != ATOMICMAIL_DOMAIN:
        raise AtomicMailCredentialFormatInvalid("email must belong to atomicmail.io")
    if len(email) > 255 or len(password) > 4096:
        raise AtomicMailCredentialFormatInvalid("credential field is too long")
    return AtomicMailCredentials(email=email, password=password)


def _device_id(email: str) -> str:
    seed = f"video-task-service|{email.lower()}|atomicmail"
    return hashlib.sha256(seed.encode()).hexdigest()[:32]


def _json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        value = response.json()
    except ValueError as exc:
        raise AtomicMailProviderUnavailable("Atomic Mail returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise AtomicMailProviderUnavailable("Atomic Mail returned invalid JSON")
    return value


def _retry_after(response: httpx.Response) -> int:
    try:
        return max(int(response.headers.get("Retry-After", "60")), 1)
    except ValueError:
        return 60


class AtomicMailClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        timeout_seconds: float = 15,
        base_url: str = ATOMICMAIL_API_BASE_URL,
    ) -> None:
        self.http = http
        self.timeout_seconds = timeout_seconds
        self.base_url = base_url.rstrip("/")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        json: dict[str, Any] | None = None,
    ) -> tuple[httpx.Response, dict[str, Any]]:
        headers = {"Accept": "application/json"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        try:
            response = await self.http.request(
                method,
                f"{self.base_url}{path}",
                headers=headers,
                json=json,
                timeout=self.timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise AtomicMailProviderUnavailable("Atomic Mail request failed") from exc
        if response.status_code == 429:
            raise AtomicMailProviderRateLimited(_retry_after(response))
        if response.status_code >= 500:
            raise AtomicMailProviderUnavailable("Atomic Mail service is unavailable")
        return response, _json_object(response)

    async def login(self, credentials: AtomicMailCredentials) -> str:
        username = credentials.email.removesuffix(f"@{ATOMICMAIL_DOMAIN}")
        password_sha256 = hashlib.sha256(credentials.password.encode()).hexdigest()
        response, payload = await self._request(
            "POST",
            "/v1/auth/sign-in",
            json={
                "username": username,
                "password": password_sha256,
                "token2fa": None,
                "platform": "web",
                "deviceId": _device_id(credentials.email),
            },
        )
        if response.status_code in {400, 401, 403, 422}:
            raise AtomicMailCredentialsInvalid("Atomic Mail credentials were rejected")
        if response.status_code != 201:
            raise AtomicMailProviderUnavailable("Atomic Mail login returned an unexpected status")
        access_token = payload.get("accessToken")
        if not isinstance(access_token, str) or not access_token:
            raise AtomicMailProviderUnavailable("Atomic Mail login omitted access token")
        return access_token

    async def inbox_id(self, access_token: str) -> str:
        response, payload = await self._request("GET", "/v1/mailboxes", token=access_token)
        if response.status_code in {401, 403}:
            raise AtomicMailCredentialsInvalid("Atomic Mail session was rejected")
        if response.status_code != 200:
            raise AtomicMailProviderUnavailable("Atomic Mail mailbox query failed")
        results = payload.get("results")
        if not isinstance(results, list):
            raise AtomicMailProviderUnavailable("Atomic Mail mailbox response is invalid")
        for item in results:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path", ""))
            name = str(item.get("name", ""))
            mailbox_id = item.get("id")
            if (path.upper() == "INBOX" or name.lower() == "inbox") and isinstance(
                mailbox_id, str
            ):
                return mailbox_id
        raise AtomicMailProviderUnavailable("Atomic Mail Inbox was not found")

    async def messages(self, access_token: str, mailbox_id: str) -> list[dict[str, Any]]:
        response, payload = await self._request(
            "GET",
            f"/v1/mailboxes/{mailbox_id}/messages",
            token=access_token,
        )
        if response.status_code in {401, 403}:
            raise AtomicMailCredentialsInvalid("Atomic Mail session was rejected")
        if response.status_code != 200:
            raise AtomicMailProviderUnavailable("Atomic Mail message list query failed")
        results = payload.get("results")
        if not isinstance(results, list):
            raise AtomicMailProviderUnavailable("Atomic Mail message list is invalid")
        return [item for item in results if isinstance(item, dict)]

    async def message(
        self,
        access_token: str,
        mailbox_id: str,
        message_id: str,
    ) -> dict[str, Any]:
        response, payload = await self._request(
            "GET",
            f"/v1/mailboxes/{mailbox_id}/messages/{message_id}",
            token=access_token,
        )
        if response.status_code in {401, 403}:
            raise AtomicMailCredentialsInvalid("Atomic Mail session was rejected")
        if response.status_code != 200:
            raise AtomicMailProviderUnavailable("Atomic Mail message query failed")
        result = payload.get("result")
        return result if isinstance(result, dict) else payload


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _message_received_at(message: dict[str, Any]) -> datetime | None:
    return _parse_datetime(message.get("date")) or _parse_datetime(message.get("idate"))


def _message_body(message: dict[str, Any]) -> str:
    values: list[str] = []
    text = message.get("text")
    if isinstance(text, str):
        values.append(text)
    html = message.get("html")
    if isinstance(html, str):
        values.append(html)
    elif isinstance(html, list):
        values.extend(item for item in html if isinstance(item, str))
    return "\n".join(values)


def _sender(message: dict[str, Any]) -> str:
    sender = message.get("from")
    if not isinstance(sender, dict):
        return ""
    return str(sender.get("address", ""))


def _strict_code_match(
    subject: str,
    preview: str,
    body: str,
) -> VerificationCodeMatch | None:
    match = extract_verification_code(subject, preview, body)
    if match is None:
        return None
    if match.matched_by != "NUMERIC_FALLBACK":
        return match
    if KEYWORD_PATTERN.search(f"{subject}\n{preview}") is not None:
        return match
    return None


async def fetch_atomicmail_code(
    credentials: AtomicMailCredentials,
    client: AtomicMailCodeClient,
    *,
    timeout_seconds: int,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> AtomicMailCode:
    deadline = monotonic() + timeout_seconds
    access_token = await client.login(credentials)
    inbox_id = await client.inbox_id(access_token)
    checked_message_ids: set[str] = set()

    while monotonic() < deadline:
        summaries = await client.messages(access_token, inbox_id)
        cutoff = now().astimezone(UTC) - timedelta(seconds=ATOMICMAIL_CODE_LOOKBACK_SECONDS)
        candidates: list[tuple[datetime, dict[str, Any]]] = []
        for summary in summaries:
            message_id = str(summary.get("id", ""))
            received_at = _message_received_at(summary)
            if not message_id or message_id in checked_message_ids or received_at is None:
                continue
            if received_at >= cutoff:
                candidates.append((received_at, summary))
        candidates.sort(key=lambda item: item[0], reverse=True)

        for received_at, summary in candidates:
            message_id = str(summary["id"])
            detail = await client.message(access_token, inbox_id, message_id)
            subject = str(detail.get("subject", summary.get("subject", "")))
            preview = str(summary.get("intro") or "")
            match = _strict_code_match(subject, preview, _message_body(detail))
            if match is not None:
                return AtomicMailCode(
                    email=credentials.email,
                    code=match.code,
                    received_at=_message_received_at(detail) or received_at,
                    subject=subject,
                    sender=_sender(detail) or _sender(summary),
                    message_id=message_id,
                    matched_by=match.matched_by,
                )
            checked_message_ids.add(message_id)

        remaining = deadline - monotonic()
        if remaining <= 0:
            break
        await sleep(min(ATOMICMAIL_CODE_POLL_SECONDS, remaining))

    raise AtomicMailCodeTimeout("no verification code arrived before the deadline")
