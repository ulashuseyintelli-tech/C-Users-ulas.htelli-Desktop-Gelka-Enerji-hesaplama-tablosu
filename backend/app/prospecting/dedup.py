"""
S4 — Prospecting — dedup / identity eşleştirme.

Owner kararı: "Hiçbir field'a tek başına güvenme." Sinyal önceliği:
1) normalized_domain (en güçlü)  2) tax-ID/MERSIS (V1'de alan YOK — bkz.
aşağıdaki NOT)  3) normalized_name  4) phone  5) address.

"Silent merge YOK": bu modül HİÇBİR ZAMAN otomatik birleştirme yapmaz —
yalnız üç olası sonuçtan birini üretir: exact_duplicate (mevcut kaydı
kullan) / probable_duplicate (review_required — kullanıcı karar verir) /
distinct (yeni kayıt). Karar her zaman app/prospecting/service.py'de,
kullanıcının gördüğü bir response içinde şeffaf şekilde iletilir.

NOT (V1 kapsam sınırlaması, açıkça belgeleniyor — 004 migration residual
ile aynı dürüstlük ilkesi): owner'ın sinyal #2'si (tax-ID/MERSIS) ve
sinyal #5'i (address) bu modülde UYGULANMADI:
- tax-ID/MERSIS: ProspectCompany'de böyle bir alan yok (owner'ın DATA
  MODEL listesinde de yok) — eklenmedi, gerekirse ayrı bir iş.
- address: güvenilir bir adres-normalizasyonu/fuzzy-match olmadan yanlış
  pozitif riski yüksek (owner: "erken normalization uğruna karmaşıklık
  artırma"); domain/isim/telefon üçü zaten güçlü bir sinyal seti.

Customer dedup (pre-conversion): main.py'deki list_customers'ın
search filtresiyle (Customer.name/company/email ILIKE OR) AYNI mantık —
main.py'ye DOKUNULMADI (S1 CRM Core, packaged/user-verified/closed;
owner'ın CLAUDE.md kuralı: mevcut davranışı bozma riski taşıyan dosyalara
gereksiz dokunma). Bu YÜZDEN gerçek bir Python-seviyeli import yerine
AYNI basit filtre burada bağımsız olarak yeniden yazıldı — iki taraf da
değişirse senkronize edilmesi gerektiği bilinçli bir trade-off'tur.

Çağrıldığı yerler:
- app/prospecting/service.py (create_prospect, convert_to_customer) [S4-WB2/WB5]
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.orm import Session

from .. import database as db_models
from .normalize import normalize_domain, normalize_name, normalize_phone

VERDICT_EXACT_DUPLICATE = "exact_duplicate"
VERDICT_PROBABLE_DUPLICATE = "probable_duplicate"
VERDICT_DISTINCT = "distinct"


@dataclass
class DedupMatch:
    company_id: int
    match_signal: str  # "domain" | "name" | "phone"
    display_name: Optional[str]
    website: Optional[str]
    city: Optional[str]


@dataclass
class DedupResult:
    verdict: str
    matches: list[DedupMatch] = field(default_factory=list)


def _to_match(company: "db_models.ProspectCompany", signal: str) -> DedupMatch:
    return DedupMatch(
        company_id=company.id,
        match_signal=signal,
        display_name=company.trade_name or company.legal_name,
        website=company.website,
        city=company.city,
    )


def check_prospect_duplicate(
    db: Session,
    tenant_id: str,
    *,
    legal_name: Optional[str] = None,
    trade_name: Optional[str] = None,
    website: Optional[str] = None,
    phone: Optional[str] = None,
    exclude_id: Optional[int] = None,
) -> DedupResult:
    """
    ProspectCompany tablosuna karşı dedup — owner'ın sinyal önceliğiyle
    (domain > isim > telefon) sırayla dener, İLK GÜÇLÜ eşleşmede durur.

    exclude_id: bir kaydı GÜNCELLERKEN kendisiyle eşleşmesini önlemek
    için (V1'de update akışı yok ama fonksiyon ileride PUT /prospects/{id}
    için de güvenle kullanılabilsin diye baştan eklendi).
    """
    domain = normalize_domain(website)
    name_key = normalize_name(trade_name) or normalize_name(legal_name)
    phone_key = normalize_phone(phone)

    base_query = db.query(db_models.ProspectCompany).filter(
        db_models.ProspectCompany.tenant_id == tenant_id,
    )
    if exclude_id is not None:
        base_query = base_query.filter(db_models.ProspectCompany.id != exclude_id)

    # Sinyal #1: domain — en güçlü, tek başına EXACT_DUPLICATE kararı verdirir.
    if domain:
        domain_hits = base_query.filter(db_models.ProspectCompany.normalized_domain == domain).all()
        if domain_hits:
            return DedupResult(
                verdict=VERDICT_EXACT_DUPLICATE,
                matches=[_to_match(c, "domain") for c in domain_hits],
            )

    # Sinyal #3: isim — aynı isim ama (yukarıda domain eşleşmedi, yani)
    # farklı/eksik domain → owner kararı: BİRLEŞTİRME YOK, review_required.
    if name_key:
        name_hits = base_query.filter(db_models.ProspectCompany.normalized_name == name_key).all()
        if name_hits:
            return DedupResult(
                verdict=VERDICT_PROBABLE_DUPLICATE,
                matches=[_to_match(c, "name") for c in name_hits],
            )

    # Sinyal #4: telefon.
    if phone_key:
        phone_hits = base_query.filter(db_models.ProspectCompany.phone.isnot(None)).all()
        matched = [c for c in phone_hits if normalize_phone(c.phone) == phone_key]
        if matched:
            return DedupResult(
                verdict=VERDICT_PROBABLE_DUPLICATE,
                matches=[_to_match(c, "phone") for c in matched],
            )

    return DedupResult(verdict=VERDICT_DISTINCT, matches=[])


@dataclass
class CustomerMatch:
    customer_id: int
    name: str
    company: Optional[str]
    email: Optional[str]


def find_matching_customers(
    db: Session,
    *,
    name: Optional[str] = None,
    company: Optional[str] = None,
    email: Optional[str] = None,
    limit: int = 10,
) -> list[CustomerMatch]:
    """
    Prospect → Customer dönüştürme ÖNCESİ mevcut Customer tablosuna karşı
    dedup kontrolü (owner: "Before Customer conversion, ALSO dedupe
    against existing Customer table"). main.py list_customers'ın search
    OR-filtresiyle aynı mantık — bkz. modül docstring'i (main.py'ye
    dokunulmadı, bağımsız yazıldı).
    """
    terms = [t for t in (name, company, email) if t and t.strip()]
    if not terms:
        return []

    query = db.query(db_models.Customer)
    conditions = []
    for term in terms:
        pattern = f"%{term.strip()}%"
        conditions.append(db_models.Customer.name.ilike(pattern))
        conditions.append(db_models.Customer.company.ilike(pattern))
        conditions.append(db_models.Customer.email.ilike(pattern))

    from sqlalchemy import or_
    query = query.filter(or_(*conditions)).limit(limit)

    return [
        CustomerMatch(customer_id=c.id, name=c.name, company=c.company, email=c.email)
        for c in query.all()
    ]
