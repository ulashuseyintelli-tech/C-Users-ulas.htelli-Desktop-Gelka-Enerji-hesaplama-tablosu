"""
PR-11: ReleasePolicy unit tests + property-based tests.

Unit tests (≥25): individual signal checks, input validation edge cases,
multi-signal combinations, RequiredAction generation.

PBT (5): determinism, monotonicity, clean→OK, HOLD/BLOCK→action,
absolute block non-overridable.

Validates: Requirements 1.1-1.10, 2.1-2.4, 5.1-5.2, 6.1-6.2
"""
import pytest
from hypothesis import given, settings, HealthCheck, assume
from hypothesis import strategies as st

from backend.app.testing.perf_budget import (
    TestTier,
    TierRunResult,
    TestTiming,
)
from backend.app.testing.policy_engine import OpsGateStatus
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


# ---------------------------------------------------------------------------
# Helpers — build clean/dirty inputs
# ---------------------------------------------------------------------------

def _clean_tier(tier: TestTier = TestTier.SMOKE) -> TierRunResult:
    return TierRunResult(
        tier=tier, total_seconds=1.0, test_count=5,
        budget_seconds=10.0, passed=True, slowest=[],
    )


def _failed_tier(tier: TestTier = TestTier.SMOKE) -> TierRunResult:
    return TierRunResult(
        tier=tier, total_seconds=15.0, test_count=5,
        budget_seconds=10.0, passed=False, slowest=[],
    )


def _clean_drift() -> DriftSnapshot:
    return DriftSnapshot(
        window_size=20, total_decisions=100,
        abort_count=0, promote_count=90, hold_count=5,
        degrade_count=0, override_count=5,
        abort_rate=0.0, override_rate=0.05,
        alert=False, alert_reason="",
    )


def _alerted_drift() -> DriftSnapshot:
    return DriftSnapshot(
        window_size=20, total_decisions=100,
        abort_count=30, promote_count=40, hold_count=10,
        degrade_count=5, override_count=15,
        abort_rate=0.30, override_rate=0.15,
        alert=True, alert_reason="abort rate too high",
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
        total=100, safe=80, upgrade=10, breaking=10,
        guard_violations=0, recommendation="abort", reason="breaking drifts",
    )


def _guard_violation_canary() -> PolicyCanaryResult:
    return PolicyCanaryResult(
        old_version="v1", new_version="v2",
        total=100, safe=80, upgrade=10, breaking=5,
        guard_violations=5, recommendation="abort", reason="guard violations",
    )


def _clean_input() -> ReleasePolicyInput:
    return ReleasePolicyInput(
        tier_results=[_clean_tier(TestTier.SMOKE), _clean_tier(TestTier.CORE)],
        flake_snapshot=[],
        drift_snapshot=_clean_drift(),
        canary_result=_safe_canary(),
        ops_gate=OpsGateStatus(passed=True),
    )


POLICY = ReleasePolicy()


# ===================================================================
# Unit Tests (≥25)
# ===================================================================

class TestAllClean:
    """Req 1.1: all clean → RELEASE_OK"""

    def test_all_clean_signals_produce_ok(self):
        result = POLICY.evaluate(_clean_input())
        assert result.verdict == ReleaseVerdict.RELEASE_OK
        assert result.reasons == []
        assert result.required_actions == []

    def test_ok_has_no_details(self):
        result = POLICY.evaluate(_clean_input())
        assert result.details == {} or all(
            v == [] or v == 0 for v in result.details.values()
        )


