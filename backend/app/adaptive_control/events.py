"""
Control Decision Events — structured audit events + JSON logging.

Every mode transition produces a ControlDecisionEvent. No transition
without event, no event without transition (Req 11.8, CC.6).

Feature: slo-adaptive-control, Tasks 9.1, 9.3
Requirements: CC.6, 3.5, 6.6, 11.5–11.8
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Optional

from backend.app.adaptive_control.signals import ControlSignal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ControlDecisionEvent:
    """Structured audit event for every mode transition (Req CC.6, 11.6)."""
    event_id: str
    correlation_id: str
    reason: str
    previous_mode: str
    new_mode: str
    subsystem_id: str
    transition_timestamp_ms: int
    trigger_metric: str
    trigger_value: float
    threshold: float
    burn_rate: Optional[float] = None
    actor: str = "adaptive_control"


# In-memory event store for testing/audit
_event_log: list[ControlDecisionEvent] = []


def get_event_log() -> list[ControlDecisionEvent]:
    """Get all emitted events (for testing/audit)."""
    return list(_event_log)


def clear_event_log() -> None:
    """Clear event log (for testing)."""
    _event_log.clear()


def emit_control_decision_event(
    signal: ControlSignal,
    previous_mode: str,
    new_mode: str,
    burn_rate: Optional[float] = None,
) -> ControlDecisionEvent:
    """Emit a structured control decision event (Req 11.6, 11.7).

    Must be called for every mode transition. No transition without event.
    """
    event = ControlDecisionEvent(
        event_id=str(uuid.uuid4()),
        correlation_id=signal.correlation_id,
        reason=signal.signal_type.value,
        previous_mode=previous_mode,
        new_mode=new_mode,
        subsystem_id=signal.subsystem_id,
        transition_timestamp_ms=signal.timestamp_ms,
        trigger_metric=signal.metric_name,
        trigger_value=signal.trigger_value,
        threshold=signal.threshold,
        burn_rate=burn_rate,
        actor="adaptive_control",
    )

    _event_log.append(event)

    # Structured JSON log (Req 11.5)
    log_entry = {
        "event": "control_decision",
        "event_id": event.event_id,
        "correlation_id": event.correlation_id,
        "reason": event.reason,
        "previous_mode": event.previous_mode,
        "new_mode": event.new_mode,
        "subsystem_id": event.subsystem_id,
        "timestamp_ms": event.transition_timestamp_ms,
        "trigger_metric": event.trigger_metric,
        "trigger_value": event.trigger_value,
        "threshold": event.threshold,
        "burn_rate": event.burn_rate,
    }
    logger.info(f"[ADAPTIVE-CONTROL] {json.dumps(log_entry)}")

    return event


def emit_signal_log(signal: ControlSignal) -> None:
    """Emit structured JSON log for any control signal (Req 11.5)."""
    log_entry = {
        "event": "control_signal",
        "signal_type": signal.signal_type.value,
        "subsystem_id": signal.subsystem_id,
        "metric_name": signal.metric_name,
        "trigger_value": signal.trigger_value,
        "threshold": signal.threshold,
        "timestamp_ms": signal.timestamp_ms,
        "correlation_id": signal.correlation_id,
        "action": signal.signal_type.value,
    }
    logger.info(f"[ADAPTIVE-CONTROL] {json.dumps(log_entry)}")


def emit_failsafe_log(
    reason: str,
    exception_type: str,
    guard_mode: str,
    pdf_mode: str,
    correlation_id: Optional[str] = None,
) -> None:
    """Emit structured error log for fail-safe transitions (Req 6.6)."""
    log_entry = {
        "level": "ERROR",
        "component": "adaptive_control",
        "event": "failsafe_entered",
        "reason": reason,
        "exception_type": exception_type,
        "guard_mode": guard_mode,
        "pdf_mode": pdf_mode,
        "correlation_id": correlation_id or str(uuid.uuid4()),
    }
    logger.error(f"[ADAPTIVE-CONTROL] {json.dumps(log_entry)}")
