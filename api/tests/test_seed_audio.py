import itertools
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import ValidationError

from video_task_service.config import Settings
from video_task_service.pricing import quote_credit_cost
from video_task_service.schemas import TaskCreate
from video_task_service.seed_audio import (
    SEED_AUDIO_DEFAULT_VOICE_ID,
    SEED_AUDIO_MODEL,
    SEED_AUDIO_SCHEMA_VERSION,
    build_leonardo_seed_audio_request,
    is_seed_audio_model,
)
from video_task_service.upstream import LeonardoUpstream, MockUpstream


@pytest.mark.parametrize(
    ("speed", "volume", "pitch", "quantity"),
    itertools.product(
        (0.5, 1.0, 2.0),
        (0.5, 1.0, 2.0),
        (-12, 0, 12),
        (1, 2, 3, 4),
    ),
)
def test_seed_audio_complete_parameter_matrix(
    speed: float,
    volume: float,
    pitch: int,
    quantity: int,
) -> None:
    task = TaskCreate(
        task_type="AUDIO_GENERATION",
        model=SEED_AUDIO_MODEL,
        mode="text-to-speech",
        input={
            "prompt": "  FRAME OPS Seed Audio smoke test.  ",
            "voice_id": SEED_AUDIO_DEFAULT_VOICE_ID,
            "speed": speed,
            "volume": volume,
            "pitch": pitch,
            "quantity": quantity,
        },
    )
    request = build_leonardo_seed_audio_request(
        model=task.model,
        mode=str(task.mode),
        task_input=task.input_document(),
    )

    assert task.input_document()["prompt"] == "FRAME OPS Seed Audio smoke test."
    assert task.estimated_credit_cost == 350 * quantity
    assert quote_credit_cost(task.model, task.input_document()) == 350 * quantity
    assert request == {
        "model": SEED_AUDIO_MODEL,
        "public": False,
        "parameters": {
            "prompt": "FRAME OPS Seed Audio smoke test.",
            "voice_id": SEED_AUDIO_DEFAULT_VOICE_ID,
            "speed": speed,
            "volume": volume,
            "pitch": pitch,
            "quantity": quantity,
        },
    }


def test_seed_audio_defaults_and_schema_identity() -> None:
    task = TaskCreate(
        task_type="AUDIO_GENERATION",
        model="SEED-AUDIO-1.0",
        mode="text-to-speech",
        input={"prompt": "Hello from Seed Audio."},
    )
    assert is_seed_audio_model(task.model)
    assert SEED_AUDIO_SCHEMA_VERSION == "seed-audio-1.v1"
    assert task.input_document() == {
        "prompt": "Hello from Seed Audio.",
        "voice_id": SEED_AUDIO_DEFAULT_VOICE_ID,
        "speed": 1.0,
        "volume": 1.0,
        "pitch": 0,
        "quantity": 1,
    }
    assert task.estimated_credit_cost == 350


@pytest.mark.parametrize(
    "task_type,mode",
    [
        ("VIDEO_GENERATION", "text-to-speech"),
        ("AUDIO_GENERATION", "text-to-video"),
    ],
)
def test_seed_audio_rejects_wrong_task_type_or_mode(
    task_type: str,
    mode: str,
) -> None:
    with pytest.raises(ValueError):
        TaskCreate(
            task_type=task_type,
            model=SEED_AUDIO_MODEL,
            mode=mode,
            input={"prompt": "Hello"},
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("speed", 0.45),
        ("speed", 2.05),
        ("speed", 1.03),
        ("volume", 0.45),
        ("volume", 2.05),
        ("volume", 1.03),
        ("pitch", -13),
        ("pitch", 13),
        ("quantity", 0),
        ("quantity", 5),
        ("voice_id", "bad voice id"),
    ],
)
def test_seed_audio_rejects_out_of_range_controls(field: str, value: object) -> None:
    with pytest.raises((ValueError, ValidationError)):
        TaskCreate(
            task_type="AUDIO_GENERATION",
            model=SEED_AUDIO_MODEL,
            mode="text-to-speech",
            input={"prompt": "Hello", field: value},
        )


def test_seed_audio_builder_rejects_other_models_and_modes() -> None:
    with pytest.raises(ValueError, match="unsupported Seed Audio model"):
        build_leonardo_seed_audio_request(
            model="other-model",
            mode="text-to-speech",
            task_input={"prompt": "Hello"},
        )
    with pytest.raises(ValueError, match="mode=text-to-speech"):
        build_leonardo_seed_audio_request(
            model=SEED_AUDIO_MODEL,
            mode="text-to-video",
            task_input={"prompt": "Hello"},
        )


@pytest.mark.asyncio
async def test_mock_upstream_returns_audio_media() -> None:
    upstream = MockUpstream(Settings(mock_generation_seconds=0.1))
    request = build_leonardo_seed_audio_request(
        model=SEED_AUDIO_MODEL,
        mode="text-to-speech",
        task_input={"prompt": "Hello"},
    )
    submitted = await upstream.submit(
        token="mock-token",
        model=SEED_AUDIO_MODEL,
        task_input={"request": request},
    )
    result = await upstream.poll(
        token="mock-token",
        generation_id=submitted.generation_id,
        submitted_at=datetime.now(UTC).replace(tzinfo=None)
        - timedelta(seconds=1),
        estimated_credit_cost=350,
    )

    assert result.status == "COMPLETE"
    assert result.actual_credit_cost == 350
    media = result.output["media"][0]
    assert media["type"] == "audio/wav"
    assert media["url"].startswith("data:audio/wav;base64,")
    assert media["sample_rate"] == 24_000


@pytest.mark.asyncio
async def test_leonardo_poll_reads_audio_from_nested_asset_url() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        body = json.loads(request.read())
        if body["operationName"] == "GetAIGenerationFeedStatuses":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "generations": [
                            {
                                "id": "audio-generation",
                                "status": "COMPLETE",
                                "nsfw": False,
                                "flagged": False,
                            }
                        ],
                        "generationNotes": [],
                    }
                },
            )
        assert body["operationName"] == "GetGenerationFeed"
        assert "urls {" in body["query"]
        return httpx.Response(
            200,
            json={
                "data": {
                    "generations": [
                        {
                            "generated_images": [
                                {
                                    "id": "audio-1",
                                    "url": "",
                                    "urls": {
                                        "asset": "https://cdn.example.com/result.mp3",
                                        "thumbnail": None,
                                    },
                                    "motionMP4URL": None,
                                    "motionGIFURL": None,
                                    "image_width": None,
                                    "image_height": None,
                                }
                            ]
                        }
                    ]
                }
            },
        )

    upstream = LeonardoUpstream(Settings())
    await upstream._client.aclose()
    upstream._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await upstream.poll(
            token="token",
            generation_id="audio-generation",
            submitted_at=datetime.now(UTC).replace(tzinfo=None),
            estimated_credit_cost=350,
        )
    finally:
        await upstream.close()

    assert calls == 2
    assert result.status == "COMPLETE"
    assert result.actual_credit_cost == 350
    assert result.output == {
        "provider": "leonardo",
        "generation_id": "audio-generation",
        "media": [
            {
                "id": "audio-1",
                "type": "audio/mpeg",
                "url": "https://cdn.example.com/result.mp3",
                "width": None,
                "height": None,
            }
        ],
    }
