"""
Invoice Reconciliation Engine — Reconciler.

Fatura beyan değerleri ile Excel'den hesaplanan değerleri karşılaştırır.
IC-1: Tüm hesaplamalar Decimal ile yapılır.
IC-4: Zorunlu output alanları: excel_total_kwh, invoice_total_kwh, delta_kwh, delta_pct, severity.

Tolerans: hem yüzdesel (±1%) hem mutlak (±1 kWh) — her ikisi de sağlanmalı MATCH için.
Severity: LOW / WARNING / CRITICAL.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from .schemas import (
    InvoiceInput,
    ReconciliationItem,
    ReconciliationStatus,
    Severity,
    TimeZoneSummary,
    ToleranceConfig,
)


def reconcile_consumption(
    calculated: TimeZoneSummary,
    invoice: InvoiceInput,
    config: ToleranceConfig,
) -> list[ReconciliationItem]:
    """Hesaplanan vs beyan edilen değerleri karşılaştır.

    IC-4: Her ReconciliationItem şu alanları expose eder:
    - excel_total_kwh (hesaplanan)
    - invoice_total_kwh (beyan edilen)
    - delta_kwh (excel - invoice)
    - delta_pct (yüzdesel fark)
    - severity (LOW / WARNING / CRITICAL veya None)

    Args:
        calculated: Excel'den hesaplanan T1/T2/T3 özeti
        invoice: Fatura beyan değerleri
        config: Tolerans konfigürasyonu

    Returns:
        ReconciliationItem listesi (her alan için bir kayıt)
    """
    results: list[ReconciliationItem] = []

    # T1 karşılaştırma
    if invoice.declared_t1_kwh is not None:
        results.append(_compare_field(
            field="t1_kwh",
            calculated=calculated.t1_kwh,
            declared=invoice.declared_t1_kwh,
            config=config,
        ))

    # T2 karşılaştırma
    if invoice.declared_t2_kwh is not None:
        results.append(_compare_field(
            field="t2_kwh",
            calculated=calculated.t2_kwh,
            declared=invoice.declared_t2_kwh,
            config=config,
        ))

    # T3 karşılaştırma
    if invoice.declared_t3_kwh is not None:
        results.append(_compare_field(
            field="t3_kwh",
            calculated=calculated.t3_kwh,
            declared=invoice.declared_t3_kwh,
            config=config,
        ))

    # Toplam karşılaştırma
    if invoice.declared_total_kwh is not None:
        results.append(_compare_field(
            field="total_kwh",
            calculated=calculated.total_kwh,
            declared=invoice.declared_total_kwh,
            config=config,
        ))

    return results


def calculate_effective_price(
    unit_price: Decimal, discount_pct: Optional[Decimal]
) -> Decimal:
    """Efektif birim fiyat hesapla.

    Formula: effective = unit_price × (1 - discount_pct / 100)

    Property 11: For any unit_price >= 0 and discount_pct in [0, 100],
    effective_price == unit_price × (1 - discount_pct / 100).
    """
    if discount_pct is None or discount_pct == Decimal("0"):
        return unit_price
    return unit_price * (Decimal("1") - discount_pct / Decimal("100"))


def get_overall_status(items: list[ReconciliationItem]) -> ReconciliationStatus:
    """Tüm reconciliation item'lardan genel durum belirle."""
    if not items:
        return ReconciliationStatus.NOT_CHECKED

    has_mismatch = any(i.status == ReconciliationStatus.MISMATCH for i in items)
    if has_mismatch:
        return ReconciliationStatus.MISMATCH
    return ReconciliationStatus.MATCH


def get_overall_severity(items: list[ReconciliationItem]) -> Optional[Severity]:
    """Tüm item'lardan en yüksek severity'yi döndür."""
    severities = [i.severity for i in items if i.severity is not None]
    if not severities:
        return None

    # CRITICAL > WARNING > LOW
    if Severity.CRITICAL in severities:
        return Severity.CRITICAL
    if Severity.WARNING in severities:
        return Severity.WARNING
    return Severity.LOW


# ═══════════════════════════════════════════════════════════════════════════════
# Internal
# ═══════════════════════════════════════════════════════════════════════════════


def _compare_field(
    field: str,
    calculated: Decimal,
    declared: Decimal,
    config: ToleranceConfig,
) -> ReconciliationItem:
    """Tek alan karşılaştırması.

    MATCH koşulu: |delta_pct| <= pct_tolerance AND |delta_kwh| <= abs_tolerance_kwh
    Aksi halde MISMATCH + severity.
    """
    delta_kwh = calculated - declared

    # Yüzdesel fark (declared == 0 ise sadece mutlak tolerans kullanılır)
    if declared > Decimal("0"):
        delta_pct = (delta_kwh / declared) * Decimal("100")
    else:
        delta_pct = Decimal("0") if delta_kwh == Decimal("0") else Decimal("100")

    abs_delta_pct = abs(delta_pct)
    abs_delta_kwh = abs(delta_kwh)

    # MATCH: her iki tolerans da sağlanmalı
    if abs_delta_pct <= config.pct_tolerance and abs_delta_kwh <= config.abs_tolerance_kwh:
        return ReconciliationItem(
            field=field,
            excel_total_kwh=float(calculated),
            invoice_total_kwh=float(declared),
            delta_kwh=float(delta_kwh),
            delta_pct=float(delta_pct),
            status=ReconciliationStatus.MATCH,
            severity=None,
        )

    # MISMATCH — severity sınıflandırması
    severity = _classify_severity(abs_delta_pct, abs_delta_kwh)

    return ReconciliationItem(
        field=field,
        excel_total_kwh=float(calculated),
        invoice_total_kwh=float(declared),
        delta_kwh=float(delta_kwh),
        delta_pct=float(delta_pct),
        status=ReconciliationStatus.MISMATCH,
        severity=severity,
    )


def _classify_severity(abs_delta_pct: Decimal, abs_delta_kwh: Decimal) -> Severity:
    """Uyumsuzluk şiddet sınıflandırması.

    Property 14:
    - CRITICAL: |diff_pct| > 5 OR |diff_kwh| > 20
    - WARNING:  |diff_pct| > 2 OR |diff_kwh| > 5 (and not CRITICAL)
    - LOW:      otherwise
    """
    if abs_delta_pct > Decimal("5") or abs_delta_kwh > Decimal("20"):
        return Severity.CRITICAL
    if abs_delta_pct > Decimal("2") or abs_delta_kwh > Decimal("5"):
        return Severity.WARNING
    return Severity.LOW
