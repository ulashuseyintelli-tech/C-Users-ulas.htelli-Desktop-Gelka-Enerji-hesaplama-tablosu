"""
Fiyat Doğruluğu Faz 1 — teklif fiyatının kaynağı (provenance) ve kesinleştirme kapısı.

Eksik ya da doğrulanmamış PTF/YEKDEM ile teklif KESİNLEŞMEZ (kayıt/PDF). Eksik
fiyat sabit bir değerle ya da 0 ile SESSİZCE doldurulmaz. Fiyatın dönemi,
kaynağı ve kullanıcı doğrulaması teklif snapshot'ına yazılır.

YEKDEM için üç durum birbirinden AYRILIR:
- missing  : değer yok (None). Kesinleştirme engellenir.
- değer    : >= 0 bir sayı (0 dahil). DB kolonu NOT NULL olduğu ve eski
             yazımlar girilmemiş YEKDEM'i 0 bıraktığı için DB'deki 0 sistem
             tarafından doğrulanmış SAYILMAZ ("zero_unverified"); kullanıcı
             açıkça onaylarsa gerçek 0 olarak kabul edilir.
- excluded : açıkça seçilen "YEKDEM hariç" (manuel akış) ya da faturada YEKDEM
             bulunmaması (AI akışı). YEKDEM teklife dahil edilmez.

Sistem doğrulaması (kullanıcı onayı gerektirmeyen) yalnız teklif değeri aynı
dönemin GÜVENİLİR kaynaktan türetilen etkin değeriyle eşleştiğinde vardır:
- market_reference_prices.source ∈ {epias_manual, epias_api, manual_override}
  (açılıştaki geliştirme örnek verisi ve mock senkron izi HARİÇ),
- hourly_market_prices (yetkili EPİAŞ Excel yüklemesi).
Kaynağı bilinmeyen/eski kayıtlar (seed, migration, örnek veri) doğrulanmış
sayılmaz; kullanıcı onayı istenir.

PDF'te "EPİAŞ verileri esas alınarak" ifadesi yalnız epias_basis=True iken
kullanılır (PTF ve dahil ise YEKDEM EPİAŞ etiketli güvenilir kaynaktan).

Çağrıldığı yerler:
- main.get_prices_with_epias_fallback() → GET /api/epias/prices/{period} (manuel akış fiyat durumu)
- calculator.calculate_offer() → POST /full-process, POST /calculate-offer (AI akışı meta_price_provenance)
- main.create_offer() → POST /offers (sunucu kapısı + snapshot)
- main.generate_pdf_for_offer() → POST /offers/{id}/generate-pdf (snapshot kapısı)
- main.generate_pdf_simple() → POST /generate-pdf-simple (doğrudan API kapısı)
- main.generate_pdf_direct() → POST /generate-pdf-direct (doğrudan API kapısı)

Multitenant: piyasa fiyat tabloları tenant kolonu taşımaz (piyasa geneli veri);
bu modül yalnız OKUR, tenant kapsamına dokunmaz.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

PROVENANCE_VERSION = 1
# Teklif değeri ile DB'den türetilen değerin "aynı" sayılacağı tolerans (TL/MWh).
PRICE_TOLERANCE_TL_PER_MWH = 0.01

TRUSTED_REFERENCE_SOURCES = frozenset({"epias_manual", "epias_api", "manual_override"})
EPIAS_REFERENCE_SOURCES = frozenset({"epias_manual", "epias_api"})
EPIAS_HOURLY_SOURCES = frozenset({"epias_excel", "epias_api"})
# Faz 1'de kaldırılan açılış tohumlamasının (main._add_sample_market_prices) ve
# mock EPİAŞ senkronunun kayıtta bıraktığı izler.
DEV_SAMPLE_NOTE_PREFIX = "Sample data"
MOCK_SYNC_UPDATED_BY = "mock_sync"

REASON_PTF_MISSING = "ptf_missing"
REASON_YEKDEM_MISSING = "yekdem_missing"
REASON_CONFIRMATION_REQUIRED = "confirmation_required"

REASON_MESSAGES = {
    REASON_PTF_MISSING: "Teklif PTF değeri eksik (0'dan büyük bir değer gerekli).",
    REASON_YEKDEM_MISSING: (
        "YEKDEM birim bedeli bilinmiyor: değeri girin ya da 'YEKDEM hariç' seçin."
    ),
    REASON_CONFIRMATION_REQUIRED: (
        "Fiyat, dönemin güvenilir kayıtlı değeriyle eşleşmiyor ya da kaynağı "
        "doğrulanamıyor: PTF/YEKDEM değerlerini kontrol edip açıkça onaylayın."
    ),
}

_PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def normalize_period(period: Any) -> Optional[str]:
    """YYYY-MM biçimini doğrular; değilse None (sistem doğrulaması yapılamaz)."""
    if not period:
        return None
    p = str(period).strip()
    return p if _PERIOD_RE.match(p) else None


def _finite(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


@dataclass(frozen=True)
class PriceCandidate:
    """Sistemin bir dönem için seçebileceği etkin PTF değeri."""
    value: float
    source: str  # manual_override | hourly_consumption:<id> | hourly_weighted:<profil> | reference_scalar
    source_detail: Optional[str]
    trusted: bool
    epias: bool


@dataclass(frozen=True)
class YekdemResolution:
    """Dönem YEKDEM birim bedelinin çözümü."""
    value: Optional[float]  # None = bilinmiyor
    status: str  # known | zero_unverified | missing
    source: Optional[str]
    trusted: bool
    epias: bool


_YEKDEM_MISSING = YekdemResolution(None, "missing", None, False, False)


def _reference_record(db: Session, period: str):
    from .database import MarketReferencePrice

    return (
        db.query(MarketReferencePrice)
        .filter(
            MarketReferencePrice.period == period,
            MarketReferencePrice.price_type == "PTF",
        )
        .first()
    )


def reference_trust(record: Any) -> tuple[bool, bool]:
    """market_reference_prices kaydı için (güvenilir, EPİAŞ etiketli).

    Açılıştaki geliştirme örnek verisi ("Sample data (dev)") ve mock senkron
    izi, kaynak etiketi ne olursa olsun güvenilir SAYILMAZ.
    """
    note = str(getattr(record, "source_note", None) or "")
    if note.startswith(DEV_SAMPLE_NOTE_PREFIX) or getattr(record, "updated_by", None) == MOCK_SYNC_UPDATED_BY:
        return (False, False)
    source = str(getattr(record, "source", None) or "")
    trusted = source in TRUSTED_REFERENCE_SOURCES
    return (trusted, trusted and source in EPIAS_REFERENCE_SOURCES)


def resolve_period_yekdem(db: Optional[Session], period: Any) -> YekdemResolution:
    """Dönem YEKDEM birim bedeli — kayıt yoksa 'missing' (ASLA sabit/0 ile doldurulmaz).

    DB'deki 0 'zero_unverified' döner: değer 0 olarak taşınır (eksikle
    karışmaz) ama kullanıcı onayı olmadan doğrulanmış sayılmaz.

    Çağrıldığı yerler:
    - calculator.get_ptf_yekdem_for_period() → AI akışı YEKDEM'i (eski davranış: kayıt yoksa 0.0)
    - main.get_prices_with_epias_fallback() → GET /api/epias/prices/{period}
    - price_provenance.build_price_provenance() → teklif kesinleştirme kapısı
    """
    p = normalize_period(period)
    if db is None or p is None:
        return _YEKDEM_MISSING
    record = _reference_record(db, p)
    if record is None:
        return _YEKDEM_MISSING
    value = _finite(record.yekdem_tl_per_mwh)
    if value is None:
        return _YEKDEM_MISSING
    source = f"reference:{record.source or 'unknown'}"
    if value == 0.0:
        return YekdemResolution(0.0, "zero_unverified", source, False, False)
    trusted, epias = reference_trust(record)
    return YekdemResolution(value, "known", source, trusted, epias)


def _hourly_trust(db: Session, period: str) -> tuple[bool, bool]:
    from .pricing.schemas import HourlyMarketPrice

    sources = {
        s
        for (s,) in db.query(HourlyMarketPrice.source)
        .filter(HourlyMarketPrice.period == period, HourlyMarketPrice.is_active == 1)
        .distinct()
    }
    if not sources:
        return (False, False)
    # Saatlik veri yalnız yetkili yükleme ucundan (pricing admin) girer.
    return (True, sources <= EPIAS_HOURLY_SOURCES)


def effective_ptf_candidates(
    db: Session, period: str, customer_id: Optional[str] = None
) -> list[PriceCandidate]:
    """Bu dönem için sistemin seçebileceği ETKİN PTF değerleri.

    Öncelik zinciri calculator.get_ptf_yekdem_for_period ve GET
    /api/epias/prices ile birebir aynıdır: manual_override skaler > gerçek
    tüketim ağırlıklı (bayrak + firma) > saatlik profil ağırlıklı (her profil
    bir aday) > aylık referans skaler. Zincir burada yalnız OKUNUR.

    Çağrıldığı yerler:
    - price_provenance.build_price_provenance() → teklif kesinleştirme kapısı
    """
    from . import market_prices as mp

    record = _reference_record(db, period)
    ref_ptf = _finite(record.ptf_tl_per_mwh) if record is not None else None
    ref_trusted, ref_epias = reference_trust(record) if record is not None else (False, False)
    if record is not None and record.source == "manual_override" and ref_ptf and ref_ptf > 0:
        return [PriceCandidate(ref_ptf, "manual_override", record.source, ref_trusted, ref_epias)]

    hourly_trusted, hourly_epias = _hourly_trust(db, period)
    if mp.OFFER_USE_REAL_CONSUMPTION and customer_id:
        cw = mp.consumption_weighted_ptf(db, period, customer_id)
        if cw is not None and cw > 0:
            return [PriceCandidate(cw, f"hourly_consumption:{customer_id}", "hourly",
                                   hourly_trusted, hourly_epias)]

    hourly: list[PriceCandidate] = []
    for profile in mp.PROFILE_WEIGHTS:
        wr = mp.weighted_ptf_for_profile(db, period, profile)
        if wr.ptf_tl_per_mwh is not None and wr.ptf_tl_per_mwh > 0:
            hourly.append(PriceCandidate(wr.ptf_tl_per_mwh, f"hourly_weighted:{profile}",
                                         "hourly", hourly_trusted, hourly_epias))
    if hourly:
        return hourly

    if record is not None and ref_ptf and ref_ptf > 0:
        return [PriceCandidate(ref_ptf, "reference_scalar", record.source, ref_trusted, ref_epias)]
    return []


def build_price_provenance(
    db: Optional[Session],
    *,
    period: Any,
    ptf: Any,
    yekdem: Any,
    yekdem_excluded: bool,
    user_confirmed: bool,
    customer_id: Optional[str] = None,
    exclusion_basis: Optional[str] = None,
) -> dict[str, Any]:
    """Teklif fiyatının kaynağı + kesinleştirme kararı (JSON'a yazılabilir dict).

    `db=None` → sistem doğrulaması yapılamaz (system_lookup='unavailable');
    fiyat ancak kullanıcı onayıyla doğrulanır. DB sorgusu hata verirse de aynı
    (fail-closed; 'error'). Değerler asla değiştirilmez/doldurulmaz — yalnız
    sınıflandırılır.

    Çağrıldığı yerler: modül başlığındaki liste (GET fiyat durumu, AI hesabı,
    POST /offers kapısı, doğrudan PDF uçları).
    """
    p = normalize_period(period)
    ptf_value = _finite(ptf)
    yekdem_value = _finite(yekdem)

    lookup = "ok"
    candidates: list[PriceCandidate] = []
    period_yekdem = _YEKDEM_MISSING
    if db is None or p is None:
        lookup = "unavailable"
    else:
        try:
            candidates = effective_ptf_candidates(db, p, customer_id)
            period_yekdem = resolve_period_yekdem(db, p)
        except SQLAlchemyError as exc:
            logger.warning("Fiyat kaynağı sorgulanamadı (dönem=%s): %s", p, type(exc).__name__)
            lookup = "error"
            candidates, period_yekdem = [], _YEKDEM_MISSING

    reasons: list[str] = []

    # ── PTF ────────────────────────────────────────────────────────────────
    ptf_block: dict[str, Any] = {
        "value": ptf_value,
        "db_values": sorted({round(c.value, 2) for c in candidates}),
    }
    if ptf_value is None or ptf_value <= 0:
        ptf_block.update(status="missing", source=None, source_detail=None,
                         system_verified=False, epias=False)
        reasons.append(REASON_PTF_MISSING)
    else:
        match = next((c for c in candidates
                      if abs(c.value - ptf_value) <= PRICE_TOLERANCE_TL_PER_MWH), None)
        if match is not None:
            ptf_block.update(status="matched", source=match.source,
                             source_detail=match.source_detail,
                             system_verified=match.trusted, epias=match.epias)
        else:
            ptf_block.update(status="user_entered", source="user_entered",
                             source_detail=None, system_verified=False, epias=False)

    # ── YEKDEM ─────────────────────────────────────────────────────────────
    yekdem_block: dict[str, Any] = {
        "period_status": period_yekdem.status,
        "period_value": period_yekdem.value,
    }
    if yekdem_excluded:
        yekdem_block.update(mode="excluded", exclusion_basis=exclusion_basis or "user",
                            value=None, status="excluded", source=None,
                            system_verified=False, epias=False)
    elif yekdem_value is None or yekdem_value < 0:
        yekdem_block.update(mode="included", value=yekdem_value, status="missing",
                            source=None, system_verified=False, epias=False)
        reasons.append(REASON_YEKDEM_MISSING)
    else:
        yekdem_block.update(mode="included", value=yekdem_value)
        if (period_yekdem.status == "known" and period_yekdem.value is not None
                and abs(period_yekdem.value - yekdem_value) <= PRICE_TOLERANCE_TL_PER_MWH):
            yekdem_block.update(status="matched", source=period_yekdem.source,
                                system_verified=period_yekdem.trusted,
                                epias=period_yekdem.epias)
        elif yekdem_value == 0.0:
            # Gerçek 0 ancak kullanıcı onayıyla kabul edilir: eksik veri de 0'a
            # dönüşmüş olabilir (kolon NOT NULL; eski yazımlar 0 bırakıyordu).
            yekdem_block.update(status="zero", source="zero_requires_confirmation",
                                system_verified=False, epias=False)
        else:
            yekdem_block.update(status="user_entered", source="user_entered",
                                system_verified=False, epias=False)

    ptf_needs = REASON_PTF_MISSING not in reasons and not ptf_block["system_verified"]
    yekdem_needs = (not yekdem_excluded and REASON_YEKDEM_MISSING not in reasons
                    and not yekdem_block["system_verified"])
    requires_confirmation = ptf_needs or yekdem_needs
    if requires_confirmation and not user_confirmed:
        reasons.append(REASON_CONFIRMATION_REQUIRED)

    verified = not reasons
    epias_basis = bool(
        verified
        and ptf_block["system_verified"] and ptf_block["epias"]
        and (yekdem_excluded or (yekdem_block["system_verified"] and yekdem_block["epias"]))
    )
    if not verified:
        verified_by = None
    elif requires_confirmation:
        verified_by = "user"
    else:
        verified_by = "system"
    return {
        "version": PROVENANCE_VERSION,
        "period": p,
        "system_lookup": lookup,
        "ptf": ptf_block,
        "yekdem": yekdem_block,
        "requires_confirmation": requires_confirmation,
        "user_confirmed": bool(user_confirmed),
        "verified": verified,
        "verified_by": verified_by,
        "epias_basis": epias_basis,
        "blocking_reasons": reasons,
        "messages": [REASON_MESSAGES[r] for r in reasons],
    }


def price_block_content(provenance: dict[str, Any]) -> dict[str, Any]:
    """Kesinleştirme reddi için JSON gövdesi (HTTP 422).

    Çağrıldığı yerler:
    - main.create_offer() → POST /offers
    - main.generate_pdf_simple() → POST /generate-pdf-simple
    - main.generate_pdf_direct() → POST /generate-pdf-direct
    """
    messages = provenance.get("messages") or []
    return {
        "error": {
            "code": "price_unverified",
            "message": " ".join(messages) or "Teklif fiyatı doğrulanmadı.",
            "blocking_reasons": list(provenance.get("blocking_reasons") or []),
            "price_provenance": provenance,
        }
    }


def snapshot_price_verified(calculation_result: Any) -> bool:
    """Kaydedilmiş teklif snapshot'ı sunucuda doğrulanmış fiyat taşıyor mu?

    Faz 1 öncesi kayıtlarda provenance YOKTUR → False: kaynağı bilinmeyen
    geçmiş kayıt doğrulanmış SAYILMAZ ve yeniden hesaplanmaz.

    Çağrıldığı yerler:
    - main.generate_pdf_for_offer() → POST /offers/{id}/generate-pdf
    """
    if not isinstance(calculation_result, dict):
        return False
    prov = calculation_result.get("meta_price_provenance")
    return (isinstance(prov, dict)
            and prov.get("version") == PROVENANCE_VERSION
            and prov.get("verified") is True)
