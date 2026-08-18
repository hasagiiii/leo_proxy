import itertools

import pytest

from video_task_service.h3 import ResolvedMedia
from video_task_service.pricing import quote_credit_cost
from video_task_service.schemas import TaskCreate
from video_task_service.veo_3_1 import (
    VEO_3_1_DIMENSIONS,
    build_leonardo_veo_3_1_request,
)


@pytest.mark.parametrize(
    ("duration", "resolution", "aspect_ratio", "audio"),
    itertools.product(
        (4, 6, 8),
        ("720P", "1080P", "4K"),
        ("16:9", "9:16"),
        (False, True),
    ),
)
def test_veo_3_1_text_matrix(
    duration: int,
    resolution: str,
    aspect_ratio: str,
    audio: bool,
) -> None:
    request = build_leonardo_veo_3_1_request(
        model="veo-3.1-generate-001",
        mode="text-to-video",
        task_input={
            "prompt": "A paper boat on a lake",
            "duration": duration,
            "resolution": resolution,
            "aspect_ratio": aspect_ratio,
            "audio": audio,
        },
        assets=[],
    )
    parameters = request["parameters"]
    assert request["public"] is False
    assert parameters["quantity"] == 1
    assert parameters["duration"] == duration
    assert parameters["motion_has_audio"] is audio
    assert (parameters["width"], parameters["height"]) == VEO_3_1_DIMENSIONS[
        resolution
    ][aspect_ratio]


@pytest.mark.parametrize(
    ("duration", "resolution", "audio", "expected"),
    [
        (4, "720P", False, 800),
        (6, "720P", False, 1200),
        (8, "720P", False, 1600),
        (4, "1080P", False, 800),
        (6, "1080P", False, 1200),
        (8, "1080P", False, 1600),
        (4, "4K", False, 1600),
        (6, "4K", False, 2400),
        (8, "4K", False, 3200),
        (4, "720P", True, 1600),
        (6, "720P", True, 2400),
        (8, "720P", True, 3200),
        (4, "1080P", True, 1600),
        (6, "1080P", True, 2400),
        (8, "1080P", True, 3200),
        (4, "4K", True, 2400),
        (6, "4K", True, 3600),
        (8, "4K", True, 4800),
    ],
)
def test_veo_3_1_pricing_matrix(
    duration: int,
    resolution: str,
    audio: bool,
    expected: int,
) -> None:
    quotes = {
        quote_credit_cost(
            "veo-3.1-generate-001",
            {
                "duration": duration,
                "resolution": resolution,
                "aspect_ratio": aspect,
                "audio": audio,
            },
        )
        for aspect in ("16:9", "9:16")
    }
    assert quotes == {expected}


def test_veo_3_1_image_request_maps_start_and_end_frames() -> None:
    assets = [
        ResolvedMedia("IMAGE", "END_FRAME", 0, "https://a/end.png", "end-id"),
        ResolvedMedia("IMAGE", "START_FRAME", 0, "https://a/start.png", "start-id"),
    ]
    request = build_leonardo_veo_3_1_request(
        model="veo-3.1-generate-001",
        mode="image-to-video",
        task_input={
            "prompt": "Move between the frames",
            "duration": 6,
            "resolution": "1080P",
            "aspect_ratio": "9:16",
            "audio": False,
            "negative_prompt": "camera shake",
            "seed": 42,
        },
        assets=assets,
    )
    parameters = request["parameters"]
    assert parameters["negative_prompt"] == "camera shake"
    assert parameters["seed"] == 42
    assert parameters["guidances"] == {
        "start_frame": [{"image": {"id": "start-id", "type": "UPLOADED"}}],
        "end_frame": [{"image": {"id": "end-id", "type": "UPLOADED"}}],
    }


@pytest.mark.parametrize("strength", ["LOW", "MID", "HIGH"])
def test_veo_3_1_reference_images_are_ordered(strength: str) -> None:
    assets = [
        ResolvedMedia("IMAGE", "REFERENCE_IMAGE", 1, "https://a/i2.png", "i2"),
        ResolvedMedia("IMAGE", "REFERENCE_IMAGE", 0, "https://a/i1.png", "i1"),
    ]
    request = build_leonardo_veo_3_1_request(
        model="veo-3.1-generate-001",
        mode="reference-to-video",
        task_input={
            "prompt": "Follow the references",
            "reference_strength": strength,
        },
        assets=assets,
    )
    references = request["parameters"]["guidances"]["image_reference"]
    assert [item["image"]["id"] for item in references] == ["i1", "i2"]
    assert {item["strength"] for item in references} == {strength}


def test_veo_3_1_task_contract_normalizes_and_quotes() -> None:
    task = TaskCreate(
        model="veo-3.1-generate-001",
        task_type="VIDEO_GENERATION",
        mode="text-to-video",
        input={
            "prompt": "A tiny blue robot waves",
            "duration": 4,
            "resolution": "720P",
            "aspect_ratio": "16:9",
            "audio": False,
        },
    )
    assert task.estimated_credit_cost == 800
    assert task.input_document()["audio"] is False


@pytest.mark.parametrize("duration", [3, 5, 7, 9])
def test_veo_3_1_rejects_unsupported_duration(duration: int) -> None:
    with pytest.raises(ValueError):
        TaskCreate(
            model="veo-3.1-generate-001",
            mode="text-to-video",
            input={"prompt": "move", "duration": duration},
        )


def test_veo_3_1_rejects_end_frame_without_start_frame() -> None:
    with pytest.raises(ValueError):
        TaskCreate(
            model="veo-3.1-generate-001",
            mode="text-to-video",
            input={
                "prompt": "move",
                "end_image_url": "https://cdn.example.com/end.png",
            },
        )


def test_veo_3_1_rejects_too_many_references() -> None:
    with pytest.raises(ValueError):
        TaskCreate(
            model="veo-3.1-generate-001",
            mode="reference-to-video",
            input={
                "prompt": "move",
                "reference_image_urls": [
                    f"https://cdn.example.com/{index}.png" for index in range(4)
                ],
            },
        )


def test_veo_3_1_builder_requires_resolved_start_frame() -> None:
    with pytest.raises(ValueError, match="resolved start frame"):
        build_leonardo_veo_3_1_request(
            model="veo-3.1-generate-001",
            mode="image-to-video",
            task_input={"prompt": "move"},
            assets=[],
        )
