"""
Tedarikçi Profilleri - Her tedarikçi için extraction anchor'ları ve regex'leri.

Bu modül LLM'e bağımlı olmadan çalışabilecek endüstriyel parser altyapısı sağlar.
Her tedarikçi için:
- Fatura detay bloğu anchor'ları
- Kalem regex'leri
- Toplam/KDV regex'leri
- Doğrulama kuralları
- Region koordinatları (PDF görsel bölgeleme için)

Desteklenen tedarikçiler:
- CK Boğaziçi (BBE, BEDAŞ)
- Enerjisa (ES, Başkent, Toroslar, AYEDAŞ)
- Uludağ (PBA, UEDAŞ)
- Osmangazi (EAL, OEDAŞ)
- Kolen (KSE)
- Ekvator
- Yelden
"""

import re
from dataclasses import dataclass, field
from typing import Optional, Callable
from enum import Enum


# ═══════════════════════════════════════════════════════════════════════════════
# TR Sayı Parser - Tek Standart Fonksiyon
# ═══════════════════════════════════════════════════════════════════════════════

def tr_money(s: str) -> Optional[float]:
    """
    Türkçe para formatını parse et.
    
    Örnekler:
    - "1.590,66" -> 1590.66
    - "22.202,71" -> 22202.71
    - "593.000,00" -> 593000.00
    - "4.192,947" -> 4192.947
    - "1 590,66" -> 1590.66
    """
    if not s:
        return None
    
    # String'e çevir
    s = str(s).strip().replace(" ", "").replace("\xa0", "")
    
    if not s:
        return None
    
    # Negatif işareti kontrol
    negative = False
    if s.startswith("-"):
        negative = True
        s = s[1:]
    elif s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1]
    
    # TR formatı: 1.234.567,89 -> 1234567.89
    # Binlik ayracı nokta, ondalık ayracı virgül
    if "," in s:
        # Virgülden sonrası ondalık
        s = s.replace(".", "").replace(",", ".")
    else:
        # Sadece nokta var - bu binlik mi ondalık mı?
        # Eğer noktadan sonra 3 hane varsa binlik, değilse ondalık
        parts = s.split(".")
        if len(parts) == 2 and len(parts[1]) == 3:
            # Binlik ayracı (örn: 1.234 -> 1234)
            s = s.replace(".", "")
        # Aksi halde nokta ondalık ayracı olarak kalır
    
    try:
        result = float(s)
        return -result if negative else result
    except ValueError:
        return None


def tr_kwh(s: str) -> Optional[float]:
    """
    kWh değerini parse et (tr_money ile aynı mantık).
    """
    return tr_money(s)


# ═══════════════════════════════════════════════════════════════════════════════
# Kanonik Veri Modeli
# ═══════════════════════════════════════════════════════════════════════════════

class LineCode(str, Enum):
    """Standart kalem kodları"""
    ACTIVE_ENERGY = "active_energy"
    ACTIVE_ENERGY_HIGH = "active_energy_high"  # Yüksek kademe
    ACTIVE_ENERGY_LOW = "active_energy_low"    # Düşük kademe
    ACTIVE_ENERGY_T1 = "active_energy_t1"      # Gündüz
    ACTIVE_ENERGY_T2 = "active_energy_t2"      # Puant
    ACTIVE_ENERGY_T3 = "active_energy_t3"      # Gece
    DISTRIBUTION = "distribution"
    YEK = "yek"
    YEK_DIFF = "yek_diff"
    REACTIVE = "reactive"
    REACTIVE_INDUCTIVE = "reactive_inductive"
    REACTIVE_CAPACITIVE = "reactive_capacitive"
    DEMAND = "demand"
    TAX_BTV = "tax_btv"
    TAX_TRT = "tax_trt"
    TAX_ENERGY_FUND = "tax_energy_fund"
    SERVICE_FEE = "service_fee"
    OTHER = "other"


