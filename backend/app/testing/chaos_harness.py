"""
PR-5: Chaos test harness — FakeClock, FaultSchedule, ChaosTrace.

Provides deterministic, replayable chaos testing infrastructure.
All chaos tests use seed-based scheduling; no randomness leaks.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ---------------------------------------------------------------------------
# FakeClock — deterministic virtual time
# ---------------------------------------------------------------------------

class FakeClock:
    """
    Virtual clock for time anomaly testing.
    Supports forward jump, backward jump, and jitter.
    All operations are deterministic given the same sequence of calls.
    """

    def __init__(self, start_ms: int = 1_000_000):
        self._current_ms = start_ms

    @property
    def now_ms(self) -> int:
        return self._current_ms

    def now_ms_fn(self) -> int:
        """Callable compatible with LoadHarness(now_ms_fn=...)."""
        return self._current_ms

    def advance(self, delta_ms: int) -> None:
        """Move time forward by delta_ms."""
        if delta_ms < 0:
            raise ValueError("Use jump_backward for negative deltas")
        self._current_ms += delta_ms

    def jump_forward(self, delta_ms: int) -> None:
        """Simulate a forward time jump (e.g., NTP correction)."""
        self._current_ms += abs(delta_ms)

    def jump_backward(self, delta_ms: int) -> None:
        """Simulate a backward time jump (e.g., NTP correction, clock skew)."""
        self._current_ms -= abs(delta_ms)
        # Floor at 0 to avoid negative timestamps
        self._current_ms = max(0, self._current_ms)

    def jitter(self, max_jitter_ms: int, rng: random.Random) -> None:
        """Apply random jitter within [-max_jitter_ms, +max_jitter_ms]."""
        delta = rng.randint(-max_jitter_ms, max_jitter_ms)
        self._current_ms = max(0, self._current_ms + delta)


# ---------------------------------------------------------------------------
# FaultSchedule — deterministic fault plan
# ---------------------------------------------------------------------------

class FaultAction(str, Enum):
    FAIL = "fail"
    TIMEOUT = "timeout"
    TRUNCATE = "truncate"
    SKIP = "skip"          # no fault (success)
    CLOCK_JUMP_FWD = "clock_jump_fwd"
    CLOCK_JUMP_BWD = "clock_jump_bwd"


@dataclass(frozen=True)
class ScheduledFault:
    """A single fault event in the schedule."""
    step: int
    action: FaultAction
    params: dict[str, Any] = field(default_factory=dict)


class FaultSchedule:
    """
    Deterministic fault schedule generated from a seed.
    Replayable: same seed + same config → same schedule.
    """

    def __init__(self, seed: int, total_steps: int, fault_rate: float = 0.3,
                 allowed_actions: Optional[list[FaultAction]] = None):
        self._seed = seed
        self._total_steps = total_steps
        self._fault_rate = fault_rate
        self._allowed = allowed_actions or [FaultAction.FAIL, FaultAction.TIMEOUT]
        self._schedule = self._generate()

    def _generate(self) -> list[ScheduledFault]:
        rng = random.Random(self._seed)
        schedule = []
        for step in range(self._total_steps):
            if rng.random() < self._fault_rate:
                action = rng.choice(self._allowed)
                params: dict[str, Any] = {}
                if action == FaultAction.TIMEOUT:
                    params["delay_ms"] = rng.randint(100, 5000)
                elif action in (FaultAction.CLOCK_JUMP_FWD, FaultAction.CLOCK_JUMP_BWD):
                    params["delta_ms"] = rng.randint(10, 500)
                elif action == FaultAction.TRUNCATE:
                    params["truncate_pct"] = rng.uniform(0.1, 0.9)
                schedule.append(ScheduledFault(step=step, action=action, params=params))
            else:
                schedule.append(ScheduledFault(step=step, action=FaultAction.SKIP, params={}))
        return schedule

    @property
    def seed(self) -> int:
        return self._seed

    @property
    def total_steps(self) -> int:
        return self._total_steps

    @property
    def events(self) -> list[ScheduledFault]:
        return list(self._schedule)

    @property
    def fault_count(self) -> int:
        return sum(1 for e in self._schedule if e.action != FaultAction.SKIP)

    def action_at(self, step: int) -> ScheduledFault:
        if 0 <= step < len(self._schedule):
            return self._schedule[step]
        return ScheduledFault(step=step, action=FaultAction.SKIP)


# ---------------------------------------------------------------------------
# FaultBudget — max fault rate + burst limit
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FaultBudget:
    """Limits on fault injection to prevent flaky tests."""
    max_fault_rate: float = 0.5       # max fraction of steps that can be faults
    max_burst: int = 5                # max consecutive faults
    max_clock_jumps: int = 3          # max clock anomalies per run

    def validate(self, schedule: FaultSchedule) -> bool:
        """Return True if schedule respects budget constraints."""
        events = schedule.events
        total = len(events)
        if total == 0:
            return True

        # Check overall fault rate
        fault_count = schedule.fault_count
        if fault_count / total > self.max_fault_rate:
            return False

        # Check burst limit
        consecutive = 0
        for e in events:
            if e.action != FaultAction.SKIP:
                consecutive += 1
                if consecutive > self.max_burst:
                    return False
            else:
                consecutive = 0

        # Check clock jump limit
        clock_jumps = sum(
            1 for e in events
            if e.action in (FaultAction.CLOCK_JUMP_FWD, FaultAction.CLOCK_JUMP_BWD)
        )
        if clock_jumps > self.max_clock_jumps:
            return False

        return True


# ---------------------------------------------------------------------------
# ChaosTrace — replayable execution trace
# ---------------------------------------------------------------------------

@dataclass
class TraceEntry:
    step: int
    action: FaultAction
    clock_ms: int
    outcome: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChaosTrace:
    """
    Captures full execution trace for replay on failure.
    Includes seed, schedule, and per-step decisions.
    """
    seed: int
    schedule_summary: dict[str, Any]
    entries: list[TraceEntry] = field(default_factory=list)

    def add(self, step: int, action: FaultAction, clock_ms: int,
            outcome: str, detail: Optional[dict[str, Any]] = None) -> None:
        self.entries.append(TraceEntry(
            step=step, action=action, clock_ms=clock_ms,
            outcome=outcome, detail=detail or {},
        ))

    def replay_info(self) -> dict[str, Any]:
        """Minimal info needed to reproduce this run."""
        return {
            "seed": self.seed,
            "total_steps": self.schedule_summary.get("total_steps"),
            "fault_count": self.schedule_summary.get("fault_count"),
            "entries_count": len(self.entries),
            "failed_steps": [
                e.step for e in self.entries if e.outcome == "invariant_violation"
            ],
        }
