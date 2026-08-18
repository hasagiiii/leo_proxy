from __future__ import annotations

from typing import Any

from video_task_service.h3 import ResolvedMedia

NANO_IMAGE_MODELS: dict[str, str] = {
    "nano-banana-2": "nano-banana-2",
    "nano-banana-pro": "gemini-image-2",
}
NANO_IMAGE_NONE_STYLE_ID = "556c1ee5-ec38-42e8-955a-1e82dad0ffa1"

NANO_IMAGE_DIMENSIONS: dict[str, dict[str, tuple[int, int]]] = {
    "21:9": {"SMALL": (1584, 672), "MEDIUM": (3168, 1344), "LARGE": (6336, 2688)},
    "16:9": {"SMALL": (1376, 768), "MEDIUM": (2752, 1536), "LARGE": (5504, 3072)},
    "3:2": {"SMALL": (1264, 848), "MEDIUM": (2528, 1696), "LARGE": (5056, 3392)},
    "4:3": {"SMALL": (1200, 896), "MEDIUM": (2400, 1792), "LARGE": (4800, 3584)},
    "5:4": {"SMALL": (1152, 928), "MEDIUM": (2304, 1856), "LARGE": (4608, 3712)},
    "1:1": {"SMALL": (1024, 1024), "MEDIUM": (2048, 2048), "LARGE": (4096, 4096)},
    "4:5": {"SMALL": (928, 1152), "MEDIUM": (1856, 2304), "LARGE": (3712, 4608)},
    "3:4": {"SMALL": (896, 1200), "MEDIUM": (1792, 2400), "LARGE": (3584, 4800)},
    "2:3": {"SMALL": (848, 1264), "MEDIUM": (1696, 2528), "LARGE": (3392, 5056)},
    "9:16": {"SMALL": (768, 1376), "MEDIUM": (1536, 2752), "LARGE": (3072, 5504)},
}


def is_nano_image_model(model: str) -> bool:
    return model.lower() in NANO_IMAGE_MODELS


def nano_upstream_model(model: str) -> str:
    normalized = model.lower()
    upstream_model = NANO_IMAGE_MODELS.get(normalized)
    if upstream_model is None:
        raise ValueError(f"unsupported Nano image model: {model}")
    return upstream_model


def nano_image_dimensions(aspect_ratio: str, size: str) -> tuple[int, int]:
    dimensions = NANO_IMAGE_DIMENSIONS.get(aspect_ratio, {}).get(size.upper())
    if dimensions is None:
        raise ValueError(f"unsupported Nano image aspect ratio/size: {aspect_ratio} {size}")
    return dimensions


def nano_image_resolution(aspect_ratio: str, size: str) -> str:
    width, height = nano_image_dimensions(aspect_ratio, size)
    return f"{width}x{height}"


def build_leonardo_nano_image_request(
    *,
    model: str,
    mode: str,
    task_input: dict[str, Any],
    assets: list[ResolvedMedia],
) -> dict[str, Any]:
    normalized_model = model.lower()
    upstream_model = nano_upstream_model(normalized_model)
    supported_modes = {"text-to-image", "image-to-image"}
    if mode not in supported_modes:
        raise ValueError(f"unsupported {normalized_model} mode: {mode}")

    size = str(task_input.get("size", "SMALL")).upper()
    aspect_ratio = str(task_input.get("aspect_ratio", "1:1"))
    width, height = nano_image_dimensions(aspect_ratio, size)
    expected_resolution = f"{width}x{height}"
    supplied_resolution = str(task_input.get("resolution", expected_resolution)).replace(
        "×", "x"
    )
    if supplied_resolution != expected_resolution:
        raise ValueError(
            "Nano image resolution must match the selected aspect_ratio and size"
        )

    images = sorted(
        (item for item in assets if item.role == "REFERENCE_IMAGE"),
        key=lambda item: item.ordinal,
    )
    videos = sorted(
        (item for item in assets if item.role == "REFERENCE_VIDEO"),
        key=lambda item: item.ordinal,
    )
    if len(images) > 6:
        raise ValueError("Nano image models accept at most 6 image references")
    if videos:
        raise ValueError("Nano image models do not accept video references")

    if mode == "text-to-image" and (images or videos):
        raise ValueError("text-to-image does not accept reference media")
    if mode == "image-to-image":
        if not images:
            raise ValueError("image-to-image requires at least one resolved image reference")
        if videos:
            raise ValueError("image-to-image does not accept video references")

    parameters: dict[str, Any] = {
        "width": width,
        "height": height,
        "prompt": str(task_input["prompt"]).strip(),
        "quantity": 1,
        "prompt_enhance": "OFF",
        "style_ids": [NANO_IMAGE_NONE_STYLE_ID],
    }
    guidances: dict[str, Any] = {}
    if images:
        guidances["image_reference"] = [
            {
                "image": {
                    "id": item.provider_asset_id,
                    "type": item.provider_asset_type,
                },
                "strength": "MID",
            }
            for item in images
        ]
    if guidances:
        parameters["guidances"] = guidances

    return {"model": upstream_model, "public": False, "parameters": parameters}
