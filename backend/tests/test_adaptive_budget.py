"""
Tests for adaptive control error budget calculator.

Feature: slo-adaptive-control, Tasks 5.2–5.4
MUST Property: P11 (Error Budget Formula Correctness)
Optional Property: P12 (Burn Rate Threshold Triggering)
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given, settings, assume, HealthCheck
from hypothesis import strategies as st

from backend.app.adaptive_control.budget import (
    BudgetStatus,
    ErrorBudgetCalculator,
    ErrorBudgetConfig,
)
from backend.app.testing.slo_evaluator import MetricSample


def make_sample(ts_ms: int, total: int = 1000, successful: int = 990) -> MetricSample:
    return MetricSample(
        timestamp_ms=ts_ms,
        total_requests=total,
        successful_requests=successful,
        latency_p99_seconds=0.1,
    )


# ══════════════════════════════════════════════════════════════════════════════
# MUST Property P11: Error Budget Formula Correctness
# allowed_errors = (1 - t) × w × r, budget_remaining_pct correct.
# Validates: Req 3.1, 3.2, 3.6, 3.7
# ══════════════════════════════════════════════════════════════════════════════

class TestErrorBudgetFormulaPropertyP11:
    """MUST — Property 11: Error Budget Formula Correctness."""

    @given(
        slo_target=st.floats(min_value=0.9, max_value=0.9999),
        window_days=st.integers(min_value=1, max_value=90),
        total_requests=st.integers(min_value=100, max_value=100_000),
        error_count=st.integers(min_value=0, max_value=1000),
    )
    @settings(max_examples=100, derandomize=True, suppress_health_check=[HealthCheck.too_slow])
    def test_budget_formula(
        self, slo_target: float, window_days: int, total_requests: int, error_count: int,
    ):
        """Budget formula: allowed_errors = (1 - t) × w × r."""
        assume(error_count <= total_requests)
        window_seconds = window_days * 86400
        successful = total_requests - error_count

        cfg = ErrorBudgetConfig(
            subsystem_id="guard",
            metric="5xx_rate",
            window_seconds=window_seconds,
            slo_target=slo_target,
        )
        calc = ErrorBudgetCalculator([cfg])

        # Single sample at the midpoint of the window
        now_ms = window_seconds * 1000
        sample = make_sample(now_ms // 2, total=total_requests, successful=successful)
        results = calc.evaluate([sample], now_ms)

        assert len(results) == 1
        status = results[0]

        # Verify formula
        request_rate = total_requests / window_seconds
        expected_budget = (1.0 - slo_target) * window_seconds * request_rate
        assert abs(status.budget_total - expected_budget) < 1e-6, (
            f"budget_total={status.budget_total}, expected={expected_budget}"
        )
        assert status.budget_consumed == float(error_count)

        # Verify remaining pct
        if expected_budget > 0:
            expected_remaining = max(0.0, (1.0 - error_count / expected_budget) * 100.0)
            assert abs(status.budget_remaining_pct - expected_remaining) < 1e-4

    def test_rolling_window_excludes_old_samples(self):
        """Rolling 30d window: samples outside window are excluded."""
        window_seconds = 30 * 86400
        cfg = ErrorBudgetConfig(
            subsystem_id="guard", metric="5xx_rate",
            window_seconds=window_seconds, slo_target=0.999,
        )
        calc = ErrorBudgetCalculator([cfg])

        now_ms = window_seconds * 1000 * 2  # well past the window
        old_sample = make_sample(1000, total=1000, successful=0)  # 100% errors, but old
        new_sample = make_sample(now_ms - 1000, total=1000, successful=999)

        results = calc.evaluate([old_sample, new_sample], now_ms)
        status = results[0]
        # Only new_sample should be counted (1 error)
        assert status.budget_consumed == 1.0

    def test_empty_samples_full_budget(self):
        """No samples → budget_remaining_pct = 100%."""
        cfg = ErrorBudgetConfig(
            subsystem_id="pdf", metric="failed_rate",
            window_seconds=86400, slo_target=0.999,
        )
        calc = ErrorBudgetCalculator([cfg])
        results = calc.evaluate([], 1_000_000)
        assert results[0].budget_remaining_pct == 100.0
        assert results[0].is_exhausted is False


# ══════════════════════════════════════════════════════════════════════════════
# Optional Property P12: Burn Rate Threshold Triggering
# Validates: Req 3.4, 4.5
# ══════════════════════════════════════════════════════════════════════════════

class TestBurnRateThresholdPropertyP12:
    """Optional — Property 12: Burn Rate Threshold Triggering."""

    @given(
        burn_rate_threshold=st.floats(min_value=0.1, max_value=5.0),
        error_fraction=st.floats(min_value=0.0, max_value=0.1),
    )
    @settings(max_examples=100, derandomize=True, suppress_health_check=[HealthCheck.too_slow])
    def test_burn_rate_threshold(self, burn_rate_threshold: float, error_fraction: float):
        """Burn rate > threshold → is_burn_rate_exceeded=True."""
        total = 10000
        errors = int(total * error_fraction)
        successful = total - errors

        cfg = ErrorBudgetConfig(
            subsystem_id="guard", metric="5xx_rate",
            window_seconds=86400, slo_target=0.999,
            burn_rate_threshold=burn_rate_threshold,
        )
        calc = ErrorBudgetCalculator([cfg])
        now_ms = 86400 * 1000
        sample = make_sample(now_ms // 2, total=total, successful=successful)
        results = calc.evaluate([sample], now_ms)
        status = results[0]

        if status.budget_total > 0:
            expected_exceeded = status.burn_rate > burn_rate_threshold
            assert status.is_burn_rate_exceeded == expected_exceeded


# ══════════════════════════════════════════════════════════════════════════════
# Unit Tests (Task 5.4)
# ══════════════════════════════════════════════════════════════════════════════

class TestErrorBudgetCalculatorUnit:
    """Unit tests for ErrorBudgetCalculator."""

    def test_error_budget_config_format(self):
        """Req 3.3: Budget config has metric, window, threshold."""
        cfg = ErrorBudgetConfig(
            subsystem_id="guard", metric="5xx_rate",
            window_seconds=30 * 86400, slo_target=0.999,
        )
        assert cfg.window_seconds == 30 * 86400
        assert cfg.slo_target == 0.999
        assert cfg.metric == "5xx_rate"

    def test_zero_request_rate_budget(self):
        """Edge case: zero requests → no division by zero."""
        cfg = ErrorBudgetConfig(
            subsystem_id="guard", metric="5xx_rate",
            window_seconds=86400, slo_target=0.999,
        )
        calc = ErrorBudgetCalculator([cfg])
        sample = make_sample(43200_000, total=0, successful=0)
        results = calc.evaluate([sample], 86400_000)
        status = results[0]
        assert math.isfinite(status.budget_total)
        assert math.isfinite(status.budget_remaining_pct)

    def test_budget_reset_audit_log(self):
        """Req 3.7: Budget reset via config change produces audit."""
        calc = ErrorBudgetCalculator([])
        new_configs = [
            ErrorBudgetConfig(subsystem_id="guard", metric="5xx_rate"),
        ]
        audit = calc.update_configs(new_configs, actor="ops_user")
        assert audit["action"] == "budget_config_update"
        assert audit["actor"] == "ops_user"
        assert audit["new_version"] == 1

    def test_multiple_subsystems(self):
        """Multiple budget configs evaluated independently."""
        configs = [
            ErrorBudgetConfig(subsystem_id="guard", metric="5xx_rate", slo_target=0.999),
            ErrorBudgetConfig(subsystem_id="pdf", metric="failed_rate", slo_target=0.99),
        ]
        calc = ErrorBudgetCalculator(configs)
        now_ms = 86400 * 1000
        sample = make_sample(now_ms // 2, total=1000, successful=990)
        results = calc.evaluate([sample], now_ms)
        assert len(results) == 2
        assert results[0].subsystem_id == "guard"
        assert results[1].subsystem_id == "pdf"
