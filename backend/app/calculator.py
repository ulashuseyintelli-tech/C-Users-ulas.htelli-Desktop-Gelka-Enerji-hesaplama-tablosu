"""
Calculator Module - Fatura Hesaplama Motoru

KONTRAT (Sprint 8.3):
═══════════════════════════════════════════════════════════════════════════════
CURRENT (Mevcut Fatura) Tarafı:
- current_total_with_vat_tl = invoice_total_with_vat_tl (SOURCE OF TRUTH)
- Faturadaki gerçek toplam kullanılır, HESAPLANMAZ
- current_* kalemleri (energy, distribution, vat) sadece breakdown/evidence amaçlı
- Bu kalemler toplamı override ETMEZ

OFFER (Teklif) Tarafı:
- offer_* tamamen HESAPLANIR (formül doğrulaması burada anlamlı)
- offer_energy = (PTF + YEKDEM?) × kWh × multiplier
- offer_total = offer_matrah + offer_vat

TOTAL_MISMATCH Flag:
- computed_total = matrah + vat (formülden hesaplanan)
- invoice_total = faturadan okunan
- Fark > %5 veya > 50 TL ise INVOICE_TOTAL_MISMATCH flag üretilir
═══════════════════════════════════════════════════════════════════════════════
"""

import logging
from dataclasses import dataclass
from typing import Optional, Tuple, List
from sqlalchemy.orm import Session
from .models import InvoiceExtraction, OfferParams, CalculationResult
from .distribution_tariffs import get_distribution_unit_price_from_extraction, TariffLookupResult
from .config import THRESHOLDS

logger = logging.getLogger(__name__)


# Total mismatch thresholds - NOW FROM CONFIG
# DEPRECATED: Use THRESHOLDS.Mismatch.* directly
TOTAL_MISMATCH_RATIO_THRESHOLD = THRESHOLDS.Mismatch.RATIO
TOTAL_MISMATCH_ABSOLUTE_THRESHOLD = THRESHOLDS.Mismatch.ABSOLUTE
TOTAL_MISMATCH_SEVERE_RATIO = THRESHOLDS.Mismatch.SEVERE_RATIO
TOTAL_MISMATCH_SEVERE_ABSOLUTE = THRESHOLDS.Mismatch.SEVERE_ABSOLUTE


@dataclass
class TotalMismatchInfo:
    """Invoice total vs computed total karşılaştırma sonucu."""
    has_mismatch: bool
    invoice_total: float
    computed_total: float
    delta: float
    ratio: float
    severity: str = "S2"  # S1 veya S2
    suspect_reason: Optional[str] = None  # OCR_LOCALE_SUSPECT, vb.
    
    def to_dict(self) -> dict:
        result = {
            "has_mismatch": self.has_mismatch,
            "invoice_total": round(self.invoice_total, 2),
            "computed_total": round(self.computed_total, 2),
            "delta": round(self.delta, 2),
            "ratio": round(self.ratio, 4),
            "severity": self.severity,
        }
        if self.suspect_reason:
            result["suspect_reason"] = self.suspect_reason
        return result


