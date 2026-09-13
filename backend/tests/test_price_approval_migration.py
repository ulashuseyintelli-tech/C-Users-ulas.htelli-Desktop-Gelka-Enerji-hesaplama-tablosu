"""
Migration b7e4c2d91a60 (fiyat onay revizyonları) — YALNIZ izole geçici DB'de.

Kanıtlanan:
- 351d314819d5 → b7e4c2d91a60 yükseltmesi yalnız iki YENİ tablo ekler; mevcut tabloların
  şeması (kolon/index) ve verisi değişmez, eski kayıtlar doldurulmaz/finalleştirilmez.
- Oluşan tabloların kolon/null/benzersizlik şekli app/price_approval.onay_metadata ile aynı.
- Kısıtlar veritabanında zorlanır (segment, basis, revizyon benzersizliği).
- downgrade tabloları kaldırır ve DB 351d314819d5 şemasına döner.

Gerçek alembic betiği, testleri koşan yorumlayıcının yanından bulunur (bkz.
test_s5_r01a_raw_total_guard._alembic_yolu). Canlı / kurulu veritabanına dokunulmaz.

Çağrıldığı yerler:
- pytest (hedefli kabul)
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ONCEKI = "351d314819d5"
YENI = "b7e4c2d91a60"


def _alembic() -> Path:
    for aday in (Path(sys.executable).parent / "alembic.exe", Path(sys.executable).parent / "alembic"):
        if aday.exists():
            return aday
    pytest.fail("alembic betiği yorumlayıcının yanında bulunamadı (sessiz skip YOK)")


def _calistir(db: Path, *argv: str) -> None:
    ortam = dict(os.environ, DATABASE_URL=f"sqlite:///{db.as_posix()}")
    ortam.pop("GELKA_PACKAGED_RUNTIME", None)
    sonuc = subprocess.run([str(_alembic()), *argv], cwd=str(BACKEND), env=ortam, capture_output=True)
    assert sonuc.returncode == 0, sonuc.stderr.decode("utf-8", "replace")[-1500:]


def _sema(db: Path) -> dict:
    con = sqlite3.connect(str(db))
    try:
        tablolar = sorted(r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
        sema = {}
        for t in tablolar:
            kolonlar = [tuple(r[1:6]) for r in con.execute(f"PRAGMA table_info({t})")]
            indexler = sorted((r[1], r[2]) for r in con.execute(f"PRAGMA index_list({t})"))
            sema[t] = {"kolonlar": kolonlar, "indexler": indexler}
        return sema
    finally:
        con.close()


def _surum(db: Path) -> list:
    con = sqlite3.connect(str(db))
    try:
        return [r[0] for r in con.execute("SELECT version_num FROM alembic_version")]
    finally:
        con.close()


@pytest.fixture(scope="module")
def gecici_db(tmp_path_factory):
    db = tmp_path_factory.mktemp("fiyat_onay_migration") / "izole.db"
    _calistir(db, "upgrade", ONCEKI)
    con = sqlite3.connect(str(db))
    con.execute("INSERT INTO market_reference_prices (price_type, period, ptf_tl_per_mwh, yekdem_tl_per_mwh, "
                "status, source, is_locked, created_at, updated_at) VALUES "
                "('PTF', '2026-07', 2699.61, 486.314, 'provisional', 'manual_override', 0, datetime('now'), datetime('now'))")
    con.commit()
    con.close()
    return db


def test_yukseltme_yalniz_iki_yeni_tablo_ekler_ve_eski_veriye_dokunmaz(gecici_db):
    once = _sema(gecici_db)
    con = sqlite3.connect(str(gecici_db))
    veri_once = con.execute("SELECT * FROM market_reference_prices").fetchall()
    con.close()

    _calistir(gecici_db, "upgrade", YENI)
    sonra = _sema(gecici_db)
    assert _surum(gecici_db) == [YENI]
    assert set(sonra) - set(once) == {"ptf_onay_revizyonlari", "yekdem_onay_revizyonlari"}
    for tablo, sekil in once.items():
        assert sonra[tablo] == sekil, f"mevcut tablo şeması değişti: {tablo}"
    con = sqlite3.connect(str(gecici_db))
    try:
        assert con.execute("SELECT * FROM market_reference_prices").fetchall() == veri_once
        assert con.execute("SELECT COUNT(*) FROM ptf_onay_revizyonlari").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM yekdem_onay_revizyonlari").fetchone()[0] == 0
    finally:
        con.close()


def test_migration_sekli_uygulama_metadata_ile_ayni(gecici_db, tmp_path):
    import sqlalchemy as sa
    from app.database import Base
    from app.price_approval import onay_metadata

    _calistir(gecici_db, "upgrade", YENI)
    referans = tmp_path / "metadata.db"
    motor = sa.create_engine(f"sqlite:///{referans.as_posix()}")
    Base.metadata.tables["market_reference_prices"].create(motor)
    onay_metadata.create_all(motor)
    motor.dispose()
    m, r = _sema(gecici_db), _sema(referans)
    for tablo in ("ptf_onay_revizyonlari", "yekdem_onay_revizyonlari"):
        assert m[tablo]["kolonlar"] == r[tablo]["kolonlar"], tablo
        assert m[tablo]["indexler"] == r[tablo]["indexler"], tablo


def test_kisitlar_veritabaninda_zorlanir(gecici_db):
    _calistir(gecici_db, "upgrade", YENI)
    con = sqlite3.connect(str(gecici_db))
    try:
        ekle = ("INSERT INTO yekdem_onay_revizyonlari (period, segment, revision, value, version, "
                "kaynak_kanit_sha256, kaynak_kanit_json, captured_at, onaylayan_beyan, dogrulanan_yetki, approved_at, "
                "change_reason) VALUES ('2026-07', ?, ?, 486.3, '2026-07-20T00:00:00+03:00', 'k', ?, datetime('now'), "
                "'Y', ?, datetime('now'), 'g')")
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(ekle, ("st", 1, None, "paylasilan_yonetici_anahtari"))  # kanıtsız onay satırı yazılamaz
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(ekle, ("st", 1, "{}", None))  # doğrulanan yetkisi olmayan satır yazılamaz
        ekle_kanitli = lambda seg, rev: con.execute(ekle, (seg, rev, "{}", "paylasilan_yonetici_anahtari"))  # noqa: E731
        ekle_kanitli("st", 1)
        with pytest.raises(sqlite3.IntegrityError):
            ekle_kanitli("st", 1)
        ekle_kanitli("gts", 1)  # aynı revizyon farklı segmentte serbest
        with pytest.raises(sqlite3.IntegrityError):
            ekle_kanitli("serbest", 2)
        kid = con.execute("SELECT id FROM market_reference_prices").fetchone()[0]
        ptf = ("INSERT INTO ptf_onay_revizyonlari (period, revision, price_record_id, value, basis, "
               "kaynak_kanit_sha256, kaynak_kanit_json, kayit_parmak_izi, captured_at, onaylayan_beyan, "
               "dogrulanan_yetki, approved_at, change_reason) VALUES ('2026-07', ?, ?, 2699.61, ?, 'k', '{}', 'p', "
               "datetime('now'), 'Y', 'paylasilan_yonetici_anahtari', datetime('now'), 'g')")
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(ptf, (1, kid, "mcp_wavg"))
        con.execute(ptf, (1, kid, "mcp_avg"))
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(ptf, (1, kid, "mcp_avg"))
        con.rollback()
    finally:
        con.close()


def test_downgrade_tablolari_kaldirir_ve_onceki_semaya_doner(gecici_db, tmp_path):
    _calistir(gecici_db, "upgrade", YENI)
    _calistir(gecici_db, "downgrade", ONCEKI)
    assert _surum(gecici_db) == [ONCEKI]
    temiz = tmp_path / "temiz.db"
    _calistir(temiz, "upgrade", ONCEKI)
    assert _sema(gecici_db) == _sema(temiz)
