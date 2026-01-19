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
    severity: Optional[str] = "S2"  # S1, S2, INFO, veya None
    suspect_reason: Optional[str] = None  # OCR_LOCALE_SUSPECT, vb.
    delta_reason_candidate: str = "UNKNOWN"  # ROUNDING, VAT_CALCULATION, ADJUSTMENT, LINE_ITEM_MISSING, EXPLAINABLE_ADJUSTMENT
    
    def to_dict(self) -> dict:
        result = {
            "has_mismatch": self.has_mismatch,
            "invoice_total": round(self.invoice_total, 2),
            "computed_total": round(self.computed_total, 2),
            "delta": round(self.delta, 2),
            "ratio": round(self.ratio, 4),
            "severity": self.severity,
            "delta_reason_candidate": self.delta_reason_candidate,
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
    
    ÖNEMLİ: Faturadaki "Ödenecek Tutar" = SOURCE OF TRUTH
    Computed total sadece telemetri/kontrol amaçlı.
    
    Mismatch Sınıflandırması:
    - ROUNDING: delta <= 2 TL (yuvarlama farkı, beklenen)
    - EXPLAINABLE: delta <= 50 TL (fon/vergi yuvarlama, beklenen)
    - S2: delta > 50 TL veya ratio > %5 (incelenmeli)
    - S1: delta > 500 TL veya (ratio > %20 AND delta > 50) (kritik)
    
    delta_reason_candidate:
    - ROUNDING: Kalem/vergi yuvarlama farkı
    - VAT_CALCULATION: KDV hesaplama farkı
    - ADJUSTMENT: Mahsup/düzeltme/indirim
    - LINE_ITEM_MISSING: Eksik kalem
    - UNKNOWN: Belirsiz
    
    Returns:
        TotalMismatchInfo with mismatch flag, severity, and delta_reason_candidate
    """
    delta = abs(invoice_total - computed_total)
    ratio = delta / max(invoice_total, 0.01)  # Avoid division by zero
    
    # ═══════════════════════════════════════════════════════════════════════
    # Tolerans Bandı (Beklenen Fenomenler)
    # ═══════════════════════════════════════════════════════════════════════
    ROUNDING_TOLERANCE = 2.0  # ±2 TL yuvarlama farkı normal
    EXPLAINABLE_TOLERANCE = 50.0  # ±50 TL fon/vergi yuvarlama
    
    # Delta reason candidate belirleme
    delta_reason_candidate = "UNKNOWN"
    
    if delta <= ROUNDING_TOLERANCE:
        # Yuvarlama farkı - tamamen beklenen
        delta_reason_candidate = "ROUNDING"
        has_mismatch = False
        severity = None
    elif delta <= EXPLAINABLE_TOLERANCE:
        # Açıklanabilir fark - muhtemelen fon/vergi yuvarlama
        delta_reason_candidate = "EXPLAINABLE_ADJUSTMENT"
        has_mismatch = False  # Hard error değil, sadece log
        severity = "INFO"
    else:
        # Gerçek mismatch - incelenmeli
        # Fark türünü tahmin et
        if ratio > 0.15:
            # %15+ fark = muhtemelen eksik kalem veya büyük hata
            delta_reason_candidate = "LINE_ITEM_MISSING"
        elif 0.18 <= ratio <= 0.22:
            # ~%20 fark = muhtemelen KDV hesaplama sorunu
            delta_reason_candidate = "VAT_CALCULATION"
        elif delta < 500:
            # Küçük ama anlamlı fark = muhtemelen mahsup/düzeltme
            delta_reason_candidate = "ADJUSTMENT"
        else:
            delta_reason_candidate = "UNKNOWN"
        
        # S2 mismatch check
        has_mismatch = (ratio >= ratio_threshold) or (delta >= absolute_threshold)
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
        delta_reason_candidate=delta_reason_candidate,
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
    
    # Input değerleri - None kontrolü ile
    kwh = extraction.consumption_kwh.value if extraction.consumption_kwh and extraction.consumption_kwh.value else 0
    current_unit_price = extraction.current_active_unit_price_tl_per_kwh.value if extraction.current_active_unit_price_tl_per_kwh and extraction.current_active_unit_price_tl_per_kwh.value else 0
    demand_qty = extraction.demand_qty.value if extraction.demand_qty and extraction.demand_qty.value else 0
    demand_unit_price = extraction.demand_unit_price_tl_per_unit.value if extraction.demand_unit_price_tl_per_unit and extraction.demand_unit_price_tl_per_unit.value else 0
    
    # ═══════════════════════════════════════════════════════════════════════════════
    # DAĞITIM BİRİM FİYATI - MEVCUT FATURA İÇİN FATURADAN, TEKLİF İÇİN EPDK
    # ═══════════════════════════════════════════════════════════════════════════════
    # MEVCUT FATURA: Faturadan okunan değer kullanılır (gerçek ödenen tutar)
    # TEKLİF: EPDK tarifesi veya manuel override kullanılır
    
    # Faturadan okunan dağıtım birim fiyatı
    extracted_dist_unit_price = extraction.distribution_unit_price_tl_per_kwh.value if extraction.distribution_unit_price_tl_per_kwh and extraction.distribution_unit_price_tl_per_kwh.value else 0
    
    # EPDK tarifesinden hesapla (teklif için)
    tariff_lookup: TariffLookupResult = get_distribution_unit_price_from_extraction(extraction)
    epdk_dist_unit_price = tariff_lookup.unit_price if tariff_lookup.success else None
    
    # MEVCUT FATURA için dağıtım birim fiyatı: FATURADAN OKUNAN DEĞER
    if extracted_dist_unit_price > 0:
        current_dist_unit_price = extracted_dist_unit_price
        distribution_source = "extracted_from_invoice"
        logger.info(f"Mevcut dağıtım birim fiyatı: {current_dist_unit_price:.6f} TL/kWh (faturadan)")
    elif epdk_dist_unit_price is not None:
        current_dist_unit_price = epdk_dist_unit_price
        distribution_source = f"epdk_tariff:{tariff_lookup.tariff_key}"
        logger.warning(f"Mevcut dağıtım birim fiyatı: {current_dist_unit_price:.6f} TL/kWh (EPDK - faturada değer yok)")
    else:
        distribution_source = "not_found"
        error_msg = f"Dağıtım birim fiyatı hesaplanamadı! EPDK tarife lookup: {tariff_lookup.error_message or 'Tarife bilgisi bulunamadı'}"
        logger.error(error_msg)
        raise CalculationError(error_msg)
    
    # TEKLİF için dağıtım birim fiyatı: EPDK veya manuel override
    if params.use_offer_distribution and params.offer_distribution_unit_price_tl_per_kwh is not None:
        offer_dist_unit_price = params.offer_distribution_unit_price_tl_per_kwh
        offer_distribution_source = "manual_override"
    elif epdk_dist_unit_price is not None:
        offer_dist_unit_price = epdk_dist_unit_price
        offer_distribution_source = f"epdk_tariff:{tariff_lookup.tariff_key}"
    else:
        # Fallback: faturadan okunan değer
        offer_dist_unit_price = current_dist_unit_price
        offer_distribution_source = distribution_source
    
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
    
    # Faturadan okunan gerçek değerler (raw_breakdown) - None kontrolü ile
    invoice_total = extraction.invoice_total_with_vat_tl.value if extraction.invoice_total_with_vat_tl and extraction.invoice_total_with_vat_tl.value else 0
    raw_energy_tl = extraction.raw_breakdown.energy_total_tl.value if extraction.raw_breakdown and extraction.raw_breakdown.energy_total_tl and extraction.raw_breakdown.energy_total_tl.value else None
    raw_dist_tl = extraction.raw_breakdown.distribution_total_tl.value if extraction.raw_breakdown and extraction.raw_breakdown.distribution_total_tl and extraction.raw_breakdown.distribution_total_tl.value else None
    raw_btv_tl = extraction.raw_breakdown.btv_tl.value if extraction.raw_breakdown and extraction.raw_breakdown.btv_tl and extraction.raw_breakdown.btv_tl.value else None
    raw_vat_tl = extraction.raw_breakdown.vat_tl.value if extraction.raw_breakdown and extraction.raw_breakdown.vat_tl and extraction.raw_breakdown.vat_tl.value else None
    
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
    # Teklif için EPDK tarifesi veya faturadan okunan değer kullanılır
    # (offer_dist_unit_price yukarıda belirlendi)
    
    # === MEVCUT FATURA - FATURADAN OKUNAN DEĞERLER ===
    # Faturadaki gerçek toplam kullanılır, hesaplanmaz!
    # Bu sayede mahsuplaşma, yuvarlama farkları vs. otomatik dahil olur.
    
    # Line items'dan enerji ve dağıtım toplamlarını hesapla
    line_items_energy_total = 0.0
    line_items_dist_total = 0.0
    if extraction.line_items:
        energy_keywords = ["enerji", "aktif", "sktt", "tüketim", "kademe"]
        dist_keywords = ["dağıtım", "dskb", "elk. dağıtım"]
        for item in extraction.line_items:
            if item.amount_tl:
                label_lower = item.label.lower()
                if any(kw in label_lower for kw in energy_keywords):
                    line_items_energy_total += item.amount_tl
                elif any(kw in label_lower for kw in dist_keywords):
                    line_items_dist_total += item.amount_tl
    
    # Enerji bedeli: Öncelik sırası: raw_breakdown > line_items > hesaplama
    if raw_energy_tl is not None:
        current_energy_tl = raw_energy_tl
    elif line_items_energy_total != 0:
        current_energy_tl = line_items_energy_total
        logger.info(f"Enerji bedeli line_items'dan alındı: {current_energy_tl:.2f} TL")
    else:
        current_energy_tl = kwh * current_unit_price
    
    # Dağıtım bedeli: Öncelik sırası: raw_breakdown > line_items > hesaplama
    if raw_dist_tl is not None:
        current_distribution_tl = raw_dist_tl
    elif line_items_dist_total != 0:
        current_distribution_tl = line_items_dist_total
        logger.info(f"Dağıtım bedeli line_items'dan alındı: {current_distribution_tl:.2f} TL")
    else:
        current_distribution_tl = kwh * current_dist_unit_price
    
    # Demand bedeli (genelde faturada ayrı gösterilmez, hesapla)
    current_demand_tl = demand_qty * demand_unit_price
    
    # BTV: raw_breakdown varsa oradan, yoksa hesapla
    current_btv_tl = raw_btv_tl if raw_btv_tl is not None else (current_energy_tl * 0.01)
    
    # KDV: raw_breakdown varsa oradan, yoksa hesapla
    current_vat_tl = raw_vat_tl if raw_vat_tl is not None else None
    
    # Mevcut toplam: FATURADAN OKUNAN DEĞER (en güvenilir)
    current_total_with_vat_tl = invoice_total
    
    # ═══════════════════════════════════════════════════════════════════════════════
    # FATURA TUTARI - FATURADAN OKUNAN DEĞER KULLANILIR
    # ═══════════════════════════════════════════════════════════════════════════════
    # Faturadan okunan invoice_total KDV DAHİL toplam tutardır.
    # Eğer faturadan okunamadıysa line_items'dan hesapla.
    # ═══════════════════════════════════════════════════════════════════════════════
    
    # Line items'dan KDV hariç toplam hesapla (referans için)
    line_items_subtotal = line_items_energy_total + line_items_dist_total
    
    if current_total_with_vat_tl == 0 and line_items_subtotal != 0:
        # Fatura tutarı okunamadı - line_items'dan hesapla
        # NOT: KDV oranı bilinmiyor, %20 varsayıyoruz
        # Ama bazı faturalarda KDV %0 olabilir (tarımsal, vb.)
        
        # BTV (%1 enerji bedeli üzerinden)
        calculated_btv = abs(line_items_energy_total) * 0.01
        
        # KDV matrahı
        calculated_matrah = line_items_subtotal + calculated_btv
        
        # KDV (%20) - varsayılan
        calculated_vat = calculated_matrah * 0.20
        
        # KDV dahil toplam
        current_total_with_vat_tl = calculated_matrah + calculated_vat
        
        logger.warning(
            f"invoice_total LINE_ITEMS'DAN HESAPLANDI (KDV %20 varsayıldı): "
            f"enerji={line_items_energy_total:.2f} + dağıtım={line_items_dist_total:.2f} + "
            f"btv={calculated_btv:.2f} + kdv={calculated_vat:.2f} = {current_total_with_vat_tl:.2f} TL"
        )
    elif current_total_with_vat_tl > 0:
        # Faturadan okunan değer var - bu KDV DAHİL toplam
        logger.info(f"Fatura tutarı faturadan okundu (KDV dahil): {current_total_with_vat_tl:.2f} TL")
    
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
    
    # === TOTAL MISMATCH KONTROLÜ (Sprint 8.3 + 8.4 + 9) ===
    # Faturadan okunan total vs formülden hesaplanan total karşılaştırması
    # ÖNEMLİ: invoice_total = SOURCE OF TRUTH, computed_total = sadece telemetri
    # Sprint 8.4: Severity escalation (S1/S2) ve OCR_LOCALE_SUSPECT
    # Sprint 9: delta_reason_candidate ve tolerans bandı
    # Sprint 9.1: Akıllı computed_total - faturadan KDV okunmuşsa onu kullan
    
    # computed_total hesaplama stratejisi:
    # 1. Faturadan KDV okunmuşsa: invoice_total - kdv_tl = matrah, sonra matrah + kdv = computed
    # 2. Faturadan KDV okunmamışsa AMA invoice_total güvenilirse: computed = invoice_total
    #    (çünkü invoice_total SOURCE OF TRUTH, line_items'dan hesaplama güvenilmez)
    # 3. Hiçbiri yoksa: line_items'dan hesapla (eski yöntem)
    # Bu sayede "KDV hesaplama farkı" kaynaklı false positive'ler azalır
    
    # invoice_total güvenilir mi? (ROI crop veya pdfplumber'dan geldiyse)
    invoice_total_reliable = (
        extraction.invoice_total_with_vat_tl and 
        extraction.invoice_total_with_vat_tl.confidence >= 0.9 and
        invoice_total > 0
    )
    
    if raw_vat_tl is not None and invoice_total > 0:
        # Faturadan KDV okunmuş - en güvenilir yöntem
        computed_matrah_from_invoice = invoice_total - raw_vat_tl
        computed_current_total = computed_matrah_from_invoice + raw_vat_tl  # = invoice_total
        logger.info(f"TOTAL_MISMATCH: KDV faturadan okundu ({raw_vat_tl:.2f}), computed_total=invoice_total")
    elif invoice_total_reliable:
        # KDV okunmamış ama invoice_total güvenilir (ROI/pdfplumber)
        # Bu durumda computed_total = invoice_total (SOURCE OF TRUTH)
        # Line items'dan hesaplama güvenilmez (eksik kalem, yuvarlama, mahsup olabilir)
        computed_current_total = invoice_total
        conf_val = extraction.invoice_total_with_vat_tl.confidence if extraction.invoice_total_with_vat_tl else 0
        logger.info(f"TOTAL_MISMATCH: invoice_total güvenilir (conf={conf_val:.2f}), computed_total=invoice_total")
    else:
        # Faturadan KDV okunmamış ve invoice_total güvenilmez - line_items'dan hesapla
        computed_current_total = current_vat_matrah_tl + current_vat_tl
        logger.info(f"TOTAL_MISMATCH: KDV hesaplandı, matrah={current_vat_matrah_tl:.2f}, kdv={current_vat_tl:.2f}")
    
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
    
    # Log: Her zaman delta bilgisini logla (telemetri için)
    log_level = "warning" if total_mismatch_info.has_mismatch else "info"
    log_msg = (
        f"[TOTAL_MISMATCH] "
        f"payable_total={invoice_total:.2f}, "
        f"computed_total={computed_current_total:.2f}, "
        f"delta={total_mismatch_info.delta:.2f}, "
        f"ratio={total_mismatch_info.ratio:.2%}, "
        f"reason={total_mismatch_info.delta_reason_candidate}"
    )
    
    if total_mismatch_info.has_mismatch:
        log_msg += f", severity={total_mismatch_info.severity}"
        if total_mismatch_info.suspect_reason:
            log_msg += f", suspect={total_mismatch_info.suspect_reason}"
        logger.warning(log_msg)
    else:
        # Mismatch yok veya tolerans içinde - info level
        logger.info(log_msg)
    
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
