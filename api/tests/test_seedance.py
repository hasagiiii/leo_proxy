import json
from dataclasses import replace

import pytest

from video_task_service.config import Settings
from video_task_service.h3 import ResolvedMedia
from video_task_service.seedance import (
    SEEDANCE_DIMENSIONS,
    build_leonardo_seedance_request,
    seedance_input_schema_version,
)
from video_task_service.upstream import (
    DownloadedMedia,
    LeonardoUpstream,
    UpstreamError,
    validate_reference_audio_collection,
    validate_reference_audio_media,
    validate_reference_video_collection,
    validate_reference_video_media,
)


def _reference_video(**overrides: object) -> DownloadedMedia:
    values: dict[str, object] = {
        "content": b"fixture",
        "content_type": "video/mp4",
        "file_name": "reference.mp4",
        "extension": "mp4",
        "duration_ms": 8_000,
        "width": 1280,
        "height": 720,
        "frame_rate": 30.0,
        "audio_channels": 2,
        "audio_sample_rate": 48_000,
    }
    values.update(overrides)
    return DownloadedMedia(**values)  # type: ignore[arg-type]


def test_seedance_reference_video_accepts_browser_picker_boundaries() -> None:
    validate_reference_video_media(_reference_video(duration_ms=3_000, frame_rate=24.0))
    validate_reference_video_media(
        _reference_video(duration_ms=10_000, width=2160, height=2160, frame_rate=60.0)
    )
    validate_reference_video_media(
        _reference_video(frame_rate=1_000_000 / 41_667)
    )


@pytest.mark.parametrize(
    ("overrides", "expected_code"),
    [
        ({"extension": "webm"}, "MEDIA_VIDEO_FORMAT_UNSUPPORTED"),
        ({"duration_ms": 12_788}, "MEDIA_VIDEO_DURATION_INVALID"),
        ({"width": 504, "height": 896}, "MEDIA_VIDEO_DIMENSIONS_INVALID"),
        ({"frame_rate": 23.99}, "MEDIA_VIDEO_FRAME_RATE_INVALID"),
        ({"frame_rate": 60.01}, "MEDIA_VIDEO_FRAME_RATE_INVALID"),
        ({"audio_channels": 0}, "MEDIA_VIDEO_AUDIO_INVALID"),
    ],
)
def test_seedance_reference_video_rejects_browser_picker_mismatches(
    overrides: dict[str, object], expected_code: str
) -> None:
    with pytest.raises(UpstreamError) as error:
        validate_reference_video_media(_reference_video(**overrides))
    assert error.value.code == expected_code
    assert error.value.retryable is False


@pytest.mark.parametrize(
    ("width", "height"),
    [
        (628, 362),
        (3840, 2160),
    ],
)
def test_seedance_25_reference_video_skips_legacy_dimension_limits(
    width: int,
    height: int,
) -> None:
    validate_reference_video_media(
        _reference_video(width=width, height=height),
        enforce_dimensions=False,
    )


@pytest.mark.parametrize("duration_ms", [1_000, 18_048, 30_000])
def test_seedance_25_reference_video_skips_legacy_individual_duration_bounds(
    duration_ms: int,
) -> None:
    validate_reference_video_media(
        _reference_video(duration_ms=duration_ms),
        enforce_duration_bounds=False,
    )


@pytest.mark.parametrize("duration_ms", [None, 0])
def test_seedance_25_reference_video_still_requires_positive_probed_duration(
    duration_ms: int | None,
) -> None:
    with pytest.raises(UpstreamError) as error:
        validate_reference_video_media(
            _reference_video(duration_ms=duration_ms),
            enforce_duration_bounds=False,
        )
    assert error.value.code == "MEDIA_VIDEO_DURATION_INVALID"


def test_seedance_reference_videos_enforce_fifteen_second_combined_limit() -> None:
    videos = [
        ResolvedMedia(
            "VIDEO",
            "REFERENCE_VIDEO",
            ordinal,
            f"https://a/{ordinal}.mp4",
            f"video-{ordinal}",
            content_type="video/mp4",
            duration_ms=8_000,
            width=1280,
            height=720,
            extension="mp4",
            frame_rate=30.0,
            audio_channels=2,
        )
        for ordinal in range(2)
    ]
    with pytest.raises(UpstreamError) as error:
        validate_reference_video_collection(videos)
    assert error.value.code == "MEDIA_COMBINED_DURATION_INVALID"


