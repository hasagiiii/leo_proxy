from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import ipaddress
import json
import math
import mimetypes
import re
import socket
import time
import wave
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import unquote, urljoin, urlparse
from uuid import uuid4

import av
import httpx
from PIL import Image, UnidentifiedImageError

from video_task_service.config import Settings, get_settings
from video_task_service.h3 import MediaSpec, ResolvedMedia

LEONARDO_TOKEN_CREDIT_MESSAGE = (
    "Your tokens have been credited back to your account."
)
LEONARDO_PROVIDER_FAILURE_MESSAGES = {
    "PROVIDER_AUTHENTICATION_ERROR": (
        "Too many requests right now. Wait a moment, then try again. "
        f"{LEONARDO_TOKEN_CREDIT_MESSAGE}"
    ),
    "PROVIDER_RATE_LIMIT": (
        "Too many requests right now. Wait a moment, then try again. "
        f"{LEONARDO_TOKEN_CREDIT_MESSAGE}"
    ),
    "PROVIDER_INTERNAL_ERROR": (
        "Something went wrong with that model. Try generating again. "
        f"{LEONARDO_TOKEN_CREDIT_MESSAGE}"
    ),
    "PROVIDER_INVALID_REQUEST": (
        "We couldn't run this generation. "
        "Try pressing the 'Reset to Defaults' button. "
        f"{LEONARDO_TOKEN_CREDIT_MESSAGE}"
    ),
    "PROVIDER_MODERATION_ERROR": (
        "The content of your generation was moderated by this Model. "
        "Try rewording your prompt, changing reference images or changing the Model. "
        f"{LEONARDO_TOKEN_CREDIT_MESSAGE}"
    ),
    "PROVIDER_OUTPUT_ERROR": (
        "The output failed to save. Try generating again. "
        f"{LEONARDO_TOKEN_CREDIT_MESSAGE}"
    ),
    "PROVIDER_TIMEOUT": (
        "Generation timed out. Try generating again. "
        f"{LEONARDO_TOKEN_CREDIT_MESSAGE}"
    ),
    "ALL_PROVIDERS_FAILED": (
        "Sorry, we hit an error. Try again in a few minutes. "
        f"{LEONARDO_TOKEN_CREDIT_MESSAGE}"
    ),
}
LEONARDO_GENERATION_NOTE_MESSAGES = {
    "CC_NSFW_TOTAL_FAILURE": (
        "Your request did not meet content safety guidelines. "
        f"{LEONARDO_TOKEN_CREDIT_MESSAGE}"
    ),
}
LEONARDO_VIDEO_FAILURE_MESSAGE = (
    "We couldn't create your video. " f"{LEONARDO_TOKEN_CREDIT_MESSAGE}"
)
LEONARDO_CONTENT_MODERATION_CODE = "PROVIDER_MODERATION_ERROR"
LEONARDO_CONTENT_MODERATION_MESSAGE = LEONARDO_PROVIDER_FAILURE_MESSAGES[
    LEONARDO_CONTENT_MODERATION_CODE
]
UPSTREAM_ACCOUNT_SUSPENSION_MESSAGE = (
    "you are suspended for violating our user agreement"
)


def is_account_suspension_error(error_code: str, error_message: str) -> bool:
    """Identify the account-level suspension hidden inside a GraphQL error."""

    normalized_message = " ".join(error_message.casefold().split()).rstrip(".")
    return (
        error_code.strip().upper() == "UPSTREAM_GRAPHQL_ERROR"
        and UPSTREAM_ACCOUNT_SUSPENSION_MESSAGE in normalized_message
    )


class UpstreamError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = True,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


@dataclass(slots=True)
class MediaHostCircuitState:
    failures: deque[float] = field(default_factory=deque)
    open_until: float = 0.0


class MediaHostCircuitBreaker:
    """Fail fast for repeatedly unhealthy media origins without blocking Worker slots."""

    def __init__(
        self,
        hosts: str,
        *,
        failure_threshold: int,
        window_seconds: float,
        open_seconds: float,
    ) -> None:
        self._hosts = tuple(
            sorted(
                {
                    item.strip().lower().rstrip(".")
                    for item in hosts.split(",")
                    if item.strip()
                }
            )
        )
        self._failure_threshold = failure_threshold
        self._window_seconds = window_seconds
        self._open_seconds = open_seconds
        self._states: dict[str, MediaHostCircuitState] = {}

    def _protected(self, host: str) -> bool:
        normalized = host.lower().rstrip(".")
        return any(
            normalized == configured or normalized.endswith(f".{configured}")
            for configured in self._hosts
        )

    def before_request(self, host: str, *, now: float | None = None) -> None:
        if not self._protected(host):
            return
        current = time.monotonic() if now is None else now
        state = self._states.get(host)
        if state is None:
            return
        if state.open_until > current:
            retry_after = max(math.ceil(state.open_until - current), 1)
            raise UpstreamError(
                "MEDIA_HOST_CIRCUIT_OPEN",
                f"media host circuit is open for {host}",
                retry_after_seconds=retry_after,
            )
        if state.open_until:
            state.open_until = 0.0
            state.failures.clear()

    def record_failure(self, host: str, *, now: float | None = None) -> None:
        if not self._protected(host):
            return
        current = time.monotonic() if now is None else now
        state = self._states.setdefault(host, MediaHostCircuitState())
        cutoff = current - self._window_seconds
        while state.failures and state.failures[0] < cutoff:
            state.failures.popleft()
        state.failures.append(current)
        if len(state.failures) >= self._failure_threshold:
            state.open_until = current + self._open_seconds

    def record_success(self, host: str) -> None:
        if not self._protected(host):
            return
        self._states.pop(host, None)


