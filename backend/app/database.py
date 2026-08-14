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
from sqlalchemy.orm import declarative_base
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


class PriceChangeHistory(Base):
    """
    Piyasa fiyat değişiklik geçmişi — append-only audit tablosu.
    
    Her başarılı INSERT veya UPDATE işlemi sonrası bir kayıt oluşturulur.
    No-op (aynı değer + aynı status) durumunda kayıt OLUŞTURULMAZ.
    
    Audit History Spec:
    - action: INSERT (yeni kayıt) | UPDATE (güncelleme)
    - old_value/old_status: INSERT'te NULL, UPDATE'te önceki değerler
    - Append-only: Bu tabloda UPDATE veya DELETE yapılmaz
    - Best-effort: History write hatası parent upsert'ü etkilemez
    
    FK: price_record_id → market_reference_prices.id (ON DELETE RESTRICT)
    Denormalized: price_type, period (JOIN'siz query performansı için)
    """
    __tablename__ = "price_change_history"

    id = Column(Integer, primary_key=True, index=True)
    price_record_id = Column(
        Integer,
        ForeignKey("market_reference_prices.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    
    # Denormalized — query performance (JOIN'siz filtreleme)
    price_type = Column(String(20), nullable=False)   # PTF, SMF, YEKDEM
    period = Column(String(7), nullable=False)         # YYYY-MM
    
    # Değişiklik detayları
    action = Column(String(10), nullable=False)        # INSERT | UPDATE
    old_value = Column(Float, nullable=True)           # NULL for INSERT
    new_value = Column(Float, nullable=False)
    old_status = Column(String(20), nullable=True)     # NULL for INSERT
    new_status = Column(String(20), nullable=False)
    
    # Audit alanları
    change_reason = Column(Text, nullable=True)
    updated_by = Column(String(100), nullable=True)
    source = Column(String(30), nullable=True)         # epias_manual, epias_api, migration, seed
    
    # Timestamp
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


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

    # PDSMR-R1D/2R2: Bu unique index migration 004'te ZATEN vardi
    # (CREATE UNIQUE INDEX ix_incidents_dedupe_unique ON incidents
    #  (tenant_id, dedupe_key, dedupe_bucket)) ama modelde TANIMLI DEGILDI —
    # yalniz yukaridaki docstring'de anlatilmisti. Sonucu: create_all() ile
    # kurulan taze bir DB dedupe benzersizligini DB seviyesinde HIC
    # zorlamiyordu. Burada canonical alembic ciktisiyla BIREBIR ayni
    # tanim bildiriliyor; yeni bir sema niyeti yaratilmiyor.
    __table_args__ = (
        sa.Index(
            "ix_incidents_dedupe_unique",
            "tenant_id", "dedupe_key", "dedupe_bucket",
            unique=True,
        ),
    )

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


# ═══════════════════════════════════════════════════════════════════════════════
# SÖZLEŞME OLUŞTURMA TABLOLARI (Contract Generation V1)
#
# Vergi levhası + imza sirküleri OCR çıkarımından abone tüzel kişilik/yetkili
# bilgisi + mevcut Offer'ın agreement_multiplier'ı birleştirilerek elektrik
# satış sözleşmesi PDF'i üretilir. Çağrıldığı yerler: bkz. app/contracts/router.py
# (Faz 4'te eklenecek). T.C. kimlik no bilinçli olarak düz metin saklanır (V1
# kararı — encryption-at-rest gerekmiyor) ama log/hata mesajı/ilgisiz list
# endpoint'lerine hiçbir zaman yazılmaz (bkz. app/contracts/schemas.py response
# modelleri, alan bazında hariç tutma).
# ═══════════════════════════════════════════════════════════════════════════════


class CustomerLegalProfile(Base):
    """
    Müşterinin tüzel kişilik/vergi bilgileri — vergi levhası + imza sirkülerinden
    OCR ile çıkarılıp kullanıcı onayından geçtikten sonra burada saklanır.
    Customer'ın kendisine değil ayrı tabloya konur: offer/invoice akışlarının
    çoğu bu alanlara hiç ihtiyaç duymaz, Customer'ı şişirmemek için ayrıldı.
    """
    __tablename__ = "customer_legal_profiles"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(64), nullable=False, default="default", index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True, index=True)

    legal_name = Column(String(255), nullable=False)
    tax_number = Column(String(20), nullable=False)
    tax_office = Column(String(255), nullable=False)
    mersis_number = Column(String(32), nullable=True)
    trade_registry_number = Column(String(64), nullable=True)
    registered_address = Column(Text, nullable=False)
    facility_address = Column(Text, nullable=True)
    notification_address = Column(Text, nullable=True)

    verification_status = Column(String(20), nullable=False, default="pending", index=True)  # pending|confirmed

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    customer = relationship("Customer")
    representatives = relationship("CustomerAuthorizedRepresentative", back_populates="legal_profile")


class CustomerAuthorizedRepresentative(Base):
    """
    Bir tüzel kişiliğin birden fazla yetkilisi, müşterek/münferit temsil biçimi
    ve süreli/süresiz yetkisi olabileceği için ayrı tabloda (imza sirkülerinden).
    T.C. kimlik no burada DÜZ METİN saklanır (V1 owner kararı) — yalnız
    contract-hazırlama/review/preview/final PDF akışlarında gösterilir, genel
    listeleme/log/hata mesajlarına asla yazılmaz (uygulama katmanında garanti
    edilir, bkz. app/contracts/schemas.py).
    """
    __tablename__ = "customer_authorized_representatives"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(64), nullable=False, default="default", index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True, index=True)
    legal_profile_id = Column(Integer, ForeignKey("customer_legal_profiles.id"), nullable=True, index=True)

    full_name = Column(String(255), nullable=False)
    national_id = Column(String(11), nullable=False)  # T.C. kimlik no, düz metin (V1 kararı)

    authority_type = Column(String(50), nullable=True)  # Münferiden / Müştereken
    authority_scope = Column(String(255), nullable=True)  # Temsil şekli metni
    authority_start_date = Column(DateTime, nullable=True)
    authority_end_date = Column(DateTime, nullable=True)
    is_indefinite = Column(Boolean, nullable=False, default=False)  # "Aksi Karar Alınıncaya Kadar"

    source_document_id = Column(Integer, ForeignKey("uploaded_reference_documents.id"), nullable=True)
    verification_status = Column(String(20), nullable=False, default="pending", index=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    legal_profile = relationship("CustomerLegalProfile", back_populates="representatives")


class UploadedReferenceDocument(Base):
    """
    Vergi levhası / imza sirküleri gibi sözleşme-öncesi referans belge yüklemeleri.
    Invoice tablosundaki file_hash dedup deseni tekrarlanır ama belge tipinden
    bağımsız, tek tabloda (document_type ile ayrışır).
    """
    __tablename__ = "uploaded_reference_documents"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(64), nullable=False, default="default", index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True, index=True)

    document_type = Column(String(30), nullable=False, index=True)  # vergi_levhasi | imza_sirkusu
    original_filename = Column(String(500), nullable=False)
    mime_type = Column(String(100), nullable=False)
    file_size = Column(Integer, nullable=False)
    sha256 = Column(String(64), nullable=False, index=True)
    storage_ref = Column(String(500), nullable=False)
    document_date = Column(DateTime, nullable=True)  # belgenin kendi tarihi (imza sirküsü tarihi vb.)

    processing_status = Column(String(20), nullable=False, default="uploaded", index=True)
    # uploaded | extracting | extracted | failed

    uploaded_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        # document_type dahil: aynı dosya (sha256) farklı belge türüyle
        # yüklenirse dedup'a takılmaz, ayrı kayıt olur (bkz. migration
        # dc8343278cfa — LOCAL UAT FINAL REMEDIATION, dedup yanlış
        # document_type'ı kalıcılaştırıyordu).
        UniqueConstraint("tenant_id", "customer_id", "sha256", "document_type", name="uq_reference_doc_dedup"),
    )


