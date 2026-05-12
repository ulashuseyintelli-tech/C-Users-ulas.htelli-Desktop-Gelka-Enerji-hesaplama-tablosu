# Design — Codebase Audit & Cleanup Pipeline

## 0. Özet

Bu doküman `requirements.md`'deki R1–R23 gereksinimlerini somut bir **audit pipeline**'a dönüştürür. Pipeline 7 fazdan oluşur; her faz belirli scriptler, SQL sorguları ve/veya canlı API çağrılarıyla kanıt üretir. Çıktı: `.kiro/specs/codebase-audit-cleanup/audit-report.md` + destekleyici JSON artefaktları + CI'da yaşayan invariant testleri.

**Tasarım ilkeleri:**
- Kanıt önce, yorum sonra (R1).
- Pre-flight olmadan audit başlamaz (R23).
- Baseline olmadan cleanup yok (R22).
- Her adım tekrarlanabilir, parametrik, idempotent (R15).
- Read-only default; write işlemleri user-decision (R16).

## 1. Pipeline Faz Haritası

```
┌──────────────────────────────────────────────────────────────────────┐
│  FAZ 0 — PRE-FLIGHT                                                  │
│  • Schema drift check (alembic vs DB)          R23                   │
│  • Baseline snapshot alma                      R22                   │
│  • Git sha + workspace hash kayıt             R14                    │
└──────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│  FAZ 1 — ENVANTER ÇIKARMA                                            │
│  • DB tabloları + satır sayıları              R2                     │
│  • FastAPI endpoint'leri                      R8                     │
│  • Frontend fetch çağrıları                   R4,R8                  │
│  • Modül import kapanışı (main.py kök)        R5                     │
│  • 38 spec dosya envanteri                    R9                     │
│  • Cache katmanları                           R19                    │
└──────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│  FAZ 2 — EŞLEŞTİRME & HARİTA                                         │
│  • Tablo → yazıcı/okuyucu haritası            R2                     │
│  • Endpoint ↔ FE fetch matching               R4,R8                  │
│  • FE hesap ↔ BE hesap eşleştirme             R6                     │
│  • Import kapanışı canlı/ölü/dormant ayrımı   R5                     │
└──────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│  FAZ 3 — DUPLIKASYON & DRIFT TARAMASI                                │
│  • Sessiz duplikasyon tespiti                 R3                     │
│  • Dönem bütünlüğü kontrolü                   R7                     │
│  • FE/BE hesap çıktı karşılaştırma            R6                     │
│  • Input parametre eşleşmesi                  R21                    │
│  • Cache key versioning kontrolü              R19                    │
└──────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│  FAZ 4 — SoT ATAMASI & NİYET ANALİZİ                                 │
│  • Her domain verisi için SoT matrisi         R20                    │
│  • Git log + migration arkeolojisi            R20                    │
│  • Deprecation planı                          R20                    │
└──────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│  FAZ 5 — SINIFLANDIRMA & HİBRİT FIX                                  │
│  • Bulguları inline-fix / user-decision ayır  R10,R11                │
│  • Inline-fix'leri uygula + test koştur       R10                    │
│  • Baseline'a karşı drift testi               R22                    │
│  • P0/P1/P2/P3 önceliklendirme                R12                    │
└──────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│  FAZ 6 — RAPOR & ROADMAP                                             │
│  • audit-report.md üretimi                    R14                    │
│  • cleanup roadmap (P0 → P3)                  R13                    │
│  • source-of-truth.md steering yazımı         P-A                    │
│  • Invariant test generation                  P-D                    │
└──────────────────────────────────────────────────────────────────────┘
```

Her fazın somut script ve artefaktları sonraki bölümlerde.

## 2. Dizin Yapısı

Audit tarafından üretilen her şey tek çatı altında:

```
.kiro/specs/codebase-audit-cleanup/
├── requirements.md                    (mevcut — R1-R23)
├── design.md                          (bu dosya)
├── tasks.md                           (sonraki aşamada yazılacak)
├── audit-report.md                    (Faz 6'da üretilir)
├── scripts/                           (audit probe'ları — versioned)
│   ├── 00_preflight_schema.py        (R23)
│   ├── 00_preflight_baseline.py      (R22)
│   ├── 01_inventory_db.py            (R2)
│   ├── 01_inventory_endpoints.py     (R8)
│   ├── 01_inventory_fe_fetch.py      (R4)
│   ├── 01_inventory_imports.py       (R5)
│   ├── 01_inventory_specs.py         (R9)
│   ├── 01_inventory_cache.py         (R19)
│   ├── 02_map_writers_readers.py     (R2)
│   ├── 02_map_endpoint_fetch.py      (R4,R8)
│   ├── 02_map_calc_pairs.py          (R6)
│   ├── 03_detect_duplication.py      (R3)
│   ├── 03_check_period_integrity.py  (R7)
│   ├── 03_diff_calc_outputs.py       (R6)
│   ├── 03_check_input_matching.py    (R21)
│   ├── 03_check_cache_versioning.py  (R19)
│   ├── 04_sot_history_archaeology.py (R20)
│   ├── 05_classify_findings.py       (R10,R11,R12)
│   ├── 05_run_drift_test.py          (R22)
│   ├── 06_generate_report.py         (R14)
│   └── _common.py                    (ortak yardımcılar — db connect, git helpers)
├── baselines/                         (R22)
│   └── YYYY-MM-DD_golden_baseline.json
├── artifacts/                         (faz çıktıları — JSON/CSV)
│   ├── phase0_preflight.json
│   ├── phase1_inventory.json
│   ├── phase2_maps.json
│   ├── phase3_findings_raw.json
│   ├── phase4_sot_matrix.json
│   └── phase5_findings_classified.json
└── tmp/                               (R16 — audit bitiminde temizlenir)
```

**Neden ayrı klasör:** Audit scriptleri üretim kodunu kirletmez, commit'te tek çatı altında taşınır/silinir.

## 3. Faz 0 — Pre-Flight

### 3.1 Schema Drift Check (R23)

**Script:** `scripts/00_preflight_schema.py`

**Adımlar:**
1. `alembic current` → mevcut DB revision (HEAD_DB)
2. `alembic heads` → migration dosya HEAD (HEAD_FILE)
3. Eşit değilse → **STOP**, kullanıcıya bildir.
4. Eşitse, DB canlı şemasını çıkar:
   ```sql
   SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;
   -- Her tablo için:
   PRAGMA table_info('<tablo>');
   ```
5. Model tanımlarından (`backend/app/models.py`, `backend/app/pricing/schemas.py`) beklenen şemayı çıkar.
6. Kolon-kolon karşılaştır. Fark listesi JSON'a yaz.

**Çıktı formatı:**
```json
{
  "alembic_current": "abc123",
  "alembic_head": "abc123",
  "in_sync": true,
  "drift": []
  // veya in_sync: false ise:
  // "drift": [
  //   {"table": "market_reference_prices", "missing_in_model": ["captured_at"], "extra_in_model": [], "type_mismatches": []}
  // ]
}
```

