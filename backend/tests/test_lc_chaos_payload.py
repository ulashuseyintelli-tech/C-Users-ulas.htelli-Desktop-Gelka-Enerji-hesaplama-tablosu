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
from backend.app.testing.stress_report import StressReport, FailDiagnostic
from backend.app.testing.scenario_runner import ScenarioRunner, InjectionConfig
from backend.app.testing.lc_config import FaultType, DEFAULT_SEED


# ---------------------------------------------------------------------------
# CH-2.1: Out-of-order event handling
# ---------------------------------------------------------------------------

class TestOutOfOrderEvents:
    def test_shuffled_outcomes_dont_change_aggregate(self):
        """Shuffling outcome order doesn't change failure count."""
        runner = ScenarioRunner()
        inj = InjectionConfig(enabled=True, fault_type=FaultType.DB_TIMEOUT, failure_rate=0.5, seed=42)
        result = runner.run_scenario("ch2-order", inj, request_count=100)
        original_failures = result.metadata["failure_count"]

        # Shuffle outcomes — aggregate should be same
        import random
        rng = random.Random(99)
        shuffled = list(result.outcomes)
        rng.shuffle(shuffled)
        assert shuffled.count("failure") == original_failures

    @given(seed=st.integers(min_value=0, max_value=2**31 - 1))
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow])
    def test_pbt_failure_count_invariant_under_reorder(self, seed: int):
        """PBT: failure count is order-independent."""
        runner = ScenarioRunner()
        inj = InjectionConfig(enabled=True, fault_type=FaultType.EXTERNAL_5XX, failure_rate=0.4, seed=seed)
        result = runner.run_scenario("ch2-pbt", inj, request_count=50)
        assert result.outcomes.count("failure") == result.metadata["failure_count"]


# ---------------------------------------------------------------------------
# CH-2.2: Truncated/partial payload — StressReport integrity
# ---------------------------------------------------------------------------

class TestTruncatedPayload:
    def test_report_with_empty_diagnostics(self):
        """Report with empty diagnostics still produces valid JSON."""
        report = StressReport(
            results=[{"scenario_id": "trunc"}],
            table=[{"scenario_id": "trunc"}],
            fail_summary=[],
            diagnostics=[],
            metadata={"truncated": True},
        )
        payload = json.loads(report.to_json())
        assert payload["diagnostics"] == []
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
            results=[], table=[], fail_summary=[],
            diagnostics=[diag],
        )
        payload = json.loads(report.to_json())
        d = payload["diagnostics"][0]
        assert d["observed"] is None
        assert d["expected"] is None

    @given(
        n_results=st.integers(min_value=0, max_value=10),
        n_diags=st.integers(min_value=0, max_value=5),
    )
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow])
    def test_pbt_report_json_always_valid(self, n_results: int, n_diags: int):
        """PBT: StressReport.to_json() always produces valid JSON."""
        results = [{"id": i} for i in range(n_results)]
        diags = [
            FailDiagnostic(
                scenario_id=f"d{i}", dependency="dep", outcome="fail",
                observed=i, expected=0, seed=i,
            )
            for i in range(n_diags)
        ]
        report = StressReport(
            results=results, table=results, fail_summary=[],
            diagnostics=diags,
        )
        payload = json.loads(report.to_json())
        assert len(payload["results"]) == n_results
        assert len(payload["diagnostics"]) == n_diags


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