@dataclass
class InvoiceLine:
    """Tek bir fatura kalemi"""
    code: LineCode
    label: str = ""           # Orijinal etiket
    qty_kwh: Optional[float] = None
    unit_price: Optional[float] = None
    amount: Optional[float] = None
    evidence: str = ""        # Kaynak satır
    
    def is_valid(self) -> bool:
        """Kalem geçerli mi?"""
        return self.amount is not None and self.amount != 0
    
    def crosscheck(self, tolerance: float = 0.02) -> bool:
        """qty × unit_price ≈ amount kontrolü"""
        if self.qty_kwh is None or self.unit_price is None or self.amount is None:
            return True  # Kontrol yapılamaz
        if self.amount == 0:
            return True
        calculated = self.qty_kwh * self.unit_price
        delta = abs((calculated - self.amount) / self.amount)
        return delta <= tolerance


@dataclass
class TaxBreakdown:
    """Vergi/fon dökümü"""
    btv: Optional[float] = None           # Belediye Tüketim Vergisi
    trt: Optional[float] = None           # TRT Payı
    energy_fund: Optional[float] = None   # Enerji Fonu
    other: Optional[float] = None         # Diğer vergiler
    
    @property
    def total(self) -> float:
        return sum(v or 0 for v in [self.btv, self.trt, self.energy_fund, self.other])


@dataclass
class VATInfo:
    """KDV bilgisi"""
    rate: float = 0.20
    base: Optional[float] = None    # Matrah
    amount: Optional[float] = None  # KDV tutarı


@dataclass
class Totals:
    """Toplam tutarlar"""
    subtotal: Optional[float] = None   # Ara toplam (KDV hariç)
    total: Optional[float] = None      # Genel toplam
    payable: Optional[float] = None    # Ödenecek tutar


@dataclass
class CanonicalInvoice:
    """
    Kanonik fatura modeli - tüm tedarikçiler için standart çıktı.
    """
    supplier: str = "unknown"
    period: str = ""
    invoice_no: str = ""
    ettn: str = ""
    
    lines: list[InvoiceLine] = field(default_factory=list)
    taxes: TaxBreakdown = field(default_factory=TaxBreakdown)
    vat: VATInfo = field(default_factory=VATInfo)
    totals: Totals = field(default_factory=Totals)
    
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    
    # Debug bilgisi
    source_anchor: str = ""  # Hangi bloktan okundu
    raw_text_snippet: str = ""  # İlgili metin parçası
    
    # ═══════════════════════════════════════════════════════════════════════
    # Hesaplanmış değerler
    # ═══════════════════════════════════════════════════════════════════════
    
    @property
    def total_kwh(self) -> float:
        """Toplam tüketim (kWh)"""
        energy_codes = [
            LineCode.ACTIVE_ENERGY,
            LineCode.ACTIVE_ENERGY_HIGH,
            LineCode.ACTIVE_ENERGY_LOW,
            LineCode.ACTIVE_ENERGY_T1,
            LineCode.ACTIVE_ENERGY_T2,
            LineCode.ACTIVE_ENERGY_T3,
        ]
        return sum(
            line.qty_kwh or 0 
            for line in self.lines 
            if line.code in energy_codes and line.qty_kwh
        )
    
    @property
    def energy_amount(self) -> float:
        """Toplam enerji bedeli (TL)"""
        energy_codes = [
            LineCode.ACTIVE_ENERGY,
            LineCode.ACTIVE_ENERGY_HIGH,
            LineCode.ACTIVE_ENERGY_LOW,
            LineCode.ACTIVE_ENERGY_T1,
            LineCode.ACTIVE_ENERGY_T2,
            LineCode.ACTIVE_ENERGY_T3,
        ]
        return sum(
            line.amount or 0 
            for line in self.lines 
            if line.code in energy_codes
        )
    
    @property
    def distribution_amount(self) -> float:
        """Dağıtım bedeli (TL)"""
        return sum(
            line.amount or 0 
            for line in self.lines 
            if line.code == LineCode.DISTRIBUTION
        )
    
    @property
    def weighted_unit_price(self) -> Optional[float]:
        """Ağırlıklı ortalama birim fiyat (TL/kWh)"""
        if self.total_kwh <= 0:
            return None
        return self.energy_amount / self.total_kwh
    
    @property
    def distribution_unit_price(self) -> Optional[float]:
        """Dağıtım birim fiyatı (TL/kWh)"""
        if self.total_kwh <= 0:
            return None
        dist_line = next((l for l in self.lines if l.code == LineCode.DISTRIBUTION), None)
        if dist_line and dist_line.unit_price:
            return dist_line.unit_price
        if self.distribution_amount > 0:
            return self.distribution_amount / self.total_kwh
        return None
    
    # ═══════════════════════════════════════════════════════════════════════
    # Doğrulama
    # ═══════════════════════════════════════════════════════════════════════
    
    def validate(self) -> list[str]:
        """
        Tutarlılık doğrulaması yap.
        Hataları errors listesine ekle ve döndür.
        """
        errors = []
        
        # Kural 1: Payable ≈ Total
        if self.totals.payable and self.totals.total:
            if not approx(self.totals.payable, self.totals.total, tol=5.0):
                errors.append(
                    f"PAYABLE_TOTAL_MISMATCH: payable={self.totals.payable:.2f}, "
                    f"total={self.totals.total:.2f}"
                )
        
        # Kural 2: Kalemler + KDV ≈ Toplam
        if self.totals.total:
            lines_sum = sum(l.amount or 0 for l in self.lines)
            taxes_sum = self.taxes.total
            vat_amount = self.vat.amount or 0
            calculated = lines_sum + taxes_sum + vat_amount
            
            # %1 tolerans veya 5 TL
            tol = max(5.0, self.totals.total * 0.01)
            if not approx(calculated, self.totals.total, tol=tol):
                errors.append(
                    f"TOTAL_MISMATCH: calculated={calculated:.2f}, "
                    f"extracted={self.totals.total:.2f}, "
                    f"diff={abs(calculated - self.totals.total):.2f}"
                )
        
        # Kural 3: Tüketim > 0
        if self.total_kwh <= 0:
            errors.append("ZERO_CONSUMPTION: total_kwh <= 0")
        
        # Kural 4: Her kalemde crosscheck
        for line in self.lines:
            if not line.crosscheck():
                errors.append(
                    f"LINE_CROSSCHECK_FAIL: {line.label} - "
                    f"qty={line.qty_kwh}, price={line.unit_price}, amount={line.amount}"
                )
        
        self.errors.extend(errors)
        return errors
    
    def is_valid(self) -> bool:
        """Fatura geçerli mi?"""
        if not self.errors:
            self.validate()
        return len(self.errors) == 0
    
    def to_debug_dict(self) -> dict:
        """Debug için özet dict"""
        return {
            "supplier": self.supplier,
            "period": self.period,
            "picked_total_kwh": self.total_kwh,
            "picked_energy_amount": self.energy_amount,
            "picked_payable": self.totals.payable,
            "picked_total": self.totals.total,
            "weighted_unit_price": self.weighted_unit_price,
            "source_anchor": self.source_anchor,
            "line_count": len(self.lines),
            "warnings": self.warnings,
            "errors": self.errors,
        }