class DocumentExtractionRun(Base):
    """
    Ham model çağrısının kaydı — normalize edilmiş field_candidates tablosundan
    ayrı tutulur ki tek bir extraction denemesi (model/prompt versiyonu, hata
    durumu) audit edilebilsin ve tekrar çalıştırma geçmişi korunsun.
    """
    __tablename__ = "document_extraction_runs"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(64), nullable=False, default="default", index=True)
    document_id = Column(Integer, ForeignKey("uploaded_reference_documents.id"), nullable=False, index=True)

    extractor_type = Column(String(50), nullable=False)  # tax_certificate | signature_circular
    extractor_version = Column(String(20), nullable=False)
    model_name = Column(String(50), nullable=False)
    prompt_version = Column(String(20), nullable=False)

    status = Column(String(20), nullable=False, default="running", index=True)  # running|completed|failed
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    raw_response_ref = Column(String(500), nullable=True)  # storage_ref, DB'ye ham JSON gömülmez
    error_code = Column(String(100), nullable=True)


class DocumentFieldCandidate(Base):
    """
    Extraction run'ın ürettiği HER alan adayı — value/confidence/source/evidence
    ile birlikte. Belgeler arası çelişki tespiti bu tablo üzerinden (aynı
    field_name, farklı document_id, farklı normalized_value) yapılır.
    """
    __tablename__ = "document_field_candidates"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(64), nullable=False, default="default", index=True)
    extraction_run_id = Column(Integer, ForeignKey("document_extraction_runs.id"), nullable=False, index=True)
    document_id = Column(Integer, ForeignKey("uploaded_reference_documents.id"), nullable=False, index=True)

    field_name = Column(String(100), nullable=False, index=True)  # örn. legal_name, tax_office
    raw_value = Column(Text, nullable=True)
    normalized_value = Column(Text, nullable=True)
    confidence = Column(Float, nullable=False, default=0.0)
    source_page = Column(Integer, nullable=False, default=1)
    source_text = Column(Text, nullable=True)  # evidence

    validation_status = Column(String(20), nullable=False, default="pending", index=True)
    # pending | confirmed | overridden | rejected
    conflict_status = Column(String(20), nullable=False, default="none", index=True)
    # none | conflict | resolved

    user_decision = Column(String(20), nullable=True)  # accepted | corrected | rejected
    corrected_value = Column(Text, nullable=True)
    decided_by = Column(String(100), nullable=True)
    decided_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)


