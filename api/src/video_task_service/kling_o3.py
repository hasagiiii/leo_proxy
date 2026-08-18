from __future__ import annotations

from typing import Any

from video_task_service.h3 import ResolvedMedia

KLING_O3_MODEL = "kling-video-o-3"

KLING_O3_RESOLUTION_MODES = {
    "720P": "RESOLUTION_720",
    "1080P": "RESOLUTION_1080",
    "4K": "RESOLUTION_2160",
}

KLING_O3_DIMENSIONS: dict[str, dict[str, tuple[int, int]]] = {
    "720P": {
        "16:9": (1280, 720),
        "1:1": (960, 960),
        "9:16": (720, 1280),
    },
    "1080P": {
        "16:9": (1920, 1080),
        "1:1": (1440, 1440),
        "9:16": (1080, 1920),
    },
    "4K": {
        "16:9": (3840, 2160),
        "1:1": (2880, 2880),
        "9:16": (2160, 3840),
    },
}


def is_kling_o3_model(model: str) -> bool:
    return model.lower() == KLING_O3_MODEL


def _asset(
    assets: list[ResolvedMedia],
    role: str,
    ordinal: int = 0,
) -> ResolvedMedia | None:
    return next(
        (item for item in assets if item.role == role and item.ordinal == ordinal),
        None,
    )


def build_leonardo_kling_o3_request(
    *,
    model: str,
    mode: str,
    task_input: dict[str, Any],
    assets: list[ResolvedMedia],
) -> dict[str, Any]:
    normalized_model = model.lower()
    if not is_kling_o3_model(normalized_model):
        raise ValueError(f"unsupported Kling O3 model: {model}")

    resolution = str(task_input.get("resolution", "1080P")).upper()
    aspect = str(task_input.get("aspect_ratio", "16:9"))
    dimensions = KLING_O3_DIMENSIONS.get(resolution, {}).get(aspect)
    if dimensions is None:
        raise ValueError(f"unsupported Kling O3 resolution/aspect ratio: {resolution} {aspect}")
    width, height = dimensions

    parameters: dict[str, Any] = {
        "width": width,
        "height": height,
        "duration": int(task_input.get("duration", 3)),
        "quantity": 1,
        "prompt": str(task_input["prompt"]).strip(),
        "seed": -1,
        "mode": KLING_O3_RESOLUTION_MODES[resolution],
        "motion_has_audio": bool(task_input.get("audio", True)),
    }

    if mode == "image-to-video":
        start = _asset(assets, "START_FRAME")
        if start is None:
            raise ValueError("image-to-video requires a resolved start frame")
        guidances: dict[str, Any] = {
            "start_frame": [
                {
                    "image": {
                        "id": start.provider_asset_id,
                        "type": start.provider_asset_type,
                    }
                }
            ]
        }
        end = _asset(assets, "END_FRAME")
        if end is not None:
            guidances["end_frame"] = [
                {
                    "image": {
                        "id": end.provider_asset_id,
                        "type": end.provider_asset_type,
                    }
                }
            ]
        parameters["guidances"] = guidances
    elif mode == "reference-to-video":
        images = sorted(
            (item for item in assets if item.role == "REFERENCE_IMAGE"),
            key=lambda item: item.ordinal,
        )
        reference_video_id = task_input.get("reference_video_id")
        if not (images or reference_video_id):
            raise ValueError("Kling O3 reference mode requires image references or a video id")
        if resolution == "4K" and images:
            raise ValueError("Kling O3 image references are incompatible with 4K")
        max_images = 4 if reference_video_id else 7
        if len(images) > max_images:
            raise ValueError(f"Kling O3 accepts at most {max_images} image references")

        guidances = {}
        if images:
            guidances["image_reference"] = [
                {
                    "image": {
                        "id": item.provider_asset_id,
                        "type": item.provider_asset_type,
                    }
                }
                for item in images
            ]
        if reference_video_id:
            guidances["video_reference_base"] = [
                {
                    "video": {
                        "id": str(reference_video_id),
                        "type": "GENERATED",
                    }
                }
            ]
        parameters["guidances"] = guidances

    return {"model": normalized_model, "public": True, "parameters": parameters}