**Başarı kriteri:** `in_sync == true`. Değilse audit fazı 1'e geçemez (user-decision gerekli).

### 3.2 Golden Baseline Snapshot (R22)

**Script:** `scripts/00_preflight_baseline.py`

**Girdiler (parametrik):**
```python
BASELINE_PERIODS = ["2025-12", "2026-01", "2026-02", "2026-03", "2026-04"]
BASELINE_SCENARIOS = [
    # (period, consumption_kwh, current_price, demand_qty, demand_price)
    ("2026-01", 100000, 2.85, 100, 150.0),
    ("2026-01", 500000, 2.90, 500, 160.0),
    # ... her dönem için 2 senaryo
]
```

**Her senaryo için çalıştırılacaklar:**
1. `POST /api/full-process` (veya muadili) — backend hesap
2. `POST /api/pricing/analyze` — risk analizi
3. Frontend `liveCalculation` formülünü Python'da birebir replika et (test için)
4. PDF üret, sayısal alanları çek
5. Fatura → geri çözümleme (varsa)

**Çıktı formatı:**
```json
{
  "baseline_sha": "abc123",
  "captured_at": "2026-05-11T14:00:00Z",
  "scenarios": [
    {
      "id": "2026-01_s1",
      "inputs": {...},
      "outputs": {
        "backend_full_process": {...},
        "pricing_analyze": {...},
        "frontend_replica": {...},
        "pdf_numerics": {...}
      },
      "output_hash": "sha256:..."
    }
  ]
}
```

**Baseline dosyası:** `baselines/YYYY-MM-DD_golden_baseline.json` — git'e commit edilir. İleri audit turları bu dosyayı referans alır.

### 3.3 Git/Workspace Hash Kayıt (R14)

- `git rev-parse HEAD` → commit sha
- `git status --porcelain` → uncommitted file list
- Workspace hash: tüm kaynak dosyaların sha256 özeti (tek hash)

Rapora metadata olarak eklenir.

## 4. Faz 1 — Envanter Çıkarma

### 4.1 DB Envanteri (R2)

**Script:** `scripts/01_inventory_db.py`

```sql
-- Tablo listesi + satır sayısı
SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;

-- Her tablo için
SELECT COUNT(*) FROM "<tablo>";
PRAGMA table_info('<tablo>');
PRAGMA foreign_key_list('<tablo>');
PRAGMA index_list('<tablo>');
```

**Çıktı:** `artifacts/phase1_inventory.json`
```json
{
  "db": {
    "path": "gelka_enerji.db",
    "size_bytes": 215040,
    "tables": [
      {
        "name": "market_reference_prices",
        "row_count": 60,
        "columns": [
          {"name": "id", "type": "INTEGER", "pk": true},
          {"name": "period", "type": "TEXT", "nullable": false},
          ...
        ],
        "indexes": [...],
        "foreign_keys": [...]
      }
    ]
  }
}
```

### 4.2 Endpoint Envanteri (R8)

**Script:** `scripts/01_inventory_endpoints.py`

**Yöntem:** AST parse ile `@app.get/post/put/delete`, `@router.*` decorator'larını topla.

```python
import ast
from pathlib import Path

def extract_endpoints(file_path):
    tree = ast.parse(Path(file_path).read_text(encoding='utf-8'))
    endpoints = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and hasattr(dec.func, 'attr'):
                    method = dec.func.attr  # get/post/put/delete
                    if method in ("get", "post", "put", "delete", "patch"):
                        path_arg = dec.args[0].value if dec.args else None
                        endpoints.append({
                            "method": method.upper(),
                            "path": path_arg,
                            "function": node.name,
                            "file": file_path,
                            "line": node.lineno
                        })
    return endpoints
```

**Çıktı:**
```json
{
  "endpoints": [
    {"method": "GET", "path": "/api/epias/prices/{period}", "function": "get_epias_prices", "file": "backend/app/main.py", "line": 1234},
    ...
  ]
}
```

### 4.3 Frontend Fetch Envanteri (R4,R8)

**Script:** `scripts/01_inventory_fe_fetch.py`

**Yöntem:** grep + regex ile TS/TSX dosyalarında `fetch(`, `axios.`, `api.` çağrılarını yakala.

