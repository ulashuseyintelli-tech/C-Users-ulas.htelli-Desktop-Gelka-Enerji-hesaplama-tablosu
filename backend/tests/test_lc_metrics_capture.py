"""
MetricsCapture tests — snapshot, delta, whitelist, label schema, negative guard.

Feature: load-characterization, Task 2.1
Validates: R2 (2.1–2.6), GNK-1
"""
from __future__ import annotations

import pytest
from prometheus_client import CollectorRegistry

from backend.app.ptf_metrics import PTFMetrics
from backend.app.testing.metrics_capture import (
    LC_WHITELIST,
    MetricDelta,
    MetricSnapshot,
    MetricsCapture,
    _WHITELIST_NAMES,
)


# ── Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def isolated_metrics() -> PTFMetrics:
    """Fresh PTFMetrics with isolated registry (LC-4)."""
    return PTFMetrics(registry=CollectorRegistry())


@pytest.fixture
def capture(isolated_metrics: PTFMetrics) -> MetricsCapture:
    return MetricsCapture(isolated_metrics.registry)


# ── Whitelist filtering ──────────────────────────────────────────────────

class TestWhitelistFiltering:
    def test_snapshot_only_contains_whitelisted_metrics(
        self, isolated_metrics: PTFMetrics, capture: MetricsCapture
    ):
        """Snapshot keys must only contain LC whitelist metric names."""
        # Trigger some non-whitelisted metrics
        isolated_metrics.inc_upsert("provisional")
        isolated_metrics.inc_api_request("/test", "GET", 200)

        # Trigger whitelisted metrics
        isolated_metrics.inc_dependency_call("db", "success")
        isolated_metrics.inc_guard_failopen()

        snap = capture.take_snapshot()

        for key in snap.values:
            assert key[0] in _WHITELIST_NAMES, (
                f"Non-whitelisted metric in snapshot: {key[0]}"
            )

    def test_whitelist_has_five_metrics(self):
        """LC whitelist must contain exactly 5 metrics."""
        assert len(LC_WHITELIST) == 5

    def test_empty_registry_gives_empty_snapshot(self):
        """Fresh registry with no increments → empty snapshot values."""
        reg = CollectorRegistry()
        # No PTFMetrics registered → no metrics at all
        cap = MetricsCapture(reg)
        snap = cap.take_snapshot()
        assert len(snap.values) == 0


# ── Counter delta ────────────────────────────────────────────────────────

class TestCounterDelta:
    def test_basic_counter_delta(
        self, isolated_metrics: PTFMetrics, capture: MetricsCapture
    ):
        """before=0, after=5 → delta=5 for dependency_call_total."""
        before = capture.take_snapshot()

        for _ in range(5):
            isolated_metrics.inc_dependency_call("db", "success")

        after = capture.take_snapshot()
        delta = capture.compute_delta(before, after)

        # Find the dependency_call_total key for db/success
        found = False
        for key, val in delta.counter_deltas.items():
            if key[0] == "ptf_admin_dependency_call_total":
                labels = dict(key[1])
                if labels.get("dependency") == "db" and labels.get("outcome") == "success":
                    assert val == 5.0
                    found = True
        assert found, "dependency_call_total{db,success} not found in delta"

    def test_multiple_label_combinations(
        self, isolated_metrics: PTFMetrics, capture: MetricsCapture
    ):
        """Different label combos tracked independently."""
        before = capture.take_snapshot()

        isolated_metrics.inc_dependency_call("db", "success")
        isolated_metrics.inc_dependency_call("db", "success")
        isolated_metrics.inc_dependency_call("db", "failure")
        isolated_metrics.inc_dependency_call("cache", "success")

        after = capture.take_snapshot()
        delta = capture.compute_delta(before, after)

        deltas_by_labels: dict[tuple, float] = {}
        for key, val in delta.counter_deltas.items():
            if key[0] == "ptf_admin_dependency_call_total":
                deltas_by_labels[key[1]] = val

        # db/success=2, db/failure=1, cache/success=1
        assert len(deltas_by_labels) == 3

    def test_zero_delta_when_no_change(
        self, isolated_metrics: PTFMetrics, capture: MetricsCapture
    ):
        """No increments between snapshots → all deltas are 0."""
        isolated_metrics.inc_dependency_call("db", "success")
        before = capture.take_snapshot()
        after = capture.take_snapshot()
        delta = capture.compute_delta(before, after)

        for val in delta.counter_deltas.values():
            assert val == 0.0

    def test_failopen_counter_no_labels(
        self, isolated_metrics: PTFMetrics, capture: MetricsCapture
    ):
        """guard_failopen_total has no labels."""
        before = capture.take_snapshot()
        isolated_metrics.inc_guard_failopen()
        isolated_metrics.inc_guard_failopen()
        after = capture.take_snapshot()
        delta = capture.compute_delta(before, after)

        found = False
        for key, val in delta.counter_deltas.items():
            if key[0] == "ptf_admin_guard_failopen_total":
                assert val == 2.0
                assert key[1] == frozenset()  # no labels
                found = True
        assert found

    def test_map_miss_counter(
        self, isolated_metrics: PTFMetrics, capture: MetricsCapture
    ):
        """dependency_map_miss_total increments correctly."""
        before = capture.take_snapshot()
        isolated_metrics.inc_dependency_map_miss()
        after = capture.take_snapshot()
        delta = capture.compute_delta(before, after)

        found = False
        for key, val in delta.counter_deltas.items():
            if key[0] == "ptf_admin_dependency_map_miss_total":
                assert val == 1.0
                found = True
        assert found


