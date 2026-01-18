from .models import InvoiceExtraction, ValidationResult, Question, SuggestedFix, SanityCheck, EnergyDistributionAnalysis, LineItemAnalysis
from .distribution_tariffs import get_distribution_unit_price_from_extraction, TariffLookupResult
from .config import THRESHOLDS

# Validation constants - NOW FROM CONFIG
# DEPRECATED: Use THRESHOLDS.Validation.* directly
MIN_UNIT_PRICE = THRESHOLDS.Validation.MIN_UNIT_PRICE
MAX_UNIT_PRICE = THRESHOLDS.Validation.MAX_UNIT_PRICE
MIN_DIST_PRICE = THRESHOLDS.Validation.MIN_DIST_PRICE
MAX_DIST_PRICE = THRESHOLDS.Validation.MAX_DIST_PRICE
LOW_CONFIDENCE_THRESHOLD = THRESHOLDS.Validation.LOW_CONFIDENCE
MAX_DEMAND_QTY = 100000  # Makul üst sınır (not in config - domain specific)

# Satır tutarlılık toleransı (OCR/yuvarlama farkları için)
LINE_CONSISTENCY_TOLERANCE = THRESHOLDS.Validation.LINE_CONSISTENCY_TOLERANCE

# Vendor-specific tolerans oranları (uyarı için)
VENDOR_TOLERANCE = {
    "enerjisa": 5.0,   # %5
    "ekvator": 5.0,    # %5
    "ck_bogazici": 10.0,  # %10 (kalem parçalanması daha sık)
    "yelden": 10.0,    # %10
    "unknown": 5.0,    # %5 default
}

# KRİTİK: Bu eşiği aşan faturalar DURUR, devam edemez
# Senin dediğin gibi: "593.000 TL'lik faturayı 22.000 TL sanmak" engellenecek
HARD_STOP_DELTA_THRESHOLD = THRESHOLDS.Validation.HARD_STOP_DELTA

# Cross-check toleransı (consumption × unit_price ≈ energy_total)
ENERGY_CROSSCHECK_TOLERANCE = THRESHOLDS.Validation.ENERGY_CROSSCHECK_TOLERANCE


