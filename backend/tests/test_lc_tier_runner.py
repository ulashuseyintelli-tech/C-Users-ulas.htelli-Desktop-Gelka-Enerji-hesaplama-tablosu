"""
PR-10: Tier Runner — tier yürütme DOĞRULUK/KAPSAM kontrolü.

S5-R02A / OWNER KARARI C
════════════════════════
Bu meta-testler bir **kapsam/doğruluk** kontrolüdür. Subprocess içinde başka
pytest paketlerinin toplam çalışma süresi bir ÜRÜN PERFORMANS SLO'su DEĞİLDİR:
Python başlangıcını, diski, antivirüsü ve test ortamı yükünü ölçer.

Bu yüzden duvar saati PASS/FAIL kararını BELİRLEMEZ. Süre yine ölçülür ve
`INFORMATIONAL / NOT A PRODUCT SLO` olarak raporlanır.

Eski `10 s / 15 s / 30 s` eşikleri:
    HISTORICAL / UNRATIFIED / NOT ENFORCED
Bunlar hiçbir zaman gerçekte uygulanmadı (aşağıdaki iki kusur yüzünden) ve
ratifiye edilmiş bir ürün SLO'suna dayanmıyor. Yeni keyfî bütçe UYDURULMADI.
Gerçek bir performans SLO'su gerekirse ürün akışını ölçen ayrı benchmark
sözleşmesi ve fresh owner GO gerekir.

GİDERİLEN İKİ DETERMİNİSTİK KUSUR
─────────────────────────────────
1. Subprocess PATH'teki bare `python`'u çağırıyordu. venv'in Scripts dizini
   PATH'te olmadığı için bu, testleri çalıştırandan TAMAMEN FARKLI bir
   yorumlayıcıya düşüyordu (sistem Python 3.14; pytest/fastapi YOK) ve alt
   koşu HER ZAMAN başarısız oluyordu.
2. Test sayısı ayrıştırıcısı `p == "passed"` token EŞİTLİĞİ arıyordu. Özette
   başka kategori varsa (`316 passed, 9 skipped`) token `"passed,"` olur,
   eşitlik hiç tutmaz ve sayım `0` kalırdı.

Bu ikisi yüzünden tier bütçeleri hiç ölçülmedi; "flaky" görünen hatalar
aslında %100 deterministikti.

SELF-SKIP KALDIRILDI (owner Bölüm 2): yavaşlık artık skip üretmez; alt test
düşerse FAIL, sayım çıkarılamazsa FAIL, gerçek timeout açık ERROR olur.
Sessiz kapsam kaybı sıfırdır.
"""
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import NamedTuple

import pytest

from backend.app.testing.perf_budget import (
    TestTier,
    DEFAULT_BUDGETS,
    files_for_tier,
)

REPO_KOK = Path(__file__).resolve().parent.parent.parent

# Alt koşunun tamamlanması için üst sınır. Bu bir BÜTÇE DEĞİLDİR: aşılırsa
# test başarılı sayılmaz, açık `TIMEOUT` hatası üretir.
SUBPROCESS_UST_SINIR_SN = 300


class TierSonuc(NamedTuple):
    """Bir tier alt koşusunun TAM sonucu — hiçbir alan kaybolmaz."""
    tier: TestTier
    cmd: list[str]
    returncode: int | None          # None => timeout
    collected: int
    passed: int
    failed: int
    errors: int
    skipped: int
    duration_seconds: float
    stdout: str
    stderr: str
    timeout: bool

    @property
    def executed(self) -> int:
        return self.passed + self.failed + self.errors + self.skipped

    def ozet(self) -> str:
        """Hata mesajlarinda alt kosunun ciktisi KAYBOLMAZ."""
        kuyruk = (self.stdout + self.stderr).strip().splitlines()[-25:]
        return (
            f"tier={self.tier.name} rc={self.returncode} timeout={self.timeout} "
            f"collected={self.collected} passed={self.passed} failed={self.failed} "
            f"errors={self.errors} skipped={self.skipped} "
            f"duration={self.duration_seconds:.2f}s\n"
            f"cmd={' '.join(self.cmd)}\n" + "\n".join(kuyruk)
        )


