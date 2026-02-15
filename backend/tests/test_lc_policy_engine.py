"""
PR-7: Policy Engine unit tests.

Core decision logic:
- Kill-switch → ABORT (absolute priority)
- Ops gate fail → ABORT
- SLO not met + budget exhausted → ABORT
- SLO not met + budget remaining → DEGRADE
- SLO met + canary promote → PROMOTE
- SLO met + canary abort → ABORT
- SLO met + canary hold → HOLD
- PBT: determinism (same input → same output)
- PBT: kill-switch always overrides everything
- PBT: action is always a valid PolicyAction
"""
import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from backend.app.testing.policy_engine import (
    PolicyEngine,
    PolicyAction,
    PolicyInput,
    SloStatus,
    CanaryStatus,
    OpsGateStatus,
    RationaleCode,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _inp(
    slo_met: bool = True,
    budget: float = 1.0,
    canary: str = "promote",
    ops_pass: bool = True,
    ks: bool = False,
    tenant: str | None = None,
    dep: str | None = None,
) -> PolicyInput:
    return PolicyInput(
        slo=SloStatus(met=slo_met, budget_remaining=budget),
        canary=CanaryStatus(decision=canary),
        ops_gate=OpsGateStatus(passed=ops_pass),
        killswitch_active=ks,
        tenant=tenant,
        dependency=dep,
    )


ENGINE = PolicyEngine()


# ---------------------------------------------------------------------------
# Kill-switch priority
# ---------------------------------------------------------------------------

class TestKillSwitchPriority:
    def test_killswitch_active_always_abort(self):
        d = ENGINE.evaluate(_inp(ks=True, slo_met=True, canary="promote", ops_pass=True))
        assert d.action == PolicyAction.ABORT
        assert RationaleCode.KILLSWITCH_ACTIVE in d.rationale

    def test_killswitch_inactive_does_not_abort_alone(self):
        d = ENGINE.evaluate(_inp(ks=False, slo_met=True, canary="promote"))
        assert d.action != PolicyAction.ABORT or RationaleCode.KILLSWITCH_ACTIVE not in d.rationale


# ---------------------------------------------------------------------------
# Ops gate
# ---------------------------------------------------------------------------

class TestOpsGate:
    def test_ops_gate_fail_aborts(self):
        d = ENGINE.evaluate(_inp(ops_pass=False))
        assert d.action == PolicyAction.ABORT
        assert RationaleCode.OPS_GATE_FAIL in d.rationale

    def test_ops_gate_pass_continues(self):
        d = ENGINE.evaluate(_inp(ops_pass=True, canary="promote"))
        assert d.action == PolicyAction.PROMOTE


# ---------------------------------------------------------------------------
# SLO decisions
# ---------------------------------------------------------------------------

class TestSloDecisions:
    def test_slo_not_met_budget_exhausted_aborts(self):
        d = ENGINE.evaluate(_inp(slo_met=False, budget=0.0))
        assert d.action == PolicyAction.ABORT
        assert RationaleCode.BUDGET_EXHAUSTED in d.rationale

    def test_slo_not_met_budget_remaining_degrades(self):
        d = ENGINE.evaluate(_inp(slo_met=False, budget=0.3))
        assert d.action == PolicyAction.DEGRADE
        assert RationaleCode.SLO_NOT_MET in d.rationale

    def test_slo_met_proceeds_to_canary(self):
        d = ENGINE.evaluate(_inp(slo_met=True, canary="promote"))
        assert d.action == PolicyAction.PROMOTE
        assert RationaleCode.SLO_MET in d.rationale


# ---------------------------------------------------------------------------
# Canary decisions
# ---------------------------------------------------------------------------

class TestCanaryDecisions:
    def test_canary_promote(self):
        d = ENGINE.evaluate(_inp(canary="promote"))
        assert d.action == PolicyAction.PROMOTE
        assert RationaleCode.CANARY_PROMOTE in d.rationale

    def test_canary_abort(self):
        d = ENGINE.evaluate(_inp(canary="abort"))
        assert d.action == PolicyAction.ABORT
        assert RationaleCode.CANARY_ABORT in d.rationale

    def test_canary_hold(self):
        d = ENGINE.evaluate(_inp(canary="hold"))
        assert d.action == PolicyAction.HOLD
        assert RationaleCode.CANARY_HOLD in d.rationale


# ---------------------------------------------------------------------------
# Decision priority chain
# ---------------------------------------------------------------------------

class TestDecisionPriority:
    def test_killswitch_beats_slo_met_canary_promote(self):
        d = ENGINE.evaluate(_inp(ks=True, slo_met=True, canary="promote", ops_pass=True))
        assert d.action == PolicyAction.ABORT

    def test_ops_fail_beats_slo_met(self):
        d = ENGINE.evaluate(_inp(ops_pass=False, slo_met=True, canary="promote"))
        assert d.action == PolicyAction.ABORT

    def test_slo_fail_beats_canary_promote(self):
        d = ENGINE.evaluate(_inp(slo_met=False, budget=0.0, canary="promote"))
        assert d.action == PolicyAction.ABORT

    def test_full_green_path(self):
        d = ENGINE.evaluate(_inp(slo_met=True, budget=1.0, canary="promote", ops_pass=True, ks=False))
        assert d.action == PolicyAction.PROMOTE


# ---------------------------------------------------------------------------
# PBT: determinism
# ---------------------------------------------------------------------------

class TestPbtDeterminism:
    @given(
        slo_met=st.booleans(),
        budget=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
        canary=st.sampled_from(["promote", "abort", "hold"]),
        ops_pass=st.booleans(),
        ks=st.booleans(),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_same_input_same_output(self, slo_met, budget, canary, ops_pass, ks):
        inp = _inp(slo_met=slo_met, budget=budget, canary=canary, ops_pass=ops_pass, ks=ks)
        d1 = ENGINE.evaluate(inp)
        d2 = ENGINE.evaluate(inp)
        assert d1.action == d2.action
        assert d1.rationale == d2.rationale


# ---------------------------------------------------------------------------
# PBT: kill-switch always ABORT
# ---------------------------------------------------------------------------

class TestPbtKillSwitch:
    @given(
        slo_met=st.booleans(),
        budget=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
        canary=st.sampled_from(["promote", "abort", "hold"]),
        ops_pass=st.booleans(),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_killswitch_always_abort(self, slo_met, budget, canary, ops_pass):
        inp = _inp(slo_met=slo_met, budget=budget, canary=canary, ops_pass=ops_pass, ks=True)
        d = ENGINE.evaluate(inp)
        assert d.action == PolicyAction.ABORT


# ---------------------------------------------------------------------------
# PBT: action always valid
# ---------------------------------------------------------------------------

class TestPbtActionValid:
    @given(
        slo_met=st.booleans(),
        budget=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
        canary=st.sampled_from(["promote", "abort", "hold"]),
        ops_pass=st.booleans(),
        ks=st.booleans(),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_action_is_valid_enum(self, slo_met, budget, canary, ops_pass, ks):
        inp = _inp(slo_met=slo_met, budget=budget, canary=canary, ops_pass=ops_pass, ks=ks)
        d = ENGINE.evaluate(inp)
        assert d.action in list(PolicyAction)


# ---------------------------------------------------------------------------
# PBT: ops gate fail → always ABORT (regardless of other inputs)
# ---------------------------------------------------------------------------

class TestPbtOpsGateFail:
    @given(
        slo_met=st.booleans(),
        budget=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
        canary=st.sampled_from(["promote", "abort", "hold"]),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_ops_fail_always_abort_unless_killswitch(self, slo_met, budget, canary):
        """Ops gate fail → ABORT (killswitch=False to isolate ops gate)."""
        inp = _inp(slo_met=slo_met, budget=budget, canary=canary, ops_pass=False, ks=False)
        d = ENGINE.evaluate(inp)
        assert d.action == PolicyAction.ABORT