def approx(a: Optional[float], b: Optional[float], tol: float = 5.0) -> bool:
    """İki değer yaklaşık eşit mi?"""
    if a is None or b is None:
        return True
    return abs(a - b) <= tol


# ═══════════════════════════════════════════════════════════════════════════════
# Tedarikçi Profili Base Class
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class SupplierProfile:
    """Tedarikçi profili - extraction kuralları"""
    code: str                    # "ck_bogazici", "enerjisa", etc.
    name: str                    # "CK Boğaziçi Elektrik"
    invoice_prefixes: list[str]  # ["BBE", "BEDAŞ"]
    
    # Blok anchor'ları
    detail_block_start: list[str] = field(default_factory=list)  # ["Fatura Detayı", "FATURA DETAYI"]
    detail_block_end: list[str] = field(default_factory=list)    # ["Vergi ve Fonlar", "KDV"]
    
    # Regex pattern'ları
    line_patterns: list[re.Pattern] = field(default_factory=list)
    total_pattern: Optional[re.Pattern] = None
    payable_pattern: Optional[re.Pattern] = None
    vat_pattern: Optional[re.Pattern] = None
    consumption_pattern: Optional[re.Pattern] = None
    
    # Özel parse fonksiyonu (varsa)
    custom_parser: Optional[Callable[[str], CanonicalInvoice]] = None


# ═══════════════════════════════════════════════════════════════════════════════
# Tedarikçi Profilleri
# ═══════════════════════════════════════════════════════════════════════════════