def _sayi(desen: str, metin: str) -> int:
    """
    pytest ozetinden kategori sayisini cikarir.

    Noktalama BAGIMSIZ (`passed,` / `passed`) ve cogul ozetlerde
    (`316 passed, 9 skipped`) dogru calisir — eski token esitligi bunlarda
    sessizce `0` donduruyordu.
    """
    m = re.search(rf"\b(\d+)\s+{desen}\b", metin)
    return int(m.group(1)) if m else 0


def _run_tier(tier: TestTier) -> TierSonuc:
    """Bir tier'i AYRI subprocess'te calistirir ve TAM sonucu dondurur."""
    files = files_for_tier(tier)
    cmd = [sys.executable, "-m", "pytest", *files,
           "-q", "--tb=line", "--no-header", "-p", "no:warnings"]

    if not files:
        return TierSonuc(tier, cmd, 0, 0, 0, 0, 0, 0, 0.0, "", "", False)

    t0 = time.perf_counter()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=SUBPROCESS_UST_SINIR_SN, cwd=str(REPO_KOK))
        sure = time.perf_counter() - t0
        cikti = r.stdout + r.stderr
        return TierSonuc(
            tier=tier, cmd=cmd, returncode=r.returncode,
            collected=_sayi("(?:tests? )?collected", cikti) or (
                _sayi("passed", cikti) + _sayi("failed", cikti)
                + _sayi("errors?", cikti) + _sayi("skipped", cikti)),
            passed=_sayi("passed", cikti), failed=_sayi("failed", cikti),
            errors=_sayi("errors?", cikti), skipped=_sayi("skipped", cikti),
            duration_seconds=sure, stdout=r.stdout, stderr=r.stderr, timeout=False,
        )
    except subprocess.TimeoutExpired as e:
        sure = time.perf_counter() - t0
        return TierSonuc(
            tier=tier, cmd=cmd, returncode=None, collected=0, passed=0,
            failed=0, errors=0, skipped=0, duration_seconds=sure,
            stdout=(e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or ""),
            stderr=(e.stderr or b"").decode("utf-8", "replace") if isinstance(e.stderr, bytes) else (e.stderr or ""),
            timeout=True,
        )


def _tier_dogrula(sonuc: TierSonuc) -> None:
    """
    Owner Bolum 1 sozlesmesi. Duvar saati PASS/FAIL'i BELIRLEMEZ.
    """
    # Timeout basari gibi GOSTERILMEZ.
    assert not sonuc.timeout, f"TIMEOUT ({SUBPROCESS_UST_SINIR_SN}s):\n{sonuc.ozet()}"
    # Dogru yorumlayici
    assert sonuc.cmd[0] == sys.executable, (
        f"alt kosu yanlis yorumlayici kullaniyor: {sonuc.cmd[0]!r}"
    )
    # Exit code / failed / errors
    assert sonuc.returncode == 0, f"alt kosu basarisiz:\n{sonuc.ozet()}"
    assert sonuc.failed == 0, f"failed={sonuc.failed}:\n{sonuc.ozet()}"
    assert sonuc.errors == 0, f"errors={sonuc.errors}:\n{sonuc.ozet()}"
    # En az bir test GERCEKTEN calisti
    assert sonuc.executed > 0, f"hicbir test calismadi:\n{sonuc.ozet()}"
    assert sonuc.passed > 0, f"passed=0:\n{sonuc.ozet()}"
    # Ayristirici gercekten sayi cikarabildi (eski kusurun regresyon kapisi)
    assert sonuc.collected >= sonuc.executed, (
        f"collected({sonuc.collected}) < executed({sonuc.executed}):\n{sonuc.ozet()}"
    )
    # Cikti kaybolmadi
    assert (sonuc.stdout + sonuc.stderr).strip(), "alt kosu ciktisi KAYBOLDU"

    # SURE TELEMETRY'SI — bilgilendirme; PASS/FAIL'i etkilemez.
    tarihsel = DEFAULT_BUDGETS.get(sonuc.tier)
    tarihsel_sn = getattr(tarihsel, "max_seconds", None)
    print(
        f"[TIER TELEMETRY] tier={sonuc.tier.name} collected={sonuc.collected} "
        f"executed={sonuc.executed} passed={sonuc.passed} failed={sonuc.failed} "
        f"skipped={sonuc.skipped} duration_seconds={sonuc.duration_seconds:.2f} "
        f"env={sys.executable} "
        f"historical_budget_s={tarihsel_sn} "
        f"status=INFORMATIONAL/NOT_A_PRODUCT_SLO "
        f"(historical budget: HISTORICAL/UNRATIFIED/NOT_ENFORCED)"
    )


