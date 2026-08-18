from __future__ import annotations

import asyncio
import base64
import inspect
import json
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import httpx
import pytest

from video_task_service.config import Settings
from video_task_service.protocol_renewal import (
    DEFAULT_USER_AGENT,
    ProtocolRenewalError,
    ProtocolRequestGate,
    RenewalSessionStorageError,
    _protocol_headers,
    _response_error,
    decode_jwt_payload,
    encode_renewal_session,
    find_token_in_object,
    renew_protocol_session,
)
from video_task_service.schemas import RenewalSessionPayload
from video_task_service.syncer import protocol_retry_delay_seconds


def jwt(payload: dict[str, object]) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).decode().rstrip("=")
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"{header}.{body}.signature"


def material(now: datetime) -> dict[str, object]:
    return {
        "cookies": [
            {
                "name": "__Secure-better-auth.session_token",
                "value": "session-token",
                "domain": "app.leonardo.ai",
                "path": "/",
                "expiration_date": (now + timedelta(hours=2)).timestamp(),
                "secure": True,
                "http_only": True,
                "same_site": "lax",
            },
            {
                "name": "__Secure-better-auth.session_data.0",
                "value": "session-cookie",
                "domain": "app.leonardo.ai",
                "path": "/",
                "expiration_date": (now + timedelta(hours=2)).timestamp(),
                "secure": True,
                "http_only": True,
                "same_site": "lax",
            }
        ],
        "user_agent": "fixture-agent",
        "accept_language": "en-US",
    }


def test_finds_access_token_before_other_jwts() -> None:
    access = jwt({"exp": 1_900_000_000, "kind": "access"})
    other = jwt({"exp": 1_900_000_000, "kind": "other"})

    found = find_token_in_object({"token": other, "session": {"accessToken": access}})

    assert found == access
    assert decode_jwt_payload(found)["kind"] == "access"  # type: ignore[index]


def test_protocol_get_session_rotates_token_and_persists_updated_cookies() -> None:
    now = datetime(2030, 1, 1, tzinfo=UTC)
    old_token = jwt({"exp": int((now + timedelta(minutes=5)).timestamp())})
    new_token = jwt({"exp": int((now + timedelta(hours=1)).timestamp())})
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        assert request.headers["user-agent"] == "fixture-agent"
        assert "session_data.0=session-cookie" in request.headers.get("cookie", "")
        if request.url.path.endswith("/get-session"):
            return httpx.Response(
                200,
                json={
                    "session": {"accessToken": new_token},
                    "user": {"email": "worker@example.test"},
                },
                headers={
                    "set-cookie": (
                        "__Secure-better-auth.session_data.0=rotated-session; "
                        "Domain=app.leonardo.ai; Path=/; Secure; HttpOnly"
                    )
                },
            )
        return httpx.Response(
            204,
            headers={
                "set-cookie": (
                    f"CF_Access_Token={new_token}; Domain=app.leonardo.ai; "
                    "Path=/; Secure"
                )
            },
        )

    result = asyncio.run(
        renew_protocol_session(
            material=material(now),
            stored_token=old_token,
            expected_email="worker@example.test",
            settings=Settings(
                protocol_renewal_timeout_seconds=5,
                protocol_renewal_cross_origin_cookie_enabled=True,
            ),
            now=lambda: now,
            transport=httpx.MockTransport(handler),
        )
    )

    assert result.token == new_token
    assert result.session_email == "worker@example.test"
    assert result.token_changed
    assert result.token_expires_at == (now + timedelta(hours=1)).replace(tzinfo=None)
    assert calls == ["/api/auth/get-session", "/api/auth/cross-origin-cookie"]
    persisted = {cookie["name"]: cookie["value"] for cookie in result.renewal_session["cookies"]}
    assert persisted["__Secure-better-auth.session_data.0"] == "rotated-session"
    assert persisted["CF_Access_Token"] == new_token
    assert "rotated-session" in encode_renewal_session(result.renewal_session)


