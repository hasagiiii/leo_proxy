from __future__ import annotations

from typing import Any

from video_task_service.h3 import ResolvedMedia

VEO_3_1_MODEL = "veo-3.1-generate-001"
VEO_3_1_FAST_MODEL = "veo-3.1-fast-generate-001"
VEO_3_1_LITE_MODEL = "veo-3.1-lite"
VEO_3_1_MODELS = {VEO_3_1_MODEL, VEO_3_1_FAST_MODEL, VEO_3_1_LITE_MODEL}
VEO_3_1_PROMPT_MAX_CHARS = 9999
VEO_3_1_DIMENSIONS: dict[str, dict[str, tuple[int, int]]] = {
    "720P": {"16:9": (1280, 720), "9:16": (720, 1280)},
    "1080P": {"16:9": (1920, 1080), "9:16": (1080, 1920)},
    "4K": {"16:9": (3840, 2160), "9:16": (2160, 3840)},
}
VEO_3_1_MODEL_RESOLUTIONS: dict[str, frozenset[str]] = {
    VEO_3_1_MODEL: frozenset({"720P", "1080P", "4K"}),
    VEO_3_1_FAST_MODEL: frozenset({"720P", "1080P", "4K"}),
    VEO_3_1_LITE_MODEL: frozenset({"720P", "1080P"}),
}
VEO_3_1_MODEL_MODES: dict[str, frozenset[str]] = {
    VEO_3_1_MODEL: frozenset(
        {"text-to-video", "image-to-video", "reference-to-video"}
    ),
    VEO_3_1_FAST_MODEL: frozenset({"text-to-video", "image-to-video"}),
    VEO_3_1_LITE_MODEL: frozenset({"text-to-video", "image-to-video"}),
}


def is_veo_3_1_model(model: str) -> bool:
    return model.lower() in VEO_3_1_MODELS


def veo_3_1_schema_version(model: str) -> str:
    normalized_model = model.lower()
    if normalized_model == VEO_3_1_FAST_MODEL:
        return "veo-3.1-fast.v1"
    if normalized_model == VEO_3_1_LITE_MODEL:
        return "veo-3.1-lite.v1"
    return "veo-3.1.v1"


def _asset(
    assets: list[ResolvedMedia],
    role: str,
    ordinal: int = 0,
) -> ResolvedMedia | None:
    return next(
        (item for item in assets if item.role == role and item.ordinal == ordinal),
        None,
    )


def build_leonardo_veo_3_1_request(
    *,
    model: str,
    mode: str,
    task_input: dict[str, Any],
    assets: list[ResolvedMedia],
) -> dict[str, Any]:
    normalized_model = model.lower()
    if not is_veo_3_1_model(normalized_model):
        raise ValueError(f"unsupported Veo 3.1 model: {model}")
    if mode not in VEO_3_1_MODEL_MODES[normalized_model]:
        raise ValueError(f"{normalized_model} does not expose the {mode} mode")

    resolution = str(task_input.get("resolution", "720P")).upper()
    aspect_ratio = str(task_input.get("aspect_ratio", "16:9"))
    dimensions = (
        VEO_3_1_DIMENSIONS.get(resolution, {}).get(aspect_ratio)
        if resolution in VEO_3_1_MODEL_RESOLUTIONS[normalized_model]
        else None
    )
    if dimensions is None:
        raise ValueError(
            "unsupported Veo 3.1 resolution/aspect ratio: "
            f"{resolution} {aspect_ratio}"
        )
    width, height = dimensions

    parameters: dict[str, Any] = {
        "prompt": str(task_input["prompt"]).strip()[:VEO_3_1_PROMPT_MAX_CHARS],
        "duration": int(task_input.get("duration", 8)),
        "motion_has_audio": bool(task_input.get("audio", True)),
        "quantity": 1,
        "width": width,
        "height": height,
    }
    if negative_prompt := task_input.get("negative_prompt"):
        parameters["negative_prompt"] = str(negative_prompt).strip()
    if "seed" in task_input:
        parameters["seed"] = int(task_input["seed"])

    if mode == "image-to-video":
        start = _asset(assets, "START_FRAME")
        if start is None:
            raise ValueError("Veo 3.1 image-to-video requires a resolved start frame")
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
        if not images:
            raise ValueError("Veo 3.1 reference mode requires an image")
        if len(images) > 3:
            raise ValueError("Veo 3.1 accepts at most 3 image references")
        strength = str(task_input.get("reference_strength", "MID")).upper()
        parameters["guidances"] = {
            "image_reference": [
                {
                    "image": {
                        "id": item.provider_asset_id,
                        "type": item.provider_asset_type,
                    },
                    "strength": strength,
                }
                for item in images
            ]
        }

    return {"model": normalized_model, "public": False, "parameters": parameters}
