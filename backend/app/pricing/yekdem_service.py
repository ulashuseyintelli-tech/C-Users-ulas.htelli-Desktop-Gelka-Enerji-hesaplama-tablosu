"""
Pricing Risk Engine — YEKDEM CRUD Servisi.

Aylık YEKDEM birim bedellerinin oluşturma, güncelleme, sorgulama ve
listeleme işlemlerini yönetir. YEKDEM aylık sabit bir bedeldir ve
saatlik PTF/SMF verilerinden ayrı tutulur.

Requirements: 3.1, 3.2, 3.3, 3.4, 3.5
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from .schemas import MonthlyYekdemPrice


# ═══════════════════════════════════════════════════════════════════════════════
# Sabitler
# ═══════════════════════════════════════════════════════════════════════════════

_PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_YEKDEM_MIN = 0.0
_YEKDEM_MAX = 10_000.0


# ═══════════════════════════════════════════════════════════════════════════════
# CRUD Fonksiyonları
# ═══════════════════════════════════════════════════════════════════════════════


def create_or_update_yekdem(
    db: Session,
    period: str,
    yekdem_tl_per_mwh: float,
    source: str = "manual",
) -> MonthlyYekdemPrice:
    """YEKDEM kaydı oluştur veya güncelle (upsert davranışı).

    Args:
        db: SQLAlchemy oturumu.
        period: Dönem (YYYY-MM formatında).
        yekdem_tl_per_mwh: YEKDEM birim bedeli (TL/MWh).
        source: Veri kaynağı (manual, epias_api).

    Returns:
        Oluşturulan veya güncellenen MonthlyYekdemPrice kaydı.

    Raises:
        ValueError: Geçersiz dönem formatı veya aralık dışı değer.
    """
    # Dönem format doğrulama
    if not _PERIOD_RE.match(period):
        raise ValueError(
            f"Geçersiz dönem formatı: '{period}'. Beklenen: YYYY-MM"
        )

    # Aralık kontrolü
    if yekdem_tl_per_mwh < _YEKDEM_MIN or yekdem_tl_per_mwh > _YEKDEM_MAX:
        raise ValueError(
            f"YEKDEM değeri aralık dışı: {yekdem_tl_per_mwh:.2f} TL/MWh. "
            f"Beklenen: {_YEKDEM_MIN}–{_YEKDEM_MAX}"
        )

    # Mevcut kayıt var mı?
    existing = (
        db.query(MonthlyYekdemPrice)
        .filter(MonthlyYekdemPrice.period == period)
        .first()
    )

    if existing is not None:
        # Güncelle
        existing.yekdem_tl_per_mwh = yekdem_tl_per_mwh
        existing.source = source
        existing.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(existing)
        return existing

    # Yeni kayıt oluştur
    record = MonthlyYekdemPrice(
        period=period,
        yekdem_tl_per_mwh=yekdem_tl_per_mwh,
        source=source,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def get_yekdem(
    db: Session,
    period: str,
) -> Optional[MonthlyYekdemPrice]:
    """Dönem bazlı YEKDEM kaydı sorgula.

    Args:
        db: SQLAlchemy oturumu.
        period: Dönem (YYYY-MM).

    Returns:
        MonthlyYekdemPrice kaydı veya None.
    """
    return (
        db.query(MonthlyYekdemPrice)
        .filter(MonthlyYekdemPrice.period == period)
        .first()
    )


def list_yekdem(
    db: Session,
    limit: int = 24,
) -> list[MonthlyYekdemPrice]:
    """Tüm YEKDEM kayıtlarını dönem bazlı azalan sırada listele.

    Args:
        db: SQLAlchemy oturumu.
        limit: Maksimum kayıt sayısı (varsayılan 24).

    Returns:
        MonthlyYekdemPrice kayıtları listesi.
    """
    return (
        db.query(MonthlyYekdemPrice)
        .order_by(MonthlyYekdemPrice.period.desc())
        .limit(limit)
        .all()
    )
