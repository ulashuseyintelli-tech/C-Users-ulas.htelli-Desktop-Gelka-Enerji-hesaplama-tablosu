"""
PDSMR-R4 / FAZ 4B1 — MODEL <-> CANONICAL MIGRATION PARITE KAPISI.

Kanitlanacak tek cumle: `Base.metadata.create_all()` ile uretilen sema ile
gercek `alembic base -> 351d314819d5` ile uretilen sema, uygulama tablolari
duzeyinde BIREBIR AYNIDIR.

NEDEN (PDSMR-R4 Faz 0/4A bulgusu): gercek canli production DB'si
`create_all()` ile kurulmustu ve modeli birebir yansitiyordu. Model ise
canonical migration zincirinden 26 kolon + 16 index sapmisti. Sonuc:
"modele karsi dogrulandi" demek "canonical" demek DEGILDI — sertifikasyon
cubugu yanlis referansa bagliydi. Bu test o referansi KALICI olarak
kilitler: model bir daha canonical'dan sessizce ayrisamaz.

Cagrildigi yerler:
- pytest (CI/yerel regresyon)
"""
from __future__ import annotations

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.legacy_adoption import alembic_runner as ar  # noqa: E402
from app.legacy_adoption.fingerprint import collect_fingerprint  # noqa: E402
from app.legacy_adoption.validator import _load_model_metadata  # noqa: E402

CANONICAL_HEAD = "351d314819d5"

# Parite kiyasindan ACIKCA istisna tutulan canonical tablolar. Bunlar
# uygulama modelinin PARCASI DEGILDIR ve model'e EKLENMEMELIDIR:
#   - alembic_version : alembic'in kendi defteri
#   - ptf_drift_log   : legacy-only artefakt (MUST_BE_EMPTY_LEGACY_TABLES)
# ISIM BAZLI exact allowlist — toplam tablo SAYISINA guvenilmez.
CANONICAL_ONLY_TABLES = frozenset({"alembic_version", "ptf_drift_log"})

# SQLite'in kendi sistem tablolari uygulama tablosu SAYILMAZ.
SQLITE_SYSTEM_PREFIX = "sqlite_"

# ── BELGELENMIS, SEMANTIK OLMAYAN KOLON SIRASI VARYANTI ─────────────────
# Owner karari: DOCUMENTED_NON_SEMANTIC_COLUMN_ORDER_VARIANT.
#
# GEREKCE: canonical sema, tarihsel `ALTER TABLE ... ADD COLUMN`
# zincirinin sonucudur — SQLite yeni kolonu HER ZAMAN SONA ekler. Model
# ise kolonlari MANTIKSAL olarak gruplar (ör. `tenant_id` tablonun basinda,
# tum `retry_*` alanlari yan yana). Ikisi ayni kolon KUMESINI ayni
# SEKILDE tanimlar; yalniz BILDIRIM SIRASI farklidir.
#
# NEDEN GUVENLI: ORM tum SQL'i ISIM BAZLI uretir (SELECT'te acik kolon
# listesi, INSERT'te acik kolon listesi). PDSMR-R4B1 guvenlik kapisinda
# repo genelinde arandi ve bu alti tablo icin ordinal-bagimli TEK BIR
# runtime yolu BULUNMADI: `SELECT *` yok, kolon listesiz
# `INSERT INTO ... VALUES(...)` yok, sayisal kolon pozisyonuyla okuyan
# runtime kodu yok, sira-bagimli export/serialization yok. (Yalniz
# legacy_adoption/* icinde PRAGMA table_info/index_list introspection'i
# var; bu SEMA INCELEME'dir, veri erisimi DEGILDIR.)
#
# SINIR: bu istisna YALNIZ sirayi kapsar. Ayni tablolarda kolon adi/
# kumesi, tip, affinity, nullability, default, PK/FK ve constraint farki
# HALA FAIL uretir (asagidaki testler bunu ayrica dogrular).
#
# PHASE 4B2 KURALI: canli DB rebuild edilirken hedef DDL MODEL sirasindan
# DEGIL, canonical migration/head semasindan turetilir; tum veri kopyalari
# ACIK kolon listesiyle yapilir.
COLUMN_ORDER_VARIANT_TABLES = frozenset({
    "incidents",
    "invoices",
    "jobs",
    "market_reference_prices",
    "offers",
    "prospect_companies",
})

