from __future__ import annotations

from typing import Any

from video_task_service.h3 import ResolvedMedia

GEMINI_OMNI_FLASH_MODEL = "gemini-omni-flash"
GEMINI_OMNI_FLASH_PROMPT_MAX_CHARS = 2500
GEMINI_OMNI_FLASH_DIMENSIONS: dict[str, tuple[int, int]] = {
    "16:9": (1280, 720),
    "9:16": (720, 1280),
}


def is_gemini_omni_flash_model(model: str) -> bool:
    return model.lower() == GEMINI_OMNI_FLASH_MODEL


def build_leonardo_gemini_omni_flash_request(
    *,
    model: str,
    mode: str,
    task_input: dict[str, Any],
    assets: list[ResolvedMedia],
) -> dict[str, Any]:
    normalized_model = model.lower()
    if not is_gemini_omni_flash_model(normalized_model):
        raise ValueError(f"unsupported Gemini Omni Flash model: {model}")
    if mode not in {"text-to-video", "reference-to-video"}:
        raise ValueError(
            "Gemini Omni Flash supports text-to-video and reference-to-video"
        )

    resolution = str(task_input.get("resolution", "720P")).upper()
    if resolution != "720P":
        raise ValueError("Gemini Omni Flash currently supports 720P only")
    aspect_ratio = str(task_input.get("aspect_ratio", "16:9"))
    dimensions = GEMINI_OMNI_FLASH_DIMENSIONS.get(aspect_ratio)
    if dimensions is None:
        raise ValueError(f"unsupported Gemini Omni Flash aspect ratio: {aspect_ratio}")
    width, height = dimensions

    parameters: dict[str, Any] = {
        "prompt": str(task_input["prompt"]).strip()[:GEMINI_OMNI_FLASH_PROMPT_MAX_CHARS],
        "duration": int(task_input.get("duration", 5)),
        "quantity": 1,
        "width": width,
        "height": height,
    }

    if mode == "reference-to-video":
        images = sorted(
            (item for item in assets if item.role == "REFERENCE_IMAGE"),
            key=lambda item: item.ordinal,
        )
        if not images:
            raise ValueError("Gemini Omni Flash reference mode requires an image")
        if len(images) > 5:
            raise ValueError("Gemini Omni Flash accepts at most 5 image references")
        parameters["guidances"] = {
            "image_reference": [
                {
                    "image": {
                        "id": item.provider_asset_id,
                        "type": item.provider_asset_type,
                    },
                    "strength": "MID",
                }
                for item in images
            ]
        }

    # Keep provider generations private. Quantity and guidance strength are fixed
    # to the documented provider contract and are not caller-controlled.
    return {"model": normalized_model, "public": False, "parameters": parameters}