class Contract(Base):
    """
    Üretilen sözleşme kaydı. FINALIZED sonrası contract_snapshot_json donar,
    Customer/Offer/CustomerLegalProfile sonradan değişse bile bu kayıt PDF'te
    ne yazdığını birebir yansıtmaya devam eder (immutable snapshot).
    """
    __tablename__ = "contracts"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(64), nullable=False, default="default", index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True, index=True)
    offer_id = Column(Integer, ForeignKey("offers.id"), nullable=False, index=True)
    legal_profile_id = Column(Integer, ForeignKey("customer_legal_profiles.id"), nullable=True)
    authorized_representative_id = Column(Integer, ForeignKey("customer_authorized_representatives.id"), nullable=True)

    contract_number = Column(String(50), nullable=True, unique=True, index=True)
    status = Column(String(30), nullable=False, default="DRAFT", index=True)
    # DRAFT|DOCUMENTS_UPLOADED|EXTRACTION_COMPLETED|REVIEW_REQUIRED|READY_TO_GENERATE|GENERATED|FINALIZED|VOID

    start_date = Column(DateTime, nullable=True)
    end_date = Column(DateTime, nullable=True)
    template_version = Column(String(20), nullable=True)

    extraction_snapshot_json = Column(JSON, nullable=True)  # review ekranındaki tüm aday+karar durumu
    contract_snapshot_json = Column(JSON, nullable=True)  # FINALIZED anında donan tüm alanlar

    pdf_storage_ref = Column(String(500), nullable=True)
    pdf_sha256 = Column(String(64), nullable=True)

    created_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    finalized_at = Column(DateTime, nullable=True)

    offer = relationship("Offer")
    customer = relationship("Customer")


class Activity(Base):
    """
    Geçmişte gerçekleşmiş olay kaydı (S2 — Activity & Task Engine).

    Owner kararı: Activity ≠ Task. Activity geçmişi temsil eder (NOTE/CALL/
    EMAIL/MEETING/TASK_COMPLETED), Task geleceği (yapılacak aksiyon). Bir
    Task tamamlanınca TASK_COMPLETED tipinde bir Activity üretilebilir
    (idempotent, tek seferlik — bkz. app/services/task_service.py).

    Subject modeli: typed nullable FK (customer_id/offer_id/contract_id) —
    S2 preflight'ta mevcut mimarinin HİÇBİR yerde polymorphic subject_type+
    subject_id kullanmadığı doğrulandı (Contract/CustomerLegalProfile/
    UploadedReferenceDocument hep typed FK), bu yüzden mevcut desene
    uyuldu. Tam olarak biri dolu olmalı — service katmanında fail-closed
    kontrol edilir (bkz. app/services/activity_service.py), DB seviyesinde
    CHECK constraint YOK (mevcut projede hiçbir yerde CHECK kullanılmıyor,
    yeni bir konvansiyon icat edilmedi).

    Offer status geçişleri BURAYA duplicate yazılmaz — onlar zaten
    audit_logs'ta (AuditAction.OFFER_STATUS_CHANGED) organik olarak var;
    Offer timeline'ı sunulurken audit_logs + bu tablo birlikte projection
    olarak birleştirilir (bkz. app/services/activity_service.py
    get_offer_timeline()). Contract/Customer için audit_logs'ta hiçbir
    olay yok (S2 preflight'ta doğrulandı) — onların timeline'ı yalnız bu
    tablodan gelir.

    created_by alanı BİLİNÇLİ OLARAK yok — S2 preflight'ta canonical bir
    User/Identity modelinin projede mevcut olmadığı doğrulandı (owner
    kararı, madde 5: SINGLE OPERATOR — yeni auth sistemi icat edilmedi).

    Silme: hard-delete edilmez (audit niteliğinde); mevcut projede henüz
    bir soft-delete/archive konvansiyonu yok, S2 de yeni bir framework
    icat etmedi — bu tablo için hiç DELETE endpoint'i eklenmedi.

    FK'lerde ondelete BİLİNÇLİ OLARAK belirtilmedi — S2 preflight'ta
    Customer/Offer/Contract graph'ındaki TÜM FK'lerin (main.py delete_
    customer dahil) hiçbirinde ondelete kullanılmadığı, SQLite'ta zaten
    FK enforcement kapalı olduğu doğrulandı. Subject hard-delete edilirse
    bu satırlar orphan kalır — mevcut projenin HER YERİNDEKİ (Offer.
    customer_id, Contract.customer_id vb.) davranışıyla tutarlı, CASCADE
    KULLANILMADI (owner kararı: "Audit Activity'nin subject silinmesiyle
    sessizce yok olması tercih edilmez"). Bu, S2'nin kapsamı dışında,
    pre-existing bir mimari risktir — S2 onu genişletmez, düzeltmez.

    Çağrıldığı yerler:
    - app/services/activity_service.py (create/list/timeline) [S2-WB2]
    """
    __tablename__ = "activities"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(64), nullable=False, default="default", index=True)

    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True, index=True)
    offer_id = Column(Integer, ForeignKey("offers.id"), nullable=True, index=True)
    contract_id = Column(Integer, ForeignKey("contracts.id"), nullable=True, index=True)

    activity_type = Column(String(30), nullable=False, index=True)
    # NOTE | CALL | EMAIL | MEETING | TASK_COMPLETED

    title = Column(String(255), nullable=True)
    body = Column(Text, nullable=True)

    # Olayın GERÇEKLEŞTİĞİ an (kullanıcı geçmişe dönük bir arama/toplantı
    # kaydı girebilir — created_at ile occurred_at farklı olabilir).
    occurred_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)

    customer = relationship("Customer")
    offer = relationship("Offer")
    contract = relationship("Contract")