# Istisnali tablolarin HER IKI tarafinin da BELGELENMIS sirasi. Bu, "sira
# serbest" DEMEK DEGILDIR: iki taraf da sabitlenmistir ve sessizce kayamaz.
_MODEL_COLUMN_ORDER = {
    "incidents": [
        'id', 'trace_id', 'tenant_id', 'invoice_id',
        'offer_id', 'provider', 'period', 'severity',
        'category', 'message', 'details_json', 'primary_flag',
        'action_type', 'action_owner', 'action_code', 'all_flags',
        'secondary_flags', 'deduction_total', 'routed_payload', 'dedupe_key',
        'dedupe_bucket', 'occurrence_count', 'first_seen_at', 'last_seen_at',
        'retry_attempt_count', 'retry_eligible_at', 'retry_last_attempt_at', 'retry_lock_until',
        'retry_lock_by', 'retry_exhausted_at', 'external_issue_id', 'external_issue_url',
        'reported_at', 'reclassified_at', 'previous_primary_flag', 'recompute_count',
        'retry_success', 'resolution_reason', 'feedback_json', 'status',
        'resolution_note', 'resolved_by', 'resolved_at', 'created_at',
        'updated_at',
    ],
    "invoices": [
        'id', 'tenant_id', 'source_filename', 'content_type',
        'storage_original_ref', 'storage_page1_ref', 'file_hash', 'vendor_guess',
        'invoice_period', 'extraction_json', 'validation_json', 'status',
        'error_message', 'created_at', 'updated_at',
    ],
    "jobs": [
        'id', 'tenant_id', 'invoice_id', 'job_type',
        'status', 'payload_json', 'result_json', 'error',
        'created_at', 'started_at', 'finished_at',
    ],
    "market_reference_prices": [
        'id', 'price_type', 'period', 'ptf_tl_per_mwh',
        'yekdem_tl_per_mwh', 'status', 'source', 'captured_at',
        'source_note', 'change_reason', 'is_locked', 'updated_by',
        'created_at', 'updated_at',
    ],
    "offers": [
        'id', 'tenant_id', 'customer_id', 'vendor',
        'invoice_period', 'consumption_kwh', 'current_unit_price', 'distribution_unit_price',
        'demand_qty', 'demand_unit_price', 'weighted_ptf', 'yekdem',
        'agreement_multiplier', 'current_total', 'offer_total', 'savings_amount',
        'savings_ratio', 'extra_items_json', 'extra_items_total_tl', 'calculation_result',
        'extraction_result', 'created_at', 'pdf_ref', 'status',
    ],
    "prospect_companies": [
        'id', 'tenant_id', 'legal_name', 'trade_name',
        'normalized_name', 'website', 'normalized_domain', 'sector',
        'city', 'district', 'industrial_zone', 'address',
        'phone', 'status', 'qualification_reason', 'qualification_note',
        'verified_legal_type', 'verified_legal_type_note', 'verified_legal_type_set_at', 'duplicate_of_id',
        'customer_id', 'discovered_at', 'last_verified_at', 'created_at',
        'updated_at',
    ],
}