def _resolved_video(ordinal: int, duration_ms: int) -> ResolvedMedia:
    return ResolvedMedia(
        "VIDEO",
        "REFERENCE_VIDEO",
        ordinal,
        f"https://a/{ordinal}.mp4",
        f"video-{ordinal}",
        content_type="video/mp4",
        duration_ms=duration_ms,
        width=1280,
        height=720,
        extension="mp4",
        frame_rate=30.0,
        audio_channels=2,
    )


def _resolved_audio(ordinal: int, duration_ms: int) -> ResolvedMedia:
    return ResolvedMedia(
        "AUDIO",
        "REFERENCE_AUDIO",
        ordinal,
        f"https://a/{ordinal}.mp3",
        f"audio-{ordinal}",
        content_type="audio/mpeg",
        duration_ms=duration_ms,
        extension="mp3",
        audio_channels=2,
        audio_sample_rate=44_100,
    )


@pytest.mark.parametrize("duration_ms", [2_000, 18_290, 30_000])
def test_seedance_25_reference_audio_accepts_two_to_thirty_seconds(
    duration_ms: int,
) -> None:
    validate_reference_audio_media(
        _resolved_audio(0, duration_ms),
        max_duration_ms=30_000,
    )


@pytest.mark.parametrize("duration_ms", [1_999, 30_001])
def test_seedance_25_reference_audio_rejects_outside_two_to_thirty_seconds(
    duration_ms: int,
) -> None:
    with pytest.raises(UpstreamError) as error:
        validate_reference_audio_media(
            _resolved_audio(0, duration_ms),
            max_duration_ms=30_000,
        )
    assert error.value.code == "MEDIA_DURATION_INVALID"


def test_seedance_25_reference_videos_allow_thirty_seconds_combined() -> None:
    videos = [_resolved_video(0, 18_048), _resolved_video(1, 11_952)]

    videos[0] = replace(videos[0], width=628, height=362)
    validate_reference_video_collection(
        videos,
        max_combined_duration_ms=30_000,
        enforce_dimensions=False,
        enforce_individual_duration_bounds=False,
    )


def test_seedance_25_reference_videos_reject_over_thirty_seconds_combined() -> None:
    videos = [_resolved_video(0, 18_048), _resolved_video(1, 11_953)]

    with pytest.raises(UpstreamError) as error:
        validate_reference_video_collection(
            videos,
            max_combined_duration_ms=30_000,
            enforce_individual_duration_bounds=False,
        )
    assert error.value.code == "MEDIA_COMBINED_DURATION_INVALID"


def test_seedance_25_reference_audios_allow_thirty_seconds_combined() -> None:
    audios = [_resolved_audio(0, 18_290), _resolved_audio(1, 11_710)]

    validate_reference_audio_collection(
        audios,
        max_combined_duration_ms=30_000,
        max_individual_duration_ms=30_000,
    )


def test_seedance_25_reference_audios_reject_over_thirty_seconds_combined() -> None:
    audios = [_resolved_audio(0, 18_290), _resolved_audio(1, 11_711)]

    with pytest.raises(UpstreamError) as error:
        validate_reference_audio_collection(
            audios,
            max_combined_duration_ms=30_000,
            max_individual_duration_ms=30_000,
        )
    assert error.value.code == "MEDIA_COMBINED_DURATION_INVALID"


def test_seedance_25_audio_and_video_have_independent_thirty_second_budgets() -> None:
    videos = [
        _resolved_video(ordinal, 10_000) for ordinal in range(3)
    ]
    audios = [_resolved_audio(0, 15_000), _resolved_audio(1, 15_000)]

    validate_reference_video_collection(videos, max_combined_duration_ms=30_000)
    validate_reference_audio_collection(
        audios,
        max_combined_duration_ms=30_000,
        max_individual_duration_ms=30_000,
    )


