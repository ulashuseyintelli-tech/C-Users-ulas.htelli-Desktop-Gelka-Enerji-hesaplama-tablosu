# Design — PTF Single Source of Truth Unification

## 1. Genel mimari

```
Pre-migration (şu anki durum):
┌─────────────┐         ┌─────────────────────┐
│ admin panel │────────>│ market_reference_   │
└─────────────┘ write   │ prices (legacy)     │
                        └─────────────────────┘
                                 ▲
┌─────────────┐                 │ read
│ /full-process│─────────────────┤
│ /calculate  │                 │
└─────────────┘                 │
                                 │
┌─────────────┐         ┌─────────────────────┐
│ pricing_    │<───────>│ hourly_market_      │
│ router      │ rw      │ prices (canonical)  │
└─────────────┘         └─────────────────────┘
                                 ▲
┌─────────────┐                 │ read/mirror
│ yekdem_svc  │─────────────────┤ (both tables!)
└─────────────┘                 │
```

```
Post-migration (Phase 4 DoD):
┌─────────────┐         ┌─────────────────────┐
│ admin panel │────────>│ hourly_market_      │
└─────────────┘ write   │ prices (canonical)  │
                        │                     │
┌─────────────┐         │                     │
│ /full-process│────────>│                     │
│ /calculate  │  read   │                     │
└─────────────┘         │                     │
                        │                     │
┌─────────────┐         │                     │
│ pricing_    │<───────>│                     │
│ router      │  rw     │                     │
└─────────────┘         └─────────────────────┘
                                 ▲
┌─────────────┐                 │
│ yekdem_svc  │─────────────────┘ (only monthly_yekdem_prices, dual-read removed)
└─────────────┘

❌ market_reference_prices DROPPED (alembic 013)
```

## 2. Kill switch mekanizması

### 2.1 Config flag

```python
# backend/app/guard_config.py — mevcut pattern ile uyumlu

class GuardConfig(BaseSettings):
    # ... mevcut alanlar ...
    
    # ── PTF SoT Unification ──────────────────────────────────
    use_legacy_ptf: bool = False
    """Kill switch: True ise PTF okumaları legacy tabloya yönlendirilir.
    
    Normal çalışma: False (canonical = hourly_market_prices).
    Rollback: True set et → bir sonraki request'te etkili.
    
    UYARI: Bu flag yalnızca Phase 2-3 penceresinde anlamlıdır.
    Phase 4 sonrası (legacy tablo silindi) flag kaldırılmalı.
    """
    
    ptf_drift_log_enabled: bool = True
    """Dual-read penceresinde drift log yazılsın mı.
    
    Phase 1: False (henüz dual-read yok)
    Phase 2: True (dual-read aktif)
    Phase 3+: False (kaldırıldı)
    """
```

### 2.2 Kullanım

```python
# backend/app/pricing/router.py::_load_market_records içinde

from ..guard_config import get_guard_config

def _load_market_records(db: Session, period: str):
    config = get_guard_config()
    
    if config.use_legacy_ptf:
        # Kill switch aktif — legacy davranışa dön
        logger.warning(
            "ptf_legacy_fallback_active period=%s source=market_reference_prices",
            period,
        )
        get_ptf_metrics().inc_legacy_fallback(period=period)
        return _load_from_legacy_table(db, period)
    
    # Normal canonical yol
    return _load_from_canonical(db, period)
```

### 2.3 Rollback süresi garantisi

FastAPI/uvicorn worker'ları config'i her istek başında okur (`get_guard_config()` — `lru_cache` YOK). Env değişikliği → bir sonraki request = 10 saniye altı.

Eğer `lru_cache` varsa kaldırılmalı veya TTL eklenmeli. Bu değişiklik `guard_config` spec'inin şu anki davranışı ile uyumsuz olabilir; **Phase 1 başlangıcında doğrula.**

## 3. Drift log şeması

### 3.1 Tablo: `ptf_drift_log`

