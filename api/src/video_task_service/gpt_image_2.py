from __future__ import annotations

from typing import Any

from video_task_service.h3 import ResolvedMedia

GPT_IMAGE_2_MODEL = "gpt-image-2"
GPT_IMAGE_2_NONE_STYLE_ID = "556c1ee5-ec38-42e8-955a-1e82dad0ffa1"

GPT_IMAGE_2_DIMENSIONS: dict[str, dict[str, tuple[int, int]]] = {
    "21:9": {"SMALL": (1584, 672), "MEDIUM": (2048, 864), "LARGE": (3808, 1632)},
    "16:9": {"SMALL": (1376, 768), "MEDIUM": (2048, 1136), "LARGE": (3584, 2016)},
    "3:2": {"SMALL": (1264, 848), "MEDIUM": (2048, 1376), "LARGE": (3504, 2336)},
    "4:3": {"SMALL": (1200, 896), "MEDIUM": (2048, 1536), "LARGE": (3264, 2448)},
    "5:4": {"SMALL": (1152, 928), "MEDIUM": (2048, 1648), "LARGE": (3200, 2560)},
    "1:1": {"SMALL": (1024, 1024), "MEDIUM": (2048, 2048), "LARGE": (2880, 2880)},
    "4:5": {"SMALL": (928, 1152), "MEDIUM": (1648, 2048), "LARGE": (2560, 3200)},
    "3:4": {"SMALL": (896, 1200), "MEDIUM": (1536, 2048), "LARGE": (2448, 3264)},
    "2:3": {"SMALL": (848, 1264), "MEDIUM": (1376, 2048), "LARGE": (2336, 3504)},
    "9:16": {"SMALL": (768, 1376), "MEDIUM": (1136, 2048), "LARGE": (2016, 3584)},
}


def is_gpt_image_2_model(model: str) -> bool:
    return model.lower() == GPT_IMAGE_2_MODEL


def gpt_image_2_dimensions(aspect_ratio: str, size: str) -> tuple[int, int]:
    dimensions = GPT_IMAGE_2_DIMENSIONS.get(aspect_ratio, {}).get(size.upper())
    if dimensions is None:
        raise ValueError(
            f"unsupported GPT Image 2 aspect ratio/size: {aspect_ratio} {size}"
        )
    return dimensions


def gpt_image_2_resolution(aspect_ratio: str, size: str) -> str:
    width, height = gpt_image_2_dimensions(aspect_ratio, size)
    return f"{width}x{height}"


def build_leonardo_gpt_image_2_request(
    *,
    model: str,
    mode: str,
    task_input: dict[str, Any],
    assets: list[ResolvedMedia],
) -> dict[str, Any]:
    normalized_model = model.lower()
    if not is_gpt_image_2_model(normalized_model):
        raise ValueError(f"unsupported GPT Image 2 model: {model}")
    if mode not in {"text-to-image", "image-to-image"}:
        raise ValueError(f"unsupported GPT Image 2 mode: {mode}")

    quality = str(task_input.get("quality", "MEDIUM")).upper()
    if quality not in {"LOW", "MEDIUM", "HIGH"}:
        raise ValueError(f"unsupported GPT Image 2 quality: {quality}")
    size = str(task_input.get("size", "SMALL")).upper()
    aspect_ratio = str(task_input.get("aspect_ratio", "1:1"))
    width, height = gpt_image_2_dimensions(aspect_ratio, size)
    expected_resolution = f"{width}x{height}"
    supplied_resolution = str(task_input.get("resolution", expected_resolution)).replace(
        "×", "x"
    )
    if supplied_resolution != expected_resolution:
        raise ValueError(
            "GPT Image 2 resolution must match the selected aspect_ratio and size"
        )

    parameters: dict[str, Any] = {
        "width": width,
        "height": height,
        "prompt": str(task_input["prompt"]).strip(),
        "quantity": 1,
        "quality": quality,
        "prompt_enhance": "OFF",
        "style_ids": [GPT_IMAGE_2_NONE_STYLE_ID],
    }

    references = sorted(
        (item for item in assets if item.role == "REFERENCE_IMAGE"),
        key=lambda item: item.ordinal,
    )
    if mode == "text-to-image" and references:
        raise ValueError("text-to-image does not accept reference images")
    if mode == "image-to-image":
        if not references:
            raise ValueError("image-to-image requires at least one resolved image reference")
        if len(references) > 6:
            raise ValueError("GPT Image 2 accepts at most 6 image references")
        parameters["guidances"] = {
            "image_reference": [
                {
                    "image": {
                        "id": item.provider_asset_id,
                        "type": item.provider_asset_type,
                    },
                    "strength": "MID",
                }
                for item in references
            ]
        }

    return {"model": normalized_model, "public": True, "parameters": parameters}
