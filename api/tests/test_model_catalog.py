from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from video_task_service.api import catalog
from video_task_service.api.main import app
from video_task_service.config import Settings, get_settings
from video_task_service.upstream import LeonardoUpstream


def test_default_leonardo_schema_tracks_active_web_release() -> None:
    assert Settings.model_fields["leonardo_schema_version"].default == "latest"


@pytest.mark.asyncio
async def test_leonardo_client_pins_browser_schema_version() -> None:
    settings = Settings(leonardo_schema_version="1.255.2")
    upstream = LeonardoUpstream(settings)
    try:
        assert upstream._client.headers["x-leo-schema-version"] == "1.255.2"
    finally:
        await upstream.close()


@pytest.mark.asyncio
async def test_homepage_cards_query_is_public_and_parsed() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["body"] = request.read().decode()
        return httpx.Response(
            200,
            json={
                "data": {
                    "homepageCards": [
                        {
                            "id": "card-1",
                            "type": "MODEL",
                            "rank": 2,
                            "title": "Model One",
                            "description": "First model",
                            "url": "/generate?model=model-one",
                            "imageUrl": "https://cdn.example/model-one.jpg",
                            "videoUrl": None,
                        }
                    ]
                }
            },
        )

    upstream = LeonardoUpstream(Settings())
    await upstream._client.aclose()
    upstream._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        cards = await upstream.list_homepage_cards()
    finally:
        await upstream.close()

    assert cards[0]["title"] == "Model One"
    assert "authorization" not in captured["headers"]
    body = json.loads(captured["body"])
    assert body["operationName"] == "HomepageCards"
    assert "homepageCards" in body["query"]


@pytest.mark.asyncio
async def test_models_route_filters_cards_and_extracts_model_slug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeCatalogUpstream:
        closed = False

        async def list_homepage_cards(self) -> list[dict[str, object]]:
            return [
                {
                    "id": "blueprint-1",
                    "type": "BLUEPRINT",
                    "rank": 1,
                    "title": "Blueprint",
                    "description": None,
                    "url": "/blueprints/one",
                    "imageUrl": None,
                    "videoUrl": None,
                },
                {
                    "id": "model-2",
                    "type": "MODEL",
                    "rank": 3,
                    "title": "Model Two",
                    "description": "Second model",
                    "url": "/generate?model=model-two&mode=fast",
                    "imageUrl": "https://cdn.example/model-two.jpg",
                    "videoUrl": None,
                },
                {
                    "id": "model-1",
                    "type": "MODEL",
                    "rank": 2,
                    "title": "Model One",
                    "description": "First model",
                    "url": "/generate?model=model-one",
                    "imageUrl": "https://cdn.example/model-one.jpg",
                    "videoUrl": None,
                },
            ]

        async def close(self) -> None:
            self.closed = True

    fake = FakeCatalogUpstream()
    monkeypatch.setattr(catalog, "create_catalog_upstream", lambda: fake)
    transport = httpx.ASGITransport(app=app)
    headers = {"X-API-Key": get_settings().api_auth_key_value}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/models", headers=headers)
        all_response = await client.get("/v1/models?type=ALL", headers=headers)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, max-age=300"
    payload = response.json()
    assert payload["provider"] == "leonardo"
    assert payload["source"] == "homepageCards+integratedModels"
    assert payload["total"] == 3
    assert [item["model"] for item in payload["items"]] == [
        "model-one",
        "model-two",
        "seed-audio-1.0",
    ]
    seed_audio = payload["items"][-1]
    assert seed_audio["title"] == "Seed Audio 1.0"
    assert seed_audio["url"].startswith("https://app.leonardo.ai/generate?")
    assert all_response.status_code == 200
    assert all_response.json()["total"] == 4
    assert fake.closed is True


def test_integrated_models_do_not_duplicate_upstream_cards() -> None:
    cards = [
        {
            "id": "upstream-seed-audio",
            "type": "MODEL",
            "rank": 7,
            "title": "Seed Audio from upstream",
            "description": None,
            "url": "/generate?model=seed-audio-1.0",
            "imageUrl": None,
            "videoUrl": None,
        }
    ]

    merged = catalog.with_integrated_models(cards)

    assert len(merged) == 1
    assert merged[0]["id"] == "upstream-seed-audio"


@pytest.mark.asyncio
async def test_models_route_requires_api_key() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/models")

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "INVALID_API_KEY"
