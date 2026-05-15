"""
Invoice Reconciliation Engine — Pydantic schemas.

Tüm request/response modelleri, enum'lar ve iç veri yapıları.
IC-1: Hesaplama alanları Decimal; API serialization'da float'a dönüşür.
IC-4: Reconciliation output zorunlu alanlar burada tanımlı.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


# ═══════════════════════════════════════════════════════════════════════════════
# Enums
# ═══════════════════════════════════════════════════════════════════════════════


class ExcelFormat(str, Enum):
    """Algılanan Excel formatı."""
    FORMAT_A = "format_a"  # Büyük tüketici: Profil Tarihi + Tüketim (Çekiş) + Çarpan
    FORMAT_B = "format_b"  # Küçük tüketici: Tarih + Aktif Çekiş


class Severity(str, Enum):
    """Uyumsuzluk şiddet seviyesi."""
    LOW = "LOW"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class ReconciliationStatus(str, Enum):
    """Mutabakat durumu."""
    MATCH = "UYUMLU"
    MISMATCH = "UYUMSUZ"
    NOT_CHECKED = "KONTROL_EDILMEDI"


# ═══════════════════════════════════════════════════════════════════════════════
# Internal Models
# ═══════════════════════════════════════════════════════════════════════════════


class HourlyRecord(BaseModel):
    """Tek saatlik tüketim kaydı — parse sonrası iç model.

    IC-1: consumption_kwh Decimal olarak saklanır.
    Multiplier metadata-only — hiçbir hesaplamada kullanılmaz.
    """
    timestamp: datetime
    date: str  # YYYY-MM-DD
    hour: int = Field(ge=0, le=23)
    period: str  # YYYY-MM
    consumption_kwh: Decimal = Field(ge=Decimal("0"))
    multiplier: Optional[Decimal] = None  # Format A metadata, hesaplamada KULLANILMAZ

    model_config = {"arbitrary_types_allowed": True}


class ParseError(BaseModel):
    """Parse edilemeyen satır bilgisi."""
    row_number: int
    column: str
    raw_value: str
    error: str


class ParseResult(BaseModel):
    """Excel parse sonucu.

    Invariant: total_rows == successful_rows + failed_rows
    """
    success: bool
    format_detected: ExcelFormat
    records: list[HourlyRecord]
    errors: list[ParseError]
    total_rows: int
    successful_rows: int
    failed_rows: int
    warnings: list[str] = Field(default_factory=list)
    multiplier_metadata: Optional[Decimal] = None  # Format A çarpan (bilgi amaçlı)

    @field_validator("failed_rows")
    @classmethod
    def validate_row_counts(cls, v: int, info) -> int:
        """Property 5: total_rows == successful_rows + failed_rows."""
        data = info.data
        if "total_rows" in data and "successful_rows" in data:
            expected = data["total_rows"] - data["successful_rows"]
            if v != expected:
                raise ValueError(
                    f"failed_rows ({v}) != total_rows ({data['total_rows']}) "
                    f"- successful_rows ({data['successful_rows']})"
                )
        return v


class PeriodStats(BaseModel):
    """Dönem tamamlılık istatistikleri.

    IC-3: expected_hours DST-aware hesaplanır.
    """
    period: str  # YYYY-MM
    record_count: int
    expected_hours: int  # DST-aware: normal=gün×24, DST-forward=gün×24-1, DST-back=gün×24+1
    missing_hours: list[str] = Field(default_factory=list)  # "YYYY-MM-DD HH:00"
    duplicate_hours: list[str] = Field(default_factory=list)
    has_gaps: bool = False


class TimeZoneSummary(BaseModel):
    """Dönem T1/T2/T3 özeti.

    IC-1: Tüm kWh değerleri Decimal.
    Invariant: t1_kwh + t2_kwh + t3_kwh == total_kwh (±0.01)
    """
    period: str
    t1_kwh: Decimal
    t2_kwh: Decimal
    t3_kwh: Decimal
    total_kwh: Decimal
    t1_pct: Decimal
    t2_pct: Decimal
    t3_pct: Decimal


# ═══════════════════════════════════════════════════════════════════════════════
# API Request Models
# ═══════════════════════════════════════════════════════════════════════════════


class InvoiceInput(BaseModel):
    """Fatura bilgisi girişi — dönem bazlı."""
    period: str = Field(description="Fatura dönemi YYYY-MM")
    supplier_name: Optional[str] = None
    tariff_group: Optional[str] = None
    unit_price_tl_per_kwh: Optional[Decimal] = Field(default=None, ge=Decimal("0"))
    discount_pct: Optional[Decimal] = Field(default=None, ge=Decimal("0"), le=Decimal("100"))
    distribution_unit_price_tl_per_kwh: Optional[Decimal] = Field(default=None, ge=Decimal("0"))
    declared_t1_kwh: Optional[Decimal] = Field(default=None, ge=Decimal("0"))
    declared_t2_kwh: Optional[Decimal] = Field(default=None, ge=Decimal("0"))
    declared_t3_kwh: Optional[Decimal] = Field(default=None, ge=Decimal("0"))
    declared_total_kwh: Optional[Decimal] = Field(default=None, ge=Decimal("0"))
    declared_total_tl: Optional[Decimal] = Field(default=None, ge=Decimal("0"))


class ToleranceConfig(BaseModel):
    """Mutabakat tolerans konfigürasyonu.

    IC-4: Hem yüzdesel hem mutlak tolerans desteklenir.
    """
    pct_tolerance: Decimal = Field(
        default=Decimal("1.0"), ge=Decimal("0"),
        description="Yüzdesel tolerans (%)"
    )
    abs_tolerance_kwh: Decimal = Field(
        default=Decimal("1.0"), ge=Decimal("0"),
        description="Mutlak tolerans (kWh)"
    )


class ComparisonConfig(BaseModel):
    """Gelka teklif karşılaştırma konfigürasyonu."""
    gelka_margin_multiplier: Decimal = Field(
        default=Decimal("1.05"), ge=Decimal("1.0"),
        description="Gelka marj katsayısı (1.05 = %5 marj)"
    )


class ReconRequest(BaseModel):
    """Ana mutabakat analizi isteği (JSON body)."""
    invoices: list[InvoiceInput] = Field(default_factory=list)
    tolerance: ToleranceConfig = Field(default_factory=ToleranceConfig)
    comparison: ComparisonConfig = Field(default_factory=ComparisonConfig)


# ═══════════════════════════════════════════════════════════════════════════════
# API Response Models
# ═══════════════════════════════════════════════════════════════════════════════


class ReconciliationItem(BaseModel):
    """Tek alan mutabakat sonucu.

    IC-4: Zorunlu alanlar: excel değeri, invoice değeri, delta, severity.
    """
    field: str  # "t1_kwh", "t2_kwh", "t3_kwh", "total_kwh"
    excel_total_kwh: float  # Excel'den hesaplanan
    invoice_total_kwh: float  # Faturada beyan edilen
    delta_kwh: float  # excel - invoice
    delta_pct: float  # yüzdesel fark
    status: ReconciliationStatus
    severity: Optional[Severity] = None


class PtfCostResult(BaseModel):
    """PTF maliyet hesaplama sonucu."""
    total_ptf_cost_tl: float
    weighted_avg_ptf_tl_per_mwh: float
    hours_matched: int
    hours_missing_ptf: int
    missing_ptf_pct: float
    ptf_data_sufficient: bool
    warning: Optional[str] = None


class YekdemCostResult(BaseModel):
    """YEKDEM maliyet sonucu."""
    yekdem_tl_per_mwh: float
    total_yekdem_cost_tl: float
    available: bool


class CostComparison(BaseModel):
    """Fatura vs Gelka maliyet karşılaştırması."""
    invoice_energy_tl: float
    invoice_distribution_tl: float
    invoice_total_tl: float
    gelka_energy_tl: float
    gelka_distribution_tl: float
    gelka_total_tl: float
    diff_tl: float
    diff_pct: float
    message: str  # "Tasarruf potansiyeli: X TL (%Y)" veya "Mevcut tedarikçi avantajlı: ..."


class PeriodResult(BaseModel):
    """Tek dönem mutabakat sonucu."""
    period: str
    total_kwh: float
    t1_kwh: float
    t2_kwh: float
    t3_kwh: float
    t1_pct: float
    t2_pct: float
    t3_pct: float
    missing_hours: int
    duplicate_hours: int
    reconciliation: list[ReconciliationItem]
    overall_status: ReconciliationStatus
    overall_severity: Optional[Severity] = None
    ptf_cost: Optional[PtfCostResult] = None
    yekdem_cost: Optional[YekdemCostResult] = None
    cost_comparison: Optional[CostComparison] = None
    quote_blocked: bool = False
    quote_block_reason: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)


class ReconReport(BaseModel):
    """Tam mutabakat raporu — API response.

    Response contract (immutable v1):
    - api_version: always 1 (for future versioning)
    - status: "ok" (full success) | "partial" (parse+recon ok, quote blocked) | "error"
    - When status="partial": quote outputs are None, quote_blocked=true in periods
    - No savings/quote message generated when status="partial"
    """
    api_version: int = 1
    status: str = "ok"  # "ok" | "partial" | "error"
    format_detected: ExcelFormat
    parse_stats: dict  # total_rows, successful_rows, failed_rows
    periods: list[PeriodResult]
    summary: Optional[dict] = None  # Çoklu dönem toplam özeti
    warnings: list[str] = Field(default_factory=list)
    multiplier_metadata: Optional[float] = None  # Format A çarpan değeri (bilgi amaçlı)


class ErrorResponse(BaseModel):
    """Hata yanıt şeması."""
    error: str  # "empty_file", "unknown_format", "file_too_large"
    message: str  # Türkçe açıklayıcı mesaj
    details: Optional[dict] = None
