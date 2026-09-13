"""
Test yardımcısı — fiyat KİMLİĞİ onaylı sentetik kayıt (provenance sürüm 3).

Fiyat onayının KENDİSİNİ sınamayan testler (PDF motoru, R2 kapısı, idempotency,
gözlemlenebilirlik vb.) teklif kesinleştirme kapısını geçmek için dönemin yetkili onaylı
aylık aritmetik PTF'ine ve (YEKDEM dahilse) seçilen segmentte onaylı YEKDEM'e ihtiyaç duyar.
Bu modül o sentetik durumu doğrudan yazar; onay akışının kendisi
tests/test_price_approval_identity.py'de uçtan uca sınanır.

Parmak izi, uygulamadaki price_approval.kayit_parmak_izi ile AYNI fonksiyondan üretilir
(kopya formül yok).

TARİHSEL SNAPSHOT AYRIMI: Kaydedilmiş (geçmiş) tekliflerin belge üretimini sınayan testler
YENİ onaylı kayıtla üretilmiş sürüm 3 snapshot KULLANMAZ; master 38245e9 kodunun GERÇEK
build_price_provenance çıktısından yakalanmış sürüm 2 fikstürünü (tarihsel_v2_provenance)
kullanır. Böylece "eski teklifler değişmeden okunur" uyumluluğu yeni kayıtlarla taklit edilmez.

Çağrıldığı yerler:
- tests/test_pricing_phase1_price_accuracy.py, test_pdf_mismatch_guard.py, test_pr2_closure.py,
  test_pr3_observability.py, test_s5_r01_offer_pdf.py, test_s5_r01a_raw_total_guard.py,
  test_s5_r03a_pdf_engine.py, test_s5_r03b_durable_storage.py
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from types import SimpleNamespace
from typing import Optional

VARSAYILAN_SEGMENT = "st"
TARIHSEL_V2_FIKSTUR = "tarihsel_provenance_v2_38245e9.json"


def tarihsel_v2_provenance(anahtar: str) -> dict:
    """master 38245e9'dan yakalanmış sürüm 2 provenance'ın bağımsız kopyası.

    Anahtarlar: "s5_2026_01" (2026-01, 2500/50, epias_manual/final) ve "faz1_2099_01"
    (2099-01, aynı kayıt). Yakalama bilgisi fikstürün `yakalama` alanındadır.

    Çağrıldığı yerler:
    - tests/test_s5_r01_offer_pdf.py, test_s5_r03a_pdf_engine.py, test_s5_r03b_durable_storage.py
      (_dogrulanmis_fiyat), tests/test_pricing_phase1_price_accuracy.py (_tarihsel_kesin_provenance)
    """
    import copy
    import json
    import os

    yol = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", TARIHSEL_V2_FIKSTUR)
    with open(yol, encoding="utf-8") as fh:
        veri = json.load(fh)
    prov = copy.deepcopy(veri[anahtar]["provenance"])
    assert prov["version"] == 2 and prov["verified"] is True
    return prov


def _sentetik_kanit(period: str) -> tuple:
    """Sentetik (resmî OLMAYAN) kaynak kanıtı: onay kaydının NOT NULL kanıt alanları için.

    Özet uygulamadaki price_approval._kanit_yaz ile üretilir (JSON ↔ SHA tutarlı).
    """
    from app.price_approval import _kanit_yaz

    return _kanit_yaz({"kaynak": "sentetik_test_fiksturu", "period": period, "resmi": False})


def onay_tablolarini_kur(bind) -> None:
    from app.price_approval import onay_metadata

    onay_metadata.create_all(bind)


def onayli_fiyat(db, period: str, ptf: float, yekdem: Optional[float] = None,
                 segment: str = VARSAYILAN_SEGMENT, version: Optional[str] = None,
                 segmentler: Optional[dict] = None) -> None:
    """ORM oturumunda: dönem kaydı final/epias_api + PTF onayı + segment YEKDEM onay(lar)ı."""
    import sqlalchemy as sa
    from app.database import MarketReferencePrice
    from app.price_approval import (
        kayit_parmak_izi, ptf_onay_revizyonlari, son_revizyon, yekdem_onay_revizyonlari,
    )

    kanit_json, kanit_sha = _sentetik_kanit(period)

    onay_tablolarini_kur(db.get_bind())
    kayit = (db.query(MarketReferencePrice)
             .filter(MarketReferencePrice.period == period, MarketReferencePrice.price_type == "PTF").first())
    if kayit is None:
        kayit = MarketReferencePrice(period=period, price_type="PTF", is_locked=0)
        db.add(kayit)
    kayit.ptf_tl_per_mwh = ptf
    kayit.yekdem_tl_per_mwh = yekdem if yekdem is not None else 0.0
    kayit.status = "final"
    kayit.source = "epias_api"
    db.commit()
    simdi = datetime.utcnow()
    db.execute(sa.insert(ptf_onay_revizyonlari).values(
        period=period, revision=son_revizyon(db, "PTF", period) + 1, price_record_id=kayit.id,
        value=float(ptf), basis="mcp_avg", kaynak_kanit_sha256=kanit_sha, kaynak_kanit_json=kanit_json,
        kayit_parmak_izi=kayit_parmak_izi(kayit), captured_at=simdi, onaylayan_beyan="test", dogrulanan_yetki="paylasilan_yonetici_anahtari",
        approved_at=simdi, change_reason="sentetik test onayı"))
    degerler = dict(segmentler or {})
    if yekdem is not None and segment not in degerler:
        degerler[segment] = yekdem
    for seg, deger in degerler.items():
        db.execute(sa.insert(yekdem_onay_revizyonlari).values(
            period=period, segment=seg, revision=son_revizyon(db, "YEKDEM", period, seg) + 1,
            value=float(deger), version=version or period, kaynak_kanit_sha256=kanit_sha,
            kaynak_kanit_json=kanit_json, captured_at=simdi, onaylayan_beyan="test", dogrulanan_yetki="paylasilan_yonetici_anahtari", approved_at=simdi, change_reason="sentetik test onayı"))
    db.commit()


def onayli_fiyat_sqlite(db_yolu, period: str, ptf: float, yekdem: Optional[float] = None,
                        segment: str = VARSAYILAN_SEGMENT) -> None:
    """Ham sqlite3 dosyasında (alembic ile kurulmuş, onay tabloları mevcut) aynı durum."""
    from app.price_approval import kayit_parmak_izi

    kanit_json, kanit_sha = _sentetik_kanit(period)
    con = sqlite3.connect(str(db_yolu))
    try:
        con.execute(
            "INSERT INTO market_reference_prices (price_type, period, ptf_tl_per_mwh, yekdem_tl_per_mwh, "
            "status, source, is_locked, updated_by, change_reason, created_at, updated_at) VALUES "
            "('PTF', ?, ?, ?, 'final', 'epias_api', 0, 'test', 'sentetik onaylı fiyat', datetime('now'), datetime('now'))",
            (period, ptf, yekdem if yekdem is not None else 0.0))
        kid = con.execute("SELECT id FROM market_reference_prices WHERE period=? AND price_type='PTF'",
                          (period,)).fetchone()[0]
        iz = kayit_parmak_izi(SimpleNamespace(period=period, price_type="PTF", ptf_tl_per_mwh=float(ptf),
                                              status="final", source="epias_api"))
        con.execute(
            "INSERT INTO ptf_onay_revizyonlari (period, revision, price_record_id, value, basis, "
            "kaynak_kanit_sha256, kaynak_kanit_json, kayit_parmak_izi, captured_at, onaylayan_beyan, dogrulanan_yetki, approved_at, "
            "change_reason) VALUES (?, 1, ?, ?, 'mcp_avg', ?, ?, ?, datetime('now'), 'test', 'paylasilan_yonetici_anahtari', datetime('now'), "
            "'sentetik')",
            (period, kid, float(ptf), kanit_sha, kanit_json, iz))
        if yekdem is not None:
            con.execute(
                "INSERT INTO yekdem_onay_revizyonlari (period, segment, revision, value, version, "
                "kaynak_kanit_sha256, kaynak_kanit_json, captured_at, onaylayan_beyan, dogrulanan_yetki, approved_at, change_reason) "
                "VALUES (?, ?, 1, ?, ?, ?, ?, datetime('now'), 'test', 'paylasilan_yonetici_anahtari', datetime('now'), 'sentetik')",
                (period, segment, float(yekdem), period, kanit_sha, kanit_json))
        con.commit()
    finally:
        con.close()
