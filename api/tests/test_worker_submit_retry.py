from video_task_service.config import Settings
from video_task_service.upstream import UpstreamError
from video_task_service.worker import submit_retry_delay_seconds


def test_provider_outage_uses_extended_exponential_backoff() -> None:
    settings = Settings()
    error = UpstreamError(
        "UPSTREAM_PROVIDER_UNAVAILABLE",
        "remote provider returned no data",
    )

    assert submit_retry_delay_seconds(error, 1, settings) == 10
    assert submit_retry_delay_seconds(error, 2, settings) == 20
    assert submit_retry_delay_seconds(error, 3, settings) == 40
    assert submit_retry_delay_seconds(error, 4, settings) == 80
    assert submit_retry_delay_seconds(error, 5, settings) is None


def test_regular_submit_error_keeps_existing_retry_budget() -> None:
    settings = Settings()
    error = UpstreamError("UPSTREAM_GRAPHQL_ERROR", "request-level error")

    assert submit_retry_delay_seconds(error, 1, settings) == 2
    assert submit_retry_delay_seconds(error, 2, settings) == 4
    assert submit_retry_delay_seconds(error, 3, settings) is None


def test_nonretryable_submit_error_is_not_requeued() -> None:
    settings = Settings()
    error = UpstreamError("UPSTREAM_UNAUTHORIZED", "expired", retryable=False)

    assert submit_retry_delay_seconds(error, 1, settings) is None
