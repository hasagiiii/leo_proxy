from datetime import datetime, timedelta

from video_task_service.api.main import app
from video_task_service.api.stats import protocol_renewal_health
from video_task_service.config import Settings
from video_task_service.models import ProtocolRenewalEvent, ProtocolRenewalRuntime
from video_task_service.syncer import protocol_renewal_event

NOW = datetime(2026, 8, 10, 2, 0, 0)


def observability_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "protocol_renewal_enabled": True,
        "protocol_renewal_heartbeat_stale_seconds": 45,
        "protocol_renewal_queue_lag_warn_seconds": 60,
        "protocol_renewal_health_min_sample": 20,
        "protocol_renewal_success_rate_target": 0.5,
    }
    values.update(overrides)
    return Settings(**values)


def health(**overrides: object):
    values = {
        "settings": observability_settings(),
        "now": NOW,
        "last_heartbeat_at": NOW - timedelta(seconds=10),
        "last_scan_at": NOW - timedelta(seconds=2),
        "last_completed_at": NOW - timedelta(seconds=8),
        "attempts_total": 0,
        "strict_success_rate": None,
        "queue_total": 0,
        "expired_leases": 0,
        "oldest_due_age_seconds": None,
    }
    values.update(overrides)
    return protocol_renewal_health(**values)  # type: ignore[arg-type]


def test_protocol_renewal_health_distinguishes_idle_from_down() -> None:
    assert health().state == "HEALTHY_IDLE"
    down = health(last_heartbeat_at=NOW - timedelta(seconds=46))
    assert down.state == "DOWN"
    assert down.reasons[0].code == "HEARTBEAT_STALE"


def test_protocol_renewal_health_uses_strict_success_target_after_minimum_sample() -> None:
    observing = health(attempts_total=19, strict_success_rate=20.0)
    assert observing.state == "HEALTHY"
    degraded = health(attempts_total=20, strict_success_rate=49.9)
    assert degraded.state == "DEGRADED"
    assert degraded.reasons[0].code == "SUCCESS_RATE_BELOW_TARGET"


def test_protocol_renewal_health_reports_disabled_configuration() -> None:
    result = health(
        settings=observability_settings(protocol_renewal_enabled=False),
        last_heartbeat_at=None,
    )
    assert result.state == "DISABLED"
    assert result.enabled is False


def test_protocol_renewal_event_contains_metrics_without_session_material() -> None:
    event = protocol_renewal_event(
        account_id=7,
        account_uuid="00000000-0000-0000-0000-000000000007",
        attempt_number=2,
        outcome="SUCCEEDED",
        applied=True,
        retryable=False,
        next_state="IDLE",
        error_code=None,
        started_at=NOW,
        finished_at=NOW + timedelta(milliseconds=842),
        previous_token_expires_at=NOW + timedelta(minutes=5),
        renewed_token_expires_at=NOW + timedelta(hours=1),
    )
    assert event.latency_ms == 842
    assert event.applied is True
    assert not {"token", "cookie", "password", "headers"}.intersection(
        ProtocolRenewalEvent.__table__.columns.keys()
    )


def test_protocol_renewal_observability_tables_and_admin_routes_are_registered() -> None:
    assert ProtocolRenewalEvent.__tablename__ == "protocol_renewal_events"
    assert ProtocolRenewalRuntime.__tablename__ == "protocol_renewal_runtime"
    paths = app.openapi()["paths"]
    assert "get" in paths["/admin/stats/protocol-renewals"]
    assert "get" in paths["/admin/protocol-renewals/accounts"]
    assert "get" in paths["/admin/protocol-renewals/accounts/{account_uuid}/events"]
