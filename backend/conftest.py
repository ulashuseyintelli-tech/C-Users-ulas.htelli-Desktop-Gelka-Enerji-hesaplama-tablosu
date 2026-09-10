"""
backend/ kök conftest'i — YALNIZ pytest'in OTOMATİK TEST KEŞFİ için dışlama listesi.

Kanonik backend test komutu (backend/ dizininden):

    python -m pytest tests

Neden bu liste var
------------------
Aşağıdaki 25 dosya pytest testi DEĞİL, elle çalıştırılan ad-hoc betiklerdir;
yalnız adları pytest'in varsayılan keşif desenine (test_*.py) uyar. Yolsuz
çağrılar (backend/'den `pytest` ya da `pytest .`, repo kökünden `pytest` ya da
`pytest backend`) bunları toplar ve toplama sırasında import eder. Çoğu import
anında yan etki üretir: localhost:8000/8001'e HTTP isteği (8000 = masaüstü
uygulamasının backend portu), backend/.env yükleyip OpenAI çağrısı, dosya
yazma. Marker'sız async testleri de tests/conftest.py'deki "SESSIZ ASYNC SKIP"
kapısını tetikleyip tüm koşuyu exit 4 ile durdurur. Liste, 2026-09-10'da
statik incelemeyle doğrulanan envanterdir; tests/test_adhoc_collection_guard.py
ile kilitlidir.

Liste BİLİNÇLİ OLARAK dosya dosya ve açıktır: scripts/ dizininin tamamı veya
gelecekte eklenecek test_*.py dosyaları topluca gizlenmez. Yeni bir ad-hoc
betik ya test_ öneki olmayan bir adla eklenmeli ya da buraya ve kilit
testteki envantere bilinçli olarak eklenmelidir. Glob (collect_ignore_glob)
kullanılmadı: fnmatch'te `*` dizin ayırıcısını da geçer.

SINIR
-----
Bu koruma yalnız otomatik keşfi kapsar. Dosya açıkça verilirse
(`pytest test_fresh.py`, `python test_fresh.py`) pytest dışlama uygulamaz ve
betik tüm yan etkileriyle çalışır — açık çağrıyı güvenli hâle GETİRMEZ.

Çağrıldığı yerler (kod çağıranı yok; pytest bu dosyayı otomatik yükler)
------------------------------------------------------------------------
- backend/'den `python -m pytest tests` (kanonik) → liste etkisiz, toplama değişmez
- backend/'den `python -m pytest` / `python -m pytest .` → 25 dosya toplanmaz
- repo kökünden `pytest` / `pytest backend` → aynı; monitoring/tests etkilenmez
- .kiro/specs/invoice-validation/tasks.md, design.md → repo kökünden `pytest -k ...` → aynı
- backend/tests/test_lc_tier_runner.py::_run_tier() → subprocess, açık backend/tests yolları → etkisiz
"""

collect_ignore = [
    # backend/ kökü (19)
    "test_api.py",
    "test_ck_roi.py",
    "test_debug.py",
    "test_epias_live.py",
    "test_epias_mock.py",
    "test_extraction_accuracy.py",
    "test_extractor.py",
    "test_fresh.py",
    "test_pdf_output.py",
    "test_pdf_text.py",
    "test_ptf_auto.py",
    "test_quick.py",
    "test_roi_direct.py",
    "test_roi_save.py",
    "test_single.py",
    "test_speed.py",
    "test_sprint3.py",
    "test_sprint3_mock.py",
    "test_timing.py",
    # backend/scripts/ (6) — dizinin kendisi DEĞİL, yalnız bu dosyalar
    "scripts/test_api.py",
    "scripts/test_api_endpoint.py",
    "scripts/test_calculation.py",
    "scripts/test_gpt5.py",
    "scripts/test_html_invoice.py",
    "scripts/test_pipeline.py",
]
