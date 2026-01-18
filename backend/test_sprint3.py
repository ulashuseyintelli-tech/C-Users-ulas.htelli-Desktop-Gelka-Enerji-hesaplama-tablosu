"""Sprint 3 Kanıt Testleri"""
import requests
import json

BASE_URL = "http://localhost:8000"

def test_kanit_1():
    """Kanıt 1: Response örneği (trace_id + quality_score)"""
    print("=== KANIT 1: Response örneği ===")
    
    # Dosya yolunu kontrol et
    import os
    pdf_path = '../Fatura örnekler/EAL2025000070065.pdf'
    if not os.path.exists(pdf_path):
        pdf_path = 'Fatura örnekler/EAL2025000070065.pdf'
    if not os.path.exists(pdf_path):
        # Workspace root'tan dene
        for root, dirs, files in os.walk('..'):
            if 'EAL2025000070065.pdf' in files:
                pdf_path = os.path.join(root, 'EAL2025000070065.pdf')
                break
    
    print(f"Using PDF: {pdf_path}")
    
    with open(pdf_path, 'rb') as f:
        response = requests.post(
            f'{BASE_URL}/full-process?debug=true',
            files={'file': ('test.pdf', f, 'application/pdf')}
        )
    
    print(f"Response status: {response.status_code}")
    
    if response.status_code != 200:
        print(f"Error: {response.text[:500]}")
        return None
    
    data = response.json()
    trace_id = data.get('meta', {}).get('trace_id')
    quality_score = data.get('quality_score')
    
    print(f"trace_id: {trace_id}")
    print(f"quality_score: {json.dumps(quality_score, indent=2, ensure_ascii=False)}")
    
    return trace_id

def test_kanit_2(trace_id):
    """Kanıt 2: Admin incidents list'te trace_id görünmesi"""
    print("\n=== KANIT 2: Admin incidents list ===")
    
    response = requests.get(
        f'{BASE_URL}/admin/incidents',
        headers={'X-Admin-Key': 'test'}  # Dev mode bypass
    )
    
    data = response.json()
    print(f"Status: {response.status_code}")
    print(f"Total incidents: {data.get('count', 0)}")
    
    # trace_id ile eşleşen incident var mı?
    incidents = data.get('incidents', [])
    matching = [i for i in incidents if i.get('trace_id') == trace_id]
    
    if matching:
        print(f"Matching incidents for trace_id={trace_id}:")
        for inc in matching:
            print(f"  - ID: {inc['id']}, Severity: {inc['severity']}, Category: {inc['category']}")
    else:
        print(f"No incidents found for trace_id={trace_id} (quality was OK)")
    
    # Son 5 incident'ı göster
    print("\nSon 5 incident:")
    for inc in incidents[:5]:
        print(f"  - [{inc['severity']}] {inc['category']}: {inc['message'][:50]}... (trace: {inc['trace_id']})")

def test_kanit_3():
    """Kanıt 3: Yetkisiz admin çağrısında 401/403"""
    print("\n=== KANIT 3: Yetkisiz erişim testi ===")
    
    # ADMIN_API_KEY_ENABLED=true olmalı bu testin çalışması için
    # Şu an dev mode'da bypass var, bu yüzden sadece yapıyı gösterelim
    
    # Header olmadan çağrı
    response = requests.get(f'{BASE_URL}/admin/incidents')
    print(f"Without header - Status: {response.status_code}")
    
    # Yanlış key ile çağrı
    response = requests.get(
        f'{BASE_URL}/admin/incidents',
        headers={'X-Admin-Key': 'wrong-key'}
    )
    print(f"With wrong key - Status: {response.status_code}")
    
    print("\nNOT: ADMIN_API_KEY_ENABLED=false (dev mode) olduğu için bypass aktif.")
    print("Production'da ADMIN_API_KEY_ENABLED=true ve ADMIN_API_KEY set edilmeli.")

if __name__ == '__main__':
    trace_id = test_kanit_1()
    test_kanit_2(trace_id)
    test_kanit_3()
