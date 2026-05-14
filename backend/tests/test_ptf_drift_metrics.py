"""
Tests for PTF drift metrics — Phase 2 T2.3 (ptf-sot-unification).

Validates:
  - PTFMetrics.inc_ptf_drift_observed and set_ptf_canonical_monthly_avg
    increment / set the new ptf_drift_observed_total{period,severity} and
    ptf_canonical_monthly_avg{period} series correctly.
  - record_drift (with metrics emission wired in T2.3) increments the
    Prometheus counter exactly once per accepted DriftRecord.
  - The pricing path is fail-open: if metrics emission raises (registry
    bug, prometheus_client absent, label cardinality blow-up, etc.),
    record_drift still returns the DB write outcome and pricing proceeds.
  - Closed-set severity validation: invalid severities are dropped with
    a warning, no exception. Empty period likewise rejected.
  - Bounded label cardinality: counter values accumulate per (period,
    severity) combination but never produce extra series for unknown
    severity values.

These metrics are observation-only telemetry. They MUST NOT appear in any
pricing decision — that is the locked Phase 2 invariant. The tests below
verify the emission and fail-open contract; the pricing-path side-effect
contract is verified separately in `test_ptf_dual_read.py`.

Feature: ptf-sot-unification, Task T2.3
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from hypothesis import HealthCheck, given, settings as h_settings, strategies as st
from prometheus_client import CollectorRegistry

from app.ptf_drift_log import DriftRecord, _emit_drift_metrics, record_drift
from app.ptf_metrics import PTFMetrics


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _isolated_metrics() -> PTFMetrics:
    """Build a fresh PTFMetrics instance against a private registry.

    The module-level singleton uses the global registry; tests need
    isolation so counters don't leak across test cases.
    """
    return PTFMetrics(registry=CollectorRegistry())


def _counter_value(metrics: PTFMetrics, period: str, severity: str) -> float:
    """Read the current value of ptf_drift_observed_total{period,severity}."""
    return metrics._ptf_drift_observed_total.labels(
        period=period, severity=severity
    )._value.get()


def _gauge_value(metrics: PTFMetrics, period: str) -> float:
    """Read the current value of ptf_canonical_monthly_avg{period}."""
    return metrics._ptf_canonical_monthly_avg.labels(period=period)._value.get()


def _make_records(count: int, value: float):
    """Build a list of `count` ParsedMarketRecord-like objects.

    compute_drift only inspects `.ptf_tl_per_mwh`, so a tiny duck-type
    works here without dragging in the full ParsedMarketRecord class.
    """
    rec = MagicMock()
    rec.ptf_tl_per_mwh = value
    return [rec for _ in range(count)]


# ═════════════════════════════════════════════════════════════════════════════
# Unit tests — PTFMetrics methods
# ═════════════════════════════════════════════════════════════════════════════


class TestIncPtfDriftObserved:
    """ptf_drift_observed_total{period,severity} counter — direct method tests."""

    def test_low_severity_increments(self):
        m = _isolated_metrics()
        m.inc_ptf_drift_observed(period="2025-06", severity="low")
        assert _counter_value(m, "2025-06", "low") == 1.0

    def test_high_severity_increments(self):
        m = _isolated_metrics()
        m.inc_ptf_drift_observed(period="2025-06", severity="high")
        assert _counter_value(m, "2025-06", "high") == 1.0

    def test_missing_legacy_severity_increments(self):
        m = _isolated_metrics()
        m.inc_ptf_drift_observed(period="2025-06", severity="missing_legacy")
        assert _counter_value(m, "2025-06", "missing_legacy") == 1.0

    def test_invalid_severity_no_increment(self):
        m = _isolated_metrics()
        m.inc_ptf_drift_observed(period="2025-06", severity="critical")
        # No exception, no increment for any severity
        assert _counter_value(m, "2025-06", "low") == 0.0
        assert _counter_value(m, "2025-06", "high") == 0.0
        assert _counter_value(m, "2025-06", "missing_legacy") == 0.0

    def test_empty_period_no_increment(self):
        m = _isolated_metrics()
        m.inc_ptf_drift_observed(period="", severity="low")
        # No exception, no row created. We can't read with empty label so
        # we just check that no other labels were affected.
        assert _counter_value(m, "2025-06", "low") == 0.0

    def test_per_period_isolation(self):
        m = _isolated_metrics()
        m.inc_ptf_drift_observed(period="2025-06", severity="low")
        m.inc_ptf_drift_observed(period="2025-07", severity="low")
        m.inc_ptf_drift_observed(period="2025-06", severity="low")
        assert _counter_value(m, "2025-06", "low") == 2.0
        assert _counter_value(m, "2025-07", "low") == 1.0

    def test_per_severity_isolation(self):
        m = _isolated_metrics()
        m.inc_ptf_drift_observed(period="2025-06", severity="low")
        m.inc_ptf_drift_observed(period="2025-06", severity="high")
        m.inc_ptf_drift_observed(period="2025-06", severity="high")
        assert _counter_value(m, "2025-06", "low") == 1.0
        assert _counter_value(m, "2025-06", "high") == 2.0


class TestSetPtfCanonicalMonthlyAvg:
    """ptf_canonical_monthly_avg{period} gauge — direct method tests."""

    def test_set_basic(self):
        m = _isolated_metrics()
        m.set_ptf_canonical_monthly_avg(period="2025-06", value=2508.80)
        assert _gauge_value(m, "2025-06") == pytest.approx(2508.80)

    def test_set_overwrite(self):
        m = _isolated_metrics()
        m.set_ptf_canonical_monthly_avg(period="2025-06", value=100.0)
        m.set_ptf_canonical_monthly_avg(period="2025-06", value=200.0)
        # Gauge replaces, doesn't accumulate
        assert _gauge_value(m, "2025-06") == pytest.approx(200.0)

    def test_set_zero_allowed(self):
        m = _isolated_metrics()
        m.set_ptf_canonical_monthly_avg(period="2025-06", value=0.0)
        assert _gauge_value(m, "2025-06") == 0.0

    def test_set_negative_allowed(self):
        # Negative PTF is operationally unusual but Prometheus shouldn't
        # discard it — operators may need to see the wrong-direction value.
        m = _isolated_metrics()
        m.set_ptf_canonical_monthly_avg(period="2025-06", value=-50.0)
        assert _gauge_value(m, "2025-06") == pytest.approx(-50.0)

    def test_empty_period_no_set(self):
        m = _isolated_metrics()
        m.set_ptf_canonical_monthly_avg(period="", value=100.0)
        # Other periods unaffected
        assert _gauge_value(m, "2025-06") == 0.0  # default

    def test_nan_value_ignored(self):
        m = _isolated_metrics()
        m.set_ptf_canonical_monthly_avg(period="2025-06", value=float("nan"))
        # Default value (0) preserved
        assert _gauge_value(m, "2025-06") == 0.0

    def test_non_numeric_value_ignored(self):
        m = _isolated_metrics()
        m.set_ptf_canonical_monthly_avg(period="2025-06", value="2508.80")  # type: ignore[arg-type]
        # str is coerced via float() and accepted — that's the documented behavior.
        assert _gauge_value(m, "2025-06") == pytest.approx(2508.80)

    def test_garbage_value_ignored(self):
        m = _isolated_metrics()
        m.set_ptf_canonical_monthly_avg(period="2025-06", value="not-a-number")  # type: ignore[arg-type]
        assert _gauge_value(m, "2025-06") == 0.0


# ═════════════════════════════════════════════════════════════════════════════
# Property-based tests — counter monotonicity & cardinality bounds
# ═════════════════════════════════════════════════════════════════════════════


class TestPtfDriftMetricsPropertyBased:
    """Property: counter is monotonic; gauge converges to last value."""

    @h_settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        period=st.sampled_from(
            ["2025-01", "2025-06", "2025-12", "2026-01", "2026-05"]
        ),
        severity=st.sampled_from(["low", "high", "missing_legacy"]),
        count=st.integers(min_value=1, max_value=20),
    )
    def test_counter_monotonic_per_label_combo(self, period, severity, count):
        """ptf_drift_observed_total is monotonic and increments by exactly 1
        per call within a fresh registry."""
        m = _isolated_metrics()
        for _ in range(count):
            m.inc_ptf_drift_observed(period=period, severity=severity)
        assert _counter_value(m, period, severity) == count

    @h_settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        period=st.sampled_from(
            ["2025-01", "2025-06", "2025-12", "2026-01", "2026-05"]
        ),
        values=st.lists(
            st.floats(min_value=0.01, max_value=10_000.0, allow_nan=False),
            min_size=1, max_size=10,
        ),
    )
    def test_gauge_reflects_last_set(self, period, values):
        """ptf_canonical_monthly_avg is a gauge — only the last value persists."""
        m = _isolated_metrics()
        for v in values:
            m.set_ptf_canonical_monthly_avg(period=period, value=v)
        assert _gauge_value(m, period) == pytest.approx(values[-1])


# ═════════════════════════════════════════════════════════════════════════════
# Integration tests — _emit_drift_metrics + record_drift
# ═════════════════════════════════════════════════════════════════════════════


class TestEmitDriftMetricsIntegration:
    """_emit_drift_metrics calls inc + set on the singleton metrics instance.

    These tests patch get_ptf_metrics to point at an isolated registry so
    they can read counter values back deterministically.
    """

    def test_emit_low_severity(self):
        isolated = _isolated_metrics()
        record = DriftRecord(
            period="2025-06",
            canonical_price=2500.0,
            legacy_price=2495.0,
            delta_abs=5.0,
            delta_pct=0.2,
            severity="low",
            request_hash="a" * 64,
        )
        with patch("app.ptf_metrics.get_ptf_metrics", return_value=isolated):
            _emit_drift_metrics(record)
        assert _counter_value(isolated, "2025-06", "low") == 1.0
        assert _gauge_value(isolated, "2025-06") == pytest.approx(2500.0)

    def test_emit_high_severity(self):
        isolated = _isolated_metrics()
        record = DriftRecord(
            period="2025-07",
            canonical_price=3000.0,
            legacy_price=2900.0,
            delta_abs=100.0,
            delta_pct=3.45,
            severity="high",
            request_hash="b" * 64,
        )
        with patch("app.ptf_metrics.get_ptf_metrics", return_value=isolated):
            _emit_drift_metrics(record)
        assert _counter_value(isolated, "2025-07", "high") == 1.0
        assert _gauge_value(isolated, "2025-07") == pytest.approx(3000.0)

    def test_emit_missing_legacy(self):
        isolated = _isolated_metrics()
        record = DriftRecord(
            period="2025-08",
            canonical_price=2700.0,
            legacy_price=None,
            delta_abs=None,
            delta_pct=None,
            severity="missing_legacy",
            request_hash="c" * 64,
        )
        with patch("app.ptf_metrics.get_ptf_metrics", return_value=isolated):
            _emit_drift_metrics(record)
        assert _counter_value(isolated, "2025-08", "missing_legacy") == 1.0
        # canonical_price is still set even though legacy is None
        assert _gauge_value(isolated, "2025-08") == pytest.approx(2700.0)

    def test_emit_none_record_is_noop(self):
        """Defensive: None record should not raise, should not touch metrics."""
        isolated = _isolated_metrics()
        with patch("app.ptf_metrics.get_ptf_metrics", return_value=isolated):
            _emit_drift_metrics(None)  # type: ignore[arg-type]
        # No state change — sanity check on a known label
        assert _counter_value(isolated, "2025-06", "low") == 0.0


class TestEmitDriftMetricsFailOpen:
    """The metrics emission is fail-open: any exception is suppressed.

    These tests are the most important in this file. If any of them
    fails, a metrics outage will become a pricing outage — the locked
    Phase 2 invariant is violated.
    """

    def test_get_ptf_metrics_raises(self):
        """If the metrics singleton accessor itself raises, no propagation."""
        record = DriftRecord(
            period="2025-06",
            canonical_price=2500.0,
            severity="low",
            request_hash="a" * 64,
        )
        with patch(
            "app.ptf_metrics.get_ptf_metrics",
            side_effect=RuntimeError("metrics broken"),
        ):
            # Must NOT raise.
            _emit_drift_metrics(record)

    def test_inc_raises(self):
        """If inc_ptf_drift_observed raises mid-emission, no propagation."""
        record = DriftRecord(
            period="2025-06",
            canonical_price=2500.0,
            severity="low",
            request_hash="a" * 64,
        )
        broken = MagicMock()
        broken.inc_ptf_drift_observed.side_effect = RuntimeError("counter broken")
        with patch("app.ptf_metrics.get_ptf_metrics", return_value=broken):
            _emit_drift_metrics(record)
        # Sanity: side_effect was actually triggered, proving we passed
        # through the path that would have raised.
        assert broken.inc_ptf_drift_observed.called

    def test_set_raises_after_inc_succeeded(self):
        """If gauge set raises after counter inc, no propagation, no rollback."""
        record = DriftRecord(
            period="2025-06",
            canonical_price=2500.0,
            severity="low",
            request_hash="a" * 64,
        )
        partial = MagicMock()
        partial.set_ptf_canonical_monthly_avg.side_effect = RuntimeError("gauge broken")
        with patch("app.ptf_metrics.get_ptf_metrics", return_value=partial):
            _emit_drift_metrics(record)
        # Counter call still happened — no rollback expected, this is a
        # fire-and-forget telemetry surface.
        assert partial.inc_ptf_drift_observed.called


class TestRecordDriftEmitsMetrics:
    """End-to-end: record_drift increments the counter via _emit_drift_metrics.

    Validates: tasks.md T2.3 "Her drift_record çağrısı counter'ı artırır"
    """

    def _stub_session(self) -> MagicMock:
        """A SQLAlchemy session that accepts add/commit silently."""
        return MagicMock()

    def test_record_drift_low_increments_counter(self):
        isolated = _isolated_metrics()
        canonical = _make_records(24, 100.0)
        legacy = _make_records(1, 100.0)  # equal → severity=low
        with patch("app.ptf_metrics.get_ptf_metrics", return_value=isolated):
            ok = record_drift(
                self._stub_session(), canonical, legacy,
                period="2025-06", request_hash="a" * 64,
            )
        assert ok is True
        assert _counter_value(isolated, "2025-06", "low") == 1.0
        assert _gauge_value(isolated, "2025-06") == pytest.approx(100.0)

    def test_record_drift_high_increments_counter(self):
        isolated = _isolated_metrics()
        canonical = _make_records(24, 100.0)
        legacy = _make_records(1, 200.0)  # 50% drift → severity=high
        with patch("app.ptf_metrics.get_ptf_metrics", return_value=isolated):
            ok = record_drift(
                self._stub_session(), canonical, legacy,
                period="2025-07", request_hash="b" * 64,
            )
        assert ok is True
        assert _counter_value(isolated, "2025-07", "high") == 1.0

    def test_record_drift_missing_legacy_increments_counter(self):
        isolated = _isolated_metrics()
        canonical = _make_records(24, 100.0)
        with patch("app.ptf_metrics.get_ptf_metrics", return_value=isolated):
            ok = record_drift(
                self._stub_session(), canonical, None,
                period="2025-08", request_hash="c" * 64,
            )
        assert ok is True
        assert _counter_value(isolated, "2025-08", "missing_legacy") == 1.0

    def test_record_drift_canonical_empty_does_not_increment(self):
        """compute_drift returns None for empty canonical — no metric."""
        isolated = _isolated_metrics()
        with patch("app.ptf_metrics.get_ptf_metrics", return_value=isolated):
            ok = record_drift(
                self._stub_session(), [], None,
                period="2025-09", request_hash="d" * 64,
            )
        assert ok is False
        # No series for any severity should exist for this period
        assert _counter_value(isolated, "2025-09", "low") == 0.0
        assert _counter_value(isolated, "2025-09", "high") == 0.0
        assert _counter_value(isolated, "2025-09", "missing_legacy") == 0.0

    def test_record_drift_metrics_emission_failure_does_not_block_db_write(self):
        """LOCKED INVARIANT: metrics outage MUST NOT prevent DB write.

        record_drift calls _emit_drift_metrics BEFORE write_drift_record.
        If emission raises (it shouldn't, because of the inner try/except,
        but defense in depth), the write must still happen.
        """
        canonical = _make_records(24, 100.0)
        legacy = _make_records(1, 100.0)
        session = self._stub_session()
        with patch(
            "app.ptf_metrics.get_ptf_metrics",
            side_effect=RuntimeError("singleton broken"),
        ):
            ok = record_drift(
                session, canonical, legacy,
                period="2025-06", request_hash="a" * 64,
            )
        # DB write succeeded — metrics outage transparently absorbed.
        assert ok is True
        assert session.add.called
        assert session.commit.called


# ═════════════════════════════════════════════════════════════════════════════
# Exposition test — /metrics endpoint surfaces the new metrics
# ═════════════════════════════════════════════════════════════════════════════


class TestPrometheusExposition:
    """generate_metrics() output includes the new series after a record."""

    def test_drift_observed_series_appears_in_exposition(self):
        m = _isolated_metrics()
        m.inc_ptf_drift_observed(period="2025-06", severity="low")
        text = m.generate_metrics().decode("utf-8")
        # The HELP line includes the metric name; the labelled series line
        # is what scrapers actually consume.
        assert "ptf_drift_observed_total" in text
        assert 'period="2025-06"' in text
        assert 'severity="low"' in text

    def test_canonical_monthly_avg_appears_in_exposition(self):
        m = _isolated_metrics()
        m.set_ptf_canonical_monthly_avg(period="2025-06", value=2508.80)
        text = m.generate_metrics().decode("utf-8")
        assert "ptf_canonical_monthly_avg" in text
        assert 'period="2025-06"' in text
        # Value is present in the text, allow for either 2508.8 or 2508.80
        assert "2508.8" in text
