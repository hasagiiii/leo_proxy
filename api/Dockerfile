FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt ./

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install -r requirements.txt

COPY pyproject.toml README.md ./
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
COPY scripts ./scripts
COPY tests ./tests

RUN python -m pip install --no-cache-dir --no-deps .

RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

CMD ["uvicorn", "video_task_service.api.main:app", "--host", "0.0.0.0", "--port", "8080"]