# ── Negative counter delta guard ─────────────────────────────────────────

class TestNegativeCounterGuard:
    def test_negative_delta_produces_diagnostic(self):
        """
        If after < before for a counter, FailDiagnostic is generated.
        GNK-1: diagnostic payload format.
        """
        # Manually construct snapshots to simulate impossible state
        key = ("ptf_admin_dependency_call_total", frozenset({("dependency", "db"), ("outcome", "success")}))
        before = MetricSnapshot(values={key: 10.0})
        after = MetricSnapshot(values={key: 5.0})

        reg = CollectorRegistry()
        cap = MetricsCapture(reg)
        delta = cap.compute_delta(before, after, context_seed=42, context_scenario="test_neg")

        assert not delta.invariant_ok
        assert len(delta.diagnostics) == 1

        diag = delta.diagnostics[0]
        assert diag.scenario_id == "test_neg"
        assert diag.dependency == "ptf_admin_dependency_call_total"
        assert diag.outcome == "negative_counter_delta"
        assert diag.observed == 5.0
        assert diag.seed == 42

    def test_no_diagnostic_on_valid_delta(
        self, isolated_metrics: PTFMetrics, capture: MetricsCapture
    ):
        """Normal increment → no diagnostics."""
        before = capture.take_snapshot()
        isolated_metrics.inc_dependency_call("db", "success")
        after = capture.take_snapshot()
        delta = capture.compute_delta(before, after)

        assert delta.invariant_ok
        assert len(delta.diagnostics) == 0


# ── Gauge handling ───────────────────────────────────────────────────────

class TestGaugeHandling:
    def test_circuit_breaker_state_captured_as_gauge(
        self, isolated_metrics: PTFMetrics, capture: MetricsCapture
    ):
        """circuit_breaker_state is a gauge → raw after-value, no delta."""
        isolated_metrics.set_circuit_breaker_state("db", 0)  # CLOSED
        before = capture.take_snapshot()

        isolated_metrics.set_circuit_breaker_state("db", 2)  # OPEN
        after = capture.take_snapshot()

        delta = capture.compute_delta(before, after)

        # Gauge goes to gauge_values, not counter_deltas
        found = False
        for key, val in delta.gauge_values.items():
            if key[0] == "ptf_admin_circuit_breaker_state":
                labels = dict(key[1])
                if labels.get("dependency") == "db":
                    assert val == 2.0  # OPEN
                    found = True
        assert found

        # No counter_deltas for gauge metrics
        for key in delta.counter_deltas:
            assert key[0] != "ptf_admin_circuit_breaker_state"

    def test_gauge_no_invariant_on_negative(
        self, isolated_metrics: PTFMetrics, capture: MetricsCapture
    ):
        """Gauge going from 2 to 0 is valid — no diagnostic."""
        isolated_metrics.set_circuit_breaker_state("db", 2)
        before = capture.take_snapshot()

        isolated_metrics.set_circuit_breaker_state("db", 0)
        after = capture.take_snapshot()

        delta = capture.compute_delta(before, after)
        assert delta.invariant_ok  # No diagnostics for gauge changes


