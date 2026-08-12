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
    openai_model: str = "gpt-4o"  # Default model
    openai_model_fast: str = "gpt-4o-mini"  # Hızlı extraction için
    openai_model_accurate: str = "gpt-4o"  # Doğruluk kritik olduğunda
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
    # S5 — Outreach (SMTP + compliance)
    #
    # openai_api_key ile AYNI desen: dev'de .env, packaged'da electron/main.js
    # loadMachineLocalEnv() ile userData/machine-local.env'den process.env'e
    # enjekte edilir — parola BURADA sabit kodlanmaz, git'e girmez, installer'a
    # gömülmez (owner kararı, S5 GO). Owner'ın SMTP kararı (10.08): host/port/
    # security transport parametreleri de config'ten okunur, HARD-CODE edilmez
    # — V1 tercih: 587+STARTTLS, fallback: 465+implicit-TLS (ikisi de canlı
    # test edildi, PASS).
    # ═══════════════════════════════════════════════════════════════════════════
    outreach_smtp_host: str | None = None
    outreach_smtp_port: int = 587
    outreach_smtp_security: str = "starttls"  # starttls | implicit_tls
    outreach_smtp_username: str | None = None
    outreach_smtp_password: str | None = None

    # Owner-onaylı test-adres whitelist (virgülle ayrılmış) — YALNIZ bu
    # listedeki adresler recipient_category=TEST_RECIPIENT compliance
    # bypass'ından yararlanabilir (bkz. app/outreach/compliance.py).
    # Kullanıcı girdisinden ASLA türetilmez, yalnız bu config'ten okunur.
    outreach_test_recipient_emails: str = ""

    # IYS_UNKNOWN | IYS_VERIFIED | IYS_BLOCKED | IYS_NOT_REQUIRED_OR_SPECIAL_CASE
    # Owner kararı (10.08): "IYS: UNKNOWN / CREDENTIALS NOT PROVIDED" — gerçek
    # İYS entegrasyonu yazılmadı, bu yüzden varsayılan IYS_UNKNOWN; PROSPECT_
    # RECIPIENT gönderimi bu değer değişene kadar hard-blocked kalır.
    outreach_iys_status: str = "IYS_UNKNOWN"

    @property
    def outreach_test_recipient_email_set(self) -> set[str]:
        return {
            e.strip().lower()
            for e in self.outreach_test_recipient_emails.split(",")
            if e.strip()
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # S5 — Outreach: gönderici (Gelka) kimlik profili
    #
    # Owner'ın 10.08 düzeltmesi madde 5: "Mandatory sender identification
    # recipient'tan türetilmeyecek. Verified GELKA sender profile'dan
    # gelecek." Bu alanların GERÇEK değerlerini (MERSİS no, tescilli unvan
    # vb.) Claude İCAT EDEMEZ — owner tarafından doldurulmalı. Boş bırakılırsa
    # app/outreach/sender_profile.py::render_mandatory_footer() fail-closed
    # SenderProfileIncompleteError fırlatır (draft üretimi durur, sahte/eksik
    # bir yasal footer ile ASLA devam etmez).
    # ═══════════════════════════════════════════════════════════════════════════
    outreach_sender_trade_name: str | None = None  # tescilli ticaret unvanı
    outreach_sender_mersis_number: str | None = None
    outreach_sender_email: str | None = None  # boşsa outreach_smtp_username'e düşer
    outreach_sender_phone: str | None = None
    outreach_sender_website: str | None = None
    outreach_sender_privacy_notice_url: str | None = None
    outreach_sender_unsubscribe_instruction: str | None = None

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
