"""
EPDK Dağıtım Tarifeleri - Ocak 2025

Bu tarifeler EPDK tarafından belirlenir ve periyodik olarak güncellenir.
Faturadaki tarife grubu, gerilim tipi ve terim tipine göre dağıtım birim fiyatı belirlenir.

KULLANIM:
1. Faturadan tarife meta'sı okunur (sağ üst köşe: "SANAYİ OG ÇİFT TERİM")
2. Meta normalize edilir → anahtar oluşturulur
3. EPDK tablosundan birim fiyat çekilir
4. Dağıtım bedeli = kWh × birim fiyat
"""

import logging
from typing import Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class DistributionTariff:
    """Dağıtım tarife bilgisi"""
    tariff_group: str  # sanayi, kamu_ozel
    voltage_level: str  # AG, OG
    term_type: str  # tek_terim, çift_terim
    unit_price_tl_per_kwh: float  # TL/kWh


@dataclass
class TariffLookupResult:
    """Tarife lookup sonucu"""
    success: bool
    unit_price: Optional[float]
    tariff_key: str  # "sanayi/OG/çift_terim"
    normalized_group: str
    normalized_voltage: str
    normalized_term: str
    error_message: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════════════
# EPDK DAĞITIM TARİFELERİ — DÖNEM BAZLI
# ═══════════════════════════════════════════════════════════════════════════════
# EPDK tarifeleri periyodik olarak güncellenir.
# Fatura dönemi (period) hangi tarife dönemine düşüyorsa o fiyat uygulanır.
#
# Dönem kuralı:
#   period < "2026-04"  → Şubat 2026 tarifeleri (eski)
#   period >= "2026-04" → Nisan 2026 tarifeleri (yeni, ~%32 zam)
#
# Birimler: TL/kWh (Excel'deki kr/kWh × 10 = TL/MWh, / 1000 = TL/kWh)
# ═══════════════════════════════════════════════════════════════════════════════

# Şubat 2026 tarifeleri (period < 2026-04 için geçerli)
_TARIFFS_FEB_2026: list[DistributionTariff] = [
    # İSK
    DistributionTariff("isk_sanayi", "iletim", "iletim", 0.00000),
    # OG ÇT
    DistributionTariff("sanayi", "OG", "çift_terim", 0.81060),
    DistributionTariff("ticarethane", "OG", "çift_terim", 1.26329),
    DistributionTariff("mesken", "OG", "çift_terim", 1.25129),
    DistributionTariff("aydinlatma", "OG", "çift_terim", 1.21249),
    DistributionTariff("tarimsal", "OG", "çift_terim", 1.04042),
    # OG TT
    DistributionTariff("sanayi", "OG", "tek_terim", 0.89537),
    DistributionTariff("ticarethane", "OG", "tek_terim", 1.57581),
    DistributionTariff("mesken", "OG", "tek_terim", 1.54502),
    DistributionTariff("aydinlatma", "OG", "tek_terim", 1.51248),
    DistributionTariff("tarimsal", "OG", "tek_terim", 1.29543),
    # AG TT
    DistributionTariff("sanayi", "AG", "tek_terim", 1.38532),
    DistributionTariff("ticarethane", "AG", "tek_terim", 1.87741),
    DistributionTariff("mesken", "AG", "tek_terim", 1.83617),
    DistributionTariff("mesken_sehit_gazi", "AG", "tek_terim", 1.03557),
    DistributionTariff("tarimsal", "AG", "tek_terim", 1.54263),
    DistributionTariff("aydinlatma", "AG", "tek_terim", 1.79815),
    # AG ÇT (tahmini)
    DistributionTariff("sanayi", "AG", "çift_terim", 1.20),
    DistributionTariff("ticarethane", "AG", "çift_terim", 1.65),
    DistributionTariff("mesken", "AG", "çift_terim", 1.60),
    DistributionTariff("tarimsal", "AG", "çift_terim", 1.35),
    DistributionTariff("aydinlatma", "AG", "çift_terim", 1.55),
    # kamu_ozel alias
    DistributionTariff("kamu_ozel", "OG", "çift_terim", 1.26329),
    DistributionTariff("kamu_ozel", "OG", "tek_terim", 1.57581),
    DistributionTariff("kamu_ozel", "AG", "tek_terim", 1.87741),
    DistributionTariff("kamu_ozel", "AG", "çift_terim", 1.65),
]

