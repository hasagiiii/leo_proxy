import pytest

from video_task_service.models import Task
from video_task_service.pricing import PRICING_RULE_VERSION, quote_credit_cost
from video_task_service.schemas import TaskCreate
from video_task_service.worker import refresh_task_credit_quote


@pytest.mark.parametrize(
    ("model", "resolution", "expected"),
    [
        ("seedance-2.0-mini", "480P", 320),
        ("seedance-2.0-mini", "720P", 640),
        ("seedance-2.0", "480P", 562),
        ("seedance-2.0", "720P", 1209),
        ("seedance-2.0", "1080P", 2721),
        ("seedance-2.0", "4K", 7616),
        ("seedance-2.0-fast", "480P", 449),
        ("seedance-2.0-fast", "720P", 967),
    ],
)
def test_seedance_four_second_quote_matrix(model: str, resolution: str, expected: int) -> None:
    assert quote_credit_cost(model, {"duration": 4, "resolution": resolution}) == expected


@pytest.mark.parametrize(
    ("duration", "resolution", "audio", "expected"),
    [
        (4, "480P", False, 720),
        (4, "480P", True, 720),
        (8, "720P", False, 2336),
        (8, "720P", True, 2336),
        (30, "480P", True, 5400),
        (30, "720P", True, 8760),
    ],
)
def test_seedance_25_browser_credit_quotes(
    duration: int,
    resolution: str,
    audio: bool,
    expected: int,
) -> None:
    assert quote_credit_cost(
        "bytedance/seedance-2.5",
        {"duration": duration, "resolution": resolution, "audio": audio},
    ) == expected


@pytest.mark.parametrize(
    ("duration", "resolution", "expected"),
    [
        (4, "480P", 1080),
        (8, "720P", 3776),
        (18, "720P", 8496),
        (30, "720P", 14160),
    ],
)
def test_seedance_25_video_reference_credit_quotes(
    duration: int,
    resolution: str,
    expected: int,
) -> None:
    assert quote_credit_cost(
        "bytedance/seedance-2.5",
        {
            "duration": duration,
            "resolution": resolution,
            "reference_video_urls": ["https://cdn.example.com/reference.mp4"],
        },
    ) == expected


@pytest.mark.parametrize(
    ("duration", "expected"),
    [
        (4, 562),
        (5, 703),
        (6, 843),
        (7, 984),
        (8, 1124),
        (9, 1265),
        (10, 1406),
        (11, 1546),
        (12, 1687),
        (13, 1828),
        (14, 1968),
        (15, 2109),
    ],
)
def test_seedance_standard_480p_browser_duration_quotes(
    duration: int, expected: int
) -> None:
    assert quote_credit_cost(
        "seedance-2.0", {"duration": duration, "resolution": "480P"}
    ) == expected


@pytest.mark.parametrize(
    ("duration", "expected"), [(5, 700), (10, 1400), (15, 2100)]
)
def test_h3_duration_quote(duration: int, expected: int) -> None:
    assert quote_credit_cost("hailuo-03", {"duration": duration, "resolution": "2K"}) == expected


@pytest.mark.parametrize(
    ("resolution", "audio", "expected"),
    [
        ("720P", False, 504),
        ("720P", True, 672),
        ("1080P", False, 672),
        ("1080P", True, 840),
        ("4K", False, 1260),
        ("4K", True, 1260),
    ],
)
def test_kling_o3_three_second_quote_matrix(
    resolution: str, audio: bool, expected: int
) -> None:
    assert quote_credit_cost(
        "kling-video-o-3", {"duration": 3, "resolution": resolution, "audio": audio}
    ) == expected


@pytest.mark.parametrize(
    ("duration", "expected"),
    [(3, 300), (4, 400), (5, 500), (6, 600), (7, 700), (8, 800), (9, 900), (10, 1000)],
)
def test_gemini_omni_flash_duration_quotes(duration: int, expected: int) -> None:
    assert quote_credit_cost(
        "gemini-omni-flash",
        {"duration": duration, "resolution": "720P", "aspect_ratio": "16:9"},
    ) == expected


def test_h3_ratio_does_not_change_five_second_quote() -> None:
    assert {
        quote_credit_cost(
            "hailuo-03", {"duration": 5, "resolution": "2K", "aspect_ratio": ratio}
        )
        for ratio in ("21:9", "16:9", "4:3", "1:1", "3:4", "9:16")
    } == {700}


def test_typed_task_create_overwrites_zero_estimate_with_model_quote() -> None:
    task = TaskCreate(
        model="seedance-2.0",
        mode="text-to-video",
        input={"prompt": "test", "duration": 4, "resolution": "4K"},
    )
    assert task.estimated_credit_cost == 7616


def test_typed_task_create_uses_h3_default_quote() -> None:
    task = TaskCreate(model="hailuo-03", mode="text-to-video", input={"prompt": "test"})
    assert task.estimated_credit_cost == 700


def test_pricing_rule_version_is_stable() -> None:
    assert PRICING_RULE_VERSION == "leonardo-ui-20260812.v15"


def test_worker_requotes_a_queued_task_before_account_selection() -> None:
    task = Task(
        model="seedance-2.0",
        input_json={"prompt": "test", "duration": 4, "resolution": "4K"},
        estimated_credit_cost=0,
    )

    assert refresh_task_credit_quote(task) == 7616
    assert task.estimated_credit_cost == 7616


def test_worker_preserves_standard_rate_for_seedance_video_reference() -> None:
    task = Task(
        model="seedance-2.0",
        mode="reference-to-video",
        input_json={
            "prompt": "test",
            "duration": 15,
            "resolution": "480P",
            "reference_video_urls": ["https://cdn.example.com/reference.mp4"],
        },
        estimated_credit_cost=0,
    )

    assert refresh_task_credit_quote(task) == 2109
    assert task.estimated_credit_cost == 2109


def test_worker_requotes_nano_large_before_account_selection() -> None:
    task = Task(
        model="nano-banana-pro",
        input_json={"prompt": "test", "aspect_ratio": "9:16", "size": "LARGE"},
        estimated_credit_cost=0,
    )

    assert refresh_task_credit_quote(task) == 250
    assert task.estimated_credit_cost == 250


def test_worker_preserves_explicit_estimate_for_unpriced_model() -> None:
    task = Task(
        model="unpriced-model",
        input_json={"prompt": "test"},
        estimated_credit_cost=88,
    )

    assert refresh_task_credit_quote(task) == 88
