from __future__ import annotations

from typing import Any

from video_task_service.h3 import ResolvedMedia

SEEDANCE_25_MODEL = "bytedance/seedance-2.5"

SEEDANCE_MODELS = frozenset(
    {
        "seedance-2.0-mini",
        "seedance-2.0",
        "seedance-2.0-fast",
        SEEDANCE_25_MODEL,
    }
)

SEEDANCE_MODEL_RESOLUTIONS: dict[str, frozenset[str]] = {
    "seedance-2.0-mini": frozenset({"480P", "720P"}),
    "seedance-2.0": frozenset({"480P", "720P", "1080P", "4K"}),
    "seedance-2.0-fast": frozenset({"480P", "720P"}),
    SEEDANCE_25_MODEL: frozenset({"480P", "720P"}),
}

SEEDANCE_MODEL_DURATION_RANGES: dict[str, tuple[int, int]] = {
    "seedance-2.0-mini": (4, 15),
    "seedance-2.0": (4, 15),
    "seedance-2.0-fast": (4, 15),
    SEEDANCE_25_MODEL: (4, 30),
}

SEEDANCE_REFERENCE_LIMITS: dict[str, tuple[int, int, int]] = {
    "seedance-2.0-mini": (4, 3, 1),
    "seedance-2.0": (4, 3, 1),
    "seedance-2.0-fast": (4, 3, 1),
    SEEDANCE_25_MODEL: (30, 10, 10),
}

SEEDANCE_RESOLUTION_MODES = {
    "480P": "RESOLUTION_480",
    "720P": "RESOLUTION_720",
    "1080P": "RESOLUTION_1080",
    "4K": "RESOLUTION_2160",
}

# Values observed from Leonardo's current model controls. The 480P and 720P
# tiers intentionally keep the provider's aligned dimensions instead of
# calculating ideal mathematical ratios.
SEEDANCE_DIMENSIONS: dict[str, dict[str, tuple[int, int]]] = {
    "480P": {
        "21:9": (992, 432),
        "16:9": (864, 496),
        "4:3": (752, 560),
        "1:1": (640, 640),
        "3:4": (560, 752),
        "9:16": (496, 864),
    },
    "720P": {
        "21:9": (1470, 630),
        "16:9": (1280, 720),
        "4:3": (1112, 834),
        "1:1": (960, 960),
        "3:4": (834, 1112),
        "9:16": (720, 1280),
    },
    "1080P": {
        "21:9": (2520, 1080),
        "16:9": (1920, 1080),
        "4:3": (1440, 1080),
        "1:1": (1440, 1440),
        "3:4": (1080, 1440),
        "9:16": (1080, 1920),
    },
    "4K": {
        "21:9": (5040, 2160),
        "16:9": (3840, 2160),
        "4:3": (2880, 2160),
        "1:1": (2880, 2880),
        "3:4": (2160, 2880),
        "9:16": (2160, 3840),
    },
}


def is_seedance_model(model: str) -> bool:
    return model.lower() in SEEDANCE_MODELS


def is_seedance_25_model(model: str) -> bool:
    return model.lower() == SEEDANCE_25_MODEL


def seedance_input_schema_version(model: str) -> str:
    return "seedance-2.5.v1" if is_seedance_25_model(model) else "seedance.v1"


def _asset(
    assets: list[ResolvedMedia],
    role: str,
    ordinal: int = 0,
) -> ResolvedMedia | None:
    return next(
        (item for item in assets if item.role == role and item.ordinal == ordinal),
        None,
    )


