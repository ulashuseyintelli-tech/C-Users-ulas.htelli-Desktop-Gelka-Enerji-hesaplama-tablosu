"""
S5-R01A — `invoice_total_raw` fail-closed sözleşmesi (R2 gross-misread guard).

R01'de kapı pozitiflik kontrolü kullanıyordu; eksik/sıfır ham toplam sapmayı
SESSİZCE atlıyor ve sapmalı teklif persist ediliyordu:

    POST /offers?...invoice_total_raw=0  ->  200   (UAT ağ izi)

Owner Bölüm 3 sözleşmesi: `invoice_total_raw` zorunlu, sayısal, sonlu, > 0.
Reddedilenler: missing, null, empty, 0, negatif, NaN, ±Infinity, non-numeric.

POZİTİF YOL NEDEN GERÇEK HTTP İLE TEST EDİLİYOR
-----------------------------------------------
Başarılı `POST /offers` mevcut `TestClient` harness'ında ASILIYOR (async
endpoint + senkron SQLAlchemy + paylaşılan session; pristine HEAD'de de aynı).
Bu yüzden persist eden yollar AST kanıtına bırakılmaz: ayrı bir uvicorn
subprocess'i disposable SQLite üzerinde gerçek HTTP ile sürülür ve test
sonunda süreç/port artığı bırakmaz.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from decimal import Decimal
from pathlib import Path

import pytest

from app.main import (
    HamToplamGecersiz,
    PARA_TOLERANSI,
    extraction_mismatch_contract,
    invoice_total_raw_error,
    parse_invoice_total_raw,
)

BACKEND = Path(__file__).resolve().parents[1]

# computed_total = 282500 + 56500 = 339000  (sapmasız referans)
HESAP = {
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
COMPUTED_TOTAL = Decimal("339000")

# Tüketim × birim fiyat = enerji bedeli (cross-check sapması SIFIR olsun).
GOVDE_TEMEL = {
    "extraction": {
        "vendor": "Sentetik", "invoice_period": "2026-07",
        "consumption_kwh": {"value": 125000.0, "confidence": 1.0},
        "current_active_unit_price_tl_per_kwh": {"value": 2.0, "confidence": 1.0},
    },
    "calculation": HESAP,
    "params": {"weighted_ptf_tl_per_mwh": 2974.1, "yekdem_tl_per_mwh": 364.0,
               "agreement_multiplier": 1.01},
}


def _ham_icin(delta_yuzde: str) -> str:
    """
    Verilen delta yuzdesini uretecek ham toplami dondurur (duz ondalik metin).

    DIKKAT: `computed / (1 + d/100)` bolmesi periyodik ondalik uretir ve
    esigin ±1e-27 yaninda kalir; ayrica `Decimal` bilimsel gosterime
    (`2.8250E+5`) dusebilir — URL'de `+` BOSLUGA cozulur ve deger gecersiz
    olur. Bu yuzden yalniz TAM temsil edilebilir yuzdeler kullanilir ve
    sonuc duz ondalik metne normalize edilir.
    """
    ham = COMPUTED_TOTAL / (Decimal(1) + Decimal(delta_yuzde) / Decimal(100))
    gercek = abs(COMPUTED_TOTAL - ham) / ham * Decimal(100)
    assert gercek == Decimal(delta_yuzde), (
        f"%{delta_yuzde} tam temsil edilemiyor (gercek={gercek}) — "
        "esik testleri icin _bant_ozel() kullanin"
    )
    return format(ham.normalize(), "f")


def _bant_ozel(computed: str, ham: str, onay: bool = False):
    """
    Esik testleri icin TAM temsil edilebilir (computed, raw) cifti.

    Cross-check bandi kapatilir (tuketim/birim fiyat 0) ki yalniz TOPLAM
    bandi olculsun. Ornek: computed=110, raw=100 -> delta TAM %10.
    """
    return extraction_mismatch_contract(
        consumption_kwh=0.0, current_unit_price=0.0, current_energy_tl=0.0,
        current_vat_matrah_tl=float(Decimal(computed)), current_vat_tl=0.0,
        invoice_total_raw=Decimal(ham), operator_confirmed_warnings=onay,
    )


# ═══════════════════════════════════════════════════════════════════════════
# 1) parse_invoice_total_raw — sözleşme (saf, hızlı)
# ═══════════════════════════════════════════════════════════════════════════

class TestHamToplamDogrulama:
    @pytest.mark.parametrize("deger,sebep", [
        (None, "missing"),
        ("", "empty"),
        ("   ", "empty"),
        ("null", "null"),
        ("undefined", "null"),
        ("0", "non_positive"),
        ("0.00", "non_positive"),
        ("-1", "non_positive"),
        ("-0.01", "non_positive"),
        ("NaN", "not_finite"),
        ("Infinity", "not_finite"),
        ("-Infinity", "not_finite"),
        ("abc", "non_numeric"),
        ("1,5", "non_numeric"),
        ("12 34", "non_numeric"),
    ])
    def test_gecersiz_degerler_reddedilir(self, deger, sebep):
        with pytest.raises(HamToplamGecersiz) as e:
            parse_invoice_total_raw(deger)
        assert e.value.sebep == sebep

    @pytest.mark.parametrize("deger,beklenen", [
        ("1", Decimal("1")),
        ("0.01", Decimal("0.01")),
        ("339000", Decimal("339000")),
        ("339000.55", Decimal("339000.55")),
        (339000.55, Decimal("339000.55")),
        ("  339000  ", Decimal("339000")),
    ])
    def test_gecerli_degerler_kesin_ondalik_doner(self, deger, beklenen):
        assert parse_invoice_total_raw(deger) == beklenen

    def test_truthiness_kullanilmiyor(self):
        """`0` truthy DEĞİL ama `0.0001` truthy — ikisi de doğru sınıflanmalı."""
        with pytest.raises(HamToplamGecersiz):
            parse_invoice_total_raw("0")
        assert parse_invoice_total_raw("0.0001") == Decimal("0.0001")

    def test_hata_sozlesmesi_pii_icermez(self):
        govde = invoice_total_raw_error("non_positive")
        metin = json.dumps(govde, ensure_ascii=False)
        assert govde["error"]["code"] == "invalid_invoice_total_raw"
        assert govde["error"]["reason"] == "non_positive"
        assert "339000" not in metin


# ═══════════════════════════════════════════════════════════════════════════
# 2) Bant semantiği — KESİN ondalık, eşiklerde kayma yok
# ═══════════════════════════════════════════════════════════════════════════

def _bant(ham: Decimal, onay: bool = False):
    return extraction_mismatch_contract(
        consumption_kwh=125000.0, current_unit_price=2.0,
        current_energy_tl=HESAP["current_energy_tl"],
        current_vat_matrah_tl=HESAP["current_vat_matrah_tl"],
        current_vat_tl=HESAP["current_vat_tl"],
        invoice_total_raw=ham, operator_confirmed_warnings=onay,
    )


class TestBantSemantigi:
    def test_sapma_yok_gecer(self):
        s = _bant(COMPUTED_TOTAL)
        assert s.reddet is None and s.band == "pass"
        assert s.delta_percent == Decimal(0)

    def test_9_999_gecer(self):
        s = _bant_ozel("109.999", "100")     # delta TAM %9.999
        assert s.reddet is None, "%9.999 PASS olmali"
        assert s.band == "pass"
        assert s.delta_percent == Decimal("9.999")

    def test_10_onaysiz_ret_onayli_gecer(self):
        red = _bant_ozel("110", "100")       # delta TAM %10
        assert red.delta_percent == Decimal(10)
        assert red.reddet is not None and red.band == "confirmable"
        assert red.reddet["error"]["requires_operator_confirmation"] is True
        assert _bant_ozel("110", "100", onay=True).reddet is None

    def test_40_onaysiz_ret_onayli_gecer(self):
        red = _bant_ozel("140", "100")       # delta TAM %40
        assert red.delta_percent == Decimal(40)
        assert red.reddet is not None and red.band == "confirmable"
        assert _bant_ozel("140", "100", onay=True).reddet is None

    def test_40_001_onayli_olsa_bile_ret(self):
        for onay in (False, True):
            s = _bant_ozel("140.001", "100", onay=onay)   # delta TAM %40.001
            assert s.delta_percent == Decimal("40.001")
            assert s.reddet is not None, f"%40.001 onay={onay} iken de REDDEDILMELI"
            assert s.band == "blocking"
            assert s.reddet["error"]["requires_operator_confirmation"] is False

    def test_esiklerde_float_kaymasi_yok(self):
        """
        Binary float ile `110/100` gibi oranlar 9.999999...'a kayabilir ve
        bant SESSIZCE dususurdu. Decimal ile esik TAM tutmali.
        """
        for computed, ham, beklenen in (("110", "100", Decimal(10)),
                                        ("140", "100", Decimal(40)),
                                        ("109.999", "100", Decimal("9.999"))):
            s = _bant_ozel(computed, ham)
            assert s.delta_percent == beklenen, (
                f"{computed}/{ham}: delta {s.delta_percent} != {beklenen}"
            )

    def test_none_ham_toplam_bandi_atlar(self):
        """`/generate-pdf-simple` geriye dönük uyumluluğu — POST /offers KULLANMAZ."""
        s = _bant(None)
        assert s.reddet is None and s.band == "no_data"


# ═══════════════════════════════════════════════════════════════════════════
# 3) Gerçek HTTP entegrasyonu — disposable DB, subprocess uvicorn
# ═══════════════════════════════════════════════════════════════════════════

def _bos_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class CanliSunucu:
    """Disposable SQLite üzerinde gerçek uvicorn; kapanışta artık bırakmaz."""

    def __init__(self, kok: Path):
        self.kok = kok
        self.db = kok / "uat.db"
        self.port = _bos_port()
        self.proc: subprocess.Popen | None = None

    def baslat(self):
        runner = self.kok / "run.py"
        runner.write_text(
            "import os, sys\n"
            f"os.environ['DATABASE_URL'] = r'sqlite:///{self.db.as_posix()}'\n"
            f"os.environ['STORAGE_DIR'] = r'{(self.kok / 'storage').as_posix()}'\n"
            "os.environ['ENV'] = 'development'\n"
            "os.environ['API_KEY_ENABLED'] = 'false'\n"
            f"sys.path.insert(0, r'{BACKEND.as_posix()}')\n"
            f"os.chdir(r'{BACKEND.as_posix()}')\n"
            "import uvicorn\n"
            f"uvicorn.run('app.main:app', host='127.0.0.1', port={self.port}, "
            "workers=1, log_level='error')\n",
            encoding="utf-8",
        )
        self.proc = subprocess.Popen(
            [sys.executable, str(runner)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(200):  # ≤20 sn
            if self.proc.poll() is not None:
                raise RuntimeError("sunucu süreci beklenmedik şekilde sonlandı")
            try:
                self.get("/offers")
                return
            except Exception:
                time.sleep(0.1)
        raise RuntimeError("sunucu ayağa kalkmadı")

    def durdur(self):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=30)

    def _url(self, yol: str) -> str:
        return f"http://127.0.0.1:{self.port}{yol}"

    def get(self, yol: str):
        with urllib.request.urlopen(self._url(yol), timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8"))

    def post_offer(self, sorgu: str = "", govde: dict | None = None):
        veri = json.dumps(govde or GOVDE_TEMEL).encode("utf-8")
        istek = urllib.request.Request(
            self._url("/offers" + sorgu), data=veri, method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(istek, timeout=30) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def offer_sayisi(self) -> int:
        con = sqlite3.connect(f"file:{self.db.as_posix()}?mode=ro", uri=True)
        try:
            return con.execute("SELECT count(*) FROM offers").fetchone()[0]
        finally:
            con.close()

    def son_snapshot(self) -> dict:
        con = sqlite3.connect(f"file:{self.db.as_posix()}?mode=ro", uri=True)
        try:
            satir = con.execute(
                "SELECT extraction_result FROM offers ORDER BY id DESC LIMIT 1"
            ).fetchone()
        finally:
            con.close()
        return json.loads(satir[0])

    def artiklar(self) -> list[str]:
        kok = self.kok / "storage"
        if not kok.is_dir():
            return []
        return [str(p) for p in kok.rglob("*")
                if p.is_file() and (p.suffix == ".tmp" or p.suffix == ".pdf")]



def _alembic_yolu() -> Path:
    """
    Alembic betigini KOSAN YORUMLAYICIYA gore bulur.

    S5-R02 BULGUSU: burasi onceden `BACKEND/.venv/Scripts/alembic.exe` gibi
    KAYNAK AGACINA GORELI bir yol ariyordu. Izole bir git worktree'de (venv
    yok) bu dosya bulunmuyor ve 18 entegrasyon testi SESSIZCE skip'e
    dusuyordu — yani kapsam ortama gore kayboluyordu.

    Dogru capa, testleri fiilen calistiran yorumlayicinin Scripts/bin
    dizinidir; venv'in kaynak agacinda nerede durdugu onemsizdir.

    Bulunamazsa SKIP DEGIL, SETUP FAILURE uretilir: sessiz kapsam kaybi
    bir daha olusamaz (owner Bolum 8).
    """
    adaylar = [
        Path(sys.executable).parent / "alembic.exe",   # Windows venv
        Path(sys.executable).parent / "alembic",       # POSIX venv
    ]
    yoldan = shutil.which("alembic")
    if yoldan:
        adaylar.append(Path(yoldan))
    for aday in adaylar:
        if aday.exists():
            return aday
    raise RuntimeError(
        "alembic betigi bulunamadi (SETUP FAILURE, skip DEGIL). Arananlar: "
        + ", ".join(str(a) for a in adaylar)
    )

@pytest.fixture(scope="module")
def sunucu(tmp_path_factory):
    kok = tmp_path_factory.mktemp("s5r01a_http")
    (kok / "storage").mkdir()
    alembic = _alembic_yolu()
    ortam = dict(os.environ, DATABASE_URL=f"sqlite:///{(kok / 'uat.db').as_posix()}")
    sonuc = subprocess.run([str(alembic), "upgrade", "head"], cwd=str(BACKEND),
                           env=ortam, capture_output=True)
    assert sonuc.returncode == 0, sonuc.stderr.decode("utf-8", "replace")[-800:]

    s = CanliSunucu(kok)
    s.baslat()
    yield s
    s.durdur()


class TestGercekHttpKapisi:
    @pytest.mark.parametrize("sorgu,etiket", [
        ("", "missing"),
        ("?invoice_total_raw=", "empty"),
        ("?invoice_total_raw=null", "null"),
        ("?invoice_total_raw=0", "zero"),
        ("?invoice_total_raw=-5", "negative"),
        ("?invoice_total_raw=NaN", "nan"),
        ("?invoice_total_raw=Infinity", "posinf"),
        ("?invoice_total_raw=-Infinity", "neginf"),
        ("?invoice_total_raw=abc", "non_numeric"),
    ])
    def test_gecersiz_ham_toplam_4xx_ve_persist_yok(self, sunucu, sorgu, etiket):
        once = sunucu.offer_sayisi()
        kod, govde = sunucu.post_offer(sorgu)
        assert 400 <= kod < 500, f"{etiket}: 4xx bekleniyordu, {kod} geldi"
        assert govde["error"]["code"] == "invalid_invoice_total_raw"
        assert sunucu.offer_sayisi() == once, f"{etiket}: teklif PERSIST EDİLDİ"
        assert sunucu.artiklar() == [], f"{etiket}: storage artığı oluştu"

    def test_gecerli_ham_toplam_persist_eder(self, sunucu):
        once = sunucu.offer_sayisi()
        kod, govde = sunucu.post_offer("?invoice_total_raw=339000")
        assert kod == 200, govde
        assert sunucu.offer_sayisi() == once + 1

    def test_buyuk_sapma_onayli_olsa_bile_ret(self, sunucu):
        once = sunucu.offer_sayisi()
        kod, govde = sunucu.post_offer(
            "?invoice_total_raw=100000&operator_confirmed_warnings=true")
        assert 400 <= kod < 500
        assert govde["error"]["code"] == "extraction_mismatch"
        assert govde["error"]["requires_operator_confirmation"] is False
        assert sunucu.offer_sayisi() == once

    def test_orta_sapma_onaysiz_ret_onayli_gecer(self, sunucu):
        ham = _ham_icin("20")  # 282500 — TAM temsil edilebilir
        once = sunucu.offer_sayisi()
        kod, govde = sunucu.post_offer(f"?invoice_total_raw={ham}")
        assert 400 <= kod < 500
        assert govde["error"]["requires_operator_confirmation"] is True
        assert sunucu.offer_sayisi() == once

        kod2, _ = sunucu.post_offer(
            f"?invoice_total_raw={ham}&operator_confirmed_warnings=true")
        assert kod2 == 200
        assert sunucu.offer_sayisi() == once + 1

    def test_ai_snapshot_celiskisi_reddedilir(self, sunucu):
        """Client, snapshot'tan farklı ham toplam göndererek guard'ı düşüremez."""
        govde = json.loads(json.dumps(GOVDE_TEMEL))
        govde["extraction"]["invoice_total_with_vat_tl"] = {"value": 339000.0, "confidence": 1.0}
        once = sunucu.offer_sayisi()
        kod, yanit = sunucu.post_offer("?invoice_total_raw=250000", govde)
        assert 400 <= kod < 500
        assert yanit["error"]["code"] == "invoice_total_raw_conflict"
        assert sunucu.offer_sayisi() == once

    def test_ai_snapshot_otoritedir(self, sunucu):
        """Snapshot varsa client değeri onunla eşleşmeli; eşleşiyorsa geçer."""
        govde = json.loads(json.dumps(GOVDE_TEMEL))
        govde["extraction"]["invoice_total_with_vat_tl"] = {"value": 339000.0, "confidence": 1.0}
        once = sunucu.offer_sayisi()
        kod, yanit = sunucu.post_offer("?invoice_total_raw=339000", govde)
        assert kod == 200, yanit
        assert sunucu.offer_sayisi() == once + 1
        assert sunucu.son_snapshot()["_r2_guard"]["source"] == "extraction"

    def test_manuel_ham_toplam_snapshotta_exact(self, sunucu):
        kod, _ = sunucu.post_offer("?invoice_total_raw=339000.55")
        assert kod == 200
        guard = sunucu.son_snapshot()["_r2_guard"]
        assert guard["invoice_total_raw"] == "339000.55", "ham toplam BİREBİR saklanmalı"
        assert guard["source"] == "operator"

    def test_onay_provenance_snapshotta_exact(self, sunucu):
        ham = _ham_icin("20")  # 282500 — TAM temsil edilebilir
        kod, _ = sunucu.post_offer(
            f"?invoice_total_raw={ham}&operator_confirmed_warnings=true")
        assert kod == 200
        guard = sunucu.son_snapshot()["_r2_guard"]
        assert guard["band"] == "confirmable"
        assert guard["operator_confirmed_warnings"] is True
        assert guard["delta_percent"] is not None

    def test_sapmasiz_teklifte_onay_bayragi_tasinmaz(self, sunucu):
        """`<%10` için onay GEREKSİZ — yeniden kullanılabilir yetki gibi saklanmaz."""
        kod, _ = sunucu.post_offer(
            "?invoice_total_raw=339000&operator_confirmed_warnings=true")
        assert kod == 200
        guard = sunucu.son_snapshot()["_r2_guard"]
        assert guard["band"] == "pass"
        assert guard["operator_confirmed_warnings"] is False

    def test_tum_retlerde_pdf_ve_temp_artigi_sifir(self, sunucu):
        assert sunucu.artiklar() == []


# ═══════════════════════════════════════════════════════════════════════════
# 4) Frontend statik sözleşmesi — `0` sentinel geri gelmesin
# ═══════════════════════════════════════════════════════════════════════════

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"


def _kod(metin: str) -> str:
    cikti, blokta = [], False
    for satir in metin.splitlines():
        t = satir.strip()
        if blokta:
            if "*/" in t:
                blokta, t = False, t.split("*/", 1)[1]
            else:
                continue
        if t.startswith("//") or t.startswith("*"):
            continue
        if "/*" in t:
            once, _, sonra = t.partition("/*")
            if "*/" in sonra:
                t = once + sonra.split("*/", 1)[1]
            else:
                t, blokta = once, True
        t = t.split("//", 1)[0].strip()
        if t:
            cikti.append(t)
    return chr(10).join(cikti)


class TestFrontendSentinelYok:
    def test_app_tsx_sifir_sentinel_gondermiyor(self):
        kod = _kod((FRONTEND / "App.tsx").read_text(encoding="utf-8"))
        assert "(manualValues.invoice_total_raw || 0)" not in kod
        assert "invoice_total_with_vat_tl?.value || 0)" not in kod, (
            "AI modunda `|| 0` sentinel'i geri gelmiş"
        )

    def test_app_tsx_ham_toplami_zorunlu_kiliyor(self):
        kod = _kod((FRONTEND / "App.tsx").read_text(encoding="utf-8"))
        assert "hamFaturaToplami" in kod
        assert "hamFaturaToplami === undefined" in kod, (
            "istemci tarafı zorunluluk kapısı yok"
        )

    def test_api_ts_guard_zorunlu(self):
        kod = _kod((FRONTEND / "api.ts").read_text(encoding="utf-8"))
        assert "guard: { invoice_total_raw: number" in kod, "guard opsiyonel kalmış"
        assert "invoice_total_raw: guard.invoice_total_raw," in kod, (
            "ham toplam koşulsuz gönderilmiyor"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 5) Tek helper — mantık iki endpoint arasında kopyalanmadı
# ═══════════════════════════════════════════════════════════════════════════

class TestTekHelper:
    def test_bant_mantigi_tek_yerde(self):
        import ast

        kaynak = (BACKEND / "app" / "main.py").read_text(encoding="utf-8")
        agac = ast.parse(kaynak)
        tanimlar = [d for d in ast.walk(agac)
                    if isinstance(d, ast.FunctionDef) and d.name == "extraction_mismatch_contract"]
        assert len(tanimlar) == 1, "helper birden fazla kez tanımlanmış"

        # `requires_operator_confirmation` YALNIZ helper içinde üretilmeli.
        helper = tanimlar[0]
        helper_satirlari = set(range(helper.lineno, (helper.end_lineno or helper.lineno) + 1))
        disarida = [
            i for i, satir in enumerate(kaynak.splitlines(), start=1)
            if "requires_operator_confirmation" in satir and i not in helper_satirlari
        ]
        assert disarida == [], (
            f"bant mantığı helper DIŞINDA da üretiliyor (satır {disarida}) — kopya"
        )

    def test_her_iki_endpoint_de_helperi_cagiriyor(self):
        import ast

        kaynak = (BACKEND / "app" / "main.py").read_text(encoding="utf-8")
        agac = ast.parse(kaynak)
        cagiranlar = set()
        for d in ast.walk(agac):
            if not isinstance(d, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for c in ast.walk(d):
                if isinstance(c, ast.Call) and getattr(c.func, "id", None) == "extraction_mismatch_contract":
                    cagiranlar.add(d.name)
        assert "create_offer" in cagiranlar
        assert "generate_pdf_simple" in cagiranlar, (
            "`/generate-pdf-simple` paylaşılan helper'ı kullanmıyor"
        )

    def test_tolerans_makul(self):
        assert Decimal(0) < PARA_TOLERANSI <= Decimal("1")