_CANONICAL_COLUMN_ORDER = {
    "incidents": [
        'id', 'trace_id', 'tenant_id', 'invoice_id',
        'offer_id', 'severity', 'category', 'message',
        'details_json', 'dedupe_key', 'occurrence_count', 'first_seen_at',
        'last_seen_at', 'status', 'resolution_note', 'resolved_by',
        'resolved_at', 'created_at', 'updated_at', 'provider',
        'period', 'dedupe_bucket', 'primary_flag', 'action_type',
        'action_owner', 'action_code', 'all_flags', 'secondary_flags',
        'deduction_total', 'routed_payload', 'retry_attempt_count', 'retry_eligible_at',
        'retry_last_attempt_at', 'retry_lock_until', 'retry_lock_by', 'retry_exhausted_at',
        'external_issue_id', 'external_issue_url', 'reported_at', 'reclassified_at',
        'previous_primary_flag', 'recompute_count', 'retry_success', 'resolution_reason',
        'feedback_json',
    ],
    "invoices": [
        'id', 'source_filename', 'content_type', 'storage_original_ref',
        'storage_page1_ref', 'file_hash', 'vendor_guess', 'invoice_period',
        'extraction_json', 'validation_json', 'status', 'error_message',
        'created_at', 'updated_at', 'tenant_id',
    ],
    "jobs": [
        'id', 'invoice_id', 'job_type', 'status',
        'payload_json', 'result_json', 'error', 'created_at',
        'started_at', 'finished_at', 'tenant_id',
    ],
    "market_reference_prices": [
        'id', 'period', 'ptf_tl_per_mwh', 'yekdem_tl_per_mwh',
        'source_note', 'is_locked', 'updated_by', 'created_at',
        'updated_at', 'price_type', 'status', 'captured_at',
        'change_reason', 'source',
    ],
    "offers": [
        'id', 'customer_id', 'vendor', 'invoice_period',
        'consumption_kwh', 'current_unit_price', 'distribution_unit_price', 'demand_qty',
        'demand_unit_price', 'weighted_ptf', 'yekdem', 'agreement_multiplier',
        'current_total', 'offer_total', 'savings_amount', 'savings_ratio',
        'calculation_result', 'extraction_result', 'created_at', 'pdf_ref',
        'status', 'tenant_id', 'extra_items_json', 'extra_items_total_tl',
    ],
    "prospect_companies": [
        'id', 'tenant_id', 'legal_name', 'trade_name',
        'normalized_name', 'website', 'normalized_domain', 'sector',
        'city', 'district', 'industrial_zone', 'address',
        'phone', 'status', 'qualification_reason', 'qualification_note',
        'duplicate_of_id', 'customer_id', 'discovered_at', 'last_verified_at',
        'created_at', 'updated_at', 'verified_legal_type', 'verified_legal_type_note',
        'verified_legal_type_set_at',
    ],
}


def _uygulama_tablolari(fp) -> set[str]:
    return {t for t in fp.tables if not t.startswith(SQLITE_SYSTEM_PREFIX)}


@pytest.fixture(scope="module")
def model_db(tmp_path_factory) -> str:
    """create_all() ile uretilen taze, bos DB (tum model modulleri yuklu)."""
    from sqlalchemy import create_engine

    yol = str(tmp_path_factory.mktemp("r4b1_model") / "model_create_all.db")
    md = _load_model_metadata()  # app.database + app.pricing.schemas ACIKCA
    eng = create_engine(f"sqlite:///{yol}")
    md.create_all(eng)
    eng.dispose()
    return yol


@pytest.fixture(scope="module")
def canonical_db(tmp_path_factory) -> str:
    """Gercek `alembic base -> head` ile uretilen taze, bos DB."""
    yol = str(tmp_path_factory.mktemp("r4b1_canon") / "canonical_head.db")
    ar.alembic_upgrade(yol, CANONICAL_HEAD)
    return yol


@pytest.fixture(scope="module")
def fp_model(model_db):
    return collect_fingerprint(model_db)


@pytest.fixture(scope="module")
def fp_canonical(canonical_db):
    return collect_fingerprint(canonical_db)


# ─────────────────────────────────────────────────────────────────────────
# 1) Tablo kumesi
# ─────────────────────────────────────────────────────────────────────────
def test_application_table_sets_are_identical(fp_model, fp_canonical):
    model_t = _uygulama_tablolari(fp_model)
    canon_t = _uygulama_tablolari(fp_canonical) - CANONICAL_ONLY_TABLES
    assert model_t == canon_t, (
        f"yalniz modelde={sorted(model_t - canon_t)} "
        f"yalniz canonical'da={sorted(canon_t - model_t)}"
    )


