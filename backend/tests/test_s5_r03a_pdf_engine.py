"""
S5-R03A — Paketli PDF motor sözleşmesi (kaynak seviyesi).

Owner Bölüm 7 zorunlu testleri + mutation kapıları:
- PRIMARY=ReportLab gerçek üretim → geçerli %PDF (parser ile açılır, ≥1 sayfa)
- Event-loop hiçbir motor yolunda bloke olmaz (endpoint AST kapısı +
  gerçek ASGI davranış kanıtı)
- Sync Playwright çalışan asyncio loop içinde ÇAĞRILAMAZ (runtime guard +
  AST mutation kapısı)
- ReportLab failure + fallback unavailable → kontrollü 5xx, pdf_ref=None,
  orphan/temp 0, fiziksel path/iç exception sızıntısı yok
- PDF oluşmadan pdf_ref/publish YOK (%PDF kapısı)
- WeasyPrint eksikliği ReportLab başarısını bozmaz
- build-desktop.bat ReportLab collection pinli (kaldırılırsa FAIL)

Çağrıldığı yerler:
- pytest tam regresyon + S5 kabul paketi (otomatik)
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import textwrap
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

REPO_KOK = Path(__file__).resolve().parents[2]

# CalculationResult'un zorunlu alanları — test_s5_r01_offer_pdf ile aynı
# sentetik küme (tekrar kullanılabilir tutarlı değerler).
HESAP_SONUCU = {
    "current_energy_tl": 2000.0,
    "current_distribution_tl": 300.0,
    "current_demand_tl": 0.0,
    "current_btv_tl": 100.0,
    "current_vat_matrah_tl": 2400.0,
    "current_vat_tl": 480.0,
    "current_total_with_vat_tl": 2880.0,
    "offer_ptf_tl": 1800.0,
    "offer_yekdem_tl": 50.0,
    "offer_energy_tl": 1850.0,
    "offer_distribution_tl": 300.0,
    "offer_demand_tl": 0.0,
    "offer_btv_tl": 90.0,
    "offer_vat_matrah_tl": 2240.0,
    "offer_vat_tl": 448.0,
    "offer_total_with_vat_tl": 2688.0,
    "difference_excl_vat_tl": 160.0,
    "difference_incl_vat_tl": 192.0,
    "savings_ratio": 0.0667,
    "unit_price_savings_ratio": 0.075,
}


def _sentetik_girdiler():
    from app.models import CalculationResult, InvoiceExtraction, OfferParams

    extraction = InvoiceExtraction(**{"meta": {}})
    calculation = CalculationResult(**HESAP_SONUCU)
    params = OfferParams(
        weighted_ptf_tl_per_mwh=2500.0,
        yekdem_tl_per_mwh=50.0,
        agreement_multiplier=1.01,
    )
    return extraction, calculation, params


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures (test_s5_r01_offer_pdf ile aynı kalıp)
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.database import Base
    import app.pricing.schemas  # noqa: F401

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


@pytest.fixture()
def storage_tmp(tmp_path, monkeypatch):
    from app.core.config import settings
    from app.services.storage import clear_storage_cache

    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    clear_storage_cache()
    yield tmp_path
    clear_storage_cache()


@pytest.fixture()
def client(db, storage_tmp):
    from app.main import app as fastapi_app
    from app.database import get_db

    fastapi_app.dependency_overrides[get_db] = lambda: db
    yield TestClient(fastapi_app)
    fastapi_app.dependency_overrides.clear()


def _teklif(db):
    from app.database import Offer

    o = Offer(
        tenant_id="default",
        customer_id=None,
        vendor="Sentetik Tedarikci",
        invoice_period="2026-01",
        consumption_kwh=1000.0,
        current_unit_price=2.5,
        weighted_ptf=2500.0,
        yekdem=50.0,
        agreement_multiplier=1.01,
        current_total=2880.0,
        offer_total=2688.0,
        savings_amount=192.0,
        savings_ratio=0.0667,
        extraction_result={"meta": {}},
        calculation_result=dict(HESAP_SONUCU),
    )
    db.add(o)
    db.flush()
    db.commit()
    return o


def _fonksiyon_ast(dosya: Path, ad: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    agac = ast.parse(dosya.read_text(encoding="utf-8"))
    for dugum in ast.walk(agac):
        if isinstance(dugum, (ast.FunctionDef, ast.AsyncFunctionDef)) and dugum.name == ad:
            return dugum
    raise AssertionError(f"{dosya.name} icinde {ad} bulunamadi")


# ═══════════════════════════════════════════════════════════════════════════
# 1) PRIMARY=ReportLab gerçek üretim
# ═══════════════════════════════════════════════════════════════════════════

class TestReportLabBirincil:
    def test_reportlab_gercek_uretim_gecerli_pdf(self, monkeypatch):
        """ReportLab başarı yolu: gerçek render → parser ile açılan %PDF."""
        import app.pdf_generator as pg

        assert pg.REPORTLAB_AVAILABLE is True, (
            "test ortamında reportlab KURULU olmalı — kurulu değilse bu, "
            "sahte-başarı riskidir (mutation kapısı M4'ün ön koşulu)"
        )
        # WeasyPrint eksikliği ReportLab başarısını BOZMAMALI (owner şartı):
        monkeypatch.setattr(pg, "WEASYPRINT_AVAILABLE", False)

        ex, calc, params = _sentetik_girdiler()
        pdf = pg.generate_offer_pdf_bytes(
            ex, calc, params,
            customer_name="Sentetik Musteri",
            customer_company="Sentetik Sirket A.S.",
            offer_id=42,
        )
        assert pdf.startswith(b"%PDF"), "çıktı %PDF ile başlamalı"
        assert len(pdf) > 10_000, f"gerçek render beklenenden küçük: {len(pdf)} bayt"

        import pypdfium2

        doc = pypdfium2.PdfDocument(pdf)
        assert len(doc) >= 1, "PDF en az bir sayfa içermeli"
        metin = doc[0].get_textpage().get_text_bounded()
        assert "Sentetik" in metin and "42" in metin, (
            "sentetik snapshot verileri PDF metninde bulunmalı"
        )

    def test_reportlab_gercekten_kullanilan_motor(self, caplog):
        """Başarı yolunda kullanılan motor ReportLab'dır (audit/log kanıtı)."""
        import logging

        import app.pdf_generator as pg

        ex, calc, params = _sentetik_girdiler()
        with caplog.at_level(logging.INFO, logger="app.pdf_generator"):
            pdf = pg.generate_offer_pdf_bytes(ex, calc, params, offer_id=7)
        assert pdf.startswith(b"%PDF")
        assert any("ReportLab" in k.message and "Generated PDF" in k.message
                   for k in caplog.records), (
            "başarı yolunda ReportLab kullanım kaydı (audit) düşmeli"
        )
        # Başarı ReportLab'da bittiyse Playwright/WeasyPrint HİÇ denenmemiştir:
        assert not any("Playwright PDF generation" in k.message for k in caplog.records)


