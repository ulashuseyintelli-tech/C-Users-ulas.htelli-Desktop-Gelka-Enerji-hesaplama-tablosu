"""
CK Boğaziçi faturası ROI crop testi.

Bu test:
1. CK faturasını /full-process endpoint'ine gönderir
2. ROI crop'un çalışıp çalışmadığını kontrol eder
3. Ödenecek Tutar değerini doğrular

Beklenen değerler (faturadan):
- Ödenecek Tutar: 593.740,00 TL (KDV DAHİL)
- KDV: 98.956,24 TL
- Tüketim: 116.145,630 kWh
"""

import requests
import json
from pathlib import Path
import os

API_URL = "http://localhost:8001"

# Fatura dosyasını bul
FATURA_DIR = Path("Fatura örnekler")
CK_INVOICE_PATH = None

for f in FATURA_DIR.iterdir():
    if "BBE" in f.name and "CK" in f.name:
        CK_INVOICE_PATH = f
        break

if not CK_INVOICE_PATH:
    # Alternatif arama
    for f in FATURA_DIR.iterdir():
        if f.suffix.lower() == ".pdf" and "BOĞAZİÇİ" in f.name.upper():
            CK_INVOICE_PATH = f
            break

# Beklenen değerler
EXPECTED_TOTAL = 593740.00
EXPECTED_KDV = 98956.24
EXPECTED_CONSUMPTION = 116145.63
TOLERANCE = 0.01  # %1 tolerans


def test_full_process():
    """Full process endpoint testi."""
    print("=" * 60)
    print("CK Boğaziçi Fatura Testi - ROI Crop")
    print("=" * 60)
    
    if not CK_INVOICE_PATH or not CK_INVOICE_PATH.exists():
        print(f"HATA: CK Boğaziçi faturası bulunamadı!")
        print(f"Aranan dizin: {FATURA_DIR}")
        return
    
    print(f"\nFatura: {CK_INVOICE_PATH.name}")
    print(f"Beklenen Ödenecek Tutar: {EXPECTED_TOTAL:,.2f} TL")
    print(f"Beklenen KDV: {EXPECTED_KDV:,.2f} TL")
    print(f"Beklenen Tüketim: {EXPECTED_CONSUMPTION:,.2f} kWh")
    
    # API'ye gönder
    print("\n[1] API'ye gönderiliyor...")
    
    with open(CK_INVOICE_PATH, "rb") as f:
        files = {"file": (CK_INVOICE_PATH.name, f, "application/pdf")}
        params = {"debug": "true", "fast_mode": "false"}
        
        response = requests.post(
            f"{API_URL}/full-process",
            files=files,
            params=params,
            timeout=120
        )
    
    if response.status_code != 200:
        print(f"HATA: API yanıtı {response.status_code}")
        print(response.text[:500])
        return
    
    data = response.json()
    
    # Extraction sonuçları
    extraction = data.get("extraction", {})
    debug_meta = data.get("debug_meta", {})
    calculation = data.get("calculation")
    
    print("\n[2] Extraction Sonuçları:")
    print("-" * 40)
    
    # Ödenecek Tutar
    invoice_total = extraction.get("invoice_total_with_vat_tl", {})
    total_value = invoice_total.get("value")
    total_evidence = invoice_total.get("evidence", "")
    total_confidence = invoice_total.get("confidence", 0)
    
    print(f"Ödenecek Tutar: {total_value:,.2f} TL" if total_value else "Ödenecek Tutar: YOK")
    print(f"  Evidence: {total_evidence}")
    print(f"  Confidence: {total_confidence}")
    
    if total_value:
        diff = abs(total_value - EXPECTED_TOTAL)
        diff_pct = diff / EXPECTED_TOTAL * 100
        status = "✓" if diff_pct < TOLERANCE * 100 else "✗"
        print(f"  Fark: {diff:,.2f} TL ({diff_pct:.2f}%) {status}")
    
    # Tüketim
    consumption = extraction.get("consumption_kwh", {})
    consumption_value = consumption.get("value")
    print(f"\nTüketim: {consumption_value:,.2f} kWh" if consumption_value else "Tüketim: YOK")
    
    if consumption_value:
        diff = abs(consumption_value - EXPECTED_CONSUMPTION)
        diff_pct = diff / EXPECTED_CONSUMPTION * 100
        status = "✓" if diff_pct < TOLERANCE * 100 else "✗"
        print(f"  Fark: {diff:,.2f} kWh ({diff_pct:.2f}%) {status}")
    
    # Debug meta (ROI crop bilgisi)
    print("\n[3] Debug Meta:")
    print("-" * 40)
    
    warnings = debug_meta.get("warnings", [])
    for w in warnings:
        print(f"  ⚠️ {w}")
    
    errors = debug_meta.get("errors", [])
    for e in errors:
        print(f"  ❌ {e}")
    
    # ROI crop kullanıldı mı?
    roi_used = any("ROI" in str(w) for w in warnings)
    pdfplumber_used = any("PDF'den okunan" in str(w) for w in warnings)
    cross_validated = any("Cross-validation" in str(w) or "düzeltildi" in str(w) for w in warnings)
    
    print(f"\n  pdfplumber kullanıldı: {'✓' if pdfplumber_used else '✗'}")
    print(f"  ROI crop kullanıldı: {'✓' if roi_used else '✗'}")
    print(f"  Cross-validation: {'✓' if cross_validated else '✗'}")
    
    # Calculation sonuçları
    if calculation:
        print("\n[4] Hesaplama Sonuçları:")
        print("-" * 40)
        print(f"  Teklif Toplam: {calculation.get('offer_total_with_vat_tl', 0):,.2f} TL")
        print(f"  Fatura Toplam: {calculation.get('invoice_total_with_vat_tl', 0):,.2f} TL")
        print(f"  Tasarruf: {calculation.get('savings_tl', 0):,.2f} TL")
        print(f"  Tasarruf %: {calculation.get('savings_percent', 0):.2f}%")
    
    # Özet
    print("\n" + "=" * 60)
    print("ÖZET")
    print("=" * 60)
    
    success = True
    
    if total_value:
        diff_pct = abs(total_value - EXPECTED_TOTAL) / EXPECTED_TOTAL * 100
        if diff_pct < 1:
            print(f"✓ Ödenecek Tutar DOĞRU: {total_value:,.2f} TL")
        else:
            print(f"✗ Ödenecek Tutar YANLIŞ: {total_value:,.2f} TL (beklenen: {EXPECTED_TOTAL:,.2f})")
            success = False
    else:
        print("✗ Ödenecek Tutar OKUNAMADI")
        success = False
    
    if roi_used:
        print("✓ ROI crop stratejisi ÇALIŞTI")
    elif pdfplumber_used:
        print("✓ pdfplumber stratejisi ÇALIŞTI")
    else:
        print("⚠️ Hiçbir strateji çalışmadı, Vision tek başına")
    
    return success


if __name__ == "__main__":
    test_full_process()
