"""
Fiyat Doğruluğu Faz 1 — teklif fiyatının kaynağı (provenance) ve kesinleştirme kapısı.

Eksik ya da doğrulanmamış PTF/YEKDEM ile teklif KESİNLEŞMEZ (kayıt/PDF). Eksik
fiyat sabit bir değerle ya da 0 ile SESSİZCE doldurulmaz. Fiyatın dönemi,
kaynağı ve kayıt durumu teklif snapshot'ına yazılır.

Owner teyidi (provenance sürüm 2):
- Doğrulama YALNIZ SUNUCUDADIR. İstemcinin "doğrulandı/onaylandı" beyanı kapıyı
  AÇMAZ; sürüm 1'deki kullanıcı onayı yolu kaldırıldı.
- Kesin teklif için fiyat, dönemin GÜVENİLİR kaynaklı ve KESİNLEŞMİŞ
  (status='final') kaydıyla birebir eşleşmelidir. 'provisional' kayıttan gelen
  fiyat YALNIZ açıkça işaretlenmiş TASLAK hesapta kullanılabilir
  (draft_only=True, provisional=True); POST /offers ve PDF uçları reddeder.
- YEKDEM uygulaması açık bir seçimdir: included (dahil) | excluded (hariç).
  Doğrulanmış bir muafiyet kuralı olmadığı için "muaf" seçeneği YOKTUR.
  Aşağıdaki durumlar birbirine EŞİTLENMEZ:
  * dahil: değer, dönemin güvenilir + kesin kaydıyla eşleşmelidir.
  * hariç: YEKDEM enerji birim fiyatına eklenmez (açık seçim).
  * eksik: dönem kaydı yok (None). 0 ile temsil edilmez.
  * gerçek 0: yetkili Piyasa Fiyatları ekranından AÇIKÇA girilen 0, mevcut denetim
    tablosuna (price_change_history, price_type='YEKDEM') ayrı bir satır olarak
    yazılır. Kaydın EN SON geçmiş satırı bu açık sıfır satırıysa (durum ve kaynak
    kayıtla aynı), kayıt kesin ve kaynağı güvenilirse sıfır GERÇEK kabul edilir ve
    doğrulanır. YEKDEM yeniden girilmeden yapılan sonraki güncelleme ya da başka bir
    yolun yazımı teyidi düşürür (fail-closed).
  * anlamı bilinmeyen 0: denetim satırı olmayan (eski) sıfır otomatik gerçek ya da
    eksik SAYILMAZ. Değer 0 olarak taşınır ama kesin teklifte kullanılmaz
    (yekdem_zero_unverified). Kolon NOT NULL (varsayılan 0) olduğu için ayrım
    kolonda değil denetim kaydındadır; migration gerekmez.

Sistem doğrulaması (güvenilir kaynak):
- market_reference_prices: source ∈ {epias_manual, epias_api, manual_override}
  VE status == 'final'. Açılıştaki geliştirme örnek verisi ve mock senkron izi
  HARİÇ tutulur.
- hourly_market_prices: yetkili EPİAŞ Excel yüklemesi. Saatlik PTF'te
  provisional/final ayrımı yoktur; yayımlanan PTF kesindir.
Kaynağı bilinmeyen ya da eski kayıtlar (seed, migration, örnek veri)
doğrulanmış sayılmaz.

PDF'te "EPİAŞ verileri esas alınarak" ifadesi yalnız epias_basis=True iken
kullanılır. Bunun için PTF'in (ve dahilse YEKDEM'in) EPİAŞ etiketli güvenilir
kaynaktan gelmesi gerekir.

Çağrıldığı yerler:
- main.get_prices_with_epias_fallback() → GET /api/epias/prices/{period} (manuel akış fiyat durumu)
- calculator.calculate_offer() → POST /full-process, POST /calculate-offer (AI akışı meta_price_provenance)
- main.create_offer() → POST /offers (sunucu kapısı + snapshot)
- main.generate_pdf_for_offer() → POST /offers/{id}/generate-pdf (snapshot kapısı)
- main.generate_pdf_simple() → POST /generate-pdf-simple (doğrudan API kapısı)
- main.generate_pdf_direct() → POST /generate-pdf-direct (doğrudan API kapısı)
- main.generate_html_for_offer() → POST /offers/{id}/generate-html (snapshot kapısı)
- main.generate_html_direct() → POST /generate-html-direct (doğrudan API kapısı)
- market_price_admin_service.MarketPriceAdminService._handle_insert() / _handle_update() → YEKDEM_AUDIT_PRICE_TYPE (açık sıfır satırı yazımı)
- market_price_admin_service.MarketPriceAdminService.get_history() → GET /admin/market-prices/history?price_type=YEKDEM

Multitenant: piyasa fiyat tabloları tenant kolonu taşımaz (piyasa geneli veri).
Bu modül yalnız OKUR ve tenant kapsamına dokunmaz.
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

# Sürüm 2: kullanıcı onayıyla doğrulama kaldırıldı, status='final' şartı eklendi.
# Sürüm 1 snapshot'ları (kullanıcı onaylı olabilir) PDF kapısında doğrulanmış SAYILMAZ.
PROVENANCE_VERSION = 2
# Teklif değeri ile DB'den türetilen değerin "aynı" sayılacağı tolerans (TL/MWh).
PRICE_TOLERANCE_TL_PER_MWH = 0.01
# Hariç seçiminde teklif hesabındaki YEKDEM tutarının "sıfır" sayılacağı tolerans (TL).
AMOUNT_TOLERANCE_TL = 0.01

TRUSTED_REFERENCE_SOURCES = frozenset({"epias_manual", "epias_api", "manual_override"})
EPIAS_REFERENCE_SOURCES = frozenset({"epias_manual", "epias_api"})
EPIAS_HOURLY_SOURCES = frozenset({"epias_excel", "epias_api"})
# Faz 1'de kaldırılan açılış tohumlamasının (main._add_sample_market_prices) ve
# mock EPİAŞ senkronunun kayıtta bıraktığı izler.
DEV_SAMPLE_NOTE_PREFIX = "Sample data"
MOCK_SYNC_UPDATED_BY = "mock_sync"
FINAL_STATUS = "final"
# Açıkça girilen YEKDEM=0'ın denetim satırı: price_change_history.price_type değeri.
# Satır, YEKDEM'i taşıyan PTF kaydına (price_record_id) bağlanır. Geçmiş ucu bu tipi
# zaten belgeler ("PTF, SMF, YEKDEM"); PTF geçmiş görünümü bu satırları içermez.
YEKDEM_AUDIT_PRICE_TYPE = "YEKDEM"

YEKDEM_MODE_INCLUDED = "included"
YEKDEM_MODE_EXCLUDED = "excluded"
YEKDEM_MODES = frozenset({YEKDEM_MODE_INCLUDED, YEKDEM_MODE_EXCLUDED})

REASON_PTF_MISSING = "ptf_missing"
REASON_PTF_UNVERIFIED = "ptf_unverified"
REASON_PTF_PROVISIONAL = "ptf_provisional"
REASON_YEKDEM_MISSING = "yekdem_missing"
REASON_YEKDEM_ZERO_UNVERIFIED = "yekdem_zero_unverified"
REASON_YEKDEM_UNVERIFIED = "yekdem_unverified"
REASON_YEKDEM_PROVISIONAL = "yekdem_provisional"
REASON_YEKDEM_MODE_REQUIRED = "yekdem_mode_required"
REASON_YEKDEM_MODE_INVALID = "yekdem_mode_invalid"
REASON_YEKDEM_MODE_CONFLICT = "yekdem_mode_conflict"

PROVISIONAL_REASONS = frozenset({REASON_PTF_PROVISIONAL, REASON_YEKDEM_PROVISIONAL})

REASON_MESSAGES = {
    REASON_PTF_MISSING: "Teklif PTF değeri eksik (0'dan büyük bir değer gerekli).",
    REASON_PTF_UNVERIFIED: (
        "PTF, dönemin güvenilir kayıtlı değeriyle eşleşmiyor ya da kaynağı "
        "doğrulanamıyor. Kesin teklif için PTF, Piyasa Fiyatları kaydı ya da "
        "EPİAŞ saatlik yüklemesiyle birebir aynı olmalıdır."
    ),
    REASON_PTF_PROVISIONAL: (
        "Dönemin PTF kaydı kesinleşmemiş (provisional). Bu fiyat yalnız TASLAK "
        "hesapta kullanılabilir; kesin teklif için kaydı Piyasa Fiyatları "
        "ekranında 'final' yapın."
    ),
    REASON_YEKDEM_MISSING: (
        "YEKDEM birim bedeli bilinmiyor. Dönemin YEKDEM değerini Piyasa Fiyatları "
        "ekranından girin ya da YEKDEM uygulamasını açıkça 'hariç' seçin."
    ),
    REASON_YEKDEM_ZERO_UNVERIFIED: (
        "Kayıtlı YEKDEM 0 için Piyasa Fiyatları ekranında yapılmış geçerli bir açık "
        "giriş kaydı yok (eski ya da anlamı doğrulanmamış sıfır); kesin teklifte "
        "kullanılamaz. Gerçek sıfırsa YEKDEM'i Piyasa Fiyatları ekranında 0 olarak "
        "açıkça girin ve kaydı kesinleştirin."
    ),
    REASON_YEKDEM_UNVERIFIED: (
        "YEKDEM, dönemin güvenilir kayıtlı değeriyle eşleşmiyor ya da kaynağı "
        "doğrulanamıyor."
    ),
    REASON_YEKDEM_PROVISIONAL: (
        "Dönemin YEKDEM kaydı kesinleşmemiş (provisional). Bu değer yalnız TASLAK "
        "hesapta kullanılabilir; kesin teklif için kaydı 'final' yapın."
    ),
    REASON_YEKDEM_MODE_REQUIRED: (
        "YEKDEM uygulaması seçilmedi (faturada YEKDEM kalemi yok). 'Dahil' ya da "
        "'Hariç' seçimini açıkça yapın."
    ),
    REASON_YEKDEM_MODE_INVALID: (
        "Geçersiz YEKDEM seçimi: 'included' ya da 'excluded' olmalı "
        "(doğrulanmış bir muafiyet kuralı tanımlı değil)."
    ),
    REASON_YEKDEM_MODE_CONFLICT: (
        "YEKDEM 'hariç' seçildi, ancak teklif hesabında YEKDEM tutarı var. "
        "Seçim ile hesap çelişiyor."
    ),
}

_PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def normalize_period(period: Any) -> Optional[str]:
    """YYYY-MM biçimini doğrular; değilse None (sistem doğrulaması yapılamaz)."""
    if not period:
        return None
    p = str(period).strip()
    return p if _PERIOD_RE.match(p) else None


def parse_yekdem_mode(raw: Any) -> tuple[Optional[str], bool]:
    """YEKDEM seçimini (mod, geçerli_mi) olarak döndürür.

    None ya da boş değer → (None, True): seçim yapılmadı (kapı yekdem_mode_required).
    Tanınmayan değer ('exempt' dahil) → (None, False): kapı yekdem_mode_invalid.

    Çağrıldığı yerler:
    - price_provenance.build_price_provenance()
    """
    if raw is None:
        return (None, True)
    mode = str(raw).strip().lower()
    if not mode:
        return (None, True)
    return (mode, True) if mode in YEKDEM_MODES else (None, False)


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
    final: bool  # kayıt kesinleşmiş mi (saatlik EPİAŞ verisi her zaman kesin)


@dataclass(frozen=True)
class YekdemResolution:
    """Dönem YEKDEM birim bedelinin çözümü."""
    value: Optional[float]  # None = bilinmiyor
    status: str  # known | zero_unverified | invalid | missing
    source: Optional[str]
    trusted: bool
    epias: bool
    final: bool
    record_status: Optional[str]  # kaydın durumu (final | provisional); kayıt yoksa None
    # Kayıtlı 0 için açık giriş kanıtı: price_change_history satırının id'si.
    zero_audit_id: Optional[int] = None


_YEKDEM_MISSING = YekdemResolution(None, "missing", None, False, False, False, None)


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


def _acik_sifir_kaydi(db: Session, record: Any):
    """Kayıtlı 0'ı hâlâ temsil eden açık giriş satırı (price_change_history); yoksa None.

    Geçmiş tablosunu YALNIZ yetkili yönetim servisi yazar; açıkça girilen YEKDEM=0
    için PTF satırından sonra ayrı bir satır (price_type='YEKDEM', new_value=0) ekler.
    Satır kayıtlı 0'ı ancak şu durumda temsil eder:
    - kaydın EN SON geçmiş satırıdır (id sırası; saat çözünürlüğüne bağlı değil).
      YEKDEM yeniden girilmeden yapılan sonraki yetkili güncelleme (ör. taslağı
      kesinleştirme, PTF düzeltme) yeni bir PTF satırı yazar ve teyidi düşürür;
    - durumu ve kaynağı kaydın bugünkü durumu/kaynağıyla aynıdır. Geçmiş yazmayan
      başka bir yolun (hızlı kayıt, betik, senkron) sonraki yazımı kaynağı ya da
      durumu değiştirir ve teyidi düşürür.
    Kilit aç/kapa geçmiş satırı yazmaz ve durum/kaynağa dokunmaz; teyidi etkilemez.

    Çağrıldığı yerler:
    - price_provenance.resolve_period_yekdem() → kayıtlı 0'ın açık giriş kanıtı
    """
    from .database import PriceChangeHistory

    son = (
        db.query(PriceChangeHistory)
        .filter(PriceChangeHistory.price_record_id == record.id)
        .order_by(PriceChangeHistory.id.desc())
        .first()
    )
    if (son is None or son.price_type != YEKDEM_AUDIT_PRICE_TYPE
            or _finite(son.new_value) != 0.0):
        return None
    if (str(son.new_status or "") != str(record.status or "")
            or str(son.source or "") != str(record.source or "")):
        return None
    return son


def reference_trust(record: Any) -> tuple[bool, bool]:
    """market_reference_prices kaydı için (güvenilir kaynak, EPİAŞ etiketli).

    Açılıştaki geliştirme örnek verisi ("Sample data (dev)") ve mock senkron
    izi, kaynak etiketi ne olursa olsun güvenilir SAYILMAZ. Kaydın kesinleşip
    kesinleşmediği ayrıca reference_is_final() ile denetlenir.

    Çağrıldığı yerler:
    - price_provenance.resolve_period_yekdem()
    - price_provenance.effective_ptf_candidates()
    """
    note = str(getattr(record, "source_note", None) or "")
    if note.startswith(DEV_SAMPLE_NOTE_PREFIX) or getattr(record, "updated_by", None) == MOCK_SYNC_UPDATED_BY:
        return (False, False)
    source = str(getattr(record, "source", None) or "")
    trusted = source in TRUSTED_REFERENCE_SOURCES
    return (trusted, trusted and source in EPIAS_REFERENCE_SOURCES)


def reference_is_final(record: Any) -> bool:
    """Kayıt AÇIKÇA kesinleşmiş mi (status == 'final')?

    NULL ya da boş durum kesin SAYILMAZ (fail-closed). Canonical şemada kolon
    zaten NOT NULL'dur.

    Çağrıldığı yerler:
    - price_provenance.resolve_period_yekdem()
    - price_provenance.effective_ptf_candidates()
    """
    return str(getattr(record, "status", None) or "") == FINAL_STATUS


def resolve_period_yekdem(db: Optional[Session], period: Any) -> YekdemResolution:
    """Dönem YEKDEM birim bedeli — kayıt yoksa 'missing' (ASLA sabit/0 ile doldurulmaz).

    Kayıtlı 0 için ayrım denetim kaydıyla yapılır (kolon NOT NULL, varsayılan 0):
    - Geçerli bir açık sıfır satırı varsa (_acik_sifir_kaydi) sıfır açıkça girilmiştir:
      'known'. Kayıt kesinse kesin gerçek sıfır; taslaksa yalnız taslak.
    - Yoksa 'zero_unverified': eski ya da anlamı doğrulanmamış sıfır. Otomatik gerçek
      ya da eksik SAYILMAZ; değer 0 olarak taşınır ve kesin teklifte kullanılmaz.

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
    record_status = str(record.status) if record.status else None
    final = reference_is_final(record)
    if value == 0.0:
        denetim = _acik_sifir_kaydi(db, record)
        if denetim is not None:
            trusted, epias = reference_trust(record)
            return YekdemResolution(0.0, "known", source, trusted, epias, final, record_status,
                                    zero_audit_id=denetim.id)
        return YekdemResolution(0.0, "zero_unverified", source, False, False, final, record_status)
    if value < 0:
        return YekdemResolution(value, "invalid", source, False, False, final, record_status)
    trusted, epias = reference_trust(record)
    return YekdemResolution(value, "known", source, trusted, epias, final, record_status)


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
    ref_final = reference_is_final(record) if record is not None else False
    if record is not None and record.source == "manual_override" and ref_ptf and ref_ptf > 0:
        return [PriceCandidate(ref_ptf, "manual_override", record.source, ref_trusted, ref_epias,
                               ref_final)]

    hourly_trusted, hourly_epias = _hourly_trust(db, period)
    if mp.OFFER_USE_REAL_CONSUMPTION and customer_id:
        cw = mp.consumption_weighted_ptf(db, period, customer_id)
        if cw is not None and cw > 0:
            return [PriceCandidate(cw, f"hourly_consumption:{customer_id}", "hourly",
                                   hourly_trusted, hourly_epias, True)]

    hourly: list[PriceCandidate] = []
    for profile in mp.PROFILE_WEIGHTS:
        wr = mp.weighted_ptf_for_profile(db, period, profile)
        if wr.ptf_tl_per_mwh is not None and wr.ptf_tl_per_mwh > 0:
            hourly.append(PriceCandidate(wr.ptf_tl_per_mwh, f"hourly_weighted:{profile}",
                                         "hourly", hourly_trusted, hourly_epias, True))
    if hourly:
        return hourly

    if record is not None and ref_ptf and ref_ptf > 0:
        return [PriceCandidate(ref_ptf, "reference_scalar", record.source, ref_trusted, ref_epias,
                               ref_final)]
    return []