# ═══════════════════════════════════════════════════════════════════════════
# 2) Sync Playwright loop-guard + browser availability
# ═══════════════════════════════════════════════════════════════════════════

class TestPlaywrightFallbackKapilari:
    @pytest.mark.anyio
    async def test_sync_playwright_loop_icinde_reddedilir(self):
        """Çalışan asyncio loop içinden sync API çağrısı fail-fast RuntimeError."""
        from app.services.pdf_playwright import html_to_pdf_bytes_sync_v2

        with pytest.raises(RuntimeError) as h:
            html_to_pdf_bytes_sync_v2("<html><body>x</body></html>")
        assert "asyncio" in str(h.value), "hata mesajı loop yasağını açıkça söylemeli"

    def test_browser_bulunamazsa_fail_fast_ve_yol_sizmaz(self, tmp_path):
        """Chromium binary'si yoksa launch hatası PlaywrightBrowserUnavailable
        olarak sınıflandırılır; exception mesajında/zincirinde fiziksel yol
        YOK (`from None` orijinal yol-taşıyan hatayı keser)."""
        from app.services import pdf_playwright as pp

        sahte_yol = str(tmp_path / "olmayan" / "chrome.exe")
        sahte_p = MagicMock()
        sahte_p.chromium.launch.side_effect = Exception(
            f"BrowserType.launch: Executable doesn't exist at {sahte_yol}"
        )
        sahte_cm = MagicMock()
        sahte_cm.__enter__ = MagicMock(return_value=sahte_p)
        sahte_cm.__exit__ = MagicMock(return_value=False)

        with patch("playwright.sync_api.sync_playwright", return_value=sahte_cm):
            with pytest.raises(pp.PlaywrightBrowserUnavailable) as h:
                pp.html_to_pdf_bytes_sync_v2("<html><body>x</body></html>")

        mesaj = str(h.value)
        assert str(tmp_path) not in mesaj and "chrome.exe" not in mesaj, (
            "fiziksel yol exception mesajına sızmamalı"
        )
        assert h.value.__cause__ is None and h.value.__suppress_context__, (
            "orijinal yol-taşıyan hata `from None` ile kesilmeli"
        )

    def test_debug_html_dump_kaldirildi(self):
        """S5-R03A: render edilen HTML artık diske DUMP edilmez (temp artığı +
        path log sızıntısı yasağı)."""
        kaynak = (REPO_KOK / "backend" / "app" / "services" / "pdf_playwright.py").read_text(
            encoding="utf-8"
        )
        assert "debug_rendered" not in kaynak, "debug HTML dump geri gelmiş"

    def test_mutation_loop_guard_kaldirilirsa_kirilir(self):
        """AST kapısı: html_to_pdf_bytes_sync_v2 gövdesi loop-guard'ı çağırmalı."""
        fn = _fonksiyon_ast(
            REPO_KOK / "backend" / "app" / "services" / "pdf_playwright.py",
            "html_to_pdf_bytes_sync_v2",
        )
        cagrilar = {
            d.func.id
            for d in ast.walk(fn)
            if isinstance(d, ast.Call) and isinstance(d.func, ast.Name)
        }
        assert "_calisan_asyncio_loop_var" in cagrilar, (
            "sync Playwright loop-guard'ı kaldırılamaz (S5-R03A mutation kapısı)"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 3) Endpoint: event-loop bloke olmaz (AST + gerçek ASGI davranışı)
