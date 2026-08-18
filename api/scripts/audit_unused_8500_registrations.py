#!/usr/bin/env python3
"""Audit unused 8,500-point registration records without promoting them.

The script is intentionally independent from the account-pool workflows.  It
only SELECTs ``registration_records`` and, in live mode, performs read-only
Leonardo session/GraphQL checks using the encrypted registration session that
is already stored on that row.  It never creates, updates, or deletes an
``accounts`` row and never submits an image task.

Run it on the API host with the service environment loaded, for example:

    set -a; . /opt/frame-ops/shared/config/api.env; set +a
    PYTHONPATH=/opt/frame-ops/current/apps/api/src \
      python3 /opt/frame-ops/current/apps/api/scripts/audit_unused_8500_registrations.py \
      --mode live --output /secure/audits/unused-8500-status.txt

``--mode db`` is a fast database-only preview.  Since the database does not
persist Leonardo's provider-side ``users.blocked`` field, preview rows are
reported as ``INDETERMINATE``.  ``--mode live`` checks each stored registration
session directly and queries the provider ``users.blocked`` field.  A literal
``true`` is ``BLOCKED`` and a literal ``false`` with a matching identity is
``NORMAL``; transport, rate-limit, identity, and malformed responses remain
``INDETERMINATE``.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import secrets
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
from curl_cffi import requests as curl_requests
from curl_cffi.requests.errors import RequestsError as CurlRequestsError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from video_task_service.config import Settings, get_settings
from video_task_service.crypto import decrypt_secret
from video_task_service.models import RegistrationRecord
from video_task_service.protocol_renewal import (
    ProtocolRenewalError,
    ProtocolRequestGate,
    decode_jwt_payload,
    renew_protocol_session,
)
from video_task_service.schemas import RenewalSessionPayload
from video_task_service.upstream import (
    AccountValidation,
    LeonardoUpstream,
    UpstreamError,
)

SOURCE_STATUS = "SUCCEEDED"
SOURCE_POINTS = 8500
DEFAULT_CLIPROXY_ENDPOINT = "https://webipapi.cliproxy.com/api/getIpInfo"
DEFAULT_PROXY_PROBE_URL = "https://ipinfo.io/json"
PROXY_ALLOCATION_PORT_MIN = 443
PROXY_ALLOCATION_PORT_MAX = 3000
ACCOUNT_BLOCKED_QUERY = """query AccountBlockedStatus($sub: String) {
  users(where: {user_details: {cognitoId: {_eq: $sub}}}) {
    id
    blocked
    user_details {
      auth0Email
    }
  }
}"""
BLOCKED_CODES = frozenset(
    {
        "PROTOCOL_SESSION_REVOKED",
        "PROTOCOL_SESSION_UNAUTHORIZED",
        "UPSTREAM_UNAUTHORIZED",
    }
)
PROXY_ROTATE_CODES = frozenset(
    {
        "PROTOCOL_RATE_LIMITED",
        "PROTOCOL_NETWORK_ERROR",
        "PROTOCOL_TIMEOUT",
        "UPSTREAM_RATE_LIMITED",
        "UPSTREAM_NETWORK_ERROR",
        "UPSTREAM_TIMEOUT",
    }
)


@dataclass(frozen=True, slots=True)
class AuditResult:
    registration_id: int
    email: str
    classification: str
    code: str
    balance_credits: int | None
    checked_at: str


@dataclass(frozen=True, slots=True)
class ProxyEndpoint:
    worker: int
    source: str
    proxy_url: str
    exit_ip_hash: str
    country: str
    allocation_port: int | None = None


class ProxyProvisionError(RuntimeError):
    """A sanitized proxy error that never includes credentials or an API key."""


def read_private_text(path: Path | None) -> str:
    if path is None:
        return ""
    return path.read_text(encoding="utf-8").strip()


def read_env_file(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def masked_secret_fingerprint(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode()).hexdigest()[:12]


def registration_ids_from_retry_report(path: Path) -> set[int]:
    """Read INDETERMINATE ids from either an in-progress or finalized report."""

    ids: set[int] = set()
    section = ""
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        fields = line.split("\t")
        candidate = ""
        if section == "RESULTS" and len(fields) >= 3 and fields[0] == "INDETERMINATE":
            candidate = fields[2]
        elif section == "INDETERMINATE" and len(fields) >= 2:
            candidate = fields[1]
        if candidate.isdigit() and int(candidate) > 0:
            ids.add(int(candidate))
    if not ids:
        raise ValueError("--retry-report contains no INDETERMINATE registration ids")
    return ids


def decided_registration_ids_from_report(path: Path) -> set[int]:
    """Read only BLOCKED/NORMAL ids from an in-progress or finalized report."""

    ids: set[int] = set()
    section = ""
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        fields = line.split("\t")
        candidate = ""
        if section == "RESULTS" and len(fields) >= 3:
            if fields[0] in {"BLOCKED", "NORMAL"}:
                candidate = fields[2]
        elif section in {"BLOCKED", "NORMAL"} and len(fields) >= 2:
            candidate = fields[1]
        if candidate.isdigit() and int(candidate) > 0:
            ids.add(int(candidate))
    return ids


def _proxy_url(server: str, username: str, password: str) -> str:
    normalized = str(server or "").strip()
    if not normalized:
        raise ProxyProvisionError("PROXY_SERVER_MISSING")
    if "://" not in normalized:
        normalized = f"http://{normalized}"
    parsed = urlsplit(normalized)
    if not parsed.scheme or not parsed.hostname or not parsed.port:
        raise ProxyProvisionError("PROXY_SERVER_INVALID")
    credentials = f"{quote(username, safe='')}:{quote(password, safe='')}"
    return urlunsplit(
        (
            parsed.scheme,
            f"{credentials}@{parsed.hostname}:{parsed.port}",
            parsed.path,
            parsed.query,
            parsed.fragment,
        )
    )


def dynamic_proxy_url(values: dict[str, str], *, country: str) -> str:
    enabled = values.get("LEONARDO_PROXY_ENABLED", "").lower() == "true"
    username = values.get("LEONARDO_PROXY_USERNAME", "").strip()
    password = values.get("LEONARDO_PROXY_PASSWORD", "").strip()
    server = values.get("LEONARDO_PROXY_SERVER", "").strip()
    if not enabled or not username or not password or not server:
        raise ProxyProvisionError("DYNAMIC_PROXY_NOT_CONFIGURED")
    sid = secrets.token_hex(8)
    if re.search(r"-sid-[^-]+", username, flags=re.IGNORECASE):
        username = re.sub(
            r"-sid-[^-]+",
            f"-sid-{sid}",
            username,
            count=1,
            flags=re.IGNORECASE,
        )
    else:
        username = f"{username}-sid-{sid}"
    normalized_country = str(country or "RANDOM").strip().upper()
    region = "Rand" if normalized_country == "RANDOM" else normalized_country
    if re.search(r"-region-[^-]+", username, flags=re.IGNORECASE):
        username = re.sub(
            r"-region-[^-]+",
            f"-region-{region}",
            username,
            count=1,
            flags=re.IGNORECASE,
        )
    else:
        username = f"{username}-region-{region}"
    return _proxy_url(server, username, password)


def _parse_proxy_string(value: object) -> tuple[str, int, str, str] | None:
    text = str(value or "").strip()
    if not text:
        return None
    if "://" in text:
        parsed = urlsplit(text)
        if parsed.hostname and parsed.port and parsed.username and parsed.password:
            return parsed.hostname, parsed.port, parsed.username, parsed.password
        return None
    if "@" in text:
        credentials, address = text.rsplit("@", 1)
        if ":" not in credentials or ":" not in address:
            return None
        username, password = credentials.split(":", 1)
        host, raw_port = address.rsplit(":", 1)
    else:
        parts = text.split(":")
        if len(parts) < 4:
            return None
        host, raw_port, username = parts[:3]
        password = ":".join(parts[3:])
    try:
        port = int(raw_port)
    except ValueError:
        return None
    if not host or not username or not password or port < 1 or port > 65535:
        return None
    return host, port, username, password


def _find_proxy(value: object, seen: set[int] | None = None) -> tuple[str, int, str, str] | None:
    if isinstance(value, str):
        return _parse_proxy_string(value)
    if not isinstance(value, (dict, list)):
        return None
    seen = seen or set()
    marker = id(value)
    if marker in seen:
        return None
    seen.add(marker)
    if isinstance(value, list):
        for item in value:
            parsed = _find_proxy(item, seen)
            if parsed:
                return parsed
        return None
    for key in ("proxy", "proxy_url", "proxyUrl", "address"):
        parsed = _parse_proxy_string(value.get(key))
        if parsed:
            return parsed
    host = str(value.get("host") or value.get("hostname") or value.get("ip") or "").strip()
    raw_port = value.get("port") or value.get("proxy_port") or value.get("proxyPort")
    username = str(value.get("username") or value.get("user") or value.get("account") or "").strip()
    password = str(value.get("password") or value.get("passwd") or value.get("pwd") or "").strip()
    try:
        port = int(raw_port or 0)
    except (TypeError, ValueError):
        port = 0
    if host and username and password and 1 <= port <= 65535:
        return host, port, username, password
    for key in ("list", "data", "result", "rows", "items", "proxyList"):
        parsed = _find_proxy(value.get(key), seen)
        if parsed:
            return parsed
    return None


async def cliproxy_api_proxy_url(
    *,
    api_key: str,
    allocation_port: int,
    country: str,
    endpoint: str,
) -> str:
    if not api_key:
        raise ProxyProvisionError("CLIPROXY_API_KEY_MISSING")
    params: dict[str, str] = {
        "key": api_key,
        "port": str(allocation_port),
        "num": "1",
        "type": "2",
    }
    normalized_country = str(country or "RANDOM").strip().upper()
    if normalized_country != "RANDOM":
        params["country"] = normalized_country
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                endpoint,
                params=params,
                headers={"accept": "application/json"},
            )
    except httpx.HTTPError as error:
        raise ProxyProvisionError("CLIPROXY_API_NETWORK_ERROR") from error
    if response.status_code >= 400:
        raise ProxyProvisionError(f"CLIPROXY_API_HTTP_{response.status_code}")
    try:
        payload = response.json()
    except json.JSONDecodeError as error:
        raise ProxyProvisionError("CLIPROXY_API_INVALID_JSON") from error
    if isinstance(payload, dict) and payload.get("code") not in (None, 0, "0", 200, "200"):
        message = str(payload.get("msg") or payload.get("message") or "").lower()
        if "insufficient balance" in message:
            raise ProxyProvisionError("CLIPROXY_INSUFFICIENT_BALANCE")
        raise ProxyProvisionError(f"CLIPROXY_API_CODE_{payload.get('code')}")
    parsed = _find_proxy(payload)
    if not parsed:
        raise ProxyProvisionError("CLIPROXY_PROXY_MISSING")
    host, port, username, password = parsed
    return _proxy_url(f"socks5://{host}:{port}", username, password)


async def probe_proxy(proxy_url: str, *, probe_url: str, browser_profile: str) -> tuple[str, str]:
    try:
        async with curl_requests.AsyncSession(
            proxy=proxy_url,
            timeout=20,
            allow_redirects=True,
            impersonate=browser_profile,
            default_headers=True,
            max_clients=1,
        ) as client:
            response = await client.get(probe_url, headers={"accept": "application/json"})
    except CurlRequestsError as error:
        raise ProxyProvisionError("PROXY_PROBE_NETWORK_ERROR") from error
    if int(response.status_code) >= 400:
        raise ProxyProvisionError(f"PROXY_PROBE_HTTP_{response.status_code}")
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError) as error:
        raise ProxyProvisionError("PROXY_PROBE_INVALID_JSON") from error
    ip = str(payload.get("ip") or payload.get("query") or "").strip()
    if not ip:
        raise ProxyProvisionError("PROXY_PROBE_IP_MISSING")
    country = str(
        payload.get("country_code") or payload.get("countryCode") or payload.get("country") or ""
    ).strip().upper()
    return hashlib.sha256(ip.encode()).hexdigest()[:16], country


async def probe_provider_proxy(
    proxy_url: str,
    *,
    probe_url: str,
    browser_profile: str,
) -> None:
    """Reject exits already rate-limited by the provider before the audit starts."""

    try:
        async with curl_requests.AsyncSession(
            proxy=proxy_url,
            timeout=20,
            allow_redirects=True,
            impersonate=browser_profile,
            default_headers=True,
            max_clients=1,
        ) as client:
            response = await client.get(
                probe_url,
                headers={
                    "accept": "*/*",
                    "origin": "https://app.leonardo.ai",
                    "referer": "https://app.leonardo.ai/",
                },
            )
    except CurlRequestsError as error:
        raise ProxyProvisionError("PROXY_PROVIDER_PROBE_NETWORK_ERROR") from error
    status = int(response.status_code)
    if status == 429:
        raise ProxyProvisionError("PROXY_PROVIDER_RATE_LIMITED")
    if status < 200 or status >= 300:
        raise ProxyProvisionError(f"PROXY_PROVIDER_PROBE_HTTP_{status}")


async def provision_proxy_pool(
    *,
    count: int,
    mode: str,
    api_key: str,
    endpoint: str,
    country: str,
    dynamic_values: dict[str, str],
    probe_url: str,
    provider_probe_url: str,
    browser_profile: str,
    allocation_port_start: int,
) -> tuple[list[ProxyEndpoint], str, str]:
    """Allocate and prove one unique exit address per worker."""

    selected_mode = mode
    fallback_code = ""
    if mode == "auto":
        try:
            first_url = await cliproxy_api_proxy_url(
                api_key=api_key,
                allocation_port=allocation_port_start,
                country=country,
                endpoint=endpoint,
            )
            first_hash, first_country = await probe_proxy(
                first_url,
                probe_url=probe_url,
                browser_profile=browser_profile,
            )
            await probe_provider_proxy(
                first_url,
                probe_url=provider_probe_url,
                browser_profile=browser_profile,
            )
            first = ProxyEndpoint(
                worker=1,
                source="cliproxy-api",
                proxy_url=first_url,
                exit_ip_hash=first_hash,
                country=first_country,
                allocation_port=allocation_port_start,
            )
            selected_mode = "cliproxy-api"
        except ProxyProvisionError as error:
            selected_mode = "dynamic"
            fallback_code = str(error)
            first = None
    else:
        first = None

    used_hashes: set[str] = set()
    endpoints: list[ProxyEndpoint] = []
    endpoint_lock = asyncio.Lock()
    if first is not None:
        endpoints.append(first)
        used_hashes.add(first.exit_ip_hash)

    async def provision_worker(worker: int) -> ProxyEndpoint:
        for attempt in range(30):
            allocation_port = allocation_port_start + (worker - 1) + attempt * count
            if allocation_port > PROXY_ALLOCATION_PORT_MAX:
                raise ProxyProvisionError("CLIPROXY_ALLOCATION_PORT_EXHAUSTED")
            try:
                if selected_mode == "cliproxy-api":
                    proxy_url = await cliproxy_api_proxy_url(
                        api_key=api_key,
                        allocation_port=allocation_port,
                        country=country,
                        endpoint=endpoint,
                    )
                    source = "cliproxy-api"
                elif selected_mode == "dynamic":
                    proxy_url = dynamic_proxy_url(dynamic_values, country=country)
                    source = "dynamic-fallback" if fallback_code else "dynamic"
                    allocation_port = None
                else:
                    raise ProxyProvisionError("PROXY_MODE_INVALID")
                exit_hash, actual_country = await probe_proxy(
                    proxy_url,
                    probe_url=probe_url,
                    browser_profile=browser_profile,
                )
                await probe_provider_proxy(
                    proxy_url,
                    probe_url=provider_probe_url,
                    browser_profile=browser_profile,
                )
            except ProxyProvisionError:
                if attempt < 29:
                    await asyncio.sleep(0.25)
                continue
            candidate = ProxyEndpoint(
                worker=worker,
                source=source,
                proxy_url=proxy_url,
                exit_ip_hash=exit_hash,
                country=actual_country,
                allocation_port=allocation_port,
            )
            async with endpoint_lock:
                if exit_hash in used_hashes:
                    continue
                endpoints.append(candidate)
                used_hashes.add(exit_hash)
                print(
                    f"proxy_ready={len(endpoints)}/{count}",
                    file=sys.stderr,
                    flush=True,
                )
                return candidate
        raise ProxyProvisionError("PROXY_EXIT_NOT_UNIQUE")

    pending_workers = range(len(endpoints) + 1, count + 1)
    if pending_workers:
        tasks = [asyncio.create_task(provision_worker(worker)) for worker in pending_workers]
        try:
            await asyncio.gather(*tasks)
        except Exception:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
    endpoints.sort(key=lambda item: item.worker)
    return endpoints, selected_mode, fallback_code


def write_proxy_manifest(
    path: Path,
    *,
    endpoints: list[ProxyEndpoint],
    requested_mode: str,
    selected_mode: str,
    fallback_code: str,
    api_key_fingerprint: str,
) -> None:
    payload = {
        "generated_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "requested_mode": requested_mode,
        "selected_mode": selected_mode,
        "fallback_code": fallback_code,
        "api_key_configured": bool(api_key_fingerprint),
        "api_key_sha256_prefix": api_key_fingerprint,
        "workers": len(endpoints),
        "unique_exit_ips": len({item.exit_ip_hash for item in endpoints}),
        "endpoints": [
            {
                "worker": item.worker,
                "source": item.source,
                "exit_ip_sha256_prefix": item.exit_ip_hash,
                "country": item.country,
                "allocation_port": item.allocation_port,
            }
            for item in endpoints
        ],
    }
    write_report(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


class ProxyLeonardoAuditClient:
    """Minimal read-only GraphQL client bound to one worker proxy."""

    BALANCE_QUERY = LeonardoUpstream.BALANCE_QUERY

    def __init__(self, settings: Settings, proxy_url: str) -> None:
        self._settings = settings
        self._client = curl_requests.AsyncSession(
            proxy=proxy_url,
            timeout=120,
            allow_redirects=True,
            impersonate=settings.protocol_renewal_browser_profile,
            default_headers=True,
            max_clients=1,
            headers={
                "accept": "*/*",
                "origin": "https://app.leonardo.ai",
                "referer": "https://app.leonardo.ai/",
                "x-leo-schema-version": settings.leonardo_schema_version,
            },
        )

    async def _gql(
        self,
        token: str | None,
        operation_name: str,
        variables: dict[str, Any],
        query: str,
    ) -> dict[str, Any]:
        headers = {"content-type": "application/json"}
        if token:
            headers["authorization"] = f"Bearer {token}"
        try:
            response = await self._client.post(
                self._settings.leonardo_graphql_url,
                headers=headers,
                json={
                    "operationName": operation_name,
                    "variables": variables,
                    "query": query,
                },
            )
        except CurlRequestsError as error:
            raise UpstreamError("UPSTREAM_NETWORK_ERROR", "provider network error") from error
        status = int(response.status_code)
        if status in {401, 403}:
            raise UpstreamError("UPSTREAM_UNAUTHORIZED", "provider rejected token", retryable=False)
        if status == 429:
            raise UpstreamError("UPSTREAM_RATE_LIMITED", "provider rate limited account")
        if status >= 500:
            raise UpstreamError("UPSTREAM_SERVER_ERROR", "provider server error")
        if status >= 400:
            raise UpstreamError(
                "UPSTREAM_REQUEST_REJECTED",
                f"provider HTTP {status}",
                retryable=False,
            )
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as error:
            raise UpstreamError(
                "UPSTREAM_INVALID_JSON",
                "provider returned invalid JSON",
            ) from error
        if not isinstance(payload, dict):
            raise UpstreamError("UPSTREAM_INVALID_JSON", "provider returned non-object JSON")
        errors = payload.get("errors") or []
        if errors:
            message = " | ".join(
                str(item.get("message", "GraphQL error"))[:300]
                for item in errors
                if isinstance(item, dict)
            )
            lowered = message.lower()
            unauthorized = "unauthorized" in lowered or "jwt" in lowered
            raise UpstreamError(
                "UPSTREAM_UNAUTHORIZED" if unauthorized else "UPSTREAM_GRAPHQL_ERROR",
                message or "provider GraphQL error",
                retryable=not unauthorized,
            )
        return payload

    async def validate_account(self, *, token: str) -> AccountValidation:
        try:
            payload = await self._gql(token, "GetTokenBalance", {}, self.BALANCE_QUERY)
        except UpstreamError as error:
            return AccountValidation(valid=False, error_code=error.code)
        details = (payload.get("data") or {}).get("user_details") or []
        if not details:
            return AccountValidation(valid=False, error_code="UPSTREAM_NO_USER_DETAILS")
        first = details[0]
        balance = sum(
            int(first.get(key) or 0)
            for key in ("subscriptionTokens", "paidTokens", "rolloverTokens")
        )
        login_name = normalize_email(first.get("auth0Email")) or None
        return AccountValidation(valid=True, balance_credits=balance, login_name=login_name)

    async def close(self) -> None:
        await self._client.close()


def normalize_email(value: str | None) -> str:
    return str(value or "").strip().lower()


def classify_code(code: str, *, valid: bool = False) -> str:
    """Map a provider result to a conservative report section."""

    if valid:
        return "NORMAL"
    return "BLOCKED" if code in BLOCKED_CODES else "INDETERMINATE"


def source_statement(
    limit: int | None,
    start_after_id: int,
    registration_ids: set[int] | None = None,
):
    statement = (
        select(RegistrationRecord)
        .where(
            RegistrationRecord.status == SOURCE_STATUS,
            RegistrationRecord.is_used.is_(False),
            RegistrationRecord.awarded_points == SOURCE_POINTS,
            RegistrationRecord.id > start_after_id,
        )
        .order_by(RegistrationRecord.created_at.asc(), RegistrationRecord.id.asc())
    )
    if registration_ids is not None:
        statement = statement.where(RegistrationRecord.id.in_(sorted(registration_ids)))
    return statement.limit(limit) if limit is not None else statement


async def load_records(
    engine: AsyncEngine,
    *,
    limit: int | None,
    start_after_id: int,
    registration_ids: set[int] | None = None,
) -> list[RegistrationRecord]:
    async with engine.connect() as connection:
        await connection.exec_driver_sql("SET SESSION TRANSACTION READ ONLY")
        await connection.commit()
        async with AsyncSession(bind=connection, expire_on_commit=False) as session:
            result = await session.scalars(
                source_statement(limit, start_after_id, registration_ids)
            )
            rows = list(result)
            session.expunge_all()
            await session.rollback()
            return rows


def _result(
    row: RegistrationRecord,
    classification: str,
    code: str,
    balance_credits: int | None = None,
) -> AuditResult:
    return AuditResult(
        registration_id=int(row.id),
        email=normalize_email(row.verified_email or row.email_snapshot),
        classification=classification,
        code=code,
        balance_credits=balance_credits,
        checked_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )


def _user_detail_email(value: object) -> str:
    if isinstance(value, list):
        value = value[0] if value else None
    if not isinstance(value, dict):
        return ""
    return normalize_email(value.get("auth0Email"))


def blocked_verdict(
    payload: dict[str, Any],
    *,
    expected_email: str,
) -> tuple[bool | None, str]:
    users = (payload.get("data") or {}).get("users")
    if not isinstance(users, list):
        return None, "UPSTREAM_BLOCKED_STATUS_UNAVAILABLE"
    if not users:
        return None, "UPSTREAM_BLOCKED_USER_NOT_FOUND"
    if len(users) != 1 or not isinstance(users[0], dict):
        return None, "UPSTREAM_BLOCKED_USER_AMBIGUOUS"
    user = users[0]
    actual_email = _user_detail_email(user.get("user_details"))
    if not actual_email:
        return None, "UPSTREAM_BLOCKED_IDENTITY_UNAVAILABLE"
    if actual_email != normalize_email(expected_email):
        return None, "UPSTREAM_BLOCKED_IDENTITY_MISMATCH"
    blocked = user.get("blocked")
    if not isinstance(blocked, bool):
        return None, "UPSTREAM_BLOCKED_STATUS_UNAVAILABLE"
    return blocked, (
        "UPSTREAM_ACCOUNT_BLOCKED" if blocked else "UPSTREAM_ACCOUNT_NOT_BLOCKED"
    )


async def query_provider_blocked(
    *,
    upstream: Any,
    token: str,
    expected_email: str,
) -> tuple[bool | None, str]:
    token_payload = decode_jwt_payload(token)
    subject = str((token_payload or {}).get("sub") or "").strip()
    if not subject:
        return None, "UPSTREAM_BLOCKED_SUBJECT_UNAVAILABLE"
    payload = await upstream._gql(  # noqa: SLF001 - standalone provider audit
        token,
        "AccountBlockedStatus",
        {"sub": subject},
        ACCOUNT_BLOCKED_QUERY,
    )
    return blocked_verdict(payload, expected_email=expected_email)


async def check_live(
    row: RegistrationRecord,
    *,
    settings: Settings,
    upstream: Any,
    proxy_url: str | None = None,
    request_gate: ProtocolRequestGate | None = None,
) -> AuditResult:
    email = normalize_email(row.verified_email or row.email_snapshot)
    if not row.session_ciphertext:
        return _result(row, "INDETERMINATE", "REGISTRATION_SESSION_MISSING")
    try:
        encoded = decrypt_secret(
            bytes(row.session_ciphertext),
            f"{row.registration_uuid}:registration_session",
        )
        material = json.loads(encoded)
        RenewalSessionPayload.model_validate(material)
        protocol = await renew_protocol_session(
            material=material,
            stored_token="",
            expected_email=email,
            settings=settings,
            proxy_url=proxy_url,
            request_gate=request_gate,
        )
        validation = await upstream.validate_account(token=protocol.token)
        code = validation.error_code or "UPSTREAM_ACCOUNT_VALIDATION_FAILED"
        if not validation.valid:
            return _result(row, classify_code(code), code)
        graphql_email = normalize_email(validation.login_name)
        if not graphql_email:
            return _result(row, "INDETERMINATE", "UPSTREAM_IDENTITY_UNAVAILABLE")
        if graphql_email != email:
            return _result(row, "INDETERMINATE", "UPSTREAM_IDENTITY_MISMATCH")
        blocked, blocked_code = await query_provider_blocked(
            upstream=upstream,
            token=protocol.token,
            expected_email=email,
        )
        if blocked is None:
            return _result(
                row,
                "INDETERMINATE",
                blocked_code,
                validation.balance_credits,
            )
        if blocked:
            return _result(
                row,
                "BLOCKED",
                blocked_code,
                validation.balance_credits,
            )
        return _result(
            row,
            "NORMAL",
            blocked_code,
            validation.balance_credits,
        )
    except ProtocolRenewalError as error:
        return _result(row, classify_code(error.code), error.code)
    except UpstreamError as error:
        return _result(row, classify_code(error.code), error.code)
    except (ValueError, TypeError, json.JSONDecodeError):
        return _result(row, "INDETERMINATE", "REGISTRATION_SESSION_INVALID")
    except Exception:  # pragma: no cover - defensive boundary for a batch row
        return _result(row, "INDETERMINATE", "AUDIT_INTERNAL_ERROR")


def check_db_only(row: RegistrationRecord) -> AuditResult:
    return _result(row, "INDETERMINATE", "PROVIDER_STATUS_NOT_PERSISTED")


def render_report(
    results: list[AuditResult],
    *,
    mode: str,
    generated_at: str,
    metadata: dict[str, str] | None = None,
) -> str:
    counts = Counter(item.classification for item in results)
    lines = [
        "# FRAME OPS standalone unused 8,500-point registration audit",
        f"# generated_at_utc={generated_at}",
        f"# mode={mode}",
        *(
            f"# {key}={str(value).replace(chr(10), ' ').replace(chr(13), ' ')}"
            for key, value in (metadata or {}).items()
        ),
        "# source_filter=status=SUCCEEDED AND is_used=0 AND awarded_points=8500",
        "# source_order=created_at ASC,id ASC",
        f"# source_rows={len(results)}",
        "# account_pool_write=false",
        "# image_task_submission=false",
        "# blocked_definition=users.blocked=true or explicit authentication rejection",
        "# indeterminate_definition=provider status not persisted or result was "
        "retryable/ambiguous",
        "# fields=email\\tregistration_id\\tcheck_code\\tbalance_credits\\tchecked_at",
        f"# counts=BLOCKED:{counts.get('BLOCKED', 0)},NORMAL:{counts.get('NORMAL', 0)},"
        f"INDETERMINATE:{counts.get('INDETERMINATE', 0)}",
    ]
    for classification in ("BLOCKED", "NORMAL", "INDETERMINATE"):
        lines.extend(("", f"[{classification}]"))
        for item in results:
            if item.classification != classification:
                continue
            balance = "" if item.balance_credits is None else str(item.balance_credits)
            lines.append(
                "\t".join(
                    (
                        item.email,
                        str(item.registration_id),
                        item.code,
                        balance,
                        item.checked_at,
                    )
                )
            )
    return "\n".join(lines) + "\n"


def write_report(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(content, encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def _format_result_line(item: AuditResult) -> str:
    balance = "" if item.balance_credits is None else str(item.balance_credits)
    return "\t".join(
        (
            item.classification,
            item.email,
            str(item.registration_id),
            item.code,
            balance,
            item.checked_at,
        )
    )


class IncrementalReportWriter:
    """Append each completed check, then materialize the grouped report."""

    def __init__(
        self,
        path: Path,
        *,
        mode: str,
        source_rows: int,
        metadata: dict[str, str] | None = None,
    ) -> None:
        self.path = path
        self.mode = mode
        self.source_rows = source_rows
        self.metadata = metadata or {}
        self.completed = 0
        self._initialize()

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            "\n".join(
                (
                    "# FRAME OPS standalone unused 8,500-point registration audit",
                    f"# generated_at_utc={datetime.now(UTC).isoformat().replace('+00:00', 'Z')}",
                    f"# mode={self.mode}",
                    *(
                        f"# {key}={str(value).replace(chr(10), ' ').replace(chr(13), ' ')}"
                        for key, value in self.metadata.items()
                    ),
                    "# report_status=IN_PROGRESS",
                    f"# source_rows={self.source_rows}",
                    "# source_order=created_at ASC,id ASC",
                    "# account_pool_write=false",
                    "# image_task_submission=false",
                    "# blocked_definition=users.blocked=true or explicit authentication rejection",
                    "# fields=classification\\temail\\tregistration_id\\tcheck_code\\t"
                    "balance_credits\\tchecked_at",
                    "[RESULTS]",
                    "",
                )
            ),
            encoding="utf-8",
        )
        os.chmod(self.path, 0o600)

    def write_result(self, result: AuditResult) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(_format_result_line(result) + "\n")
            handle.flush()
        os.chmod(self.path, 0o600)
        self.completed += 1

    def finalize(self, results: list[AuditResult]) -> None:
        generated_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        write_report(
            self.path,
            render_report(
                results,
                mode=self.mode,
                generated_at=generated_at,
                metadata=self.metadata,
            ),
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only audit of unused 8,500-point registration records."
    )
    parser.add_argument("--mode", choices=("live", "db"), default="live")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("unused-8500-status.txt"),
        help="local report path (mode 0600)",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--start-after-id", type=int, default=0)
    parser.add_argument(
        "--retry-report",
        type=Path,
        default=None,
        help="only recheck INDETERMINATE registration ids from an earlier report",
    )
    parser.add_argument(
        "--exclude-decided-report",
        type=Path,
        default=None,
        help="exclude BLOCKED/NORMAL ids already persisted by an earlier partial pass",
    )
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=2.0,
        help="delay per worker after a live provider check",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=20,
        help="maximum number of account checks in flight",
    )
    parser.add_argument("--progress-every", type=int, default=25)
    parser.add_argument(
        "--row-retries",
        type=int,
        default=2,
        help="rotate the worker proxy and retry transient rows before classifying them",
    )
    parser.add_argument(
        "--proxy-mode",
        choices=("direct", "auto", "cliproxy-api", "dynamic"),
        default="direct",
        help="bind each live worker to its own verified proxy exit",
    )
    parser.add_argument("--cliproxy-api-key-file", type=Path, default=None)
    parser.add_argument("--proxy-env-file", type=Path, default=None)
    parser.add_argument("--proxy-country", default="RANDOM")
    parser.add_argument("--cliproxy-endpoint", default=DEFAULT_CLIPROXY_ENDPOINT)
    parser.add_argument("--proxy-probe-url", default=DEFAULT_PROXY_PROBE_URL)
    parser.add_argument(
        "--proxy-provider-probe-url",
        default=None,
        help="provider endpoint that must return 2xx through every worker exit",
    )
    parser.add_argument("--proxy-allocation-port-start", type=int, default=443)
    parser.add_argument(
        "--proxy-manifest",
        type=Path,
        default=None,
        help="private JSON proof containing only exit hashes, never credentials",
    )
    return parser


async def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.limit is not None and (args.limit < 1 or args.limit > 100_000):
        raise ValueError("--limit must be between 1 and 100000")
    if args.start_after_id < 0:
        raise ValueError("--start-after-id must be non-negative")
    if args.interval_seconds < 0 or args.interval_seconds > 60:
        raise ValueError("--interval-seconds must be between 0 and 60")
    if args.concurrency < 1 or args.concurrency > 32:
        raise ValueError("--concurrency must be between 1 and 32")
    if args.progress_every < 0:
        raise ValueError("--progress-every must be non-negative")
    if args.row_retries < 0 or args.row_retries > 5:
        raise ValueError("--row-retries must be between 0 and 5")
    if not (
        PROXY_ALLOCATION_PORT_MIN
        <= args.proxy_allocation_port_start
        <= PROXY_ALLOCATION_PORT_MAX
    ):
        raise ValueError(
            f"--proxy-allocation-port-start must be between "
            f"{PROXY_ALLOCATION_PORT_MIN} and {PROXY_ALLOCATION_PORT_MAX}"
        )
    normalized_country = str(args.proxy_country or "RANDOM").strip().upper()
    if normalized_country != "RANDOM" and not re.fullmatch(r"[A-Z]{2}", normalized_country):
        raise ValueError("--proxy-country must be RANDOM or a two-letter country code")

    settings = get_settings()
    if args.mode == "live" and settings.upstream_mode.lower() != "leonardo":
        raise RuntimeError(
            "live mode requires VIDEO_SERVICE_UPSTREAM_MODE=leonardo; use --mode db for preview"
        )

    retry_ids = (
        registration_ids_from_retry_report(args.retry_report)
        if args.retry_report is not None
        else None
    )
    excluded_decided_ids = (
        decided_registration_ids_from_report(args.exclude_decided_report)
        if args.exclude_decided_report is not None
        else set()
    )
    if retry_ids is not None:
        retry_ids -= excluded_decided_ids
    api_key = read_private_text(args.cliproxy_api_key_file)
    dynamic_values = read_env_file(args.proxy_env_file)
    engine = create_async_engine(settings.mysql_dsn_value, pool_pre_ping=True)
    shared_upstream: LeonardoUpstream | None = None
    proxy_clients: list[ProxyLeonardoAuditClient] = []
    try:
        rows = await load_records(
            engine,
            limit=args.limit,
            start_after_id=args.start_after_id,
            registration_ids=retry_ids,
        )
        worker_count = min(args.concurrency, len(rows))
        proxy_endpoints: list[ProxyEndpoint] = []
        selected_proxy_mode = "direct"
        proxy_fallback_code = ""
        proxy_manifest_path: Path | None = None
        proxy_rotation_count = 0
        proxy_rotation_lock = asyncio.Lock()
        provider_probe_url = (
            args.proxy_provider_probe_url
            or f"{settings.protocol_renewal_origin.rstrip('/')}"
            "/api/auth/get-session"
        )
        if args.mode == "live" and args.proxy_mode != "direct" and worker_count:
            proxy_endpoints, selected_proxy_mode, proxy_fallback_code = await provision_proxy_pool(
                count=worker_count,
                mode=args.proxy_mode,
                api_key=api_key,
                endpoint=args.cliproxy_endpoint,
                country=normalized_country,
                dynamic_values=dynamic_values,
                probe_url=args.proxy_probe_url,
                provider_probe_url=provider_probe_url,
                browser_profile=settings.protocol_renewal_browser_profile,
                allocation_port_start=args.proxy_allocation_port_start,
            )
            proxy_manifest_path = args.proxy_manifest or args.output.with_name(
                f"{args.output.stem}.proxies.json"
            )
            write_proxy_manifest(
                proxy_manifest_path,
                endpoints=proxy_endpoints,
                requested_mode=args.proxy_mode,
                selected_mode=selected_proxy_mode,
                fallback_code=proxy_fallback_code,
                api_key_fingerprint=masked_secret_fingerprint(api_key),
            )
            proxy_clients = [
                ProxyLeonardoAuditClient(settings, endpoint.proxy_url)
                for endpoint in proxy_endpoints
            ]
            print(
                f"proxy_mode={selected_proxy_mode} "
                f"workers={len(proxy_endpoints)} "
                f"unique_exit_ips={len({item.exit_ip_hash for item in proxy_endpoints})}",
                file=sys.stderr,
                flush=True,
            )
        elif args.mode == "live":
            shared_upstream = LeonardoUpstream(settings)

        metadata = {
            "retry_filter": "INDETERMINATE" if retry_ids is not None else "none",
            "retry_source_ids": str(len(retry_ids or set())),
            "excluded_decided_ids": str(len(excluded_decided_ids)),
            "proxy_requested_mode": args.proxy_mode if args.mode == "live" else "none",
            "proxy_selected_mode": selected_proxy_mode if args.mode == "live" else "none",
            "proxy_unique_exit_ips": str(len({item.exit_ip_hash for item in proxy_endpoints})),
        }
        writer = IncrementalReportWriter(
            args.output,
            mode=args.mode,
            source_rows=len(rows),
            metadata=metadata,
        )
        results: list[AuditResult] = []
        live_counts: Counter[str] = Counter()
        queue: asyncio.Queue[tuple[int, RegistrationRecord]] = asyncio.Queue()
        for index, row in enumerate(rows, 1):
            queue.put_nowait((index, row))
        result_lock = asyncio.Lock()

        async def rotate_worker_proxy(worker_index: int) -> bool:
            nonlocal proxy_rotation_count
            for _ in range(10):
                try:
                    replacement_pool, _, _ = await provision_proxy_pool(
                        count=1,
                        mode=selected_proxy_mode,
                        api_key=api_key,
                        endpoint=args.cliproxy_endpoint,
                        country=normalized_country,
                        dynamic_values=dynamic_values,
                        probe_url=args.proxy_probe_url,
                        provider_probe_url=provider_probe_url,
                        browser_profile=settings.protocol_renewal_browser_profile,
                        allocation_port_start=(
                            min(
                                args.proxy_allocation_port_start
                                + (proxy_rotation_count + 1) * args.concurrency,
                                PROXY_ALLOCATION_PORT_MAX,
                            )
                            if selected_proxy_mode == "cliproxy-api"
                            else PROXY_ALLOCATION_PORT_MIN
                        ),
                    )
                except ProxyProvisionError:
                    continue
                replacement = replacement_pool[0]
                async with proxy_rotation_lock:
                    active_hashes = {
                        endpoint.exit_ip_hash
                        for index, endpoint in enumerate(proxy_endpoints)
                        if index != worker_index
                    }
                    if replacement.exit_ip_hash in active_hashes:
                        continue
                    replacement = ProxyEndpoint(
                        worker=worker_index + 1,
                        source=replacement.source,
                        proxy_url=replacement.proxy_url,
                        exit_ip_hash=replacement.exit_ip_hash,
                        country=replacement.country,
                        allocation_port=replacement.allocation_port,
                    )
                    previous_client = proxy_clients[worker_index]
                    proxy_endpoints[worker_index] = replacement
                    proxy_clients[worker_index] = ProxyLeonardoAuditClient(
                        settings,
                        replacement.proxy_url,
                    )
                    proxy_rotation_count += 1
                    if proxy_manifest_path is not None:
                        write_proxy_manifest(
                            proxy_manifest_path,
                            endpoints=proxy_endpoints,
                            requested_mode=args.proxy_mode,
                            selected_mode=selected_proxy_mode,
                            fallback_code=proxy_fallback_code,
                            api_key_fingerprint=masked_secret_fingerprint(api_key),
                        )
                await previous_client.close()
                print(
                    f"proxy_rotated_worker={worker_index + 1} "
                    f"rotations={proxy_rotation_count}",
                    file=sys.stderr,
                    flush=True,
                )
                return True
            return False

        async def worker(worker_index: int) -> None:
            while True:
                try:
                    index, row = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    if args.mode == "live":
                        for attempt in range(args.row_retries + 1):
                            if proxy_clients:
                                worker_upstream: Any = proxy_clients[worker_index]
                                proxy_url = proxy_endpoints[worker_index].proxy_url
                                request_gate = ProtocolRequestGate()
                            else:
                                assert shared_upstream is not None
                                worker_upstream = shared_upstream
                                proxy_url = None
                                request_gate = None
                            result = await check_live(
                                row,
                                settings=settings,
                                upstream=worker_upstream,
                                proxy_url=proxy_url,
                                request_gate=request_gate,
                            )
                            should_rotate = (
                                bool(proxy_clients)
                                and result.code in PROXY_ROTATE_CODES
                                and attempt < args.row_retries
                            )
                            if not should_rotate:
                                break
                            if not await rotate_worker_proxy(worker_index):
                                break
                    else:
                        result = check_db_only(row)
                    async with result_lock:
                        results.append(result)
                        live_counts[result.classification] += 1
                        # The append happens as soon as this account completes;
                        # the final grouped report is only a materialization step.
                        writer.write_result(result)
                        completed = writer.completed
                        if args.progress_every and (
                            completed == 1 or completed % args.progress_every == 0
                        ):
                            print(
                                f"checked={completed}/{len(rows)} "
                                f"blocked={live_counts.get('BLOCKED', 0)} "
                                f"normal={live_counts.get('NORMAL', 0)} "
                                f"indeterminate={live_counts.get('INDETERMINATE', 0)}",
                                file=sys.stderr,
                                flush=True,
                            )
                finally:
                    queue.task_done()
                if args.interval_seconds:
                    await asyncio.sleep(args.interval_seconds)

        workers = [
            asyncio.create_task(worker(worker_index))
            for worker_index in range(worker_count)
        ]
        await queue.join()
        await asyncio.gather(*workers)
        writer.finalize(results)
        counts = Counter(item.classification for item in results)
        return {
            "ok": True,
            "mode": args.mode,
            "source_rows": len(rows),
            "counts": {key: counts.get(key, 0) for key in ("BLOCKED", "NORMAL", "INDETERMINATE")},
            "output": str(args.output.resolve()),
            "concurrency": args.concurrency,
            "retry_source_ids": len(retry_ids or set()),
            "excluded_decided_ids": len(excluded_decided_ids),
            "proxy_requested_mode": args.proxy_mode,
            "proxy_selected_mode": selected_proxy_mode,
            "proxy_unique_exit_ips": len({item.exit_ip_hash for item in proxy_endpoints}),
            "proxy_rotations": proxy_rotation_count,
            "proxy_manifest": str(proxy_manifest_path.resolve()) if proxy_manifest_path else None,
            "incremental_write": True,
            "account_pool_write": False,
            "image_task_submission": False,
        }
    finally:
        for client in proxy_clients:
            await client.close()
        if shared_upstream is not None:
            await shared_upstream.close()
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = asyncio.run(run(args))
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error)[:300]}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
