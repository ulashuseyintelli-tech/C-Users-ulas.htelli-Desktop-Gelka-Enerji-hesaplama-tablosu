"""
Database configuration and models for customer/offer archive.

Supports:
- SQLite (dev) with check_same_thread=False
- PostgreSQL (prod) with connection pooling
"""
import os
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text, JSON, ForeignKey, Enum as SQLEnum, Boolean, UniqueConstraint
import sqlalchemy as sa
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

from .models import InvoiceStatus, JobType, JobStatus, OfferStatus, AuditAction

# ═══════════════════════════════════════════════════════════════════════════════
# Configuration - Try new config system, fallback to env vars
# ═══════════════════════════════════════════════════════════════════════════════
try:
    from .core.config import settings
    DATABASE_URL = settings.database_url
    STORAGE_DIR = settings.storage_dir
    API_KEY = settings.api_key
    API_KEY_ENABLED = settings.api_key_enabled
    REDIS_URL = settings.redis_url
except ImportError:
    # Fallback for backwards compatibility
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./gelka_enerji.db")
    STORAGE_DIR = os.getenv("STORAGE_DIR", "./storage")
    API_KEY = os.getenv("API_KEY", "dev-key")
    API_KEY_ENABLED = os.getenv("API_KEY_ENABLED", "false").lower() == "true"
    REDIS_URL = os.getenv("REDIS_URL", None)

# ═══════════════════════════════════════════════════════════════════════════════
# Engine Configuration
# ═══════════════════════════════════════════════════════════════════════════════
def create_db_engine():
    """Create database engine with appropriate settings."""
    if DATABASE_URL.startswith("sqlite"):
        # SQLite needs check_same_thread=False for FastAPI
        return create_engine(
            DATABASE_URL,
            connect_args={"check_same_thread": False}
        )
    else:
        # PostgreSQL with connection pooling
        return create_engine(
            DATABASE_URL,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10
        )

engine = create_db_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Invoice(Base):
    """Yüklenen fatura kayıtları - durum takibi ile"""
    __tablename__ = "invoices"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String(64), nullable=False, index=True, default="default")  # Multi-tenant
    
    source_filename = Column(String(255), nullable=False)
    content_type = Column(String(100), nullable=False)
    
    # Storage references (local path veya s3://bucket/key)
    storage_original_ref = Column(String(700), nullable=False)
    storage_page1_ref = Column(String(700), nullable=True)  # PDF'nin 1. sayfa görseli
    
    file_hash = Column(String(64), nullable=True, index=True)  # SHA-256 hash for caching
    
    # Extracted data
    vendor_guess = Column(String(50), nullable=True)
    invoice_period = Column(String(10), nullable=True)  # YYYY-MM
    extraction_json = Column(JSON, nullable=True)
    validation_json = Column(JSON, nullable=True)
    
    # Status tracking
    status = Column(SQLEnum(InvoiceStatus), default=InvoiceStatus.UPLOADED)
    error_message = Column(Text, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Backwards compatibility properties
    @property
    def storage_path(self) -> str:
        """Backwards compatibility for storage_original_ref."""
        return self.storage_original_ref
    
    @property
    def storage_page1_path(self) -> Optional[str]:
        """Backwards compatibility for storage_page1_ref."""
        return self.storage_page1_ref


class Customer(Base):
    """Müşteri kayıtları"""
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, index=True)
    company = Column(String(255), nullable=True)
    email = Column(String(255), nullable=True)
    phone = Column(String(50), nullable=True)
    address = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    offers = relationship("Offer", back_populates="customer")


class Offer(Base):
    """Teklif arşivi"""
    __tablename__ = "offers"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(64), nullable=False, index=True, default="default")  # Multi-tenant
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True)
    
    # Fatura bilgileri
    vendor = Column(String(50), nullable=True)
    invoice_period = Column(String(10), nullable=True)
    consumption_kwh = Column(Float, nullable=False)
    current_unit_price = Column(Float, nullable=False)
    distribution_unit_price = Column(Float, nullable=True)
    demand_qty = Column(Float, nullable=True)
    demand_unit_price = Column(Float, nullable=True)
    
    # Teklif parametreleri
    weighted_ptf = Column(Float, nullable=False)
    yekdem = Column(Float, nullable=False)
    agreement_multiplier = Column(Float, nullable=False)
    
    # Hesaplama sonuçları
    current_total = Column(Float, nullable=False)
    offer_total = Column(Float, nullable=False)
    savings_amount = Column(Float, nullable=False)
    savings_ratio = Column(Float, nullable=False)
    
    # Extra items for Tip-5/7 (reaktif, mahsuplaşma, etc.)
    extra_items_json = Column(JSON, nullable=True)  # [{"label": "Reaktif", "amount_tl": 123.45}]
    extra_items_total_tl = Column(Float, nullable=True, default=0)
    
    # Full calculation result as JSON
    calculation_result = Column(JSON, nullable=True)
    extraction_result = Column(JSON, nullable=True)
    
    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow)
    pdf_ref = Column(String(700), nullable=True)  # Storage ref (local path veya s3://...)
    status = Column(String(50), default="draft")  # draft, sent, accepted, rejected

    # Relationships
    customer = relationship("Customer", back_populates="offers")
    
    # Backwards compatibility
    @property
    def pdf_path(self) -> Optional[str]:
        """Backwards compatibility for pdf_ref."""
        return self.pdf_ref


