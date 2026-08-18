"""Deterministic Leonardo generation credit quotes used for task reservation.

The base values are the Generate-button observations recorded in the model
integration docs.  Leonardo may return ``apiCreditCost`` at submission time;
that provider value supersedes the reservation before settlement.
"""

from __future__ import annotations

from collections.abc import Mapping
from math import ceil
from typing import Any

PRICING_RULE_VERSION = "leonardo-ui-20260812.v15"

H3_BASE_DURATION_SECONDS = 5
H3_2K_CREDITS = 700

SEEDANCE_BASE_DURATION_SECONDS = 4
SEEDANCE_CREDIT_TABLE: dict[str, dict[str, int]] = {
    "seedance-2.0-mini": {"480P": 320, "720P": 640},
    "seedance-2.0": {
        "480P": 562,
        "720P": 1209,
        "1080P": 2721,
        "4K": 7616,
    },
    "seedance-2.0-fast": {"480P": 449, "720P": 967},
}
SEEDANCE_STANDARD_480P_DURATION_CREDITS: dict[int, int] = {
    4: 562,
    5: 703,
    6: 843,
    7: 984,
    8: 1124,
    9: 1265,
    10: 1406,
    11: 1546,
    12: 1687,
    13: 1828,
    14: 1968,
    15: 2109,
}
SEEDANCE_25_CREDITS_PER_SECOND: dict[str, int] = {
    "480P": 180,
    "720P": 292,
}
SEEDANCE_25_VIDEO_REFERENCE_CREDITS_PER_SECOND: dict[str, int] = {
    "480P": 90,
    "720P": 180,
}

KLING_O3_CREDITS_PER_SECOND: dict[str, dict[bool, int]] = {
    "720P": {False: 168, True: 224},
    "1080P": {False: 224, True: 280},
    "4K": {False: 420, True: 420},
}

GEMINI_OMNI_FLASH_CREDITS_PER_SECOND = 100
SEED_AUDIO_CREDITS_PER_GENERATION = 350

VEO_3_1_CREDITS_PER_SECOND: dict[str, dict[bool, int]] = {
    "720P": {False: 200, True: 400},
    "1080P": {False: 200, True: 400},
    "4K": {False: 400, True: 600},
}
VEO_3_1_FAST_CREDITS_PER_SECOND: dict[str, dict[bool, int]] = {
    "720P": {False: 100, True: 150},
    "1080P": {False: 100, True: 150},
    "4K": {False: 300, True: 350},
}
VEO_3_1_LITE_CREDITS_PER_SECOND: dict[str, dict[bool, int]] = {
    "720P": {False: 30, True: 50},
    "1080P": {False: 50, True: 80},
}

GPT_IMAGE_2_SIZE_INDEX = {"SMALL": 0, "MEDIUM": 1, "LARGE": 2}
GPT_IMAGE_2_CREDIT_TABLE: dict[str, dict[str, tuple[int, int, int]]] = {
    "LOW": {
        "21:9": (8, 13, 44),
        "16:9": (8, 17, 51),
        "3:2": (8, 20, 58),
        "4:3": (8, 23, 56),
        "5:4": (8, 24, 58),
        "1:1": (8, 30, 59),
        "4:5": (8, 24, 58),
        "3:4": (8, 23, 56),
        "2:3": (8, 20, 58),
        "9:16": (8, 17, 51),
    },
    "MEDIUM": {
        "21:9": (66, 110, 385),
        "16:9": (66, 144, 447),
        "3:2": (67, 175, 507),
        "4:3": (67, 195, 495),
        "5:4": (67, 209, 507),
        "1:1": (65, 260, 513),
        "4:5": (67, 209, 507),
        "3:4": (67, 195, 495),
        "2:3": (67, 175, 507),
        "9:16": (66, 144, 447),
    },
    "HIGH": {
        "21:9": (263, 436, 1530),
        "16:9": (261, 573, 1779),
        "3:2": (264, 694, 2015),
        "4:3": (265, 775, 1967),
        "5:4": (264, 831, 2017),
        "1:1": (259, 1033, 2042),
        "4:5": (264, 831, 2017),
        "3:4": (265, 775, 1967),
        "2:3": (264, 694, 2015),
        "9:16": (261, 573, 1779),
    },
}

NANO_IMAGE_ASPECT_RATIOS = {
    "21:9",
    "16:9",
    "3:2",
    "4:3",
    "5:4",
    "1:1",
    "4:5",
    "3:4",
    "2:3",
    "9:16",
}
NANO_IMAGE_CREDIT_TABLE: dict[str, dict[str, int]] = {
    "nano-banana-2": {"SMALL": 80, "MEDIUM": 120, "LARGE": 160},
    "nano-banana-pro": {"SMALL": 140, "MEDIUM": 140, "LARGE": 250},
}


