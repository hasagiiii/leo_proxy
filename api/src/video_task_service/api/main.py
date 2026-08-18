from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from video_task_service.api.account_cookie_imports import router as cookie_imports_router
from video_task_service.api.accounts import router as accounts_router
from video_task_service.api.accounts import spaces_router
from video_task_service.api.catalog import router as catalog_router
from video_task_service.api.protocol_renewals import router as protocol_renewals_router
from video_task_service.api.stats import router as stats_router
from video_task_service.api.tasks import router as tasks_router
from video_task_service.db import dispose_engine, session_factory
from video_task_service.logging_config import configure_logging

configure_logging()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await dispose_engine()


app = FastAPI(
    title="LEO Proxy API",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(tasks_router, prefix="/v1")
app.include_router(catalog_router, prefix="/v1")
app.include_router(accounts_router, prefix="/admin")
app.include_router(cookie_imports_router, prefix="/admin")
app.include_router(spaces_router, prefix="/admin")
app.include_router(stats_router, prefix="/admin")
app.include_router(protocol_renewals_router, prefix="/admin")


@app.get("/health/live", tags=["health"])
async def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
async def ready() -> dict[str, str]:
    async with session_factory() as session:
        await session.execute(text("SELECT 1"))
    return {"status": "ready"}