# Nisan 2026 tarifeleri (period >= 2026-04 için geçerli, ~%32 zam)
_TARIFFS_APR_2026: list[DistributionTariff] = [
    # İSK
    DistributionTariff("isk_sanayi", "iletim", "iletim", 0.00000),
    # OG ÇT
    DistributionTariff("sanayi", "OG", "çift_terim", 1.07050),
    DistributionTariff("ticarethane", "OG", "çift_terim", 1.66835),
    DistributionTariff("mesken", "OG", "çift_terim", 1.65248),
    DistributionTariff("aydinlatma", "OG", "çift_terim", 1.60100),  # tahmini %32
    DistributionTariff("tarimsal", "OG", "çift_terim", 1.37400),
    # OG TT
    DistributionTariff("sanayi", "OG", "tek_terim", 1.18246),
    DistributionTariff("ticarethane", "OG", "tek_terim", 2.08106),
    DistributionTariff("mesken", "OG", "tek_terim", 2.04040),
    DistributionTariff("aydinlatma", "OG", "tek_terim", 1.99700),   # tahmini %32
    DistributionTariff("tarimsal", "OG", "tek_terim", 1.71078),
    # AG TT
    DistributionTariff("sanayi", "AG", "tek_terim", 1.82950),
    DistributionTariff("ticarethane", "AG", "tek_terim", 2.47936),
    DistributionTariff("mesken", "AG", "tek_terim", 2.42490),
    DistributionTariff("mesken_sehit_gazi", "AG", "tek_terim", 1.36700),  # tahmini %32
    DistributionTariff("tarimsal", "AG", "tek_terim", 2.03600),     # tahmini %32
    DistributionTariff("aydinlatma", "AG", "tek_terim", 2.37600),   # tahmini %32
    # AG ÇT (tahmini)
    DistributionTariff("sanayi", "AG", "çift_terim", 1.58400),      # tahmini %32
    DistributionTariff("ticarethane", "AG", "çift_terim", 2.17800),  # tahmini %32
    DistributionTariff("mesken", "AG", "çift_terim", 2.11200),      # tahmini %32
    DistributionTariff("tarimsal", "AG", "çift_terim", 1.78200),    # tahmini %32
    DistributionTariff("aydinlatma", "AG", "çift_terim", 2.04600),  # tahmini %32
    # kamu_ozel alias
    DistributionTariff("kamu_ozel", "OG", "çift_terim", 1.66835),
    DistributionTariff("kamu_ozel", "OG", "tek_terim", 2.08106),
    DistributionTariff("kamu_ozel", "AG", "tek_terim", 2.47936),
    DistributionTariff("kamu_ozel", "AG", "çift_terim", 2.17800),
    # OSB tarifeleri (özel bölge — kendi dağıtım bedelleri)
    # Çerkezköy: Tüm kalemler kWh'ye bölünmüş toplam = 0.604 TL/kWh
    # (Sabit 3681/23910 + Değişken 0.135 + Tek Terim DB 0.315 = ~0.604)
    DistributionTariff("osb_cerkezkoy", "OG", "tek_terim", 0.60400),  # Çerkezköy OSB toplam
    # İkitelli: İletim (0.23) + OSB Dağıtım (0.580532) = 0.810532 TL/kWh
    DistributionTariff("osb_ikitelli", "OG", "tek_terim", 0.81053),   # İkitelli OSB toplam
]

# Dönem → tarife tablosu eşleştirmesi
# Tuple: (başlangıç_period_inclusive, tarife_listesi)
# Sıralama: en yeni önce — ilk eşleşen kullanılır
_TARIFF_PERIODS: list[tuple[str, list[DistributionTariff]]] = [
    ("2026-04", _TARIFFS_APR_2026),   # Nisan 2026+ → yeni tarifeler
    ("2000-01", _TARIFFS_FEB_2026),   # Önceki tüm dönemler → Şubat 2026 tarifeleri
]


def _get_tariffs_for_period(period: Optional[str] = None) -> list[DistributionTariff]:
    """Dönem bazlı tarife tablosunu döndür.

    Args:
        period: "YYYY-MM" formatında dönem. None ise en güncel tarife kullanılır.

    Returns:
        İlgili dönemin DistributionTariff listesi.
    """
    if period is None:
        return _TARIFFS_APR_2026  # varsayılan: en güncel

    for start_period, tariffs in _TARIFF_PERIODS:
        if period >= start_period:
            return tariffs

    return _TARIFFS_FEB_2026  # fallback


