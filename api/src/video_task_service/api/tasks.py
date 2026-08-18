from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from video_task_service.auth import require_api_key
from video_task_service.db import session_dependency
from video_task_service.gemini_omni_flash import is_gemini_omni_flash_model
from video_task_service.gpt_image_2 import is_gpt_image_2_model
from video_task_service.h3 import media_specs
from video_task_service.kling_o3 import is_kling_o3_model
from video_task_service.models import Account, Space, Task, TaskEvent, TaskQueue
from video_task_service.nano_images import is_nano_image_model
from video_task_service.schemas import TaskCreate, TaskList, TaskProgress, TaskView
from video_task_service.seed_audio import (
    SEED_AUDIO_SCHEMA_VERSION,
    is_seed_audio_model,
)
from video_task_service.seedance import is_seedance_model, seedance_input_schema_version
from video_task_service.veo_3_1 import is_veo_3_1_model, veo_3_1_schema_version

router = APIRouter(
    prefix="/tasks",
    tags=["tasks"],
    dependencies=[Depends(require_api_key)],
)


def task_filter_conditions(
    task_status: str | None,
    task_model: str | None,
) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = []
    if task_status:
        conditions.append(Task.status == task_status.upper())
    if task_model:
        conditions.append(Task.model == task_model)
    return conditions


def request_hash(body: TaskCreate) -> str:
    canonical = json.dumps(
        body.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def task_view(
    task: Task,
    account_uuid: str | None = None,
    space_uuid: str | None = None,
) -> TaskView:
    return TaskView(
        task_uuid=UUID(task.task_uuid),
        idempotency_key=task.idempotency_key,
        provider=task.provider,
        upstream_task_id=task.upstream_task_id,
        account_uuid=UUID(account_uuid) if account_uuid else None,
        space_uuid=UUID(space_uuid) if space_uuid else None,
        task_type=task.task_type,
        model=task.model,
        mode=task.mode,
        input_schema_version=task.input_schema_version,
        input=task.input_json,
        output=task.output_json,
        status=task.status,
        priority=task.priority,
        estimated_credit_cost=task.estimated_credit_cost,
        reserved_credit_cost=task.reserved_credit_cost,
        actual_credit_cost=task.actual_credit_cost,
        submit_attempts=task.submit_attempts,
        sync_attempts=task.sync_attempts,
        error_code=task.error_code,
        error_message=task.error_message,
        progress=TaskProgress(
            phase=task.status,
            resolved_assets=task.media_resolved,
            total_assets=task.media_total,
        ),
        created_at=task.created_at,
        queued_at=task.queued_at,
        assigned_at=task.assigned_at,
        upstream_submitted_at=task.upstream_submitted_at,
        finished_at=task.finished_at,
        updated_at=task.updated_at,
    )


async def load_task_view(session: AsyncSession, task_uuid: str) -> TaskView | None:
    row = (
        await session.execute(
            select(Task, Account.account_uuid, Space.space_uuid)
            .outerjoin(Account, Account.id == Task.account_id)
            .outerjoin(Space, Space.id == Task.space_id)
            .where(Task.task_uuid == task_uuid)
        )
    ).one_or_none()
    if row is None:
        return None
    return task_view(row[0], row[1], row[2])


@router.post("", response_model=TaskView, status_code=status.HTTP_202_ACCEPTED)
async def create_task(
    body: TaskCreate,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=128),
    ],
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> TaskView:
    digest = request_hash(body)
    task_uuid = str(uuid4())
    now = datetime.now(UTC).replace(tzinfo=None)
    input_document = body.input_document()
    typed_media = media_specs(body.mode, input_document) if body.mode is not None else []
    input_schema_version = "legacy"
    if body.mode is not None:
        if is_seedance_model(body.model):
            input_schema_version = seedance_input_schema_version(body.model)
        elif is_gemini_omni_flash_model(body.model):
            input_schema_version = "gemini-omni-flash.v1"
        elif is_kling_o3_model(body.model):
            input_schema_version = "kling-o3.v1"
        elif is_gpt_image_2_model(body.model):
            input_schema_version = "gpt-image-2.v1"
        elif is_nano_image_model(body.model):
            input_schema_version = "nano-image.v1"
        elif is_seed_audio_model(body.model):
            input_schema_version = SEED_AUDIO_SCHEMA_VERSION
        elif is_veo_3_1_model(body.model):
            input_schema_version = veo_3_1_schema_version(body.model)
        else:
            input_schema_version = "h3.v1"
    task = Task(
        task_uuid=task_uuid,
        idempotency_key=idempotency_key,
        request_hash=digest,
        provider=body.provider,
        task_type=body.task_type,
        model=body.model,
        mode=body.mode,
        input_schema_version=input_schema_version,
        input_json=input_document,
        status="QUEUED",
        priority=body.priority,
        estimated_credit_cost=body.estimated_credit_cost,
        media_total=len(typed_media),
        media_resolved=0,
        queued_at=now,
    )
    try:
        async with session.begin():
            existing = await session.scalar(
                select(Task).where(Task.idempotency_key == idempotency_key)
            )
            if existing is not None:
                if existing.request_hash != digest:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "IDEMPOTENCY_CONFLICT",
                            "message": "idempotency key was used with a different request",
                        },
                    )
                loaded = await load_task_view(session, existing.task_uuid)
                assert loaded is not None
                return loaded
            session.add(task)
            await session.flush()
            session.add(
                TaskQueue(
                    task_id=task.id,
                    queue_status="READY",
                    priority=body.priority,
                    available_at=now,
                )
            )
            session.add(
                TaskEvent(
                    event_uuid=str(uuid4()),
                    task_id=task.id,
                    event_type="TASK_CREATED",
                    from_status=None,
                    to_status="QUEUED",
                    actor_type="API",
                )
            )
    except IntegrityError as exc:
        repeated = await session.scalar(select(Task).where(Task.idempotency_key == idempotency_key))
        if repeated is not None and repeated.request_hash == digest:
            loaded = await load_task_view(session, repeated.task_uuid)
            assert loaded is not None
            return loaded
        raise HTTPException(
            status_code=409,
            detail={"code": "IDEMPOTENCY_CONFLICT", "message": "idempotency conflict"},
        ) from exc
    loaded = await load_task_view(session, task_uuid)
    assert loaded is not None
    return loaded


