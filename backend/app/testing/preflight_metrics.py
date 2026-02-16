"""
PR-17: Preflight Telemetry — metrik modülü.

Preflight sonuçlarını yapılandırılmış metrik olarak dışa aktarır.
Pure Python — harici bağımlılık yok (prometheus_client kullanılmaz).

Metrik modeli: Seçenek B — Cumulative totals (Counter).
MetricStore JSON persistence ile birikimli sayım tutar. Her run:
load_from_dir → add → save_to_dir. Counter'lar monoton artar.

Sabit metrik isimleri (breaking change sözleşmesi):
    release_preflight_verdict_total{verdict="OK|HOLD|BLOCK"}          — counter
    release_preflight_reason_total{reason="<BlockReasonCode.value>"}  — counter
    release_preflight_override_total{kind="attempt|applied|breach"}   — counter
    release_preflight_telemetry_write_failures_total                  — counter

Tüm label değerleri her zaman yazılır (sıfır dahil). Sparse time series yok.

Label kuralları:
    - Tüm label değerleri sabit enum setlerinden gelir
    - override_by ASLA label olmaz — yalnızca JSON audit alanı
    - Kullanıcı girdisinden label türetilmez

Override kind semantiği:
    - "attempt": override flag'leri sağlandı + BLOCK verdict → override reddedildi
                 (ABSOLUTE_BLOCK_REASONS olmasa bile BLOCK override edilemez)
    - "applied": override flag'leri sağlandı + HOLD verdict → override kabul edildi, exit 0
    - "breach":  override flag'leri sağlandı + BLOCK verdict + ABSOLUTE_BLOCK_REASONS
                 → CONTRACT_BREACH, sözleşme ihlali kaydı

Dosya yazım kuralları:
    - Write-once per run: preflight_metrics.json (cumulative) + preflight.prom (latest)
    - Atomic write: temp dosya → os.replace()
    - Fail-open: metrik yazımı başarısızsa exit code değişmez,
      write_failures_total counter artar (gözlemlenebilir fail-open)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.app.testing.release_policy import BlockReasonCode


# ===================================================================
# Sabit label değerleri (bounded cardinality)
# ===================================================================

_VERDICT_LABELS = ("OK", "HOLD", "BLOCK")

_REASON_LABELS = tuple(r.value for r in BlockReasonCode)

_OVERRIDE_KINDS = ("attempt", "applied", "breach")

# Verdict mapping: preflight JSON verdict → metrik label
_VERDICT_MAP = {
    "release_ok": "OK",
    "release_hold": "HOLD",
    "release_block": "BLOCK",
}


# ===================================================================
# PreflightMetric — tek koşumun metrik kaydı
# ===================================================================

@dataclass(frozen=True)
class PreflightMetric:
    """Tek bir preflight koşumunun metrik kaydı."""
    timestamp: str              # ISO 8601
    verdict: str                # "OK" | "HOLD" | "BLOCK"
    exit_code: int
    reasons: list[str]          # BlockReasonCode.value listesi
    override_applied: bool
    contract_breach: bool
    override_kind: str          # "attempt" | "applied" | "breach" | "none"
    spec_hash: str
    duration_ms: float = 0.0


# ===================================================================
# MetricStore — thread-safe in-memory depo
# ===================================================================

class MetricStore:
    """
    Thread-safe in-memory metrik deposu.

    Counter'lar monoton artar (Property 20).
    Reason counts tüm BlockReasonCode üyelerini kapsar (sıfır dahil).
    Label cardinality bounded (Property 21).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._metrics: list[PreflightMetric] = []
        # Verdict counters — sabit key seti
        self._verdict_counts: dict[str, int] = {v: 0 for v in _VERDICT_LABELS}
        # Reason counters — tüm enum üyeleri (sıfır dahil)
        self._reason_counts: dict[str, int] = {r: 0 for r in _REASON_LABELS}
        # Override counters — sabit key seti
        self._override_counts: dict[str, int] = {k: 0 for k in _OVERRIDE_KINDS}
        # Telemetry write failure counter (gözlemlenebilir fail-open)
        self._write_failures: int = 0
        # Guard-rail: store_generation — her başarılı save'de +1 (monoton)
        self._store_generation: int = 0
        # Guard-rail: store_start_timestamp — ilk oluşturulma zamanı (Unix epoch float)
        # Bir kez set edilir, sonraki save/load'larda değişmez.
        self._store_start_timestamp: float = time.time()

    def add(self, metric: PreflightMetric) -> None:
        """Metrik ekle. Thread-safe, monoton counter update."""
        with self._lock:
            self._metrics.append(metric)
            # Verdict counter
            if metric.verdict in self._verdict_counts:
                self._verdict_counts[metric.verdict] += 1
            # Reason counters — her reason ayrı sayılır
            for reason in metric.reasons:
                if reason in self._reason_counts:
                    self._reason_counts[reason] += 1
            # Override counter
            if metric.override_kind in self._override_counts:
                self._override_counts[metric.override_kind] += 1

    def verdict_counts(self) -> dict[str, int]:
        with self._lock:
            return dict(self._verdict_counts)

    def reason_counts(self) -> dict[str, int]:
        with self._lock:
            return dict(self._reason_counts)

    def override_counts(self) -> dict[str, int]:
        with self._lock:
            return dict(self._override_counts)

    def write_failures(self) -> int:
        """Telemetry write failure counter."""
        with self._lock:
            return self._write_failures

    def record_write_failure(self) -> None:
        """Telemetry yazım hatası sayacını artır. Fail-open gözlemlenebilirliği."""
        with self._lock:
            self._write_failures += 1

    def store_generation(self) -> int:
        """Store load-save döngü sayacı. Her başarılı save'de +1."""
        with self._lock:
            return self._store_generation

    def store_start_timestamp(self) -> float:
        """Store'un ilk oluşturulma zamanı (Unix epoch saniye)."""
        with self._lock:
            return self._store_start_timestamp

    def all_metrics(self) -> list[PreflightMetric]:
        with self._lock:
            return list(self._metrics)

    def __len__(self) -> int:
        with self._lock:
            return len(self._metrics)

    # --- JSON persistence ---

    def to_dict(self) -> dict[str, Any]:
        """Store'u JSON-serializable dict'e dönüştür."""
        with self._lock:
            return {
                "metrics": [asdict(m) for m in self._metrics],
                "verdict_counts": dict(self._verdict_counts),
                "reason_counts": dict(self._reason_counts),
                "override_counts": dict(self._override_counts),
                "write_failures": self._write_failures,
                "store_generation": self._store_generation,
                "store_start_timestamp": self._store_start_timestamp,
            }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MetricStore":
        """Dict'ten MetricStore oluştur."""
        store = cls()
        for m in data.get("metrics", []):
            store._metrics.append(PreflightMetric(**m))
        store._verdict_counts.update(data.get("verdict_counts", {}))
        store._reason_counts.update(data.get("reason_counts", {}))
        store._override_counts.update(data.get("override_counts", {}))
        store._write_failures = data.get("write_failures", 0)
        store._store_generation = data.get("store_generation", 0)
        # store_start_timestamp: yoksa __init__'teki default (time.time()) kalır
        if "store_start_timestamp" in data:
            store._store_start_timestamp = float(data["store_start_timestamp"])
        return store

    def load_from_dir(self, path: str | Path) -> bool:
        """Dizinden mevcut metrikleri yükle. Fail-open: hata → False."""
        p = Path(path) / "preflight_metrics.json"
        if not p.exists():
            return False
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            loaded = MetricStore.from_dict(data)
            with self._lock:
                self._metrics = loaded._metrics
                self._verdict_counts = loaded._verdict_counts
                self._reason_counts = loaded._reason_counts
                self._override_counts = loaded._override_counts
                self._write_failures = loaded._write_failures
                self._store_generation = loaded._store_generation
                # store_start_timestamp: dosyada varsa koru;
                # yoksa (eski format) dosyanın mtime'ını backfill olarak kullan
                if "store_start_timestamp" in data:
                    self._store_start_timestamp = loaded._store_start_timestamp
                else:
                    # Eski format backfill: disk üzerindeki dosyanın mtime'ı
                    self._store_start_timestamp = p.stat().st_mtime
            return True
        except Exception:
            # Fail-open: bozuk dosya → yeni store, generation=0, timestamp=now
            sys.stderr.write(
                f"[preflight-metrics] Uyarı: {p} okunamadı, yeni store başlatıldı.\n"
            )
            return False

    def save_to_dir(self, path: str | Path) -> bool:
        """Dizine metrik yaz. Atomic write: temp → rename. Fail-open.
        Başarılı yazımda store_generation +1 artar."""
        out = Path(path)
        out.mkdir(parents=True, exist_ok=True)
        target = out / "preflight_metrics.json"
        # Generation'ı artır (yazım öncesi, to_dict'e yansısın)
        with self._lock:
            self._store_generation += 1
        content = json.dumps(self.to_dict(), indent=2, ensure_ascii=False)
        ok = _atomic_write(target, content)
        if not ok:
            # Yazım başarısız — generation'ı geri al
            with self._lock:
                self._store_generation -= 1
            self.record_write_failure()
        return ok


