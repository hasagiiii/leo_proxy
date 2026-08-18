from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from video_task_service.api.accounts import (
    account_status_for_balance,
    account_view,
    fixed_account_concurrency,
)
from video_task_service.h3 import H3_PROMPT_MAX_CHARS
from video_task_service.schemas import (
    AccountCreate,
    AccountPatch,
    AccountSync,
    AccountTokenUpdate,
    TaskCreate,
)


def test_account_token_and_expiry_must_be_paired() -> None:
    with pytest.raises(ValidationError):
        AccountCreate(
            space_uuid="7b61daf1-ae28-41bb-bb4c-1b44706a3a75",
            login_name="test@example.com",
            password="password",
            video_token="token",
        )


def test_token_update_requires_future_expiry() -> None:
    with pytest.raises(ValidationError):
        AccountTokenUpdate(
            video_token="token",
            token_expires_at=datetime.now(UTC) - timedelta(seconds=1),
            expected_version=1,
        )


def test_account_sync_accepts_existing_account_without_password() -> None:
    payload = AccountSync(
        space_name="leo01",
        login_name="test@example.com",
        video_token="token",
        token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        balance_credits=17000,
    )
    assert payload.password is None
    assert payload.balance_credits == 17000
    assert payload.max_concurrency == 3


def test_account_sync_accepts_leonardo_renewal_session_only() -> None:
    payload = AccountSync(
        space_name="leo01",
        login_name="test@example.com",
        video_token="token",
        token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        renewal_session={
            "cookies": [
                {
                    "name": "__Secure-better-auth.session_data.0",
                    "value": "cookie-secret",
                    "domain": ".app.leonardo.ai",
                    "path": "/",
                }
            ],
            "user_agent": "fixture-agent",
            "accept_language": "en-US",
            "client_version": "7.0.8",
            "capability": "better-auth-v1",
        },
    )

    assert payload.renewal_session is not None
    assert payload.renewal_session.cookies[0].value.get_secret_value() == "cookie-secret"
    assert payload.renewal_session.client_version == "7.0.8"
    assert payload.renewal_session.capability == "better-auth-v1"


def test_account_sync_rejects_foreign_renewal_cookie_domain() -> None:
    with pytest.raises(ValidationError):
        AccountSync(
            space_name="leo01",
            login_name="test@example.com",
            video_token="token",
            token_expires_at=datetime.now(UTC) + timedelta(hours=1),
            renewal_session={
                "cookies": [
                    {
                        "name": "session",
                        "value": "cookie-secret",
                        "domain": "example.test",
                    }
                ]
            },
        )


def test_account_view_exposes_the_complete_login_name() -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    account = SimpleNamespace(
        account_uuid="4f83f0ae-b08a-4d4d-a51f-3cce30523511",
        login_name="full.account@example.com",
        credential_source="COOKIE_SESSION",
        label="macbook",
        status="ACTIVE",
        disabled_reason=None,
        video_token_ciphertext="configured",
        token_expires_at=now + timedelta(hours=1),
        token_refreshed_at=now,
        balance_credits=17_000,
        reserved_credits=0,
        balance_synced_at=now,
        max_concurrency=3,
        active_tasks=0,
        completed_tasks=1,
        failed_tasks=0,
        version=1,
        created_at=now,
        updated_at=now,
    )
    space = SimpleNamespace(space_uuid="7b61daf1-ae28-41bb-bb4c-1b44706a3a75")

    payload = account_view(account, space).model_dump()

    assert payload["login_name"] == "full.account@example.com"
    assert payload["login_name_masked"] == "full.account@example.com"
    assert payload["credential_source"] == "COOKIE_SESSION"
    assert payload["label"] == "macbook"


@pytest.mark.parametrize("label", ["mmoshenqi", "macbook"])
def test_account_create_accepts_supported_labels(label: str) -> None:
    payload = AccountCreate(
        space_uuid="7b61daf1-ae28-41bb-bb4c-1b44706a3a75",
        login_name="test@example.com",
        password="password",
        label=label,
    )

    assert payload.label == label


def test_account_create_rejects_unknown_label() -> None:
    with pytest.raises(ValidationError):
        AccountCreate(
            space_uuid="7b61daf1-ae28-41bb-bb4c-1b44706a3a75",
            login_name="test@example.com",
            password="password",
            label="unknown",
        )


