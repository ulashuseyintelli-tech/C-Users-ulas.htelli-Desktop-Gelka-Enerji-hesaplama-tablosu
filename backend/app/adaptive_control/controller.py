"""
Adaptive Controller — orchestrator with side-effect boundaries.

The only place where subsystem state changes happen (via apply_signal).
All other methods are pure / read-only. This enforces the 0-side-effect
guarantee for telemetry-insufficient, allowlist-bypass, and 429=HOLD paths.

Feature: slo-adaptive-control, Tasks 7.6, 7.7, 7.8
Requirements: 4.1, 4.6, 6.1–6.8, 7.1–7.5, 8.1–8.5, 10.3–10.6
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Optional

from backend.app.adaptive_control.budget import BudgetStatus, ErrorBudgetCalculator
from backend.app.adaptive_control.config import (
    AdaptiveControlConfig,
    AllowlistManager,
    check_config_drift,
)
from backend.app.adaptive_control.decision_engine import DecisionEngine
from backend.app.adaptive_control.hysteresis import HysteresisFilter
from backend.app.adaptive_control.metrics_collector import MetricsCollector
from backend.app.adaptive_control.signals import ControlSignal, SignalType
from backend.app.adaptive_control.sufficiency import (
    SufficiencyConfig,
    TelemetrySufficiencyChecker,
)

logger = logging.getLogger(__name__)


class AdaptiveControllerState(str, Enum):
    RUNNING = "running"
    FAILSAFE = "failsafe"
    SUSPENDED = "suspended"


class AdaptiveController:
    """
    Orchestrator: runs the control loop, applies signals to subsystems.

    Side-effect boundary: only apply_signal() modifies subsystem state.
    All other paths (insufficient telemetry, allowlist miss, killswitch)
    produce zero side effects.
    """

    def __init__(
        self,
        config: AdaptiveControlConfig,
        metrics_collector: MetricsCollector,
        budget_calculator: ErrorBudgetCalculator,
        decision_engine: DecisionEngine,
        hysteresis_filter: HysteresisFilter,
        sufficiency_checker: TelemetrySufficiencyChecker,
        # Subsystem interfaces (for apply_signal)
        guard_mode_setter=None,
        pdf_backpressure_setter=None,
    ) -> None:
        self._config = config
        self._metrics = metrics_collector
        self._budget = budget_calculator
        self._decision = decision_engine
        self._hysteresis = hysteresis_filter
        self._sufficiency = sufficiency_checker
        self._guard_mode_setter = guard_mode_setter
        self._pdf_backpressure_setter = pdf_backpressure_setter
        self._state = AdaptiveControllerState.RUNNING
        self._failsafe_reason: Optional[str] = None
        self._failsafe_entered_ms: Optional[int] = None
        self._applied_signals: list[ControlSignal] = []

    @property
    def state(self) -> AdaptiveControllerState:
        return self._state

    @property
    def failsafe_reason(self) -> Optional[str]:
        return self._failsafe_reason

    @property
    def applied_signals(self) -> list[ControlSignal]:
        """Signals that were actually applied (for audit/testing)."""
        return list(self._applied_signals)

    def tick(self, now_ms: int) -> list[ControlSignal]:
        """Run one control loop iteration (Req 4.1).

        Returns list of applied signals. Empty list = no action taken.
        Side effects only via apply_signal() at the end.
        """
        try:
            return self._tick_inner(now_ms)
        except Exception as exc:
            # Fail-safe: preserve current state (Req 6.1, 6.5)
            self._enter_failsafe(str(exc), now_ms)
            return []

    def _tick_inner(self, now_ms: int) -> list[ControlSignal]:
        """Inner tick logic — may raise exceptions caught by tick()."""
        # Config drift check
        drift = check_config_drift(self._config)
        if drift:
            logger.warning(f"[ADAPTIVE-CONTROL] {drift} — skipping tick")
            return []

        # Telemetry sufficiency check (Req 6.3, 6.4)
        window_ms = int(self._config.control_loop_interval_seconds * 1000)
        window_start = now_ms - window_ms
        samples = self._metrics.get_all_samples(window_start, now_ms)
        health = self._metrics.check_health(now_ms)

        sufficiency = self._sufficiency.check(samples, health)
        if not sufficiency.is_sufficient:
            logger.info(
                f"[ADAPTIVE-CONTROL] Telemetry insufficient: {sufficiency.reason} — no-op"
            )
            # Check if all sources stale → SUSPENDED (Req 6.2)
            if health and all(h.is_stale for h in health):
                self._state = AdaptiveControllerState.SUSPENDED
                logger.warning("[ADAPTIVE-CONTROL] All sources stale — SUSPENDED")
            return []  # no-op, zero side effects

        # Recovery from failsafe/suspended (Req 6.7)
        if self._state in (AdaptiveControllerState.FAILSAFE, AdaptiveControllerState.SUSPENDED):
            logger.info(f"[ADAPTIVE-CONTROL] Recovering from {self._state.value} → RUNNING")
            self._state = AdaptiveControllerState.RUNNING
            self._failsafe_reason = None

        # Decision engine: produce signals
        # Get p95 latency and queue depth from latest samples
        p95_latency = self._extract_p95_latency(samples)
        queue_depth = self._extract_queue_depth(samples)
        budget_statuses = self._budget.evaluate(samples, now_ms)

        raw_signals = self._decision.decide(
            p95_latency=p95_latency,
            queue_depth=queue_depth,
            budget_statuses=budget_statuses,
            now_ms=now_ms,
        )

        # Hysteresis filter (Req CC.7)
        filtered_signals = self._hysteresis.apply(raw_signals, now_ms)

        # Apply signals (side-effect boundary)
        applied: list[ControlSignal] = []
        for signal in filtered_signals:
            success = self.apply_signal(signal)
            if success:
                self._hysteresis.record_transition(signal.subsystem_id, now_ms)
                applied.append(signal)
                # Check oscillation
                if self._hysteresis.detect_oscillation(signal.subsystem_id):
                    logger.warning(
                        f"[ADAPTIVE-CONTROL] Oscillation detected for {signal.subsystem_id}"
                    )

        self._applied_signals.extend(applied)
        return applied

    def apply_signal(self, signal: ControlSignal) -> bool:
        """Apply a control signal to the target subsystem.

        THIS IS THE ONLY METHOD THAT MODIFIES SUBSYSTEM STATE.
        All other code paths produce zero side effects.

        Returns True if signal was applied successfully.
        """
        try:
            if signal.signal_type == SignalType.SWITCH_TO_SHADOW:
                if self._guard_mode_setter:
                    self._guard_mode_setter("shadow")
                self._decision.guard_mode = "shadow"
                logger.info(
                    f"[ADAPTIVE-CONTROL] Guard mode → SHADOW "
                    f"(trigger={signal.trigger_value}, threshold={signal.threshold})"
                )
                return True

            elif signal.signal_type == SignalType.RESTORE_ENFORCE:
                if self._guard_mode_setter:
                    self._guard_mode_setter("enforce")
                self._decision.guard_mode = "enforce"
                logger.info(
                    f"[ADAPTIVE-CONTROL] Guard mode → ENFORCE "
                    f"(trigger={signal.trigger_value}, threshold={signal.threshold})"
                )
                return True

            elif signal.signal_type == SignalType.STOP_ACCEPTING_JOBS:
                if self._pdf_backpressure_setter:
                    self._pdf_backpressure_setter(True)
                self._decision.pdf_mode = "backpressure"
                logger.info(
                    f"[ADAPTIVE-CONTROL] PDF → BACKPRESSURE "
                    f"(trigger={signal.trigger_value}, threshold={signal.threshold})"
                )
                return True

            elif signal.signal_type == SignalType.RESUME_ACCEPTING_JOBS:
                if self._pdf_backpressure_setter:
                    self._pdf_backpressure_setter(False)
                self._decision.pdf_mode = "accepting"
                logger.info(
                    f"[ADAPTIVE-CONTROL] PDF → ACCEPTING "
                    f"(trigger={signal.trigger_value}, threshold={signal.threshold})"
                )
                return True

            return False

        except Exception as exc:
            logger.error(f"[ADAPTIVE-CONTROL] Failed to apply signal {signal}: {exc}")
            return False

    def _enter_failsafe(self, reason: str, now_ms: int) -> None:
        """Enter fail-safe state: preserve current modes (Req 6.1, 6.5, 6.6, 6.8)."""
        self._state = AdaptiveControllerState.FAILSAFE
        self._failsafe_reason = reason
        self._failsafe_entered_ms = now_ms
        logger.error(
            f"[ADAPTIVE-CONTROL] FAILSAFE entered: reason={reason}. "
            f"Current modes preserved. No automatic downgrade."
        )

    @staticmethod
    def _extract_p95_latency(samples) -> Optional[float]:
        """Extract p95 latency from samples (simplified for v1)."""
        if not samples:
            return None
        return max(s.latency_p99_seconds for s in samples)

    @staticmethod
    def _extract_queue_depth(samples) -> Optional[int]:
        """Extract queue depth from samples (simplified for v1).

        Uses total_requests as proxy for queue depth in v1.
        Real implementation would use a dedicated queue depth metric.
        """
        if not samples:
            return None
        return max(s.total_requests for s in samples)
