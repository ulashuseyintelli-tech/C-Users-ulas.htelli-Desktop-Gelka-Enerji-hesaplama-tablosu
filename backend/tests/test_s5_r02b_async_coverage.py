"""
S5-R02B — Async kapsam sözleşmesi ve mutation kapıları.

88 async test (8 dosya) repoda async plugin etkin olmadığı için ÖTEDEN BERİ
hiç koşmuyordu ("async def function and no async plugin" skip'i). Aktivasyon:
`pytest.mark.anyio` + conftest'te parametresiz `anyio_backend` fixture'ı.

Bu dosyadaki kapılar (owner R02B Bölüm 7):
- Beklenen 88 kimlikten biri kaybolursa FAIL (dosya başına AST sayımı).
- AnyIO backend asyncio dışına kayarsa FAIL.
- Backend parametrize edilirse (88 -> 176 riski) FAIL.
- Marker kaldırılırsa / async test sessizce skip'e dönerse: conftest'teki
  `pytest_collection_modifyitems` nöbetçisi koşuyu UsageError ile KESER
  (mutation kanıtı: marker'sız geçici async test ERROR üretti).
- Coroutine çağrılmadan sahte PASS oluşursa FAIL (davranışsal nöbetçi çifti).
- Event-loop testler arasında taşınırsa FAIL (loop kimliği nöbetçisi).
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

pytestmark = pytest.mark.anyio

TESTS = Path(__file__).resolve().parent

# Dosya başına BEKLENEN async test sayısı — biri kaybolursa bu kapı kırılır.
BEKLENEN_ASYNC: dict[str, int] = {
    "test_lc_failure_matrix.py": 30,
    "test_lc_scenario_runner.py": 22,
    "test_lc_checkpoint_gnk.py": 16,
    "test_lc_multi_instance.py": 8,
    "test_epias_client.py": 4,
    "test_lc_write_safety.py": 4,
    "test_lc_integration.py": 3,
    "test_lc_chaos_payload.py": 1,
}
BEKLENEN_TOPLAM = 88


def _async_testler(dosya: str) -> list[str]:
    agac = ast.parse((TESTS / dosya).read_text(encoding="utf-8"))
    adlar: list[str] = []

    def gez(dugum, onek=""):
        for d in getattr(dugum, "body", []):
            if isinstance(d, ast.ClassDef):
                gez(d, d.name + "::")
            elif isinstance(d, ast.AsyncFunctionDef) and d.name.startswith("test_"):
                adlar.append(onek + d.name)

    gez(agac)
    return adlar


class TestAsyncSayimKapisi:
    def test_dosya_basina_sayim_sabit(self):
        """Beklenen 88 kimlikten biri kaybolursa/kayarsa burasi kirilir."""
        gercek = {d: len(_async_testler(d)) for d in BEKLENEN_ASYNC}
        assert gercek == BEKLENEN_ASYNC, (
            f"async test sayimi kaydi: {gercek} != {BEKLENEN_ASYNC}"
        )
        assert sum(gercek.values()) == BEKLENEN_TOPLAM

    def test_her_dosya_anyio_pytestmark_tasiyor(self):
        """Marker herhangi bir dosyadan kaldirilirsa burasi kirilir (AST)."""
        for dosya in BEKLENEN_ASYNC:
            agac = ast.parse((TESTS / dosya).read_text(encoding="utf-8"))
            atamalar = [
                ast.unparse(d.value)
                for d in agac.body
                if isinstance(d, ast.Assign)
                and any(getattr(h, "id", "") == "pytestmark" for h in d.targets)
            ]
            assert any("anyio" in a for a in atamalar), (
                f"{dosya}: modul duzeyi anyio pytestmark yok"
            )

    def test_nobetci_hook_conftestte_duruyor(self):
        """Sessiz-skip nobetcisi kaldirilirsa burasi kirilir."""
        kaynak = (TESTS / "conftest.py").read_text(encoding="utf-8")
        agac = ast.parse(kaynak)
        hook = [d for d in agac.body
                if isinstance(d, ast.FunctionDef)
                and d.name == "pytest_collection_modifyitems"]
        assert hook, "pytest_collection_modifyitems nobetcisi kaldirilmis"
        govde = ast.unparse(hook[0])
        assert "iscoroutinefunction" in govde and "UsageError" in govde


class TestBackendPinKapisi:
    def test_backend_exact_asyncio(self, anyio_backend):
        assert anyio_backend == "asyncio", (
            f"backend asyncio disina kaymis: {anyio_backend!r}"
        )

    def test_backend_fixture_parametresiz(self):
        """
        Fixture parametrize edilirse test kimlikleri `[asyncio]`/`[trio]`
        soneki alir ve sayi 176'ya cikabilir. Parametresiz kalmali.
        """
        kaynak = (TESTS / "conftest.py").read_text(encoding="utf-8")
        agac = ast.parse(kaynak)
        fx = next(
            d for d in agac.body
            if isinstance(d, ast.FunctionDef) and d.name == "anyio_backend"
        )
        assert fx.args.args == [] or [a.arg for a in fx.args.args] == [], (
            "anyio_backend fixture'i parametre almamali"
        )
        for dek in fx.decorator_list:
            metin = ast.unparse(dek)
            assert "params" not in metin, f"fixture parametrize edilmis: {metin}"
        assert "trio" not in kaynak.split("def anyio_backend")[1][:200]


# ── Davranışsal nöbetçi: coroutine GERÇEKTEN yürüyor ────────────────────────
# Sahte-PASS senaryosu: bir runner coroutine'i çağırmadan testi geçirirse
# `_calisti` bayrağı yazılmaz ve sync doğrulayıcı kırılır. Sıra garantisi:
# pytest aynı dosyada tanım sırasıyla koşar.
_calisti: dict[str, object] = {}


async def test_coroutine_gercekten_yuruyor():
    await asyncio.sleep(0)
    _calisti["async_govde"] = True
    _calisti["loop"] = id(asyncio.get_running_loop())
    assert asyncio.get_running_loop().is_running()


async def test_loop_testler_arasi_tasinmiyor():
    """
    AnyIO her teste TAZE loop kurar; onceki testin loop kimligi farkli
    olmali. Loop taşınırsa (kapali loop yeniden kullanilirsa) burasi kirilir.
    """
    await asyncio.sleep(0)
    onceki = _calisti.get("loop")
    simdiki = id(asyncio.get_running_loop())
    assert onceki is not None, "ilk nobetci calismamis"
    assert simdiki != onceki, "event loop testler arasinda TASINMIS"
    # Bekleyen KULLANICI task'i birakmadigimizi kanitla. AnyIO TestRunner'in
    # kendi ic task'lari (`TestRunner._*`) HER ZAMAN pending gorunur — bunlar
    # runner altyapisidir, sizinti degildir ve dislanir.
    bekleyen = [
        t for t in asyncio.all_tasks()
        if not t.done()
        and "TestRunner" not in repr(t.get_coro())
        and t is not asyncio.current_task()
    ]
    assert bekleyen == [], f"beklenmeyen KULLANICI pending task: {bekleyen}"


def test_sahte_pass_yok():
    """Async govdeler yurumeden bu sync dogrulayici gecemez."""
    assert _calisti.get("async_govde") is True, (
        "coroutine govdesi HIC CALISMAMIS — sahte PASS tespit edildi"
    )
