"""
PDSMR-R1D — legacy adoption ALLOWLIST politikasi (acik ve gozden gecirilebilir).

Bu dosya, hangi veritabani ailesinin adoption on-kosullarini sagladigini
tanimlayan TEK yerdir. Owner kurali: bu arac genel amacli bir SQLite
adopter DEGILDIR; yalnizca burada ADI ADINA sayilan production ailesini
tanir. Tanimadigi her sey HARD_STOP'tur (fail-closed).

Buradaki her sabit ya PDSMR-R1D Faz 1'de gercek production DB'den
salt-okunur olarak OLCULMUS kanittir, ya da owner'in acikca kabul ettigi
bir varyanttir. Hicbiri tahmin degildir.

Cagrildigi yerler:
- app/legacy_adoption/validator.py [PDSMR-R1D/Faz2]
- tests/test_legacy_adoption_validator.py
"""
from __future__ import annotations

# ── Kimlik ───────────────────────────────────────────────────────────────
ALLOWED_ALEMBIC_REVISION = "013_extend_ptf_drift_severity"
EXPECTED_TABLE_COUNT = 31

# ── Kritik row-count parity (Faz 1 fingerprint) ──────────────────────────
EXPECTED_ROW_COUNTS = {
    "customers": 3,
    "offers": 6,
    "contracts": 3,
    "activities": 7,
    "tasks": 7,
    "prospect_companies": 1,
    "prospect_contacts": 0,
    "prospect_sources": 3,
    "incidents": 1,
    "invoices": 0,
    "ptf_drift_log": 0,
    "market_reference_prices": 60,
}

# ── Legacy artifact: korunur, silinmez, aktif modele alinmaz ─────────────
REQUIRED_LEGACY_TABLE = "ptf_drift_log"
REQUIRED_LEGACY_TABLE_ROW_COUNT = 0

# ── Modelde OLMAYAN ama production'da BULUNAN tablolar ───────────────────
# Bu listede olmayan her model-disi tablo UNKNOWN_TABLE_PRESENT -> HARD_STOP.
#
# UYARI (kalibrasyon notu): app/pricing/schemas.py'deki 7 tablo
# (analysis_cache, consumption_hourly_data, consumption_profiles,
# data_versions, hourly_market_prices, monthly_yekdem_prices,
# profile_templates) ilk bakista "legacy" gorunur, ANCAK modelin gercek
# parcasidir; yalnizca app.database import edildiginde metadata'ya
# kaydolmadiklari icin oyle sanilir. Buraya EKLENMEZLER — aksi halde
# kolon/PK/FK dogrulamasindan muaf tutulurlar. Bkz.
# validator._load_model_metadata().
KNOWN_LEGACY_ONLY_TABLES = frozenset({
    "alembic_version",          # alembic'in kendi tablosu (modelde olmaz)
    "ptf_drift_log",            # ayrica REQUIRED_LEGACY_TABLE
})

# ── Modelde OLAN ama pre-adoption DB'de OLMAMASI GEREKEN tablolar ────────
# S5/Outreach tablolari. Adoption ONCESI bunlarin VARLIGI, DB'nin daha once
# create_all() ile yarim semaya ugradiginin isaretidir -> HARD_STOP.
EXPECTED_ABSENT_MODEL_TABLES = frozenset({
    "outreach_messages",
    "outreach_templates",
    "suppression_entries",
})

# ── Modelde OLAN ama pre-adoption DB'de OLMAMASI BEKLENEN kolonlar ───────
# S5'te eklenen human-verified legal type alanlari. Faz 1'de olculdu:
# production'da bu ucu de YOK — dogru ve beklenen durum.
EXPECTED_ABSENT_MODEL_COLUMNS = frozenset({
    ("prospect_companies", "verified_legal_type"),
    ("prospect_companies", "verified_legal_type_note"),
    ("prospect_companies", "verified_legal_type_set_at"),
})

# ── SQLAlchemy<->SQLite tip esdegerlikleri (tip sistemi kurali) ──────────
# Bunlar "kolon bazli istisna" DEGIL, SQLAlchemy'nin SQLite dialect'inin
# evrensel davranisidir: JSON degerleri TEXT olarak serilestirilir,
# Boolean 0/1 INTEGER olarak saklanir, DATETIME/TIMESTAMP ayni NUMERIC
# affinity'ye duser. Bu yuzden ilgili farklar semantik drift sayilmaz.
# Anahtar: modelin DERLENMIS DDL tipi -> DB'de kabul edilen affinity'ler.
TYPE_EQUIVALENCES = {
    "JSON": frozenset({"TEXT", "NUMERIC", "BLOB"}),
    "BOOLEAN": frozenset({"INTEGER", "NUMERIC"}),
    "DATETIME": frozenset({"NUMERIC", "TEXT"}),
    "TIMESTAMP": frozenset({"NUMERIC", "TEXT"}),
    "DATE": frozenset({"NUMERIC", "TEXT"}),
    "TIME": frozenset({"NUMERIC", "TEXT"}),
    "NUMERIC": frozenset({"NUMERIC", "REAL"}),
}