@router.get("/{task_uuid}", response_model=TaskView)
async def get_task(
    task_uuid: UUID,
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> TaskView:
    loaded = await load_task_view(session, str(task_uuid))
    if loaded is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "TASK_NOT_FOUND", "message": "task was not found"},
        )
    return loaded


@router.get("", response_model=TaskList)
async def list_tasks(
    session: Annotated[AsyncSession, Depends(session_dependency)],
    task_status: Annotated[str | None, Query(alias="status")] = None,
    task_model: Annotated[
        str | None,
        Query(alias="model", min_length=1, max_length=64),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TaskList:
    conditions = task_filter_conditions(task_status, task_model)
    statement = (
        select(Task, Account.account_uuid, Space.space_uuid)
        .outerjoin(Account, Account.id == Task.account_id)
        .outerjoin(Space, Space.id == Task.space_id)
        .where(*conditions)
        .order_by(Task.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(statement)).all()
    total = await session.scalar(select(func.count(Task.id)).where(*conditions))
    models = (
        await session.scalars(select(Task.model).distinct().order_by(Task.model.asc()))
    ).all()
    return TaskList(
        items=[task_view(row[0], row[1], row[2]) for row in rows],
        total=int(total or 0),
        models=list(models),
    )


@router.post("/{task_uuid}/cancel", response_model=TaskView)
async def cancel_task(
    task_uuid: UUID,
    session: Annotated[AsyncSession, Depends(session_dependency)],
) -> TaskView:
    now = datetime.now(UTC).replace(tzinfo=None)
    async with session.begin():
        task = await session.scalar(
            select(Task).where(Task.task_uuid == str(task_uuid)).with_for_update()
        )
        if task is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "TASK_NOT_FOUND", "message": "task was not found"},
            )
        if task.status not in {"QUEUED", "WAITING_ACCOUNT", "RETRY_WAIT"}:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "TASK_NOT_CANCELLABLE",
                    "message": "only tasks not submitted upstream can be canceled",
                },
            )
        previous = task.status
        task.status = "CANCELED"
        task.canceled_at = now
        task.finished_at = now
        task.version += 1
        queue = await session.get(TaskQueue, task.id, with_for_update=True)
        if queue is not None:
            queue.queue_status = "DONE"
            queue.lease_owner = None
            queue.lease_until = None
        session.add(
            TaskEvent(
                event_uuid=str(uuid4()),
                task_id=task.id,
                event_type="TASK_CANCELED",
                from_status=previous,
                to_status="CANCELED",
                actor_type="API",
            )
        )
    loaded = await load_task_view(session, str(task_uuid))
    assert loaded is not None
    return loaded
