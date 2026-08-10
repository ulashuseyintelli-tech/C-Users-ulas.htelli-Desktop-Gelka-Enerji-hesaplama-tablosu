"""
S4 — Prospecting — website enrichment (DETERMİNİSTİK, LLM YOK).

Güvenlik yükümlülüğü (owner'ın S4 GO'sunda ben — Claude — bu turda açıkça
taahhüt ettim, kod olarak burada uygulanıyor): website içeriği YALNIZ
regex/stdlib HTML parsing ile okunur; hiçbir aşamada bir LLM'e
"yorumlatılmaz". Gerekçe: taranan bir şirket web sitesine gömülü,
bana yönelik bir talimat gibi görünen (prompt-injection tarzı) metin,
bu mimaride hiçbir zaman bir "komut" olarak İŞLENEMEZ — yalnız düz veri
olarak (email/telefon adayı, kısa alıntı) ele alınır.

Tüm dış HTTP istekleri app/prospecting/security.py::safe_get() ÜZERİNDEN
yapılır (SSRF-safe tek giriş noktası) — bu modül httpx'i DOĞRUDAN
kullanmaz.

Bounded crawl (owner kararı): en fazla 3 sayfa denenir — ana sayfa
(her zaman), iletişim (ana sayfadaki linklerden bulunur, bulunamazsa TEK
bir fallback path denenir), hakkımızda (aynı mantık). Toplam istek
sayısı sabit ve küçük — "İlk sürümde interneti sınırsız tara butonu YOK"
ilkesiyle uyumlu. Recursive crawl YOK, dosya indirme YOK.

Çağrıldığı yerler:
- app/prospecting/service.py (POST /prospects/{id}/enrich, discover akışı) [S4-WB4/WB5]
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urljoin, urlparse

from .normalize import email_domain, is_free_mail_domain
from .security import STATUS_OK, FetchResult, safe_get

MAX_PAGES = 3
_EXCERPT_MAX_LEN = 280

_CONTACT_PATH_FALLBACK = "/iletisim"
_ABOUT_PATH_FALLBACK = "/hakkimizda"

_CONTACT_KEYWORDS = ("iletişim", "iletisim", "contact", "bize ulaş", "bize ulas")
_ABOUT_KEYWORDS = ("hakkımızda", "hakkimizda", "about", "kurumsal", "biz kimiz")

_EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
# TR telefon: opsiyonel +90/0 öneki + alan kodu + numara, ayraçlar serbest.
_PHONE_PATTERN = re.compile(
    r"(?:\+?90[\s.\-]?)?0?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{2}[\s.\-]?\d{2}\b"
)

_GENERAL_LOCAL_PARTS = {"info", "iletisim", "contact", "bilgi", "merhaba", "hello", "office"}
_DEPARTMENT_LOCAL_PARTS = {
    "satis", "sales", "satinalma", "purchasing", "muhasebe", "accounting",
    "ik", "hr", "insankaynaklari", "pazarlama", "marketing", "destek", "support",
    "ihracat", "export", "ithalat", "import",
}

CONTACT_TYPE_GENERAL = "GENERAL_CORPORATE"
CONTACT_TYPE_DEPARTMENT = "DEPARTMENT"
CONTACT_TYPE_NAMED_PERSON = "NAMED_CORPORATE_PERSON"
CONTACT_TYPE_FREE_MAIL = "PERSONAL_OR_FREE_MAIL"
CONTACT_TYPE_OTHER = "OTHER"


class _PageHTMLParser(HTMLParser):
    """
    Stdlib html.parser tabanlı, DETERMİNİSTİK çıkarım. <script>/<style>
    içeriği metne dahil EDİLMEZ (hem gürültü hem güvenlik — sayfaya gömülü
    JS kodu asla "okunan metin" olarak değerlendirilmez). mailto:/tel:
    linkleri ayrıca, en güvenilir sinyal olarak toplanır.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: str = ""
        self.text_parts: list[str] = []
        self.links: list[tuple[str, str]] = []  # (href, link_text)
        self.mailto: list[str] = []
        self.tel: list[str] = []
        self._in_title = False
        self._skip_depth = 0  # script/style içindeyken > 0
        self._current_href: Optional[str] = None
        self._current_link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        attr_dict = dict(attrs)
        if tag in ("script", "style"):
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "a":
            href = attr_dict.get("href") or ""
            self._current_href = href
            self._current_link_text = []
            if href.lower().startswith("mailto:"):
                self.mailto.append(href[7:].split("?")[0].strip())
            elif href.lower().startswith("tel:"):
                self.tel.append(href[4:].strip())

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False
        elif tag == "a" and self._current_href is not None:
            self.links.append((self._current_href, " ".join(self._current_link_text).strip()))
            self._current_href = None
            self._current_link_text = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        if self._in_title:
            self.title += data
        if self._current_href is not None:
            self._current_link_text.append(data)
        stripped = data.strip()
        if stripped:
            self.text_parts.append(stripped)

    @property
    def full_text(self) -> str:
        return " ".join(self.text_parts)


def sanitize_excerpt(text: str, max_len: int = _EXCERPT_MAX_LEN) -> str:
    """
    HIGH PRIORITY (owner): "sanitize evidence excerpt". Girdi zaten
    _PageHTMLParser'dan (script/style hariç, yalnız düz metin) geliyor —
    burada ayrıca kontrol karakterleri/fazla boşluk temizlenir ve kısa
    bir uzunluğa kesilir. Ham HTML/etiket İÇERMEZ.
    """
    if not text:
        return ""
    no_control = "".join(ch for ch in text if ch == " " or ch.isprintable())
    collapsed = re.sub(r"\s+", " ", no_control).strip()
    if len(collapsed) > max_len:
        return collapsed[:max_len].rstrip() + "…"
    return collapsed