class TestTierFail:
    """Req 1.2: tier fail → HOLD"""

    def test_single_tier_fail_holds(self):
        inp = ReleasePolicyInput(
            tier_results=[_failed_tier(TestTier.SMOKE), _clean_tier(TestTier.CORE)],
            flake_snapshot=[], drift_snapshot=_clean_drift(),
            canary_result=_safe_canary(), ops_gate=OpsGateStatus(passed=True),
        )
        result = POLICY.evaluate(inp)
        assert result.verdict == ReleaseVerdict.RELEASE_HOLD
        assert BlockReasonCode.TIER_FAIL in result.reasons

    def test_multiple_tier_fails_still_hold(self):
        inp = ReleasePolicyInput(
            tier_results=[_failed_tier(TestTier.SMOKE), _failed_tier(TestTier.CORE)],
            flake_snapshot=[], drift_snapshot=_clean_drift(),
            canary_result=_safe_canary(), ops_gate=OpsGateStatus(passed=True),
        )
        result = POLICY.evaluate(inp)
        assert result.verdict == ReleaseVerdict.RELEASE_HOLD
        assert BlockReasonCode.TIER_FAIL in result.reasons

    def test_tier_fail_reports_failed_tier_names(self):
        inp = ReleasePolicyInput(
            tier_results=[_failed_tier(TestTier.CONCURRENCY)],
            flake_snapshot=[], drift_snapshot=_clean_drift(),
            canary_result=_safe_canary(), ops_gate=OpsGateStatus(passed=True),
        )
        result = POLICY.evaluate(inp)
        assert "concurrency" in result.details.get("failed_tiers", [])


class TestFlake:
    """Req 1.3: flaky tests → HOLD"""

    def test_flaky_tests_hold(self):
        inp = ReleasePolicyInput(
            tier_results=[_clean_tier()],
            flake_snapshot=["test_a", "test_b"],
            drift_snapshot=_clean_drift(),
            canary_result=_safe_canary(), ops_gate=OpsGateStatus(passed=True),
        )
        result = POLICY.evaluate(inp)
        assert result.verdict == ReleaseVerdict.RELEASE_HOLD
        assert BlockReasonCode.FLAKY_TESTS in result.reasons

    def test_empty_flake_list_is_clean(self):
        inp = ReleasePolicyInput(
            tier_results=[_clean_tier()],
            flake_snapshot=[],
            drift_snapshot=_clean_drift(),
            canary_result=_safe_canary(), ops_gate=OpsGateStatus(passed=True),
        )
        result = POLICY.evaluate(inp)
        assert BlockReasonCode.FLAKY_TESTS not in result.reasons


class TestDrift:
    """Req 1.4: drift alert → HOLD"""

    def test_drift_alert_holds(self):
        inp = ReleasePolicyInput(
            tier_results=[_clean_tier()],
            flake_snapshot=[],
            drift_snapshot=_alerted_drift(),
            canary_result=_safe_canary(), ops_gate=OpsGateStatus(passed=True),
        )
        result = POLICY.evaluate(inp)
        assert result.verdict == ReleaseVerdict.RELEASE_HOLD
        assert BlockReasonCode.DRIFT_ALERT in result.reasons

    def test_drift_no_alert_is_clean(self):
        inp = ReleasePolicyInput(
            tier_results=[_clean_tier()],
            flake_snapshot=[],
            drift_snapshot=_clean_drift(),
            canary_result=_safe_canary(), ops_gate=OpsGateStatus(passed=True),
        )
        result = POLICY.evaluate(inp)
        assert BlockReasonCode.DRIFT_ALERT not in result.reasons


class TestCanaryBreaking:
    """Req 1.5: canary BREAKING → HOLD"""

    def test_breaking_canary_holds(self):
        inp = ReleasePolicyInput(
            tier_results=[_clean_tier()],
            flake_snapshot=[],
            drift_snapshot=_clean_drift(),
            canary_result=_breaking_canary(),
            ops_gate=OpsGateStatus(passed=True),
        )
        result = POLICY.evaluate(inp)
        assert result.verdict == ReleaseVerdict.RELEASE_HOLD
        assert BlockReasonCode.CANARY_BREAKING in result.reasons