# CK Boğaziçi (BBE, BEDAŞ)
CK_BOGAZICI_PROFILE = SupplierProfile(
    code="ck_bogazici",
    name="CK Boğaziçi Elektrik",
    invoice_prefixes=["BBE", "STCC"],
    detail_block_start=["Fatura Detayı", "FATURA DETAYI", "Fatura Bilgileri"],
    detail_block_end=["Vergi ve Fonlar", "KDV", "TOPLAM"],
    line_patterns=[
        # Enerji Bedeli SKTT satırı (ana tüketim)
        re.compile(
            r"(?P<label>Enerji\s*Bedeli\s*(?:SKTT|Tüketim)?)\s+"
            r"(?P<qty>[\d\.\,]+)\s*(?:kWh|KWH)\s+"
            r"(?P<unit_price>[\d\.\,]+)\s+"
            r"(?P<amount>[\d\.\,]+)",
            re.IGNORECASE
        ),
        # Enerji Bedeli (Ek Tüketim) - NEGATİF OLABİLİR (mahsuplaşma)
        re.compile(
            r"(?P<label>Enerji\s*Bedeli\s*\(?Ek\s*Tüketim\)?)\s+"
            r"(?P<qty>-?[\d\.\,]+)\s*(?:kWh|KWH)\s+"
            r"(?P<unit_price>[\d\.\,]+)\s+"
            r"(?P<amount>-?[\d\.\,]+)",
            re.IGNORECASE
        ),
        # Yüksek/Düşük Kademe Enerji Bedeli
        re.compile(
            r"(?P<label>(?:Yüksek|Düşük|Tek)\s*(?:Kademe)?\s*Enerji\s*Bedeli)\s+"
            r"(?P<qty>[\d\.\,]+)\s*(?:kWh|KWH)\s+"
            r"(?P<unit_price>[\d\.\,]+)\s+"
            r"(?P<amount>[\d\.\,]+)",
            re.IGNORECASE
        ),
        # Dağıtım Bedeli
        re.compile(
            r"(?P<label>Dağıtım\s*Bedeli|Elk\.\s*Dağıtım)\s+"
            r"(?P<qty>[\d\.\,]+)\s*(?:kWh|KWH)\s+"
            r"(?P<unit_price>[\d\.\,]+)\s+"
            r"(?P<amount>[\d\.\,]+)",
            re.IGNORECASE
        ),
    ],
    total_pattern=re.compile(r"(?:Fatura\s*Tutarı|FATURA\s*TUTARI)\s*[:\s]*(?P<v>[\d\.\,]+)", re.IGNORECASE),
    payable_pattern=re.compile(r"(?:ÖDENECEK\s*TUTAR|Ödenecek\s*Tutar)\s*[:\s]*(?P<v>[\d\.\,]+)", re.IGNORECASE),
    vat_pattern=re.compile(r"KDV\s*(?:%\s*)?(?:20|18)?\s*(?:\(Matrah[^)]*\))?\s*[:\s]*(?P<v>[\d\.\,]+)", re.IGNORECASE),
    # Tüketim için SKTT satırını kullan
    consumption_pattern=re.compile(r"Enerji\s*Bedeli\s*SKTT\s+(?P<v>[\d\.\,]+)\s*(?:kWh|KWH)", re.IGNORECASE),
)

# Enerjisa (ES, Başkent, Toroslar, AYEDAŞ)
ENERJISA_PROFILE = SupplierProfile(
    code="enerjisa",
    name="Enerjisa",
    invoice_prefixes=["ES0", "ES1", "ES2", "ENS"],
    detail_block_start=["Enerji Bedelleri", "FATURA DETAYI", "Tüketim Bilgileri"],
    detail_block_end=["Vergi/Fonlar", "KDV", "TOPLAM"],
    line_patterns=[
        # Aktif Enerji: "AKTİF TOPLAM 289.415 kWh 3,2456 939.456,78"
        re.compile(
            r"(?P<label>AKTİF\s*TOPLAM|Aktif\s*Enerji|Enerji\s*Bedeli)\s+"
            r"(?P<qty>[\d\.\,]+)\s*(?:kWh|KWH)\s+"
            r"(?P<unit_price>[\d\.\,]+)\s+"
            r"(?P<amount>[\d\.\,]+)",
            re.IGNORECASE
        ),
        # Dağıtım
        re.compile(
            r"(?P<label>Elk\.\s*Dağıtım|Dağıtım\s*Bedeli)\s+"
            r"(?P<qty>[\d\.\,]+)\s*(?:kWh|KWH)\s+"
            r"(?P<unit_price>[\d\.\,]+)\s+"
            r"(?P<amount>[\d\.\,]+)",
            re.IGNORECASE
        ),
    ],
    total_pattern=re.compile(r"(?:FATURA\s*TUTARI|TOPLAM)\s*[:\s]*(?P<v>[\d\.\,]+)", re.IGNORECASE),
    payable_pattern=re.compile(r"ÖDENECEK\s*TUTAR\s*[:\s]*(?P<v>[\d\.\,]+)", re.IGNORECASE),
    vat_pattern=re.compile(r"KDV\s*%\s*20\s*(?:\(Matrah[^)]*\))?\s*[:\s]*(?P<v>[\d\.\,]+)", re.IGNORECASE),
    consumption_pattern=re.compile(r"ENERJİ\s*TÜKETİM\s*TOPLAM\s*[:\s]*(?P<v>[\d\.\,]+)", re.IGNORECASE),
)