def extract_emails(text: str, mailto: Optional[list[str]] = None) -> list[str]:
    found = set(m.group(0).lower() for m in _EMAIL_PATTERN.finditer(text))
    for m in mailto or []:
        candidate = m.strip().lower()
        if candidate and "@" in candidate:
            found.add(candidate)
    return sorted(found)


def extract_phones(text: str, tel: Optional[list[str]] = None) -> list[str]:
    found = set(m.group(0).strip() for m in _PHONE_PATTERN.finditer(text))
    for t in tel or []:
        if t.strip():
            found.add(t.strip())
    return sorted(found)


def classify_contact_type(email: str) -> str:
    """
    Owner — E-MAIL DISCOVERY sınıflandırması. Sıra önemli: önce free-mail
    (kurumsal olmayan domain kesin sinyal), sonra local-part sözlükleri,
    sonra "ad.soyad" deseni, hiçbiri değilse OTHER.
    """
    domain = email_domain(email)
    if not domain:
        return CONTACT_TYPE_OTHER
    if is_free_mail_domain(domain):
        return CONTACT_TYPE_FREE_MAIL

    local_part = email.split("@", 1)[0].lower()
    if local_part in _GENERAL_LOCAL_PARTS:
        return CONTACT_TYPE_GENERAL
    if local_part in _DEPARTMENT_LOCAL_PARTS:
        return CONTACT_TYPE_DEPARTMENT
    # "ad.soyad@kurumsal.tld" deseni — iki alfabetik parça, nokta ile ayrılmış.
    if re.fullmatch(r"[a-zçğıöşü]+\.[a-zçğıöşü]+", local_part, flags=re.IGNORECASE):
        return CONTACT_TYPE_NAMED_PERSON
    return CONTACT_TYPE_OTHER


def _find_link(links: list[tuple[str, str]], keywords: tuple[str, ...]) -> Optional[str]:
    for href, text in links:
        haystack = f"{href} {text}".lower()
        if any(kw in haystack for kw in keywords):
            return href
    return None


@dataclass
class PageEnrichment:
    url: str
    fetch: FetchResult
    title: str = ""
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    excerpt: str = ""
    content_hash: Optional[str] = None


@dataclass
class EnrichmentResult:
    pages: list[PageEnrichment]

    @property
    def all_emails(self) -> list[str]:
        seen: dict[str, None] = {}
        for p in self.pages:
            for e in p.emails:
                seen.setdefault(e, None)
        return list(seen.keys())

    @property
    def all_phones(self) -> list[str]:
        seen: dict[str, None] = {}
        for p in self.pages:
            for ph in p.phones:
                seen.setdefault(ph, None)
        return list(seen.keys())


def _fetch_and_parse(url: str) -> tuple[PageEnrichment, Optional[_PageHTMLParser]]:
    fetch = safe_get(url)
    page = PageEnrichment(url=url, fetch=fetch)
    if fetch.status != STATUS_OK or not fetch.text:
        return page, None

    parser = _PageHTMLParser()
    try:
        parser.feed(fetch.text)
    except Exception:
        # Bozuk/uyumsuz HTML — sessizce yutulmaz, sayfa OK fetch edildi
        # ama parse edilemedi olarak işaretlenir (excerpt boş kalır,
        # emails/phones boş kalır — çağıran bunu ProspectSource'a yine
        # de bir satır olarak yazar, "sessizce kaybolmaz").
        return page, None

    page.title = sanitize_excerpt(parser.title, max_len=200)
    page.emails = extract_emails(parser.full_text, parser.mailto)
    page.phones = extract_phones(parser.full_text, parser.tel)
    page.excerpt = sanitize_excerpt(parser.full_text)
    page.content_hash = hashlib.sha256(fetch.text.encode("utf-8", errors="replace")).hexdigest()
    return page, parser


def enrich_website(base_url: str) -> EnrichmentResult:
    """
    Bounded website enrichment — MAX_PAGES (3) sayfa: ana sayfa + tespit
    edilen/varsayılan iletişim sayfası + tespit edilen/varsayılan hakkımızda
    sayfası. Her sayfa TEK bir safe_get() çağrısı — recursive crawl YOK.
    """
    pages: list[PageEnrichment] = []

    home_page, home_parser = _fetch_and_parse(base_url)
    pages.append(home_page)

    contact_href = _find_link(home_parser.links, _CONTACT_KEYWORDS) if home_parser else None
    contact_url = urljoin(base_url, contact_href) if contact_href else urljoin(base_url, _CONTACT_PATH_FALLBACK)
    if contact_url != home_page.url:
        contact_page, _ = _fetch_and_parse(contact_url)
        pages.append(contact_page)

    about_href = _find_link(home_parser.links, _ABOUT_KEYWORDS) if home_parser else None
    about_url = urljoin(base_url, about_href) if about_href else urljoin(base_url, _ABOUT_PATH_FALLBACK)
    if about_url != home_page.url and len(pages) < MAX_PAGES:
        about_page, _ = _fetch_and_parse(about_url)
        pages.append(about_page)

    return EnrichmentResult(pages=pages)


def normalize_base_url(website: str) -> Optional[str]:
    """Kullanıcı/kaynak girişini (şemasız olabilir) bir fetch edilebilir base URL'e çevirir."""
    if not website or not website.strip():
        return None
    candidate = website.strip()
    if "://" not in candidate:
        candidate = "https://" + candidate
    parsed = urlparse(candidate)
    if not parsed.hostname:
        return None
    return candidate
