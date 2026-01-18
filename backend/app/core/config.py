"""
Unified Configuration - Prod-ready settings.

Supports:
- SQLite (dev) / PostgreSQL (prod)
- Local storage / S3/MinIO
- Optional Redis for RQ
"""
import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "gelka-enerji-api"
    env: str = "dev"  # dev | staging | prod

    # ═══════════════════════════════════════════════════════════════════════════
    # Database
    # ═══════════════════════════════════════════════════════════════════════════
    # SQLite (dev): sqlite:///./gelka_enerji.db
    # Postgres (prod): postgresql+psycopg://user:pass@host:5432/db
    database_url: str = "sqlite:///./gelka_enerji.db"

    # ═══════════════════════════════════════════════════════════════════════════
    # Auth
    # ═══════════════════════════════════════════════════════════════════════════
    api_key: str = "dev-key"
    api_key_enabled: bool = False

    # ═══════════════════════════════════════════════════════════════════════════
    # OpenAI
    # ═══════════════════════════════════════════════════════════════════════════
    openai_api_key: str | None = None
    openai_model: str = "gpt-5.2"  # En iyi model (Ocak 2026)
    openai_model_fast: str = "gpt-5.2"  # Hızlı extraction için (gpt-5-mini sorunlu)
    openai_model_accurate: str = "gpt-5.2"  # Doğruluk kritik olduğunda
    openai_max_retries: int = 3
    openai_retry_delay: float = 1.0
    openai_image_detail: str = "high"  # low | high | auto (high = daha doğru okuma)

    # ═══════════════════════════════════════════════════════════════════════════
    # Redis (RQ - opsiyonel)
    # ═══════════════════════════════════════════════════════════════════════════
    redis_url: str | None = None

    # ═══════════════════════════════════════════════════════════════════════════
    # Storage
    # ═══════════════════════════════════════════════════════════════════════════
    storage_backend: str = "local"  # local | s3
    storage_dir: str = "./storage"

    # S3/MinIO
    s3_endpoint_url: str | None = None  # MinIO için: http://minio:9000
    s3_region: str = "us-east-1"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_bucket: str = "invoices"

    # ═══════════════════════════════════════════════════════════════════════════
    # Worker
    # ═══════════════════════════════════════════════════════════════════════════
    worker_poll_interval: float = 1.0

    # ═══════════════════════════════════════════════════════════════════════════
    # Rate Limiting
    # ═══════════════════════════════════════════════════════════════════════════
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 60

    # ═══════════════════════════════════════════════════════════════════════════
    # Multi-tenant
    # ═══════════════════════════════════════════════════════════════════════════
    tenant_required: bool = False  # MVP: false, Prod: true
    default_tenant: str = "default"

    # ═══════════════════════════════════════════════════════════════════════════
    # Helpers
    # ═══════════════════════════════════════════════════════════════════════════
    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def is_s3_storage(self) -> bool:
        return self.storage_backend == "s3"


# Singleton
settings = Settings()