def test_protocol_retries_once_after_better_auth_rotates_cookie_shards() -> None:
    now = datetime(2030, 1, 1, tzinfo=UTC)
    old_token = jwt({"exp": int((now + timedelta(minutes=5)).timestamp())})
    new_token = jwt({"exp": int((now + timedelta(hours=1)).timestamp())})
    cookies_seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        cookies_seen.append(request.headers.get("cookie", ""))
        if len(cookies_seen) == 1:
            return httpx.Response(
                200,
                json={},
                headers={
                    "set-cookie": (
                        "__Secure-better-auth.session_data.0=phase-two; "
                        "Domain=app.leonardo.ai; Path=/; Secure; HttpOnly"
                    )
                },
            )
        return httpx.Response(
            200,
            json={
                "session": {"accessToken": new_token},
                "user": {"email": "worker@example.test"},
            },
        )

    result = asyncio.run(
        renew_protocol_session(
            material=material(now),
            stored_token=old_token,
            expected_email="worker@example.test",
            settings=Settings(protocol_renewal_timeout_seconds=5),
            now=lambda: now,
            transport=httpx.MockTransport(handler),
        )
    )

    assert result.token == new_token
    assert len(cookies_seen) == 2
    assert "session_data.0=phase-two" in cookies_seen[1]


def test_protocol_classifies_deleted_better_auth_session_as_revoked() -> None:
    now = datetime(2030, 1, 1, tzinfo=UTC)
    old_token = jwt({"exp": int((now + timedelta(minutes=5)).timestamp())})
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        headers: list[tuple[str, str]] = [("content-type", "application/json")]
        if calls == 1:
            headers.extend(
                [
                    (
                        "set-cookie",
                        "__Secure-better-auth.session_token=; Domain=app.leonardo.ai; "
                        "Path=/; Max-Age=0; Secure; HttpOnly",
                    ),
                    (
                        "set-cookie",
                        "__Secure-better-auth.session_data.0=; Domain=app.leonardo.ai; "
                        "Path=/; Max-Age=0; Secure; HttpOnly",
                    ),
                ]
            )
        return httpx.Response(200, content=b"null", headers=headers)

    with pytest.raises(ProtocolRenewalError) as caught:
        asyncio.run(
            renew_protocol_session(
                material=material(now),
                stored_token=old_token,
                expected_email="worker@example.test",
                settings=Settings(protocol_renewal_timeout_seconds=5),
                now=lambda: now,
                transport=httpx.MockTransport(handler),
            )
        )

    assert calls in {1, 2}
    assert caught.value.code == "PROTOCOL_SESSION_REVOKED"
    assert caught.value.retryable is False


def test_rate_limit_honors_retry_after_and_extends_retry_delay() -> None:
    now = datetime(2030, 1, 1, tzinfo=UTC)
    response = httpx.Response(429, headers={"retry-after": "420"})

    error = _response_error(response, "get-session", now)
    delay = protocol_retry_delay_seconds(
        account_id=7,
        attempt_count=1,
        error=error,
        settings=Settings(
            protocol_renewal_retry_base_seconds=300,
            protocol_renewal_retry_jitter_seconds=30,
        ),
    )

    assert error.code == "PROTOCOL_RATE_LIMITED"
    assert error.retry_after_seconds == 420
    assert delay == 427


def test_protocol_request_gate_fails_fast_while_circuit_is_open() -> None:
    async def run() -> ProtocolRenewalError:
        gate = ProtocolRequestGate()
        gate.block(30)
        with pytest.raises(ProtocolRenewalError) as caught:
            await gate.acquire(2)
        return caught.value

    error = asyncio.run(run())

    assert error.code == "PROTOCOL_RATE_LIMITED"
    assert error.status == 429
    assert 1 <= (error.retry_after_seconds or 0) <= 30


def test_impersonated_headers_use_browser_profile_consistent_values() -> None:
    headers = _protocol_headers(
        {"user_agent": "desktop-agent", "accept_language": "zh-CN"},
        origin="https://app.leonardo.ai",
        impersonated=True,
    )

    assert headers["user-agent"] == DEFAULT_USER_AGENT
    assert headers["sec-fetch-site"] == "same-origin"
    assert '"136"' in headers["sec-ch-ua"]