```python
import re, os
FETCH_PATTERNS = [
    r'fetch\s*\(\s*[`"\']([^`"\']+)[`"\']',
    r'axios\.\w+\s*\(\s*[`"\']([^`"\']+)[`"\']',
    r'\.(get|post|put|delete|patch)\s*[<(]?\s*[`"\']?([^`"\')]+)[`"\']?',
]
```

Her match → `{file, line, method, url_template, variable_interpolations}`

**Kritik detay:** URL template interpolation'ları yakala (`${period}` gibi). Regex sonuçları **hipotez**, kesin eşleşme Faz 2'de yapılacak.

### 4.4 Import Kapanışı (R5)

**Script:** `scripts/01_inventory_imports.py`

**Yöntem:**
1. `backend/app/main.py` → AST parse → tüm `import` ve `from ... import` ifadeleri
2. Transitive: her imported modülü aç, onun import'larını ekle, BFS ile kapanış
3. **Lazy import'ları dahil et:** fonksiyon gövdesi içindeki `import` ifadeleri de sayılır (grep + AST hibrit)
4. Sonuç: `alive_modules: Set[str]`

**Ayrı set'ler:**
- `alive_from_main`: main.py kapanışı
- `alive_from_tests`: `backend/tests/` kapanışı
- `orphan`: hiçbirinde olmayan

**Dormant kontrolü (R5):**
- `guard_config.py` ve benzeri feature flag dosyalarını oku
- Flag = `False` ise bağlı modülleri "dormant" işaretle
- Bağlılık tespiti: `if guard_config.FEATURE_X:` grep'i

**Çıktı:**
```json
{
  "imports": {
    "alive_from_main": [...],
    "alive_from_tests_only": [...],
    "orphan": [...],
    "dormant": [
      {"module": "backend.app.adaptive_control.controller", "flag": "ADAPTIVE_ENABLED", "flag_value": false, "flag_file": "backend/app/guard_config.py:42"}
    ]
  }
}
```

### 4.5 Spec Envanteri (R9)

**Script:** `scripts/01_inventory_specs.py`

Her `.kiro/specs/<spec>/` altında:
- `requirements.md` var mı?
- `design.md` var mı?
- `tasks.md` var mı? → varsa `[x]` / `[ ]` sayılarını çıkar
- `bugfix.md` / `README.md` gibi ek dosyalar

**Tamamlanma yüzdesi:** `done / (done + open)` × 100

```json
{
  "specs": [
    {
      "name": "pricing-risk-engine",
      "has_requirements": true,
      "has_design": true,
      "has_tasks": true,
      "tasks_done": 24,
      "tasks_open": 3,
      "completion_pct": 88.9,
      "category": "core"  // core / governance / infrastructure
    }
  ]
}
```

### 4.6 Cache Envanteri (R19)

**Script:** `scripts/01_inventory_cache.py`

**Aranacak pattern'ler:**
- `@lru_cache`, `@cache`, `@functools.cache`
- `TTLCache`, `LRUCache`, `cachetools`
- DB tabloları: `analysis_cache`, `*_cache` naming
- Frontend: `localStorage`, `sessionStorage`, `@tanstack/react-query`, `swr`
- PDF: `pdf_artifact_store`, `pdf_job_store`
- HTTP middleware cache

Her cache entry için:
```json
{
  "name": "analysis_cache",
  "type": "db_table",
  "location": "backend/app/pricing/cache.py:15 + gelka_enerji.db::analysis_cache",
  "key_schema": "period:input_hash",
  "ttl": null,
  "invalidation_trigger": "manual DELETE",
  "source_version_in_key": false
}
```

## 5. Faz 2 — Eşleştirme & Harita

### 5.1 Tablo Yazıcı/Okuyucu Haritası (R2)

**Script:** `scripts/02_map_writers_readers.py`

**Yöntem (hibrit grep + AST):**

1. **SQL-level tarama (grep):**
   ```
   SELECT tespiti:  "FROM\s+<tablo>"  "JOIN\s+<tablo>"
   INSERT tespiti:  "INTO\s+<tablo>"  
   UPDATE tespiti:  "UPDATE\s+<tablo>"
   DELETE tespiti:  "DELETE\s+FROM\s+<tablo>"
   ```
2. **ORM-level tarama (AST):**
   - `Session.query(<Model>)` → okuyucu
   - `session.add(<Model>())` → yazıcı
   - `Model.__tablename__` → tablo ↔ sınıf eşleşmesi
   - SQLAlchemy 2.0 `select(Model)`, `insert(Model)`, `update(Model)`

3. **Şizofrenik tablo tespiti:**
   - Tablo T, M1 ve M2 tarafından yazılıyor.
   - M1'in yazdığı kolon seti: C1. M2'nin: C2.
   - `C1 != C2` ve `C1 ∩ C2 < min(|C1|, |C2|)` → şizofrenik adayı

**Çıktı:**
```json
{
  "tables": {
    "market_reference_prices": {
      "writers": [
        {"module": "backend/app/main.py", "line": 2341, "columns_written": ["period", "ptf_tl_per_mwh"]},
        {"module": "backend/app/admin/market_prices.py", "line": 89, "columns_written": ["period", "ptf_tl_per_mwh", "yekdem_tl_per_mwh", "source"]}
      ],
      "readers": [
        {"module": "backend/app/epias_service.py", "line": 145, "columns_read": ["period", "ptf_tl_per_mwh", "yekdem_tl_per_mwh"]},
        {"module": "backend/app/pricing/yekdem_service.py", "line": 78, "columns_read": ["yekdem_tl_per_mwh"]}
      ],
      "schizophrenic_candidate": false
    }
  }
}
```

### 5.2 Endpoint ↔ FE Fetch Eşleştirme (R4,R8)

**Script:** `scripts/02_map_endpoint_fetch.py`

**Yöntem:**
1. Her backend endpoint'in path template'ini normalize et: `/api/epias/prices/{period}` → regex `/api/epias/prices/[^/]+`
2. Her FE fetch URL template'ini normalize et: `` `/api/epias/prices/${period}` `` → aynı regex
3. Regex eşleşmesiyle çiftleri kur

**Sonuç üç set:**
- `matched`: hem BE hem FE'de var
- `endpoint_only`: BE'de tanımlı, FE'de çağrı yok → **ölü endpoint**
- `fetch_only`: FE çağırıyor, BE'de endpoint yok → **kırık bağ**

**Canlı doğrulama (opsiyonel, R4.3):**
Her `matched` için uvicorn çalışırken `GET /api/...` canlı istek at, 200/422 response'unu artifact'a yaz.

### 5.3 Hesap Çifti Eşleştirme (R6)

**Script:** `scripts/02_map_calc_pairs.py`

**Zorluk:** FE ve BE farklı dillerde; otomatik "aynı hesabı yapıyor" tespiti %100 mümkün değil. Hibrit yaklaşım:

**Heuristic 1 — isim eşleşmesi:**
- BE: `calculate_total_cost`, `calc_yekdem`, `compute_net_margin`
- FE: `calculateTotalCost`, `calcYekdem`, `computeNetMargin` (camelCase)

**Heuristic 2 — formül eşleşmesi:**
- BE: `yekdem * consumption_kwh / 1000`
- FE: `yekdem * consumptionKwh / 1000` → expression AST düzeyinde benzerlik

**Heuristic 3 — steering override:**
- `source-of-truth.md` içinde elle belirtilmiş çiftler (kesin)

**Çıktı:**
```json
{
  "calc_pairs": [
    {
      "name": "yekdem_inclusive_unit_price",
      "backend": {"file": "backend/app/pricing/engine.py", "func": "calc_yekdem_inclusive", "line": 234},
      "frontend": {"file": "frontend/src/App.tsx", "func": "liveCalculation (inline)", "line": 1456},
      "confidence": "high|medium|low",
      "evidence": "name match + formula AST similarity 0.92"
    }
  ]
}
```

## 6. Faz 3 — Duplikasyon & Drift Taraması

### 6.1 Sessiz Duplikasyon Tespiti (R3)

**Script:** `scripts/03_detect_duplication.py`

**Domain veri listesi (hardcoded, manuel maintenance):**
```python
DOMAIN_CONCEPTS = {
    "yekdem": ["yekdem_tl_per_mwh", "yekdem", "YEKDEM"],
    "ptf": ["ptf_tl_per_mwh", "ptf", "weighted_ptf", "PTF"],
    "distribution_tariff": ["distribution_tariff", "dist_tariff_tl_per_kwh"],
    "retail_tariff": ["retail_tariff", "unit_price"],
    "btv": ["btv", "btv_oran"],
    "kdv": ["kdv", "vat_rate"],
    "commission": ["bayi_payi", "commission", "margin_rate"],
}
```

**Her domain için:**
1. DB'de hangi tablo + kolonlarda geçiyor (grep + schema inventory join)
2. Kodda hangi fonksiyon/değişkenlerde (grep)
3. Kesişim noktaları → duplikasyon aday setleri

**Çıktı format:**
```json
{
  "silent_duplications": [
    {
      "domain": "yekdem",
      "sources": [
        {"type": "db_column", "location": "market_reference_prices.yekdem_tl_per_mwh", "row_count": 60},
        {"type": "db_column", "location": "monthly_yekdem_prices.yekdem_tl_per_mwh", "row_count": 21},
        {"type": "hardcoded", "location": "frontend/src/App.tsx:82", "value": "various fallbacks"}
      ],
      "period_overlap": ["2026-01", "2026-02", "..."],
      "value_diffs": [
        {"period": "2026-01", "market_ref": 162.73, "monthly": 162.73, "diff": 0.0}
      ],
      "suggested_sot": "monthly_yekdem_prices",
      "sot_reason": "R20 niyet analizi sonucu belirlenecek",
      "classification": "user-decision"
    }
  ]
}
```

### 6.2 Dönem Bütünlüğü (R7)

**Script:** `scripts/03_check_period_integrity.py`

**Girdi:**
```python
PERIOD_RANGE = ("2025-01", "2026-12")
DOMAIN_TABLES = {
    "ptf_hourly": "hourly_market_prices",
    "ptf_monthly": "market_reference_prices",  # period, ptf_tl_per_mwh
    "yekdem_canonical": "monthly_yekdem_prices",
    "yekdem_legacy": "market_reference_prices",  # period, yekdem_tl_per_mwh
    "distribution_tariff": "distribution_tariffs",
    "retail_tariff": None,  # yoksa null — bulgu
}
```

**Her domain için:**
```sql
SELECT period FROM <tablo> ORDER BY period;
```

Ardından `PERIOD_RANGE` ile fark al → eksik dönemler.

**Cross-source fark:**
```sql
-- YEKDEM için iki kaynak arası dönem farkı
SELECT period FROM market_reference_prices WHERE yekdem_tl_per_mwh > 0
EXCEPT
SELECT period FROM monthly_yekdem_prices;
```

**Çıktı:**
```json
{
  "period_integrity": {
    "yekdem_canonical": {
      "present_periods": ["2025-01", ..., "2026-12"],
      "missing_in_expected_range": [],
      "cross_source_diff": {
        "in_legacy_only": [],
        "in_canonical_only": [],
        "value_mismatches": []
      }
    }
  }
}
```

### 6.3 Hesap Çıktı Diff Testi (R6)

**Script:** `scripts/03_diff_calc_outputs.py`

**Zorluk:** FE JS'de, BE Python'da. Çözüm 2 yoldan biri:

**Yol A — Node subprocess:**
```python
import subprocess, json