def check_total_mismatch(
    invoice_total: float,
    computed_total: float,
    extraction_confidence: float = 1.0,
    ratio_threshold: float = TOTAL_MISMATCH_RATIO_THRESHOLD,
    absolute_threshold: float = TOTAL_MISMATCH_ABSOLUTE_THRESHOLD,
) -> TotalMismatchInfo:
    """
    Invoice total ile computed total arasındaki farkı kontrol et.
    
    S2 Mismatch koşulu (OR):
    - ratio >= 0.05 (%5)
    - delta >= 50 TL
    
    S1 Escalation koşulu:
    - (ratio >= 0.20 AND delta >= 50) OR delta >= 500
    
    OCR_LOCALE_SUSPECT:
    - extraction_confidence < 0.7 AND has_mismatch
    
    Returns:
        TotalMismatchInfo with mismatch flag, severity, and suspect_reason
    """
    delta = abs(invoice_total - computed_total)
    ratio = delta / max(invoice_total, 0.01)  # Avoid division by zero
    
    # S2 mismatch check
    has_mismatch = (ratio >= ratio_threshold) or (delta >= absolute_threshold)
    
    # Default severity
    severity = "S2"
    
    # S1 escalation: (ratio >= 20% AND delta >= 50) OR delta >= 500
    if has_mismatch:
        is_severe_ratio = (ratio >= TOTAL_MISMATCH_SEVERE_RATIO and delta >= absolute_threshold)
        is_severe_absolute = (delta >= TOTAL_MISMATCH_SEVERE_ABSOLUTE)
        if is_severe_ratio or is_severe_absolute:
            severity = "S1"
    
    # OCR/Locale suspect detection
    suspect_reason = None
    if has_mismatch and extraction_confidence < 0.7:
        suspect_reason = "OCR_LOCALE_SUSPECT"
    
    return TotalMismatchInfo(
        has_mismatch=has_mismatch,
        invoice_total=invoice_total,
        computed_total=computed_total,
        delta=delta,
        ratio=ratio,
        severity=severity,
        suspect_reason=suspect_reason,
    )


class CalculationError(Exception):
    """Hesaplama hatası - hard error"""
    pass


def get_ptf_yekdem_for_period(
    db: Optional[Session],
    period: Optional[str],
    params: OfferParams
) -> Tuple[float, float, str, Optional[str]]:
    """
    PTF/YEKDEM değerlerini belirle.
    
    Öncelik:
    1. params.use_reference_prices=False ve değerler verilmişse → override
    2. DB'den dönem bazlı çek
    3. Default değerler (fallback)
    
    Returns:
        (ptf_tl_per_mwh, yekdem_tl_per_mwh, source, error_message)
    """
    from .market_prices import (
        get_market_prices, 
        DEFAULT_PTF_TL_PER_MWH, 
        DEFAULT_YEKDEM_TL_PER_MWH
    )
    
    # Override: Kullanıcı değerleri verilmişse ve use_reference_prices=False
    if not params.use_reference_prices:
        if params.weighted_ptf_tl_per_mwh is not None and params.weighted_ptf_tl_per_mwh > 0:
            ptf = params.weighted_ptf_tl_per_mwh
            yekdem = params.yekdem_tl_per_mwh or 0
            return (ptf, yekdem, "override", None)
    
    # DB'den çek (dönem varsa)
    if db is not None and period:
        prices = get_market_prices(db, period)
        if prices:
            return (prices.ptf_tl_per_mwh, prices.yekdem_tl_per_mwh, "reference", None)
        else:
            # Dönem için veri yok - HARD ERROR
            error_msg = f"Dönem {period} için referans fiyat bulunamadı. Admin panelden ekleyin."
            return (0, 0, "not_found", error_msg)
    
    # Fallback: Default değerler (DB yok veya dönem yok)
    logger.warning(f"PTF/YEKDEM için default değerler kullanılıyor (period={period})")
    return (DEFAULT_PTF_TL_PER_MWH, DEFAULT_YEKDEM_TL_PER_MWH, "default", None)