# Uludağ (PBA, UEDAŞ)
ULUDAG_PROFILE = SupplierProfile(
    code="uludag",
    name="Uludağ Elektrik",
    invoice_prefixes=["PBA", "UED"],
    detail_block_start=["FATURA DETAYI", "Fatura Detayı"],
    detail_block_end=["KDV", "TOPLAM"],
    line_patterns=[
        re.compile(
            r"(?P<label>Enerji\s*tüketim\s*bedeli|Aktif\s*Enerji)\s+"
            r"(?P<qty>[\d\.\,]+)\s*(?:kWh|KWH)?\s*"
            r"(?P<unit_price>[\d\.\,]+)?\s*"
            r"(?P<amount>[\d\.\,]+)",
            re.IGNORECASE
        ),
    ],
    total_pattern=re.compile(r"Fatura\s*Tutarı\s*[:\s]*(?P<v>[\d\.\,]+)", re.IGNORECASE),
    payable_pattern=re.compile(r"ÖDENECEK\s*TUTAR\s*[:\s]*(?P<v>[\d\.\,]+)", re.IGNORECASE),
)

# Osmangazi (EAL, OEDAŞ)
OSMANGAZI_PROFILE = SupplierProfile(
    code="osmangazi",
    name="Osmangazi Elektrik",
    invoice_prefixes=["EAL", "OED"],
    detail_block_start=["FATURA BİLGİLERİ", "Fatura Detayı"],
    detail_block_end=["KDV", "TOPLAM"],
    line_patterns=[
        re.compile(
            r"(?P<label>Aktif\s*Enerji|Enerji\s*Bedeli)\s+"
            r"(?P<qty>[\d\.\,]+)\s*(?:kWh|KWH)\s+"
            r"(?P<unit_price>[\d\.\,]+)\s+"
            r"(?P<amount>[\d\.\,]+)",
            re.IGNORECASE
        ),
    ],
    total_pattern=re.compile(r"(?:Toplam\s*Tutar|TOPLAM)\s*[:\s]*(?P<v>[\d\.\,]+)", re.IGNORECASE),
    payable_pattern=re.compile(r"ÖDENECEK\s*TUTAR\s*[:\s]*(?P<v>[\d\.\,]+)", re.IGNORECASE),
)

# Kolen (KSE)
KOLEN_PROFILE = SupplierProfile(
    code="kolen",
    name="Kolen Enerji",
    invoice_prefixes=["KSE", "KOL"],
    detail_block_start=["AÇIKLAMA", "Fatura Detayı"],
    detail_block_end=["TOPLAM", "KDV"],
    line_patterns=[
        re.compile(
            r"(?P<label>Aktif\s*enerji\s*bedeli|Enerji\s*Bedeli)\s+"
            r"(?P<qty>[\d\.\,]+)\s*(?:kWh|KWH)\s+"
            r"(?P<unit_price>[\d\.\,]+)\s+"
            r"(?P<amount>[\d\.\,]+)",
            re.IGNORECASE
        ),
    ],
    total_pattern=re.compile(r"TOPLAM\s*ÖDENECEK\s*TUTAR\s*[:\s]*(?P<v>[\d\.\,]+)", re.IGNORECASE),
    payable_pattern=re.compile(r"TOPLAM\s*ÖDENECEK\s*TUTAR\s*[:\s]*(?P<v>[\d\.\,]+)", re.IGNORECASE),
)

