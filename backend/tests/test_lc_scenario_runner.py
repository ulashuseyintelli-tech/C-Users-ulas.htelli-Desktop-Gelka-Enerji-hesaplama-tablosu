"""
Task 3.1: ScenarioRunner unit tests.

Tests:
- Noop scenario (no injection)
- Basic injection (failure rate applied)
- Determinism (same seed → same outcomes)
- Cleanup on success and exception
- Metrics delta attached
- Diagnostic payload format
- Multi-instance isolation
- Backward compat: InjectionConfig defaults, ScenarioResult fields
"""
import asyncio
import pytest
from unittest.mock import patch

from prometheus_client import CollectorRegistry

from backend.app.testing.lc_config import (
    DEFAULT_SEED,
    FaultType,
    ProfileType,
)
from backend.app.testing.load_harness import LoadProfile, DEFAULT_PROFILES
from backend.app.testing.scenario_runner import (
    InjectionConfig,
    ScenarioResult,
    ScenarioRunner,
)
from backend.app.testing.fault_injection import FaultInjector
from backend.app.testing.metrics_capture import MetricDelta


# ── Helpers ──────────────────────────────────────────────────────────────

def _small_profile() -> LoadProfile:
    """Tiny profile for fast CI tests."""
    return LoadProfile(ProfileType.BASELINE, target_rps=50.0, duration_seconds=10.0)


def _injection(
    fault_type: FaultType = FaultType.DB_TIMEOUT,
    failure_rate: float = 1.0,
    seed: int = DEFAULT_SEED,
    scale_factor: float = 0.01,
) -> InjectionConfig:
    return InjectionConfig(
        enabled=True,
        fault_type=fault_type,
        failure_rate=failure_rate,
        seed=seed,
        profile=_small_profile(),
        scale_factor=scale_factor,
    )


def _noop_injection(seed: int = DEFAULT_SEED) -> InjectionConfig:
    return InjectionConfig(
        enabled=False,
        seed=seed,
        profile=_small_profile(),
        scale_factor=0.01,
    )


# ── Noop scenario ────────────────────────────────────────────────────────

class TestNoopScenario:
    @pytest.mark.asyncio
    async def test_noop_returns_result(self):
        runner = ScenarioRunner()
        result = await runner.run_scenario("noop-1", _noop_injection())
        assert result.scenario_id == "noop-1"
        assert result.cb_opened is False
        assert result.load_result is not None
        assert result.metrics_delta is not None

    @pytest.mark.asyncio
    async def test_noop_all_success(self):
        runner = ScenarioRunner()
        result = await runner.run_scenario("noop-2", _noop_injection())
        assert result.load_result is not None
        assert result.load_result.failed_requests == 0
        assert result.load_result.successful_requests > 0

    @pytest.mark.asyncio
    async def test_noop_no_diagnostics(self):
        runner = ScenarioRunner()
        result = await runner.run_scenario("noop-3", _noop_injection())
        assert result.diagnostics == []


# ── Basic injection ──────────────────────────────────────────────────────

class TestBasicInjection:
    @pytest.mark.asyncio
    async def test_100pct_failure_all_fail(self):
        runner = ScenarioRunner()
        inj = _injection(failure_rate=1.0)
        result = await runner.run_scenario("inj-100", inj)
        assert result.load_result is not None
        assert result.load_result.successful_requests == 0
        assert result.load_result.failed_requests > 0

    @pytest.mark.asyncio
    async def test_0pct_failure_all_success(self):
        runner = ScenarioRunner()
        inj = _injection(failure_rate=0.0)
        result = await runner.run_scenario("inj-0", inj)
        assert result.load_result is not None
        assert result.load_result.failed_requests == 0
        assert result.load_result.successful_requests > 0

    @pytest.mark.asyncio
    async def test_partial_failure_mixed(self):
        runner = ScenarioRunner()
        inj = _injection(failure_rate=0.5, seed=42)
        result = await runner.run_scenario("inj-50", inj)
        lr = result.load_result
        assert lr is not None
        assert lr.successful_requests > 0
        assert lr.failed_requests > 0

    @pytest.mark.asyncio
    async def test_cb_opened_for_db_timeout_100pct(self):
        runner = ScenarioRunner()
        inj = _injection(fault_type=FaultType.DB_TIMEOUT, failure_rate=1.0)
        result = await runner.run_scenario("cb-test", inj)
        assert result.cb_opened is True

    @pytest.mark.asyncio
    async def test_cb_not_opened_for_killswitch(self):
        runner = ScenarioRunner()
        inj = _injection(fault_type=FaultType.KILLSWITCH, failure_rate=1.0)
        result = await runner.run_scenario("ks-test", inj)
        assert result.cb_opened is False


# ── Determinism (GNK-2) ─────────────────────────────────────────────────

class TestDeterminism:
    @pytest.mark.asyncio
    async def test_same_seed_same_outcome_counts(self):
        """Two runs with identical seed → identical success/fail counts."""
        for ft in [FaultType.DB_TIMEOUT, FaultType.EXTERNAL_5XX]:
            inj = _injection(fault_type=ft, failure_rate=0.5, seed=9999)
            r1 = await ScenarioRunner().run_scenario("det-1", inj)
            r2 = await ScenarioRunner().run_scenario("det-2", inj)
            assert r1.load_result.successful_requests == r2.load_result.successful_requests
            assert r1.load_result.failed_requests == r2.load_result.failed_requests

    @pytest.mark.asyncio
    async def test_different_seed_different_counts(self):
        """Different seeds should (very likely) produce different counts at 50%."""
        inj_a = _injection(failure_rate=0.5, seed=1)
        inj_b = _injection(failure_rate=0.5, seed=99999)
        r_a = await ScenarioRunner().run_scenario("diff-a", inj_a)
        r_b = await ScenarioRunner().run_scenario("diff-b", inj_b)
        # Not guaranteed but extremely likely with 200 requests
        lr_a, lr_b = r_a.load_result, r_b.load_result
        assert (lr_a.successful_requests != lr_b.successful_requests or
                lr_a.failed_requests != lr_b.failed_requests)


