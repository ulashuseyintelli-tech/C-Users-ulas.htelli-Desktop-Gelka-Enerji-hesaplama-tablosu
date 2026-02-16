"""
PR-12: Release Gate Telemetry — metrik modülü.

ReleaseGate enforcement hook'una gözlemlenebilirlik ekler.
Pure Python — harici bağımlılık yok (prometheus_client kullanılmaz).

Sabit metrik isimleri:
    release_gate_decision_total{decision="ALLOW|DENY"}        — counter
    release_gate_reason_total{reason="<BlockReasonCode>"}     — counter
    release_gate_contract_breach_total{kind="NO_OVERRIDE"}    — counter
    release_gate_audit_write_failures_total                   — counter
    release_gate_metric_write_failures_total                  — counter

Label kuralları:
    - Tüm label değerleri sabit enum setlerinden gelir
    - Kullanıcı girdisinden label türetilmez
    - Geçersiz reason değerleri sessizce atlanır (bounded cardinality)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from backend.app.testing.release_policy import BlockReasonCode


# ===================================================================
# Sabit label kümeleri (bounded cardinality)
# ===================================================================

_DECISION_LABELS: list[str] = ["ALLOW", "DENY"]
_REASON_LABELS: list[str] = [r.value for r in BlockReasonCode]
_BREACH_KINDS: list[str] = ["NO_OVERRIDE"]


# ===================================================================
# GateMetricStore — thread-safe in-memory depo
# ===================================================================

class GateMetricStore:
    """
    Thread-safe in-memory metrik deposu — ReleaseGate telemetrisi.

    Counter'lar monoton artar.
    Label cardinality bounded: decision ∈ {ALLOW, DENY},
    reason ∈ BlockReasonCode, kind ∈ {NO_OVERRIDE}.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._decision_counts: dict[str, int] = {d: 0 for d in _DECISION_LABELS}
        self._reason_counts: dict[str, int] = {r: 0 for r in _REASON_LABELS}
        self._breach_counts: dict[str, int] = {k: 0 for k in _BREACH_KINDS}
        self._audit_write_failures: int = 0
        self._metric_write_failures: int = 0
        self._store_generation: int = 0
        self._store_start_timestamp: float = time.time()

    # --- Yazma ---

    def record_decision(self, allowed: bool, reasons: list[str]) -> None:
        """
        Karar sayacını artır. Her check() çağrısında bir kez.

        allowed=True → ALLOW, reasons boş olabilir
        allowed=False → DENY, her geçerli reason ayrı sayılır
        Geçersiz reason değerleri sessizce atlanır (bounded cardinality).
        """
        with self._lock:
            decision = "ALLOW" if allowed else "DENY"
            self._decision_counts[decision] += 1
            for reason in reasons:
                if reason in self._reason_counts:
                    self._reason_counts[reason] += 1

    def record_breach(self) -> None:
        """Sözleşme ihlali sayacını artır. CONTRACT_BREACH_NO_OVERRIDE path'inde."""
        with self._lock:
            self._breach_counts["NO_OVERRIDE"] += 1

    def record_audit_write_failure(self) -> None:
        """Audit yazım hatası sayacını artır."""
        with self._lock:
            self._audit_write_failures += 1

    def record_metric_write_failure(self) -> None:
        """Dahili metrik yazım hatası sayacını artır (fail-open gözlemlenebilirlik)."""
        with self._lock:
            self._metric_write_failures += 1

    # --- Okuma ---

    def decision_counts(self) -> dict[str, int]:
        with self._lock:
            return dict(self._decision_counts)

    def reason_counts(self) -> dict[str, int]:
        with self._lock:
            return dict(self._reason_counts)

    def breach_counts(self) -> dict[str, int]:
        with self._lock:
            return dict(self._breach_counts)

    def audit_write_failures(self) -> int:
        with self._lock:
            return self._audit_write_failures

    def metric_write_failures(self) -> int:
        with self._lock:
            return self._metric_write_failures

    def store_generation(self) -> int:
        with self._lock:
            return self._store_generation

    def store_start_timestamp(self) -> float:
        with self._lock:
            return self._store_start_timestamp

    # --- JSON persistence ---

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "decision_counts": dict(self._decision_counts),
                "reason_counts": dict(self._reason_counts),
                "breach_counts": dict(self._breach_counts),
                "audit_write_failures": self._audit_write_failures,
                "metric_write_failures": self._metric_write_failures,
                "store_generation": self._store_generation,
                "store_start_timestamp": self._store_start_timestamp,
            }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GateMetricStore":
        store = cls()
        store._decision_counts.update(data.get("decision_counts", {}))
        store._reason_counts.update(data.get("reason_counts", {}))
        store._breach_counts.update(data.get("breach_counts", {}))
        store._audit_write_failures = data.get("audit_write_failures", 0)
        store._metric_write_failures = data.get("metric_write_failures", 0)
        store._store_generation = data.get("store_generation", 0)
        if "store_start_timestamp" in data:
            store._store_start_timestamp = float(data["store_start_timestamp"])
        return store

    def load_from_dir(self, path: str | Path) -> bool:
        """Dizinden mevcut metrikleri yükle. Fail-open: hata → False."""
        p = Path(path) / "gate_metrics.json"
        if not p.exists():
            return False
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            loaded = GateMetricStore.from_dict(data)
            with self._lock:
                self._decision_counts = loaded._decision_counts
                self._reason_counts = loaded._reason_counts
                self._breach_counts = loaded._breach_counts
                self._audit_write_failures = loaded._audit_write_failures
                self._metric_write_failures = loaded._metric_write_failures
                self._store_generation = loaded._store_generation
                if "store_start_timestamp" in data:
                    self._store_start_timestamp = loaded._store_start_timestamp
                else:
                    self._store_start_timestamp = p.stat().st_mtime
            return True
        except Exception:
            sys.stderr.write(
                f"[gate-metrics] Uyarı: {p} okunamadı, yeni store başlatıldı.\n"
            )
            return False

    def save_to_dir(self, path: str | Path) -> bool:
        """Atomik yazım. Başarısızlıkta metric_write_failures artırılır."""
        out = Path(path)
        out.mkdir(parents=True, exist_ok=True)
        target = out / "gate_metrics.json"
        with self._lock:
            self._store_generation += 1
        content = json.dumps(self.to_dict(), indent=2, ensure_ascii=False)
        ok = _atomic_write(target, content)
        if not ok:
            with self._lock:
                self._store_generation -= 1
            self.record_metric_write_failure()
        return ok