def _build_lookup(tariffs: list[DistributionTariff]) -> dict[str, float]:
    """Tarife listesinden hızlı lookup dict oluştur."""
    return {
        f"{t.tariff_group}/{t.voltage_level}/{t.term_type}": t.unit_price_tl_per_kwh
        for t in tariffs
    }


# Geriye uyumluluk: period parametresi olmayan çağrılar için varsayılan tablo
# (eski kod DISTRIBUTION_TARIFFS ve _TARIFF_LOOKUP kullanıyor olabilir)
DISTRIBUTION_TARIFFS = _TARIFFS_APR_2026

_TARIFF_LOOKUP: dict[str, float] = _build_lookup(DISTRIBUTION_TARIFFS)


# ═══════════════════════════════════════════════════════════════════════════════
# NORMALIZE FONKSİYONLARI
# ═══════════════════════════════════════════════════════════════════════════════

def normalize_tariff_group(raw: str) -> str:
    """
    Faturadaki tarife grubu metnini normalize et.
    
    Örnekler:
    - "SANAYİ" → "sanayi"
    - "Sanayi" → "sanayi"
    - "KAMU VE ÖZEL SEKTÖR" → "kamu_ozel"
    - "Ticarethane" → "ticarethane"
    - "Mesken" → "mesken"
    - "Tarımsal" → "tarimsal"
    - "Aydınlatma" → "aydinlatma"
    - "Mesken Şehit Gazi" → "mesken_sehit_gazi"
    - "TİCARETHANE AG TEK TERİM" → "ticarethane" (tam string'den de çıkarır)
    """
    if not raw:
        return "unknown"
    
    # Türkçe karakterleri normalize et
    import unicodedata
    raw_normalized = unicodedata.normalize('NFKC', raw)
    tr_map = str.maketrans('İIŞĞÜÖÇ', 'iışğüöç')
    raw_lower = raw_normalized.translate(tr_map).lower().strip()
    
    # Şehit Gazi - en spesifik, önce kontrol et
    if "şehit" in raw_lower or "sehit" in raw_lower or "gazi" in raw_lower:
        return "mesken_sehit_gazi"
    
    # Sanayi
    if "sanayi" in raw_lower or "sanayı" in raw_lower or "sanayii" in raw_lower:
        return "sanayi"
    
    # Tarımsal
    if "tarım" in raw_lower or "tarim" in raw_lower or "zirai" in raw_lower:
        return "tarimsal"
    
    # Aydınlatma
    if "aydınlatma" in raw_lower or "aydinlatma" in raw_lower or "sokak" in raw_lower:
        return "aydinlatma"
    
    # Ticarethane (ticari, dükkan, mağaza, vb.)
    ticarethane_keywords = ["ticarethane", "ticaret", "ticari", "dükkan", "dukkan", "mağaza", "magaza", "işyeri", "isyeri"]
    if any(kw in raw_lower for kw in ticarethane_keywords):
        return "ticarethane"
    
    # Mesken (konut, ev, daire)
    mesken_keywords = ["mesken", "konut", "ev", "daire", "apartman", "site"]
    if any(kw in raw_lower for kw in mesken_keywords):
        return "mesken"
    
    # Kamu ve Özel Sektör (genel kategori - ticarethane ile eşleşir)
    kamu_keywords = ["kamu", "özel", "ozel", "resmi", "kurum"]
    if any(kw in raw_lower for kw in kamu_keywords):
        return "kamu_ozel"
    
    return "unknown"


