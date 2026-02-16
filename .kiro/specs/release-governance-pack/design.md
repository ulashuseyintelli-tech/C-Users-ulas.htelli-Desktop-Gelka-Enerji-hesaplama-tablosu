# Tasarım Dokümanı — Release Governance Pack (PR-13)

## Genel Bakış

PR-11 (release-governance) ve PR-12 (e2e-pipeline) ile kanıtlanan release zincirini ekip dışına devredilebilir hale getirir. Yeni production karar mantığı yok; çıktılar dokümantasyon, CI komut referansı, spec hash versiyonlama ve paket bütünlüğü testi.

## Bileşenler

### 1. Index README (`.kiro/specs/release-governance/README.md`)

Tek sayfa referans dokümanı. İçerik:

- Sistem özeti (3-5 cümle)
- Dosya haritası (modül → dosya yolu tablosu)
- Test haritası (test dosyası → ne test ediyor tablosu)
- CI komutları bölümü (copy-paste çalışır pytest komutları)
- Reason code → required action referans tablosu (otomatik üretilmiş)
- Runbook ve spec referansları (linkler)

### 2. Reason Code Tablosu (otomatik üretim)

`release_policy.py` içindeki `_ACTION_DESCRIPTIONS` dict'inden ve `ABSOLUTE_BLOCK_REASONS` frozenset'inden üretilir.

```python
def generate_reason_code_table() -> str:
    """
    _ACTION_DESCRIPTIONS + ABSOLUTE_BLOCK_REASONS → markdown tablosu.
    Sütunlar: Neden Kodu | Verdict | Aksiyon | Override?
    Deterministik: enum tanım sırasına göre sıralı.
    """
```

Tablo README içine gömülür (ayrı dosya değil). Üretim fonksiyonu `release_version.py` içinde yer alır.

### 3. CI Pipeline Komut Referansı

README'nin bir bölümü. Komutlar:

| Amaç | Komut |
|---|---|
| Tüm release testleri | `pytest backend/tests/test_release_policy.py backend/tests/test_release_report.py backend/tests/test_release_gate.py backend/tests/test_release_e2e.py -v` |
| Sadece unit | `pytest backend/tests/test_release_policy.py backend/tests/test_release_report.py backend/tests/test_release_gate.py backend/tests/test_release_e2e.py -v -k "not PBT"` |
| Sadece PBT | `pytest backend/tests/test_release_policy.py backend/tests/test_release_report.py backend/tests/test_release_gate.py backend/tests/test_release_e2e.py -v -k "PBT"` |
| Tek modül (policy) | `pytest backend/tests/test_release_policy.py -v` |
| Tek modül (report) | `pytest backend/tests/test_release_report.py -v` |
| Tek modül (gate) | `pytest backend/tests/test_release_gate.py -v` |
| Tek modül (e2e) | `pytest backend/tests/test_release_e2e.py -v` |
| PBT seed ile | `pytest backend/tests/test_release_policy.py -v --hypothesis-seed=12345` |

### 4. Spec Hash Versiyonlama (`backend/app/testing/release_version.py`)

```python
import hashlib
from pathlib import Path

_SPEC_FILES = [
    "backend/app/testing/release_policy.py",
    "backend/app/testing/release_report.py",
    "backend/app/testing/release_gate.py",
]

def spec_hash(base_dir: str | Path = ".") -> str:
    """
    SHA-256 hash of release_policy.py + release_report.py + release_gate.py.
    Dosyalar sıralı okunur, içerikleri birleştirilir, tek hash üretilir.
    Deterministik: aynı dosya içeriği → aynı hash.
    """

def generate_reason_code_table() -> str:
    """
    _ACTION_DESCRIPTIONS + ABSOLUTE_BLOCK_REASONS → markdown tablosu.
    Deterministik: BlockReasonCode enum tanım sırasına göre.
    """
```

### 5. Paket Bütünlüğü Testi (`backend/tests/test_release_pack.py`)

Smoke test: tüm modüller import edilebilir, public sınıflar instantiate edilebilir, spec_hash deterministik, reason code tablosu boş değil.

## Doğruluk Özellikleri (Correctness Properties)

Bu PR adoption/operability odaklı olduğundan ağır PBT property'ler yerine basit deterministik doğruluk kontrolleri yeterlidir.

### Property 15: Spec hash determinizmi

*For any* çağrı, `spec_hash()` aynı dosya içeriği ile iki kez çağrıldığında aynı hash string'ini döndürmelidir.

**Validates: Requirements 4.3, 4.5**

### Property 16: Reason code tablosu bütünlüğü

*For any* çağrı, `generate_reason_code_table()` çıktısı tüm `BlockReasonCode` enum üyelerini içermelidir ve deterministik olmalıdır (iki çağrı aynı string).

**Validates: Requirements 2.1, 2.2, 2.3**

## Test Stratejisi

- Kütüphane: `pytest` + `hypothesis` (Python)
- Tek test dosyası: `backend/tests/test_release_pack.py`
- DoD: ≥6 unit test + 2 PBT, 0 flaky
- PBT max_examples: 50 (lightweight)

| Test Türü | Kapsam |
|---|---|
| Unit: import smoke | Tüm modüller import edilebilir |
| Unit: instantiation | ReleasePolicy, ReleaseReportGenerator, ReleaseGate instantiate edilebilir |
| Unit: spec_hash not empty | spec_hash() None/boş değil |
| Unit: spec_hash deterministic | İki çağrı aynı sonuç |
| Unit: reason table not empty | generate_reason_code_table() boş değil |
| Unit: reason table all codes | Tüm BlockReasonCode'lar tabloda var |
| PBT: spec_hash determinism | Property 15 |
| PBT: reason table completeness | Property 16 |
