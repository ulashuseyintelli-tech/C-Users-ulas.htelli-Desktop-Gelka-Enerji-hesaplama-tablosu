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
from typing import TYPE_CHECKING, Any

from backend.app.testing.policy_engine import AuditEntry, AuditLog
from backend.app.testing.release_policy import (
    ABSOLUTE_BLOCK_REASONS,
    BlockReasonCode,
    ReleasePolicyResult,
    ReleaseVerdict,
)

if TYPE_CHECKING:
    from backend.app.testing.gate_metrics import GateMetricStore


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
        gate = ReleaseGate(audit_log, metric_store)
        decision = gate.check(policy_result, release_scope="v2.4", ...)
        if decision.allowed:
            orchestrator.execute(...)
        else:
            # no effects, audit already recorded
    """

    def __init__(
        self,
        audit_log: AuditLog | None = None,
        metric_store: "GateMetricStore | None" = None,
    ):
        self._audit = audit_log or AuditLog()
        self._metrics: "GateMetricStore | None" = metric_store

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

        Single exit: decision computed → audit attempted → metrics emitted.
        """
        verdict = policy_result.verdict
        reasons = policy_result.reasons
        is_breach = False

        # --- RELEASE_OK ---
        if verdict == ReleaseVerdict.RELEASE_OK:
            decision = GateDecision(
                allowed=True,
                verdict=verdict,
                reasons=reasons,
                override_applied=False,
                audit_detail="release_ok: promote allowed",
            )
            audit_action = "release_gate_allow"

        # --- RELEASE_BLOCK ---
        elif verdict == ReleaseVerdict.RELEASE_BLOCK:
            if override is not None and self._has_absolute_block(reasons):
                decision = GateDecision(
                    allowed=False,
                    verdict=verdict,
                    reasons=reasons,
                    override_applied=False,
                    audit_detail="CONTRACT_BREACH_NO_OVERRIDE: "
                                "override rejected for absolute block reasons",
                )
                audit_action = "release_gate_override_rejected_absolute"
                is_breach = True
            else:
                decision = GateDecision(
                    allowed=False,
                    verdict=verdict,
                    reasons=reasons,
                    override_applied=False,
                    audit_detail=f"release_block: {[r.value for r in reasons]}",
                )
                audit_action = "release_gate_block"

        # --- RELEASE_HOLD ---
        elif override is None:
            decision = GateDecision(
                allowed=False,
                verdict=verdict,
                reasons=reasons,
                override_applied=False,
                audit_detail="release_hold: manual override required",
            )
            audit_action = "release_gate_hold"
        else:
            # Override provided — validate
            decision, audit_action = self._validate_override(
                override, verdict, reasons, release_scope, now_ms,
            )

        # --- Single exit: audit → metrics ---
        self._record_audit(audit_action, decision)
        self._emit_metrics(decision, is_breach)
        return decision

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
    ) -> tuple[GateDecision, str]:
        # TTL check
        if override.is_expired(now_ms):
            return GateDecision(
                allowed=False,
                verdict=verdict,
                reasons=reasons,
                override_applied=False,
                audit_detail="OVERRIDE_EXPIRED: override TTL has elapsed",
            ), "release_gate_override_expired"

        # Scope check
        if release_scope and override.scope != release_scope:
            return GateDecision(
                allowed=False,
                verdict=verdict,
                reasons=reasons,
                override_applied=False,
                audit_detail=f"SCOPE_MISMATCH: override scope '{override.scope}' "
                             f"!= release scope '{release_scope}'",
            ), "release_gate_scope_mismatch"

        # Valid override
        return GateDecision(
            allowed=True,
            verdict=verdict,
            reasons=reasons,
            override_applied=True,
            audit_detail=f"release_hold: override accepted by {override.created_by}",
        ), "release_gate_override_accepted"

    def _record_audit(self, action: str, decision: GateDecision) -> None:
        try:
            entry = AuditEntry(
                timestamp_ms=0,
                action=action,
                override=None,
                policy_input=None,
                decision=None,
                detail=decision.audit_detail,
            )
            self._audit.record(entry)
        except Exception:
            # R3 fail-closed: audit write failure → emit counter
            self._emit_audit_failure()

    def _emit_metrics(self, decision: GateDecision, is_breach: bool) -> None:
        """Fail-open metrik emisyonu. Hata gate kararını etkilemez."""
        if self._metrics is None:
            return
        try:
            reason_values = [r.value for r in decision.reasons]
            self._metrics.record_decision(decision.allowed, reason_values)
            if is_breach:
                self._metrics.record_breach()
        except Exception:
            pass  # fail-open

    def _emit_audit_failure(self) -> None:
        """Audit yazım hatası durumunda çağrılır."""
        if self._metrics is None:
            return
        try:
            self._metrics.record_audit_write_failure()
        except Exception:
            pass  # fail-open