# Ekvator
EKVATOR_PROFILE = SupplierProfile(
    code="ekvator",
    name="Ekvator Enerji",
    invoice_prefixes=["EKV"],
    detail_block_start=["Fatura Detayı", "FATURA DETAYI"],
    detail_block_end=["KDV", "TOPLAM"],
    line_patterns=[
        re.compile(
            r"(?P<label>Enerji\s*Bedeli|Aktif\s*Enerji)\s+"
            r"(?P<qty>[\d\.\,]+)\s*(?:kWh|KWH)\s+"
            r"(?P<unit_price>[\d\.\,]+)\s+"
            r"(?P<amount>[\d\.\,]+)",
            re.IGNORECASE
        ),
    ],
    total_pattern=re.compile(r"(?:TOPLAM|Fatura\s*Tutarı)\s*[:\s]*(?P<v>[\d\.\,]+)", re.IGNORECASE),
    payable_pattern=re.compile(r"ÖDENECEK\s*TUTAR\s*[:\s]*(?P<v>[\d\.\,]+)", re.IGNORECASE),
)

# Yelden
YELDEN_PROFILE = SupplierProfile(
    code="yelden",
    name="Yelden Enerji",
    invoice_prefixes=["YLD", "YEL"],
    detail_block_start=["Fatura Detayı", "FATURA DETAYI"],
    detail_block_end=["KDV", "TOPLAM"],
    line_patterns=[
        re.compile(
            r"(?P<label>(?:Yüksek|Düşük)\s*Kademe\s*Enerji|Enerji\s*Bedeli)\s+"
            r"(?P<qty>[\d\.\,]+)\s*(?:kWh|KWH)\s+"
            r"(?P<unit_price>[\d\.\,]+)\s+"
            r"(?P<amount>[\d\.\,]+)",
            re.IGNORECASE
        ),
    ],
    total_pattern=re.compile(r"(?:TOPLAM|Fatura\s*Tutarı)\s*[:\s]*(?P<v>[\d\.\,]+)", re.IGNORECASE),
    payable_pattern=re.compile(r"ÖDENECEK\s*TUTAR\s*[:\s]*(?P<v>[\d\.\,]+)", re.IGNORECASE),
)

# Tüm profiller
ALL_PROFILES = [
    CK_BOGAZICI_PROFILE,
    ENERJISA_PROFILE,
    ULUDAG_PROFILE,
    OSMANGAZI_PROFILE,
    KOLEN_PROFILE,
    EKVATOR_PROFILE,
    YELDEN_PROFILE,
]


def get_profile_by_code(code: str) -> Optional[SupplierProfile]:
    """Kod ile profil getir"""
    for profile in ALL_PROFILES:
        if profile.code == code:
            return profile
    return None


def detect_supplier(text: str, invoice_no: str = "") -> Optional[SupplierProfile]:
    """
    Metin veya fatura numarasından tedarikçiyi tespit et.
    """
    text_lower = text.lower()
    
    # Fatura numarası prefix'i ile kontrol
    if invoice_no:
        for profile in ALL_PROFILES:
            for prefix in profile.invoice_prefixes:
                if invoice_no.upper().startswith(prefix):
                    return profile
    
    # Metin içinde tedarikçi adı ara
    supplier_keywords = {
        "ck_bogazici": ["ck boğaziçi", "bedaş", "boğaziçi elektrik"],
        "enerjisa": ["enerjisa", "başkent elektrik", "toroslar", "ayedaş"],
        "uludag": ["uludağ elektrik", "uedaş"],
        "osmangazi": ["osmangazi", "oedaş"],
        "kolen": ["kolen"],
        "ekvator": ["ekvator"],
        "yelden": ["yelden"],
    }
    
    for code, keywords in supplier_keywords.items():
        for kw in keywords:
            if kw in text_lower:
                return get_profile_by_code(code)
    
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# Region Koordinatları (PDF Görsel Bölgeleme)
# ═══════════════════════════════════════════════════════════════════════════════
# Koordinatlar normalize edilmiş (0..1 aralığında)
# Format: (x0, y0, x1, y1) - sol üst ve sağ alt köşe

