"""
PR-11: ReleaseGate unit tests + property-based tests.

Unit tests (≥4): OK/BLOCK/HOLD flows, override TTL/scope validation,
absolute block override rejection.

PBT (3): gate verdict alignment, override validation, audit record.

Validates: Requirements 4.1-4.7, 6.3
"""
import pytest
from hypothesis import given, settings, HealthCheck, assume
from hypothesis import strategies as st

from backend.app.testing.perf_budget import TestTier, TierRunResult
from backend.app.testing.policy_engine import AuditLog, OpsGateStatus
from backend.app.testing.rollout_orchestrator import (
    DriftSnapshot,
    PolicyCanaryResult,
)
from backend.app.testing.release_policy import (
    ReleaseVerdict,
    BlockReasonCode,
    ABSOLUTE_BLOCK_REASONS,
    RequiredAction,
    ReleasePolicyInput,
    ReleasePolicyResult,
    ReleasePolicy,
)
from backend.app.testing.release_gate import (
    ReleaseOverride,
    GateDecision,
    ReleaseGate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

POLICY = ReleasePolicy()


def _clean_tier(tier: TestTier = TestTier.SMOKE) -> TierRunResult:
    return TierRunResult(
        tier=tier, total_seconds=1.0, test_count=5,
        budget_seconds=10.0, passed=True, slowest=[],
    )


def _clean_drift() -> DriftSnapshot:
    return DriftSnapshot(
        window_size=20, total_decisions=100,
        abort_count=0, promote_count=90, hold_count=5,
        degrade_count=0, override_count=5,
        abort_rate=0.0, override_rate=0.05,
        alert=False, alert_reason="",
    )


def _safe_canary() -> PolicyCanaryResult:
    return PolicyCanaryResult(
        old_version="v1", new_version="v2",
        total=100, safe=95, upgrade=5, breaking=0,
        guard_violations=0, recommendation="promote", reason="all safe",
    )


def _guard_violation_canary() -> PolicyCanaryResult:
    return PolicyCanaryResult(
        old_version="v1", new_version="v2",
        total=100, safe=80, upgrade=10, breaking=5,
        guard_violations=5, recommendation="abort", reason="guard violations",
    )


def _ok_result() -> ReleasePolicyResult:
    inp = ReleasePolicyInput(
        tier_results=[_clean_tier()],
        flake_snapshot=[],
        drift_snapshot=_clean_drift(),
        canary_result=_safe_canary(),
        ops_gate=OpsGateStatus(passed=True),
    )
    return POLICY.evaluate(inp)


def _hold_result() -> ReleasePolicyResult:
    inp = ReleasePolicyInput(
        tier_results=[TierRunResult(
            tier=TestTier.SMOKE, total_seconds=15.0, test_count=5,
            budget_seconds=10.0, passed=False, slowest=[],
        )],
        flake_snapshot=[],
        drift_snapshot=_clean_drift(),
        canary_result=_safe_canary(),
        ops_gate=OpsGateStatus(passed=True),
    )
    return POLICY.evaluate(inp)


def _block_result_ops_gate() -> ReleasePolicyResult:
    inp = ReleasePolicyInput(
        tier_results=[_clean_tier()],
        flake_snapshot=[],
        drift_snapshot=_clean_drift(),
        canary_result=_safe_canary(),
        ops_gate=OpsGateStatus(passed=False),
    )
    return POLICY.evaluate(inp)


def _block_result_guard_violation() -> ReleasePolicyResult:
    inp = ReleasePolicyInput(
        tier_results=[_clean_tier()],
        flake_snapshot=[],
        drift_snapshot=_clean_drift(),
        canary_result=_guard_violation_canary(),
        ops_gate=OpsGateStatus(passed=True),
    )
    return POLICY.evaluate(inp)


def _valid_override(scope: str = "v2.4", now_ms: int = 1000) -> ReleaseOverride:
    return ReleaseOverride(
        ttl_seconds=3600,
        created_at_ms=0,
        scope=scope,
        reason="manual approval",
        created_by="admin",
    )


# ===================================================================
# Unit Tests (≥4 integration-focused)
# ===================================================================

class TestGateOKFlow:
    """Req 4.1: RELEASE_OK → allowed=True"""

    def test_ok_verdict_allows_release(self):
        gate = ReleaseGate()
        decision = gate.check(_ok_result(), release_scope="v2.4")
        assert decision.allowed is True
        assert decision.verdict == ReleaseVerdict.RELEASE_OK
        assert decision.override_applied is False

    def test_ok_verdict_records_audit(self):
        audit = AuditLog()
        gate = ReleaseGate(audit_log=audit)
        gate.check(_ok_result())
        assert len(audit.entries) == 1
        assert "allow" in audit.entries[0].action


class TestGateHoldFlow:
    """Req 4.2: RELEASE_HOLD → allowed=False without override"""

    def test_hold_without_override_denied(self):
        gate = ReleaseGate()
        decision = gate.check(_hold_result(), release_scope="v2.4")
        assert decision.allowed is False
        assert decision.verdict == ReleaseVerdict.RELEASE_HOLD
        assert "hold" in decision.audit_detail

    def test_hold_with_valid_override_allowed(self):
        gate = ReleaseGate()
        override = _valid_override(scope="v2.4")
        decision = gate.check(
            _hold_result(), override=override,
            release_scope="v2.4", now_ms=1000,
        )
        assert decision.allowed is True
        assert decision.override_applied is True


class TestGateBlockFlow:
    """Req 4.3: RELEASE_BLOCK → allowed=False"""

    def test_block_denied(self):
        gate = ReleaseGate()
        decision = gate.check(_block_result_ops_gate(), release_scope="v2.4")
        assert decision.allowed is False
        assert decision.verdict == ReleaseVerdict.RELEASE_BLOCK
        assert "block" in decision.audit_detail

    def test_absolute_block_override_rejected(self):
        """Req 6.3: absolute block + override → CONTRACT_BREACH_NO_OVERRIDE"""
        gate = ReleaseGate()
        override = _valid_override()
        decision = gate.check(
            _block_result_ops_gate(), override=override,
            release_scope="v2.4", now_ms=1000,
        )
        assert decision.allowed is False
        assert decision.override_applied is False
        assert "CONTRACT_BREACH_NO_OVERRIDE" in decision.audit_detail

    def test_guard_violation_block_override_rejected(self):
        gate = ReleaseGate()
        override = _valid_override()
        decision = gate.check(
            _block_result_guard_violation(), override=override,
            release_scope="v2.4", now_ms=1000,
        )
        assert decision.allowed is False
        assert "CONTRACT_BREACH_NO_OVERRIDE" in decision.audit_detail


class TestOverrideValidation:
    """Req 4.4-4.6: TTL + scope checks"""

    def test_expired_override_denied(self):
        gate = ReleaseGate()
        expired = ReleaseOverride(
            ttl_seconds=10, created_at_ms=0,
            scope="v2.4", reason="test", created_by="admin",
        )
        # now_ms=10_000 → expires_at_ms=10*1000=10_000 → expired
        decision = gate.check(
            _hold_result(), override=expired,
            release_scope="v2.4", now_ms=10_000,
        )
        assert decision.allowed is False
        assert "OVERRIDE_EXPIRED" in decision.audit_detail

    def test_scope_mismatch_denied(self):
        gate = ReleaseGate()
        wrong_scope = _valid_override(scope="v1.0")
        decision = gate.check(
            _hold_result(), override=wrong_scope,
            release_scope="v2.4", now_ms=1000,
        )
        assert decision.allowed is False
        assert "SCOPE_MISMATCH" in decision.audit_detail

    def test_override_ttl_boundary_exact_expiry(self):
        """TTL exactly at boundary: created_at=0, ttl=10s → expires_at=10000ms.
        now_ms=10000 → expired (>= check)."""
        gate = ReleaseGate()
        boundary = ReleaseOverride(
            ttl_seconds=10, created_at_ms=0,
            scope="v2.4", reason="test", created_by="admin",
        )
        decision = gate.check(
            _hold_result(), override=boundary,
            release_scope="v2.4", now_ms=10_000,
        )
        assert decision.allowed is False
        assert "OVERRIDE_EXPIRED" in decision.audit_detail

    def test_override_just_before_expiry_allowed(self):
        gate = ReleaseGate()
        boundary = ReleaseOverride(
            ttl_seconds=10, created_at_ms=0,
            scope="v2.4", reason="test", created_by="admin",
        )
        decision = gate.check(
            _hold_result(), override=boundary,
            release_scope="v2.4", now_ms=9_999,
        )
        assert decision.allowed is True
        assert decision.override_applied is True


class TestAuditRecording:
    """Req 4.7: every check() produces audit entry"""

    def test_every_check_records_audit(self):
        audit = AuditLog()
        gate = ReleaseGate(audit_log=audit)
        gate.check(_ok_result())
        gate.check(_hold_result())
        gate.check(_block_result_ops_gate())
        assert len(audit.entries) == 3

    def test_audit_contains_action_detail(self):
        audit = AuditLog()
        gate = ReleaseGate(audit_log=audit)
        gate.check(_block_result_guard_violation(), override=_valid_override())
        entry = audit.entries[0]
        assert "CONTRACT_BREACH_NO_OVERRIDE" in entry.detail


# ===================================================================
# Hypothesis strategies
# ===================================================================

_tier_st = st.sampled_from(list(TestTier))

_tier_run_result_st = st.builds(
    TierRunResult,
    tier=_tier_st,
    total_seconds=st.floats(min_value=0.0, max_value=200.0, allow_nan=False),
    test_count=st.integers(min_value=1, max_value=100),
    budget_seconds=st.floats(min_value=0.1, max_value=200.0, allow_nan=False),
    passed=st.booleans(),
    slowest=st.just([]),
)

_drift_snapshot_st = st.one_of(
    st.none(),
    st.builds(
        DriftSnapshot,
        window_size=st.just(20),
        total_decisions=st.integers(min_value=1, max_value=1000),
        abort_count=st.integers(min_value=0, max_value=500),
        promote_count=st.integers(min_value=0, max_value=500),
        hold_count=st.integers(min_value=0, max_value=500),
        degrade_count=st.integers(min_value=0, max_value=500),
        override_count=st.integers(min_value=0, max_value=500),
        abort_rate=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
        override_rate=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
        alert=st.booleans(),
        alert_reason=st.text(max_size=20),
    ),
)

_canary_result_st = st.one_of(
    st.none(),
    st.builds(
        PolicyCanaryResult,
        old_version=st.just("v1"),
        new_version=st.just("v2"),
        total=st.integers(min_value=1, max_value=1000),
        safe=st.integers(min_value=0, max_value=1000),
        upgrade=st.integers(min_value=0, max_value=1000),
        breaking=st.integers(min_value=0, max_value=100),
        guard_violations=st.integers(min_value=0, max_value=100),
        recommendation=st.sampled_from(["promote", "abort", "hold"]),
        reason=st.text(max_size=20),
    ),
)

_release_input_st = st.builds(
    ReleasePolicyInput,
    tier_results=st.lists(_tier_run_result_st, min_size=0, max_size=5),
    flake_snapshot=st.one_of(
        st.none(),
        st.lists(st.text(min_size=1, max_size=20, alphabet=st.characters(
            whitelist_categories=("L", "N"))), max_size=10),
    ),
    drift_snapshot=_drift_snapshot_st,
    canary_result=_canary_result_st,
    ops_gate=st.builds(OpsGateStatus, passed=st.booleans()),
)

_override_st = st.builds(
    ReleaseOverride,
    ttl_seconds=st.integers(min_value=1, max_value=7200),
    created_at_ms=st.integers(min_value=0, max_value=100_000),
    scope=st.text(min_size=1, max_size=10, alphabet=st.characters(
        whitelist_categories=("L", "N"))),
    reason=st.text(min_size=1, max_size=20),
    created_by=st.text(min_size=1, max_size=10, alphabet=st.characters(
        whitelist_categories=("L",))),
)

_now_ms_st = st.integers(min_value=0, max_value=200_000)

_scope_st = st.text(min_size=1, max_size=10, alphabet=st.characters(
    whitelist_categories=("L", "N")))


# ===================================================================
# Property-Based Tests (3 PBT)
# ===================================================================

class TestPBTGateVerdictAlignment:
    """
    Property 9: Gate verdict uyumu
    **Validates: Requirements 4.1, 4.2, 4.3**
    """

    @given(inp=_release_input_st)
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_gate_respects_policy_verdict(self, inp: ReleasePolicyInput):
        # Feature: release-governance, Property 9: Gate verdict uyumu
        result = POLICY.evaluate(inp)
        gate = ReleaseGate()
        decision = gate.check(result)

        if result.verdict == ReleaseVerdict.RELEASE_OK:
            assert decision.allowed is True
        elif result.verdict == ReleaseVerdict.RELEASE_BLOCK:
            assert decision.allowed is False
        elif result.verdict == ReleaseVerdict.RELEASE_HOLD:
            # No override provided → denied
            assert decision.allowed is False


class TestPBTOverrideValidation:
    """
    Property 10: Override doğrulama
    **Validates: Requirements 4.4, 4.5, 4.6**
    """

    @given(inp=_release_input_st, override=_override_st, now_ms=_now_ms_st,
           scope=_scope_st)
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_override_rules(
        self, inp: ReleasePolicyInput, override: ReleaseOverride,
        now_ms: int, scope: str,
    ):
        # Feature: release-governance, Property 10: Override doğrulama
        result = POLICY.evaluate(inp)
        gate = ReleaseGate()
        decision = gate.check(result, override=override,
                              release_scope=scope, now_ms=now_ms)

        # If verdict is OK, override is irrelevant — always allowed
        if result.verdict == ReleaseVerdict.RELEASE_OK:
            assert decision.allowed is True
            return

        # If verdict is BLOCK with absolute reasons, override must be rejected
        has_absolute = bool(set(result.reasons) & ABSOLUTE_BLOCK_REASONS)
        if result.verdict == ReleaseVerdict.RELEASE_BLOCK and has_absolute:
            assert decision.allowed is False
            assert decision.override_applied is False
            return

        # If verdict is BLOCK (non-absolute), still denied
        if result.verdict == ReleaseVerdict.RELEASE_BLOCK:
            assert decision.allowed is False
            return

        # HOLD with override — check TTL and scope
        if override.is_expired(now_ms):
            assert decision.allowed is False
        elif scope and override.scope != scope:
            assert decision.allowed is False
        else:
            # Valid override on HOLD → allowed
            assert decision.allowed is True
            assert decision.override_applied is True


class TestPBTAuditRecord:
    """
    Property 11: Audit kaydı
    **Validates: Requirements 4.7**
    """

    @given(inp=_release_input_st, override=st.one_of(st.none(), _override_st),
           now_ms=_now_ms_st, scope=_scope_st)
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_every_check_produces_audit_entry(
        self, inp: ReleasePolicyInput,
        override: ReleaseOverride | None,
        now_ms: int, scope: str,
    ):
        # Feature: release-governance, Property 11: Audit kaydı
        audit = AuditLog()
        gate = ReleaseGate(audit_log=audit)
        initial_count = len(audit.entries)
        gate.check(POLICY.evaluate(inp), override=override,
                   release_scope=scope, now_ms=now_ms)
        assert len(audit.entries) == initial_count + 1
