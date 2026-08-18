from __future__ import annotations

import asyncio

import pytest

import video_task_service.worker as worker_module
from video_task_service.config import Settings
from video_task_service.h3 import MediaSpec, ResolvedMedia
from video_task_service.upstream import (
    LeonardoUpstream,
    MediaHostCircuitBreaker,
    UpstreamError,
)
from video_task_service.worker import Assignment, resolve_assignment_media


class ConcurrentMediaUpstream:
    def __init__(self) -> None:
        self.active = 0
        self.maximum = 0

    async def resolve_media(
        self,
        *,
        token: str,
        spec: MediaSpec,
        **_: object,
    ) -> ResolvedMedia:
        assert token == "token"
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        try:
            await asyncio.sleep(0.02)
        finally:
            self.active -= 1
        return ResolvedMedia(
            kind=spec.kind,
            role=spec.role,
            ordinal=spec.ordinal,
            source_url=spec.source_url,
            provider_asset_id=f"asset-{spec.ordinal}",
        )


def test_media_resolution_uses_three_parallel_slots_and_preserves_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upstream = ConcurrentMediaUpstream()
    assignment = Assignment(
        task_id=1,
        task_uuid="task-uuid",
        account_id=2,
        account_uuid="account-uuid",
        space_id=3,
        attempt_no=1,
        model="gpt-image-2",
        mode="image-to-image",
        input_schema_version="gpt-image-2.v1",
        task_input={
            "reference_image_urls": [
                f"https://media.example/{index}.png" for index in range(6)
            ]
        },
        encrypted_token=b"ciphertext",
        estimated_credit_cost=8,
    )

    async def initialize(*_: object) -> dict[tuple[str, str, int], ResolvedMedia]:
        return {}

    async def record_ready(*_: object) -> None:
        return None

    monkeypatch.setattr(worker_module, "initialize_media_assets", initialize)
    monkeypatch.setattr(worker_module, "record_media_ready", record_ready)

    resolved = asyncio.run(
        resolve_assignment_media(
            assignment,
            upstream,  # type: ignore[arg-type]
            "token",
            "worker-1",
            "leonardo",
            3,
        )
    )

    assert upstream.maximum == 3
    assert [item.ordinal for item in resolved] == list(range(6))


def test_quantv_circuit_opens_after_threshold_and_exposes_retry_delay() -> None:
    circuit = MediaHostCircuitBreaker(
        "cdn.quantv.com",
        failure_threshold=3,
        window_seconds=60,
        open_seconds=60,
    )

    circuit.record_failure("cdn.quantv.com", now=10)
    circuit.record_failure("cdn.quantv.com", now=11)
    circuit.before_request("cdn.quantv.com", now=11.5)
    circuit.record_failure("cdn.quantv.com", now=12)

    with pytest.raises(UpstreamError) as captured:
        circuit.before_request("cdn.quantv.com", now=13)

    assert captured.value.code == "MEDIA_HOST_CIRCUIT_OPEN"
    assert captured.value.retryable is True
    assert captured.value.retry_after_seconds == 59
    circuit.before_request("unrelated.example", now=13)


def test_success_resets_quantv_circuit() -> None:
    circuit = MediaHostCircuitBreaker(
        "cdn.quantv.com",
        failure_threshold=1,
        window_seconds=60,
        open_seconds=60,
    )
    circuit.record_failure("cdn.quantv.com", now=10)
    circuit.record_success("cdn.quantv.com")
    circuit.before_request("cdn.quantv.com", now=11)


def test_images_use_independent_shorter_download_timeout() -> None:
    upstream = LeonardoUpstream(
        Settings(
            upstream_mode="leonardo",
            media_image_connect_timeout_seconds=4,
            media_image_read_timeout_seconds=25,
        )
    )
    try:
        image = upstream._media_download_timeout("IMAGE")
        video = upstream._media_download_timeout("VIDEO")
        assert image.connect == 4
        assert image.read == 25
        assert video.connect == 10
        assert video.read == 120
    finally:
        asyncio.run(upstream.close())
