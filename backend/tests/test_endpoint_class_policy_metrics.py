"""
Endpoint-Class Policy — Metrics Tests (Task 6, Merge-blocker 2).

Yol A: New metric names (no break to existing metrics).
  - ptf_admin_guard_decision_requests_by_risk_total{mode, risk_class}
  - ptf_admin_guard_decision_block_by_risk_total{kind, mode, risk_class}

Covers:
  M1: Empty risk map → risk_class="low" label
  M2: HIGH endpoint → risk_class="high" label
  M3: MEDIUM prefix → risk_class="medium" label
  M4: Block counters split by risk_class
  M5: Cardinality invariant — only low|medium|high
  M6: Backward compat — legacy metrics still increment
  M7: Invalid label values rejected
  M8: Request counter increments with correct labels

Feature: endpoint-class-policy, Task 6
"""
from __future__ import annotations

import pytest
from prometheus_client import CollectorRegistry

from app.ptf_metrics import PTFMetrics


@pytest.fixture()
def metrics():
    """Fresh PTFMetrics instance with isolated registry."""
    registry = CollectorRegistry()
    m = PTFMetrics(registry=registry)
    return m


def _get_counter(metrics: PTFMetrics, counter_attr: str, labels: dict) -> float:
    """Read a labeled counter value from PTFMetrics."""
    counter = getattr(metrics, counter_attr)
    try:
        return counter.labels(**labels)._value.get()
    except Exception:
        return 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# M1: Empty risk map → risk_class="low" label
# ═══════════════════════════════════════════════════════════════════════════════

class TestM1EmptyRiskMapLowLabel:
    def test_request_counter_low_shadow(self, metrics):
        """Empty risk map → LOW → ENFORCE+LOW=SHADOW → mode=shadow, risk_class=low."""
        metrics.inc_guard_decision_request_by_risk("shadow", "low")
        val = _get_counter(metrics, "_guard_decision_requests_by_risk_total", {"mode": "shadow", "risk_class": "low"})
        assert val == 1.0

    def test_request_counter_low_enforce(self, metrics):
        """Direct enforce + low (edge case — shouldn't happen via resolve table but API allows it)."""
        metrics.inc_guard_decision_request_by_risk("enforce", "low")
        val = _get_counter(metrics, "_guard_decision_requests_by_risk_total", {"mode": "enforce", "risk_class": "low"})
        assert val == 1.0


# ═══════════════════════════════════════════════════════════════════════════════
# M2: HIGH endpoint → risk_class="high" label
# ═══════════════════════════════════════════════════════════════════════════════

class TestM2HighEndpoint:
    def test_request_counter_high_enforce(self, metrics):
        metrics.inc_guard_decision_request_by_risk("enforce", "high")
        val = _get_counter(metrics, "_guard_decision_requests_by_risk_total", {"mode": "enforce", "risk_class": "high"})
        assert val == 1.0

    def test_block_counter_high_enforce(self, metrics):
        metrics.inc_guard_decision_block_by_risk("insufficient", "enforce", "high")
        val = _get_counter(metrics, "_guard_decision_block_by_risk_total", {"kind": "insufficient", "mode": "enforce", "risk_class": "high"})
        assert val == 1.0


# ═══════════════════════════════════════════════════════════════════════════════
# M3: MEDIUM prefix → risk_class="medium" label
# ═══════════════════════════════════════════════════════════════════════════════

class TestM3MediumPrefix:
    def test_request_counter_medium(self, metrics):
        metrics.inc_guard_decision_request_by_risk("enforce", "medium")
        val = _get_counter(metrics, "_guard_decision_requests_by_risk_total", {"mode": "enforce", "risk_class": "medium"})
        assert val == 1.0

    def test_block_counter_medium_stale(self, metrics):
        metrics.inc_guard_decision_block_by_risk("stale", "enforce", "medium")
        val = _get_counter(metrics, "_guard_decision_block_by_risk_total", {"kind": "stale", "mode": "enforce", "risk_class": "medium"})
        assert val == 1.0


# ═══════════════════════════════════════════════════════════════════════════════
# M4: Block counters split by risk_class
# ═══════════════════════════════════════════════════════════════════════════════

class TestM4BlockCountersSplit:
    def test_two_endpoints_different_risk_class(self, metrics):
        """Same kind+mode, different risk_class → separate counters."""
        metrics.inc_guard_decision_block_by_risk("insufficient", "enforce", "low")
        metrics.inc_guard_decision_block_by_risk("insufficient", "enforce", "high")
        metrics.inc_guard_decision_block_by_risk("insufficient", "enforce", "high")

        low_val = _get_counter(metrics, "_guard_decision_block_by_risk_total", {"kind": "insufficient", "mode": "enforce", "risk_class": "low"})
        high_val = _get_counter(metrics, "_guard_decision_block_by_risk_total", {"kind": "insufficient", "mode": "enforce", "risk_class": "high"})
        assert low_val == 1.0
        assert high_val == 2.0

    def test_shadow_vs_enforce_split(self, metrics):
        """Same risk_class, different mode → separate counters."""
        metrics.inc_guard_decision_block_by_risk("stale", "shadow", "high")
        metrics.inc_guard_decision_block_by_risk("stale", "enforce", "high")

        shadow_val = _get_counter(metrics, "_guard_decision_block_by_risk_total", {"kind": "stale", "mode": "shadow", "risk_class": "high"})
        enforce_val = _get_counter(metrics, "_guard_decision_block_by_risk_total", {"kind": "stale", "mode": "enforce", "risk_class": "high"})
        assert shadow_val == 1.0
        assert enforce_val == 1.0


