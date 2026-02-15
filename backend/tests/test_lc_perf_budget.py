"""
PR-10: Performance Budget + Tiering + Flake Sentinel tests.

- Tier definitions and file mapping
- Budget check pass/fail
- Slowest report formatting
- Flake sentinel detection
- PBT: budget pass iff total <= max
- PBT: flake detection iff mixed outcomes
- PBT: tier file lists are disjoint
"""
import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from backend.app.testing.perf_budget import (
    TestTier,
    TierBudget,
    DEFAULT_BUDGETS,
    TIER_FILE_MAP,
    files_for_tier,
    files_up_to_tier,
    TestTiming,
    TierRunResult,
    check_budget,
    format_slowest_report,
    FlakeSentinel,
    RunRecord,
)


# ---------------------------------------------------------------------------
# Tier definitions
# ---------------------------------------------------------------------------

class TestTierDefinitions:
    def test_all_tiers_have_budgets(self):
        for tier in TestTier:
            assert tier in DEFAULT_BUDGETS

    def test_smoke_budget_is_10s(self):
        assert DEFAULT_BUDGETS[TestTier.SMOKE].max_seconds == 10.0

    def test_core_budget_is_15s(self):
        assert DEFAULT_BUDGETS[TestTier.CORE].max_seconds == 15.0

    def test_concurrency_budget_is_30s(self):
        assert DEFAULT_BUDGETS[TestTier.CONCURRENCY].max_seconds == 30.0

    def test_soak_budget_is_120s(self):
        assert DEFAULT_BUDGETS[TestTier.SOAK].max_seconds == 120.0


# ---------------------------------------------------------------------------
# File mapping
# ---------------------------------------------------------------------------

class TestFileMapping:
    def test_smoke_files_exist(self):
        files = TIER_FILE_MAP[TestTier.SMOKE]
        assert len(files) >= 4

    def test_core_files_exist(self):
        files = TIER_FILE_MAP[TestTier.CORE]
        assert len(files) >= 10

    def test_concurrency_files_exist(self):
        files = TIER_FILE_MAP[TestTier.CONCURRENCY]
        assert len(files) == 2

    def test_files_for_tier_adds_prefix(self):
        files = files_for_tier(TestTier.SMOKE)
        assert all(f.startswith("backend/tests/") for f in files)

    def test_files_up_to_core_includes_smoke(self):
        files = files_up_to_tier(TestTier.CORE)
        smoke_files = files_for_tier(TestTier.SMOKE)
        for sf in smoke_files:
            assert sf in files

    def test_tier_files_are_disjoint(self):
        all_files: list[str] = []
        for tier in [TestTier.SMOKE, TestTier.CORE, TestTier.CONCURRENCY]:
            tier_files = TIER_FILE_MAP[tier]
            for f in tier_files:
                assert f not in all_files, f"{f} appears in multiple tiers"
                all_files.append(f)


# ---------------------------------------------------------------------------
# Budget check
# ---------------------------------------------------------------------------

class TestBudgetCheck:
    def test_within_budget_passes(self):
        result = check_budget(TestTier.SMOKE, 8.0, 50)
        assert result.passed is True
        assert result.margin_seconds == 2.0

    def test_over_budget_fails(self):
        result = check_budget(TestTier.SMOKE, 12.0, 50)
        assert result.passed is False
        assert result.margin_seconds == -2.0

    def test_exact_budget_passes(self):
        result = check_budget(TestTier.CORE, 15.0, 100)
        assert result.passed is True

    def test_slowest_sorted(self):
        timings = [
            TestTiming("fast", 0.1),
            TestTiming("slow", 2.0),
            TestTiming("mid", 0.5),
        ]
        result = check_budget(TestTier.CORE, 5.0, 3, timings)
        assert result.slowest[0].name == "slow"
        assert result.slowest[1].name == "mid"

    def test_slowest_capped_at_10(self):
        timings = [TestTiming(f"t{i}", float(i)) for i in range(20)]
        result = check_budget(TestTier.CORE, 5.0, 20, timings)
        assert len(result.slowest) == 10


# ---------------------------------------------------------------------------
# Slowest report
# ---------------------------------------------------------------------------