```python
# backend/app/ptf_drift_log.py

class PtfDriftLog(Base):
    __tablename__ = "ptf_drift_log"
    
    id = Column(Integer, primary_key=True)
    period = Column(String(7), nullable=False, index=True)  # YYYY-MM
    canonical_value_tl_per_mwh = Column(Float, nullable=False)
    legacy_value_tl_per_mwh = Column(Float, nullable=True)  # legacy'de yoksa NULL
    diff_abs = Column(Float, nullable=True)
    diff_percent = Column(Float, nullable=True)
    severity = Column(String(10), nullable=False, default="low")  # "low" | "high"
    captured_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    source_endpoint = Column(String(200), nullable=False, index=True)
    # Debug için: isteğe bağlı request_id
    request_id = Column(String(50), nullable=True)
    
    __table_args__ = (
        Index("idx_ptf_drift_period_severity", "period", "severity"),
    )
```

### 3.2 Drift hesaplama — edge case'ler

```python
# backend/app/ptf_drift_log.py

def compute_drift(canonical: float, legacy: float | None) -> dict:
    """Drift hesaplama. Edge case'ler:
    - legacy None (period legacy'de yok): diff_abs=None, severity="low" (migration durumu, drift değil)
    - canonical == 0 (teorik olarak olmamalı): diff_percent = abs(legacy) / 1.0 * 100 (güvenlik)
    - ikisi de 0: diff = 0
    """
    if legacy is None:
        return {
            "legacy_value_tl_per_mwh": None,
            "diff_abs": None,
            "diff_percent": None,
            "severity": "low",
            "note": "legacy_period_missing",
        }
    diff_abs = abs(canonical - legacy)
    if canonical == 0 and legacy == 0:
        diff_percent = 0.0
    elif canonical == 0:
        diff_percent = 100.0  # canonical sıfır, legacy değil → %100 drift
    else:
        diff_percent = diff_abs / canonical * 100.0
    severity = "high" if diff_percent > 0.5 else "low"
    return {
        "legacy_value_tl_per_mwh": legacy,
        "diff_abs": round(diff_abs, 4),
        "diff_percent": round(diff_percent, 4),
        "severity": severity,
    }
```

### 3.3 Canonical değer hesaplama (saatlik → aylık karşılaştırma)

Legacy tablo aylık, canonical saatlik. Karşılaştırma için canonical'ı **ağırlıklı ortalama** olarak indirgiyoruz:

```python
def canonical_monthly_avg(db: Session, period: str) -> float | None:
    """hourly_market_prices üzerinden aylık ağırlıklı ortalama PTF.
    
    Ağırlık: şu an basit aritmetik ortalama (saat uzunlukları eşit).
    Gelecekte tüketim profiline göre weighted hesap eklenebilir — ama drift
    karşılaştırması için simple avg yeterli (legacy değer zaten basit ortalama).
    """
    from sqlalchemy import func
    result = db.query(func.avg(HourlyMarketPrice.ptf_tl_per_mwh)).filter(
        HourlyMarketPrice.period == period,
        HourlyMarketPrice.is_active == 1,
    ).scalar()
    return float(result) if result is not None else None
```

## 4. Fallback yasağı — implementation

### 4.1 canonical okuma wrapper'ı