def calculate_offer(
    extraction: InvoiceExtraction, 
    params: OfferParams,
    db: Optional[Session] = None
) -> CalculationResult:
    """
    Excel formüllerini Python'da hesapla - Excel bağımlılığı yok.
    
    UI Switches:
    - extra_items_apply_to_offer: Ek kalemleri teklife dahil et
    - use_offer_distribution: Dağıtımı farklı birim fiyatla hesapla
    
    IMPORTANT: Mevcut fatura tutarı faturadan okunur, hesaplanmaz!
    Hesaplama sadece teklif için yapılır.
    
    YEKDEM KURALI:
    - Faturada YEKDEM bedeli varsa (yek_amount > 0) → Teklife YEKDEM dahil
    - Faturada YEKDEM bedeli yoksa veya 0 ise → Teklife YEKDEM dahil DEĞİL
    - Bu otomatik tespit, params.include_yekdem_in_offer ile override edilebilir
    """
    
    # Input değerleri
    kwh = extraction.consumption_kwh.value or 0
    current_unit_price = extraction.current_active_unit_price_tl_per_kwh.value or 0
    demand_qty = extraction.demand_qty.value or 0
    demand_unit_price = extraction.demand_unit_price_tl_per_unit.value or 0
    
    # ═══════════════════════════════════════════════════════════════════════════════
    # DAĞITIM BİRİM FİYATI - EPDK TARİFESİNDEN HESAPLA
    # ═══════════════════════════════════════════════════════════════════════════════
    # Öncelik sırası:
    # 1. params.offer_distribution_unit_price_tl_per_kwh (manuel override)
    # 2. EPDK tarifesinden hesaplanan değer (tarife meta'dan)
    # 3. Faturadan okunan değer (fallback)
    
    # Faturadan okunan dağıtım birim fiyatı (fallback için)
    extracted_dist_unit_price = extraction.distribution_unit_price_tl_per_kwh.value or 0
    
    # EPDK tarifesinden hesapla
    tariff_lookup: TariffLookupResult = get_distribution_unit_price_from_extraction(extraction)
    epdk_dist_unit_price = tariff_lookup.unit_price if tariff_lookup.success else None
    
    # Dağıtım birim fiyatı seçimi
    distribution_source = "unknown"
    if params.use_offer_distribution and params.offer_distribution_unit_price_tl_per_kwh is not None:
        # Manuel override
        current_dist_unit_price = params.offer_distribution_unit_price_tl_per_kwh
        distribution_source = "manual_override"
        logger.info(f"Dağıtım birim fiyatı: {current_dist_unit_price:.6f} TL/kWh (manuel override)")
    elif epdk_dist_unit_price is not None:
        # EPDK tarifesinden
        current_dist_unit_price = epdk_dist_unit_price
        distribution_source = f"epdk_tariff:{tariff_lookup.tariff_key}"
        logger.info(f"Dağıtım birim fiyatı: {current_dist_unit_price:.6f} TL/kWh (EPDK: {tariff_lookup.tariff_key})")
    elif extracted_dist_unit_price > 0:
        # Faturadan okunan (fallback)
        current_dist_unit_price = extracted_dist_unit_price
        distribution_source = "extracted_from_invoice"
        logger.warning(f"Dağıtım birim fiyatı: {current_dist_unit_price:.6f} TL/kWh (faturadan - EPDK lookup başarısız: {tariff_lookup.error_message})")
    else:
        # Hiçbiri yok - HARD ERROR
        distribution_source = "not_found"
        error_msg = f"Dağıtım birim fiyatı hesaplanamadı! EPDK tarife lookup: {tariff_lookup.error_message or 'Tarife bilgisi bulunamadı'}"
        logger.error(error_msg)
        raise CalculationError(error_msg)
    
    # Cross-check: Faturadan okunan vs EPDK
    distribution_mismatch_warning = None
    if extracted_dist_unit_price > 0 and epdk_dist_unit_price is not None:
        diff_percent = abs(extracted_dist_unit_price - epdk_dist_unit_price) / epdk_dist_unit_price * 100
        if diff_percent > 5:
            distribution_mismatch_warning = (
                f"Dağıtım birim fiyatı uyuşmazlığı: "
                f"Faturadan={extracted_dist_unit_price:.6f}, EPDK={epdk_dist_unit_price:.6f} TL/kWh "
                f"(fark: %{diff_percent:.1f})"
            )
            logger.warning(distribution_mismatch_warning)
    
    # Faturadan okunan YEKDEM bedeli
    invoice_yek_amount = 0.0
    if extraction.charges and extraction.charges.yek_amount and extraction.charges.yek_amount.value:
        invoice_yek_amount = extraction.charges.yek_amount.value
    
    # YEKDEM otomatik tespit: Faturada YEKDEM > 0 ise teklife dahil et
    # Bu değer params.include_yekdem_in_offer ile override edilebilir
    # Ama default olarak faturaya göre karar ver
    should_include_yekdem = invoice_yek_amount > 0
    
    # Faturadan okunan gerçek değerler (raw_breakdown)
    invoice_total = extraction.invoice_total_with_vat_tl.value or 0
    raw_energy_tl = extraction.raw_breakdown.energy_total_tl.value if extraction.raw_breakdown and extraction.raw_breakdown.energy_total_tl.value else None
    raw_dist_tl = extraction.raw_breakdown.distribution_total_tl.value if extraction.raw_breakdown and extraction.raw_breakdown.distribution_total_tl.value else None
    raw_btv_tl = extraction.raw_breakdown.btv_tl.value if extraction.raw_breakdown and extraction.raw_breakdown.btv_tl.value else None
    raw_vat_tl = extraction.raw_breakdown.vat_tl.value if extraction.raw_breakdown and extraction.raw_breakdown.vat_tl.value else None
    
    # ═══════════════════════════════════════════════════════════════════════════════
    # PTF/YEKDEM - DÖNEM BAZLI OTOMATİK ÇEK
    # ═══════════════════════════════════════════════════════════════════════════════
    invoice_period = extraction.invoice_period  # "2025-01" format
    
    ptf_tl_per_mwh, yekdem_tl_per_mwh, pricing_source, pricing_error = get_ptf_yekdem_for_period(
        db=db,
        period=invoice_period,
        params=params
    )
    
    # HARD ERROR: Dönem için referans fiyat yoksa hesaplama yapma
    if pricing_error and pricing_source == "not_found":
        raise CalculationError(pricing_error)
    
    # Teklif parametreleri
    ptf_tl_kwh = ptf_tl_per_mwh / 1000
    yekdem_tl_kwh = yekdem_tl_per_mwh / 1000
    agreement_mult = params.agreement_multiplier
    
    # === EK KALEMLER (Tip-5/7: reaktif, mahsuplaşma, etc.) ===
    # NOT: Ek kalemler zaten fatura toplamına dahil, tekrar ekleme!
    extra_items_total = 0.0
    extra_items_labels = []
    if extraction.extra_items:
        for item in extraction.extra_items:
            if item.amount_tl is not None:
                extra_items_total += item.amount_tl
                if item.label:
                    extra_items_labels.append(item.label)
    
    # Teklif için ek kalemler (switch'e göre)
    offer_extra_items = extra_items_total if params.extra_items_apply_to_offer else 0.0
    
    # Extra items note
    extra_items_note = ""
    if extra_items_labels:
        if params.extra_items_apply_to_offer:
            extra_items_note = f"Ek kalemler teklife dahil edildi: {', '.join(extra_items_labels)}"
        else:
            extra_items_note = f"Ek kalemler teklif kapsamı dışındadır: {', '.join(extra_items_labels)}"
    
    # === DAĞITIM BİRİM FİYATI (teklif için) ===
    # Teklif için de aynı EPDK tarifesi kullanılır (dağıtım bedeli değişmez)
    if params.use_offer_distribution and params.offer_distribution_unit_price_tl_per_kwh is not None:
        offer_dist_unit_price = params.offer_distribution_unit_price_tl_per_kwh
    else:
        offer_dist_unit_price = current_dist_unit_price
    
    # === MEVCUT FATURA - FATURADAN OKUNAN DEĞERLER ===
    # Faturadaki gerçek toplam kullanılır, hesaplanmaz!
    # Bu sayede mahsuplaşma, yuvarlama farkları vs. otomatik dahil olur.
    
    # Enerji bedeli: raw_breakdown varsa oradan, yoksa hesapla
    current_energy_tl = raw_energy_tl if raw_energy_tl is not None else (kwh * current_unit_price)
    
    # Dağıtım bedeli: raw_breakdown varsa oradan, yoksa hesapla
    current_distribution_tl = raw_dist_tl if raw_dist_tl is not None else (kwh * current_dist_unit_price)
    
    # Demand bedeli (genelde faturada ayrı gösterilmez, hesapla)
    current_demand_tl = demand_qty * demand_unit_price
    
    # BTV: raw_breakdown varsa oradan, yoksa hesapla
    current_btv_tl = raw_btv_tl if raw_btv_tl is not None else (current_energy_tl * 0.01)
    
    # KDV: raw_breakdown varsa oradan, yoksa hesapla
    current_vat_tl = raw_vat_tl if raw_vat_tl is not None else None
    
    # Mevcut toplam: FATURADAN OKUNAN DEĞER (en güvenilir)
    current_total_with_vat_tl = invoice_total
    
    # KDV matrahı: Toplam - KDV
    if current_vat_tl is not None and current_total_with_vat_tl > 0:
        current_vat_matrah_tl = current_total_with_vat_tl - current_vat_tl
    else:
        # Fallback: hesapla
        current_vat_matrah_tl = current_energy_tl + current_distribution_tl + current_demand_tl + current_btv_tl
        current_vat_tl = current_vat_matrah_tl * 0.20
        # Eğer fatura toplamı yoksa hesaplanan değeri kullan
        if current_total_with_vat_tl == 0:
            current_total_with_vat_tl = current_vat_matrah_tl + current_vat_tl
    
    # === TEKLİF FATURA HESABI ===
    offer_ptf_tl = ptf_tl_kwh * kwh
    # YEKDEM: Faturada YEKDEM varsa teklife dahil et, yoksa dahil etme
    # should_include_yekdem faturadan otomatik tespit edildi
    if should_include_yekdem:
        offer_yekdem_tl = yekdem_tl_kwh * kwh
    else:
        offer_yekdem_tl = 0.0
    offer_energy_base = offer_ptf_tl + offer_yekdem_tl
    offer_energy_tl = offer_energy_base * agreement_mult
    offer_distribution_tl = kwh * offer_dist_unit_price
    offer_demand_tl = demand_qty * demand_unit_price
    offer_btv_tl = offer_energy_tl * 0.01
    # Teklif matrah: ek kalemler switch'e göre
    offer_vat_matrah_tl = offer_energy_tl + offer_distribution_tl + offer_demand_tl + offer_btv_tl + offer_extra_items
    offer_vat_tl = offer_vat_matrah_tl * 0.20
    offer_total_with_vat_tl = offer_vat_matrah_tl + offer_vat_tl
    
    # === FARK VE TASARRUF ===
    difference_excl_vat_tl = current_vat_matrah_tl - offer_vat_matrah_tl
    difference_incl_vat_tl = current_total_with_vat_tl - offer_total_with_vat_tl
    
    savings_ratio = (difference_incl_vat_tl / current_total_with_vat_tl) if current_total_with_vat_tl > 0 else 0
    
    # Aktif enerji birim fiyat tasarrufu
    if should_include_yekdem:
        offer_unit_price = (ptf_tl_kwh + yekdem_tl_kwh) * agreement_mult
    else:
        offer_unit_price = ptf_tl_kwh * agreement_mult
    unit_price_savings_ratio = ((current_unit_price - offer_unit_price) / current_unit_price) if current_unit_price > 0 else 0
    
    # === kWh BAŞI TASARRUF (satış için) ===
    current_total_tl_per_kwh = (current_total_with_vat_tl / kwh) if kwh > 0 else 0
    offer_total_tl_per_kwh = (offer_total_with_vat_tl / kwh) if kwh > 0 else 0
    saving_tl_per_kwh = current_total_tl_per_kwh - offer_total_tl_per_kwh
    
    # === YILLIK PROJEKSİYON (satış için) ===
    annual_saving_tl = difference_incl_vat_tl * 12
    
    # === TOTAL MISMATCH KONTROLÜ (Sprint 8.3 + 8.4) ===
    # Faturadan okunan total vs formülden hesaplanan total karşılaştırması
    # Mismatch varsa INVOICE_TOTAL_MISMATCH flag üretilir
    # Sprint 8.4: Severity escalation (S1/S2) ve OCR_LOCALE_SUSPECT
    computed_current_total = current_vat_matrah_tl + current_vat_tl
    
    # Extraction confidence: en düşük kritik alan confidence'ını al
    extraction_confidence = min(
        extraction.consumption_kwh.confidence if extraction.consumption_kwh else 1.0,
        extraction.invoice_total_with_vat_tl.confidence if extraction.invoice_total_with_vat_tl else 1.0,
    )
    
    total_mismatch_info = check_total_mismatch(
        invoice_total=invoice_total,
        computed_total=computed_current_total,
        extraction_confidence=extraction_confidence,
    )
    
    if total_mismatch_info.has_mismatch:
        logger.warning(
            f"[TOTAL_MISMATCH] severity={total_mismatch_info.severity}, "
            f"invoice_total={invoice_total:.2f}, "
            f"computed_total={computed_current_total:.2f}, "
            f"delta={total_mismatch_info.delta:.2f}, "
            f"ratio={total_mismatch_info.ratio:.2%}"
            + (f", suspect={total_mismatch_info.suspect_reason}" if total_mismatch_info.suspect_reason else "")
        )
    
    return CalculationResult(
        # Mevcut fatura
        current_energy_tl=round(current_energy_tl, 2),
        current_distribution_tl=round(current_distribution_tl, 2),
        current_demand_tl=round(current_demand_tl, 2),
        current_btv_tl=round(current_btv_tl, 2),
        current_vat_matrah_tl=round(current_vat_matrah_tl, 2),
        current_vat_tl=round(current_vat_tl, 2),
        current_total_with_vat_tl=round(current_total_with_vat_tl, 2),
        current_extra_items_tl=round(extra_items_total, 2),
        current_energy_unit_tl_per_kwh=round(current_unit_price, 6),
        current_distribution_unit_tl_per_kwh=round(current_dist_unit_price, 6),
        # Teklif fatura
        offer_ptf_tl=round(offer_ptf_tl, 2),
        offer_yekdem_tl=round(offer_yekdem_tl, 2),
        offer_energy_tl=round(offer_energy_tl, 2),
        offer_distribution_tl=round(offer_distribution_tl, 2),
        offer_demand_tl=round(offer_demand_tl, 2),
        offer_btv_tl=round(offer_btv_tl, 2),
        offer_vat_matrah_tl=round(offer_vat_matrah_tl, 2),
        offer_vat_tl=round(offer_vat_tl, 2),
        offer_total_with_vat_tl=round(offer_total_with_vat_tl, 2),
        offer_energy_unit_tl_per_kwh=round(offer_unit_price, 6),
        offer_distribution_unit_tl_per_kwh=round(offer_dist_unit_price, 6),
        offer_extra_items_tl=round(offer_extra_items, 2),
        extra_items_note=extra_items_note,
        # Fark ve tasarruf
        difference_excl_vat_tl=round(difference_excl_vat_tl, 2),
        difference_incl_vat_tl=round(difference_incl_vat_tl, 2),
        savings_ratio=round(savings_ratio, 4),
        unit_price_savings_ratio=round(unit_price_savings_ratio, 4),
        # kWh başı tasarruf (satış için)
        current_total_tl_per_kwh=round(current_total_tl_per_kwh, 6),
        offer_total_tl_per_kwh=round(offer_total_tl_per_kwh, 6),
        saving_tl_per_kwh=round(saving_tl_per_kwh, 6),
        # Yıllık projeksiyon
        annual_saving_tl=round(annual_saving_tl, 2),
        # Meta
        meta_extra_items_apply_to_offer=params.extra_items_apply_to_offer,
        meta_use_offer_distribution=params.use_offer_distribution,
        meta_include_yekdem_in_offer=should_include_yekdem,  # Faturadan otomatik tespit
        meta_consumption_kwh=round(kwh, 2),
        # Dağıtım kaynağı bilgisi
        meta_distribution_source=distribution_source,
        meta_distribution_tariff_key=tariff_lookup.tariff_key if tariff_lookup.success else None,
        meta_distribution_mismatch_warning=distribution_mismatch_warning,
        # PTF/YEKDEM kaynağı bilgisi
        meta_pricing_source=pricing_source,
        meta_pricing_period=invoice_period,
        meta_ptf_tl_per_mwh=round(ptf_tl_per_mwh, 2),
        meta_yekdem_tl_per_mwh=round(yekdem_tl_per_mwh, 2),
        # Total mismatch bilgisi (Sprint 8.3)
        meta_total_mismatch=total_mismatch_info.has_mismatch,
        meta_total_mismatch_info=total_mismatch_info.to_dict() if total_mismatch_info.has_mismatch else None,
    )