@dataclass(slots=True)
class SubmitResult:
    generation_id: str
    api_credit_cost: int | None = None
    response: dict[str, Any] | None = None


@dataclass(slots=True)
class PollResult:
    status: str
    output: dict[str, Any] | None = None
    actual_credit_cost: int | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class LeonardoFailure:
    code: str
    message: str
    note_type: str | None = None
    failure_reason: dict[str, Any] | None = None


def resolve_leonardo_failure(
    generation_notes: object,
    *,
    nsfw: bool,
) -> LeonardoFailure:
    """Mirror Leonardo Web's generation-note failure selection order."""

    notes = (
        [note for note in generation_notes if isinstance(note, dict)]
        if isinstance(generation_notes, list)
        else []
    )
    for note in notes:
        failure_reason = note.get("failureReason")
        if not isinstance(failure_reason, dict):
            continue
        provider_code = failure_reason.get("errorCode")
        if not isinstance(provider_code, str):
            continue
        message = LEONARDO_PROVIDER_FAILURE_MESSAGES.get(provider_code)
        if message is not None:
            note_type = note.get("noteType")
            return LeonardoFailure(
                code=provider_code,
                message=message,
                note_type=note_type if isinstance(note_type, str) else None,
                failure_reason=dict(failure_reason),
            )

    for note in notes:
        note_type = note.get("noteType")
        if not isinstance(note_type, str):
            continue
        message = LEONARDO_GENERATION_NOTE_MESSAGES.get(note_type)
        if message is not None:
            return LeonardoFailure(
                code=note_type,
                message=message,
                note_type=note_type,
            )

    # Retain compatibility with older responses that only exposed the nsfw flag.
    if nsfw:
        return LeonardoFailure(
            code=LEONARDO_CONTENT_MODERATION_CODE,
            message=LEONARDO_CONTENT_MODERATION_MESSAGE,
        )

    return LeonardoFailure(
        code="UPSTREAM_GENERATION_FAILED",
        message=LEONARDO_VIDEO_FAILURE_MESSAGE,
    )


@dataclass(slots=True)
class AccountValidation:
    valid: bool
    balance_credits: int | None = None
    login_name: str | None = None
    error_code: str | None = None


@dataclass(slots=True)
class DownloadedMedia:
    content: bytes
    content_type: str
    file_name: str
    extension: str
    duration_ms: int | None = None
    width: int | None = None
    height: int | None = None
    frame_rate: float | None = None
    audio_channels: int | None = None
    audio_sample_rate: int | None = None


def validate_reference_video_media(
    media: DownloadedMedia | ResolvedMedia,
    *,
    enforce_dimensions: bool = True,
    enforce_duration_bounds: bool = True,
) -> None:
    """Apply the constraints shown by Leonardo's current Video Reference picker."""

    extension = getattr(media, "extension", None)
    content_type = (media.content_type or "").lower()
    if extension is not None:
        if extension.lower() not in {"mp4", "mov"}:
            raise UpstreamError(
                "MEDIA_VIDEO_FORMAT_UNSUPPORTED",
                "reference video must be an MP4 or MOV file",
                retryable=False,
            )
    elif content_type and content_type not in {"video/mp4", "video/quicktime"}:
        raise UpstreamError(
            "MEDIA_VIDEO_FORMAT_UNSUPPORTED",
            "reference video must be an MP4 or MOV file",
            retryable=False,
        )

    if media.duration_ms is None or media.duration_ms <= 0:
        raise UpstreamError(
            "MEDIA_VIDEO_DURATION_INVALID",
            "reference video duration must be detectable and greater than 0 seconds",
            retryable=False,
        )
    if enforce_duration_bounds and not 3_000 <= media.duration_ms <= 10_000:
        raise UpstreamError(
            "MEDIA_VIDEO_DURATION_INVALID",
            "reference video duration must be between 3 and 10 seconds",
            retryable=False,
        )
    if enforce_dimensions and (
        media.width is None
        or media.height is None
        or not 720 <= media.width <= 2160
        or not 720 <= media.height <= 2160
    ):
        raise UpstreamError(
            "MEDIA_VIDEO_DIMENSIONS_INVALID",
            "reference video dimensions must each be between 720 and 2160 pixels",
            retryable=False,
        )
    if media.frame_rate is not None and not 24 <= round(media.frame_rate, 2) <= 60:
        raise UpstreamError(
            "MEDIA_VIDEO_FRAME_RATE_INVALID",
            "reference video frame rate must be between 24 and 60 FPS",
            retryable=False,
        )
    if media.audio_channels is not None and media.audio_channels <= 0:
        raise UpstreamError(
            "MEDIA_VIDEO_AUDIO_INVALID",
            "reference video contains an invalid audio stream",
            retryable=False,
        )


def validate_reference_audio_media(
    media: DownloadedMedia | ResolvedMedia,
    *,
    max_duration_ms: int = 15_000,
) -> None:
    if media.duration_ms is None or not 2_000 <= media.duration_ms <= max_duration_ms:
        raise UpstreamError(
            "MEDIA_DURATION_INVALID",
            "reference audio duration must be between 2 and "
            f"{max_duration_ms / 1000:g} seconds",
            retryable=False,
        )


def validate_reference_audio_collection(
    assets: list[ResolvedMedia],
    *,
    max_combined_duration_ms: int = 15_000,
    max_individual_duration_ms: int = 15_000,
) -> None:
    audios = [asset for asset in assets if asset.role == "REFERENCE_AUDIO"]
    for audio in audios:
        validate_reference_audio_media(
            audio,
            max_duration_ms=max_individual_duration_ms,
        )
    combined_duration_ms = sum(audio.duration_ms or 0 for audio in audios)
    if combined_duration_ms > max_combined_duration_ms:
        raise UpstreamError(
            "MEDIA_COMBINED_DURATION_INVALID",
            "combined reference audio duration exceeds "
            f"{max_combined_duration_ms / 1000:g} seconds",
            retryable=False,
        )


