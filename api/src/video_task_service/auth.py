from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Header, HTTPException, status

from video_task_service.config import get_settings


def _check(received: str | None, expected: str, scope: str) -> None:
    if received is None or not secrets.compare_digest(received, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_API_KEY", "scope": scope},
        )


async def require_api_key(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    _check(x_api_key, get_settings().api_auth_key_value, "api")


async def require_admin_key(
    x_admin_key: Annotated[str | None, Header(alias="X-Admin-Key")] = None,
) -> None:
    _check(x_admin_key, get_settings().admin_auth_key_value, "admin")


async def require_login_worker_key(
    x_login_worker_key: Annotated[str | None, Header(alias="X-Login-Worker-Key")] = None,
) -> None:
    _check(
        x_login_worker_key,
        get_settings().login_worker_auth_key_value,
        "login_worker",
    )