class Task(Base):
    """
    Gelecekte yapılacak aksiyon kaydı (S2 — Activity & Task Engine).

    Bkz. Activity docstring'i — subject modeli, created_by/assignee
    yokluğu, silme politikası ve FK/ondelete gerekçesi AYNI nedenlerle
    burada da geçerli (assignee için ayrıca: owner kararı, madde 5 —
    canonical User yok, SINGLE OPERATOR, multi-user assignment S2 kapsamı
    dışı bırakıldı).

    completed_at: yalnız İLK completion'da set edilir (idempotent — tekrar
    complete çağrısı no-op döner, ikinci bir TASK_COMPLETED Activity
    ÜRETMEZ; bkz. app/services/task_service.py complete_task()).

    Priority alanı BİLİNÇLİ OLARAK eklenmedi (owner: "Priority ancak
    gerçek UI ihtiyacı varsa" — S2 kapsamındaki hiçbir ekran bunu talep
    etmiyor, YAGNI).

    Çağrıldığı yerler:
    - app/services/task_service.py (create/list/complete/cancel) [S2-WB3]
    """
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(64), nullable=False, default="default", index=True)

    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True, index=True)
    offer_id = Column(Integer, ForeignKey("offers.id"), nullable=True, index=True)
    contract_id = Column(Integer, ForeignKey("contracts.id"), nullable=True, index=True)

    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)

    due_at = Column(DateTime, nullable=True, index=True)
    status = Column(String(20), nullable=False, default="OPEN", index=True)
    # OPEN | COMPLETED | CANCELLED

    completed_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    customer = relationship("Customer")
    offer = relationship("Offer")
    contract = relationship("Contract")


# =============================================================================
# S4 — Prospecting.
#
# PROSPECT ≠ CUSTOMER (owner kararı, madde 1): bu üç tablo Customer'dan
# TAMAMEN ayrı bir keşif/qualification alanı temsil eder. Customer'a geçiş
# YALNIZ explicit "Müşteriye Dönüştür" aksiyonuyla olur (bkz.
# app/prospecting/service.py convert_to_customer()) — discovery/enrichment
# hiçbir aşamada Customer tablosuna doğrudan yazmaz.
#
# tenant_id: mevcut CRM pattern'iyle (Activity/Task) birebir aynı
# (String(64), default="default") — SINGLE GELKA TENANT devam ediyor
# (owner madde 7), yeni bir Customer tenant migration'ı AÇILMADI.
#
# FK'lerde ondelete BİLİNÇLİ OLARAK yok — mevcut projenin HİÇBİR yerinde
# kullanılmıyor (bkz. Activity/Task docstring'i), o konvansiyon burada da
# korundu. ProspectContact/ProspectSource → ProspectCompany ilişkisinde
# yalnız ORM-seviyeli cascade var (ham SQL DELETE'i etkilemez); S4 API'sinde
# zaten hiçbir DELETE endpoint'i yok (owner'ın backend API listesinde yok).
# =============================================================================