def normalize_voltage_level(raw: str) -> str:
    """
    Faturadaki gerilim seviyesini normalize et.
    
    Örnekler:
    - "OG" → "OG"
    - "O.G." → "OG"
    - "Orta Gerilim" → "OG"
    - "ORTA GERİLİM" → "OG"
    - "AG" → "AG"
    - "A.G." → "AG"
    - "Alçak Gerilim" → "AG"
    - "SANAYİ OG ÇİFT TERİM" → "OG" (tam string'den de çıkarır)
    """
    if not raw:
        return "unknown"
    
    # Türkçe karakterleri normalize et
    import unicodedata
    raw_normalized = unicodedata.normalize('NFKC', raw)
    tr_map = str.maketrans('İIŞĞÜÖÇ', 'iışğüöç')
    raw_lower = raw_normalized.translate(tr_map).lower().strip()
    raw_upper = raw_lower.upper()
    # Noktalı versiyonları da yakala: O.G., A.G.
    raw_clean = raw_upper.replace(".", "").replace(" ", "")
    
    # OG kontrolü
    if "OG" in raw_clean or "ORTA" in raw_upper or "ORTAGERILIM" in raw_clean:
        return "OG"
    
    # AG kontrolü
    if "AG" in raw_clean or "ALÇAK" in raw_upper or "ALCAK" in raw_upper or "ALCAKGERILIM" in raw_clean:
        return "AG"
    
    # YG kontrolü (yüksek gerilim - nadir)
    if "YG" in raw_clean or "YÜKSEK" in raw_upper or "YUKSEK" in raw_upper:
        return "YG"
    
    return "unknown"


def normalize_term_type(raw: str) -> str:
    """
    Faturadaki terim tipini normalize et.
    
    Örnekler:
    - "Tek Terim" → "tek_terim"
    - "TEK TERİM" → "tek_terim"
    - "Tek Terimli" → "tek_terim"
    - "Tek Zamanlı" → "tek_terim"
    - "Single" → "tek_terim"
    - "Çift Terim" → "çift_terim"
    - "ÇİFT TERİM" → "çift_terim"
    - "Çift Terimli" → "çift_terim"
    - "Çok Zamanlı" → "çift_terim"
    - "Multi" → "çift_terim"
    - "T1-T2-T3" → "çift_terim"
    - "SANAYİ OG ÇİFT TERİM" → "çift_terim" (tam string'den de çıkarır)
    """
    if not raw:
        return "unknown"
    
    # Türkçe karakterleri normalize et (İ → i, I → ı, vb.)
    import unicodedata
    raw_normalized = unicodedata.normalize('NFKC', raw)
    # Türkçe büyük harfleri manuel çevir
    tr_map = str.maketrans('İIŞĞÜÖÇ', 'iışğüöç')
    raw_lower = raw_normalized.translate(tr_map).lower().strip()
    
    # Çift terim / çok zamanlı - önce kontrol et (daha spesifik)
    cift_keywords = ["çift", "cift", "çok", "cok", "multi", "t1-t2", "t1/t2", "gündüz-puant", "gunduz-puant"]
    if any(kw in raw_lower for kw in cift_keywords):
        return "çift_terim"
    
    # Tek terim / tek zamanlı
    tek_keywords = ["tek", "single", "mono"]
    if any(kw in raw_lower for kw in tek_keywords):
        return "tek_terim"
    
    return "unknown"


def parse_tariff_string(tariff_string: str) -> Tuple[str, str, str]:
    """
    Tam tarife string'inden (örn: "SANAYİ OG ÇİFT TERİM") üç bileşeni çıkar.
    
    Returns:
        (tariff_group, voltage_level, term_type) - normalize edilmiş
    """
    if not tariff_string:
        return ("unknown", "unknown", "unknown")
    
    # Tüm string'i normalize fonksiyonlarına gönder
    # Her fonksiyon kendi keyword'lerini arar
    group = normalize_tariff_group(tariff_string)
    voltage = normalize_voltage_level(tariff_string)
    term = normalize_term_type(tariff_string)
    
    return (group, voltage, term)


# ═══════════════════════════════════════════════════════════════════════════════
# LOOKUP FONKSİYONLARI
# ═══════════════════════════════════════════════════════════════════════════════

