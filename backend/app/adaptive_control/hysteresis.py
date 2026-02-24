"""
Hysteresis Filter — dwell time, cooldown, oscillation detection.

Prevents mode flapping by enforcing minimum dwell time between transitions
and cooldown periods between signals. Cannot be bypassed (Req 5.6).

Feature: slo-adaptive-control, Task 7.3
Requirements: CC.7, 5.1–5.6
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from backend.app.adaptive_control.signals import ControlSignal

logger = logging.getLogger(__name__)


@dataclass
class HysteresisState:
    """Per-subsystem hysteresis tracking state."""
    last_transition_ms: Optional[int] = None
    last_signal_ms: Optional[int] = None
    current_mode: Optional[str] = None
    transition_history: list[int] = field(default_factory=list)


class HysteresisFilter:
    """
    Filters control signals through dwell time + cooldown constraints.
    Dwell time and cooldown cannot be bypassed (Req 5.6).
    """

    def __init__(
        self,
        dwell_time_ms: int = 600_000,
        cooldown_ms: int = 300_000,
        oscillation_window_size: int = 10,
        oscillation_max_transitions: int = 4,
    ) -> None:
        self._dwell_time_ms = dwell_time_ms
        self._cooldown_ms = cooldown_ms
        self._oscillation_window_size = oscillation_window_size
        self._oscillation_max_transitions = oscillation_max_transitions
        self._states: dict[str, HysteresisState] = {}

    def _get_state(self, subsystem_id: str) -> HysteresisState:
        if subsystem_id not in self._states:
            self._states[subsystem_id] = HysteresisState()
        return self._states[subsystem_id]

    def apply(
        self,
        signals: list[ControlSignal],
        now_ms: int,
    ) -> list[ControlSignal]:
        """Filter signals through dwell time + cooldown (Req 5.2, 5.3).

        Returns only signals that pass hysteresis constraints.
        Blocked signals are logged but not acted upon (Req 5.4).
        """
        passed: list[ControlSignal] = []
        for signal in signals:
            state = self._get_state(signal.subsystem_id)

            # Dwell time check (Req 5.2)
            if state.last_transition_ms is not None:
                elapsed = now_ms - state.last_transition_ms
                if elapsed < self._dwell_time_ms:
                    logger.info(
                        f"[ADAPTIVE-CONTROL] Signal {signal.signal_type.value} blocked by "
                        f"dwell time: {elapsed}ms < {self._dwell_time_ms}ms "
                        f"(subsystem={signal.subsystem_id})"
                    )
                    continue

            # Cooldown check (Req 5.3)
            if state.last_signal_ms is not None:
                elapsed = now_ms - state.last_signal_ms
                if elapsed < self._cooldown_ms:
                    logger.info(
                        f"[ADAPTIVE-CONTROL] Signal {signal.signal_type.value} blocked by "
                        f"cooldown: {elapsed}ms < {self._cooldown_ms}ms "
                        f"(subsystem={signal.subsystem_id})"
                    )
                    continue

            passed.append(signal)
        return passed

    def record_transition(self, subsystem_id: str, now_ms: int) -> None:
        """Record that a transition occurred (called after apply_signal)."""
        state = self._get_state(subsystem_id)
        state.last_transition_ms = now_ms
        state.last_signal_ms = now_ms
        state.transition_history.append(now_ms)
        # Trim history to window size
        if len(state.transition_history) > self._oscillation_window_size:
            state.transition_history = state.transition_history[-self._oscillation_window_size:]

    def detect_oscillation(self, subsystem_id: str) -> bool:
        """Detect oscillation: too many transitions in recent history (Req 5.5)."""
        state = self._get_state(subsystem_id)
        return len(state.transition_history) >= self._oscillation_max_transitions

    def get_state(self, subsystem_id: str) -> HysteresisState:
        """Get current hysteresis state for a subsystem."""
        return self._get_state(subsystem_id)