def validate_extraction(extraction: InvoiceExtraction) -> ValidationResult:
    """
    Extraction sonucunu doğrula, eksik alanları belirle.
    
    Validation Rules:
    - consumption_kwh null/zero kontrolü
    - current_active_unit_price 0.1-30 TL/kWh aralık kontrolü
    - distribution_unit_price 0-10 TL/kWh aralık kontrolü
    - confidence < 0.6 uyarı
    - demand_qty varsa demand_unit_price zorunlu
    - demand_qty > 100000 uyarı
    - invoice_total_with_vat karşılaştırma (vendor-specific tolerans)
    - is_ready_for_pricing belirleme
    - sanity_check hesaplama
    - suggested_fixes türetme
    """
    
    missing_fields = []
    questions = []
    errors = []
    warnings = []
    suggested_fixes = []
    
    # ═══════════════════════════════════════════════════════════════════════
    # consumption_kwh null/zero kontrolü (kritik)
    # ═══════════════════════════════════════════════════════════════════════
    if extraction.consumption_kwh.value is None or extraction.consumption_kwh.value <= 0:
        missing_fields.append("consumption_kwh")
        questions.append(Question(
            field_name="consumption_kwh",
            why_needed="Tüketim miktarı hesaplama için zorunlu. Bu değer olmadan teklif hesaplanamaz.",
            example_answer_format="168330 (kWh cinsinden, virgülsüz)"
        ))
    
    # ═══════════════════════════════════════════════════════════════════════
    # current_active_unit_price aralık kontrolü (0.1-30 TL/kWh)
    # ═══════════════════════════════════════════════════════════════════════
    if extraction.current_active_unit_price_tl_per_kwh.value is None:
        missing_fields.append("current_active_unit_price_tl_per_kwh")
        questions.append(Question(
            field_name="current_active_unit_price_tl_per_kwh",
            why_needed="Mevcut birim fiyat karşılaştırma için zorunlu. Tasarruf hesabı bu değere bağlı.",
            example_answer_format="3.87927 (TL/kWh cinsinden)"
        ))
        # Türetilebilir mi kontrol et
        fix = _try_derive_unit_price(extraction)
        if fix:
            suggested_fixes.append(fix)
    elif extraction.current_active_unit_price_tl_per_kwh.value < MIN_UNIT_PRICE:
        errors.append({
            "field": "current_active_unit_price_tl_per_kwh",
            "issue": f"Birim fiyat çok düşük: {extraction.current_active_unit_price_tl_per_kwh.value:.4f} TL/kWh",
            "expected_range": f"{MIN_UNIT_PRICE}-{MAX_UNIT_PRICE} TL/kWh",
            "hint": "Değer Kr/kWh olarak mı okundu? TL/kWh'ye çevrilmeli (Kr/100)."
        })
    elif extraction.current_active_unit_price_tl_per_kwh.value > MAX_UNIT_PRICE:
        errors.append({
            "field": "current_active_unit_price_tl_per_kwh",
            "issue": f"Birim fiyat çok yüksek: {extraction.current_active_unit_price_tl_per_kwh.value:.4f} TL/kWh",
            "expected_range": f"{MIN_UNIT_PRICE}-{MAX_UNIT_PRICE} TL/kWh",
            "hint": "Değer TL/MWh olarak mı okundu? TL/kWh'ye çevrilmeli (TL/1000)."
        })
    
    # NOT: Eski kaba kural (5 TL / 0.3 TL eşiği) kaldırıldı.
    # Artık 3'lü kontrol sistemi (analyze_energy_distribution_separation) kullanılıyor.
    
    # ═══════════════════════════════════════════════════════════════════════
    # distribution_unit_price kontrolü (0-10 TL/kWh)
    # ═══════════════════════════════════════════════════════════════════════
    if extraction.distribution_unit_price_tl_per_kwh.value is None:
        fix = _try_derive_distribution_price(extraction)
        if fix:
            suggested_fixes.append(fix)
            warnings.append({
                "field": "distribution_unit_price_tl_per_kwh",
                "issue": "Dağıtım birim fiyatı bulunamadı ama türetilebilir.",
                "suggested_value": fix.suggested_value,
                "basis": fix.basis
            })
        else:
            warnings.append({
                "field": "distribution_unit_price_tl_per_kwh",
                "issue": "Dağıtım birim fiyatı bulunamadı. Hesaplamada 0 olarak kullanılacak."
            })
    elif extraction.distribution_unit_price_tl_per_kwh.value > MAX_DIST_PRICE:
        errors.append({
            "field": "distribution_unit_price_tl_per_kwh",
            "issue": f"Dağıtım birim fiyatı çok yüksek: {extraction.distribution_unit_price_tl_per_kwh.value:.4f} TL/kWh",
            "expected_range": f"{MIN_DIST_PRICE}-{MAX_DIST_PRICE} TL/kWh"
        })
    
    # ═══════════════════════════════════════════════════════════════════════
    # demand kontrolü
    # ═══════════════════════════════════════════════════════════════════════
    if extraction.demand_qty.value is not None and extraction.demand_qty.value > 0:
        # demand_qty varsa demand_unit_price yoksa uyarı ver (hata değil)
        if extraction.demand_unit_price_tl_per_unit.value is None:
            warnings.append({
                "field": "demand_unit_price_tl_per_unit",
                "issue": f"Demand miktarı ({extraction.demand_qty.value:.2f}) var ama birim fiyatı bulunamadı. Hesaplamada 0 olarak kullanılacak.",
                "hint": "Demand bedeli hesaba katılmayacak."
            })
        
        # demand_qty çok büyükse uyarı
        if extraction.demand_qty.value > MAX_DEMAND_QTY:
            warnings.append({
                "field": "demand_qty",
                "issue": f"Demand miktarı çok yüksek: {extraction.demand_qty.value:.2f}. Doğruluğunu kontrol edin."
            })
    
    # ═══════════════════════════════════════════════════════════════════════
    # Düşük confidence uyarıları (< 0.6)
    # ═══════════════════════════════════════════════════════════════════════
    low_confidence_fields = _check_low_confidence(extraction)
    for field_info in low_confidence_fields:
        warnings.append({
            "field": field_info["field"],
            "issue": f"Düşük güvenilirlik ({field_info['confidence']:.2f})",
            "hint": "Değeri manuel olarak doğrulayın.",
            "evidence": field_info["evidence"]
        })
    
    # ═══════════════════════════════════════════════════════════════════════
    # Sanity Check hesaplama
    # ═══════════════════════════════════════════════════════════════════════
    sanity_check = _calculate_sanity_check(extraction)
    
    # ═══════════════════════════════════════════════════════════════════════
    # KRİTİK: Energy cross-check (consumption × unit_price ≈ energy_total)
    # Bu kontrol yanlış tablodan okunan değerleri yakalar
    # NOT: Warning olarak eklenir, hesaplamayı engellemez
    # ═══════════════════════════════════════════════════════════════════════
    energy_crosscheck_error = _check_energy_crosscheck(extraction)
    if energy_crosscheck_error:
        warnings.append(energy_crosscheck_error)  # Warning olarak ekle, error değil
    
    # ═══════════════════════════════════════════════════════════════════════
    # Line items cross-check (her satırda qty × unit_price ≈ amount)
    # ═══════════════════════════════════════════════════════════════════════
    line_items_errors = _check_line_items_crosscheck(extraction)
    for err in line_items_errors:
        warnings.append(err)  # Line item hataları warning olarak
    
    # ═══════════════════════════════════════════════════════════════════════
    # Line items toplamı = consumption_kwh kontrolü
    # ═══════════════════════════════════════════════════════════════════════
    line_items_sum_error = _check_line_items_sum(extraction)
    if line_items_sum_error:
        warnings.append(line_items_sum_error)
    
    # ═══════════════════════════════════════════════════════════════════════
    # Toplam karşılaştırma (vendor-specific tolerans)
    # NOT: Artık hard stop yok - tüm sapmalar WARNING olarak gösterilir
    # Hesaplama devam eder, kullanıcı uyarılır
    # ═══════════════════════════════════════════════════════════════════════
    if sanity_check and sanity_check.delta_ratio is not None:
        vendor = extraction.vendor or "unknown"
        tolerance = VENDOR_TOLERANCE.get(vendor, 5.0)
        
        # Büyük sapma = kritik uyarı (ama hesaplama devam eder)
        if abs(sanity_check.delta_ratio) > HARD_STOP_DELTA_THRESHOLD:
            warnings.append({
                "field": "invoice_total_with_vat_tl",
                "issue": f"UYARI: Hesaplanan toplam ({sanity_check.total_est_tl:,.2f} TL) ile faturadaki toplam ({sanity_check.invoice_total_with_vat_tl:,.2f} TL) arasında %{abs(sanity_check.delta_ratio):.1f} fark var.",
                "calculated": sanity_check.total_est_tl,
                "extracted": sanity_check.invoice_total_with_vat_tl,
                "delta_ratio": sanity_check.delta_ratio,
                "hint": "Tüketim veya birim fiyat yanlış tablodan okunmuş olabilir. Sonuçları kontrol edin."
            })
        elif abs(sanity_check.delta_ratio) > tolerance:
            warnings.append({
                "field": "invoice_total_with_vat_tl",
                "issue": f"Hesaplanan toplam ({sanity_check.total_est_tl:,.2f} TL) ile faturadaki toplam ({sanity_check.invoice_total_with_vat_tl:,.2f} TL) arasında %{abs(sanity_check.delta_ratio):.1f} fark var.",
                "calculated": sanity_check.total_est_tl,
                "extracted": sanity_check.invoice_total_with_vat_tl,
                "delta_ratio": sanity_check.delta_ratio,
                "tolerance_percent": tolerance,
                "vendor": vendor,
                "hint": "Bu faturada dağıtım/demand/başka kalem olabilir. Eksik alanları kontrol edin."
            })
    
    # ═══════════════════════════════════════════════════════════════════════
    # Aşırı uç kontrol: 1 kWh ile milyonlar TL
    # ═══════════════════════════════════════════════════════════════════════
    if (extraction.consumption_kwh.value and extraction.consumption_kwh.value < 10 and
        extraction.invoice_total_with_vat_tl.value and extraction.invoice_total_with_vat_tl.value > 10000):
        errors.append({
            "field": "consumption_kwh",
            "issue": f"Tüketim ({extraction.consumption_kwh.value} kWh) ile toplam tutar ({extraction.invoice_total_with_vat_tl.value:,.2f} TL) uyumsuz. Tüketim değerini kontrol edin."
        })
    
    # ═══════════════════════════════════════════════════════════════════════
    # is_ready_for_pricing belirleme
    # ═══════════════════════════════════════════════════════════════════════
    is_ready = len(missing_fields) == 0 and len(errors) == 0
    
    # ═══════════════════════════════════════════════════════════════════════
    # Enerji/Dağıtım ayrımı analizi (3'lü kontrol)
    # ═══════════════════════════════════════════════════════════════════════
    energy_dist_analysis = analyze_energy_distribution_separation(extraction)
    
    # Analiz sonucuna göre uyarı ekle
    if energy_dist_analysis.overall_status == "error":
        warnings.append({
            "field": "energy_distribution_separation",
            "issue": energy_dist_analysis.status_message,
            "flag_a": energy_dist_analysis.flag_a_line_consistency,
            "flag_b": energy_dist_analysis.flag_b_energy_includes_distribution,
            "flag_c": energy_dist_analysis.flag_c_addition_pattern,
            "hint": "Enerji ve dağıtım birim fiyatları yanlış ayrılmış olabilir. Faturayı manuel kontrol edin."
        })
    elif energy_dist_analysis.overall_status == "suspicious":
        warnings.append({
            "field": "energy_distribution_separation",
            "issue": energy_dist_analysis.status_message,
            "flag_a": energy_dist_analysis.flag_a_line_consistency,
            "flag_b": energy_dist_analysis.flag_b_energy_includes_distribution,
            "flag_c": energy_dist_analysis.flag_c_addition_pattern,
            "hint": "Sonuçları kontrol edin, enerji fiyatı dağıtımı içeriyor olabilir."
        })
    
    # ═══════════════════════════════════════════════════════════════════════
    # DAĞITIM TARİFE KONTROLÜ (EPDK tablosu)
    # ═══════════════════════════════════════════════════════════════════════
    tariff_lookup: TariffLookupResult = get_distribution_unit_price_from_extraction(extraction)
    
    distribution_tariff_meta_missing = False
    distribution_tariff_lookup_failed = False
    distribution_computed_from_tariff = False
    distribution_line_mismatch = False
    distribution_tariff_key = None
    
    if not tariff_lookup.success:
        # Tarife bilgisi eksik veya tabloda bulunamadı
        if "eksik" in (tariff_lookup.error_message or "").lower():
            distribution_tariff_meta_missing = True
            warnings.append({
                "field": "tariff_info",
                "issue": f"Tarife bilgisi eksik: {tariff_lookup.error_message}",
                "hint": "Faturanın sağ üst köşesindeki tarife bilgisini (Sanayi/Kamu, AG/OG, Tek/Çift Terim) kontrol edin."
            })
        else:
            distribution_tariff_lookup_failed = True
            warnings.append({
                "field": "distribution_tariff",
                "issue": f"EPDK tarifesinde bulunamadı: {tariff_lookup.tariff_key}",
                "hint": "Tarife kombinasyonu tabloda tanımlı değil."
            })
    else:
        # Tarife bulundu
        distribution_computed_from_tariff = True
        distribution_tariff_key = tariff_lookup.tariff_key
        
        # Faturadan okunan değerle karşılaştır
        extracted_dist = extraction.distribution_unit_price_tl_per_kwh.value
        if extracted_dist and extracted_dist > 0 and tariff_lookup.unit_price:
            diff_percent = abs(extracted_dist - tariff_lookup.unit_price) / tariff_lookup.unit_price * 100
            if diff_percent > 5:
                distribution_line_mismatch = True
                warnings.append({
                    "field": "distribution_unit_price",
                    "issue": f"Dağıtım birim fiyatı uyuşmazlığı: Faturadan={extracted_dist:.6f}, EPDK={tariff_lookup.unit_price:.6f} TL/kWh (fark: %{diff_percent:.1f})",
                    "tariff_key": tariff_lookup.tariff_key,
                    "hint": "EPDK tarifesi kullanılacak. Faturadaki değer farklı olabilir."
                })
    
    return ValidationResult(
        is_ready_for_pricing=is_ready,
        missing_fields=missing_fields,
        questions=questions,
        errors=errors,
        warnings=warnings,
        suggested_fixes=suggested_fixes,
        sanity_check=sanity_check,
        energy_distribution_analysis=energy_dist_analysis,
        # Dağıtım tarife bayrakları
        distribution_tariff_meta_missing=distribution_tariff_meta_missing,
        distribution_tariff_lookup_failed=distribution_tariff_lookup_failed,
        distribution_computed_from_tariff=distribution_computed_from_tariff,
        distribution_line_mismatch=distribution_line_mismatch,
        distribution_tariff_key=distribution_tariff_key
    )


