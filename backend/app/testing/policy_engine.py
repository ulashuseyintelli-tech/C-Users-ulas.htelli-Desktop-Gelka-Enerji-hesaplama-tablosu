"""
PR-7: Runtime Policy Engine + Safe Overrides (Governance Layer).

Policy-as-code: deterministic decision function over SLO/canary/ops/kill-switch state.
Override governance: bounded TTL, scoped, auditable, non-overridable guards.
Pure functions — no IO, no real time. FakeClock-compatible.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Policy action + rationale
# ---------------------------------------------------------------------------

class PolicyAction(str, Enum):
    PROMOTE = "promote"
    ABORT = "abort"
    HOLD = "hold"
    DEGRADE = "degrade"


class RationaleCode(str, Enum):
    SLO_MET = "SLO_MET"
    SLO_NOT_MET = "SLO_NOT_MET"
    CANARY_PROMOTE = "CANARY_PROMOTE"
    CANARY_ABORT = "CANARY_ABORT"
    CANARY_HOLD = "CANARY_HOLD"
    OPS_GATE_PASS = "OPS_GATE_PASS"
    OPS_GATE_FAIL = "OPS_GATE_FAIL"
    KILLSWITCH_ACTIVE = "KILLSWITCH_ACTIVE"
    OVERRIDE_APPLIED = "OVERRIDE_APPLIED"
    OVERRIDE_EXPIRED = "OVERRIDE_EXPIRED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


# ---------------------------------------------------------------------------
# Policy input
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SloStatus:
    met: bool
    budget_remaining: float  # [0, 1]


@dataclass(frozen=True)
class CanaryStatus:
    decision: str  # "promote" | "abort" | "hold"


@dataclass(frozen=True)
class OpsGateStatus:
    passed: bool


@dataclass(frozen=True)
class PolicyInput:
    slo: SloStatus
    canary: CanaryStatus
    ops_gate: OpsGateStatus
    killswitch_active: bool = False
    tenant: Optional[str] = None
    dependency: Optional[str] = None


# ---------------------------------------------------------------------------
# Policy decision output
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PolicyDecision:
    action: PolicyAction
    rationale: list[RationaleCode]
    required_approvals: int = 0
    details: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Override types + scope
# ---------------------------------------------------------------------------

class OverrideType(str, Enum):
    FORCE_PROMOTE = "force_promote"
    FORCE_ABORT = "force_abort"
    FORCE_DEGRADE = "force_degrade"


class OverrideScope(str, Enum):
    GLOBAL = "global"
    TENANT = "tenant"
    DEPENDENCY = "dependency"


# Non-overridable guards — these invariants cannot be bypassed
NON_OVERRIDABLE_GUARDS = frozenset({
    "no_false_positive",
    "cardinality_bound",
    "ops_label_contract",
})


@dataclass(frozen=True)
class Override:
    override_type: OverrideType
    scope: OverrideScope
    scope_value: Optional[str]  # tenant name or dependency name
    ttl_seconds: int
    created_at_ms: int
    reason: str
    created_by: str
    idempotency_key: str  # prevents duplicate overrides

    @property
    def expires_at_ms(self) -> int:
        return self.created_at_ms + (self.ttl_seconds * 1000)

    def is_expired(self, now_ms: int) -> bool:
        return now_ms >= self.expires_at_ms


MAX_OVERRIDE_TTL_SECONDS = 3600  # 1 hour hard cap


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AuditEntry:
    timestamp_ms: int
    action: str
    override: Optional[Override]
    policy_input: Optional[PolicyInput]
    decision: Optional[PolicyDecision]
    detail: str = ""


class AuditLog:
    """Append-only audit log for policy decisions and overrides."""

    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []
        self._seen_keys: set[str] = set()

    @property
    def entries(self) -> list[AuditEntry]:
        return list(self._entries)

    def record(self, entry: AuditEntry) -> bool:
        """Record an entry. Returns False if idempotency_key is duplicate."""
        if entry.override and entry.override.idempotency_key:
            if entry.override.idempotency_key in self._seen_keys:
                return False
            self._seen_keys.add(entry.override.idempotency_key)
        self._entries.append(entry)
        return True

    def has_key(self, key: str) -> bool:
        return key in self._seen_keys


# ---------------------------------------------------------------------------
# Policy Engine — pure decision function
# ---------------------------------------------------------------------------

_OVERRIDE_TO_ACTION = {
    OverrideType.FORCE_PROMOTE: PolicyAction.PROMOTE,
    OverrideType.FORCE_ABORT: PolicyAction.ABORT,
    OverrideType.FORCE_DEGRADE: PolicyAction.DEGRADE,
}


class PolicyEngine:
    """
    Deterministic policy engine.
    Same input → same output. No IO, no real time.
    """

    def __init__(self, max_override_ttl: int = MAX_OVERRIDE_TTL_SECONDS):
        self._max_ttl = max_override_ttl

    def evaluate(self, inp: PolicyInput) -> PolicyDecision:
        """Core policy evaluation — no overrides."""
        rationale: list[RationaleCode] = []

        # Kill-switch takes absolute priority
        if inp.killswitch_active:
            rationale.append(RationaleCode.KILLSWITCH_ACTIVE)
            return PolicyDecision(
                action=PolicyAction.ABORT,
                rationale=rationale,
                details={"reason": "killswitch active"},
            )

        # Ops gate
        if inp.ops_gate.passed:
            rationale.append(RationaleCode.OPS_GATE_PASS)
        else:
            rationale.append(RationaleCode.OPS_GATE_FAIL)
            return PolicyDecision(
                action=PolicyAction.ABORT,
                rationale=rationale,
                details={"reason": "ops gate failed"},
            )

        # SLO
        if inp.slo.met:
            rationale.append(RationaleCode.SLO_MET)
        else:
            rationale.append(RationaleCode.SLO_NOT_MET)
            if inp.slo.budget_remaining <= 0:
                rationale.append(RationaleCode.BUDGET_EXHAUSTED)
                return PolicyDecision(
                    action=PolicyAction.ABORT,
                    rationale=rationale,
                    details={"budget_remaining": inp.slo.budget_remaining},
                )
            # Budget not exhausted but SLO not met → DEGRADE
            return PolicyDecision(
                action=PolicyAction.DEGRADE,
                rationale=rationale,
                details={"budget_remaining": inp.slo.budget_remaining},
            )

        # Canary
        if inp.canary.decision == "promote":
            rationale.append(RationaleCode.CANARY_PROMOTE)
            return PolicyDecision(action=PolicyAction.PROMOTE, rationale=rationale)
        elif inp.canary.decision == "abort":
            rationale.append(RationaleCode.CANARY_ABORT)
            return PolicyDecision(action=PolicyAction.ABORT, rationale=rationale)
        else:
            rationale.append(RationaleCode.CANARY_HOLD)
            return PolicyDecision(
                action=PolicyAction.HOLD,
                rationale=rationale,
                details={"canary_decision": inp.canary.decision},
            )

    def evaluate_with_override(
        self,
        inp: PolicyInput,
        override: Optional[Override],
        now_ms: int,
        violated_guards: Optional[set[str]] = None,
    ) -> PolicyDecision:
        """
        Evaluate with optional override.
        Override is rejected if:
        - expired
        - TTL exceeds max
        - scope escalation (tenant→global not allowed)
        - non-overridable guard violated
        """
        base = self.evaluate(inp)

        if override is None:
            return base

        # Check TTL cap
        if override.ttl_seconds > self._max_ttl:
            return PolicyDecision(
                action=base.action,
                rationale=base.rationale + [RationaleCode.OVERRIDE_EXPIRED],
                details={**base.details, "override_rejected": "ttl_exceeds_max"},
            )

        # Check expiry
        if override.is_expired(now_ms):
            return PolicyDecision(
                action=base.action,
                rationale=base.rationale + [RationaleCode.OVERRIDE_EXPIRED],
                details={**base.details, "override_rejected": "expired"},
            )

        # Check scope escalation
        if not self._scope_matches(override, inp):
            return PolicyDecision(
                action=base.action,
                rationale=base.rationale,
                details={**base.details, "override_rejected": "scope_mismatch"},
            )

        # Check non-overridable guards
        guards = violated_guards or set()
        blocked = guards & NON_OVERRIDABLE_GUARDS
        if blocked:
            return PolicyDecision(
                action=base.action,
                rationale=base.rationale,
                details={
                    **base.details,
                    "override_rejected": "non_overridable_guard",
                    "blocked_guards": sorted(blocked),
                },
            )

        # Apply override
        action = _OVERRIDE_TO_ACTION[override.override_type]
        return PolicyDecision(
            action=action,
            rationale=base.rationale + [RationaleCode.OVERRIDE_APPLIED],
            details={
                "override_type": override.override_type.value,
                "override_scope": override.scope.value,
                "override_expires_at_ms": override.expires_at_ms,
            },
        )

    @staticmethod
    def _scope_matches(override: Override, inp: PolicyInput) -> bool:
        """
        Scope validation:
        - GLOBAL matches everything
        - TENANT matches only if inp.tenant == scope_value
        - DEPENDENCY matches only if inp.dependency == scope_value
        Escalation (tenant→global) is prevented by requiring exact match.
        """
        if override.scope == OverrideScope.GLOBAL:
            return True
        if override.scope == OverrideScope.TENANT:
            return inp.tenant is not None and inp.tenant == override.scope_value
        if override.scope == OverrideScope.DEPENDENCY:
            return inp.dependency is not None and inp.dependency == override.scope_value
        return False
