"""
EPİAŞ ↔ Gelka SALT OKUNUR fiyat karşılaştırması.

Kural (DUZELTME-EKI-01):
- Yalnız OKUR. Fiyat/şema yazmaz, onay/kesinleştirme yapmaz, hesaplama
  önceliklerini değiştirmez, otomatik senkron kurmaz.
- Karşılaştırma UYGULANABİLİR kimlik alanlarına göre yapılır:
  PTF için yöntem; YEKDEM için versiyon ve segment. Bir kalemde uygulanmayan
  alan karşılaştırmayı ENGELLEMEZ ("uygulanamaz").
- Bilinmeyen alan TAHMİN EDİLMEZ; satır KARSILASTIRILAMAZ olur.
- Kimlik eşleşmedikçe FİYAT FARKI ÜRETİLMEZ.
- EPİAŞ adayı hiçbir zaman "kesinleşmiş" sayılmaz (kesinlik: BELIRSIZ).
- Kayıttaki kimlik jetonu bir İDDİADIR; bağlayıcı kanıt değildir.

Çağrıldığı yerler:
- main.epias_compare_endpoint() → GET /admin/market-prices/epias-compare
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from .epias_public_client import BIRIM, EpiasIstemciHatasi, YekdemSatiri, _maskele

logger = logging.getLogger(__name__)

TOLERANS_TL_MWH = 0.005
MAKS_DONEM = 12

TIP_AYNI = "AYNI"
TIP_DEGER_FARKI = "DEGER_FARKI"
TIP_DONEM = "DONEM_UYUSMAZLIGI"
TIP_BIRIM = "BIRIM_UYUSMAZLIGI"
TIP_YONTEM = "YONTEM_UYUSMAZLIGI"
TIP_VERSIYON = "VERSIYON_UYUSMAZLIGI"
TIP_SEGMENT = "SEGMENT_UYUSMAZLIGI"
TIP_YOK = "KARSILASTIRILAMAZ"

ESLESTI = "eslesti"
BILINMIYOR = "bilinmiyor"
UYGULANAMAZ = "uygulanamaz"
UYUSMUYOR = "uyusmuyor"

# Kayıt kaynağındaki jetondan çözülen PTF yöntemleri (yalnız OKUMA).
YONTEM_JETONLARI = {
    "mcp_avg": "mcp_avg",
    "mcp_wavg": "mcp_wavg",
    "abone_agirlikli": "abone_agirlikli",
}
SEGMENT_JETONLARI = {"st": "st", "gts": "gts"}


def _donem_gecerli(donem: Any) -> bool:
    s = str(donem or "")
    return len(s) == 7 and s[4] == "-" and s[:4].isdigit() and s[5:].isdigit() and 1 <= int(s[5:]) <= 12


def donem_listesi(baslangic: str, bitis: str) -> list:
    """YYYY-MM aralığını dönem listesine çevirir (kapsayıcı).

    Çağrıldığı yerler:
    - build_comparison()
    - main.epias_compare_endpoint() → aralık sınırı denetimi
    """
    if not _donem_gecerli(baslangic) or not _donem_gecerli(bitis):
        raise ValueError("Dönem biçimi YYYY-MM olmalı.")
    yil, ay = int(baslangic[:4]), int(baslangic[5:])
    son_yil, son_ay = int(bitis[:4]), int(bitis[5:])
    if (yil, ay) > (son_yil, son_ay):
        raise ValueError("Başlangıç dönemi bitişten sonra olamaz.")
    cikti = []
    while (yil, ay) <= (son_yil, son_ay):
        cikti.append("%04d-%02d" % (yil, ay))
        ay += 1
        if ay == 13:
            yil, ay = yil + 1, 1
    return cikti


def ptf_kimligi(kayit: Any) -> dict:
    """Kayıttan PTF kimliği (iddia). Yöntem jetonu yoksa 'bilinmiyor'.

    YEKDEM alanları PTF için UYGULANAMAZ; karşılaştırmayı engellemez.

    Çağrıldığı yerler:
    - build_comparison()
    """
    kaynak = str(getattr(kayit, "source", "") or "")
    yontem = None
    if "." in kaynak:
        son = kaynak.rsplit(".", 1)[-1].strip().lower()
        yontem = YONTEM_JETONLARI.get(son)
    return {
        "donem": getattr(kayit, "period", None),
        "birim": BIRIM,
        "yontem": yontem,
        "versiyon": UYGULANAMAZ,
        "segment": UYGULANAMAZ,
        "kaynak": kaynak or None,
    }


def yekdem_kimligi(kayit: Any, gecmis_satiri: Any) -> dict:
    """Kayıt + en son YEKDEM geçmiş satırından YEKDEM kimliği (iddia).

    Versiyon ya da segment yoksa TAHMİN EDİLMEZ; None kalır.
    Yöntem YEKDEM için UYGULANAMAZ.

    Çağrıldığı yerler:
    - build_comparison()
    """
    versiyon = None
    segment = None
    jeton = str(getattr(gecmis_satiri, "source", "") or "")
    parcalar = [p.strip().lower() for p in jeton.split(".")]
    if len(parcalar) >= 4 and parcalar[1] == "uc":
        segment = SEGMENT_JETONLARI.get(parcalar[2])
        aday = parcalar[3]
        if _donem_gecerli(aday):
            versiyon = aday
    return {
        "donem": getattr(kayit, "period", None),
        "birim": BIRIM,
        "yontem": UYGULANAMAZ,
        "versiyon": versiyon,
        "segment": segment,
        "kaynak": jeton or None,
    }


def _alan_durumu(gelka: Any, epias: Any) -> str:
    if gelka == UYGULANAMAZ or epias == UYGULANAMAZ:
        return UYGULANAMAZ
    if gelka is None or epias is None:
        return BILINMIYOR
    return ESLESTI if str(gelka) == str(epias) else UYUSMUYOR


def classify_row(kalem: str, gelka: dict, epias: dict, gelka_deger: Optional[float],
                 epias_deger: Optional[float], tolerans: float = TOLERANS_TL_MWH) -> dict:
    """SAF sınıflandırma: DB ve ağ kullanmaz.

    Kimlik eşleşmedikçe 'fark' ÜRETİLMEZ (None kalır).

    Çağrıldığı yerler:
    - build_comparison()
    - backend/tests/test_epias_compare.py
    """
    alanlar = {ad: _alan_durumu(gelka.get(ad), epias.get(ad))
               for ad in ("donem", "birim", "yontem", "versiyon", "segment")}
    uygulanabilir = ("yontem",) if kalem == "PTF" else ("versiyon", "segment")

    def sonuc(tip, nedenler, fark=None):
        return {"tip": tip, "nedenler": nedenler, "alanlar": alanlar, "fark": fark}

    if alanlar["donem"] == BILINMIYOR:
        return sonuc(TIP_YOK, ["donem_bilinmiyor"])
    if alanlar["donem"] == UYUSMUYOR:
        return sonuc(TIP_DONEM, ["donem_uyusmuyor"])
    if alanlar["birim"] == UYUSMUYOR:
        return sonuc(TIP_BIRIM, ["birim_uyusmuyor"])
    eksik = [ad for ad in uygulanabilir if alanlar[ad] == BILINMIYOR]
    if eksik:
        return sonuc(TIP_YOK, [ad + "_bilinmiyor" for ad in eksik])
    for ad, tip in (("yontem", TIP_YONTEM), ("versiyon", TIP_VERSIYON), ("segment", TIP_SEGMENT)):
        if ad in uygulanabilir and alanlar[ad] == UYUSMUYOR:
            return sonuc(tip, [ad + "_uyusmuyor"])
    if gelka_deger is None or epias_deger is None:
        return sonuc(TIP_YOK, ["deger_yok"])
    fark = round(float(gelka_deger) - float(epias_deger), 4)
    return sonuc(TIP_AYNI if abs(fark) <= tolerans else TIP_DEGER_FARKI, [], fark)


def _yekdem_adayi(satirlar: list, donem: str, degerlendirme_ayi: str) -> Optional[YekdemSatiri]:
    uygun = [s for s in satirlar if s.donem == donem and s.versiyon and s.versiyon <= degerlendirme_ayi]
    if not uygun:
        return None
    return sorted(uygun, key=lambda s: s.versiyon)[-1]


def build_comparison(db: Session, donem_baslangic: str, donem_bitis: str, client: Any,
                     evaluated_at: Optional[date] = None, bugun: Optional[date] = None) -> dict:
    """Kayıtları ve EPİAŞ'ı OKUR, karşılaştırma raporu üretir. HİÇBİR YAZMA YOK.

    Çağrıldığı yerler:
    - main.epias_compare_endpoint() → GET /admin/market-prices/epias-compare
    """
    from .database import MarketReferencePrice, PriceChangeHistory

    donemler = donem_listesi(donem_baslangic, donem_bitis)
    if len(donemler) > MAKS_DONEM:
        raise ValueError("En fazla %d dönem sorgulanabilir." % MAKS_DONEM)
    bugun = bugun or datetime.now(timezone.utc).date()
    evaluated_at = evaluated_at or bugun
    gecmis = evaluated_at < bugun
    degerlendirme_ayi = "%04d-%02d" % (evaluated_at.year, evaluated_at.month)

    uyarilar = []
    if gecmis:
        uyarilar.append({"kod": "gecmis_tarih_kaniti_yok",
                         "mesaj": ("Değerlendirme tarihi geçmişte (%s). EPİAŞ yalnız BUGÜN yayımlanan "
                                   "versiyonları döndürür; o tarihte hangi versiyonun bilindiği "
                                   "KANITLANAMAZ." % evaluated_at.isoformat())})

    kayitlar = {k.period: k for k in db.query(MarketReferencePrice).filter(
        MarketReferencePrice.price_type == "PTF",
        MarketReferencePrice.period.in_(donemler)).all()}
    yekdem_gecmisi = {}
    for donem, kayit in kayitlar.items():
        yekdem_gecmisi[donem] = (db.query(PriceChangeHistory)
                                 .filter(PriceChangeHistory.price_record_id == kayit.id,
                                         PriceChangeHistory.price_type == "YEKDEM")
                                 .order_by(PriceChangeHistory.id.desc()).first())

    try:
        yekdem_satirlari = client.fetch_unit_cost(donem_baslangic, donem_bitis)
        yekdem_hatasi = None
    except EpiasIstemciHatasi as exc:
        yekdem_satirlari, yekdem_hatasi = [], _maskele(exc)
        uyarilar.append({"kod": "yekdem_api_hatasi", "mesaj": yekdem_hatasi})

    satirlar = []
    for donem in donemler:
        kayit = kayitlar.get(donem)
        try:
            ptf_ozet = client.fetch_mcp(donem)
            ptf_hatasi = None
        except EpiasIstemciHatasi as exc:
            ptf_ozet, ptf_hatasi = None, _maskele(exc)
            uyarilar.append({"kod": "ptf_api_hatasi", "donem": donem, "mesaj": ptf_hatasi})

        satirlar.append(_ptf_satiri(donem, kayit, ptf_ozet, ptf_hatasi))
        satirlar.append(_yekdem_satiri(donem, kayit, yekdem_gecmisi.get(donem),
                                       _yekdem_adayi(yekdem_satirlari, donem, degerlendirme_ayi),
                                       yekdem_hatasi, gecmis))

    ozet = {}
    for s in satirlar:
        ozet[s["tip"]] = ozet.get(s["tip"], 0) + 1
    return {
        "kayit": "EPIAS-KARSILASTIRMA",
        "olusturuldu_utc": datetime.now(timezone.utc).isoformat(),
        "evaluated_at": evaluated_at.isoformat(),
        "gecmis_tarih_kaniti": "yok" if gecmis else "uygulanamaz",
        "tolerans_tl_mwh": TOLERANS_TL_MWH,
        "donemler": donemler,
        "uyarilar": uyarilar,
        "satirlar": satirlar,
        "ozet": ozet,
        "not": ("Salt okunur. EPİAŞ adayı kesinleşmiş SAYILMAZ; kayıttaki kimlik jetonu "
                "iddiadır, bağlayıcı kanıt değildir."),
    }


_BOS_KIMLIK_PTF = {"donem": None, "birim": BIRIM, "yontem": None,
                   "versiyon": UYGULANAMAZ, "segment": UYGULANAMAZ, "kaynak": None}
_BOS_KIMLIK_YEKDEM = {"donem": None, "birim": BIRIM, "yontem": UYGULANAMAZ,
                      "versiyon": None, "segment": None, "kaynak": None}


def _yok_sonucu(nedenler: list) -> dict:
    return {"tip": TIP_YOK, "nedenler": nedenler, "fark": None,
            "alanlar": {ad: BILINMIYOR for ad in ("donem", "birim", "yontem", "versiyon", "segment")}}


def _satir(kalem: str, donem: str, kayit: Any, gelka_kimlik: dict, gelka_deger: Optional[float],
           epias_kimlik: Optional[dict], epias_deger: Optional[float], sonuc: dict,
           notlar: Optional[list] = None) -> dict:
    return {
        "donem": donem,
        "kalem": kalem,
        "tip": sonuc["tip"],
        "nedenler": list(sonuc["nedenler"]) + list(notlar or []),
        "alanlar": sonuc["alanlar"],
        "fark": sonuc["fark"],
        "gelka": {
            "deger": gelka_deger,
            "birim": gelka_kimlik.get("birim"),
            "yontem": gelka_kimlik.get("yontem"),
            "versiyon": gelka_kimlik.get("versiyon"),
            "segment": gelka_kimlik.get("segment"),
            "kaynak_jetonu": gelka_kimlik.get("kaynak"),
            "durum": getattr(kayit, "status", None),
            "kayit_id": getattr(kayit, "id", None),
        },
        "epias": epias_kimlik and {**epias_kimlik, "deger": epias_deger},
    }


def _ptf_satiri(donem: str, kayit: Any, ptf_ozet: Any, ptf_hatasi: Optional[str]) -> dict:
    """PTF satırı. YEKDEM alanları UYGULANAMAZ olduğu için karşılaştırmayı engellemez."""
    if kayit is None:
        return _satir("PTF", donem, None, dict(_BOS_KIMLIK_PTF), None, None, None,
                      _yok_sonucu(["kayit_yok"]))
    gelka_kimlik = ptf_kimligi(kayit)
    gelka_deger = _float(getattr(kayit, "ptf_tl_per_mwh", None))
    if ptf_hatasi or ptf_ozet is None:
        return _satir("PTF", donem, kayit, gelka_kimlik, gelka_deger, None, None,
                      _yok_sonucu(["api_hatasi"]))
    yontem = gelka_kimlik.get("yontem")
    if yontem == "mcp_wavg":
        epias_deger, epias_yontem = ptf_ozet.agirlikli, "mcp_wavg"
    else:
        epias_deger, epias_yontem = ptf_ozet.aritmetik, "mcp_avg"
    epias_kimlik = {"donem": ptf_ozet.donem, "birim": ptf_ozet.birim, "yontem": epias_yontem,
                    "versiyon": UYGULANAMAZ, "segment": UYGULANAMAZ, "kesinlik": "BELIRSIZ"}
    sonuc = classify_row("PTF", gelka_kimlik, epias_kimlik, gelka_deger, epias_deger)
    notlar = ["epias_karsiligi_yok"] if yontem == "abone_agirlikli" else []
    return _satir("PTF", donem, kayit, gelka_kimlik, gelka_deger, epias_kimlik, epias_deger, sonuc, notlar)


def _yekdem_satiri(donem: str, kayit: Any, gecmis_satiri: Any, aday: Optional[YekdemSatiri],
                   yekdem_hatasi: Optional[str], gecmis: bool) -> dict:
    """YEKDEM satırı. Yöntem UYGULANAMAZ; versiyon/segment bilinmiyorsa TAHMİN YOK."""
    notlar = ["gecmis_tarih_kaniti_yok"] if gecmis else []
    if kayit is None:
        return _satir("YEKDEM", donem, None, dict(_BOS_KIMLIK_YEKDEM), None, None, None,
                      _yok_sonucu(["kayit_yok"]), notlar)
    gelka_kimlik = yekdem_kimligi(kayit, gecmis_satiri)
    gelka_deger = _float(getattr(kayit, "yekdem_tl_per_mwh", None))
    if yekdem_hatasi:
        return _satir("YEKDEM", donem, kayit, gelka_kimlik, gelka_deger, None, None,
                      _yok_sonucu(["api_hatasi"]), notlar)
    if aday is None:
        return _satir("YEKDEM", donem, kayit, gelka_kimlik, gelka_deger, None, None,
                      _yok_sonucu(["epias_versiyon_yok"]), notlar)
    segment = gelka_kimlik.get("segment")
    if segment == "st" and aday.serbest_tuketici is not None:
        epias_segment, epias_deger = "st", aday.serbest_tuketici
    elif segment == "gts" and aday.gts_k1 is not None:
        epias_segment, epias_deger = "gts", aday.gts_k1
    elif aday.serbest_tuketici is not None:
        epias_segment, epias_deger = "st", aday.serbest_tuketici
    elif aday.gts_k1 is not None:
        epias_segment, epias_deger = "gts", aday.gts_k1
    else:
        epias_segment, epias_deger = None, None
    epias_kimlik = {"donem": aday.donem, "birim": aday.birim, "yontem": UYGULANAMAZ,
                    "versiyon": aday.versiyon, "segment": epias_segment, "kesinlik": "BELIRSIZ",
                    "aday_secimi": "degerlendirme_ayina_kadar_son_yayimlanan"}
    sonuc = classify_row("YEKDEM", gelka_kimlik, epias_kimlik, gelka_deger, epias_deger)
    return _satir("YEKDEM", donem, kayit, gelka_kimlik, gelka_deger, epias_kimlik, epias_deger, sonuc, notlar)


def _float(deger: Any) -> Optional[float]:
    if deger is None or isinstance(deger, bool):
        return None
    try:
        return float(deger)
    except (TypeError, ValueError):
        return None