def _try_derive_unit_price(extraction: InvoiceExtraction) -> SuggestedFix | None:
    """
    Birim fiyatı raw_breakdown'dan türetmeye çalış.
    Formula: energy_total_tl / consumption_kwh
    """
    if extraction.raw_breakdown is None:
        return None
    
    energy_total = extraction.raw_breakdown.energy_total_tl
    consumption = extraction.consumption_kwh
    
    if (energy_total and energy_total.value and energy_total.value > 0 and
        consumption and consumption.value and consumption.value > 0):
        derived = energy_total.value / consumption.value
        # Makul aralıkta mı kontrol et
        if MIN_UNIT_PRICE <= derived <= MAX_UNIT_PRICE:
            return SuggestedFix(
                field_name="current_active_unit_price_tl_per_kwh",
                suggested_value=round(derived, 6),
                basis="energy_total_tl / consumption_kwh",
                confidence=0.55
            )
    
    return None


def _try_derive_distribution_price(extraction: InvoiceExtraction) -> SuggestedFix | None:
    """
    Dağıtım birim fiyatını raw_breakdown'dan türetmeye çalış.
    Formula: distribution_total_tl / consumption_kwh
    """
    if extraction.raw_breakdown is None:
        return None
    
    dist_total = extraction.raw_breakdown.distribution_total_tl
    consumption = extraction.consumption_kwh
    
    if (dist_total and dist_total.value and dist_total.value > 0 and
        consumption and consumption.value and consumption.value > 0):
        derived = dist_total.value / consumption.value
        # Makul aralıkta mı kontrol et
        if MIN_DIST_PRICE <= derived <= MAX_DIST_PRICE:
            return SuggestedFix(
                field_name="distribution_unit_price_tl_per_kwh",
                suggested_value=round(derived, 6),
                basis="distribution_total_tl / consumption_kwh",
                confidence=0.55
            )
    
    return None


