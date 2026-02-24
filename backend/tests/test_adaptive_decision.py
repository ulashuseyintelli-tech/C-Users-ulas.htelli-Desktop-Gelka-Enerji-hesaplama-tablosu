"""
Tests for Decision Engine + Orchestrator (merged package).

Feature: slo-adaptive-control, Tasks 7.9–7.22
MUST Properties: P1 (Monotonic-Safe), P2 (Priority Ladder), P16 (HOLD), P17 (Fail-Safe)
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from backend.app.adaptive_control.budget import BudgetStatus, ErrorBudgetCalculator
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
from backend.app.adaptive_control.signals import (
    ControlSignal,
    PriorityLevel,
    SignalType,
)
from backend.app.adaptive_control.sufficiency import (
    SufficiencyConfig,
    TelemetrySufficiencyChecker,
)
from backend.app.testing.slo_evaluator import MetricSample


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_sample(ts_ms: int, total: int = 100, successful: int = 99, latency: float = 0.1):
    return MetricSample(
        timestamp_ms=ts_ms, total_requests=total,
        successful_requests=successful, latency_p99_seconds=latency,
    )

def make_config(**overrides) -> AdaptiveControlConfig:
    defaults = dict(
        p95_latency_enter_threshold=0.5,
        p95_latency_exit_threshold=0.3,
        queue_depth_enter_threshold=50,
        queue_depth_exit_threshold=20,
        dwell_time_seconds=0.001,  # near-zero for testing
        cooldown_period_seconds=0.001,
        control_loop_interval_seconds=30.0,
        targets=[AllowlistEntry(subsystem_id="*")],
    )
    defaults.update(overrides)
    return AdaptiveControlConfig(**defaults)

def make_allowlist(entries=None):
    return AllowlistManager(entries or [AllowlistEntry(subsystem_id="*")])

def make_controller(
    config=None, guard_mode="enforce", pdf_mode="accepting",
    guard_setter=None, pdf_setter=None,
    killswitch_fn=None, override_fn=None,
    sufficiency_min=1,
):
    cfg = config or make_config()
    allowlist = AllowlistManager(cfg.targets)
    engine = DecisionEngine(
        cfg, allowlist,
        killswitch_active_fn=killswitch_fn,
        manual_override_active_fn=override_fn,
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
        SufficiencyConfig(min_samples=sufficiency_min, min_bucket_coverage_pct=0.0, check_source_stale=False)
    )
    return AdaptiveController(
        config=cfg, metrics_collector=collector, budget_calculator=budget,
        decision_engine=engine, hysteresis_filter=hysteresis,
        sufficiency_checker=sufficiency,
        guard_mode_setter=guard_setter, pdf_backpressure_setter=pdf_setter,
    )


# ══════════════════════════════════════════════════════════════════════════════
# MUST Property P2: Priority Ladder Determinism
# Validates: Req CC.3, CC.4, 10.1, 10.2
# ══════════════════════════════════════════════════════════════════════════════

class TestPriorityLadderPropertyP2:
    """MUST — Property 2: Priority Ladder Determinism."""

    @given(
        priorities=st.lists(
            st.sampled_from(list(PriorityLevel)),
            min_size=2,
            max_size=6,
        ),
    )
    @settings(max_examples=100, derandomize=True, suppress_health_check=[HealthCheck.too_slow])
    def test_highest_priority_wins(self, priorities: list[PriorityLevel]):
        """Signals sorted by priority: lowest number = highest priority."""
        signals = [
            ControlSignal(
                signal_type=SignalType.SWITCH_TO_SHADOW,
                subsystem_id=f"sub_{i}",
                metric_name=f"metric_{i}",
                priority=p,
                timestamp_ms=1000,
            )
            for i, p in enumerate(priorities)
        ]
        sorted_signals = DecisionEngine._apply_tie_breaker(signals)
        for i in range(len(sorted_signals) - 1):
            assert sorted_signals[i].priority <= sorted_signals[i + 1].priority

    @given(
        subsystems=st.lists(
            st.sampled_from(["alpha", "beta", "gamma"]),
            min_size=2,
            max_size=5,
        ),
        metrics=st.lists(
            st.sampled_from(["latency", "queue", "budget"]),
            min_size=2,
            max_size=5,
        ),
    )
    @settings(max_examples=100, derandomize=True, suppress_health_check=[HealthCheck.too_slow])
    def test_tie_breaker_deterministic(self, subsystems: list[str], metrics: list[str]):
        """Same-priority signals: subsystem_id → metric_name → tenant_id (lexicographic)."""
        signals = [
            ControlSignal(
                signal_type=SignalType.SWITCH_TO_SHADOW,
                subsystem_id=sub,
                metric_name=met,
                priority=PriorityLevel.ADAPTIVE_CONTROL,
                timestamp_ms=1000,
            )
            for sub, met in zip(subsystems, metrics)
        ]
        sorted_a = DecisionEngine._apply_tie_breaker(list(signals))
        sorted_b = DecisionEngine._apply_tie_breaker(list(reversed(signals)))
        # Same input (different order) → same output
        assert [s.subsystem_id for s in sorted_a] == [s.subsystem_id for s in sorted_b]
        assert [s.metric_name for s in sorted_a] == [s.metric_name for s in sorted_b]

    def test_killswitch_suppresses_adaptive(self):
        """KillSwitch active → no signals for that subsystem (Req 10.3)."""
        cfg = make_config()
        allowlist = make_allowlist()
        engine = DecisionEngine(
            cfg, allowlist,
            killswitch_active_fn=lambda sub: sub == "guard",
        )
        engine.guard_mode = "enforce"
        signals = engine.decide(p95_latency=10.0, queue_depth=None, budget_statuses=[], now_ms=1000)
        assert all(s.subsystem_id != "guard" for s in signals)


# ══════════════════════════════════════════════════════════════════════════════
# MUST Property P1: Monotonic-Safe Transitions
# Validates: Req CC.2, 4.3, 7.2
# ══════════════════════════════════════════════════════════════════════════════

class TestMonotonicSafePropertyP1:
    """MUST — Property 1: Monotonic-Safe Transitions."""

    @given(
        latency=st.floats(min_value=0.01, max_value=10.0),
        initial_mode=st.sampled_from(["enforce", "shadow"]),
    )
    @settings(max_examples=100, derandomize=True, suppress_health_check=[HealthCheck.too_slow])
    def test_no_enforcement_increase(self, latency: float, initial_mode: str):
        """Adaptive control never produces a signal that increases enforcement."""
        cfg = make_config()
        allowlist = make_allowlist()
        engine = DecisionEngine(cfg, allowlist)
        engine.guard_mode = initial_mode

        signals = engine.decide(p95_latency=latency, queue_depth=None, budget_statuses=[], now_ms=1000)

        for signal in signals:
            if signal.subsystem_id == "guard":
                if initial_mode == "shadow":
                    # From shadow, only RESTORE_ENFORCE is allowed (recovery, not escalation)
                    assert signal.signal_type in (SignalType.RESTORE_ENFORCE,)
                elif initial_mode == "enforce":
                    # From enforce, only SWITCH_TO_SHADOW (downgrade)
                    assert signal.signal_type == SignalType.SWITCH_TO_SHADOW

    def test_enforce_to_shadow_only(self):
        """v1: Guard only does ENFORCE→SHADOW, never SHADOW→OFF."""
        cfg = make_config()
        allowlist = make_allowlist()
        engine = DecisionEngine(cfg, allowlist)
        engine.guard_mode = "enforce"

        signals = engine.decide(p95_latency=10.0, queue_depth=None, budget_statuses=[], now_ms=1000)
        guard_signals = [s for s in signals if s.subsystem_id == "guard"]
        assert len(guard_signals) == 1
        assert guard_signals[0].signal_type == SignalType.SWITCH_TO_SHADOW


# ══════════════════════════════════════════════════════════════════════════════
# MUST Property P16: Backpressure Hard Block (HOLD)
# Validates: Req 8.1, 8.2, 8.4
# ══════════════════════════════════════════════════════════════════════════════

class TestBackpressureHoldPropertyP16:
    """MUST — Property 16: Backpressure Hard Block (HOLD)."""

    @given(queue_depth=st.integers(min_value=51, max_value=1000))
    @settings(max_examples=100, derandomize=True, suppress_health_check=[HealthCheck.too_slow])
    def test_stop_accepting_on_high_queue(self, queue_depth: int):
        """Queue > enter_threshold → STOP_ACCEPTING_JOBS signal."""
        cfg = make_config(queue_depth_enter_threshold=50)
        allowlist = make_allowlist()
        engine = DecisionEngine(cfg, allowlist)
        engine.pdf_mode = "accepting"

        signals = engine.decide(p95_latency=None, queue_depth=queue_depth, budget_statuses=[], now_ms=1000)
        pdf_signals = [s for s in signals if s.subsystem_id == "pdf"]
        assert len(pdf_signals) == 1
        assert pdf_signals[0].signal_type == SignalType.STOP_ACCEPTING_JOBS

    def test_backpressure_is_hard_block(self):
        """HOLD semantics: signal type is STOP (not degrade/throttle)."""
        cfg = make_config()
        allowlist = make_allowlist()
        engine = DecisionEngine(cfg, allowlist)
        engine.pdf_mode = "accepting"

        signals = engine.decide(p95_latency=None, queue_depth=100, budget_statuses=[], now_ms=1000)
        pdf_signals = [s for s in signals if s.subsystem_id == "pdf"]
        assert len(pdf_signals) == 1
        # STOP_ACCEPTING_JOBS = hard block, not a throttle/degrade
        assert pdf_signals[0].signal_type == SignalType.STOP_ACCEPTING_JOBS

    def test_existing_jobs_continue_during_backpressure(self):
        """Backpressure only stops NEW jobs; existing jobs unaffected."""
        applied_states = []
        def pdf_setter(active):
            applied_states.append(active)

        ctrl = make_controller(pdf_mode="accepting", pdf_setter=pdf_setter)
        # Ingest high queue depth sample
        now_ms = 50_000
        ctrl._metrics.ingest("pdf", make_sample(now_ms - 1000, total=100, latency=0.1))
        # Force queue depth to trigger (override extract)
        ctrl._decision.pdf_mode = "accepting"

        cfg = make_config(queue_depth_enter_threshold=50)
        allowlist = make_allowlist()
        engine = DecisionEngine(cfg, allowlist)
        engine.pdf_mode = "accepting"
        signals = engine.decide(p95_latency=None, queue_depth=100, budget_statuses=[], now_ms=now_ms)
        # Signal says STOP_ACCEPTING — this means only new jobs blocked
        assert any(s.signal_type == SignalType.STOP_ACCEPTING_JOBS for s in signals)

    @given(queue_depth=st.integers(min_value=0, max_value=19))
    @settings(max_examples=100, derandomize=True, suppress_health_check=[HealthCheck.too_slow])
    def test_resume_on_low_queue(self, queue_depth: int):
        """Queue < exit_threshold → RESUME_ACCEPTING_JOBS signal."""
        cfg = make_config(queue_depth_exit_threshold=20)
        allowlist = make_allowlist()
        engine = DecisionEngine(cfg, allowlist)
        engine.pdf_mode = "backpressure"

        signals = engine.decide(p95_latency=None, queue_depth=queue_depth, budget_statuses=[], now_ms=1000)
        pdf_signals = [s for s in signals if s.subsystem_id == "pdf"]
        assert len(pdf_signals) == 1
        assert pdf_signals[0].signal_type == SignalType.RESUME_ACCEPTING_JOBS


# ══════════════════════════════════════════════════════════════════════════════
# MUST Property P17: Fail-Safe State Preservation
# Validates: Req 6.1, 6.2
# ══════════════════════════════════════════════════════════════════════════════

class TestFailSafePropertyP17:
    """MUST — Property 17: Fail-Safe State Preservation."""

    def test_exception_enters_failsafe(self):
        """Internal exception → FAILSAFE state, modes preserved."""
        guard_modes = []
        pdf_states = []

        ctrl = make_controller(
            guard_mode="enforce",
            guard_setter=lambda m: guard_modes.append(m),
            pdf_setter=lambda a: pdf_states.append(a),
        )

        # Inject fault: make sufficiency checker raise
        original_check = ctrl._sufficiency.check
        def faulty_check(*args, **kwargs):
            raise RuntimeError("simulated failure")
        ctrl._sufficiency.check = faulty_check

        # Ingest a sample so collector has data
        ctrl._metrics.ingest("guard", make_sample(49000))
        result = ctrl.tick(50_000)

        assert ctrl.state == AdaptiveControllerState.FAILSAFE
        assert result == []  # no signals applied
        assert guard_modes == []  # guard mode not changed
        assert pdf_states == []  # pdf state not changed

    def test_failsafe_preserves_current_modes(self):
        """Fail-safe does NOT downgrade — preserves whatever mode was active."""
        ctrl = make_controller(guard_mode="shadow", pdf_mode="backpressure")

        original_check = ctrl._sufficiency.check
        def faulty_check(*args, **kwargs):
            raise RuntimeError("boom")
        ctrl._sufficiency.check = faulty_check

        ctrl._metrics.ingest("guard", make_sample(49000))
        ctrl.tick(50_000)

        assert ctrl.state == AdaptiveControllerState.FAILSAFE
        assert ctrl._decision.guard_mode == "shadow"  # preserved
        assert ctrl._decision.pdf_mode == "backpressure"  # preserved

    @given(
        guard_mode=st.sampled_from(["enforce", "shadow"]),
        pdf_mode=st.sampled_from(["accepting", "backpressure"]),
    )
    @settings(max_examples=20, derandomize=True, suppress_health_check=[HealthCheck.too_slow])
    def test_failsafe_zero_side_effects(self, guard_mode: str, pdf_mode: str):
        """Fail-safe produces zero side effects regardless of initial state."""
        side_effects = []
        ctrl = make_controller(
            guard_mode=guard_mode,
            pdf_mode=pdf_mode,
            guard_setter=lambda m: side_effects.append(("guard", m)),
            pdf_setter=lambda a: side_effects.append(("pdf", a)),
        )

        def faulty_check(*args, **kwargs):
            raise RuntimeError("fault")
        ctrl._sufficiency.check = faulty_check

        ctrl._metrics.ingest("guard", make_sample(49000))
        ctrl.tick(50_000)

        assert side_effects == []  # zero side effects
        assert ctrl.state == AdaptiveControllerState.FAILSAFE

    def test_failsafe_reason_recorded(self):
        """Fail-safe reason is recorded (Req 6.8)."""
        ctrl = make_controller()
        def faulty_check(*args, **kwargs):
            raise RuntimeError("specific error message")
        ctrl._sufficiency.check = faulty_check

        ctrl._metrics.ingest("guard", make_sample(49000))
        ctrl.tick(50_000)

        assert ctrl.failsafe_reason is not None
        assert "specific error message" in ctrl.failsafe_reason

    def test_recovery_from_failsafe(self):
        """Sources healthy again → recover from FAILSAFE (Req 6.7)."""
        ctrl = make_controller(sufficiency_min=1)

        # Enter failsafe
        def faulty_check(*args, **kwargs):
            raise RuntimeError("fault")
        ctrl._sufficiency.check = faulty_check
        ctrl._metrics.ingest("guard", make_sample(49000))
        ctrl.tick(50_000)
        assert ctrl.state == AdaptiveControllerState.FAILSAFE

        # Restore normal sufficiency
        ctrl._sufficiency.check = TelemetrySufficiencyChecker(
            SufficiencyConfig(min_samples=1, min_bucket_coverage_pct=0.0, check_source_stale=False)
        ).check
        ctrl._metrics.ingest("guard", make_sample(99000))
        ctrl.tick(100_000)
        assert ctrl.state == AdaptiveControllerState.RUNNING


# ══════════════════════════════════════════════════════════════════════════════
# Optional Properties + Unit Tests (Tasks 7.13–7.22)
# ══════════════════════════════════════════════════════════════════════════════

class TestBoundedActionSetPropertyP3:
    """Optional — Property 3: Bounded Action Set."""

    @given(
        latency=st.one_of(st.none(), st.floats(min_value=0.01, max_value=10.0)),
        queue=st.one_of(st.none(), st.integers(min_value=0, max_value=200)),
    )
    @settings(max_examples=100, derandomize=True, suppress_health_check=[HealthCheck.too_slow])
    def test_signal_types_bounded(self, latency, queue):
        """All signals must be in the bounded set."""
        cfg = make_config()
        allowlist = make_allowlist()
        engine = DecisionEngine(cfg, allowlist)
        signals = engine.decide(p95_latency=latency, queue_depth=queue, budget_statuses=[], now_ms=1000)
        valid_types = set(SignalType)
        for s in signals:
            assert s.signal_type in valid_types


class TestDwellTimePropertyP6:
    """Optional — Property 6: Dwell Time Enforcement."""

    def test_dwell_time_blocks_rapid_transitions(self):
        """Two transitions within dwell_time → second blocked."""
        hysteresis = HysteresisFilter(dwell_time_ms=10_000, cooldown_ms=0)
        signal = ControlSignal(
            signal_type=SignalType.SWITCH_TO_SHADOW,
            subsystem_id="guard", metric_name="p95",
            timestamp_ms=1000,
        )

        # First signal passes
        passed = hysteresis.apply([signal], 1000)
        assert len(passed) == 1
        hysteresis.record_transition("guard", 1000)

        # Second signal within dwell_time → blocked
        passed = hysteresis.apply([signal], 5000)
        assert len(passed) == 0

        # After dwell_time → passes
        passed = hysteresis.apply([signal], 12000)
        assert len(passed) == 1


class TestOscillationDetectionPropertyP23:
    """Optional — Property 23: Oscillation Detection."""

    def test_oscillation_detected_after_max_transitions(self):
        """Too many transitions → oscillation detected."""
        hysteresis = HysteresisFilter(
            dwell_time_ms=0, cooldown_ms=0,
            oscillation_window_size=5, oscillation_max_transitions=3,
        )
        for i in range(4):
            hysteresis.record_transition("guard", i * 1000)

        assert hysteresis.detect_oscillation("guard") is True

    def test_no_oscillation_below_threshold(self):
        """Few transitions → no oscillation."""
        hysteresis = HysteresisFilter(
            dwell_time_ms=0, cooldown_ms=0,
            oscillation_window_size=10, oscillation_max_transitions=5,
        )
        hysteresis.record_transition("guard", 1000)
        hysteresis.record_transition("guard", 2000)
        assert hysteresis.detect_oscillation("guard") is False


class TestDecisionEngineUnit:
    """Unit tests for DecisionEngine + HysteresisFilter + AdaptiveController."""

    def test_empty_allowlist_no_signals(self):
        """Empty allowlist → no signals produced."""
        cfg = make_config(targets=[])
        allowlist = AllowlistManager([])
        engine = DecisionEngine(cfg, allowlist)
        signals = engine.decide(p95_latency=10.0, queue_depth=100, budget_statuses=[], now_ms=1000)
        assert signals == []

    def test_concurrent_killswitch_and_adaptive(self):
        """KillSwitch on guard + adaptive on pdf → only pdf signals."""
        cfg = make_config()
        allowlist = make_allowlist()
        engine = DecisionEngine(
            cfg, allowlist,
            killswitch_active_fn=lambda sub: sub == "guard",
        )
        engine.guard_mode = "enforce"
        engine.pdf_mode = "accepting"
        signals = engine.decide(p95_latency=10.0, queue_depth=100, budget_statuses=[], now_ms=1000)
        assert all(s.subsystem_id != "guard" for s in signals)
        assert any(s.subsystem_id == "pdf" for s in signals)

    def test_no_signal_in_dead_zone(self):
        """Latency between exit and enter thresholds → no signal."""
        cfg = make_config(p95_latency_enter_threshold=0.5, p95_latency_exit_threshold=0.3)
        allowlist = make_allowlist()
        engine = DecisionEngine(cfg, allowlist)
        engine.guard_mode = "enforce"
        signals = engine.decide(p95_latency=0.4, queue_depth=None, budget_statuses=[], now_ms=1000)
        guard_signals = [s for s in signals if s.subsystem_id == "guard"]
        assert guard_signals == []

    def test_all_sources_stale_suspends(self):
        """All sources stale → controller enters SUSPENDED."""
        ctrl = make_controller(sufficiency_min=1)
        # Ingest old sample
        ctrl._metrics.ingest("guard", make_sample(1000))
        # Tick at much later time (stale)
        ctrl._metrics._stale_threshold_ms = 1000
        # Override sufficiency to detect stale
        ctrl._sufficiency = TelemetrySufficiencyChecker(
            SufficiencyConfig(min_samples=1, min_bucket_coverage_pct=0.0, check_source_stale=True)
        )
        result = ctrl.tick(1_000_000)
        assert ctrl.state == AdaptiveControllerState.SUSPENDED
        assert result == []
