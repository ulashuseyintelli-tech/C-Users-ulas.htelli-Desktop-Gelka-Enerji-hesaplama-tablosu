"""
Decision Engine — 4-level priority ladder, tie-breaker, guard + PDF logic.

Pure decision logic: takes metrics/budget status, produces ControlSignals.
No side effects — side effects are in AdaptiveController.apply_signal().

Feature: slo-adaptive-control, Tasks 7.2, 7.4, 7.5
Requirements: CC.2, CC.3, CC.4, 4.2–4.4, 7.1–7.3, 8.1–8.4, 10.1–10.4
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from backend.app.adaptive_control.budget import BudgetStatus
from backend.app.adaptive_control.config import AdaptiveControlConfig, AllowlistManager
from backend.app.adaptive_control.signals import (
    ControlSignal,
    PriorityLevel,
    SignalType,
)

logger = logging.getLogger(__name__)


class DecisionEngine:
    """
    Pure decision function: metrics → ControlSignals.
    4-level priority ladder with deterministic tie-breaker.
    """

    def __init__(
        self,
        config: AdaptiveControlConfig,
        allowlist: AllowlistManager,
        killswitch_active_fn=None,
        manual_override_active_fn=None,
    ) -> None:
        self._config = config
        self._allowlist = allowlist
        self._killswitch_active = killswitch_active_fn or (lambda sub: False)
        self._manual_override_active = manual_override_active_fn or (lambda sub: False)
        # Track current modes for monotonic-safe check
        self._guard_mode: str = "enforce"  # "enforce" or "shadow"
        self._pdf_mode: str = "accepting"  # "accepting" or "backpressure"

    @property
    def guard_mode(self) -> str:
        return self._guard_mode

    @guard_mode.setter
    def guard_mode(self, value: str) -> None:
        self._guard_mode = value

    @property
    def pdf_mode(self) -> str:
        return self._pdf_mode

    @pdf_mode.setter
    def pdf_mode(self, value: str) -> None:
        self._pdf_mode = value

    def decide(
        self,
        p95_latency: Optional[float],
        queue_depth: Optional[int],
        budget_statuses: list[BudgetStatus],
        now_ms: int,
    ) -> list[ControlSignal]:
        """Produce control signals based on current metrics.

        Returns signals sorted by priority ladder + tie-breaker.
        KillSwitch/Manual Override active → no signals for that subsystem.
        """
        signals: list[ControlSignal] = []
        correlation_id = str(uuid.uuid4())

        # Guard subsystem signals (Req 4.2, 4.3, 7.1–7.3)
        if not self._killswitch_active("guard") and not self._manual_override_active("guard"):
            guard_signal = self._evaluate_guard(p95_latency, correlation_id, now_ms)
            if guard_signal is not None:
                signals.append(guard_signal)

        # PDF subsystem signals (Req 4.4, 8.1–8.4)
        if not self._killswitch_active("pdf") and not self._manual_override_active("pdf"):
            pdf_signal = self._evaluate_pdf(queue_depth, correlation_id, now_ms)
            if pdf_signal is not None:
                signals.append(pdf_signal)

        # Budget-triggered signals
        for status in budget_statuses:
            if self._killswitch_active(status.subsystem_id):
                continue
            if self._manual_override_active(status.subsystem_id):
                continue
            if status.is_burn_rate_exceeded or status.is_exhausted:
                budget_signal = self._evaluate_budget_trigger(status, correlation_id, now_ms)
                if budget_signal is not None:
                    signals.append(budget_signal)

        # Apply tie-breaker for deterministic ordering (Req CC.3)
        return self._apply_tie_breaker(signals)

    def _evaluate_guard(
        self,
        p95_latency: Optional[float],
        correlation_id: str,
        now_ms: int,
    ) -> Optional[ControlSignal]:
        """Guard mode decision logic (Req 4.2, 4.3, 7.1–7.3)."""
        if p95_latency is None:
            return None

        # Check allowlist scope
        if not self._allowlist.is_in_scope(subsystem_id="guard"):
            return None

        # ENFORCE → SHADOW: p95 > enter_threshold (Req 4.2)
        if (
            self._guard_mode == "enforce"
            and p95_latency > self._config.p95_latency_enter_threshold
        ):
            return ControlSignal(
                signal_type=SignalType.SWITCH_TO_SHADOW,
                subsystem_id="guard",
                metric_name="p95_latency",
                trigger_value=p95_latency,
                threshold=self._config.p95_latency_enter_threshold,
                priority=PriorityLevel.ADAPTIVE_CONTROL,
                correlation_id=correlation_id,
                timestamp_ms=now_ms,
            )

        # SHADOW → ENFORCE: p95 < exit_threshold (Req 7.3)
        # Monotonic-safe: only restore, never increase enforcement (Req CC.2)
        if (
            self._guard_mode == "shadow"
            and p95_latency < self._config.p95_latency_exit_threshold
        ):
            return ControlSignal(
                signal_type=SignalType.RESTORE_ENFORCE,
                subsystem_id="guard",
                metric_name="p95_latency",
                trigger_value=p95_latency,
                threshold=self._config.p95_latency_exit_threshold,
                priority=PriorityLevel.ADAPTIVE_CONTROL,
                correlation_id=correlation_id,
                timestamp_ms=now_ms,
            )

        return None

    def _evaluate_pdf(
        self,
        queue_depth: Optional[int],
        correlation_id: str,
        now_ms: int,
    ) -> Optional[ControlSignal]:
        """PDF backpressure decision logic (Req 4.4, 8.1–8.4)."""
        if queue_depth is None:
            return None

        if not self._allowlist.is_in_scope(subsystem_id="pdf"):
            return None

        # ACCEPTING → BACKPRESSURE: queue > enter_threshold (Req 4.4)
        if (
            self._pdf_mode == "accepting"
            and queue_depth > self._config.queue_depth_enter_threshold
        ):
            return ControlSignal(
                signal_type=SignalType.STOP_ACCEPTING_JOBS,
                subsystem_id="pdf",
                metric_name="queue_depth",
                trigger_value=float(queue_depth),
                threshold=float(self._config.queue_depth_enter_threshold),
                priority=PriorityLevel.ADAPTIVE_CONTROL,
                correlation_id=correlation_id,
                timestamp_ms=now_ms,
            )

        # BACKPRESSURE → ACCEPTING: queue < exit_threshold (Req 8.3)
        if (
            self._pdf_mode == "backpressure"
            and queue_depth < self._config.queue_depth_exit_threshold
        ):
            return ControlSignal(
                signal_type=SignalType.RESUME_ACCEPTING_JOBS,
                subsystem_id="pdf",
                metric_name="queue_depth",
                trigger_value=float(queue_depth),
                threshold=float(self._config.queue_depth_exit_threshold),
                priority=PriorityLevel.ADAPTIVE_CONTROL,
                correlation_id=correlation_id,
                timestamp_ms=now_ms,
            )

        return None

    def _evaluate_budget_trigger(
        self,
        status: BudgetStatus,
        correlation_id: str,
        now_ms: int,
    ) -> Optional[ControlSignal]:
        """Budget exhaustion → protective signal (Req 3.4, 4.5)."""
        if not self._allowlist.is_in_scope(subsystem_id=status.subsystem_id):
            return None

        # Guard budget exhaustion → switch to shadow (monotonic-safe)
        if status.subsystem_id == "guard" and self._guard_mode == "enforce":
            return ControlSignal(
                signal_type=SignalType.SWITCH_TO_SHADOW,
                subsystem_id="guard",
                metric_name=status.metric,
                trigger_value=status.burn_rate,
                threshold=self._config.burn_rate_threshold,
                priority=PriorityLevel.ADAPTIVE_CONTROL,
                correlation_id=correlation_id,
                timestamp_ms=now_ms,
            )

        # PDF budget exhaustion → stop accepting
        if status.subsystem_id == "pdf" and self._pdf_mode == "accepting":
            return ControlSignal(
                signal_type=SignalType.STOP_ACCEPTING_JOBS,
                subsystem_id="pdf",
                metric_name=status.metric,
                trigger_value=status.burn_rate,
                threshold=self._config.burn_rate_threshold,
                priority=PriorityLevel.ADAPTIVE_CONTROL,
                correlation_id=correlation_id,
                timestamp_ms=now_ms,
            )

        return None

    @staticmethod
    def _apply_tie_breaker(signals: list[ControlSignal]) -> list[ControlSignal]:
        """Deterministic tie-breaker: subsystem_id → metric_name → tenant_id (Req CC.3)."""
        return sorted(
            signals,
            key=lambda s: (s.priority, s.subsystem_id, s.metric_name, s.tenant_id),
        )
