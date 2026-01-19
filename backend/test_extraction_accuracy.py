"""
Extraction doğruluk testi - tüm kritik alanlar.

CK Boğaziçi faturası için beklenen değerler:
- Ödenecek Tutar: 593.740,00 TL (KDV DAHİL)
- KDV: 98.956,24 TL
- Tüketim: 116.145,630 kWh
- Enerji Bedeli: ~506.738 TL (line items toplamı)
- Dağıtım Bedeli: ~86.952 TL
"""

import requests
import json
from pathlib import Path

API_URL = "http://localhost:8001"

# Fatura dosyasını bul
FATURA_DIR = Path("Fatura örnekler")
CK_INVOICE_PATH = None

for f in FATURA_DIR.iterdir():
    if "BBE" in f.name and "CK" in f.name:
        CK_INVOICE_PATH = f
        break

# Beklenen değerler (faturadan manuel kontrol)
EXPECTED = {
    "payable_total": 593740.00,
    "vat": 98956.24,
    "consumption_kwh": 116145.63,
    "energy_total": 506738.26,  # line items toplamı
    "distribution_total": 86952.60,
}


def test_accuracy():
    print("=" * 70)
    print("EXTRACTION DOĞRULUK TESTİ - TÜM KRİTİK ALANLAR")
    print("=" * 70)
    
    if not CK_INVOICE_PATH or not CK_INVOICE_PATH.exists():
        print(f"HATA: CK Boğaziçi faturası bulunamadı!")
        return
    
    print(f"\nFatura: {CK_INVOICE_PATH.name}")
    print(f"\nBeklenen Değerler:")
    for k, v in EXPECTED.items():
        print(f"  {k}: {v:,.2f}")
    
    # API'ye gönder
    print("\n" + "-" * 70)
    print("API'ye gönderiliyor...")
    
    with open(CK_INVOICE_PATH, "rb") as f:
        files = {"file": (CK_INVOICE_PATH.name, f, "application/pdf")}
        params = {"debug": "true", "fast_mode": "false"}
        
        response = requests.post(
            f"{API_URL}/full-process",
            files=files,
            params=params,
            timeout=180
        )
    
    if response.status_code != 200:
        print(f"HATA: API yanıtı {response.status_code}")
        print(response.text[:500])
        return
    
    data = response.json()
    extraction = data.get("extraction", {})
    
    # Değerleri çıkar
    results = {}
    
    # Ödenecek Tutar
    invoice_total = extraction.get("invoice_total_with_vat_tl", {})
    results["payable_total"] = {
        "value": invoice_total.get("value"),
        "confidence": invoice_total.get("confidence"),
        "evidence": invoice_total.get("evidence", "")[:50],
    }
    
    # KDV
    raw_breakdown = extraction.get("raw_breakdown", {})
    vat = raw_breakdown.get("vat_tl", {}) if raw_breakdown else {}
    results["vat"] = {
        "value": vat.get("value") if vat else None,
        "confidence": vat.get("confidence") if vat else None,
        "evidence": (vat.get("evidence", "") if vat else "")[:50],
    }
    
    # Tüketim
    consumption = extraction.get("consumption_kwh", {})
    results["consumption_kwh"] = {
        "value": consumption.get("value"),
        "confidence": consumption.get("confidence"),
        "evidence": consumption.get("evidence", "")[:50],
    }
    
    # Enerji Bedeli
    energy = raw_breakdown.get("energy_total_tl", {}) if raw_breakdown else {}
    results["energy_total"] = {
        "value": energy.get("value") if energy else None,
        "confidence": energy.get("confidence") if energy else None,
        "evidence": (energy.get("evidence", "") if energy else "")[:50],
    }
    
    # Dağıtım Bedeli
    dist = raw_breakdown.get("distribution_total_tl", {}) if raw_breakdown else {}
    results["distribution_total"] = {
        "value": dist.get("value") if dist else None,
        "confidence": dist.get("confidence") if dist else None,
        "evidence": (dist.get("evidence", "") if dist else "")[:50],
    }
    
    # Sonuçları göster
    print("\n" + "=" * 70)
    print("SONUÇLAR")
    print("=" * 70)
    
    total_score = 0
    max_score = len(EXPECTED)
    
    for field, expected in EXPECTED.items():
        result = results.get(field, {})
        actual = result.get("value")
        confidence = result.get("confidence")
        evidence = result.get("evidence", "")
        
        print(f"\n{field.upper()}:")
        print(f"  Beklenen: {expected:,.2f}")
        print(f"  Okunan:   {actual:,.2f}" if actual else "  Okunan:   YOK")
        print(f"  Confidence: {confidence}")
        print(f"  Evidence: {evidence}")
        
        if actual:
            diff = abs(actual - expected)
            diff_pct = diff / expected * 100
            
            if diff_pct < 1:
                status = "✓ DOĞRU"
                total_score += 1
            elif diff_pct < 5:
                status = "⚠️ YAKIN"
                total_score += 0.5
            else:
                status = f"✗ YANLIŞ (fark: {diff:,.2f} / %{diff_pct:.1f})"
            
            print(f"  Durum: {status}")
        else:
            print(f"  Durum: ✗ OKUNAMADI")
    
    # Özet
    print("\n" + "=" * 70)
    print("ÖZET")
    print("=" * 70)
    print(f"Doğruluk Skoru: {total_score}/{max_score} ({total_score/max_score*100:.0f}%)")
    
    # Debug meta
    debug_meta = data.get("debug_meta", {})
    warnings = debug_meta.get("warnings", [])
    if warnings:
        print(f"\nUyarılar:")
        for w in warnings:
            print(f"  ⚠️ {w}")
    
    # Kaynak analizi
    print(f"\nKaynak Analizi:")
    if "ROI" in str(warnings):
        print("  ✓ ROI crop kullanıldı")
    if "pdfplumber" in str(warnings) or "PDF'den okunan" in str(warnings):
        print("  ✓ pdfplumber kullanıldı")
    if "CROSS-VALIDATED" in str(results):
        print("  ✓ Cross-validation yapıldı")


if __name__ == "__main__":
    test_accuracy()
