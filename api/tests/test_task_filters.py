import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.dialects import mysql

from video_task_service.api.main import app
from video_task_service.api.tasks import task_filter_conditions
from video_task_service.models import Task


def test_task_filter_conditions_combine_status_and_exact_model() -> None:
    statement = select(Task.id).where(
        *task_filter_conditions("completed", "gpt-image-2")
    )
    compiled = str(
        statement.compile(dialect=mysql.dialect(), compile_kwargs={"literal_binds": True})
    )

    assert "tasks.status = 'COMPLETED'" in compiled
    assert "tasks.model = 'gpt-image-2'" in compiled


def test_task_filter_conditions_allow_unfiltered_task_history() -> None:
    assert task_filter_conditions(None, None) == []


def test_task_model_history_index_matches_filter_and_sort() -> None:
    indexes = {
        index.name: tuple(column.name for column in index.columns)
        for index in Task.__table__.indexes
    }

    assert indexes["idx_tasks_model_created"] == ("model", "created_at")


@pytest.mark.asyncio
async def test_task_list_openapi_exposes_model_filter_and_model_options() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/openapi.json")

    assert response.status_code == 200
    document = response.json()
    parameters = document["paths"]["/v1/tasks"]["get"]["parameters"]
    assert any(parameter["name"] == "model" for parameter in parameters)
    task_list_schema = document["components"]["schemas"]["TaskList"]
    assert task_list_schema["properties"]["models"]["items"]["type"] == "string"
    assert "models" in task_list_schema["required"]