# ── Cleanup guarantee (R3 AC4) ───────────────────────────────────────────

class TestCleanupGuarantee:
    @pytest.mark.asyncio
    async def test_injector_reset_after_success(self):
        """FaultInjector singleton is reset after successful run."""
        runner = ScenarioRunner()
        inj = _injection()
        await runner.run_scenario("cleanup-ok", inj)
        # After run, singleton should be reset (None)
        assert FaultInjector._instance is None

    @pytest.mark.asyncio
    async def test_injector_reset_after_exception(self):
        """FaultInjector singleton is reset even if harness raises."""
        runner = ScenarioRunner()
        inj = _injection()

        # Patch LoadHarness.run_profile to raise
        with patch(
            "backend.app.testing.scenario_runner.LoadHarness.run_profile",
            side_effect=RuntimeError("boom"),
        ):
            with pytest.raises(RuntimeError, match="boom"):
                await runner.run_scenario("cleanup-err", inj)

        assert FaultInjector._instance is None

    @pytest.mark.asyncio
    async def test_injection_disabled_after_run(self):
        """All injection points disabled after run."""
        # Pre-enable an injection point
        injector = FaultInjector.get_instance()
        from backend.app.testing.fault_injection import InjectionPoint
        injector.enable(InjectionPoint.DB_TIMEOUT)
        assert injector.is_enabled(InjectionPoint.DB_TIMEOUT)

        runner = ScenarioRunner()
        inj = _injection()
        await runner.run_scenario("cleanup-dis", inj)

        # After reset, fresh instance should have nothing enabled
        fresh = FaultInjector.get_instance()
        for point in InjectionPoint:
            assert not fresh.is_enabled(point)
        FaultInjector.reset_instance()


# ── Metrics delta attached ───────────────────────────────────────────────

class TestMetricsDelta:
    @pytest.mark.asyncio
    async def test_delta_present_on_injection(self):
        runner = ScenarioRunner()
        inj = _injection()
        result = await runner.run_scenario("delta-1", inj)
        assert result.metrics_delta is not None
        assert isinstance(result.metrics_delta, MetricDelta)

    @pytest.mark.asyncio
    async def test_delta_present_on_noop(self):
        runner = ScenarioRunner()
        result = await runner.run_scenario("delta-noop", _noop_injection())
        assert result.metrics_delta is not None

    @pytest.mark.asyncio
    async def test_delta_invariant_ok_on_clean_run(self):
        """No negative counter deltas on a clean run."""
        runner = ScenarioRunner()
        result = await runner.run_scenario("delta-inv", _noop_injection())
        assert result.metrics_delta.invariant_ok is True


# ── Diagnostic payload format (GNK-1) ───────────────────────────────────

class TestDiagnosticPayload:
    @pytest.mark.asyncio
    async def test_result_has_diagnostics_list(self):
        runner = ScenarioRunner()
        inj = _injection()
        result = await runner.run_scenario("diag-1", inj)
        assert isinstance(result.diagnostics, list)

    @pytest.mark.asyncio
    async def test_summary_contains_required_keys(self):
        runner = ScenarioRunner()
        inj = _injection()
        result = await runner.run_scenario("diag-2", inj)
        s = result.summary()
        assert "scenario_id" in s
        assert "cb_opened" in s
        assert "diagnostic_count" in s
        assert "load" in s
        assert "metrics" in s


# ── Multi-instance isolation (LC-3) ─────────────────────────────────────

class TestMultiInstance:
    @pytest.mark.asyncio
    async def test_multi_instance_returns_n_results(self):
        runner = ScenarioRunner()
        inj = _injection(failure_rate=0.5)
        results = await runner.run_multi_instance_scenario("multi-1", inj, instance_count=3)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_multi_instance_separate_ids(self):
        runner = ScenarioRunner()
        inj = _injection()
        results = await runner.run_multi_instance_scenario("multi-2", inj, instance_count=2)
        ids = [r.scenario_id for r in results]
        assert len(set(ids)) == 2  # unique IDs

    @pytest.mark.asyncio
    async def test_multi_instance_each_has_load_result(self):
        runner = ScenarioRunner()
        inj = _injection()
        results = await runner.run_multi_instance_scenario("multi-3", inj, instance_count=2)
        for r in results:
            assert r.load_result is not None
            assert r.metrics_delta is not None


# ── Backward compat ──────────────────────────────────────────────────────

class TestBackwardCompat:
    def test_injection_config_defaults(self):
        cfg = InjectionConfig()
        assert cfg.enabled is False
        assert cfg.fault_type is None
        assert cfg.failure_rate == 1.0
        assert cfg.seed == DEFAULT_SEED
        assert cfg.scale_factor == 0.1

    def test_scenario_result_has_outcomes(self):
        r = ScenarioResult(scenario_id="bc-1")
        assert isinstance(r.outcomes, list)
        assert isinstance(r.diagnostics, list)

    @pytest.mark.asyncio
    async def test_metadata_contains_fault_type(self):
        runner = ScenarioRunner()
        inj = _injection(fault_type=FaultType.EXTERNAL_5XX)
        result = await runner.run_scenario("bc-meta", inj)
        assert result.metadata["fault_type"] == "external_5xx"
        assert "seed" in result.metadata
        assert "failure_rate" in result.metadata