class TestGuardViolation:
    """Req 1.6, 6.1: GUARD_VIOLATION → absolute BLOCK"""

    def test_guard_violation_always_blocks(self):
        inp = ReleasePolicyInput(
            tier_results=[_clean_tier()],
            flake_snapshot=[],
            drift_snapshot=_clean_drift(),
            canary_result=_guard_violation_canary(),
            ops_gate=OpsGateStatus(passed=True),
        )
        result = POLICY.evaluate(inp)
        assert result.verdict == ReleaseVerdict.RELEASE_BLOCK
        assert BlockReasonCode.GUARD_VIOLATION in result.reasons

    def test_guard_violation_ignores_other_clean_signals(self):
        """Even if everything else is perfect, guard violation = BLOCK."""
        inp = ReleasePolicyInput(
            tier_results=[_clean_tier(TestTier.SMOKE), _clean_tier(TestTier.CORE)],
            flake_snapshot=[],
            drift_snapshot=_clean_drift(),
            canary_result=_guard_violation_canary(),
            ops_gate=OpsGateStatus(passed=True),
        )
        result = POLICY.evaluate(inp)
        assert result.verdict == ReleaseVerdict.RELEASE_BLOCK

    def test_guard_violation_is_absolute_block_reason(self):
        assert BlockReasonCode.GUARD_VIOLATION in ABSOLUTE_BLOCK_REASONS


class TestOpsGateFail:
    """Req 1.7, 6.2: OPS_GATE_FAIL → absolute BLOCK"""

    def test_ops_gate_fail_always_blocks(self):
        inp = ReleasePolicyInput(
            tier_results=[_clean_tier()],
            flake_snapshot=[],
            drift_snapshot=_clean_drift(),
            canary_result=_safe_canary(),
            ops_gate=OpsGateStatus(passed=False),
        )
        result = POLICY.evaluate(inp)
        assert result.verdict == ReleaseVerdict.RELEASE_BLOCK
        assert BlockReasonCode.OPS_GATE_FAIL in result.reasons

    def test_ops_gate_fail_ignores_other_clean_signals(self):
        inp = ReleasePolicyInput(
            tier_results=[_clean_tier()],
            flake_snapshot=[],
            drift_snapshot=_clean_drift(),
            canary_result=_safe_canary(),
            ops_gate=OpsGateStatus(passed=False),
        )
        result = POLICY.evaluate(inp)
        assert result.verdict == ReleaseVerdict.RELEASE_BLOCK

    def test_ops_gate_fail_is_absolute_block_reason(self):
        assert BlockReasonCode.OPS_GATE_FAIL in ABSOLUTE_BLOCK_REASONS


class TestInputValidation:
    """Req 2.1-2.4: missing/None inputs → fail-closed"""

    def test_empty_tier_results_blocks(self):
        inp = ReleasePolicyInput(
            tier_results=[],
            flake_snapshot=[],
            drift_snapshot=_clean_drift(),
            canary_result=_safe_canary(),
            ops_gate=OpsGateStatus(passed=True),
        )
        result = POLICY.evaluate(inp)
        assert result.verdict == ReleaseVerdict.RELEASE_BLOCK
        assert BlockReasonCode.NO_TIER_DATA in result.reasons

    def test_none_flake_snapshot_blocks(self):
        inp = ReleasePolicyInput(
            tier_results=[_clean_tier()],
            flake_snapshot=None,
            drift_snapshot=_clean_drift(),
            canary_result=_safe_canary(),
            ops_gate=OpsGateStatus(passed=True),
        )
        result = POLICY.evaluate(inp)
        assert result.verdict == ReleaseVerdict.RELEASE_BLOCK
        assert BlockReasonCode.NO_FLAKE_DATA in result.reasons

    def test_none_drift_snapshot_holds(self):
        inp = ReleasePolicyInput(
            tier_results=[_clean_tier()],
            flake_snapshot=[],
            drift_snapshot=None,
            canary_result=_safe_canary(),
            ops_gate=OpsGateStatus(passed=True),
        )
        result = POLICY.evaluate(inp)
        assert result.verdict == ReleaseVerdict.RELEASE_HOLD
        assert BlockReasonCode.NO_DRIFT_DATA in result.reasons

    def test_none_canary_result_holds(self):
        inp = ReleasePolicyInput(
            tier_results=[_clean_tier()],
            flake_snapshot=[],
            drift_snapshot=_clean_drift(),
            canary_result=None,
            ops_gate=OpsGateStatus(passed=True),
        )
        result = POLICY.evaluate(inp)
        assert result.verdict == ReleaseVerdict.RELEASE_HOLD
        assert BlockReasonCode.NO_CANARY_DATA in result.reasons