class ProspectCompany(Base):
    """
    Keşfedilen aday şirket kaydı (S4 — Prospecting).

    status TEK alan (owner notu: "status/qualification aynı kavramı
    duplicate etmesin, mevcut proje konvansiyonu destekliyorsa tek alana
    indir") — hem discovery lifecycle hem qualification sonucunu taşır:
    DISCOVERED → VERIFIED → QUALIFIED/DISQUALIFIED → CONVERTED. Emsal:
    Offer.status aynı şekilde tek alanda lifecycle+outcome taşıyor.

    Dedup (owner: "silent merge YOK"): duplicate_of_id, olası bir
    duplicate'e rağmen kullanıcı "yine de ayrı kayıt" derse izi kaybetmemek
    için tutulur (self-referential FK, ilişki tanımlanmadı — yalnız ID
    yeterli, owner'ın "gereksiz tablo/karmaşıklık artırma" ilkesi). Otomatik
    BİRLEŞTİRME asla yapılmaz. Gerçek dedup kararı app/prospecting/dedup.py.

    Çağrıldığı yerler:
    - app/prospecting/service.py (create/list/verify/qualify/disqualify/
      convert) [S4-WB2]
    - app/prospecting/dedup.py (duplicate tespiti, aynı tablo üzerinde
      arama) [S4-WB3]
    """
    __tablename__ = "prospect_companies"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(64), nullable=False, default="default", index=True)

    legal_name = Column(String(255), nullable=True)
    trade_name = Column(String(255), nullable=True)
    normalized_name = Column(String(255), nullable=True, index=True)  # dedup sinyali #3

    website = Column(String(500), nullable=True)  # ham, kullanıcı/kaynak girişi — kaybedilmez
    normalized_domain = Column(String(255), nullable=True, index=True)  # dedup sinyali #1 (en güçlü)

    sector = Column(String(255), nullable=True)
    city = Column(String(100), nullable=True, index=True)
    district = Column(String(100), nullable=True)
    industrial_zone = Column(String(255), nullable=True)
    address = Column(Text, nullable=True)
    phone = Column(String(50), nullable=True)  # dedup sinyali #4 (ham gösterim değeri)

    status = Column(String(20), nullable=False, default="DISCOVERED", index=True)
    # DISCOVERED | VERIFIED | QUALIFIED | DISQUALIFIED | CONVERTED

    qualification_reason = Column(String(50), nullable=True)
    # sector_fit | location_fit | has_corporate_contact | energy_intensive_signal
    # | too_small_unsuitable | duplicate | other
    qualification_note = Column(Text, nullable=True)

    # S5 — Outreach ihtiyacı (owner'ın 10.08 düzeltme talimatı): hukuki
    # statü (tacir/esnaf/bireysel) contact_type/e-posta örüntüsünden ASLA
    # otomatik ÇIKARILMAZ (bkz. app/outreach/compliance.py modül
    # docstring'i — "Sessiz inference yapma"). Bu alan yalnız bir insan
    # tarafından, güvenilir bir kaynaktan (örn. Ticaret Sicili Gazetesi/
    # MERSİS sorgusu) DOĞRULANDIKTAN SONRA elle işaretlenir. NULL =
    # henüz doğrulanmadı → compliance engine UNKNOWN sayar, gerçek
    # prospect gönderimini fail-closed bloke eder.
    verified_legal_type = Column(String(20), nullable=True)
    # TACIR | ESNAF | BIREYSEL | NULL (doğrulanmadı)
    verified_legal_type_note = Column(Text, nullable=True)  # doğrulama kaynağı/gerekçesi
    verified_legal_type_set_at = Column(DateTime, nullable=True)

    duplicate_of_id = Column(Integer, ForeignKey("prospect_companies.id"), nullable=True)

    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True, index=True)

    discovered_at = Column(DateTime, default=datetime.utcnow)
    last_verified_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    customer = relationship("Customer", foreign_keys=[customer_id])
    contacts = relationship("ProspectContact", back_populates="company", cascade="all, delete-orphan")
    sources = relationship("ProspectSource", back_populates="company", cascade="all, delete-orphan")


