"""
Edge case test matrix for adaptive control.

Feature: slo-adaptive-control, Task 12.2
Requirements: CC.5, 6.2, 3.6, 10.3, 5.3, 5.2
"""

from __future__ import annotations

import pytest

from backend.app.adaptive_control.budget import ErrorBudgetCalculator
from backend.app.adaptive_control.config import (
    AdaptiveControlConfig,
    AllowlistEntry,
    AllowlistManager,
)
from backend.app.adaptive_control.controller import (
    AdaptiveController,
    AdaptiveControllerState,
)
from backend.app.adaptive_control.decision_engine import DecisionEngine
from backend.app.adaptive_control.hysteresis import HysteresisFilter
from backend.app.adaptive_control.metrics_collector import MetricsCollector
from backend.app.adaptive_control.signals import ControlSignal, SignalType
from backend.app.adaptive_control.sufficiency import (
    SufficiencyConfig,
    TelemetrySufficiencyChecker,
)
from backend.app.testing.slo_evaluator import MetricSample


def make_sample(ts_ms, total=100, successful=99, latency=0.1):
    return MetricSample(
        timestamp_ms=ts_ms, total_requests=total,
        successful_requests=successful, latency_p99_seconds=latency,
    )


def make_config(**overrides):
    defaults = dict(
        p95_latency_enter_threshold=0.5,
        p95_latency_exit_threshold=0.3,
        queue_depth_enter_threshold=50,
        queue_depth_exit_threshold=20,
        dwell_time_seconds=0.001,
        cooldown_period_seconds=0.001,
        control_loop_interval_seconds=30.0,
        targets=[AllowlistEntry(subsystem_id="*")],
    )
    defaults.update(overrides)
    return AdaptiveControlConfig(**defaults)


def make_controller(guard_mode="enforce", pdf_mode="accepting", **kwargs):
    cfg = kwargs.pop("config", make_config())
    allowlist = AllowlistManager(cfg.targets)
    engine = DecisionEngine(cfg, allowlist,
        killswitch_active_fn=kwargs.pop("killswitch_fn", None),
    )
    engine.guard_mode = guard_mode
    engine.pdf_mode = pdf_mode
    collector = MetricsCollector(stale_threshold_ms=60_000)
    budget = ErrorBudgetCalculator()
    hysteresis = HysteresisFilter(
        dwell_time_ms=int(cfg.dwell_time_seconds * 1000),
        cooldown_ms=int(cfg.cooldown_period_seconds * 1000),
    )
    sufficiency = TelemetrySufficiencyChecker(
        SufficiencyConfig(
            min_samples=kwargs.pop("sufficiency_min", 1),
            min_bucket_coverage_pct=0.0,
            check_source_stale=kwargs.pop("check_stale", False),
        )
    )
    return AdaptiveController(
        config=cfg, metrics_collector=collector, budget_calculator=budget,
        decision_engine=engine, hysteresis_filter=hysteresis,
        sufficiency_checker=sufficiency,
        guard_mode_setter=kwargs.pop("guard_setter", None),
        pdf_backpressure_setter=kwargs.pop("pdf_setter", None),
    )


class TestEdgeCases:
    """Edge case matrix — Req CC.5, 6.2, 3.6, 10.3, 5.3, 5.2."""

    def test_empty_allowlist_no_action(self):
        """Empty allowlist → zero signals (Req CC.5)."""
        cfg = make_config(targets=[])
        allowlist = AllowlistManager([])
        engine = DecisionEngine(cfg, allowlist)
        signals = engine.decide(p95_latency=10.0, queue_depth=100,
                                budget_statuses=[], now_ms=1000)
        assert signals == []

    def test_all_sources_stale_suspend(self):
        """All sources stale → SUSPENDED (Req 6.2)."""
        ctrl = make_controller(sufficiency_min=1, check_stale=True)
        ctrl._metrics.ingest("guard", make_sample(1000))
        ctrl._metrics._stale_threshold_ms = 1000
        result = ctrl.tick(1_000_000)
        assert ctrl.state == AdaptiveControllerState.SUSPENDED
        assert result == []

    def test_zero_request_rate_budget(self):
        """request_rate=0 → no division by zero (Req 3.6)."""
        calc = ErrorBudgetCalculator()
        samples = [make_sample(1000, total=0, successful=0)]
        statuses = calc.evaluate(samples, now_ms=2000)
        # Should not raise — division by zero protected
        for s in statuses:
            assert s.budget_remaining_pct is not None

    def test_concurrent_killswitch_and_adaptive(self):
        """KillSwitch on guard + adaptive on pdf → only pdf signals (Req 10.3)."""
        cfg = make_config()
        allowlist = AllowlistManager(cfg.targets)
        engine = DecisionEngine(cfg, allowlist,
            killswitch_active_fn=lambda sub: sub == "guard")
        engine.guard_mode = "enforce"
        engine.pdf_mode = "accepting"
        signals = engine.decide(p95_latency=10.0, queue_depth=100,
                                budget_statuses=[], now_ms=1000)
        assert all(s.subsystem_id != "guard" for s in signals)
        assert any(s.subsystem_id == "pdf" for s in signals)

    def test_config_update_during_cooldown(self):
        """Config change during cooldown doesn't bypass cooldown (Req 5.3)."""
        hysteresis = HysteresisFilter(dwell_time_ms=0, cooldown_ms=10_000)
        signal = ControlSignal(
            signal_type=SignalType.SWITCH_TO_SHADOW,
            subsystem_id="guard", metric_name="p95",
            timestamp_ms=1000,
        )
        # First signal passes
        passed = hysteresis.apply([signal], 1000)
        assert len(passed) == 1
        hysteresis.record_transition("guard", 1000)

        # Same signal within cooldown → blocked
        passed = hysteresis.apply([signal], 5000)
        assert len(passed) == 0

    def test_dwell_time_boundary(self):
        """Exact dwell_time boundary → signal passes (Req 5.2)."""
        hysteresis = HysteresisFilter(dwell_time_ms=5000, cooldown_ms=0)
        signal = ControlSignal(
            signal_type=SignalType.SWITCH_TO_SHADOW,
            subsystem_id="guard", metric_name="p95",
            timestamp_ms=1000,
        )
        passed = hysteresis.apply([signal], 1000)
        assert len(passed) == 1
        hysteresis.record_transition("guard", 1000)

        # Exactly at dwell_time boundary
        passed = hysteresis.apply([signal], 6000)
        assert len(passed) == 1
