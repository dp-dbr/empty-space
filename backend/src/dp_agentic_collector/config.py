"""Settings from environment variables ``DP_AC_*`` (and optional .env file)."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DP_AC_", env_file=".env", extra="ignore")

    env: str = "dev"
    # migrate: alembic upgrade head on start; create_all: metadata.create_all (tests); none
    schema_mode: str = "migrate"
    database_url: str = "sqlite:///./var/dpac.db"
    data_dir: Path = Path("./var")
    plugins_dirs: list[Path] = Field(default_factory=lambda: [Path(__file__).resolve().parents[2] / "plugins"])
    agents_dir: Path | None = None

    artifact_store: str = "git"
    projection: str = "sql"
    attachment_store: str = "filesystem"
    credential_vault: str = "db"
    task_queue: str = "in_process"
    identity_provider: str = "local"

    # Comma-separated Fernet keys; first is used for encryption (rotation).
    vault_keys: str = ""
    secret_key: str = "dev-insecure-change-me"

    default_llm_provider: str | None = None  # overrides AgentDefinition provider when set
    default_llm_model: str | None = None
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    openai_base_url: str | None = None

    attachment_max_bytes: int = 20 * 1024 * 1024
    attachment_mime_allowlist: list[str] = Field(default_factory=lambda: [
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/csv", "text/plain", "text/markdown", "application/json",
        "image/png", "image/jpeg",
    ])

    run_worker: bool = True
    worker_poll_seconds: float = 0.5
    public_base_url: str = "http://localhost:8000"
    cookie_secure: bool = False
    login_rate_limit: int = 10  # attempts per 5 minutes per (tenant, email)
    otlp_endpoint: str | None = None
    log_json: bool = True
    mcp_allowed_hosts: list[str] = Field(default_factory=list)
    default_tenant: str = "default"