class ProspectContact(Base):
    """
    Keşfedilen şirkete bağlı iletişim kişisi/kanalı (S4 — Prospecting).

    COMPANY ≠ CONTACT (owner madde 2): şirket adres/telefon alanları
    BURAYA kopyalanmaz — yalnız contact'a özgü alanlar tutulur.

    contact_type (owner — E-MAIL DISCOVERY): GENERAL_CORPORATE (info@,
    contact@) | DEPARTMENT (satis@, purchasing@ vb.) |
    NAMED_CORPORATE_PERSON (ad.soyad@sirket.tld) | PERSONAL_OR_FREE_MAIL
    (gmail/outlook/yahoo vb.) | OTHER.

    verification_status V1'de yalnız sözdizimi/domain seviyesi (owner:
    "SMTP mailbox probing / intrusive verification YAPMA").

    source_id: bu contact'ın HANGİ ProspectSource'tan geldiği — "Bu
    bilgiyi nereden bulduk?" (owner: SOURCE EVIDENCE MANDATORY) her zaman
    yanıtlanabilir olmalı.

    Çağrıldığı yerler:
    - app/prospecting/service.py (contact create/list) [S4-WB2]
    - app/prospecting/enrichment.py (website'ten otomatik extraction) [S4-WB4]
    """
    __tablename__ = "prospect_contacts"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(64), nullable=False, default="default", index=True)

    prospect_company_id = Column(Integer, ForeignKey("prospect_companies.id"), nullable=False, index=True)

    full_name = Column(String(255), nullable=True)
    job_title = Column(String(255), nullable=True)
    email = Column(String(255), nullable=True, index=True)
    phone = Column(String(50), nullable=True)

    contact_type = Column(String(30), nullable=False, default="OTHER")
    # GENERAL_CORPORATE | DEPARTMENT | NAMED_CORPORATE_PERSON | PERSONAL_OR_FREE_MAIL | OTHER

    verification_status = Column(String(30), nullable=False, default="UNVERIFIED")
    # UNVERIFIED | SYNTAX_VALID | DOMAIN_VALID (V1'de SMTP probing YOK)

    source_id = Column(Integer, ForeignKey("prospect_sources.id"), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    company = relationship("ProspectCompany", back_populates="contacts", foreign_keys=[prospect_company_id])
    source = relationship("ProspectSource", foreign_keys=[source_id])


class ProspectSource(Base):
    """
    Bir prospect verisinin kaynak kanıtı (S4 — Prospecting).

    SOURCE EVIDENCE MANDATORY (owner kararı) — her keşfedilen veri bu
    tablo üzerinden "nereden bulundu" sorusuna yanıtlanabilir olmalı.
    evidence_text SANITIZE edilmiş kısa bir alıntıdır — ham HTML/script
    İÇERMEZ (bkz. app/prospecting/enrichment.py _sanitize_excerpt();
    owner'ın HIGH PRIORITY güvenlik listesi, "sanitize evidence excerpt").

    content_hash: app/pricing/pricing_cache.py'deki SHA256-tabanlı cache
    desenine benzer (aynı URL'i gereksiz yere tekrar çekmemek için) — TTL
    burada YOK (V1 basit tutuldu, owner: "erken normalization uğruna
    karmaşıklık artırma"), yalnız "bu içerik daha önce görüldü mü" için.

    fetch_status: SSRF reddi/timeout/boyut/content-type dahil HER fetch
    denemesinin sonucu buraya yazılır — S3'ten taşınan "hiçbir kayıt
    sessizce kaybolmayacak" ilkesi burada da geçerli: başarısız bir fetch
    de bir satır olarak görünür kalır, sessizce yutulmaz.

    Çağrıldığı yerler:
    - app/prospecting/enrichment.py (her sayfa fetch'i bir satır üretir) [S4-WB4]
    - app/prospecting/service.py (GET /prospects/{id}/sources) [S4-WB2]
    """
    __tablename__ = "prospect_sources"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(64), nullable=False, default="default", index=True)

    prospect_company_id = Column(Integer, ForeignKey("prospect_companies.id"), nullable=False, index=True)

    source_url = Column(String(1000), nullable=False)
    source_type = Column(String(30), nullable=False, default="WEBSITE")
    # WEBSITE | SEARCH_RESULT | DIRECTORY | MANUAL

    source_title = Column(String(500), nullable=True)
    evidence_text = Column(Text, nullable=True)  # sanitize edilmiş kısa alıntı
    content_hash = Column(String(64), nullable=True, index=True)  # SHA256

    fetch_status = Column(String(30), nullable=False, default="PENDING")
    # PENDING | OK | FAILED | BLOCKED_SSRF | TIMEOUT | TOO_LARGE | UNSUPPORTED_CONTENT_TYPE

    discovered_at = Column(DateTime, default=datetime.utcnow)
    last_checked_at = Column(DateTime, nullable=True)

    company = relationship("ProspectCompany", back_populates="sources", foreign_keys=[prospect_company_id])


# =============================================================================
# S5 — Outreach.
#
# İlk kez sistem dışarıya (üçüncü kişi şirketlere) gerçek ticari e-posta
# gönderiyor — bu yüzden owner kararı: hiçbir gönderim insan onayı
# (Approve → Send, ayrı aksiyonlar) olmadan olamaz; compliance kararı
# backend'de zorunlu (evaluate_email_send_eligibility, app/outreach/
# compliance.py), frontend'in can_send=true beyanına asla güvenilmez.
#
# tenant_id: mevcut CRM/Prospecting pattern'iyle birebir aynı (String(64),
# default="default") — SINGLE GELKA TENANT devam ediyor.
# =============================================================================


