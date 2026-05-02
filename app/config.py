"""Application settings loaded from environment variables.

Reads `.env` in development; in production env vars are injected by the host.
Access via `from app.config import settings`.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- App ---
    app_env: Literal["development", "staging", "production"] = "development"
    app_name: str = "polier-pilot"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_base_url: str = "http://localhost:8000"
    log_level: str = "INFO"
    timezone: str = "Europe/Berlin"

    # --- Security ---
    encryption_key: SecretStr
    secret_key: SecretStr

    # --- Database ---
    database_url: str
    database_url_sync: str
    postgres_user: str = "polier"
    postgres_password: SecretStr = SecretStr("polier")
    postgres_db: str = "polier"

    # --- Redis / RQ ---
    redis_url: str = "redis://redis:6379/0"

    # --- Twilio ---
    twilio_account_sid: str
    twilio_auth_token: SecretStr
    twilio_whatsapp_from: str = "whatsapp:+14155238886"
    twilio_webhook_validate: bool = True

    # --- Telegram ---
    telegram_bot_token: SecretStr | None = None
    telegram_webhook_secret: SecretStr | None = None

    # --- OpenAI ---
    openai_api_key: SecretStr
    openai_whisper_model: str = "whisper-1"

    # --- Anthropic ---
    anthropic_api_key: SecretStr
    anthropic_model_generation: str = "claude-sonnet-4-5"
    anthropic_model_classification: str = "claude-haiku-4-5"

    # --- Storage ---
    storage_backend: Literal["local", "s3"] = "local"
    storage_local_path: Path = Path("/app/storage")
    s3_endpoint_url: str | None = None
    s3_bucket: str | None = None
    s3_access_key: SecretStr | None = None
    s3_secret_key: SecretStr | None = None
    s3_region: str = "eu-central"

    # --- Email ---
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str = "no-reply@polier-pilot.de"

    # --- Weather ---
    open_meteo_base_url: str = "https://archive-api.open-meteo.com/v1/archive"

    # --- Retention ---
    raw_audio_retention_days: int = Field(default=90, ge=1)
    report_retention_days: int = Field(default=730, ge=1)

    # ---- Validators ----
    @field_validator("database_url")
    @classmethod
    def _check_async_dsn(cls, v: str) -> str:
        if "+asyncpg" not in v:
            raise ValueError("DATABASE_URL must use the asyncpg driver (postgresql+asyncpg://...)")
        return v

    @field_validator("database_url_sync")
    @classmethod
    def _check_sync_dsn(cls, v: str) -> str:
        if "+psycopg2" not in v and "+psycopg" not in v:
            raise ValueError("DATABASE_URL_SYNC must use psycopg/psycopg2 driver (for Alembic)")
        return v

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor - read once, reuse forever."""
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