class TestMultiSignalCombinations:
    """Req 1.10, 5.1-5.2: monotonic merge, worst verdict wins"""

    def test_tier_fail_plus_flake_stays_hold(self):
        inp = ReleasePolicyInput(
            tier_results=[_failed_tier()],
            flake_snapshot=["test_x"],
            drift_snapshot=_clean_drift(),
            canary_result=_safe_canary(),
            ops_gate=OpsGateStatus(passed=True),
        )
        result = POLICY.evaluate(inp)
        assert result.verdict == ReleaseVerdict.RELEASE_HOLD
        assert BlockReasonCode.TIER_FAIL in result.reasons
        assert BlockReasonCode.FLAKY_TESTS in result.reasons

    def test_hold_plus_block_escalates_to_block(self):
        inp = ReleasePolicyInput(
            tier_results=[_failed_tier()],
            flake_snapshot=["test_x"],
            drift_snapshot=_alerted_drift(),
            canary_result=_safe_canary(),
            ops_gate=OpsGateStatus(passed=False),
        )
        result = POLICY.evaluate(inp)
        assert result.verdict == ReleaseVerdict.RELEASE_BLOCK
        assert BlockReasonCode.OPS_GATE_FAIL in result.reasons
        assert BlockReasonCode.TIER_FAIL in result.reasons

    def test_guard_violation_plus_ops_gate_fail_both_block(self):
        inp = ReleasePolicyInput(
            tier_results=[_clean_tier()],
            flake_snapshot=[],
            drift_snapshot=_clean_drift(),
            canary_result=_guard_violation_canary(),
            ops_gate=OpsGateStatus(passed=False),
        )
        result = POLICY.evaluate(inp)
        assert result.verdict == ReleaseVerdict.RELEASE_BLOCK
        assert BlockReasonCode.GUARD_VIOLATION in result.reasons
        assert BlockReasonCode.OPS_GATE_FAIL in result.reasons

    def test_all_bad_signals_block_with_all_reasons(self):
        inp = ReleasePolicyInput(
            tier_results=[_failed_tier()],
            flake_snapshot=["test_flaky"],
            drift_snapshot=_alerted_drift(),
            canary_result=_guard_violation_canary(),
            ops_gate=OpsGateStatus(passed=False),
        )
        result = POLICY.evaluate(inp)
        assert result.verdict == ReleaseVerdict.RELEASE_BLOCK
        assert len(result.reasons) >= 4


class TestRequiredActions:
    """Req 1.9: HOLD/BLOCK → at least one RequiredAction"""

    def test_ok_has_no_actions(self):
        result = POLICY.evaluate(_clean_input())
        assert result.required_actions == []

    def test_hold_has_at_least_one_action(self):
        inp = ReleasePolicyInput(
            tier_results=[_failed_tier()],
            flake_snapshot=[],
            drift_snapshot=_clean_drift(),
            canary_result=_safe_canary(),
            ops_gate=OpsGateStatus(passed=True),
        )
        result = POLICY.evaluate(inp)
        assert len(result.required_actions) >= 1

    def test_block_has_at_least_one_action(self):
        inp = ReleasePolicyInput(
            tier_results=[_clean_tier()],
            flake_snapshot=[],
            drift_snapshot=_clean_drift(),
            canary_result=_safe_canary(),
            ops_gate=OpsGateStatus(passed=False),
        )
        result = POLICY.evaluate(inp)
        assert len(result.required_actions) >= 1

    def test_actions_match_reasons(self):
        inp = ReleasePolicyInput(
            tier_results=[_failed_tier()],
            flake_snapshot=["test_x"],
            drift_snapshot=_clean_drift(),
            canary_result=_safe_canary(),
            ops_gate=OpsGateStatus(passed=True),
        )
        result = POLICY.evaluate(inp)
        action_codes = [a.code for a in result.required_actions]
        for reason in result.reasons:
            assert reason in action_codes

    def test_action_descriptions_are_nonempty(self):
        inp = ReleasePolicyInput(
            tier_results=[_clean_tier()],
            flake_snapshot=[],
            drift_snapshot=_clean_drift(),
            canary_result=_safe_canary(),
            ops_gate=OpsGateStatus(passed=False),
        )
        result = POLICY.evaluate(inp)
        for action in result.required_actions:
            assert len(action.description) > 0


