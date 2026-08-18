#!/usr/bin/env python3
"""Audit Seedance 2.5 availability for unused 8,500-credit registrations.

The script reads successful registration rows directly from the FRAME OPS
database, decrypts stored JWTs in memory, evaluates the public feature flags,
reads the public model release, and writes redacted JSON/CSV reports. It does
not update registration usage state, renew sessions, or persist credentials.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import csv
import hashlib
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

APP_ORIGIN = "https://app.leonardo.ai"
GRAPHQL_URL = "https://api.leonardo.ai/v1/graphql"
LAUNCHDARKLY_CLIENT_ID = "64e42b5da32b9113dcf065c2"
MODEL_SCHEMA_ID = "https://leonardo.ai/platform/requests/generate/meta"
SEEDANCE25_MODEL = "bytedance/seedance-2.5"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/151.0.0.0 Safari/537.36"
)

USER_CONTEXT_QUERY = """query SeedanceContext($sub: String) {
  users(where: {user_details: {cognitoId: {_eq: $sub}}}) {
    id
    username
    createdAt
    blocked
    user_details {
      cognitoId
      auth0Email
      plan
      showNsfw
      interests
      interestsRoles
      interestsRolesOther
    }
  }
  teams { id createdAt }
}"""

GET_RELEASE_QUERY = """query GetRelease(
  $version: String!,
  $schemaIds: [String!]!
) @cached(ttl: 300) {
  publicJsonSchemaRegistry {
    release(id: $version) {
      id
      status
      description
      createdAt
      releasedAt
      deprecatedAt
      schemaReferences(schemaIds: $schemaIds, recursive: true) {
        schemaId
        schemaData
        md5Hash
      }
    }
  }
}"""


class AuditError(RuntimeError):
    """Expected remote or input error safe to include in a redacted report."""


def chunks(values: list[Any], size: int) -> list[list[Any]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def decode_jwt_payload(token: str) -> dict[str, Any]:
    parts = str(token or "").strip().split(".")
    if len(parts) != 3:
        raise AuditError("token_invalid_shape")
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        value = json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, json.JSONDecodeError) as exc:
        raise AuditError("token_invalid_payload") from exc
    if not isinstance(value, dict):
        raise AuditError("token_invalid_payload")
    return value


def split_export_line(line: str) -> tuple[str, str]:
    login_name, separator, remainder = line.partition("|")
    if not separator:
        raise AuditError("credential_export_invalid_line")
    _, separator, token = remainder.rpartition("|")
    if not separator or not login_name.strip() or not token.strip():
        raise AuditError("credential_export_missing_token")
    return login_name.strip().lower(), token.strip()


def launchdarkly_flag_value(flags: dict[str, Any], key: str) -> Any:
    value = flags.get(key)
    if isinstance(value, dict) and "value" in value:
        return value["value"]
    return value


def timestamp_milliseconds(value: Any, *, now: float | None = None) -> int:
    current = time.time() if now is None else now
    if isinstance(value, int) or isinstance(value, float):
        timestamp = float(value)
    elif isinstance(value, str) and value.strip():
        normalized = value.strip().replace("Z", "+00:00")
        try:
            # Python 3.9 only accepts 0, 3, or 6 fractional-second digits in
            # fromisoformat(), while the upstream GraphQL timestamp can use 1-6.
            fractional = re.fullmatch(
                r"(.+[T ][0-9]{2}:[0-9]{2}:[0-9]{2})\.([0-9]+)(.*)",
                normalized,
            )
            if fractional:
                digits = fractional.group(2)[:6].ljust(6, "0")
                normalized = f"{fractional.group(1)}.{digits}{fractional.group(3)}"
            parsed = datetime.fromisoformat(normalized)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)  # noqa: UP017
            timestamp = parsed.timestamp()
        except ValueError:
            try:
                timestamp = float(normalized)
            except ValueError as exc:
                raise AuditError("user_created_at_invalid") from exc
    else:
        timestamp = current
    if timestamp > 1_000_000_000_000:
        return int(timestamp)
    return int(timestamp * 1000)


def build_launchdarkly_context(
    user: dict[str, Any],
    details: dict[str, Any],
    teams: list[dict[str, Any]],
    token_payload: dict[str, Any],
    *,
    account_uuid: str,
    now: float | None = None,
) -> dict[str, Any]:
    current = time.time() if now is None else now
    created_at = timestamp_milliseconds(user.get("createdAt"), now=current)
    email = str(details.get("auth0Email") or "")
    email_domain = email.rsplit("@", 1)[-1].lower() if "@" in email else ""
    provider = str(token_payload.get("custom:provider") or "").strip().lower()
    device_key = str(uuid.uuid5(uuid.NAMESPACE_URL, f"frame-ops:{account_uuid}"))
    return {
        "kind": "multi",
        "user": {
            "key": str(user.get("id") or ""),
            "username": str(user.get("username") or ""),
            "emailDomain": email_domain,
            "plan": str(details.get("plan") or "FREE"),
            "showNsfw": bool(details.get("showNsfw")),
            "hasActiveTeams": bool(teams),
            "teamsIds": [str(team.get("id")) for team in teams if team.get("id")],
            "inChecklistExperiment": False,
            "interests": details.get("interests"),
            "interestsRoles": details.get("interestsRoles"),
            "interestsRolesOther": str(details.get("interestsRolesOther") or ""),
            "blocked": bool(user.get("blocked")),
            "deployment": "production",
            "createdAt": created_at,
            "accountAgeSeconds": max(0, int(current - created_at / 1000)),
            "isMobile": False,
            "isCanvaForBusiness": provider in {"canva", "canva_for_business"},
            "selectedTeamUUID": False,
        },
        "device": {
            "key": device_key,
            "operatingSystem": "macOS",
            "osVersion": "10.15.7",
            "browser": "Chrome",
            "browserVersion": "151.0.0.0",
            "isMobile": False,
        },
    }


def hasura_user_id(token_payload: dict[str, Any]) -> str:
    raw_claims = token_payload.get("https://hasura.io/jwt/claims")
    if isinstance(raw_claims, str):
        try:
            claims = json.loads(raw_claims)
        except json.JSONDecodeError as exc:
            raise AuditError("token_hasura_claims_invalid") from exc
    elif isinstance(raw_claims, dict):
        claims = raw_claims
    else:
        raise AuditError("token_hasura_claims_missing")
    user_id = str(claims.get("x-hasura-user-id") or "").strip()
    if not user_id:
        raise AuditError("token_hasura_user_id_missing")
    return user_id


def build_launchdarkly_context_from_token(
    token_payload: dict[str, Any],
    *,
    registration_uuid: str,
    plan: str,
    now: float | None = None,
) -> dict[str, Any]:
    current = time.time() if now is None else now
    created_at = timestamp_milliseconds(
        token_payload.get("auth_time") or token_payload.get("iat"),
        now=current,
    )
    email = str(token_payload.get("email") or "")
    email_domain = email.rsplit("@", 1)[-1].lower() if "@" in email else ""
    provider = str(
        token_payload.get("custom:provider")
        or token_payload.get("leonardoai_external_provider")
        or ""
    ).strip().lower()
    device_key = str(uuid.uuid5(uuid.NAMESPACE_URL, f"frame-ops:{registration_uuid}"))
    return {
        "kind": "multi",
        "user": {
            "key": hasura_user_id(token_payload),
            "username": str(token_payload.get("cognito:username") or ""),
            "emailDomain": email_domain,
            "plan": plan,
            "showNsfw": False,
            "hasActiveTeams": False,
            "teamsIds": [],
            "inChecklistExperiment": False,
            "interests": None,
            "interestsRoles": None,
            "interestsRolesOther": "",
            "blocked": False,
            "deployment": "production",
            "createdAt": created_at,
            "accountAgeSeconds": max(0, int(current - created_at / 1000)),
            "isMobile": False,
            "isCanvaForBusiness": provider in {"canva", "canva_for_business"},
            "selectedTeamUUID": False,
        },
        "device": {
            "key": device_key,
            "operatingSystem": "macOS",
            "osVersion": "10.15.7",
            "browser": "Chrome",
            "browserVersion": "151.0.0.0",
            "isMobile": False,
        },
    }


def redacted_registration(record: dict[str, Any]) -> dict[str, Any]:
    email = str(record.get("login_name") or "").strip().lower()
    domain = email.rsplit("@", 1)[-1] if "@" in email else ""
    return {
        "registration_uuid": str(record.get("registration_uuid") or ""),
        "email_sha256": hashlib.sha256(email.encode("utf-8")).hexdigest()[:16],
        "email_domain": domain,
        "status": str(record.get("status") or ""),
        "is_used": bool(record.get("is_used")),
        "awarded_points": record.get("awarded_points"),
        "token_expires_at": record.get("token_expires_at"),
    }


async def load_registration_accounts(
    *,
    status: str,
    credits: int,
    is_used: bool,
    offset: int,
    limit: int,
) -> list[dict[str, Any]]:
    from sqlalchemy import select

    from video_task_service.crypto import decrypt_secret
    from video_task_service.db import dispose_engine, session_factory
    from video_task_service.models import RegistrationRecord

    statement = (
        select(
            RegistrationRecord.id,
            RegistrationRecord.registration_uuid,
            RegistrationRecord.verified_email,
            RegistrationRecord.email_snapshot,
            RegistrationRecord.status,
            RegistrationRecord.is_used,
            RegistrationRecord.awarded_points,
            RegistrationRecord.video_token_ciphertext,
            RegistrationRecord.token_expires_at,
        )
        .where(
            RegistrationRecord.status == status,
            RegistrationRecord.is_used.is_(is_used),
            RegistrationRecord.awarded_points == credits,
        )
        .order_by(RegistrationRecord.id)
        .offset(offset)
    )
    if limit:
        statement = statement.limit(limit)
    try:
        async with session_factory() as session:
            rows = (await session.execute(statement)).all()
        records: list[dict[str, Any]] = []
        for row in rows:
            token = ""
            token_error = None
            if row.video_token_ciphertext is None:
                token_error = "stored_token_missing"
            else:
                try:
                    token = decrypt_secret(
                        bytes(row.video_token_ciphertext),
                        f"{row.registration_uuid}:registration_video_token",
                    )
                except Exception as exc:  # pragma: no cover - secret backend boundary
                    token_error = f"stored_token_decrypt_error:{type(exc).__name__}"
            records.append(
                {
                    "registration_uuid": row.registration_uuid,
                    "login_name": row.verified_email or row.email_snapshot,
                    "status": row.status,
                    "is_used": bool(row.is_used),
                    "awarded_points": row.awarded_points,
                    "token_expires_at": (
                        row.token_expires_at.isoformat()
                        if row.token_expires_at is not None
                        else None
                    ),
                    "_token": token,
                    "_token_error": token_error,
                }
            )
        return records
    finally:
        await dispose_engine()


def model_record(schema_reference: dict[str, Any]) -> dict[str, Any] | None:
    schema = schema_reference.get("schemaData")
    if not isinstance(schema, dict):
        return None
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return None
    model = properties.get("model")
    if not isinstance(model, dict) or not isinstance(model.get("const"), str):
        return None
    ui = model.get("ui:metadata") if isinstance(model.get("ui:metadata"), dict) else {}
    config = (
        model.get("leo:model_config")
        if isinstance(model.get("leo:model_config"), dict)
        else {}
    )
    capabilities = (
        config.get("capabilities") if isinstance(config.get("capabilities"), dict) else {}
    )
    return {
        "slug": model["const"],
        "id": config.get("id"),
        "name": config.get("name"),
        "type": config.get("type"),
        "ui_flag": ui.get("flag"),
        "featured": bool(ui.get("is_featured")),
        "generate": capabilities.get("generate"),
        "production_api_availability": capabilities.get("production_api_availability"),
        "schema_id": schema_reference.get("schemaId"),
        "order": ui.get("order"),
    }


def parse_model_catalog(response: dict[str, Any]) -> dict[str, Any]:
    try:
        release = response["data"]["publicJsonSchemaRegistry"]["release"]
    except (KeyError, TypeError) as exc:
        raise AuditError("model_release_missing") from exc
    if not isinstance(release, dict):
        raise AuditError("model_release_missing")
    references = release.get("schemaReferences")
    if not isinstance(references, list):
        raise AuditError("model_schema_references_missing")
    records = [record for item in references if (record := model_record(item)) is not None]
    records.sort(key=lambda item: (item.get("order") is None, item.get("order") or 0, item["slug"]))
    seedance = next((item for item in records if item["slug"] == SEEDANCE25_MODEL), None)
    return {
        "release_id": release.get("id"),
        "release_status": release.get("status"),
        "schema_reference_count": len(references),
        "model_count": len(records),
        "models": [item["slug"] for item in records],
        "seedance25": seedance,
    }


def safe_url_label(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc == "clientsdk.launchdarkly.com":
        return f"{parsed.scheme}://{parsed.netloc}/sdk/evalx/<client>/contexts/<redacted>"
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


class HttpClient:
    def __init__(self, timeout: float = 30, retries: int = 3) -> None:
        self.timeout = timeout
        self.retries = retries

    def request_bytes(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        payload = None
        request_headers = {"Accept": "application/json", **(headers or {})}
        if body is not None:
            payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            request = urllib.request.Request(
                url,
                data=payload,
                headers=request_headers,
                method=method,
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return response.status, dict(response.headers), response.read()
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code not in {408, 425, 429, 500, 502, 503, 504} or attempt >= self.retries:
                    raise AuditError(
                        f"remote_http_{exc.code}:{safe_url_label(url)}"
                    ) from exc
                retry_after = exc.headers.get("Retry-After")
                try:
                    delay = max(0.2, min(30.0, float(retry_after)))
                except (TypeError, ValueError):
                    delay = min(8.0, 0.5 * (2**attempt) + random.random() * 0.25)
                time.sleep(delay)
            except (TimeoutError, urllib.error.URLError) as exc:
                last_error = exc
                if attempt >= self.retries:
                    raise AuditError(f"remote_network_error:{safe_url_label(url)}") from exc
                time.sleep(min(8.0, 0.5 * (2**attempt) + random.random() * 0.25))
        raise AuditError(f"remote_request_failed:{last_error}")

    def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, str], Any]:
        status, response_headers, raw = self.request_bytes(
            method, url, headers=headers, body=body
        )
        try:
            return status, response_headers, json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AuditError(f"remote_invalid_json:{safe_url_label(url)}") from exc


def graphql_headers(token: str | None = None) -> dict[str, str]:
    headers = {
        "Accept": "*/*",
        "Origin": APP_ORIGIN,
        "Referer": f"{APP_ORIGIN}/",
        "User-Agent": USER_AGENT,
        "x-leo-schema-version": "latest",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def graphql(
    client: HttpClient,
    operation_name: str,
    query: str,
    variables: dict[str, Any],
    *,
    token: str | None = None,
) -> dict[str, Any]:
    _, _, response = client.request_json(
        "POST",
        GRAPHQL_URL,
        headers=graphql_headers(token),
        body={"operationName": operation_name, "variables": variables, "query": query},
    )
    if not isinstance(response, dict):
        raise AuditError("graphql_invalid_response")
    errors = response.get("errors")
    if isinstance(errors, list) and errors:
        messages = [str(item.get("message") or "") for item in errors if isinstance(item, dict)]
        digest = hashlib.sha256("|".join(messages).encode("utf-8")).hexdigest()[:12]
        raise AuditError(f"graphql_error:{operation_name}:{digest}")
    return response


def launchdarkly_flags(
    client: HttpClient,
    context: dict[str, Any],
    *,
    client_id: str,
) -> dict[str, Any]:
    encoded = base64.urlsafe_b64encode(
        json.dumps(context, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    url = f"https://clientsdk.launchdarkly.com/sdk/evalx/{client_id}/contexts/{encoded}"
    _, _, response = client.request_json(
        "GET",
        url,
        headers={
            "Origin": APP_ORIGIN,
            "Referer": f"{APP_ORIGIN}/",
            "User-Agent": USER_AGENT,
            "X-LaunchDarkly-Event-Schema": "4",
            "X-LaunchDarkly-Wrapper": "JSClient:4.7.4",
        },
    )
    if not isinstance(response, dict):
        raise AuditError("launchdarkly_invalid_response")
    return response


def inspect_account(
    account: dict[str, Any],
    token: str,
    client: HttpClient,
    *,
    launchdarkly_client_id: str,
    plan: str,
) -> dict[str, Any]:
    result = {
        **redacted_registration(account),
        "plan": plan,
        "token_expired": None,
        "feature_flag": None,
        "model_release": None,
        "catalog_has_seedance25": None,
        "has_seedance25": None,
        "reason": "unknown",
        "error": None,
    }
    try:
        token_error = account.get("_token_error")
        if token_error:
            raise AuditError(str(token_error))
        payload = decode_jwt_payload(token)
        expires_at = int(payload.get("exp") or 0)
        result["token_expired"] = expires_at <= int(time.time())
        context = build_launchdarkly_context_from_token(
            payload,
            registration_uuid=result["registration_uuid"],
            plan=plan,
        )
        flags = launchdarkly_flags(
            client,
            context,
            client_id=launchdarkly_client_id,
        )
        result["feature_flag"] = launchdarkly_flag_value(flags, "isSeedance25Enabled")
        result["model_release"] = launchdarkly_flag_value(
            flags, "modelMatrixActiveRelease"
        )
        if not isinstance(result["feature_flag"], bool):
            raise AuditError("seedance25_feature_flag_missing")
        if not isinstance(result["model_release"], str) or not result["model_release"]:
            raise AuditError("model_release_flag_missing")
        if result["feature_flag"] is False:
            result["has_seedance25"] = False
            result["reason"] = "feature_flag_disabled"
    except AuditError as exc:
        result["error"] = str(exc)
        result["reason"] = "inspection_error"
    except Exception as exc:  # pragma: no cover - defensive redaction boundary
        result["error"] = f"unexpected_error:{type(exc).__name__}"
        result["reason"] = "inspection_error"
    return result


def fetch_catalog(client: HttpClient, release: str) -> dict[str, Any]:
    response = graphql(
        client,
        "GetRelease",
        GET_RELEASE_QUERY,
        {"version": release, "schemaIds": [MODEL_SCHEMA_ID]},
    )
    return parse_model_catalog(response)


def write_private_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(value, encoding="utf-8")
    path.chmod(0o600)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "registration_uuid",
        "email_sha256",
        "email_domain",
        "status",
        "is_used",
        "awarded_points",
        "token_expires_at",
        "plan",
        "token_expired",
        "feature_flag",
        "model_release",
        "catalog_has_seedance25",
        "has_seedance25",
        "reason",
        "error",
    ]
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    path.chmod(0o600)


def apply_catalog_verdicts(
    results: list[dict[str, Any]], catalogs: dict[str, dict[str, Any]]
) -> None:
    for result in results:
        release = result.get("model_release")
        if not isinstance(release, str) or release not in catalogs:
            continue
        catalog = catalogs[release]
        seedance = catalog.get("seedance25")
        present = isinstance(seedance, dict)
        result["catalog_has_seedance25"] = present
        if result.get("feature_flag") is False:
            result["has_seedance25"] = False
            result["reason"] = "feature_flag_disabled"
        elif result.get("feature_flag") is True and not present:
            result["has_seedance25"] = False
            result["reason"] = "catalog_missing_model"
        elif result.get("feature_flag") is True and seedance.get("generate") is True:
            result["has_seedance25"] = True
            result["reason"] = "enabled"
        elif result.get("feature_flag") is True:
            result["has_seedance25"] = False
            result["reason"] = "catalog_generation_disabled"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read successful registration records directly from the FRAME OPS "
            "database and audit Seedance 2.5 availability."
        )
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env.local"))
    parser.add_argument("--status", default="SUCCEEDED")
    parser.add_argument("--credits", type=int, default=8500)
    parser.add_argument("--used", action="store_true")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--concurrency", type=int, default=12)
    parser.add_argument("--plan", default="BASIC")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--release", default="", help="Optional catalog release fallback")
    parser.add_argument("--launchdarkly-client-id", default=LAUNCHDARKLY_CLIENT_ID)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fail-on-unknown", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    env_file = load_env_file(args.env_file)
    for key, value in env_file.items():
        os.environ.setdefault(key, value)
    if not os.environ.get("VIDEO_SERVICE_MYSQL_DSN"):
        parser.error("VIDEO_SERVICE_MYSQL_DSN is required")
    if not os.environ.get("VIDEO_SERVICE_CREDENTIAL_MASTER_KEY"):
        parser.error("VIDEO_SERVICE_CREDENTIAL_MASTER_KEY is required")
    if not 1 <= args.concurrency <= 64:
        parser.error("--concurrency must be between 1 and 64")
    if args.limit < 0:
        parser.error("--limit must be zero or positive")
    if args.offset < 0:
        parser.error("--offset must be zero or positive")
    if args.credits < 0:
        parser.error("--credits must be zero or positive")

    client = HttpClient(timeout=args.timeout, retries=args.retries)
    accounts = asyncio.run(
        load_registration_accounts(
            status=args.status,
            credits=args.credits,
            is_used=args.used,
            offset=args.offset,
            limit=args.limit,
        )
    )
    print(
        json.dumps(
            {
                "stage": "inventory",
                "status": args.status,
                "is_used": args.used,
                "credits": args.credits,
                "offset": args.offset,
                "account_count": len(accounts),
                "token_configured": sum(bool(account.get("_token")) for account in accounts),
                "dry_run": args.dry_run,
            },
            ensure_ascii=False,
        )
    )
    if args.dry_run:
        return 0

    results: list[dict[str, Any]] = []
    enabled_so_far = 0
    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        pending = {
            executor.submit(
                inspect_account,
                account,
                str(account.get("_token") or ""),
                client,
                launchdarkly_client_id=args.launchdarkly_client_id,
                plan=args.plan,
            ): account
            for account in accounts
        }
        for index, future in enumerate(as_completed(pending), start=1):
            result = future.result()
            results.append(result)
            if result.get("feature_flag") is True:
                enabled_so_far += 1
            if index == len(accounts) or index % 25 == 0:
                print(
                    f"checked={index}/{len(accounts)} flag_enabled={enabled_so_far}",
                    file=sys.stderr,
                )
    results.sort(key=lambda item: item["registration_uuid"])

    releases = {
        str(result["model_release"])
        for result in results
        if isinstance(result.get("model_release"), str) and result["model_release"]
    }
    if args.release:
        releases.add(args.release)
    catalogs: dict[str, dict[str, Any]] = {}
    catalog_errors: dict[str, str] = {}
    for release in sorted(releases):
        try:
            catalogs[release] = fetch_catalog(client, release)
        except AuditError as exc:
            catalog_errors[release] = str(exc)
    apply_catalog_verdicts(results, catalogs)

    checked_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    summary = {
        "checked_at": checked_at,
        "source_status": args.status,
        "source_is_used": args.used,
        "source_credits": args.credits,
        "source_offset": args.offset,
        "assumed_plan": args.plan,
        "account_count": len(results),
        "seedance25_enabled": sum(item.get("has_seedance25") is True for item in results),
        "seedance25_disabled": sum(item.get("has_seedance25") is False for item in results),
        "unknown": sum(item.get("has_seedance25") is None for item in results),
        "release_ids": sorted(catalogs),
        "catalog_errors": catalog_errors,
    }
    output_dir = args.output_dir or Path(
        "work",
        f"seedance25-account-audit-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
    )
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    output_dir.chmod(0o700)
    report_path = output_dir / "seedance25-account-audit.json"
    csv_path = output_dir / "seedance25-account-audit.csv"
    enabled_path = output_dir / "seedance25-enabled.csv"
    catalogs_path = output_dir / "model-catalogs.json"
    write_private_text(
        report_path,
        json.dumps(
            {"summary": summary, "accounts": results}, ensure_ascii=False, indent=2
        )
        + "\n",
    )
    write_csv(csv_path, results)
    write_csv(enabled_path, [item for item in results if item.get("has_seedance25") is True])
    write_private_text(
        catalogs_path,
        json.dumps(
            {"catalogs": catalogs, "errors": catalog_errors}, ensure_ascii=False, indent=2
        )
        + "\n",
    )
    print(
        json.dumps(
            {
                "summary": summary,
                "artifacts": {
                    "json": str(report_path),
                    "csv": str(csv_path),
                    "enabled_csv": str(enabled_path),
                    "model_catalogs": str(catalogs_path),
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 2 if args.fail_on_unknown and summary["unknown"] else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AuditError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1) from exc
