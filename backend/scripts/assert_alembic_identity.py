"""
PDSMR-R3B STEP 1 — derleme ONCESI (pre-build) dogrulama.

KANITLAMASI GEREKEN: bu script'in calistigi Python surecinde `import alembic`
YUKLU UCUNCU PARTI paketi cozer, `backend/alembic/` (proje migration script
klasoru - AYNI isimde ama TAMAMEN FARKLI bir sey, kendi __init__.py'si var)
DEGIL. Bu, PDSMR-R3A'da GERCEK derlemeyle KANITLANMIS "derleme-zamani
golgeleme" kok nedeninin (bkz. app/legacy_adoption/alembic_runner.py modul
dokstring'i, PDSMR-R3B kapanis raporu) build-desktop.bat'ta TEKRARLANMADIGINI
build-desktop.bat CALISMADAN ONCE (PyInstaller'in ~2-3 dakikalik analiz/
derleme suresini beklemeden) HIZLICA yakalar.

ONEMLI — bu script'in KENDISI ayni golgeleme sinifina DUSMEMELIDIR: bu yuzden
KASITLI olarak `sys.path`'ten CWD'ye esdeger HER girisi (hem `-m` modul
cagrisinin koydugu MUTLAK CWD yolu, hem `-c`/interaktif kabugun koydugu bos
string `''`) ACIKCA CIKARIR, backend_dir'i (icinde `alembic/` proje klasoru
barindiran dizin) ASLA sys.path'e KENDI EKLEMEZ - PDSMR-R3A'nin kendi test
harness'inde AYNI hataya dusulup duzeltildi (bkz.
tests/test_pdsmr_r3a_alembic_runner.py modul dokstring'i). Bu script,
`scripts/` alt-dizininden dogrudan dosya-yolu ile calistirilmalidir
(`python scripts\assert_alembic_identity.py`) - BU invocation biciminde
sys.path[0] zaten `scripts/` (alembic/ ICERMEYEN) olur, AMA savunma
KATMANLI: yukaridaki temizlik HER durumda calisir.

Basarisizlikta exit(1) - build-desktop.bat bunu FAIL olarak ele alip
PyInstaller'i HIC CALISTIRMAMALIDIR.

Cagrildigi yerler:
- build-desktop.bat [PDSMR-R3B STEP 1, PyInstaller cagrisindan HEMEN ONCE]
"""
from __future__ import annotations

import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── Savunma katmani: CWD-esdegeri sys.path girislerini ACIKCA cikar ────────
sys.path = [p for p in sys.path if p not in ("", BACKEND_DIR)]

hatalar: list[str] = []


def _basarisiz(mesaj: str) -> None:
    hatalar.append(mesaj)
    print(f"HATA: {mesaj}", file=sys.stderr)


print(f"[assert_alembic_identity] backend_dir = {BACKEND_DIR}")
print(f"[assert_alembic_identity] sys.path (ilk 5) = {sys.path[:5]}")

# ── 1) `alembic` UCUNCU PARTI paket olarak import edilebiliyor mu? ─────────
try:
    import alembic
    import alembic.config
    import alembic.command
    import alembic.script
except Exception as exc:  # noqa: BLE001 - taninmasi ONEMLI, GENIS yakalama BILEREK
    _basarisiz(f"'import alembic(.config/.command/.script)' basarisiz: {type(exc).__name__}: {exc}")
    alembic = None  # type: ignore[assignment]

if alembic is not None:
    alembic_dosyasi = os.path.abspath(getattr(alembic, "__file__", "") or "")
    proje_migration_klasoru = os.path.join(BACKEND_DIR, "alembic")
    print(f"[assert_alembic_identity] alembic.__file__ = {alembic_dosyasi}")

    # ── 2) Cozulen 'alembic', backend/alembic/ (proje migration klasoru) ─────
    #      DEGIL mi? (os.path.commonpath ile KESIN dizin-icindelik kontrolu -
    #      basit string prefix DEGIL, cunku "...\alembicX" gibi YANLIS
    #      pozitif/negatif riskini de ONLER.)
    try:
        ortak = os.path.commonpath([alembic_dosyasi, proje_migration_klasoru])
        icinde_mi = os.path.normcase(ortak) == os.path.normcase(proje_migration_klasoru)
    except ValueError:
        icinde_mi = False  # farkli suruculer (Windows) -> KESINLIKLE icinde DEGIL

    if icinde_mi:
        _basarisiz(
            f"'alembic' YUKLU pakete DEGIL, PROJE migration klasorune "
            f"({proje_migration_klasoru}) COZULUYOR - DERLEME-ZAMANI GOLGELEME "
            f"(PDSMR-R3A/R3B kok nedeni tekrarlaniyor)"
        )

    # ── 3) UCUNCU PARTI pakete OZGU nesneler GERCEKTEN erisilebilir mi? ───────
    #      (proje klasorunun config.py/command.py/script.py ALT MODULLERI YOK -
    #      golgeleme olsaydi import #1 zaten BASARISIZ OLURDU, ama ekstra kanit.)
    for nitelik_yolu in ("alembic.config.Config", "alembic.command.upgrade", "alembic.script.ScriptDirectory"):
        modul_adi, _, nitelik_adi = nitelik_yolu.rpartition(".")
        modul = sys.modules.get(modul_adi)
        if modul is None or not hasattr(modul, nitelik_adi):
            _basarisiz(f"'{nitelik_yolu}' erisilebilir DEGIL - golgelenmis/eksik paket")

# ── 4) `app` paketi HALA (bu script'in KENDI temizliginden SONRA bile,
#      backend_dir ACIKCA sys.path'e EKLENEREK) bulunabiliyor mu? PyInstaller
#      analiz asamasinin `app`'i GORMESI icin gerekli olan AYNI kosulu
#      (backend_dir sys.path'te) TEKRAR KURUP dogrular - boylece bu script
#      hem "alembic golgelenmemis" HEM "app hala bulunabilir" ikisini de TEK
#      surecte, BIRBIRINE KARISTIRMADAN (once biri temizlenmis halde, sonra
#      digeri acikca eklenmis halde) kanitlar.
sys.path.insert(0, BACKEND_DIR)
try:
    import app  # noqa: F401
    import app.core.config  # noqa: F401
    import app.main  # noqa: F401
except Exception as exc:  # noqa: BLE001
    _basarisiz(f"'import app(.core.config/.main)' basarisiz: {type(exc).__name__}: {exc}")
else:
    print(f"[assert_alembic_identity] app.__file__ = {os.path.abspath(app.__file__)}")

if hatalar:
    print(f"\n[assert_alembic_identity] BASARISIZ - {len(hatalar)} hata:", file=sys.stderr)
    for h in hatalar:
        print(f"  - {h}", file=sys.stderr)
    sys.exit(1)

print("\n[assert_alembic_identity] BASARILI - alembic ucuncu parti pakete cozuluyor, "
      "backend/alembic/ tarafindan golgelenmiyor; app paketi bulunabilir.")
sys.exit(0)