@dataclass
class RegionCoordinates:
    """PDF sayfa bölgesi koordinatları (normalize 0..1)"""
    ozet: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 0.2)
    fatura_detayi: tuple[float, float, float, float] = (0.0, 0.2, 1.0, 0.6)
    vergiler: tuple[float, float, float, float] = (0.0, 0.6, 1.0, 0.8)
    fatura_tutari: tuple[float, float, float, float] = (0.0, 0.8, 1.0, 1.0)


# CK Boğaziçi region koordinatları (kalibre edilmiş)
CK_BOGAZICI_REGIONS = RegionCoordinates(
    ozet=(0.52, 0.40, 0.95, 0.53),
    fatura_detayi=(0.52, 0.53, 0.95, 0.64),
    vergiler=(0.52, 0.72, 0.95, 0.83),
    fatura_tutari=(0.52, 0.83, 0.95, 0.90),
)

# Enerjisa region koordinatları (tahmini - kalibre edilmeli)
ENERJISA_REGIONS = RegionCoordinates(
    ozet=(0.50, 0.10, 0.95, 0.25),
    fatura_detayi=(0.05, 0.30, 0.95, 0.60),
    vergiler=(0.05, 0.60, 0.95, 0.75),
    fatura_tutari=(0.50, 0.75, 0.95, 0.90),
)

# Uludağ region koordinatları (tahmini - kalibre edilmeli)
ULUDAG_REGIONS = RegionCoordinates(
    ozet=(0.50, 0.10, 0.95, 0.25),
    fatura_detayi=(0.05, 0.25, 0.95, 0.55),
    vergiler=(0.05, 0.55, 0.95, 0.70),
    fatura_tutari=(0.50, 0.70, 0.95, 0.85),
)

# Varsayılan region koordinatları
DEFAULT_REGIONS = RegionCoordinates()

# Tedarikçi -> Region mapping
SUPPLIER_REGIONS = {
    "ck_bogazici": CK_BOGAZICI_REGIONS,
    "enerjisa": ENERJISA_REGIONS,
    "uludag": ULUDAG_REGIONS,
}


def get_regions_for_supplier(supplier_code: str) -> RegionCoordinates:
    """Tedarikçi için region koordinatlarını getir"""
    return SUPPLIER_REGIONS.get(supplier_code, DEFAULT_REGIONS)


# ═══════════════════════════════════════════════════════════════════════════════
# Region-Based Extraction Prompts
# ═══════════════════════════════════════════════════════════════════════════════

REGION_PROMPTS = {
    "fatura_detayi": """
Bu görsel bir elektrik faturasının FATURA DETAYI bölümüdür.
SADECE bu tablodaki kalem satırlarını oku ve JSON döndür.

Beklenen format:
{
  "lines": [
    {"label": "Enerji Bedeli", "qty_kwh": "150.000", "unit_price": "3,8500", "amount_tl": "577.500,00"},
    {"label": "Dağıtım Bedeli", "qty_kwh": "150.000", "unit_price": "0,5000", "amount_tl": "75.000,00"}
  ]
}

KURALLAR:
- Sadece bu tablodaki satırları oku
- Grafik veya istatistik değerlerini OKUMA
- "Ort. Tüketim" veya "kWh/gün" değerlerini OKUMA
- Sayıları olduğu gibi yaz (TR formatında)
""",

    "vergiler": """
Bu görsel bir elektrik faturasının VERGİ VE FONLAR bölümüdür.
SADECE vergi kalemlerini oku ve JSON döndür.

Beklenen format:
{
  "btv_tl": "5.775,00",
  "other_taxes_tl": "1.500,00",
  "vat_base_tl": "660.000,00",
  "vat_amount_tl": "132.000,00"
}
""",

    "ozet": """
Bu görsel bir elektrik faturasının ÖZET bölümüdür.
SADECE ödenecek tutar ve son ödeme tarihini oku ve JSON döndür.

Beklenen format:
{
  "payable_tl": "792.000,00",
  "due_date": "15.02.2025"
}
""",

    "fatura_tutari": """
Bu görsel bir elektrik faturasının FATURA TUTARI bölümüdür.
SADECE toplam tutarı oku ve JSON döndür.

Beklenen format:
{
  "total_tl": "792.000,00"
}
""",
}


def get_region_prompt(region_name: str) -> str:
    """Region için prompt getir"""
    return REGION_PROMPTS.get(region_name, "Bu görseldeki bilgileri JSON olarak döndür.")