def run_frontend_calc(inputs: dict) -> dict:
    script = f"""
    const {{ liveCalculation }} = require('./frontend/dist/calc-exports.js');
    const result = liveCalculation({json.dumps(inputs)});
    console.log(JSON.stringify(result));
    """
    out = subprocess.check_output(["node", "-e", script])
    return json.loads(out)
```
Gerektirir: FE hesaplama kodu bundle edilmiş olarak export edilmeli (yeni bir build target). Ağır ama kesin.

**Yol B — Python replika (pragmatik):**
FE formülünü Python'a elle çevir, kesin manual mapping. Hata payı var ama test kolaylığı yüksek.

**Öneri:** Başlangıçta Yol B, kritik çiftler için Yol A'ya geç.

**Diff testi:**
```python
for pair in calc_pairs:
    for test_input in TEST_MATRIX:
        be_result = call_backend_api(pair, test_input)
        fe_result = run_frontend_replica(pair, test_input)
        diff = abs(be_result - fe_result)
        if diff > 0.01:
            findings.append({
                "type": "fe_be_output_drift",
                "severity": "P0" if diff > 1.0 else "P1",
                "pair": pair["name"],
                "input": test_input,
                "be": be_result,
                "fe": fe_result,
                "diff": diff
            })
```

### 6.4 Input Parametre Eşleşmesi (R21)

**Script:** `scripts/03_check_input_matching.py`

Faz 2'den gelen `calc_pairs` için:
- BE fonksiyon imzasını AST'den çıkar → param listesi + default'lar
- FE fonksiyon imzasını TS parse et → param listesi + default'lar
- Ad eşleştirme (snake_case ↔ camelCase normalize)
- Birim tespiti: docstring / comment / değişken adından (örn: `_tl_per_mwh`, `_kwh`, `_pct`)

**Çıktı:** "Input Matching Matrix" her çift için bir tablo.

### 6.5 Cache Versioning Kontrolü (R19)

**Script:** `scripts/03_check_cache_versioning.py`

Faz 1.6'daki envanter için, her cache entry'nin key şemasını incele:

```python
for cache in caches:
    key = cache["key_schema"]
    has_version = bool(re.search(r'(v=|version|hash|sha|rev)', key))
    if not has_version:
        findings.append({
            "severity": "P0",
            "cache": cache["name"],
            "issue": "Cache key'inde source version yok → stale cache riski",
            "recommendation": f"Key şemasını '{key}:sha=<source_hash>' olarak güncelle"
        })
```

## 7. Faz 4 — SoT Ataması & Niyet Analizi

### 7.1 Niyet Analizi (R20)

**Script:** `scripts/04_sot_history_archaeology.py`

Her duplike kaynak için **git log arkeolojisi:**

```bash
# Tablo modelinin ilk eklendiği commit
git log --diff-filter=A --all --format="%h|%ai|%s" -- backend/app/models.py | grep -i "<table_name>"

