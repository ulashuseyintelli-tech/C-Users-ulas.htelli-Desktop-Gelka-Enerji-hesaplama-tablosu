"""
backend/conftest.py toplama korumasının hedefli testleri.

Kilitlenenler
-------------
1. collect_ignore, 2026-09-10'da doğrulanan 25 ad-hoc betiğin AÇIK ve göreli
   listesidir (ast ile okunur; ad-hoc dosyalar bu modülde ASLA import edilmez).
2. Listedeki her yol gerçekten var, pytest'in varsayılan keşif desenine
   (test_*.py) uyuyor ve tests/ altında değil — kanonik testler gizlenemez;
   glob ya da dizin girdisi yok.
3. Davranış izole geçici ağaçta sınanır: gerçek backend/conftest.py ve gerçek
   tests/conftest.py (SESSIZ ASYNC SKIP kapısı dahil) kopyalanır; 25 göreli
   yola, import edilirse YALNIZ işaret dosyası bırakan zararsız ikizler konur.
   - backend/'den yolsuz / ".", repo kökünden yolsuz / "backend" toplama
     bunları import etmez; meşru tests/ ve monitoring/tests toplanır.
   - Marker'sız async test hâlâ reddedilir (exit 4).
   - Açıkça verilen dosya YİNE import edilir (belgelenen sınır).
   - Listede olmayan yeni bir scripts/test_*.py toplanır (dizin topluca gizlenmez).

Çağrıldığı yerler: kanonik suite (backend/'den `python -m pytest tests`).
"""
import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent

# Doğrulanmış envanter — backend/conftest.py'deki liste ile BİLİNÇLİ olarak
# ikizdir: listeye ekleme/çıkarma bu envanter güncellenmeden geçemez.
BEKLENEN_ADHOC = (
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
    "scripts/test_api.py",
    "scripts/test_api_endpoint.py",
    "scripts/test_calculation.py",
    "scripts/test_gpt5.py",
    "scripts/test_html_invoice.py",
    "scripts/test_pipeline.py",
)

ISARET_EKI = ".ADHOC_IMPORT_EDILDI"
KAPI_MESAJI = "SESSIZ ASYNC SKIP ENGELLENDI"
BACKEND_MESRU = [
    "tests/test_mesru.py::test_mesru_senkron",
    "tests/test_mesru.py::test_mesru_async",
]
MONITORING_MESRU = ["monitoring/tests/test_mesru_monitoring.py::test_mesru_monitoring"]


def _collect_ignore_literali():
    agac = ast.parse((BACKEND / "conftest.py").read_text(encoding="utf-8"))
    for dugum in agac.body:
        if isinstance(dugum, ast.Assign) and any(
            isinstance(h, ast.Name) and h.id == "collect_ignore" for h in dugum.targets
        ):
            assert isinstance(dugum.value, ast.List), "collect_ignore düz bir liste literali olmalı"
            degerler = []
            for eleman in dugum.value.elts:
                assert isinstance(eleman, ast.Constant) and isinstance(eleman.value, str), (
                    "collect_ignore yalnız sabit metin içermeli (hesaplanan değer yok)"
                )
                degerler.append(eleman.value)
            return degerler
    raise AssertionError("backend/conftest.py içinde collect_ignore bulunamadı")


def test_liste_dogrulanmis_envanterle_birebir():
    degerler = _collect_ignore_literali()
    assert len(degerler) == len(set(degerler)), "yinelenen giriş var"
    assert sorted(degerler) == sorted(BEKLENEN_ADHOC)


def test_liste_glob_dizin_ve_kanonik_test_icermez():
    agac = ast.parse((BACKEND / "conftest.py").read_text(encoding="utf-8"))
    atananlar = {
        h.id
        for d in agac.body
        if isinstance(d, ast.Assign)
        for h in d.targets
        if isinstance(h, ast.Name)
    }
    assert "collect_ignore_glob" not in atananlar
    for giris in _collect_ignore_literali():
        yol = Path(giris)
        assert not set(giris) & set("*?[]"), f"glob karakteri: {giris}"
        assert yol.parent.as_posix() in (".", "scripts"), f"beklenmeyen konum: {giris}"
        assert yol.name.startswith("test_") and yol.suffix == ".py", f"keşif deseni dışı: {giris}"
        assert (BACKEND / yol).is_file(), f"listedeki dosya yok (envanteri güncelle): {giris}"


def _topla(cwd, *argumanlar):
    env = dict(os.environ)
    env.pop("PYTEST_ADDOPTS", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "--co", "-q", "-p", "no:cacheprovider", *argumanlar],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=180,
    )
    kimlikler = []
    for satir in r.stdout.splitlines():
        if not satir.strip() or satir.startswith(("=", "-")):
            break
        if "::" in satir and not satir.startswith((" ", "ERROR", "E ")):
            kimlikler.append(satir.strip())
    return r.returncode, kimlikler, r.stdout + r.stderr


