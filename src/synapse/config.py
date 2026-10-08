"""Application configuration.

All runtime configuration is loaded through pydantic-settings so that
env vars are validated once at boot, secrets are never logged, and the
service fails fast (not at request time) when required settings are missing.

The single source of truth is the ``Settings`` class below. Import the
cached singleton with ``from synapse.config import get_settings``.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Synapse runtime settings — populated from environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="SYNAPSE_",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Runtime profile ──────────────────────────────────────────────────────
    env: Literal["development", "test", "production"] = "development"

    # ── Application ──────────────────────────────────────────────────────────
    app_name: str = "AgentCraft Synapse"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    request_id_header: str = "X-Request-ID"

    # ── Database ─────────────────────────────────────────────────────────────
    db_url: str = "sqlite+aiosqlite:///./synapse.db"
    db_echo: bool = False
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_connect_timeout: int = 5

    # ── Auth ──────────────────────────────────────────────────────────────────
    auth_mode: Literal["development", "production"] = "development"
    auth_provider: str | None = None  # set when production IdP is configured
    dev_api_keys: str = ""  # comma-separated
    admin_api_keys: str = ""  # comma-separated

    # ── CORS ─────────────────────────────────────────────────────────────────
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    cors_allow_credentials: bool = True

    # ── Rate limit ────────────────────────────────────────────────────────────
    rate_limit_per_minute: int = 120
    rate_limit_burst: int = 20

    # ── SSRF ──────────────────────────────────────────────────────────────────
    ssrf_allow_private: bool = False
    ssrf_fetch_timeout_seconds: int = 10
    ssrf_max_response_bytes: int = 10 * 1024 * 1024

    # ── Observability ────────────────────────────────────────────────────────
    log_json: bool = False
    otel_exporter: str = ""

    # ── Derived helpers ──────────────────────────────────────────────────────

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def dev_api_key_set(self) -> set[str]:
        return {k.strip() for k in self.dev_api_keys.split(",") if k.strip()}

    @property
    def admin_api_key_set(self) -> set[str]:
        return {k.strip() for k in self.admin_api_keys.split(",") if k.strip()}

    # ── Validators ────────────────────────────────────────────────────────────

    @field_validator("env", mode="after")
    @classmethod
    def _normalize_env(cls, v: str) -> str:
        return v.lower()

    @model_validator(mode="after")
    def _fail_fast_on_insecure_production(self) -> Settings:
        """Fail closed: production cannot run without explicit auth + CORS."""
        if self.env == "production":
            if self.auth_mode == "development":
                raise ValueError(
                    "SYNAPSE_AUTH_MODE=development is forbidden when "
                    "SYNAPSE_ENV=production (fail-closed)."
                )
            if not self.auth_provider:
                raise ValueError("SYNAPSE_AUTH_PROVIDER must be set in production.")
            if "*" in self.cors_origin_list and self.cors_allow_credentials:
                raise ValueError("CORS '*' with credentials is forbidden in production.")
        if self.env == "test":
            # tests should never hit a real DB
            if "postgresql" in self.db_url:
                raise ValueError(
                    "Tests must not use PostgreSQL — set SYNAPSE_DB_URL "
                    "to a sqlite+aiosqlite:// URL."
                )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached singleton — validated once at first import."""
    return Settings()