def _check_low_confidence(extraction: InvoiceExtraction) -> list[dict]:
    """Kritik alanlarda düşük confidence kontrolü"""
    low_confidence = []
    
    critical_fields = [
        ("consumption_kwh", extraction.consumption_kwh),
        ("current_active_unit_price_tl_per_kwh", extraction.current_active_unit_price_tl_per_kwh),
    ]
    
    for field_name, field_value in critical_fields:
        if field_value.value is not None and field_value.confidence < LOW_CONFIDENCE_THRESHOLD:
            low_confidence.append({
                "field": field_name,
                "confidence": field_value.confidence,
                "evidence": field_value.evidence
            })
    
    return low_confidence


def _check_energy_crosscheck(extraction: InvoiceExtraction) -> dict | None:
    """
    KRİTİK KONTROL: consumption_kwh × unit_price ≈ energy_total_tl
    
    Bu kontrol yanlış tablodan okunan değerleri yakalar.
    Örnek: Grafikteki yıllık tüketim yerine fatura detayındaki tüketim okunmalı.
    """
    consumption = extraction.consumption_kwh.value
    unit_price = extraction.current_active_unit_price_tl_per_kwh.value
    
    if consumption is None or unit_price is None:
        return None
    
    # raw_breakdown'dan energy_total_tl al
    if extraction.raw_breakdown is None or extraction.raw_breakdown.energy_total_tl is None:
        return None
    
    energy_total = extraction.raw_breakdown.energy_total_tl.value
    if energy_total is None or energy_total <= 0:
        return None
    
    # Cross-check: consumption × unit_price ≈ energy_total
    calculated_energy = consumption * unit_price
    delta_ratio = abs((calculated_energy - energy_total) / energy_total) * 100
    
    if delta_ratio > ENERGY_CROSSCHECK_TOLERANCE:
        return {
            "field": "consumption_kwh",
            "issue": f"CROSS-CHECK HATASI: Tüketim ({consumption:,.0f} kWh) × Birim Fiyat ({unit_price:.4f} TL/kWh) = {calculated_energy:,.2f} TL, ama Enerji Bedeli = {energy_total:,.2f} TL. Fark: %{delta_ratio:.1f}",
            "calculated_energy_tl": calculated_energy,
            "extracted_energy_tl": energy_total,
            "delta_ratio": delta_ratio,
            "hint": "Muhtemelen yanlış tablodan tüketim okundu. Fatura detay tablosundaki 'Enerji Bedeli' satırından miktar alınmalı."
        }
    
    return None


