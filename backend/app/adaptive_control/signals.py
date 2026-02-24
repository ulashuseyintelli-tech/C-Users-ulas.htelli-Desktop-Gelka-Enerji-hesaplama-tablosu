"""
Control Signal types — bounded action set + priority levels.

Feature: slo-adaptive-control, Task 7.1
Requirements: CC.1, CC.3
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum, IntEnum


class SignalType(str, Enum):
    """Bounded action set (Req CC.1). No other signal types allowed."""
    SWITCH_TO_SHADOW = "switch_to_shadow"
    RESTORE_ENFORCE = "restore_enforce"
    STOP_ACCEPTING_JOBS = "stop_accepting_jobs"
    RESUME_ACCEPTING_JOBS = "resume_accepting_jobs"


class PriorityLevel(IntEnum):
    """Deterministic priority order (Req CC.3). Lower = higher priority."""
    KILLSWITCH = 1
    MANUAL_OVERRIDE = 2
    ADAPTIVE_CONTROL = 3
    DEFAULT_CONFIG = 4


@dataclass(frozen=True)
class ControlSignal:
    """A single control signal produced by the decision engine."""
    signal_type: SignalType
    subsystem_id: str
    metric_name: str
    tenant_id: str = "*"
    trigger_value: float = 0.0
    threshold: float = 0.0
    priority: PriorityLevel = PriorityLevel.ADAPTIVE_CONTROL
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp_ms: int = 0
