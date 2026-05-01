"""
Pricing Risk Engine — SQLAlchemy ORM modelleri.

Tablolar:
- hourly_market_prices: Saatlik PTF/SMF verileri
- monthly_yekdem_prices: Aylık YEKDEM bedelleri
- consumption_profiles: Müşteri tüketim profilleri
- consumption_hourly_data: Saatlik tüketim verileri
- profile_templates: Sektörel profil şablonları
- data_versions: Veri versiyonlama arşivi
- analysis_cache: Analiz sonuç önbelleği

Tüm tablolar init_db() → Base.metadata.create_all() ile oluşturulur.
"""

import sqlalchemy as sa
from sqlalchemy import Column, Integer, String, Float, Text, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..database import Base


class HourlyMarketPrice(Base):
    """Saatlik PTF/SMF piyasa verileri — EPİAŞ uzlaştırma Excel'inden yüklenir."""
    __tablename__ = "hourly_market_prices"

    id = Column(Integer, primary_key=True, index=True)
    period = Column(String(7), nullable=False, index=True)          # YYYY-MM
    date = Column(String(10), nullable=False)                        # YYYY-MM-DD
    hour = Column(Integer, nullable=False)                           # 0-23
    ptf_tl_per_mwh = Column(Float, nullable=False)                   # 0–50000
    smf_tl_per_mwh = Column(Float, nullable=False)                   # 0–50000
    currency = Column(String(3), nullable=False, default="TRY")
    source = Column(String(30), nullable=False, default="epias_excel")  # epias_excel, epias_api, manual
    version = Column(Integer, nullable=False, default=1)
    is_active = Column(Integer, nullable=False, default=1)           # 1=aktif, 0=arşiv
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    __table_args__ = (
        sa.UniqueConstraint(
            "period", "date", "hour", "version",
            name="uq_hourly_period_date_hour_version",
        ),
        sa.Index("idx_hourly_market_period_active", "period", "is_active"),
        sa.Index("idx_hourly_market_date_hour", "date", "hour"),
    )


class MonthlyYekdemPrice(Base):
    """Aylık YEKDEM bedelleri — saatlik PTF/SMF'den ayrı tablo."""
    __tablename__ = "monthly_yekdem_prices"

    id = Column(Integer, primary_key=True, index=True)
    period = Column(String(7), nullable=False, unique=True, index=True)  # YYYY-MM
    yekdem_tl_per_mwh = Column(Float, nullable=False)                     # 0–10000
    source = Column(String(30), nullable=False, default="manual")         # manual, epias_api
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())


class ConsumptionProfile(Base):
    """Müşteri tüketim profilleri — Excel veya şablondan oluşturulur."""
    __tablename__ = "consumption_profiles"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(String(100), nullable=False, index=True)
    customer_name = Column(String(255), nullable=True)
    period = Column(String(7), nullable=False, index=True)           # YYYY-MM
    profile_type = Column(String(20), nullable=False, default="actual")  # actual, template
    template_name = Column(String(100), nullable=True)
    total_kwh = Column(Float, nullable=False)                        # >= 0
    source = Column(String(30), nullable=False, default="excel")     # excel, template, manual
    version = Column(Integer, nullable=False, default=1)
    is_active = Column(Integer, nullable=False, default=1)           # 1=aktif, 0=arşiv
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    hourly_data = relationship(
        "ConsumptionHourlyData",
        back_populates="profile",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "customer_id", "period", "version",
            name="uq_consumption_customer_period_version",
        ),
        sa.Index("idx_consumption_active", "customer_id", "period", "is_active"),
    )


class ConsumptionHourlyData(Base):
    """Saatlik tüketim verileri — consumption_profiles ile ilişkili."""
    __tablename__ = "consumption_hourly_data"

    id = Column(Integer, primary_key=True, index=True)
    profile_id = Column(
        Integer,
        ForeignKey("consumption_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    date = Column(String(10), nullable=False)                        # YYYY-MM-DD
    hour = Column(Integer, nullable=False)                           # 0-23
    consumption_kwh = Column(Float, nullable=False)

    profile = relationship("ConsumptionProfile", back_populates="hourly_data")

    __table_args__ = (
        sa.UniqueConstraint(
            "profile_id", "date", "hour",
            name="uq_consumption_hourly",
        ),
    )


class ProfileTemplate(Base):
    """Sektörel profil şablonları — 24 saatlik normalize ağırlık dizisi."""
    __tablename__ = "profile_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True)          # "3_vardiya_sanayi"
    display_name = Column(String(200), nullable=False)               # "3 Vardiya Sanayi"
    description = Column(Text, nullable=True)
    hourly_weights = Column(Text, nullable=False)                    # JSON: 24 elemanlı dizi
    is_builtin = Column(Integer, nullable=False, default=1)          # 1=sistem, 0=kullanıcı
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())


class DataVersion(Base):
    """Veri versiyonlama arşivi — piyasa ve tüketim verisi yükleme geçmişi."""
    __tablename__ = "data_versions"

    id = Column(Integer, primary_key=True, index=True)
    data_type = Column(String(30), nullable=False)                   # market_data, consumption
    period = Column(String(7), nullable=False)                       # YYYY-MM
    customer_id = Column(String(100), nullable=True)                 # NULL for market data
    version = Column(Integer, nullable=False)
    uploaded_by = Column(String(100), nullable=True)
    upload_filename = Column(String(255), nullable=True)
    row_count = Column(Integer, nullable=False)
    quality_score = Column(Integer, nullable=True)                   # 0-100
    is_active = Column(Integer, nullable=False, default=0)           # 1=aktif versiyon
    created_at = Column(DateTime, nullable=False, default=func.now())

    __table_args__ = (
        sa.UniqueConstraint(
            "data_type", "period", "customer_id", "version",
            name="uq_data_version",
        ),
        sa.Index("idx_data_versions_lookup", "data_type", "period", "customer_id"),
    )


class AnalysisCache(Base):
    """Analiz sonuç önbelleği — SHA256 cache key ile."""
    __tablename__ = "analysis_cache"

    id = Column(Integer, primary_key=True, index=True)
    cache_key = Column(String(64), nullable=False, unique=True)      # SHA256 hash
    customer_id = Column(String(100), nullable=False)
    period = Column(String(7), nullable=False)
    params_hash = Column(String(64), nullable=False)
    result_json = Column(Text, nullable=False)                       # JSON analiz sonucu
    created_at = Column(DateTime, nullable=False, default=func.now())
    expires_at = Column(DateTime, nullable=False)                    # TTL süresi
    hit_count = Column(Integer, nullable=False, default=0)

    __table_args__ = (
        sa.Index("idx_cache_expires", "expires_at"),
        sa.Index("idx_cache_customer_period", "customer_id", "period"),
    )