# ═══════════════════════════════════════════════════════════════════════════

class TestEndpointExecutorKapisi:
    def test_mutation_uretim_dogrudan_loop_icinde_cagrilirsa_kirilir(self):
        """AST kapısı: generate_pdf_for_offer içinde generate_and_store_offer_pdf
        çağrısı YALNIZ iç yardımcı fonksiyonda (executor'a verilen) olabilir;
        coroutine gövdesinde doğrudan çağrı = mutation → FAIL."""
        fn = _fonksiyon_ast(REPO_KOK / "backend" / "app" / "main.py", "generate_pdf_for_offer")

        ic_fonksiyonlar = [d for d in ast.walk(fn)
                           if isinstance(d, ast.FunctionDef) and d is not fn]
        ic_satirlar: set[int] = set()
        for icf in ic_fonksiyonlar:
            ic_satirlar.update(range(icf.lineno, (icf.end_lineno or icf.lineno) + 1))

        dogrudan = [
            d.lineno
            for d in ast.walk(fn)
            if isinstance(d, ast.Call)
            and isinstance(d.func, ast.Name)
            and d.func.id == "generate_and_store_offer_pdf"
            and d.lineno not in ic_satirlar
        ]
        assert not dogrudan, (
            f"generate_and_store_offer_pdf coroutine gövdesinde DOĞRUDAN çağrılmış "
            f"(satır {dogrudan}) — üretim executor'a taşınmalı (S5-R03A)"
        )

        # Pozitif kanıt: run_in_executor(_pdf_executor, ...) çağrısı mevcut.
        exec_cagrilari = [
            d
            for d in ast.walk(fn)
            if isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and d.func.attr == "run_in_executor"
            and any(isinstance(a, ast.Name) and a.id == "_pdf_executor" for a in d.args)
        ]
        assert exec_cagrilari, "üretim _pdf_executor üzerinden koşmalı (S5-R03A)"

    @pytest.mark.anyio
    async def test_event_loop_uretim_sirasinda_bloke_olmaz(self, db, storage_tmp):
        """Gerçek ASGI çağrısı: üretim executor thread'inde 0.3 sn sürerken
        event-loop'taki heartbeat görevi ilerleyebilmeli."""
        import httpx

        from app.main import app as fastapi_app
        from app.database import get_db

        offer = _teklif(db)
        fastapi_app.dependency_overrides[get_db] = lambda: db

        kalp_atisi = {"sayi": 0}

        async def _kalp():
            while True:
                kalp_atisi["sayi"] += 1
                await asyncio.sleep(0.02)

        def _yavas_uretici(**kw):
            time.sleep(0.3)  # executor thread'inde koşar; loop'u bloklamamalı
            from app.services.storage import get_storage
            return get_storage().put_bytes(
                f"offers/{kw['offer_id']}/offer.pdf", b"%PDF-yavas-sentetik", "application/pdf"
            )

        try:
            with patch("app.pdf_generator.generate_and_store_offer_pdf",
                       side_effect=_yavas_uretici):
                gorev = asyncio.get_running_loop().create_task(_kalp())
                try:
                    transport = httpx.ASGITransport(app=fastapi_app)
                    async with httpx.AsyncClient(
                        transport=transport, base_url="http://test"
                    ) as ac:
                        r = await ac.post(f"/offers/{offer.id}/generate-pdf")
                finally:
                    gorev.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await gorev
        finally:
            fastapi_app.dependency_overrides.clear()

        assert r.status_code == 200, r.text
        assert kalp_atisi["sayi"] >= 5, (
            f"üretim sırasında event-loop bloke oldu (heartbeat={kalp_atisi['sayi']})"
        )


