# Invoice Validation — Faz A / Task 4.1 Design

## 1. Scope / Non-scope

### 4.1 Scope (bu doküman)
- `ValidationErrorCode` enum (kapalı küme, genişletilebilir)
- `InvoiceValidationError`, `InvoiceValidationResult` data contracts
- `validate(invoice: dict, supplier: str | None = None) -> InvoiceValidationResult` tek fonksiyon arayüzü
- ETTN doğrulama (UUID-like regex)
- T1/T2/T3 period tutarlılık kontrolü
- Reaktif ceza bidirectional mismatch kontrolü
- Fixture formatı (`{supplier}_{scenario}.json`) ve fixture-driven testler
- Minimal PBT (3 property)
- Regression checkpoint (mevcut ~490 test yeşil kalır)

### 4.2+ Non-scope (ertelenen)
- Supplier-specific normalizasyon katmanı
- `NormalizedInvoice` typed model (şimdilik `dict | None`)
- Supplier-specific ETTN varyantları / override (`supplier` parametresi 4.2'de aktif olur)
- `UNSUPPORTED_SUPPLIER` validator kuralı (enum'da tanımlı, kullanılmıyor)
- kwh/amount precision / rounding kontrolü
- Tam PBT suite (6.1/6.2)
- Yeni validator'ın canonical path'e kademeli geçişi (feature flag / config)

---

## 2. Dosya / Modül Yapısı

```
backend/app/invoice/__init__.py
backend/app/invoice/validation/__init__.py
backend/app/invoice/validation/error_codes.py    # ValidationErrorCode enum
backend/app/invoice/validation/types.py           # InvoiceValidationError, InvoiceValidationResult, ValidationSeverity
backend/app/invoice/validation/validator.py        # validate() fonksiyonu

backend/tests/fixtures/invoices/validation/       # 4.1 fixture'ları (mevcut enerjisa/ vb. ile karışmaz)
    enerjisa_t1t2t3_ok.json
    enerjisa_missing_ettn.json
    enerjisa_invalid_ettn.json
    enerjisa_inconsistent_periods.json
    enerjisa_reactive_mismatch.json
    enerjisa_negative_values.json

backend/tests/test_invoice_validator_fixtures.py   # Fixture-driven testler
backend/tests/test_invoice_validator_pbt.py        # Minimal PBT (3 property)
```

Not: Mevcut `backend/app/validator.py` ve `backend/app/models.py:ValidationResult` ile çakışma yok.
Yeni modül `backend/app/invoice/validation/` namespace'inde yaşar. Mevcut `ValidationResult` (models.py)
extraction pipeline'a ait; yeni tipler `InvoiceValidationResult` / `InvoiceValidationError` olarak adlandırılır —
import sırasında kafa karışıklığı sıfır.

### Fixture Loader Convention

- Fixture root: `backend/tests/fixtures/`
- Validation fixtures: `invoices/validation/*.json` (glob pattern)
- Supplier/OCR fixtures: mevcut klasörler (`invoices/enerjisa/`, `invoices/ck_bogazici/` vb.) — dokunulmaz
- Test runner'da `FIXTURE_DIR = Path(__file__).parent / "fixtures" / "invoices" / "validation"` kullanılır
- CI'da relative path sorunu olmaz: pytest cwd = repo root, fixture path test dosyasına göre resolve edilir

---

## 3. Data Contracts

### 3.1 ValidationSeverity

```python
from typing import Literal

ValidationSeverity = Literal["ERROR", "WARN"]
```

### 3.2 ValidationErrorCode (kapalı küme)

```python
from enum import Enum

class ValidationErrorCode(str, Enum):
    MISSING_FIELD = "MISSING_FIELD"
    INVALID_FORMAT = "INVALID_FORMAT"
    INVALID_ETTN = "INVALID_ETTN"
    INVALID_DATETIME = "INVALID_DATETIME"
    INCONSISTENT_PERIODS = "INCONSISTENT_PERIODS"
    NEGATIVE_VALUE = "NEGATIVE_VALUE"
    REACTIVE_PENALTY_MISMATCH = "REACTIVE_PENALTY_MISMATCH"
    UNSUPPORTED_SUPPLIER = "UNSUPPORTED_SUPPLIER"  # 4.1'de kullanılmaz
```

`str, Enum` mixin: JSON serialization doğal çalışır, response model'de `.value` gerekmez.

### 3.3 InvoiceValidationError

```python
@dataclass(frozen=True)
class InvoiceValidationError:
    code: ValidationErrorCode
    field: str                          # dot-notation: "ettn", "periods.t2.start", "reactive.penalty_amount"
    message: str                        # insan okunur, non-breaking
    severity: ValidationSeverity = "ERROR"

    def to_dict(self) -> dict:
        """JSON-serializable dict. Response model ve test assertion'larında kullanılır."""
        return {
            "code": self.code.value,    # enum → string
            "field": self.field,
            "message": self.message,
            "severity": self.severity,  # Literal zaten string
        }
```

`frozen=True`: immutable, set/dict key olarak kullanılabilir, test assert'lerinde güvenli.
`to_dict()`: `dataclasses.asdict()` enum'ları otomatik çevirmez; bu wrapper garantili JSON-safe output verir.

### 3.4 InvoiceValidationResult

```python
@dataclass
class InvoiceValidationResult:
    valid: bool
    errors: list[InvoiceValidationError]
    normalized: dict | None = None      # 4.1'de None; 4.2'de NormalizedInvoice'a evrilir

    def to_dict(self) -> dict:
        """JSON-serializable dict. Response model'e beslenebilir."""
        return {
            "valid": self.valid,
            "errors": [e.to_dict() for e in self.errors],
            "normalized": self.normalized,
        }
```

Forward alias (opsiyonel, 4.2 geçişi kolaylaştırır):
```python
NormalizedInvoice = dict  # 4.2'de gerçek typed model'e dönüşür
```

### 3.5 Validator Arayüzü

```python
def validate(invoice: dict, supplier: str | None = None) -> InvoiceValidationResult:
    """
    Kanonik fatura dict'ini doğrular.
    
    Args:
        invoice: Fixture formatındaki fatura dict'i (meta hariç, sadece invoice bölümü)
        supplier: Opsiyonel supplier kodu. 4.1'de kullanılmaz; 4.2'de supplier-specific
                  kurallar ve UNSUPPORTED_SUPPLIER kontrolü için aktif olur.
    
    Returns:
        InvoiceValidationResult: valid=True ise errors boş; valid=False ise en az 1 error var.
    
    Invariant:
        result.valid == (len(result.errors) == 0)
    """
```

### 3.6 Kapalı Küme Enforcement

`ValidationErrorCode` ve `ValidationSeverity` dışında serbest string kabul edilmez.
Bu kural hem runtime'da hem test'te enforce edilir:

- **Runtime**: `InvoiceValidationError.__init__` sadece `ValidationErrorCode` enum member kabul eder
  (frozen dataclass + type annotation; mypy/pyright strict mode'da catch edilir)
- **Test**: Fixture `expected.errors[].code` değerleri `ValidationErrorCode` enum value'larıyla
  birebir eşleşmeli. Fixture loader'da assertion:
  ```python
  assert all(e["code"] in ValidationErrorCode._value2member_map_ for e in expected_errors)
  ```
- **Lint**: Yeni error code eklemek için enum'a member eklemek zorunlu; serbest string geçmez

---

## 4. Fixture Contract

### 4.1 Format

`backend/tests/fixtures/invoices/validation/{supplier}_{scenario}.json`:

```json
{
  "meta": {
    "supplier": "enerjisa",
    "scenario": "t1t2t3_ok",
    "currency": "TRY",
    "timezone": "Europe/Istanbul"
  },
  "invoice": {
    "ettn": "550e8400-e29b-41d4-a716-446655440000",
    "periods": [
      {"code": "T1", "start": "2026-01-01", "end": "2026-01-31", "kwh": 1200, "amount": 360.00},
      {"code": "T2", "start": "2026-01-01", "end": "2026-01-31", "kwh": 800, "amount": 200.00},
      {"code": "T3", "start": "2026-01-01", "end": "2026-01-31", "kwh": 400, "amount": 80.00}
    ],
    "reactive": {
      "penalty_amount": 0,
      "penalty_kvarh": 0
    }
  },
  "expected": {
    "valid": true,
    "errors": []
  }
}
```

### 4.2 expected.errors[] item formatı

```json
{
  "code": "MISSING_FIELD",
  "field": "ettn"
}
```

`message` opsiyonel — assert'lerde `code` + `field` yeterli.

### 4.3 periods canonical modeli

**Karar: list + code alanı**

```
periods: list[{"code": str, "start": str, "end": str, "kwh": number, "amount": number}]
```

- Fixture convention ile birebir uyumlu
- İleride T4/T5 eklenmesi doğal
- Validator'da set kontrolü: `{p["code"] for p in periods}`

---

## 5. Validation Rules (4.1 minimum)

### 5.1 ETTN Doğrulama

| Sıra | Koşul | Error Code | Field |
|------|-------|-----------|-------|
| 1 | `ettn` key yok veya `None`/boş string | `MISSING_FIELD` | `ettn` |
| 2 | `ettn` string değil | `INVALID_FORMAT` | `ettn` |
| 3 | UUID-like regex match değil | `INVALID_ETTN` | `ettn` |

Regex (case-insensitive):
```
^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$
```

Kural sırası önemli: ilk match eden hata döner (ETTN için tek hata yeterli, early return).

### 5.2 Periods (T1/T2/T3) Tutarlılık

| Sıra | Koşul | Error Code | Field |
|------|-------|-----------|-------|
| 1 | `periods` key yok veya boş list | `MISSING_FIELD` | `periods` |
| 2 | T1, T2, T3 code'larından biri eksik | `MISSING_FIELD` | `periods.codes` |
| 3 | Herhangi bir period'da `start`/`end` parse edilemiyor | `INVALID_DATETIME` | `periods.{code}.start` veya `periods.{code}.end` |
| 4 | T1/T2/T3 start değerleri birbirine eşit değil VEYA end değerleri birbirine eşit değil | `INCONSISTENT_PERIODS` | `periods` |
| 5 | Herhangi bir period'da `kwh`/`amount` number değil | `INVALID_FORMAT` | `periods.{code}.kwh` veya `periods.{code}.amount` |
| 6 | Herhangi bir period'da `kwh`/`amount` negatif | `NEGATIVE_VALUE` | `periods.{code}.kwh` veya `periods.{code}.amount` |

Tarih parse formatı: `YYYY-MM-DD` (ISO 8601 date-only). `datetime.date.fromisoformat()` kullanılır.

### 5.3 Reactive Ceza (Bidirectional)

| Sıra | Koşul | Error Code | Field |
|------|-------|-----------|-------|
| 1 | `reactive` key yok → skip (opsiyonel bölüm) | — | — |
| 2 | `penalty_amount` var ama `penalty_kvarh` yok (veya tersi) | `MISSING_FIELD` | `reactive.penalty_amount` veya `reactive.penalty_kvarh` |
| 3 | `penalty_amount`/`penalty_kvarh` number değil | `INVALID_FORMAT` | `reactive.penalty_amount` veya `reactive.penalty_kvarh` |
| 4 | Negatif değer | `NEGATIVE_VALUE` | `reactive.penalty_amount` veya `reactive.penalty_kvarh` |
| 5 | `penalty_amount > 0` ve `penalty_kvarh <= 0` | `REACTIVE_PENALTY_MISMATCH` | `reactive` |
| 6 | `penalty_kvarh > 0` ve `penalty_amount <= 0` | `REACTIVE_PENALTY_MISMATCH` | `reactive` |

---

## 6. Tests

### 6.1 Fixture-Driven Tests (`test_invoice_validator_fixtures.py`)

```python
# Pseudocode
@pytest.mark.parametrize("fixture_path", glob("backend/tests/fixtures/invoices/validation/*.json"))
def test_fixture(fixture_path):
    data = json.load(fixture_path)
    
    # Kapalı küme enforcement: fixture code'ları enum'da olmalı
    for err in data["expected"]["errors"]:
        assert err["code"] in ValidationErrorCode._value2member_map_
    
    result = validate(data["invoice"])
    assert result.valid == data["expected"]["valid"]
    
    actual_codes = {(e.code.value, e.field) for e in result.errors}
    expected_codes = {(e["code"], e["field"]) for e in data["expected"]["errors"]}
    assert actual_codes == expected_codes
```

Minimum fixture seti (6 dosya):
1. `enerjisa_t1t2t3_ok.json` — happy path, valid=true
2. `enerjisa_missing_ettn.json` — ettn eksik, MISSING_FIELD
3. `enerjisa_invalid_ettn.json` — ettn format bozuk, INVALID_ETTN
4. `enerjisa_inconsistent_periods.json` — T1/T2 farklı tarih, INCONSISTENT_PERIODS
5. `enerjisa_reactive_mismatch.json` — amount>0 kvarh=0, REACTIVE_PENALTY_MISMATCH
6. `enerjisa_negative_values.json` — negatif kwh, NEGATIVE_VALUE

### 6.2 Minimal PBT (`test_invoice_validator_pbt.py`)

**P1 — Invalid ETTN always detected:**
```
∀ ettn ∈ {None, "", int, malformed_string}:
    result = validate(invoice_with(ettn=ettn))
    assert result.valid == False
    assert any(e.code in {MISSING_FIELD, INVALID_FORMAT, INVALID_ETTN} for e in result.errors)
```

**P2 — Inconsistent periods always detected:**
```
∀ (t1_start, t2_start, t3_start) where not all equal:
    result = validate(invoice_with(periods=make_periods(t1_start, t2_start, t3_start)))
    assert INCONSISTENT_PERIODS in {e.code for e in result.errors}
```

**P3 — Reactive mismatch always detected:**
```
∀ (amount, kvarh) where (amount > 0 and kvarh <= 0) or (kvarh > 0 and amount <= 0):
    result = validate(invoice_with(reactive={"penalty_amount": amount, "penalty_kvarh": kvarh}))
    assert REACTIVE_PENALTY_MISMATCH in {e.code for e in result.errors}
```

Hypothesis strategies:
- ETTN: `st.one_of(st.none(), st.just(""), st.integers(), st.text().filter(not_uuid))`
- Dates: `st.dates()` ile farklı start değerleri üret
- Reactive: `st.floats(min_value=0.01)` ve `st.floats(max_value=0)` kombinasyonları

### 6.3 Regression Checkpoint

Faz A milestone kriteri:
- Mevcut test suite (~490 test) yeşil kalır — yeni modül izole, mevcut koda dokunmaz
- Yeni fixture testleri (6 test) yeşil
- Yeni PBT (3 property) yeşil
- Toplam: ~499 test yeşil

---

## 7. Rollout / Gate Mapping

### Gate #1 kapanış kriteri (Faz A)
- [ ] `ValidationErrorCode` enum tanımlı ve import edilebilir
- [ ] `validate()` fonksiyonu 6 fixture'ı doğru işler
- [ ] 3 PBT property yeşil
- [ ] Mevcut test suite regresyon yok
- [ ] `InvoiceValidationResult.to_dict()` contract 7.2 response model'e beslenebilir durumda

### Gate #2 bağlantısı (4.2 — normalizasyon)
- `UNSUPPORTED_SUPPLIER` aktif olur (`supplier` parametresi kullanılır)
- `normalized` alanı typed `NormalizedInvoice` olur
- Supplier-specific ETTN override'lar eklenir

---

## 8. Migration Stratejisi (İki Validator'ın Birlikte Yaşaması)

Mevcut `CanonicalInvoice.validate()` (string-based) ve yeni `invoice.validation.validate()` (enum-based)
kasıtlı olarak bağımsız yaşar. Prod'da hangisinin authoritative olduğu net olmalı:

| Faz | Mevcut validator | Yeni validator | Authoritative |
|-----|-----------------|----------------|---------------|
| 4.1 (A+B) | Prod'da aktif, değişmez | Sadece test/diagnostic (ETTN+periods+reactive) | Mevcut |
| 4.2 (C) | Prod'da aktif | Kural genişletme: eski 4 kural port edilir, test-only | Mevcut |
| 4.3 (D) | Prod'da aktif | Shadow mode: paralel çalışır, sonuçlar loglanır, karar vermez | Mevcut |
| 4.4+ | Kademeli geçiş (feature flag) | Config ile aktif/pasif | Yeni (flag açıksa) |

- 4.1'de yeni validator hiçbir prod path'e bağlanmaz — sadece test suite'te çalışır
- 4.2'de kural genişletme: eski validator'ın 4 kuralı yeni çerçeveye port edilir, hâlâ test-only
- 4.3'de shadow mode: her iki validator çalışır, sonuçlar karşılaştırılır, divergence loglanır
- 4.4+'da feature flag ile kademeli geçiş; flag kapalıyken mevcut validator authoritative kalır

---

## 9. Kararlar Özeti

| Karar | Seçim | Gerekçe |
|-------|-------|---------|
| periods modeli | `list + code` | Fixture convention uyumu, doğal genişleme |
| severity | `"ERROR" \| "WARN"` şimdiden | Wire format sabit, WARN üretimi 4.2+ |
| ETTN regex | UUID-like `^[0-9a-f]{8}-...` | Supplier-agnostic başlangıç |
| UNSUPPORTED_SUPPLIER | Enum'da var, 4.1'de unused | Supplier bilgisi 4.2'de gelir |
| PBT P1 | MISSING_FIELD dahil | Boş/eksik ETTN de invalid |
| Reactive | Bidirectional check | Her iki yön de mismatch |
| kwh/amount | Number type check only | Precision/rounding 4.2 |
| Tip isimleri | `InvoiceValidationError` / `InvoiceValidationResult` | Mevcut `ValidationResult` ile karışmaz |
| Serileştirme | `to_dict()` method | Enum → string garantili, `asdict()` yetersiz |
| Kapalı küme | Enum + fixture lint | Serbest string kabul edilmez |
| Supplier hook | `supplier: str \| None = None` parametresi | 4.1'de unused, 4.2 signature kırılmaz |
| Migration | 4.1 test-only → 4.2 shadow → 4.3+ feature flag | İki validator çelişmez |
| Fixture loader | `Path(__file__).parent / "fixtures" / ...` | CI relative path güvenli |
| Namespace | `backend/app/invoice/validation/` | Mevcut validator.py ile çakışma yok |
| NormalizedInvoice | `dict` alias (4.1) | 4.2'de typed model'e evrilir |

---

## 10. Phase B — Canonical Fixture Hardening (Gate #2)

### 10.1 Scope

Faz A'daki 6 fixture'a 4 yeni fixture eklenerek toplam ≥10 fixture'a ulaşılır.
Yeni kod değişikliği yok — sadece fixture + test coverage genişletme.
Validator davranışı değişmez; mevcut kuralların edge-case'leri fixture ile kanıtlanır.

### 10.2 Non-scope

- Supplier-specific field mapping (4.2 normalizasyon)
- PDF/OCR ingestion
- `/suppliers` API endpoint (Faz C / 4.2+)
- Ek PBT property (mevcut 3 yeterli)

### 10.3 Yeni Fixture'lar (deduplicated)

| # | Dosya | Senaryo | Expected | Coverage Gap |
|---|-------|---------|----------|-------------|
| 7 | `enerjisa_reactive_consistent_ok.json` | amount>0 AND kvarh>0 (tutarlı) | valid=true | Faz A'da "her ikisi pozitif" yok |
| 8 | `enerjisa_reactive_mismatch_kvarh_only.json` | kvarh>0, amount=0 (ters yön) | valid=false, REACTIVE_PENALTY_MISMATCH | Faz A sadece amount>0 yönü |
| 9 | `enerjisa_bool_as_number.json` | kwh=true (bool) | valid=false, INVALID_FORMAT | `_is_number()` guard kanıtı |
| 10 | `enerjisa_missing_periods.json` | periods key yok | valid=false, MISSING_FIELD | Faz A'da periods hep mevcut |

### 10.4 Gate #2 Kapanış Kriterleri

- [ ] `backend/tests/fixtures/invoices/validation/` altında fixture sayısı ≥ 10
- [ ] Fixture-driven test suite: `expected.valid` birebir, `expected.errors` sadece `(code, field)` eşlemesi
- [ ] Enum kapalı küme enforcement (fixture schema smoke test)
- [ ] CI fast lane hedefleri:
  - `pytest -k test_invoice_fixture_schema`
  - `pytest -k test_invoice_validator_fixtures`
- [ ] Mevcut test suite regresyon yok

### 10.5 Faz C / Faz D — Yol Haritası

- Faz C: Kural Genişletme (eski validator'ın 4 kuralını yeni çerçeveye port) → §11
- Faz D: Shadow Compare (kural kümeleri örtüştükten sonra anlamlı) → §12 (henüz yazılmadı)

---

## 11. Phase C — Kural Genişletme / Port (4.2)

### 11.1 Motivasyon

Eski `CanonicalInvoice.validate()` ile yeni `invoice.validation.validate()` kural kümeleri
tamamen disjoint (∅ kesişim). Shadow compare bu durumda anlamsız mismatch counter şişirir.
Önce kural kümelerini örtüştürmek gerekiyor.

Eski validator'ın 4 kuralı prod risk açısından gerçek "hardening" değeri taşıyor:
- Payable ≈ Total (ödeme tutarı doğruluğu)
- Lines + Taxes + VAT ≈ Total (kalem toplamı tutarlılığı)
- Zero Consumption (sıfır tüketim tespiti)
- Line Crosscheck (qty × unit_price ≈ amount)

### 11.2 Eski Validator Sözleşmesi (Referans)

```
input:   CanonicalInvoice (dataclass) — self method, parametre almaz
         Gerekli alanlar: lines (list[InvoiceLine]), taxes (TaxBreakdown),
         vat (VATInfo), totals (Totals)
output:  list[str] — hata mesajları, ayrıca self.errors'a extend eder
         is_valid() → len(self.errors) == 0
errors:  4 kural, her biri f-string:
         1. "PAYABLE_TOTAL_MISMATCH: payable=X, total=Y"
         2. "TOTAL_MISMATCH: calculated=X, extracted=Y, diff=Z"
         3. "ZERO_CONSUMPTION: total_kwh <= 0"
         4. "LINE_CROSSCHECK_FAIL: label - qty=X, price=Y, amount=Z"
```

### 11.3 Yeni Enum Üyeleri (4 adet)

`ValidationErrorCode`'a eklenir — eski string prefix'leriyle birebir aynı isim:

```python
class ValidationErrorCode(str, Enum):
    # ... mevcut 8 üye ...
    PAYABLE_TOTAL_MISMATCH = "PAYABLE_TOTAL_MISMATCH"
    TOTAL_MISMATCH = "TOTAL_MISMATCH"
    ZERO_CONSUMPTION = "ZERO_CONSUMPTION"
    LINE_CROSSCHECK_FAIL = "LINE_CROSSCHECK_FAIL"
```

Toplam: 12 üye (8 mevcut + 4 yeni). Migration/trace kolay: eski string prefix = yeni enum value.

### 11.4 Input Contract Genişletme (Compat Layer)

`validate(invoice: dict, supplier=None)` imzası değişmez.
`invoice` dict'ine opsiyonel alanlar eklenir:

```python
# Opsiyonel — yoksa eski 4 kural SKIP edilir (hata üretmez)
{
    "totals": {
        "total": number,          # Genel toplam (KDV dahil)
        "payable": number         # Ödenecek tutar
    },
    "lines": [
        {
            "label": str,         # Kalem etiketi (debug)
            "qty_kwh": number,    # Miktar (kWh)
            "unit_price": number, # Birim fiyat
            "amount": number      # Tutar
        }
    ],
    "taxes_total": number,        # Vergi/fon toplamı
    "vat_amount": number          # KDV tutarı
}
```

Geriye uyumluluk garantisi: bu alanlar yoksa eski 4 kural sessizce skip edilir.
Faz A/B fixture'ları kırılmaz — onlarda `totals`/`lines` yok, sadece `ettn`/`periods`/`reactive` var.

### 11.5 Validation Rules (Faz C — port edilen 4 kural)

#### 11.5.1 PAYABLE_TOTAL_MISMATCH

| Koşul | Error Code | Field |
|-------|-----------|-------|
| `totals` yok veya `total`/`payable` yok → skip | — | — |
| `total`/`payable` number değil → skip (INVALID_FORMAT üretmez, compat) | — | — |
| `abs(payable - total) > PAYABLE_TOLERANCE` | `PAYABLE_TOTAL_MISMATCH` | `totals` |

Tolerans: `PAYABLE_TOLERANCE = 5.0` (TL) — eski validator ile birebir aynı (`approx(a, b, tol=5.0)`).

#### 11.5.2 TOTAL_MISMATCH

| Koşul | Error Code | Field |
|-------|-----------|-------|
| `totals.total` yok → skip | — | — |
| `lines` yok veya boş → skip | — | — |
| `calculated = sum(line.amount) + taxes_total + vat_amount` | — | — |
| `tol = max(5.0, total * 0.01)` | — | — |
| `abs(calculated - total) > tol` | `TOTAL_MISMATCH` | `totals.total` |

Tolerans: `max(5.0 TL, %1)` — eski validator ile birebir aynı.

#### 11.5.3 ZERO_CONSUMPTION

| Koşul | Error Code | Field |
|-------|-----------|-------|
| `lines` yok veya boş → skip | — | — |
| Hiçbir line'da `qty_kwh` (number) yoksa → skip (veri yoksa hüküm yok) | — | — |
| `consumption_kwh = sum(line["qty_kwh"] for line in lines if _is_number(line.get("qty_kwh")))` | — | — |
| `consumption_kwh <= 0` | `ZERO_CONSUMPTION` | `lines` |

Phase C semantiği: `line_code` alanı opsiyonel, yoksa tüm `qty_kwh` değerleri toplanır.
Phase D+'da `line_code` varsa ve supplier profile energy kodlarını biliyorsa → sadece energy kodları filtrelenir.
Bu, Faz D shadow compare'de mismatch üretirse rollback planı olarak `line_code` filtresi eklenir.

#### 11.5.4 LINE_CROSSCHECK_FAIL

Eski validator'daki `InvoiceLine.crosscheck()` birebir port:

```python
# Eski: calculated = qty_kwh × unit_price; delta = |calculated - amount| / |amount|
# Yeni: aynı formül, dict üzerinden
```

| Koşul | Error Code | Field |
|-------|-----------|-------|
| `lines` yok veya boş → skip | — | — |
| Her line için: `qty_kwh`, `unit_price`, `amount` üçünden biri number değilse → skip (o line) | — | — |
| `amount == 0` → skip (o line) | — | — |
| `calculated = qty_kwh * unit_price` | — | — |
| `delta = abs((calculated - amount) / amount)` | — | — |
| `delta > 0.02` | `LINE_CROSSCHECK_FAIL` | `lines[{index}]` |

Tolerans: `LINE_CROSSCHECK_TOLERANCE = 0.02` (%2 relatif) — eski `InvoiceLine.crosscheck(tolerance=0.02)` ile birebir aynı.
Üç alandan biri eksik veya amount=0 ise o line skip edilir (eski davranışla aynı: "kontrol yapılamaz → True").

### 11.6 Tolerans Kararları (Kilitli)

| Kural | Tolerans | Kaynak | Gerekçe |
|-------|----------|--------|---------|
| PAYABLE_TOTAL_MISMATCH | `abs(a-b) <= 5.0` TL | Eski `approx(a, b, tol=5.0)` | Birebir port, shadow compare'de eşleşme garantisi |
| TOTAL_MISMATCH | `abs(a-b) <= max(5.0, total*0.01)` | Eski validator kural 2 | Hibrit: mutlak 5 TL veya %1 (hangisi büyükse) |
| ZERO_CONSUMPTION | `total_kwh <= 0` | Eski validator kural 3 | Boolean, tolerans yok |
| LINE_CROSSCHECK | `abs(delta/amount) <= 0.02` | Eski `InvoiceLine.crosscheck(tolerance=0.02)` | %2 relatif |

Neden eski toleransları birebir port ediyoruz: Faz D'de shadow compare yapılacak.
Toleranslar farklı olursa valid mismatch alarmı yanlış pozitif üretir.
Tolerans değişikliği ancak Faz D sonrası kalibrasyon verisiyle yapılır.

### 11.7 Fixture Set (Faz C — yeni klasör)

Faz A/B fixture'ları `invoices/validation/` altında kalır (ettn/periods/reactive).
Faz C fixture'ları ayrı klasörde: `invoices/validation_totals/` — karışma riski sıfır.

| # | Dosya | Senaryo | Expected |
|---|-------|---------|----------|
| 1 | `totals_ok.json` | Tutarlı totals + lines | valid=true |
| 2 | `payable_total_mismatch.json` | payable-total farkı > 5 TL | PAYABLE_TOTAL_MISMATCH |
| 3 | `total_mismatch.json` | lines+taxes+vat ≠ total | TOTAL_MISMATCH |
| 4 | `zero_consumption.json` | Tüm line'larda qty_kwh=0 | ZERO_CONSUMPTION |
| 5 | `line_crosscheck_fail.json` | qty×price ≠ amount (%2+) | LINE_CROSSCHECK_FAIL |
| 6 | `missing_totals_skips.json` | totals/lines yok, ama ettn/periods valid | valid=true (eski kurallar skip) |

Fixture format (totals family):

```json
{
  "meta": {
    "supplier": "enerjisa",
    "scenario": "totals_ok",
    "currency": "TRY",
    "timezone": "Europe/Istanbul"
  },
  "invoice": {
    "ettn": "550e8400-e29b-41d4-a716-446655440000",
    "periods": [
      {"code": "T1", "start": "2026-01-01", "end": "2026-01-31", "kwh": 1200, "amount": 360.00},
      {"code": "T2", "start": "2026-01-01", "end": "2026-01-31", "kwh": 800, "amount": 200.00},
      {"code": "T3", "start": "2026-01-01", "end": "2026-01-31", "kwh": 400, "amount": 80.00}
    ],
    "reactive": {"penalty_amount": 0, "penalty_kvarh": 0},
    "totals": {"total": 1000.00, "payable": 1000.00},
    "lines": [
      {"label": "Enerji Bedeli", "qty_kwh": 2400, "unit_price": 0.30, "amount": 720.00},
      {"label": "Dağıtım Bedeli", "qty_kwh": 2400, "unit_price": 0.05, "amount": 120.00}
    ],
    "taxes_total": 80.00,
    "vat_amount": 80.00
  },
  "expected": {
    "valid": true,
    "errors": []
  }
}
```

### 11.8 Test Dosyası

`backend/tests/test_invoice_validator_totals_fixtures.py` — Faz C fixture'ları için.
Aynı pattern: parametrized + schema smoke. Mevcut `test_invoice_validator_fixtures.py` dokunulmaz.

### 11.9 Non-scope (Faz C)

- Supplier mapping / normalizasyon (Faz D)
- Shadow compare (Faz D — kural kümeleri örtüştükten sonra)
- Prod path'e bağlanma yok
- PDF/OCR ingestion
- `CanonicalInvoice` → dict adaptörü (Faz D)

### 11.10 Faz C Kapanış Kriterleri

- [ ] `ValidationErrorCode` enum: 12 üye (8 mevcut + 4 yeni)
- [ ] `validator.py`: 4 yeni kural fonksiyonu (`_validate_totals`, `_validate_lines`)
- [ ] Opsiyonel alan yoksa eski kurallar skip — Faz A/B fixture'ları kırılmaz
- [ ] `invoices/validation_totals/` altında ≥ 6 fixture
- [ ] `test_invoice_validator_totals_fixtures.py`: parametrized + schema smoke
- [ ] Toleranslar eski validator ile birebir aynı (shadow compare uyumu)
- [ ] Mevcut test suite regresyon yok (Faz A/B testleri dahil)

---

## 12. Phase D — Shadow Compare (4.3)

### 12.1 Purpose

Regression detection: eski `CanonicalInvoice.validate()` ile yeni `invoice.validation.validate()`
sonuçlarını aynı veri üzerinde karşılaştırarak divergence tespit etmek.
Faz C'de kural kümeleri örtüştürüldü — artık shadow compare anlamlı.

### 12.2 Compare Hedefi

```python
valid_match = (old_valid == new_valid)   # hard — tek alarm kaynağı
code_overlap = old_codes & new_codes     # soft — log-only, threshold yok
```

İlk etap: threshold yok, sadece mismatch count + debug dump.
İleride (4.4+): `intersection_ratio >= threshold` gibi baraj eklenebilir.

### 12.3 Adaptör Tasarımı

#### 12.3.1 dict → CanonicalInvoice Builder (test helper)

Fixture dict'inden `CanonicalInvoice` oluşturan helper. Sadece test'te kullanılır.

```python
def build_canonical_invoice(invoice_dict: dict) -> CanonicalInvoice:
    """Fixture dict → CanonicalInvoice (shadow compare için).
    
    Mapping:
      invoice_dict["totals"]["total"]   → totals.total
      invoice_dict["totals"]["payable"] → totals.payable
      invoice_dict["lines"][i]          → InvoiceLine(code=ACTIVE_ENERGY, ...)
      invoice_dict["taxes_total"]       → taxes.other (TaxBreakdown.total'e katkı)
      invoice_dict["vat_amount"]        → vat.amount
    
    line_code varsayımı: tüm line'lara LineCode.ACTIVE_ENERGY atanır.
    Bu, fixture'larımızda energy line'ları olduğu için pratikte doğru.
    Faz E+'da line_code alanı fixture'a eklenirse, bu helper güncellenir.
    """
```

Neden fixture'a alan eklemiyoruz: non-breaking, fixture şişmez, mevcut testler etkilenmez.

#### 12.3.2 old_errors → code set mapper

```python
_OLD_CODE_PREFIX_MAP = {
    "PAYABLE_TOTAL_MISMATCH": ValidationErrorCode.PAYABLE_TOTAL_MISMATCH,
    "TOTAL_MISMATCH": ValidationErrorCode.TOTAL_MISMATCH,
    "ZERO_CONSUMPTION": ValidationErrorCode.ZERO_CONSUMPTION,
    "LINE_CROSSCHECK_FAIL": ValidationErrorCode.LINE_CROSSCHECK_FAIL,
}

def extract_old_codes(errors: list[str]) -> set[str]:
    """Eski validator error string'lerinden code prefix çıkar.
    
    "PAYABLE_TOTAL_MISMATCH: payable=100, total=200" → "PAYABLE_TOTAL_MISMATCH"
    """
    codes = set()
    for e in errors:
        prefix = e.split(":")[0].strip()
        if prefix in _OLD_CODE_PREFIX_MAP:
            codes.add(prefix)
    return codes
```

#### 12.3.3 ShadowCompareResult

```python
@dataclass(frozen=True)
class ShadowCompareResult:
    old_valid: bool
    new_valid: bool
    valid_match: bool
    old_codes: frozenset[str]
    new_codes: frozenset[str]
    codes_only_old: frozenset[str]   # old - new
    codes_only_new: frozenset[str]   # new - old
    codes_common: frozenset[str]     # old & new

    def to_dict(self) -> dict: ...
```

#### 12.3.4 compare_validators()

```python
def compare_validators(
    invoice_dict: dict,
) -> ShadowCompareResult:
    """Tek dict'ten her iki validator'ı çalıştır ve karşılaştır.
    
    1. invoice_dict → CanonicalInvoice (builder)
    2. CanonicalInvoice.validate() → old_valid, old_codes
    3. validate(invoice_dict) → new_valid, new_codes
    4. Compare
    """
```

### 12.4 Fixture Scope

Sadece `validation_totals/` fixture'ları shadow'a girer (ortak alanlar burada).
`validation/` fixture'ları (ETTN/period/reactive) shadow'a girmez — eski validator bu alanları bilmez.

Özel durum: `missing_totals_skips.json` — totals/lines yok, eski validator'a besleyecek veri yok.
Bu fixture shadow'da "both valid, both empty codes" olarak assert edilir (skip semantiği her iki tarafta da aynı).

### 12.5 Test Planı

`backend/tests/test_invoice_validator_shadow.py`:

| # | Test | Assert |
|---|------|--------|
| 1 | `totals_ok.json` — her iki validator valid | valid_match=True, codes_common=∅ (hata yok) |
| 2 | `payable_total_mismatch.json` — her ikisi fail | valid_match=True, PAYABLE_TOTAL_MISMATCH ∈ codes_common |
| 3 | `total_mismatch.json` — her ikisi fail | valid_match=True, TOTAL_MISMATCH ∈ codes_common |
| 4 | `zero_consumption.json` — her ikisi fail | valid_match=True, ZERO_CONSUMPTION ∈ codes_common |
| 5 | `line_crosscheck_fail.json` — her ikisi fail | valid_match=True, LINE_CROSSCHECK_FAIL ∈ codes_common |
| 6 | `missing_totals_skips.json` — her ikisi valid (skip) | valid_match=True, codes=∅ |
| 7 | `ShadowCompareResult.to_dict()` round-trip | JSON-serializable |
| 8 | Mismatch counter: valid_match=False durumunda SHADOW_METRIC_NAME increment | test-only counter |

### 12.6 Mismatch Counter (test-only)

```python
SHADOW_METRIC_NAME = "invoice_validation_shadow_mismatch_total"  # types.py'de tanımlı
# Test'te: basit int sayaç, valid_match=False → +1
# Prod metric emisyonu 4.4+ / Faz E
```

### 12.7 Dosya Yapısı

```
backend/app/invoice/validation/shadow.py     # ShadowCompareResult, compare_validators, build_canonical_invoice
backend/tests/test_invoice_validator_shadow.py  # shadow compare testleri
```

### 12.8 Non-scope (Faz D)

- Prod middleware / batch job (4.4+)
- Threshold / baraj (4.4+)
- Supplier mapping / normalizasyon
- `validation/` fixture'ları shadow'a sokmak (ETTN/period/reactive eski validator'da yok)

### 12.9 Faz D Kapanış Kriterleri

- [ ] `shadow.py`: `ShadowCompareResult`, `build_canonical_invoice()`, `extract_old_codes()`, `compare_validators()`
- [ ] `test_invoice_validator_shadow.py`: ≥ 8 test
- [ ] Tüm `validation_totals/` fixture'ları shadow'dan geçirilmiş
- [ ] Port edilen 4 kural: valid_match=True ve codes_common'da ortak code var
- [ ] Mismatch counter test-only çalışıyor
- [ ] Mevcut test suite regresyon yok