def _dogrulanmamis(**ek: Any) -> dict[str, Any]:
    """Doğrulanmamış bileşen için ortak alanlar."""
    return {"source": None, "trusted": False, "final": False, "system_verified": False,
            "epias": False, **ek}


def build_price_provenance(
    db: Optional[Session],
    *,
    period: Any,
    ptf: Any,
    yekdem: Any,
    yekdem_mode: Any,
    mode_basis: str = "user",
    customer_id: Optional[str] = None,
    offer_yekdem_tl: Any = None,
) -> dict[str, Any]:
    """Teklif fiyatının kaynağı ve kesinleştirme kararı (JSON'a yazılabilir dict).

    Karar YALNIZ sunucu verisine dayanır; istemci beyanı parametre olarak bile
    alınmaz. `db=None` ya da DB hatası → sistem doğrulaması yapılamaz
    (system_lookup='unavailable'/'error') ve fiyat doğrulanmış SAYILMAZ
    (fail-closed). Değerler asla değiştirilmez ya da doldurulmaz; yalnız
    sınıflandırılır.

    Args:
        yekdem_mode: 'included' | 'excluded'. None = seçim yapılmadı (AI akışında
            faturada YEKDEM kalemi yoksa) → yekdem_mode_required.
        mode_basis: seçimin dayanağı: 'user' (açık seçim), 'invoice' (faturada
            YEKDEM kalemi var) ya da 'default' (uç varsayılanı: included).
        offer_yekdem_tl: teklif hesabındaki YEKDEM tutarı. Hariç seçiminde 0
            değilse seçim ile hesap çelişir (yekdem_mode_conflict).

    Çağrıldığı yerler: modül başlığındaki liste. GET fiyat durumu ve AI hesabı
    doğrudan çağırır; POST /offers ve doğrudan belge (PDF/HTML) uçları
    offer_price_gate() üzerinden çağırır.
    """
    p = normalize_period(period)
    ptf_value = _finite(ptf)
    mode, mode_valid = parse_yekdem_mode(yekdem_mode)
    included = mode == YEKDEM_MODE_INCLUDED
    yekdem_value = _finite(yekdem) if included else None

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
        ptf_block.update(_dogrulanmamis(status="missing", source_detail=None))
        reasons.append(REASON_PTF_MISSING)
    else:
        eslesen = [c for c in candidates if abs(c.value - ptf_value) <= PRICE_TOLERANCE_TL_PER_MWH]
        # Aynı değere birden çok aday eşleşirse en güçlüsü (güvenilir + kesin) seçilir.
        match = max(eslesen, key=lambda c: (c.trusted and c.final, c.trusted), default=None)
        if match is None:
            ptf_block.update(_dogrulanmamis(status="user_entered", source_detail=None))
            ptf_block["source"] = "user_entered"
            reasons.append(REASON_PTF_UNVERIFIED)
        else:
            ptf_block.update(status="matched", source=match.source,
                             source_detail=match.source_detail, trusted=match.trusted,
                             final=match.final, system_verified=match.trusted and match.final,
                             epias=match.epias)
            if not match.trusted:
                reasons.append(REASON_PTF_UNVERIFIED)
            elif not match.final:
                reasons.append(REASON_PTF_PROVISIONAL)

    # ── YEKDEM ─────────────────────────────────────────────────────────────
    # Dönemin kendi YEKDEM durumu seçimden BAĞIMSIZ döner: istemci "dahil"e
    # geçildiğinde değerin kesinleşip kesinleşmediğini buradan gösterir.
    period_verified = bool(period_yekdem.status == "known" and period_yekdem.trusted
                           and period_yekdem.final)
    yekdem_block: dict[str, Any] = {
        "mode": mode,
        "mode_basis": mode_basis if mode is not None else None,
        "period_status": period_yekdem.status,
        "period_value": period_yekdem.value,
        "period_record_status": period_yekdem.record_status,
        "period_trusted": period_yekdem.trusted,
        "period_verified": period_verified,
        "period_zero_audit_id": period_yekdem.zero_audit_id,
    }
    if not mode_valid:
        yekdem_block.update(_dogrulanmamis(value=None, status="mode_invalid"))
        reasons.append(REASON_YEKDEM_MODE_INVALID)
    elif mode is None:
        yekdem_block.update(_dogrulanmamis(value=None, status="mode_required"))
        reasons.append(REASON_YEKDEM_MODE_REQUIRED)
    elif not included:
        # Hariç: açık seçimdir, değer gerekmez ve teklif fiyatına YEKDEM girmez.
        yekdem_block.update(_dogrulanmamis(value=None, status=mode))
        teklif_yekdem = _finite(offer_yekdem_tl)
        if teklif_yekdem is not None and abs(teklif_yekdem) > AMOUNT_TOLERANCE_TL:
            reasons.append(REASON_YEKDEM_MODE_CONFLICT)
    elif yekdem_value is None:
        yekdem_block.update(_dogrulanmamis(value=None, status="missing"))
        reasons.append(REASON_YEKDEM_MISSING)
    elif yekdem_value < 0:
        yekdem_block.update(_dogrulanmamis(value=yekdem_value, status="invalid"))
        reasons.append(REASON_YEKDEM_UNVERIFIED)
    elif (period_yekdem.status == "known" and period_yekdem.value is not None
            and abs(period_yekdem.value - yekdem_value) <= PRICE_TOLERANCE_TL_PER_MWH):
        # Gerçek 0 da buradan geçer: dönem 'known' 0 yalnız açık giriş kaydıyla oluşur.
        yekdem_block.update(value=yekdem_value, status="matched", source=period_yekdem.source,
                            trusted=period_yekdem.trusted, final=period_yekdem.final,
                            system_verified=period_verified, epias=period_yekdem.epias)
        if not period_yekdem.trusted:
            reasons.append(REASON_YEKDEM_UNVERIFIED)
        elif not period_yekdem.final:
            reasons.append(REASON_YEKDEM_PROVISIONAL)
    elif yekdem_value == 0.0 and period_yekdem.status == "zero_unverified":
        # Kayıtlı 0'ın açık ve kesin giriş kaydı yok: değer 0 olarak taşınır, otomatik
        # gerçek ya da eksik sayılmaz, kesinleşmez. Hariç'e çevrilmez.
        yekdem_block.update(_dogrulanmamis(value=0.0, status="zero_unverified"))
        reasons.append(REASON_YEKDEM_ZERO_UNVERIFIED)
    else:
        yekdem_block.update(_dogrulanmamis(value=yekdem_value, status="user_entered"))
        yekdem_block["source"] = "user_entered"
        reasons.append(REASON_YEKDEM_UNVERIFIED)

    verified = not reasons
    epias_basis = bool(verified and ptf_block["epias"] and (not included or yekdem_block["epias"]))
    return {
        "version": PROVENANCE_VERSION,
        "period": p,
        "system_lookup": lookup,
        "ptf": ptf_block,
        "yekdem": yekdem_block,
        "verified": verified,
        "verified_by": "system" if verified else None,
        # Doğrulanmamış fiyatla hesap yalnız TASLAK olarak gösterilebilir.
        "draft_only": not verified,
        "provisional": any(r in PROVISIONAL_REASONS for r in reasons),
        "epias_basis": epias_basis,
        "blocking_reasons": reasons,
        "messages": [REASON_MESSAGES[r] for r in reasons],
    }


