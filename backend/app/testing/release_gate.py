"""
PR-11: Release Gate — enforcement hook for ReleasePolicy decisions.

Sits between ReleasePolicy and Orchestrator. Produces no side effects
itself; only returns a GateDecision that the Orchestrator respects.

Key invariants:
- RELEASE_OK → allowed=True
- RELEASE_BLOCK → allowed=False (no override path)
- RELEASE_HOLD → allowed=False unless valid override (TTL + scope)
- ABSOLUTE_BLOCK_REASONS (GUARD_VIOLATION, OPS_GATE_FAIL) →
  override attempts hard-rejected with CONTRACT_BREACH_NO_OVERRIDE
- Every check() call produces an audit entry
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.app.testing.policy_engine import AuditEntry, AuditLog
from backend.app.testing.release_policy import (
    ABSOLUTE_BLOCK_REASONS,
    BlockReasonCode,
    ReleasePolicyResult,
    ReleaseVerdict,
)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReleaseOverride:
    ttl_seconds: int
    created_at_ms: int
    scope: str          # release identifier to match against
    reason: str
    created_by: str

    @property
    def expires_at_ms(self) -> int:
        return self.created_at_ms + (self.ttl_seconds * 1000)

    def is_expired(self, now_ms: int) -> bool:
        return now_ms >= self.expires_at_ms


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    verdict: ReleaseVerdict
    reasons: list[BlockReasonCode]
    override_applied: bool
    audit_detail: str


# ---------------------------------------------------------------------------
# ReleaseGate
# ---------------------------------------------------------------------------

class ReleaseGate:
    """
    Enforcement hook. Pure decision — no side effects.

    Integration point: call ReleaseGate.check() before
    Orchestrator.execute(). If not allowed, skip execution.

    Entegrasyon:
        gate = ReleaseGate(audit_log)
        decision = gate.check(policy_result, release_scope="v2.4", ...)
        if decision.allowed:
            orchestrator.execute(...)
        else:
            # no effects, audit already recorded
    """

    def __init__(self, audit_log: AuditLog | None = None):
        self._audit = audit_log or AuditLog()

    @property
    def audit_log(self) -> AuditLog:
        return self._audit

    def check(
        self,
        policy_result: ReleasePolicyResult,
        override: ReleaseOverride | None = None,
        release_scope: str = "",
        now_ms: int = 0,
    ) -> GateDecision:
        """
        Evaluate gate decision from policy result.

        1. RELEASE_OK → allowed
        2. RELEASE_BLOCK → denied (absolute blocks reject override)
        3. RELEASE_HOLD → denied unless valid override
        """
        verdict = policy_result.verdict
        reasons = policy_result.reasons

        # --- RELEASE_OK ---
        if verdict == ReleaseVerdict.RELEASE_OK:
            decision = GateDecision(
                allowed=True,
                verdict=verdict,
                reasons=reasons,
                override_applied=False,
                audit_detail="release_ok: promote allowed",
            )
            self._record_audit("release_gate_allow", decision)
            return decision

        # --- RELEASE_BLOCK ---
        if verdict == ReleaseVerdict.RELEASE_BLOCK:
            # Check if override was attempted on absolute block
            if override is not None and self._has_absolute_block(reasons):
                decision = GateDecision(
                    allowed=False,
                    verdict=verdict,
                    reasons=reasons,
                    override_applied=False,
                    audit_detail="CONTRACT_BREACH_NO_OVERRIDE: "
                                "override rejected for absolute block reasons",
                )
                self._record_audit("release_gate_override_rejected_absolute", decision)
                return decision

            decision = GateDecision(
                allowed=False,
                verdict=verdict,
                reasons=reasons,
                override_applied=False,
                audit_detail=f"release_block: {[r.value for r in reasons]}",
            )
            self._record_audit("release_gate_block", decision)
            return decision

        # --- RELEASE_HOLD ---
        if override is None:
            decision = GateDecision(
                allowed=False,
                verdict=verdict,
                reasons=reasons,
                override_applied=False,
                audit_detail="release_hold: manual override required",
            )
            self._record_audit("release_gate_hold", decision)
            return decision

        # Override provided — validate
        return self._validate_override(override, verdict, reasons, release_scope, now_ms)

    # -- private --

    def _has_absolute_block(self, reasons: list[BlockReasonCode]) -> bool:
        return bool(set(reasons) & ABSOLUTE_BLOCK_REASONS)

    def _validate_override(
        self,
        override: ReleaseOverride,
        verdict: ReleaseVerdict,
        reasons: list[BlockReasonCode],
        release_scope: str,
        now_ms: int,
    ) -> GateDecision:
        # TTL check
        if override.is_expired(now_ms):
            decision = GateDecision(
                allowed=False,
                verdict=verdict,
                reasons=reasons,
                override_applied=False,
                audit_detail="OVERRIDE_EXPIRED: override TTL has elapsed",
            )
            self._record_audit("release_gate_override_expired", decision)
            return decision

        # Scope check
        if release_scope and override.scope != release_scope:
            decision = GateDecision(
                allowed=False,
                verdict=verdict,
                reasons=reasons,
                override_applied=False,
                audit_detail=f"SCOPE_MISMATCH: override scope '{override.scope}' "
                             f"!= release scope '{release_scope}'",
            )
            self._record_audit("release_gate_scope_mismatch", decision)
            return decision

        # Valid override
        decision = GateDecision(
            allowed=True,
            verdict=verdict,
            reasons=reasons,
            override_applied=True,
            audit_detail=f"release_hold: override accepted by {override.created_by}",
        )
        self._record_audit("release_gate_override_accepted", decision)
        return decision

    def _record_audit(self, action: str, decision: GateDecision) -> None:
        entry = AuditEntry(
            timestamp_ms=0,
            action=action,
            override=None,
            policy_input=None,
            decision=None,
            detail=decision.audit_detail,
        )
        self._audit.record(entry)
