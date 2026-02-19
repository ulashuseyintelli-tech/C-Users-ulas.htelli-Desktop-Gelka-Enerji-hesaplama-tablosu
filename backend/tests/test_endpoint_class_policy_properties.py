"""
Endpoint-Class Policy — Property-Based Tests (Hypothesis).

3 properties, 200 examples each:
  EP-1: Resolve table determinism — same (tenant_mode, risk_class) → same effective_mode
  EP-2: Identity otherwise — when not (ENFORCE, LOW), effective_mode == tenant_mode
  EP-3: Monotonic safety — rank(effective_mode) <= rank(tenant_mode)

Feature: endpoint-class-policy, Tasks 2.3 / 2.4 / 2.5
"""
from __future__ import annotations

from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from backend.app.guards.guard_decision import (
    RiskClass,
    TenantMode,
    resolve_effective_mode,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Strategies
# ═══════════════════════════════════════════════════════════════════════════════

tenant_mode_st = st.sampled_from(list(TenantMode))
risk_class_st = st.sampled_from(list(RiskClass))


# ═══════════════════════════════════════════════════════════════════════════════
# Rank helper — OFF < SHADOW < ENFORCE
# ═══════════════════════════════════════════════════════════════════════════════

_MODE_RANK = {
    TenantMode.OFF: 0,
    TenantMode.SHADOW: 1,
    TenantMode.ENFORCE: 2,
}


# ═══════════════════════════════════════════════════════════════════════════════
# EP-1: Resolve Table Determinism
# Feature: endpoint-class-policy, Property EP-1
# ═══════════════════════════════════════════════════════════════════════════════

class TestEP1ResolveTableDeterminism:
    """
    Property EP-1: For any (tenant_mode, risk_class) pair,
    resolve_effective_mode() always returns the same TenantMode.
    Pure function — no side-effects, same input → same output.

    **Validates: Requirements E4.1, E4.4**
    """

    @given(tenant_mode=tenant_mode_st, risk_class=risk_class_st)
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_deterministic(self, tenant_mode: TenantMode, risk_class: RiskClass):
        result1 = resolve_effective_mode(tenant_mode, risk_class)
        result2 = resolve_effective_mode(tenant_mode, risk_class)
        assert result1 == result2, (
            f"Non-deterministic: resolve_effective_mode({tenant_mode}, {risk_class}) "
            f"returned {result1} then {result2}"
        )

    @given(tenant_mode=tenant_mode_st, risk_class=risk_class_st)
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_special_case_correctness(self, tenant_mode: TenantMode, risk_class: RiskClass):
        """EP-1 special case: ENFORCE + LOW must always be SHADOW."""
        result = resolve_effective_mode(tenant_mode, risk_class)
        if tenant_mode == TenantMode.ENFORCE and risk_class == RiskClass.LOW:
            assert result == TenantMode.SHADOW, (
                f"ENFORCE + LOW should be SHADOW, got {result}"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# EP-2: Identity Otherwise (OFF dominates + SHADOW preserved)
# Feature: endpoint-class-policy, Property EP-2
# ═══════════════════════════════════════════════════════════════════════════════

class TestEP2IdentityOtherwise:
    """
    Property EP-2: When not (ENFORCE, LOW), effective_mode == tenant_mode.
    OFF dominates for any risk_class. SHADOW preserved for any risk_class.

    **Validates: Requirements E1.1, E4.2**
    """

    @given(risk_class=risk_class_st)
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_off_dominates(self, risk_class: RiskClass):
        result = resolve_effective_mode(TenantMode.OFF, risk_class)
        assert result == TenantMode.OFF, (
            f"OFF + {risk_class} should be OFF, got {result}"
        )

    @given(risk_class=risk_class_st)
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_shadow_preserved(self, risk_class: RiskClass):
        result = resolve_effective_mode(TenantMode.SHADOW, risk_class)
        assert result == TenantMode.SHADOW, (
            f"SHADOW + {risk_class} should be SHADOW, got {result}"
        )

    @given(tenant_mode=tenant_mode_st, risk_class=risk_class_st)
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_identity_when_not_enforce_low(self, tenant_mode: TenantMode, risk_class: RiskClass):
        """If not (ENFORCE, LOW), effective_mode == tenant_mode."""
        result = resolve_effective_mode(tenant_mode, risk_class)
        if not (tenant_mode == TenantMode.ENFORCE and risk_class == RiskClass.LOW):
            assert result == tenant_mode, (
                f"Expected identity: resolve({tenant_mode}, {risk_class}) = {result}, "
                f"expected {tenant_mode}"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# EP-3: Monotonic Safety — effective_mode never more aggressive than tenant_mode
# Feature: endpoint-class-policy, Property EP-3
# ═══════════════════════════════════════════════════════════════════════════════

class TestEP3MonotonicSafety:
    """
    Property EP-3: rank(effective_mode) <= rank(tenant_mode).
    Effective mode can never be more aggressive than tenant_mode.
    OFF(0) < SHADOW(1) < ENFORCE(2).

    This catches drift like SHADOW + HIGH → ENFORCE (which must never happen).

    **Validates: Requirements E4.2**
    """

    @given(tenant_mode=tenant_mode_st, risk_class=risk_class_st)
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_monotonic(self, tenant_mode: TenantMode, risk_class: RiskClass):
        result = resolve_effective_mode(tenant_mode, risk_class)
        assert _MODE_RANK[result] <= _MODE_RANK[tenant_mode], (
            f"Monotonic violation: resolve({tenant_mode}, {risk_class}) = {result}, "
            f"rank {_MODE_RANK[result]} > {_MODE_RANK[tenant_mode]}"
        )
