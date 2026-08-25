from typing import Any

from pydantic import AliasChoices, BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    api_key: str = Field(
        default="",
        validation_alias=AliasChoices("API_KEY", "OPENAI_API_KEY", "NVIDIA_API_KEY"),
    )
    base_url: str = Field(
        default="https://integrate.api.nvidia.com/v1",
        validation_alias=AliasChoices("BASE_URL", "OPENAI_BASE_URL", "NVIDIA_BASE_URL"),
    )
    models_url: str = ""
    default_model: str = ""
    rate_limit_rpm: float = 40
    bucket_capacity: float = 1
    proxy_port: int = 8100
    database_url: str = "postgresql+asyncpg://prismux_app:replace-this-proxy-password@supabase-db:5432/postgres"
    database_migration_url: str = "postgresql+asyncpg://postgres:replace-this-postgres-password@supabase-db:5432/postgres"
    database_pool_size: int = Field(default=10, ge=1, le=100)
    database_max_overflow: int = Field(default=10, ge=0, le=100)
    supabase_auth_url: str = "http://supabase-auth:9999"
    supabase_public_url: str = "http://localhost:8100"
    supabase_jwt_secret: str = ""
    supabase_jwt_issuer: str = ""
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""
    security_enabled: bool = True
    cookie_secure: bool = True
    cookie_domain: str = ""
    access_cookie_name: str = "rlp_access"
    refresh_cookie_name: str = "rlp_refresh"
    csrf_cookie_name: str = "rlp_csrf"
    trusted_proxy_cidrs: str = "127.0.0.1/32,::1/128"
    api_key_pepper: str = ""
    outbound_disallowed_hosts: str = ""
    outbound_disallowed_cidrs: str = ""
    outbound_disallowed_ports: str = "22,25,53,2375,2376,5432,6379"
    auth_login_attempts: int = Field(default=5, ge=1, le=100)
    auth_login_window_seconds: int = Field(default=300, ge=10, le=86400)
    retention_hours: float = 24  # queue_samples retention (live queue/token chart history)
    payload_retention_days: float = 7  # full request/response payloads (requests table)
    stats_retention_days: float = 365  # lightweight per-request stats (request_stats table)
    settings_encryption_key: str = ""

    # Alerting thresholds (Overview page banners)
    alert_queue_seconds: float = 30  # sustained throttling: queue never empty for this long
    alert_error_rate_pct: float = 10  # elevated error rate over the trailing 5 minutes
    alert_rpm_pct: float = 80  # approaching the configured rate limit ceiling

    def runtime_values(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "models_url": self.models_url,
            "api_key": self.api_key,
            "default_model": self.default_model,
            "rate_limit_rpm": self.rate_limit_rpm,
            "bucket_capacity": self.bucket_capacity,
            "retention_hours": self.retention_hours,
            "payload_retention_days": self.payload_retention_days,
            "stats_retention_days": self.stats_retention_days,
            "alert_queue_seconds": self.alert_queue_seconds,
            "alert_error_rate_pct": self.alert_error_rate_pct,
            "alert_rpm_pct": self.alert_rpm_pct,
        }


class RuntimeSettings(BaseModel):
    base_url: str
    models_url: str = ""
    api_key: str = ""
    default_model: str = ""
    rate_limit_rpm: float = Field(gt=0, le=1_000_000)
    bucket_capacity: float = Field(gt=0, le=1_000_000)
    retention_hours: float = Field(gt=0)
    payload_retention_days: float = Field(gt=0)
    stats_retention_days: float = Field(gt=0)
    alert_queue_seconds: float = Field(gt=0)
    alert_error_rate_pct: float = Field(ge=0, le=100)
    alert_rpm_pct: float = Field(ge=0, le=100)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise ValueError("Base URL must start with http:// or https://")
        return value

    @field_validator("models_url")
    @classmethod
    def validate_models_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if value and not value.startswith(("http://", "https://")):
            raise ValueError("Models URL must start with http:// or https://")
        return value

    @field_validator("default_model")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @classmethod
    def from_sources(cls, env_settings: Settings, stored: dict[str, Any]) -> "RuntimeSettings":
        values = env_settings.runtime_values()
        values.update({key: value for key, value in stored.items() if key in values})
        return cls.model_validate(values)


settings = Settings()
