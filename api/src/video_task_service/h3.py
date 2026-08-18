from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

H3Mode = Literal["text-to-video", "image-to-video", "reference-to-video"]
H3_PROMPT_MAX_CHARS = 2000

H3_2K_DIMENSIONS: dict[str, tuple[int, int]] = {
    "21:9": (3360, 1440),
    "16:9": (2560, 1440),
    "4:3": (1920, 1440),
    "1:1": (1440, 1440),
    "3:4": (1440, 1920),
    "9:16": (1440, 2560),
}


def normalize_h3_prompt(value: str) -> str:
    """Match Leonardo H3's prompt boundary before the Generate mutation."""

    return value.strip()[:H3_PROMPT_MAX_CHARS]


@dataclass(frozen=True, slots=True)
class MediaSpec:
    kind: Literal["IMAGE", "AUDIO", "VIDEO"]
    role: Literal[
        "START_FRAME",
        "END_FRAME",
        "REFERENCE_IMAGE",
        "REFERENCE_AUDIO",
        "REFERENCE_VIDEO",
    ]
    ordinal: int
    source_url: str


@dataclass(frozen=True, slots=True)
class ResolvedMedia:
    kind: str
    role: str
    ordinal: int
    source_url: str
    provider_asset_id: str
    provider_asset_type: str = "UPLOADED"
    content_type: str | None = None
    content_length: int | None = None
    duration_ms: int | None = None
    width: int | None = None
    height: int | None = None
    extension: str | None = None
    frame_rate: float | None = None
    audio_channels: int | None = None
    audio_sample_rate: int | None = None


def media_specs(mode: str, task_input: dict[str, Any]) -> list[MediaSpec]:
    specs: list[MediaSpec] = []
    if mode == "image-to-video":
        specs.append(
            MediaSpec(
                kind="IMAGE",
                role="START_FRAME",
                ordinal=0,
                source_url=str(task_input["image_url"]),
            )
        )
        if end_url := task_input.get("end_image_url"):
            specs.append(
                MediaSpec(
                    kind="IMAGE",
                    role="END_FRAME",
                    ordinal=0,
                    source_url=str(end_url),
                )
            )
    elif mode in {"reference-to-video", "image-to-image"}:
        structured_images = task_input.get("reference_images") or []
        image_urls = (
            [item["url"] for item in structured_images]
            if structured_images
            else task_input.get("reference_image_urls") or []
        )
        for ordinal, url in enumerate(image_urls):
            specs.append(
                MediaSpec(
                    kind="IMAGE",
                    role="REFERENCE_IMAGE",
                    ordinal=ordinal,
                    source_url=str(url),
                )
            )
        for ordinal, url in enumerate(task_input.get("reference_video_urls") or []):
            specs.append(
                MediaSpec(
                    kind="VIDEO",
                    role="REFERENCE_VIDEO",
                    ordinal=ordinal,
                    source_url=str(url),
                )
            )
        for ordinal, url in enumerate(task_input.get("reference_audio_urls") or []):
            specs.append(
                MediaSpec(
                    kind="AUDIO",
                    role="REFERENCE_AUDIO",
                    ordinal=ordinal,
                    source_url=str(url),
                )
            )
    return specs


def nearest_aspect(width: int | None, height: int | None) -> str:
    if not width or not height or width <= 0 or height <= 0:
        return "16:9"
    ratio = width / height
    return min(
        H3_2K_DIMENSIONS,
        key=lambda value: abs(
            ratio - H3_2K_DIMENSIONS[value][0] / H3_2K_DIMENSIONS[value][1]
        ),
    )


def _asset(
    assets: list[ResolvedMedia],
    role: str,
    ordinal: int = 0,
) -> ResolvedMedia | None:
    return next(
        (item for item in assets if item.role == role and item.ordinal == ordinal),
        None,
    )


def build_leonardo_h3_request(
    *,
    model: str,
    mode: str,
    task_input: dict[str, Any],
    assets: list[ResolvedMedia],
) -> dict[str, Any]:
    if task_input.get("resolution", "2K") != "2K":
        raise ValueError("Leonardo H3 currently maps the public resolution to 2K only")

    aspect = str(task_input.get("aspect_ratio") or "16:9")
    if mode == "image-to-video":
        start = _asset(assets, "START_FRAME")
        if start is None:
            raise ValueError("image-to-video requires a resolved start frame")
        aspect = nearest_aspect(start.width, start.height)
    elif aspect == "adaptive":
        first_visual = _asset(assets, "REFERENCE_IMAGE") or _asset(
            assets, "REFERENCE_VIDEO"
        )
        aspect = nearest_aspect(
            first_visual.width if first_visual else None,
            first_visual.height if first_visual else None,
        )

    dimensions = H3_2K_DIMENSIONS.get(aspect)
    if dimensions is None:
        raise ValueError(f"unsupported H3 aspect ratio: {aspect}")
    width, height = dimensions
    parameters: dict[str, Any] = {
        "width": width,
        "height": height,
        "duration": int(task_input.get("duration", 5)),
        "quantity": 1,
        "prompt": normalize_h3_prompt(str(task_input["prompt"])),
        "seed": -1,
        "mode": "RESOLUTION_1440",
    }

    if mode == "image-to-video":
        start = _asset(assets, "START_FRAME")
        assert start is not None
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
        if len(images) > 5:
            raise ValueError("Leonardo H3 currently accepts at most 5 image references")
        if videos:
            raise ValueError("Leonardo H3 video-reference guidance is not enabled")
        if audios and not images:
            raise ValueError("Leonardo H3 audio references require an image reference")
        guidances = {}
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
        if videos:
            guidances["video_reference_base"] = [
                {
                    "video": {
                        "id": item.provider_asset_id,
                        "type": item.provider_asset_type,
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
        if guidances:
            parameters["guidances"] = guidances

    return {"model": model, "public": True, "parameters": parameters}