def test_curl_impersonation_path_rotates_cookie_and_returns_token() -> None:
    now = datetime(2030, 1, 1, tzinfo=UTC)
    old_token = jwt({"exp": int((now + timedelta(minutes=5)).timestamp())})
    new_token = jwt({"exp": int((now + timedelta(hours=1)).timestamp())})

    class Handler(BaseHTTPRequestHandler):
        calls = 0

        def do_GET(self) -> None:  # noqa: N802
            type(self).calls += 1
            if type(self).calls == 1:
                payload = b"{}"
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("set-cookie", "session_data=phase-two; Path=/")
            else:
                payload = json.dumps(
                    {
                        "session": {"accessToken": new_token},
                        "user": {"email": "worker@example.test"},
                    }
                ).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = asyncio.run(
            renew_protocol_session(
                material={
                    "cookies": [
                        {
                            "name": "session_data",
                            "value": "phase-one",
                            "domain": "127.0.0.1",
                            "path": "/",
                        }
                    ],
                    "accept_language": "en-US",
                },
                stored_token=old_token,
                expected_email="worker@example.test",
                settings=Settings(
                    protocol_renewal_origin=f"http://127.0.0.1:{server.server_port}",
                    protocol_renewal_browser_profile="chrome136",
                    protocol_renewal_min_interval_seconds=0,
                ),
                now=lambda: now,
            )
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert Handler.calls == 2
    assert result.token == new_token
    persisted = {cookie["name"]: cookie["value"] for cookie in result.renewal_session["cookies"]}
    assert persisted["session_data"] == "phase-two"


def test_protocol_identity_mismatch_is_nonretryable() -> None:
    now = datetime(2030, 1, 1, tzinfo=UTC)
    token = jwt({"exp": int((now + timedelta(hours=1)).timestamp())})

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "session": {"accessToken": token},
                "user": {"email": "another@example.test"},
            },
        )

    with pytest.raises(ProtocolRenewalError) as caught:
        asyncio.run(
            renew_protocol_session(
                material=material(now),
                stored_token="old-token",
                expected_email="worker@example.test",
                settings=Settings(protocol_renewal_timeout_seconds=5),
                now=lambda: now,
                transport=httpx.MockTransport(handler),
            )
        )

    assert caught.value.code == "PROTOCOL_WRONG_ACCOUNT"
    assert caught.value.retryable is False


def test_protocol_expected_identity_requires_session_email() -> None:
    now = datetime(2030, 1, 1, tzinfo=UTC)
    token = jwt({"exp": int((now + timedelta(hours=1)).timestamp())})

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"session": {"accessToken": token}, "user": {}})

    with pytest.raises(ProtocolRenewalError) as caught:
        asyncio.run(
            renew_protocol_session(
                material=material(now),
                stored_token="",
                expected_email="worker@example.test",
                settings=Settings(protocol_renewal_timeout_seconds=5),
                now=lambda: now,
                transport=httpx.MockTransport(handler),
            )
        )

    assert caught.value.code == "PROTOCOL_IDENTITY_UNAVAILABLE"
    assert caught.value.retryable is False


def test_protocol_renewal_exposes_worker_proxy_and_gate() -> None:
    parameters = inspect.signature(renew_protocol_session).parameters
    assert "proxy_url" in parameters
    assert "request_gate" in parameters
    source = inspect.getsource(renew_protocol_session)
    assert "proxy=proxy_url" in source
    assert "request_gate or protocol_request_gate()" in source


def test_oversized_session_error_does_not_echo_cookie_secret() -> None:
    secret = "cookie-secret-" + "x" * 16_000
    payload = RenewalSessionPayload(
        cookies=[
            {
                "name": f"session-{index}",
                "value": secret,
                "domain": "app.leonardo.ai",
            }
            for index in range(5)
        ]
    )

    with pytest.raises(RenewalSessionStorageError) as caught:
        encode_renewal_session(payload)

    assert "cookie-secret" not in str(caught.value)
