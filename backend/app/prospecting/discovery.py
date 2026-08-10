"""
S4 — Prospecting — discovery provider arayüzü + V1 implementasyonu.

Source-agnostic mimari (owner kararı, "don't lock architecture to one
provider"): ProspectDiscoveryProvider bir Protocol olarak tanımlanır —
search()/fetch_company() metodları. V1 tek bir somut provider'a MİMARİ
olarak KİLİTLENMEZ; gelecekte B) OSB/oda dizinleri veya D) ücretli
sağlayıcı adaptörleri aynı arayüzle eklenebilir.

*** ÖNEMLİ, GERÇEK BULGU (S4-WB4, bu turda canlı test edildi) ***
V1 provider'ı önce owner'ın tercih sırası C'sini (açık arama motoru
sonuç keşfi) denedi: DuckDuckGo'nun JS'siz HTML arayüzü
(html.duckduckgo.com/html/). Canlı testte DDG istekleri bir
"anomaly-modal" (bot doğrulama / CAPTCHA-benzeri challenge) sayfasıyla
karşılandı. Owner kararı açık: "CAPTCHA bypass YOK." — bu challenge'ı
AŞMAYA ÇALIŞMADIK (form submit etmek, farklı header/user-agent denemek,
vb. HİÇBİRİ yapılmadı). Bunun yerine:
- search() bu durumu SESSİZCE boş liste olarak DÖNDÜRMEZ (owner:
  "hiçbir kayıt sessizce kaybolmayacak") — SearchOutcome.status =
  "UNAVAILABLE" ile AÇIKÇA işaretler, kullanıcıya net mesaj gösterilir.
  Şart farklı bir zamanda/koşulda değişip DDG normal sonuç döndürürse
  (anomaly-modal yoksa, result__a linkleri varsa) kod bunları GERÇEK
  DiscoveryCandidate'lara çevirir — yani search() best-effort'tur,
  KAPALI değildir.
- fetch_company() (doğrudan bir şirket web sitesi URL'sinden enrichment,
  bkz. enrichment.py) HİÇBİR arama motoruna bağımlı DEĞİLDİR — bu V1'in
  BİRİNCİL, HER ZAMAN ÇALIŞAN keşif yoludur; kullanıcı arama
  kullanılamadığında da bir URL'yi doğrudan girerek devam edebilir.

Çağrıldığı yerler:
- app/prospecting/service.py (POST /prospects/discover) [S4-WB4]
"""
from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Optional, Protocol
from urllib.parse import parse_qs, quote, unquote, urlparse

from .security import STATUS_OK, safe_get

MAX_SEARCH_RESULTS = 20  # owner: "max result count" — bounded, sınırsız tarama YOK

OUTCOME_OK = "OK"
OUTCOME_UNAVAILABLE = "UNAVAILABLE"  # kaynak bot-challenge/beklenmeyen yapı döndürdü — bypass EDİLMEDİ
OUTCOME_FETCH_FAILED = "FETCH_FAILED"  # SSRF reddi/timeout/genel hata (bkz. security.FetchResult)


@dataclass
class DiscoveryCandidate:
    title: str
    url: str
    snippet: str
    source_type: str = "SEARCH_RESULT"


@dataclass
class SearchOutcome:
    status: str  # OK | UNAVAILABLE | FETCH_FAILED
    candidates: list[DiscoveryCandidate] = field(default_factory=list)
    message: Optional[str] = None


class ProspectDiscoveryProvider(Protocol):
    """
    Gelecekteki provider'ların (B/D) uyması gereken sözleşme. V1'de tek
    somut implementasyon DuckDuckGoHtmlProvider'dır.
    """

    def search(self, query: str, *, limit: int = MAX_SEARCH_RESULTS) -> SearchOutcome: ...


class _DdgResultParser(HTMLParser):
    """
    DuckDuckGo HTML-lite sonuç sayfasına özgü, dar kapsamlı parser —
    yalnız `result__a` class'lı linkleri ve anomaly-modal varlığını
    tespit eder. Genel amaçlı enrichment._PageHTMLParser'dan BİLİNÇLİ
    OLARAK ayrı tutuldu (farklı, siteye özgü bir yapıyı hedefliyor).
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.saw_anomaly_modal = False
        self.results: list[DiscoveryCandidate] = []
        self._capturing_result_link = False
        self._current_href: Optional[str] = None
        self._current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        attr_dict = dict(attrs)
        classes = (attr_dict.get("class") or "").split()
        if tag == "div" and "anomaly-modal" in classes:
            self.saw_anomaly_modal = True
        if tag == "a" and "result__a" in classes:
            self._capturing_result_link = True
            self._current_href = attr_dict.get("href") or ""
            self._current_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._capturing_result_link:
            title = " ".join(self._current_text).strip()
            if self._current_href:
                self.results.append(
                    DiscoveryCandidate(title=title, url=_resolve_ddg_redirect(self._current_href), snippet="")
                )
            self._capturing_result_link = False
            self._current_href = None
            self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._capturing_result_link:
            self._current_text.append(data)


def _resolve_ddg_redirect(href: str) -> str:
    """DDG sonuç linkleri //duckduckgo.com/l/?uddg=<encoded_real_url> şeklinde sarmalanır."""
    if "uddg=" not in href:
        return href
    parsed = urlparse(href if "://" in href else f"https:{href}")
    qs = parse_qs(parsed.query)
    real = qs.get("uddg", [None])[0]
    return unquote(real) if real else href


class DuckDuckGoHtmlProvider:
    """
    V1 — owner'ın tercih sırası C'si (açık arama motoru sonuç keşfi).
    Login/CAPTCHA/anti-bot-bypass GEREKTİRMEZ ve DENEMEZ — bkz. modül
    docstring'indeki canlı test bulgusu.
    """

    def search(self, query: str, *, limit: int = MAX_SEARCH_RESULTS) -> SearchOutcome:
        if not query or not query.strip():
            return SearchOutcome(status=OUTCOME_OK, candidates=[], message="Boş sorgu.")

        url = f"https://html.duckduckgo.com/html/?q={quote(query.strip())}"
        fetch = safe_get(url, timeout_s=10.0)

        if fetch.status != STATUS_OK or not fetch.text:
            return SearchOutcome(
                status=OUTCOME_FETCH_FAILED,
                message=f"Arama kaynağına ulaşılamadı ({fetch.status}).",
            )

        parser = _DdgResultParser()
        try:
            parser.feed(fetch.text)
        except Exception:
            return SearchOutcome(status=OUTCOME_UNAVAILABLE, message="Arama sonucu ayrıştırılamadı.")

        if parser.saw_anomaly_modal or not parser.results:
            # Bot-challenge tespit edildi (ya da hiç sonuç linki bulunamadı,
            # ki bu da genelde aynı nedenden kaynaklanır) — BYPASS EDİLMEZ.
            return SearchOutcome(
                status=OUTCOME_UNAVAILABLE,
                message=(
                    "Otomatik arama şu an kullanılamıyor (kaynak bir bot doğrulaması "
                    "sunuyor — bu aşılmaya çalışılmadı). Şirketin web sitesini doğrudan "
                    "adres olarak girerek devam edebilirsiniz."
                ),
            )

        return SearchOutcome(status=OUTCOME_OK, candidates=parser.results[:limit])


def build_search_query(keyword: str, city: Optional[str] = None, district: Optional[str] = None) -> str:
    parts = [keyword.strip()] if keyword and keyword.strip() else []
    if city and city.strip():
        parts.append(city.strip())
    if district and district.strip():
        parts.append(district.strip())
    return " ".join(parts)