def quote_credit_cost(model: str, task_input: Mapping[str, Any]) -> int | None:
    """Return the reservation quote for a typed Leonardo generation task.

    H3 is quoted from its 5-second 2K baseline. Seedance is quoted from its
    4-second resolution baseline. Kling O3 uses the exact browser-observed
    per-second price for its resolution and audio combination. A provider-
    reported ``apiCreditCost`` remains authoritative.
    """

    normalized_model = model.lower()
    if normalized_model == "gpt-image-2":
        quality = str(task_input.get("quality", "MEDIUM")).upper()
        size = str(task_input.get("size", "SMALL")).upper()
        aspect_ratio = str(task_input.get("aspect_ratio", "1:1"))
        size_index = GPT_IMAGE_2_SIZE_INDEX.get(size)
        price_row = GPT_IMAGE_2_CREDIT_TABLE.get(quality, {}).get(aspect_ratio)
        if size_index is None or price_row is None:
            return None
        return price_row[size_index]

    if normalized_model in NANO_IMAGE_CREDIT_TABLE:
        aspect_ratio = str(task_input.get("aspect_ratio", "1:1"))
        size = str(task_input.get("size", "SMALL")).upper()
        if aspect_ratio not in NANO_IMAGE_ASPECT_RATIOS:
            return None
        return NANO_IMAGE_CREDIT_TABLE[normalized_model].get(size)

    if normalized_model == "seed-audio-1.0":
        try:
            quantity = int(task_input.get("quantity", 1))
        except (TypeError, ValueError):
            return None
        if quantity not in {1, 2, 3, 4}:
            return None
        return SEED_AUDIO_CREDITS_PER_GENERATION * quantity

    try:
        duration = int(task_input.get("duration", 5 if normalized_model == "hailuo-03" else 4))
    except (TypeError, ValueError):
        return None

    if normalized_model == "hailuo-03":
        if not 5 <= duration <= 15:
            return None
        if str(task_input.get("resolution", "2K")).upper() != "2K":
            return None
        return ceil(H3_2K_CREDITS * duration / H3_BASE_DURATION_SECONDS)

    if normalized_model == "kling-video-o-3":
        if not 3 <= duration <= 15:
            return None
        resolution = str(task_input.get("resolution", "1080P")).upper()
        audio = task_input.get("audio", True)
        if not isinstance(audio, bool):
            return None
        per_second = KLING_O3_CREDITS_PER_SECOND.get(resolution, {}).get(audio)
        return None if per_second is None else per_second * duration

    if normalized_model == "gemini-omni-flash":
        if not 3 <= duration <= 10:
            return None
        if str(task_input.get("resolution", "720P")).upper() != "720P":
            return None
        if str(task_input.get("aspect_ratio", "16:9")) not in {"16:9", "9:16"}:
            return None
        return GEMINI_OMNI_FLASH_CREDITS_PER_SECOND * duration

    if normalized_model in {
        "veo-3.1-generate-001",
        "veo-3.1-fast-generate-001",
        "veo-3.1-lite",
    }:
        if duration not in {4, 6, 8}:
            return None
        resolution = str(task_input.get("resolution", "720P")).upper()
        if str(task_input.get("aspect_ratio", "16:9")) not in {"16:9", "9:16"}:
            return None
        audio = task_input.get("audio", True)
        if not isinstance(audio, bool):
            return None
        if normalized_model == "veo-3.1-fast-generate-001":
            price_table = VEO_3_1_FAST_CREDITS_PER_SECOND
        elif normalized_model == "veo-3.1-lite":
            price_table = VEO_3_1_LITE_CREDITS_PER_SECOND
        else:
            price_table = VEO_3_1_CREDITS_PER_SECOND
        per_second = price_table.get(resolution, {}).get(audio)
        return None if per_second is None else per_second * duration

    resolution = str(task_input.get("resolution", "480P")).upper()
    if normalized_model == "bytedance/seedance-2.5":
        per_second = SEEDANCE_25_CREDITS_PER_SECOND.get(resolution)
        if per_second is None or not 4 <= duration <= 30:
            return None
        video_references = task_input.get("reference_video_urls")
        video_reference_rate = (
            SEEDANCE_25_VIDEO_REFERENCE_CREDITS_PER_SECOND[resolution]
            if isinstance(video_references, list) and video_references
            else 0
        )
        return (per_second + video_reference_rate) * duration
    if normalized_model == "seedance-2.0" and resolution == "480P":
        exact = SEEDANCE_STANDARD_480P_DURATION_CREDITS.get(duration)
        if exact is not None:
            return exact
    base = SEEDANCE_CREDIT_TABLE.get(normalized_model, {}).get(resolution)
    if base is None or not 4 <= duration <= 15:
        return None
    return ceil(base * duration / SEEDANCE_BASE_DURATION_SECONDS)
