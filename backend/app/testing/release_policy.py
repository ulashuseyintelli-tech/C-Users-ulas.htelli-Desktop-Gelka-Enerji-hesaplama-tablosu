"""
PR-11: Release Governance — ReleasePolicy (pure-math decision function).

Consumes PR-10 outputs (TierRunResult, FlakeSentinel, DriftSnapshot,
PolicyCanaryResult, OpsGateStatus) and produces a deterministic
RELEASE_OK / RELEASE_HOLD / RELEASE_BLOCK verdict.

Pure function — no IO, no side effects.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from backend.app.testing.perf_budget import TierRunResult
from backend.app.testing.policy_engine import OpsGateStatus
from backend.app.testing.rollout_orchestrator import (
    DriftSnapshot,
    PolicyCanaryResult,
)


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------

class ReleaseVerdict(str, Enum):
    """Ordered: OK < HOLD < BLOCK (monotonic)."""
    RELEASE_OK = "release_ok"
    RELEASE_HOLD = "release_hold"
    RELEASE_BLOCK = "release_block"


_VERDICT_ORDER = {
    ReleaseVerdict.RELEASE_OK: 0,
    ReleaseVerdict.RELEASE_HOLD: 1,
    ReleaseVerdict.RELEASE_BLOCK: 2,
}


def _worst(a: ReleaseVerdict, b: ReleaseVerdict) -> ReleaseVerdict:
    return a if _VERDICT_ORDER[a] >= _VERDICT_ORDER[b] else b


# ---------------------------------------------------------------------------
# Block reason codes
# ---------------------------------------------------------------------------

class BlockReasonCode(str, Enum):
    TIER_FAIL = "TIER_FAIL"
    FLAKY_TESTS = "FLAKY_TESTS"
    DRIFT_ALERT = "DRIFT_ALERT"
    CANARY_BREAKING = "CANARY_BREAKING"
    GUARD_VIOLATION = "GUARD_VIOLATION"
    OPS_GATE_FAIL = "OPS_GATE_FAIL"
    NO_TIER_DATA = "NO_TIER_DATA"
    NO_FLAKE_DATA = "NO_FLAKE_DATA"
    NO_DRIFT_DATA = "NO_DRIFT_DATA"
    NO_CANARY_DATA = "NO_CANARY_DATA"


# Absolute block reasons — cannot be overridden
ABSOLUTE_BLOCK_REASONS: frozenset[BlockReasonCode] = frozenset({
    BlockReasonCode.GUARD_VIOLATION,
    BlockReasonCode.OPS_GATE_FAIL,
})


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RequiredAction:
    code: BlockReasonCode
    description: str


@dataclass(frozen=True)
class ReleasePolicyInput:
    tier_results: list[TierRunResult]
    flake_snapshot: list[str] | None        # FlakeSentinel.detect_flaky() or None
    drift_snapshot: DriftSnapshot | None
    canary_result: PolicyCanaryResult | None
    ops_gate: OpsGateStatus


@dataclass(frozen=True)
class ReleasePolicyResult:
    verdict: ReleaseVerdict
    reasons: list[BlockReasonCode]
    required_actions: list[RequiredAction]
    details: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Required-action descriptions (deterministic)
# ---------------------------------------------------------------------------

_ACTION_DESCRIPTIONS: dict[BlockReasonCode, str] = {
    BlockReasonCode.TIER_FAIL: "Fix failing tier tests and re-run the tier",
    BlockReasonCode.FLAKY_TESTS: "Investigate and stabilise flaky tests",
    BlockReasonCode.DRIFT_ALERT: "Review drift monitor alerts and reduce abort/override rates",
    BlockReasonCode.CANARY_BREAKING: "Resolve breaking policy drifts before release",
    BlockReasonCode.GUARD_VIOLATION: "Fix guard violations — contract breach, no override allowed",
    BlockReasonCode.OPS_GATE_FAIL: "Fix ops gate failures — contract breach, no override allowed",
    BlockReasonCode.NO_TIER_DATA: "Provide tier run results before release evaluation",
    BlockReasonCode.NO_FLAKE_DATA: "Provide flake sentinel snapshot before release evaluation",
    BlockReasonCode.NO_DRIFT_DATA: "Provide drift monitor snapshot before release evaluation",
    BlockReasonCode.NO_CANARY_DATA: "Provide policy canary result before release evaluation",
}


# ---------------------------------------------------------------------------
# ReleasePolicy — pure-math decision function
# ---------------------------------------------------------------------------

class ReleasePolicy:
    """
    Deterministic release decision function.

    Evaluation order:
    1. Input validation (missing data → BLOCK or HOLD)
    2. Absolute blocks (GUARD_VIOLATION, OPS_GATE_FAIL → BLOCK)
    3. Tier results (fail → HOLD)
    4. Flake check (flaky tests → HOLD)
    5. Drift check (alert → HOLD)
    6. Canary check (BREAKING → HOLD)
    7. All signals clean → OK

    Monotonic rule: BLOCK > HOLD > OK.
    Adding a bad signal never lowers the verdict.
    All reason codes are merged.
    """

    def evaluate(self, inp: ReleasePolicyInput) -> ReleasePolicyResult:
        verdict = ReleaseVerdict.RELEASE_OK
        reasons: list[BlockReasonCode] = []
        details: dict[str, Any] = {}

        # --- 1. Input validation ---
        if not inp.tier_results:
            verdict = _worst(verdict, ReleaseVerdict.RELEASE_BLOCK)
            reasons.append(BlockReasonCode.NO_TIER_DATA)

        if inp.flake_snapshot is None:
            verdict = _worst(verdict, ReleaseVerdict.RELEASE_BLOCK)
            reasons.append(BlockReasonCode.NO_FLAKE_DATA)

        if inp.drift_snapshot is None:
            verdict = _worst(verdict, ReleaseVerdict.RELEASE_HOLD)
            reasons.append(BlockReasonCode.NO_DRIFT_DATA)

        if inp.canary_result is None:
            verdict = _worst(verdict, ReleaseVerdict.RELEASE_HOLD)
            reasons.append(BlockReasonCode.NO_CANARY_DATA)

        # --- 2. Absolute blocks (contract breaches) ---
        if inp.ops_gate is not None and not inp.ops_gate.passed:
            verdict = _worst(verdict, ReleaseVerdict.RELEASE_BLOCK)
            reasons.append(BlockReasonCode.OPS_GATE_FAIL)

        if inp.canary_result is not None and inp.canary_result.guard_violations > 0:
            verdict = _worst(verdict, ReleaseVerdict.RELEASE_BLOCK)
            reasons.append(BlockReasonCode.GUARD_VIOLATION)
            details["guard_violations"] = inp.canary_result.guard_violations

        # --- 3. Tier results ---
        failed_tiers: list[str] = []
        for tr in inp.tier_results:
            if not tr.passed:
                failed_tiers.append(tr.tier.value)
        if failed_tiers:
            verdict = _worst(verdict, ReleaseVerdict.RELEASE_HOLD)
            reasons.append(BlockReasonCode.TIER_FAIL)
            details["failed_tiers"] = failed_tiers

        # --- 4. Flake check ---
        if inp.flake_snapshot is not None and len(inp.flake_snapshot) > 0:
            verdict = _worst(verdict, ReleaseVerdict.RELEASE_HOLD)
            reasons.append(BlockReasonCode.FLAKY_TESTS)
            details["flaky_tests"] = inp.flake_snapshot

        # --- 5. Drift check ---
        if inp.drift_snapshot is not None and inp.drift_snapshot.alert:
            verdict = _worst(verdict, ReleaseVerdict.RELEASE_HOLD)
            reasons.append(BlockReasonCode.DRIFT_ALERT)
            details["drift_abort_rate"] = inp.drift_snapshot.abort_rate
            details["drift_override_rate"] = inp.drift_snapshot.override_rate

        # --- 6. Canary check ---
        if (
            inp.canary_result is not None
            and inp.canary_result.breaking > 0
            and BlockReasonCode.GUARD_VIOLATION not in reasons
        ):
            verdict = _worst(verdict, ReleaseVerdict.RELEASE_HOLD)
            reasons.append(BlockReasonCode.CANARY_BREAKING)
            details["canary_breaking"] = inp.canary_result.breaking

        # --- Build required actions ---
        actions = [
            RequiredAction(code=r, description=_ACTION_DESCRIPTIONS[r])
            for r in reasons
        ]

        return ReleasePolicyResult(
            verdict=verdict,
            reasons=reasons,
            required_actions=actions,
            details=details,
        )