```python
# backend/app/pricing/router.py — yeni helper

class MarketDataNotFound(HTTPException):
    """Canonical tabloda veri yok — fallback yasağı gereği 409."""
    def __init__(self, period: str, legacy_has_data: bool):
        super().__init__(
            status_code=409,
            detail={
                "error": "market_data_not_found",
                "message": (
                    f"{period} dönemi için canonical piyasa verisi (hourly_market_prices) bulunamadı. "
                    "Lütfen önce admin panelinden saatlik veri girin veya EPİAŞ sync tetikleyin."
                ),
                "period": period,
                "canonical_source": "hourly_market_prices",
                "legacy_has_data": legacy_has_data,
            },
        )


def _load_market_records_strict(db: Session, period: str) -> list[ParsedMarketRecord]:
    """Canonical-only okuma. Veri yoksa 409.
    
    Kill switch (use_legacy_ptf=True) iken bu fonksiyon çağrılmaz.
    """
    rows = (
        db.query(HourlyMarketPrice)
        .filter(
            HourlyMarketPrice.period == period,
            HourlyMarketPrice.is_active == 1,
        )
        .order_by(HourlyMarketPrice.date, HourlyMarketPrice.hour)
        .all()
    )
    if not rows:
        # Legacy'de var mı bilgisi audit için dönülür — ama oraya DÜŞMEK yasak
        legacy_exists = db.query(
            db.query(MarketReferencePrice).filter(
                MarketReferencePrice.period == period,
                MarketReferencePrice.price_type == "PTF",
                MarketReferencePrice.ptf_tl_per_mwh > 0,
            ).exists()
        ).scalar()
        raise MarketDataNotFound(period=period, legacy_has_data=bool(legacy_exists))
    return _parse_market_records(rows)
```

### 4.2 Dual-read (Phase 2 only)

```python
def _load_market_records_dual(db: Session, period: str) -> list[ParsedMarketRecord]:
    """Phase 2: canonical'dan oku, legacy'den de oku, drift logla, canonical'ı dön.
    
    Bu fonksiyon Phase 3'te silinir.
    """
    canonical_records = _load_market_records_strict(db, period)
    # strict fonksiyon zaten 409 fırlatır veri yoksa — buraya ulaşıldıysa canonical var
    
    # Legacy'den okuma başarısız olursa drift log "legacy_unavailable" ile geçer
    try:
        legacy_row = db.query(MarketReferencePrice).filter(
            MarketReferencePrice.period == period,
            MarketReferencePrice.price_type == "PTF",
        ).first()
        legacy_value = legacy_row.ptf_tl_per_mwh if legacy_row else None
    except Exception:
        legacy_value = None
    
    # Drift log — async-safe değil, ama sync DB ile sorun yok
    from ..ptf_drift_log import record_drift
    canonical_avg = sum(r.ptf_tl_per_mwh for r in canonical_records) / len(canonical_records)
    record_drift(
        db=db,
        period=period,
        canonical_value=canonical_avg,
        legacy_value=legacy_value,
        source_endpoint="/api/pricing/analyze",  # request context'ten alınabilir
    )
    
    return canonical_records  # Legacy değeri response'a KARIŞTIRILMAZ
```

### 4.3 Router dispatcher

```python
# backend/app/pricing/router.py

def _load_market_records(db: Session, period: str) -> list[ParsedMarketRecord]:
    """Phase-aware market records loader.
    
    Phase 1 (write lock): strict canonical (dual-read yok)
    Phase 2 (dual-read): dual + drift log
    Phase 3 (single read): strict canonical (drift log silindi)
    Phase 4 (legacy dropped): strict canonical (kill switch silindi)
    """
    config = get_guard_config()
    
    # Kill switch (Phase 1-3 arası rollback için)
    if config.use_legacy_ptf:
        logger.warning("ptf_legacy_fallback_active period=%s", period)
        get_ptf_metrics().inc_legacy_fallback(period=period)
        return _load_from_legacy_table(db, period)
    
    # Dual-read penceresi — Phase 2 only
    if config.ptf_drift_log_enabled:
        return _load_market_records_dual(db, period)
    
    # Default — strict canonical
    return _load_market_records_strict(db, period)
```

## 5. Migration akışı (faz bazlı detay)

### Phase 1 — Write lock + kill switch skeleton

**Dokunulan dosyalar:**

