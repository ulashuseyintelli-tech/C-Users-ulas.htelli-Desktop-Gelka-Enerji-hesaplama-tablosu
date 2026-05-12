"""
Pricing Risk Engine — Analiz Cache Yönetimi.

SHA256 bazlı cache key ile analiz sonuçlarını önbelleğe alır.
TTL süresi dolmuş kayıtlar otomatik atlanır.

Invalidation kuralları:
- Tüketim verisi güncelleme → müşteri cache sil
- Piyasa verisi güncelleme → dönem cache sil
- YEKDEM güncelleme → dönem cache sil

Cache key bileşenleri (eksiksiz):
- customer_id
- period
- multiplier
- dealer_commission_pct
- imbalance_params (forecast_error_rate, imbalance_cost, smf_enabled)
- template_name (varsa)
- template_monthly_kwh (varsa)

Requirements: 21.1, 21.2, 21.3, 21.4
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from .schemas import AnalysisCache

logger = logging.getLogger(__name__)

# TTL yapılandırması: env var veya varsayılan 24 saat
PRICING_CACHE_TTL_HOURS = int(os.getenv("PRICING_CACHE_TTL_HOURS", "24"))

# Cache key version (T1 / Decision 1 — pricing-cache-key-completeness).
# v1 key'leri t1_kwh/t2_kwh/t3_kwh/use_template/voltage_level alanlarını key'e
# dahil etmiyordu → farklı tüketim profilleri aynı cache kaydına collide ediyordu
# (P0 finansal hata, B1 baseline'da kanıtlandı).
# v2 bump'ı eski kayıtları hash seviyesinde izole eder; eski v1 satırları TTL ile
# doğal olarak temizlenir (tablo DDL değişmez, TRUNCATE yok).
CACHE_KEY_VERSION = "v2"


def build_cache_key(
    customer_id: Optional[str],
    period: str,
    multiplier: float,
    dealer_commission_pct: float,
    imbalance_params: dict,
    template_name: Optional[str] = None,
    template_monthly_kwh: Optional[float] = None,
    t1_kwh: Optional[float] = None,
    t2_kwh: Optional[float] = None,
    t3_kwh: Optional[float] = None,
    use_template: Optional[bool] = None,
    voltage_level: Optional[str] = None,
) -> str:
    """Analiz parametrelerinden SHA256 cache key oluştur.

    Tüm parametreler dahil — eksik parametre = yanlış cache hit riski.

    Key formülüne `_cache_version` prefix'i eklenir (T1 / Decision 1); eski v1
    kayıtları aynı core-7 argümanla çağrılsa bile v2 ile farklı key üretir.

    T2 / Decision 2: 5 yeni alan (`t1_kwh`, `t2_kwh`, `t3_kwh`, `use_template`,
    `voltage_level`) key'e dahil edilir. Bu alanlar response'u doğrudan
    etkilediği için (tüketim toplamı, zaman dilimi dağılımı, dağıtım bedeli)
    key'e girmeden cache kontaminasyonu üretiyorlardı (B1 baseline'da kanıtlandı).

    T3 / Decision 10: `voltage_level=None` canonical `"og"` değerine normalize
    edilir (handler default'u ile aynı). None/og aynı cache key üretir; `"ag"`
    farklı.

    T4 / Decision 11: Float alanlar `round()` ile sabit precision'a normalize
    edilir. kWh alanları 4 hane (input precision'ının üstünde tampon); mevcut
    core alanlar kendi precision'larını korur.

    Args:
        customer_id: Müşteri kimliği (None ise şablon kullanılıyor).
        period: Dönem (YYYY-MM).
        multiplier: Katsayı.
        dealer_commission_pct: Bayi komisyon yüzdesi.
        imbalance_params: Dengesizlik parametreleri dict.
        template_name: Şablon adı (opsiyonel).
        template_monthly_kwh: Şablon aylık tüketim (opsiyonel).
        t1_kwh: Gündüz (T1) tüketimi kWh. None = verilmemiş.
        t2_kwh: Puant (T2) tüketimi kWh. None = verilmemiş.
        t3_kwh: Gece (T3) tüketimi kWh. None = verilmemiş.
        use_template: Şablon modu flag. None = belirtilmemiş (False ile farklıdır:
            None validate edilmemiş durumu temsil eder, False ise T1/T2/T3 zorunlu).
        voltage_level: Gerilim seviyesi. None/empty → canonical "og".

    Returns:
        64 karakter SHA256 hash string.

    Requirements: pricing-cache-key-completeness 2.1-2.9, 3.1-3.8.
    """
    # T4 / Decision 11: float normalization
    t1_normalized = round(t1_kwh, 4) if t1_kwh is not None else None
    t2_normalized = round(t2_kwh, 4) if t2_kwh is not None else None
    t3_normalized = round(t3_kwh, 4) if t3_kwh is not None else None

    # T3 / Decision 10: voltage_level canonical normalize
    voltage_normalized = voltage_level or "og"

    # Decision 2: use_template None korunur (bool() dönüşümü YAPILMAZ — semantic
    # difference: None = validate edilmemiş vs False = explicitly chosen).
    use_tpl_normalized = use_template if use_template is not None else None

    key_data = {
        "_cache_version": CACHE_KEY_VERSION,
        "customer_id": customer_id or "__template__",
        "period": period,
        "multiplier": round(multiplier, 6),
        "dealer_commission_pct": round(dealer_commission_pct, 2),
        "imbalance": {
            "forecast_error_rate": round(imbalance_params.get("forecast_error_rate", 0.05), 4),
            "imbalance_cost_tl_per_mwh": round(imbalance_params.get("imbalance_cost_tl_per_mwh", 50.0), 2),
            "smf_based_imbalance_enabled": imbalance_params.get("smf_based_imbalance_enabled", False),
        },
        "template_name": template_name,
        "template_monthly_kwh": round(template_monthly_kwh, 2) if template_monthly_kwh else None,
        "t1_kwh": t1_normalized,
        "t2_kwh": t2_normalized,
        "t3_kwh": t3_normalized,
        "use_template": use_tpl_normalized,
        "voltage_level": voltage_normalized,
    }

    # Deterministik JSON (sorted keys)
    key_json = json.dumps(key_data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(key_json.encode("utf-8")).hexdigest()


def get_cached_result(
    db: Session,
    cache_key: str,
) -> Optional[dict]:
    """Cache'den sonuç al — TTL kontrolü + hit_count artırma.

    Args:
        db: SQLAlchemy session.
        cache_key: SHA256 cache key.

    Returns:
        Cache'deki analiz sonucu dict veya None (miss/expired).
    """
    record = (
        db.query(AnalysisCache)
        .filter(AnalysisCache.cache_key == cache_key)
        .first()
    )

    if record is None:
        return None

    # TTL kontrolü
    now = datetime.utcnow()
    if record.expires_at and record.expires_at < now:
        # Süresi dolmuş — sil
        db.delete(record)
        db.commit()
        logger.debug("Cache expired: key=%s", cache_key[:16])
        return None

    # Hit count artır
    record.hit_count = (record.hit_count or 0) + 1
    db.commit()

    logger.debug("Cache hit: key=%s, hits=%d", cache_key[:16], record.hit_count)

    try:
        return json.loads(record.result_json)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Cache corrupt: key=%s", cache_key[:16])
        db.delete(record)
        db.commit()
        return None


def set_cached_result(
    db: Session,
    cache_key: str,
    customer_id: Optional[str],
    period: str,
    params_hash: str,
    result: dict,
) -> None:
    """Analiz sonucunu cache'e yaz.

    Mevcut kayıt varsa günceller, yoksa yeni oluşturur.

    Args:
        db: SQLAlchemy session.
        cache_key: SHA256 cache key.
        customer_id: Müşteri kimliği.
        period: Dönem.
        params_hash: Parametre hash'i (cache_key ile aynı olabilir).
        result: Analiz sonucu dict (JSON serializable).
    """
    now = datetime.utcnow()
    expires_at = now + timedelta(hours=PRICING_CACHE_TTL_HOURS)

    result_json = json.dumps(result, ensure_ascii=False, default=str)

    existing = (
        db.query(AnalysisCache)
        .filter(AnalysisCache.cache_key == cache_key)
        .first()
    )

    if existing:
        existing.result_json = result_json
        existing.expires_at = expires_at
        existing.hit_count = 0
        existing.created_at = now
    else:
        db.add(AnalysisCache(
            cache_key=cache_key,
            customer_id=customer_id or "__template__",
            period=period,
            params_hash=params_hash,
            result_json=result_json,
            created_at=now,
            expires_at=expires_at,
            hit_count=0,
        ))

    db.commit()
    logger.debug("Cache set: key=%s, ttl=%dh", cache_key[:16], PRICING_CACHE_TTL_HOURS)


def invalidate_cache_for_customer(
    db: Session,
    customer_id: str,
) -> int:
    """Müşteriye ait tüm cache kayıtlarını sil.

    Tetikleyici: Tüketim verisi güncelleme.

    Returns:
        Silinen kayıt sayısı.
    """
    count = (
        db.query(AnalysisCache)
        .filter(AnalysisCache.customer_id == customer_id)
        .delete()
    )
    db.commit()
    if count > 0:
        logger.info("Cache invalidated: customer_id=%s, deleted=%d", customer_id, count)
    return count


def invalidate_cache_for_period(
    db: Session,
    period: str,
) -> int:
    """Döneme ait tüm cache kayıtlarını sil.

    Tetikleyici: Piyasa verisi veya YEKDEM güncelleme.

    Returns:
        Silinen kayıt sayısı.
    """
    count = (
        db.query(AnalysisCache)
        .filter(AnalysisCache.period == period)
        .delete()
    )
    db.commit()
    if count > 0:
        logger.info("Cache invalidated: period=%s, deleted=%d", period, count)
    return count


def cleanup_expired_cache(db: Session) -> int:
    """Süresi dolmuş tüm cache kayıtlarını temizle.

    Periyodik bakım için kullanılır.

    Returns:
        Silinen kayıt sayısı.
    """
    now = datetime.utcnow()
    count = (
        db.query(AnalysisCache)
        .filter(AnalysisCache.expires_at < now)
        .delete()
    )
    db.commit()
    if count > 0:
        logger.info("Cache cleanup: expired=%d", count)
    return count
