"""
PR-7: Override Governance + Audit Log tests.

- Override applied when valid
- Override rejected: expired TTL
- Override rejected: TTL exceeds max
- Override rejected: scope mismatch (escalation prevention)
- Override rejected: non-overridable guard violated
- TTL expiry → automatic revert to base decision
- Audit log idempotency (duplicate key rejected)
- Audit log append-only
- PBT: override TTL expiry deterministic
- PBT: non-overridable guards always block
- PBT: scope escalation never succeeds
"""
import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from backend.app.testing.policy_engine import (
    PolicyEngine,
    PolicyAction,
    PolicyInput,
    PolicyDecision,
    SloStatus,
    CanaryStatus,
    OpsGateStatus,
    Override,
    OverrideType,
    OverrideScope,
    RationaleCode,
    AuditLog,
    AuditEntry,
    NON_OVERRIDABLE_GUARDS,
    MAX_OVERRIDE_TTL_SECONDS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _inp(canary: str = "abort", slo_met: bool = True, budget: float = 1.0,
         tenant: str | None = None, dep: str | None = None) -> PolicyInput:
    return PolicyInput(
        slo=SloStatus(met=slo_met, budget_remaining=budget),
        canary=CanaryStatus(decision=canary),
        ops_gate=OpsGateStatus(passed=True),
        tenant=tenant,
        dependency=dep,
    )


def _override(
    otype: OverrideType = OverrideType.FORCE_PROMOTE,
    scope: OverrideScope = OverrideScope.GLOBAL,
    scope_value: str | None = None,
    ttl: int = 600,
    created_at: int = 1_000_000,
    key: str = "key-1",
    reason: str = "hotfix",
    by: str = "ops-lead",
) -> Override:
    return Override(
        override_type=otype, scope=scope, scope_value=scope_value,
        ttl_seconds=ttl, created_at_ms=created_at, reason=reason,
        created_by=by, idempotency_key=key,
    )


ENGINE = PolicyEngine()


# ---------------------------------------------------------------------------
# Override applied
# ---------------------------------------------------------------------------

class TestOverrideApplied:
    def test_force_promote_overrides_canary_abort(self):
        inp = _inp(canary="abort")
        ovr = _override(OverrideType.FORCE_PROMOTE)
        now = 1_100_000  # within TTL
        d = ENGINE.evaluate_with_override(inp, ovr, now)
        assert d.action == PolicyAction.PROMOTE
        assert RationaleCode.OVERRIDE_APPLIED in d.rationale

    def test_force_abort_overrides_canary_promote(self):
        inp = _inp(canary="promote")
        ovr = _override(OverrideType.FORCE_ABORT)
        d = ENGINE.evaluate_with_override(inp, ovr, 1_100_000)
        assert d.action == PolicyAction.ABORT

    def test_force_degrade_applied(self):
        inp = _inp(canary="promote")
        ovr = _override(OverrideType.FORCE_DEGRADE)
        d = ENGINE.evaluate_with_override(inp, ovr, 1_100_000)
        assert d.action == PolicyAction.DEGRADE

    def test_no_override_returns_base(self):
        inp = _inp(canary="promote")
        d = ENGINE.evaluate_with_override(inp, None, 1_000_000)
        assert d.action == PolicyAction.PROMOTE


# ---------------------------------------------------------------------------
# Override rejected: expired
# ---------------------------------------------------------------------------

class TestOverrideExpired:
    def test_expired_override_reverts_to_base(self):
        inp = _inp(canary="abort")
        ovr = _override(ttl=60, created_at=1_000_000)  # expires at 1_060_000
        now = 2_000_000  # well past expiry
        d = ENGINE.evaluate_with_override(inp, ovr, now)
        assert d.action == PolicyAction.ABORT  # base decision (canary abort)
        assert RationaleCode.OVERRIDE_EXPIRED in d.rationale

    def test_override_at_exact_expiry_is_expired(self):
        ovr = _override(ttl=100, created_at=1_000_000)  # expires at 1_100_000
        assert ovr.is_expired(1_100_000) is True


# ---------------------------------------------------------------------------
# Override rejected: TTL exceeds max
# ---------------------------------------------------------------------------

class TestOverrideTtlMax:
    def test_ttl_exceeds_max_rejected(self):
        inp = _inp(canary="abort")
        ovr = _override(ttl=MAX_OVERRIDE_TTL_SECONDS + 1)
        d = ENGINE.evaluate_with_override(inp, ovr, 1_100_000)
        assert d.action == PolicyAction.ABORT  # base
        assert "ttl_exceeds_max" in str(d.details)


# ---------------------------------------------------------------------------
# Override rejected: scope mismatch
# ---------------------------------------------------------------------------

class TestOverrideScopeMismatch:
    def test_tenant_override_wrong_tenant(self):
        inp = _inp(canary="abort", tenant="tenant-a")
        ovr = _override(scope=OverrideScope.TENANT, scope_value="tenant-b")
        d = ENGINE.evaluate_with_override(inp, ovr, 1_100_000)
        assert d.action == PolicyAction.ABORT  # base
        assert "scope_mismatch" in str(d.details)

    def test_tenant_override_correct_tenant(self):
        inp = _inp(canary="abort", tenant="tenant-a")
        ovr = _override(scope=OverrideScope.TENANT, scope_value="tenant-a")
        d = ENGINE.evaluate_with_override(inp, ovr, 1_100_000)
        assert d.action == PolicyAction.PROMOTE

    def test_dependency_override_wrong_dep(self):
        inp = _inp(canary="abort", dep="db_primary")
        ovr = _override(scope=OverrideScope.DEPENDENCY, scope_value="cache_redis")
        d = ENGINE.evaluate_with_override(inp, ovr, 1_100_000)
        assert d.action == PolicyAction.ABORT  # base

    def test_dependency_override_correct_dep(self):
        inp = _inp(canary="abort", dep="db_primary")
        ovr = _override(scope=OverrideScope.DEPENDENCY, scope_value="db_primary")
        d = ENGINE.evaluate_with_override(inp, ovr, 1_100_000)
        assert d.action == PolicyAction.PROMOTE

    def test_global_override_matches_any(self):
        inp = _inp(canary="abort", tenant="x", dep="y")
        ovr = _override(scope=OverrideScope.GLOBAL)
        d = ENGINE.evaluate_with_override(inp, ovr, 1_100_000)
        assert d.action == PolicyAction.PROMOTE


# ---------------------------------------------------------------------------
# Override rejected: non-overridable guard
# ---------------------------------------------------------------------------

class TestNonOverridableGuards:
    def test_no_false_positive_guard_blocks_override(self):
        inp = _inp(canary="abort")
        ovr = _override()
        d = ENGINE.evaluate_with_override(inp, ovr, 1_100_000, violated_guards={"no_false_positive"})
        assert d.action == PolicyAction.ABORT  # base
        assert "non_overridable_guard" in str(d.details)

    def test_cardinality_guard_blocks_override(self):
        inp = _inp(canary="abort")
        ovr = _override()
        d = ENGINE.evaluate_with_override(inp, ovr, 1_100_000, violated_guards={"cardinality_bound"})
        assert d.action == PolicyAction.ABORT

    def test_non_guarded_violation_allows_override(self):
        inp = _inp(canary="abort")
        ovr = _override()
        d = ENGINE.evaluate_with_override(inp, ovr, 1_100_000, violated_guards={"some_other_check"})
        assert d.action == PolicyAction.PROMOTE  # override applied


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

class TestAuditLog:
    def test_append_only(self):
        log = AuditLog()
        e1 = AuditEntry(timestamp_ms=1000, action="evaluate", override=None, policy_input=None, decision=None)
        e2 = AuditEntry(timestamp_ms=2000, action="override", override=None, policy_input=None, decision=None)
        log.record(e1)
        log.record(e2)
        assert len(log.entries) == 2
        assert log.entries[0].timestamp_ms == 1000

    def test_idempotency_key_rejects_duplicate(self):
        log = AuditLog()
        ovr = _override(key="unique-1")
        e1 = AuditEntry(timestamp_ms=1000, action="override", override=ovr, policy_input=None, decision=None)
        e2 = AuditEntry(timestamp_ms=2000, action="override", override=ovr, policy_input=None, decision=None)
        assert log.record(e1) is True
        assert log.record(e2) is False  # duplicate
        assert len(log.entries) == 1

    def test_different_keys_both_accepted(self):
        log = AuditLog()
        ovr1 = _override(key="key-a")
        ovr2 = _override(key="key-b")
        e1 = AuditEntry(timestamp_ms=1000, action="override", override=ovr1, policy_input=None, decision=None)
        e2 = AuditEntry(timestamp_ms=2000, action="override", override=ovr2, policy_input=None, decision=None)
        assert log.record(e1) is True
        assert log.record(e2) is True
        assert len(log.entries) == 2

    def test_has_key(self):
        log = AuditLog()
        ovr = _override(key="check-me")
        e = AuditEntry(timestamp_ms=1000, action="override", override=ovr, policy_input=None, decision=None)
        assert log.has_key("check-me") is False
        log.record(e)
        assert log.has_key("check-me") is True


# ---------------------------------------------------------------------------
# PBT: TTL expiry deterministic
# ---------------------------------------------------------------------------

class TestPbtTtlExpiry:
    @given(
        ttl=st.integers(min_value=1, max_value=MAX_OVERRIDE_TTL_SECONDS),
        created_at=st.integers(min_value=0, max_value=10_000_000),
        elapsed=st.integers(min_value=0, max_value=10_000_000),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_expiry_is_deterministic(self, ttl, created_at, elapsed):
        ovr = _override(ttl=ttl, created_at=created_at)
        now = created_at + elapsed
        expired = ovr.is_expired(now)
        # expired iff elapsed >= ttl * 1000
        assert expired == (elapsed >= ttl * 1000)


# ---------------------------------------------------------------------------
# PBT: non-overridable guards always block
# ---------------------------------------------------------------------------

class TestPbtNonOverridableGuards:
    @given(
        guard=st.sampled_from(sorted(NON_OVERRIDABLE_GUARDS)),
        otype=st.sampled_from(list(OverrideType)),
        canary=st.sampled_from(["promote", "abort", "hold"]),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_non_overridable_always_blocks(self, guard, otype, canary):
        inp = _inp(canary=canary)
        ovr = _override(otype=otype)
        d = ENGINE.evaluate_with_override(inp, ovr, 1_100_000, violated_guards={guard})
        # Override must NOT be applied
        assert RationaleCode.OVERRIDE_APPLIED not in d.rationale


# ---------------------------------------------------------------------------
# PBT: scope escalation never succeeds
# ---------------------------------------------------------------------------

class TestPbtScopeEscalation:
    @given(
        tenant=st.text(min_size=1, max_size=10, alphabet=st.characters(whitelist_categories=("L",))),
        wrong_tenant=st.text(min_size=1, max_size=10, alphabet=st.characters(whitelist_categories=("L",))),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_tenant_scope_never_matches_wrong_tenant(self, tenant, wrong_tenant):
        if tenant == wrong_tenant:
            return  # skip trivial case
        inp = _inp(canary="abort", tenant=tenant)
        ovr = _override(scope=OverrideScope.TENANT, scope_value=wrong_tenant)
        d = ENGINE.evaluate_with_override(inp, ovr, 1_100_000)
        assert RationaleCode.OVERRIDE_APPLIED not in d.rationale
