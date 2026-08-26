# -*- coding: utf-8 -*-
"""
S5-R03A — Paketli GERÇEK PDF smoke (owner Bölüm 8).

Gerçek paketlenmiş gelka-backend.exe'ye karşı, disposable sentetik canonical
DB ile uçtan uca teklif-PDF zincirini mekanik olarak doğrular:

  1. Exe PYZ envanterinde ReportLab modülleri (build'den collection
     kaldırılırsa BURADA FAIL — mutation kapısı M1'in packaged yarısı)
  2. Üretim ÖNCESİ ön-durum: offers listesinde pdf_ref=null (pdf_ref'in
     yalnız üretim/publish SONRASI yazıldığının iki-uçlu kanıtı)
  3. POST /offers/{id}/generate-pdf → 200, regenerated=true
  4. GET  /offers/{id}/download    → 200, %PDF, parser ile açılır, ≥1 sayfa,
     "Teklif No: {id}" bağlamlı kalıbı + sentetik snapshot verileri metinde
  5. İkinci çağrı regenerated=false, aynı ref
  6. Tek fiziksel PDF; .tmp/orphan 0; kapanış sonrası .generate.lock üzerinde
     AKTİF OS kilidi 0 (dosyanın varlığı tasarım gereğidir, kilit tutulmamalı)
  7. Access-log kanalı CANLI (pozitif kontrol: '"POST /offers' satırı) VE
     /generate-pdf-simple runtime çağrısı 0
  8. Kullanılan motor ReportLab (log/audit kanıtı; Playwright'a düşüş YOK)

Kullanım (opt-in komut; pytest regresyonuna DAHİL DEĞİLDİR — packaged exe
gerektirir):
    python -m scripts.packaged_pdf_smoke <gelka-backend.exe> <calisma-dizini>

<calisma-dizini> boş/temiz bir dizin olmalıdır; sentetik DB + storage +
loglar buraya yazılır. Production'a HİÇBİR koşulda dokunmaz.

Çağrıldığı yerler:
- S5-R03A Bölüm 8 packaged smoke (elle / release qualification)
- backend/tests/test_s5_r03a_pdf_engine.py yalnız varlığını/pinini referanslar
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PORT = 8951
BEKLENEN_TERMINAL = "351d314819d5"

REPORTLAB_ZORUNLU_MODULLER = {
    "reportlab",
    "reportlab.pdfgen",
    "reportlab.pdfgen.canvas",
    "reportlab.pdfbase",
    "reportlab.pdfbase.pdfmetrics",
    "reportlab.pdfbase.ttfonts",
    "reportlab.platypus",
    "reportlab.lib.pagesizes",
    "reportlab.lib.styles",
    "reportlab.lib.units",
    "reportlab.lib.colors",
    "reportlab.lib.enums",
}


def _kontrol(kosul: bool, mesaj: str) -> None:
    durum = "PASS" if kosul else "FAIL"
    print(f"[{durum}] {mesaj}")
    if not kosul:
        raise SystemExit(f"SMOKE FAIL: {mesaj}")


def _http(metod: str, yol: str, veri: dict | None = None) -> tuple[int, bytes]:
    url = f"http://127.0.0.1:{PORT}{yol}"
    istek = urllib.request.Request(url, method=metod)
    govde = None
    if veri is not None:
        govde = json.dumps(veri).encode()
        istek.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(istek, data=govde, timeout=120) as y:
            return y.status, y.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def pyz_reportlab_envanteri(exe: Path) -> None:
    """1) Exe PYZ'sinde ReportLab modülleri gerçekten var mı?"""
    from PyInstaller.archive.readers import CArchiveReader

    ar = CArchiveReader(str(exe))
    pyz = ar.open_embedded_archive("PYZ.pyz")
    moduller = set(pyz.toc.keys())
    eksik = REPORTLAB_ZORUNLU_MODULLER - moduller
    toplam = sum(1 for m in moduller if m == "reportlab" or m.startswith("reportlab."))
    _kontrol(not eksik, f"PYZ ReportLab envanteri (toplam {toplam} modul; eksik={sorted(eksik)})")


def sentetik_db_hazirla(db_yolu: Path) -> None:
    """2) Disposable sentetik canonical DB (terminal 351d…, 34 tablo)."""
    import sqlite3

    alembic = Path(sys.executable).parent / "alembic.exe"
    _kontrol(alembic.exists(), f"alembic bulunabilir ({alembic.name})")
    ortam = {**os.environ, "DATABASE_URL": f"sqlite:///{db_yolu.as_posix()}"}
    backend_kok = Path(__file__).resolve().parents[1]
    r = subprocess.run(
        [str(alembic), "upgrade", "head"],
        cwd=str(backend_kok), env=ortam, capture_output=True, text=True, timeout=300,
    )
    _kontrol(r.returncode == 0, f"alembic upgrade head (rc={r.returncode})")
    c = sqlite3.connect(str(db_yolu))
    terminal = c.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    tablolar = c.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    c.close()
    _kontrol(terminal == BEKLENEN_TERMINAL and tablolar == 34,
             f"sentetik canonical DB (terminal={terminal}, tablo={tablolar})")


