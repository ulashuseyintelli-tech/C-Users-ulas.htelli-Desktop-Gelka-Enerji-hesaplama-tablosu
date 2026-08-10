"""
S4 — Prospecting — normalization yardımcıları.

Owner kararı: "normalize et ama ham değeri kaybetme." Bu modüldeki HİÇBİR
fonksiyon, çağıranın ham/orijinal değeri (legal_name, trade_name, website,
phone alanlarının kullanıcıya GÖSTERİLEN hali) üzerine YAZMAZ — yalnız
AYRI bir dedup/karşılaştırma anahtarı (normalized_name, normalized_domain)
üretir. "Türkçe karakterleri bilinçsiz ASCII'ye çevirip canonical display
değerini kaybetme" (owner) — bu ASCII-fold SADECE normalize_name() içinde,
yalnızca dedup anahtarı için yapılır; legal_name/trade_name'e asla
uygulanmaz ve service.py bu fonksiyonların çıktısını display alanlarına
YAZMAZ.

Çağrıldığı yerler:
- app/prospecting/dedup.py (candidate matching) [S4-WB3]
- app/prospecting/service.py (create/discover sırasında normalized_*
  alanlarını doldurma) [S4-WB2]
- app/prospecting/enrichment.py (contact_type sınıflandırması için
  FREE_MAIL_DOMAINS) [S4-WB4]
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional
from urllib.parse import urlparse

# E-posta sınıflandırması (owner — E-MAIL DISCOVERY): bu domain'lerden
# gelen adresler PERSONAL_OR_FREE_MAIL sayılır, kurumsal sinyal DEĞİLDİR.
FREE_MAIL_DOMAINS = frozenset({
    "gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "yahoo.com.tr",
    "icloud.com", "mail.com", "yandex.com", "yandex.com.tr", "protonmail.com",
    "live.com", "msn.com", "hotmail.com.tr", "outlook.com.tr", "windowslive.com",
})

# Dedup anahtarı üretirken kaldırılan çok yaygın TR/EN şirket sonekleri.
# Yalnız normalize_name() içinde kullanılır — display alanlarına ASLA
# uygulanmaz. Amaç: "ABC Plastik San. Tic. A.Ş." ile "ABC Plastik Sanayi
# ve Ticaret Ltd. Şti." aynı dedup anahtarına düşsün. Tek başına
# otomatik BİRLEŞTİRME tetiklemez (owner: "silent merge YOK") — yalnız
# review_required sinyali güçlendirir.
#
# NOT: bu pattern _ascii_fold() SONRASI metne uygulanır — bu yüzden
# Türkçe karaktersiz (ASCII) yazılmıştır ("şti" değil "sti", "a.ş" değil
# "a.s") — aksi halde fold edilmiş "sti" hiçbir zaman "şti" pattern'ine
# eşleşmezdi (gerçek bug, ilk yazımda bulundu ve düzeltildi).
_COMPANY_SUFFIX_PATTERN = re.compile(
    r"\b("
    r"a\.?\s?s\.?|anonim sirketi|ltd\.?\s?sti\.?|limited sirketi|"
    r"san\.?\s?(ve\s?)?tic\.?|sanayi ve ticaret|sanayi|ticaret|"
    r"tic\.?\s?(ve\s?)?san\.?|paz\.?|pazarlama|insaat|holding|grup|group|"
    r"co\.?|corp\.?|inc\.?|llc|gmbh"
    r")\b",
    flags=re.IGNORECASE,
)

_NON_ALNUM_PATTERN = re.compile(r"[^a-z0-9]+")

# unicodedata NFKD, Türkçe'ye özgü ı/İ/ş/ğ harflerini AYRIŞTIRMAZ (bunlar
# combining-mark birleşimi değil, kendi başlarına temel karakterlerdir) —
# bu yüzden önce açık bir çeviri tablosuyla katlanır, ardından (varsa)
# kalan aksanlı Latin karakterler için NFKD uygulanır.
_TR_FOLD_MAP = str.maketrans({
    "ç": "c", "Ç": "c", "ğ": "g", "Ğ": "g", "ı": "i", "I": "i", "İ": "i",
    "ö": "o", "Ö": "o", "ş": "s", "Ş": "s", "ü": "u", "Ü": "u",
})


def _ascii_fold(text: str) -> str:
    """Türkçe/aksanlı karakterleri ASCII'ye indirger — yalnız dedup anahtarı için."""
    tr_folded = text.translate(_TR_FOLD_MAP)
    decomposed = unicodedata.normalize("NFKD", tr_folded)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_name(raw_name: Optional[str]) -> Optional[str]:
    """
    Dedup sinyali #3 (öncelik sırası: domain > tax-ID > isim > telefon >
    adres). Şirket soneklerini + noktalama/boşluk farklarını atar, ASCII
    fold uygular. Yalnız KARŞILAŞTIRMA için — display değeri DEĞİL.
    """
    if not raw_name or not raw_name.strip():
        return None
    folded = _ascii_fold(raw_name).lower()
    folded = _COMPANY_SUFFIX_PATTERN.sub(" ", folded)
    collapsed = _NON_ALNUM_PATTERN.sub(" ", folded).strip()
    collapsed = re.sub(r"\s+", " ", collapsed)
    return collapsed or None


def normalize_domain(raw_website: Optional[str]) -> Optional[str]:
    """
    Dedup sinyali #1 (en güçlü). "www.Sirket.com.tr/anasayfa?x=1" gibi
    girdileri "sirket.com.tr"ye indirger — şema/www/path/query/port atılır,
    lowercase yapılır. Ham `website` alanı KAYBEDİLMEZ (ayrı sütun).
    """
    if not raw_website or not raw_website.strip():
        return None
    candidate = raw_website.strip()
    if "://" not in candidate:
        candidate = "http://" + candidate
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if not host:
        return None
    if host.startswith("www."):
        host = host[4:]
    return host or None


def normalize_phone(raw_phone: Optional[str]) -> Optional[str]:
    """
    Dedup sinyali #4. Yalnız rakamları tutar, TR ülke kodu/trunk-prefix
    farklarını (+90 / 90 / 0 önekleri) tek bir 10 haneli forma indirger.
    Ham `phone` alanı KAYBEDİLMEZ (ayrı sütun, kullanıcıya bu gösterilir).
    """
    if not raw_phone:
        return None
    digits = re.sub(r"\D", "", raw_phone)
    if not digits:
        return None
    if digits.startswith("90") and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    return digits or None


def normalize_email(raw_email: Optional[str]) -> Optional[str]:
    """Yalnız trim + lowercase — anlamsal bir dönüşüm yapılmaz."""
    if not raw_email:
        return None
    cleaned = raw_email.strip().lower()
    return cleaned or None


def email_domain(email: Optional[str]) -> Optional[str]:
    normalized = normalize_email(email)
    if not normalized or "@" not in normalized:
        return None
    return normalized.rsplit("@", 1)[-1] or None


def is_free_mail_domain(domain: Optional[str]) -> bool:
    return bool(domain) and domain.lower() in FREE_MAIL_DOMAINS