# ── Retry amplification ──────────────────────────────────────────────────

class TestRetryAmplification:
    def test_retry_amplification_basic(
        self, isolated_metrics: PTFMetrics, capture: MetricsCapture
    ):
        """10 calls + 5 retries → amplification = 0.5."""
        before = capture.take_snapshot()

        for _ in range(10):
            isolated_metrics.inc_dependency_call("db", "success")
        for _ in range(5):
            isolated_metrics.inc_dependency_retry("db")

        after = capture.take_snapshot()
        delta = capture.compute_delta(before, after)

        assert abs(delta.retry_amplification - 0.5) < 1e-9

    def test_retry_amplification_zero_calls(self):
        """No calls → amplification = 0.0 (no division by zero)."""
        delta = MetricDelta(counter_deltas={}, gauge_values={})
        assert delta.retry_amplification == 0.0

    def test_assert_retry_amp_close_passes(
        self, isolated_metrics: PTFMetrics, capture: MetricsCapture
    ):
        """R2 AC4: within tolerance → no assertion error."""
        before = capture.take_snapshot()
        for _ in range(100):
            isolated_metrics.inc_dependency_call("db", "success")
        for _ in range(20):
            isolated_metrics.inc_dependency_retry("db")
        after = capture.take_snapshot()
        delta = capture.compute_delta(before, after)

        # Expected 0.2, actual 0.2 → passes
        delta.assert_retry_amp_close(0.2)

    def test_assert_retry_amp_close_fails(
        self, isolated_metrics: PTFMetrics, capture: MetricsCapture
    ):
        """R2 AC4: outside tolerance → AssertionError."""
        before = capture.take_snapshot()
        for _ in range(100):
            isolated_metrics.inc_dependency_call("db", "success")
        for _ in range(50):
            isolated_metrics.inc_dependency_retry("db")
        after = capture.take_snapshot()
        delta = capture.compute_delta(before, after)

        # Expected 0.2, actual 0.5 → fails
        with pytest.raises(AssertionError, match="retry_amplification mismatch"):
            delta.assert_retry_amp_close(0.2)


# ── Registry isolation ───────────────────────────────────────────────────

class TestRegistryIsolation:
    def test_two_registries_independent(self):
        """LC-4: Two PTFMetrics instances don't interfere."""
        m1 = PTFMetrics(registry=CollectorRegistry())
        m2 = PTFMetrics(registry=CollectorRegistry())

        cap1 = MetricsCapture(m1.registry)
        cap2 = MetricsCapture(m2.registry)

        before1 = cap1.take_snapshot()
        before2 = cap2.take_snapshot()

        # Only increment m1
        m1.inc_dependency_call("db", "success")
        m1.inc_dependency_call("db", "success")
        m1.inc_dependency_call("db", "success")

        after1 = cap1.take_snapshot()
        after2 = cap2.take_snapshot()

        delta1 = cap1.compute_delta(before1, after1)
        delta2 = cap2.compute_delta(before2, after2)

        # m1 should have delta=3
        total1 = sum(
            v for k, v in delta1.counter_deltas.items()
            if k[0] == "ptf_admin_dependency_call_total"
        )
        assert total1 == 3.0

        # m2 should have delta=0 (or no keys at all)
        total2 = sum(
            v for k, v in delta2.counter_deltas.items()
            if k[0] == "ptf_admin_dependency_call_total"
        )
        assert total2 == 0.0


# ── Summary serialization ────────────────────────────────────────────────

class TestSummary:
    def test_summary_has_expected_keys(
        self, isolated_metrics: PTFMetrics, capture: MetricsCapture
    ):
        """Delta summary contains all required fields."""
        before = capture.take_snapshot()
        isolated_metrics.inc_dependency_call("db", "success")
        after = capture.take_snapshot()
        delta = capture.compute_delta(before, after)

        s = delta.summary()
        assert "counter_deltas" in s
        assert "gauge_values" in s
        assert "retry_amplification" in s
        assert "invariant_ok" in s
        assert "diagnostic_count" in s
        assert s["invariant_ok"] is True
        assert s["diagnostic_count"] == 0