# ── Owner'in kabul ettigi affinity varyantlari (R1-B karari) ────────────
# SQLite VARCHAR(n)'deki n'i ZORLAMAZ ve iki tarafta da CHECK constraint
# yoktur; bu dortlu ayni TEXT affinity'sine duser -> semantik fark YOK.
# Validator zaten affinity duzeyinde karsilastirdigi icin bu farklar
# dogal olarak gorunmez; liste kanit/izlenebilirlik icin tutulur.
ACCEPTED_AFFINITY_VARIANTS = frozenset({
    ("audit_logs", "action"),
    ("invoices", "status"),
    ("jobs", "job_type"),
    ("jobs", "status"),
})

# ── Owner'in kabul ettigi nullability varyantlari (R1-C karari) ─────────
# Production'da 11 alanin HEPSINDE NULL sayisi 0'di; schema rebuild
# NOT JUSTIFIED. Bunlar "bilinmeyen drift" DEGIL, adlandirilmis varyanttir.
ACCEPTED_NULLABILITY_VARIANTS = {
    # migration daha siki (NOT NULL) — LEGACY_COMPATIBILITY_REQUIRED
    ("customers", "created_at"): "LEGACY_COMPATIBILITY_REQUIRED",
    ("customers", "updated_at"): "LEGACY_COMPATIBILITY_REQUIRED",
    ("invoices", "created_at"): "LEGACY_COMPATIBILITY_REQUIRED",
    ("invoices", "status"): "LEGACY_COMPATIBILITY_REQUIRED",
    ("invoices", "updated_at"): "LEGACY_COMPATIBILITY_REQUIRED",
    ("jobs", "created_at"): "LEGACY_COMPATIBILITY_REQUIRED",
    ("jobs", "invoice_id"): "LEGACY_COMPATIBILITY_REQUIRED",
    ("jobs", "status"): "LEGACY_COMPATIBILITY_REQUIRED",
    ("offers", "created_at"): "LEGACY_COMPATIBILITY_REQUIRED",
    ("offers", "status"): "LEGACY_COMPATIBILITY_REQUIRED",
    # model daha siki — MODEL_RUNTIME_INVARIANT
    ("market_reference_prices", "captured_at"): "MODEL_RUNTIME_INVARIANT",
}

# ── CHECK constraint tasidigi BILINEN tablolar (Faz 1'de olculdu) ───────
# Bu listede olmayan bir tabloda CHECK constraint gorulmesi, semanin
# beklenmeyen bir yoldan uretildigi anlamina gelir -> HARD_STOP.
KNOWN_CHECK_CONSTRAINT_TABLES = frozenset({
    "prospect_sources",
    "ptf_drift_log",
})

# ── Index politikasi ────────────────────────────────────────────────────
# UNIQUE index'ler VERI BUTUNLUGU tasir: modeldeki bir unique kisit DB'de
# yoksa cift kayit girebilir; DB'de modelde olmayan bir unique kisit varsa
# adoption sonrasi INSERT'ler beklenmedik sekilde reddedilir. Ikisi de
# HARD_STOP'tur.
#
# NON-UNIQUE index'ler yalnizca PERFORMANS tasir; eksikligi/fazlaligi veri
# guvenligi riski degildir. Bunlar kanita (evidence) yazilir, HARD_STOP
# uretmez. Bu, bilincli ve adlandirilmis bir politika kararidir — gizli bir
# gevsetme degil; degeri False yapmak kurali sikilastirir.
ENFORCE_NON_UNIQUE_INDEX_PARITY = False

# ── Yedekleme on-kosulu ─────────────────────────────────────────────────
# Adoption'dan once DB'nin yedegi alinabilmelidir. Serbest alan, DB
# boyutunun bu kati kadar olmalidir (yedek + calisma payi).
BACKUP_FREE_SPACE_FACTOR = 3

# ── Kanit sanitizasyonu ─────────────────────────────────────────────────
# Rapor/kanit ciktisi sema ve sayim disinda hicbir sey icermemelidir.
FORBIDDEN_EVIDENCE_SUBSTRINGS = (
    "password", "passwd", "secret", "api_key", "apikey", "token",
    "smtp_password", "outreach_smtp_password", "openai",
)