def offer_price_gate(
    db: Optional[Session],
    *,
    period: Any,
    ptf: Any,
    yekdem: Any,
    yekdem_mode: Optional[str],
    customer_id: Optional[str] = None,
    offer_yekdem_tl: Any = None,
) -> dict[str, Any]:
    """Teklif kaydı ve doğrudan belge (PDF/HTML) uçlarının ORTAK fiyat kapısı.

    İstek YEKDEM seçimi göndermezse 'included' kabul edilir (en katı yol:
    doğrulanmış YEKDEM ister; sessiz "hariç" YOK) ve dayanağı 'default' yazılır.
    Dönen provenance['verified'] False ise uç 422 (price_block_content) döner.
    İstemcinin "doğrulandı" beyanı parametre olarak bile alınmaz.

    Çağrıldığı yerler:
    - main.create_offer() → POST /offers
    - main.generate_pdf_direct() → POST /generate-pdf-direct
    - main.generate_pdf_simple() → POST /generate-pdf-simple
    - main.generate_html_direct() → POST /generate-html-direct
    """
    return build_price_provenance(
        db,
        period=period,
        ptf=ptf,
        yekdem=yekdem,
        yekdem_mode=yekdem_mode if yekdem_mode is not None else YEKDEM_MODE_INCLUDED,
        mode_basis="user" if yekdem_mode is not None else "default",
        customer_id=customer_id,
        offer_yekdem_tl=offer_yekdem_tl,
    )