class TestExecutorBackpressureVeTimeout:
    """S5-R03A adversarial doğrulama düzeltmeleri: offers üretimi paylaşılan
    havuzun iznini (_pdf_semaphore) tüketmeli ve _PDF_RENDER_TIMEOUT ile
    sınırlanmalı — izinsiz işgal simple'ın 429 sözleşmesini bypass eder,
    timeout'suz üretim per-offer kilidi süresiz tutar."""

    def test_mutation_semaphore_veya_timeout_kaldirilirsa_kirilir(self):
        fn = _fonksiyon_ast(REPO_KOK / "backend" / "app" / "main.py", "generate_pdf_for_offer")
        kaynak = ast.unparse(fn)
        assert "_pdf_semaphore.acquire" in kaynak, (
            "offers üretimi _pdf_semaphore iznini tüketmeli (S5-R03A)"
        )
        assert "_pdf_semaphore.release" in kaynak, "izin finally'de bırakılmalı"
        assert "_PDF_RENDER_TIMEOUT" in kaynak, (
            "üretim _PDF_RENDER_TIMEOUT ile sınırlanmalı (asılı üretim kilidi "
            "süresiz tutamaz)"
        )

    def test_semaphore_doluyken_429(self, client, db, storage_tmp, monkeypatch):
        """DIKKAT: global _pdf_semaphore'a dışarıdan asyncio.run ile acquire
        YAPILMAZ — asyncio.Semaphore ilk beklemeli kullanımda loop'a bağlanır
        ve TestClient'ların portal loop'ları arasında 'bound to a different
        event loop' üretir (tam-suite'te gerçek yaşandı). İzinsiz durumu taze
        bir Semaphore(0) ile temsil ederiz; endpoint kendi loop'unda bekleyip
        2 sn'de 429'a düşer."""
        import app.main as m

        offer = _teklif(db)
        monkeypatch.setattr(m, "_pdf_semaphore", asyncio.Semaphore(0))

        with patch("app.pdf_generator.generate_and_store_offer_pdf") as sahte:
            r = client.post(f"/offers/{offer.id}/generate-pdf")
        assert r.status_code == 429, r.text
        assert "too_many_requests" in r.text
        sahte.assert_not_called()
        db.refresh(offer)
        assert offer.pdf_ref is None

    def test_timeout_504_kilit_serbest_pdf_ref_none(self, client, db, storage_tmp, monkeypatch):
        import app.main as m

        offer = _teklif(db)
        monkeypatch.setattr(m, "_PDF_RENDER_TIMEOUT", 1)

        def _asili_uretici(**kw):
            time.sleep(3)  # timeout'tan uzun — executor'da asili kalir
            return "gec-kalan-ref"

        with patch("app.pdf_generator.generate_and_store_offer_pdf",
                   side_effect=_asili_uretici):
            r = client.post(f"/offers/{offer.id}/generate-pdf")

        assert r.status_code == 504, r.text
        assert "render_timeout" in r.text
        db.refresh(offer)
        assert offer.pdf_ref is None, "timeout'ta pdf_ref YAZILMAMALI"

        # Kilit with-bloğu çıkışında bırakılmış olmalı: yeni istek 409
        # generation_in_progress DEĞİL, normal üretim yoluna girebilmeli.
        with patch("app.pdf_generator.generate_and_store_offer_pdf") as hizli:
            from app.services.storage import get_storage

            hizli.side_effect = lambda **kw: get_storage().put_bytes(
                f"offers/{kw['offer_id']}/offer.pdf", b"%PDF-hizli", "application/pdf"
            )
            r2 = client.post(f"/offers/{offer.id}/generate-pdf")
        assert r2.status_code == 200, (
            f"timeout sonrası kilit serbest olmalıydı: {r2.status_code} {r2.text[:200]}"
        )

        # Asılı thread'in bitmesini bekle ki sonraki testlere sızmasın.
        time.sleep(2.5)