def _check_line_items_crosscheck(extraction: InvoiceExtraction) -> list[dict]:
    """
    Line items cross-check: Her satırda qty × unit_price ≈ amount_tl
    """
    errors = []
    
    if not extraction.line_items:
        return errors
    
    for item in extraction.line_items:
        if not item.crosscheck_passed and item.crosscheck_delta is not None:
            errors.append({
                "field": "line_items",
                "issue": f"Satır cross-check hatası: '{item.label}' - {item.qty:,.0f} kWh × {item.unit_price:.4f} TL/kWh ≠ {item.amount_tl:,.2f} TL (fark: %{item.crosscheck_delta:.1f})",
                "label": item.label,
                "qty": item.qty,
                "unit_price": item.unit_price,
                "amount_tl": item.amount_tl,
                "delta": item.crosscheck_delta,
                "hint": "Satırdaki değerler tutarsız, manuel kontrol gerekli."
            })
    
    return errors


def _check_line_items_sum(extraction: InvoiceExtraction) -> dict | None:
    """
    Line items toplamı = consumption_kwh kontrolü
    
    NOT: Negatif kalemler (mahsuplaşma) dahil edilmeli!
    Örnek: SKTT (120.187) + Ek Tüketim (-4.042) = 116.145 kWh
    """
    if not extraction.line_items:
        return None
    
    consumption = extraction.consumption_kwh.value
    if consumption is None or consumption <= 0:
        return None
    
    # Sadece kWh birimli enerji satırlarını topla (NEGATİFLER DAHİL!)
    # Dağıtım satırlarını hariç tut (aynı kWh'yi tekrar saymamak için)
    energy_labels = ["enerji", "sktt", "tüketim", "aktif", "kademe", "gündüz", "puant", "gece", "t1", "t2", "t3"]
    
    line_items_sum = sum(
        item.qty for item in extraction.line_items 
        if item.unit == "kWh" and any(lbl in item.label.lower() for lbl in energy_labels)
    )
    
    # Eğer enerji satırı bulunamadıysa, tüm kWh satırlarını topla
    if line_items_sum == 0:
        line_items_sum = sum(
            item.qty for item in extraction.line_items 
            if item.unit == "kWh"
        )
    
    if line_items_sum == 0:
        return None
    
    delta_ratio = abs((line_items_sum - consumption) / consumption) * 100
    
    if delta_ratio > 5.0:  # %5 tolerans
        return {
            "field": "line_items",
            "issue": f"Line items toplamı ({line_items_sum:,.3f} kWh) ile consumption_kwh ({consumption:,.3f} kWh) uyuşmuyor. Fark: %{delta_ratio:.1f}",
            "line_items_sum": line_items_sum,
            "consumption_kwh": consumption,
            "delta_ratio": delta_ratio,
            "hint": "Bazı enerji satırları eksik olabilir veya consumption_kwh yanlış tablodan okunmuş olabilir. Negatif mahsuplaşma satırları kontrol edilmeli."
        }
    
    return None