def test_account_create_defaults_to_three_concurrent_tasks() -> None:
    payload = AccountCreate(
        space_uuid="7b61daf1-ae28-41bb-bb4c-1b44706a3a75",
        login_name="test@example.com",
        password="password",
    )
    assert payload.max_concurrency == 3


def test_account_sync_rejects_expired_token() -> None:
    with pytest.raises(ValidationError):
        AccountSync(
            space_name="leo01",
            login_name="test@example.com",
            video_token="token",
            token_expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )


def test_account_patch_requires_a_change() -> None:
    with pytest.raises(ValidationError):
        AccountPatch(expected_version=3)


def test_account_patch_accepts_admin_edit_fields() -> None:
    payload = AccountPatch(
        space_uuid="7b61daf1-ae28-41bb-bb4c-1b44706a3a75",
        password="rotated-password",
        max_concurrency=4,
        expected_version=3,
    )
    assert payload.max_concurrency == 4
    assert payload.password is not None


def test_server_fixed_account_concurrency_normalizes_create_and_sync_values() -> None:
    assert fixed_account_concurrency(1, 3, reject_mismatch=False) == 3
    assert fixed_account_concurrency(99, 3, reject_mismatch=False) == 3


def test_server_fixed_account_concurrency_rejects_admin_override() -> None:
    with pytest.raises(HTTPException) as exc_info:
        fixed_account_concurrency(2, 3, reject_mismatch=True)
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["code"] == "ACCOUNT_CONCURRENCY_FIXED"


def test_balance_status_enforces_threshold_and_manual_disable() -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    expiry = now + timedelta(hours=1)
    status, reason = account_status_for_balance(
        "PENDING_VALIDATION",
        expiry,
        99,
        now=now,
        low_balance_threshold=100,
        token_guard_seconds=120,
    )
    assert (status, reason) == ("LOW_BALANCE_DISABLED", "balance_below_threshold")
    status, reason = account_status_for_balance(
        "MANUAL_DISABLED",
        expiry,
        1000,
        now=now,
        low_balance_threshold=100,
        token_guard_seconds=120,
    )
    assert (status, reason) == ("MANUAL_DISABLED", "manual")


def test_task_input_rejects_credentials() -> None:
    with pytest.raises(ValidationError):
        TaskCreate(model="seedance-2.0-mini", input={"authorization": "Bearer secret"})


def test_task_input_accepts_video_parameters() -> None:
    task = TaskCreate(
        model="seedance-2.0-mini",
        input={"prompt": "a calm lake", "duration": 4, "width": 864, "height": 496},
        estimated_credit_cost=50,
    )
    assert task.input["duration"] == 4


def test_typed_h3_text_input_applies_fal_defaults() -> None:
    task = TaskCreate(
        model="hailuo-03",
        mode="text-to-video",
        input={"prompt": "a calm lake"},
    )
    assert task.input == {
        "prompt": "a calm lake",
        "duration": 5,
        "resolution": "2K",
        "aspect_ratio": "16:9",
    }


def test_typed_h3_input_truncates_prompt_to_provider_limit() -> None:
    prompt = "x" * (H3_PROMPT_MAX_CHARS + 546)
    task = TaskCreate(
        model="hailuo-03",
        mode="text-to-video",
        input={"prompt": prompt},
    )

    assert task.input_document()["prompt"] == prompt[:H3_PROMPT_MAX_CHARS]
    assert len(task.input_document()["prompt"]) == H3_PROMPT_MAX_CHARS


def test_typed_h3_image_input_accepts_public_urls() -> None:
    task = TaskCreate(
        model="hailuo-03",
        mode="image-to-video",
        input={
            "prompt": "slow camera move",
            "image_url": "https://cdn.example.com/start.png",
            "end_image_url": "https://cdn.example.com/end.png",
        },
    )
    assert task.input_document()["image_url"] == "https://cdn.example.com/start.png"
    assert task.input_document()["end_image_url"] == "https://cdn.example.com/end.png"


def test_typed_h3_image_input_rejects_base64() -> None:
    with pytest.raises(ValidationError):
        TaskCreate(
            model="hailuo-03",
            mode="image-to-video",
            input={"prompt": "move", "image_url": "data:image/png;base64,AAAA"},
        )