class TestSlowestReport:
    def test_report_contains_tier(self):
        result = check_budget(TestTier.SMOKE, 8.0, 10)
        report = format_slowest_report(result)
        assert "smoke" in report

    def test_report_contains_pass(self):
        result = check_budget(TestTier.SMOKE, 8.0, 10)
        report = format_slowest_report(result)
        assert "PASS" in report

    def test_report_contains_fail(self):
        result = check_budget(TestTier.SMOKE, 12.0, 10)
        report = format_slowest_report(result)
        assert "FAIL" in report

    def test_report_contains_slowest(self):
        timings = [TestTiming("slow_test", 1.5)]
        result = check_budget(TestTier.CORE, 5.0, 1, timings)
        report = format_slowest_report(result)
        assert "slow_test" in report


# ---------------------------------------------------------------------------
# Flake Sentinel
# ---------------------------------------------------------------------------

class TestFlakeSentinel:
    def test_no_records_is_clean(self):
        s = FlakeSentinel()
        assert s.is_clean() is True

    def test_all_pass_is_clean(self):
        s = FlakeSentinel()
        for i in range(5):
            s.record(i, "test_a", True)
        assert s.is_clean() is True

    def test_all_fail_is_clean(self):
        s = FlakeSentinel()
        for i in range(5):
            s.record(i, "test_a", False)
        assert s.is_clean() is True

    def test_mixed_outcomes_is_flaky(self):
        s = FlakeSentinel()
        s.record(1, "test_a", True)
        s.record(2, "test_a", False)
        assert s.is_clean() is False
        assert "test_a" in s.detect_flaky()

    def test_different_tests_independent(self):
        s = FlakeSentinel()
        s.record(1, "test_a", True)
        s.record(1, "test_b", False)
        s.record(2, "test_a", True)
        s.record(2, "test_b", False)
        assert s.is_clean() is True  # each test is consistent

    def test_window_respects_limit(self):
        s = FlakeSentinel(window_size=3)
        # Old runs: flaky
        s.record(1, "test_a", True)
        s.record(2, "test_a", False)
        # Recent runs: stable
        s.record(10, "test_a", True)
        s.record(11, "test_a", True)
        s.record(12, "test_a", True)
        # Window only sees last 3 runs → clean
        assert s.is_clean() is True


# ---------------------------------------------------------------------------
# PBT: budget pass iff total <= max
# ---------------------------------------------------------------------------

class TestPbtBudgetPass:
    @given(
        total=st.floats(min_value=0.0, max_value=200.0, allow_nan=False),
        budget_max=st.floats(min_value=0.1, max_value=200.0, allow_nan=False),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_pass_iff_within_budget(self, total, budget_max):
        budgets = {TestTier.SMOKE: TierBudget(TestTier.SMOKE, budget_max)}
        result = check_budget(TestTier.SMOKE, total, 10, budgets=budgets)
        assert result.passed == (total <= budget_max)


# ---------------------------------------------------------------------------
# PBT: flake detection iff mixed outcomes
# ---------------------------------------------------------------------------

class TestPbtFlakeDetection:
    @given(
        n_pass=st.integers(min_value=0, max_value=10),
        n_fail=st.integers(min_value=0, max_value=10),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_flaky_iff_mixed(self, n_pass, n_fail):
        s = FlakeSentinel(window_size=20)
        for i in range(n_pass):
            s.record(i, "test_x", True)
        for i in range(n_fail):
            s.record(n_pass + i, "test_x", False)
        if n_pass > 0 and n_fail > 0:
            assert not s.is_clean()
        else:
            assert s.is_clean()


# ---------------------------------------------------------------------------
# PBT: tier file lists are disjoint
# ---------------------------------------------------------------------------

class TestPbtTierDisjoint:
    @given(
        t1=st.sampled_from([TestTier.SMOKE, TestTier.CORE, TestTier.CONCURRENCY]),
        t2=st.sampled_from([TestTier.SMOKE, TestTier.CORE, TestTier.CONCURRENCY]),
    )
    @settings(max_examples=20, suppress_health_check=[HealthCheck.too_slow])
    def test_different_tiers_no_overlap(self, t1, t2):
        if t1 == t2:
            return
        f1 = set(TIER_FILE_MAP[t1])
        f2 = set(TIER_FILE_MAP[t2])
        assert not f1 & f2