class OutreachMessage(Base):
    """
    Tekil, insan-onaylı tanışma e-postası kaydı (S5 — Outreach).

    recipient_email_snapshot: contact_id dolu olsa BİLE gönderim ANINDAKİ
    email adresi burada AYRICA donar (immutable send snapshot, owner
    madde 9) — contact kaydı sonradan güncellenirse/silinirse geçmiş
    gönderimin "kime gittiği" bilgisi kaybolmaz. contact_id NULLABLE'dır
    çünkü owner'ın modelinde bazı senaryolarda hedef doğrudan
    Customer.email olabilir (S4'ün ayrı bir ProspectContact kaydı
    üretmediği durum) — bu durumda contact_id boş kalır, snapshot yine de
    doludur.

    recipient_category (owner'ın 10.08 ek talimatı — SMTP kararı
    sırasında eklendi): PROSPECT_RECIPIENT | TEST_RECIPIENT. TEST_RECIPIENT
    YALNIZ config'teki owner-onaylı test-adres whitelist'inden türetilir
    (bkz. app/outreach/compliance.py) — asla kullanıcı girdisinden veya
    request body'sinden gelmez. İYS/policy netleşene kadar
    PROSPECT_RECIPIENT gönderimi hard-blocked kalır (owner: "real prospect
    send remains hard-blocked"); TEST_RECIPIENT owner-controlled UAT için
    ayrı bir compliance yoluna sahiptir.

    status akışı (owner madde 4): DRAFT → READY_FOR_REVIEW → APPROVED →
    SENDING → SENT | FAILED | BOUNCED; ayrıca REPLIED, SUPPRESSED,
    CANCELLED. SENDING durumu, aynı mesajın iki kez gönderilmesini önleyen
    atomic claim için kullanılır (owner: "double-click / retry idempotent
    ... local atomic SENDING claim").

    Çağrıldığı yerler:
    - app/outreach/service.py (draft/approve/send/status update) [S5-WB2+]
    """
    __tablename__ = "outreach_messages"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(64), nullable=False, default="default", index=True)

    prospect_company_id = Column(Integer, ForeignKey("prospect_companies.id"), nullable=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True, index=True)
    contact_id = Column(Integer, ForeignKey("prospect_contacts.id"), nullable=True, index=True)

    recipient_email_snapshot = Column(String(255), nullable=False, index=True)
    recipient_legal_type = Column(String(20), nullable=True)
    # TACIR | ESNAF | BIREYSEL | UNKNOWN — hukuki statü (opt-out vs opt-in
    # rejimi). Owner'ın 10.08 düzeltmesi: contact_type (info@/satis@ vb.
    # e-posta ÖRÜNTÜSÜ) İLE bu alan (alıcının HUKUKİ statüsü) BAĞIMSIZ
    # eksenlerdir — biri diğerinden ASLA otomatik türetilmez. Yalnız
    # ProspectCompany.verified_legal_type doğrulanmışsa TACIR/ESNAF/BIREYSEL
    # olur; aksi halde UNKNOWN (fail-closed, gerçek prospect gönderimi
    # bloke). Bkz. app/outreach/compliance.py.

    recipient_category = Column(String(30), nullable=False, default="PROSPECT_RECIPIENT")
    # PROSPECT_RECIPIENT | TEST_RECIPIENT

    channel = Column(String(20), nullable=False, default="EMAIL")

    subject = Column(String(500), nullable=False)

    # Owner'ın 10.08 ek talimatı: AI/editable ile SYSTEM/immutable AYRI
    # KOLONLARDA tutulur — yalnız kod disipliniyle DEĞİL, VERİ KATMANINDA
    # da garanti edilsin diye ("AI prompt değişse bile hukuki footer'ın
    # bozulmasını engeller"). body_snapshot YALNIZ editable (selamlama/
    # tanıtım/neden/görüşme talebi) kısmı tutar; system_footer_snapshot
    # YALNIZ app/outreach/sender_profile.py::render_mandatory_footer()
    # çıktısını tutar ve draft oluşturulduktan sonra HİÇBİR endpoint
    # tarafından değiştirilmez (bkz. app/outreach/service.py
    # finalize_draft_message() — yalnız body_snapshot'a yazar).
    body_snapshot = Column(Text, nullable=False)  # EDİTABLE blok
    system_footer_snapshot = Column(Text, nullable=False)  # SYSTEM/immutable blok

    status = Column(String(30), nullable=False, default="DRAFT", index=True)
    # DRAFT | READY_FOR_REVIEW | APPROVED | SENDING | SENT | FAILED | BOUNCED
    # | REPLIED | SUPPRESSED | CANCELLED

    provider = Column(String(30), nullable=True)
    provider_message_id = Column(String(255), nullable=True, index=True)

    approved_at = Column(DateTime, nullable=True)
    sent_at = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime, nullable=True)
    replied_at = Column(DateTime, nullable=True)
    bounced_at = Column(DateTime, nullable=True)
    failed_at = Column(DateTime, nullable=True)
    failure_code = Column(String(100), nullable=True)

    source_snapshot_json = Column(JSON, nullable=True)
    compliance_snapshot_json = Column(JSON, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    prospect_company = relationship("ProspectCompany", foreign_keys=[prospect_company_id])
    customer = relationship("Customer", foreign_keys=[customer_id])
    contact = relationship("ProspectContact", foreign_keys=[contact_id])


class SuppressionEntry(Base):
    """
    Kalıcı gönderim engeli kaydı (S5 — Outreach).

    Owner madde D (HARD GATE): bir adres ret verdiyse / do-not-contact
    işaretliyse / permanent bounce aldıysa / hukuki olarak bloklandıysa,
    HİÇBİR normal send endpoint'i bunu bypass edemez. Bu tablo, o
    kontrolün TEK doğruluk kaynağıdır — compliance engine her zaman bu
    tabloya (email_normalized üzerinden) bakar.

    Aynı email için BİRDEN FAZLA suppression kaydı olabilir (örn. önce
    PERMANENT_BOUNCE sonra ayrıca USER_REJECTED) — bilinçli olarak UNIQUE
    constraint YOK, her kayıt tarihsel bir iz olarak kalır ("hiçbir kayıt
    sessizce kaybolmayacak" ilkesi, S3'ten taşınan genel ilke); can_send
    kontrolü "bu email için EN AZ BİR suppression kaydı var mı" sorusuna
    bakar.

    Çağrıldığı yerler:
    - app/outreach/compliance.py (evaluate_email_send_eligibility) [S5-WB3]
    """
    __tablename__ = "suppression_entries"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(64), nullable=False, default="default", index=True)

    email_normalized = Column(String(255), nullable=False, index=True)
    reason = Column(String(30), nullable=False)
    # USER_REJECTED | DO_NOT_CONTACT | PERMANENT_BOUNCE | LEGAL_BLOCK | MANUAL_BLOCK

    source = Column(String(255), nullable=True)  # nereden geldi: "user" | "provider_bounce" | "manual" vb.
    note = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    effective_at = Column(DateTime, default=datetime.utcnow)


