"""
PR-8: Orchestrator tests.

- PROMOTE → pipeline allow
- ABORT → pipeline block + killswitch
- HOLD → noop
- DEGRADE → degrade_service
- Idempotency: same event_id → duplicate
- Fail-closed semantics
- PBT: idempotency invariant
- PBT: every action produces at least one effect
- PBT: applied_count monotonically increases
"""
import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from backend.app.testing.policy_engine import (
    PolicyAction,
    PolicyDecision,
    RationaleCode,
)
from backend.app.testing.rollout_orchestrator import (
    Orchestrator,
    EffectKind,
    EffectOutcome,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _decision(action: PolicyAction, rationale: list[RationaleCode] | None = None):
    return PolicyDecision(action=action, rationale=rationale or [])


# ---------------------------------------------------------------------------
# Action → effect mapping
# ---------------------------------------------------------------------------

class TestPromoteEffects:
    def test_promote_gates_pipeline_allow(self):
        orch = Orchestrator()
        results = orch.execute(_decision(PolicyAction.PROMOTE), "evt-1")
        assert len(results) == 1
        assert results[0].effect.kind == EffectKind.GATE_PIPELINE
        assert "allow" in results[0].detail

    def test_promote_applied(self):
        orch = Orchestrator()
        results = orch.execute(_decision(PolicyAction.PROMOTE), "evt-1")
        assert all(r.outcome == EffectOutcome.APPLIED for r in results)


class TestAbortEffects:
    def test_abort_blocks_pipeline_and_sets_killswitch(self):
        orch = Orchestrator()
        results = orch.execute(_decision(PolicyAction.ABORT), "evt-2")
        kinds = [r.effect.kind for r in results]
        assert EffectKind.GATE_PIPELINE in kinds
        assert EffectKind.SET_KILLSWITCH in kinds

    def test_abort_pipeline_is_block(self):
        orch = Orchestrator()
        results = orch.execute(_decision(PolicyAction.ABORT), "evt-2")
        gate = [r for r in results if r.effect.kind == EffectKind.GATE_PIPELINE][0]
        assert "block" in gate.detail


class TestHoldEffects:
    def test_hold_is_noop(self):
        orch = Orchestrator()
        results = orch.execute(_decision(PolicyAction.HOLD), "evt-3")
        assert len(results) == 1
        assert results[0].effect.kind == EffectKind.NOOP


class TestDegradeEffects:
    def test_degrade_activates_service_degrade(self):
        orch = Orchestrator()
        results = orch.execute(_decision(PolicyAction.DEGRADE), "evt-4")
        assert len(results) == 1
        assert results[0].effect.kind == EffectKind.DEGRADE_SERVICE
        assert results[0].outcome == EffectOutcome.APPLIED


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_same_event_id_returns_duplicate(self):
        orch = Orchestrator()
        orch.execute(_decision(PolicyAction.PROMOTE), "evt-dup")
        results = orch.execute(_decision(PolicyAction.PROMOTE), "evt-dup")
        assert len(results) == 1
        assert results[0].outcome == EffectOutcome.DUPLICATE

    def test_different_event_ids_both_applied(self):
        orch = Orchestrator()
        r1 = orch.execute(_decision(PolicyAction.PROMOTE), "evt-a")
        r2 = orch.execute(_decision(PolicyAction.PROMOTE), "evt-b")
        assert all(r.outcome == EffectOutcome.APPLIED for r in r1)
        assert all(r.outcome == EffectOutcome.APPLIED for r in r2)

    def test_applied_count_tracks_unique_events(self):
        orch = Orchestrator()
        orch.execute(_decision(PolicyAction.PROMOTE), "e1")
        orch.execute(_decision(PolicyAction.ABORT), "e2")
        orch.execute(_decision(PolicyAction.PROMOTE), "e1")  # dup
        assert orch.applied_count == 3  # 1 (promote) + 2 (abort) = 3


# ---------------------------------------------------------------------------
# Log
# ---------------------------------------------------------------------------

class TestOrchestratorLog:
    def test_log_captures_all_results(self):
        orch = Orchestrator()
        orch.execute(_decision(PolicyAction.PROMOTE), "e1")
        orch.execute(_decision(PolicyAction.ABORT), "e2")
        assert len(orch.log) == 3  # 1 promote + 2 abort


# ---------------------------------------------------------------------------
# PBT: idempotency invariant
# ---------------------------------------------------------------------------

class TestPbtIdempotency:
    @given(
        action=st.sampled_from(list(PolicyAction)),
        event_id=st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("L", "N"))),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_second_call_always_duplicate(self, action, event_id):
        orch = Orchestrator()
        orch.execute(_decision(action), event_id)
        r2 = orch.execute(_decision(action), event_id)
        assert all(r.outcome == EffectOutcome.DUPLICATE for r in r2)


# ---------------------------------------------------------------------------
# PBT: every action produces at least one effect
# ---------------------------------------------------------------------------

class TestPbtAtLeastOneEffect:
    @given(action=st.sampled_from(list(PolicyAction)))
    @settings(max_examples=20, suppress_health_check=[HealthCheck.too_slow])
    def test_always_at_least_one_result(self, action):
        orch = Orchestrator()
        results = orch.execute(_decision(action), f"pbt-{action.value}")
        assert len(results) >= 1


# ---------------------------------------------------------------------------
# PBT: applied_count monotonically increases
# ---------------------------------------------------------------------------

class TestPbtAppliedCountMonotonic:
    @given(
        actions=st.lists(
            st.sampled_from(list(PolicyAction)),
            min_size=1, max_size=10,
        ),
    )
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow])
    def test_applied_count_never_decreases(self, actions):
        orch = Orchestrator()
        prev = 0
        for i, action in enumerate(actions):
            orch.execute(_decision(action), f"mono-{i}")
            curr = orch.applied_count
            assert curr >= prev
            prev = curr
