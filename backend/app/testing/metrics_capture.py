"""
MetricsCapture — LC whitelist-filtered metric snapshot & delta engine.

Feature: load-characterization, Task 2.1
Validates: R2 (2.1–2.6), GNK-1

Reads metrics from PTFMetrics.registry via registry.collect() (public API).
No production code changes required.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, FrozenSet, Mapping, Optional

from prometheus_client import CollectorRegistry

from .lc_config import retry_amp_tolerance
from .stress_report import FailDiagnostic


# ── LC Whitelist: canonical Prometheus names + expected label schemas ─────

@dataclass(frozen=True)
class _MetricSpec:
    """Defines a whitelisted metric: name, type, and expected label set."""
    name: str
    kind: str  # "counter" | "gauge"
    expected_labels: FrozenSet[str]


LC_WHITELIST: tuple[_MetricSpec, ...] = (
    _MetricSpec("ptf_admin_dependency_call_total", "counter", frozenset({"dependency", "outcome"})),
    _MetricSpec("ptf_admin_dependency_retry_total", "counter", frozenset({"dependency"})),
    _MetricSpec("ptf_admin_circuit_breaker_state", "gauge", frozenset({"dependency"})),
    _MetricSpec("ptf_admin_guard_failopen_total", "counter", frozenset()),
    _MetricSpec("ptf_admin_dependency_map_miss_total", "counter", frozenset()),
)

_WHITELIST_NAMES: frozenset[str] = frozenset(spec.name for spec in LC_WHITELIST)
_SPEC_BY_NAME: dict[str, _MetricSpec] = {spec.name: spec for spec in LC_WHITELIST}


# ── Key type for metric samples ──────────────────────────────────────────

MetricKey = tuple[str, FrozenSet[tuple[str, str]]]  # (metric_name, frozenset(labels))


# ── Snapshot ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MetricSnapshot:
    """
    Whitelist-filtered point-in-time snapshot of LC-relevant metrics.

    values: {MetricKey: float}
    Each key is (canonical_name, frozenset of (label_name, label_value) pairs).
    """
    values: Mapping[MetricKey, float] = field(default_factory=dict)


# ── Delta ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MetricDelta:
    """
    Delta between two snapshots.

    counter_deltas: non-negative deltas for counter metrics.
    gauge_values: raw after-values for gauge metrics (not deltas).
    diagnostics: FailDiagnostic list for any invariant violations.
    """
    counter_deltas: Mapping[MetricKey, float] = field(default_factory=dict)
    gauge_values: Mapping[MetricKey, float] = field(default_factory=dict)
    diagnostics: list[FailDiagnostic] = field(default_factory=list)

    @property
    def retry_amplification(self) -> float:
        """
        retry_total / call_total.  Returns 0.0 if no calls recorded.
        R2 AC4: retry amplification factor.
        """
        total_calls = 0.0
        total_retries = 0.0
        for key, val in self.counter_deltas.items():
            name = key[0]
            if name == "ptf_admin_dependency_call_total":
                total_calls += val
            elif name == "ptf_admin_dependency_retry_total":
                total_retries += val
        if total_calls <= 0:
            return 0.0
        return total_retries / total_calls

    def assert_retry_amp_close(self, expected: float) -> None:
        """R2 AC4: assert retry amplification within tolerance."""
        diff = abs(self.retry_amplification - expected)
        tol = retry_amp_tolerance(expected)
        if diff > tol:
            raise AssertionError(
                f"retry_amplification mismatch: "
                f"observed={self.retry_amplification:.6f} "
                f"expected={expected:.6f} diff={diff:.6f} tol={tol:.6f}"
            )

    @property
    def invariant_ok(self) -> bool:
        """True if no diagnostics were generated during delta computation."""
        return len(self.diagnostics) == 0

    def summary(self) -> dict[str, Any]:
        """JSON-serializable summary for reporting."""
        return {
            "counter_deltas": {
                f"{k[0]}|{dict(sorted(k[1]))}": v
                for k, v in sorted(self.counter_deltas.items())
            },
            "gauge_values": {
                f"{k[0]}|{dict(sorted(k[1]))}": v
                for k, v in sorted(self.gauge_values.items())
            },
            "retry_amplification": self.retry_amplification,
            "invariant_ok": self.invariant_ok,
            "diagnostic_count": len(self.diagnostics),
        }


# ── MetricsCapture ───────────────────────────────────────────────────────

class MetricsCapture:
    """
    LC metric capture engine.

    Usage:
        metrics = PTFMetrics(registry=CollectorRegistry())
        cap = MetricsCapture(metrics.registry)
        before = cap.take_snapshot()
        # ... run load ...
        after = cap.take_snapshot()
        delta = cap.compute_delta(before, after)
    """

    def __init__(self, registry: CollectorRegistry) -> None:
        self._registry = registry

    def take_snapshot(self) -> MetricSnapshot:
        """
        Read current metric values from registry.collect().
        Filters to LC_WHITELIST only.  Validates label schemas.

        prometheus_client naming:
          - MetricFamily.name for counters: 'ptf_admin_dependency_call' (no _total)
          - Sample.name for counters: 'ptf_admin_dependency_call_total'
          - Sample.name for _created: 'ptf_admin_dependency_call_created' (skip)
          - Gauge: MetricFamily.name == Sample.name (no suffix)
        """
        values: dict[MetricKey, float] = {}

        for metric_family in self._registry.collect():
            # Resolve MetricFamily name to whitelist canonical name
            canonical = _resolve_to_whitelist(metric_family.name)
            if canonical is None:
                continue

            spec = _SPEC_BY_NAME[canonical]

            for sample in metric_family.samples:
                # Skip _created samples (counter bookkeeping)
                if sample.name.endswith("_created"):
                    continue

                # For counters, sample.name has _total suffix; for gauges it matches MF name
                # Verify this sample maps to our canonical name
                sample_canonical = _resolve_to_whitelist(sample.name)
                if sample_canonical != canonical:
                    continue

                labels_set = frozenset(sample.labels.items())
                label_keys = frozenset(sample.labels.keys())

                # Label schema validation
                if label_keys != spec.expected_labels:
                    # Label drift — skip this sample but don't crash
                    continue

                key: MetricKey = (canonical, labels_set)
                values[key] = sample.value

        return MetricSnapshot(values=values)

    def compute_delta(
        self,
        before: MetricSnapshot,
        after: MetricSnapshot,
        *,
        context_seed: int = 0,
        context_scenario: str = "",
    ) -> MetricDelta:
        """
        Compute delta between two snapshots.

        Counter: delta = after - before.  Negative → FailDiagnostic.
        Gauge: raw after-value (no delta; state can be anything).
        """
        counter_deltas: dict[MetricKey, float] = {}
        gauge_values: dict[MetricKey, float] = {}
        diagnostics: list[FailDiagnostic] = []

        # Collect all keys from both snapshots
        all_keys = set(before.values.keys()) | set(after.values.keys())

        for key in sorted(all_keys):
            name = key[0]
            spec = _SPEC_BY_NAME.get(name)
            if spec is None:
                continue

            before_val = before.values.get(key, 0.0)
            after_val = after.values.get(key, 0.0)

            if spec.kind == "counter":
                delta = after_val - before_val
                if delta < 0:
                    diagnostics.append(FailDiagnostic(
                        scenario_id=context_scenario or "delta",
                        dependency=name,
                        outcome="negative_counter_delta",
                        observed=after_val,
                        expected=f">= {before_val}",
                        seed=context_seed,
                    ))
                    # Still record the (invalid) delta for visibility
                counter_deltas[key] = delta

            elif spec.kind == "gauge":
                # Gauge: record raw after-value, no invariant
                gauge_values[key] = after_val

        return MetricDelta(
            counter_deltas=counter_deltas,
            gauge_values=gauge_values,
            diagnostics=diagnostics,
        )


# ── Helpers ──────────────────────────────────────────────────────────────

def _resolve_to_whitelist(name: str) -> str | None:
    """
    Resolve a prometheus_client name (MetricFamily or Sample) to a whitelist canonical name.

    prometheus_client naming for counters:
      - MetricFamily.name: 'ptf_admin_dependency_call' (base, no _total)
      - Sample.name: 'ptf_admin_dependency_call_total'
      - Sample.name: 'ptf_admin_dependency_call_created'

    Whitelist canonical names use the _total form for counters.
    Gauges: MetricFamily.name == Sample.name == canonical name.

    Returns canonical name if matched, None otherwise.
    """
    # Direct match (gauge names, or if whitelist name == sample name)
    if name in _WHITELIST_NAMES:
        return name

    # Counter: sample name is 'base_total', MF name is 'base'
    # Try adding _total to see if it matches whitelist
    candidate = name + "_total"
    if candidate in _WHITELIST_NAMES:
        return candidate

    # Counter: sample name might be 'base_total_created' or similar — skip
    return None