# Migration dosyaları
ls backend/alembic/versions/*.py | xargs grep -l "<table_name>"

# Son değiştirildiği commit
git log -1 --format="%h|%ai|%s" -- <model_file>
```

**Çıktı her kaynak için:**
```json
{
  "source": "market_reference_prices",
  "introduced_at": {"sha": "abc123", "date": "2025-08-15", "msg": "feat: EPİAŞ market reference prices import"},
  "last_touched": {"sha": "xyz789", "date": "2026-04-21", "msg": "fix: added provisional status column"},
  "migrations": ["0042_add_market_reference_prices.py", "0057_add_price_type_column.py"],
  "inferred_intent": "legacy — ilk PTF/YEKDEM import yolu",
  "confidence": "medium"
}
```

**Niyet etiketleri:**
- `canonical_new` — yeni, temiz şema
- `legacy_import` — eski import yolu, muhtemelen deprecate
- `domain_specific` — belirli iş akışı için yazılmış
- `unclear` — git geçmişinden anlaşılamıyor → user-decision

### 7.2 SoT Matrisi Üretimi (R20)

**Her domain için karar tablosu:**

| Faktör | market_reference_prices | monthly_yekdem_prices |
|---|---|---|
| Satır sayısı | 60 | 21 |
| Okuyucu modül sayısı | 3 | 2 |
| Yazıcı modül sayısı | 2 | 1 |
| Şema temizliği (kolon sayısı, amaç netliği) | Karışık (PTF+YEKDEM+status) | Tek amaçlı |
| Git yaşı | Eski | Yeni |
| Niyet | legacy_import | canonical_new (muhtemel) |
| Canlı iş akışında kullanılıyor | Manuel mod (FE) | Risk analizi |

**Karar:** Skor bazlı değil, **kullanıcı kararı**. Agent öneriyi yazar, kullanıcı onaylar.

**SoT matrisi çıktısı:**
```json
{
  "sot_matrix": [
    {
      "concept": "yekdem",
      "canonical_source": "monthly_yekdem_prices.yekdem_tl_per_mwh",
      "canonical_writer": "backend/app/pricing/yekdem_service.py",
      "canonical_readers": ["backend/app/pricing/*"],
      "deprecated_sources": [
        {"location": "market_reference_prices.yekdem_tl_per_mwh", "migration_plan": "fallback-only-for-legacy-periods"}
      ],
      "migration_status": "partial_migration_required",
      "user_decision_required": true,
      "reason_if_undecided": "Hem yeni hem legacy kullanımda; legacy okuyucular (manuel mod) kırılmadan migrasyon planı gerekli."
    }
  ]
}
```

## 8. Faz 5 — Sınıflandırma & Hibrit Fix

### 8.1 Bulgu Sınıflandırıcı (R10,R11,R12)

**Script:** `scripts/05_classify_findings.py`

**Girdi:** Tüm önceki fazlardan toplanan ham bulgular (`phase3_findings_raw.json` + Faz 4 sonuçları).

**Sınıflandırma karar ağacı:**

```python
def classify(finding):
    # R11 — User-decision tetikleyicileri (ANY match → user-decision)
    if finding["touches_multiple_files"]:
        return "user-decision"
    if finding["requires_db_migration"]:
        return "user-decision"
    if finding["changes_public_api"]:
        return "user-decision"
    if finding["sot_choice_ambiguous"]:
        return "user-decision"
    if finding["delta_lines"] > 200:
        return "user-decision"
    if finding["category"] == "fe_to_be_migration":
        return "user-decision"
    
    # R10 — Inline-fix aday (tümü sağlanmalı)
    if (finding["single_file"] and
        not finding["behavior_change"] and
        not finding["api_change"] and
        not finding["schema_change"] and
        finding["tests_pass_after"]):
        return "inline-fix"
    
    # Belirsiz → default user-decision (güvenli yön)
    return "user-decision"
```

**Öncelik ataması (R12):**

```python
def prioritize(finding):
    # P0 — production'da yanlış çıktı
    if finding["type"] in ("fe_be_output_drift_gt_1tl", "cache_no_versioning", "silent_missing_data_in_active_path"):
        return "P0"
    # P1 — sessiz duplikasyon, yakın P0 riski
    if finding["type"] in ("silent_duplication", "schizophrenic_table", "partial_implementation"):
        return "P1"
    # P2 — dead code, maintenance
    if finding["type"] in ("dead_module", "orphan_endpoint", "spec_inflation"):
        return "P2"
    # P3 — kozmetik
    return "P3"
```

### 8.2 Inline-Fix Uygulama Protokolü (R10)

**Script:** `scripts/05_apply_inline_fixes.py` (**audit agent tarafından interaktif çağrılır**)

```
For each finding classified as "inline-fix":
  1. Backup: git stash / geçici branch
  2. Apply change
  3. Run affected tests:
     - pytest backend/tests/<related>
     - npm test -- <related>
  4. If tests fail:
       - git restore
       - Re-classify as user-decision
       - Log: "auto-escalated: tests failed"
  5. If tests pass:
       - Run drift test (R22) for all baseline scenarios
       - If drift > 0.01:
           - git restore
           - Re-classify as user-decision
           - Log: "auto-escalated: drift detected"
       - Else:
           - Commit: "audit-inline-fix: F<id> <short>"
           - Log in inline-fix bölümü
```

**Güvenlik:** Inline fix'ler ayrı commit olur (`audit-inline-fix:` prefix). Her biri tek tek revert edilebilir.

### 8.3 Drift Test Harness (R22)

**Script:** `scripts/05_run_drift_test.py`

```python
def drift_test(baseline_path, tolerance=0.01):
    baseline = json.load(open(baseline_path))
    report = {"passed": [], "failed": []}
    
    for scenario in baseline["scenarios"]:
        current_output = rerun_scenario(scenario["inputs"])
        
        for output_type in ["backend_full_process", "pricing_analyze", "frontend_replica"]:
            old = scenario["outputs"][output_type]
            new = current_output[output_type]
            
            # Numerik alanları karşılaştır (nested dict traversal)
            diffs = deep_numeric_diff(old, new, tolerance)
            
            if diffs:
                report["failed"].append({
                    "scenario_id": scenario["id"],
                    "output_type": output_type,
                    "diffs": diffs
                })
            else:
                report["passed"].append(scenario["id"])
    
    return report
```

**Integration:** Her inline-fix sonrası ve audit sonunda bu script koşar. `failed` boş değilse → cleanup durur, kullanıcıya bildir.

## 9. Faz 6 — Rapor & Roadmap

### 9.1 Rapor Şablonu (R14)

**Script:** `scripts/06_generate_report.py`

Rapor `audit-report.md` formatı:

```markdown
# Audit Report — Gelka Enerji Hesaplama Motoru

## Metadata
- Oluşturma tarihi: {timestamp}
- Commit SHA: {git_sha}
- Workspace hash: {ws_hash}
- Audit süre: {duration_minutes} dakika
- Baseline referansı: {baseline_file}

## Yönetici Özeti
- Toplam bulgu: {total}
- P0: {p0_count} | P1: {p1_count} | P2: {p2_count} | P3: {p3_count}
- Inline-fix uygulandı: {inline_fix_count}
- User-decision bekleyen: {user_decision_count}
- Drift testi sonucu: {PASS|FAIL}

## 1. Metodoloji ve Kanıt Standardı
(R1 referansı, audit prensipleri)

## 2. DB Tablosu Haritası
| Tablo | Satır | Yazıcılar | Okuyucular | Not |
|---|---|---|---|---|
{table_map_rows}

## 3. Veri Akış Haritası
(her kritik akış için FE → BE → DB zinciri)

### 3.1 Manuel Fiyat Hesaplama Akışı
- Frontend: `App.tsx:664` → `fetch('/api/epias/prices/${period}')`
- Backend: `main.py:1234` → `get_epias_prices(period)`
- DB: `SELECT * FROM market_reference_prices WHERE period=?`
- Canlı test: `GET /api/epias/prices/2026-01` → 200 OK, YEKDEM=162.73

...(diğer akışlar)

## 4. Sessiz Duplikasyon Bulguları
### F1 — YEKDEM üç kaynakta
- **Öncelik:** P1
- **Tip:** user-decision
- **Kanıt:**
  - SQL: `market_reference_prices` → 21 dönem YEKDEM>0
  - SQL: `monthly_yekdem_prices` → 21 dönem (önceki audit'te 1'den mirror edildi)
  - Grep: `hourly_market_prices` → saatlik PTF/SMF, YEKDEM yok
- **Bulgu:** YEKDEM verisi üç tabloda (2 aktif kullanım). `yekdem_service.py:78` fallback ile her ikisini okuyor.
- **Öneri:** SoT = `monthly_yekdem_prices`. Legacy tablo deprecation planı (bkz. F1-plan).

...

## 5. Canlı/Ölü Modül Haritası
| Modül | Main import? | Test-only? | Orphan? | Flag? | Satır | Öneri |
|---|---|---|---|---|---|---|
{module_table_rows}

## 6. Frontend-Backend Tutarlılık
### Input Matching Matrix
| Hesap | BE Param | FE Param | Birim Farkı | Default Farkı |
|---|---|---|---|---|
{input_matching_rows}

### Output Drift
- 2026-01 scenario_1: BE=3210.53, FE=3210.53, diff=0.00 ✓
- 2026-02 scenario_1: ...

## 7. Dönem Bütünlüğü
| Kaynak | Beklenen Dönem | Eksik | Cross-Source Fark |
|---|---|---|---|
{period_integrity_rows}

## 8. Endpoint Çağrılma Durumu
| Endpoint | Method | FE Çağırıyor? | Test Çağırıyor? | Durum |
|---|---|---|---|---|
{endpoint_table}

## 9. 38 Spec Implementasyon Durumu
| Spec | Req | Design | Tasks | %Tamamlandı | Kategori | Öneri |
|---|---|---|---|---|---|---|
{spec_table}

## 10. SoT Matrisi (R20)
| Kavram | Canonical | Yazıcı | Okuyucular | Deprecated | Migrasyon |
|---|---|---|---|---|---|
{sot_matrix_rows}

## 11. Inline-Fix Log (R10)
### F<id>: {title}
- Tarih: {ts}
- Commit: {sha}
- Değişiklik özeti: {diff_summary}
- Test sonucu: PASS
- Drift sonucu: PASS (tolerans 0.01)
- Geri alma: `git revert {sha}`

## 12. User-Decision Bekleyen Bulgular
### F<id>: {title}
- Öncelik: {P0-P3}
- Blast radius: {etkilenen dosya/endpoint/akış sayısı}
- Alternatif A: {desc + artı/eksi}
- Alternatif B: {desc + artı/eksi}
- Önerilen: {A|B} — gerekçe: {reason}

## 13. Cleanup Roadmap
### P0 (öncelik 1)
- [ ] R-P0-1: {title} — efor S/M/L — bağımlılık: yok — risk: {...}

### P1
...
```

### 9.2 Steering Dosyası Üretimi (P-A)

**Aynı script:** SoT matrisinden `.kiro/steering/source-of-truth.md` oluştur.

```markdown
---
inclusion: always
---

# Source of Truth — Gelka Enerji

Bu dosya her agent oturumuna yüklenir. Yeni veri kaynağı yaratmadan/okumadan önce buraya bak.

## Canonical Veri Kaynakları

| Kavram | Canonical Kaynak | Yazıcı | Okuma İzni |
|---|---|---|---|
| YEKDEM (aylık) | `monthly_yekdem_prices.yekdem_tl_per_mwh` | `backend/app/pricing/yekdem_service.py::write_yekdem()` | herkes |
| PTF (saatlik) | `hourly_market_prices.ptf_tl_per_mwh` | `backend/app/epias_service.py::import_excel()` | herkes |
| PTF (aylık ağırlıklı) | hesap: `hourly_market_prices` → ağırlıklı ortalama | `backend/app/pricing/engine.py::calc_weighted_ptf()` | herkes |
| Dağıtım tarifesi | `distribution_tariffs` | `backend/app/services/distribution_tariffs.py` | herkes |
| ... | ... | ... | ... |

## Yasak Davranışlar
- `market_reference_prices`'a YEKDEM yazmayın (deprecated yol)
- Frontend'de hardcoded tarife değeri yazmayın (R6)
- Cache key'de source version olmadan cache yazmayın (R19)

## Yeni Veri Kaynağı Eklemeden Önce
1. Bu dosyada aynı kavram var mı?
2. Varsa canonical kaynağı kullan, yeni tablo/alan açma.
3. Yoksa bu dosyayı güncellemeden tablo yaratma (R20).
```

### 9.3 Invariant Test Generation (P-D)

**Script:** `scripts/06_generate_report.py`'nin son adımı → `backend/tests/test_invariants.py` üretir.

```python
# test_invariants.py (auto-generated by audit)
import pytest
from hypothesis import given, strategies as st

CALC_PAIRS = [
    # Faz 2'den gelen eşleşme listesi
    ("yekdem_inclusive_unit_price", calc_be_v1, calc_fe_v1),
    ...
]

@pytest.mark.parametrize("name,be,fe", CALC_PAIRS)
@given(inputs=st.builds(dict, ...))
def test_fe_be_parity(name, be, fe, inputs):
    assert abs(be(**inputs) - fe(**inputs)) <= 0.01, f"{name} drift"

def test_no_new_yekdem_writers():
    """Canonical haricinde YEKDEM yazan kod yok."""
    allowed = {"backend/app/pricing/yekdem_service.py"}
    actual = grep_yekdem_writers()
    assert actual <= allowed, f"Unauthorized YEKDEM writer: {actual - allowed}"

def test_cache_keys_have_version():
    """Tüm cache key'leri source version suffix içerir."""
    for cache in get_all_caches():
        assert has_version_suffix(cache.key_schema), f"{cache.name} missing version"
```

Bu testler CI'da sürekli koşar. Yeni sessiz duplikasyon üretilmeye çalışılırsa PR aşamasında kırmızı bayrak çalar.

## 10. Execution Model

### 10.1 Çalışma Modu

Audit, agent tarafından aşağıdaki sırada interaktif koşturulur:

```
1. agent: "Faz 0 çalıştırılıyor (preflight)..."
   → 00_preflight_schema.py
   → 00_preflight_baseline.py
   → Durum raporu → user

2. IF schema drift: user onayı iste (devam mı, fix mi?)

3. agent: "Faz 1 envanter..."
   → 6 script paralel çalıştır
   → Özet → user

4. agent: "Faz 2 eşleştirme..."
   → 3 script
   → Özet → user

5. agent: "Faz 3 drift tarama..."
   → 5 script
   → Ham bulgu sayısı → user

6. agent: "Faz 4 SoT ataması..."
   → Niyet analizi + SoT matrisi
   → Belirsizler için user-decision soruları (interaktif)

7. agent: "Faz 5 sınıflandırma..."
   → Otomatik sınıflandırma
   → Inline-fix adaylarını listele
   → user onayı: "X inline-fix uygulanacak, onay?"
   → Uygula + drift test

8. agent: "Faz 6 rapor üretimi..."
   → audit-report.md
   → source-of-truth.md
   → test_invariants.py
   → Kullanıcıya özet mesaj
```

### 10.2 Kesinti/Yeniden Başlama

Her fazın çıktısı `artifacts/phase<N>_*.json` dosyasına yazılır. Audit yarıda kesilirse sonraki koşu:

```python
if Path(f"artifacts/phase{N}_*.json").exists():
    print(f"Phase {N} cached, skipping. Delete to rerun.")
    load_from_cache()
else:
    run_phase(N)
```

### 10.3 Acil Durum Exit (P-C)

Herhangi bir faz sırasında P0 bulgu (yanlış fiyat üreten aktif duplikasyon) tespit edilirse:
1. Audit duraklar.
2. Bulgu detayıyla kullanıcıya yazılır.
3. Kullanıcı onayıyla inline-fix veya user-decision fix uygulanır.
4. Baseline güncellenir (yeni "post-fix" baseline).
5. Audit kaldığı fazdan devam eder.

### 10.4 Güvenlik Kontrolleri (R16)

- `_common.py` içinde SQL guard: sadece `SELECT`, `PRAGMA`, `EXPLAIN` izinli. `DROP`, `DELETE`, `TRUNCATE`, `ALTER` çağrısı exception atar.
- `.env` veya credential dosyaları raporda ad düzeyinde referans edilir, içerik ASLA.
- Canlı API çağrıları: sadece GET (idempotent) default. POST/PUT/DELETE için explicit user onayı.

## 11. Başarı Kriterleri (DoD — R18)

Audit "tamamlandı" denilebilmesi için:

- [ ] Faz 0-6 tamamlandı, artifacts/ altında her faz için JSON mevcut
- [ ] audit-report.md oluştu, 13 bölüm dolu
- [ ] Metadata bloğu (tarih, sha, sayımlar) dolu
- [ ] Her denetim alanı (R2-R9) için en az bir kanıtlı bulgu VEYA "bu alanda bulgu yok" teyidi
- [ ] Inline-fix log bölümü mevcut (0 bile olsa)
- [ ] Cleanup roadmap en az 1 P0 + 1 P1 madde içeriyor VEYA yoksa gerekçe yazıldı
- [ ] Drift testi PASS
- [ ] source-of-truth.md yazıldı
- [ ] test_invariants.py yazıldı ve CI'da yeşil
- [ ] Kullanıcıya özet mesaj gönderildi

## 12. Kapsam Sınırı (P-B)

### Derin denetim (tüm R1-R23 uygulanır):
- `backend/app/pricing/**`
- `backend/app/invoice/**` (canlı yol: `extractor.py`, `calculator.py`)
- `backend/app/epias_*`
- `backend/app/services/distribution_tariffs*`
- `backend/app/market_prices*`, `backend/app/admin/market_prices*`
- `frontend/src/App.tsx` (ilgili hesap blokları)
- `frontend/src/market-prices/**`
- `frontend/src/api.ts` (fiyatlama endpoint'leri)
- İlgili DB tabloları: `market_reference_prices`, `monthly_yekdem_prices`, `hourly_market_prices`, `distribution_tariffs`, `analysis_cache`, `offers`, `invoices`

### Yüzeysel denetim (sadece binary "canlı mı?" check):
- `backend/app/testing/**`
- `backend/app/adaptive_control/**`
- `backend/app/guards/**`
- `backend/app/invoice/validation/**` (canonical_extractor dışı)
- 38 spec'in governance/SLO/chaos/telemetry olanları

### Kapsam dışı:
- Test coverage ölçümü
- Performance tuning
- UI/UX önerileri
- İş kuralı doğruluğu (KDV oranı doğru mu? değil, "KDV iki yerde aynı mı?")

## 13. Riskler ve Azaltmalar

| Risk | Etki | Azaltma |
|---|---|---|
| Faz 3.3 drift testi FE/BE farklı dilde → replika hatası | P1 yanlış negatif | Kritik çiftlerde Yol A (Node subprocess) |
| Git log arkeolojisi (R20) belirsiz sonuç | SoT kararı user'a düşer | Zaten user-decision |
| Canlı API çağrıları backend çalışmıyor olabilir | Faz 2.2 eksik kalır | Opsiyonel, yok sayılıp grep'le devam |
| Inline-fix sonrası gizli drift | P0 | Her fix sonrası mutlaka drift test |
| Baseline alınmadan cleanup | Regresyon | R22 DoD — baseline yoksa cleanup bloke |
| Audit süresi > 60 dk | Kullanıcı sabrı | Faz bazlı cache + yeniden başlama |

---

**Tasarım tamamlandı.** Sonraki adım: `tasks.md` yazımı. Tasks, bu design'daki 7 fazı 20-30 somut `[ ]` göreve çevirir; her task belirli bir script/artefact üretimine denk düşer.


---

# DESIGN REVIZYON v2 — Smoke Test Bulgular Sonrası

Smoke test (`01_inventory_db.py` v1 + `_probe_ptf_coverage.py`) 5 script hatası + 1 P0 mimari bulgu (F-PTF) ortaya çıkardı. Bu bölüm design v1'i **eklemelerle** günceller; v1 iptal değil, genişleme.

## 14. F-PTF — Baseline P0 Bulgusu (R24 kanıtlı örneği)

### 14.1 Kanıt Özeti

**Kod kanıtı:**
- `backend/app/pricing/router.py:175-188` `_load_market_records()` SADECE `HourlyMarketPrice` sorguluyor.
- `backend/app/pricing/router.py:474-480` `if not market_records: raise HTTPException(404, "market_data_not_found")` — sessiz fallback YOK.
- `backend/app/market_prices.py:74-82` `get_market_prices()` `MarketReferencePrice`'ı okuyor — manuel mod / admin EPİAŞ fiyat bakma yolu buradan geliyor.
- `backend/app/pricing/yekdem_service.py:118-152` YEKDEM için fallback MEVCUT (önceki oturum çözümü) ama PTF için yok.

**DB kanıtı:**
- `hourly_market_prices`: 4 dönem (2026-01..2026-04), 2880 satır.
- `market_reference_prices`: 60 dönem (2022-01..2026-12), aylık PTF+YEKDEM.
- Kesişim: 4 dönem. `market_reference_prices − hourly_market_prices`: **56 dönem** — sadece manuel modda görünür.

**API kanıtı (canlı):**
- `POST /api/pricing/analyze {period:"2025-12"}` → 404 `market_data_not_found` ✓
- `POST /api/pricing/analyze {period:"2024-06"}` → 404 `market_data_not_found` ✓
- Risk engine deterministik; sessiz fallback yok. **Ama manuel mod aynı dönemde (örn. 2025-12) aylık PTF ile hesap yapıp teklif'e doğru akıtıyor.**

### 14.2 Karar — Hybrid-C Policy (R26)

| Durum | Karar | offer_allowed | pdf_allowed | model_used |
|---|---|---|---|---|
| Saatlik PTF mevcut | Teklif üretilir | true | true | `hourly_canonical` |
| Saatlik yok, aylık referans var | Read-only preview | false | false | `monthly_reference_only` |
| Hiç PTF yok | Tüm işlem reddi | false | false | `none` |

Bu karar requirements.md R26'da bağlayıcı hale getirildi.

### 14.3 F-PTF Cleanup Roadmap Maddesi (önceden sabitlenmiş)

```
P0-M1: Manuel mod PTF okuma yolunu değiştir
  - frontend/src/App.tsx::liveCalculation: market_reference_prices.ptf → bloke
  - /api/epias/prices/{period} response'u değişmeli: fallback_mode flag
  - Teklif butonu ve PDF butonu fallback_mode=true iken disabled
  - Tolerans: 0.01 TL
  
P0-M2: Backend SoT guard
  - test_invariants.py::test_no_manual_ptf_fallback_in_offer_flow
  - Grep guard: offers/invoices create path'inde market_reference_prices.ptf OKUNAMAZ
  
P0-M3: Response schema genişlemesi
  - Tüm fiyat endpoint'leri fallback_mode + model_used döndürsün
  - Schema migration değil, additive JSON field
```

**Sıralama:** audit-report.md'nin ilk bulgusu F-PTF olacak. Roadmap bu 3 madde ile açılacak.

## 15. Script Düzeltmeleri — `01_inventory_db.py` v2

v1'de 5 hata vardı (kullanıcı teyidi):

### 15.1 Regex suffix tabanlı

**Eski:**
```python
"ptf": [r"\bptf\b"]   # ptf_tl_per_mwh yakalamıyor (word boundary bug)
```

**Yeni:**
```python
"ptf": [r"\bptf_tl_per_", r"weighted_ptf", r"^ptf$", r"_ptf_tl_"],
"yekdem": [r"yekdem_tl_per_", r"^yekdem$", r"_yekdem_tl_"],
"dist_tariff_price": [r"dist.*_tl_per_kwh", r"distribution_unit_price"],
"retail_tariff_price": [r"retail_.*_tl_per_", r"unit_price_tl_per_"],
```

Kural: Fiyat domain'leri **sadece numeric suffix'li kolonlarda** tetiklenir.

### 15.2 Snapshot/History tablo kategorisi

```python
SNAPSHOT_TABLES = {
    # Bu tablolar bir "olay" anındaki fiyat durumunu DONDURUR.
    # Duplikasyon taramasında MUAF.
    "offers": "price_snapshot",             # teklif anındaki fiyatlar
    "invoices": "invoice_data_snapshot",    # fatura verisinin kaydı
    "price_change_history": "audit_trail",  # fiyat değişim izi
    "consumption_hourly_data": "time_series_data",
    "audit_logs": "audit_trail",
}
```

Sessiz duplikasyon analizi bu tabloların kolonlarını farklı kaynak olarak saymaz — sadece "snapshot kaydı" olarak raporlar.

### 15.3 Type-aware domain matching

```python
NUMERIC_TYPES = {"FLOAT", "NUMERIC", "DECIMAL", "REAL", "DOUBLE"}

def match_domains(col_name: str, col_type: str, col_name_suffix: str) -> list[str]:
    # Fiyat domain'leri sadece numeric tiplerde eşleşir
    is_numeric = col_type.upper() in NUMERIC_TYPES
    price_domains = {"ptf", "yekdem", "dist_tariff_price", "retail_tariff_price", "btv", "kdv"}
    # ...
    # String kolonlar (tariff_group, price_type gibi) fiyat domain'ine giremez
```

### 15.4 Granüler invoice domain'i

```python
"invoice_master":       [r"^invoices$"],               # sadece invoices tablosu
"invoice_reference_fk": [r"^invoice_id$"],             # FK kolonlar
"invoice_period_bucket":[r"^invoice_period$"],         # period alias
# Eski tek "invoice" domain'i KALDIRILDI
```

### 15.5 Cross-source period coverage matrisi

Mevcut `period_coverage` tek tablo için dönem listesi veriyordu. Yeni:

```python
def cross_source_period_diff(inv: dict) -> list[dict]:
    """Aynı domain'i birden fazla tabloda taşıyan kaynaklar arası dönem diff."""
    diffs = []
    for domain in ("ptf", "yekdem"):
        sources = [t for t in inv["tables"] if domain in t["domains_hit"]]
        if len(sources) < 2:
            continue
        all_periods = set()
        per_source_periods = {}
        for s in sources:
            periods = {p["period"] for p in (s["period_coverage"] or [])}
            per_source_periods[s["name"]] = periods
            all_periods |= periods
        # Her kaynak için: bu kaynakta yok ama diğerlerinde var olan dönemler
        for name, periods in per_source_periods.items():
            missing = all_periods - periods
            if missing:
                diffs.append({
                    "domain": domain,
                    "source": name,
                    "missing_periods": sorted(missing),
                    "present_in_other_sources": sorted(missing),
                })
    return diffs
```

### 15.6 F-PTF auto-flag

Script, `ptf` domain'inde >1 canonical-aday kaynak tespit ederse **otomatik olarak** F-PTF kanıt bloğu üretir ve bunu P0 işaretler. Bu yeni script'in kendisi baseline bulgu üretir.

## 16. Tablo Rol Etiketleri (yeni kavram)

v1'de her tablo eşit sayılıyordu; artık rol bazlı:

| Rol | Açıklama | Duplikasyon taramasında |
|---|---|---|
| `canonical_source` | SoT veri (örn. hourly_market_prices) | Dahil |
| `derived_view` | Başka kaynaktan hesaplanmış (future: monthly_avg_view) | Dahil, uyarı |
| `snapshot` | Bir olayın anlık kopyası (offers, invoices) | **Muaf** |
| `audit_trail` | Değişim izi (price_change_history, audit_logs) | **Muaf** |
| `legacy_deprecated` | Eski yol, migrasyon bekliyor (market_reference_prices) | Dahil, flag'li |
| `config` | Değişmeyen konfigürasyon (distribution_tariffs) | Dahil |
| `cache` | Hesap cache'i (analysis_cache) | Ayrı kontrol (R19) |

Script bu rolleri **heuristic + elle override** ile atar; elle override dosyası: `.kiro/specs/codebase-audit-cleanup/scripts/table_roles.json`.

## 17. Design v2 — Değişmeyen Kısımlar

Aşağıdaki bölümler v1'den aynen korunur: §1 faz haritası, §2 dizin yapısı, §3 Faz 0, §4.2-4.6 envanter script'leri, §5 Faz 2, §6 Faz 3, §7 Faz 4, §8 Faz 5, §9 Faz 6, §10 execution model, §11 DoD, §12 kapsam, §13 risk tablosu.

**Sadece §4.1 (DB inventory) scripti v2 ile değiştirildi; §3 Faz 0'a F-PTF baseline bulgusu eklendi; §7/§8'de R24-R26 entegrasyonu yer alıyor.**