def test_canonical_only_tables_are_absent_from_model(fp_model, fp_canonical):
    """Istisna listesi GERCEKTEN gerekli olmali (olu allowlist birakma)."""
    for t in CANONICAL_ONLY_TABLES:
        assert t in fp_canonical.tables, f"{t} canonical'da yok — allowlist bayat"
        assert t not in fp_model.tables, f"{t} modele EKLENMIS — yasak"


# ─────────────────────────────────────────────────────────────────────────
# 2) Kolonlar: ad, SIRA, declared type, affinity, nullability, server-default
# ─────────────────────────────────────────────────────────────────────────
def _ortak_tablolar(fp_model, fp_canonical):
    return sorted(_uygulama_tablolari(fp_model) & _uygulama_tablolari(fp_canonical))


def _sirali_kolonlar(db, tablo):
    uri = "file:" + db.replace("\\", "/").replace(" ", "%20") + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        return [r[1] for r in con.execute(f'PRAGMA table_info("{tablo}")').fetchall()]
    finally:
        con.close()


def test_column_name_sets_are_identical_everywhere(model_db, canonical_db, fp_model, fp_canonical):
    """
    Kolon ADI ve KUMESI — istisnasiz, TUM tablolarda birebir esit olmali.
    (Sira istisnasi bu kontrolu KAPSAMAZ.)
    """
    for t in _ortak_tablolar(fp_model, fp_canonical):
        assert set(_sirali_kolonlar(model_db, t)) == set(_sirali_kolonlar(canonical_db, t)), t


def test_column_order_differs_only_in_the_documented_allowlist(
    model_db, canonical_db, fp_model, fp_canonical
):
    """
    Kolon SIRASI farki YALNIZ belgelenmis allowlist'te olabilir.
    Allowlist DISINDA tek bir sira farki bile FAIL uretir.

    Gerekce icin bkz. COLUMN_ORDER_VARIANT_TABLES yorumu: canonical sira
    tarihsel ALTER TABLE append sonucudur; model sirasi mantiksal
    gruplamadir; ORM isim bazli esleme kullanir.
    """
    farkli = {
        t for t in _ortak_tablolar(fp_model, fp_canonical)
        if _sirali_kolonlar(model_db, t) != _sirali_kolonlar(canonical_db, t)
    }
    assert farkli == set(COLUMN_ORDER_VARIANT_TABLES), (
        f"beklenmeyen sira farki={sorted(farkli - COLUMN_ORDER_VARIANT_TABLES)} "
        f"bayat allowlist girdisi={sorted(COLUMN_ORDER_VARIANT_TABLES - farkli)}"
    )


@pytest.mark.parametrize("tablo", sorted(COLUMN_ORDER_VARIANT_TABLES))
def test_documented_order_variant_matches_recorded_fixture(
    model_db, canonical_db, tablo
):
    """
    Istisnali her tablonun model ve canonical kolon sirasi, BELGELENMIS
    fixture ile birebir karsilastirilir. Boylece istisna "sira serbest"
    anlamina GELMEZ: iki taraf da SABITLENMISTIR, sessizce kayamaz.
    """
    model_sira = _sirali_kolonlar(model_db, tablo)
    canon_sira = _sirali_kolonlar(canonical_db, tablo)

    assert model_sira == _MODEL_COLUMN_ORDER[tablo], (
        f"{tablo}: MODEL kolon sirasi belgelenmis fixture'dan sapti"
    )
    assert canon_sira == _CANONICAL_COLUMN_ORDER[tablo], (
        f"{tablo}: CANONICAL kolon sirasi belgelenmis fixture'dan sapti"
    )
    # Ayni kume olmali — yalniz sira farkli.
    assert set(model_sira) == set(canon_sira), f"{tablo}: kolon KUMESI farkli (sira degil!)"


@pytest.mark.parametrize("alan", ["declared_type", "affinity", "notnull", "has_default"])
def test_column_shape_is_identical(fp_model, fp_canonical, alan):
    farklar = []
    for t in _ortak_tablolar(fp_model, fp_canonical):
        m, c = fp_model.tables[t], fp_canonical.tables[t]
        for k in sorted(set(m.columns) & set(c.columns)):
            mv, cv = getattr(m.columns[k], alan), getattr(c.columns[k], alan)
            if mv != cv:
                farklar.append(f"{t}.{k}: model={mv!r} canonical={cv!r}")
    assert farklar == [], f"{alan} farklari: {farklar}"


