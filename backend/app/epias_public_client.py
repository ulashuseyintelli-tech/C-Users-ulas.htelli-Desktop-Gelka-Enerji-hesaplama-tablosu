"""
EPİAŞ Şeffaflık Platformu — SALT OKUNUR karşılaştırma istemcisi.

Kapsam (READ_ONLY_EPIAS_COMPARISON):
- Yalnız iki servis OKUNUR: PTF (mcp) istatistikleri ve YEKDEM birim maliyeti.
- Hiçbir yazma yok: DB'ye, dosyaya ve EPİAŞ'a yazmaz.
- Kimlik bilgileri ortam değişkenlerinden okunur; yanıta, loga ve hata metnine
  ASLA yazılmaz (_maskele).
- Özellik VARSAYILAN KAPALI: EPIAS_COMPARE_ENABLED=true olmadan kullanılmaz.
- Bu istemci eptr2/pandas kullanmaz; yalnız mevcut bağımlılık httpx.

Çağrıldığı yerler:
- epias_compare.build_comparison() → GET /admin/market-prices/epias-compare
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

BASE_URL = "https://seffaflik.epias.com.tr"
MCP_PATH = "/electricity-service/v1/markets/dam/data/mcp"
UNIT_COST_PATH = "/electricity-service/v1/renewables/data/unit-cost"
TGT_URL = "https://giris.epias.com.tr/cas/v1/tickets"
BIRIM = "TL/MWh"
VARSAYILAN_ZAMAN_ASIMI = 20.0

# CAS bilet jetonlari: TGT-... (ticket granting) ve ST-... (service ticket).
_BILET_DESENI = re.compile("(?:TGT|ST)-[A-Za-z0-9._~-]+")


def ozellik_acik() -> bool:
    """EPİAŞ karşılaştırması açık mı? (varsayılan KAPALI)

    Çağrıldığı yerler:
    - main.epias_compare_endpoint() → GET /admin/market-prices/epias-compare
    """
    return os.getenv("EPIAS_COMPARE_ENABLED", "false").strip().lower() == "true"


class EpiasIstemciHatasi(Exception):
    """EPİAŞ okuma hatası. Mesajı maskelenmiş olarak taşır."""


@dataclass(frozen=True)
class PtfOzet:
    """Bir dönem için PTF istatistikleri (EPİAŞ mcp servisi)."""
    donem: str
    aritmetik: Optional[float]
    agirlikli: Optional[float]
    birim: str = BIRIM


@dataclass(frozen=True)
class YekdemSatiri:
    """YEKDEM birim maliyeti: dönem x versiyon x segment."""
    donem: str
    versiyon: Optional[str]
    serbest_tuketici: Optional[float]
    gts_k1: Optional[float]
    birim: str = BIRIM


def _maskele(metin: Any) -> str:
    """Kimlik bilgisi ve bilet içerebilecek metni maskeler.

    Çağrıldığı yerler:
    - EpiasReadOnlyClient._istek() → hata metni üretimi
    """
    s = str(metin)
    for gizli in (os.getenv("EPIAS_PASSWORD"), os.getenv("EPIAS_USERNAME")):
        if gizli:
            s = s.replace(gizli, "***")
    # Bilet metnin ORTASINDA da geçebilir (ör. "ticket=TGT-..."); boşlukla
    # ayrılmış jeton varsayımı yetersizdi.
    return _BILET_DESENI.sub("***", s)


def _ay_ilk_gun(donem: str) -> str:
    return donem + "-01T00:00:00+03:00"


def _ay_son_gun(donem: str) -> str:
    yil, ay = int(donem[:4]), int(donem[5:7])
    if ay == 12:
        yil, ay = yil + 1, 1
    else:
        ay += 1
    from datetime import date, timedelta
    son = date(yil, ay, 1) - timedelta(days=1)
    return son.isoformat() + "T23:00:00+03:00"


class EpiasReadOnlyClient:
    """EPİAŞ salt-okunur istemcisi (TGT + iki POST).

    http parametresi testlerde sahte istemciyle doldurulur; üretimde httpx.

    Çağrıldığı yerler:
    - epias_compare.build_comparison()
    """

    def __init__(self, http: Any = None, timeout: float = VARSAYILAN_ZAMAN_ASIMI):
        self._http = http
        self._timeout = timeout
        self._tgt: Optional[str] = None

    def _client(self) -> Any:
        if self._http is None:
            import httpx
            self._http = httpx.Client(timeout=self._timeout)
        return self._http

    def kapat(self) -> None:
        """Varsa HTTP oturumunu kapatır (istek sonunda uç tarafından çağrılır).

        Çağrıldığı yerler:
        - main.epias_compare_endpoint() → finally
        """
        kapat = getattr(self._http, "close", None)
        if callable(kapat):
            kapat()
        self._tgt = None

    def get_tgt(self) -> str:
        """TGT alır. Kullanıcı adı/şifre yalnız bu istekte kullanılır."""
        if self._tgt:
            return self._tgt
        kullanici = os.getenv("EPIAS_USERNAME")
        sifre = os.getenv("EPIAS_PASSWORD")
        if not kullanici or not sifre:
            raise EpiasIstemciHatasi("EPİAŞ kimlik bilgisi yok (EPIAS_USERNAME/EPIAS_PASSWORD).")
        try:
            yanit = self._client().post(
                TGT_URL,
                data={"username": kullanici, "password": sifre},
                headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "text/plain"},
            )
        except Exception as exc:
            raise EpiasIstemciHatasi("TGT alınamadı: " + _maskele(exc)) from None
        # BELGE (teknik dokuman §2): "Başarılı Sonuç: HTTP 201 Created ile TGT değeri: TGT-..."
        # Örnek kodlar bileti YANIT GÖVDESİNDEN (response.text) okur. Başlıktan okuma
        # belgede KANITLANMADIĞI için uygulanmaz.
        if getattr(yanit, "status_code", 0) not in (200, 201):
            raise EpiasIstemciHatasi("TGT reddedildi (HTTP %s)." % getattr(yanit, "status_code", "?"))
        bilet = str(getattr(yanit, "text", "") or "").strip()
        if not bilet.startswith("TGT-"):
            raise EpiasIstemciHatasi("TGT yanıtı belgelenen biçimde değil (gövde 'TGT-' ile başlamıyor).")
        self._tgt = bilet
        logger.info("[EPIAS-COMPARE] TGT alındı (maskeli).")
        return bilet

    def _istek(self, yol: str, govde: dict) -> dict:
        basliklar = {"TGT": self.get_tgt(), "Content-Type": "application/json",
                     "Accept": "application/json"}
        try:
            yanit = self._client().post(BASE_URL + yol, json=govde, headers=basliklar)
        except Exception as exc:
            raise EpiasIstemciHatasi("EPİAŞ isteği başarısız: " + _maskele(exc)) from None
        kod = getattr(yanit, "status_code", 0)
        if kod != 200:
            raise EpiasIstemciHatasi("EPİAŞ yanıtı HTTP %s." % kod)
        try:
            return yanit.json()
        except Exception as exc:
            raise EpiasIstemciHatasi("EPİAŞ yanıtı çözümlenemedi: " + _maskele(exc)) from None

    def fetch_mcp(self, donem: str) -> PtfOzet:
        """Bir dönem için PTF aritmetik ve ağırlıklı ortalamasını okur."""
        ham = self._istek(MCP_PATH, {"startDate": _ay_ilk_gun(donem), "endDate": _ay_son_gun(donem)})
        # BELGE: PtfResponseDto alanı "statistic" (TEKİL); istatistikler
        # PtfResponseStatisticsDto (priceAvg, ptfWeightedAvg) içinde.
        ist = (ham or {}).get("statistic")
        if not isinstance(ist, dict):
            raise EpiasIstemciHatasi("EPİAŞ PTF yanıtında 'statistic' alanı yok.")
        return PtfOzet(
            donem=donem,
            aritmetik=_sayi(ist.get("priceAvg")),
            agirlikli=_sayi(ist.get("ptfWeightedAvg")),
        )

    def fetch_unit_cost(self, donem_baslangic: str, donem_bitis: str) -> list:
        """Dönem aralığı için YEKDEM birim maliyeti satırlarını okur."""
        ham = self._istek(UNIT_COST_PATH, {"startDate": _ay_ilk_gun(donem_baslangic),
                                           "endDate": _ay_son_gun(donem_bitis)})
        satirlar = []
        for oge in (ham or {}).get("items") or []:
            donem = str(oge.get("period") or "")[:7]
            versiyon = str(oge.get("version") or "")[:7] or None
            satirlar.append(YekdemSatiri(
                donem=donem,
                versiyon=versiyon,
                serbest_tuketici=_sayi(oge.get("supplierUnitCost")),
                gts_k1=_sayi(oge.get("unitCost")),
            ))
        return satirlar


def _sayi(deger: Any) -> Optional[float]:
    if deger is None or isinstance(deger, bool):
        return None
    try:
        return float(deger)
    except (TypeError, ValueError):
        return None
