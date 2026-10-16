"""Application configuration.

Every value is sourced from the environment (or a local ``.env`` file) so that
no secret or environment-specific value is ever committed to source control.

Note the distinction between two kinds of configuration in this system:

* **Runtime/infrastructure configuration** (this module) - database URL, secret
  key, token lifetimes, CORS. Known at process start, immutable afterwards.
* **Business policy configuration** (``app.modules.settings``) - leave year,
  grace periods, statutory rates. Stored as database rows so HR/Admin can change
  them without a redeploy, and so historical records can reference the version
  that applied at the time.

Business rules must never be hardcoded here.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, PostgresDsn, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

AppEnv = Literal["local", "test", "staging", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="IAMS_",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------------------ app
    app_env: AppEnv = "local"
    app_name: str = "Ishvara Attendance Management System"
    app_version: str = "0.1.0"
    api_v1_prefix: str = "/api/v1"
    debug: bool = False

    # ------------------------------------------------------------- database
    database_url: PostgresDsn = Field(
        default="postgresql+psycopg://iams:iams_local_dev@localhost:5433/iams",
        description="SQLAlchemy URL. Must use the psycopg (v3) driver.",
    )
    db_echo: bool = False
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_pre_ping: bool = True
    db_statement_timeout_ms: int = Field(
        default=30_000,
        description="Per-session statement timeout; guards against runaway report queries.",
    )

    # ------------------------------------------------------------- security
    secret_key: SecretStr = Field(
        default=SecretStr("dev-only-insecure-secret-change-me"),
        description="Signing key for access/refresh tokens. MUST be overridden outside local.",
    )
    jwt_algorithm: Literal["HS256", "HS512"] = "HS256"
    access_token_ttl_minutes: int = 30
    refresh_token_ttl_days: int = 14

    # Argon2id parameters. Defaults follow the argon2-cffi "low memory" profile,
    # which is a reasonable starting point for a small self-hosted deployment.
    # Tune with `iams benchmark-hash` once the production host is known.
    argon2_time_cost: int = 3
    argon2_memory_cost_kib: int = 65_536
    argon2_parallelism: int = 2

    # Field-level encryption for bank account numbers and PAN. Generate with
    # `iams generate-encryption-key`. Deliberately separate from secret_key:
    # rotating the token-signing key must not make stored data unreadable.
    field_encryption_key: SecretStr | None = None
    field_encryption_keys_retired: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        description=(
            "Previous keys, tried on decrypt only, to allow rotation without "
            "downtime. Remove an entry once 'iams rotate-encryption-key' has "
            "re-encrypted everything."
        ),
    )

    # File uploads (employment documents).
    storage_dir: str = Field(
        default="storage",
        description="Directory for uploaded files, relative to the backend root.",
    )
    max_upload_bytes: int = Field(
        default=10 * 1024 * 1024,
        description="Largest accepted upload. Guards against disk exhaustion.",
    )

    # Login throttling (per username+IP pair).
    login_max_attempts: int = 5
    login_lockout_minutes: int = 15
    login_attempt_window_minutes: int = 15

    # ------------------------------------------------------------------ web
    # NoDecode stops pydantic-settings from JSON-parsing the raw environment
    # value, which lets the validator below accept a plain comma-separated list.
    cors_allow_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )
    docs_enabled: bool = True

    # ------------------------------------------------------------ behaviour
    default_timezone: str = Field(
        default="Asia/Kolkata",
        description=(
            "Fallback IANA timezone used only when a work location has none set. "
            "Attendance calculations prefer the employee's work-location timezone."
        ),
    )

    @field_validator("database_url")
    @classmethod
    def _require_psycopg_driver(cls, value: PostgresDsn) -> PostgresDsn:
        scheme = value.scheme
        if scheme not in {"postgresql+psycopg", "postgresql"}:
            raise ValueError(
                f"Unsupported database scheme {scheme!r}. Use 'postgresql+psycopg://' (psycopg v3)."
            )
        return value

    @field_validator("field_encryption_keys_retired", mode="before")
    @classmethod
    def _split_retired_keys(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("cors_allow_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept either a comma-separated string or a JSON array."""
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                import json

                return json.loads(text)
            return [item.strip() for item in text.split(",") if item.strip()]
        return value

    @property
    def sqlalchemy_url(self) -> str:
        """Normalised URL that always names the psycopg v3 driver explicitly."""
        url = str(self.database_url)
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        return url

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    def assert_production_safe(self) -> None:
        """Fail fast on configurations that are acceptable locally but not in production."""
        problems: list[str] = []
        if self.secret_key.get_secret_value() == "dev-only-insecure-secret-change-me":
            problems.append("IAMS_SECRET_KEY is still the insecure development default.")
        if len(self.secret_key.get_secret_value()) < 32:
            problems.append("IAMS_SECRET_KEY must be at least 32 characters.")
        if self.debug:
            problems.append("IAMS_DEBUG must be false in production.")
        if "*" in self.cors_allow_origins:
            problems.append("IAMS_CORS_ALLOW_ORIGINS must not be a wildcard in production.")
        if not self.field_encryption_key:
            problems.append(
                "IAMS_FIELD_ENCRYPTION_KEY must be set in production, or bank and "
                "statutory details cannot be stored."
            )
        if problems:
            raise RuntimeError(
                "Refusing to start in production with an unsafe configuration:\n  - "
                + "\n  - ".join(problems)
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton.

    Cached so that importing modules never re-read the environment. Tests clear
    the cache via ``get_settings.cache_clear()``.
    """
    return Settings()
