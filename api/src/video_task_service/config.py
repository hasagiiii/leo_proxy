from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VIDEO_SERVICE_",
        case_sensitive=False,
        extra="ignore",
    )

    mysql_dsn: SecretStr = SecretStr(
        "mysql+aiomysql://video_service:video_service@mysql:3306/video_task_service?charset=utf8mb4"
    )
    api_auth_key: SecretStr = SecretStr("local-api-key")
    admin_auth_key: SecretStr = SecretStr("local-admin-key")
    login_worker_auth_key: SecretStr = SecretStr("local-login-worker-key")
    credential_master_key: SecretStr = SecretStr("bG9jYWwtdmlkZW8tdGFzay1tYXN0ZXIta2V5LTAwMDE=")

    upstream_mode: str = "mock"
    service_version: str = "0.1.0"
    leonardo_graphql_url: str = "https://api.leonardo.ai/v1/graphql"
    # Leonardo's current Web client resolves the active model-matrix release
    # through the moving "latest" schema.  Pinning the retired 1.255.2 release
    # now makes otherwise valid Generate mutations fail with an upstream 500.
    leonardo_schema_version: str = "latest"
    media_max_image_bytes: int = Field(default=30 * 1024 * 1024, ge=1)
    media_max_audio_bytes: int = Field(default=30 * 1024 * 1024, ge=1)
    media_max_video_bytes: int = Field(default=200 * 1024 * 1024, ge=1)
    media_dns_over_https_url: str = "https://cloudflare-dns.com/dns-query"
    media_fake_ip_network: str = "198.18.0.0/15"
    media_image_moderation_attempts: int = Field(default=45, ge=1, le=120)
    media_image_moderation_interval_seconds: float = Field(default=2.0, gt=0)
    media_upload_settle_seconds: float = Field(default=8.0, ge=0, le=60)
    media_resolution_concurrency: int = Field(default=3, ge=1, le=20)
    media_image_connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    media_image_read_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    media_circuit_breaker_hosts: str = "cdn.quantv.com"
    media_circuit_breaker_failure_threshold: int = Field(default=3, ge=1, le=100)
    media_circuit_breaker_window_seconds: float = Field(default=60.0, gt=0, le=3600)
    media_circuit_breaker_open_seconds: float = Field(default=60.0, gt=0, le=3600)

    low_balance_threshold: int = Field(default=100, ge=0)
    token_guard_seconds: int = Field(default=120, ge=0)
    account_max_concurrency: int = Field(default=3, ge=1, le=100)
    worker_concurrency: int = Field(default=12, ge=1, le=100)
    queue_lease_seconds: int = Field(default=30, ge=5)
    worker_poll_seconds: float = Field(default=0.25, gt=0)
    worker_max_submit_attempts: int = Field(default=3, ge=1, le=20)
    retry_base_seconds: int = Field(default=2, ge=1)
    upstream_outage_max_submit_attempts: int = Field(default=5, ge=1, le=20)
    upstream_outage_retry_base_seconds: int = Field(default=10, ge=1, le=300)
    account_unavailable_retry_max_seconds: int = Field(default=30, ge=2, le=300)
    sync_batch_size: int = Field(default=20, ge=1, le=500)
    sync_interval_seconds: float = Field(default=0.5, gt=0)
    sync_lease_seconds: int = Field(default=30, ge=5)
    cookie_import_batch_size: int = Field(default=2, ge=1, le=10)
    cookie_import_poll_seconds: float = Field(default=1.0, gt=0, le=30)
    cookie_import_lease_seconds: int = Field(default=120, ge=30, le=900)
    cookie_import_max_attempts: int = Field(default=3, ge=1, le=10)
    task_running_timeout_seconds: int = Field(default=2 * 60 * 60, ge=60)
    account_maintenance_seconds: float = Field(default=1.0, gt=0)
    mock_generation_seconds: float = Field(default=2.0, ge=0.1)
    # Kept for one compatibility window so older desktop builds can still
    # render the legacy idle-account metrics returned by the login-job API.
    login_idle_target: int = Field(default=20, ge=0, le=10000)
    login_active_credit_target: int = Field(default=1_000_000, ge=0)
    login_renewal_window_seconds: int = Field(default=600, ge=0)
    login_job_lease_seconds: int = Field(default=300, ge=30, le=3600)
    login_job_max_batch_size: int = Field(default=20, ge=1, le=100)
    login_job_retry_base_seconds: int = Field(default=60, ge=1, le=3600)
    login_job_nonretryable_retry_seconds: int = Field(default=86400, ge=60)
    login_validation_timeout_seconds: int = Field(default=300, ge=30, le=3600)
    login_activation_max_in_flight: int = Field(default=3, ge=1, le=20)
    login_job_max_account_failures: int = Field(default=5, ge=1, le=20)
    login_job_stalled_max_account_failures: int = Field(default=3, ge=1, le=20)
    login_job_worker_backoff_seconds: int = Field(default=300, ge=30, le=3600)
    protocol_renewal_enabled: bool = True
    protocol_renewal_origin: str = "https://app.leonardo.ai"
    protocol_renewal_browser_profile: str = "chrome136"
    protocol_renewal_cross_origin_cookie_enabled: bool = False
    protocol_renewal_grace_seconds: int = Field(default=420, ge=5, le=3600)
    protocol_renewal_max_attempts: int = Field(default=2, ge=1, le=10)
    protocol_renewal_retry_base_seconds: int = Field(default=300, ge=1, le=3600)
    protocol_renewal_retry_jitter_seconds: int = Field(default=30, ge=0, le=300)
    protocol_renewal_rate_limit_cooldown_seconds: int = Field(
        default=300, ge=1, le=3600
    )
    protocol_renewal_min_interval_seconds: float = Field(default=2.0, ge=0, le=60)
    protocol_renewal_timeout_seconds: float = Field(default=15, gt=0, le=60)
    protocol_renewal_batch_size: int = Field(default=3, ge=1, le=20)
    protocol_renewal_keepalive_enabled: bool = True
    protocol_renewal_keepalive_interval_seconds: int = Field(
        default=15 * 60, ge=60, le=12 * 60 * 60
    )
    protocol_renewal_client_session_max_age_seconds: int = Field(
        default=80 * 60, ge=15 * 60, le=24 * 60 * 60
    )
    protocol_renewal_success_rate_target: float = Field(default=0.7, ge=0, le=1)
    protocol_renewal_heartbeat_seconds: float = Field(default=15, ge=1, le=300)
    protocol_renewal_heartbeat_stale_seconds: int = Field(default=45, ge=5, le=3600)
    protocol_renewal_queue_lag_warn_seconds: int = Field(default=60, ge=1, le=3600)
    protocol_renewal_health_min_sample: int = Field(default=20, ge=1, le=10000)
    protocol_renewal_event_retention_days: int = Field(default=30, ge=1, le=365)
    mailbox_validation_batch_size: int = Field(default=10, ge=1, le=100)
    mailbox_validation_concurrency: int = Field(default=10, ge=1, le=100)
    mailbox_validation_poll_seconds: float = Field(default=2, gt=0, le=60)
    mailbox_validation_lease_seconds: int = Field(default=60, ge=15, le=600)
    mailbox_provider_timeout_seconds: float = Field(default=15, gt=0, le=60)
    registration_job_lease_seconds: int = Field(default=300, ge=30, le=3600)
    registration_job_timeout_seconds: int = Field(default=900, ge=60, le=86400)
    registration_validation_batch_size: int = Field(default=2, ge=1, le=20)
    registration_validation_poll_seconds: float = Field(default=1.0, gt=0, le=60)
    registration_validation_lease_seconds: int = Field(default=120, ge=30, le=900)
    registration_validation_max_attempts: int = Field(default=6, ge=1, le=20)
    registration_validation_retry_base_seconds: int = Field(default=60, ge=1, le=3600)
    registration_validation_retry_max_seconds: int = Field(default=1800, ge=1, le=86400)

    @property
    def mysql_dsn_value(self) -> str:
        return self.mysql_dsn.get_secret_value()

    @property
    def api_auth_key_value(self) -> str:
        return self.api_auth_key.get_secret_value()

    @property
    def admin_auth_key_value(self) -> str:
        return self.admin_auth_key.get_secret_value()

    @property
    def login_worker_auth_key_value(self) -> str:
        return self.login_worker_auth_key.get_secret_value()


@lru_cache
def get_settings() -> Settings:
    return Settings()