# ═══════════════════════════════════════════════════════════════════════════
# 4) Fail-closed: kontrollü hata, pdf_ref=None, sızıntı yok, artık yok
# ═══════════════════════════════════════════════════════════════════════════

class TestFailClosedVeSizinti:
    def test_tum_motorlar_dusunce_kontrollu_500(self, client, db, storage_tmp):
        offer = _teklif(db)

        with patch(
            "app.pdf_generator.generate_and_store_offer_pdf",
            side_effect=RuntimeError(
                "ic detay: C:/cok/gizli/yol/motor.dll yuklenemedi"
            ),
        ):
            r = client.post(f"/offers/{offer.id}/generate-pdf")

        assert r.status_code == 500
        govde = r.text
        # İç exception ayrıntısı ve fiziksel yol RESPONSE'a sızmamalı:
        assert "gizli" not in govde and "motor.dll" not in govde and "C:/" not in govde
        assert "beklenmeyen bir hata" in govde, "kontrollü generic mesaj dönmeli"

        db.refresh(offer)
        assert offer.pdf_ref is None, "başarısızlıkta pdf_ref YAZILMAMALI"
        artiklar = [p for p in storage_tmp.rglob("*.tmp")]
        assert artiklar == [], f"temp artığı kaldı: {artiklar}"
        assert not (storage_tmp / "offers" / str(offer.id) / "offer.pdf").exists()

    def test_bos_cikti_publish_ve_pdf_ref_engellenir(self, client, db, storage_tmp):
        """Mutation kapısı M3: motor boş/PDF-olmayan bytes döndürürse dosya
        yayımlanmaz, pdf_ref commit edilmez (sonraki isteği kalıcı 409'a
        düşürecek geçersiz artifact oluşamaz)."""
        offer = _teklif(db)

        for bozuk in (b"", b"tiny", b"HTML degil PDF hic degil"):
            with patch("app.pdf_generator.generate_offer_pdf_bytes", return_value=bozuk):
                r = client.post(f"/offers/{offer.id}/generate-pdf")
            assert r.status_code == 500, f"bozuk çıktı {bozuk!r} kabul edildi"
            db.refresh(offer)
            assert offer.pdf_ref is None
            assert not (storage_tmp / "offers" / str(offer.id) / "offer.pdf").exists(), (
                f"geçersiz çıktı {bozuk!r} publish edilmiş"
            )

    def test_reportlab_mock_ile_dusurulunce_sahte_basari_yok(self, monkeypatch, tmp_path):
        """Mutation kapısı M4: ReportLab kullanılamaz + fallback'ler kullanılamaz
        → sahte %PDF üretilmez, kontrollü RuntimeError yükselir."""
        import app.pdf_generator as pg
        from app.services.pdf_playwright import PlaywrightBrowserUnavailable

        monkeypatch.setattr(pg, "REPORTLAB_AVAILABLE", False)
        monkeypatch.setattr(pg, "WEASYPRINT_AVAILABLE", False)

        def _browser_yok(_html):
            raise PlaywrightBrowserUnavailable("Playwright fallback kullanilamaz.")

        monkeypatch.setattr(pg, "_html_to_pdf_playwright", _browser_yok)

        ex, calc, params = _sentetik_girdiler()
        with pytest.raises(RuntimeError):
            pg.generate_offer_pdf_bytes(ex, calc, params, offer_id=1)


