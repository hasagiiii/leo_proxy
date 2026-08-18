import itertools

import pytest

from video_task_service.h3 import ResolvedMedia
from video_task_service.pricing import quote_credit_cost
from video_task_service.schemas import TaskCreate
from video_task_service.veo_3_1 import (
    VEO_3_1_DIMENSIONS,
    VEO_3_1_LITE_MODEL,
    build_leonardo_veo_3_1_request,
    is_veo_3_1_model,
    veo_3_1_schema_version,
)


@pytest.mark.parametrize(
    ("duration", "resolution", "aspect_ratio", "audio"),
    itertools.product(
        (4, 6, 8),
        ("720P", "1080P"),
        ("16:9", "9:16"),
        (False, True),
    ),
)
def test_veo_3_1_lite_text_matrix(
    duration: int,
    resolution: str,
    aspect_ratio: str,
    audio: bool,
) -> None:
    task = TaskCreate(
        model=VEO_3_1_LITE_MODEL,
        mode="text-to-video",
        input={
            "prompt": "A paper boat crosses a quiet lake",
            "duration": duration,
            "resolution": resolution,
            "aspect_ratio": aspect_ratio,
            "audio": audio,
        },
    )
    request = build_leonardo_veo_3_1_request(
        model=task.model,
        mode=str(task.mode),
        task_input=task.input_document(),
        assets=[],
    )
    parameters = request["parameters"]
    assert request == {
        "model": VEO_3_1_LITE_MODEL,
        "public": False,
        "parameters": parameters,
    }
    assert parameters["quantity"] == 1
    assert parameters["duration"] == duration
    assert parameters["motion_has_audio"] is audio
    assert (parameters["width"], parameters["height"]) == VEO_3_1_DIMENSIONS[
        resolution
    ][aspect_ratio]


@pytest.mark.parametrize(
    ("duration", "resolution", "audio", "expected"),
    [
        (4, "720P", False, 120),
        (6, "720P", False, 180),
        (8, "720P", False, 240),
        (4, "720P", True, 200),
        (6, "720P", True, 300),
        (8, "720P", True, 400),
        (4, "1080P", False, 200),
        (6, "1080P", False, 300),
        (8, "1080P", False, 400),
        (4, "1080P", True, 320),
        (6, "1080P", True, 480),
        (8, "1080P", True, 640),
    ],
)
def test_veo_3_1_lite_pricing_matrix(
    duration: int,
    resolution: str,
    audio: bool,
    expected: int,
) -> None:
    quotes = {
        quote_credit_cost(
            VEO_3_1_LITE_MODEL,
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


def test_veo_3_1_lite_image_request_maps_optional_end_frame() -> None:
    assets = [
        ResolvedMedia("IMAGE", "END_FRAME", 0, "https://a/end.png", "end-id"),
        ResolvedMedia("IMAGE", "START_FRAME", 0, "https://a/start.png", "start-id"),
    ]
    request = build_leonardo_veo_3_1_request(
        model=VEO_3_1_LITE_MODEL,
        mode="image-to-video",
        task_input={
            "prompt": "Move smoothly between both frames",
            "duration": 4,
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


def test_veo_3_1_lite_contract_and_schema_version() -> None:
    task = TaskCreate(
        model="VEO-3.1-LITE",
        task_type="VIDEO_GENERATION",
        mode="text-to-video",
        input={"prompt": "A tiny blue robot waves", "audio": False},
    )
    assert is_veo_3_1_model(task.model)
    assert task.estimated_credit_cost == 240
    assert veo_3_1_schema_version(task.model) == "veo-3.1-lite.v1"


def test_veo_3_1_lite_rejects_unsupported_resolution() -> None:
    with pytest.raises(ValueError, match="does not expose the 4K resolution tier"):
        TaskCreate(
            model=VEO_3_1_LITE_MODEL,
            mode="text-to-video",
            input={"prompt": "move", "resolution": "4K"},
        )


def test_veo_3_1_lite_rejects_reference_mode() -> None:
    with pytest.raises(ValueError, match="does not expose the reference-to-video mode"):
        TaskCreate(
            model=VEO_3_1_LITE_MODEL,
            mode="reference-to-video",
            input={
                "prompt": "move",
                "reference_image_urls": ["https://cdn.example.com/reference.png"],
            },
        )


def test_veo_3_1_lite_rejects_end_frame_without_start_frame() -> None:
    with pytest.raises(ValueError):
        TaskCreate(
            model=VEO_3_1_LITE_MODEL,
            mode="text-to-video",
            input={
                "prompt": "move",
                "end_image_url": "https://cdn.example.com/end.png",
            },
        )


def test_veo_3_1_lite_builder_rejects_main_only_capabilities() -> None:
    with pytest.raises(ValueError, match="does not expose the reference-to-video mode"):
        build_leonardo_veo_3_1_request(
            model=VEO_3_1_LITE_MODEL,
            mode="reference-to-video",
            task_input={"prompt": "move"},
            assets=[],
        )
    with pytest.raises(ValueError, match="unsupported Veo 3.1 resolution"):
        build_leonardo_veo_3_1_request(
            model=VEO_3_1_LITE_MODEL,
            mode="text-to-video",
            task_input={"prompt": "move", "resolution": "4K"},
            assets=[],
        )