def validate_reference_video_collection(
    assets: list[ResolvedMedia],
    *,
    max_combined_duration_ms: int = 15_000,
    enforce_dimensions: bool = True,
    enforce_individual_duration_bounds: bool = True,
) -> None:
    videos = [asset for asset in assets if asset.role == "REFERENCE_VIDEO"]
    for video in videos:
        validate_reference_video_media(
            video,
            enforce_dimensions=enforce_dimensions,
            enforce_duration_bounds=enforce_individual_duration_bounds,
        )
    combined_duration_ms = sum(video.duration_ms or 0 for video in videos)
    if combined_duration_ms > max_combined_duration_ms:
        raise UpstreamError(
            "MEDIA_COMBINED_DURATION_INVALID",
            "combined reference video duration exceeds "
            f"{max_combined_duration_ms / 1000:g} seconds",
            retryable=False,
        )


class Upstream(Protocol):
    async def resolve_media(
        self,
        *,
        token: str,
        spec: MediaSpec,
        max_audio_duration_ms: int = 15_000,
        enforce_video_dimensions: bool = True,
        enforce_video_duration_bounds: bool = True,
    ) -> ResolvedMedia: ...

    async def submit(
        self,
        *,
        token: str,
        model: str,
        task_input: dict[str, Any],
    ) -> SubmitResult: ...

    async def poll(
        self,
        *,
        token: str,
        generation_id: str,
        submitted_at: datetime,
        estimated_credit_cost: int,
    ) -> PollResult: ...

    async def validate_account(self, *, token: str) -> AccountValidation: ...

    async def close(self) -> None: ...