# ═══════════════════════════════════════════════════════════════════════════
# 5) Build zinciri pinleri (mutation kapısı M1)
# ═══════════════════════════════════════════════════════════════════════════

class TestBuildZinciriPini:
    def test_build_desktop_reportlab_toplamasi_pinli(self):
        """build-desktop.bat'tan ReportLab collection kaldırılırsa FAIL.
        (Packaged smoke ayrıca PYZ envanterini gerçek exe'de doğrular —
        scripts/packaged_pdf_smoke.py; bu test kaynak-seviyesi çift kilittir.)"""
        icerik = (REPO_KOK / "build-desktop.bat").read_text(encoding="utf-8", errors="replace")
        # DIKKAT: naif substring araması YETMEZ — açıklama yorumu (:: ile
        # başlayan) aynı diziyi içerir ve komut satırı silinse bile testi
        # yeşil bırakırdı (mutasyon provasında GERÇEK yakalandı). Yalnız
        # yorum-olmayan KOMUT satırı sayılır.
        komut_satirlari = [
            s for s in icerik.splitlines()
            if s.strip().startswith("--collect-submodules reportlab")
            and not s.lstrip().startswith("::")
        ]
        assert komut_satirlari, (
            "kanonik build zincirinin PyInstaller KOMUTUNDA "
            "'--collect-submodules reportlab' satırı olmalı (S5-R03A; "
            "S5-R03 HARD STOP kök nedeni; yorumdaki geçiş SAYILMAZ)"
        )

    def test_weasyprint_dll_ayrintisi_loglanmiyor(self):
        """Bölüm 4: WeasyPrint import hatasının DLL adı/yol ayrıntısı loga
        yazılmaz — pdf_generator import bloğu exception metnini basmamalı."""
        kaynak = (REPO_KOK / "backend" / "app" / "pdf_generator.py").read_text(encoding="utf-8")
        agac = ast.parse(kaynak)
        # Modül düzeyindeki try/except (weasyprint bloğu) handler'larını bul:
        for dugum in agac.body:
            if isinstance(dugum, ast.Try):
                govde_kaynagi = ast.get_source_segment(kaynak, dugum) or ""
                if "weasyprint" not in govde_kaynagi:
                    continue
                for handler in dugum.handlers:
                    h_kaynak = ast.get_source_segment(kaynak, handler) or ""
                    assert "{e}" not in h_kaynak and "str(e)" not in h_kaynak, (
                        "WeasyPrint import handler'ı exception metnini (DLL adı/yol "
                        "içerebilir) loglamamalı — S5-R03A sızıntı sözleşmesi"
                    )
                return
        raise AssertionError("pdf_generator modül düzeyi weasyprint try bloğu bulunamadı")