- `backend/app/guard_config.py` — `use_legacy_ptf`, `ptf_drift_log_enabled` eklendi
- `backend/app/pricing/router.py::_load_market_records` — kill switch branch eklendi (dual-read henüz YOK; default strict path)
- `backend/app/market_prices.py::upsert_market_price` — **devre dışı**: `POST /admin/market-prices` → 409 `manual_ptf_disabled` (R6.3)
- `backend/app/main.py::_add_sample_market_prices` — **devre dışı**: seed fonksiyonu fırlatılır (no-op + log warning)
- `backend/app/main.py` `/api/epias/prices/{period}` POST — `hourly_market_prices`'a yazmaya yönlendirildi (EPİAŞ API saatlik data döner)
- `backend/alembic/versions/012_ptf_drift_log.py` — `ptf_drift_log` tablosu oluştur
- `backend/app/ptf_drift_log.py` — module skeleton

**DoD:** Tüm yazma yolları canonical'a; pre-migration baseline hash'leri bozulmamış (R8.3).

### Phase 2 — Dual-read + drift log

**Dokunulan dosyalar:**

- `backend/app/pricing/router.py::_load_market_records` — `_load_market_records_dual` branch aktif
- `backend/app/ptf_drift_log.py::record_drift` — implementation
- `backend/app/ptf_metrics.py` — `ptf_drift_observed_total{period,severity}` counter
- `scripts/11_drift_analysis.py` (yeni) — drift log raporu üretir

**Pencere:** 7-14 gün. Metrikler Grafana'da izlenir.

**DoD:** Drift analizi `severity: "high"` ≤%5, ortalama `diff_percent` ≤0.5% tüm dönemler için (R10).

### Phase 3 — Single read (legacy kapalı)

**Dokunulan dosyalar:**

- `backend/app/pricing/router.py::_load_market_records` — dual-read dalı silinir
- `backend/app/guard_config.py::ptf_drift_log_enabled` = False (default değişir)
- `backend/app/pricing/yekdem_service.py` — legacy fallback + mirror bloğu silinir (R7)
- `backend/app/main.py` `/api/epias/prices/{period}` GET — canonical'dan döner (legacy'ye düşme yok)
- `backend/app/market_prices.py::get_market_prices` vb. — deprecation warning + HTTP 410 Gone?