def get_distribution_unit_price(
    tariff_group: str,
    voltage_level: str,
    term_type: str,
    period: Optional[str] = None,
) -> TariffLookupResult:
    """
    Tarife bilgilerine göre dağıtım birim fiyatını döndür.
    
    Dönem bazlı tarife seçimi:
      period < "2026-04"  → Şubat 2026 tarifeleri
      period >= "2026-04" → Nisan 2026 tarifeleri (~%32 zam)
    
    Args:
        tariff_group: Raw tarife grubu (normalize edilecek)
        voltage_level: Raw gerilim seviyesi (normalize edilecek)
        term_type: Raw terim tipi (normalize edilecek)
        period: Fatura dönemi "YYYY-MM" (None ise en güncel tarife)
    
    Returns:
        TariffLookupResult - success, unit_price, error_message içerir
    """
    # Normalize et
    group = normalize_tariff_group(tariff_group)
    voltage = normalize_voltage_level(voltage_level)
    term = normalize_term_type(term_type)
    
    tariff_key = f"{group}/{voltage}/{term}"
    
    # Eksik bilgi kontrolü
    missing = []
    if group == "unknown":
        missing.append("tarife_grubu")
    if voltage == "unknown":
        missing.append("gerilim")
    if term == "unknown":
        missing.append("terim_tipi")
    
    if missing:
        error_msg = f"Tarife bilgisi eksik: {', '.join(missing)} (raw: {tariff_group}/{voltage_level}/{term_type})"
        logger.warning(f"EPDK tarife lookup başarısız: {error_msg}")
        return TariffLookupResult(
            success=False,
            unit_price=None,
            tariff_key=tariff_key,
            normalized_group=group,
            normalized_voltage=voltage,
            normalized_term=term,
            error_message=error_msg
        )
    
    # Dönem bazlı tarife tablosunu seç
    tariffs = _get_tariffs_for_period(period)
    lookup = _build_lookup(tariffs)
    unit_price = lookup.get(tariff_key)
    
    if unit_price is None:
        error_msg = f"Tarife tablosunda bulunamadı: {tariff_key} (dönem: {period or 'güncel'})"
        logger.warning(f"EPDK tarife lookup başarısız: {error_msg}")
        return TariffLookupResult(
            success=False,
            unit_price=None,
            tariff_key=tariff_key,
            normalized_group=group,
            normalized_voltage=voltage,
            normalized_term=term,
            error_message=error_msg
        )
    
    period_label = f"Nisan 2026+" if period and period >= "2026-04" else "Şubat 2026"
    logger.info(f"EPDK tarife lookup başarılı: {tariff_key} → {unit_price:.6f} TL/kWh (tarife dönemi: {period_label})")
    return TariffLookupResult(
        success=True,
        unit_price=unit_price,
        tariff_key=tariff_key,
        normalized_group=group,
        normalized_voltage=voltage,
        normalized_term=term
    )


def get_distribution_from_tariff_string(tariff_string: str) -> TariffLookupResult:
    """
    Tam tarife string'inden (örn: "SANAYİ OG ÇİFT TERİM") dağıtım birim fiyatını döndür.
    
    Args:
        tariff_string: Faturadan okunan tam tarife string'i
    
    Returns:
        TariffLookupResult
    """
    group, voltage, term = parse_tariff_string(tariff_string)
    return get_distribution_unit_price(group, voltage, term)


def get_distribution_unit_price_from_extraction(extraction, period: Optional[str] = None) -> TariffLookupResult:
    """
    Extraction sonucundan tarife bilgilerini alıp dağıtım birim fiyatını döndür.
    
    Öncelik sırası:
    1. extraction.tariff (yapılandırılmış tarife bilgisi)
    2. extraction.meta (tahmin edilen tarife bilgisi)
    
    Args:
        extraction: InvoiceExtraction objesi
        period: Fatura dönemi "YYYY-MM" (None ise en güncel tarife)
    
    Returns:
        TariffLookupResult
    """
    tariff_group = "unknown"
    voltage_level = "unknown"
    term_type = "unknown"
    
    # 1. Önce tariff objesinden al
    if extraction.tariff:
        tariff_group = extraction.tariff.tariff_type or "unknown"
        voltage_level = extraction.tariff.voltage_level or "unknown"
        term_type = extraction.tariff.time_of_use or "unknown"
    
    # 2. Meta'dan eksikleri tamamla
    if extraction.meta:
        if tariff_group == "unknown":
            tariff_group = extraction.meta.tariff_group_guess or "unknown"
        if voltage_level == "unknown":
            voltage_level = extraction.meta.voltage_guess or "unknown"
        if term_type == "unknown":
            term_type = extraction.meta.term_type_guess or "unknown"
    
    # 3. Extraction'dan dönem bilgisi (fallback)
    if period is None and hasattr(extraction, 'invoice_period') and extraction.invoice_period:
        period = extraction.invoice_period
    
    logger.debug(f"Extraction'dan tarife bilgisi: group={tariff_group}, voltage={voltage_level}, term={term_type}, period={period}")
    
    return get_distribution_unit_price(tariff_group, voltage_level, term_type, period=period)


