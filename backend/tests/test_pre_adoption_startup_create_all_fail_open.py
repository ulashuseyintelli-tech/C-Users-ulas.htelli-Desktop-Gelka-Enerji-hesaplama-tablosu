"""
PDSMR-R2I (temel bulgu) + PDSMR-R3 (kapanis) — pre-adoption DB uzerinde
backend baslatmanin GERCEK davranisi.

PDSMR-R2I'de KANITLANAN bulgu (asagida KORUNDU, hala dogru): `init_db()`
(o zamanki hali) `settings.env != "prod"` oldugu surece
`Base.metadata.create_all(bind=engine)`'i KOSULSUZ calistiriyordu — ve
paketlenmis uygulamaya gomulen backend/.env.production `ENV=development`
yazdigindan bu guard GERCEK paketlenmis runtime'da HICBIR ZAMAN devreye
girmiyordu.

PDSMR-R3'te KAPATILDI: `init_db()`'ye YENI, KOSULSUZ bir on-kontrol
eklendi — `GELKA_PACKAGED_RUNTIME=1` (electron/main.js tarafindan
machine-local.env SONRASINDA, EZILEMEZ sekilde enjekte edilir) gorulurse
create_all() HIC CAGRILMAZ. Ayrica backend/.env.production'daki
`ENV=development` -> `ENV=staging` DUZELTILDI (check_production_guard()'in
"production" icin ZORUNLU kildigi ADMIN_API_KEY_ENABLED+32-karakter
gereksinimini TETIKLEMEDEN, yanlis "development" etiketini duzeltir).

Bu dosyadaki testler ARTIK IKI SENARYOYU birlikte belgeler:
  1) GELKA_PACKAGED_RUNTIME YOKSA (ör. eski/harici bir cagiran): create_all()
     HALA fail-open calisir (REGRESYONA KARSI - eski davranis KORUNMALI,
     "geriye donuk uyumluluk" owner kurali).
  2) GELKA_PACKAGED_RUNTIME=1 VARSA (gercek paketlenmis app, PDSMR-R3
     startup_gate.py DB'yi ONCEDEN hazirladiktan SONRA main.js'in
     enjekte ettigi sinyal): create_all() ATLANIR - fail-open KAPANMISTIR.

Cagrildigi yerler:
- pytest suite (backend/tests/) - CI/manuel regresyon
"""
from __future__ import annotations

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_legacy_adoption_validator import _build_golden_legacy_db  # noqa: E402

from app.legacy_adoption import policy  # noqa: E402
from app.legacy_adoption.result import Outcome  # noqa: E402
from app.legacy_adoption.validator import validate_legacy_db  # noqa: E402


def _tablolar(db_path: str) -> set[str]:
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        return {r[0] for r in rows}
    finally:
        con.close()


def _alembic_version(db_path: str) -> str:
    con = sqlite3.connect(db_path)
    try:
        row = con.execute("SELECT version_num FROM alembic_version").fetchone()
        return row[0] if row else None
    finally:
        con.close()


def _sha256_dosya(path: str) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@pytest.fixture()
def pre_adoption_db(tmp_path) -> str:
    """
    013 revizyonunda, S5 tablolari EKSIK, golden legacy DB - validator ile
    AYNI semaya sahip fixture builder'dan (kod tekrari yok).
    """
    return _build_golden_legacy_db(str(tmp_path / "pre_adoption.db"))


def test_settings_env_is_desktop_not_staging_or_prod_in_shipped_config():
    """
    PDSMR-R3B DUZELTMESI: sirasiyla "development" (yanlis) -> "staging"
    (PDSMR-R3, HALA yanlis - owner: bu bir dev/test ortami degil) -> "desktop"
    (PDSMR-R3B, GERCEK/DURUST deger, incident_service.py::VALID_ENVIRONMENTS'a
    eklendi). init_db()'nin ESKI `settings.env == "prod"` erken-donus korumasi
    bu deger ile HALA devreye GIRMEZ (regresyon yok) - ama bu ASIL guard
    DEGIL, GELKA_PACKAGED_RUNTIME kontrolu ASIL guard'dir. Ayrica
    run_server.py::_enforce_packaged_environment_invariants() (PDSMR-R3B
    STEP 5) frozen modda ENV'in GERCEKTEN 'desktop' oldugunu BAGIMSIZ
    dogrular - bkz. test_pdsmr_r3_run_server_import_order.py.
    """
    wt_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_production_path = os.path.join(wt_backend, ".env.production")
    with open(env_production_path, "r", encoding="utf-8") as fh:
        content = fh.read()
    assert "ENV=desktop" in content, (
        "backend/.env.production ENV=desktop YAZMIYOR - PDSMR-R3B STEP 5 "
        "duzeltmesi geri alinmis olabilir."
    )
    assert "ENV=development" not in content
    assert "ENV=staging" not in content, (
        "ENV=staging ARTIK GECERSIZ - PDSMR-R3A'da owner tarafindan "
        "yaniltici bulundu, PDSMR-R3B'de ENV=desktop'a DUZELTILDI"
    )
    assert "ENV=production" not in content, (
        "ENV=production check_production_guard()'i TETIKLER - "
        "ADMIN_API_KEY_ENABLED+32-karakter key ZORUNLU olur, mevcut "
        "API_KEY_ENABLED=false ile paketlenmis app'in baslangicini KIRAR"
    )