def _video_duration_seconds(asset: ResolvedMedia) -> int:
    if asset.duration_ms is None:
        raise ValueError("Seedance video references require a probed duration")
    return max(1, (asset.duration_ms + 500) // 1000)


def build_leonardo_seedance_request(
    *,
    model: str,
    mode: str,
    task_input: dict[str, Any],
    assets: list[ResolvedMedia],
) -> dict[str, Any]:
    normalized_model = model.lower()
    if normalized_model not in SEEDANCE_MODELS:
        raise ValueError(f"unsupported Seedance model: {model}")

    resolution = str(task_input.get("resolution", "480P")).upper()
    if resolution not in SEEDANCE_MODEL_RESOLUTIONS[normalized_model]:
        raise ValueError(f"{model} does not expose the {resolution} resolution tier")

    aspect = str(task_input.get("aspect_ratio", "16:9"))
    dimensions = SEEDANCE_DIMENSIONS.get(resolution, {}).get(aspect)
    if dimensions is None:
        raise ValueError(f"unsupported Seedance aspect ratio: {aspect}")
    width, height = dimensions

    duration = int(task_input.get("duration", 4))
    minimum_duration, maximum_duration = SEEDANCE_MODEL_DURATION_RANGES[normalized_model]
    if not minimum_duration <= duration <= maximum_duration:
        raise ValueError(
            f"{model} duration must be between {minimum_duration} and "
            f"{maximum_duration} seconds"
        )
    audio = task_input.get("audio", True)
    if not isinstance(audio, bool):
        raise ValueError("Seedance audio must be a boolean")

    parameters: dict[str, Any] = {
        "width": width,
        "height": height,
        "duration": duration,
        "quantity": 1,
        "prompt": str(task_input["prompt"]).strip(),
        "seed": -1,
        "motion_has_audio": audio,
    }

    # Current Leonardo browser traffic treats width/height as canonical for
    # Mini and Fast.  Standard Seedance still emits the legacy resolution mode
    # for text and first/end-frame generations, but Omni/reference requests do
    # not emit it for any of the three models.
    if normalized_model == "seedance-2.0" and mode != "reference-to-video":
        parameters["mode"] = SEEDANCE_RESOLUTION_MODES[resolution]

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
        videos = sorted(
            (item for item in assets if item.role == "REFERENCE_VIDEO"),
            key=lambda item: item.ordinal,
        )
        audios = sorted(
            (item for item in assets if item.role == "REFERENCE_AUDIO"),
            key=lambda item: item.ordinal,
        )
        image_limit, video_limit, audio_limit = SEEDANCE_REFERENCE_LIMITS[
            normalized_model
        ]
        if len(images) > image_limit:
            raise ValueError(
                f"{model} accepts at most {image_limit} image references"
            )
        if len(videos) > video_limit:
            raise ValueError(
                f"{model} accepts at most {video_limit} video references"
            )
        if len(audios) > audio_limit:
            raise ValueError(
                f"{model} accepts at most {audio_limit} audio references"
            )
        if not (images or videos):
            raise ValueError("Seedance omni mode requires an image or video reference")

        guidances = {}
        if images:
            structured_images = task_input.get("reference_images") or []
            image_strengths = {
                ordinal: str(item.get("strength", "MID")).upper()
                for ordinal, item in enumerate(structured_images)
                if isinstance(item, dict)
            }
            image_guidances: list[dict[str, Any]] = []
            for item in images:
                guidance: dict[str, Any] = {
                    "image": {
                        "id": item.provider_asset_id,
                        "type": item.provider_asset_type,
                    },
                    "strength": image_strengths.get(item.ordinal, "MID"),
                }
                if is_seedance_25_model(normalized_model):
                    guidance["order"] = item.ordinal
                image_guidances.append(guidance)
            guidances["image_reference"] = image_guidances
        if videos:
            guidances["video_reference_base"] = [
                {
                    "video": {
                        "id": item.provider_asset_id,
                        "type": item.provider_asset_type,
                        "duration": _video_duration_seconds(item),
                    }
                }
                for item in videos
            ]
        if audios:
            guidances["audio_reference"] = [
                {
                    "audio": {
                        "id": item.provider_asset_id,
                        "type": item.provider_asset_type,
                    }
                }
                for item in audios
            ]
        parameters["guidances"] = guidances

    return {
        "model": normalized_model,
        "public": not is_seedance_25_model(normalized_model),
        "parameters": parameters,
    }
