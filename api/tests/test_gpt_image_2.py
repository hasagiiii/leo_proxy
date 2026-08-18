from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import ValidationError

from video_task_service.config import Settings
from video_task_service.gpt_image_2 import (
    GPT_IMAGE_2_DIMENSIONS,
    GPT_IMAGE_2_NONE_STYLE_ID,
    build_leonardo_gpt_image_2_request,
)
from video_task_service.h3 import ResolvedMedia, media_specs
from video_task_service.pricing import GPT_IMAGE_2_CREDIT_TABLE, quote_credit_cost
from video_task_service.schemas import TaskCreate
from video_task_service.upstream import LeonardoUpstream


@pytest.mark.parametrize(
    ("aspect_ratio", "size", "expected"),
    [
        (aspect_ratio, size, dimensions)
        for aspect_ratio, sizes in GPT_IMAGE_2_DIMENSIONS.items()
        for size, dimensions in sizes.items()
    ],
)
def test_gpt_image_2_dimension_matrix(
    aspect_ratio: str,
    size: str,
    expected: tuple[int, int],
) -> None:
    request = build_leonardo_gpt_image_2_request(
        model="gpt-image-2",
        mode="text-to-image",
        task_input={
            "prompt": "A blue paper airplane",
            "quality": "MEDIUM",
            "aspect_ratio": aspect_ratio,
            "size": size,
        },
        assets=[],
    )
    parameters = request["parameters"]
    assert (parameters["width"], parameters["height"]) == expected
    assert parameters["quantity"] == 1
    assert parameters["prompt_enhance"] == "OFF"
    assert parameters["style_ids"] == [GPT_IMAGE_2_NONE_STYLE_ID]


@pytest.mark.parametrize(
    ("quality", "aspect_ratio", "size", "expected"),
    [
        (quality, aspect_ratio, size, prices[index])
        for quality, aspects in GPT_IMAGE_2_CREDIT_TABLE.items()
        for aspect_ratio, prices in aspects.items()
        for index, size in enumerate(("SMALL", "MEDIUM", "LARGE"))
    ],
)
def test_gpt_image_2_full_browser_credit_matrix(
    quality: str,
    aspect_ratio: str,
    size: str,
    expected: int,
) -> None:
    assert (
        quote_credit_cost(
            "gpt-image-2",
            {"quality": quality, "aspect_ratio": aspect_ratio, "size": size},
        )
        == expected
    )


def test_gpt_image_2_text_task_normalizes_resolution_and_quote() -> None:
    task = TaskCreate(
        model="gpt-image-2",
        task_type="IMAGE_GENERATION",
        mode="text-to-image",
        input={"prompt": "A blue paper airplane"},
    )
    assert task.input == {
        "prompt": "A blue paper airplane",
        "quality": "MEDIUM",
        "aspect_ratio": "1:1",
        "size": "SMALL",
        "resolution": "1024x1024",
    }
    assert task.estimated_credit_cost == 65


def test_gpt_image_2_requires_image_task_type_and_exposes_openapi_models() -> None:
    with pytest.raises(ValidationError, match="task_type=IMAGE_GENERATION"):
        TaskCreate(
            model="gpt-image-2",
            mode="text-to-image",
            input={"prompt": "test"},
        )
    schema = TaskCreate.model_json_schema()
    references = {
        item.get("$ref", "").rsplit("/", 1)[-1]
        for item in schema["properties"]["input"]["anyOf"]
    }
    assert {"GPTImage2TextToImageInput", "GPTImage2ImageToImageInput"} <= references


@pytest.mark.parametrize("field", ["prompt_enhance", "style", "style_ids", "quantity"])
def test_gpt_image_2_rejects_fixed_or_internal_fields(field: str) -> None:
    with pytest.raises(ValidationError):
        TaskCreate(
            model="gpt-image-2",
            task_type="IMAGE_GENERATION",
            mode="text-to-image",
            input={"prompt": "test", field: "override"},
        )


def test_gpt_image_2_rejects_resolution_that_conflicts_with_size() -> None:
    with pytest.raises(ValidationError, match="resolution must match"):
        TaskCreate(
            model="gpt-image-2",
            task_type="IMAGE_GENERATION",
            mode="text-to-image",
            input={
                "prompt": "test",
                "aspect_ratio": "1:1",
                "size": "SMALL",
                "resolution": "2048x2048",
            },
        )


def test_gpt_image_2_image_mode_builds_six_ordered_references() -> None:
    urls = [f"https://cdn.example.com/reference-{index}.png" for index in range(6)]
    task = TaskCreate(
        model="gpt-image-2",
        task_type="IMAGE_GENERATION",
        mode="image-to-image",
        input={"prompt": "Follow the references", "reference_image_urls": urls},
    )
    specs = media_specs(task.mode or "", task.input_document())
    assert [item.role for item in specs] == ["REFERENCE_IMAGE"] * 6
    assets = [
        ResolvedMedia("IMAGE", "REFERENCE_IMAGE", index, url, f"image-{index}")
        for index, url in reversed(list(enumerate(urls)))
    ]
    request = build_leonardo_gpt_image_2_request(
        model=task.model,
        mode=task.mode or "",
        task_input=task.input_document(),
        assets=assets,
    )
    references = request["parameters"]["guidances"]["image_reference"]
    assert [item["image"]["id"] for item in references] == [
        f"image-{index}" for index in range(6)
    ]
    assert {item["strength"] for item in references} == {"MID"}
    assert task.estimated_credit_cost == 65


def test_gpt_image_2_image_mode_requires_one_to_six_references() -> None:
    for count in (0, 7):
        with pytest.raises(ValidationError):
            TaskCreate(
                model="gpt-image-2",
                task_type="IMAGE_GENERATION",
                mode="image-to-image",
                input={
                    "prompt": "test",
                    "reference_image_urls": [
                        f"https://cdn.example.com/{index}.png" for index in range(count)
                    ],
                },
            )


@pytest.mark.asyncio
async def test_leonardo_poll_returns_completed_image_media() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        body = json.loads(request.read())
        if body["operationName"] == "GetAIGenerationFeedStatuses":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "generations": [
                            {
                                "id": "image-generation",
                                "status": "COMPLETE",
                                "nsfw": False,
                                "flagged": False,
                            }
                        ],
                        "generationNotes": [],
                    }
                },
            )
        assert body["operationName"] == "GetGenerationFeed"
        return httpx.Response(
            200,
            json={
                "data": {
                    "generations": [
                        {
                            "generated_images": [
                                {
                                    "id": "image-1",
                                    "url": "https://cdn.example.com/result.jpg",
                                    "motionMP4URL": None,
                                    "motionGIFURL": None,
                                    "image_width": 1024,
                                    "image_height": 1024,
                                }
                            ]
                        }
                    ]
                }
            },
        )

    upstream = LeonardoUpstream(Settings())
    await upstream._client.aclose()
    upstream._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await upstream.poll(
            token="token",
            generation_id="image-generation",
            submitted_at=datetime.now(UTC).replace(tzinfo=None),
            estimated_credit_cost=65,
        )
    finally:
        await upstream.close()

    assert calls == 2
    assert result.status == "COMPLETE"
    assert result.actual_credit_cost == 65
    assert result.output == {
        "provider": "leonardo",
        "generation_id": "image-generation",
        "media": [
            {
                "id": "image-1",
                "type": "image/jpeg",
                "url": "https://cdn.example.com/result.jpg",
                "width": 1024,
                "height": 1024,
            }
        ],
    }
