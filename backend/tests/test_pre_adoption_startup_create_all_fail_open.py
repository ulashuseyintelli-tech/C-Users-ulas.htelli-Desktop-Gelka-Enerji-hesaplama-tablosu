"""
PDSMR-R2I — pre-adoption DB uzerinde backend baslatmanin GERCEK davranisi.

BU TEST FAIL-CLOSED KANITI DEGILDIR. TAM TERSINI kanitlar: `init_db()`
(app/database.py, DEGISTIRILMEDI) `settings.env != "prod"` oldugu surece
`Base.metadata.create_all(bind=engine)`'i KOSULSUZ calistirir. Paketlenmis
uygulamaya gomulen backend/.env.production `ENV=development` yazdigindan,
gercek paketlenmis runtime'da bu guard HICBIR ZAMAN devreye girmez.

Sonuc: pre-adoption (013 revizyonunda, S5 tablolari eksik) bir DB'ye karsi
backend baslatilirsa:
  - alembic_version DEGISMEZ (013'te kalir) - create_all migration lineage'i
    KULLANMAZ, sadece "yoksa yarat" yapar.
  - Modelde tanimli ama DB'de eksik olan tablolar (outreach_messages,
    outreach_templates, suppression_entries) SESSIZCE, migration'siz
    yaratilir.
  - Bu karisik durum PDSMR-R1D Faz 2R2 validator'i tarafindan HARD_STOP
    olarak yakalanir (policy.EXPECTED_ABSENT_MODEL_TABLES) - ANCAK bu
    yakalama create_all() CALISTIKTAN SONRA, geriye donuk olur; create_all()
    calismasini ONCEDEN ENGELLEMEZ.

Bu, PDSMR-R2I owner kapanis talimatinin 3. maddesi geregi eklendi: bulgu
"deterministik bir NEGATIF test" olarak kalici hale getirildi. init_db(),
database.py, main.py'ye HICBIR DEGISIKLIK yapilmadi (owner: madde 1) -
bu dosya SADECE mevcut, degistirilmemis fonksiyonlari cagirir.

Ilgili: docs/PDSMR-R3 (henuz yetkilendirilmedi) - startup schema gate +
controlled adoption wiring bu bulguyu KAPATACAK sonraki faz.

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


@pytest.fixture()
def pre_adoption_db(tmp_path) -> str:
    """
    013 revizyonunda, S5 tablolari EKSIK, golden legacy DB - validator ile
    AYNI semaya sahip fixture builder'dan (kod tekrari yok).
    """
    return _build_golden_legacy_db(str(tmp_path / "pre_adoption.db"))


def test_settings_env_is_not_prod_in_shipped_config():
    """
    init_db()'nin `settings.env == "prod"` erken-donus koruması, paketlenmis
    uygulamaya gomulen backend/.env.production dosyasi ENV=development
    yazdigi surece HICBIR ZAMAN devreye girmez. Bu, asagidaki testin NEDEN
    gercek paketlenmis davranisi temsil ettigini kanitlayan on-kosuldur.
    """
    wt_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_production_path = os.path.join(wt_backend, ".env.production")
    with open(env_production_path, "r", encoding="utf-8") as fh:
        content = fh.read()
    assert "ENV=development" in content, (
        "backend/.env.production artik ENV=development YAZMIYOR - bu test "
        "ve PDSMR-R2I kapanis bulgusu bu varsayima dayaniyordu; PDSMR-R3 "
        "kapsaminda yeniden degerlendirilmeli."
    )


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
