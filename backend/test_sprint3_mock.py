"""Sprint 3 Kanıt Testleri - Mock Data ile"""
import requests
import json

BASE_URL = "http://localhost:8000"

def test_kanit_1_mock():
    """Kanıt 1: Quality score algoritması testi (mock data)"""
    print("=== KANIT 1: Quality Score Algoritması ===")
    
    from app.incident_service import calculate_quality_score, QualityScore
    
    # Test 1: Temiz extraction (yüksek skor)
    quality = calculate_quality_score(
        extraction={"consumption_kwh": {"value": 1000, "confidence": 0.95}},
        validation={"is_ready_for_pricing": True, "missing_fields": [], "warnings": []},
        calculation={"meta_distribution_source": "epdk_tariff", "meta_pricing_source": "reference"},
        calculation_error=None,
        debug_meta={"warnings": [], "json_repair_applied": False}
    )
    print(f"Test 1 (temiz): score={quality.score}, grade={quality.grade}, flags={quality.flags}")
    
    # Test 2: Market price missing (S1)
    quality = calculate_quality_score(
        extraction={"consumption_kwh": {"value": 1000, "confidence": 0.95}},
        validation={"is_ready_for_pricing": True, "missing_fields": [], "warnings": []},
        calculation=None,
        calculation_error="Dönem 2025-03 için referans fiyat bulunamadı",
        debug_meta={"warnings": [], "errors": []}
    )
    print(f"Test 2 (price missing): score={quality.score}, grade={quality.grade}, flags={quality.flags}")
    
    # Test 3: Distribution mismatch + JSON repair (S2 + S3)
    quality = calculate_quality_score(
        extraction={"consumption_kwh": {"value": 1000, "confidence": 0.95}},
        validation={"is_ready_for_pricing": True, "missing_fields": [], "warnings": ["test"]},
        calculation={"meta_distribution_source": "epdk_tariff", "meta_pricing_source": "reference", "meta_distribution_mismatch_warning": "Fark: 5%"},
        calculation_error=None,
        debug_meta={"warnings": ["mismatch detected"], "json_repair_applied": True}
    )
    print(f"Test 3 (mismatch+repair): score={quality.score}, grade={quality.grade}, flags={quality.flags}")
    
    return quality

def test_dedupe():
    """Sprint 4: Dedupe testi"""
    print("\n=== SPRINT 4: Dedupe Testi ===")
    
    from app.incident_service import (
        calculate_quality_score, create_incidents_from_quality, get_incidents,
        generate_invoice_fingerprint, generate_dedupe_key
    )
    from app.database import SessionLocal
    
    db = SessionLocal()
    
    # Fingerprint testi
    fp1 = generate_invoice_fingerprint("CK", "INV001", "2025-01", 1000, 500)
    fp2 = generate_invoice_fingerprint("CK", "INV001", "2025-01", 1000, 500)
    fp3 = generate_invoice_fingerprint("CK", "INV002", "2025-01", 1000, 500)
    
    print(f"Fingerprint 1: {fp1}")
    print(f"Fingerprint 2: {fp2} (aynı fatura = aynı fp)")
    print(f"Fingerprint 3: {fp3} (farklı fatura no = farklı fp)")
    assert fp1 == fp2, "Aynı fatura aynı fingerprint üretmeli"
    assert fp1 != fp3, "Farklı fatura farklı fingerprint üretmeli"
    
    # Dedupe key testi
    dk1 = generate_dedupe_key("default", "TARIFF_MISSING", "2025-01", fp1)
    dk2 = generate_dedupe_key("default", "TARIFF_MISSING", "2025-01", fp1)
    dk3 = generate_dedupe_key("default", "PRICE_MISSING", "2025-01", fp1)
    
    print(f"\nDedupe key 1: {dk1[:16]}...")
    print(f"Dedupe key 2: {dk2[:16]}... (aynı = aynı dk)")
    print(f"Dedupe key 3: {dk3[:16]}... (farklı category = farklı dk)")
    assert dk1 == dk2, "Aynı parametreler aynı dedupe key üretmeli"
    assert dk1 != dk3, "Farklı category farklı dedupe key üretmeli"
    
    # Dedupe incident testi
    print("\n--- Dedupe Incident Testi ---")
    
    quality = calculate_quality_score(
        extraction={},
        validation={"is_ready_for_pricing": False, "missing_fields": ["consumption_kwh"], "warnings": []},
        calculation=None,
        calculation_error="Dağıtım birim fiyatı bulunamadı",
        debug_meta={"warnings": [], "errors": []}
    )
    
    # İlk incident oluştur
    trace_id_1 = "dedupe-test-001"
    ids_1 = create_incidents_from_quality(
        db=db,
        trace_id=trace_id_1,
        quality=quality,
        tenant_id="default",
        period="2025-01",
        invoice_fingerprint=fp1
    )
    print(f"İlk çağrı: {len(ids_1)} incident oluşturuldu, IDs: {ids_1}")
    
    # Aynı hata tekrar (dedupe olmalı)
    trace_id_2 = "dedupe-test-002"
    ids_2 = create_incidents_from_quality(
        db=db,
        trace_id=trace_id_2,
        quality=quality,
        tenant_id="default",
        period="2025-01",
        invoice_fingerprint=fp1
    )
    print(f"İkinci çağrı (aynı fatura): {len(ids_2)} incident, IDs: {ids_2}")
    print(f"Dedupe çalıştı mı? {ids_1 == ids_2}")
    
    # Farklı fatura (yeni incident olmalı)
    trace_id_3 = "dedupe-test-003"
    ids_3 = create_incidents_from_quality(
        db=db,
        trace_id=trace_id_3,
        quality=quality,
        tenant_id="default",
        period="2025-01",
        invoice_fingerprint=fp3  # Farklı fingerprint
    )
    print(f"Üçüncü çağrı (farklı fatura): {len(ids_3)} incident, IDs: {ids_3}")
    print(f"Yeni incident oluştu mu? {ids_1 != ids_3}")
    
    # Occurrence count kontrol
    incidents = get_incidents(db, tenant_id="default", limit=10)
    print(f"\nSon incidents:")
    for inc in incidents[:5]:
        print(f"  - #{inc['id']} [{inc['severity']}] {inc['category']}: occ={inc.get('occurrence_count', 1)}×")
    
    db.close()

def test_kanit_3_admin_auth():
    """Kanıt 3: Admin auth testi"""
    print("\n=== KANIT 3: Admin Auth ===")
    
    print("ADMIN_API_KEY_ENABLED=false (dev mode) - bypass aktif")
    
    response = requests.get(f'{BASE_URL}/admin/incidents')
    print(f"GET /admin/incidents: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        print(f"Total incidents: {data.get('count', 0)}")
        for inc in data.get('incidents', [])[:3]:
            print(f"  - #{inc['id']} [{inc['severity']}] occ={inc.get('occurrence_count', 1)}×: {inc['message'][:40]}...")

if __name__ == '__main__':
    test_kanit_1_mock()
    test_dedupe()
    test_kanit_3_admin_auth()
