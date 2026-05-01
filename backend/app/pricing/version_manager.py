"""
Pricing Risk Engine — Veri Versiyonlama Yöneticisi.

Piyasa ve tüketim verisi yükleme geçmişini yönetir.
Arşivlenmiş versiyonlar görüntülenebilir ama hesaplamada kullanılmaz.

Requirements: 20.1, 20.2, 20.3, 20.4
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from .schemas import DataVersion

logger = logging.getLogger(__name__)


def archive_and_create_version(
    db: Session,
    data_type: str,
    period: str,
    customer_id: Optional[str],
    row_count: int,
    quality_score: Optional[int] = None,
    filename: Optional[str] = None,
    uploaded_by: Optional[str] = None,
) -> DataVersion:
    """Mevcut aktif versiyonu arşivle ve yeni versiyon oluştur.

    Args:
        db: SQLAlchemy session.
        data_type: Veri tipi (market_data, consumption).
        period: Dönem (YYYY-MM).
        customer_id: Müşteri kimliği (market_data için None).
        row_count: Satır sayısı.
        quality_score: Kalite skoru (0–100).
        filename: Yüklenen dosya adı.
        uploaded_by: Yükleyen kullanıcı.

    Returns:
        Oluşturulan DataVersion kaydı.
    """
    # Mevcut aktif versiyonları arşivle
    existing_active = (
        db.query(DataVersion)
        .filter(
            DataVersion.data_type == data_type,
            DataVersion.period == period,
            DataVersion.customer_id == customer_id,
            DataVersion.is_active == 1,
        )
        .all()
    )
    for dv in existing_active:
        dv.is_active = 0

    # Yeni versiyon numarası
    max_version_row = (
        db.query(DataVersion.version)
        .filter(
            DataVersion.data_type == data_type,
            DataVersion.period == period,
            DataVersion.customer_id == customer_id,
        )
        .order_by(DataVersion.version.desc())
        .first()
    )
    new_version = (max_version_row[0] + 1) if max_version_row else 1

    new_dv = DataVersion(
        data_type=data_type,
        period=period,
        customer_id=customer_id,
        version=new_version,
        uploaded_by=uploaded_by,
        upload_filename=filename,
        row_count=row_count,
        quality_score=quality_score,
        is_active=1,
    )
    db.add(new_dv)
    db.commit()
    db.refresh(new_dv)

    logger.info(
        "Version created: type=%s, period=%s, customer=%s, v=%d, rows=%d",
        data_type, period, customer_id, new_version, row_count,
    )
    return new_dv


def list_versions(
    db: Session,
    data_type: str,
    period: Optional[str] = None,
    customer_id: Optional[str] = None,
) -> list[DataVersion]:
    """Yükleme geçmişi listele.

    Args:
        db: SQLAlchemy session.
        data_type: Veri tipi filtresi.
        period: Dönem filtresi (opsiyonel).
        customer_id: Müşteri filtresi (opsiyonel).

    Returns:
        DataVersion kayıtları (versiyon azalan sırada).
    """
    query = db.query(DataVersion).filter(DataVersion.data_type == data_type)

    if period:
        query = query.filter(DataVersion.period == period)
    if customer_id:
        query = query.filter(DataVersion.customer_id == customer_id)

    return query.order_by(DataVersion.version.desc()).all()


def get_active_version(
    db: Session,
    data_type: str,
    period: str,
    customer_id: Optional[str] = None,
) -> Optional[DataVersion]:
    """Aktif versiyon bilgisi al.

    Returns:
        Aktif DataVersion kaydı veya None.
    """
    return (
        db.query(DataVersion)
        .filter(
            DataVersion.data_type == data_type,
            DataVersion.period == period,
            DataVersion.customer_id == customer_id,
            DataVersion.is_active == 1,
        )
        .first()
    )
