from __future__ import annotations

from typing import Any

SEED_AUDIO_MODEL = "seed-audio-1.0"
SEED_AUDIO_SCHEMA_VERSION = "seed-audio-1.v1"
SEED_AUDIO_PROMPT_MAX_CHARS = 3000
SEED_AUDIO_DEFAULT_VOICE_ID = "zh_female_jitangnv_uranus_bigtts"


def is_seed_audio_model(model: str) -> bool:
    return model.lower() == SEED_AUDIO_MODEL


def build_leonardo_seed_audio_request(
    *,
    model: str,
    mode: str,
    task_input: dict[str, Any],
) -> dict[str, Any]:
    normalized_model = model.lower()
    if not is_seed_audio_model(normalized_model):
        raise ValueError(f"unsupported Seed Audio model: {model}")
    if mode != "text-to-speech":
        raise ValueError("Seed Audio 1.0 requires mode=text-to-speech")

    prompt = str(task_input["prompt"]).strip()
    if not prompt:
        raise ValueError("Seed Audio 1.0 prompt must not be empty")

    parameters = {
        "prompt": prompt[:SEED_AUDIO_PROMPT_MAX_CHARS],
        "voice_id": str(
            task_input.get("voice_id", SEED_AUDIO_DEFAULT_VOICE_ID)
        ),
        "speed": float(task_input.get("speed", 1.0)),
        "volume": float(task_input.get("volume", 1.0)),
        "pitch": int(task_input.get("pitch", 0)),
        "quantity": int(task_input.get("quantity", 1)),
    }

    # Seed Audio generations are always private in FRAME OPS. The caller cannot
    # override provider visibility through the public task contract.
    return {"model": normalized_model, "public": False, "parameters": parameters}