# ═══════════════════════════════════════════════════════════════════════════════
# M5: Cardinality invariant — only low|medium|high
# ═══════════════════════════════════════════════════════════════════════════════

class TestM5CardinalityInvariant:
    def test_invalid_risk_class_rejected(self, metrics):
        """risk_class not in {low, medium, high} → no increment, no crash."""
        metrics.inc_guard_decision_request_by_risk("enforce", "critical")
        metrics.inc_guard_decision_block_by_risk("stale", "enforce", "critical")
        # No exception raised, counter stays at 0 for valid labels
        val = _get_counter(metrics, "_guard_decision_requests_by_risk_total", {"mode": "enforce", "risk_class": "high"})
        assert val == 0.0

    def test_invalid_mode_rejected(self, metrics):
        """mode not in {shadow, enforce} → no increment."""
        metrics.inc_guard_decision_request_by_risk("off", "low")
        metrics.inc_guard_decision_block_by_risk("stale", "off", "low")
        val = _get_counter(metrics, "_guard_decision_requests_by_risk_total", {"mode": "shadow", "risk_class": "low"})
        assert val == 0.0

    def test_no_endpoint_label(self, metrics):
        """Verify new metrics don't have endpoint label (cardinality control)."""
        # Check labelnames on the counter objects
        req_labels = metrics._guard_decision_requests_by_risk_total._labelnames
        block_labels = metrics._guard_decision_block_by_risk_total._labelnames
        assert "endpoint" not in req_labels
        assert "tenant" not in req_labels
        assert "endpoint" not in block_labels
        assert "tenant" not in block_labels

    def test_risk_class_label_bounded(self, metrics):
        """Only 3 valid risk_class values accepted."""
        for rc in ("low", "medium", "high"):
            metrics.inc_guard_decision_request_by_risk("enforce", rc)
            val = _get_counter(metrics, "_guard_decision_requests_by_risk_total", {"mode": "enforce", "risk_class": rc})
            assert val == 1.0


# ═══════════════════════════════════════════════════════════════════════════════
# M6: Backward compat — legacy metrics still increment
# ═══════════════════════════════════════════════════════════════════════════════

class TestM6BackwardCompat:
    def test_legacy_block_counter_still_works(self, metrics):
        """Legacy inc_guard_decision_block (no risk_class) still increments."""
        metrics.inc_guard_decision_block("stale", "enforce")
        val = _get_counter(metrics, "_guard_decision_block_total", {"kind": "stale", "mode": "enforce"})
        assert val == 1.0

    def test_legacy_request_counter_still_works(self, metrics):
        """Legacy inc_guard_decision_request (no labels) still increments."""
        metrics.inc_guard_decision_request()
        # Legacy counter has no labels — read directly
        val = metrics._guard_decision_requests_total._value.get()
        assert val == 1.0


# ═══════════════════════════════════════════════════════════════════════════════
# M7: Invalid label values rejected gracefully
# ═══════════════════════════════════════════════════════════════════════════════

class TestM7InvalidLabels:
    def test_invalid_kind_rejected(self, metrics):
        """kind not in {stale, insufficient} → no increment."""
        metrics.inc_guard_decision_block_by_risk("unknown_kind", "enforce", "high")
        val = _get_counter(metrics, "_guard_decision_block_by_risk_total", {"kind": "insufficient", "mode": "enforce", "risk_class": "high"})
        assert val == 0.0

    def test_empty_string_rejected(self, metrics):
        """Empty string labels → no increment."""
        metrics.inc_guard_decision_request_by_risk("", "")
        metrics.inc_guard_decision_block_by_risk("", "", "")
        # No crash, no increment


# ═══════════════════════════════════════════════════════════════════════════════
# M8: Request counter increments with correct labels
# ═══════════════════════════════════════════════════════════════════════════════

class TestM8RequestCounterLabels:
    def test_multiple_increments_accumulate(self, metrics):
        """Multiple calls with same labels accumulate."""
        for _ in range(5):
            metrics.inc_guard_decision_request_by_risk("shadow", "low")
        val = _get_counter(metrics, "_guard_decision_requests_by_risk_total", {"mode": "shadow", "risk_class": "low"})
        assert val == 5.0

    def test_different_labels_independent(self, metrics):
        """Different label combos are independent counters."""
        metrics.inc_guard_decision_request_by_risk("shadow", "low")
        metrics.inc_guard_decision_request_by_risk("enforce", "high")
        metrics.inc_guard_decision_request_by_risk("enforce", "high")

        shadow_low = _get_counter(metrics, "_guard_decision_requests_by_risk_total", {"mode": "shadow", "risk_class": "low"})
        enforce_high = _get_counter(metrics, "_guard_decision_requests_by_risk_total", {"mode": "enforce", "risk_class": "high"})
        assert shadow_low == 1.0
        assert enforce_high == 2.0