# ===================================================================
# MetricExporter — format dönüşümleri
# ===================================================================

class MetricExporter:
    """
    Preflight sonuçlarını metrik formatına dönüştürür.

    Determinism (Property 19): Aynı store → aynı çıktı (byte-level).
    Label safety (Property 21): Tüm label'lar sabit enum setlerinden.
    """

    @staticmethod
    def from_preflight_output(
        output: dict[str, Any],
        duration_ms: float = 0.0,
    ) -> PreflightMetric:
        """
        Preflight JSON çıktısından PreflightMetric oluştur.

        override_kind türetme:
            override_applied=True → "applied"
            contract_breach=True → "breach"
            override flag'leri sağlanmış + BLOCK → "attempt"
            override flag'leri sağlanmamış → "none"
        """
        verdict_raw = output.get("verdict", "release_block")
        verdict = _VERDICT_MAP.get(verdict_raw, "BLOCK")

        override_applied = output.get("override_applied", False)
        contract_breach = output.get("contract_breach", False)
        has_override_by = bool(output.get("override_by"))

        if override_applied:
            override_kind = "applied"
        elif contract_breach:
            override_kind = "breach"
        elif has_override_by and verdict == "BLOCK":
            override_kind = "attempt"
        else:
            override_kind = "none"

        # Reason'ları BlockReasonCode enum'undan filtrele (bounded)
        raw_reasons = output.get("reasons", [])
        valid_reasons = [r for r in raw_reasons if r in _REASON_LABELS]

        return PreflightMetric(
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            verdict=verdict,
            exit_code=output.get("exit_code", 2),
            reasons=valid_reasons,
            override_applied=override_applied,
            contract_breach=contract_breach,
            override_kind=override_kind,
            spec_hash=output.get("spec_hash", "unknown"),
            duration_ms=duration_ms,
        )

    @staticmethod
    def export_json(store: MetricStore) -> str:
        """Store'u JSON formatında dışa aktar. Deterministik."""
        return json.dumps(store.to_dict(), indent=2, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def export_prometheus(store: MetricStore) -> str:
        """
        Store'u Prometheus text exposition formatında dışa aktar.

        Deterministik: sabit sıralama, sabit label seti.
        """
        lines: list[str] = []

        # Verdict counters
        vc = store.verdict_counts()
        lines.append("# HELP release_preflight_verdict_total Preflight verdict counter")
        lines.append("# TYPE release_preflight_verdict_total counter")
        for v in _VERDICT_LABELS:
            lines.append(f'release_preflight_verdict_total{{verdict="{v}"}} {vc.get(v, 0)}')
        lines.append("")

        # Reason counters
        rc = store.reason_counts()
        lines.append("# HELP release_preflight_reason_total Preflight reason code counter")
        lines.append("# TYPE release_preflight_reason_total counter")
        for r in _REASON_LABELS:
            lines.append(f'release_preflight_reason_total{{reason="{r}"}} {rc.get(r, 0)}')
        lines.append("")

        # Override counters
        oc = store.override_counts()
        lines.append("# HELP release_preflight_override_total Override attempt counter")
        lines.append("# TYPE release_preflight_override_total counter")
        for k in _OVERRIDE_KINDS:
            lines.append(f'release_preflight_override_total{{kind="{k}"}} {oc.get(k, 0)}')
        lines.append("")

        # Telemetry write failure counter (gözlemlenebilir fail-open)
        wf = store.write_failures()
        lines.append("# HELP release_preflight_telemetry_write_failures_total Telemetry write failure counter")
        lines.append("# TYPE release_preflight_telemetry_write_failures_total counter")
        lines.append(f"release_preflight_telemetry_write_failures_total {wf}")
        lines.append("")

        # Store generation gauge (guard-rail: counter reset tespiti)
        sg = store.store_generation()
        lines.append("# HELP release_preflight_store_generation Store load-save cycle count")
        lines.append("# TYPE release_preflight_store_generation gauge")
        lines.append(f"release_preflight_store_generation {sg}")
        lines.append("")

        # Store start timestamp gauge (guard-rail: counter reset tespiti)
        sst = store.store_start_timestamp()
        lines.append("# HELP release_preflight_store_start_time_seconds Store creation time (unix epoch)")
        lines.append("# TYPE release_preflight_store_start_time_seconds gauge")
        lines.append(f"release_preflight_store_start_time_seconds {sst}")
        lines.append("")

        return "\n".join(lines)


# ===================================================================
# Atomic write — fail-open
# ===================================================================

def _atomic_write(target: Path, content: str) -> bool:
    """
    Atomic write: temp dosya → os.replace().
    Fail-open: hata → stderr uyarı, False döner.
    """
    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=str(target.parent), suffix=".tmp",
        )
        try:
            os.write(fd, content.encode("utf-8"))
            os.close(fd)
            fd = -1  # closed
            os.replace(tmp_path, str(target))
            return True
        finally:
            if fd >= 0:
                os.close(fd)
            # Temp dosya kaldıysa temizle
            if os.path.exists(tmp_path) and tmp_path != str(target):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
    except Exception as exc:
        sys.stderr.write(
            f"[preflight-metrics] Uyarı: metrik yazılamadı ({target}): {exc}\n"
        )
        return False