class Job(Base):
    """Async job queue - DB tabanlı (Redis'e geçiş kolay)"""
    __tablename__ = "jobs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String(64), nullable=False, index=True, default="default")  # Multi-tenant
    invoice_id = Column(String(36), ForeignKey("invoices.id"), index=True)
    
    job_type = Column(SQLEnum(JobType), nullable=False)
    status = Column(SQLEnum(JobStatus), default=JobStatus.QUEUED)
    
    # Worker için input
    payload_json = Column(JSON, nullable=True)
    
    # Sonuç/hata
    result_json = Column(JSON, nullable=True)
    error = Column(String(2000), nullable=True)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)


class AuditLog(Base):
    """Audit log - kim ne zaman ne yaptı"""
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(64), nullable=False, index=True, default="default")
    
    # Actor
    actor_type = Column(String(50), nullable=False, default="system")  # user, system, api_key, webhook
    actor_id = Column(String(100), nullable=True)  # user_id, api_key hash, etc.
    
    # Action
    action = Column(SQLEnum(AuditAction), nullable=False)
    
    # Target
    target_type = Column(String(50), nullable=True)  # invoice, offer, customer
    target_id = Column(String(100), nullable=True)
    
    # Details
    details_json = Column(JSON, nullable=True)  # Ek bilgiler
    ip_address = Column(String(45), nullable=True)  # IPv4/IPv6
    user_agent = Column(String(500), nullable=True)
    
    # Timestamp
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class WebhookConfig(Base):
    """Webhook konfigürasyonları - tenant bazlı"""
    __tablename__ = "webhook_configs"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(64), nullable=False, index=True)
    
    # Webhook URL
    url = Column(String(2000), nullable=False)
    
    # Events to trigger (JSON array)
    events = Column(JSON, nullable=False)  # ["offer_accepted", "offer_rejected", "invoice_extracted"]
    
    # Auth
    secret = Column(String(255), nullable=True)  # HMAC signing secret
    headers_json = Column(JSON, nullable=True)  # Custom headers
    
    # Status
    is_active = Column(Integer, default=1)  # 1=active, 0=disabled
    
    # Stats
    last_triggered_at = Column(DateTime, nullable=True)
    success_count = Column(Integer, default=0)
    failure_count = Column(Integer, default=0)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class WebhookDelivery(Base):
    """Webhook delivery log - gönderim geçmişi"""
    __tablename__ = "webhook_deliveries"

    id = Column(Integer, primary_key=True, index=True)
    webhook_config_id = Column(Integer, ForeignKey("webhook_configs.id"), index=True)
    
    # Event
    event_type = Column(String(100), nullable=False)
    payload_json = Column(JSON, nullable=False)
    
    # Delivery
    status = Column(String(20), nullable=False, default="pending")  # pending, success, failed
    response_status_code = Column(Integer, nullable=True)
    response_body = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    
    # Retry
    attempt_count = Column(Integer, default=0)
    next_retry_at = Column(DateTime, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    delivered_at = Column(DateTime, nullable=True)


# ═══════════════════════════════════════════════════════════════════════════════
# REFERANS VERİ TABLOLARI (Faz 1)
# ═══════════════════════════════════════════════════════════════════════════════

class MarketReferencePrice(Base):
    """
    PTF ve YEKDEM aylık referans fiyatları.
    
    Kaynak: EPİAŞ Şeffaflık Platformu (manuel güncelleme veya API)
    Kullanım: Teklif hesaplamada enerji maliyeti bileşeni
    
    PTF Admin Management (Sprint 10):
    - price_type: Fiyat serisi tipi (PTF, SMF, YEKDEM - gelecek genişleme)
    - status: provisional (geçici) | final (kesinleşmiş)
    - captured_at: Verinin EPİAŞ'tan alındığı tarih
    - source: epias_manual | epias_api | migration | seed
    - change_reason: Değişiklik nedeni (audit)
    
    Unique constraint: (price_type, period)
    """
    __tablename__ = "market_reference_prices"

    id = Column(Integer, primary_key=True, index=True)
    
    # Fiyat tipi ve dönem (composite unique)
    price_type = Column(String(20), nullable=False, default="PTF", index=True)  # PTF, SMF, YEKDEM
    period = Column(String(7), nullable=False, index=True)  # YYYY-MM format
    
    # Fiyatlar (TL/MWh) - DECIMAL(12,2) precision
    ptf_tl_per_mwh = Column(Float, nullable=False)  # Piyasa Takas Fiyatı
    yekdem_tl_per_mwh = Column(Float, nullable=False, default=0)  # YEKDEM bedeli
    
    # Status ve kaynak
    status = Column(String(20), nullable=False, default="provisional")  # provisional | final
    source = Column(String(30), nullable=False, default="epias_manual")  # epias_manual | epias_api | migration | seed
    captured_at = Column(DateTime, nullable=False, default=datetime.utcnow)  # Verinin alındığı tarih
    
    # Meta
    source_note = Column(String(500), nullable=True)  # "EPİAŞ şeffaflık ekranı / manuel"
    change_reason = Column(Text, nullable=True)  # Değişiklik nedeni (audit)
    is_locked = Column(Integer, default=0)  # 1=kilitli (geçmiş dönem), 0=düzenlenebilir
    
    # Audit
    updated_by = Column(String(100), nullable=True)  # Güncelleyen kullanıcı
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Unique constraint: (price_type, period)
    __table_args__ = (
        sa.UniqueConstraint('price_type', 'period', name='uq_market_reference_prices_price_type_period'),
    )


class DistributionTariffDB(Base):
    """
    EPDK Dağıtım Tarifeleri - dönem bazlı sürümleme.
    
    Kaynak: EPDK Tarife Tabloları
    Kullanım: Dağıtım bedeli hesaplama
    
    NOT: Bu tablo in-memory DISTRIBUTION_TARIFFS'ın DB versiyonu.
    Dönem bazlı sürümleme için valid_from/valid_to kullanılır.
    """
    __tablename__ = "distribution_tariffs"

    id = Column(Integer, primary_key=True, index=True)
    
    # Geçerlilik dönemi
    valid_from = Column(String(10), nullable=False, index=True)  # YYYY-MM-DD
    valid_to = Column(String(10), nullable=True)  # YYYY-MM-DD, NULL = hala geçerli
    
    # Tarife bilgileri
    tariff_group = Column(String(20), nullable=False)  # sanayi, kamu_ozel
    voltage_level = Column(String(5), nullable=False)  # AG, OG
    term_type = Column(String(20), nullable=False)  # tek_terim, çift_terim
    
    # Fiyat
    unit_price_tl_per_kwh = Column(Float, nullable=False)
    
    # Meta
    source_note = Column(String(500), nullable=True)  # "EPDK 2025-01 tarifesi"
    
    # Audit
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ═══════════════════════════════════════════════════════════════════════════════
# INCIDENT TABLOSU (Sprint 3)
# ═══════════════════════════════════════════════════════════════════════════════

class Incident(Base):
    """
    Sistem incident'ları - kalite ve hata takibi.
    
    Sprint 6.1 Güncellemeleri:
    - provider, period: Fatura kaynağı ve dönemi
    - dedupe_bucket: 24h TTL için epoch-day
    - primary_flag, action_*: Action router entegrasyonu
    - all_flags, secondary_flags: Multi-flag desteği
    - routed_payload: UI alert / retry schedule / issue payload
    
    Severity:
    - S1: Kritik (hesaplama yapılamadı, veri kaybı riski)
    - S2: Yüksek (yanlış hesaplama riski, manuel müdahale gerekli)
    - S3: Orta (uyarı, sistem çalışıyor ama dikkat gerekli)
    - S4: Düşük (bilgi amaçlı, log)
    
    Status:
    - OPEN: Yeni incident, aksiyon bekliyor
    - ACK: Kabul edildi, üzerinde çalışılıyor
    - RESOLVED: Çözüldü
    - AUTO_RESOLVED: FALLBACK_OK ile otomatik çözüldü
    - PENDING_RETRY: RETRY_LOOKUP bekliyor
    - REPORTED: BUG_REPORT olarak raporlandı
    
    Dedupe:
    - dedupe_key: (provider, invoice_id, primary_flag, category, action_code, period) hash
    - dedupe_bucket: epoch-day (24h TTL)
    - Unique: (tenant_id, dedupe_key, dedupe_bucket)
    """
    __tablename__ = "incidents"

    id = Column(Integer, primary_key=True, index=True)
    
    # Trace
    trace_id = Column(String(50), nullable=False, index=True)
    tenant_id = Column(String(50), nullable=False, default="default", index=True)
    
    # İlişkili kayıtlar (opsiyonel)
    invoice_id = Column(String(50), nullable=True, index=True)
    offer_id = Column(Integer, nullable=True)
    
    # Sprint 6.1: Provider ve Period
    provider = Column(String(100), nullable=True, index=True)
    period = Column(String(7), nullable=True, index=True)  # YYYY-MM
    
    # Incident detayları
    severity = Column(String(5), nullable=False, index=True)  # S1, S2, S3, S4
    category = Column(String(50), nullable=False, index=True)
    message = Column(String(1000), nullable=False)
    details_json = Column(JSON, nullable=True)  # Ek detaylar
    
    # Sprint 6.1: Primary flag ve action bilgileri
    primary_flag = Column(String(50), nullable=True, index=True)
    action_type = Column(String(30), nullable=True, index=True)  # USER_FIX, RETRY_LOOKUP, BUG_REPORT, FALLBACK_OK
    action_owner = Column(String(30), nullable=True)  # user, extraction, tariff, market_price, calc
    action_code = Column(String(50), nullable=True)  # HintCode
    
    # Sprint 6.1: Multi-flag desteği
    all_flags = Column(JSON, nullable=True)  # ["CALC_BUG", "DISTRIBUTION_MISMATCH"]
    secondary_flags = Column(JSON, nullable=True)  # Primary hariç diğerleri
    deduction_total = Column(Integer, nullable=True, default=0)
    
    # Sprint 6.1: Routed payload (UI alert / retry schedule / issue payload)
    routed_payload = Column(JSON, nullable=True)
    
    # Dedupe (Sprint 4 + Sprint 6.1)
    dedupe_key = Column(String(64), nullable=True, index=True)  # SHA256 hash
    dedupe_bucket = Column(Integer, nullable=True, index=True)  # epoch-day for 24h TTL
    occurrence_count = Column(Integer, nullable=False, default=1)
    first_seen_at = Column(DateTime, default=datetime.utcnow)
    last_seen_at = Column(DateTime, default=datetime.utcnow)
    
    # Sprint 7.0: Retry execution
    retry_attempt_count = Column(Integer, nullable=True, default=0)
    retry_eligible_at = Column(DateTime, nullable=True, index=True)
    retry_last_attempt_at = Column(DateTime, nullable=True)
    retry_lock_until = Column(DateTime, nullable=True)
    retry_lock_by = Column(String(100), nullable=True)
    retry_exhausted_at = Column(DateTime, nullable=True)
    
    # Sprint 7.1: Issue integration
    external_issue_id = Column(String(100), nullable=True)
    external_issue_url = Column(String(500), nullable=True)
    reported_at = Column(DateTime, nullable=True)
    
    # Sprint 7.1.2: Reclassification
    reclassified_at = Column(DateTime, nullable=True)
    previous_primary_flag = Column(String(50), nullable=True)
    recompute_count = Column(Integer, nullable=True, default=0)
    
    # Sprint 8.0: Retry Orchestrator
    retry_success = Column(Boolean, nullable=True)  # Lookup başarılı mı (RESOLVED değil!)
    
    # Sprint 8.1: Resolution Reasons
    resolution_reason = Column(String(50), nullable=True)  # ResolutionReason enum değerleri
    
    # Sprint 8.7: Feedback Loop
    feedback_json = Column(JSON, nullable=True)  # Operator feedback for calibration
    
    # Durum
    status = Column(String(20), nullable=False, default="OPEN", index=True)
    resolution_note = Column(String(1000), nullable=True)
    resolved_by = Column(String(100), nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    
    # Audit
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


def init_db():
    """
    Veritabanı tablolarını oluştur.
    
    NOT: Production'da Alembic migration kullanılmalı.
    Bu fonksiyon sadece dev/test için.
    """
    # Production'da migration kullan
    try:
        from .core.config import settings
        if settings.env == "prod":
            import logging
            logging.getLogger(__name__).warning(
                "init_db() called in prod mode. Use 'alembic upgrade head' instead."
            )
            return
    except ImportError:
        pass
    
    Base.metadata.create_all(bind=engine)


def get_db():
    """Database session dependency for FastAPI"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