class MockUpstream:
    def __init__(self, settings: Settings) -> None:
        self._generation_seconds = settings.mock_generation_seconds

    async def submit(
        self,
        *,
        token: str,
        model: str,
        task_input: dict[str, Any],
    ) -> SubmitResult:
        if token.startswith("invalid"):
            raise UpstreamError("UPSTREAM_UNAUTHORIZED", "mock token rejected", retryable=False)
        if task_input.get("force_submit_failure"):
            raise UpstreamError("MOCK_SUBMIT_FAILED", "forced mock submit failure")
        request = task_input.get("request")
        parameters = request.get("parameters") if isinstance(request, dict) else None
        dimensions = ""
        if isinstance(parameters, dict):
            width = parameters.get("width")
            height = parameters.get("height")
            if isinstance(width, int) and isinstance(height, int):
                dimensions = f"-{width}x{height}"
        generation_id = f"mock-{model}{dimensions}-{uuid4()}"
        return SubmitResult(
            generation_id=generation_id,
            api_credit_cost=None,
            response={"mode": "mock", "model": model},
        )

    async def resolve_media(
        self,
        *,
        token: str,
        spec: MediaSpec,
        max_audio_duration_ms: int = 15_000,
        enforce_video_dimensions: bool = True,
        enforce_video_duration_bounds: bool = True,
    ) -> ResolvedMedia:
        if token.startswith("invalid"):
            raise UpstreamError("UPSTREAM_UNAUTHORIZED", "mock token rejected", retryable=False)
        digest = hashlib.sha256(spec.source_url.encode()).hexdigest()[:24]
        return ResolvedMedia(
            kind=spec.kind,
            role=spec.role,
            ordinal=spec.ordinal,
            source_url=spec.source_url,
            provider_asset_id=f"mock-{spec.kind.lower()}-{digest}",
            content_type={
                "IMAGE": "image/png",
                "AUDIO": "audio/mpeg",
                "VIDEO": "video/mp4",
            }[spec.kind],
            content_length=1024,
            duration_ms=5000 if spec.kind in {"AUDIO", "VIDEO"} else None,
            width=1920 if spec.kind in {"IMAGE", "VIDEO"} else None,
            height=1080 if spec.kind in {"IMAGE", "VIDEO"} else None,
        )

    async def poll(
        self,
        *,
        token: str,
        generation_id: str,
        submitted_at: datetime,
        estimated_credit_cost: int,
    ) -> PollResult:
        if token.startswith("invalid"):
            raise UpstreamError("UPSTREAM_UNAUTHORIZED", "mock token rejected", retryable=False)
        now = datetime.now(UTC).replace(tzinfo=None)
        elapsed = (now - submitted_at).total_seconds()
        if elapsed < self._generation_seconds:
            return PollResult(status="RUNNING")
        if any(
            image_model in generation_id
            for image_model in ("gpt-image-2", "nano-banana-2", "nano-banana-pro")
        ):
            match = re.search(r"-(\d+)x(\d+)-[0-9a-f-]+$", generation_id)
            width, height = (
                (int(match.group(1)), int(match.group(2)))
                if match is not None
                else (1024, 1024)
            )
            content = io.BytesIO()
            Image.new("RGB", (width, height), color=(37, 99, 235)).save(
                content, format="PNG", optimize=True
            )
            data_url = "data:image/png;base64," + base64.b64encode(
                content.getvalue()
            ).decode()
            media = [
                {
                    "type": "image/png",
                    "url": data_url,
                    "width": width,
                    "height": height,
                }
            ]
        elif "seed-audio-1.0" in generation_id:
            content = io.BytesIO()
            with wave.open(content, "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(24_000)
                output.writeframes(b"\x00\x00" * 2_400)
            data_url = "data:audio/wav;base64," + base64.b64encode(
                content.getvalue()
            ).decode()
            media = [
                {
                    "type": "audio/wav",
                    "url": data_url,
                    "duration_seconds": 0.1,
                    "sample_rate": 24_000,
                    "channels": 1,
                }
            ]
        else:
            media = [
                {
                    "type": "video/mp4",
                    "url": f"https://example.invalid/generated/{generation_id}.mp4",
                    "width": 1280,
                    "height": 720,
                    "duration_seconds": 4,
                }
            ]
        return PollResult(
            status="COMPLETE",
            actual_credit_cost=estimated_credit_cost,
            output={
                "provider": "mock",
                "generation_id": generation_id,
                "media": media,
            },
        )

    async def validate_account(self, *, token: str) -> AccountValidation:
        if not token or token.startswith("invalid"):
            return AccountValidation(valid=False, error_code="UPSTREAM_UNAUTHORIZED")
        return AccountValidation(valid=True)

    async def close(self) -> None:
        return None


class LeonardoUpstream:
    HOMEPAGE_CARDS_QUERY = """query HomepageCards {
  homepageCards {
    id
    type
    rank
    title
    description
    url
    imageUrl
    videoUrl
  }
}"""

    GENERATE_QUERY = """mutation Generate($request: CreateGenerationRequest!) {
  generate(request: $request) {
    apiCreditCost
    generationId
    __typename
  }
}"""

    STATUS_QUERY = """query GetAIGenerationFeedStatuses(
  $where: generations_bool_exp = {},
  $noteWhere: generation_notes_bool_exp = {}
) {
  generations(where: $where) {
    id
    status
    nsfw
    flagged
    __typename
  }
  generationNotes: generation_notes(where: $noteWhere) {
    noteType
    failureReason
    __typename
  }
}"""

    GENERATION_RESULT_QUERY = """query GetGenerationFeed(
  $where: generations_bool_exp = {}, $limit: Int
) {
  generations(where: $where, limit: $limit) {
    id
    status
    motionDurationSeconds
    motionGenerationResolution
    generated_images(order_by: [{url: desc}]) {
      id
      url
      urls {
        asset
        thumbnail
      }
      motionMP4URL
      motionGIFURL
      image_width
      image_height
      __typename
    }
    __typename
  }
}"""

    BALANCE_QUERY = """query GetTokenBalance {
  user_details {
    subscriptionTokens
    paidTokens
    rolloverTokens
    auth0Email
    __typename
  }
}"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._media_host_circuit = MediaHostCircuitBreaker(
            settings.media_circuit_breaker_hosts,
            failure_threshold=settings.media_circuit_breaker_failure_threshold,
            window_seconds=settings.media_circuit_breaker_window_seconds,
            open_seconds=settings.media_circuit_breaker_open_seconds,
        )
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10, read=120, write=120, pool=10),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            headers={
                "accept": "*/*",
                "origin": "https://app.leonardo.ai",
                "referer": "https://app.leonardo.ai/",
                "x-leo-schema-version": settings.leonardo_schema_version,
                "user-agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150 Safari/537.36"
                ),
            },
        )

    async def _gql(
        self,
        token: str | None,
        operation_name: str,
        variables: dict[str, Any],
        query: str,
    ) -> dict[str, Any]:
        request_headers = {"content-type": "application/json"}
        if token:
            request_headers["authorization"] = f"Bearer {token}"
        try:
            response = await self._client.post(
                self._settings.leonardo_graphql_url,
                headers=request_headers,
                json={
                    "operationName": operation_name,
                    "variables": variables,
                    "query": query,
                },
            )
        except httpx.TimeoutException as exc:
            raise UpstreamError("UPSTREAM_TIMEOUT", "Leonardo request timed out") from exc
        except httpx.HTTPError as exc:
            raise UpstreamError("UPSTREAM_NETWORK_ERROR", "Leonardo network error") from exc

        if response.status_code in {401, 403}:
            raise UpstreamError(
                "UPSTREAM_UNAUTHORIZED", "Leonardo rejected the token", retryable=False
            )
        if response.status_code == 429:
            raise UpstreamError("UPSTREAM_RATE_LIMITED", "Leonardo rate limited the account")
        if response.status_code >= 500:
            raise UpstreamError("UPSTREAM_SERVER_ERROR", "Leonardo server error")
        if response.status_code >= 400:
            raise UpstreamError(
                "UPSTREAM_REQUEST_REJECTED",
                f"Leonardo returned HTTP {response.status_code}",
                retryable=False,
            )
        try:
            decoded = response.json()
        except json.JSONDecodeError as exc:
            raise UpstreamError("UPSTREAM_INVALID_JSON", "Leonardo returned invalid JSON") from exc
        if not isinstance(decoded, dict):
            raise UpstreamError("UPSTREAM_INVALID_JSON", "Leonardo returned a non-object JSON body")
        payload: dict[str, Any] = decoded
        errors = payload.get("errors") or []
        if errors:
            message = " | ".join(
                str(item.get("message", "GraphQL error"))[:300]
                for item in errors
                if isinstance(item, dict)
            )
            lowered = message.lower()
            unauthorized = "unauthorized" in lowered or "jwt" in lowered
            provider_unavailable = (
                'missing "data" field with no errors in response from remote'
                in lowered
            )
            insufficient_tokens = "insufficient tokens" in lowered
            if unauthorized:
                error_code = "UPSTREAM_UNAUTHORIZED"
            elif provider_unavailable:
                error_code = "UPSTREAM_PROVIDER_UNAVAILABLE"
            elif insufficient_tokens:
                error_code = "UPSTREAM_INSUFFICIENT_TOKENS"
            else:
                error_code = "UPSTREAM_GRAPHQL_ERROR"
            raise UpstreamError(
                error_code,
                message or "Leonardo GraphQL error",
                retryable=not unauthorized,
            )
        return payload

    async def list_homepage_cards(self) -> list[dict[str, Any]]:
        payload = await self._gql(
            None,
            "HomepageCards",
            {},
            self.HOMEPAGE_CARDS_QUERY,
        )
        cards = (payload.get("data") or {}).get("homepageCards")
        if not isinstance(cards, list) or any(not isinstance(card, dict) for card in cards):
            raise UpstreamError(
                "UPSTREAM_MODEL_CATALOG_INVALID",
                "Leonardo returned an invalid homepage card catalog",
            )
        return cast(list[dict[str, Any]], cards)

    async def _validate_download_url(self, raw_url: str) -> None:
        parsed = urlparse(raw_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise UpstreamError(
                "MEDIA_URL_INVALID",
                "media URL must use http or https",
                retryable=False,
            )
        if parsed.username or parsed.password:
            raise UpstreamError(
                "MEDIA_URL_INVALID",
                "media URL user information is not accepted",
                retryable=False,
            )
        host = parsed.hostname
        try:
            addresses = [ipaddress.ip_address(host)]
        except ValueError:
            try:
                records = await asyncio.to_thread(
                    socket.getaddrinfo,
                    host,
                    parsed.port or (443 if parsed.scheme == "https" else 80),
                    type=socket.SOCK_STREAM,
                )
            except socket.gaierror as exc:
                raise UpstreamError("MEDIA_DNS_FAILED", "media host lookup failed") from exc
            addresses = list({ipaddress.ip_address(item[4][0]) for item in records})
            fake_ip_network = ipaddress.ip_network(self._settings.media_fake_ip_network)
            if addresses and all(address in fake_ip_network for address in addresses):
                addresses = await self._resolve_public_dns_over_https(host)
        if not addresses or any(not address.is_global for address in addresses):
            raise UpstreamError(
                "MEDIA_URL_NOT_PUBLIC",
                "media URL must resolve to a public network address",
                retryable=False,
            )

    async def _resolve_public_dns_over_https(
        self, host: str
    ) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
        """Resolve Clash-style fake IP records through a fixed public DNS endpoint."""
        addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
        try:
            responses = await asyncio.gather(
                *(
                    self._client.get(
                        self._settings.media_dns_over_https_url,
                        params={"name": host, "type": record_type},
                        headers={"accept": "application/dns-json"},
                    )
                    for record_type in ("A", "AAAA")
                )
            )
            for response in responses:
                response.raise_for_status()
                payload = response.json()
                if payload.get("Status") != 0:
                    continue
                for answer in payload.get("Answer") or []:
                    if not isinstance(answer, dict) or answer.get("type") not in {1, 28}:
                        continue
                    try:
                        addresses.add(ipaddress.ip_address(str(answer.get("data"))))
                    except ValueError:
                        continue
        except (httpx.HTTPError, json.JSONDecodeError, TypeError) as exc:
            raise UpstreamError(
                "MEDIA_DNS_FAILED",
                "public media host verification failed",
            ) from exc
        if not addresses or any(not address.is_global for address in addresses):
            raise UpstreamError(
                "MEDIA_URL_NOT_PUBLIC",
                "media URL must resolve to a public network address",
                retryable=False,
            )
        return list(addresses)

    def _media_limit(self, kind: str) -> int:
        return {
            "IMAGE": self._settings.media_max_image_bytes,
            "AUDIO": self._settings.media_max_audio_bytes,
            "VIDEO": self._settings.media_max_video_bytes,
        }[kind]

    def _media_download_timeout(self, kind: str) -> httpx.Timeout:
        if kind == "IMAGE":
            return httpx.Timeout(
                connect=self._settings.media_image_connect_timeout_seconds,
                read=self._settings.media_image_read_timeout_seconds,
                write=self._settings.media_image_read_timeout_seconds,
                pool=10,
            )
        return httpx.Timeout(connect=10, read=120, write=120, pool=10)

    async def _probe_av(
        self,
        content: bytes,
        extension: str,
        expected_kind: str,
    ) -> tuple[
        int | None,
        int | None,
        int | None,
        float | None,
        int | None,
        int | None,
    ]:
        del extension

        def probe() -> tuple[
            int | None,
            int | None,
            int | None,
            float | None,
            int | None,
            int | None,
        ]:
            try:
                with av.open(io.BytesIO(content)) as container:
                    input_container = cast(av.container.InputContainer, container)
                    matching = (
                        list(input_container.streams.audio)
                        if expected_kind == "AUDIO"
                        else list(input_container.streams.video)
                    )
                    if not matching:
                        raise UpstreamError(
                            "MEDIA_TYPE_MISMATCH",
                            f"media does not contain an expected {expected_kind.lower()} stream",
                            retryable=False,
                        )
                    duration_ms = (
                        int(input_container.duration / 1000)
                        if input_container.duration is not None
                        else None
                    )
                    first = matching[0]
                    width = getattr(first.codec_context, "width", None)
                    height = getattr(first.codec_context, "height", None)
                    frame_rate: float | None = None
                    audio_channels: int | None = None
                    audio_sample_rate: int | None = None
                    if expected_kind == "VIDEO":
                        raw_rate = (
                            getattr(first, "average_rate", None)
                            or getattr(first, "base_rate", None)
                            or getattr(first, "guessed_rate", None)
                        )
                        frame_rate = float(raw_rate) if raw_rate is not None else None
                        audio_streams = list(input_container.streams.audio)
                        if audio_streams:
                            audio_context = audio_streams[0].codec_context
                            audio_channels = getattr(audio_context, "channels", None)
                            audio_sample_rate = getattr(audio_context, "sample_rate", None)
                    return (
                        duration_ms,
                        width,
                        height,
                        frame_rate,
                        audio_channels,
                        audio_sample_rate,
                    )
            except av.FFmpegError as exc:
                raise UpstreamError(
                    "MEDIA_PROBE_FAILED",
                    "media decoder rejected the audio or video reference",
                    retryable=False,
                ) from exc

        return await asyncio.to_thread(probe)

    async def _download_media(
        self,
        spec: MediaSpec,
        *,
        max_audio_duration_ms: int = 15_000,
        enforce_video_dimensions: bool = True,
        enforce_video_duration_bounds: bool = True,
    ) -> DownloadedMedia:
        current_url = spec.source_url
        limit = self._media_limit(spec.kind)
        response_headers: httpx.Headers | None = None
        content = bytearray()
        for redirect_no in range(4):
            parsed = urlparse(current_url)
            assert parsed.hostname is not None
            media_host = parsed.hostname.lower().rstrip(".")
            self._media_host_circuit.before_request(media_host)
            await self._validate_download_url(current_url)
            try:
                async with self._client.stream(
                    "GET",
                    current_url,
                    headers={"accept": "*/*"},
                    timeout=self._media_download_timeout(spec.kind),
                ) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        self._media_host_circuit.record_success(media_host)
                        location = response.headers.get("location")
                        if not location or redirect_no == 3:
                            raise UpstreamError(
                                "MEDIA_REDIRECT_INVALID",
                                "media URL redirect chain is invalid",
                                retryable=False,
                            )
                        current_url = urljoin(current_url, location)
                        continue
                    if response.status_code >= 500:
                        self._media_host_circuit.record_failure(media_host)
                        raise UpstreamError(
                            "MEDIA_DOWNLOAD_SERVER_ERROR",
                            f"media server returned HTTP {response.status_code}",
                        )
                    if response.status_code >= 400:
                        self._media_host_circuit.record_success(media_host)
                        raise UpstreamError(
                            "MEDIA_DOWNLOAD_REJECTED",
                            f"media server returned HTTP {response.status_code}",
                            retryable=False,
                        )
                    length_header = response.headers.get("content-length")
                    if length_header and int(length_header) > limit:
                        raise UpstreamError(
                            "MEDIA_TOO_LARGE",
                            f"media exceeds the {limit}-byte limit",
                            retryable=False,
                        )
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > limit:
                            raise UpstreamError(
                                "MEDIA_TOO_LARGE",
                                f"media exceeds the {limit}-byte limit",
                                retryable=False,
                            )
                    response_headers = response.headers
                    self._media_host_circuit.record_success(media_host)
                    break
            except httpx.TimeoutException as exc:
                self._media_host_circuit.record_failure(media_host)
                raise UpstreamError("MEDIA_DOWNLOAD_TIMEOUT", "media download timed out") from exc
            except httpx.HTTPError as exc:
                self._media_host_circuit.record_failure(media_host)
                raise UpstreamError("MEDIA_DOWNLOAD_FAILED", "media download failed") from exc
        if response_headers is None or not content:
            raise UpstreamError(
                "MEDIA_DOWNLOAD_EMPTY",
                "media download returned no content",
                retryable=False,
            )

        parsed = urlparse(current_url)
        file_name = Path(unquote(parsed.path)).name or f"reference-{spec.ordinal}"
        file_name = file_name[:240]
        content_type = response_headers.get("content-type", "").split(";", 1)[0].lower()
        extension = Path(file_name).suffix.lower().lstrip(".")
        if not extension:
            extension = (mimetypes.guess_extension(content_type) or "").lstrip(".")
        if extension == "jpeg":
            extension = "jpg"

        duration_ms: int | None = None
        width: int | None = None
        height: int | None = None
        frame_rate: float | None = None
        audio_channels: int | None = None
        audio_sample_rate: int | None = None
        if spec.kind == "IMAGE":
            try:
                with Image.open(io.BytesIO(content)) as source:
                    width, height = source.size
                    image_format = (source.format or "").lower()
                    source.verify()
            except (UnidentifiedImageError, OSError) as exc:
                raise UpstreamError(
                    "MEDIA_TYPE_MISMATCH",
                    "image URL did not return a valid image",
                    retryable=False,
                ) from exc
            extension = {"jpeg": "jpg", "png": "png", "webp": "webp"}.get(
                image_format,
                extension,
            )
            if extension not in {"jpg", "png", "webp"}:
                raise UpstreamError(
                    "MEDIA_IMAGE_FORMAT_UNSUPPORTED",
                    f"unsupported image format: {image_format or extension}",
                    retryable=False,
                )
            content_type = {
                "jpg": "image/jpeg",
                "png": "image/png",
                "webp": "image/webp",
            }[extension]
        else:
            if not extension:
                raise UpstreamError(
                    "MEDIA_EXTENSION_MISSING",
                    "audio and video URLs must expose a file extension",
                    retryable=False,
                )
            (
                duration_ms,
                width,
                height,
                frame_rate,
                audio_channels,
                audio_sample_rate,
            ) = await self._probe_av(bytes(content), extension, spec.kind)
            if not content_type or content_type == "application/octet-stream":
                content_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"

        downloaded = DownloadedMedia(
            content=bytes(content),
            content_type=content_type,
            file_name=file_name,
            extension=extension,
            duration_ms=duration_ms,
            width=width,
            height=height,
            frame_rate=frame_rate,
            audio_channels=audio_channels,
            audio_sample_rate=audio_sample_rate,
        )
        if spec.role == "REFERENCE_VIDEO":
            validate_reference_video_media(
                downloaded,
                enforce_dimensions=enforce_video_dimensions,
                enforce_duration_bounds=enforce_video_duration_bounds,
            )
        elif spec.role == "REFERENCE_AUDIO":
            validate_reference_audio_media(
                downloaded,
                max_duration_ms=max_audio_duration_ms,
            )
        return downloaded

    async def _post_presigned_upload(
        self,
        *,
        upload_url: str,
        raw_fields: str,
        media: DownloadedMedia,
    ) -> None:
        try:
            fields = json.loads(raw_fields)
        except json.JSONDecodeError as exc:
            raise UpstreamError(
                "MEDIA_UPLOAD_FIELDS_INVALID",
                "Leonardo returned invalid upload fields",
            ) from exc
        content_type = fields.get("Content-Type") or media.content_type
        try:
            response = await self._client.post(
                upload_url,
                data=fields,
                files={"file": (media.file_name, media.content, content_type)},
            )
        except httpx.TimeoutException as exc:
            raise UpstreamError("MEDIA_UPLOAD_TIMEOUT", "Leonardo media upload timed out") from exc
        except httpx.HTTPError as exc:
            raise UpstreamError("MEDIA_UPLOAD_FAILED", "Leonardo media upload failed") from exc
        if response.status_code not in {200, 201, 204}:
            raise UpstreamError(
                "MEDIA_UPLOAD_REJECTED",
                f"Leonardo media storage returned HTTP {response.status_code}",
            )

    async def _upload_image(self, token: str, media: DownloadedMedia) -> str:
        query = """mutation UploadImage($input: UploadImageInput!) {
  uploadImage(arg1: $input) { uploadId url fields __typename }
}"""
        payload = await self._gql(
            token,
            "UploadImage",
            {"input": {"uploadType": "INIT", "extension": media.extension}},
            query,
        )
        upload = (payload.get("data") or {}).get("uploadImage") or {}
        upload_id = str(upload.get("uploadId") or "")
        upload_url = str(upload.get("url") or "")
        raw_fields = str(upload.get("fields") or "")
        if not upload_id or not upload_url or not raw_fields:
            raise UpstreamError(
                "MEDIA_UPLOAD_SESSION_INVALID",
                "Leonardo returned an incomplete image upload session",
            )
        await self._post_presigned_upload(
            upload_url=upload_url,
            raw_fields=raw_fields,
            media=media,
        )
        moderation_query = """query GetInitImageModeration($uploadId: uuid!) {
  init_image_moderation(where: {akUUID: {_eq: $uploadId}}) {
    akUUID initImageId checkStatus __typename
  }
}"""
        for _ in range(self._settings.media_image_moderation_attempts):
            await asyncio.sleep(self._settings.media_image_moderation_interval_seconds)
            result = await self._gql(
                token,
                "GetInitImageModeration",
                {"uploadId": upload_id},
                moderation_query,
            )
            records = (result.get("data") or {}).get("init_image_moderation") or []
            if not records:
                continue
            status = str(records[0].get("checkStatus") or "")
            image_id = str(records[0].get("initImageId") or "")
            if status == "Accepted" and image_id:
                return image_id
            if status == "Rejected":
                raise UpstreamError(
                    "MEDIA_MODERATION_REJECTED",
                    "Leonardo rejected the uploaded image",
                    retryable=False,
                )
        raise UpstreamError("MEDIA_MODERATION_TIMEOUT", "Leonardo image moderation timed out")

    async def _upload_media(self, token: str, media: DownloadedMedia) -> str:
        query = """mutation UploadMedia($input: MediaUploadInput!) {
  uploadMedia(arg1: $input) { uploadId url fields __typename }
}"""
        payload = await self._gql(
            token,
            "UploadMedia",
            {
                "input": {
                    "extension": media.extension,
                    "originalFilename": media.file_name,
                }
            },
            query,
        )
        upload = (payload.get("data") or {}).get("uploadMedia") or {}
        upload_id = str(upload.get("uploadId") or "")
        upload_url = str(upload.get("url") or "")
        raw_fields = str(upload.get("fields") or "")
        if not upload_id or not upload_url or not raw_fields:
            raise UpstreamError(
                "MEDIA_UPLOAD_SESSION_INVALID",
                "Leonardo returned an incomplete media upload session",
            )
        await self._post_presigned_upload(
            upload_url=upload_url,
            raw_fields=raw_fields,
            media=media,
        )
        # The upload mutation returns before Leonardo's generation service can
        # resolve the media ID. Browser submissions remain disabled during this
        # processing window; mirror that handoff before issuing Generate.
        if self._settings.media_upload_settle_seconds:
            await asyncio.sleep(self._settings.media_upload_settle_seconds)
        return upload_id

    async def resolve_media(
        self,
        *,
        token: str,
        spec: MediaSpec,
        max_audio_duration_ms: int = 15_000,
        enforce_video_dimensions: bool = True,
        enforce_video_duration_bounds: bool = True,
    ) -> ResolvedMedia:
        media = await self._download_media(
            spec,
            max_audio_duration_ms=max_audio_duration_ms,
            enforce_video_dimensions=enforce_video_dimensions,
            enforce_video_duration_bounds=enforce_video_duration_bounds,
        )
        provider_asset_id = (
            await self._upload_image(token, media)
            if spec.kind == "IMAGE"
            else await self._upload_media(token, media)
        )
        return ResolvedMedia(
            kind=spec.kind,
            role=spec.role,
            ordinal=spec.ordinal,
            source_url=spec.source_url,
            provider_asset_id=provider_asset_id,
            content_type=media.content_type,
            content_length=len(media.content),
            duration_ms=media.duration_ms,
            width=media.width,
            height=media.height,
            extension=media.extension,
            frame_rate=media.frame_rate,
            audio_channels=media.audio_channels,
            audio_sample_rate=media.audio_sample_rate,
        )

    async def submit(
        self,
        *,
        token: str,
        model: str,
        task_input: dict[str, Any],
    ) -> SubmitResult:
        request = task_input.get("request")
        if not isinstance(request, dict):
            public = bool(task_input.get("public", True))
            parameters = dict(task_input)
            parameters.pop("public", None)
            request = {"model": model, "public": public, "parameters": parameters}
        payload = await self._gql(
            token,
            "Generate",
            {"request": request},
            self.GENERATE_QUERY,
        )
        generated = (payload.get("data") or {}).get("generate") or {}
        generation_id = generated.get("generationId")
        if not generation_id:
            raise UpstreamError(
                "UPSTREAM_MISSING_GENERATION_ID",
                "Leonardo response did not contain generationId",
            )
        cost = generated.get("apiCreditCost")
        return SubmitResult(
            generation_id=str(generation_id),
            api_credit_cost=int(cost) if isinstance(cost, (int, float)) else None,
            response={"apiCreditCost": cost},
        )

    async def poll(
        self,
        *,
        token: str,
        generation_id: str,
        submitted_at: datetime,
        estimated_credit_cost: int,
    ) -> PollResult:
        del submitted_at
        where = {"id": {"_eq": generation_id}}
        payload = await self._gql(
            token,
            "GetAIGenerationFeedStatuses",
            {
                "where": where,
                "noteWhere": {"generationId": {"_eq": generation_id}},
            },
            self.STATUS_QUERY,
        )
        data = payload.get("data") or {}
        generations = data.get("generations") or []
        if not generations:
            return PollResult(status="PENDING")
        generation = generations[0]
        upstream_status = str(generation.get("status") or "PENDING").upper()
        if upstream_status in {"FAILED", "ERROR"}:
            generation_notes = data.get("generationNotes") or []
            failure = resolve_leonardo_failure(
                generation_notes,
                nsfw=generation.get("nsfw") is True,
            )
            error: dict[str, Any] = {
                "code": failure.code,
                "message": failure.message,
                "upstream_status": upstream_status,
                "nsfw": generation.get("nsfw"),
                "flagged": generation.get("flagged"),
            }
            if failure.note_type is not None:
                error["note_type"] = failure.note_type
            if failure.failure_reason is not None:
                error["failure_reason"] = failure.failure_reason
            return PollResult(
                status="FAILED",
                output={
                    "provider": "leonardo",
                    "generation_id": generation_id,
                    "error": error,
                },
                error_code=failure.code,
                error_message=failure.message,
            )
        if upstream_status not in {"COMPLETE", "COMPLETED"}:
            return PollResult(status="RUNNING")

        result_payload = await self._gql(
            token,
            "GetGenerationFeed",
            {"where": where, "limit": 1},
            self.GENERATION_RESULT_QUERY,
        )
        result_generations = (result_payload.get("data") or {}).get("generations") or []
        media: list[dict[str, Any]] = []
        if result_generations:
            for item in result_generations[0].get("generated_images") or []:
                urls = item.get("urls") or {}
                motion_url = item.get("motionMP4URL")
                image_url = item.get("url") or urls.get("asset")
                if motion_url:
                    media.append(
                        {
                            "id": item.get("id"),
                            "type": "video/mp4",
                            "url": motion_url,
                            "gif_url": item.get("motionGIFURL"),
                            "thumbnail_url": image_url or urls.get("thumbnail"),
                            "width": item.get("image_width"),
                            "height": item.get("image_height"),
                        }
                    )
                    continue
                if image_url:
                    media.append(
                        {
                            "id": item.get("id"),
                            "type": (
                                mimetypes.guess_type(urlparse(image_url).path)[0]
                                or "image/*"
                            ),
                            "url": image_url,
                            "width": item.get("image_width"),
                            "height": item.get("image_height"),
                        }
                    )
        if not media:
            return PollResult(status="RUNNING")
        return PollResult(
            status="COMPLETE",
            actual_credit_cost=estimated_credit_cost,
            output={
                "provider": "leonardo",
                "generation_id": generation_id,
                "media": media,
            },
        )

    async def validate_account(self, *, token: str) -> AccountValidation:
        try:
            payload = await self._gql(token, "GetTokenBalance", {}, self.BALANCE_QUERY)
        except UpstreamError as exc:
            return AccountValidation(valid=False, error_code=exc.code)
        details = (payload.get("data") or {}).get("user_details") or []
        if not details:
            return AccountValidation(valid=False, error_code="UPSTREAM_NO_USER_DETAILS")
        first = details[0]
        balance = sum(
            int(first.get(key) or 0)
            for key in ("subscriptionTokens", "paidTokens", "rolloverTokens")
        )
        raw_login_name = first.get("auth0Email")
        login_name = (
            str(raw_login_name).strip().lower()
            if raw_login_name is not None and str(raw_login_name).strip()
            else None
        )
        return AccountValidation(
            valid=True,
            balance_credits=balance,
            login_name=login_name,
        )

    async def close(self) -> None:
        await self._client.aclose()


def create_upstream(settings: Settings | None = None) -> Upstream:
    resolved = settings or get_settings()
    if resolved.upstream_mode.lower() == "mock":
        return MockUpstream(resolved)
    if resolved.upstream_mode.lower() == "leonardo":
        return LeonardoUpstream(resolved)
    raise RuntimeError(f"unsupported upstream mode: {resolved.upstream_mode}")
