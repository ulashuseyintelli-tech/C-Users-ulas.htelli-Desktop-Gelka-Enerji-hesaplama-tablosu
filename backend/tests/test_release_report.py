"""
PR-11: ReleaseReportGenerator unit tests + property-based tests.

Unit tests (≥10): tier summary accuracy, text format structure,
JSON serialization, empty/None edge cases, ordering determinism.

PBT (3): report integrity, report determinism, round-trip.

Validates: Requirements 3.1-3.10
"""
import json
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
    RequiredAction,
    ReleasePolicyInput,
    ReleasePolicyResult,
    ReleasePolicy,
)
from backend.app.testing.release_report import (
    TierSummary,
    DriftSummary,
    OverrideSummary,
    GuardSummary,
    ReleaseReport,
    ReleaseReportGenerator,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

POLICY = ReleasePolicy()
GEN = ReleaseReportGenerator()


def _make_tier(tier: TestTier, total: float, budget: float,
               passed: bool, slowest: list[TestTiming] | None = None) -> TierRunResult:
    return TierRunResult(
        tier=tier, total_seconds=total, test_count=5,
        budget_seconds=budget, passed=passed,
        slowest=slowest or [],
    )


def _clean_input() -> ReleasePolicyInput:
    return ReleasePolicyInput(
        tier_results=[
            _make_tier(TestTier.SMOKE, 3.0, 10.0, True),
            _make_tier(TestTier.CORE, 8.0, 15.0, True),
        ],
        flake_snapshot=[],
        drift_snapshot=DriftSnapshot(
            window_size=20, total_decisions=100,
            abort_count=2, promote_count=90, hold_count=5,
            degrade_count=0, override_count=3,
            abort_rate=0.02, override_rate=0.03,
            alert=False, alert_reason="",
        ),
        canary_result=PolicyCanaryResult(
            old_version="v1", new_version="v2",
            total=100, safe=95, upgrade=5, breaking=0,
            guard_violations=0, recommendation="promote", reason="all safe",
        ),
        ops_gate=OpsGateStatus(passed=True),
    )


def _report_from_clean(generated_at: str = "2026-02-15T15:00:00Z") -> ReleaseReport:
    inp = _clean_input()
    result = POLICY.evaluate(inp)
    return GEN.generate(result, inp, generated_at=generated_at)


# ===================================================================
# Unit Tests (≥10)
# ===================================================================

class TestTierSummaryAccuracy:
    """Req 3.1, 3.2: slowest tests + budget usage"""

    def test_tier_summary_count_matches_input(self):
        report = _report_from_clean()
        assert len(report.tier_summaries) == 2

    def test_tier_summary_order_is_canonical(self):
        """SMOKE before CORE."""
        report = _report_from_clean()
        tiers = [ts.tier for ts in report.tier_summaries]
        assert tiers == ["smoke", "core"]

    def test_budget_usage_percent_correct(self):
        report = _report_from_clean()
        smoke = report.tier_summaries[0]
        assert smoke.tier == "smoke"
        assert abs(smoke.usage_percent - 30.0) < 0.01  # 3/10 * 100

    def test_slowest_tests_max_10(self):
        timings = [TestTiming(name=f"test_{i}", duration_seconds=float(i))
                    for i in range(15)]
        inp = ReleasePolicyInput(
            tier_results=[_make_tier(TestTier.SMOKE, 5.0, 10.0, True, timings)],
            flake_snapshot=[],
            drift_snapshot=DriftSnapshot(
                window_size=20, total_decisions=100,
                abort_count=0, promote_count=100, hold_count=0,
                degrade_count=0, override_count=0,
                abort_rate=0.0, override_rate=0.0,
                alert=False, alert_reason="",
            ),
            canary_result=PolicyCanaryResult(
                old_version="v1", new_version="v2",
                total=100, safe=100, upgrade=0, breaking=0,
                guard_violations=0, recommendation="promote", reason="ok",
            ),
            ops_gate=OpsGateStatus(passed=True),
        )
        result = POLICY.evaluate(inp)
        report = GEN.generate(result, inp)
        assert len(report.tier_summaries[0].slowest_tests) <= 10

    def test_slowest_tests_ordered_by_duration_desc_name_asc(self):
        timings = [
            TestTiming(name="test_b", duration_seconds=5.0),
            TestTiming(name="test_a", duration_seconds=5.0),
            TestTiming(name="test_c", duration_seconds=3.0),
        ]
        inp = ReleasePolicyInput(
            tier_results=[_make_tier(TestTier.SMOKE, 5.0, 10.0, True, timings)],
            flake_snapshot=[],
            drift_snapshot=DriftSnapshot(
                window_size=20, total_decisions=100,
                abort_count=0, promote_count=100, hold_count=0,
                degrade_count=0, override_count=0,
                abort_rate=0.0, override_rate=0.0,
                alert=False, alert_reason="",
            ),
            canary_result=PolicyCanaryResult(
                old_version="v1", new_version="v2",
                total=100, safe=100, upgrade=0, breaking=0,
                guard_violations=0, recommendation="promote", reason="ok",
            ),
            ops_gate=OpsGateStatus(passed=True),
        )
        result = POLICY.evaluate(inp)
        report = GEN.generate(result, inp)
        names = report.tier_summaries[0].slowest_tests
        # Equal duration → name asc: test_a before test_b
        assert names == ["test_a", "test_b", "test_c"]


class TestTextFormat:
    """Req 3.8: structured, readable plain text"""

    def test_text_contains_verdict(self):
        report = _report_from_clean()
        text = GEN.format_text(report)
        assert "RELEASE_OK" in text

    def test_text_contains_tier_info(self):
        report = _report_from_clean()
        text = GEN.format_text(report)
        assert "smoke" in text
        assert "core" in text

    def test_text_na_for_missing_drift(self):
        inp = ReleasePolicyInput(
            tier_results=[_make_tier(TestTier.SMOKE, 3.0, 10.0, True)],
            flake_snapshot=[],
            drift_snapshot=None,
            canary_result=None,
            ops_gate=OpsGateStatus(passed=True),
        )
        result = POLICY.evaluate(inp)
        report = GEN.generate(result, inp)
        text = GEN.format_text(report)
        assert "Drift Summary: N/A" in text

    def test_text_na_for_missing_override(self):
        report = _report_from_clean()
        text = GEN.format_text(report)
        assert "Override Summary: N/A" in text

    def test_text_na_for_missing_guard(self):
        report = _report_from_clean()
        text = GEN.format_text(report)
        assert "Guard Summary: N/A" in text


class TestJsonSerialization:
    """Req 3.9, 3.10: JSON serializable + round-trip"""

    def test_to_dict_is_json_serializable(self):
        report = _report_from_clean()
        d = GEN.to_dict(report)
        # Should not raise
        json_str = json.dumps(d)
        assert len(json_str) > 0

    def test_round_trip_preserves_verdict(self):
        report = _report_from_clean()
        d = GEN.to_dict(report)
        restored = GEN.from_dict(d)
        assert restored.verdict == report.verdict

    def test_round_trip_preserves_tier_summaries(self):
        report = _report_from_clean()
        d = GEN.to_dict(report)
        restored = GEN.from_dict(d)
        assert len(restored.tier_summaries) == len(report.tier_summaries)
        for orig, rest in zip(report.tier_summaries, restored.tier_summaries):
            assert orig.tier == rest.tier
            assert orig.total_seconds == rest.total_seconds
            assert orig.passed == rest.passed


# ===================================================================
# Hypothesis strategies
# ===================================================================

_tier_st = st.sampled_from(list(TestTier))

_timing_st = st.builds(
    TestTiming,
    name=st.text(min_size=1, max_size=20, alphabet=st.characters(
        whitelist_categories=("L", "N"))),
    duration_seconds=st.floats(min_value=0.0, max_value=100.0, allow_nan=False),
    tier=_tier_st,
)

_tier_run_st = st.builds(
    TierRunResult,
    tier=_tier_st,
    total_seconds=st.floats(min_value=0.0, max_value=200.0, allow_nan=False),
    test_count=st.integers(min_value=1, max_value=50),
    budget_seconds=st.floats(min_value=0.1, max_value=200.0, allow_nan=False),
    passed=st.booleans(),
    slowest=st.lists(_timing_st, max_size=15),
)

_drift_st = st.one_of(
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

_canary_st = st.one_of(
    st.none(),
    st.builds(
        PolicyCanaryResult,
        old_version=st.just("v1"),
        new_version=st.just("v2"),
        total=st.integers(min_value=1, max_value=1000),
        safe=st.integers(min_value=0, max_value=1000),
        upgrade=st.integers(min_value=0, max_value=100),
        breaking=st.integers(min_value=0, max_value=100),
        guard_violations=st.integers(min_value=0, max_value=100),
        recommendation=st.sampled_from(["promote", "abort", "hold"]),
        reason=st.text(max_size=20),
    ),
)

_input_st = st.builds(
    ReleasePolicyInput,
    tier_results=st.lists(_tier_run_st, min_size=0, max_size=5),
    flake_snapshot=st.one_of(
        st.none(),
        st.lists(st.text(min_size=1, max_size=20, alphabet=st.characters(
            whitelist_categories=("L", "N"))), max_size=10),
    ),
    drift_snapshot=_drift_st,
    canary_result=_canary_st,
    ops_gate=st.builds(OpsGateStatus, passed=st.booleans()),
)

_override_st = st.one_of(
    st.none(),
    st.builds(OverrideSummary,
              total_overrides=st.integers(0, 100),
              active_overrides=st.integers(0, 50),
              expired_overrides=st.integers(0, 50)),
)

_guard_st = st.one_of(
    st.none(),
    st.builds(GuardSummary,
              active_guards=st.lists(st.text(min_size=1, max_size=10,
                                             alphabet=st.characters(whitelist_categories=("L",))),
                                     max_size=5),
              violated_guards=st.lists(st.text(min_size=1, max_size=10,
                                               alphabet=st.characters(whitelist_categories=("L",))),
                                       max_size=5)),
)


# ===================================================================
# Property-Based Tests (3 PBT)
# ===================================================================

class TestPBTReportIntegrity:
    """
    Property 6: Rapor bütünlüğü
    **Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6**
    """

    @given(inp=_input_st, override=_override_st, guard=_guard_st)
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_report_contains_all_required_fields(
        self, inp: ReleasePolicyInput,
        override: OverrideSummary | None,
        guard: GuardSummary | None,
    ):
        # Feature: release-governance, Property 6: Rapor bütünlüğü
        result = POLICY.evaluate(inp)
        report = GEN.generate(result, inp, override, guard, "2026-01-01T00:00:00Z")

        # verdict matches
        assert report.verdict == result.verdict.value
        # reasons match
        assert report.reasons == [r.value for r in result.reasons]
        # required_actions match
        assert len(report.required_actions) == len(result.required_actions)
        # tier summaries: one per input tier, max 10 slowest each
        assert len(report.tier_summaries) == len(inp.tier_results)
        for ts in report.tier_summaries:
            assert len(ts.slowest_tests) <= 10
        # flaky tests sorted
        if inp.flake_snapshot:
            assert report.flaky_tests == sorted(inp.flake_snapshot)
        else:
            assert report.flaky_tests == []
        # drift summary present iff input has drift
        if inp.drift_snapshot is not None:
            assert report.drift_summary is not None
        else:
            assert report.drift_summary is None
        # override/guard pass-through
        assert report.override_summary == override
        assert report.guard_summary == guard


class TestPBTReportDeterminism:
    """
    Property 7: Rapor determinizmi
    **Validates: Requirements 3.7**
    """

    @given(inp=_input_st, override=_override_st, guard=_guard_st)
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_same_input_same_report(
        self, inp: ReleasePolicyInput,
        override: OverrideSummary | None,
        guard: GuardSummary | None,
    ):
        # Feature: release-governance, Property 7: Rapor determinizmi
        result = POLICY.evaluate(inp)
        ts = "2026-01-01T00:00:00Z"
        r1 = GEN.generate(result, inp, override, guard, ts)
        r2 = GEN.generate(result, inp, override, guard, ts)
        # Byte-level determinism via text output
        assert GEN.format_text(r1) == GEN.format_text(r2)
        # Dict-level determinism
        assert GEN.to_dict(r1) == GEN.to_dict(r2)


class TestPBTRoundTrip:
    """
    Property 8: ReleaseReport round-trip (serileştirme)
    **Validates: Requirements 3.10**
    """

    @given(inp=_input_st, override=_override_st, guard=_guard_st)
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_from_dict_to_dict_round_trip(
        self, inp: ReleasePolicyInput,
        override: OverrideSummary | None,
        guard: GuardSummary | None,
    ):
        # Feature: release-governance, Property 8: ReleaseReport round-trip
        result = POLICY.evaluate(inp)
        report = GEN.generate(result, inp, override, guard, "2026-01-01T00:00:00Z")
        d = GEN.to_dict(report)
        restored = GEN.from_dict(d)
        d2 = GEN.to_dict(restored)
        assert d == d2
