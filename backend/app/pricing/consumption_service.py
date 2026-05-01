"""
Pricing Risk Engine — Tüketim Profili Yükleme Servisi.

Excel parse sonucunu DB'ye kaydeder:
- consumption_profiles tablosu
- consumption_hourly_data tablosu
- data_versions tablosu

Versiyonlama: Aynı müşteri+dönem için tekrar yükleme →
önceki versiyon arşivlenir (is_active=0), yeni versiyon oluşturulur.

Requirements: 4.1, 4.2, 4.4, 20.1, 20.2, 20.3, 21.2
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from .schemas import ConsumptionProfile, ConsumptionHourlyData, DataVersion
from .excel_parser import ParsedConsumptionRecord

logger = logging.getLogger(__name__)


def save_consumption_profile(
    db: Session,
    customer_id: str,
    customer_name: Optional[str],
    period: str,
    records: list[ParsedConsumptionRecord],
    source: str = "excel",
    template_name: Optional[str] = None,
) -> ConsumptionProfile:
    """Tüketim profilini DB'ye kaydet.

    Versiyonlama mantığı:
    1. Aynı müşteri+dönem için mevcut aktif profil varsa → arşivle (is_active=0)
    2. Yeni versiyon numarası belirle (mevcut max + 1)
    3. Yeni profil oluştur (is_active=1)
    4. Saatlik verileri kaydet
    5. data_versions tablosuna kayıt ekle

    Args:
        db: SQLAlchemy session.
        customer_id: Müşteri kimliği.
        customer_name: Müşteri adı (opsiyonel).
        period: Dönem (YYYY-MM).
        records: Ayrıştırılmış saatlik tüketim kayıtları.
        source: Veri kaynağı (excel, template, manual).
        template_name: Şablon adı (şablon kullanıldıysa).

    Returns:
        Oluşturulan ConsumptionProfile ORM nesnesi.
    """
    # ── 1. Mevcut aktif profilleri arşivle ─────────────────────────────────
    existing_active = (
        db.query(ConsumptionProfile)
        .filter(
            ConsumptionProfile.customer_id == customer_id,
            ConsumptionProfile.period == period,
            ConsumptionProfile.is_active == 1,
        )
        .all()
    )

    for profile in existing_active:
        profile.is_active = 0
        logger.info(
            "Profil arşivlendi: customer_id=%s, period=%s, version=%d",
            customer_id, period, profile.version,
        )

    # ── 2. Yeni versiyon numarası belirle ──────────────────────────────────
    max_version_row = (
        db.query(ConsumptionProfile.version)
        .filter(
            ConsumptionProfile.customer_id == customer_id,
            ConsumptionProfile.period == period,
        )
        .order_by(ConsumptionProfile.version.desc())
        .first()
    )
    new_version = (max_version_row[0] + 1) if max_version_row else 1

    # ── 3. Toplam tüketim hesapla ──────────────────────────────────────────
    total_kwh = sum(r.consumption_kwh for r in records)

    # ── 4. Profil tipi belirle ─────────────────────────────────────────────
    profile_type = "template" if template_name else "actual"

    # ── 5. Yeni profil oluştur ─────────────────────────────────────────────
    new_profile = ConsumptionProfile(
        customer_id=customer_id,
        customer_name=customer_name,
        period=period,
        profile_type=profile_type,
        template_name=template_name,
        total_kwh=round(total_kwh, 4),
        source=source,
        version=new_version,
        is_active=1,
    )
    db.add(new_profile)
    db.flush()  # ID almak için flush

    # ── 6. Saatlik verileri kaydet ─────────────────────────────────────────
    hourly_data_objects = [
        ConsumptionHourlyData(
            profile_id=new_profile.id,
            date=r.date,
            hour=r.hour,
            consumption_kwh=r.consumption_kwh,
        )
        for r in records
    ]
    db.add_all(hourly_data_objects)

    # ── 7. data_versions tablosuna kayıt ekle ─────────────────────────────
    # Mevcut aktif data_version'ları arşivle
    existing_dv = (
        db.query(DataVersion)
        .filter(
            DataVersion.data_type == "consumption",
            DataVersion.period == period,
            DataVersion.customer_id == customer_id,
            DataVersion.is_active == 1,
        )
        .all()
    )
    for dv in existing_dv:
        dv.is_active = 0

    new_dv = DataVersion(
        data_type="consumption",
        period=period,
        customer_id=customer_id,
        version=new_version,
        row_count=len(records),
        is_active=1,
    )
    db.add(new_dv)

    # ── 8. Commit ──────────────────────────────────────────────────────────
    db.commit()
    db.refresh(new_profile)

    logger.info(
        "Tüketim profili kaydedildi: customer_id=%s, period=%s, version=%d, "
        "rows=%d, total_kwh=%.2f",
        customer_id, period, new_version, len(records), total_kwh,
    )

    return new_profile