@pytest.mark.asyncio
async def test_media_upload_waits_for_leonardo_processing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upstream = LeonardoUpstream(Settings(media_upload_settle_seconds=8.0))
    sleeps: list[float] = []

    async def fake_gql(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {
            "data": {
                "uploadMedia": {
                    "uploadId": "video-id",
                    "url": "https://uploads.example.com",
                    "fields": json.dumps({"key": "fixture"}),
                }
            }
        }

    async def fake_post_presigned_upload(**_kwargs: object) -> None:
        return None

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(upstream, "_gql", fake_gql)
    monkeypatch.setattr(upstream, "_post_presigned_upload", fake_post_presigned_upload)
    monkeypatch.setattr("video_task_service.upstream.asyncio.sleep", fake_sleep)
    try:
        upload_id = await upstream._upload_media("token", _reference_video())
    finally:
        await upstream.close()

    assert upload_id == "video-id"
    assert sleeps == [8.0]


@pytest.mark.parametrize(
    ("resolution", "aspect_ratio", "expected"),
    [
        (resolution, aspect_ratio, dimensions)
        for resolution, matrix in SEEDANCE_DIMENSIONS.items()
        for aspect_ratio, dimensions in matrix.items()
    ],
)
def test_seedance_dimension_matrix(
    resolution: str,
    aspect_ratio: str,
    expected: tuple[int, int],
) -> None:
    request = build_leonardo_seedance_request(
        model="seedance-2.0",
        mode="text-to-video",
        task_input={
            "prompt": "A paper boat on a lake",
            "duration": 4,
            "resolution": resolution,
            "aspect_ratio": aspect_ratio,
        },
        assets=[],
    )
    parameters = request["parameters"]
    assert (parameters["width"], parameters["height"]) == expected
    assert parameters["mode"] == {
        "480P": "RESOLUTION_480",
        "720P": "RESOLUTION_720",
        "1080P": "RESOLUTION_1080",
        "4K": "RESOLUTION_2160",
    }[resolution]


def test_seedance_mini_text_request_matches_browser_payload() -> None:
    request = build_leonardo_seedance_request(
        model="seedance-2.0-mini",
        mode="text-to-video",
        task_input={
            "prompt": "百世可乐宣传片",
            "duration": 4,
            "resolution": "720P",
            "aspect_ratio": "1:1",
        },
        assets=[],
    )

    assert request == {
        "model": "seedance-2.0-mini",
        "public": True,
        "parameters": {
            "height": 960,
            "width": 960,
            "duration": 4,
            "motion_has_audio": True,
            "quantity": 1,
            "prompt": "百世可乐宣传片",
            "seed": -1,
        },
    }


def test_seedance_25_text_request_matches_browser_controls() -> None:
    request = build_leonardo_seedance_request(
        model="bytedance/seedance-2.5",
        mode="text-to-video",
        task_input={
            "prompt": "A paper kite rises above a meadow",
            "duration": 30,
            "resolution": "720P",
            "aspect_ratio": "9:16",
            "audio": False,
        },
        assets=[],
    )

    assert request == {
        "model": "bytedance/seedance-2.5",
        "public": False,
        "parameters": {
            "width": 720,
            "height": 1280,
            "duration": 30,
            "quantity": 1,
            "prompt": "A paper kite rises above a meadow",
            "seed": -1,
            "motion_has_audio": False,
        },
    }
    assert seedance_input_schema_version("bytedance/seedance-2.5") == (
        "seedance-2.5.v1"
    )


def test_seedance_25_rejects_out_of_range_duration() -> None:
    with pytest.raises(ValueError, match="between 4 and 30"):
        build_leonardo_seedance_request(
            model="bytedance/seedance-2.5",
            mode="text-to-video",
            task_input={
                "prompt": "test",
                "duration": 31,
                "resolution": "480P",
                "aspect_ratio": "16:9",
            },
            assets=[],
        )


def test_seedance_25_omni_preserves_reference_strength_and_order() -> None:
    request = build_leonardo_seedance_request(
        model="bytedance/seedance-2.5",
        mode="reference-to-video",
        task_input={
            "prompt": "follow both references",
            "duration": 8,
            "resolution": "480P",
            "aspect_ratio": "1:1",
            "reference_images": [
                {"url": "https://a/first.png", "strength": "LOW"},
                {"url": "https://a/second.png", "strength": "HIGH"},
            ],
        },
        assets=[
            ResolvedMedia("IMAGE", "REFERENCE_IMAGE", 1, "https://a/second.png", "i2"),
            ResolvedMedia("IMAGE", "REFERENCE_IMAGE", 0, "https://a/first.png", "i1"),
        ],
    )

    assert request["parameters"]["guidances"]["image_reference"] == [
        {
            "image": {"id": "i1", "type": "UPLOADED"},
            "strength": "LOW",
            "order": 0,
        },
        {
            "image": {"id": "i2", "type": "UPLOADED"},
            "strength": "HIGH",
            "order": 1,
        },
    ]


def test_seedance_25_omni_accepts_thirty_images_ten_videos_and_ten_audios() -> None:
    images = [
        ResolvedMedia(
            "IMAGE",
            "REFERENCE_IMAGE",
            ordinal,
            f"https://a/image-{ordinal}.png",
            f"image-{ordinal}",
        )
        for ordinal in range(30)
    ]
    videos = [
        ResolvedMedia(
            "VIDEO",
            "REFERENCE_VIDEO",
            ordinal,
            f"https://a/video-{ordinal}.mp4",
            f"video-{ordinal}",
            duration_ms=10_000,
        )
        for ordinal in range(10)
    ]
    audios = [
        ResolvedMedia(
            "AUDIO",
            "REFERENCE_AUDIO",
            ordinal,
            f"https://a/audio-{ordinal}.mp3",
            f"audio-{ordinal}",
        )
        for ordinal in range(10)
    ]

    request = build_leonardo_seedance_request(
        model="bytedance/seedance-2.5",
        mode="reference-to-video",
        task_input={
            "prompt": "maximum reference cardinality",
            "duration": 8,
            "resolution": "720P",
            "aspect_ratio": "16:9",
        },
        assets=[*images, *videos, *audios],
    )

    guidances = request["parameters"]["guidances"]
    assert len(guidances["image_reference"]) == 30
    assert len(guidances["video_reference_base"]) == 10
    assert len(guidances["audio_reference"]) == 10


@pytest.mark.parametrize(
    ("kind", "role", "count", "message"),
    [
        ("IMAGE", "REFERENCE_IMAGE", 31, "at most 30 image references"),
        ("VIDEO", "REFERENCE_VIDEO", 11, "at most 10 video references"),
        ("AUDIO", "REFERENCE_AUDIO", 11, "at most 10 audio references"),
    ],
)
def test_seedance_25_omni_rejects_reference_counts_above_limits(
    kind: str,
    role: str,
    count: int,
    message: str,
) -> None:
    assets = [
        ResolvedMedia(
            kind,  # type: ignore[arg-type]
            role,  # type: ignore[arg-type]
            ordinal,
            f"https://a/{kind.lower()}-{ordinal}",
            f"asset-{ordinal}",
            duration_ms=5_000 if kind == "VIDEO" else None,
        )
        for ordinal in range(count)
    ]
    if kind == "AUDIO":
        assets.append(
            ResolvedMedia(
                "IMAGE",
                "REFERENCE_IMAGE",
                0,
                "https://a/image.png",
                "image-0",
            )
        )

    with pytest.raises(ValueError, match=message):
        build_leonardo_seedance_request(
            model="bytedance/seedance-2.5",
            mode="reference-to-video",
            task_input={
                "prompt": "too many references",
                "duration": 8,
                "resolution": "720P",
                "aspect_ratio": "16:9",
            },
            assets=assets,
        )


def test_mini_and_fast_reject_full_hd() -> None:
    for model in ("seedance-2.0-mini", "seedance-2.0-fast"):
        with pytest.raises(ValueError, match="does not expose"):
            build_leonardo_seedance_request(
                model=model,
                mode="text-to-video",
                task_input={
                    "prompt": "test",
                    "duration": 4,
                    "resolution": "1080P",
                    "aspect_ratio": "16:9",
                },
                assets=[],
            )


def test_seedance_first_and_last_frame_guidances() -> None:
    assets = [
        ResolvedMedia("IMAGE", "START_FRAME", 0, "https://a/start.png", "start-id"),
        ResolvedMedia("IMAGE", "END_FRAME", 0, "https://a/end.png", "end-id"),
    ]
    request = build_leonardo_seedance_request(
        model="seedance-2.0-mini",
        mode="image-to-video",
        task_input={
            "prompt": "move",
            "duration": 4,
            "resolution": "720P",
            "aspect_ratio": "16:9",
        },
        assets=assets,
    )
    guidances = request["parameters"]["guidances"]
    assert guidances["start_frame"][0]["image"]["id"] == "start-id"
    assert guidances["end_frame"][0]["image"]["id"] == "end-id"


def test_seedance_omni_guidance_order_and_types() -> None:
    assets = [
        ResolvedMedia(
            "VIDEO",
            "REFERENCE_VIDEO",
            1,
            "https://a/v2.mp4",
            "v2",
            duration_ms=8_057,
        ),
        ResolvedMedia("IMAGE", "REFERENCE_IMAGE", 1, "https://a/i2.png", "i2"),
        ResolvedMedia("AUDIO", "REFERENCE_AUDIO", 0, "https://a/a1.mp3", "a1"),
        ResolvedMedia(
            "VIDEO",
            "REFERENCE_VIDEO",
            0,
            "https://a/v1.mp4",
            "v1",
            duration_ms=6_499,
        ),
        ResolvedMedia("IMAGE", "REFERENCE_IMAGE", 0, "https://a/i1.png", "i1"),
    ]
    request = build_leonardo_seedance_request(
        model="seedance-2.0-fast",
        mode="reference-to-video",
        task_input={
            "prompt": "follow all references",
            "duration": 4,
            "resolution": "480P",
            "aspect_ratio": "1:1",
        },
        assets=assets,
    )
    guidances = request["parameters"]["guidances"]
    assert [item["image"]["id"] for item in guidances["image_reference"]] == ["i1", "i2"]
    assert [item["video"]["id"] for item in guidances["video_reference_base"]] == [
        "v1",
        "v2",
    ]
    assert [item["video"]["duration"] for item in guidances["video_reference_base"]] == [
        6,
        8,
    ]
    assert request["parameters"]["motion_has_audio"] is True
    assert "mode" not in request["parameters"]
    assert guidances["audio_reference"][0]["audio"]["id"] == "a1"


@pytest.mark.parametrize(
    "model",
    ["seedance-2.0-mini", "seedance-2.0", "seedance-2.0-fast"],
)
def test_seedance_video_only_omni_preserves_requested_model(model: str) -> None:
    request = build_leonardo_seedance_request(
        model=model,
        mode="reference-to-video",
        task_input={
            "prompt": "follow the reference video",
            "duration": 4,
            "resolution": "480P",
            "aspect_ratio": "16:9",
        },
        assets=[
            ResolvedMedia(
                "VIDEO",
                "REFERENCE_VIDEO",
                0,
                "https://a/video.mp4",
                "video-id",
                duration_ms=8_000,
                width=1280,
                height=720,
                extension="mp4",
                frame_rate=30.0,
            )
        ],
    )

    assert request["model"] == model
    assert "mode" not in request["parameters"]
    assert request["parameters"]["guidances"] == {
        "video_reference_base": [
            {
                "video": {
                    "id": "video-id",
                    "type": "UPLOADED",
                    "duration": 8,
                }
            }
        ]
    }


@pytest.mark.parametrize(
    "model",
    ["seedance-2.0-mini", "seedance-2.0", "seedance-2.0-fast"],
)
def test_seedance_image_video_omni_matches_browser_payload(model: str) -> None:
    request = build_leonardo_seedance_request(
        model=model,
        mode="reference-to-video",
        task_input={
            "prompt": "百世可乐宣传片",
            "duration": 4,
            "resolution": "480P",
            "aspect_ratio": "1:1",
        },
        assets=[
            ResolvedMedia(
                "IMAGE",
                "REFERENCE_IMAGE",
                0,
                "https://a/image.png",
                "image-id",
            ),
            ResolvedMedia(
                "VIDEO",
                "REFERENCE_VIDEO",
                0,
                "https://a/video.mp4",
                "video-id",
                duration_ms=8_000,
            ),
        ],
    )

    assert request == {
        "model": model,
        "public": True,
        "parameters": {
            "height": 640,
            "width": 640,
            "duration": 4,
            "motion_has_audio": True,
            "quantity": 1,
            "prompt": "百世可乐宣传片",
            "guidances": {
                "image_reference": [
                    {
                        "image": {"id": "image-id", "type": "UPLOADED"},
                        "strength": "MID",
                    }
                ],
                "video_reference_base": [
                    {
                        "video": {
                            "id": "video-id",
                            "type": "UPLOADED",
                            "duration": 8,
                        }
                    }
                ],
            },
            "seed": -1,
        },
    }


def test_seedance_image_only_omni_omits_resolution_mode() -> None:
    request = build_leonardo_seedance_request(
        model="seedance-2.0",
        mode="reference-to-video",
        task_input={
            "prompt": "follow the reference image",
            "duration": 4,
            "resolution": "720P",
            "aspect_ratio": "16:9",
        },
        assets=[
            ResolvedMedia(
                "IMAGE",
                "REFERENCE_IMAGE",
                0,
                "https://a/image.png",
                "image-id",
            )
        ],
    )

    assert "mode" not in request["parameters"]


def test_seedance_video_reference_requires_probed_duration() -> None:
    with pytest.raises(ValueError, match="require a probed duration"):
        build_leonardo_seedance_request(
            model="seedance-2.0",
            mode="reference-to-video",
            task_input={
                "prompt": "follow the reference video",
                "duration": 6,
                "resolution": "1080P",
                "aspect_ratio": "16:9",
            },
            assets=[
                ResolvedMedia(
                    "VIDEO",
                    "REFERENCE_VIDEO",
                    0,
                    "https://a/reference.mp4",
                    "video-id",
                )
            ],
        )
