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
# EPDK DAĞITIM TARİFELERİ (Şubat 2026)
# ═══════════════════════════════════════════════════════════════════════════════
# Bu tablo EPDK tarafından belirlenir ve periyodik olarak güncellenir.
# Kaynak: EPDK Elektrik Piyasası Tarifeler Yönetmeliği
# Son güncelleme: 2026-02

DISTRIBUTION_TARIFFS = [
    # ═══════════════════════════════════════════════════════════════════════════
    # İSK - İletim Sistemi Kullanıcısı (dağıtım bedeli yok)
    # ═══════════════════════════════════════════════════════════════════════════
    DistributionTariff("isk_sanayi", "iletim", "iletim", 0.00000),   # İSK Sanayi (İletim)
    
    # ═══════════════════════════════════════════════════════════════════════════
    # OG (Orta Gerilim) - Çift Terim (ÇT)
    # ═══════════════════════════════════════════════════════════════════════════
    DistributionTariff("sanayi", "OG", "çift_terim", 0.81060),       # DSK Sanayi ÇT OG
    DistributionTariff("ticarethane", "OG", "çift_terim", 1.26329),  # DSK Ticarethane ÇT OG
    DistributionTariff("mesken", "OG", "çift_terim", 1.25129),       # DSK Mesken ÇT OG
    DistributionTariff("aydinlatma", "OG", "çift_terim", 1.21249),   # DSK Aydınlatma ÇT OG
    DistributionTariff("tarimsal", "OG", "çift_terim", 1.04042),     # DSK Tarımsal ÇT OG
    
    # ═══════════════════════════════════════════════════════════════════════════
    # OG (Orta Gerilim) - Tek Terim (TT)
    # ═══════════════════════════════════════════════════════════════════════════
    DistributionTariff("sanayi", "OG", "tek_terim", 0.89537),        # DSK Sanayi TT OG
    DistributionTariff("ticarethane", "OG", "tek_terim", 1.57581),   # DSK Ticarethane TT OG
    DistributionTariff("mesken", "OG", "tek_terim", 1.54502),        # DSK Mesken TT OG
    DistributionTariff("aydinlatma", "OG", "tek_terim", 1.51248),    # DSK Aydınlatma TT OG
    DistributionTariff("tarimsal", "OG", "tek_terim", 1.29543),      # DSK Tarımsal TT OG
    
    # ═══════════════════════════════════════════════════════════════════════════
    # AG (Alçak Gerilim) - Tek Terim (TT)
    # ═══════════════════════════════════════════════════════════════════════════
    DistributionTariff("sanayi", "AG", "tek_terim", 1.38532),        # DSK Sanayi TT AG
    DistributionTariff("ticarethane", "AG", "tek_terim", 1.87741),   # DSK Ticarethane TT AG
    DistributionTariff("mesken", "AG", "tek_terim", 1.83617),        # DSK Mesken TT AG
    DistributionTariff("mesken_sehit_gazi", "AG", "tek_terim", 1.03557),  # DSK Mesken Şehit Gazi
    DistributionTariff("tarimsal", "AG", "tek_terim", 1.54263),      # DSK Tarımsal TT AG
    DistributionTariff("aydinlatma", "AG", "tek_terim", 1.79815),    # DSK Aydınlatma TT AG
    
    # ═══════════════════════════════════════════════════════════════════════════
    # AG (Alçak Gerilim) - Çift Terim (ÇT) - Görüntüde yok, tahmini değerler
    # ═══════════════════════════════════════════════════════════════════════════
    DistributionTariff("sanayi", "AG", "çift_terim", 1.20),          # Tahmini
    DistributionTariff("ticarethane", "AG", "çift_terim", 1.65),     # Tahmini
    DistributionTariff("mesken", "AG", "çift_terim", 1.60),          # Tahmini
    DistributionTariff("tarimsal", "AG", "çift_terim", 1.35),        # Tahmini
    DistributionTariff("aydinlatma", "AG", "çift_terim", 1.55),      # Tahmini
    
    # ═══════════════════════════════════════════════════════════════════════════
    # Geriye uyumluluk için kamu_ozel alias'ları (ticarethane ile eşleşir)
    # ═══════════════════════════════════════════════════════════════════════════
    DistributionTariff("kamu_ozel", "OG", "çift_terim", 1.26329),    # = ticarethane OG ÇT
    DistributionTariff("kamu_ozel", "OG", "tek_terim", 1.57581),     # = ticarethane OG TT
    DistributionTariff("kamu_ozel", "AG", "tek_terim", 1.87741),     # = ticarethane AG TT
    DistributionTariff("kamu_ozel", "AG", "çift_terim", 1.65),       # = ticarethane AG ÇT (tahmini)
]