def test_incident_service_accepts_desktop_as_valid_environment():
    """PDSMR-R3B STEP 5 — check_production_guard()'un ilk adimi
    (validate_environment) 'desktop'u REDDETMEMELI (aksi halde main.py
    modul-yukleme anindaki cagri RuntimeError firlatir, TUM app.main
    import'unu kirar)."""
    from app.incident_service import check_production_guard

    ok, hata = check_production_guard("desktop", False, "")
    assert ok, f"'desktop' check_production_guard'i BASARISIZ yapiyor: {hata}"


def test_pre_adoption_startup_create_all_is_fail_open(pre_adoption_db, monkeypatch):
    """
    DETERMINISTIK NEGATIF TEST (PDSMR-R2I owner kapanis, madde 3).

    FAIL-CLOSED IDDIASI YOKTUR. Kanitlanan: init_db() pre-adoption bir DB'ye
    karsi calistirildiginda create_all() SESSIZCE S5 tablolarini yaratir ve
    alembic_version 013'te KALIR (migration lineage atlanir).
    """
    from sqlalchemy import create_engine

    import app.database as db_module

    onceki_tablolar = _tablolar(pre_adoption_db)
    onceki_revizyon = _alembic_version(pre_adoption_db)
    assert onceki_revizyon == policy.ALLOWED_ALEMBIC_REVISION
    for hedef in policy.EXPECTED_ABSENT_MODEL_TABLES:
        assert hedef not in onceki_tablolar, (
            f"fixture zaten {hedef} iceriyor - golden legacy DB semasi bozuk"
        )

    # init_db()'nin kullandigi engine'i BU TEST SIRASINDA izole bir sekilde
    # pre_adoption_db'ye baglar - gercek paketlenmis app'te bu baglanti
    # electron/dbRouting.js -> DATABASE_URL spawn env'i araciligiyla olur;
    # burada AYNI etkiyi (create_all(bind=engine)) dogrudan test ediyoruz.
    #
    # settings.env: init_db() bunu KENDI ICINDE, YEREL olarak
    # `from .core.config import settings` ile okur (modul-seviyesinde
    # app.database'in bir ozniteligi DEGILDIR - monkeypatch EDILEMEZ/
    # GEREKMEZ). backend/.env(.production) bu test surecinde YOK ve alan
    # varsayilani "dev" - ikisi de "prod" DEGIL, guard zaten devreye
    # girmiyor (bkz. test_settings_env_is_not_prod_in_shipped_config -
    # paketlenmis app icin AYNI sonucu ayrica kanitlar).
    test_engine = create_engine(f"sqlite:///{pre_adoption_db}")
    monkeypatch.setattr(db_module, "engine", test_engine)

    db_module.init_db()  # GERCEK, DEGISTIRILMEMIS fonksiyon
    test_engine.dispose()

    sonraki_tablolar = _tablolar(pre_adoption_db)
    sonraki_revizyon = _alembic_version(pre_adoption_db)

    # 1) alembic_version DEGISMEDI - create_all migration'i BILMEZ.
    assert sonraki_revizyon == policy.ALLOWED_ALEMBIC_REVISION, (
        "beklenmedik: alembic_version degisti - create_all migration "
        "lineage'ine mi dokundu? (olmamali)"
    )

    # 2) S5 tablolari SESSIZCE yaratildi - migration'siz, fail-open.
    yeni_tablolar = sonraki_tablolar - onceki_tablolar
    for hedef in policy.EXPECTED_ABSENT_MODEL_TABLES:
        assert hedef in sonraki_tablolar, (
            f"{hedef} yaratilmadi - PDSMR-R2I kapanis bulgusu artik "
            "reprodüklenmiyor; bu ya kod DEGISTI ya da ortam farkli. "
            "Bu durumda bu testin ve owner rapor formatinin "
            "('CREATE_ALL FAIL-OPEN CONFIRMED') yeniden dogrulanmasi GEREKIR."
        )
    assert policy.EXPECTED_ABSENT_MODEL_TABLES <= yeni_tablolar