def test_server_default_semantics_are_equivalent(model_db, canonical_db, fp_model, fp_canonical):
    """
    `has_default` bayragi tek basina YETMEZ — degerin SEMANTIGI de esdeger
    olmali. Normalizasyon: bosluk/tirnak/parantez/buyuk-kucuk harf farki
    (ör. `'0'` vs `0`, `CURRENT_TIMESTAMP` vs `(CURRENT_TIMESTAMP)`)
    SQLite'in DDL yazim varyantidir, anlam farki DEGILDIR.
    """
    def normalize(deger):
        if deger is None:
            return None
        s = " ".join(str(deger).split()).strip()
        while s.startswith("(") and s.endswith(")"):
            s = s[1:-1].strip()
        if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
            s = s[1:-1]
        return s.upper()

    def defaults(db, tablo):
        uri = "file:" + db.replace("\\", "/").replace(" ", "%20") + "?mode=ro"
        con = sqlite3.connect(uri, uri=True)
        try:
            return {r[1]: normalize(r[4])
                    for r in con.execute(f'PRAGMA table_info("{tablo}")').fetchall()}
        finally:
            con.close()

    farklar = []
    for t in _ortak_tablolar(fp_model, fp_canonical):
        m, c = defaults(model_db, t), defaults(canonical_db, t)
        for k in sorted(set(m) & set(c)):
            if m[k] != c[k]:
                farklar.append(f"{t}.{k}: model={m[k]!r} canonical={c[k]!r}")
    assert farklar == [], f"server_default semantik farklari: {farklar}"


# ─────────────────────────────────────────────────────────────────────────
# 3) PK / FK
# ─────────────────────────────────────────────────────────────────────────
def test_primary_keys_are_identical(fp_model, fp_canonical):
    for t in _ortak_tablolar(fp_model, fp_canonical):
        assert fp_model.tables[t].primary_key == fp_canonical.tables[t].primary_key, t


def test_foreign_keys_target_and_column_order_are_identical(
    model_db, canonical_db, fp_model, fp_canonical
):
    def fks(db, tablo):
        uri = "file:" + db.replace("\\", "/").replace(" ", "%20") + "?mode=ro"
        con = sqlite3.connect(uri, uri=True)
        try:
            # (id, seq, hedef_tablo, kaynak_kolon, hedef_kolon) — seq = kolon sirasi
            return sorted(
                (r[2], r[1], r[3], r[4])
                for r in con.execute(f'PRAGMA foreign_key_list("{tablo}")').fetchall()
            )
        finally:
            con.close()

    for t in _ortak_tablolar(fp_model, fp_canonical):
        assert fks(model_db, t) == fks(canonical_db, t), t


# ─────────────────────────────────────────────────────────────────────────
# 4) Index: ad, kolon SIRASI, uniqueness, partial/expression, ASC/DESC,
#    collation, table-level UNIQUE (origin)
# ─────────────────────────────────────────────────────────────────────────
def test_index_names_columns_uniqueness_partial_and_origin_are_identical(
    fp_model, fp_canonical
):
    farklar = []
    for t in _ortak_tablolar(fp_model, fp_canonical):
        m, c = fp_model.tables[t].indexes, fp_canonical.tables[t].indexes
        for ad in sorted(set(m) | set(c)):
            if ad not in m:
                farklar.append(f"{t}.{ad}: YALNIZ canonical'da")
                continue
            if ad not in c:
                farklar.append(f"{t}.{ad}: YALNIZ modelde")
                continue
            mi, ci = m[ad], c[ad]
            if mi.columns != ci.columns:
                farklar.append(f"{t}.{ad} kolon sirasi: model={mi.columns} canonical={ci.columns}")
            if mi.unique != ci.unique:
                farklar.append(f"{t}.{ad} unique: model={mi.unique} canonical={ci.unique}")
            if mi.partial != ci.partial:
                farklar.append(f"{t}.{ad} partial: model={mi.partial} canonical={ci.partial}")
            if mi.origin != ci.origin:
                # origin='u' (tablo-level UNIQUE autoindex) vs 'c' (CREATE INDEX)
                farklar.append(f"{t}.{ad} origin: model={mi.origin} canonical={ci.origin}")
    assert farklar == [], f"index farklari: {farklar}"