# Hızlı lookup için dict
_TARIFF_LOOKUP: dict[str, float] = {
    f"{t.tariff_group}/{t.voltage_level}/{t.term_type}": t.unit_price_tl_per_kwh
    for t in DISTRIBUTION_TARIFFS
}


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
    term_type: str
) -> TariffLookupResult:
    """
    Tarife bilgilerine göre dağıtım birim fiyatını döndür.
    
    Args:
        tariff_group: Raw tarife grubu (normalize edilecek)
        voltage_level: Raw gerilim seviyesi (normalize edilecek)
        term_type: Raw terim tipi (normalize edilecek)
    
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
    
    # Tabloda ara
    unit_price = _TARIFF_LOOKUP.get(tariff_key)
    
    if unit_price is None:
        error_msg = f"Tarife tablosunda bulunamadı: {tariff_key}"
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
    
    logger.info(f"EPDK tarife lookup başarılı: {tariff_key} → {unit_price:.6f} TL/kWh")
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


def get_distribution_unit_price_from_extraction(extraction) -> TariffLookupResult:
    """
    Extraction sonucundan tarife bilgilerini alıp dağıtım birim fiyatını döndür.
    
    Öncelik sırası:
    1. extraction.tariff (yapılandırılmış tarife bilgisi)
    2. extraction.meta (tahmin edilen tarife bilgisi)
    
    Args:
        extraction: InvoiceExtraction objesi
    
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
    
    logger.debug(f"Extraction'dan tarife bilgisi: group={tariff_group}, voltage={voltage_level}, term={term_type}")
    
    return get_distribution_unit_price(tariff_group, voltage_level, term_type)


def calculate_distribution_amount(
    total_kwh: float,
    tariff_group: str,
    voltage_level: str,
    term_type: str
) -> Tuple[Optional[float], TariffLookupResult]:
    """
    Toplam kWh ve tarife bilgilerinden dağıtım bedelini hesapla.
    
    Args:
        total_kwh: Toplam tüketim (kWh)
        tariff_group: Tarife grubu
        voltage_level: Gerilim seviyesi
        term_type: Terim tipi
    
    Returns:
        (distribution_amount_tl, lookup_result)
    """
    lookup = get_distribution_unit_price(tariff_group, voltage_level, term_type)
    
    if not lookup.success or lookup.unit_price is None:
        return (None, lookup)
    
    distribution_amount = total_kwh * lookup.unit_price
    logger.info(f"Dağıtım bedeli hesaplandı: {total_kwh:.2f} kWh × {lookup.unit_price:.6f} TL/kWh = {distribution_amount:.2f} TL")
    
    return (distribution_amount, lookup)


# ═══════════════════════════════════════════════════════════════════════════════
# YARDIMCI FONKSİYONLAR
# ═══════════════════════════════════════════════════════════════════════════════

def get_all_tariffs() -> list[dict]:
    """
    Tüm tarifeleri liste olarak döndür (UI için).
    """
    group_labels = {
        "sanayi": "Sanayi",
        "ticarethane": "Ticarethane",
        "mesken": "Mesken",
        "mesken_sehit_gazi": "Mesken Şehit Gazi",
        "tarimsal": "Tarımsal",
        "aydinlatma": "Aydınlatma",
        "kamu_ozel": "Kamu ve Özel Sektör",
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
        for t in DISTRIBUTION_TARIFFS
    ]


def validate_distribution_against_table(
    extracted_unit_price: float,
    tariff_group: str,
    voltage_level: str,
    term_type: str,
    tolerance_percent: float = 5.0
) -> Tuple[bool, Optional[str]]:
    """
    Faturadan okunan dağıtım birim fiyatını EPDK tablosuyla karşılaştır.
    
    Args:
        extracted_unit_price: Faturadan okunan birim fiyat
        tariff_group, voltage_level, term_type: Tarife bilgileri
        tolerance_percent: Kabul edilebilir fark yüzdesi
    
    Returns:
        (is_valid, warning_message)
    """
    lookup = get_distribution_unit_price(tariff_group, voltage_level, term_type)
    
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