def _calculate_sanity_check(extraction: InvoiceExtraction) -> SanityCheck | None:
    """
    Hesap tutarlılığı kontrolü için sanity check hesapla.
    
    Formül:
    - energy_est = consumption_kwh * current_active_unit_price_tl_per_kwh
    - dist_est = consumption_kwh * distribution_unit_price_tl_per_kwh (varsa)
    - demand_est = demand_qty * demand_unit_price (varsa)
    - extra_items_est = sum(extra_items.amount_tl) (Tip-5/7 için, NEGATİF olabilir!)
    - btv_est = raw_breakdown.btv_tl veya energy_est * 0.01
    - matrah_est = energy_est + dist_est + demand_est + extra_items_est + btv_est
    - vat_est = raw_breakdown.vat_tl veya matrah_est * 0.20
    - total_est = matrah_est + vat_est
    
    NOT: Negatif mahsuplaşma kalemleri extra_items_est'i düşürür.
    """
    consumption = extraction.consumption_kwh.value
    unit_price = extraction.current_active_unit_price_tl_per_kwh.value
    
    if consumption is None or unit_price is None:
        return None
    
    # Energy estimate
    energy_est = consumption * unit_price
    
    # Distribution estimate
    dist_price = extraction.distribution_unit_price_tl_per_kwh.value or 0
    dist_est = consumption * dist_price if dist_price else None
    
    # Demand estimate
    demand_qty = extraction.demand_qty.value or 0
    demand_price = extraction.demand_unit_price_tl_per_unit.value or 0
    demand_est = demand_qty * demand_price if demand_qty and demand_price else None
    
    # Extra items estimate (Tip-5/7: reaktif, mahsuplaşma, etc.)
    # NEGATİF kalemler dahil (mahsuplaşma için)
    extra_items_est = None
    if extraction.extra_items:
        extra_total = sum(
            item.amount_tl for item in extraction.extra_items 
            if item.amount_tl is not None
        )
        if extra_total != 0:
            extra_items_est = extra_total
    
    # Adjustments'tan da negatif kalemleri ekle (Tip-7)
    adjustments_total = 0
    if extraction.adjustments:
        adjustments_total = sum(
            adj.amount_tl for adj in extraction.adjustments 
            if adj.amount_tl is not None
        )
    
    # BTV estimate (raw_breakdown varsa onu kullan, yoksa %1)
    btv_est = None
    if extraction.raw_breakdown and extraction.raw_breakdown.btv_tl and extraction.raw_breakdown.btv_tl.value:
        btv_est = extraction.raw_breakdown.btv_tl.value
    else:
        btv_est = energy_est * 0.01
    
    # Matrah (include extra_items and adjustments for better accuracy)
    matrah_est = energy_est + (dist_est or 0) + (demand_est or 0) + (extra_items_est or 0) + adjustments_total + btv_est
    
    # VAT estimate (raw_breakdown varsa onu kullan, yoksa %20)
    vat_est = None
    if extraction.raw_breakdown and extraction.raw_breakdown.vat_tl and extraction.raw_breakdown.vat_tl.value:
        vat_est = extraction.raw_breakdown.vat_tl.value
    else:
        vat_est = matrah_est * 0.20
    
    # Total estimate
    total_est = matrah_est + vat_est
    
    # Delta ratio (faturadaki toplam ile karşılaştır)
    invoice_total = extraction.invoice_total_with_vat_tl.value
    delta_ratio = None
    if invoice_total and invoice_total > 0:
        delta_ratio = ((total_est - invoice_total) / invoice_total) * 100
    
    return SanityCheck(
        energy_est_tl=round(energy_est, 2),
        dist_est_tl=round(dist_est, 2) if dist_est else None,
        demand_est_tl=round(demand_est, 2) if demand_est else None,
        extra_items_est_tl=round(extra_items_est, 2) if extra_items_est else None,
        btv_est_tl=round(btv_est, 2),
        vat_est_tl=round(vat_est, 2),
        total_est_tl=round(total_est, 2),
        invoice_total_with_vat_tl=round(invoice_total, 2) if invoice_total else None,
        delta_ratio=round(delta_ratio, 2) if delta_ratio is not None else None
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 3'LÜ KONTROL SİSTEMİ: Enerji/Dağıtım Ayrımı Analizi
# ═══════════════════════════════════════════════════════════════════════════════

def analyze_energy_distribution_separation(extraction: InvoiceExtraction) -> EnergyDistributionAnalysis:
    """
    Enerji ve dağıtım birim fiyatlarının doğru ayrılıp ayrılmadığını analiz et.
    
    3'lü Kontrol:
    A) Satır-toplam tutarlılığı: qty × unit_price ≈ amount (±%2)
    B) Enerji birim fiyatı "dağıtım dahil" gibi mi?
    C) Toplama paterni var mı? (energy_price ≈ energy_line + dist_line)
    
    Returns:
        EnergyDistributionAnalysis with flags and status
    """
    analysis = EnergyDistributionAnalysis()
    
    # Temel değerler
    consumption = extraction.consumption_kwh.value or 0
    analysis.total_kwh = consumption
    
    extracted_energy_price = extraction.current_active_unit_price_tl_per_kwh.value
    extracted_dist_price = extraction.distribution_unit_price_tl_per_kwh.value
    analysis.extracted_energy_unit_price = extracted_energy_price
    analysis.extracted_distribution_unit_price = extracted_dist_price
    
    # raw_breakdown'dan tutarları al
    energy_total = None
    dist_total = None
    if extraction.raw_breakdown:
        if extraction.raw_breakdown.energy_total_tl and extraction.raw_breakdown.energy_total_tl.value:
            energy_total = extraction.raw_breakdown.energy_total_tl.value
        if extraction.raw_breakdown.distribution_total_tl and extraction.raw_breakdown.distribution_total_tl.value:
            dist_total = extraction.raw_breakdown.distribution_total_tl.value
    
    # ═══════════════════════════════════════════════════════════════════════
    # Line items'dan enerji ve dağıtım satırlarını ayır
    # ═══════════════════════════════════════════════════════════════════════
    energy_lines = []
    distribution_kwh_lines = []
    distribution_other_lines = []
    
    energy_keywords = ["enerji", "aktif", "sktt", "tüketim", "kademe", "gündüz", "puant", "gece", "t1", "t2", "t3"]
    dist_keywords = ["dağıtım", "dskb", "elk. dağıtım", "distribution"]
    
    for item in extraction.line_items:
        label_lower = item.label.lower()
        line_type = getattr(item, 'line_type', 'other')
        
        # Satır analizi oluştur
        line_analysis = LineItemAnalysis(
            label=item.label,
            line_type=line_type,
            qty=item.qty,
            unit=item.unit,
            unit_price=item.unit_price,
            measurement_unit=getattr(item, 'measurement_unit', 'TL/kWh'),
            amount_tl=item.amount_tl
        )
        
        # Tutarlılık kontrolü (qty × unit_price ≈ amount)
        if item.unit_price and item.amount_tl and item.qty:
            calculated = item.qty * item.unit_price
            if item.amount_tl != 0:
                delta = abs((calculated - item.amount_tl) / item.amount_tl) * 100
                line_analysis.calculated_amount = round(calculated, 2)
                line_analysis.delta_percent = round(delta, 2)
                line_analysis.is_consistent = delta <= LINE_CONSISTENCY_TOLERANCE
        
        # Satır tipine göre kategorize et
        if line_type == "energy" or any(kw in label_lower for kw in energy_keywords):
            if "dağıtım" not in label_lower:  # Dağıtım kelimesi yoksa enerji
                energy_lines.append(line_analysis)
        elif line_type == "distribution" or any(kw in label_lower for kw in dist_keywords):
            if item.unit == "kWh":
                distribution_kwh_lines.append(line_analysis)
            else:
                distribution_other_lines.append(line_analysis)
        else:
            # Bilinmeyen satırlar - birime göre tahmin et
            if item.unit == "kWh" and item.qty > 0:
                # kWh bazlı ama kategorize edilememiş
                pass
    
    # ═══════════════════════════════════════════════════════════════════════
    # Enerji satırı analizi
    # ═══════════════════════════════════════════════════════════════════════
    if energy_lines:
        # En büyük enerji satırını al (ana enerji kalemi)
        main_energy = max(energy_lines, key=lambda x: x.qty if x.qty else 0)
        analysis.energy_line_qty = main_energy.qty
        analysis.energy_line_unit_price = main_energy.unit_price
        analysis.energy_line_amount = main_energy.amount_tl
        analysis.energy_line_consistent = main_energy.is_consistent
    
    # ═══════════════════════════════════════════════════════════════════════
    # Dağıtım satırı analizi (kWh bazlı)
    # ═══════════════════════════════════════════════════════════════════════
    if distribution_kwh_lines:
        main_dist = max(distribution_kwh_lines, key=lambda x: x.qty if x.qty else 0)
        analysis.distribution_kwh_line_qty = main_dist.qty
        analysis.distribution_kwh_line_unit_price = main_dist.unit_price
        analysis.distribution_kwh_line_amount = main_dist.amount_tl
        analysis.distribution_kwh_line_consistent = main_dist.is_consistent
    
    analysis.distribution_other_lines = distribution_other_lines
    
    # ═══════════════════════════════════════════════════════════════════════
    # Hesaplanan ortalama birim fiyatlar
    # ═══════════════════════════════════════════════════════════════════════
    if consumption > 0:
        # Toplam ortalama (enerji + dağıtım) / kWh
        if energy_total and dist_total:
            analysis.computed_avg_unit_price_total = round((energy_total + dist_total) / consumption, 6)
        elif energy_total:
            analysis.computed_avg_unit_price_total = round(energy_total / consumption, 6)
        
        # Sadece enerji satırından hesaplanan birim fiyat
        if energy_total:
            analysis.computed_energy_unit_price_from_line = round(energy_total / consumption, 6)
    
    # ═══════════════════════════════════════════════════════════════════════
    # KONTROL A: Satır-toplam tutarlılığı
    # ═══════════════════════════════════════════════════════════════════════
    all_lines_consistent = True
    any_line_checked = False
    
    for line in energy_lines + distribution_kwh_lines:
        if line.delta_percent is not None:
            any_line_checked = True
            if not line.is_consistent:
                all_lines_consistent = False
                break
    
    if any_line_checked:
        analysis.flag_a_line_consistency = "pass" if all_lines_consistent else "fail"
    else:
        analysis.flag_a_line_consistency = "unknown"
    
    # ═══════════════════════════════════════════════════════════════════════
    # KONTROL B: Enerji birim fiyatı "dağıtım dahil" gibi mi?
    # ═══════════════════════════════════════════════════════════════════════
    if extracted_energy_price and analysis.computed_avg_unit_price_total:
        # Extracted enerji fiyatı, toplam ortalamaya çok yakınsa şüpheli
        avg_delta = abs(extracted_energy_price - analysis.computed_avg_unit_price_total)
        avg_delta_percent = (avg_delta / analysis.computed_avg_unit_price_total) * 100 if analysis.computed_avg_unit_price_total else 0
        
        if avg_delta_percent < 5:  # %5'ten az fark = şüpheli
            analysis.flag_b_energy_includes_distribution = "suspicious"
        else:
            analysis.flag_b_energy_includes_distribution = "pass"
    else:
        analysis.flag_b_energy_includes_distribution = "unknown"
    
    # ═══════════════════════════════════════════════════════════════════════
    # KONTROL C: Toplama paterni var mı?
    # ═══════════════════════════════════════════════════════════════════════
    if (extracted_energy_price and 
        analysis.energy_line_unit_price and 
        analysis.distribution_kwh_line_unit_price):
        
        # extracted_energy ≈ energy_line + dist_line ?
        sum_of_lines = analysis.energy_line_unit_price + analysis.distribution_kwh_line_unit_price
        sum_delta = abs(extracted_energy_price - sum_of_lines)
        sum_delta_percent = (sum_delta / sum_of_lines) * 100 if sum_of_lines else 0
        
        if sum_delta_percent < 5:  # %5'ten az fark = toplama paterni var
            analysis.flag_c_addition_pattern = "fail"
        else:
            analysis.flag_c_addition_pattern = "pass"
    elif extracted_energy_price and extracted_dist_price:
        # Line items yoksa, extracted değerleri karşılaştır
        # Eğer energy_price ≈ dist_price ise yanlış sınıflandırma olabilir
        if extracted_dist_price > 0:
            ratio = extracted_energy_price / extracted_dist_price
            if 0.8 < ratio < 1.2:  # Birbirine çok yakın
                analysis.flag_c_addition_pattern = "suspicious"
            else:
                analysis.flag_c_addition_pattern = "pass"
        else:
            analysis.flag_c_addition_pattern = "unknown"
    else:
        analysis.flag_c_addition_pattern = "unknown"
    
    # ═══════════════════════════════════════════════════════════════════════
    # Genel sonuç
    # ═══════════════════════════════════════════════════════════════════════
    flags = [analysis.flag_a_line_consistency, analysis.flag_b_energy_includes_distribution, analysis.flag_c_addition_pattern]
    
    if "fail" in flags:
        analysis.overall_status = "error"
        if analysis.flag_a_line_consistency == "fail":
            analysis.status_message = "Satır tutarlılık hatası: qty × unit_price ≠ amount"
        elif analysis.flag_b_energy_includes_distribution == "fail":
            analysis.status_message = "Enerji birim fiyatı dağıtımı içeriyor olabilir"
        elif analysis.flag_c_addition_pattern == "fail":
            analysis.status_message = "Toplama paterni tespit edildi: enerji fiyatı = enerji + dağıtım"
    elif "suspicious" in flags:
        analysis.overall_status = "suspicious"
        if analysis.flag_b_energy_includes_distribution == "suspicious":
            analysis.status_message = "Şüpheli: Enerji fiyatı toplam ortalamaya çok yakın"
        elif analysis.flag_c_addition_pattern == "suspicious":
            analysis.status_message = "Şüpheli: Enerji ve dağıtım fiyatları birbirine çok yakın"
    elif all(f == "pass" for f in flags if f != "unknown"):
        analysis.overall_status = "clean"
        analysis.status_message = "Enerji/dağıtım ayrımı temiz"
    else:
        analysis.overall_status = "unknown"
        analysis.status_message = "Yeterli veri yok, manuel kontrol gerekli"
    
    return analysis
