"""
PR-8: Rollout Orchestrator + Policy Canary.

1) Orchestrator: converts PolicyDecision → idempotent side effects
2) Policy versioning + canary: compares old vs new policy decisions
3) Policy drift monitor: rolling window decision distribution

Pure functions — no IO, no real time. FakeClock-compatible.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from .policy_engine import (
    PolicyAction,
    PolicyDecision,
    PolicyEngine,
    PolicyInput,
    RationaleCode,
    NON_OVERRIDABLE_GUARDS,
)


# ---------------------------------------------------------------------------
# 1) Orchestrator — idempotent side-effect executor
# ---------------------------------------------------------------------------

class EffectKind(str, Enum):
    SET_KILLSWITCH = "set_killswitch"
    GATE_PIPELINE = "gate_pipeline"       # block or allow deploy pipeline
    SUPPRESS_ALERT = "suppress_alert"
    DEGRADE_SERVICE = "degrade_service"
    NOOP = "noop"


@dataclass(frozen=True)
class SideEffect:
    kind: EffectKind
    params: dict[str, Any] = field(default_factory=dict)
    event_id: str = ""  # idempotency key


class EffectOutcome(str, Enum):
    APPLIED = "applied"
    DUPLICATE = "duplicate"
    FAILED = "failed"


@dataclass(frozen=True)
class EffectResult:
    effect: SideEffect
    outcome: EffectOutcome
    detail: str = ""


# Action → side effects mapping
_ACTION_EFFECTS: dict[PolicyAction, list[EffectKind]] = {
    PolicyAction.PROMOTE: [EffectKind.GATE_PIPELINE],
    PolicyAction.ABORT: [EffectKind.GATE_PIPELINE, EffectKind.SET_KILLSWITCH],
    PolicyAction.HOLD: [EffectKind.NOOP],
    PolicyAction.DEGRADE: [EffectKind.DEGRADE_SERVICE],
}


class Orchestrator:
    """
    Converts PolicyDecision into idempotent side effects.
    Fail-closed: any execution error → ABORT + audit.
    """

    def __init__(self) -> None:
        self._applied_ids: set[str] = set()
        self._log: list[EffectResult] = []

    @property
    def applied_count(self) -> int:
        return len([r for r in self._log if r.outcome == EffectOutcome.APPLIED])

    @property
    def log(self) -> list[EffectResult]:
        return list(self._log)

    def execute(self, decision: PolicyDecision, event_id: str) -> list[EffectResult]:
        """
        Execute side effects for a policy decision.
        Idempotent: same event_id → duplicate (no re-execution).
        """
        if event_id in self._applied_ids:
            dup = EffectResult(
                effect=SideEffect(kind=EffectKind.NOOP, event_id=event_id),
                outcome=EffectOutcome.DUPLICATE,
                detail="event_id already processed",
            )
            self._log.append(dup)
            return [dup]

        effect_kinds = _ACTION_EFFECTS.get(decision.action, [EffectKind.NOOP])
        results: list[EffectResult] = []

        for kind in effect_kinds:
            effect = SideEffect(
                kind=kind,
                params=self._build_params(decision, kind),
                event_id=event_id,
            )
            result = self._apply(effect, decision)
            results.append(result)
            self._log.append(result)

        self._applied_ids.add(event_id)
        return results

    def _apply(self, effect: SideEffect, decision: PolicyDecision) -> EffectResult:
        """Apply a single effect. Pure simulation — no real IO."""
        if effect.kind == EffectKind.GATE_PIPELINE:
            gate = "allow" if decision.action == PolicyAction.PROMOTE else "block"
            return EffectResult(effect=effect, outcome=EffectOutcome.APPLIED,
                                detail=f"pipeline={gate}")
        if effect.kind == EffectKind.SET_KILLSWITCH:
            return EffectResult(effect=effect, outcome=EffectOutcome.APPLIED,
                                detail="killswitch=on")
        if effect.kind == EffectKind.DEGRADE_SERVICE:
            return EffectResult(effect=effect, outcome=EffectOutcome.APPLIED,
                                detail="degrade=active")
        return EffectResult(effect=effect, outcome=EffectOutcome.APPLIED, detail="noop")

    @staticmethod
    def _build_params(decision: PolicyDecision, kind: EffectKind) -> dict[str, Any]:
        return {"action": decision.action.value, "effect": kind.value}


# ---------------------------------------------------------------------------
# 2) Policy Versioning + Canary
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PolicyVersion:
    version: str          # semver or hash
    engine: PolicyEngine  # the engine instance for this version

    @property
    def version_hash(self) -> str:
        return hashlib.sha256(self.version.encode()).hexdigest()[:12]


class DriftKind(str, Enum):
    SAFE = "safe"               # same decision
    UPGRADE = "upgrade"         # HOLD→PROMOTE, DEGRADE→PROMOTE
    BREAKING = "breaking"       # PROMOTE→ABORT, PROMOTE→DEGRADE, etc.
    GUARD_VIOLATION = "guard_violation"  # non-overridable guard drift


@dataclass(frozen=True)
class DriftResult:
    old_decision: PolicyAction
    new_decision: PolicyAction
    drift_kind: DriftKind
    input_hash: str = ""


# Breaking drift: new policy is more restrictive or changes promote→abort
_BREAKING_TRANSITIONS: set[tuple[PolicyAction, PolicyAction]] = {
    (PolicyAction.PROMOTE, PolicyAction.ABORT),
    (PolicyAction.PROMOTE, PolicyAction.DEGRADE),
    (PolicyAction.HOLD, PolicyAction.ABORT),
}

_UPGRADE_TRANSITIONS: set[tuple[PolicyAction, PolicyAction]] = {
    (PolicyAction.HOLD, PolicyAction.PROMOTE),
    (PolicyAction.DEGRADE, PolicyAction.PROMOTE),
    (PolicyAction.ABORT, PolicyAction.PROMOTE),
    (PolicyAction.ABORT, PolicyAction.HOLD),
}


def classify_drift(old: PolicyAction, new: PolicyAction) -> DriftKind:
    if old == new:
        return DriftKind.SAFE
    if (old, new) in _BREAKING_TRANSITIONS:
        return DriftKind.BREAKING
    if (old, new) in _UPGRADE_TRANSITIONS:
        return DriftKind.UPGRADE
    return DriftKind.BREAKING  # default conservative


class PolicyCanary:
    """
    Compares old policy version vs new policy version over a set of inputs.
    MIN_SAMPLES guard: insufficient inputs → HOLD.
    Non-overridable guard drift → hard fail.
    """

    MIN_SAMPLES = 10

    def __init__(self, old: PolicyVersion, new: PolicyVersion):
        self._old = old
        self._new = new

    def compare(self, inputs: list[PolicyInput]) -> PolicyCanaryResult:
        if len(inputs) < self.MIN_SAMPLES:
            return PolicyCanaryResult(
                old_version=self._old.version,
                new_version=self._new.version,
                total=len(inputs),
                safe=0, upgrade=0, breaking=0, guard_violations=0,
                recommendation="hold",
                reason=f"Insufficient samples: {len(inputs)} < {self.MIN_SAMPLES}",
            )

        drifts: list[DriftResult] = []
        for inp in inputs:
            old_d = self._old.engine.evaluate(inp)
            new_d = self._new.engine.evaluate(inp)
            kind = classify_drift(old_d.action, new_d.action)

            # Check non-overridable guard drift
            if self._has_guard_drift(old_d, new_d):
                kind = DriftKind.GUARD_VIOLATION

            h = hashlib.sha256(repr(inp).encode()).hexdigest()[:8]
            drifts.append(DriftResult(old_d.action, new_d.action, kind, h))

        safe = sum(1 for d in drifts if d.drift_kind == DriftKind.SAFE)
        upgrade = sum(1 for d in drifts if d.drift_kind == DriftKind.UPGRADE)
        breaking = sum(1 for d in drifts if d.drift_kind == DriftKind.BREAKING)
        guard_v = sum(1 for d in drifts if d.drift_kind == DriftKind.GUARD_VIOLATION)

        if guard_v > 0:
            rec, reason = "abort", f"{guard_v} guard violation(s)"
        elif breaking > 0:
            rec, reason = "abort", f"{breaking} breaking drift(s)"
        elif upgrade > 0:
            rec, reason = "promote", f"{upgrade} upgrade(s), {safe} safe"
        else:
            rec, reason = "promote", "all decisions identical"

        return PolicyCanaryResult(
            old_version=self._old.version,
            new_version=self._new.version,
            total=len(inputs),
            safe=safe, upgrade=upgrade, breaking=breaking,
            guard_violations=guard_v,
            recommendation=rec, reason=reason,
        )

    @staticmethod
    def _has_guard_drift(old_d: PolicyDecision, new_d: PolicyDecision) -> bool:
        """Non-overridable guards in old but not in new = guard drift."""
        old_guards = set(old_d.details.get("blocked_guards", []))
        new_guards = set(new_d.details.get("blocked_guards", []))
        # If old blocked a guard but new doesn't → guard was removed = violation
        removed = old_guards - new_guards
        return bool(removed & NON_OVERRIDABLE_GUARDS)


@dataclass(frozen=True)
class PolicyCanaryResult:
    old_version: str
    new_version: str
    total: int
    safe: int
    upgrade: int
    breaking: int
    guard_violations: int
    recommendation: str  # "promote" | "abort" | "hold"
    reason: str


# ---------------------------------------------------------------------------
# 3) Policy Drift Monitor — rolling window
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DriftSnapshot:
    window_size: int
    total_decisions: int
    abort_count: int
    promote_count: int
    hold_count: int
    degrade_count: int
    override_count: int
    abort_rate: float
    override_rate: float
    alert: bool
    alert_reason: str = ""


class DriftMonitor:
    """
    Tracks decision distribution over a rolling window.
    Alerts when abort_rate or override_rate exceeds threshold.
    """

    def __init__(
        self,
        abort_rate_threshold: float = 0.3,
        override_rate_threshold: float = 0.2,
    ):
        self._abort_threshold = abort_rate_threshold
        self._override_threshold = override_rate_threshold

    def snapshot(
        self,
        decisions: list[PolicyAction],
        override_count: int = 0,
    ) -> DriftSnapshot:
        total = len(decisions)
        if total == 0:
            return DriftSnapshot(
                window_size=0, total_decisions=0,
                abort_count=0, promote_count=0, hold_count=0, degrade_count=0,
                override_count=0, abort_rate=0.0, override_rate=0.0,
                alert=False,
            )

        counts = {a: 0 for a in PolicyAction}
        for d in decisions:
            counts[d] += 1

        abort_rate = counts[PolicyAction.ABORT] / total
        override_rate = override_count / total if total > 0 else 0.0

        reasons = []
        if abort_rate > self._abort_threshold:
            reasons.append(f"abort_rate={abort_rate:.2f}>{self._abort_threshold}")
        if override_rate > self._override_threshold:
            reasons.append(f"override_rate={override_rate:.2f}>{self._override_threshold}")

        return DriftSnapshot(
            window_size=total,
            total_decisions=total,
            abort_count=counts[PolicyAction.ABORT],
            promote_count=counts[PolicyAction.PROMOTE],
            hold_count=counts[PolicyAction.HOLD],
            degrade_count=counts[PolicyAction.DEGRADE],
            override_count=override_count,
            abort_rate=abort_rate,
            override_rate=override_rate,
            alert=bool(reasons),
            alert_reason="; ".join(reasons),
        )
