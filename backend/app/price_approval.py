"""
Fiyat kimliği ve yetkili onay revizyonları (OWNER-KARARI-01/02, 2026-09-13).

Politika:
- Kesin teklifte PTF, kaynağı resmî veriden (EPİAŞ mcp `statistic.priceAvg`)
  doğrulanmış ve yetkili tarafından onaylanmış AYLIK ARİTMETİK PTF'dir
  (yöntem jetonu `mcp_avg`). Onay revizyonu kaydın onay anındaki parmak izini
  taşır; kayda sonradan yapılan HER değer/durum/kaynak değişikliği parmak izini
  bozar ve eski onayı kendiliğinden GEÇERSİZ kılar.
- YEKDEM onayı SEGMENT BAŞINADIR (st = Serbest Tüketici `supplierUnitCost`,
  gts = GTŞ-K1 `unitCost`). İki segmentin değeri eşit olsa bile ayrı onaylanır.
  Güncel onay = (dönem, segment) için en büyük revizyon.
- Onay adayı sunucuda resmî veriden YENİDEN çekilir; istemcinin gönderdiği sayıya
  güvenilmez. Aday, ekranda gösterilenden farklıysa HİÇBİR ŞEY yazılmaz ve yeni aday
  yeniden onaya sunulur. Aynı revizyona yarışan onaylardan yalnız biri başarılı olur
  (benzersizlik kısıtı + kayıt üzerinde karşılaştır-ve-yaz).
- PTF onayında değer, durum (final), kaynak (epias_api), onaylayan, fiyat geçmişi
  satırı ve onay revizyonu TEK İŞLEMDE yazılır; herhangi bir adım başarısızsa tamamı
  geri alınır. Otomatik finalleştirme, zamanlayıcı ya da eski kayıtları doldurma YOK.
- İç onay resmî kesinleşme DEĞİLDİR; bu modül "resmî kesin" iddiası üretmez.
- YEKDEM VERSİYON KARARI (OWNER, 2026-09-13, kapalı): dönemin EN SON yayımlanan tam
  versiyonu onaya sunulur; resmî kesinleşme BELİRSİZ kalır (`resmi_kesinlesme`). Sıralama
  versiyon metninin saat dilimli ISO tarih olarak ayrıştırılmasıyla yapılır; ayrıştırılamayan
  versiyon varsa aday oluşmaz (metin sıralamasıyla tahmin yapılmaz).
- KİMLİK GÜVEN SINIRI: sunucunun doğrulayabildiği tek şey isteğin yetki anahtarını
  taşıdığıdır (`dogrulanan_yetki`, ör. paylaşılan ADMIN_API_KEY). Kişi doğrulanmaz; formdaki
  ad yalnız BEYANDIR ve `onaylayan_beyan` olarak ayrı saklanır, snapshot'ta
  `onaylayan_dogrulandi: False` ile işaretlenir. Yeni kullanıcı sistemi yoktur.

Şema: iki yeni tablo yalnız bu modülün ayrı MetaData'sındadır. `Base.metadata`
PDSMR canonical parite sözleşmesiyle `351d314819d5`e kilitlidir (bkz.
tests/test_pdsmr_r4b1_model_canonical_parity.py); tablolar migration
`b7e4c2d91a60` ile oluşur. Tablolar yoksa (ör. paketli runtime'ın başlangıç kapısı
henüz bu revizyona yükseltmiyorsa) okuma yolları "onay yok" döner (fail-closed).

Multitenant: piyasa fiyat tabloları tenant kolonu taşımaz (piyasa geneli veri);
onay revizyonları da aynı kapsamdadır.

Çağrıldığı yerler:
- price_provenance.effective_ptf_candidates() / build_price_provenance() → kimlik kapısı
- calculator.get_ptf_yekdem_for_period() → AI akışı yöntem paritesi
- main.get_prices_with_epias_fallback() → GET /api/epias/prices/{period}
- main.price_approval_candidate_endpoint() → GET /admin/market-prices/approval-candidate
- main.price_approval_endpoint() → POST /admin/market-prices/approve
- database.init_db() → geliştirme/test ortamında tablo oluşturma (paketli runtime HARİÇ)
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from .database import MarketReferencePrice

logger = logging.getLogger(__name__)

PTF_YONTEM_ARITMETIK = "mcp_avg"
PTF_ONAYLI_ADAY_KAYNAGI = "monthly_arithmetic:mcp_avg"
PTF_YONTEM_ETIKETI = "Aylık aritmetik PTF (EPİAŞ)"
ONAY_KAYNAK_ETIKETI = "epias_api"
SEGMENTLER = ("st", "gts")
SEGMENT_ETIKETLERI = {"st": "Serbest Tüketici", "gts": "GTŞ-K1"}
KALEM_PTF = "PTF"
KALEM_YEKDEM = "YEKDEM"
TOLERANS_TL_MWH = 0.005

onay_metadata = sa.MetaData()

ptf_onay_revizyonlari = sa.Table(
    "ptf_onay_revizyonlari",
    onay_metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("period", sa.String(7), nullable=False),
    sa.Column("revision", sa.Integer, nullable=False),
    # Ayrı MetaData'dan Base tablosuna FK: dize yerine kolon nesnesiyle bağlanır.
    sa.Column("price_record_id", sa.Integer,
              sa.ForeignKey(MarketReferencePrice.__table__.c.id, ondelete="RESTRICT"), nullable=False),
    sa.Column("value", sa.Float, nullable=False),
    sa.Column("basis", sa.String(20), nullable=False),
    sa.Column("kaynak_kanit_sha256", sa.String(64), nullable=False),
    sa.Column("kaynak_kanit_json", sa.Text, nullable=False),
    sa.Column("kayit_parmak_izi", sa.String(64), nullable=False),
    sa.Column("captured_at", sa.DateTime, nullable=False),
    # Beyan edilen ad (DOĞRULANMAMIŞ) ve sunucunun doğruladığı yetki türü AYRI saklanır.
    sa.Column("onaylayan_beyan", sa.String(100), nullable=False),
    sa.Column("dogrulanan_yetki", sa.String(64), nullable=False),
    sa.Column("approved_at", sa.DateTime, nullable=False),
    sa.Column("change_reason", sa.Text, nullable=False),
    sa.UniqueConstraint("period", "revision", name="uq_ptf_onay_period_revision"),
    sa.CheckConstraint("basis = 'mcp_avg'", name="ck_ptf_onay_basis"),
)

yekdem_onay_revizyonlari = sa.Table(
    "yekdem_onay_revizyonlari",
    onay_metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("period", sa.String(7), nullable=False),
    sa.Column("segment", sa.String(10), nullable=False),
    sa.Column("revision", sa.Integer, nullable=False),
    sa.Column("value", sa.Float, nullable=False),
    sa.Column("version", sa.String(64), nullable=False),
    sa.Column("kaynak_kanit_sha256", sa.String(64), nullable=False),
    sa.Column("kaynak_kanit_json", sa.Text, nullable=False),
    sa.Column("captured_at", sa.DateTime, nullable=False),
    # Beyan edilen ad (DOĞRULANMAMIŞ) ve sunucunun doğruladığı yetki türü AYRI saklanır.
    sa.Column("onaylayan_beyan", sa.String(100), nullable=False),
    sa.Column("dogrulanan_yetki", sa.String(64), nullable=False),
    sa.Column("approved_at", sa.DateTime, nullable=False),
    sa.Column("change_reason", sa.Text, nullable=False),
    sa.UniqueConstraint("period", "segment", "revision", name="uq_yekdem_onay_period_segment_revision"),
    sa.CheckConstraint("segment IN ('st', 'gts')", name="ck_yekdem_onay_segment"),
)

ONAY_TABLOLARI = (ptf_onay_revizyonlari.name, yekdem_onay_revizyonlari.name)

# Sunucunun doğrulayabildiği yetki türleri (kişi kimliği DEĞİL).
YETKI_PAYLASILAN_YONETICI_ANAHTARI = "paylasilan_yonetici_anahtari"
YETKI_TURLERI = (YETKI_PAYLASILAN_YONETICI_ANAHTARI,)
YETKI_ETIKETLERI = {
    YETKI_PAYLASILAN_YONETICI_ANAHTARI: "Paylaşılan yönetici anahtarı (X-Admin-Key) doğrulandı; kişi doğrulanmadı",
}


class OnayHatasi(Exception):
    """Onay reddi. `kod` HTTP eşlemesi için; mesaj kullanıcıya gösterilir."""

    def __init__(self, kod: str, mesaj: str, http: int, **ek: Any):
        super().__init__(mesaj)
        self.kod = kod
        self.mesaj = mesaj
        self.http = http
        self.ek = ek


@dataclass(frozen=True)
class PtfOnayi:
    id: int
    period: str
    revision: int
    price_record_id: int
    value: float
    basis: str
    kaynak_kanit_sha256: str
    kayit_parmak_izi: str
    onaylayan_beyan: str
    dogrulanan_yetki: str
    approved_at: datetime


@dataclass(frozen=True)
class YekdemOnayi:
    id: int
    period: str
    segment: str
    revision: int
    value: float
    version: str
    kaynak_kanit_sha256: str
    onaylayan_beyan: str
    dogrulanan_yetki: str
    approved_at: datetime


# ── yardımcılar ──────────────────────────────────────────────────────────
def _sha256_json(nesne: Any) -> str:
    metin = json.dumps(nesne, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(metin.encode("utf-8")).hexdigest()


def _sayi(deger: Any) -> Optional[float]:
    if deger is None or isinstance(deger, bool):
        return None
    try:
        v = float(deger)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def segment_coz(ham: Any) -> tuple[Optional[str], bool]:
    """Segment seçimini (segment, geçerli_mi) döndürür. Boş → (None, True).

    Çağrıldığı yerler:
    - price_provenance.build_price_provenance()
    - main.price_approval_candidate_endpoint(), main.price_approval_endpoint()
    """
    if ham is None:
        return (None, True)
    s = str(ham).strip().lower()
    if not s:
        return (None, True)
    return (s, True) if s in SEGMENTLER else (None, False)


def onay_tablolari_var(db: Session) -> bool:
    """Onay tabloları bu veritabanında var mı? Yoksa okuma yolları 'onay yok' döner.

    Çağrıldığı yerler:
    - gecerli_ptf_onayi(), gecerli_yekdem_onayi(), son_revizyon()
    - main.price_approval_endpoint() → tablo yoksa 503
    """
    try:
        # Oturumun KENDİ bağlantısıyla denetlenir: motor üzerinden ayrı bağlantı almak,
        # tek bağlantılı havuzlarda (StaticPool) bağlantı iadesinde oturumun yazılmamış
        # işlemini geri alabilir.
        denetci = sa.inspect(db.connection())
        return all(denetci.has_table(ad) for ad in ONAY_TABLOLARI)
    except SQLAlchemyError:
        return False


def kayit_parmak_izi(kayit: Any) -> str:
    """PTF kaydının onaya bağlanan durumu: dönem, tip, PTF değeri, durum, kaynak.

    Bu alanlardan herhangi biri sonradan değişirse parmak izi değişir ve onay geçersiz
    olur (kayda hangi yoldan yazıldığından bağımsız). Kayıt yoksa sabit "kayit_yok".

    Çağrıldığı yerler:
    - gecerli_ptf_onayi(), ptf_adayi_hazirla(), ptf_onayla()
    """
    if kayit is None:
        return "kayit_yok"
    deger = _sayi(getattr(kayit, "ptf_tl_per_mwh", None))
    return _sha256_json({
        "period": str(getattr(kayit, "period", "") or ""),
        "price_type": str(getattr(kayit, "price_type", "") or ""),
        "ptf_tl_per_mwh": repr(deger) if deger is not None else None,
        "status": str(getattr(kayit, "status", "") or ""),
        "source": str(getattr(kayit, "source", "") or ""),
    })


def _ptf_kaydi(db: Session, period: str):
    from .database import MarketReferencePrice

    return (
        db.query(MarketReferencePrice)
        .filter(MarketReferencePrice.period == period, MarketReferencePrice.price_type == "PTF")
        .first()
    )


def son_revizyon(db: Session, kalem: str, period: str, segment: Optional[str] = None) -> int:
    """Dönem(+segment) için en son onay revizyonu; hiç yoksa 0.

    Çağrıldığı yerler:
    - ptf_adayi_hazirla(), yekdem_adayi_hazirla(), ptf_onayla(), yekdem_onayla()
    """
    if not onay_tablolari_var(db):
        return 0
    if kalem == KALEM_PTF:
        t = ptf_onay_revizyonlari
        q = sa.select(sa.func.max(t.c.revision)).where(t.c.period == period)
    else:
        t = yekdem_onay_revizyonlari
        q = sa.select(sa.func.max(t.c.revision)).where(t.c.period == period, t.c.segment == segment)
    return int(db.execute(q).scalar() or 0)


def gecerli_ptf_onayi(db: Session, period: str) -> Optional[PtfOnayi]:
    """Dönemin GEÇERLİ PTF onayı: en son revizyon VE kaydın bugünkü parmak izi onaydakiyle aynı.

    Onaydan sonra kayda yapılan her değer/durum/kaynak değişikliği onayı geçersiz kılar.

    Çağrıldığı yerler:
    - price_provenance.effective_ptf_candidates()
    - calculator.get_ptf_yekdem_for_period()
    - main.get_prices_with_epias_fallback()
    """
    if not onay_tablolari_var(db):
        return None
    t = ptf_onay_revizyonlari
    satir = db.execute(
        sa.select(t).where(t.c.period == period).order_by(t.c.revision.desc()).limit(1)
    ).mappings().first()
    if satir is None:
        return None
    kayit = _ptf_kaydi(db, period)
    if kayit is None or kayit.id != satir["price_record_id"]:
        return None
    if kayit_parmak_izi(kayit) != satir["kayit_parmak_izi"]:
        return None
    return PtfOnayi(
        id=satir["id"], period=satir["period"], revision=satir["revision"],
        price_record_id=satir["price_record_id"], value=float(satir["value"]), basis=satir["basis"],
        kaynak_kanit_sha256=satir["kaynak_kanit_sha256"], kayit_parmak_izi=satir["kayit_parmak_izi"],
        onaylayan_beyan=satir["onaylayan_beyan"], dogrulanan_yetki=satir["dogrulanan_yetki"],
        approved_at=satir["approved_at"],
    )


def gecerli_yekdem_onayi(db: Session, period: str, segment: str) -> Optional[YekdemOnayi]:
    """(dönem, segment) için güncel YEKDEM onayı (en büyük revizyon); yoksa None.

    Çağrıldığı yerler:
    - price_provenance.build_price_provenance()
    - main.get_prices_with_epias_fallback()
    """
    if segment not in SEGMENTLER or not onay_tablolari_var(db):
        return None
    t = yekdem_onay_revizyonlari
    satir = db.execute(
        sa.select(t).where(t.c.period == period, t.c.segment == segment)
        .order_by(t.c.revision.desc()).limit(1)
    ).mappings().first()
    if satir is None:
        return None
    return YekdemOnayi(
        id=satir["id"], period=satir["period"], segment=satir["segment"], revision=satir["revision"],
        value=float(satir["value"]), version=satir["version"],
        kaynak_kanit_sha256=satir["kaynak_kanit_sha256"], onaylayan_beyan=satir["onaylayan_beyan"],
        dogrulanan_yetki=satir["dogrulanan_yetki"], approved_at=satir["approved_at"],
    )


def _onaylayan_ozeti(onay: Any) -> dict:
    """Onaylayan bilgisi: beyan (doğrulanmamış) ve doğrulanan yetki ayrı alanlarda."""
    return {"onaylayan_beyan": onay.onaylayan_beyan, "onaylayan_dogrulandi": False,
            "dogrulanan_yetki": onay.dogrulanan_yetki,
            "dogrulanan_yetki_etiketi": YETKI_ETIKETLERI.get(onay.dogrulanan_yetki, onay.dogrulanan_yetki)}


def ptf_onay_ozeti(onay: PtfOnayi) -> dict:
    """Teklif snapshot'ına yazılan PTF onay kimliği (JSON uyumlu).

    Çağrıldığı yerler:
    - price_provenance.effective_ptf_candidates() → PriceCandidate.approval
    """
    return {"id": onay.id, "revision": onay.revision, "price_record_id": onay.price_record_id,
            "basis": onay.basis, "value": onay.value, "kaynak_kanit_sha256": onay.kaynak_kanit_sha256,
            "kayit_parmak_izi": onay.kayit_parmak_izi, **_onaylayan_ozeti(onay),
            "approved_at": onay.approved_at.isoformat() if onay.approved_at else None,
            "yontem_etiketi": PTF_YONTEM_ETIKETI}


def yekdem_onay_ozeti(onay: YekdemOnayi) -> dict:
    """Teklif snapshot'ına yazılan YEKDEM onay kimliği (JSON uyumlu).

    Çağrıldığı yerler:
    - price_provenance.build_price_provenance() → yekdem.approval
    """
    return {"id": onay.id, "revision": onay.revision, "segment": onay.segment,
            "segment_etiketi": SEGMENT_ETIKETLERI.get(onay.segment), "version": onay.version,
            "value": onay.value, "kaynak_kanit_sha256": onay.kaynak_kanit_sha256,
            **_onaylayan_ozeti(onay),
            "approved_at": onay.approved_at.isoformat() if onay.approved_at else None,
            "resmi_kesinlesme": "BELIRSIZ"}


# ── resmî veriden aday ────────────────────────────────────────────────────
def _aday_parmak_izi(aday: dict) -> str:
    return _sha256_json({k: aday.get(k) for k in (
        "kalem", "period", "value", "basis", "segment", "version", "kaynak_kanit_sha256")})


def _kanit_yaz(kanit: dict) -> tuple[str, str]:
    """Kanıtın kanonik JSON metni ve SHA256'sı (onay kaydında AYNEN saklanır, ekranda incelenir)."""
    metin = json.dumps(kanit, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return metin, hashlib.sha256(metin.encode("utf-8")).hexdigest()


def _ay_saatleri(period: str) -> list:
    """Dönemin beklenen saat damgaları (Türkiye sabit +03:00; 2016'dan beri yaz saati yok)."""
    from datetime import timedelta

    yil, ay = int(period[:4]), int(period[5:7])
    tz = timezone(timedelta(hours=3))
    bas = datetime(yil, ay, 1, tzinfo=tz)
    bitis = datetime(yil + (ay == 12), 1 if ay == 12 else ay + 1, 1, tzinfo=tz)
    saatler, t = [], bas
    while t < bitis:
        saatler.append(t)
        t += timedelta(hours=1)
    return saatler


def saatlik_ptf_dogrula(period: str, saatlik: Any, price_avg: Optional[float], sayfa: Any = None) -> dict:
    """Aritmetik PTF adayının saatlik dayanağını denetler (SALT HESAP; ağ/DB yok).

    Denetimler: her kalemin geçerli saat damgası ve sayısal fiyatı; dönem kapsamı (dönem dışı
    saat yok); tekillik (aynı saat iki kez yok); eksiksizlik (dönemin her saati var); sayfalama
    kırpılması yok; saatlik ortalamanın 2 ondalığa yuvarlanmış hâlinin yayımlanan priceAvg ile
    tutarlılığı (|ortalama − priceAvg| ≤ 0,005). Herhangi biri bozuksa onaylanabilir DEĞİL.

    Çağrıldığı yerler:
    - ptf_resmi_aday() → onay adayı ve kaynak kanıtı
    """
    beklenen = _ay_saatleri(period)
    beklenen_kume = set(beklenen)
    nedenler: list = []
    gecersiz_damga = gecersiz_fiyat = donem_disi = 0
    gorulen: dict = {}
    fiyatlar: list = []
    for damga, fiyat in (saatlik or ()):
        try:
            t = datetime.fromisoformat(str(damga))
        except ValueError:
            gecersiz_damga += 1
            continue
        if t.tzinfo is None:
            gecersiz_damga += 1
            continue
        if t not in beklenen_kume:
            donem_disi += 1
            continue
        gorulen[t] = gorulen.get(t, 0) + 1
        f = _sayi(fiyat)
        if f is None:
            gecersiz_fiyat += 1
        else:
            fiyatlar.append(f)
    tekrar = sum(adet - 1 for adet in gorulen.values() if adet > 1)
    eksik = [t for t in beklenen if t not in gorulen]
    donen = len(tuple(saatlik or ()))
    if donen == 0:
        nedenler.append("saatlik_veri_yok")
    if gecersiz_damga:
        nedenler.append("gecersiz_saat_damgasi")
    if donem_disi:
        nedenler.append("donem_disi_saat")
    if tekrar:
        nedenler.append("tekrarlanan_saat")
    if eksik:
        nedenler.append("eksik_saat")
    if gecersiz_fiyat:
        nedenler.append("gecersiz_fiyat")
    sayfa_toplam = sayfa.get("total") if isinstance(sayfa, dict) else None
    if isinstance(sayfa_toplam, (int, float)) and int(sayfa_toplam) != donen:
        nedenler.append("sayfalama_kirpilmasi")
    ortalama = math.fsum(fiyatlar) / len(fiyatlar) if fiyatlar else None
    fark = None
    if price_avg is None:
        nedenler.append("price_avg_yok")
    elif ortalama is not None:
        fark = abs(ortalama - price_avg)
        if fark > 0.005 + 1e-9:
            nedenler.append("ortalama_yuvarlama_tutarsiz")
    return {
        "beklenen_saat": len(beklenen), "donen_kalem": donen, "tekil_gecerli_saat": len(gorulen),
        "eksik_saat": len(eksik), "eksik_ornek": [t.isoformat() for t in eksik[:3]],
        "tekrarlanan_saat": tekrar, "donem_disi_saat": donem_disi,
        "gecersiz_saat_damgasi": gecersiz_damga, "gecersiz_fiyat": gecersiz_fiyat,
        "ilk_saat": beklenen[0].isoformat(), "son_saat": beklenen[-1].isoformat(),
        "sayfa_toplam": sayfa_toplam,
        "saatlik_ortalama": repr(ortalama) if ortalama is not None else None,
        "yayimlanan_price_avg": repr(price_avg) if price_avg is not None else None,
        "ortalama_farki": repr(fark) if fark is not None else None,
        "yuvarlama_toleransi": "0.005",
        "saatlik_veri_sha256": _sha256_json(sorted([str(d), repr(_sayi(f))] for d, f in (saatlik or ()))),
        "onaylanabilir": not nedenler, "nedenler": nedenler,
    }


def ptf_resmi_aday(client: Any, period: str) -> dict:
    """Resmî PTF adayı (EPİAŞ mcp priceAvg) + saatlik dayanak denetimi — DB'ye dokunmaz.

    Değer yayımlanan `statistic.priceAvg`dır (yuvarlanmaz, dönüştürülmez). Saatlik dayanak
    eksik ya da tutarsızsa aday yine gösterilir ama `onaylanabilir=False` olur ve onay reddedilir.

    Çağrıldığı yerler:
    - ptf_adayi_hazirla(), ptf_onayla() → resmi verilmemişse
    - main.price_approval_candidate_endpoint(), main.price_approval_endpoint() → external_api sarmalayıcısı
    """
    from .epias_public_client import MCP_PATH, _ay_ilk_gun, _ay_son_gun

    ozet = client.fetch_mcp(period)
    deger = _sayi(getattr(ozet, "aritmetik", None))
    if deger is None or deger < 0:
        raise OnayHatasi("resmi_veri_yok", "EPİAŞ bu dönem için aritmetik PTF döndürmedi.", 422)
    dogrulama = saatlik_ptf_dogrula(period, getattr(ozet, "saatlik", ()), deger, getattr(ozet, "sayfa", None))
    kanit = {
        "kalem": KALEM_PTF, "kaynak": "EPİAŞ Şeffaflık Platformu", "servis": MCP_PATH,
        "istek": {"startDate": _ay_ilk_gun(period), "endDate": _ay_son_gun(period)},
        "period": period, "alan": "statistic.priceAvg", "priceAvg": repr(deger),
        "ptfWeightedAvg": repr(_sayi(getattr(ozet, "agirlikli", None))),
        "birim": getattr(ozet, "birim", None), "yontem": PTF_YONTEM_ARITMETIK,
        "saatlik_dogrulama": dogrulama,
    }
    kanit_json, kanit_sha = _kanit_yaz(kanit)
    aday = {"kalem": KALEM_PTF, "period": period, "value": deger, "basis": PTF_YONTEM_ARITMETIK,
            "segment": None, "version": None, "birim": getattr(ozet, "birim", None),
            "kaynak_kanit_sha256": kanit_sha, "kaynak_kanit": kanit, "kaynak_kanit_json": kanit_json,
            "yontem_etiketi": PTF_YONTEM_ETIKETI,
            "onaylanabilir": dogrulama["onaylanabilir"], "onaylanamama_nedenleri": dogrulama["nedenler"]}
    aday["aday_parmak_izi"] = _aday_parmak_izi(aday)
    return aday


def versiyonlari_sirala(versiyonlar: Any) -> list:
    """Tam versiyon metinlerini yayım zamanına göre sıralar (eskiden yeniye).

    Her metin saat dilimli ISO tarih olmalıdır (EPİAŞ: "2026-07-01T00:00:00+03:00").
    Ayrıştırılamayan ya da aynı ana karşılık gelen farklı metinler varsa "en son" belirsizdir
    → 422 resmi_versiyon_siralanamaz (metin sıralamasıyla tahmin yapılmaz).

    Çağrıldığı yerler:
    - yekdem_resmi_aday()
    """
    anlar = {}
    for metin in versiyonlar:
        try:
            an = datetime.fromisoformat(str(metin))
        except ValueError:
            an = None
        if an is None or an.tzinfo is None:
            raise OnayHatasi("resmi_versiyon_siralanamaz",
                             f"EPİAŞ YEKDEM versiyonu tarih olarak okunamadı ({metin!r}); en son versiyon belirlenemez.",
                             422)
        anlar[metin] = an
    if len(set(anlar.values())) != len(anlar):
        raise OnayHatasi("resmi_versiyon_siralanamaz",
                         "Aynı yayım anını gösteren farklı versiyon metinleri var; en son versiyon belirsiz.", 422)
    return sorted(anlar, key=anlar.__getitem__)


def yekdem_resmi_aday(client: Any, period: str, segment: str) -> dict:
    """Resmî YEKDEM adayı (EPİAŞ unit-cost, segment başına) — DB'ye dokunmaz.

    Versiyon resmî yanıttaki TAM metindir; aynı ay içindeki farklı versiyonlar ayrı adaydır ve
    en son yayımlanan onaya sunulur. Aynı tam versiyon farklı değerlerle gelirse aday
    belirsizdir (reddedilir). Gerçek 0 değeri geçerlidir; değer YOKSA (null) aday oluşmaz.

    Çağrıldığı yerler:
    - yekdem_adayi_hazirla(), yekdem_onayla() → resmi verilmemişse
    - main.price_approval_candidate_endpoint(), main.price_approval_endpoint() → external_api sarmalayıcısı
    """
    from .epias_public_client import UNIT_COST_PATH, _ay_ilk_gun, _ay_son_gun

    satirlar = [s for s in (client.fetch_unit_cost(period, period) or []) if s.donem == period]
    if not satirlar:
        raise OnayHatasi("resmi_veri_yok", "EPİAŞ bu dönem için YEKDEM yayımlamadı.", 422)
    if any(not getattr(s, "versiyon_tam", None) for s in satirlar):
        raise OnayHatasi("resmi_versiyon_yok", "EPİAŞ YEKDEM satırı tam versiyon bilgisi taşımıyor.", 422)
    tam = versiyonlari_sirala({s.versiyon_tam for s in satirlar})
    en_son = tam[-1]
    secilenler = [s for s in satirlar if s.versiyon_tam == en_son]
    degerler = {(_sayi(s.serbest_tuketici), _sayi(s.gts_k1)) for s in secilenler}
    if len(degerler) != 1:
        raise OnayHatasi("resmi_versiyon_belirsiz",
                         "Aynı tam YEKDEM versiyonu farklı değerlerle döndü; aday belirsiz.", 422)
    st, gts = next(iter(degerler))
    deger = st if segment == "st" else gts
    if deger is None:
        raise OnayHatasi("resmi_segment_degeri_yok",
                         f"EPİAŞ satırında {SEGMENT_ETIKETLERI[segment]} değeri yok (eksik değer sıfır sayılmaz).",
                         422)
    kanit = {
        "kalem": KALEM_YEKDEM, "kaynak": "EPİAŞ Şeffaflık Platformu", "servis": UNIT_COST_PATH,
        "istek": {"startDate": _ay_ilk_gun(period), "endDate": _ay_son_gun(period)},
        "period": period, "version": en_son, "ayni_donem_versiyonlari": tam,
        "supplierUnitCost": repr(st), "unitCost": repr(gts), "birim": secilenler[0].birim,
        "segment": segment, "segment_alani": "supplierUnitCost" if segment == "st" else "unitCost",
        "resmi_kesinlesme": "BELIRSIZ",
    }
    kanit_json, kanit_sha = _kanit_yaz(kanit)
    aday = {"kalem": KALEM_YEKDEM, "period": period, "value": deger, "basis": None,
            "segment": segment, "version": en_son, "birim": secilenler[0].birim,
            "kaynak_kanit_sha256": kanit_sha, "kaynak_kanit": kanit, "kaynak_kanit_json": kanit_json,
            "segment_etiketi": SEGMENT_ETIKETLERI[segment], "onaylanabilir": True,
            "onaylanamama_nedenleri": []}
    aday["aday_parmak_izi"] = _aday_parmak_izi(aday)
    return aday


def ptf_adayi_hazirla(db: Session, client: Any, period: str, resmi: Optional[dict] = None) -> dict:
    """PTF onay adayı + kaydın bugünkü durumu (SALT OKUNUR; hiçbir şey yazmaz).

    Çağrıldığı yerler:
    - main.price_approval_candidate_endpoint() → GET /admin/market-prices/approval-candidate
    - ptf_onayla() → sunucu tarafı yeniden çekim
    """
    aday = dict(resmi) if resmi is not None else ptf_resmi_aday(client, period)
    kayit = _ptf_kaydi(db, period)
    onay = gecerli_ptf_onayi(db, period)
    aday.update({
        "beklenen_revision": son_revizyon(db, KALEM_PTF, period),
        "beklenen_kayit_parmak_izi": kayit_parmak_izi(kayit),
        "mevcut_kayit": None if kayit is None else {
            "id": kayit.id, "ptf_tl_per_mwh": kayit.ptf_tl_per_mwh, "status": kayit.status,
            "source": kayit.source},
        "gecerli_onay": None if onay is None else {"revision": onay.revision, "value": onay.value,
                                                    **_onaylayan_ozeti(onay)},
    })
    return aday


def yekdem_adayi_hazirla(db: Session, client: Any, period: str, segment: str,
                         resmi: Optional[dict] = None) -> dict:
    """YEKDEM onay adayı (segment başına; SALT OKUNUR).

    Çağrıldığı yerler:
    - main.price_approval_candidate_endpoint()
    - yekdem_onayla() → sunucu tarafı yeniden çekim
    """
    aday = dict(resmi) if resmi is not None else yekdem_resmi_aday(client, period, segment)
    onay = gecerli_yekdem_onayi(db, period, segment)
    aday.update({
        "beklenen_revision": son_revizyon(db, KALEM_YEKDEM, period, segment),
        "gecerli_onay": None if onay is None else {"revision": onay.revision, "value": onay.value,
                                                    "version": onay.version,
                                                    **_onaylayan_ozeti(onay)},
    })
    return aday


# ── yazma: tek işlem ─────────────────────────────────────────────────────
def _gecmis_satiri_ekle(db: Session, **alanlar: Any) -> None:
    """Fiyat geçmişi satırı — onay İŞLEMİNİN parçası (ayrı commit/best-effort DEĞİL).

    Çağrıldığı yerler:
    - ptf_onayla()
    """
    from .database import PriceChangeHistory

    db.execute(sa.insert(PriceChangeHistory.__table__).values(**alanlar))


def _ortak_dogrulama(onaylayan_beyan: Any, change_reason: Any, dogrulanan_yetki: Any) -> tuple[str, str, str]:
    """Beyan edilen ad + gerekçe + sunucunun doğruladığı yetki türü.

    `dogrulanan_yetki` istemciden ALINMAZ; yalnız uç bağımlılığının (require_price_approval_key)
    döndürdüğü değerdir. Bilinmeyen tür yazılmaz.
    """
    yetki = str(dogrulanan_yetki or "")
    if yetki not in YETKI_TURLERI:
        raise OnayHatasi("yetki_dogrulanmadi", "Onay isteğinin yetkisi sunucuda doğrulanmadı.", 403)
    onaylayan = str(onaylayan_beyan or "").strip()
    gerekce = str(change_reason or "").strip()
    if not onaylayan:
        raise OnayHatasi("onaylayan_zorunlu", "Onaylayan adı (beyan) zorunludur.", 422)
    if len(onaylayan) > 100:
        raise OnayHatasi("onaylayan_gecersiz", "Onaylayan adı en fazla 100 karakter olabilir.", 422)
    if not gerekce:
        raise OnayHatasi("gerekce_zorunlu", "Onay gerekçesi (change_reason) zorunludur.", 422)
    return onaylayan, gerekce, yetki


def ptf_onayla(
    db: Session,
    client: Any,
    *,
    period: str,
    aday_parmak_izi: str,
    beklenen_revision: int,
    beklenen_kayit_parmak_izi: str,
    onaylayan_beyan: str,
    dogrulanan_yetki: str,
    change_reason: str,
    yaris_kancasi: Optional[Callable[[], None]] = None,
    resmi: Optional[dict] = None,
) -> dict:
    """Aylık aritmetik PTF'i yetkili onayıyla kaydeder (tek işlem).

    `resmi`: sunucunun o istekte resmî veriden YENİDEN çektiği aday (uç, çekimi
    external_api sarmalayıcısında yapar). Verilmezse `client` ile burada çekilir.
    İstemcinin gönderdiği değer hiçbir yolda kullanılmaz.

    Ret (hiçbir şey yazılmaz): aday değişti (409), kayıt değişti (409), yarışan onay
    (409), onay tabloları yok (503), eksik onaylayan/gerekçe (422).

    `yaris_kancasi` YALNIZ testler içindir: okuma ile yazma arasına eşzamanlı işlem sokar.

    Çağrıldığı yerler:
    - main.price_approval_endpoint() → POST /admin/market-prices/approve (kalem=PTF)
    """
    from .database import MarketReferencePrice

    onaylayan, gerekce, yetki = _ortak_dogrulama(onaylayan_beyan, change_reason, dogrulanan_yetki)
    if not onay_tablolari_var(db):
        raise OnayHatasi("onay_altyapisi_yok",
                         "Onay tabloları bu veritabanında yok (migration uygulanmamış).", 503)
    aday = ptf_adayi_hazirla(db, client, period, resmi=resmi)
    if aday["aday_parmak_izi"] != aday_parmak_izi:
        raise OnayHatasi("aday_degisti", "Resmî aday ekranda gösterilenden farklı; yeniden onaya sunun.",
                         409, yeni_aday=_aday_yaniti(aday))
    if not aday.get("onaylanabilir"):
        raise OnayHatasi("aday_dogrulanamadi",
                         "Resmî adayın saatlik dayanağı eksik ya da tutarsız; onaylanamaz: "
                         + ", ".join(aday.get("onaylanamama_nedenleri") or []), 422, yeni_aday=_aday_yaniti(aday))
    if aday["beklenen_revision"] != int(beklenen_revision):
        raise OnayHatasi("kayit_degisti", "Bu dönem için başka bir onay yapılmış; yeniden onaya sunun.",
                         409, yeni_aday=_aday_yaniti(aday))
    if aday["beklenen_kayit_parmak_izi"] != beklenen_kayit_parmak_izi:
        raise OnayHatasi("kayit_degisti", "Fiyat kaydı aday gösterildikten sonra değişti; yeniden onaya sunun.",
                         409, yeni_aday=_aday_yaniti(aday))

    kayit = _ptf_kaydi(db, period)
    eski = None if kayit is None else {
        "id": kayit.id, "ptf": kayit.ptf_tl_per_mwh, "status": kayit.status, "source": kayit.source}
    if yaris_kancasi is not None:
        yaris_kancasi()

    simdi = datetime.now(timezone.utc).replace(tzinfo=None)
    tablo = MarketReferencePrice.__table__
    try:
        if eski is None:
            sonuc = db.execute(sa.insert(tablo).values(
                price_type="PTF", period=period, ptf_tl_per_mwh=aday["value"], yekdem_tl_per_mwh=0,
                status="final", source=ONAY_KAYNAK_ETIKETI, captured_at=simdi, change_reason=gerekce,
                updated_by=onaylayan, is_locked=0, created_at=simdi, updated_at=simdi))
            kayit_id = sonuc.inserted_primary_key[0]
            eylem = "INSERT"
        else:
            # Karşılaştır-ve-yaz: okunan durum araya giren bir yazımla değiştiyse 0 satır.
            sonuc = db.execute(
                sa.update(tablo)
                .where(tablo.c.id == eski["id"], tablo.c.ptf_tl_per_mwh == eski["ptf"],
                       tablo.c.status == eski["status"], tablo.c.source == eski["source"])
                .values(ptf_tl_per_mwh=aday["value"], status="final", source=ONAY_KAYNAK_ETIKETI,
                        captured_at=simdi, change_reason=gerekce, updated_by=onaylayan, updated_at=simdi))
            if sonuc.rowcount != 1:
                raise OnayHatasi("kayit_degisti", "Fiyat kaydı onay sırasında değişti; yeniden onaya sunun.", 409)
            kayit_id = eski["id"]
            eylem = "UPDATE"
        _gecmis_satiri_ekle(
            db, price_record_id=kayit_id, price_type="PTF", period=period, action=eylem,
            old_value=None if eski is None else eski["ptf"], new_value=aday["value"],
            old_status=None if eski is None else eski["status"], new_status="final",
            change_reason=(f"{gerekce} [yetkili onay: {PTF_YONTEM_ARITMETIK} r{int(beklenen_revision) + 1}; "
                           f"yetki: {yetki}; onaylayan adı beyandır]"),
            updated_by=onaylayan, source=ONAY_KAYNAK_ETIKETI, created_at=simdi)
        yeni_iz = _sha256_json({
            "period": period, "price_type": "PTF", "ptf_tl_per_mwh": repr(float(aday["value"])),
            "status": "final", "source": ONAY_KAYNAK_ETIKETI})
        yeni_revizyon = int(beklenen_revision) + 1
        db.execute(sa.insert(ptf_onay_revizyonlari).values(
            period=period, revision=yeni_revizyon, price_record_id=kayit_id, value=aday["value"],
            basis=PTF_YONTEM_ARITMETIK, kaynak_kanit_sha256=aday["kaynak_kanit_sha256"],
            kaynak_kanit_json=aday["kaynak_kanit_json"],
            kayit_parmak_izi=yeni_iz, captured_at=simdi, onaylayan_beyan=onaylayan, dogrulanan_yetki=yetki, approved_at=simdi,
            change_reason=gerekce))
        db.commit()
    except OnayHatasi:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        raise OnayHatasi("onay_yarisi", "Aynı revizyon için başka bir onay önce tamamlandı; yeniden onaya sunun.",
                         409) from None
    except Exception:
        db.rollback()
        raise
    logger.info("[FIYAT-ONAY] PTF %s r%s onaylandı (%s)", period, yeni_revizyon, onaylayan)
    return {"kalem": KALEM_PTF, "period": period, "revision": yeni_revizyon, "value": aday["value"],
            "basis": PTF_YONTEM_ARITMETIK, "yontem_etiketi": PTF_YONTEM_ETIKETI,
            "kaynak_kanit_sha256": aday["kaynak_kanit_sha256"], "onceki_kayit": eski,
            "onaylayan_beyan": onaylayan, "onaylayan_dogrulandi": False, "dogrulanan_yetki": yetki}


def yekdem_onayla(
    db: Session,
    client: Any,
    *,
    period: str,
    segment: str,
    aday_parmak_izi: str,
    beklenen_revision: int,
    onaylayan_beyan: str,
    dogrulanan_yetki: str,
    change_reason: str,
    yaris_kancasi: Optional[Callable[[], None]] = None,
    resmi: Optional[dict] = None,
) -> dict:
    """Segment başına YEKDEM onayı (ekle-yalnız revizyon; tek INSERT).

    `resmi`: sunucunun o istekte yeniden çektiği aday; verilmezse `client` ile çekilir.

    Çağrıldığı yerler:
    - main.price_approval_endpoint() → POST /admin/market-prices/approve (kalem=YEKDEM)
    """
    onaylayan, gerekce, yetki = _ortak_dogrulama(onaylayan_beyan, change_reason, dogrulanan_yetki)
    if segment not in SEGMENTLER:
        raise OnayHatasi("segment_zorunlu", "YEKDEM onayı için segment (st | gts) açıkça seçilmelidir.", 422)
    if not onay_tablolari_var(db):
        raise OnayHatasi("onay_altyapisi_yok",
                         "Onay tabloları bu veritabanında yok (migration uygulanmamış).", 503)
    aday = yekdem_adayi_hazirla(db, client, period, segment, resmi=resmi)
    if aday["aday_parmak_izi"] != aday_parmak_izi:
        raise OnayHatasi("aday_degisti", "Resmî aday ekranda gösterilenden farklı; yeniden onaya sunun.",
                         409, yeni_aday=_aday_yaniti(aday))
    if aday["beklenen_revision"] != int(beklenen_revision):
        raise OnayHatasi("kayit_degisti", "Bu dönem/segment için başka bir onay yapılmış; yeniden onaya sunun.",
                         409, yeni_aday=_aday_yaniti(aday))
    if yaris_kancasi is not None:
        yaris_kancasi()
    simdi = datetime.now(timezone.utc).replace(tzinfo=None)
    yeni_revizyon = int(beklenen_revision) + 1
    try:
        db.execute(sa.insert(yekdem_onay_revizyonlari).values(
            period=period, segment=segment, revision=yeni_revizyon, value=aday["value"],
            version=aday["version"], kaynak_kanit_sha256=aday["kaynak_kanit_sha256"],
            kaynak_kanit_json=aday["kaynak_kanit_json"], captured_at=simdi,
            onaylayan_beyan=onaylayan, dogrulanan_yetki=yetki, approved_at=simdi, change_reason=gerekce))
        db.commit()
    except IntegrityError:
        db.rollback()
        raise OnayHatasi("onay_yarisi", "Aynı revizyon için başka bir onay önce tamamlandı; yeniden onaya sunun.",
                         409) from None
    except Exception:
        db.rollback()
        raise
    logger.info("[FIYAT-ONAY] YEKDEM %s/%s r%s onaylandı (%s)", period, segment, yeni_revizyon, onaylayan)
    return {"kalem": KALEM_YEKDEM, "period": period, "segment": segment, "revision": yeni_revizyon,
            "value": aday["value"], "version": aday["version"],
            "kaynak_kanit_sha256": aday["kaynak_kanit_sha256"], "onaylayan_beyan": onaylayan, "onaylayan_dogrulandi": False, "dogrulanan_yetki": yetki}


def calistir_izole(bind: Any, fn: Callable, /, *args: Any, **kwargs: Any):
    """`fn`i request-scoped Session yerine `bind`e (engine) bağlı KISA ÖMÜRLÜ kendi
    Session'ıyla çalıştırır; Session finally'de kapatılır.

    Neden (epias_compare.kayitlari_oku_izole ile aynı gerekçe): fiyat onay/aday/geçmiş
    uçları bu çağrıyı `_get_wrapper("db_primary")` üzerinden yapar; wrapper
    `asyncio.wait_for(asyncio.to_thread(...), timeout)` kullanır. Zaman aşımında `to_thread`
    worker'ı İPTAL EDİLEMEZ; TimeoutError yükselirken thread arka planda (orphan) çalışmaya
    DEVAM eder (Python 3.13.14 ile ampirik doğrulandı). Bu orphan, get_db'nin finally'de
    kapattığı REQUEST Session'ını kullanırsa iş parçacıkları arası Session kullanımı doğar:
    ampirik olarak get_db close ile worker commit'i çakışıp yazmayı SESSİZCE düşürebilir
    (belirlenimsiz sonuç). Bu sarmalayıcı ile worker YALNIZ kendi Session'ına dokunur ve onu
    KENDİSİ kapatır; request Session'ı hiç görmez → Session paylaşımı yapısal olarak ELENİR.

    `fn` tek Session içinde çalıştığından işlem ATOMİKLİĞİ ve revizyon/parmak izi
    karşılaştır-ve-yaz denetimleri KORUNUR. `fn` düz dict döndürür (ORM detach sorunu yok).

    SINIR: Bu sarmalayıcı zaman aşımı SONRASI orphan commit'ini (phantom-write) ENGELLEMEZ;
    yalnız Session paylaşımını eler. Zaman aşımı belirsizliği uçta ayrıca ele alınır
    (bkz. main.price_approval_endpoint → 504 "belirsiz" + approvals uzlaştırması); tekrar
    denemede çift yazım revizyon benzersizliği + parmak izi denetimiyle engellenir.

    Çağrıldığı yerler:
    - main.price_approval_endpoint() → ptf_onayla / yekdem_onayla (is_write=True)
    - main.price_approval_candidate_endpoint() → ptf_adayi_hazirla / yekdem_adayi_hazirla
    - main.price_approval_history_endpoint() → onay_gecmisi
    """
    izole = Session(bind=bind)
    try:
        return fn(izole, *args, **kwargs)
    finally:
        izole.close()


def _aday_yaniti(aday: dict) -> dict:
    """API yanıtı için aday (kanonik kanıt metni yerine ayrıştırılmış kanıt nesnesi)."""
    return {k: v for k, v in aday.items() if k != "kaynak_kanit_json"}


def aday_yaniti(aday: dict) -> dict:
    """Aday ucunun döndürdüğü biçim (kanıt incelenebilir nesne olarak).

    Çağrıldığı yerler:
    - main.price_approval_candidate_endpoint()
    """
    return _aday_yaniti(aday)


def onay_gecmisi(db: Session, period: str) -> dict:
    """Dönemin tüm onay revizyonları + kaynak kanıtları (SALT OKUNUR; ekranda inceleme için).

    PTF revizyonu için `gecerli`: en son revizyon VE kaydın bugünkü parmak izi aynı.
    YEKDEM için segment başına en büyük revizyon güncel onaydır. "resmi_kesinlesme" BELİRSİZ.

    Çağrıldığı yerler:
    - main.price_approval_history_endpoint() → GET /admin/market-prices/approvals
    """
    if not onay_tablolari_var(db):
        return {"period": period, "altyapi": False, "ptf": [], "yekdem": []}
    gecerli = gecerli_ptf_onayi(db, period)

    def _satir(m):
        d = dict(m)
        d["kaynak_kanit"] = json.loads(d.pop("kaynak_kanit_json"))
        for alan in ("captured_at", "approved_at"):
            if d.get(alan) is not None:
                d[alan] = d[alan].isoformat()
        return d

    t, y = ptf_onay_revizyonlari, yekdem_onay_revizyonlari
    ptf = [_satir(m) for m in db.execute(sa.select(t).where(t.c.period == period)
                                         .order_by(t.c.revision.desc())).mappings()]
    for satir in ptf:
        satir["gecerli"] = bool(gecerli is not None and gecerli.id == satir["id"])
    yekdem = [_satir(m) for m in db.execute(sa.select(y).where(y.c.period == period)
                                            .order_by(y.c.segment, y.c.revision.desc())).mappings()]
    gorulen: set = set()
    for satir in yekdem:
        satir["guncel"] = satir["segment"] not in gorulen
        gorulen.add(satir["segment"])
    return {"period": period, "altyapi": True, "ptf": ptf, "yekdem": yekdem,
            "resmi_kesinlesme": "BELIRSIZ"}
