from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from video_task_service.auth import require_admin_key
from video_task_service.db import session_dependency
from video_task_service.registration_monitor import (
    MonitorWindow,
    aggregate_client_metrics,
    aggregate_client_rows,
    choose_bucket_seconds,
    client_activity_statement,
    client_catalog_statement,
    client_list_item,
    client_metric_statement,
    client_summary,
    latest_error_statement,
    normalize_monitor_window,
    recent_terminal_statement,
    row_is_stalled,
    series_points,
    task_view,
)
from video_task_service.schemas import (
    ClientRegistrationTaskList,
    RegistrationClientDetailResponse,
    RegistrationClientListItem,
    RegistrationClientListResponse,
    RegistrationMonitorWindow,
)

router = APIRouter(
    tags=["registration-client-monitor"],
    dependencies=[Depends(require_admin_key)],
)

HEALTH_ORDER = {"ABNORMAL": 0, "ATTENTION": 1, "NORMAL": 2, "NO_ACTIVITY": 3}
ALLOWED_HEALTH = frozenset(HEALTH_ORDER)
ALLOWED_STATUS = frozenset(
    {
        "RUNNING",
        "COOKIE_REPORTED",
        "VALIDATING",
        "VALIDATION_RETRY_WAIT",
        "VALIDATION_FAILED",
        "FAILED",
        "SUCCEEDED",
        "PROCESSING",
        "STALLED",
    }
)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _window_or_422(
    from_value: datetime | None,
    to_value: datetime | None,
    server_now: datetime,
) -> MonitorWindow:
    try:
        return normalize_monitor_window(from_value, to_value, now=server_now)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "REGISTRATION_MONITOR_WINDOW_INVALID", "message": str(exc)},
        ) from exc


def _monitor_window(window: MonitorWindow) -> RegistrationMonitorWindow:
    return RegistrationMonitorWindow(from_=window.from_at, to=window.to_at)


async def _mappings(session: AsyncSession, statement: Any) -> list[dict[str, Any]]:
    result = await session.execute(statement)
    return [dict(row) for row in result.mappings().all()]


async def _catalog(session: AsyncSession, client_id: str | None = None) -> list[dict[str, Any]]:
    return await _mappings(session, client_catalog_statement(client_id))


def _client_not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={
            "code": "REGISTRATION_CLIENT_NOT_FOUND",
            "message": "registration client was not found",
        },
    )


def _validate_health(value: str | None) -> str | None:
    normalized = (value or "").strip().upper()
    if not normalized:
        return None
    if normalized not in ALLOWED_HEALTH:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "REGISTRATION_CLIENT_HEALTH_INVALID",
                "message": "health filter is invalid",
            },
        )
    return normalized


def _validate_status(value: str | None) -> str | None:
    normalized = (value or "").strip().upper()
    if not normalized:
        return None
    if normalized not in ALLOWED_STATUS:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "REGISTRATION_CLIENT_STATUS_INVALID",
                "message": "status filter is invalid",
            },
        )
    return normalized


@router.get("/registration-clients", response_model=RegistrationClientListResponse)
async def list_registration_clients(
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    health: str | None = None,
    search: str | None = Query(default=None, max_length=128),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> RegistrationClientListResponse:
    _no_store(response)
    server_now = _now()
    window = _window_or_422(from_, to, server_now)
    normalized_health = _validate_health(health)
    aggregate_rows = await _mappings(session, client_metric_statement(window, server_now))
    terminal_rows = await _mappings(session, recent_terminal_statement(window))
    error_rows = await _mappings(session, latest_error_statement(window))
    metrics = aggregate_client_metrics(aggregate_rows, terminal_rows, error_rows)
    all_items = [client_list_item(value) for value in metrics.values()]
    summary = client_summary(all_items)
    needle = (search or "").strip().lower()
    filtered = [
        item
        for item in all_items
        if (normalized_health is None or item.health == normalized_health)
        and (not needle or needle in item.client_id.lower() or needle in item.display_name.lower())
    ]
    filtered.sort(
        key=lambda item: (
            HEALTH_ORDER[item.health],
            -(item.last_activity_at.timestamp() if item.last_activity_at else 0),
            item.client_id,
        )
    )
    total = len(filtered)
    return RegistrationClientListResponse(
        server_now=server_now,
        window=_monitor_window(window),
        summary=summary,
        items=filtered[offset : offset + limit],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/registration-clients/{client_id}/registrations",
    response_model=ClientRegistrationTaskList,
)
async def list_registration_client_tasks(
    client_id: Annotated[str, Path(min_length=1, max_length=128)],
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    status: str | None = None,
    search: str | None = Query(default=None, max_length=255),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ClientRegistrationTaskList:
    _no_store(response)
    server_now = _now()
    window = _window_or_422(from_, to, server_now)
    normalized_status = _validate_status(status)
    catalog = await _catalog(session, client_id)
    if not catalog:
        raise _client_not_found()
    statement = client_activity_statement(
        window,
        client_id=client_id,
        status=normalized_status,
        search=search,
    )
    if normalized_status == "STALLED":
        rows = await _mappings(session, statement)
        rows = [row for row in rows if row_is_stalled(row, server_now)]
        total = len(rows)
        page = rows[offset : offset + limit]
    else:
        count_statement = select(func.count()).select_from(statement.order_by(None).subquery())
        total = int((await session.execute(count_statement)).scalar_one())
        page = await _mappings(session, statement.limit(limit).offset(offset))
    return ClientRegistrationTaskList(
        items=[task_view(row, server_now) for row in page],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/registration-clients/{client_id}",
    response_model=RegistrationClientDetailResponse,
)
async def get_registration_client_detail(
    client_id: Annotated[str, Path(min_length=1, max_length=128)],
    response: Response,
    session: Annotated[AsyncSession, Depends(session_dependency)],
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
) -> RegistrationClientDetailResponse:
    _no_store(response)
    server_now = _now()
    window = _window_or_422(from_, to, server_now)
    client_catalog = await _catalog(session, client_id)
    if not client_catalog:
        raise _client_not_found()
    rows = await _mappings(session, client_activity_statement(window, client_id=client_id))
    metrics = aggregate_client_rows(rows, client_catalog, window, server_now)
    item: RegistrationClientListItem = client_list_item(metrics[client_id])
    bucket_seconds = choose_bucket_seconds(window)
    return RegistrationClientDetailResponse(
        server_now=server_now,
        window=_monitor_window(window),
        client=item,
        series=series_points(rows, window, bucket_seconds),
    )