class TestAbsoluteBlockNoOverrideAction:
    """Req 6.1-6.2: absolute block actions must not suggest override"""

    def test_guard_violation_action_says_no_override(self):
        inp = ReleasePolicyInput(
            tier_results=[_clean_tier()],
            flake_snapshot=[],
            drift_snapshot=_clean_drift(),
            canary_result=_guard_violation_canary(),
            ops_gate=OpsGateStatus(passed=True),
        )
        result = POLICY.evaluate(inp)
        gv_actions = [a for a in result.required_actions
                      if a.code == BlockReasonCode.GUARD_VIOLATION]
        assert len(gv_actions) == 1
        assert "no override" in gv_actions[0].description.lower()

    def test_ops_gate_action_says_no_override(self):
        inp = ReleasePolicyInput(
            tier_results=[_clean_tier()],
            flake_snapshot=[],
            drift_snapshot=_clean_drift(),
            canary_result=_safe_canary(),
            ops_gate=OpsGateStatus(passed=False),
        )
        result = POLICY.evaluate(inp)
        og_actions = [a for a in result.required_actions
                      if a.code == BlockReasonCode.OPS_GATE_FAIL]
        assert len(og_actions) == 1
        assert "no override" in og_actions[0].description.lower()


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

_flake_snapshot_st = st.one_of(
    st.none(),
    st.lists(st.text(min_size=1, max_size=30, alphabet=st.characters(
        whitelist_categories=("L", "N", "P"))), max_size=10),
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

_ops_gate_st = st.builds(OpsGateStatus, passed=st.booleans())

_release_input_st = st.builds(
    ReleasePolicyInput,
    tier_results=st.lists(_tier_run_result_st, min_size=0, max_size=5),
    flake_snapshot=_flake_snapshot_st,
    drift_snapshot=_drift_snapshot_st,
    canary_result=_canary_result_st,
    ops_gate=_ops_gate_st,
)

# Strategy for "all clean" inputs
_clean_tier_st = st.builds(
    TierRunResult,
    tier=_tier_st,
    total_seconds=st.floats(min_value=0.0, max_value=9.0, allow_nan=False),
    test_count=st.integers(min_value=1, max_value=50),
    budget_seconds=st.just(10.0),
    passed=st.just(True),
    slowest=st.just([]),
)

_clean_input_st = st.builds(
    ReleasePolicyInput,
    tier_results=st.lists(_clean_tier_st, min_size=1, max_size=5),
    flake_snapshot=st.just([]),
    drift_snapshot=st.builds(
        DriftSnapshot,
        window_size=st.just(20),
        total_decisions=st.integers(min_value=1, max_value=1000),
        abort_count=st.just(0),
        promote_count=st.integers(min_value=1, max_value=500),
        hold_count=st.just(0),
        degrade_count=st.just(0),
        override_count=st.just(0),
        abort_rate=st.just(0.0),
        override_rate=st.just(0.0),
        alert=st.just(False),
        alert_reason=st.just(""),
    ),
    canary_result=st.builds(
        PolicyCanaryResult,
        old_version=st.just("v1"),
        new_version=st.just("v2"),
        total=st.integers(min_value=1, max_value=1000),
        safe=st.integers(min_value=1, max_value=1000),
        upgrade=st.just(0),
        breaking=st.just(0),
        guard_violations=st.just(0),
        recommendation=st.just("promote"),
        reason=st.just("all safe"),
    ),
    ops_gate=st.just(OpsGateStatus(passed=True)),
)


# ===================================================================
# Property-Based Tests (5 PBT)
# ===================================================================

class TestPBTCleanSignalsOK:
    """
    Property 1: Tüm temiz sinyaller → RELEASE_OK
    **Validates: Requirements 1.1**
    """

    @given(inp=_clean_input_st)
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_all_clean_signals_produce_ok(self, inp: ReleasePolicyInput):
        # Feature: release-governance, Property 1: Tüm temiz sinyaller → RELEASE_OK
        result = POLICY.evaluate(inp)
        assert result.verdict == ReleaseVerdict.RELEASE_OK
        assert result.reasons == []
        assert result.required_actions == []


class TestPBTDeterminism:
    """
    Property 2: Determinizm — aynı girdi → aynı çıktı
    **Validates: Requirements 1.8**
    """

    @given(inp=_release_input_st)
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_same_input_same_output(self, inp: ReleasePolicyInput):
        # Feature: release-governance, Property 2: Determinizm
        r1 = POLICY.evaluate(inp)
        r2 = POLICY.evaluate(inp)
        assert r1.verdict == r2.verdict
        assert r1.reasons == r2.reasons
        assert r1.required_actions == r2.required_actions


class TestPBTHoldBlockRequiresAction:
    """
    Property 3: HOLD/BLOCK → en az bir RequiredAction
    **Validates: Requirements 1.9**
    """

    @given(inp=_release_input_st)
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_hold_or_block_has_action(self, inp: ReleasePolicyInput):
        # Feature: release-governance, Property 3: HOLD/BLOCK → RequiredAction
        result = POLICY.evaluate(inp)
        if result.verdict != ReleaseVerdict.RELEASE_OK:
            assert len(result.required_actions) >= 1


class TestPBTMonotonicBlock:
    """
    Property 4: Monotonik blok kuralı — daha kötü input OK'a dönemez
    **Validates: Requirements 1.10, 5.1, 5.2**
    """

    @given(inp=_release_input_st)
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_adding_bad_signal_never_lowers_verdict(self, inp: ReleasePolicyInput):
        # Feature: release-governance, Property 4: Monotonik blok kuralı
        #
        # We need at least one tier so that NO_TIER_DATA doesn't dominate.
        # Degradation = flip ops_gate to failed (always BLOCK).
        assume(len(inp.tier_results) >= 1)

        original = POLICY.evaluate(inp)
        verdict_order = {"release_ok": 0, "release_hold": 1, "release_block": 2}

        # Degrade 1: flip ops_gate to failed
        degraded_ops = ReleasePolicyInput(
            tier_results=inp.tier_results,
            flake_snapshot=inp.flake_snapshot,
            drift_snapshot=inp.drift_snapshot,
            canary_result=inp.canary_result,
            ops_gate=OpsGateStatus(passed=False),
        )
        r_ops = POLICY.evaluate(degraded_ops)
        assert verdict_order[r_ops.verdict.value] >= verdict_order[original.verdict.value]

        # Degrade 2: add flaky tests (if snapshot exists)
        if inp.flake_snapshot is not None:
            degraded_flake = ReleasePolicyInput(
                tier_results=inp.tier_results,
                flake_snapshot=list(inp.flake_snapshot) + ["injected_flaky"],
                drift_snapshot=inp.drift_snapshot,
                canary_result=inp.canary_result,
                ops_gate=inp.ops_gate,
            )
            r_flake = POLICY.evaluate(degraded_flake)
            assert verdict_order[r_flake.verdict.value] >= verdict_order[original.verdict.value]


class TestPBTAbsoluteBlock:
    """
    Property 5: Mutlak blok — sözleşme ihlalleri override edilemez
    **Validates: Requirements 1.6, 1.7, 6.1, 6.2, 6.3**
    """

    @given(inp=_release_input_st)
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_guard_violation_or_ops_fail_always_blocks(self, inp: ReleasePolicyInput):
        # Feature: release-governance, Property 5: Mutlak blok
        has_guard_violation = (
            inp.canary_result is not None and inp.canary_result.guard_violations > 0
        )
        has_ops_fail = not inp.ops_gate.passed

        result = POLICY.evaluate(inp)

        if has_guard_violation:
            assert result.verdict == ReleaseVerdict.RELEASE_BLOCK
            assert BlockReasonCode.GUARD_VIOLATION in result.reasons

        if has_ops_fail:
            assert result.verdict == ReleaseVerdict.RELEASE_BLOCK
            assert BlockReasonCode.OPS_GATE_FAIL in result.reasons
