"""
Pricing Risk Engine — Pydantic request/response modelleri.

Tüm API endpoint'leri için giriş doğrulama ve çıkış serileştirme modelleri.
Enum'lar, hesaplama sonuçları, API request/response yapıları burada tanımlanır.

Requirements: 7.1, 7.4, 8.1, 8.3, 9.2, 10.1, 11.4, 12.1, 14.1, 14.3, 14.4, 16.3
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════════════════
# Enum'lar
# ═══════════════════════════════════════════════════════════════════════════════


class RiskLevel(str, Enum):
    """Profil risk seviyesi — sapma yüzdesine göre sınıflandırma."""
    LOW = "Düşük"
    MEDIUM = "Orta"
    HIGH = "Yüksek"


class TimeZone(str, Enum):
    """Zaman dilimi sınıflandırması — T1 gündüz, T2 puant, T3 gece."""
    T1 = "T1"  # Gündüz 06:00-16:59
    T2 = "T2"  # Puant  17:00-21:59
    T3 = "T3"  # Gece   22:00-05:59


# ═══════════════════════════════════════════════════════════════════════════════
# Parametre Modelleri
# ═══════════════════════════════════════════════════════════════════════════════


class ImbalanceParams(BaseModel):
    """Dengesizlik maliyeti parametreleri.

    İki mod desteklenir:
    - SMF bazlı (smf_based_imbalance_enabled=True):
        |Ağırlıklı_SMF − Ağırlıklı_PTF| × forecast_error_rate
    - Sabit oran (smf_based_imbalance_enabled=False):
        imbalance_cost_tl_per_mwh × forecast_error_rate
    """
    forecast_error_rate: float = Field(
        ge=0, le=1.0, default=0.05,
        description="Tahmini öngörü hata oranı (0–1 arası, varsayılan %5)",
    )
    imbalance_cost_tl_per_mwh: float = Field(
        ge=0, default=50.0,
        description="Sabit mod dengesizlik birim maliyeti (TL/MWh)",
    )
    smf_based_imbalance_enabled: bool = Field(
        default=False,
        description="SMF bazlı dengesizlik hesabı aktif mi",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Parse Sonuç Modelleri
# ═══════════════════════════════════════════════════════════════════════════════


class ExcelParseResult(BaseModel):
    """EPİAŞ uzlaştırma Excel ayrıştırma sonucu."""
    success: bool
    period: str = Field(description="Dönem (YYYY-MM)")
    total_rows: int = Field(ge=0, description="Ayrıştırılan satır sayısı")
    expected_hours: int = Field(ge=0, description="Beklenen saat sayısı (672–744)")
    missing_hours: list[int] = Field(default_factory=list, description="Eksik saat indeksleri")
    rejected_rows: list[dict] = Field(default_factory=list, description="Reddedilen satırlar + sebep")
    warnings: list[str] = Field(default_factory=list, description="Uyarı mesajları")
    quality_score: int = Field(ge=0, le=100, description="Veri kalite skoru (0–100)")


class ConsumptionParseResult(BaseModel):
    """Müşteri tüketim Excel ayrıştırma sonucu."""
    success: bool
    customer_id: str = Field(description="Müşteri kimliği")
    period: str = Field(description="Dönem (YYYY-MM)")
    total_rows: int = Field(ge=0, description="Ayrıştırılan satır sayısı")
    total_kwh: float = Field(ge=0, description="Toplam tüketim (kWh)")
    negative_hours: list[int] = Field(default_factory=list, description="Negatif tüketim saat indeksleri")
    warnings: list[str] = Field(default_factory=list, description="Uyarı mesajları")
    quality_score: int = Field(ge=0, le=100, description="Veri kalite skoru (0–100)")
    profile_id: Optional[int] = Field(default=None, description="Oluşturulan profil ID'si")


# ═══════════════════════════════════════════════════════════════════════════════
# Hesaplama Sonuç Modelleri
# ═══════════════════════════════════════════════════════════════════════════════


class WeightedPriceResult(BaseModel):
    """Ağırlıklı fiyat hesaplama sonucu.

    Formül: Σ(Saatlik_Tüketim × Saatlik_PTF) / Σ(Saatlik_Tüketim)
    """
    weighted_ptf_tl_per_mwh: float = Field(description="Ağırlıklı PTF (TL/MWh)")
    weighted_smf_tl_per_mwh: float = Field(description="Ağırlıklı SMF (TL/MWh)")
    arithmetic_avg_ptf: float = Field(description="Aritmetik ortalama PTF (TL/MWh)")
    arithmetic_avg_smf: float = Field(description="Aritmetik ortalama SMF (TL/MWh)")
    total_consumption_kwh: float = Field(description="Toplam tüketim (kWh)")
    total_cost_tl: float = Field(description="Toplam maliyet (TL)")
    hours_count: int = Field(ge=0, description="Hesaplanan saat sayısı")


class HourlyCostEntry(BaseModel):
    """Tek saat maliyet detayı — saatlik kârlılık analizi için."""
    date: str = Field(description="Tarih (YYYY-MM-DD)")
    hour: int = Field(ge=0, le=23, description="Saat (0–23)")
    consumption_kwh: float = Field(description="Saatlik tüketim (kWh)")
    ptf_tl_per_mwh: float = Field(description="Saatlik PTF (TL/MWh)")
    smf_tl_per_mwh: float = Field(description="Saatlik SMF (TL/MWh)")
    yekdem_tl_per_mwh: float = Field(description="YEKDEM bedeli (TL/MWh)")
    base_cost_tl: float = Field(description="Baz maliyet (TL) = (PTF + YEKDEM) × kWh / 1000")
    sales_price_tl: float = Field(description="Satış fiyatı (TL)")
    margin_tl: float = Field(description="Marj (TL) = satış - baz maliyet")
    is_loss_hour: bool = Field(description="Zarar saati mi (margin < 0)")
    time_zone: TimeZone = Field(description="Zaman dilimi (T1/T2/T3)")


class HourlyCostResult(BaseModel):
    """Saatlik maliyet hesaplama sonucu — tüm saatlerin toplu özeti."""
    hour_costs: list[HourlyCostEntry] = Field(description="Her saat için maliyet detayı")
    total_base_cost_tl: float = Field(description="Toplam baz maliyet (TL)")
    total_sales_revenue_tl: float = Field(description="Toplam satış geliri (TL)")
    total_gross_margin_tl: float = Field(description="Toplam brüt marj (TL)")
    total_net_margin_tl: float = Field(description="Toplam net marj (TL)")
    supplier_real_cost_tl_per_mwh: float = Field(
        description="Tedarikçi gerçek maliyet (TL/MWh) = Ağırlıklı_PTF + YEKDEM + Dengesizlik",
    )


class SimulationRow(BaseModel):
    """Tek katsayı simülasyon sonucu — simülasyon tablosunun bir satırı."""
    multiplier: float = Field(ge=1.0, description="Katsayı değeri")
    total_sales_tl: float = Field(description="Toplam satış geliri (TL)")
    total_cost_tl: float = Field(description="Toplam maliyet (TL)")
    gross_margin_tl: float = Field(description="Brüt marj (TL)")
    dealer_commission_tl: float = Field(description="Bayi komisyonu (TL)")
    net_margin_tl: float = Field(description="Net marj (TL)")
    loss_hours: int = Field(ge=0, description="Zararlı saat sayısı")
    total_loss_tl: float = Field(description="Toplam zarar tutarı (TL)")


class SafeMultiplierResult(BaseModel):
    """Güvenli katsayı hesaplama sonucu — 5. persentil algoritması."""
    safe_multiplier: float = Field(description="Güvenli katsayı (3 ondalık, örn: 1.042)")
    recommended_multiplier: float = Field(description="Önerilen katsayı (bir üst 0.01 adımı)")
    confidence_level: float = Field(
        ge=0, le=1.0, default=0.95,
        description="Güven düzeyi (varsayılan %95)",
    )
    periods_analyzed: int = Field(ge=0, description="Analiz edilen dönem sayısı")
    monthly_margins: list[float] = Field(
        default_factory=list,
        description="Her ay net marj listesi (TL)",
    )
    warning: Optional[str] = Field(
        default=None,
        description="Uyarı mesajı (×1.10 üzeri durumunda)",
    )


class RiskScoreResult(BaseModel):
    """Profil risk skoru sonucu.

    Eşikler:
    - sapma > %5 → Yüksek
    - %2 ≤ sapma ≤ %5 → Orta
    - sapma < %2 → Düşük

    Override kuralları:
    - T2 tüketim payı > %55 → Yüksek
    - T2 tüketim payı > %40 → en az Orta
    - Peak concentration > %45 → en az Orta
    """
    score: RiskLevel = Field(description="Risk seviyesi (Düşük/Orta/Yüksek)")
    weighted_ptf: float = Field(description="Ağırlıklı PTF (TL/MWh)")
    arithmetic_avg_ptf: float = Field(description="Aritmetik ortalama PTF (TL/MWh)")
    deviation_pct: float = Field(description="Sapma yüzdesi (%)")
    t2_consumption_pct: float = Field(description="T2 (puant) dilimi tüketim payı (%)")
    peak_concentration: float = Field(description="Yüksek PTF saatlerine yoğunlaşma oranı")
    reasons: list[str] = Field(
        default_factory=list,
        description="Risk seviyesini belirleyen açıklama listesi (Türkçe)",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Zaman Dilimi Dağılım Modelleri
# ═══════════════════════════════════════════════════════════════════════════════


class TimeZoneBreakdown(BaseModel):
    """Tek zaman dilimi dağılım sonucu — T1/T2/T3 her biri için."""
    label: str = Field(description="Dilim etiketi (örn: 'Gündüz (06:00-16:59)')")
    consumption_kwh: float = Field(description="Dilim toplam tüketimi (kWh)")
    consumption_pct: float = Field(description="Dilim tüketim payı (%)")
    weighted_ptf_tl_per_mwh: float = Field(description="Dilim ağırlıklı PTF (TL/MWh)")
    weighted_smf_tl_per_mwh: float = Field(description="Dilim ağırlıklı SMF (TL/MWh)")
    total_cost_tl: float = Field(description="Dilim toplam maliyeti (TL)")


class LossMapSummary(BaseModel):
    """Zarar haritası özeti — zararlı saatlerin toplu analizi."""
    total_loss_hours: int = Field(ge=0, description="Toplam zararlı saat sayısı")
    total_loss_tl: float = Field(description="Toplam zarar tutarı (TL)")
    by_time_zone: dict[str, int] = Field(
        default_factory=dict,
        description="Zaman dilimine göre zararlı saat sayıları (T1/T2/T3)",
    )
    worst_hours: list[dict] = Field(
        default_factory=list,
        description="En kötü zararlı saatler (tarih, saat, PTF, satış fiyatı, zarar)",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Veri Kalite Modeli
# ═══════════════════════════════════════════════════════════════════════════════


class DataQualityReport(BaseModel):
    """Veri kalite raporu — yüklenen verinin kalite kontrol sonucu."""
    market_data_score: Optional[int] = Field(
        default=None, ge=0, le=100,
        description="Piyasa verisi kalite skoru (0–100)",
    )
    consumption_data_score: Optional[int] = Field(
        default=None, ge=0, le=100,
        description="Tüketim verisi kalite skoru (0–100)",
    )
    issues: list[dict] = Field(
        default_factory=list,
        description="Tespit edilen sorunlar (tür, saat, değer, açıklama)",
    )
    warning: Optional[str] = Field(
        default=None,
        description="Kalite skoru < 80 ise uyarı mesajı",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# API Request Modelleri
# ═══════════════════════════════════════════════════════════════════════════════


class AnalyzeRequest(BaseModel):
    """Tam fiyatlama analizi isteği — POST /api/pricing/analyze."""
    customer_id: Optional[str] = Field(
        default=None,
        description="Müşteri kimliği (gerçek profil kullanılacaksa)",
    )
    period: str = Field(description="Dönem (YYYY-MM)")
    multiplier: float = Field(
        ge=1.0,
        description="Katsayı değeri (minimum 1.0)",
    )
    dealer_commission_pct: float = Field(
        ge=0, le=100, default=0,
        description="Bayi komisyon yüzdesi (0–100 arası, varsayılan 0)",
    )
    imbalance_params: ImbalanceParams = Field(
        default_factory=ImbalanceParams,
        description="Dengesizlik maliyeti parametreleri",
    )
    use_template: Optional[bool] = Field(
        default=None,
        description="Profil şablonu kullanılsın mı",
    )
    template_name: Optional[str] = Field(
        default=None,
        description="Şablon adı (use_template=True ise zorunlu)",
    )
    template_monthly_kwh: Optional[float] = Field(
        default=None, ge=0,
        description="Şablon aylık toplam tüketim (kWh)",
    )


class SimulateRequest(BaseModel):
    """Katsayı simülasyonu isteği — POST /api/pricing/simulate."""
    customer_id: Optional[str] = Field(
        default=None,
        description="Müşteri kimliği",
    )
    period: str = Field(description="Dönem (YYYY-MM)")
    dealer_commission_pct: float = Field(
        ge=0, le=100, default=0,
        description="Bayi komisyon yüzdesi (0–100 arası, varsayılan 0)",
    )
    imbalance_params: ImbalanceParams = Field(
        default_factory=ImbalanceParams,
        description="Dengesizlik maliyeti parametreleri",
    )
    multiplier_start: float = Field(
        ge=1.0, default=1.02,
        description="Simülasyon başlangıç katsayısı (minimum 1.0)",
    )
    multiplier_end: float = Field(
        le=2.0, default=1.10,
        description="Simülasyon bitiş katsayısı (maksimum 2.0)",
    )
    multiplier_step: float = Field(
        gt=0, default=0.01,
        description="Simülasyon adım değeri (> 0)",
    )
    use_template: Optional[bool] = Field(default=None)
    template_name: Optional[str] = Field(default=None)
    template_monthly_kwh: Optional[float] = Field(default=None, ge=0)


class CompareRequest(BaseModel):
    """Çoklu ay karşılaştırma isteği — POST /api/pricing/compare."""
    customer_id: Optional[str] = Field(
        default=None,
        description="Müşteri kimliği",
    )
    periods: list[str] = Field(
        min_length=2, max_length=12,
        description="Karşılaştırılacak dönemler (2–12 adet, YYYY-MM)",
    )
    multiplier: float = Field(
        ge=1.0,
        description="Katsayı değeri (minimum 1.0)",
    )
    dealer_commission_pct: float = Field(
        ge=0, le=100, default=0,
        description="Bayi komisyon yüzdesi (0–100 arası, varsayılan 0)",
    )
    imbalance_params: ImbalanceParams = Field(
        default_factory=ImbalanceParams,
        description="Dengesizlik maliyeti parametreleri",
    )
    use_template: Optional[bool] = Field(default=None)
    template_name: Optional[str] = Field(default=None)
    template_monthly_kwh: Optional[float] = Field(default=None, ge=0)


class ReportRequest(BaseModel):
    """Rapor üretim isteği — POST /api/pricing/report/pdf veya /excel."""
    customer_id: str = Field(description="Müşteri kimliği")
    period: str = Field(description="Dönem (YYYY-MM)")
    multiplier: float = Field(
        ge=1.0,
        description="Katsayı değeri (minimum 1.0)",
    )
    dealer_commission_pct: float = Field(
        ge=0, le=100, default=0,
        description="Bayi komisyon yüzdesi (0–100 arası, varsayılan 0)",
    )
    imbalance_params: ImbalanceParams = Field(
        default_factory=ImbalanceParams,
        description="Dengesizlik maliyeti parametreleri",
    )
    customer_name: Optional[str] = Field(
        default=None,
        description="Müşteri adı (rapor başlığı için)",
    )
    contact_person: Optional[str] = Field(
        default=None,
        description="İlgili kişi adı",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# API Response Modelleri
# ═══════════════════════════════════════════════════════════════════════════════


class SupplierCostSummary(BaseModel):
    """Tedarikçi maliyet özeti — analiz yanıtında kullanılır."""
    weighted_ptf_tl_per_mwh: float
    yekdem_tl_per_mwh: float
    imbalance_tl_per_mwh: float
    total_cost_tl_per_mwh: float


class PricingSummary(BaseModel):
    """Fiyatlama özeti — analiz yanıtında kullanılır."""
    multiplier: float
    sales_price_tl_per_mwh: float
    gross_margin_tl_per_mwh: float
    dealer_commission_tl_per_mwh: float
    net_margin_tl_per_mwh: float
    total_sales_tl: float
    total_cost_tl: float
    total_gross_margin_tl: float
    total_dealer_commission_tl: float
    total_net_margin_tl: float


class AnalyzeResponse(BaseModel):
    """Tam fiyatlama analizi yanıtı — POST /api/pricing/analyze."""
    status: str = Field(default="ok")
    period: str
    customer_id: Optional[str] = None
    weighted_prices: WeightedPriceResult
    supplier_cost: SupplierCostSummary
    pricing: PricingSummary
    time_zone_breakdown: dict[str, TimeZoneBreakdown] = Field(
        description="T1/T2/T3 zaman dilimi dağılımı",
    )
    loss_map: LossMapSummary
    risk_score: RiskScoreResult
    safe_multiplier: SafeMultiplierResult
    warnings: list[dict] = Field(default_factory=list)
    data_quality: DataQualityReport
    cache_hit: bool = Field(default=False)


class SimulateResponse(BaseModel):
    """Katsayı simülasyonu yanıtı — POST /api/pricing/simulate."""
    status: str = Field(default="ok")
    period: str
    simulation: list[SimulationRow]
    safe_multiplier: SafeMultiplierResult


class PeriodComparison(BaseModel):
    """Tek dönem karşılaştırma sonucu — compare yanıtında kullanılır."""
    period: str
    weighted_ptf_tl_per_mwh: float
    weighted_smf_tl_per_mwh: float
    total_cost_tl: float
    net_margin_tl: float
    risk_score: RiskLevel
    change_pct: Optional[dict[str, float]] = Field(
        default=None,
        description="Önceki döneme göre değişim yüzdeleri",
    )


class CompareResponse(BaseModel):
    """Çoklu ay karşılaştırma yanıtı — POST /api/pricing/compare."""
    status: str = Field(default="ok")
    periods_analyzed: int
    missing_periods: list[str] = Field(default_factory=list)
    comparison: list[PeriodComparison]
    safe_multiplier: SafeMultiplierResult


# ═══════════════════════════════════════════════════════════════════════════════
# Upload Response Modelleri
# ═══════════════════════════════════════════════════════════════════════════════


class UploadMarketDataResponse(BaseModel):
    """Piyasa verisi yükleme yanıtı — POST /api/pricing/upload-market-data."""
    status: str = Field(default="ok")
    period: str
    total_rows: int
    expected_hours: int
    missing_hours: list[int] = Field(default_factory=list)
    rejected_rows: list[dict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    quality_score: int = Field(ge=0, le=100)
    version: int
    previous_version_archived: bool = Field(default=False)


class UploadConsumptionResponse(BaseModel):
    """Tüketim verisi yükleme yanıtı — POST /api/pricing/upload-consumption."""
    status: str = Field(default="ok")
    customer_id: str
    period: str
    total_rows: int
    total_kwh: float
    negative_hours: list[int] = Field(default_factory=list)
    quality_score: int = Field(ge=0, le=100)
    profile_id: int
    version: int
