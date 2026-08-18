from __future__ import annotations

import io
import urllib.request
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image
from pydantic import ValidationError

from video_task_service.config import Settings
from video_task_service.h3 import ResolvedMedia, media_specs
from video_task_service.nano_images import (
    NANO_IMAGE_DIMENSIONS,
    NANO_IMAGE_NONE_STYLE_ID,
    build_leonardo_nano_image_request,
)
from video_task_service.pricing import NANO_IMAGE_CREDIT_TABLE, quote_credit_cost
from video_task_service.schemas import TaskCreate
from video_task_service.upstream import MockUpstream


@pytest.mark.parametrize("model", ["nano-banana-2", "nano-banana-pro"])
@pytest.mark.parametrize(
    ("aspect_ratio", "size", "expected"),
    [
        (aspect_ratio, size, dimensions)
        for aspect_ratio, sizes in NANO_IMAGE_DIMENSIONS.items()
        for size, dimensions in sizes.items()
    ],
)
def test_nano_full_dimension_matrix(
    model: str,
    aspect_ratio: str,
    size: str,
    expected: tuple[int, int],
) -> None:
    request = build_leonardo_nano_image_request(
        model=model,
        mode="text-to-image",
        task_input={
            "prompt": "A blue paper airplane",
            "aspect_ratio": aspect_ratio,
            "size": size,
        },
        assets=[],
    )
    parameters = request["parameters"]
    assert (parameters["width"], parameters["height"]) == expected
    assert parameters["quantity"] == 1
    assert parameters["prompt_enhance"] == "OFF"
    assert parameters["style_ids"] == [NANO_IMAGE_NONE_STYLE_ID]
    assert request["public"] is False


@pytest.mark.parametrize("model", ["nano-banana-2", "nano-banana-pro"])
@pytest.mark.parametrize("aspect_ratio", NANO_IMAGE_DIMENSIONS)
@pytest.mark.parametrize("size", ["SMALL", "MEDIUM", "LARGE"])
def test_nano_full_credit_matrix(model: str, aspect_ratio: str, size: str) -> None:
    assert quote_credit_cost(
        model,
        {"aspect_ratio": aspect_ratio, "size": size},
    ) == NANO_IMAGE_CREDIT_TABLE[model][size]


@pytest.mark.parametrize(
    ("model", "upstream_model", "expected_quote"),
    [
        ("nano-banana-2", "nano-banana-2", 80),
        ("nano-banana-pro", "gemini-image-2", 140),
    ],
)
def test_nano_text_task_normalizes_resolution_quote_and_upstream_alias(
    model: str,
    upstream_model: str,
    expected_quote: int,
) -> None:
    task = TaskCreate(
        model=model,
        task_type="IMAGE_GENERATION",
        mode="text-to-image",
        input={"prompt": "A blue paper airplane"},
    )
    assert task.input_document() == {
        "prompt": "A blue paper airplane",
        "aspect_ratio": "1:1",
        "size": "SMALL",
        "resolution": "1024x1024",
    }
    assert task.estimated_credit_cost == expected_quote
    request = build_leonardo_nano_image_request(
        model=task.model,
        mode=task.mode or "",
        task_input=task.input_document(),
        assets=[],
    )
    assert request["model"] == upstream_model
    assert request["public"] is False


@pytest.mark.parametrize(
    "field",
    [
        "quality",
        "prompt_enhance",
        "style",
        "style_ids",
        "quantity",
        "guidances",
        "public",
    ],
)
def test_nano_rejects_fixed_or_internal_fields(field: str) -> None:
    with pytest.raises(ValidationError):
        TaskCreate(
            model="nano-banana-2",
            task_type="IMAGE_GENERATION",
            mode="text-to-image",
            input={"prompt": "test", field: "override"},
        )


def test_nano_rejects_conflicting_resolution() -> None:
    with pytest.raises(ValidationError, match="resolution must match"):
        TaskCreate(
            model="nano-banana-2",
            task_type="IMAGE_GENERATION",
            mode="text-to-image",
            input={
                "prompt": "test",
                "aspect_ratio": "1:1",
                "size": "SMALL",
                "resolution": "2048x2048",
            },
        )


@pytest.mark.parametrize("model", ["nano-banana-2", "nano-banana-pro"])
def test_nano_image_mode_builds_six_ordered_references(model: str) -> None:
    urls = [f"https://cdn.example.com/reference-{index}.png" for index in range(6)]
    task = TaskCreate(
        model=model,
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
    request = build_leonardo_nano_image_request(
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


@pytest.mark.parametrize("model", ["nano-banana-2", "nano-banana-pro"])
def test_nano_models_reject_video_reference_mode(model: str) -> None:
    with pytest.raises(ValidationError):
        TaskCreate(
            model=model,
            task_type="IMAGE_GENERATION",
            mode="reference-to-image",
            input={
                "prompt": "test",
                "reference_video_urls": ["https://cdn.example.com/reference.mp4"],
            },
        )


@pytest.mark.parametrize("count", [0, 7])
def test_nano_image_mode_requires_one_to_six_references(count: int) -> None:
    with pytest.raises(ValidationError):
        TaskCreate(
            model="nano-banana-pro",
            task_type="IMAGE_GENERATION",
            mode="image-to-image",
            input={
                "prompt": "test",
                "reference_image_urls": [
                    f"https://cdn.example.com/{index}.png" for index in range(count)
                ],
            },
        )


def test_nano_requires_image_task_type_and_exposes_openapi_models() -> None:
    with pytest.raises(ValidationError, match="task_type=IMAGE_GENERATION"):
        TaskCreate(
            model="nano-banana-2",
            mode="text-to-image",
            input={"prompt": "test"},
        )
    schema = TaskCreate.model_json_schema()
    references = {
        item.get("$ref", "").rsplit("/", 1)[-1]
        for item in schema["properties"]["input"]["anyOf"]
    }
    assert {
        "NanoImageTextToImageInput",
        "NanoImageImageToImageInput",
    } <= references
    assert "NanoImageReferenceToImageInput" not in references


@pytest.mark.asyncio
async def test_mock_upstream_returns_downloadable_requested_image_dimensions() -> None:
    upstream = MockUpstream(Settings(mock_generation_seconds=0.1))
    request = build_leonardo_nano_image_request(
        model="nano-banana-2",
        mode="text-to-image",
        task_input={
            "prompt": "test",
            "aspect_ratio": "16:9",
            "size": "SMALL",
        },
        assets=[],
    )
    submitted = await upstream.submit(
        token="mock-token",
        model="nano-banana-2",
        task_input={"request": request},
    )
    result = await upstream.poll(
        token="mock-token",
        generation_id=submitted.generation_id,
        submitted_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1),
        estimated_credit_cost=80,
    )

    assert result.status == "COMPLETE"
    assert result.actual_credit_cost == 80
    media = result.output["media"][0]
    assert (media["width"], media["height"]) == (1376, 768)
    with urllib.request.urlopen(media["url"]) as response:
        content = response.read()
        assert response.headers.get_content_type() == "image/png"
    with Image.open(io.BytesIO(content)) as image:
        assert image.size == (1376, 768)