def _isaretler(kok):
    return sorted(p.relative_to(kok).as_posix() for p in kok.rglob("*" + ISARET_EKI))


def _isaretleri_sil(kok):
    for p in kok.rglob("*" + ISARET_EKI):
        p.unlink()


@pytest.fixture(scope="module")
def izole_kok(tmp_path_factory):
    kok = tmp_path_factory.mktemp("adhoc_koruma")
    backend = kok / "backend"
    for dizin in (backend / "tests", backend / "scripts", kok / "monitoring" / "tests"):
        dizin.mkdir(parents=True)
    for paket in (backend, backend / "tests", backend / "scripts"):
        (paket / "__init__.py").write_text("", encoding="utf-8")
    shutil.copyfile(BACKEND / "conftest.py", backend / "conftest.py")
    shutil.copyfile(BACKEND / "tests" / "conftest.py", backend / "tests" / "conftest.py")
    (backend / "tests" / "test_mesru.py").write_text(
        "import pytest\n\n\n"
        "def test_mesru_senkron():\n    pass\n\n\n"
        "@pytest.mark.anyio\n"
        "async def test_mesru_async():\n    pass\n",
        encoding="utf-8",
    )
    (kok / "monitoring" / "tests" / "test_mesru_monitoring.py").write_text(
        "def test_mesru_monitoring():\n    pass\n", encoding="utf-8"
    )
    for giris in _collect_ignore_literali():
        # Zararsız ikiz: import edilirse YALNIZ işaret bırakır; toplanırsa
        # marker'sız async testi kapıyı tetikler (gerçek epias betikleri gibi).
        (backend / giris).write_text(
            "from pathlib import Path\n\n"
            f"Path(__file__).with_name(Path(__file__).name + {ISARET_EKI!r}).write_text('x')\n\n\n"
            "async def test_adhoc_markersiz():\n    pass\n",
            encoding="utf-8",
        )
    return kok


@pytest.mark.parametrize(
    ("cwd", "argumanlar", "beklenen"),
    [
        ("backend", (), BACKEND_MESRU),
        ("backend", (".",), BACKEND_MESRU),
        ("backend", ("tests",), BACKEND_MESRU),
        (".", (), ["backend/" + k for k in BACKEND_MESRU] + MONITORING_MESRU),
        (".", ("backend",), ["backend/" + k for k in BACKEND_MESRU]),
    ],
    ids=["backend-yolsuz", "backend-nokta", "backend-kanonik", "kok-yolsuz", "kok-backend"],
)
def test_otomatik_kesif_adhoc_import_etmez(izole_kok, cwd, argumanlar, beklenen):
    _isaretleri_sil(izole_kok)
    rc, kimlikler, cikti = _topla(izole_kok / cwd, *argumanlar)
    assert _isaretler(izole_kok) == [], cikti
    assert rc == 0, cikti
    assert sorted(kimlikler) == sorted(beklenen), cikti


def test_markersiz_async_hala_reddedilir(izole_kok):
    gecici = izole_kok / "backend" / "tests" / "test_markersiz_gecici.py"
    gecici.write_text("async def test_markersiz():\n    pass\n", encoding="utf-8")
    try:
        for argumanlar in ((), ("tests",)):
            _isaretleri_sil(izole_kok)
            rc, _, cikti = _topla(izole_kok / "backend", *argumanlar)
            assert rc == 4, cikti
            assert KAPI_MESAJI in cikti, cikti
            assert "test_markersiz_gecici.py::test_markersiz" in cikti, cikti
            assert _isaretler(izole_kok) == [], cikti
    finally:
        gecici.unlink()


def test_acik_verilen_dosya_korunmaz_belgelenen_sinir(izole_kok):
    _isaretleri_sil(izole_kok)
    try:
        _topla(izole_kok / "backend", "test_fresh.py")
        assert _isaretler(izole_kok) == ["backend/test_fresh.py" + ISARET_EKI]
    finally:
        _isaretleri_sil(izole_kok)


def test_listede_olmayan_dosya_topluca_gizlenmez(izole_kok):
    yeni = izole_kok / "backend" / "scripts" / "test_listede_olmayan.py"
    yeni.write_text("def test_listesiz():\n    pass\n", encoding="utf-8")
    try:
        _isaretleri_sil(izole_kok)
        rc, kimlikler, cikti = _topla(izole_kok / "backend")
        assert rc == 0, cikti
        assert "scripts/test_listede_olmayan.py::test_listesiz" in kimlikler, cikti
        assert _isaretler(izole_kok) == [], cikti
    finally:
        yeni.unlink()