class TestTierSmoke:
    """Tier-0 dogruluk kontrolu (duvar saati BAGLAYICI DEGIL)."""

    def test_smoke_tier_dogru_calisiyor(self):
        _tier_dogrula(_run_tier(TestTier.SMOKE))


class TestTierCore:
    """Tier-1 dogruluk kontrolu (duvar saati BAGLAYICI DEGIL)."""

    def test_core_tier_dogru_calisiyor(self):
        _tier_dogrula(_run_tier(TestTier.CORE))


class TestTierConcurrency:
    """Tier-2 dogruluk kontrolu (duvar saati BAGLAYICI DEGIL)."""

    def test_concurrency_tier_dogru_calisiyor(self):
        _tier_dogrula(_run_tier(TestTier.CONCURRENCY))


class TestZeroFlaky:
    """Tum tier'lar tutarli sekilde gecer (kapsam invariant'i)."""

    def test_all_tiers_pass(self):
        for tier in (TestTier.SMOKE, TestTier.CORE, TestTier.CONCURRENCY):
            if not files_for_tier(tier):
                continue
            _tier_dogrula(_run_tier(tier))


class TestAyristiriciSozlesmesi:
    """
    MUTATION KAPISI: ayristirici noktalama ve cogul ozetlerde calismali.
    Eski `p == "passed"` token esitligi bu vakalarda `0` donduruyordu.
    """

    @pytest.mark.parametrize("satir,beklenen", [
        ("27 passed in 1.20s", 27),
        ("316 passed, 9 skipped in 3.59s", 316),
        ("1 failed, 12 passed, 2 skipped in 4.00s", 12),
        ("5 passed, 1 xfailed in 0.50s", 5),
        ("no tests ran in 0.01s", 0),
    ])
    def test_passed_sayimi_dogru_ayristirilir(self, satir, beklenen):
        assert _sayi("passed", satir) == beklenen

    def test_diger_kategoriler_de_ayristirilir(self):
        satir = "1 failed, 12 passed, 2 skipped, 3 errors in 4.00s"
        assert _sayi("failed", satir) == 1
        assert _sayi("skipped", satir) == 2
        assert _sayi("errors?", satir) == 3

    def test_duvar_saati_assertion_olarak_kullanilmiyor(self):
        """
        Owner Karar C: sure PASS/FAIL'i belirlememeli. Bu kapi, dosyaya
        yeniden bir sure-esigi assertion'i eklenirse kirilir.
        """
        import ast

        kaynak = Path(__file__).read_text(encoding="utf-8")
        agac = ast.parse(kaynak)
        # Bu kapinin KENDI govdesi denetim disi tutulur — aksi halde
        # yasakli metinleri denetleyen assert'ler kendi kendini yakalar.
        kapi = next(
            d for d in ast.walk(agac)
            if isinstance(d, ast.FunctionDef)
            and d.name == "test_duvar_saati_assertion_olarak_kullanilmiyor"
        )
        kapi_satirlari = set(range(kapi.lineno, (kapi.end_lineno or kapi.lineno) + 1))
        for dugum in ast.walk(agac):
            if not isinstance(dugum, ast.Assert) or dugum.lineno in kapi_satirlari:
                continue
            metin = ast.unparse(dugum.test)
            assert "duration_seconds" not in metin, (
                f"sure bir assertion'da kullanilmis: {metin}"
            )
            assert "max_seconds" not in metin and "budget" not in metin.lower(), (
                f"butce bir assertion'da kullanilmis: {metin}"
            )

    def test_self_skip_geri_gelmedi(self):
        """Owner Bolum 2: yavaslik skip URETMEZ."""
        import ast

        kaynak = Path(__file__).read_text(encoding="utf-8")
        agac = ast.parse(kaynak)
        skipler = [
            ast.unparse(d) for d in ast.walk(agac)
            if isinstance(d, ast.Call)
            and ast.unparse(d.func).endswith("skip")
        ]
        assert skipler == [], f"self-skip geri gelmis: {skipler}"
