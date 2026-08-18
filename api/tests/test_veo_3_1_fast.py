import itertools

import pytest

from video_task_service.h3 import ResolvedMedia
from video_task_service.pricing import quote_credit_cost
from video_task_service.schemas import TaskCreate
from video_task_service.veo_3_1 import (
    VEO_3_1_DIMENSIONS,
    VEO_3_1_FAST_MODEL,
    build_leonardo_veo_3_1_request,
    is_veo_3_1_model,
    veo_3_1_schema_version,
)


def expected_credits(duration: int, resolution: str, audio: bool) -> int:
    per_second = {
        "720P": {False: 100, True: 150},
        "1080P": {False: 100, True: 150},
        "4K": {False: 300, True: 350},
    }[resolution][audio]
    return duration * per_second


@pytest.mark.parametrize(
    ("mode", "duration", "resolution", "aspect_ratio", "audio"),
    itertools.product(
        ("text-to-video", "image-to-video"),
        (4, 6, 8),
        ("720P", "1080P", "4K"),
        ("16:9", "9:16"),
        (False, True),
    ),
)
def test_veo_3_1_fast_complete_parameter_matrix(
    mode: str,
    duration: int,
    resolution: str,
    aspect_ratio: str,
    audio: bool,
) -> None:
    task_input: dict[str, object] = {
        "prompt": "A cobalt paper airplane glides over a quiet lake",
        "duration": duration,
        "resolution": resolution,
        "aspect_ratio": aspect_ratio,
        "audio": audio,
    }
    assets: list[ResolvedMedia] = []
    if mode == "image-to-video":
        task_input["image_url"] = "https://cdn.example.com/start.png"
        assets = [
            ResolvedMedia(
                "IMAGE",
                "START_FRAME",
                0,
                "https://cdn.example.com/start.png",
                "start-id",
            )
        ]

    task = TaskCreate(
        model=VEO_3_1_FAST_MODEL,
        task_type="VIDEO_GENERATION",
        mode=mode,
        input=task_input,
    )
    request = build_leonardo_veo_3_1_request(
        model=task.model,
        mode=str(task.mode),
        task_input=task.input_document(),
        assets=assets,
    )
    parameters = request["parameters"]
    credits = expected_credits(duration, resolution, audio)

    assert request["model"] == VEO_3_1_FAST_MODEL
    assert request["public"] is False
    assert parameters["quantity"] == 1
    assert parameters["duration"] == duration
    assert parameters["motion_has_audio"] is audio
    assert (parameters["width"], parameters["height"]) == VEO_3_1_DIMENSIONS[
        resolution
    ][aspect_ratio]
    assert task.estimated_credit_cost == credits
    assert quote_credit_cost(task.model, task.input_document()) == credits
    if mode == "text-to-video":
        assert "guidances" not in parameters
    else:
        assert parameters["guidances"] == {
            "start_frame": [
                {"image": {"id": "start-id", "type": "UPLOADED"}}
            ]
        }


def test_veo_3_1_fast_maps_optional_end_frame_and_advanced_fields() -> None:
    assets = [
        ResolvedMedia("IMAGE", "START_FRAME", 0, "https://a/start.png", "start-id"),
        ResolvedMedia("IMAGE", "END_FRAME", 0, "https://a/end.png", "end-id"),
    ]
    request = build_leonardo_veo_3_1_request(
        model=VEO_3_1_FAST_MODEL,
        mode="image-to-video",
        task_input={
            "prompt": "Move smoothly between both frames",
            "duration": 8,
            "resolution": "4K",
            "aspect_ratio": "9:16",
            "audio": True,
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


def test_veo_3_1_fast_contract_and_schema_version() -> None:
    task = TaskCreate(
        model="VEO-3.1-FAST-GENERATE-001",
        task_type="VIDEO_GENERATION",
        mode="text-to-video",
        input={"prompt": "A tiny blue robot waves", "audio": False},
    )
    assert is_veo_3_1_model(task.model)
    assert task.estimated_credit_cost == 800
    assert veo_3_1_schema_version(task.model) == "veo-3.1-fast.v1"


def test_veo_3_1_fast_rejects_image_reference_mode() -> None:
    with pytest.raises(
        ValueError,
        match="does not expose the reference-to-video mode",
    ):
        TaskCreate(
            model=VEO_3_1_FAST_MODEL,
            mode="reference-to-video",
            input={
                "prompt": "Follow this visual reference",
                "reference_image_urls": ["https://cdn.example.com/reference.png"],
            },
        )

    with pytest.raises(
        ValueError,
        match="does not expose the reference-to-video mode",
    ):
        build_leonardo_veo_3_1_request(
            model=VEO_3_1_FAST_MODEL,
            mode="reference-to-video",
            task_input={"prompt": "Follow this visual reference"},
            assets=[],
        )


def test_veo_3_1_fast_requires_resolved_start_frame() -> None:
    with pytest.raises(ValueError, match="resolved start frame"):
        build_leonardo_veo_3_1_request(
            model=VEO_3_1_FAST_MODEL,
            mode="image-to-video",
            task_input={"prompt": "Animate the image"},
            assets=[],
        )
