import pytest

from video_task_service.gemini_omni_flash import (
    GEMINI_OMNI_FLASH_DIMENSIONS,
    build_leonardo_gemini_omni_flash_request,
)
from video_task_service.h3 import ResolvedMedia
from video_task_service.schemas import TaskCreate


@pytest.mark.parametrize(
    ("aspect_ratio", "expected"), list(GEMINI_OMNI_FLASH_DIMENSIONS.items())
)
def test_gemini_omni_flash_dimensions(
    aspect_ratio: str, expected: tuple[int, int]
) -> None:
    request = build_leonardo_gemini_omni_flash_request(
        model="gemini-omni-flash",
        mode="text-to-video",
        task_input={
            "prompt": "A paper kite above a meadow",
            "duration": 3,
            "resolution": "720P",
            "aspect_ratio": aspect_ratio,
        },
        assets=[],
    )
    assert request["public"] is False
    assert request["parameters"]["quantity"] == 1
    assert (request["parameters"]["width"], request["parameters"]["height"]) == expected
    assert "mode" not in request["parameters"]


def test_gemini_omni_flash_reference_images_are_ordered() -> None:
    assets = [
        ResolvedMedia("IMAGE", "REFERENCE_IMAGE", 1, "https://a/i2.png", "i2"),
        ResolvedMedia("IMAGE", "REFERENCE_IMAGE", 0, "https://a/i1.png", "i1"),
    ]
    request = build_leonardo_gemini_omni_flash_request(
        model="gemini-omni-flash",
        mode="reference-to-video",
        task_input={
            "prompt": "Follow the image references",
            "duration": 5,
            "resolution": "720P",
            "aspect_ratio": "9:16",
        },
        assets=assets,
    )
    references = request["parameters"]["guidances"]["image_reference"]
    assert [item["image"]["id"] for item in references] == ["i1", "i2"]
    assert {item["strength"] for item in references} == {"MID"}


def test_gemini_omni_flash_task_contract_and_quote() -> None:
    task = TaskCreate(
        model="gemini-omni-flash",
        task_type="VIDEO_GENERATION",
        mode="text-to-video",
        input={
            "prompt": "A tiny blue robot waves",
            "duration": 10,
            "resolution": "720P",
            "aspect_ratio": "16:9",
        },
    )
    assert task.estimated_credit_cost == 1000


def test_gemini_omni_flash_omni_alias_normalizes() -> None:
    task = TaskCreate(
        model="gemini-omni-flash",
        task_type="VIDEO_GENERATION",
        mode="omni",
        input={
            "prompt": "Animate the reference",
            "duration": 3,
            "resolution": "720P",
            "aspect_ratio": "9:16",
            "reference_image_urls": ["https://cdn.example.com/reference.png"],
        },
    )
    assert task.mode == "reference-to-video"
    assert task.estimated_credit_cost == 300


def test_gemini_omni_flash_rejects_start_frame_mode() -> None:
    with pytest.raises(ValueError, match="text-to-video or reference-to-video"):
        TaskCreate(
            model="gemini-omni-flash",
            task_type="VIDEO_GENERATION",
            mode="image-to-video",
            input={
                "prompt": "move",
                "duration": 3,
                "resolution": "720P",
                "aspect_ratio": "16:9",
                "image_url": "https://cdn.example.com/start.png",
            },
        )
