from __future__ import annotations

import asyncio
import io
import json
import zipfile
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from video_task_service.api.account_cookie_imports import (
    cookie_import_batch_view,
    persist_cookie_import_batch,
)
from video_task_service.api.main import app
from video_task_service.config import get_settings
from video_task_service.cookie_import_archive import (
    ParsedCookieImportArchive,
    ParsedCookieImportItem,
)
from video_task_service.crypto import decrypt_secret
from video_task_service.db import session_dependency
from video_task_service.models import AccountCookieImportBatch, Space
from video_task_service.schemas import RenewalSessionPayload


class FakeSession:
    def __init__(self, scalar_values: list[object | None]) -> None:
        self.scalar_values = scalar_values
        self.added: list[object] = []

    class Transaction:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *args: object) -> None:
            return None

    def begin(self) -> Transaction:
        return self.Transaction()

    async def scalar(self, statement: object) -> object | None:
        assert statement is not None
        return self.scalar_values.pop(0)

    def add(self, value: object) -> None:
        self.added.append(value)

    def add_all(self, values: list[object]) -> None:
        self.added.extend(values)

    async def flush(self) -> None:
        for value in self.added:
            if isinstance(value, Space) and value.id is None:
                value.id = 5
            elif isinstance(value, AccountCookieImportBatch) and value.id is None:
                value.id = 7


def _parsed_archive() -> ParsedCookieImportArchive:
    renewal = RenewalSessionPayload(
        cookies=[
            {
                "name": "__Secure-better-auth.session_token",
                "value": "session-token-secret",
                "domain": "app.leonardo.ai",
            },
            {
                "name": "__Secure-better-auth.session_data.0",
                "value": "session-data-secret",
                "domain": "app.leonardo.ai",
            },
        ],
        client_version="server-cookie-zip-v1",
    )
    return ParsedCookieImportArchive(
        archive_sha256="a" * 64,
        items=(
            ParsedCookieImportItem(
                entry_name="valid@example.test.json",
                entry_sha256="b" * 64,
                expected_login_name="valid@example.test",
                renewal_session=renewal,
                error_code=None,
                error_message=None,
            ),
            ParsedCookieImportItem(
                entry_name="broken@example.test.json",
                entry_sha256="c" * 64,
                expected_login_name="broken@example.test",
                renewal_session=None,
                error_code="ENTRY_JSON_INVALID",
                error_message="Entry is not valid UTF-8 JSON",
            ),
        ),
    )


def _upload_zip() -> bytes:
    payload = {
        "url": "https://app.leonardo.ai",
        "cookies": [
            {
                "name": "__Secure-better-auth.session_token",
                "value": "upload-session-token-secret",
                "domain": "app.leonardo.ai",
                "path": "/",
                "expirationDate": 1_950_000_000,
            },
            {
                "name": "__Secure-better-auth.session_data.0",
                "value": "upload-session-data-secret",
                "domain": "app.leonardo.ai",
                "path": "/",
                "expirationDate": 1_950_000_000,
            },
        ],
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("emails.txt", "upload@example.test\n")
        archive.writestr("leodev_links.txt", "https://leodev.app/?email=upload%40example.test\n")
        archive.writestr("upload@example.test.json", json.dumps(payload))
    return output.getvalue()


def test_cookie_import_admin_routes_are_registered() -> None:
    paths = app.openapi()["paths"]

    assert "post" in paths["/admin/account-cookie-imports"]
    assert "get" in paths["/admin/account-cookie-imports"]
    assert "get" in paths["/admin/account-cookie-imports/{batch_uuid}"]


def test_persists_encrypted_sessions_and_failed_item_without_secret_response() -> None:
    space = Space(
        id=5,
        space_uuid="50000000-0000-0000-0000-000000000001",
        name="cookie-batch-20300101",
        max_concurrency=10,
    )
    session = FakeSession([None, space])
    now = datetime(2030, 1, 1, tzinfo=UTC)

    persisted = asyncio.run(
        persist_cookie_import_batch(
            session,  # type: ignore[arg-type]
            parsed=_parsed_archive(),
            archive_filename="cookies.zip",
            space_name=space.name,
            idempotency_key="idempotency-key-20300101",
            now=now,
        )
    )

    assert persisted.replayed is False
    assert persisted.batch.status == "QUEUED"
    assert persisted.batch.item_count == 2
    assert [item.status for item in persisted.items] == ["QUEUED", "FAILED"]
    assert persisted.items[1].session_ciphertext is None
    assert persisted.items[1].finished_at == now.replace(tzinfo=None)
    plaintext = decrypt_secret(
        bytes(persisted.items[0].session_ciphertext),
        f"{persisted.items[0].item_uuid}:cookie_import_session",
    )
    assert "session-token-secret" in plaintext

    response = cookie_import_batch_view(persisted).model_dump_json()
    assert "session-token-secret" not in response
    assert "session_ciphertext" not in response
    assert "video_token" not in response
    assert '"queued":1' in response
    assert '"failed":1' in response


def test_idempotency_key_conflict_rejects_different_archive() -> None:
    existing = AccountCookieImportBatch(
        id=7,
        batch_uuid="10000000-0000-0000-0000-000000000001",
        idempotency_key="idempotency-key-20300101",
        archive_filename="old.zip",
        archive_sha256="d" * 64,
        space_id=5,
        status="COMPLETED",
        item_count=1,
        created_at=datetime(2030, 1, 1),
        updated_at=datetime(2030, 1, 1),
    )
    session = FakeSession([existing])

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            persist_cookie_import_batch(
                session,  # type: ignore[arg-type]
                parsed=_parsed_archive(),
                archive_filename="cookies.zip",
                space_name="cookie-batch-20300101",
                idempotency_key="idempotency-key-20300101",
                now=datetime(2030, 1, 1, tzinfo=UTC),
            )
        )

    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == "COOKIE_IMPORT_IDEMPOTENCY_CONFLICT"


def test_upload_endpoint_returns_202_no_store_and_never_echoes_cookie() -> None:
    space = Space(
        id=5,
        space_uuid="50000000-0000-0000-0000-000000000001",
        name="cookie-batch-20300101",
        max_concurrency=10,
    )
    session = FakeSession([None, space])

    async def override_session():  # type: ignore[no-untyped-def]
        yield session

    app.dependency_overrides[session_dependency] = override_session
    try:
        with TestClient(app) as client:
            response = client.post(
                "/admin/account-cookie-imports",
                headers={
                    "X-Admin-Key": get_settings().admin_auth_key_value,
                    "Idempotency-Key": "upload-key-20300101",
                },
                data={"space_name": space.name},
                files={"archive": ("cookies.zip", _upload_zip(), "application/zip")},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 202
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["item_count"] == 1
    assert response.json()["queued"] == 1
    assert "upload-session-token-secret" not in response.text
    assert "upload-session-data-secret" not in response.text


def test_upload_endpoint_rejects_wrong_content_type() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/admin/account-cookie-imports",
            headers={
                "X-Admin-Key": get_settings().admin_auth_key_value,
                "Idempotency-Key": "upload-key-20300102",
            },
            data={"space_name": "cookie-batch-20300101"},
            files={"archive": ("cookies.zip", _upload_zip(), "text/plain")},
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "ARCHIVE_CONTENT_TYPE_INVALID"
    assert response.headers["cache-control"] == "no-store"