def test_typed_h3_reference_input_enforces_audio_companion() -> None:
    with pytest.raises(ValidationError):
        TaskCreate(
            model="hailuo-03",
            mode="reference-to-video",
            input={
                "prompt": "follow Audio 1",
                "reference_audio_urls": ["https://cdn.example.com/audio.mp3"],
            },
        )


def test_task_create_openapi_exposes_all_h3_input_models() -> None:
    schema = TaskCreate.model_json_schema()
    input_schema = schema["properties"]["input"]
    references = {
        item.get("$ref", "").rsplit("/", 1)[-1]
        for item in input_schema["anyOf"]
    }
    assert {
        "H3TextToVideoInput",
        "H3ImageToVideoInput",
        "H3ReferenceToVideoInput",
    } <= references


def test_typed_seedance_mini_defaults_and_omni_alias() -> None:
    task = TaskCreate(
        model="seedance-2.0-mini",
        mode="omini",
        input={
            "prompt": "follow Image 1",
            "reference_image_urls": ["https://cdn.example.com/reference.png"],
        },
    )
    assert task.mode == "reference-to-video"
    assert task.input == {
        "prompt": "follow Image 1",
        "duration": 4,
        "resolution": "480P",
        "aspect_ratio": "16:9",
        "reference_image_urls": ["https://cdn.example.com/reference.png"],
        "reference_video_urls": [],
        "reference_audio_urls": [],
    }


def test_typed_seedance_reference_limits_match_provider_controls() -> None:
    with pytest.raises(ValidationError):
        TaskCreate(
            model="seedance-2.0-fast",
            mode="omni",
            input={
                "prompt": "too many images",
                "reference_image_urls": [
                    f"https://cdn.example.com/{index}.png" for index in range(5)
                ],
            },
        )
    with pytest.raises(ValidationError):
        TaskCreate(
            model="seedance-2.0-fast",
            mode="omni",
            input={
                "prompt": "audio alone",
                "reference_audio_urls": ["https://cdn.example.com/audio.mp3"],
            },
        )


def test_typed_seedance_standard_video_reference_preserves_model_quote() -> None:
    task = TaskCreate(
        model="seedance-2.0",
        mode="omini",
        input={
            "prompt": "follow the reference video",
            "duration": 15,
            "resolution": "480P",
            "reference_video_urls": ["https://cdn.example.com/reference.mp4"],
        },
    )

    assert task.model == "seedance-2.0"
    assert task.mode == "reference-to-video"
    assert task.estimated_credit_cost == 2109


def test_seedance_resolution_tiers_are_model_specific() -> None:
    with pytest.raises(ValidationError):
        TaskCreate(
            model="seedance-2.0-mini",
            mode="text-to-video",
            input={"prompt": "test", "resolution": "1080P"},
        )
    task = TaskCreate(
        model="seedance-2.0",
        mode="text-to-video",
        input={"prompt": "test", "resolution": "4K", "aspect_ratio": "21:9"},
    )
    assert task.input["resolution"] == "4K"


def test_task_create_openapi_exposes_seedance_input_models() -> None:
    schema = TaskCreate.model_json_schema()
    input_schema = schema["properties"]["input"]
    references = {
        item.get("$ref", "").rsplit("/", 1)[-1]
        for item in input_schema["anyOf"]
    }
    assert {
        "SeedanceTextToVideoInput",
        "SeedanceImageToVideoInput",
        "SeedanceReferenceToVideoInput",
    } <= references


def test_typed_seedance_25_defaults_and_max_duration_quote() -> None:
    task = TaskCreate(
        model="bytedance/seedance-2.5",
        mode="text-to-video",
        input={"prompt": "A quiet meadow", "duration": 30},
    )

    assert task.input == {
        "prompt": "A quiet meadow",
        "duration": 30,
        "resolution": "720P",
        "aspect_ratio": "16:9",
        "audio": True,
    }
    assert task.estimated_credit_cost == 8760