# ===================================================================
# GateMetricExporter — format dönüşümleri
# ===================================================================

class GateMetricExporter:
    """Prometheus text exposition ve JSON export. Deterministik."""

    @staticmethod
    def export_prometheus(store: GateMetricStore) -> str:
        lines: list[str] = []

        # Decision counters
        dc = store.decision_counts()
        lines.append("# HELP release_gate_decision_total Gate decision counter")
        lines.append("# TYPE release_gate_decision_total counter")
        for d in _DECISION_LABELS:
            lines.append(f'release_gate_decision_total{{decision="{d}"}} {dc.get(d, 0)}')
        lines.append("")

        # Reason counters
        rc = store.reason_counts()
        lines.append("# HELP release_gate_reason_total Gate deny reason counter")
        lines.append("# TYPE release_gate_reason_total counter")
        for r in _REASON_LABELS:
            lines.append(f'release_gate_reason_total{{reason="{r}"}} {rc.get(r, 0)}')
        lines.append("")

        # Breach counters
        bc = store.breach_counts()
        lines.append("# HELP release_gate_contract_breach_total Contract breach counter")
        lines.append("# TYPE release_gate_contract_breach_total counter")
        for k in _BREACH_KINDS:
            lines.append(f'release_gate_contract_breach_total{{kind="{k}"}} {bc.get(k, 0)}')
        lines.append("")

        # Audit write failures
        awf = store.audit_write_failures()
        lines.append("# HELP release_gate_audit_write_failures_total Audit write failure counter")
        lines.append("# TYPE release_gate_audit_write_failures_total counter")
        lines.append(f"release_gate_audit_write_failures_total {awf}")
        lines.append("")

        # Metric write failures
        mwf = store.metric_write_failures()
        lines.append("# HELP release_gate_metric_write_failures_total Metric write failure counter")
        lines.append("# TYPE release_gate_metric_write_failures_total counter")
        lines.append(f"release_gate_metric_write_failures_total {mwf}")
        lines.append("")

        # Store generation gauge
        sg = store.store_generation()
        lines.append("# HELP release_gate_store_generation Store load-save cycle count")
        lines.append("# TYPE release_gate_store_generation gauge")
        lines.append(f"release_gate_store_generation {sg}")
        lines.append("")

        # Store start timestamp gauge
        sst = store.store_start_timestamp()
        lines.append("# HELP release_gate_store_start_time_seconds Store creation time (unix epoch)")
        lines.append("# TYPE release_gate_store_start_time_seconds gauge")
        lines.append(f"release_gate_store_start_time_seconds {sst}")
        lines.append("")

        return "\n".join(lines)

    @staticmethod
    def export_json(store: GateMetricStore) -> str:
        return json.dumps(store.to_dict(), indent=2, ensure_ascii=False, sort_keys=True)


# ===================================================================
# Atomic write — fail-open
# ===================================================================

def _atomic_write(target: Path, content: str) -> bool:
    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=str(target.parent), suffix=".tmp",
        )
        try:
            os.write(fd, content.encode("utf-8"))
            os.close(fd)
            fd = -1
            os.replace(tmp_path, str(target))
            return True
        finally:
            if fd >= 0:
                os.close(fd)
            if os.path.exists(tmp_path) and tmp_path != str(target):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
    except Exception as exc:
        sys.stderr.write(
            f"[gate-metrics] Uyarı: metrik yazılamadı ({target}): {exc}\n"
        )
        return False
