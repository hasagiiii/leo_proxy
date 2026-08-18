import pytest

from video_task_service.h3 import ResolvedMedia
from video_task_service.kling_o3 import (
    KLING_O3_DIMENSIONS,
    build_leonardo_kling_o3_request,
)


@pytest.mark.parametrize(
    ("resolution", "aspect_ratio", "expected"),
    [
        (resolution, aspect_ratio, dimensions)
        for resolution, matrix in KLING_O3_DIMENSIONS.items()
        for aspect_ratio, dimensions in matrix.items()
    ],
)
def test_kling_o3_dimension_matrix(
    resolution: str,
    aspect_ratio: str,
    expected: tuple[int, int],
) -> None:
    request = build_leonardo_kling_o3_request(
        model="kling-video-o-3",
        mode="text-to-video",
        task_input={
            "prompt": "A paper boat on a lake",
            "duration": 3,
            "resolution": resolution,
            "aspect_ratio": aspect_ratio,
            "audio": True,
        },
        assets=[],
    )
    parameters = request["parameters"]
    assert (parameters["width"], parameters["height"]) == expected
    assert parameters["motion_has_audio"] is True


def test_kling_o3_start_end_frames_and_audio_off() -> None:
    assets = [
        ResolvedMedia("IMAGE", "START_FRAME", 0, "https://a/start.png", "start-id"),
        ResolvedMedia("IMAGE", "END_FRAME", 0, "https://a/end.png", "end-id"),
    ]
    request = build_leonardo_kling_o3_request(
        model="kling-video-o-3",
        mode="image-to-video",
        task_input={
            "prompt": "move",
            "duration": 5,
            "resolution": "1080P",
            "aspect_ratio": "16:9",
            "audio": False,
        },
        assets=assets,
    )
    assert request["parameters"]["motion_has_audio"] is False
    guidances = request["parameters"]["guidances"]
    assert guidances["start_frame"][0]["image"]["id"] == "start-id"
    assert guidances["end_frame"][0]["image"]["id"] == "end-id"


def test_kling_o3_reference_uses_generated_video_and_uploaded_images() -> None:
    assets = [
        ResolvedMedia("IMAGE", "REFERENCE_IMAGE", 1, "https://a/i2.png", "i2"),
        ResolvedMedia("IMAGE", "REFERENCE_IMAGE", 0, "https://a/i1.png", "i1"),
    ]
    request = build_leonardo_kling_o3_request(
        model="kling-video-o-3",
        mode="reference-to-video",
        task_input={
            "prompt": "follow the references",
            "duration": 8,
            "resolution": "1080P",
            "aspect_ratio": "1:1",
            "reference_video_id": "generated-video-id",
        },
        assets=assets,
    )
    guidances = request["parameters"]["guidances"]
    assert [x["image"]["id"] for x in guidances["image_reference"]] == ["i1", "i2"]
    assert guidances["video_reference_base"] == [
        {"video": {"id": "generated-video-id", "type": "GENERATED"}}
    ]


def test_kling_o3_rejects_4k_image_guidance() -> None:
    with pytest.raises(ValueError, match="incompatible with 4K"):
        build_leonardo_kling_o3_request(
            model="kling-video-o-3",
            mode="reference-to-video",
            task_input={
                "prompt": "style",
                "duration": 3,
                "resolution": "4K",
                "aspect_ratio": "16:9",
                "reference_video_id": "video",
            },
            assets=[ResolvedMedia("IMAGE", "REFERENCE_IMAGE", 0, "https://a/i.png", "i")],
        )