def test_typed_seedance_25_reference_objects_and_limits() -> None:
    task = TaskCreate(
        model="bytedance/seedance-2.5",
        mode="omni",
        input={
            "prompt": "Preserve the reference identity",
            "reference_images": [
                {"url": "https://cdn.example.com/a.png", "strength": "HIGH"}
            ],
        },
    )

    assert task.mode == "reference-to-video"
    assert task.input["reference_images"] == [
        {"url": "https://cdn.example.com/a.png", "strength": "HIGH"}
    ]
    with pytest.raises(ValidationError):
        TaskCreate(
            model="bytedance/seedance-2.5",
            mode="text-to-video",
            input={"prompt": "too long", "duration": 31},
        )


def test_typed_seedance_25_accepts_reference_capacity_30_10_10() -> None:
    task = TaskCreate(
        model="bytedance/seedance-2.5",
        mode="reference-to-video",
        input={
            "prompt": "maximum references",
            "reference_images": [
                {"url": f"https://cdn.example.com/image-{index}.png"}
                for index in range(30)
            ],
            "reference_video_urls": [
                f"https://cdn.example.com/video-{index}.mp4" for index in range(10)
            ],
            "reference_audio_urls": [
                f"https://cdn.example.com/audio-{index}.mp3" for index in range(10)
            ],
        },
    )

    assert len(task.input["reference_images"]) == 30
    assert len(task.input["reference_video_urls"]) == 10
    assert len(task.input["reference_audio_urls"]) == 10


@pytest.mark.parametrize(
    ("field", "count"),
    [
        ("reference_images", 31),
        ("reference_video_urls", 11),
        ("reference_audio_urls", 11),
    ],
)
def test_typed_seedance_25_rejects_reference_counts_above_capacity(
    field: str,
    count: int,
) -> None:
    suffix = "png" if field == "reference_images" else "mp4"
    values: list[object]
    if field == "reference_images":
        values = [
            {"url": f"https://cdn.example.com/item-{index}.{suffix}"}
            for index in range(count)
        ]
    else:
        values = [
            f"https://cdn.example.com/item-{index}.{suffix}" for index in range(count)
        ]
    payload: dict[str, object] = {
        "prompt": "too many references",
        "reference_images": [{"url": "https://cdn.example.com/base.png"}],
        field: values,
    }
    if field == "reference_images":
        payload = {"prompt": "too many references", field: values}

    with pytest.raises(ValidationError):
        TaskCreate(
            model="bytedance/seedance-2.5",
            mode="reference-to-video",
            input=payload,
        )


def test_task_create_openapi_exposes_seedance_25_input_models() -> None:
    schema = TaskCreate.model_json_schema()
    references = {
        item.get("$ref", "").rsplit("/", 1)[-1]
        for item in schema["properties"]["input"]["anyOf"]
    }
    assert {
        "Seedance25TextToVideoInput",
        "Seedance25ImageToVideoInput",
        "Seedance25ReferenceToVideoInput",
    } <= references


def test_typed_kling_o3_defaults_and_audio_quote() -> None:
    task = TaskCreate(
        model="kling-video-o-3",
        mode="text-to-video",
        input={"prompt": "a paper boat", "duration": 3, "resolution": "720P", "audio": False},
    )
    assert task.input == {
        "prompt": "a paper boat",
        "duration": 3,
        "resolution": "720P",
        "aspect_ratio": "16:9",
        "audio": False,
    }
    assert task.estimated_credit_cost == 504


def test_typed_kling_o3_reference_limits_and_4k_rule() -> None:
    with pytest.raises(ValidationError):
        TaskCreate(
            model="kling-video-o-3",
            mode="omni",
            input={"prompt": "edit", "reference_video_id": "video-1", "duration": 11},
        )
    with pytest.raises(ValidationError):
        TaskCreate(
            model="kling-video-o-3",
            mode="reference-to-video",
            input={
                "prompt": "style",
                "resolution": "4K",
                "reference_image_urls": ["https://cdn.example.com/ref.png"],
            },
        )
    with pytest.raises(ValidationError):
        TaskCreate(
            model="kling-video-o-3",
            mode="reference-to-video",
            input={"prompt": "style"},
        )


def test_task_create_openapi_exposes_kling_o3_input_models() -> None:
    schema = TaskCreate.model_json_schema()
    references = {
        item.get("$ref", "").rsplit("/", 1)[-1]
        for item in schema["properties"]["input"]["anyOf"]
    }
    assert {
        "KlingO3TextToVideoInput",
        "KlingO3ImageToVideoInput",
        "KlingO3ReferenceToVideoInput",
    } <= references