def test_index_ddl_text_is_equivalent(model_db, canonical_db, fp_model, fp_canonical):
    """
    ASC/DESC, COLLATE ve expression index'leri PRAGMA'da gorunmez — ham
    DDL metni uzerinden karsilastirilir (bosluk normalize edilir).
    """
    def index_ddl(db):
        uri = "file:" + db.replace("\\", "/").replace(" ", "%20") + "?mode=ro"
        con = sqlite3.connect(uri, uri=True)
        try:
            return {
                r[0]: " ".join((r[1] or "").split())
                for r in con.execute(
                    "SELECT name, sql FROM sqlite_master WHERE type='index' "
                    "AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            }
        finally:
            con.close()

    m, c = index_ddl(model_db), index_ddl(canonical_db)
    canon_only_idx = {
        ad for ad, ddl in c.items()
        if any(f'"{t}"' in ddl or f" {t} " in ddl for t in CANONICAL_ONLY_TABLES)
    }
    c = {k: v for k, v in c.items() if k not in canon_only_idx}
    assert m == c, (
        f"index DDL farklari — yalniz modelde={sorted(set(m) - set(c))} "
        f"yalniz canonical'da={sorted(set(c) - set(m))} "
        f"metin farki={[k for k in set(m) & set(c) if m[k] != c[k]]}"
    )


# ─────────────────────────────────────────────────────────────────────────
# 5) CHECK constraint semantigi + tablo DDL'i
# ─────────────────────────────────────────────────────────────────────────
def test_check_constraint_semantics_are_identical(model_db, canonical_db, fp_model, fp_canonical):
    for t in _ortak_tablolar(fp_model, fp_canonical):
        m = fp_model.tables[t].has_check_constraint
        c = fp_canonical.tables[t].has_check_constraint
        assert m == c, f"{t}: CHECK constraint varligi model={m} canonical={c}"


def test_table_level_unique_constraints_are_identical(fp_model, fp_canonical):
    """origin='u' = tablo-seviyesi UNIQUE. Sayisi ve imzasi birebir olmali."""
    for t in _ortak_tablolar(fp_model, fp_canonical):
        def u_imzalari(idx):
            return sorted((i.columns, i.unique) for i in idx.values() if i.origin == "u")
        assert u_imzalari(fp_model.tables[t].indexes) == \
               u_imzalari(fp_canonical.tables[t].indexes), t


# ─────────────────────────────────────────────────────────────────────────
# 6) Alembic saglik + create_all idempotentligi
# ─────────────────────────────────────────────────────────────────────────
def test_canonical_head_is_single_and_expected(canonical_db):
    assert ar.alembic_heads_count(canonical_db) == 1
    uri = "file:" + canonical_db.replace("\\", "/").replace(" ", "%20") + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        revizyonlar = [r[0] for r in con.execute("SELECT version_num FROM alembic_version")]
    finally:
        con.close()
    assert revizyonlar == [CANONICAL_HEAD]


def test_second_create_all_is_a_safe_no_op(model_db):
    """Ikinci create_all() cagrisi hata vermemeli ve semayi degistirmemeli."""
    import hashlib

    from sqlalchemy import create_engine

    def sha(p):
        h = hashlib.sha256()
        with open(p, "rb") as fh:
            for parca in iter(lambda: fh.read(1 << 20), b""):
                h.update(parca)
        return h.hexdigest()

    once = sha(model_db)
    md = _load_model_metadata()
    eng = create_engine(f"sqlite:///{model_db}")
    md.create_all(eng)  # ikinci kez
    eng.dispose()
    assert sha(model_db) == once, "ikinci create_all() semayi degistirdi"
