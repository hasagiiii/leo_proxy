from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from http.cookiejar import Cookie, CookieJar
from typing import Any
from urllib.parse import urlparse

import httpx
from curl_cffi import requests as curl_requests
from curl_cffi.const import CurlECode
from curl_cffi.requests.errors import RequestsError as CurlRequestsError
from sqlalchemy.ext.asyncio import AsyncSession

from video_task_service.config import Settings
from video_task_service.crypto import decrypt_secret, encrypt_secret
from video_task_service.models import Account, AccountRenewalSession
from video_task_service.schemas import RenewalSessionPayload

GET_SESSION_PATH = "/api/auth/get-session"
CROSS_ORIGIN_COOKIE_PATH = "/api/auth/cross-origin-cookie"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)
DEFAULT_SEC_CH_UA = '"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"'
MAX_SESSION_PLAINTEXT_BYTES = 65_000
logger = logging.getLogger(__name__)


class RenewalSessionStorageError(ValueError):
    """A persistence-boundary error whose text never contains session material."""


class ProtocolRenewalError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = True,
        status: int = 0,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.status = status
        self.retry_after_seconds = retry_after_seconds


class ProtocolRequestGate:
    """Process-wide pacing and short circuit breaking for auth requests."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._next_request_at = 0.0
        self._blocked_until = 0.0

    async def acquire(self, min_interval_seconds: float) -> None:
        loop = asyncio.get_running_loop()
        async with self._lock:
            now = loop.time()
            if self._blocked_until > now:
                raise ProtocolRenewalError(
                    "PROTOCOL_RATE_LIMITED",
                    "protocol renewal circuit is cooling down",
                    status=429,
                    retry_after_seconds=max(1, math.ceil(self._blocked_until - now)),
                )
            delay = self._next_request_at - now
            if delay > 0:
                await asyncio.sleep(delay)
                now = loop.time()
                if self._blocked_until > now:
                    raise ProtocolRenewalError(
                        "PROTOCOL_RATE_LIMITED",
                        "protocol renewal circuit is cooling down",
                        status=429,
                        retry_after_seconds=max(
                            1, math.ceil(self._blocked_until - now)
                        ),
                    )
            self._next_request_at = now + min_interval_seconds

    def block(self, seconds: int) -> None:
        loop = asyncio.get_running_loop()
        self._blocked_until = max(self._blocked_until, loop.time() + max(seconds, 1))


_request_gate: ProtocolRequestGate | None = None
_request_gate_loop: asyncio.AbstractEventLoop | None = None


def protocol_request_gate() -> ProtocolRequestGate:
    global _request_gate, _request_gate_loop  # noqa: PLW0603
    loop = asyncio.get_running_loop()
    if _request_gate is None or _request_gate_loop is not loop:
        _request_gate = ProtocolRequestGate()
        _request_gate_loop = loop
    return _request_gate


@dataclass(slots=True)
class ProtocolRenewalResult:
    token: str
    token_expires_at: datetime
    renewal_session: dict[str, Any]
    token_changed: bool
    needs_refresh: bool
    get_session_status: int
    cross_origin_cookie_status: int
    session_email: str = ""


def renewal_session_dict(payload: RenewalSessionPayload) -> dict[str, Any]:
    return {
        "cookies": [
            {
                "name": cookie.name,
                "value": cookie.value.get_secret_value(),
                "domain": cookie.domain,
                "path": cookie.path,
                "expiration_date": cookie.expiration_date,
                "secure": cookie.secure,
                "http_only": cookie.http_only,
                "same_site": cookie.same_site,
            }
            for cookie in payload.cookies
        ],
        "user_agent": payload.user_agent,
        "accept_language": payload.accept_language,
        "client_version": payload.client_version,
        "capability": payload.capability,
    }


def encode_renewal_session(payload: RenewalSessionPayload | dict[str, Any]) -> str:
    value = renewal_session_dict(payload) if isinstance(payload, RenewalSessionPayload) else payload
    encoded = json.dumps(value, separators=(",", ":"), sort_keys=True)
    if len(encoded.encode("utf-8")) > MAX_SESSION_PLAINTEXT_BYTES:
        raise RenewalSessionStorageError("renewal session exceeds the encrypted storage limit")
    return encoded


def decode_renewal_session(row: AccountRenewalSession, account_uuid: str) -> dict[str, Any]:
    decoded = decrypt_secret(
        bytes(row.session_ciphertext),
        f"{account_uuid}:renewal_session",
    )
    value = json.loads(decoded)
    if not isinstance(value, dict) or not isinstance(value.get("cookies"), list):
        raise ValueError("renewal session has an invalid shape")
    return value


def reset_renewal_state(row: AccountRenewalSession) -> None:
    row.status = "IDLE"
    row.attempt_count = 0
    row.claimed_account_version = None
    row.lease_owner = None
    row.lease_until = None
    row.fallback_after = None
    row.retry_after = None
    row.last_error_code = None
    row.previous_token_expires_at = None
    row.renewed_token_expires_at = None


async def store_renewal_session(
    session: AsyncSession,
    account: Account,
    payload: RenewalSessionPayload,
) -> AccountRenewalSession:
    encoded = encode_renewal_session(payload)
    row = await session.get(AccountRenewalSession, account.id, with_for_update=True)
    ciphertext = encrypt_secret(encoded, f"{account.account_uuid}:renewal_session")
    if row is None:
        row = AccountRenewalSession(
            account_id=account.id,
            session_ciphertext=ciphertext,
        )
        session.add(row)
    else:
        row.session_ciphertext = ciphertext
    reset_renewal_state(row)
    now = datetime.now(UTC).replace(tzinfo=None)
    row.session_refreshed_at = now
    row.client_reported_at = now
    row.client_version = payload.client_version
    row.renewal_capability = payload.capability
    return row


async def delete_stored_renewal_session(
    session: AsyncSession,
    account_id: int,
) -> None:
    row = await session.get(AccountRenewalSession, account_id, with_for_update=True)
    if row is not None:
        await session.delete(row)


async def reset_stored_renewal_state(session: AsyncSession, account_id: int) -> None:
    row = await session.get(AccountRenewalSession, account_id, with_for_update=True)
    if row is not None:
        reset_renewal_state(row)


def decode_jwt_payload(token: str) -> dict[str, Any] | None:
    parts = str(token or "").strip().split(".")
    if len(parts) != 3:
        return None
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, json.JSONDecodeError):
        return None
    return decoded if isinstance(decoded, dict) else None


def normalize_token(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    normalized = value.strip()
    if normalized.lower().startswith("bearer "):
        normalized = normalized[7:].strip()
    return normalized if decode_jwt_payload(normalized) is not None else ""


def find_token_in_object(value: Any) -> str:
    candidates: list[tuple[int, str]] = []

    def visit(node: Any, key: str = "") -> None:
        if isinstance(node, str):
            token = normalize_token(node)
            if token:
                priority = {
                    "accesstoken": 4,
                    "access_token": 4,
                    "idtoken": 3,
                    "id_token": 3,
                    "token": 2,
                }.get(key.lower(), 1)
                candidates.append((priority, token))
            return
        if isinstance(node, list):
            for item in node:
                visit(item, key)
            return
        if not isinstance(node, dict):
            return
        for child_key, child in node.items():
            if "cf_access_token" in str(child_key).lower():
                continue
            visit(child, str(child_key))

    visit(value)
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1] if candidates else ""


def session_email(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    user = value.get("user")
    if not isinstance(user, dict) and isinstance(value.get("session"), dict):
        user = value["session"].get("user")
    if not isinstance(user, dict):
        user = value.get("account")
    if not isinstance(user, dict):
        return ""
    email = user.get("email") or user.get("auth0Email") or user.get("username") or ""
    return str(email).strip().lower()


def session_needs_refresh(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if value.get("needsRefresh") is True:
        return True
    session = value.get("session")
    return isinstance(session, dict) and session.get("needsRefresh") is True


def _cookie_expiry(cookie: Cookie) -> int:
    try:
        return int(cookie.expires or 0)
    except (TypeError, ValueError):
        return 0


def _cookie_payload(cookie: Cookie) -> dict[str, Any]:
    same_site = str(cookie._rest.get("SameSite") or "unspecified").lower()  # noqa: SLF001
    if same_site == "none":
        same_site = "no_restriction"
    if same_site not in {"strict", "lax", "no_restriction", "unspecified"}:
        same_site = "unspecified"
    return {
        "name": cookie.name,
        "value": cookie.value,
        "domain": cookie.domain,
        "path": cookie.path or "/",
        "expiration_date": float(cookie.expires) if cookie.expires else None,
        "secure": bool(cookie.secure),
        "http_only": "HttpOnly" in cookie._rest,  # noqa: SLF001
        "same_site": same_site,
    }


def _load_cookie_jar(material: dict[str, Any]) -> CookieJar:
    jar = CookieJar()
    for item in material.get("cookies") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        value = str(item.get("value") or "")
        domain = str(item.get("domain") or "")
        path = str(item.get("path") or "/")
        if name and domain:
            raw_expiry = item.get("expiration_date")
            try:
                expires = int(float(raw_expiry)) if raw_expiry else None
            except (TypeError, ValueError):
                expires = None
            same_site = str(item.get("same_site") or "unspecified").lower()
            if same_site == "no_restriction":
                same_site = "None"
            elif same_site != "unspecified":
                same_site = same_site.capitalize()
            rest: dict[str, Any] = {}
            if bool(item.get("http_only")):
                rest["HttpOnly"] = None
            if same_site != "unspecified":
                rest["SameSite"] = same_site
            jar.set_cookie(
                Cookie(
                    version=0,
                    name=name,
                    value=value,
                    port=None,
                    port_specified=False,
                    domain=domain,
                    domain_specified=True,
                    domain_initial_dot=domain.startswith("."),
                    path=path,
                    path_specified=True,
                    secure=bool(item.get("secure")),
                    expires=expires,
                    discard=expires is None,
                    comment=None,
                    comment_url=None,
                    rest=rest,
                    rfc2109=False,
                )
            )
    return jar


def _cookie_jar(cookies: Any) -> CookieJar:
    jar = getattr(cookies, "jar", cookies)
    if not isinstance(jar, CookieJar):
        raise TypeError("protocol client returned an unsupported cookie jar")
    return jar


def _cookie_fingerprint(cookies: Any) -> tuple[tuple[str, str, str, str], ...]:
    return tuple(
        sorted(
            (cookie.name, cookie.value, cookie.domain, cookie.path or "/")
            for cookie in _cookie_jar(cookies)
        )
    )


def _deduplicate_rotated_cookies(
    cookies: Any,
    response_cookies: Any,
    response_host: str,
) -> None:
    """Drop an obsolete domain-cookie copy after a host-only Set-Cookie rotation.

    Some curl-cffi versions preserve an imported ``.host`` cookie alongside the
    newer host-only cookie returned by the session endpoint. Browsers prefer the
    host-only value for that host; persisting both can make a later consumer pick
    the stale value. Cookie path and canonicalized domain remain part of the key.
    """
    jar = _cookie_jar(cookies)
    updates = {
        (
            cookie.name,
            (cookie.domain or response_host).lstrip(".").lower(),
            cookie.path or "/",
        ): cookie.value
        for cookie in _cookie_jar(response_cookies)
    }
    winners: dict[tuple[str, str, str], Cookie] = {}
    losers: list[Cookie] = []
    for cookie in list(jar):
        key = (cookie.name, cookie.domain.lstrip(".").lower(), cookie.path or "/")
        existing = winners.get(key)
        if existing is None:
            winners[key] = cookie
            continue
        rotated_value = updates.get(key)
        cookie_is_rotated = rotated_value is not None and cookie.value == rotated_value
        existing_is_rotated = rotated_value is not None and existing.value == rotated_value
        if cookie_is_rotated and not existing_is_rotated:
            winners[key] = cookie
            losers.append(existing)
        elif not existing_is_rotated and existing.domain_specified and not cookie.domain_specified:
            winners[key] = cookie
            losers.append(existing)
        else:
            losers.append(cookie)
    for cookie in losers:
        try:
            jar.clear(cookie.domain, cookie.path or "/", cookie.name)
        except KeyError:
            continue


def _persisted_session(cookies: Any, headers: dict[str, str]) -> dict[str, Any]:
    persisted = {
        "cookies": [_cookie_payload(cookie) for cookie in _cookie_jar(cookies)],
        "user_agent": headers["user-agent"],
        "accept_language": headers["accept-language"],
    }
    encode_renewal_session(persisted)
    return persisted


def _has_live_session_cookie(cookies: Any) -> bool:
    live = {
        cookie.name.lower()
        for cookie in _cookie_jar(cookies)
        if str(cookie.value or "").strip()
    }
    has_token = "__secure-better-auth.session_token" in live
    has_data = any(name.startswith("__secure-better-auth.session_data") for name in live)
    return has_token and has_data


def _response_revokes_session(response: Any) -> bool:
    get_list = getattr(response.headers, "get_list", None)
    if callable(get_list):
        values = [str(value) for value in get_list("set-cookie")]
    else:
        value = str(response.headers.get("set-cookie") or "")
        values = [value] if value else []
    for value in values:
        normalized = value.lower()
        if "__secure-better-auth.session_token=" not in normalized:
            continue
        match = re.search(
            r"__secure-better-auth\.session_token=([^;,\s]*)",
            normalized,
        )
        cleared = match is not None and not match.group(1)
        expired = re.search(r"max-age\s*=\s*0(?:\D|$)", normalized) is not None
        if cleared or expired:
            return True
    return False


def _retry_after_seconds(value: str, current_time: datetime) -> int | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    try:
        return max(1, math.ceil(float(normalized)))
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(normalized)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return max(1, math.ceil((parsed - current_time).total_seconds()))


def _response_error(
    response: Any,
    operation: str,
    current_time: datetime,
) -> ProtocolRenewalError:
    status = int(response.status_code)
    if status in {401, 403}:
        return ProtocolRenewalError(
            "PROTOCOL_SESSION_UNAUTHORIZED",
            f"{operation} returned HTTP {status}",
            retryable=False,
            status=status,
        )
    if status == 429:
        retry_after = _retry_after_seconds(
            str(response.headers.get("retry-after") or ""), current_time
        )
        return ProtocolRenewalError(
            "PROTOCOL_RATE_LIMITED",
            f"{operation} returned HTTP 429",
            status=status,
            retry_after_seconds=retry_after,
        )
    if status >= 500:
        return ProtocolRenewalError(
            "PROTOCOL_UPSTREAM_ERROR",
            f"{operation} returned HTTP {status}",
            status=status,
        )
    return ProtocolRenewalError(
        "PROTOCOL_REQUEST_REJECTED",
        f"{operation} returned HTTP {status}",
        retryable=False,
        status=status,
    )


def _protocol_headers(
    material: dict[str, Any], *, origin: str, impersonated: bool
) -> dict[str, str]:
    user_agent = (
        DEFAULT_USER_AGENT
        if impersonated
        else str(material.get("user_agent") or DEFAULT_USER_AGENT)
    )
    headers = {
        "accept": "*/*",
        "accept-language": str(material.get("accept_language") or "en-US,en;q=0.9"),
        "user-agent": user_agent,
        "origin": origin,
        "referer": f"{origin}/",
    }
    if impersonated:
        headers.update(
            {
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
                "sec-ch-ua": DEFAULT_SEC_CH_UA,
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
            }
        )
    return headers


async def renew_protocol_session(
    *,
    material: dict[str, Any],
    stored_token: str,
    expected_email: str,
    settings: Settings,
    now: Callable[[], datetime] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    proxy_url: str | None = None,
    request_gate: ProtocolRequestGate | None = None,
) -> ProtocolRenewalResult:
    current_time = (now or (lambda: datetime.now(UTC)))()
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=UTC)
    origin = settings.protocol_renewal_origin.rstrip("/")
    impersonated = transport is None
    headers = _protocol_headers(material, origin=origin, impersonated=impersonated)
    jar = _load_cookie_jar(material)
    gate = (request_gate or protocol_request_gate()) if impersonated else None
    if transport is not None:
        client: Any = httpx.AsyncClient(
            cookies=httpx.Cookies(jar),
            headers=headers,
            timeout=httpx.Timeout(settings.protocol_renewal_timeout_seconds),
            follow_redirects=True,
            transport=transport,
        )
    else:
        client = curl_requests.AsyncSession(
            cookies=curl_requests.Cookies(jar),
            headers=headers,
            timeout=settings.protocol_renewal_timeout_seconds,
            allow_redirects=True,
            impersonate=settings.protocol_renewal_browser_profile,
            default_headers=True,
            max_clients=1,
            proxy=proxy_url,
        )

    async def request(path: str, operation: str) -> Any:
        if gate is not None:
            await gate.acquire(settings.protocol_renewal_min_interval_seconds)
        response = await client.get(f"{origin}{path}")
        if impersonated:
            _deduplicate_rotated_cookies(
                client.cookies,
                response.cookies,
                str(urlparse(str(response.url)).hostname or ""),
            )
        if 200 <= int(response.status_code) < 300:
            return response
        error = _response_error(response, operation, current_time)
        if gate is not None and error.code == "PROTOCOL_RATE_LIMITED":
            cooldown = (
                error.retry_after_seconds
                or settings.protocol_renewal_rate_limit_cooldown_seconds
            )
            error.retry_after_seconds = cooldown
            gate.block(cooldown)
        raise error

    try:
        async with client:
            token = ""
            body: Any = {}
            exp = 0
            get_session_status = 0
            session_revoked = False
            for attempt in range(2):
                before = _cookie_fingerprint(client.cookies)
                response = await request(GET_SESSION_PATH, "get-session")
                get_session_status = int(response.status_code)
                session_revoked = session_revoked or _response_revokes_session(response)
                try:
                    body = response.json()
                except (json.JSONDecodeError, ValueError) as exc:
                    raise ProtocolRenewalError(
                        "PROTOCOL_INVALID_JSON", "get-session returned invalid JSON"
                    ) from exc
                token = find_token_in_object(body)
                payload = decode_jwt_payload(token)
                exp = int(payload.get("exp") or 0) if payload else 0
                if token and exp > int(current_time.timestamp()):
                    break
                rotated = _cookie_fingerprint(client.cookies) != before
                if attempt == 0 and rotated:
                    continue
                if session_revoked or not _has_live_session_cookie(client.cookies):
                    raise ProtocolRenewalError(
                        "PROTOCOL_SESSION_REVOKED",
                        "get-session removed the stored session cookies",
                        retryable=False,
                    )
                raise ProtocolRenewalError(
                    "PROTOCOL_TOKEN_UNAVAILABLE",
                    "get-session did not return a live access token",
                )

            payload = decode_jwt_payload(token)
            exp = int(payload.get("exp") or 0) if payload else 0
            actual_email = session_email(body)
            if expected_email and not actual_email:
                raise ProtocolRenewalError(
                    "PROTOCOL_IDENTITY_UNAVAILABLE",
                    "get-session did not return an account identity",
                    retryable=False,
                )
            if expected_email and expected_email.strip().lower() != actual_email:
                raise ProtocolRenewalError(
                    "PROTOCOL_WRONG_ACCOUNT",
                    "get-session returned another account",
                    retryable=False,
                )

            needs_refresh = session_needs_refresh(body)
            token_changed = token != stored_token
            cookie_jar = _cookie_jar(client.cookies)
            cf_cookie = next(
                (cookie for cookie in cookie_jar if cookie.name == "CF_Access_Token"),
                None,
            )
            cf_exp = _cookie_expiry(cf_cookie) if cf_cookie is not None else 0
            cf_near_expiry = cf_exp > 0 and cf_exp <= int(current_time.timestamp()) + 30 * 60
            should_sync_cookie = (
                needs_refresh
                or token_changed
                or cf_cookie is None
                or cf_near_expiry
                or (exp > 0 and cf_exp < exp)
            )
            cross_status = 0
            if settings.protocol_renewal_cross_origin_cookie_enabled and should_sync_cookie:
                try:
                    cross_response = await request(
                        CROSS_ORIGIN_COOKIE_PATH, "cross-origin-cookie"
                    )
                    cross_status = int(cross_response.status_code)
                except ProtocolRenewalError as exc:
                    cross_status = exc.status
                    logger.warning(
                        "protocol cross-origin cookie sync failed",
                        extra={
                            "error_code": exc.code,
                            "status": exc.status,
                            "retry_after_seconds": exc.retry_after_seconds,
                        },
                    )

            persisted = _persisted_session(client.cookies, headers)
            return ProtocolRenewalResult(
                token=token,
                token_expires_at=datetime.fromtimestamp(exp, UTC).replace(tzinfo=None),
                renewal_session=persisted,
                session_email=actual_email,
                token_changed=token_changed,
                needs_refresh=needs_refresh,
                get_session_status=get_session_status,
                cross_origin_cookie_status=cross_status,
            )
    except ProtocolRenewalError:
        raise
    except httpx.TimeoutException as exc:
        raise ProtocolRenewalError(
            "PROTOCOL_TIMEOUT", "protocol renewal timed out"
        ) from exc
    except httpx.HTTPError as exc:
        raise ProtocolRenewalError(
            "PROTOCOL_NETWORK_ERROR", "protocol renewal network request failed"
        ) from exc
    except CurlRequestsError as exc:
        code = getattr(exc, "code", 0)
        error_code = (
            "PROTOCOL_TIMEOUT"
            if code == CurlECode.OPERATION_TIMEDOUT
            else "PROTOCOL_NETWORK_ERROR"
        )
        message = (
            "protocol renewal timed out"
            if error_code == "PROTOCOL_TIMEOUT"
            else "protocol renewal network request failed"
        )
        raise ProtocolRenewalError(error_code, message) from exc