def test_packaged_runtime_flag_prevents_create_all_fail_open(pre_adoption_db, monkeypatch):
    """
    PDSMR-R3 STEP 7 — DOGRUDAN DUZELTME KANITI (yukaridaki testin TERSI):
    GELKA_PACKAGED_RUNTIME=1 gorulunce create_all() HIC CAGRILMAZ - S5
    tablolari YARATILMAZ, DB pre-adoption (013) haliyle DEGISMEDEN kalir.

    Bu, "Add a direct regression test proving zero create_all calls"
    (owner, PDSMR-R3 STEP 7) gereksinimini karsilar.
    """
    from sqlalchemy import create_engine

    import app.database as db_module

    onceki_tablolar = _tablolar(pre_adoption_db)
    onceki_revizyon = _alembic_version(pre_adoption_db)
    onceki_hash = _sha256_dosya(pre_adoption_db)

    monkeypatch.setenv("GELKA_PACKAGED_RUNTIME", "1")
    test_engine = create_engine(f"sqlite:///{pre_adoption_db}")
    monkeypatch.setattr(db_module, "engine", test_engine)

    db_module.init_db()  # GERCEK, DEGISTIRILMIS fonksiyon - YENI guard
    test_engine.dispose()

    sonraki_tablolar = _tablolar(pre_adoption_db)
    sonraki_revizyon = _alembic_version(pre_adoption_db)

    assert sonraki_revizyon == onceki_revizyon == policy.ALLOWED_ALEMBIC_REVISION
    assert sonraki_tablolar == onceki_tablolar, (
        "GELKA_PACKAGED_RUNTIME=1 iken create_all() YINE DE tablo yaratti - "
        "STEP 7 guard'i regrese olmus olabilir"
    )
    for hedef in policy.EXPECTED_ABSENT_MODEL_TABLES:
        assert hedef not in sonraki_tablolar
    assert _sha256_dosya(pre_adoption_db) == onceki_hash, (
        "DB dosyasinin ICERIGI DEGISTI (create_all() gercekten atlanmadi mi?)"
    )


def test_create_all_fail_open_baseline_still_reproduces_without_flag(
    pre_adoption_db, monkeypatch
):
    """
    REGRESYONA KARSI KORUMA: GELKA_PACKAGED_RUNTIME set EDILMEZSE eski
    (PDSMR-R2I'de bulunan) fail-open davranisi HALA aynen calisir - STEP 7
    guard'i yalniz OPT-IN'dir, var olan (dev/test) davranisi degistirmez.
    """
    from sqlalchemy import create_engine

    import app.database as db_module

    monkeypatch.delenv("GELKA_PACKAGED_RUNTIME", raising=False)
    onceki_tablolar = _tablolar(pre_adoption_db)

    test_engine = create_engine(f"sqlite:///{pre_adoption_db}")
    monkeypatch.setattr(db_module, "engine", test_engine)
    db_module.init_db()
    test_engine.dispose()

    sonraki_tablolar = _tablolar(pre_adoption_db)
    yeni_tablolar = sonraki_tablolar - onceki_tablolar
    assert policy.EXPECTED_ABSENT_MODEL_TABLES <= yeni_tablolar, (
        "GELKA_PACKAGED_RUNTIME yokken bile create_all() artik S5 "
        "tablolarini yaratmiyor - bu, OPT-IN guard'in yanlislikla HERKESE "
        "uygulandigi/regresyona yol actigi anlamina gelebilir"
    )


def test_validator_hard_stops_after_create_all_fail_open(pre_adoption_db, monkeypatch):
    """
    create_all() fail-open calistiktan SONRA, PDSMR-R1D Faz 2R2 validator'i
    bu karisik durumu (013 revizyonu + S5 tablolari mevcut) dogru sekilde
    HARD_STOP olarak yakalar. Bu, "onceden engelleme" DEGIL "sonradan tespit"
    kanitidir - iki test birlikte bulgunun TAM CERCEVESINI belgeler.
    """
    from sqlalchemy import create_engine

    import app.database as db_module

    test_engine = create_engine(f"sqlite:///{pre_adoption_db}")
    monkeypatch.setattr(db_module, "engine", test_engine)
    db_module.init_db()
    test_engine.dispose()

    rapor = validate_legacy_db(pre_adoption_db)

    assert rapor.outcome == Outcome.HARD_STOP, (
        "beklenmedik: validator bu karisik semayi PASS olarak isaretledi - "
        "policy.EXPECTED_ABSENT_MODEL_TABLES kontrolu regrese olmus olabilir"
    )