class OutreachTemplate(Base):
    """
    E-posta taslak şablonu (S5 — Outreach).

    Owner: "İlk sürümde template sayısını küçük tut." V1'de tek bir aktif
    "tanışma e-postası" şablonu yeterli — version alanı, şablon
    güncellendiğinde geçmiş gönderimlerin hangi versiyonla gittiğini geri
    dönük izlemek için (body_snapshot zaten immutable olduğu için asıl
    izlenebilirlik OutreachMessage'da, bu alan yalnız şablon tarafı için
    ek bir referans).

    Çağrıldığı yerler:
    - app/outreach/drafting.py (aktif template'i okuma) [S5-WB4]
    """
    __tablename__ = "outreach_templates"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(64), nullable=False, default="default", index=True)

    name = Column(String(255), nullable=False)
    subject_template = Column(String(500), nullable=False)
    body_template = Column(Text, nullable=False)
    version = Column(Integer, nullable=False, default=1)
    active = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


def init_db():
    """
    Veritabanı tablolarını oluştur.

    NOT: Production'da Alembic migration kullanılmalı.
    Bu fonksiyon sadece dev/test için.

    PDSMR-R3 (STEP 7): paketlenmiş (frozen) runtime'da create_all() ARTIK
    ÇAĞRILMAZ — backend/run_server.py, app.main import edilmeden ÖNCE
    startup_gate.py'nin durum makinesini çalıştırır (fresh init: Alembic
    base->head; legacy: kontrollü adoption; zaten canonical: no-op
    sertifikasyon) ve DB'yi ZATEN 351d314819d5'e getirmiş olur. Bu noktada
    create_all() çağrılması SESSİZCE S5 tablolarını migration'sız
    yaratabilir (PDSMR-R2I'de KANITLANMIŞ fail-open bulgusu, bkz.
    tests/test_pre_adoption_startup_create_all_fail_open.py) — GEREKSİZ
    ve TEHLİKELİDİR, bu yüzden GELKA_PACKAGED_RUNTIME sinyali görülünce
    KOŞULSUZ atlanır.

    GELKA_PACKAGED_RUNTIME, electron/main.js tarafından machine-local.env
    SONRASINDA literal olarak enjekte edilir (machine-local.env tarafından
    EZİLEMEZ — bkz. main.js::startBackend() paketlenmiş dal). Dev ortamı bu
    değişkeni HİÇ görmez — create_all() DAVRANIŞI DEĞİŞMEDEN kalır (owner
    kararı: "Development/test create_all behavior may remain... explicitly
    isolated from packaged mode").

    Eski `settings.env == "prod"` kontrolü de KORUNDU (geriye dönük
    uyumluluk — harici bir araç/script gerçekten ENV=prod ile çağırırsa).
    """
    if os.environ.get("GELKA_PACKAGED_RUNTIME") == "1":
        import logging
        logging.getLogger(__name__).info(
            "init_db(): GELKA_PACKAGED_RUNTIME=1 - create_all() ATLANDI "
            "(PDSMR-R3 startup_gate.py DB'yi zaten hazırladı)."
        )
        return

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

    # Pricing modülü tablolarını kaydet (Base.metadata'ya)
    import app.pricing.schemas  # noqa: F401

    Base.metadata.create_all(bind=engine)


def get_db():
    """Database session dependency for FastAPI"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