def calculate_distribution_amount(
    total_kwh: float,
    tariff_group: str,
    voltage_level: str,
    term_type: str,
    period: Optional[str] = None,
) -> Tuple[Optional[float], TariffLookupResult]:
    """
    Toplam kWh ve tarife bilgilerinden dağıtım bedelini hesapla.
    
    Dönem bazlı tarife seçimi otomatik yapılır.
    
    Args:
        total_kwh: Toplam tüketim (kWh)
        tariff_group: Tarife grubu
        voltage_level: Gerilim seviyesi
        term_type: Terim tipi
        period: Fatura dönemi "YYYY-MM" (None ise en güncel tarife)
    
    Returns:
        (distribution_amount_tl, lookup_result)
    """
    lookup = get_distribution_unit_price(tariff_group, voltage_level, term_type, period=period)
    
    if not lookup.success or lookup.unit_price is None:
        return (None, lookup)
    
    distribution_amount = total_kwh * lookup.unit_price
    logger.info(f"Dağıtım bedeli hesaplandı: {total_kwh:.2f} kWh × {lookup.unit_price:.6f} TL/kWh = {distribution_amount:.2f} TL")
    
    return (distribution_amount, lookup)


# ═══════════════════════════════════════════════════════════════════════════════
# YARDIMCI FONKSİYONLAR
# ═══════════════════════════════════════════════════════════════════════════════

def get_all_tariffs(period: Optional[str] = None) -> list[dict]:
    """
    Tüm tarifeleri liste olarak döndür (UI için).
    
    Args:
        period: Fatura dönemi "YYYY-MM". None ise en güncel tarife.
    """
    tariffs = _get_tariffs_for_period(period)
    
    group_labels = {
        "sanayi": "Sanayi",
        "ticarethane": "Ticarethane",
        "mesken": "Mesken",
        "mesken_sehit_gazi": "Mesken Şehit Gazi",
        "tarimsal": "Tarımsal",
        "aydinlatma": "Aydınlatma",
        "kamu_ozel": "Kamu ve Özel Sektör",
        "osb_cerkezkoy": "Çerkezköy OSB",
        "osb_ikitelli": "İkitelli OSB",
    }
    
    return [
        {
            "key": f"{t.tariff_group}_{t.voltage_level}_{t.term_type}".lower().replace("ç", "c"),
            "tariff_group": t.tariff_group,
            "tariff_group_label": group_labels.get(t.tariff_group, t.tariff_group.title()),
            "voltage_level": t.voltage_level,
            "term_type": t.term_type,
            "term_type_label": "Tek Terim" if t.term_type == "tek_terim" else "Çift Terim",
            "unit_price_tl_per_kwh": t.unit_price_tl_per_kwh,
            "label": f"{group_labels.get(t.tariff_group, t.tariff_group.title())} {t.voltage_level} {'Tek' if t.term_type == 'tek_terim' else 'Çift'} Terim"
        }
        for t in tariffs
    ]


def validate_distribution_against_table(
    extracted_unit_price: float,
    tariff_group: str,
    voltage_level: str,
    term_type: str,
    tolerance_percent: float = 5.0,
    period: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    """
    Faturadan okunan dağıtım birim fiyatını EPDK tablosuyla karşılaştır.
    
    Args:
        extracted_unit_price: Faturadan okunan birim fiyat
        tariff_group, voltage_level, term_type: Tarife bilgileri
        tolerance_percent: Kabul edilebilir fark yüzdesi
        period: Fatura dönemi "YYYY-MM" (None ise en güncel tarife)
    
    Returns:
        (is_valid, warning_message)
    """
    lookup = get_distribution_unit_price(tariff_group, voltage_level, term_type, period=period)
    
    if not lookup.success or lookup.unit_price is None:
        return (True, None)  # Karşılaştırma yapılamıyor, geç
    
    expected = lookup.unit_price
    diff_percent = abs(extracted_unit_price - expected) / expected * 100
    
    if diff_percent > tolerance_percent:
        warning = (
            f"Dağıtım birim fiyatı uyuşmazlığı: "
            f"Faturadan={extracted_unit_price:.6f}, EPDK={expected:.6f} TL/kWh "
            f"(fark: %{diff_percent:.1f}, tarife: {lookup.tariff_key})"
        )
        logger.warning(warning)
        return (False, warning)
    
    return (True, None)