**Karar:** Deprecation pattern mı, hard fail mi? → **Hard fail**. `get_market_prices` fonksiyonu silinir; bu fonksiyonu çağıran `/admin/market-prices/{period}/lock|unlock` endpoint'leri de silinir (R9.2). Lock kavramı canonical'da yok (yeni admin UI'si ile tasarlanacak).

**DoD:** CI guard `test_rule2_no_new_legacy_ptf_writers` PASS (xfail değil).

### Phase 4 — Hard delete

**Dokunulan dosyalar:**

- `backend/alembic/versions/013_drop_market_reference_prices.py` — `DROP TABLE market_reference_prices`
- `backend/app/market_prices.py` — dosya silinir (R9.2)
- `backend/app/bulk_importer.py` — `MarketReferencePrice` referansları kaldırılır (bu spec'te bulk importer davranışı değişir; detay aşağıda)
- `backend/app/market_price_admin_service.py` — legacy query yolları kaldırılır (PTF için); YEKDEM için bu dosya hâlâ canlı
- `backend/app/seed_market_prices.py` — dosya silinir (orphan, hard delete candidate)
- `backend/app/database.py::MarketReferencePrice` class silinir
- `backend/app/database.py::PriceChangeHistory` — `price_record_id` FK hedefi değişir veya tablo silinir (audit trail kararına bağlı)
- `backend/app/guard_config.py` — `use_legacy_ptf` ve `ptf_drift_log_enabled` silinir

**DoD:** Post-Phase-4 golden baseline hash'leri pre-migration ile BYTE-WISE eşit (25/30; 2025-12 × 2 × 3 = 5 senaryo 409 kalır).

## 6. `bulk_importer.py` davranışı

Bulk importer şu anda `market_reference_prices`'a yazıyor (line 246-250). Bu dosya admin panelden CSV/JSON upload ile çoklu PTF girişine yarar. Phase 1-3 arasında bu dosya:

- Phase 1: devre dışı (409 `bulk_ptf_disabled`, R6.3 benzeri)
- Phase 3: yeni davranış — `hourly_market_prices`'a yazar ama **saatlik CSV şeması** bekler (aylık CSV kabul edilmez — türetme yasak, R2.3)

Bu değişiklik `ptf-admin-frontend` spec'inin kapsamına giren UI değişikliği gerektirir. PTF migration spec'i yalnızca BE API'sini hazırlar; FE değişikliği ayrı PR.

## 7. Test stratejisi

### 7.1 Yeni invariant testler (mevcut `test_main_wiring_invariant.py`'ye eklenir)

```python
# test_main_wiring_invariant.py içine eklenecek

def test_rule4_no_silent_ptf_fallback(main_closure):
    """Fallback yasağı: canonical'da veri yoksa 409 dönmeli, legacy'ye düşmemeli.
    
    Heuristic: pricing/router.py içinde hem hourly_market_prices hem
    market_reference_prices okuma hem de aynı fonksiyon scope'unda kullanılırsa
    (if/else olmadan) FAIL.
    """
    # AST tabanlı basit kontrol; false positive kabul edilebilir


def test_rule5_no_hourly_derived_from_monthly():
    """Aylık → saatlik türetme yasak (R2.3).
    
    grep guard: 'hourly' ve 'monthly' aynı satırda × / for loop'ta 24x bölme.
    """
    # Regex: r'hourly.*=.*monthly.*/\s*24|monthly_to_hourly|aylik_saatlik'
```

### 7.2 Baseline diff testi

```bash
# Her phase sonunda
python scripts/09_golden_baseline.py --label post-phase-1
python scripts/10_baseline_compare.py \
    baselines/2026-05-12_pre-ptf-unification_baseline.json \
    baselines/2026-05-15_post-phase-1_baseline.json
# Exit code 0 = eşit, 1 = regresyon
```

### 7.3 Drift log property test

```python
# backend/tests/test_ptf_drift_log.py
from hypothesis import given, strategies as st

@given(canonical=st.floats(0.01, 10_000), legacy=st.floats(0.01, 10_000))
def test_drift_computation_is_symmetric(canonical, legacy):
    """compute_drift(c, l).diff_abs == compute_drift(l, c).diff_abs"""
    d1 = compute_drift(canonical, legacy)
    d2 = compute_drift(legacy, canonical)
    assert d1["diff_abs"] == d2["diff_abs"]
```

## 8. Rollback senaryoları

### S1 — Phase 1'de hata (write lock fail)

**Belirti:** Admin panelden PTF girişi başarısız, kullanıcı 500 veya beklenmeyen 409 alıyor.

**Aksiyon:**
1. `USE_LEGACY_PTF=true` set et (10 saniye içinde yeni davranış)
2. Phase 1 PR'ını revert et
3. Root cause analiz

### S2 — Phase 2'de yüksek drift

**Belirti:** `ptf_drift_observed_total{severity="high"}` Grafana'da yükseliyor.

**Aksiyon:**
1. Drift log satırlarından pattern'i bul (hangi dönem, hangi endpoint)
2. Eğer saatlik veri yanlışsa backfill düzelt
3. Eğer aylık legacy yanlışsa → planlı davranış (legacy verinin yanlış olması migration sebebi)
4. `diff_percent` kabul edilebilirse: user-decision + R10.3

### S3 — Phase 3'te production incident

**Belirti:** Canonical-only geçişten sonra beklenmeyen 409 fırtınası (müşteri etkili).

**Aksiyon:**
1. `USE_LEGACY_PTF=true` — eski davranışa dön
2. Phase 3 PR'ı revert et
3. Drift log retrospektif analiz (neyi kaçırdık?)

### S4 — Phase 4 sonrası regression

**Belirti:** Post-Phase-4 baseline hash'leri farklı.

**Aksiyon:**
1. `DROP TABLE` migration'ı reverse et (alembic downgrade 013 → 012)
2. Legacy tabloyu geri getir
3. Phase 4 PR'ı revert

## 9. Metrikler (Prometheus)

```python
# backend/app/ptf_metrics.py'ye eklenecek

ptf_legacy_fallback_total = Counter(
    "ptf_legacy_fallback_total",
    "Kill switch ile legacy tabloya düşen istek sayısı (Phase 2-3 rollback göstergesi)",
    ["period"],
)

ptf_drift_observed_total = Counter(
    "ptf_drift_observed_total",
    "Dual-read penceresinde gözlenen drift olayları",
    ["period", "severity"],
)

ptf_market_data_not_found_total = Counter(
    "ptf_market_data_not_found_total",
    "Canonical'da veri olmayıp 409 dönülen istek sayısı",
    ["period", "endpoint"],
)

ptf_canonical_monthly_avg = Gauge(
    "ptf_canonical_monthly_avg_tl_per_mwh",
    "Canonical tablodan hesaplanan aylık ortalama PTF — drift analizi için",
    ["period"],
)
```

## 10. Uyarılar ve kararlar

### U1 — `price_change_history` tablosu

Audit trail tablosu `price_change_history` → FK: `market_reference_prices.id`. Phase 4'te legacy tablo silindiğinde ya:

- (a) `price_change_history` de silinir (audit kaybı)
- (b) FK kaldırılır, denormalized `price_type + period` alanları (zaten var) yeterli — **önerilen**

Karar: (b). `price_record_id` FK'sı nullable hale getirilir + yeni migration'da bu kolon drop edilir.

### U2 — Admin UI etkisi

`POST /admin/market-prices` (aylık PTF input) devre dışı bırakıldığında admin panel UI'si broken olur. Bu:

- **Kısa vadede**: `ptf-admin-frontend` spec'i saatlik giriş UI'sini yazana kadar admin aylık PTF giremez
- **Kabul edilebilir mi?** Kullanıcı onayı gerek. Alternatif: Phase 1 write lock'u yumuşat — aylık girilen PTF `hourly_market_prices`'a 744 satır olarak replike edilir (AMA R2.3 ihlali)
- **Karar (user-decision):** Bu spec (b)'yi önerir (devre dışı); `ptf-admin-frontend` hızla başlatılmalı

### U3 — Dual-read performans etkisi

Phase 2 penceresinde her PTF okuması 2 query + 1 insert (drift log). Hot path için yük ~3x. Beklenen etki: p95 latency `/api/pricing/analyze` 150ms → 200ms. Kabul edilebilir.

Drift log batch insert yapılabilir (async worker), ama bu ayrı implementation complexity. Bu spec'te **sync insert yeterli**; Phase 2 penceresi kısa (14 gün max).

### U4 — `MarketReferencePrice` admin service

`market_price_admin_service.py` yalnızca `market_reference_prices` ile çalışır; bu dosya `ptf-admin-management` spec'inin çıktısı. Phase 3-4'te:

- Option A: Dosya silinir, `ptf-admin-management` spec'i baştan canonical için yazılır
- Option B: Dosya `hourly_market_prices` için refactor edilir

Karar: (A). Ptf-admin tarafı zaten dual-client (F-DUAL_FE) sorunu yaşıyor; PTF canonical migration ile admin stack'in yeniden yazılması aynı anda yapılmalı. **Bu PTF migration spec'inin kapsamı dışı** — `ptf-admin-management` spec'i refaktör yapar.

### U5 — Baseline'da `/api/epias/prices/{period}` GET davranışı

Pre-migration: `market_reference_prices` okur.
Post-Phase-3: `hourly_market_prices`'tan hesaplanır (aylık ortalama).

Response şeması aynı kalır (`ptf_tl_per_mwh: float`), değer yaklaşık eşit olur (drift log penceresinden sonra). Baseline hash **yaklaşık eşit değil, tam eşit** olmayabilir — çünkü legacy manuel girilmiş değer vs canonical EPİAŞ değeri.

**Karar:** Baseline doğrulamada `/api/epias/prices/{period}` için **tolerance 0** (byte-wise eşit). Eğer değer farkı varsa Phase 3 geçiş kriteri olarak **manuel gözden geçirme** ile onaylanır (R8.5).
