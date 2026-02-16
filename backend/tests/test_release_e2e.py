"""
PR-12: End-to-End Release Pipeline Simulation.

Validates the full chain: input → policy → report → gate → orchestrator.
Single-threaded, deterministic. No new production code.

Golden canonicalization: all timestamps are fixed strings ("2026-02-15T15:00:00Z"),
no random seeds, no trace IDs. Determinism is byte-level via fixed inputs.

Unit tests (≥14) + PBT (3): P12 determinism, P13 side-effect isolation,
P14 absolute block chain guarantee.

Validates: Requirements 1.1-1.8, 2.1-2.5, 3.1-3.6, 4.1-4.4, 5.1-5.3
"""
import json
import pytest
from hypothesis import given, settings, HealthCheck, assume
from hypothesis import strategies as st

from backend.app.testing.perf_budget import TestTier, TierRunResult, TestTiming
from backend.app.testing.policy_engine import (
    AuditLog,
    OpsGateStatus,
    PolicyAction,
    PolicyDecision,
    SloStatus,
    CanaryStatus,
    PolicyInput,
)
from backend.app.testing.rollout_orchestrator import (
    DriftSnapshot,
    PolicyCanaryResult,
    Orchestrator,
    EffectOutcome,
    EffectResult,
)
from backend.app.testing.release_policy import (
    ReleaseVerdict,
    BlockReasonCode,
    ABSOLUTE_BLOCK_REASONS,
    ReleasePolicyInput,
    ReleasePolicyResult,
    ReleasePolicy,
)
from backend.app.testing.release_report import (
    ReleaseReport,
    ReleaseReportGenerator,
)
from backend.app.testing.release_gate import (
    ReleaseOverride,
    GateDecision,
    ReleaseGate,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FIXED_TS = "2026-02-15T15:00:00Z"
FIXED_SCOPE = "v2.4"
FIXED_NOW_MS = 1000
FIXED_EVENT_ID = "release-v2.4-001"

# Map ReleaseVerdict → PolicyDecision for Orchestrator
# OK → PROMOTE, HOLD → HOLD, BLOCK → ABORT
_VERDICT_TO_POLICY_DECISION = {
    ReleaseVerdict.RELEASE_OK: PolicyDecision(
        action=PolicyAction.PROMOTE, rationale=[], details={},
    ),
    ReleaseVerdict.RELEASE_HOLD: PolicyDecision(
        action=PolicyAction.HOLD, rationale=[], details={},
    ),
    ReleaseVerdict.RELEASE_BLOCK: PolicyDecision(
        action=PolicyAction.ABORT, rationale=[], details={},
    ),
}


# ---------------------------------------------------------------------------
# Fixture inputs (stable, deterministic)
# ---------------------------------------------------------------------------

def _clean_tier(tier: TestTier = TestTier.SMOKE) -> TierRunResult:
    return TierRunResult(
        tier=tier, total_seconds=2.0, test_count=5,
        budget_seconds=10.0, passed=True, slowest=[],
    )


def _failed_tier(tier: TestTier = TestTier.SMOKE) -> TierRunResult:
    return TierRunResult(
        tier=tier, total_seconds=15.0, test_count=5,
        budget_seconds=10.0, passed=False, slowest=[
            TestTiming(name="test_slow_a", duration_seconds=5.0, tier=tier),
            TestTiming(name="test_slow_b", duration_seconds=4.0, tier=tier),
        ],
    )


def _clean_drift() -> DriftSnapshot:
    return DriftSnapshot(
        window_size=20, total_decisions=100,
        abort_count=0, promote_count=95, hold_count=3,
        degrade_count=0, override_count=2,
        abort_rate=0.0, override_rate=0.02,
        alert=False, alert_reason="",
    )


def _safe_canary() -> PolicyCanaryResult:
    return PolicyCanaryResult(
        old_version="v1", new_version="v2",
        total=100, safe=95, upgrade=5, breaking=0,
        guard_violations=0, recommendation="promote", reason="all safe",
    )


def _breaking_canary() -> PolicyCanaryResult:
    return PolicyCanaryResult(
        old_version="v1", new_version="v2",
        total=100, safe=70, upgrade=10, breaking=20,
        guard_violations=0, recommendation="abort", reason="breaking drifts",
    )


def _guard_violation_canary() -> PolicyCanaryResult:
    return PolicyCanaryResult(
        old_version="v1", new_version="v2",
        total=100, safe=80, upgrade=10, breaking=5,
        guard_violations=5, recommendation="abort", reason="guard violations",
    )


def _ok_input() -> ReleasePolicyInput:
    return ReleasePolicyInput(
        tier_results=[_clean_tier(TestTier.SMOKE), _clean_tier(TestTier.CORE)],
        flake_snapshot=[],
        drift_snapshot=_clean_drift(),
        canary_result=_safe_canary(),
        ops_gate=OpsGateStatus(passed=True),
    )


def _hold_input_tier_fail_flaky() -> ReleasePolicyInput:
    return ReleasePolicyInput(
        tier_results=[_failed_tier(TestTier.SMOKE), _clean_tier(TestTier.CORE)],
        flake_snapshot=["test_flaky_x", "test_flaky_y"],
        drift_snapshot=_clean_drift(),
        canary_result=_safe_canary(),
        ops_gate=OpsGateStatus(passed=True),
    )


def _block_input_ops_gate() -> ReleasePolicyInput:
    return ReleasePolicyInput(
        tier_results=[_clean_tier()],
        flake_snapshot=[],
        drift_snapshot=_clean_drift(),
        canary_result=_safe_canary(),
        ops_gate=OpsGateStatus(passed=False),
    )


def _block_input_guard_violation() -> ReleasePolicyInput:
    return ReleasePolicyInput(
        tier_results=[_clean_tier()],
        flake_snapshot=[],
        drift_snapshot=_clean_drift(),
        canary_result=_guard_violation_canary(),
        ops_gate=OpsGateStatus(passed=True),
    )


def _valid_override() -> ReleaseOverride:
    return ReleaseOverride(
        ttl_seconds=3600, created_at_ms=0,
        scope=FIXED_SCOPE, reason="manual approval",
        created_by="admin",
    )


def _expired_override() -> ReleaseOverride:
    return ReleaseOverride(
        ttl_seconds=1, created_at_ms=0,
        scope=FIXED_SCOPE, reason="expired test",
        created_by="admin",
    )


def _wrong_scope_override() -> ReleaseOverride:
    return ReleaseOverride(
        ttl_seconds=3600, created_at_ms=0,
        scope="v1.0-wrong", reason="scope test",
        created_by="admin",
    )


# ---------------------------------------------------------------------------
# Pipeline runner helper
# ---------------------------------------------------------------------------

POLICY = ReleasePolicy()
GEN = ReleaseReportGenerator()


def _run_pipeline(
    inp: ReleasePolicyInput,
    override: ReleaseOverride | None = None,
    release_scope: str = FIXED_SCOPE,
    now_ms: int = FIXED_NOW_MS,
    event_id: str = FIXED_EVENT_ID,
) -> tuple[ReleasePolicyResult, ReleaseReport, GateDecision, Orchestrator, list[EffectResult]]:
    """
    Full chain: input → policy → report → gate → orchestrator.
    Returns all intermediate results for cross-layer validation.
    """
    # 1. Policy
    result = POLICY.evaluate(inp)

    # 2. Report (deterministic — fixed timestamp)
    report = GEN.generate(result, inp, generated_at=FIXED_TS)

    # 3. Gate
    audit = AuditLog()
    gate = ReleaseGate(audit_log=audit)
    decision = gate.check(result, override=override,
                          release_scope=release_scope, now_ms=now_ms)

    # 4. Orchestrator (only if allowed)
    orch = Orchestrator()
    effects: list[EffectResult] = []
    if decision.allowed:
        policy_decision = _VERDICT_TO_POLICY_DECISION[result.verdict]
        effects = orch.execute(policy_decision, event_id)

    return result, report, decision, orch, effects


# ===================================================================
# Task 1+2: E2E Scenario Tests (≥8) + Chain Integrity
# ===================================================================

class TestE2EOKFlow:
    """Req 1.1: all clean → OK → allowed → orchestrator executes"""

    def test_ok_full_chain(self):
        result, report, decision, orch, effects = _run_pipeline(_ok_input())

        # Policy
        assert result.verdict == ReleaseVerdict.RELEASE_OK
        assert result.reasons == []

        # Report ↔ Policy
        assert report.verdict == "release_ok"
        assert report.reasons == []
        assert len(report.required_actions) == 0

        # Gate
        assert decision.allowed is True
        assert decision.verdict == ReleaseVerdict.RELEASE_OK
        assert decision.override_applied is False

        # Orchestrator
        assert orch.applied_count > 0
        assert len(effects) > 0
        assert all(e.outcome == EffectOutcome.APPLIED for e in effects)


class TestE2EHoldFlow:
    """Req 1.2: tier fail + flaky → HOLD → denied → no effects"""

    def test_hold_full_chain(self):
        result, report, decision, orch, effects = _run_pipeline(
            _hold_input_tier_fail_flaky(),
        )

        # Policy
        assert result.verdict == ReleaseVerdict.RELEASE_HOLD
        assert BlockReasonCode.TIER_FAIL in result.reasons
        assert BlockReasonCode.FLAKY_TESTS in result.reasons

        # Report ↔ Policy
        assert report.verdict == "release_hold"
        assert "TIER_FAIL" in report.reasons
        assert "FLAKY_TESTS" in report.reasons
        assert len(report.required_actions) >= 2

        # Gate
        assert decision.allowed is False
        assert "hold" in decision.audit_detail

        # Orchestrator — no side effects
        assert orch.applied_count == 0
        assert effects == []

    def test_hold_with_valid_override_allows(self):
        """Req 1.6: HOLD + valid override → allowed → orchestrator executes"""
        result, report, decision, orch, effects = _run_pipeline(
            _hold_input_tier_fail_flaky(),
            override=_valid_override(),
        )
        assert decision.allowed is True
        assert decision.override_applied is True
        assert orch.applied_count > 0

    def test_hold_with_expired_override_denied(self):
        """Req 1.7: HOLD + expired override → denied"""
        result, report, decision, orch, effects = _run_pipeline(
            _hold_input_tier_fail_flaky(),
            override=_expired_override(),
        )
        assert decision.allowed is False
        assert "OVERRIDE_EXPIRED" in decision.audit_detail
        assert orch.applied_count == 0

    def test_hold_with_scope_mismatch_denied(self):
        """Req 1.8: HOLD + scope mismatch → denied"""
        result, report, decision, orch, effects = _run_pipeline(
            _hold_input_tier_fail_flaky(),
            override=_wrong_scope_override(),
        )
        assert decision.allowed is False
        assert "SCOPE_MISMATCH" in decision.audit_detail
        assert orch.applied_count == 0


class TestE2EBlockFlow:
    """Req 1.3, 1.4: BLOCK → denied → override rejected"""

    def test_ops_gate_fail_block_chain(self):
        result, report, decision, orch, effects = _run_pipeline(
            _block_input_ops_gate(),
        )

        # Policy
        assert result.verdict == ReleaseVerdict.RELEASE_BLOCK
        assert BlockReasonCode.OPS_GATE_FAIL in result.reasons

        # Report ↔ Policy
        assert report.verdict == "release_block"
        assert "OPS_GATE_FAIL" in report.reasons

        # Gate
        assert decision.allowed is False

        # Orchestrator — no side effects
        assert orch.applied_count == 0
        assert effects == []

    def test_ops_gate_fail_override_breach(self):
        """Req 1.3: OPS_GATE_FAIL + override → CONTRACT_BREACH_NO_OVERRIDE"""
        result, report, decision, orch, effects = _run_pipeline(
            _block_input_ops_gate(),
            override=_valid_override(),
        )
        assert decision.allowed is False
        assert decision.override_applied is False
        assert "CONTRACT_BREACH_NO_OVERRIDE" in decision.audit_detail
        assert orch.applied_count == 0

    def test_guard_violation_override_breach(self):
        """Req 1.4: GUARD_VIOLATION + override → CONTRACT_BREACH_NO_OVERRIDE"""
        result, report, decision, orch, effects = _run_pipeline(
            _block_input_guard_violation(),
            override=_valid_override(),
        )
        assert decision.allowed is False
        assert "CONTRACT_BREACH_NO_OVERRIDE" in decision.audit_detail
        assert orch.applied_count == 0


class TestE2ECanaryFlow:
    """Req 1.5: canary BREAKING → correct verdict"""

    def test_canary_breaking_holds(self):
        inp = ReleasePolicyInput(
            tier_results=[_clean_tier()],
            flake_snapshot=[],
            drift_snapshot=_clean_drift(),
            canary_result=_breaking_canary(),
            ops_gate=OpsGateStatus(passed=True),
        )
        result, report, decision, orch, effects = _run_pipeline(inp)
        assert result.verdict == ReleaseVerdict.RELEASE_HOLD
        assert BlockReasonCode.CANARY_BREAKING in result.reasons
        assert decision.allowed is False
        assert orch.applied_count == 0


# ===================================================================
# Task 2: Chain Integrity Tests (≥3)
# ===================================================================

class TestChainIntegrity:
    """Req 2.1-2.5: cross-layer invariants"""

    def test_policy_verdict_matches_gate_verdict(self):
        """Req 2.1: policy verdict == gate verdict in every scenario"""
        for inp in [_ok_input(), _hold_input_tier_fail_flaky(),
                    _block_input_ops_gate(), _block_input_guard_violation()]:
            result, report, decision, orch, effects = _run_pipeline(inp)
            assert decision.verdict == result.verdict

    def test_policy_reasons_match_report_reasons(self):
        """Req 2.2, 2.3: policy reasons == report reasons"""
        for inp in [_ok_input(), _hold_input_tier_fail_flaky(),
                    _block_input_ops_gate()]:
            result, report, decision, orch, effects = _run_pipeline(inp)
            assert report.reasons == [r.value for r in result.reasons]
            assert len(report.required_actions) == len(result.required_actions)

    def test_denied_gate_means_zero_effects(self):
        """Req 2.4: gate denied → orchestrator applied_count unchanged"""
        for inp in [_hold_input_tier_fail_flaky(), _block_input_ops_gate()]:
            result, report, decision, orch, effects = _run_pipeline(inp)
            assert decision.allowed is False
            assert orch.applied_count == 0
            assert effects == []

    def test_allowed_gate_means_effects(self):
        """Req 2.5: gate allowed → orchestrator produces ≥1 effect"""
        result, report, decision, orch, effects = _run_pipeline(_ok_input())
        assert decision.allowed is True
        assert orch.applied_count > 0
        assert len(effects) >= 1


# ===================================================================
# Task 3: Golden Audit Artifact Tests (≥3)
# ===================================================================

class TestGoldenArtifacts:
    """Req 3.1-3.6: deterministic golden snapshots"""

    def test_golden_ok_deterministic(self):
        """Req 3.1: OK scenario golden snapshot is byte-level deterministic."""
        r1 = _run_pipeline(_ok_input())
        r2 = _run_pipeline(_ok_input())

        # Report text identical
        text1 = GEN.format_text(r1[1])
        text2 = GEN.format_text(r2[1])
        assert text1 == text2

        # Report JSON identical
        json1 = json.dumps(GEN.to_dict(r1[1]), sort_keys=True)
        json2 = json.dumps(GEN.to_dict(r2[1]), sort_keys=True)
        assert json1 == json2

        # Gate decision detail identical
        assert r1[2].audit_detail == r2[2].audit_detail

        # Content checks
        assert "RELEASE_OK" in text1
        assert r1[2].allowed is True

    def test_golden_hold_deterministic(self):
        """Req 3.2: HOLD scenario golden snapshot (tier fail + flaky)."""
        r1 = _run_pipeline(_hold_input_tier_fail_flaky())
        r2 = _run_pipeline(_hold_input_tier_fail_flaky())

        text1 = GEN.format_text(r1[1])
        text2 = GEN.format_text(r2[1])
        assert text1 == text2

        json1 = json.dumps(GEN.to_dict(r1[1]), sort_keys=True)
        json2 = json.dumps(GEN.to_dict(r2[1]), sort_keys=True)
        assert json1 == json2

        assert r1[2].audit_detail == r2[2].audit_detail

        # Content checks
        assert "RELEASE_HOLD" in text1
        assert "TIER_FAIL" in text1
        d = GEN.to_dict(r1[1])
        assert len(d["required_actions"]) >= 2

    def test_golden_block_deterministic(self):
        """Req 3.3: BLOCK scenario golden snapshot (OPS_GATE_FAIL + breach)."""
        r1 = _run_pipeline(_block_input_ops_gate(), override=_valid_override())
        r2 = _run_pipeline(_block_input_ops_gate(), override=_valid_override())

        text1 = GEN.format_text(r1[1])
        text2 = GEN.format_text(r2[1])
        assert text1 == text2

        json1 = json.dumps(GEN.to_dict(r1[1]), sort_keys=True)
        json2 = json.dumps(GEN.to_dict(r2[1]), sort_keys=True)
        assert json1 == json2

        assert r1[2].audit_detail == r2[2].audit_detail
        assert "CONTRACT_BREACH_NO_OVERRIDE" in r1[2].audit_detail

        # Content checks
        assert "RELEASE_BLOCK" in text1
        assert "OPS_GATE_FAIL" in text1


# ===================================================================
# Task 4: Side-Effect Isolation Tests
# ===================================================================

class TestSideEffectIsolation:
    """Req 4.1-4.4: orchestrator effects only when allowed"""

    def test_hold_zero_effects(self):
        """Req 4.1"""
        _, _, decision, orch, effects = _run_pipeline(_hold_input_tier_fail_flaky())
        assert decision.allowed is False
        assert orch.applied_count == 0
        assert effects == []

    def test_block_zero_effects(self):
        """Req 4.2"""
        _, _, decision, orch, effects = _run_pipeline(_block_input_ops_gate())
        assert decision.allowed is False
        assert orch.applied_count == 0
        assert effects == []

    def test_ok_has_effects(self):
        """Req 4.3"""
        _, _, decision, orch, effects = _run_pipeline(_ok_input())
        assert decision.allowed is True
        assert orch.applied_count > 0

    def test_hold_override_has_effects(self):
        """Req 4.4"""
        _, _, decision, orch, effects = _run_pipeline(
            _hold_input_tier_fail_flaky(), override=_valid_override(),
        )
        assert decision.allowed is True
        assert orch.applied_count > 0


# ===================================================================
# Hypothesis strategies (reused from PR-11 tests)
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


# ===================================================================
# Task 5: Property-Based Tests (3 PBT)
# ===================================================================

class TestPBTE2EDeterminism:
    """
    Property 12: E2E Zincir Determinizmi
    **Validates: Requirements 5.1**
    """

    @given(inp=_release_input_st)
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_full_chain_deterministic(self, inp: ReleasePolicyInput):
        # Feature: release-e2e-pipeline, Property 12: E2E Zincir Determinizmi
        r1 = _run_pipeline(inp, event_id="det-1")
        r2 = _run_pipeline(inp, event_id="det-2")

        # Policy verdict identical
        assert r1[0].verdict == r2[0].verdict
        assert r1[0].reasons == r2[0].reasons

        # Report identical (JSON level)
        assert GEN.to_dict(r1[1]) == GEN.to_dict(r2[1])

        # Gate decision identical
        assert r1[2].allowed == r2[2].allowed
        assert r1[2].verdict == r2[2].verdict
        assert r1[2].override_applied == r2[2].override_applied


class TestPBTSideEffectIsolation:
    """
    Property 13: Gate-Orchestrator Yan Etki İzolasyonu
    **Validates: Requirements 5.2, 4.1, 4.2**
    """

    @given(inp=_release_input_st)
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_denied_gate_no_effects(self, inp: ReleasePolicyInput):
        # Feature: release-e2e-pipeline, Property 13: Yan Etki İzolasyonu
        result, report, decision, orch, effects = _run_pipeline(inp)

        if not decision.allowed:
            assert orch.applied_count == 0
            assert effects == []
        else:
            # If allowed, orchestrator must have executed
            assert orch.applied_count > 0


class TestPBTAbsoluteBlockChain:
    """
    Property 14: Mutlak Blok Zincir Garantisi
    **Validates: Requirements 5.3, 1.3, 1.4**
    """

    @given(inp=_release_input_st, override=_override_st,
           now_ms=st.integers(min_value=0, max_value=200_000))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_absolute_block_override_always_rejected(
        self, inp: ReleasePolicyInput, override: ReleaseOverride, now_ms: int,
    ):
        # Feature: release-e2e-pipeline, Property 14: Mutlak Blok Zincir Garantisi
        result = POLICY.evaluate(inp)
        has_absolute = bool(set(result.reasons) & ABSOLUTE_BLOCK_REASONS)

        if not has_absolute:
            return  # not relevant for this property

        # Full chain with override attempt
        audit = AuditLog()
        gate = ReleaseGate(audit_log=audit)
        decision = gate.check(result, override=override,
                              release_scope=FIXED_SCOPE, now_ms=now_ms)

        assert decision.allowed is False
        assert decision.override_applied is False

        # Orchestrator must not execute
        orch = Orchestrator()
        assert orch.applied_count == 0
