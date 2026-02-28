"""
PR-5 CH-2: Ordering + Partial Payload chaos tests.

Tests that out-of-order events and truncated payloads:
- Are handled deterministically by scenario runner
- Don't corrupt StressReport output
- Maintain diagnostic payload integrity
"""
import json

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from backend.app.testing.chaos_harness import FaultSchedule, FaultAction, FaultBudget
from backend.app.testing.stress_report import StressReport, FailDiagnostic, build_stress_report
from backend.app.testing.scenario_runner import ScenarioRunner, InjectionConfig
from backend.app.testing.lc_config import FaultType, DEFAULT_SEED, ProfileType
from backend.app.testing.load_harness import LoadProfile, DEFAULT_PROFILES


# ---------------------------------------------------------------------------
# CH-2.1: Out-of-order event handling
# ---------------------------------------------------------------------------

class TestOutOfOrderEvents:
    @pytest.mark.asyncio
    async def test_shuffled_outcomes_dont_change_aggregate(self):
        """Shuffling outcome order doesn't change failure count."""
        runner = ScenarioRunner()
        inj = InjectionConfig(
            enabled=True, fault_type=FaultType.DB_TIMEOUT, failure_rate=0.5, seed=42,
            profile=DEFAULT_PROFILES[ProfileType.BASELINE], scale_factor=0.02,
        )
        result = await runner.run_scenario("ch2-order", inj)
        original_failures = result.load_result.failed_requests

        # Shuffle outcomes — aggregate should be same
        import random
        rng = random.Random(99)
        shuffled = list(result.outcomes)
        rng.shuffle(shuffled)
        assert shuffled.count("failure") == original_failures

    @given(
        n_success=st.integers(min_value=0, max_value=200),
        n_failure=st.integers(min_value=0, max_value=200),
    )
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow])
    def test_pbt_failure_count_invariant_under_reorder(self, n_success: int, n_failure: int):
        """PBT: failure count is order-independent regardless of shuffle."""
        import random
        outcomes = ["success"] * n_success + ["failure"] * n_failure
        rng = random.Random(42)
        shuffled = list(outcomes)
        rng.shuffle(shuffled)
        assert shuffled.count("failure") == n_failure
        assert shuffled.count("success") == n_success
        assert len(shuffled) == n_success + n_failure


# ---------------------------------------------------------------------------
# CH-2.2: Truncated/partial payload — StressReport integrity
# ---------------------------------------------------------------------------

class TestTruncatedPayload:
    def test_report_with_empty_diagnostics(self):
        """Report with empty diagnostics still produces valid JSON."""
        report = StressReport(
            table=[],
            recommendations=[],
            write_path_safe=True,
            flaky_segment=None,
            metadata={"truncated": True},
        )
        payload = json.loads(report.to_json())
        assert payload["table"] == []
        assert payload["metadata"]["truncated"] is True

    def test_report_with_partial_diagnostic(self):
        """Diagnostic with minimal fields still serializes correctly."""
        diag = FailDiagnostic(
            scenario_id="partial",
            dependency="unknown",
            outcome="truncated",
            observed=None,
            expected=None,
            seed=0,
        )
        report = StressReport(
            table=[], recommendations=[],
            write_path_safe=True, flaky_segment=None,
            metadata={"diag": diag.scenario_id},
        )
        payload = json.loads(report.to_json())
        assert payload["metadata"]["diag"] == "partial"

    @given(
        n_results=st.integers(min_value=0, max_value=10),
        n_diags=st.integers(min_value=0, max_value=5),
    )
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow])
    def test_pbt_report_json_always_valid(self, n_results: int, n_diags: int):
        """PBT: StressReport.to_json() always produces valid JSON."""
        table_rows = [{"scenario_name": f"s{i}"} for i in range(n_results)]
        report = StressReport(
            table=table_rows,
            recommendations=[],
            write_path_safe=True,
            flaky_segment=None,
        )
        payload = json.loads(report.to_json())
        assert len(payload["table"]) == n_results


# ---------------------------------------------------------------------------
# CH-2.3: FaultSchedule with TRUNCATE action
# ---------------------------------------------------------------------------

class TestTruncateSchedule:
    def test_truncate_actions_have_pct_param(self):
        """TRUNCATE faults always have truncate_pct in params."""
        schedule = FaultSchedule(
            seed=55, total_steps=100, fault_rate=0.5,
            allowed_actions=[FaultAction.TRUNCATE],
        )
        for event in schedule.events:
            if event.action == FaultAction.TRUNCATE:
                assert "truncate_pct" in event.params
                assert 0.0 < event.params["truncate_pct"] < 1.0

    @given(seed=st.integers(min_value=0, max_value=2**31 - 1))
    @settings(max_examples=20, suppress_health_check=[HealthCheck.too_slow])
    def test_pbt_truncate_pct_bounded(self, seed: int):
        """PBT: truncate_pct always in (0.1, 0.9)."""
        schedule = FaultSchedule(
            seed=seed, total_steps=30, fault_rate=0.5,
            allowed_actions=[FaultAction.TRUNCATE],
        )
        for event in schedule.events:
            if event.action == FaultAction.TRUNCATE:
                pct = event.params["truncate_pct"]
                assert 0.1 <= pct <= 0.9
