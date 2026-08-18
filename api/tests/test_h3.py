from video_task_service.h3 import (
    H3_PROMPT_MAX_CHARS,
    ResolvedMedia,
    build_leonardo_h3_request,
    media_specs,
)


def test_text_to_video_request_mapping() -> None:
    request = build_leonardo_h3_request(
        model="hailuo-03",
        mode="text-to-video",
        task_input={
            "prompt": "A white kitten runs through a garden.",
            "duration": 5,
            "resolution": "2K",
            "aspect_ratio": "16:9",
        },
        assets=[],
    )
    parameters = request["parameters"]
    assert request["model"] == "hailuo-03"
    assert parameters["mode"] == "RESOLUTION_1440"
    assert (parameters["width"], parameters["height"]) == (2560, 1440)
    assert "motion_has_audio" not in parameters


def test_request_mapping_truncates_prompt_for_provider() -> None:
    prompt = "x" * (H3_PROMPT_MAX_CHARS + 546)
    request = build_leonardo_h3_request(
        model="hailuo-03",
        mode="text-to-video",
        task_input={
            "prompt": prompt,
            "duration": 15,
            "resolution": "2K",
            "aspect_ratio": "16:9",
        },
        assets=[],
    )

    assert request["parameters"]["prompt"] == prompt[:H3_PROMPT_MAX_CHARS]


def test_image_to_video_url_becomes_start_frame_id() -> None:
    task_input = {
        "prompt": "Slow movement",
        "duration": 5,
        "resolution": "2K",
        "image_url": "https://cdn.example.com/start.png",
    }
    specs = media_specs("image-to-video", task_input)
    assert [(item.kind, item.role) for item in specs] == [("IMAGE", "START_FRAME")]
    request = build_leonardo_h3_request(
        model="hailuo-03",
        mode="image-to-video",
        task_input=task_input,
        assets=[
            ResolvedMedia(
                kind="IMAGE",
                role="START_FRAME",
                ordinal=0,
                source_url=task_input["image_url"],
                provider_asset_id="image-id",
                width=2000,
                height=1152,
            )
        ],
    )
    parameters = request["parameters"]
    assert (parameters["width"], parameters["height"]) == (2560, 1440)
    assert parameters["guidances"]["start_frame"][0]["image"] == {
        "id": "image-id",
        "type": "UPLOADED",
    }


def test_reference_urls_preserve_guidance_order() -> None:
    task_input = {
        "prompt": "Image 1 and Image 2 follow Audio 1.",
        "duration": 10,
        "resolution": "2K",
        "aspect_ratio": "16:9",
        "reference_image_urls": ["https://a/1.png", "https://a/2.png"],
        "reference_audio_urls": ["https://a/1.mp3"],
        "reference_video_urls": [],
    }
    assets = [
        ResolvedMedia("IMAGE", "REFERENCE_IMAGE", 1, task_input["reference_image_urls"][1], "i2"),
        ResolvedMedia("AUDIO", "REFERENCE_AUDIO", 0, task_input["reference_audio_urls"][0], "a1"),
        ResolvedMedia("IMAGE", "REFERENCE_IMAGE", 0, task_input["reference_image_urls"][0], "i1"),
    ]
    request = build_leonardo_h3_request(
        model="hailuo-03",
        mode="reference-to-video",
        task_input=task_input,
        assets=assets,
    )
    guidances = request["parameters"]["guidances"]
    assert [item["image"]["id"] for item in guidances["image_reference"]] == ["i1", "i2"]
    assert guidances["audio_reference"][0]["audio"]["id"] == "a1"