def main() -> int:
    exe = Path(sys.argv[1]).resolve()
    kok = Path(sys.argv[2]).resolve()
    _kontrol(exe.is_file(), f"exe mevcut: {exe.name}")
    kok.mkdir(parents=True, exist_ok=True)

    pyz_reportlab_envanteri(exe)

    db_yolu = kok / "smoke.db"
    storage = kok / "storage"
    log_yolu = kok / "backend-smoke.log"
    sentetik_db_hazirla(db_yolu)

    ortam = {
        **os.environ,
        "GELKA_PACKAGED_RUNTIME": "1",
        "ENV": "desktop",
        "DATABASE_URL": f"sqlite:///{db_yolu.as_posix()}",
        "STORAGE_DIR": str(storage),
        "API_KEY_ENABLED": "false",
    }
    ortam.pop("OPENAI_API_KEY", None)  # dış AI çağrısı imkânsız olsun

    # Port ONCEDEN bos olmali — aksi halde istekler baska bir surece gider
    # ve smoke sahte-yesil/sahte-kirmizi olur (gercek kosuda yasandi:
    # onceki kosunun orphan child'i portu tutuyordu).
    try:
        durum, _g = _http("GET", "/version")
        raise SystemExit(
            f"SMOKE FAIL: port {PORT} zaten dinleniyor (HTTP {durum}) — "
            "onceki kosu artigi temizlenmeli"
        )
    except SystemExit:
        raise
    except Exception:
        pass  # baglanti reddi = port bos (beklenen)

    log_f = open(log_yolu, "wb")
    proc = subprocess.Popen(
        [str(exe), "--port", str(PORT)],
        cwd=str(exe.parent), env=ortam, stdout=log_f, stderr=subprocess.STDOUT,
    )
    try:
        # onefile ilk açılış çıkarması uzun sürebilir
        for _ in range(120):
            time.sleep(2)
            try:
                durum, _g = _http("GET", "/version")
                if durum == 200:
                    break
            except Exception:
                pass
        else:
            raise SystemExit("SMOKE FAIL: backend saglik vermedi")
        print("[PASS] paketli backend ayakta (/version 200)")

        durum, g = _http("POST", "/customers?name=Smoke%20Sentetik%20A.S.")
        _kontrol(durum == 200, f"musteri olusturuldu ({durum})")
        musteri_id = json.loads(g)["id"]

        # R01A kanonik govde kalibi (test_s5_r01a_raw_total_guard.GOVDE_TEMEL):
        # computed_total = 282500 + 56500 = 339000; raw=339000 → sapma 0.
        hesap = {
            "current_energy_tl": 250000.0, "current_distribution_tl": 30000.0,
            "current_demand_tl": 0.0, "current_btv_tl": 2500.0,
            "current_vat_matrah_tl": 282500.0, "current_vat_tl": 56500.0,
            "current_total_with_vat_tl": 339000.0, "offer_ptf_tl": 225000.0,
            "offer_yekdem_tl": 5000.0, "offer_energy_tl": 230000.0,
            "offer_distribution_tl": 30000.0, "offer_demand_tl": 0.0,
            "offer_btv_tl": 2300.0, "offer_vat_matrah_tl": 262300.0,
            "offer_vat_tl": 52460.0, "offer_total_with_vat_tl": 314760.0,
            "difference_excl_vat_tl": 20200.0, "difference_incl_vat_tl": 24240.0,
            "savings_ratio": 0.0715, "unit_price_savings_ratio": 0.08,
        }
        durum, g = _http(
            "POST",
            f"/offers?customer_id={musteri_id}&invoice_total_raw=339000",
            veri={
                "extraction": {
                    "vendor": "Smoke Sentetik", "invoice_period": "2026-07",
                    "consumption_kwh": {"value": 125000.0, "confidence": 1.0},
                    "current_active_unit_price_tl_per_kwh": {"value": 2.0, "confidence": 1.0},
                },
                "calculation": hesap,
                "params": {"weighted_ptf_tl_per_mwh": 2974.1,
                           "yekdem_tl_per_mwh": 364.0,
                           "agreement_multiplier": 1.01},
            },
        )
        _kontrol(durum == 200, f"teklif persist ({durum}): {g[:200]!r}")
        offer_id = json.loads(g)["id"]

        # ON-DURUM: uretimden ONCE pdf_ref null olmali — "pdf_ref yalniz
        # publish sonrasi yazilir" iddiasinin oncesi-ucu (adversarial
        # dogrulama bulgusu: yalniz sonrasi-ucu kontrol etmek eksikti).
        durum, g = _http("GET", "/offers")
        _kontrol(durum == 200, f"offers listesi ({durum})")
        kayit = next(o for o in json.loads(g) if o["id"] == offer_id)
        _kontrol(kayit.get("pdf_ref") is None, "uretim ONCESI pdf_ref=null")

        # generate-pdf → 200 regenerated=true
        durum, g = _http("POST", f"/offers/{offer_id}/generate-pdf")
        _kontrol(durum == 200, f"generate-pdf ilk cagri ({durum}): {g[:300]!r}")
        ilk = json.loads(g)
        _kontrol(ilk.get("regenerated") is True, "ilk cagri regenerated=true")
        _kontrol(bool(ilk.get("pdf_ref")), "pdf_ref publish sonrasi yazildi")

        # 3) download → gerçek PDF
        durum, pdf = _http("GET", f"/offers/{offer_id}/download")
        _kontrol(durum == 200, f"download ({durum})")
        _kontrol(pdf.startswith(b"%PDF"), "govde %PDF ile basliyor")
        _kontrol(len(pdf) > 10_000, f"boyut makul ({len(pdf)} bayt)")
        import pypdfium2

        doc = pypdfium2.PdfDocument(pdf)
        _kontrol(len(doc) >= 1, f"parser acti, sayfa={len(doc)}")
        metin = doc[0].get_textpage().get_text_bounded()
        # BAGLAMLI kimlik kalibi (adversarial bulgu: cıplak str(offer_id)
        # araması vakum-yesildi — "1" her PDF'te gecer). Gercek format:
        # "Teklif No: {id} | {tarih} | Gecerlilik: ...".
        import re as _re

        _kontrol(bool(_re.search(rf"Teklif No:\s*{offer_id}\b", metin)),
                 f"'Teklif No: {offer_id}' baglamli kalibi PDF metninde")
        _kontrol("Smoke Sentetik" in metin,
                 "sentetik musteri/snapshot verileri PDF metninde")

        # 4) idempotency
        durum, g = _http("POST", f"/offers/{offer_id}/generate-pdf")
        ikinci = json.loads(g)
        _kontrol(durum == 200 and ikinci.get("regenerated") is False,
                 "ikinci cagri regenerated=false")
        _kontrol(ikinci.get("pdf_ref") == ilk.get("pdf_ref"), "pdf_ref degismedi")

        # tek fiziksel PDF, artik yok
        pdfler = list(storage.rglob("*.pdf"))
        _kontrol(len(pdfler) == 1, f"tek fiziksel PDF ({len(pdfler)})")
        tmpler = list(storage.rglob("*.tmp"))
        _kontrol(not tmpler, f".tmp artigi 0 ({tmpler})")
        kilit_yolu = storage / "offers" / str(offer_id) / ".generate.lock"
        _kontrol(kilit_yolu.exists(), ".generate.lock mevcut (tasarim geregi silinmez)")
    finally:
        # PyInstaller onefile: proc.pid BOOTLOADER'dir; yalniz onu oldurmek
        # child server'i ORPHAN birakir ve portu tutmaya devam eder (gercek
        # kosuda yasandi). Windows'ta agac-kill sart.
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True, timeout=30,
            )
        else:
            proc.kill()
        proc.wait(timeout=30)
        log_f.close()

    time.sleep(2)
    log = log_yolu.read_bytes().decode("utf-8", errors="replace")

    # Kapanis sonrasi .generate.lock uzerinde AKTIF OS kilidi kalmamali
    # (adversarial bulgu: docstring'deki bu iddia implement edilmemisti).
    import msvcrt

    kilit_yolu = kok / "storage" / "offers" / "1" / ".generate.lock"
    fd = os.open(str(kilit_yolu), os.O_RDWR)
    try:
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        kilit_serbest = True
    except OSError:
        kilit_serbest = False
    finally:
        os.close(fd)
    _kontrol(kilit_serbest, "kapanis sonrasi aktif OS kilidi 0")

    # Access-log kanali CANLI olmali (pozitif kontrol) — aksi halde
    # asagidaki yokluk-kontrolu bos-yesil olurdu (adversarial bulgu).
    _kontrol('"POST /offers' in log, "uvicorn access-log kanali canli")
    # /generate-pdf-simple runtime çağrısı 0
    _kontrol("generate-pdf-simple" not in log, "/generate-pdf-simple cagrisi 0")
    # motor audit: ReportLab kullanıldı, Playwright'a düşülmedi
    _kontrol("Generated PDF with ReportLab" in log, "motor=ReportLab (audit log)")
    _kontrol("Playwright PDF generation" not in log, "Playwright'a dusus yok")
    _kontrol("Traceback" not in log, "log'da beklenmeyen traceback yok")

    print("SMOKE OK: paketli ReportLab PDF zinciri dogrulandi")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