def yekdem_applied(provenance: dict[str, Any]) -> bool:
    """Teklif fiyatına YEKDEM uygulanıyor mu (seçim 'included')? Hariç seçiminde
    uygulanan YEKDEM 0'dır; hariç ile gerçek 0 ayrımı provenance['yekdem']['mode']
    alanındadır.

    Çağrıldığı yerler: offer_price_gate() ile aynı uçlar (uygulanan YEKDEM değeri).
    """
    return (provenance.get("yekdem") or {}).get("mode") == YEKDEM_MODE_INCLUDED


def price_block_content(provenance: dict[str, Any]) -> dict[str, Any]:
    """Kesinleştirme reddi için JSON gövdesi (HTTP 422).

    Çağrıldığı yerler:
    - main.create_offer() → POST /offers
    - main.generate_pdf_simple() → POST /generate-pdf-simple
    - main.generate_pdf_direct() → POST /generate-pdf-direct
    - main.generate_html_direct() → POST /generate-html-direct
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
    geçmiş kayıt doğrulanmış SAYILMAZ ve yeniden hesaplanmaz. Sürüm 1
    snapshot'ları (kullanıcı onayıyla doğrulanmış olabilir) de False döner.

    Çağrıldığı yerler:
    - main.generate_pdf_for_offer() → POST /offers/{id}/generate-pdf
    - main.generate_html_for_offer() → POST /offers/{id}/generate-html
    """
    if not isinstance(calculation_result, dict):
        return False
    prov = calculation_result.get("meta_price_provenance")
    return (isinstance(prov, dict)
            and prov.get("version") == PROVENANCE_VERSION
            and prov.get("verified") is True
            and prov.get("verified_by") == "system")
